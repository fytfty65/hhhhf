"""探针第五轮：库尔勒政务公开栏目树 + 文旅部景区服务的数据接口（2026-10-07）。"""

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
START = [
    ("库尔勒·政府信息公开", "https://www.xjkel.gov.cn/xjkrls/c115482/jump_column.shtml"),
]
MCT_API_GUESSES = [
    "https://lyfw.mct.gov.cn/api/site/special/scenic",
    "https://lyfw.mct.gov.cn/api/scenic/list",
    "https://lyfw.mct.gov.cn/site/api/special/scenic",
]


def links(html: str, base: str):
    out = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html or "", flags=re.S | re.I):
        clean = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", text))
        if 2 <= len(clean) <= 30 and href and not href.lower().startswith(("javascript:", "#", "mailto:")):
            out.append((urljoin(base, href), clean))
    return out


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}, follow_redirects=True
    ) as client:
        print("=== 库尔勒：政府信息公开栏目（先解跳转，再看栏目树）")
        for label, url in START:
            resp = await client.get(url)
            body = resp.text or ""
            target = re.search(r'id="url">([^<]+)<', body)
            if target:
                url = urljoin(url, target.group(1).strip())
                print(f"  跳转到: {url}")
                resp = await client.get(url)
                body = resp.text or ""
            print(f"  {resp.status_code}｜{len(body)} 字符｜标题 {re.search(r'<title>(.*?)</title>', body, flags=re.S).group(1).strip()[:30] if re.search(r'<title>(.*?)</title>', body, flags=re.S) else ''}")
            rows = links(body, str(resp.url))
            interesting = [
                (href, name)
                for href, name in rows
                if any(word in name for word in ("文旅", "旅游", "景区", "文化", "公开", "名录", "A级"))
            ]
            print(f"  相关栏目/条目 {len(interesting)} 个")
            for href, name in interesting[:16]:
                print(f"    · {name[:22]:24s} {href[:80]}")
            # 顺着"文旅/旅游"再进一层
            for href, name in interesting[:3]:
                if not any(word in name for word in ("文旅", "旅游", "景区")):
                    continue
                sub = await client.get(href)
                subbody = sub.text or ""
                subs = links(subbody, str(sub.url))
                spots = [(h, n) for h, n in subs if any(w in n for w in ("景区", "A级", "名录", "公园", "古城"))]
                print(f"    ↳ {name[:16]}｜{sub.status_code}｜{len(subbody)} 字符｜含『门票』{'门票' in subbody}｜含『A级』{'A级' in subbody}｜疑似条目 {len(spots)}")
                for h, n in spots[:6]:
                    print(f"        ★ {n[:20]:22s} {h[:78]}")

        print("\n=== 文旅部景区服务：试数据接口")
        for api in MCT_API_GUESSES:
            try:
                resp = await client.get(api)
            except Exception as exc:
                print(f"  {api:52s} 失败 {type(exc).__name__}")
                continue
            print(f"  {api:52s} {resp.status_code}｜{len(resp.text or '')} 字符｜{str(resp.text)[:80]}")
        # 从 SPA 的 JS 里找接口线索
        resp = await client.get("https://lyfw.mct.gov.cn/site/special/scenic")
        js = re.findall(r'src="([^"]+\.js[^"]*)"', resp.text or "")
        print("  JS 资源:", js[:4])
        for asset in js[:3]:
            js_url = urljoin("https://lyfw.mct.gov.cn/", asset)
            try:
                js_resp = await client.get(js_url)
            except Exception:
                continue
            text = js_resp.text or ""
            apis = sorted(set(re.findall(r'["\'](/(?:api|site)[A-Za-z0-9_/\-]{3,60})["\']', text)))
            print(f"    {js_url[-42:]}｜{len(text)} 字符｜接口线索 {apis[:8]}")
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
