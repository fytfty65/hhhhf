"""探针第四轮：喀什「喀什旅游」栏目、库尔勒跳转页、文旅部 5A 服务页（2026-10-07）。"""

import asyncio
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
PAGES = [
    ("喀什·喀什旅游栏目", "https://www.kashi.gov.cn/ksdqxzgs/c106707/list.shtml"),
    ("喀什·喀什风韵栏目", "https://www.kashi.gov.cn/ksdqxzgs/c106705/list.shtml"),
    ("库尔勒·政务公开跳转页", "https://www.xjkel.gov.cn/xjkrls/c110128/jump_column.shtml"),
    ("文旅部·5A景区服务", "https://lyfw.mct.gov.cn/site/special/scenic"),
]


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}, follow_redirects=True
    ) as client:
        for label, url in PAGES:
            print(f"\n=== {label}｜{url}")
            try:
                resp = await client.get(url)
            except Exception as exc:
                print(f"  失败 {type(exc).__name__}: {str(exc)[:60]}")
                continue
            body = resp.text or ""
            print(f"  {resp.status_code}｜{len(body)} 字符")
            if len(body) < 1200:
                print("  原文：", re.sub(r"\s+", " ", body)[:900])
            # 关键词盘点
            for word in ("门票", "A级", "景区", "名录", "名单", "票价"):
                if word in body:
                    spot = body.find(word)
                    print(f"  含『{word}』{body.count(word)} 次，例：", re.sub(r"\s+", " ", body[max(0, spot - 60): spot + 60])[:140])
            # 条目
            items = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, flags=re.S | re.I)
            rows = []
            for href, text in items:
                clean = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", text))
                if 4 <= len(clean) <= 40 and not clean.startswith(("登录", "首页", "第")):
                    rows.append((urljoin(str(resp.url), href), clean))
            print(f"  条目 {len(rows)} 个：")
            for href, text in rows[:12]:
                print(f"    · {text[:30]:32s} {href[:76]}")
            # JSON/API 线索
            for marker in ("api", "json", "ajax", ".do", "getList", "queryList"):
                if marker in body:
                    print(f"    含 {marker}")
            sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
