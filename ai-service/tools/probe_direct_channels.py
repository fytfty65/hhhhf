"""探针：**不依赖搜索引擎**的定向通道（2026-10-07）。

已经证实：Bing 现在给我们的结果是**随机热门页**（"Switch怎么查看系统版本"、"微信"），
`site:` 也无效 —— 也就是说它作为"定向通道"不可靠。那就换两条真正定向的路子：

  ① **高德 POI 详情**（`place/detail`）：`biz_ext` 里到底有没有门票相关字段？
     （我们已经在用它的 rating/坐标；若有票价字段，就是一条不经过搜索引擎的定向通道。）
  ② **官方文旅站的景区名录**：伊犁文旅 `ylwl.xjyl.gov.cn` 有 `show/scenicspot/detail?id=581`
     这种详情页（天鹅泉 ¥30 就是从这儿来的）。看它有没有**名录/列表接口**可以枚举，
     从而建立"景区名 → 官方页"的索引，再在官方页上抽票价。
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


async def probe_amap_detail() -> None:
    import httpx

    key = os.getenv("AMAP_API_KEY")
    print("=== ① 高德 POI 详情里有哪些字段（找门票）")
    # 先按名字搜 POI 拿 id
    async with httpx.AsyncClient(timeout=15.0) as client:
        for name, city in (("天鹅泉湿地公园", "伊宁"), ("英吉沙国家湿地公园", "喀什"), ("图开沙漠冰雪乐园", "伊宁")):
            search = await client.get(
                "https://restapi.amap.com/v3/place/text",
                params={"key": key, "keywords": name, "city": city, "offset": 3, "extensions": "all"},
            )
            data = search.json()
            pois = data.get("pois") or []
            if not pois:
                print(f"\n  {name}: 搜不到 POI（status={data.get('status')} info={data.get('info')}）")
                continue
            poi = pois[0]
            print(f"\n  {name} → {poi.get('name')}（id={poi.get('id')}）")
            detail = await client.get(
                "https://restapi.amap.com/v3/place/detail",
                params={"key": key, "id": poi.get("id"), "extensions": "all"},
            )
            info = (detail.json().get("pois") or [{}])[0]
            biz = info.get("biz_ext") or {}
            print(f"     biz_ext 键: {sorted(biz.keys())}")
            for field in ("cost", "rating", "open_time", "ticket_info", "price", "opentime2", "parking_type"):
                if field in biz:
                    print(f"       {field} = {biz.get(field)}")
            # 也看看顶层有没有票价相关字段
            interesting = {
                key_name: info.get(key_name)
                for key_name in info
                if any(word in key_name for word in ("cost", "price", "ticket", "biz", "tag", "type"))
            }
            print(f"     顶层相关字段: {interesting}")


async def probe_tourism_site() -> None:
    import httpx

    print("\n=== ② 官方文旅站的景区名录能不能枚举")
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for url in (
            "http://ylwl.xjyl.gov.cn/",
            "http://ylwl.xjyl.gov.cn/show/scenicspot",
            "http://ylwl.xjyl.gov.cn/show/scenicspot/detail?id=581",
        ):
            try:
                resp = await client.get(url)
            except Exception as exc:
                print(f"  {url:52s} 失败 {type(exc).__name__}: {str(exc)[:60]}")
                continue
            html = resp.text or ""
            links = sorted(
                set(re.findall(r'href="([^"]*(?:scenicspot|jingqu|spot|detail)[^"]*)"', html, flags=re.I))
            )
            print(f"  {url:52s} {resp.status_code}｜{len(html)} 字符｜相关链接 {len(links)}")
            for link in links[:6]:
                print(f"      · {link[:100]}")
            if "detail" in url:
                text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text)
                price_hits = re.findall(r"[^。；]{0,18}(?:门票|票价)[^。；]{0,24}", text)
                print(f"      正文里 门票/票价 片段: {price_hits[:3]}")


async def main() -> int:
    await probe_amap_detail()
    await probe_tourism_site()
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
