"""middleware/file_upload.py 单元测试。

聚焦 _doc_stages_block(状态机判定)与流程指令注入的关键分支。
不依赖 LangGraph runtime,只校验纯函数行为。
"""

from __future__ import annotations

import unittest

from middleware.file_upload import FileUploadMiddleware


def _upload(name: str = "需求文档.md", size: int = 1234) -> dict:
    return {
        "path": f"/uploads/{name}",
        "name": name,
        "size": size,
        "uploaded_at": "2026-09-23T00:00:00Z",
    }


class TestDocStagesBlock(unittest.TestCase):
    def test_stage_a_when_no_analysis_files(self):
        # 阶段 A:只有上传,没有 /analysis 产物
        uploads = [_upload()]
        files: dict = {}
        out = FileUploadMiddleware._doc_stages_block(uploads, files)
        self.assertIn("(状态 A)", out)
        self.assertIn("requirement-rough-scanner", out)
        self.assertIn("未开始", out)

    def test_stage_b_when_only_rough_scan_exists(self):
        # 阶段 B:rough-scan 已存在,test-points 不存在
        uploads = [_upload()]
        files = {"/analysis/需求文档-rough-scan.md": {}}
        out = FileUploadMiddleware._doc_stages_block(uploads, files)
        self.assertIn("(状态 B)", out)
        self.assertIn("已粗扫,待询问 scope", out)
        self.assertIn("request_scope_selection", out)

    def test_stage_c_when_only_test_points_exists(self):
        # 阶段 C:test-points 已存在,testcases 不存在
        uploads = [_upload()]
        files = {
            "/analysis/需求文档-rough-scan.md": {},
            "/analysis/需求文档-test-points.md": {},
        }
        out = FileUploadMiddleware._doc_stages_block(uploads, files)
        self.assertIn("(状态 C)", out)
        self.assertIn("已做详细分析,待用例设计", out)
        self.assertIn("request_approval", out)

    def test_stage_d_when_only_test_cases_exists(self):
        # 阶段 D:testcases 已存在,review 不存在
        uploads = [_upload()]
        files = {
            "/analysis/需求文档-rough-scan.md": {},
            "/analysis/需求文档-test-points.md": {},
            "/testcases/需求文档-testcases.md": {},
        }
        out = FileUploadMiddleware._doc_stages_block(uploads, files)
        self.assertIn("(状态 D)", out)
        self.assertIn("已设计用例,待 lint + 评审", out)
        self.assertIn("testcase-reviewer", out)

    def test_stage_e_when_review_exists(self):
        # 阶段 E:review 已存在,导出收尾
        uploads = [_upload()]
        files = {
            "/analysis/需求文档-rough-scan.md": {},
            "/analysis/需求文档-test-points.md": {},
            "/testcases/需求文档-testcases.md": {},
            "/review/需求文档-review.md": {},
        }
        out = FileUploadMiddleware._doc_stages_block(uploads, files)
        self.assertIn("(状态 E)", out)
        self.assertIn("已评审,等导出收尾", out)
        self.assertIn("generate_testcase_excel", out)
        self.assertIn("generate_xmind", out)

    def test_doc_name_strips_extension(self):
        # doc_name 应当只剥最后一个 .md 后缀
        uploads = [_upload("订单系统v2.pdf.md")]  # 即便文件名含多个点,只剥最后一个扩展名
        out = FileUploadMiddleware._doc_stages_block(uploads, {})
        self.assertIn("doc_name=`订单系统v2.pdf`", out)
        # 动作文本用 <doc_name> 占位符,模型在替换时会拼出正确路径;
        # 这里验证占位符确实存在,而不是直接拼出长路径
        self.assertIn("<doc_name>-rough-scan.md", out)

    def test_multi_doc_independent_stages(self):
        # 多份文档各自独立判定
        uploads = [_upload("a.md"), _upload("b.md")]
        files = {"/analysis/a-rough-scan.md": {}}  # 只 a 做过粗扫
        out = FileUploadMiddleware._doc_stages_block(uploads, files)
        self.assertIn("doc_name=`a`", out)
        self.assertIn("doc_name=`b`", out)
        self.assertIn("(状态 B)", out)
        self.assertIn("(状态 A)", out)


class TestInjectUploadsContextShape(unittest.TestCase):
    """校验 _inject_uploads_context 的 system message 注入关键内容(轻量冒烟,
    不启动 LangGraph runtime;用一个简单的 fake request 对象满足最小依赖)。
    """

    class _FakeRequest:
        def __init__(self, system_message, state):
            self.system_message = system_message
            self.state = state

        def override(self, **kwargs):
            new = type(self)(self.system_message, self.state)
            for k, v in kwargs.items():
                setattr(new, k, v)
            return new

    class _Msg:
        def __init__(self, content=""):
            self.content = content

        def __str__(self):
            return self.content if isinstance(self.content, str) else str(self.content)

    def _build_middleware(self):
        from middleware.file_upload import FileUploadMiddleware

        return FileUploadMiddleware()

    def test_empty_uploads_no_injection(self):
        # 无上传时,流程指令不注入,直接返回原 request
        mw = self._build_middleware()
        req = self._FakeRequest(
            system_message=self._Msg("BASE"),
            state={"uploads": [], "files": {}},
        )
        out = mw._inject_uploads_context(req)
        self.assertIs(out, req)

    def test_uploads_with_rough_scan_injects_stage_b(self):
        # 有上传且 rough-scan 已存在 → 注入阶段 B 的动作描述
        mw = self._build_middleware()
        req = self._FakeRequest(
            system_message=self._Msg("BASE"),
            state={
                "uploads": [_upload()],
                "files": {"/analysis/需求文档-rough-scan.md": {}},
            },
        )
        out = mw._inject_uploads_context(req)
        self.assertIn("阶段 A:需求粗扫", out.system_message.content)
        self.assertIn("阶段 B:scope 确认", out.system_message.content)
        self.assertIn("(状态 B)", out.system_message.content)
        self.assertIn("已粗扫,待询问 scope", out.system_message.content)
        self.assertIn("request_scope_selection", out.system_message.content)


if __name__ == "__main__":
    unittest.main()