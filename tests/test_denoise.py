"""tools/denoise.py 单元测试:合成 md/json,不依赖 docling-serve。

运行: python -m unittest discover tests
每条规则至少覆盖一个"删"用例和一个"宁留勿删"用例。
"""

from __future__ import annotations

import unittest

from tools.denoise import (
    DenoiseResult,
    _collect_cross_page_repeats,
    _collect_furniture_texts,
    _Config,
    denoise_markdown,
    render_audit_json,
    render_audit_md,
)


def _json_text(text, label="text", pages=(1,), content_layer="body"):
    return {
        "text": text,
        "label": label,
        "content_layer": content_layer,
        "prov": [{"page_no": p} for p in pages],
    }


class TestR1Furniture(unittest.TestCase):
    """R1: JSON furniture/页眉页脚整行删除。"""

    def test_delete_page_header_line(self):
        md = "机密文件\n\n正文第一段。\n\n机密文件\n\n正文第二段。\n"
        js = {"texts": [
            _json_text("机密文件", label="page_header", pages=(1, 2), content_layer="furniture"),
            _json_text("正文第一段。"),
            _json_text("正文第二段。"),
        ]}
        result = denoise_markdown(md, js)
        self.assertNotIn("机密文件", result.markdown)
        self.assertIn("正文第一段。", result.markdown)
        self.assertEqual(result.stats["R1"], 2)

    def test_keep_when_embedded_in_paragraph(self):
        # 同名文本出现在段落中间(非整行)时不删
        md = "本文件为机密文件级别,请注意保密。\n"
        js = {"texts": [
            _json_text("机密文件", label="page_header", pages=(1,), content_layer="furniture"),
        ]}
        result = denoise_markdown(md, js)
        self.assertEqual(result.markdown, md)
        self.assertEqual(result.stats["R1"], 0)


class TestR2CrossPageRepeat(unittest.TestCase):
    """R2: 跨 >=3 页重复的短文本删除。"""

    def test_delete_watermark_across_pages(self):
        md = "内部资料 请勿外传\n\n第一段。\n\n内部资料 请勿外传\n\n第二段。\n\n内部资料 请勿外传\n"
        js = {"texts": [
            _json_text("内部资料 请勿外传", pages=(1, 2, 3)),
            _json_text("第一段。", pages=(1,)),
        ]}
        result = denoise_markdown(md, js)
        self.assertNotIn("内部资料", result.markdown)
        self.assertIn("第一段。", result.markdown)
        self.assertEqual(result.stats["R2"], 3)

    def test_keep_when_only_two_pages(self):
        js = {"texts": [_json_text("内部资料 请勿外传", pages=(1, 2))]}
        result = denoise_markdown("内部资料 请勿外传\n\n内部资料 请勿外传\n", js)
        self.assertEqual(result.stats["R2"], 0)
        self.assertIn("内部资料", result.markdown)

    def test_keep_section_header_repeats(self):
        # 章节标题跨页重复是合法的(如"修订记录"分节)
        js = {"texts": [_json_text("修订记录", label="section_header", pages=(1, 2, 3, 4))]}
        repeats = _collect_cross_page_repeats(js, _Config())
        self.assertEqual(repeats, {})

    def test_keep_same_page_repeats(self):
        # 同一页正文重复引用术语不算(按不同页数计数)
        js = {"texts": [_json_text("系统应当", pages=(1,)), _json_text("系统应当", pages=(1,))]}
        repeats = _collect_cross_page_repeats(js, _Config())
        self.assertEqual(repeats, {})

    def test_keep_pure_digits(self):
        # 纯数字留给 R3 的孤立页码规则
        js = {"texts": [_json_text("12", pages=(1, 2, 3, 4))]}
        repeats = _collect_cross_page_repeats(js, _Config())
        self.assertEqual(repeats, {})


