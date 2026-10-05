"""离线验证搜狗解析器：拿已保存的真实页面跑解析，打印每条结果的关键字段。

用法：python tools/parse_sogou_sample.py [work/sogou-sample.html]
（先用 tools/dump_sogou_sample.py 抓一份真实页面；这样调试解析规则不烧网络也不打扰对方站点。）
"""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.web_price_tool import extract_price_candidates, parse_sogou_results  # noqa: E402


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "..", "work", "sogou-sample.html")
    path = os.path.abspath(path)
    if not os.path.exists(path):
        print(f"❌ 找不到样本文件: {path}（先跑 tools/dump_sogou_sample.py）")
        return 1
    with io.open(path, encoding="utf-8", errors="ignore") as handle:
        body = handle.read()
    rows = parse_sogou_results(body, limit=8)
    print(f"样本 {len(body)} 字节 → 解析出 {len(rows)} 条\n")
    hits = 0
    for index, row in enumerate(rows, start=1):
        prices = extract_price_candidates(f"{row['title']} {row['snippet']}")
        if prices:
            hits += 1
        print(f"[{index}] title={row['title'][:46]!r}")
        print(f"    url={(row['url'] or '(空)')[:80]}")
        print(f"    cite={row['cite_site']!r} date={row['cite_date']!r}")
        print(f"    snippet={row['snippet'][:80]!r}")
        if prices:
            print(f"    💰 {[item['value'] for item in prices[:3]]}")
    print(f"\n含价格候选: {hits}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
