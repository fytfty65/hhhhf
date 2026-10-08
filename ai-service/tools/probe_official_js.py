"""探针第三轮：这两个站是**客户端渲染**（罗布人村寨的子页都返回同一份 2490 字符壳、
巴音布鲁克的门票页只有 1432 字符）。那就去问它的数据从哪来（2026-10-07）：

  ① 站点的 JS/接口里有没有票价（找 `35`/`140`/`门票`/`price`）；
  ② 官方公众号文章（`mp.weixin.qq.com`，服务端渲染）里有没有写票价 —— 用户给的数字
     （罗布人 35/15、巴音布鲁克 140）应该能在这里核到。
"""

from __future__ import annotations

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
SITES = [
    ("罗布人村寨", "https://www.xjlbr.com/"),
    ("巴音布鲁克", "https://www.byblk.cn/"),
]
WEIXIN = [
    ("罗布人村寨·区间车公告", "https://mp.weixin.qq.com/s/QlsSPaoPEvnjl-t8cYccWg"),
    ("罗布人村寨·最大承载量", "https://mp.weixin.qq.com/s/4mhGQ9EWKqKeHh2rpTmzDg"),
]


async def main() -> int:
    import httpx

    from core.web_price_tool import extract_ticket_price

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}, follow_redirects=True
    ) as client:
        for label, base in SITES:
            print(f"\n=== {label}｜{base}")
            home = await client.get(base)
            html = home.text or ""
            assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|json)[^"]*)"', html, flags=re.I)
            apis = sorted(set(re.findall(r'["\'](/(?:api|index|data|json)[A-Za-z0-9_/\-\.]{2,60})["\']', html)))
            print(f"  JS/JSON 资源 {len(assets)} 个｜接口线索 {apis[:6]}")
            for asset in assets[:6]:
                url = urljoin(str(home.url), asset)
                if "xjlbr" not in url and "byblk" not in url:
                    continue
                try:
                    resp = await client.get(url)
                except Exception:
                    continue
                text = resp.text or ""
                price_hits = re.findall(r"(?:门票|票价|联票|观光车|区间车)[^,;\"'<>]{0,26}", text)
                numbers = [n for n in ("35", "15", "140") if n in text]
                if price_hits or numbers:
                    print(f"    {url[-46:]:48s} {resp.status_code}｜{len(text):7d} 字符｜含数字 {numbers}")
                    for hit in price_hits[:4]:
                        print("       ", hit.strip()[:100])

        for label, url in WEIXIN:
            print(f"\n=== {label}")
            try:
                resp = await client.get(url)
            except Exception as exc:
                print(f"  失败 {type(exc).__name__}")
                continue
            body = resp.text or ""
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", body)))
            print(f"  {resp.status_code}｜正文 {len(text)} 字符")
            hits = re.findall(r"[^。；]{0,24}(?:门票|票价|观光车|区间车)[^。；]{0,30}", text)
            for hit in hits[:6]:
                print("   片段:", hit.strip()[:120])
            print("  抽取:", extract_ticket_price(text, require_name="罗布人村寨", name_window=160))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
