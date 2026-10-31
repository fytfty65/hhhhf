"""User-input parsing and small text/reasoning helpers.

Extracted from ``api/agent.py`` (2026-10-10) as the second batch of that module's
decomposition. ``agent.py`` was ~7,000 lines with a single request function of
several thousand lines, so every edit carried the whole service's blast radius.
The functions here were selected by measurement rather than taste: an AST scan
found them free of intra-module dependencies (their only references are the stdlib
and nothing else in ``agent``), which is what makes moving them safe and
mechanical.

They are re-exported from ``api.agent`` so no caller changes. Prefer importing
from here in new code.

Contents:
  - ``parse_chinese_days`` / ``parse_budget_amount``: user-text parsing for trip
    length and budget, including the "人均/每天/万" forms users actually type.
  - ``extract_preference_signals``: preference extraction with the negation and
    "nature-focus" handling that several past bugs were about.
  - ``safe_repair_and_parse_json``: tolerant parsing of model-emitted JSON.
  - ``build_reasoning_step``: one structured reasoning entry for the stream.
  - ``_static_map_url``: Amap static-map URL used as a never-blank image fallback.
  - ``_fairness_limit_from_env``: the group fairness stddev limit.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any, Dict, List


def _fairness_limit_from_env() -> float:
    """Read the group fairness standard deviation limit defensively."""
    try:
        return max(0.0, float(os.getenv("PLANNING_FAIRNESS_MAX_STD", "0.15")))
    except (TypeError, ValueError):
        return 0.15


def parse_chinese_days(text: str) -> int:
    """
    智能解析用户输入中的游玩天数，支持 '3天', '三天', '五日游', '两天一夜', '七天大环线' 等表述
    """
    if not text:
        return 3

    cn_num_map = {
        '一': 1, '二': 2, '两': 2, '仨': 3, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15
    }

    # 优先匹配阿拉伯数字：如 "3天", "5日", "7天6晚"
    match_digit = re.search(r'(\d+)\s*[天日]', text)
    if match_digit:
        val = int(match_digit.group(1))
        return max(1, min(15, val))

    # 匹配中文连词复合表达：如 "两天一夜" -> 2天, "三天两晚" -> 3天
    match_compound = re.search(r'([一二两仨三四五六七八九十\d]+)\s*天\s*[一二两仨三四五六七八九十\d]+\s*[晚夜]', text)
    if match_compound:
        val_str = match_compound.group(1)
        if val_str.isdigit():
            return max(1, min(15, int(val_str)))
        return cn_num_map.get(val_str, 3)

    # 匹配中文单个数字：如 "三天", "五天", "两日游"
    match_cn = re.search(r'([一二两仨三四五六七八九十]+)\s*[天日]', text)
    if match_cn:
        cn_char = match_cn.group(1)
        return cn_num_map.get(cn_char, 3)

    return 3


def parse_budget_amount(text: str, trip_days: int = 1, member_count: int = 1) -> int:
    """
    从用户诉求文本中解析预算金额，返回「总预算」数值（元），无法解析时返回 0。
    支持："预算5000"、"总预算1万"、"人均300"、"每天600"、"8000元"、"1.5万" 等表述。
    """
    if not text:
        return 0
    days = max(trip_days, 1)
    members = max(member_count, 1)

    def _to_number(num_str: str, unit: str = "") -> float:
        try:
            val = float(num_str)
        except (TypeError, ValueError):
            return 0.0
        if "万" in (unit or ""):
            val *= 10000
        elif "千" in (unit or ""):
            val *= 1000
        return val

    # 1) 总预算/预算 明确表述（优先级最高）
    m = re.search(r'(?:总预算|整体预算|预算|花销)\D{0,4}(\d+(?:\.\d+)?)\s*(万|千|元|块)?', text)
    if m:
        val = _to_number(m.group(1), m.group(2))
        if val >= 100:
            return int(val)

    # 2) 人均表述 -> 人均 × 人数 × 天数
    m = re.search(r'人均\D{0,4}(\d+(?:\.\d+)?)\s*(万|千|元|块)?', text)
    if m:
        per = _to_number(m.group(1), m.group(2))
        if per >= 10:
            return int(per * members * days)

    # 3) 每天/每日/一天 表述 -> 日均 × 天数
    m = re.search(r'(?:每天|每日|一天|日均)\D{0,4}(\d+(?:\.\d+)?)\s*(万|千|元|块)?', text)
    if m:
        per = _to_number(m.group(1), m.group(2))
        if per >= 10:
            return int(per * days)

    # 4) 裸金额带「万」：如 "1万"、"1.5万"
    m = re.search(r'(\d+(?:\.\d+)?)\s*万', text)
    if m:
        return int(_to_number(m.group(1), "万"))

    # 5) 裸金额带「元/块」：如 "5000元"、"800块"
    m = re.search(r'(\d{3,})\s*(?:元|块)', text)
    if m:
        return int(float(m.group(1)))

    return 0


def extract_preference_signals(text: str) -> Dict[str, Any]:
    """从用户诉求文本中抽取住宿/餐饮/风景/文化/购物偏好信号，供权重与校验使用。"""
    t = str(text or "").lower()

    def positive_mention(words: List[str]) -> bool:
        """忽略“不想去/少安排”等否定上下文，避免把排除项当成偏好项。"""
        for word in words:
            for match in re.finditer(re.escape(word), t):
                prefix = t[max(0, match.start() - 10):match.start()]
                if re.search(r"(?:不想|不要|不去|别|少|减少|去掉|换掉|排除|不需要)", prefix):
                    continue
                return True
        return False

    cultural_words = ["博物馆", "博物院", "展馆", "展览馆", "纪念馆", "美术馆", "历史", "古迹", "文化", "人文", "人文景观"]
    # 排除人文的三种说法（用户口径 2026-10-04：说"只想看自然"时人文就该让位）：
    #   ① 明确否定："不要博物馆/少安排人文"；
    #   ② 只谈自然："只想看自然景观""专门来爬山的"——且整句里根本没提人文/文化；
    #   ③ 老口径：否定词 + 文化类词。
    excludes_cultural = bool(
        re.search(r"(?:不想|不要|不去|别|少|减少|去掉|换掉|排除|不需要)[^。；，,!?！?]{0,10}(?:博物馆|博物院|展馆|展览馆|纪念馆|美术馆|文化|人文)", t)
    ) or bool(
        re.search(r"(?:不想|不要|不去|少安排|不用安排|别安排|别去|不看)[^。；，,!?！?]{0,6}(?:人文|博物馆|文化|古迹)", t)
    ) or bool(
        # 语序反过来："人文少安排""博物馆就不用去了"。这里必须列具体词，
        # 不能写成"文化+不"，否则"人文不少"会被误判成排除。
        re.search(r"(?:人文|文化|博物馆|古迹|历史)[^。；，,!?！?]{0,3}(?:少安排|少一点|少去|少逛|不用|不要|不去|免了|砍掉|去掉|别安排|别去)", t)
    )
    mentions_culture = any(word in t for word in ["人文", "文化", "博物馆", "博物院", "古迹", "历史"])
    # ⚠️ 2026-10-07 用户裁决：「如果用户说只看自然，就没必要安排人文了，按照用户的需求来制定方案」。
    #    原来的触发词只有"只想/只看/只要/专门/就想/全程"——真实的诉求「想看自然风景」**不匹配**，
    #    于是 `exclude_cultural` 一直是 False、配比里仍留 20% 人文（伊宁真跑：9 景区 + 2 人文，
    #    然后门禁反过来说"某天玩点全是人文"）。这里把"想看/想去看/主要是/以…为主/重点看"
    #    一并算作"以自然为主"；`mentions_culture` 这道护栏不变（说了"也想去博物馆"就不排除）。
    nature_focus = bool(
        re.search(
            r"(?:只想|只看|只要|专门|就想|想去看|想看|主要是|重点看|以|全程|就是为了)"
            r"[^。；，,!?！?]{0,6}(?:自然|风景|景区|户外|雪山|爬山|登山|徒步|山|湖|草原|沙漠|峡谷|胡杨|湿地|森林)",
            t,
        )
    ) or bool(re.search(r"(?:自然|风景|景区|户外)[^。；，,!?！?]{0,4}(?:为主|优先|就行|即可)", t))
    if nature_focus and not mentions_culture:
        excludes_cultural = True

    # 距离容忍度：用户说"不考虑距离"时，规划形态应该变成"在更大范围里找景区最优路径"，
    # 而不是继续在市区打转（实测：市区检索只有 ≤5km 的公园，真景区在 47-76km 外）。
    #
    # ⚠️ 顺序要紧：**先判"不介意距离"**，再判"嫌远" —— 否则"不介意太远"会被后一条抓成 low。
    # 用户口径（2026-10-05）：方案里出现"单日 447km"这种是可以接受的（只要标明），
    # 但**用户一说不想跑远，就必须按他的意思把远点移出去**（distance_tolerance=low
    # → daily_far_limit=0 → travel 修复会移出远点）。所以"嫌远"的说法要尽量收全。
    if any(k in t for k in ["不考虑距离", "不介意距离", "不嫌远", "多远都行", "多远都可以", "可以跑远", "跑远一点", "距离不是问题", "不介意远", "愿意跑远", "远一点也没关系", "不在乎距离", "不介意太远", "远点也行", "远一点也行", "可以远一点", "不介意跑远", "不介意跑太远", "跑远也行", "不介意走远", "远也愿意"]):
        distance_tolerance = "high"
    elif any(k in t for k in [
        "不想跑远", "别太远", "不要太远", "不想走远", "就近", "附近玩", "近距离",
        # 用户投诉/二次增量里真实会说的说法（2026-10-05 补）
        "太远", "太远了", "太折腾", "路上太折腾", "别折腾", "不想跑那么远", "不想跑这么远",
        "不想去那么远", "不想去这么远", "别安排那么远", "别排那么远", "换近", "近一点", "近一点的",
        "不要那么远", "不要这么远", "远的地方不去", "少跑点", "别跑太远",
    ]):
        distance_tolerance = "low"
    else:
        distance_tolerance = "normal"

    signals: Dict[str, Any] = {
        "wants_hotel": False, "wants_food": False, "wants_scenic": False,
        "wants_cultural": False, "wants_shopping": False, "luxury_hotel": False,
        "exclude_cultural": excludes_cultural,
        "distance_tolerance": distance_tolerance,
        "requirements": [],
    }
    if any(k in t for k in ["酒店", "住宿", "民宿", "度假村", "客栈", "宾馆", "入住", "五星", "豪华"]):
        signals["wants_hotel"] = True
        signals["requirements"].append("住宿/酒店")
    if any(k in t for k in ["高档", "豪华", "五星", "奢华", "品质", "度假", "高端"]):
        signals["luxury_hotel"] = True
    if any(k in t for k in ["美食", "吃", "餐厅", "特色菜", "小吃", "老字号", "夜市", "火锅", "口味", "本地菜"]) or "餐" in t:
        signals["wants_food"] = True
        signals["requirements"].append("餐饮/美食")
    # 自然景观：只认**多字短语**，不收"景观""自然"这种宽词 ——
    # 否则"人文景观"会被判成想看自然（用户的抱怨原话里就有这个词）。
    scenic_phrases = [
        "风景", "自然风光", "自然风景", "自然景观", "自然保护区", "地质公园", "湿地公园",
        "森林公园", "山水", "公园", "景点", "景区", "户外", "峡谷", "草原", "温泉", "园林",
        "爬山", "徒步", "雪山", "海岛", "湖边", "看海", "湖泊", "森林", "湿地", "沙漠",
        "戈壁", "观景", "绿洲",
        # 「以自然为主」这类说法也是明确的自然诉求（2026-10-07 用户裁决：以自然为主就别排人文）。
        # 收的是**多字短语**，不会把"人文景观"误判成自然。
        "自然为主", "以自然为主", "自然优先", "只看自然", "只想看自然",
    ]
    if any(k in t for k in scenic_phrases):
        # 实测事故（2026-10-04）：用户原话是"我想看更多的自然景观"，而词表里只有
        # "自然风光/风景"，于是 wants_scenic=False —— 诉求被丢掉，行程排成了人文景点。
        signals["wants_scenic"] = True
        signals["requirements"].append("自然风景")
    if positive_mention(cultural_words) and not excludes_cultural:
        signals["wants_cultural"] = True
        signals["requirements"].append("文化历史")
    if any(k in t for k in ["购物", "商场", "商圈", "商业街", "逛街"]):
        signals["wants_shopping"] = True
        signals["requirements"].append("购物商圈")
    # 城市公园：默认限量（1~2 个），用户**明确想逛公园/乐园**时放宽 ——
    # "喜欢的话后续再添加"这条产品口径就落在这个信号上。
    park_words = ["公园", "游乐园", "动物园", "植物园", "乐园", "园林"]
    if any(k in t for k in park_words) and positive_mention(park_words):
        signals["wants_park"] = True
        signals["requirements"].append("城市公园/乐园")
    if distance_tolerance == "high":
        signals["requirements"].append("不介意跑远（跨景区）")
    elif distance_tolerance == "low":
        signals["requirements"].append("尽量就近")
    if excludes_cultural:
        signals["requirements"] = [item for item in signals["requirements"] if item != "文化历史"]
    if not signals["requirements"]:
        signals["requirements"] = ["常规观光、特色美食与舒适住宿"]
    return signals


def safe_repair_and_parse_json(json_str: str) -> dict:
    if not json_str:
        return {}

    clean_str = str(json_str).strip()
    clean_str = re.sub(r'```json', '', clean_str)
    clean_str = re.sub(r'```', '', clean_str).strip()

    try:
        return json.loads(clean_str)
    except Exception:
        pass

    # 正则修复：清理 JSON 数组/对象末尾多余的逗号
    clean_str = re.sub(r',\s*([\}\]])', r'\1', clean_str)
    # 替换未转义的普通换行符
    clean_str = clean_str.replace('\n', '\\n').replace('\r', '')

    try:
        return json.loads(clean_str)
    except Exception:
        last_brace = clean_str.rfind("}")
        if last_brace != -1:
            try:
                return json.loads(clean_str[:last_brace+1])
            except Exception:
                pass
    return {}


def build_reasoning_step(agent: str, action: str, claim: str, basis: str) -> dict:
    """构造一条结构化推理步骤（供前端博弈过程可视化回放 + Redis 存档）。
    action: claim（主张）/ compromise（妥协）/ decision（决策）。
    """
    return {
        "agent": agent,
        "action": action,
        "claim": claim,
        "basis": basis,
        "ts": datetime.datetime.now().strftime("%H:%M:%S"),
    }


def _static_map_url(lnglat, amap_key, size: str = "720*480", zoom: int = 15) -> str:
    """按坐标生成高德静态地图 URL（保底图片，永不空白）。"""
    if not (isinstance(lnglat, list) and len(lnglat) >= 2):
        return ""
    try:
        lng, lat = float(lnglat[0]), float(lnglat[1])
    except (TypeError, ValueError):
        return ""
    if lng == 0.0 and lat == 0.0 or not amap_key:
        return ""
    loc_str = f"{lng},{lat}"
    return (
        f"https://restapi.amap.com/v3/staticmap?location={loc_str}&zoom={zoom}&size={size}&scale=2"
        f"&markers=mid,0xFF5722,A:{loc_str}&key={amap_key}"
    )
