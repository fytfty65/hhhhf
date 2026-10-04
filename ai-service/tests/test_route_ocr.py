"""参考路线图片识别测试：校验口径、提示词口径、失败绝不臆造。

真实事故面（这套测试要挡住的）：
  1. 把 HEIC/超限图片放进来，前端"上传成功"，后端识别不出来 → 用户以为功能坏了；
  2. 模型回了"这张图片中的文字如下："被当成行程正文 → 抽取器把废话学成偏好；
  3. 视觉模型调用失败时返回一段兜底文本 → 用户拿到的是**编造的行程**（最严重的一种）；
  4. 纯风景照（没有文字）被当成"识别成功但内容为空"，静默存下空画像。
"""

import asyncio
import base64
import os
import struct
import unittest
import zlib

from core.route_ocr import (
    IMAGE_PRIVACY_NOTE,
    MAX_IMAGE_BYTES,
    NO_TEXT_SENTINEL,
    OcrUnavailable,
    TRANSCRIBE_SYSTEM_PROMPT,
    build_data_url,
    build_vision_messages,
    clean_transcription,
    decode_image,
    recognize_route_text,
    sniff_mime,
)


def png_bytes(width: int = 2, height: int = 2, pad: int = 0) -> bytes:
    """构造一张真实可解的最小 PNG（不依赖 Pillow）。"""
    raw = b""
    for _ in range(height):
        raw += b"\x00" + b"\xff\xff\xff" * width

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    body = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return body + (b"\x00" * pad)


def data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


class _StubCompletions:
    def __init__(self, content=None, error=None, captured=None):
        self._content = content
        self._error = error
        self._captured = captured if captured is not None else {}

    async def create(self, **kwargs):
        self._captured.update(kwargs)

        class _Msg:
            def __init__(self, content):
                self.content = content

        class _Choice:
            def __init__(self, content):
                self.message = _Msg(content)

        class _Resp:
            def __init__(self, content):
                self.choices = [_Choice(content)]

        if self._error:
            raise self._error
        return _Resp(self._content)


class _StubClient:
    def __init__(self, content=None, error=None, captured=None):
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _StubCompletions(content, error, captured)


class ImageValidationTests(unittest.TestCase):
    def test_png_data_url_is_accepted_and_mime_sniffed(self):
        raw, mime = decode_image(data_url(png_bytes()))
        self.assertEqual(mime, "image/png")
        self.assertEqual(raw[:4], b"\x89PNG")

    def test_declared_mime_is_not_trusted_over_magic_bytes(self):
        """客户端把 webp 声明成 png 时，按魔数纠正 —— 否则视觉接口会报格式错。"""
        fake_webp = b"RIFF" + struct.pack("<I", 16) + b"WEBP" + b"VP8 " + b"\x00" * 8
        _, mime = decode_image(data_url(fake_webp, "image/png"))
        self.assertEqual(mime, "image/webp")

    def test_raw_base64_without_prefix_still_works(self):
        raw, mime = decode_image(base64.b64encode(png_bytes()).decode("ascii"))
        self.assertEqual(mime, "image/png")

    def test_heic_gets_an_actionable_message(self):
        heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16
        with self.assertRaises(OcrUnavailable) as ctx:
            decode_image(data_url(heic, "image/heic"))
        self.assertEqual(ctx.exception.code, "unsupported_mime")
        self.assertIn("HEIC", ctx.exception.message)

    def test_gif_is_rejected_with_supported_formats(self):
        with self.assertRaises(OcrUnavailable) as ctx:
            decode_image(data_url(b"GIF89a" + b"\x00" * 12, "image/gif"))
        self.assertEqual(ctx.exception.code, "unsupported_mime")
        self.assertIn("PNG", ctx.exception.message)

    def test_oversized_image_is_rejected_with_size(self):
        with self.assertRaises(OcrUnavailable) as ctx:
            decode_image(data_url(png_bytes(pad=MAX_IMAGE_BYTES)))
        self.assertEqual(ctx.exception.code, "too_large")

    def test_not_base64_is_rejected(self):
        with self.assertRaises(OcrUnavailable) as ctx:
            decode_image("data:image/png;base64,!!!!")
        self.assertEqual(ctx.exception.code, "not_base64")

    def test_empty_image_is_rejected(self):
        with self.assertRaises(OcrUnavailable) as ctx:
            decode_image("   ")
        self.assertEqual(ctx.exception.code, "empty_image")

    def test_sniff_mime_recognises_supported_formats(self):
        self.assertEqual(sniff_mime(png_bytes()), "image/png")
        self.assertEqual(sniff_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 8), "image/jpeg")
        self.assertEqual(sniff_mime(b"not an image"), "")


