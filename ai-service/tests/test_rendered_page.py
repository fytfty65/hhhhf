"""无头浏览器渲染器（`core/rendered_page.py`）的测试（2026-10-07）。

这一层最容易悄悄坏掉，所以把三件事钉住：
  ① 渲染器输出的是"最后一行 JSON"（Playwright/Node 可能先打警告），解析要稳；
  ② **落盘缓存**生效（一个官方票价页不必每次规划都渲染），TTL 过期要重新渲染；
  ③ 失败（脚本不存在/超时/非 JSON）一律如实返回 `ok=False`，绝不抛异常打断取价链路。
"""

import asyncio
import io
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import rendered_page  # noqa: E402


class RenderedPageTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._old_env = {
            "OFFICIAL_RENDER_CACHE_FILE": os.environ.get("OFFICIAL_RENDER_CACHE_FILE"),
            "OFFICIAL_RENDER_SCRIPT": os.environ.get("OFFICIAL_RENDER_SCRIPT"),
            "OFFICIAL_RENDER_CACHE_TTL_SECONDS": os.environ.get("OFFICIAL_RENDER_CACHE_TTL_SECONDS"),
        }
        os.environ["OFFICIAL_RENDER_CACHE_FILE"] = os.path.join(self._dir.name, "cache.json")
        os.environ["OFFICIAL_RENDER_CACHE_TTL_SECONDS"] = "3600"

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._dir.cleanup()

    def _write_fake_renderer(self, body: str) -> str:
        path = os.path.join(self._dir.name, "fake_render.mjs")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.environ["OFFICIAL_RENDER_SCRIPT"] = path
        return path

    def test_parses_the_last_json_line(self):
        self._write_fake_renderer(
            "console.log('warning: something');\n"
            "console.log(JSON.stringify({ok: true, url: 'https://x/', text: '票价：140元', seconds: 1.5}));\n"
        )
        result = asyncio.run(rendered_page.render_page_text("https://x/"))
        self.assertTrue(result["ok"])
        self.assertIn("140元", result["text"])
        self.assertEqual(result["seconds"], 1.5)

    def test_result_is_cached_on_disk_and_reused(self):
        self._write_fake_renderer(
            "console.log(JSON.stringify({ok: true, url: 'https://y/', text: '第一次', seconds: 2}));\n"
        )
        first = asyncio.run(rendered_page.render_page_text("https://y/"))
        self.assertTrue(first["ok"])
        # 第二次：把渲染器换成"必失败"，若仍返回第一次的正文就说明走了缓存
        self._write_fake_renderer("console.log(JSON.stringify({ok: false, error: '不该被调用'}));\n")
        second = asyncio.run(rendered_page.render_page_text("https://y/"))
        self.assertTrue(second["ok"])
        self.assertTrue(second.get("cached"))
        self.assertEqual(second["text"], "第一次")
        with io.open(rendered_page.render_cache_path(), "r", encoding="utf-8") as handle:
            self.assertIn("https://y/", json.load(handle))

    def test_expired_cache_is_ignored(self):
        self._write_fake_renderer(
            "console.log(JSON.stringify({ok: true, url: 'https://z/', text: '新内容', seconds: 1}));\n"
        )
        asyncio.run(rendered_page.render_page_text("https://z/"))
        os.environ["OFFICIAL_RENDER_CACHE_TTL_SECONDS"] = "0"
        try:
            again = asyncio.run(rendered_page.render_page_text("https://z/"))
        finally:
            os.environ["OFFICIAL_RENDER_CACHE_TTL_SECONDS"] = "3600"
        self.assertFalse(again.get("cached"))
        self.assertEqual(again["text"], "新内容")

    def test_missing_renderer_is_reported_not_raised(self):
        os.environ["OFFICIAL_RENDER_SCRIPT"] = os.path.join(self._dir.name, "没有这个文件.mjs")
        result = asyncio.run(rendered_page.render_page_text("https://a/"))
        self.assertFalse(result["ok"])
        self.assertIn("渲染器不存在", result["error"])

    def test_non_json_output_is_reported(self):
        self._write_fake_renderer("console.log('这不是 JSON');\n")
        result = asyncio.run(rendered_page.render_page_text("https://b/"))
        self.assertFalse(result["ok"])
        self.assertIn("JSON", result["error"])

    def test_empty_url_is_rejected(self):
        result = asyncio.run(rendered_page.render_page_text(""))
        self.assertFalse(result["ok"])
        self.assertIn("空 URL", result["error"])


if __name__ == "__main__":
    unittest.main()
