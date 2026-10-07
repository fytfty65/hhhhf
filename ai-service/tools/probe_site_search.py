"""探针：政府站**站内检索**能不能当定向通道（2026-10-07）。

喀什没有景区名录（猜的文旅局域名全 502/404），但 `www.xjks.gov.cn` 是通的 ——
我们先前那条"阳光公园 ¥30"就是从这个站的**票价通知页**上来的。如果这个站有**站内检索**，
那就可以"只在官方站里搜景区名"，比通用搜索可靠得多（Bing 现在返回随机热门页）。

这个探针找：页面里的 search 表单（action / 参数名）与常见检索路径，并**实测一次**查询。
"""

import asyncio
import os
import re
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
SITES = [
    ("喀什市政府", "http://www.xjks.gov.cn/"),
    ("巴州政府", "http://www.xjbz.gov.cn/"),
]
CANDIDATE_PATHS = ["/search", "/so", "/ss", "/search.jsp", "/jsearchfront/search.do"]


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}) as client:
        for label, base in SITES:
            print(f"\n=== {label}｜{base}")
            try:
                resp = await client.get(base, follow_redirects=True)
            except Exception as exc:
                print(f"  失败 {type(exc).__name__}: {str(exc)[:50]}")
                continue
            html = resp.text or ""
            forms = re.findall(r"<form[^>]*>(.*?)</form>", html, flags=re.S | re.I)
            print(f"  {resp.status_code}｜{len(html)} 字符｜form {len(forms)} 个")
            for form in forms[:4]:
                action = re.search(r'action="([^"]*)"', form, flags=re.I)
                names = re.findall(r'name="([^"]+)"', form, flags=re.I)
                if action or names:
                    print(f"    form action={action.group(1) if action else '(无)'}｜字段 {names[:5]}")
            # 常见检索路径试一下
            for path in CANDIDATE_PATHS[:3]:
                try:
                    probe = await client.get(base.rstrip("/") + path, follow_redirects=True)
                except Exception as exc:
                    print(f"    {path:22s} 失败 {type(exc).__name__}")
                    continue
                print(f"    {path:22s} {probe.status_code}｜{len(probe.text or ''):6d} 字符")
                if probe.status_code == 200 and ("搜索" in (probe.text or "") or "search" in path):
                    # 用常见参数名各试一次，看有没有结果块
                    for param in ("q", "query", "keyword", "searchword", "wd", "keyWord"):
                        try:
                            hit = await client.get(base.rstrip("/") + path, params={param: "英吉沙国家湿地公园"})
                        except Exception:
                            continue
                        body = hit.text or ""
                        if "英吉沙" in body:
                            print(f"       参数 {param} 命中『英吉沙』（{len(body)} 字符）")
                            spot = body.find("英吉沙")
                            print("       " + re.sub(r"\s+", " ", body[max(0, spot - 120): spot + 120])[:220])
                            break
            sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
