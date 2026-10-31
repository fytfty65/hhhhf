"""把**客户端渲染**的官方页面渲染成正文文本（2026-10-07，用户同意上无头浏览器）。

为什么需要：罗布人村寨（xjlbr.com）与巴音布鲁克（byblk.cn）的票价页服务端只回壳页
（2490 / 1432 字符），票价在 JS 渲染之后才有。实测用 Playwright 渲染：
  · **巴音布鲁克** `/goumaiyuding/` → 正文里明确写着「票价：140元（包含门票+区间车票）」✓
  · 罗布人村寨 → 渲染后仍**没有票价文本**（票价只在微信小程序里公布）→ 这条就如实说

实现要点（都是踩过的坑）：
  · 渲染器放在 **front/**（Playwright 与浏览器装在那侧），Python 用子进程调用：
    `node front/scripts/render-page.mjs <url>`，它回一行 JSON；
  · **不能用 `networkidle`**：罗布人村寨首页挂着长轮询接口，30s 都等不到网络空闲 → 用
    `domcontentloaded` + 固定等待（渲染器内部处理）；
  · 结果**落盘缓存**（默认 24h，可关）：一个官方票价页没必要每次规划都渲染一遍；
  · 严格超时（默认 25s）+ 失败如实返回（不抛异常、不影响其它取价通道）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, Optional

DEFAULT_TIMEOUT_SECONDS = 25.0


def renderer_script() -> str:
    """渲染器路径（默认仓库内 `front/scripts/render-page.mjs`）。"""
    raw = str(os.getenv("OFFICIAL_RENDER_SCRIPT") or "").strip()
    if raw:
        return raw
    service_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(service_root, "..", "front", "scripts", "render-page.mjs")


def render_cache_path() -> str:
    """渲染结果缓存文件（默认 `ai-service/official_render_cache.json`）。

    ⚠️ 这里以前多拼了一层 `..`：它先取到 service_root，又 join 了 `".."`，
    于是缓存被写到**仓库根目录**，而同一批运行时缓存
    （`mct_marker_cache.json` / `official_directory_cache.json` /
    `official_domain_candidates.json` / `web_price_usage.json`）全都写在
    `ai-service/` 下 —— 结果就是根目录出现一份 13,908 字节的真缓存，
    而 `ai-service/official_render_cache.json` 永远是一份 2 字节的 `{}`。
    除了一致性，这也让"哪些文件是运行时产生的"变得难以判断。

    判定基准：与本目录其它缓存函数保持一致，即
    `os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "<name>.json")`，
    用 `normpath` 消掉 `..` 以免路径里带上一串 `..` 让人误判层级。
    """
    raw = str(os.getenv("OFFICIAL_RENDER_CACHE_FILE") or "").strip()
    if raw:
        return raw
    service_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.normpath(os.path.join(service_root, "official_render_cache.json"))


def render_cache_ttl_seconds() -> float:
    raw = str(os.getenv("OFFICIAL_RENDER_CACHE_TTL_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 86400.0
    return value if value >= 0 else 86400.0


def _load_cache() -> Dict[str, Any]:
    try:
        with open(render_cache_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data: Dict[str, Any]) -> None:
    try:
        with open(render_cache_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _cached(url: str) -> Optional[Dict[str, Any]]:
    entry = _load_cache().get(str(url))
    if not isinstance(entry, dict):
        return None
    if render_cache_ttl_seconds() <= 0:
        return None
    if time.time() - float(entry.get("fetched_at") or 0.0) > render_cache_ttl_seconds():
        return None
    if not entry.get("text"):
        return None
    return {"ok": True, "url": url, "text": str(entry.get("text")), "seconds": entry.get("seconds"), "cached": True}


async def render_page_text(url: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """渲染一个页面并返回 `{ok, url, text, seconds, cached?}`；失败回 `{ok: False, error}`。"""
    target = str(url or "").strip()
    if not target:
        return {"ok": False, "url": target, "error": "空 URL"}
    cached = _cached(target)
    if cached:
        return cached
    script = renderer_script()
    if not os.path.exists(script):
        return {"ok": False, "url": target, "error": f"渲染器不存在：{script}"}
    limit = float(timeout or os.getenv("OFFICIAL_RENDER_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(os.path.dirname(script), "..", "..", "work", "ms-playwright"))
    try:
        process = await asyncio.create_subprocess_exec(
            "node",
            script,
            target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            process.kill()
            return {"ok": False, "url": target, "error": f"渲染超时（{limit:g}s）"}
    except Exception as exc:
        return {"ok": False, "url": target, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    line = ""
    for candidate in reversed((stdout or b"").decode("utf-8", errors="replace").splitlines()):
        if candidate.strip().startswith("{"):
            line = candidate.strip()
            break
    if not line:
        return {"ok": False, "url": target, "error": "渲染器没有输出 JSON"}
    try:
        payload = json.loads(line)
    except Exception as exc:
        return {"ok": False, "url": target, "error": f"渲染输出解析失败（{type(exc).__name__}）"}
    if payload.get("ok") and payload.get("text"):
        cache = _load_cache()
        cache[target] = {
            "fetched_at": time.time(),
            "text": str(payload.get("text")),
            "seconds": payload.get("seconds"),
        }
        _save_cache(cache)
    return payload if isinstance(payload, dict) else {"ok": False, "url": target, "error": "渲染输出不是对象"}
