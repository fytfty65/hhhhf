"""待确认的官方域名清单：把"像官网"的域名摊给人看，确认后才写进白名单。

为什么要有这一步（2026-10-05 用户口径"把 gov/景区官网页面（白名单里的）视作可核实来源"）：
白名单必须是**人工确认**的 —— 景区官网域名千奇百怪（xxx-scenic.com / xxxlvyou.cn…），
自动信任任意 .com 就等于把"可核实"这四个字废掉。系统只**提议**：从搜索结果里认出
"标题/站点名写着官网、管委会、旅游局、文旅集团"的域名，记进待确认清单；人看完再加进
`WEB_PRICE_HOST_SUFFIXES`。加进去之后，同一类价格才会从"网摘估算"变成"可核实"。

用法：
    # 看清单（含建议的 .env 片段，可直接复制）
    cd ai-service && .\\venv\\Scripts\\python.exe tools\\review_official_domains.py
    # 清掉清单（确认完或不想再收）
    python tools\\review_official_domains.py --clear
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

from core.web_price_tool import (  # noqa: E402
    domain_candidate_path,
    load_domain_candidates,
    official_host_suffixes,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="待确认的官方域名清单（人工确认后才进白名单）")
    parser.add_argument("--clear", action="store_true", help="清空清单（确认完或不想再收）")
    args = parser.parse_args(argv)

    path = domain_candidate_path()
    if args.clear:
        if os.path.exists(path):
            os.remove(path)
            print(f"已清空 {path}")
        else:
            print("清单本来就不存在")
        return 0

    candidates = load_domain_candidates()
    print("=" * 78)
    print("官方域名候选（只提议、不自动信任；确认后写进 WEB_PRICE_HOST_SUFFIXES）")
    print("=" * 78)
    print(f"清单文件：{path}")
    if not candidates:
        print("\n（还没有候选。取价时若「白名单页面没帮上忙」且搜索结果里有像官网的域名，会自动记进来。）")
        return 0

    by_host: dict = {}
    for item in candidates:
        host = str(item.get("host") or "")
        if not host:
            continue
        entry = by_host.setdefault(host, {"host": host, "why": item.get("why") or "", "titles": [], "names": []})
        title = str(item.get("title") or "")
        name = str(item.get("query_name") or "")
        if title and title not in entry["titles"]:
            entry["titles"].append(title)
        if name and name not in entry["names"]:
            entry["names"].append(name)

    print(f"\n候选域名 {len(by_host)} 个：")
    for host, entry in sorted(by_host.items()):
        print(f"  · {host}｜{entry['why']}")
        if entry["names"]:
            print(f"      出现在这些节点的取价里：{'、'.join(entry['names'][:4])}")
        if entry["titles"]:
            print(f"      页面标题：{entry['titles'][0]}")

    print("\n确认过之后，把域名加进 ai-service/.env（逗号分隔，不要带 https://）：")
    print("  WEB_PRICE_HOST_SUFFIXES=" + ",".join(sorted(by_host)))
    print("\n注意：这是「人工确认」环节 —— 请自己打开链接确认那确实是景区/政府官网，")
    print("      再写进白名单；系统不会自动信任这些域名（否则「可核实」就没有意义了）。")
    print(f"\n现阶段白名单后缀：{'、'.join(official_host_suffixes())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
