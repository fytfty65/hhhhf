"""探针第七轮：① 库尔勒「A级旅游景区基本情况」表；② 文旅部 marker/list 的正确参数（2026-10-07）。

上一轮的收获：
  · 库尔勒「旅游」页（`/xjkrls/c117750/202309/44e21cae…shtml`，231772 字符）里有
    **「A级旅游景区基本情况」表格** —— 这正是用户说的"政务公开里的 A 级景区列表"。
    要确认表里有没有票价/等级/地址，能不能解析成"景区名 → 行"。
  · 文旅部 `/api/marker/province/list` 返回 70KB JSON（全国省份 + 数量），
    但 `/api/marker/list` 报"选择景区类型" —— 参数名要对上（八成是 typeId/type_id）。
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
KEL_PAGE = "https://www.xjkel.gov.cn/xjkrls/c117750/202309/44e21cae67204c6b954db90cb9fe738f.shtml"


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


async def main() -> int:
    import httpx

    headers = {"User-Agent": UA, "Accept-Language": "zh-CN"}
    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
        print("=== ① 库尔勒 A 级景区表")
        resp = await client.get(KEL_PAGE)
        body = resp.text or ""
        tables = re.findall(r"<table.*?</table>", body, flags=re.S | re.I)
        print(f"  页面 {resp.status_code}｜{len(body)} 字符｜表格 {len(tables)} 个")
        for index, table in enumerate(tables):
            text = strip_tags(table)
            if "A级" in text or "景区" in text:
                rows = re.findall(r"<tr.*?</tr>", table, flags=re.S | re.I)
                print(f"  表格#{index}：{len(rows)} 行｜前 3 行：")
                for row in rows[:3]:
                    cells = [strip_tags(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)]
                    print("     ", " | ".join(cell[:24] for cell in cells[:8]))
                flat = strip_tags(table)
                for word in ("门票", "票价", "免费"):
                    if word in flat:
                        spot = flat.find(word)
                        print(f"     含『{word}』：", flat[max(0, spot - 60): spot + 60][:140])
                break

        print("\n=== ② 文旅部 marker 接口参数")
        api_headers = {**headers, "Accept": "application/json", "Referer": "https://lyfw.mct.gov.cn/site/special/scenic"}
        prov = await client.get("https://lyfw.mct.gov.cn/api/marker/province/list", headers=api_headers)
        data = prov.json().get("data") or []
        xinjiang = [item for item in data if "新疆" in str(item.get("name"))]
        print("  省份条目样例:", [(item.get("id"), item.get("name"), item.get("type_id"), item.get("count")) for item in data[:3]])
        print("  新疆相关:", [(item.get("id"), item.get("name"), item.get("type_id"), item.get("count")) for item in xinjiang])
        children = []
        for item in xinjiang:
            for child in item.get("children") or []:
                children.append((child.get("id"), child.get("name"), child.get("type_id"), child.get("count")))
        print("  新疆子级（前 8）:", children[:8])
        type_ids = sorted({str(item.get("type_id")) for item in data if item.get("type_id")} | {str(child[2]) for child in children if child[2]})
        print("  出现过的 type_id:", type_ids[:10])
        for params in (
            {"typeId": type_ids[0] if type_ids else 1, "provinceId": xinjiang[0]["id"] if xinjiang else 650000, "pageNum": 1, "pageSize": 5},
            {"type_id": type_ids[0] if type_ids else 1, "province_id": xinjiang[0]["id"] if xinjiang else 650000, "pageNum": 1, "pageSize": 5},
            {"type": type_ids[0] if type_ids else 1, "pageNum": 1, "pageSize": 5},
        ):
            try:
                listing = await client.get("https://lyfw.mct.gov.cn/api/marker/list", params=params, headers=api_headers)
            except Exception as exc:
                print(f"  marker/list {params} 失败 {type(exc).__name__}")
                continue
            text = (listing.text or "").strip()
            print(f"  marker/list {str(params)[:70]:72s} → {len(text)} 字符｜{text[:130]}")
            if '"name"' in text:
                import json as _json

                payload = _json.loads(text)
                items = (payload.get("data") or {}).get("list") if isinstance(payload.get("data"), dict) else payload.get("data")
                if isinstance(items, list) and items:
                    print("     首条:", _json.dumps(items[0], ensure_ascii=False)[:300])
                    break
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
