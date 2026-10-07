"""探针：给**喀什/库尔勒**找一条同样可用的官方通道（2026-10-07）。

伊犁那条已通（`ylwl.xjyl.gov.cn/show/scenicspot/list?nodeId=2465`）。喀什/库尔勒要照同样的
形态找：**名录页是纯 HTML + 详情页写票价**。猜过的域名都不通（kswl.xjks.gov.cn 502、
xjks.gov.cn/whly 404、bzwl.xinjiang.gov.cn 502），这一轮换一批候选并检查省级站。

判据（只有满足才值得写进渠道表）：
  · 站点可达（200）；
  · 页面里能找到**景区名录/列表**的链接（含 scenicspot / 景区 / 景点 / A级 等线索）；
  · 最好能直接看到"详情页 + 票价"的形态。
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

HOSTS = [
    ("喀什地区文旅局(猜)", "http://wlj.xjks.gov.cn/"),
    ("喀什地区旅游局(猜)", "http://lyj.xjks.gov.cn/"),
    ("喀什政府-旅游(猜)", "http://www.xjks.gov.cn/ly/"),
    ("喀什政府-文旅(猜)", "http://www.xjks.gov.cn/wl/"),
    ("巴州政府", "http://www.xjbz.gov.cn/"),
    ("巴州文旅局(猜)", "http://wlj.xjbz.gov.cn/"),
    ("库尔勒文旅(猜)", "http://wlj.xjkel.gov.cn/"),
    ("新疆文旅厅", "https://wlt.xinjiang.gov.cn/"),
]

KEYWORDS = ("scenicspot", "景区", "景点", "A级", "旅游线路", "名录", "景点详情")


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for label, url in HOSTS:
            try:
                resp = await client.get(url)
            except Exception as exc:
                print(f"{label:22s} {url:38s} 失败 {type(exc).__name__}: {str(exc)[:44]}")
                continue
            html = resp.text or ""
            title = re.search(r"<title>(.*?)</title>", html, flags=re.S)
            print(
                f"{label:22s} {url:38s} {resp.status_code}｜{len(html):7d} 字符｜"
                f"{(title.group(1).strip()[:26] if title else '(无标题)')}"
            )
            hrefs = sorted(set(re.findall(r'href="([^"]+)"', html)))
            hits = [href for href in hrefs if any(word in href for word in KEYWORDS)]
            if hits:
                print(f"    名录/景区类链接 {len(hits)} 个，样例：")
                for href in hits[:6]:
                    print(f"      · {href[:96]}")
            # 页面正文里有没有"门票"字样（说明这个站确实写票价）
            if "门票" in html:
                spot = html.find("门票")
                snippet = re.sub(r"<[^>]+>", " ", html[max(0, spot - 60): spot + 60])
                print(f"    正文含『门票』：{re.sub(r'[ ]+', ' ', snippet).strip()[:90]}")
            sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
