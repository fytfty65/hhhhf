"""参考路线**图片 → 文字**：视觉模型识别，外加诚实的失败口径。

为什么单独一个模块（而不是塞进 api/operations.py）
------------------------------------------------
1. 这一层要做的事只有一件：把截图里的文字**原样抄出来**，交给既有的
   `core.route_profile.extract_route_profile` 走同一条特征抽取管线。
   于是"粘贴文字"和"上传图片"两条入口在**同一个确定性内核**上汇合，
   不会出现"图片进来看起来更聪明"的两套逻辑。
2. 图片校验（格式/体积/base64）与提示词是可以纯函数测试的；真实调用不可测。
   把不可测的部分压缩到 `recognize_route_text` 一个函数里。

三条诚实口径（都写进了返回值/异常，不靠文档）：
------------------------------------------------
- **不落库**：图片只在内存里转成 data URL 发给视觉模型，服务端不保存图片，
  也不保存识别原文（用户确认后由网关只存抽出来的特征）。
- **不臆造**：看不到文字就如实报 `no_text_in_image`，模型调用失败就报
  `vision_call_failed` 并把原因带出来 —— 绝不返回一段"看起来像行程"的假文本。
- **先核对再学习**：本模块只回文字，不回画像；调用方必须先把文字交给用户核对，
  再由用户决定是否学习。识别错了不会被静默存成偏好。
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core.llm_metrics import record_llm_call  # 视觉调用同样计入 LLM 延迟/失败率/token

# 截图（小红书/朋友圈）常见的三种格式；HEIC 等手机原生格式各家视觉接口支持不一，
# 与其"上传成功然后识别不出来"，不如在入口就说清楚要先转格式。
SUPPORTED_MIME = ("image/png", "image/jpeg", "image/webp")
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4MB：截图足够，超过通常是原图直出

# 识别不到文字时的哨兵：让模型给一个机器可判定的信号，而不是回一句"图片中没有文字"，
# 后者会和真实文本混在一起被当成行程内容。
NO_TEXT_SENTINEL = "NO_TEXT"

TRANSCRIBE_SYSTEM_PROMPT = (
    "你是一个只做文字识别的工具，不是旅行助理。"
    "把图片里的文字**原样**抄写出来，保持原有顺序与换行。"
    "严格遵守：\n"
    "1. 不要总结、不要改写、不要润色、不要翻译；\n"
    "2. 不要补充图片里没有的地名、价格、天数或建议；\n"
    "3. 表情符号、点赞数、水印可以忽略，但地点名/天数/价格/交通/店名必须保留；\n"
    "4. 看不清的字用〔?〕表示，不要猜；\n"
    f"5. 如果这张图里没有任何行程文字（纯风景照、纯人物照），只回 {NO_TEXT_SENTINEL}。"
)

IMAGE_PRIVACY_NOTE = (
    "图片只用于本次文字识别，不会保存到服务端；识别出的文字会显示在下方，"
    "请你核对（可能有错字/漏字）后再决定是否学习。"
)

# 模型常见的"开场白"，会把它们误当成行程正文（例如"这张图片中的文字如下："）
_PREAMBLE_PATTERNS = (
    r"^\s*(?:以下|下面)?(?:是)?(?:这张|该)?(?:图片|照片|截图)(?:中|里)?(?:的)?(?:文字|内容)(?:如下|是)?[：:]\s*",
    r"^\s*(?:图片|截图)(?:文字)?(?:识别结果|内容)(?:如下|是)?[：:]\s*",
    r"^\s*好的[，,]\s*",
)


class OcrUnavailable(Exception):
    """识别不可用/失败：任何情况下都要如实抛出，不许降级成编造的文本。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": False, "code": self.code, "message": self.message}


