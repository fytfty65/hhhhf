"""把搜狗结果页的原始结构 dump 出来，用于写解析规则（只做诊断，不用于生产）。

用法：python tools/dump_sogou_sample.py "库尔勒 博斯腾湖 门票 价格"
输出：work/sogou-sample.html（原始页面）+ 控制台打印前 2 个结果块的结构片段。
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


async def main() -> int:
    import httpx

    query = sys.argv[1] if len(sys.argv) > 1 else "库尔勒 博斯腾湖 门票 价格"
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        await client.get("https://www.sogou.com/")
        response = await client.get("https://www.sogou.com/web", params={"query": query})
    body = response.text
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "work")
    out_path = os.path.abspath(os.path.join(out_dir, "sogou-sample.html"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8") as handle:
        handle.write(body)
    print(f"HTTP {response.status_code} | {len(body)} 字节 → {out_path}")

    parts = re.split(r'(?=<div[^>]+class="(?:vrwrap|rb)[^"]*")', body)
    blocks = [part for part in parts if len(part) > 300][:2]
    print(f"候选结果块 {len(blocks)} 个（截取前 2 个，各 900 字符）\n")
    for index, block in enumerate(blocks, start=1):
        print(f"===== 块 {index} =====")
        print(block[:900].replace("\n", " ")[:900])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
