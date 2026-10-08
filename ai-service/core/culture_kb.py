"""项目内**可信文化知识库**（P1：文化智能体的唯一数据源）。

为什么是"项目内维护的少量条目"而不是抓取
----------------------------------------
文化背景这类东西**错了比没有更糟**：编一个"某某节庆每年三月举行"看起来很像真的，
用户会照着安排行程。而这类内容又没有统一定价页那样可机读的官方结构化数据，
四处抓来的说法质量参差、也难核实到具体出处。

所以这里只放**人工维护、逐条写明出处**的条目，并且：

- 每条都必须有 `sources`（来源名 + URL）与 `reviewed_at`（人工复核日期）；
- **没有条目就是没有**：文化智能体返回 `degraded + omit_unsourced_fact`，
  绝不用模型知识补一段"一般来说……"；
- 条目里**不写**会变的数字（票价、开放时间、电话）——那些属于别的通道，
  写进来就会过期、就会有第二个"真相源"。

可信度分级（与 P3 `fact_trust` 一致）
------------------------------------
知识库不是一手来源 → 统归 `knowledge_base`（→ `estimated`），
**不会**冒充"已核实"。前端会显示成"估算 · 项目内可信知识库"。

怎么加条目
----------
在 `CULTURE_ENTRIES` 里按下面的字段加，`reviewed_at` 写你**真的看过**出处的日期。
新条目会被 `tests/test_culture_kb.py` 校验：必须是真 URL、必须有复核日期、
不许出现票价/时间这类易变数字。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

#: 人工复核日期（整库最近一次复核）—— 前端"更新时间"显示它
KB_REVIEWED_AT = "2026-10-08"

#: 知识库版本：条目变化时递增，便于方案里追溯"当时用的是哪一版"
KB_VERSION = "culture-kb-1"

#: 哪个字段算"易变数字"，条目里不许出现（写进来就会过期，且有第二个真相源）
_VOLATILE_PATTERNS = (
    re.compile(r"\d+\s*元"),
    re.compile(r"[¥￥]\s*\d+"),
    re.compile(r"\d{1,2}:\d{2}"),
    re.compile(r"\d+\s*月\s*\d+\s*日"),
)

CULTURE_ENTRIES: List[Dict[str, Any]] = [
    {
        "name": "喀什古城",
        "aliases": ["喀什老城", "喀什古城景区", "老城景区"],
        "city_hints": ["喀什"],
        "background": (
            "喀什古城是我国唯一保存完整的以伊斯兰文化为特色的迷宫式城市街区，"
            "街巷以艾提尕尔清真寺为中心向外放射，生土建筑依地势层层叠叠。"
            "老城民居的「过街楼」「半街楼」是应对狭窄巷道的传统做法。"
        ),
        "etiquette": (
            "进入居民区拍照前先征得同意（不少院落是住家）；"
            "清真寺周边着装宜端庄；当地人递茶递食物用双手接更得体。"
        ),
        "recommended_stay": "半天到一天：主街 2~3 小时，进小巷与手工作坊再加 2~3 小时",
        "sources": [
            {"label": "喀什地区行政公署（喀什古城介绍）", "url": "http://www.kashi.gov.cn/"},
            {"label": "新疆维吾尔自治区文化和旅游厅", "url": "http://wlt.xinjiang.gov.cn/"},
        ],
        "reviewed_at": KB_REVIEWED_AT,
    },
    {
        "name": "香妃园",
        "aliases": ["阿帕霍加麻扎", "香妃墓"],
        "city_hints": ["喀什"],
        "background": (
            "阿帕霍加麻扎（俗称香妃墓）是阿帕霍加家族的陵墓，"
            "建筑以绿色琉璃砖与白底蓝花琉璃装饰，是新疆伊斯兰建筑的代表作之一。"
            "民间因乾隆容妃（香妃）的传说而熟知这个名字。"
        ),
        "etiquette": "陵墓属宗教场所，保持安静、勿攀爬墓碑；拍照前留意现场提示。",
        "recommended_stay": "1.5~2 小时",
        "sources": [
            {"label": "喀什地区行政公署", "url": "http://www.kashi.gov.cn/"},
        ],
        "reviewed_at": KB_REVIEWED_AT,
    },
    {
        "name": "巴音布鲁克景区",
        "aliases": ["巴音布鲁克", "巴音布鲁克草原", "天鹅湖景区"],
        "city_hints": ["库尔勒", "巴州", "和静", "巴音郭楞"],
        "background": (
            "巴音布鲁克蒙古语意为「富饶的泉水」，是天山山脉中段的高山盆地草原，"
            "开都河在此形成著名的「九曲十八弯」。这里也是天鹅等水禽的繁殖地，"
            "设有天鹅自然保护区。草原海拔高、昼夜温差大。"
        ),
        "etiquette": (
            "草原与湿地是牧区与保护区：车辆走既有道路、不碾压草场；"
            "遇到牧民牲畜减速让行；垃圾随身带走。"
        ),
        "recommended_stay": "半天（含区间车与观景台）；看九曲十八弯日落建议留到傍晚",
        "sources": [
            {"label": "巴音布鲁克景区官网", "url": "https://www.byblk.cn/"},
            {"label": "巴音郭楞蒙古自治州人民政府", "url": "http://www.xjbz.gov.cn/"},
        ],
        "reviewed_at": KB_REVIEWED_AT,
    },
    {
        "name": "罗布人村寨",
        "aliases": ["罗布人村寨景区"],
        "city_hints": ["库尔勒", "巴州", "尉犁", "巴音郭楞"],
        "background": (
            "罗布人村寨位于塔里木河与渭干河交汇一带，以「罗布人」（世代生活在"
            "塔里木河下游、以渔猎为生的族群）的民俗文化为主题，"
            "景区内可见胡杨、沙丘、河道并存的干旱区景观。"
        ),
        "etiquette": "胡杨林与沙丘生态脆弱，按指定路线行走；民俗展示区拍照先征询。",
        "recommended_stay": "半天",
        "sources": [
            {"label": "罗布人村寨官网", "url": "https://www.xjlbr.com/"},
        ],
        "reviewed_at": KB_REVIEWED_AT,
    },
    {
        "name": "伊犁河旅游风景区",
        "aliases": ["伊犁河", "伊犁河大桥"],
        "city_hints": ["伊宁", "伊犁"],
        "background": (
            "伊犁河是新疆水量最丰的内陆河之一，伊宁市段沿河建有景观带，"
            "当地有在河畔看日落、举办婚礼拍照的习俗。"
        ),
        "etiquette": "河畔观景带人多，注意骑行道与步行道分流；垃圾入箱。",
        "recommended_stay": "1~2 小时（傍晚最佳）",
        "sources": [
            {"label": "伊犁州人民政府", "url": "http://www.xjyl.gov.cn/"},
        ],
        "reviewed_at": KB_REVIEWED_AT,
    },
    {
        "name": "天鹅泉湿地公园",
        "aliases": ["天鹅泉", "伊宁县天鹅泉"],
        "city_hints": ["伊宁", "伊犁"],
        "background": (
            "天鹅泉是伊犁河谷的冬季湿地，因地下温泉不冻，"
            "每年冬季有疣鼻天鹅等在此越冬，是当地重要的观鸟点。"
        ),
        "etiquette": "观鸟保持距离、勿投喂、勿用闪光灯；沿木栈道行走。",
        "recommended_stay": "2~3 小时（冬季清晨观鸟最佳）",
        "sources": [
            {"label": "伊犁州文化和旅游局（伊犁文旅）", "url": "http://ylwl.xjyl.gov.cn/"},
        ],
        "reviewed_at": KB_REVIEWED_AT,
    },
]


def _normalize(text: Any) -> str:
    """去掉常见后缀，便于"香妃墓"匹配到"香妃园"这类写法。"""
    value = str(text or "").strip()
    for suffix in ("风景名胜区", "景区", "公园", "博物馆", "古城", "老城"):
        if value.endswith(suffix) and len(value) > len(suffix):
            value = value[: -len(suffix)]
    return value


def culture_entry_for(name: Any, city: Any = "") -> Optional[Dict[str, Any]]:
    """按名字（可带城市提示）查一条文化条目；查不到返回 None（**不猜**）。"""
    target = _normalize(name)
    if not target:
        return None
    city_text = str(city or "").strip()
    fallback: Optional[Dict[str, Any]] = None
    for entry in CULTURE_ENTRIES:
        names = [str(entry.get("name") or "")] + [str(item) for item in entry.get("aliases") or []]
        for candidate in names:
            if not candidate:
                continue
            normalized = _normalize(candidate)
            if normalized == target or (len(target) >= 3 and target in normalized) or (
                len(normalized) >= 3 and normalized in target
            ):
                hints = [str(item) for item in entry.get("city_hints") or []]
                if not city_text or not hints:
                    return entry
                if any(hint and hint in city_text for hint in hints):
                    return entry
                if fallback is None:
                    fallback = entry
                break
    return fallback


def culture_fact(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """把条目整理成"带出处的文化事实"，字段名与前端约定一致。"""
    sources = [dict(item) for item in entry.get("sources") or [] if isinstance(item, Mapping)]
    return {
        "name": str(entry.get("name") or ""),
        "background": str(entry.get("background") or ""),
        "etiquette": str(entry.get("etiquette") or ""),
        "recommended_stay": str(entry.get("recommended_stay") or ""),
        "sources": sources,
        # 知识库不是一手来源 → 归 knowledge_base（前端显示"估算 · 项目内可信知识库"）
        "source_type": "knowledge_base",
        "reviewed_at": str(entry.get("reviewed_at") or KB_REVIEWED_AT),
        "kb_version": KB_VERSION,
    }


def unsourced_note(name: Any) -> Dict[str, Any]:
    """查不到条目时给用户的**如实说明**（而不是编一段背景）。"""
    return {
        "name": str(name or ""),
        "background": "",
        "etiquette": "",
        "recommended_stay": "",
        "sources": [],
        "source_type": "none",
        "reviewed_at": "",
        "kb_version": KB_VERSION,
        "note": "项目内可信知识库里没有这个地点的文化条目，未提供背景说明（不编造）",
    }


def has_volatile_numbers(text: Any) -> bool:
    """条目里出现了易变数字（票价/时刻/日期）—— 供测试把关。"""
    value = str(text or "")
    return any(pattern.search(value) for pattern in _VOLATILE_PATTERNS)


__all__ = [
    "CULTURE_ENTRIES",
    "KB_REVIEWED_AT",
    "KB_VERSION",
    "culture_entry_for",
    "culture_fact",
    "has_volatile_numbers",
    "unsourced_note",
]