class TestR3PageNumber(unittest.TestCase):
    """R3: 孤立页码行。"""

    def test_delete_isolated_page_number(self):
        md = "第一段内容。\n\n12\n\n第二段内容。\n"
        result = denoise_markdown(md)
        self.assertNotIn("\n12\n", result.markdown)
        self.assertEqual(result.stats["R3"], 1)

    def test_delete_page_number_variants(self):
        for variant in ("- 12 -", "第 12 页", "12/120"):
            md = f"上文。\n\n{variant}\n\n下文。\n"
            result = denoise_markdown(md)
            self.assertNotIn(variant, result.markdown, variant)

    def test_keep_number_in_context(self):
        # "版本 12 说明"不是页码模式
        md = "上文。\n\n版本 12 说明\n\n下文。\n"
        result = denoise_markdown(md)
        self.assertIn("版本 12 说明", result.markdown)
        self.assertEqual(result.stats["R3"], 0)

    def test_keep_non_isolated_number(self):
        # 紧跟段落的数字行不满足孤立性
        md = "总分:\n12\n\n下文。\n"
        result = denoise_markdown(md)
        self.assertIn("\n12\n", result.markdown)


class TestR4EmptyImagePlaceholder(unittest.TestCase):
    """R4: 空 image 占位符。"""

    def test_delete_placeholder_without_description(self):
        md = "上文。\n\n<!-- image -->\n\n## 下一节\n\n内容。\n"
        result = denoise_markdown(md)
        self.assertNotIn("<!-- image -->", result.markdown)
        self.assertEqual(result.stats["R4"], 1)

    def test_keep_placeholder_with_description(self):
        md = "上文。\n\n<!-- image -->\n流程图:用户提交订单后进入支付环节。\n\n下文。\n"
        result = denoise_markdown(md)
        self.assertIn("<!-- image -->", result.markdown)
        self.assertEqual(result.stats["R4"], 0)


class TestR5BlankLines(unittest.TestCase):
    def test_compress_blank_runs(self):
        md = "第一段。\n\n\n\n\n\n第二段。"
        result = denoise_markdown(md)
        self.assertEqual(result.markdown, "第一段。\n\n\n第二段。")

    def test_keep_two_blanks(self):
        md = "第一段。\n\n\n第二段。"
        result = denoise_markdown(md)
        self.assertEqual(result.markdown, md)


class TestSafetyBrake(unittest.TestCase):
    """安全刹车:删除占比超限整单放弃。"""

    def test_abort_when_delete_ratio_exceeded(self):
        # 10 行里 8 行是跨页重复噪音(>30%),应触发刹车保留原文
        noise = ["内部资料"] * 8
        md = "\n".join(["正文一。", *noise, "正文二。"]) + "\n"
        js = {"texts": [_json_text("内部资料", pages=(1, 2, 3))]}
        result = denoise_markdown(md, js)
        self.assertTrue(result.aborted)
        self.assertEqual(result.markdown, md)
        self.assertEqual(result.stats.get("aborted"), 1)


class TestNoJsonDegrade(unittest.TestCase):
    """json_content=None 时 R1/R2 跳过,R3-R5 照常。"""

    def test_degrade_gracefully(self):
        md = "上文。\n\n7\n\n<!-- image -->\n\n\n\n\n下文。\n"
        result = denoise_markdown(md, None)
        self.assertEqual(result.stats["R1"], 0)
        self.assertEqual(result.stats["R2"], 0)
        self.assertEqual(result.stats["R3"], 1)
        self.assertEqual(result.stats["R4"], 1)
        self.assertIn("上文。", result.markdown)
        self.assertIn("下文。", result.markdown)
        self.assertNotIn("<!-- image -->", result.markdown)


class TestAuditOutput(unittest.TestCase):
    def test_audit_json_and_md(self):
        md = "机密文件\n\n正文。\n"
        js = {"texts": [_json_text("机密文件", label="page_footer", pages=(3,), content_layer="furniture")]}
        result = denoise_markdown(md, js)
        audit = render_audit_json(result)
        self.assertIn('"rule_id": "R1"', audit)
        self.assertIn('"page_no": 3', audit)
        audit_md = render_audit_md(result, "测试文档")
        self.assertIn("测试文档", audit_md)
        self.assertIn("机密文件", audit_md)

    def test_audit_md_aborted_notice(self):
        result = DenoiseResult(markdown="x", aborted=True)
        audit_md = render_audit_md(result, "doc")
        self.assertIn("已保留原文", audit_md)


if __name__ == "__main__":
    unittest.main()
