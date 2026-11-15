"""省域范围（A）：用户把目的地写成一个省时，怎么**覆盖全省**而不是只查省会。

为什么要这个
------------
2026-11-01 实测：`place/text` 传 `city=四川` 或 `city=510000` 都返回 count=1000，
但前 25 条**全部在成都各区**；`city=成都` 分布正常。省级单查询不可用，
所以旧逻辑把「四川」收敛到成都 —— 于是九寨沟、四姑娘山、稻城亚丁这些
离成都 300 公里外的川西景点，根本不会出现在候选池里（用户要 3 天四川行程，
拿到的是成都市区 + 1 个周边景区）。

做法（只做**取数范围**，不做日内编排 —— 编排见 B）
------------------------------------------------
1. `config/district` 拿省下属的地州/市（adcode + center），这是"全省有哪些片区"的事实来源；
2. `pick_anchors` 按天数决定查几个地州：3 天内 6 个、4-5 天 9 个、6 天以上全查。
   短行程不可能跑完 21 个地州，所以按**静态旅游权重 + 地理分散**先选最可能出真实景区的那几个；
   长行程则逐个查，覆盖不依赖这张表；
3. 每个地州各查一次真实 POI（`place/text` 的 city 参数逐地州可用，实测阿坝 311 条、
   乐山 204 条、九寨沟县 69 条），候选带上 `region_city`，让编排阶段知道"这个点在哪一片"。

纯函数、不取数：取数在 `api/agent.py` 的 `ExpertToolbox.get_sub_districts`，
这里只做"查哪些地州、怎么分片、跨片大概要多久"这些可离线测试的判断。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .poi_pool import haversine_km

# 省级行政区 → 高德检索用的核心城市（省会/直辖市）。高德 place/text 的 city 参数不认省名。
PROVINCE_DEFAULT_CITY: Dict[str, str] = {
    "河北": "石家庄", "山西": "太原", "辽宁": "沈阳", "吉林": "长春", "黑龙江": "哈尔滨",
    "江苏": "南京", "浙江": "杭州", "安徽": "合肥", "福建": "福州", "江西": "南昌",
    "山东": "济南", "河南": "郑州", "湖北": "武汉", "湖南": "长沙", "广东": "广州",
    "广西": "南宁", "海南": "海口", "四川": "成都", "贵州": "贵阳", "云南": "昆明",
    "陕西": "西安", "甘肃": "兰州", "青海": "西宁", "宁夏": "银川", "新疆": "乌鲁木齐",
    "西藏": "拉萨", "内蒙古": "呼和浩特", "内蒙": "呼和浩特",
    "北京": "北京", "上海": "上海", "天津": "天津", "重庆": "重庆",
    "台湾": "台北", "香港": "香港", "澳门": "澳门",
}
PROVINCE_SUFFIXES = ("维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区", "自治区", "省", "市")

# 直辖市/特别行政区本身就是一个市域，不需要"省域多锚点"：
# 北京拆成朝阳/海淀等区去查，只会把 6 次检索配额浪费在同一座城市里。
_NOT_PROVINCE_SCOPE = {"北京", "上海", "天津", "重庆", "香港", "澳门", "台湾"}

# 民族自治州名里的族群词：用来把「阿坝藏族羌族自治州」压成「阿坝」，
# 这样静态权重表能按短名匹配（不压缩的话每个州都要单独写一条全名）。
_ETHNIC_TOKENS = (
    "蒙古", "维吾尔", "哈萨克", "柯尔克孜", "朝鲜", "土家", "傈僳", "哈尼", "仡佬",
    "纳西", "撒拉", "东乡", "保安", "裕固", "锡伯", "达斡尔", "鄂温克", "鄂伦春",
    "赫哲", "门巴", "珞巴", "普米", "独龙", "基诺", "德昂", "阿昌", "布朗", "毛南",
    "仫佬", "塔吉克", "乌孜别克", "俄罗斯", "塔塔尔", "藏", "羌", "彝", "苗", "壮",
    "回", "白", "傣", "景颇", "布依", "侗", "瑶", "黎", "水", "佤", "怒", "京",
)
_REGION_SUFFIXES = ("自治州", "地区", "盟", "市", "州")

# 地州旅游权重（**只用来说明"短行程先查哪几个"**，不是景区质量评分）。
# 天数 ≥6 时 pick_anchors 会全量返回，覆盖不依赖这张表；短行程漏掉冷门地州是可接受的，
# 因为 3 天内本来也跑不了几个片区。数值越大越可能藏着用户想去的自然景观。
TOURISM_WEIGHT: Dict[str, float] = {
    # 四川 / 云南 / 贵州 / 重庆周边
    "阿坝": 6.0, "甘孜": 6.0, "乐山": 5.0, "成都": 4.5, "雅安": 3.5, "凉山": 3.5,
    "绵阳": 3.0, "攀枝花": 2.0, "广元": 2.5, "南充": 2.0, "宜宾": 2.5, "泸州": 2.0,
    "丽江": 6.0, "大理": 6.0, "迪庆": 6.0, "西双版纳": 5.5, "昆明": 4.5, "保山": 3.0,
    "怒江": 3.5, "普洱": 3.0, "红河": 3.0, "文山": 2.5, "玉溪": 2.5, "临沧": 2.5,
    "黔东南": 4.5, "安顺": 4.5, "黔南": 3.5, "贵阳": 4.0, "遵义": 3.5, "铜仁": 3.5,
    "黔西南": 3.0, "毕节": 2.5, "六盘水": 2.5,
    # 西北
    "阿勒泰": 6.0, "伊犁": 6.0, "喀什": 5.0, "巴音郭楞": 5.0, "吐鲁番": 4.5,
    "乌鲁木齐": 4.5, "阿克苏": 3.5, "塔城": 3.0, "博尔塔拉": 3.0, "和田": 3.0,
    "克孜勒苏": 2.5, "昌吉": 2.5, "哈密": 2.5,
    "林芝": 6.0, "拉萨": 5.0, "日喀则": 4.5, "阿里": 4.5, "山南": 4.0, "昌都": 3.5,
    "那曲": 3.5,
    "海西": 4.5, "西宁": 4.0, "海南": 3.5, "海北": 3.5, "玉树": 3.5, "果洛": 3.0,
    "黄南": 3.0,
    "酒泉": 5.0, "甘南": 5.0, "张掖": 4.5, "兰州": 4.0, "天水": 3.5, "嘉峪关": 3.0,
    "陇南": 3.0, "平凉": 3.0, "武威": 2.5, "庆阳": 2.0, "定西": 2.0,
    "西安": 5.0, "渭南": 3.5, "延安": 3.5, "宝鸡": 3.5, "汉中": 3.0, "榆林": 3.0,
    "银川": 4.0, "中卫": 4.0, "固原": 3.5, "吴忠": 2.5, "石嘴山": 2.0,
    # 华北 / 东北
    "张家界": 6.0, "长沙": 4.5, "湘西": 4.5, "衡阳": 3.5, "郴州": 3.5, "岳阳": 3.5,
    "怀化": 3.0, "株洲": 2.5, "湘潭": 2.5, "永州": 2.5,
    "恩施": 4.5, "神农架": 4.5, "宜昌": 4.0, "十堰": 4.0, "武汉": 4.0, "襄阳": 3.5,
    "神农架林区": 4.5, "咸宁": 2.5, "黄冈": 2.5, "荆州": 2.5,
    "洛阳": 4.5, "开封": 3.5, "焦作": 3.5, "郑州": 3.5, "安阳": 3.0, "南阳": 3.0,
    "新乡": 2.5, "信阳": 2.5,
    "青岛": 4.5, "泰安": 4.5, "威海": 4.0, "济南": 3.5, "烟台": 3.5, "济宁": 3.0,
    "日照": 3.0, "临沂": 2.5, "潍坊": 2.5,
    "大同": 4.5, "晋中": 4.0, "忻州": 4.0, "太原": 3.5, "临汾": 3.0, "运城": 3.0,
    "长治": 2.5, "晋城": 2.5,
    "承德": 4.5, "秦皇岛": 4.0, "保定": 3.5, "石家庄": 3.0, "张家口": 3.0, "唐山": 2.5,
    "邯郸": 2.5,
    "大连": 4.5, "丹东": 3.5, "沈阳": 3.5, "鞍山": 3.0, "本溪": 3.0, "抚顺": 2.5,
    "营口": 2.5,
    "延边": 5.0, "长春": 3.5, "白山": 3.5, "吉林": 3.0, "通化": 2.5, "松原": 2.0,
    "哈尔滨": 4.5, "牡丹江": 4.0, "大兴安岭": 4.0, "伊春": 3.5, "齐齐哈尔": 2.5,
    "黑河": 2.5,
    # 华东 / 华南
    "黄山": 5.5, "池州": 3.5, "合肥": 3.5, "宣城": 3.0, "安庆": 3.0, "六安": 2.5,
    "芜湖": 2.5, "蚌埠": 2.0,
    "杭州": 5.0, "舟山": 4.5, "宁波": 3.5, "温州": 3.5, "嘉兴": 3.5, "绍兴": 3.5,
    "丽水": 3.5, "湖州": 3.0, "金华": 3.0, "衢州": 2.5, "台州": 2.5,
    "南京": 4.5, "苏州": 4.5, "扬州": 3.5, "无锡": 3.5, "常州": 3.0, "镇江": 3.0,
    "徐州": 2.5, "连云港": 2.5,
    "厦门": 5.0, "泉州": 4.0, "福州": 4.0, "南平": 4.5, "龙岩": 3.5, "宁德": 3.5,
    "三明": 3.0, "漳州": 3.0, "莆田": 2.5,
    "景德镇": 3.5, "上饶": 3.5, "九江": 3.5, "南昌": 3.5, "吉安": 3.0, "赣州": 3.0,
    "萍乡": 2.5, "鹰潭": 2.5,
    "三亚": 5.5, "海口": 4.0,
    "桂林": 6.0, "北海": 4.5, "南宁": 4.0, "柳州": 3.5, "崇左": 3.5, "防城港": 3.0,
    "百色": 3.0, "河池": 3.0, "贺州": 3.0, "梧州": 2.5, "玉林": 2.0,
    "广州": 4.5, "深圳": 4.0, "珠海": 3.5, "韶关": 3.0, "清远": 3.0, "惠州": 3.0,
    "潮州": 3.0, "湛江": 3.0, "梅州": 2.5, "肇庆": 3.0, "汕头": 2.5,
    "呼伦贝尔": 6.0, "阿拉善": 4.5, "锡林郭勒": 4.0, "呼和浩特": 3.5, "鄂尔多斯": 3.5,
    "赤峰": 3.5, "包头": 3.0, "通辽": 2.5, "兴安": 3.0,
    "台北": 4.5, "花莲": 5.0, "台东": 4.0, "南投": 4.5, "高雄": 3.5, "台中": 3.5,
}


def _aliases() -> Dict[str, str]:
    """省名别名表：短名/全名（四川省、广西壮族自治区…）都映射到短名。"""
    full_names = {
        "内蒙古": "内蒙古自治区",
        "广西": "广西壮族自治区",
        "西藏": "西藏自治区",
        "宁夏": "宁夏回族自治区",
        "新疆": "新疆维吾尔自治区",
        "香港": "香港特别行政区",
        "澳门": "澳门特别行政区",
        "台湾": "台湾省",
    }
    aliases: Dict[str, str] = {}
    for key in PROVINCE_DEFAULT_CITY:
        aliases[key] = key
        aliases[f"{key}省"] = key
    for short, full in full_names.items():
        aliases[full] = short
        aliases[short] = short
    return aliases


PROVINCE_ALIASES: Dict[str, str] = _aliases()


def province_of(name: str) -> str:
    """把目的地名归一成**省域短名**（"四川"）；不是省 / 是直辖市时返回空串。

    返空串的含义是"不要走省域多锚点检索"：直辖市本身就是一个市域，
    拆成区去查只会把检索配额浪费在同一座城里。
    """
    text = str(name or "").strip()
    if not text:
        return ""
    key = PROVINCE_ALIASES.get(text)
    if key is None:
        for suffix in PROVINCE_SUFFIXES:
            if len(text) > len(suffix) and text.endswith(suffix):
                trimmed = text[: -len(suffix)]
                if trimmed in PROVINCE_ALIASES:
                    key = PROVINCE_ALIASES[trimmed]
                    break
    if not key or key in _NOT_PROVINCE_SCOPE:
        return ""
    return key


def short_region_name(name: str) -> str:
    """把地州全名压成短名：「阿坝藏族羌族自治州」→「阿坝」，「大兴安岭地区」→「大兴安岭」。"""
    text = str(name or "").strip()
    if not text:
        return ""
    cut = len(text)
    for token in _ETHNIC_TOKENS:
        index = text.find(token)
        # 只在该词出现在**名字后段**时切断：避免「内蒙古」这类前缀里也有族群词的名字被切空
        if 0 < index < cut:
            cut = index
    text = text[:cut]
    for suffix in _REGION_SUFFIXES:
        if len(text) > len(suffix) and text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def anchor_quota(days: int) -> Optional[int]:
    """短行程查几个地州：≤3 天 6 个、4-5 天 9 个、≥6 天全查（None = 全部）。"""
    days = max(1, int(days or 1))
    if days <= 3:
        return 6
    if days <= 5:
        return 9
    return None


def _coords(value: Any) -> List[float]:
    """center 可能是 "lng,lat" 字符串，也可能是 [lng, lat]；拿不到返回空列表。"""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return [float(value[0]), float(value[1])]
        except (TypeError, ValueError):
            return []
    text = str(value or "").strip()
    if "," not in text:
        return []
    lng_text, _, lat_text = text.partition(",")
    try:
        return [float(lng_text), float(lat_text)]
    except ValueError:
        return []


def normalize_districts(districts: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """把 `config/district` 的返回整理成 `{name, short, center, adcode}`（同名的只留一条）。"""
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in districts or []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append({
            "name": name,
            "short": short_region_name(name),
            "center": _coords(raw.get("center")),
            "adcode": str(raw.get("adcode") or ""),
        })
    return rows


def pick_anchors(
    districts: Sequence[Mapping[str, Any]],
    days: int,
    *,
    capital: str = "",
    spread_km: float = 120.0,
    weights: Optional[Mapping[str, float]] = None,
) -> List[Dict[str, Any]]:
    """选**这趟要查的地州**（省域覆盖的取数范围，不是行程）。

    优先级：① 省会（交通枢纽，也是用户最可能先到的地方）；② 静态旅游权重高的地州；
    ③ 地理分散 —— 已经选了一个片区，就不再把 120km 内的另一个地州也选进来
    （近邻地州的景区，通常第一个地州那次检索就已经覆盖到）。
    权重表只影响**短行程**先查谁；天数 ≥6 时全部返回，覆盖不依赖这张表。
    """
    rows = normalize_districts(districts)
    if not rows:
        return []
    table = weights if isinstance(weights, Mapping) else TOURISM_WEIGHT

    def _weight(row: Mapping[str, Any]) -> float:
        for key in (str(row.get("short") or ""), str(row.get("name") or "")):
            if key and key in table:
                return float(table[key])
        return 1.0

    ordered = sorted(rows, key=lambda row: (-_weight(row), str(row.get("name") or "")))
    quota = anchor_quota(days)
    if quota is None or quota >= len(ordered):
        selected = list(ordered)
    else:
        selected: List[Dict[str, Any]] = []
        capital_row = None
        for row in ordered:
            if capital and capital in (str(row.get("name") or ""), str(row.get("short") or "")):
                capital_row = row
                break
        if capital_row is not None:
            selected.append(capital_row)
        deferred: List[Dict[str, Any]] = []
        for row in ordered:
            if capital_row is not None and row is capital_row:
                continue
            if len(selected) >= quota:
                break
            if any(
                haversine_km(row.get("center") or [], existing.get("center") or []) <= spread_km
                and row.get("center") and existing.get("center")
                for existing in selected
            ):
                deferred.append(row)
                continue
            selected.append(row)
        # 分散选完还有名额：按权重补回来（宁可多查一个近邻地州，也别空着配额）
        for row in deferred:
            if len(selected) >= quota:
                break
            selected.append(row)
    # 省会在最前（取数顺序稳定，日志也好看）
    if capital:
        selected.sort(key=lambda row: 0 if capital in (str(row.get("name") or ""), str(row.get("short") or "")) else 1)
    return selected


def _centroid(cluster: Sequence[Mapping[str, Any]]) -> List[float]:
    points = [p for p in (_coords(item.get("lnglat")) for item in cluster) if p]
    if not points:
        return []
    return [sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)]


def cluster_places(
    items: Sequence[Mapping[str, Any]],
    *,
    threshold_km: float = 150.0,
) -> List[List[Dict[str, Any]]]:
    """把候选点按**地理邻近**分成片区（单链接贪心，够用且可解释）。

    150km 是"同一天能连起来玩"的量级：九寨沟 ↔ 成都 约 300km，会分成两片；
    都江堰 ↔ 成都 约 50km，落在同一片。没有坐标的点各自成一片（无法判断，就不猜）。
    """
    clusters: List[List[Dict[str, Any]]] = []
    centroids: List[List[float]] = []
    for raw in items or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        point = _coords(item.get("lnglat"))
        if not point:
            clusters.append([item])
            centroids.append([])
            continue
        placed = False
        for index, centre in enumerate(centroids):
            if not centre:
                continue
            if haversine_km(centre, point) <= threshold_km:
                clusters[index].append(item)
                points = [
                    p for p in (_coords(existing.get("lnglat")) for existing in clusters[index])
                    if p
                ]
                centroids[index] = [
                    sum(p[0] for p in points) / len(points),
                    sum(p[1] for p in points) / len(points),
                ]
                placed = True
                break
        if not placed:
            clusters.append([item])
            centroids.append(point)
    return clusters


def choose_region_line(
    items: Sequence[Mapping[str, Any]],
    *,
    line_km: float = 80.0,
    span_trigger_km: float = 180.0,
) -> Dict[str, Any]:
    """把**一个地州**的候选拆成"一天能串起来"的子片区，只留这趟真正能走的那条线。

    为什么需要（2026-11-02 实测，四川 4 天）：阿坝一个州的候选里同时有九寨沟（33.1N）
    和四姑娘山（31.1N），直线相隔约 250km —— 分到 2 天就变成"第 3 天 451km、
    第 4 天 209km 却只排 4 个点"。**覆盖全省 ≠ 几天内跑完一个州的每条线**：
    候选检索仍覆盖全州，行程只走其中一条，其余方向由调用方明确告知用户。

    ⚠️ 不能用单链接聚类（2026-11-02 第二次实测）：阿坝沿山路从四姑娘山 → 毕棚沟 →
    玛嘉沟 → 孟屯河谷 → 羊茸村 → 红桦林，**相邻两点的间距都在 20~70km**，
    单链接会把它们串成一条 ~140km 的"线"整条保留 —— 于是第 3 天仍是 257km。
    "一天能串起来"要看**这一组点的最大两两间距**，而不是相邻间距。

    判定顺序：
      1. 最大跨距 ≤ `span_trigger_km` → 不拆（本来就当一条线）；
      2. 以每个候选为种子**贪心扩线**（按适应度依次纳入、且纳入后组内最大两两间距
         仍 ≤ `line_km`），取（候选数, 适应度之和）最高的一组；
      3. 最佳组 ≥2 个候选才拆（全是孤点说明彼此都不相邻，留哪个都没有取舍意义）。
    `line_km` 是**组内最大两两间距**（默认 80km）：一组点按最近邻串起来通常在 100~160km，
    正好落在当天里程预算（160km，含 10% 宽限 176km）里。取 100km 时实测仍会排出
    193km 的一天（羊茸村 → 毕棚沟 → 玛嘉沟 三个方向各跑一趟）。
    没有坐标的候选不猜位置，原样保留（下游按地州兜底归片）。
    """
    rows = [dict(item) for item in items or [] if isinstance(item, Mapping)]
    if len(rows) < 2:
        return {
            "applied": False, "kept": rows, "dropped": [], "span_km": 0.0,
            "kept_label": "", "dropped_label": "", "cluster_count": 1,
        }
    located: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    for row in rows:
        point = _coords(row.get("lnglat") or row.get("location"))
        if point:
            row["lnglat"] = point
            located.append(row)
        else:
            unknown.append(row)

    span_km = 0.0
    for index, left in enumerate(located):
        for right in located[index + 1:]:
            distance = haversine_km(left["lnglat"], right["lnglat"])
            if distance == distance:  # 排除 nan
                span_km = max(span_km, float(distance))
    if span_km <= span_trigger_km:
        return {
            "applied": False, "kept": rows, "dropped": [], "span_km": round(span_km, 1),
            "kept_label": "", "dropped_label": "", "cluster_count": 1,
        }

    def _fitness(item: Mapping[str, Any]) -> float:
        return float(item.get("fitness_score") or 0)

    # 每个候选轮流当种子，按适应度从高到低贪心扩线：只有当新点与**组内所有点**
    # 的间距都 ≤ line_km 时才纳入（即组内最大两两间距受控，避免沿山路串成一条长线）。
    best: List[Dict[str, Any]] = []
    for seed in located:
        group = [seed]
        for candidate in sorted(located, key=_fitness, reverse=True):
            if candidate is seed:
                continue
            if all(
                haversine_km(candidate["lnglat"], member["lnglat"]) <= line_km
                for member in group
            ):
                group.append(candidate)
        if len(group) > len(best) or (
            len(group) == len(best) and sum(_fitness(item) for item in group) > sum(_fitness(item) for item in best)
        ):
            best = group

    if len(best) < 2:
        return {
            "applied": False, "kept": rows, "dropped": [], "span_km": round(span_km, 1),
            "kept_label": "", "dropped_label": "", "cluster_count": len(located),
        }

    kept_names = {str(item.get("name") or "") for item in best}
    unknown_ids = {id(row) for row in unknown}
    kept = [row for row in rows if id(row) in unknown_ids or str(row.get("name") or "") in kept_names]
    dropped = [row for row in rows if id(row) not in unknown_ids and str(row.get("name") or "") not in kept_names]

    def _label(cluster: Sequence[Mapping[str, Any]]) -> str:
        ranked = sorted(cluster, key=lambda item: -float(item.get("fitness_score") or 0))
        names = [str(item.get("name") or "") for item in ranked if str(item.get("name") or "")]
        return "、".join(names[:2]) + ("方向" if len(names) >= 2 else "")

    # 被丢掉的方向按地理邻近再分一次组，只为把说明写清楚（不参与保留判断）
    dropped_located = [row for row in dropped if id(row) not in unknown_ids]
    dropped_groups = cluster_places(dropped_located, threshold_km=line_km) if dropped_located else []
    dropped_groups.sort(key=lambda group: -len(group))
    return {
        "applied": True,
        "kept": kept,
        "dropped": dropped,
        "span_km": round(span_km, 1),
        "kept_label": _label(best),
        "dropped_label": "、".join(_label(group) for group in dropped_groups),
        "cluster_count": 1 + len(dropped_groups),
    }


def assign_cluster_days(
    clusters: Sequence[Sequence[Mapping[str, Any]]],
    days: int,
    daily_nodes: int = 3,
    *,
    start_cluster: Optional[int] = None,
    max_regions: Optional[int] = None,
) -> Dict[str, Any]:
    """把 `days` 天分给各片区（B 的编排输入）：同一天只在一个片区，跨片区整段移动。

    分配口径：
      · 用到的片区数 = min(片区数, 天数)（一天跑不完两个片区）；
      · 每个用到的片区**至少 1 天**，剩余天数按"候选供给"权重补给；
      · 片区顺序从起点片区（通常是省会所在片）开始，再按邻近串接，减少来回横跳。
    返回 `day_to_cluster`（天 → 片区下标，天从 1 开始）、`cluster_days`、`order`。
    """
    days = max(1, int(days or 1))
    daily_nodes = max(1, int(daily_nodes or 1))
    usable = [(index, list(cluster)) for index, cluster in enumerate(clusters or []) if cluster]
    if not usable:
        return {"day_to_cluster": [-1] * days, "cluster_days": {}, "order": [], "used_clusters": 0}

    def _supply(cluster: Sequence[Mapping[str, Any]]) -> int:
        # 一个片区最多占满整趟的天数需要量，避免 50 个点的片区把其它片区挤光
        return min(len(cluster), days * daily_nodes)

    weights = {index: max(1, _supply(cluster)) for index, cluster in usable}
    used_count = min(len(usable), days)
    if max_regions is not None:
        # 省域长途场景再收紧：别天天换片区（4 天 2 片、5 天 3 片）
        used_count = min(used_count, max(1, int(max_regions)))
    ranked = sorted(usable, key=lambda pair: (-weights[pair[0]], pair[0]))
    chosen = [index for index, _ in ranked[:used_count]]
    if start_cluster is not None and start_cluster in {index for index, _ in usable} and start_cluster not in chosen:
        # 起点片区必须在行程里（用户落地就在那儿）
        chosen = chosen[: max(0, used_count - 1)] + [start_cluster]

    days_by_cluster = {index: 1 for index in chosen}
    remaining = days - len(chosen)
    while remaining > 0:
        # 每次补给"当前每天供给最充足"的片区（权重/已分天数），避免无限堆在一个片区
        target = max(chosen, key=lambda index: (weights[index] / days_by_cluster[index], -days_by_cluster[index]))
        days_by_cluster[target] += 1
        remaining -= 1

    # 片区顺序：起点 → 最近邻串接
    centres = {index: _centroid(cluster) for index, cluster in usable}
    order: List[int] = []
    if start_cluster in days_by_cluster:
        order.append(start_cluster)
    elif chosen:
        first = min(
            (index for index in chosen if centres.get(index)),
            key=lambda index: centres[index][0],
            default=chosen[0],
        )
        order.append(first)
    while len(order) < len(chosen):
        last_centre = centres.get(order[-1]) if order else []
        pending = [index for index in chosen if index not in order]
        if last_centre:
            nearest = min(
                pending,
                key=lambda index: haversine_km(last_centre, centres.get(index) or last_centre),
            )
        else:
            nearest = pending[0]
        order.append(nearest)

    day_to_cluster: List[int] = []
    for index in order:
        day_to_cluster.extend([index] * days_by_cluster[index])
    day_to_cluster = day_to_cluster[:days]
    return {
        "day_to_cluster": day_to_cluster,
        "cluster_days": dict(days_by_cluster),
        "order": order,
        "used_clusters": len(chosen),
    }


def estimate_rail_hours(
    origin: Any,
    destination: Any,
    *,
    speed_kmh: float = 200.0,
    overhead_hours: float = 0.5,
) -> Optional[float]:
    """两个坐标之间的**高铁耗时估算**（明确是估算：直线距离 / 200km/h + 0.5h 进出站）。

    为什么要估算而不是查：`direction/transit` 只覆盖同城公交，跨城高铁要另外的供应商；
    而在编排阶段我们只需要判断"这一天跨片区是否现实"（3 小时 vs 8 小时的区别）。
    真实出发前仍要用户按 12306 确认 —— 这里给出的数字绝不写成"实时班次"。
    """
    a = _coords(origin)
    b = _coords(destination)
    if not a or not b:
        return None
    km = haversine_km(a, b)
    if km != km:  # nan
        return None
    hours = float(km) / max(1.0, float(speed_kmh)) + float(overhead_hours)
    return round(hours, 1)


def province_scope_note(province: str, anchors: Sequence[Mapping[str, Any]], days: int) -> str:
    """给日志/审计用的一句话：这次省域覆盖查了哪些地州。"""
    names = [str(anchor.get("name") or "") for anchor in anchors or []]
    if not names:
        return f"{province} 省域覆盖：没有拿到下属地州列表（这一轮仍按省会检索）"
    return (
        f"{province} 省域覆盖（{days} 天）：检索 {len(names)} 个地州 —— "
        + "、".join(names)
    )


def _node_point(node: Mapping[str, Any]) -> List[float]:
    """节点的 [lng, lat]；`(0, 0)` 视为没有坐标（前端/里程口径一致）。"""
    for key in ("lnglat", "location", "coord"):
        point = _coords(node.get(key))
        if point and not (point[0] == 0.0 and point[1] == 0.0):
            return point
    return []


def _node_key(node: Mapping[str, Any]) -> str:
    return "".join(str(node.get("name") or node.get("location") or "").lower().split())


def _node_day(node: Mapping[str, Any]) -> int:
    try:
        return int(node.get("day") or 1)
    except (TypeError, ValueError):
        return 1


def _leg_sum(nodes: Sequence[Mapping[str, Any]]) -> float:
    """一天内的移动里程（与 `travel_feasibility._leg_km` 同一口径：不含跨片区那段）。"""
    total = 0.0
    previous: Optional[List[float]] = None
    for node in nodes:
        point = _node_point(node)
        if not point:
            continue
        if previous is not None:
            total += haversine_km(previous, point)
        previous = point
    return round(total, 1)


def _nearest_chain(
    items: Sequence[Mapping[str, Any]], start: Optional[Sequence[float]] = None
) -> List[Dict[str, Any]]:
    """按最近邻串成一条线（没有坐标的点排到最后，无法判断就不猜）。"""
    located = [dict(item) for item in items if _node_point(item)]
    unlocated = [dict(item) for item in items if not _node_point(item)]
    chain: List[Dict[str, Any]] = []
    cursor: Optional[List[float]] = list(start) if start and len(list(start)) >= 2 else None
    if cursor is None and located:
        cursor = _node_point(located[0])
    remaining = located
    while remaining:
        index = min(
            range(len(remaining)),
            key=lambda i: haversine_km(cursor or _node_point(remaining[i]), _node_point(remaining[i])),
        )
        picked = remaining.pop(index)
        chain.append(picked)
        cursor = _node_point(picked)
    return chain + unlocated


def _group_travel_km(
    group: Sequence[Mapping[str, Any]],
    hotel_rows: Sequence[Mapping[str, Any]] = (),
    previous_hotel_rows: Sequence[Mapping[str, Any]] = (),
) -> float:
    """一天的移动里程：早上从上一晚酒店出发 + 组内各段 + 回当晚酒店。

    对外报的 `travel_km` 只看组内各段（`travel_feasibility` 同口径，不含早上出发那段），
    但切天时必须把早上那段算进来：只算组内会挑出"Day 3 只有 1 个点、Day 4 串 5 个点"
    这种看着便宜、实际第二天一早要再开 60~80km 的切法（实测 2026-11-02 四川：
    1+5 的切法组内仅 109km，实际第二天是 171km）。
    """
    total = _leg_sum(list(group) + list(hotel_rows))
    if group and previous_hotel_rows:
        previous = next(
            (point for point in (_node_point(row) for row in previous_hotel_rows) if point), None
        )
        first = _node_point(group[0])
        if previous and first:
            total += haversine_km(previous, first)
    return total


def _best_split(
    chain: Sequence[Mapping[str, Any]],
    groups: int,
    hotels_by_group: Optional[Sequence[Sequence[Mapping[str, Any]]]] = None,
) -> List[List[Mapping[str, Any]]]:
    """把一条地理线切成 `groups` 段，让**最赶的一天**里程最小（DP，穷举切点）。

    单看"组内最大两两间距"不够：实测阿坝 4 个景区两两都 ≤80km，但串起来 161km，
    全放一天就是 250km。这里直接按"组内路径长度"切。

    `hotels_by_group` 给了就把当天住宿接在该组末尾一起算里程 —— 住宿是**不能搬**的，
    只按白天点算会选出"当天跑 45km、但要从段尾再开 79km 回酒店"的那种切法
    （实测 2026-11-02 四川：Day 3 判成 209km，Day 4 只剩 2 个点）。
    """
    n = len(chain)
    groups = max(1, int(groups))
    hotels = [list(rows or []) for rows in (hotels_by_group or [])]
    if n == 0:
        return []
    if groups <= 1 or n <= groups:
        # 天数 ≥ 点数：每个点各占一天（空的天留给下游从片区候选里补）
        return [[node] for node in chain] + [[] for _ in range(groups - n)]
    max_size = max(1, -(-n // groups) + 1)
    infinity = float("inf")
    dp = [[infinity] * (groups + 1) for _ in range(n + 1)]
    cut = [[-1] * (groups + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(1, n + 1):
        for g in range(1, min(groups, i) + 1):
            for j in range(max(g - 1, i - max_size), i):
                if dp[j][g - 1] == infinity:
                    continue
                rows = list(chain[j:i])
                cost = max(
                    dp[j][g - 1],
                    _group_travel_km(
                        rows,
                        hotels[g - 1] if g - 1 < len(hotels) else [],
                        hotels[g - 2] if g >= 2 and g - 2 < len(hotels) else [],
                    ),
                )
                if cost < dp[i][g]:
                    dp[i][g] = cost
                    cut[i][g] = j
    if dp[n][groups] == infinity:  # 兜底：按人数均分
        base, remainder = divmod(n, groups)
        result, cursor = [], 0
        for index in range(groups):
            size = base + (1 if index < remainder else 0)
            result.append(list(chain[cursor:cursor + size]))
            cursor += size
        return result
    result: List[List[Mapping[str, Any]]] = []
    i, g = n, groups
    while g > 0:
        j = cut[i][g]
        if j < 0:
            j = max(0, i - 1)
        result.append(list(chain[j:i]))
        i, g = j, g - 1
    return list(reversed(result))


def _playable(node: Mapping[str, Any], category_of: Callable[[Mapping[str, Any]], str]) -> bool:
    return category_of(node) in ("scenic", "cultural")


def redistribute_province_days(
    route: Sequence[Mapping[str, Any]],
    *,
    day_regions: Mapping[int, str],
    region_of: Callable[[Mapping[str, Any]], str],
    days: int,
    category_of: Optional[Callable[[Mapping[str, Any]], str]] = None,
    pool: Sequence[Mapping[str, Any]] = (),
    budget_km: float = 176.0,
    region_allow: Optional[Mapping[str, Sequence[str]]] = None,
) -> Dict[str, Any]:
    """按"哪天在哪片"把同一片区的白天节点重新分摊到该片区的每一天（方案 1）。

    为什么需要：省域覆盖只保证"候选里有全省的地州"，并不保证模型会按天分摊。
    实测（2026-11-02 四川 4 天，LLM 主推演路线）：第 3 天把阿坝 4 个景区全排进去
    （单日 251km），第 4 天计划在阿坝却排了 4 个成都餐饮 —— 没有玩点、也没去阿坝。
    `repair_province_overload` 只能裁"一天串进多个片区"，对"整天都在别的片区"无能为力。

    做法（确定性、只用真实节点与候选）：
      · 只处理**连续 ≥2 天、同一片区**的块；
      · 块内白天节点按地理最近邻串成一条线，用 DP 切成 k 段（让最赶的一天最短），
        再把玩点补给缺玩点的那天；
      · 块内**完全在别的片区**的白天节点移出（不让"计划在阿坝"实际排成成都）；
      · 移出的名额从候选池补同片区的点（缺就如实少排，不编造）；
      · 住宿留在原来的天（夜数与顺序由上游定，这里不搬）。
    块内两天本来就合规（都在本片区、每天有玩点、单日不超预算）时**不动**，避免无谓抖动。
    """
    if category_of is None:
        from .itinerary_balance import poi_category as category_of

    planned = {
        int(key): str(value).strip()
        for key, value in dict(day_regions or {}).items()
        if str(value or "").strip() and 1 <= int(key) <= max(1, int(days or 1))
    }
    if not planned:
        return {"route": list(route or []), "applied": False, "notes": [], "dropped": [], "added": [], "days": []}

    def _region(node: Mapping[str, Any]) -> str:
        try:
            return str(region_of(node) or "").strip()
        except Exception:
            return ""

    by_day: Dict[int, List[Dict[str, Any]]] = {}
    for raw in route or []:
        if not isinstance(raw, Mapping) or not _node_key(raw):
            continue
        by_day.setdefault(_node_day(raw), []).append(dict(raw))

    blocks: List[tuple[str, List[int]]] = []
    for day in sorted(planned):
        region = planned[day]
        if blocks and blocks[-1][0] == region and blocks[-1][1][-1] == day - 1:
            blocks[-1][1].append(day)
        else:
            blocks.append((region, [day]))

    used_keys = {_node_key(item) for rows in by_day.values() for item in rows}
    notes: List[str] = []
    dropped: List[Dict[str, Any]] = []
    added: List[Dict[str, Any]] = []
    report_days: List[Dict[str, Any]] = []
    applied = False
    last_point: Optional[List[float]] = None

    for region, block_days in blocks:
        block_nodes = [item for day in block_days for item in by_day.get(day, [])]
        hotels_by_day = {
            day: [item for item in by_day.get(day, []) if category_of(item) == "hotel"]
            for day in block_days
        }
        daytime = [item for item in block_nodes if category_of(item) != "hotel"]
        in_region: List[Dict[str, Any]] = []
        block_dropped: List[Dict[str, Any]] = []
        for item in daytime:
            node_region = _region(item)
            if node_region and node_region != region:
                block_dropped.append(item)
            else:
                in_region.append(item)

        # 合规就不动：都在本片区、每天有玩点、单日不超预算
        needs_split = bool(block_dropped)
        block_has_playable = any(_playable(item, category_of) for item in in_region)
        for day in block_days:
            members = [item for item in in_region if _node_day(item) == day]
            if members and block_has_playable and not any(_playable(item, category_of) for item in members):
                needs_split = True
            if _leg_sum(members + hotels_by_day.get(day, [])) > float(budget_km):
                needs_split = True
        if len(block_days) < 2 or not needs_split:
            for day in block_days:
                day_nodes = [item for item in in_region if _node_day(item) == day] + hotels_by_day.get(day, [])
                report_days.append(
                    {
                        "day": day,
                        "region": region,
                        "nodes": len(day_nodes),
                        "travel_km": _leg_sum(day_nodes),
                    }
                )
                located = [item for item in day_nodes if _node_point(item)]
                if located:
                    last_point = _node_point(located[-1])
            continue

        # 移出的名额从候选池补同片区的点（玩点优先），补不到就少排
        block_added: List[Dict[str, Any]] = []
        if block_dropped and pool:
            # 子片区收敛已经对用户说过"另一条线这次不排"，补点就不能把它再塞回来。
            keep_names = None
            if region_allow is not None:
                allowed = region_allow.get(region)
                if allowed:
                    keep_names = {_node_key({"name": str(name)}) for name in allowed}
            candidates = [
                dict(item)
                for item in pool
                if isinstance(item, Mapping)
                and _node_key(item)
                and _node_key(item) not in used_keys
                and category_of(item) != "hotel"
                and _region(item) == region
                and (
                    keep_names is None
                    or category_of(item) != "scenic"
                    or _node_key(item) in keep_names
                )
            ]
            candidates.sort(
                key=lambda item: (
                    0 if _playable(item, category_of) else 1,
                    0 if category_of(item) == "food" else 1,
                    _node_key(item),
                )
            )
            for item in candidates[: len(block_dropped)]:
                used_keys.add(_node_key(item))
                block_added.append(item)

        # 片区内的线和切分：默认起点接上一段的末尾（跨片区那天本就是整段移动）。
        # 也试一次"从本块第一天的酒店出发"：住宿是段尾那块锚点，按上一段末尾起线会把
        # 住在四姑娘山的那晚切到毕棚沟/孟屯那天（实测 2026-11-02 四川：Day 3 209km、
        # Day 4 只有 2 个点）。两种起线都算一遍，取"最赶一天最短"的那个。
        hotels_by_group = [hotels_by_day.get(day) or [] for day in block_days]
        first_hotel_point: Optional[List[float]] = None
        for row in hotels_by_group[0]:
            point = _node_point(row)
            if point:
                first_hotel_point = point
                break

        best_groups: List[List[Mapping[str, Any]]] = []
        best_score: Tuple[float, float] = (float("inf"), float("inf"))
        for start in (last_point, first_hotel_point):
            chain = _nearest_chain(list(in_region) + block_added, start=start)
            candidate = _best_split(chain, len(block_days), hotels_by_group=hotels_by_group)
            costs = [
                _group_travel_km(
                    candidate[index],
                    hotels_by_group[index] if index < len(hotels_by_group) else [],
                    hotels_by_group[index - 1] if index > 0 and index - 1 < len(hotels_by_group) else [],
                )
                for index in range(len(candidate))
            ]
            score = (
                round(max(costs), 1) if costs else 0.0,
                round(sum(costs), 1),
            )
            if score < best_score:
                best_score, best_groups = score, candidate
        groups = best_groups

        # 玩点补给：某天一个玩点都没有、别的天有两个以上，就挪最近的一个过去
        for index, group in enumerate(groups):
            if any(_playable(item, category_of) for item in group):
                continue
            donors = [
                (other, item)
                for other, rows in enumerate(groups)
                if other != index
                for item in rows
                if _playable(item, category_of)
            ]
            for other, item in donors:
                if sum(1 for row in groups[other] if _playable(row, category_of)) <= 1:
                    continue
                groups[other].remove(item)
                group.append(item)
                notes.append(f"第 {block_days[index]} 天没有玩点，从第 {block_days[other]} 天挪来「{item.get('name')}」")
                break

        for index, day in enumerate(block_days):
            rows = groups[index] if index < len(groups) else []
            for item in rows:
                item["day"] = day
            by_day[day] = rows + list(hotels_by_day.get(day, []))
            report_days.append(
                {
                    "day": day,
                    "region": region,
                    "nodes": len(by_day[day]),
                    "travel_km": _leg_sum(by_day[day]),
                }
            )
            located = [item for item in by_day[day] if _node_point(item)]
            if located:
                last_point = _node_point(located[-1])
        applied = True
        dropped.extend(block_dropped)
        added.extend(block_added)
        notes.append(
            f"{region} 占 {len(block_days)} 天：按地理线重排为 "
            + "、".join(
                f"Day {day} {len(by_day.get(day, []))} 个点（约 {_leg_sum(by_day.get(day, [])):.0f}km）"
                for day in block_days
            )
            + (f"；移出跨片区点 {len(block_dropped)} 个" if block_dropped else "")
        )

    if not applied:
        return {"route": list(route or []), "applied": False, "notes": [], "dropped": [], "added": [], "days": []}

    output: List[Dict[str, Any]] = []
    for day in sorted(by_day):
        output.extend(by_day[day])
    return {
        "route": output,
        "applied": True,
        "notes": notes,
        "dropped": [{"name": str(item.get("name") or ""), "day": _node_day(item)} for item in dropped],
        "added": [{"name": str(item.get("name") or ""), "day": _node_day(item)} for item in added],
        "days": report_days,
    }
