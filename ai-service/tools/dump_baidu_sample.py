"""把百度搜索结果页的真实结构 dump 出来（2026-10-05）。

为什么需要：搜狗那条路已经被反爬打到"填充页"（HTTP 200，只有 5KB、结果块里 0 个含查询词），
而同一时刻**百度能返回真结果**（1.1MB、22 个结果块、17 个含查询词）。
要接百度，就得先看清它现在的真实 DOM（标题/摘要/来源），照抄一份样本进单测，
而不是凭记忆写解析器。

用法：
    cd ai-service
    .\\venv\\Scripts\\python.exe tools\\dump_baidu_sample.py "库尔勒 博斯腾湖 门票 价格"
    产物写到 <repo>/work/baidu-sample.html（不入库）
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


async def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "库尔勒 博斯腾湖 门票 价格"
    url = "https://www.baidu.com/s"
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=HEADERS) as client:
        resp = await client.get(url, params={"wd": query, "rn": 20})
    html = resp.text
    print(f"GET {resp.url} → HTTP {resp.status_code} | {len(html)} 字节")

    work_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "work")
    os.makedirs(work_dir, exist_ok=True)
    out_path = os.path.join(work_dir, "baidu-sample.html")
    with io.open(out_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    print(f"已写入 {out_path}")

    # 结果块：百度现在的容器类名形如 result / c-container，标题在 h3 里的 a[href]
    blocks = re.findall(r'<div[^>]+class="[^"]*(?:result|c-container)[^"]*"[^>]*>(.*?)</div>\s*</div>', html, re.S)
    print(f"粗略结果块：{len(blocks)} 个")
    for index, block in enumerate(blocks[:5]):
        title = re.search(r"<h3[^>]*>(.*?)</h3>", block, re.S)
        title_text = re.sub(r"<[^>]+>", "", title.group(1)) if title else ""
        link = re.search(r'href="(http[^"]+)"', block)
        snippet = re.sub(r"<[^>]+>", " ", block)
        snippet = re.sub(r"\s+", " ", snippet).strip()
        print(f"  [{index}] 标题={title_text.strip()[:60]!r}")
        print(f"     链接={str(link.group(1))[:80] if link else '(无)'}")
        print(f"     文本={snippet[:120]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
