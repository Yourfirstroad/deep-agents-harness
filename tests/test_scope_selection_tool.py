"""tools/scope_selection_tool.py 单元测试。

校验工具签名、描述、可调用性;不需要启动 LangGraph runtime
(它的实际拦截机制由 agent.py 的 interrupt_on 配置承担,见 tools/approval_tool.py 同模式)。
"""

from __future__ import annotations

import unittest

from langchain_core.tools import BaseTool

from tools.scope_selection_tool import request_scope_selection


class TestRequestScopeSelection(unittest.TestCase):
    def test_is_langchain_tool(self):
        self.assertIsInstance(request_scope_selection, BaseTool)
        self.assertEqual(request_scope_selection.name, "request_scope_selection")

    def test_description_mentions_use_case(self):
        # 工具描述必须说明何时调用、何时不要调用,避免被乱用
        desc = request_scope_selection.description
        self.assertIn("粗扫后", desc)
        self.assertIn("含糊", desc)
        self.assertIn("不要", desc)  # 用户明确时不调

    def test_direct_call_returns_scope_echo(self):
        # 工具被批准后应返回 scope 描述,提醒模型把选中模块写入 task description
        result = request_scope_selection.func(
            stage="测试范围",
            summary="候选模块 5 个,用户上一轮说'几个重要的'",
            options=["首页", "购物车", "订单管理"],
            multi_select=True,
        )
        self.assertIn("已获用户选择", result)
        self.assertIn("首页", result)
        self.assertIn("购物车", result)
        # 必须提醒把 scope 传给 analyzer,否则模型可能漏写 description 段
        self.assertIn("requirement-analyzer", result)
        self.assertIn("task description", result)

    def test_empty_options_marks_rejected(self):
        # 用户全部驳回 → 返回值必须能触发模型重新询问,不能沉默
        result = request_scope_selection.func(
            stage="测试范围",
            summary="x",
            options=[],
        )
        self.assertIn("驳回", result)
        self.assertIn("重新询问", result)


if __name__ == "__main__":
    unittest.main()