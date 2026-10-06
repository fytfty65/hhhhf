"""候选大模型的**同题对照**（2026-10-05）。

为什么要有它：横向对照把单次推演的大头锁死在"主推演"这一步（一次调用 41~133s，
占总时长 6 成以上），而它慢在**模型本身的生成速度**上（8.7~11.7 块/秒，
没有重试、prompt 3.4k~4.3k tokens、max_tokens 远没打满）。
"换更快的模型"是产品取舍，不该我替用户拍 —— 但取舍需要数据，这个探针就干这件事：
**同一道题、同一套结构要求**，把几个候选模型跑一遍，量出速度与最基本的质量指标。

用法：
    cd ai-service
    .\\venv\\Scripts\\python.exe tools\\probe_model_options.py
    $env:PROBE_MODELS='qwen-plus,qwen-turbo,qwen3-max'; .\\venv\\Scripts\\python.exe tools\\probe_model_options.py

指标（都来自真实调用，不估算）：
    · 首个 token 延迟 / 总耗时 / 输出块数 / 块每秒 —— 速度；
    · JSON 能否解析、天数对不对、有没有 cost_estimate、地点是否重复 —— 最基本的质量门槛。
注意：这会消耗真实 API 额度（每个模型一次、max_tokens 有限），所以默认只跑 3 个模型。

2026-10-05 实测（同题小样本，max_tokens=1600，"库尔勒 2 天 × 每天 4 节点"）：
    模型            总耗时  首token  块/秒   JSON  天数      节点  带价  重复
    qwen-plus         9.2s   1.10s   8.8     ✓  [1, 2]      8     8     0   ← 当前默认
    qwen-turbo        2.8s   0.30s  24.2     ✓  [1, 2]      8     8     1
    qwen-max          8.1s   0.41s   8.1     ✓  [1, 2]      8     8     0
小样本说明 turbo 快 3 倍，但只丢了一个"地点重复"（会被出稿门禁修）。
真实推演的 A/B（5 个场景、冷跑、同一份代码）：
    场景            qwen-plus   qwen-turbo   提速
    库尔勒 2 天       86.6s       41.4s      2.1×
    喀什 3 天         94.8s       46.6s      2.0×
    伊宁 3 天         76.7s       61.1s      1.25×
    库尔勒(美食)     121.6s       51.5s      2.4×
且**配比/节点数/门禁结论完全一致**（偏差都是同一类"人文候选不足"的供给限制）。
换不换是产品取舍（内容深度 vs 速度），需要用户拍板；用 `LLM_MODEL_NAME` 切换，不需要改代码。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from core.llm_config import api_key, base_url, model_name  # noqa: E402

DEFAULT_MODELS = ["qwen-plus", "qwen-turbo", "qwen-max"]
MAX_TOKENS = int(os.getenv("PROBE_MAX_TOKENS", "1600"))

SYSTEM_PROMPT = """你是行程规划引擎。只输出 JSON，不要解释、不要 Markdown 代码块。
JSON 结构：
{"route":[{"day":1,"name":"地点名","type":"类型","cost_estimate":"¥45","reason":"为什么去"}]}
要求：为库尔勒生成 2 天行程，每天 4 个节点（含夜间住宿），
每天必须同时有风景名胜、特色餐饮、人文地标；全程地点不重复；每个节点必须有 cost_estimate。"""


def parse_plan(text: str) -> dict:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def probe(client: AsyncOpenAI, model: str) -> dict:
    started = time.monotonic()
    first_token_at = None
    chunks = 0
    text = ""
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}],
            stream=True,
            temperature=0.2,
            max_tokens=MAX_TOKENS,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                if first_token_at is None:
                    first_token_at = time.monotonic() - started
                text += chunk.choices[0].delta.content
                chunks += 1
    except Exception as exc:
        return {"model": model, "error": f"{type(exc).__name__}: {str(exc)[:140]}"}

    seconds = max(0.001, time.monotonic() - started)
    plan = parse_plan(text)
    route = plan.get("route") if isinstance(plan.get("route"), list) else []
    days = sorted({int(item.get("day") or 1) for item in route if isinstance(item, dict)})
    names = [str(item.get("name") or "") for item in route if isinstance(item, dict)]
    with_cost = len([item for item in route if isinstance(item, dict) and item.get("cost_estimate")])
    return {
        "model": model,
        "seconds": round(seconds, 1),
        "first_token_s": round(first_token_at or 0.0, 2),
        "chunks": chunks,
        "chunks_per_s": round(chunks / seconds, 1),
        "json_ok": bool(route),
        "days": days,
        "nodes": len(route),
        "nodes_with_cost": with_cost,
        "duplicate_names": len(names) - len(set(names)),
        "chars": len(text),
    }


async def main() -> int:
    models = [item.strip() for item in (os.getenv("PROBE_MODELS") or ",".join(DEFAULT_MODELS)).split(",") if item.strip()]
    print("=" * 78)
    print(f"候选模型同题对照（当前默认：{model_name()}；base_url={base_url()}）")
    print(f"题目：库尔勒 2 天 × 每天 4 节点，max_tokens={MAX_TOKENS}")
    print("=" * 78)
    client = AsyncOpenAI(api_key=api_key(), base_url=base_url(), timeout=180.0)
    rows = []
    for model in models:
        print(f"  ▶ {model} … ", end="", flush=True)
        row = await probe(client, model)
        rows.append(row)
        if row.get("error"):
            print(f"❌ {row['error']}")
        else:
            print(
                f"{row['seconds']}s（首 token {row['first_token_s']}s，{row['chunks_per_s']} 块/秒）｜"
                f"JSON {'✓' if row['json_ok'] else '✗'}｜天数 {row['days']}｜节点 {row['nodes']}"
                f"（带价 {row['nodes_with_cost']}）｜重复 {row['duplicate_names']}"
            )
    print()
    print("模型            总耗时  首token  块/秒   JSON  天数      节点  带价  重复")
    for row in rows:
        if row.get("error"):
            print(f"{row['model']:<15} 失败：{row['error']}")
            continue
        print(
            f"{row['model']:<15} {row['seconds']:>5.1f}s {row['first_token_s']:>6.2f}s "
            f"{row['chunks_per_s']:>5.1f}  {'✓' if row['json_ok'] else '✗':>4}  "
            f"{str(row['days']):<8} {row['nodes']:>4} {row['nodes_with_cost']:>5} {row['duplicate_names']:>5}"
        )
    print()
    print("说明：块/秒是**流式块**速度（不同模型每块含的 token 数不同，只用于同题横比）；")
    print("      质量列只覆盖「能不能用」的最低门槛，真正的口味好不好还得看真实推演出的方案。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
