"""探针第六轮：库尔勒「旅游」栏目 + 文旅部 `/api/marker/*` 接口（2026-10-07）。

上一轮在文旅部的 Nuxt 前端里抠到接口线索：
  `/api/marker/list`、`/api/marker/province/list`、`/api/marker/type/statistics`、`/api/article/list`…
"marker" 很可能就是**全国景区标注点**（含名称/等级/坐标/官方预约平台），
若是，那就是一条**全国覆盖**的官方通道（喀什、库尔勒、伊宁都能用）。
"""

import asyncio
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
API = "https://lyfw.mct.gov.cn"
CALLS = [
    ("marker/province/list", {"method": "get"}),
    ("marker/type/statistics", {"method": "get"}),
    ("marker/list", {"method": "get"}),
    ("marker/list", {"method": "get", "params": {"province": "新疆维吾尔自治区", "pageNum": 1, "pageSize": 5}}),
    ("marker/list", {"method": "get", "params": {"keyword": "喀什", "pageNum": 1, "pageSize": 5}}),
    ("marker/list", {"method": "get", "params": {"provinceCode": "650000", "pageNum": 1, "pageSize": 5}}),
    ("article/list", {"method": "get", "params": {"pageNum": 1, "pageSize": 3}}),
]


async def main() -> int:
    import httpx

    headers = {"User-Agent": UA, "Accept": "application/json", "Referer": "https://lyfw.mct.gov.cn/site/special/scenic"}
    async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
        print("=== 文旅部接口")
        for path, spec in CALLS:
            url = f"{API}/api/{path}"
            try:
                resp = await client.get(url, params=spec.get("params"))
            except Exception as exc:
                print(f"  {path:22s} {str(spec.get('params'))[:44]:46s} 失败 {type(exc).__name__}")
                continue
            text = (resp.text or "").strip()
            print(f"  {path:22s} {str(spec.get('params'))[:44]:46s} {resp.status_code}｜{len(text)} 字符｜{text[:150]}")

        print("\n=== 库尔勒「旅游」栏目")
        for label, url in (
            ("旅游(jump_article)", "https://www.xjkel.gov.cn/xjkrls/c117750/jump_article.shtml"),
            ("公共文化服务", "https://www.xjkel.gov.cn/xjkrls/c116578/zwgk_list.shtml"),
        ):
            try:
                resp = await client.get(url)
            except Exception as exc:
                print(f"  {label:18s} 失败 {type(exc).__name__}")
                continue
            body = resp.text or ""
            print(f"  {label:18s} {resp.status_code}｜{len(body)} 字符")
            target = __import__("re").search(r'id="url">([^<]+)<', body)
            if target:
                real = f"https://www.xjkel.gov.cn{target.group(1).strip()}"
                print(f"    跳转 → {real}")
                resp = await client.get(real)
                body = resp.text or ""
                print(f"    实际页面 {resp.status_code}｜{len(body)} 字符")
            import re as _re

            for word in ("门票", "A级", "景区", "票价"):
                if word in body:
                    spot = body.find(word)
                    print(f"    含『{word}』{body.count(word)} 次：", _re.sub(r"\s+", " ", body[max(0, spot - 70): spot + 70])[:150])
            rows = _re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, flags=_re.S | _re.I)
            spots = []
            for href, text in rows:
                clean = _re.sub(r"\s+", "", _re.sub(r"<[^>]+>", "", text))
                if 2 <= len(clean) <= 30 and any(w in clean for w in ("景区", "公园", "古城", "名录", "旅游", "A级", "文化")):
                    spots.append((href, clean))
            print(f"    疑似条目 {len(spots)} 个")
            for href, name in spots[:10]:
                print(f"      · {name[:22]:24s} {href[:76]}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
