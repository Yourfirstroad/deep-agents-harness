"""tools/docling_tool.py 单元测试:monkeypatch requests,不碰真实服务。

运行: python -m unittest discover tests
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.docling_tool import (
    CleanedParse,
    DoclingResult,
    _build_options,
    convert_document,
    denoise_summary_line,
    parse_and_clean,
    save_parse_outputs,
)
from tools.denoise import DenoiseResult, RemovalRecord


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def _fake_server(md: str, json_content: dict | None):
    """构造 requests.post/get 的 fake:提交 -> 轮询 -> 拉结果。"""
    posts: list[dict] = []

    def fake_post(url, files=None, data=None, headers=None, timeout=None):
        posts.append(list(data))  # 保留重复键(to_formats 拆成多个同名字段)
        return _FakeResponse({"task_id": "t-1"})

    def fake_get(url, headers=None, timeout=None):
        if "/status/" in url:
            return _FakeResponse({"task_status": "success"})
        document = {"md_content": md}
        if json_content is not None:
            document["json_content"] = json_content
        return _FakeResponse({"document": document})

    return posts, fake_post, fake_get


class TestBuildOptions(unittest.TestCase):
    def test_to_formats_includes_json_when_denoise_on(self):
        with mock.patch.dict(os.environ, {"DOCLING_DENOISE": "true"}):
            self.assertEqual(_build_options()["to_formats"], ["md", "json"])

    def test_to_formats_md_only_when_denoise_off(self):
        with mock.patch.dict(os.environ, {"DOCLING_DENOISE": "false"}):
            self.assertEqual(_build_options()["to_formats"], ["md"])


class TestConvertDocument(unittest.TestCase):
    def test_returns_docling_result_with_json(self):
        md = "机密文件\n\n正文。\n"
        js = {"texts": [{"text": "机密文件", "label": "page_header",
                         "content_layer": "furniture", "prov": [{"page_no": 1}]}]}
        posts, fake_post, fake_get = _fake_server(md, js)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-fake")
            path = f.name
        try:
            with mock.patch("tools.docling_tool.requests.post", fake_post), \
                 mock.patch("tools.docling_tool.requests.get", fake_get), \
                 mock.patch.dict(os.environ, {"DOCLING_DENOISE": "true"}):
                result = convert_document(path)
        finally:
            Path(path).unlink(missing_ok=True)

        self.assertIsInstance(result, DoclingResult)
        self.assertEqual(result.markdown, md)
        self.assertEqual(result.json_content, js)
        # 表单里 to_formats 同时含 md 和 json(列表拆同名字段)
        to_formats = [v for k, v in posts[0] if k == "to_formats"]
        self.assertEqual(sorted(to_formats), ["json", "md"])

    def test_tolerates_missing_json_content(self):
        posts, fake_post, fake_get = _fake_server("正文。\n", None)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-fake")
            path = f.name
        try:
            with mock.patch("tools.docling_tool.requests.post", fake_post), \
                 mock.patch("tools.docling_tool.requests.get", fake_get):
                result = convert_document(path)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertIsNone(result.json_content)


class TestParseAndCleanAndSave(unittest.TestCase):
    def test_denoise_applied_and_outputs_saved(self):
        md = "机密文件\n\n正文第一段。\n"
        js = {"texts": [{"text": "机密文件", "label": "page_header",
                         "content_layer": "furniture", "prov": [{"page_no": 1}]}]}
        _, fake_post, fake_get = _fake_server(md, js)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-fake")
            path = f.name
        try:
            with mock.patch("tools.docling_tool.requests.post", fake_post), \
                 mock.patch("tools.docling_tool.requests.get", fake_get), \
                 mock.patch.dict(os.environ, {"DOCLING_DENOISE": "true"}):
                parsed = parse_and_clean(path)
        finally:
            Path(path).unlink(missing_ok=True)

        self.assertEqual(parsed.raw_markdown, md)
        self.assertNotIn("机密文件", parsed.cleaned_markdown)
        self.assertIn("正文第一段。", parsed.cleaned_markdown)
        self.assertIsNotNone(parsed.denoise)

        saved = save_parse_outputs("单测文档", parsed)
        self.assertTrue(saved["md"].read_text(encoding="utf-8") == parsed.cleaned_markdown)
        self.assertTrue(saved["raw_md"].read_text(encoding="utf-8") == md)
        audit = json.loads(saved["audit"].read_text(encoding="utf-8"))
        self.assertEqual(audit["removals"][0]["rule_id"], "R1")
        for p in saved.values():
            p.unlink(missing_ok=True)

    def test_denoise_disabled_keeps_raw(self):
        _, fake_post, fake_get = _fake_server("正文。\n", None)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-fake")
            path = f.name
        try:
            with mock.patch("tools.docling_tool.requests.post", fake_post), \
                 mock.patch("tools.docling_tool.requests.get", fake_get), \
                 mock.patch.dict(os.environ, {"DOCLING_DENOISE": "false"}):
                parsed = parse_and_clean(path)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertIsNone(parsed.denoise)
        self.assertEqual(parsed.cleaned_markdown, parsed.raw_markdown)


class TestSummaryLine(unittest.TestCase):
    def test_disabled(self):
        parsed = CleanedParse("x", "x", None, None)
        self.assertEqual(denoise_summary_line(parsed), "")

    def test_aborted(self):
        parsed = CleanedParse("x", "x", DenoiseResult("x", aborted=True), None)
        self.assertIn("已保留原始", denoise_summary_line(parsed))

    def test_with_removals(self):
        d = DenoiseResult("x", removals=[RemovalRecord("R2", "跨页重复文本", "机密", 1, 3)],
                          stats={"R2": 1})
        parsed = CleanedParse("x", "x", d, None)
        line = denoise_summary_line(parsed)
        self.assertIn("1 处", line)
        self.assertIn("宁留勿删", line)


if __name__ == "__main__":
    unittest.main()
