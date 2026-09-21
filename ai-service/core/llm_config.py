"""LLM 调用参数的**唯一事实来源**（A 项：消灭"两套默认值"）。

为什么要有这个文件（2026-09-19 实测出来的真实隐患）
--------------------------------------------------
`api/agent.py` 与 `api/conflict.py` 读的是**同一个环境变量** `LLM_MODEL_NAME`，
但各自写了一个**不同的硬编码兜底值**：

    agent.py    : os.getenv("LLM_MODEL_NAME", "qwen-turbo")
    conflict.py : os.getenv("LLM_MODEL_NAME", "qwen2.5:0.5b")   ← 本地 Ollama 风格的名字

于是只要环境变量没配（或某次部署漏配），主推演走 `qwen-turbo`，冲突检测却去调
`qwen2.5:0.5b` —— 在 DashScope 上必然失败，而且失败得很隐蔽（"冲突检测没结果"，
排查半天才发现是模型名不同）。这类"两套默认值"的坑必须靠**单一来源 + 单测**堵住，不能靠记性。

用法：`from core.llm_config import model_name, temperature, max_tokens`
"""

from __future__ import annotations

import os
from typing import Optional

# 默认值只在这里定义一次
DEFAULT_MODEL_NAME = "qwen-turbo"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 4096
DEFAULT_LONG_MAX_TOKENS = 16384
# 端点兜底也要统一：conflict.py 原来兜底到本地 Ollama（http://localhost:11434/v1 + "ollama"），
# 而部署目标是 DashScope 兼容口 —— 环境变量一漏配就会去连一个根本不存在的本地模型服务。
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def base_url(override: Optional[str] = None) -> str:
    if override and str(override).strip():
        return str(override).strip()
    return str(os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL


def api_key(override: Optional[str] = None) -> str:
    if override and str(override).strip():
        return str(override).strip()
    return str(os.getenv("LLM_API_KEY") or "").strip()


def model_name(override: Optional[str] = None) -> str:
    """统一的模型名解析：显式传入 > 环境变量 > 默认值。"""
    if override and str(override).strip():
        return str(override).strip()
    return str(os.getenv("LLM_MODEL_NAME") or DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME


def temperature(default: float = DEFAULT_TEMPERATURE) -> float:
    raw = str(os.getenv("LLM_TEMPERATURE") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def max_tokens(long_output: bool = False) -> int:
    """写长文（整份路书）时用大额度，短任务（冲突检测/润色）用小额度。"""
    key = "LLM_MAX_TOKENS_LONG" if long_output else "LLM_MAX_TOKENS"
    fallback = DEFAULT_LONG_MAX_TOKENS if long_output else DEFAULT_MAX_TOKENS
    raw = str(os.getenv(key) or "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
        return value if value > 0 else fallback
    except ValueError:
        return fallback