def sniff_mime(raw: bytes) -> str:
    """按魔数判断图片类型（不看扩展名、不看客户端声明的 MIME）。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(raw) >= 12 and raw[4:8] == b"ftyp" and raw[8:12] in (b"heic", b"heix", b"mif1", b"msf1"):
        return "image/heic"
    return ""


def decode_image(image: str) -> Tuple[bytes, str]:
    """把 data URL 或裸 base64 解成字节，并返回嗅探出来的 MIME。

    只信魔数：客户端把 webp 声明成 png、或干脆不带前缀，都在这里被纠正。
    """
    value = str(image or "").strip()
    if not value:
        raise OcrUnavailable("empty_image", "没有收到图片内容")

    b64 = value
    declared = ""
    if value.startswith("data:"):
        head, _, tail = value.partition(",")
        if not tail:
            raise OcrUnavailable("not_base64", "图片数据格式不对（data URL 缺少 base64 段）")
        b64 = tail
        declared = head[5:].split(";")[0].strip().lower()

    if declared and declared not in SUPPORTED_MIME:
        # 明确声明的格式优先用于报错：此时"嗅探不出格式"会让用户看不懂
        if declared in ("image/heic", "image/heif"):
            raise OcrUnavailable("unsupported_mime", "暂不支持 HEIC（iPhone 原图）：请先截图或转成 PNG/JPEG 再上传")
        raise OcrUnavailable("unsupported_mime", f"暂不支持 {declared}：请上传 PNG / JPEG / WebP 截图")

    try:
        # validate=True 才认得出"这根本不是 base64"；但真实 data URL 里可能有换行，
        # 所以先去掉空白再严格校验（否则会把换行当成非法字符误报）。
        raw = base64.b64decode(re.sub(r"\s+", "", b64), validate=True)
    except (binascii.Error, ValueError):
        raise OcrUnavailable("not_base64", "图片数据不是合法的 base64")

    if not raw:
        raise OcrUnavailable("empty_image", "图片内容为空")
    if len(raw) > MAX_IMAGE_BYTES:
        raise OcrUnavailable(
            "too_large",
            f"图片 {len(raw) // 1024}KB 超过上限 {MAX_IMAGE_BYTES // 1024}KB，请压缩或只截取路线部分",
        )

    mime = sniff_mime(raw)
    if mime not in SUPPORTED_MIME:
        if mime:
            raise OcrUnavailable("unsupported_mime", f"暂不支持 {mime}：请上传 PNG / JPEG / WebP 截图")
        raise OcrUnavailable("unsupported_mime", "这不像一张图片（PNG / JPEG / WebP），请重新选择文件")
    return raw, mime


def build_data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def build_vision_messages(data_url: str) -> List[Dict[str, Any]]:
    """OpenAI 兼容的多模态入参（DashScope 兼容口同样吃这套结构）。"""
    return [
        {"role": "system", "content": TRANSCRIBE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "请把这张图里的文字原样抄出来。"},
            ],
        },
    ]


def clean_transcription(text: str) -> Tuple[str, List[str]]:
    """清掉模型开场白，保留正文；同时把"看起来被截断/太短"的情况标出来。"""
    value = str(text or "").strip()
    value = re.sub(r"^```[a-zA-Z]*\s*|```$", "", value).strip()
    for pattern in _PREAMBLE_PATTERNS:
        new_value = re.sub(pattern, "", value, count=1)
        if new_value != value:
            value = new_value.strip()
    warnings: List[str] = []
    if 0 < len(value) < 12:
        warnings.append("识别到的文字很少，可能是艺术字/低清晰度截图，建议手动补充")
    return value, warnings


async def recognize_route_text(
    image: str,
    *,
    client: Any = None,
    model: Optional[str] = None,
    max_tokens: int = 2048,
) -> Dict[str, Any]:
    """把一张路线截图识别成文字。失败一律抛 `OcrUnavailable`，绝不返回编造文本。"""
    raw, mime = decode_image(image)
    data_url = build_data_url(raw, mime)

    from core.llm_config import vision_model  # 局部导入：便于单测替换

    chosen_model = vision_model(model)
    if client is None:
        client = _default_client()
    if client is None:
        raise OcrUnavailable("vision_not_configured", "服务端未配置视觉模型（缺少 LLM_API_KEY），请改用粘贴文字")

    started = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=chosen_model,
            messages=build_vision_messages(data_url),
            temperature=0.0,
            max_tokens=max_tokens,
        )
    except OcrUnavailable:
        raise
    except Exception as exc:  # 网络/额度/模型名错误都要如实报出来
        record_llm_call(
            model=chosen_model,
            label="route_ocr",
            latency_ms=(time.monotonic() - started) * 1000.0,
            ok=False,
            error=type(exc).__name__,
        )
        raise OcrUnavailable("vision_call_failed", f"图片识别服务调用失败：{str(exc)[:200]}")

    _usage = getattr(response, "usage", None)
    record_llm_call(
        model=chosen_model,
        label="route_ocr",
        latency_ms=(time.monotonic() - started) * 1000.0,
        ok=True,
        prompt_tokens=getattr(_usage, "prompt_tokens", None),
        completion_tokens=getattr(_usage, "completion_tokens", None),
    )

    content = ""
    try:
        content = str(response.choices[0].message.content or "")
    except (AttributeError, IndexError, TypeError):
        content = ""

    text, warnings = clean_transcription(content)
    if not text:
        raise OcrUnavailable("empty_transcription", "识别结果为空，请改粘贴文字")
    if text.strip().upper().startswith(NO_TEXT_SENTINEL):
        raise OcrUnavailable(
            "no_text_in_image",
            "这张图里没有识别到行程文字（可能是纯风景图或清晰度不足），请改粘贴文字或换一张截图",
        )

    return {
        "ok": True,
        "text": text,
        "mime": mime,
        "bytes": len(raw),
        "model": chosen_model,
        "chars": len(text),
        "warnings": warnings,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "privacy_note": IMAGE_PRIVACY_NOTE,
    }


def _default_client() -> Any:
    """按 core/llm_config 的单一来源构造客户端；没有 key 就返回 None（由调用方如实报错）。"""

    from core.llm_config import api_key, base_url

    key = api_key()
    if not key:
        return None
    try:
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=key, base_url=base_url(), timeout=120.0)
    except Exception:
        return None