class PromptContractTests(unittest.TestCase):
    def test_prompt_demands_verbatim_and_forbids_summarising(self):
        """提示词一旦被改成"总结一下"，用户核对过的文本就和图不一致了。"""
        self.assertIn("原样", TRANSCRIBE_SYSTEM_PROMPT)
        self.assertIn("不要总结", TRANSCRIBE_SYSTEM_PROMPT)
        self.assertIn("不要补充", TRANSCRIBE_SYSTEM_PROMPT)
        self.assertIn(NO_TEXT_SENTINEL, TRANSCRIBE_SYSTEM_PROMPT)

    def test_privacy_note_states_image_is_not_stored(self):
        self.assertIn("不会保存", IMAGE_PRIVACY_NOTE)

    def test_vision_messages_carry_the_image_and_the_prompt(self):
        url = build_data_url(png_bytes(), "image/png")
        messages = build_vision_messages(url)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], TRANSCRIBE_SYSTEM_PROMPT)
        parts = messages[1]["content"]
        self.assertEqual(parts[0]["image_url"]["url"], url)
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))


class CleanTranscriptionTests(unittest.TestCase):
    def test_model_preamble_is_stripped(self):
        text, _ = clean_transcription("这张图片中的文字如下：\n第一天 博斯腾湖 门票45元")
        self.assertTrue(text.startswith("第一天"))

    def test_code_fences_are_stripped(self):
        text, _ = clean_transcription("```\nDay1 天山天池\n```")
        self.assertIn("Day1", text)
        self.assertNotIn("```", text)

    def test_short_text_is_flagged_not_silently_accepted(self):
        text, warnings = clean_transcription("文本")
        self.assertEqual(text, "文本")
        self.assertTrue(warnings)


class RecognizeRouteTextTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_success_returns_text_model_and_privacy_note(self):
        captured = {}
        client = _StubClient(content="第一天 博斯腾湖 环湖骑行\n第二天 库车老街 烤包子", captured=captured)
        result = self._run(recognize_route_text(data_url(png_bytes()), client=client, model="qwen-vl-plus"))
        self.assertTrue(result["ok"])
        self.assertIn("博斯腾湖", result["text"])
        self.assertEqual(result["model"], "qwen-vl-plus")
        self.assertEqual(result["mime"], "image/png")
        self.assertEqual(result["chars"], len(result["text"]))
        self.assertIn("不会保存", result["privacy_note"])
        # 出参里不该出现原文以外的原始图片字节/ base64
        self.assertNotIn("data:image", str(result))
        self.assertEqual(captured["model"], "qwen-vl-plus")
        self.assertEqual(captured["temperature"], 0.0)

    def test_model_failure_is_reported_never_faked(self):
        client = _StubClient(error=RuntimeError("429 rate limited"))
        with self.assertRaises(OcrUnavailable) as ctx:
            self._run(recognize_route_text(data_url(png_bytes()), client=client))
        self.assertEqual(ctx.exception.code, "vision_call_failed")
        self.assertIn("429", ctx.exception.message)

    def test_no_text_in_image_uses_the_sentinel(self):
        client = _StubClient(content=NO_TEXT_SENTINEL)
        with self.assertRaises(OcrUnavailable) as ctx:
            self._run(recognize_route_text(data_url(png_bytes()), client=client))
        self.assertEqual(ctx.exception.code, "no_text_in_image")

    def test_blank_model_output_is_an_error_not_an_empty_plan(self):
        client = _StubClient(content="   ")
        with self.assertRaises(OcrUnavailable) as ctx:
            self._run(recognize_route_text(data_url(png_bytes()), client=client))
        self.assertEqual(ctx.exception.code, "empty_transcription")

    def test_missing_api_key_is_reported_as_not_configured(self):
        saved = os.environ.pop("LLM_API_KEY", None)
        try:
            with self.assertRaises(OcrUnavailable) as ctx:
                self._run(recognize_route_text(data_url(png_bytes())))
            self.assertEqual(ctx.exception.code, "vision_not_configured")
        finally:
            if saved is not None:
                os.environ["LLM_API_KEY"] = saved

    def test_validation_happens_before_any_model_call(self):
        """格式不对时不许先烧一次模型调用。"""
        captured = {}
        client = _StubClient(content="x", captured=captured)
        with self.assertRaises(OcrUnavailable):
            self._run(recognize_route_text("data:image/heic;base64,AAAA", client=client))
        self.assertEqual(captured, {})


if __name__ == "__main__":
    unittest.main()
