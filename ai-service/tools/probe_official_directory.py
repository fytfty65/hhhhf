"""探针：官方文旅**名录**通道（2026-10-07）。

已知：`ylwl.xjyl.gov.cn/show/scenicspot/detail?id=581`（天鹅泉）正文里有「门票价格 门票30元/人」，
而且页面里带着名录链接 `/show/scenicspot/list?nodeId=2465` —— 这说明**官方景区名录可以枚举**。
这个探针把通道的关键问题一次问清：

  ① 名录页有多少条、能不能翻页、条目里有没有"景区名 + detail 链接"；
  ② 目标景点（图开沙漠 / 阿吾赞沟 / 天鹅泉）在不在名录里；
  ③ 喀什/库尔勒有没有同构的官方文旅站（按 `xxwl.<域名>` 这种命名规律试几个）。
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

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CANDIDATES = [
    ("伊犁文旅", "http://ylwl.xjyl.gov.cn/show/scenicspot/list?nodeId=2465"),
    ("伊犁文旅·第二页", "http://ylwl.xjyl.gov.cn/show/scenicspot/list?nodeId=2465&page=2"),
    ("喀什文旅(猜)", "http://kswl.xjks.gov.cn/"),
    ("喀什政府·文旅栏目(猜)", "http://www.xjks.gov.cn/whly/"),
    ("巴州文旅(猜)", "http://bzwl.xinjiang.gov.cn/"),
    ("新疆文旅厅", "https://wlt.xinjiang.gov.cn/"),
    ("库尔勒政府", "http://www.xjkel.gov.cn/"),
]


async def fetch(client, url: str):
    try:
        resp = await client.get(url)
        return resp
    except Exception as exc:
        print(f"  {url:56s} 失败 {type(exc).__name__}: {str(exc)[:60]}")
        return None


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        print("=== ① 名录页结构")
        for label, url in CANDIDATES[:2]:
            resp = await fetch(client, url)
            if resp is None:
                continue
            html = resp.text or ""
            details = sorted(set(re.findall(r'href="([^"]*scenicspot/detail\?id=\d+)"', html)))
            names = re.findall(r'scenicspot/detail\?id=\d+"[^>]*>([^<]{2,40})<', html)
            print(f"  {label}：{resp.status_code}｜{len(html)} 字符｜detail 链接 {len(details)} 个｜抓到名字 {len(names)} 个")
            for name in names[:8]:
                print(f"      · {name.strip()}")
            if details[:3]:
                print(f"      样例链接: {details[:3]}")
            # 翻页参数线索
            pages = sorted(set(re.findall(r'(?:page|pageNum|current)=\d+', html)))[:5]
            print(f"      翻页参数: {pages}")

        print("\n=== ② 目标景点在不在名录里")
        resp = await fetch(client, CANDIDATES[0][1])
        if resp is not None:
            html = resp.text or ""
            text = re.sub(r"<[^>]+>", " ", html)
            for name in ("天鹅泉", "图开沙漠", "阿吾赞沟", "伊犁河", "草原明珠", "沙漠迷宫", "草原遗迹"):
                print(f"      {name}: {'在' if name in text else '不在'}")

        print("\n=== ③ 其它地区的官方文旅站")
        for label, url in CANDIDATES[2:]:
            resp = await fetch(client, url)
            if resp is None:
                continue
            html = resp.text or ""
            title = re.search(r"<title>(.*?)</title>", html, flags=re.S)
            print(
                f"  {label:22s} {resp.status_code}｜{len(html):7d} 字符｜标题："
                f"{(title.group(1).strip()[:40] if title else '(无)')}"
            )
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
