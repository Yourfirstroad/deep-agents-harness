"""lint_testcases 工具的单元测试。

两类断言:
1. examples.md 里两个正例章节(登录/订单,含附录追溯矩阵)必须 PASS——
   防止规则误伤好示例;
2. 各类典型违规(模糊预期、抽象数据、跳号、重复、异常占比不足、
   追溯矩阵缺失/脱节等)必须被命中。
"""

import re
from pathlib import Path

from tools.lint_tool import lint_testcases

EXAMPLES = (
    Path(__file__).resolve().parent.parent
    / "skills/test-design/references/examples.md"
)


def _extract_example_sections() -> list[str]:
    """抽出示例一/示例二的完整章节文本(用例表 + 附录追溯矩阵一起 lint)。"""
    text = EXAMPLES.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^## (?=示例)", text)
    return [s for s in sections if s.startswith(("示例一", "示例二"))]


def _run(content: str) -> str:
    # lint_testcases 是 @tool 装饰的结构化工具,直接调底层函数
    return lint_testcases.func(markdown_table=content)


def test_good_examples_pass():
    sections = _extract_example_sections()
    assert len(sections) == 2, f"应抽到 2 个示例章节,实际 {len(sections)} 个"
    for section in sections:
        report = _run(section)
        assert report.startswith("lint_testcases 结果:PASS"), report


def test_vague_expected_fails():
    table = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n"
        "| TC-LOGIN-001 | 正确账号密码登录成功 | 已注册账号 | 输入账号密码;点击登录 | 登录正常 | P1 |\n"
        "| TC-LOGIN-002 | 密码错误提示统一文案 | 已注册账号 | 输入错误密码 Wrong99 | 提示「手机号或密码错误」 | P1 |\n"
    )
    report = _run(table)
    assert "FAIL" in report
    assert "模糊词「正常」" in report


def test_abstract_data_fails():
    table = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n"
        "| TC-LOGIN-001 | 正确账号密码登录成功 | 已注册账号 | 输入手机号 13800001111 和密码 Abc123 | 跳转首页 | P1 |\n"
        "| TC-LOGIN-002 | 非法手机号拦截 | 无 | 输入一个非法手机号;点击登录 | 提示手机号格式不正确 | P1 |\n"
    )
    report = _run(table)
    assert "FAIL" in report
    assert "测试数据抽象" in report


def test_whitelisted_correct_password_passes():
    # 「正确密码」是约定写法,不应误报
    table = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n"
        "| TC-LOGIN-001 | 锁定期间正确密码也拒绝 | 账号刚被锁定,密码为 Abc123 | 输入正确密码;点击「登录」 | 提示「账号已锁定,请 30 分钟后重试」 | P0 |\n"
    )
    report = _run(table)
    assert "测试数据抽象" not in report


def test_id_gap_and_duplicate_fail():
    table = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n"
        "| TC-LOGIN-001 | 正确账号密码登录成功 | 已注册账号 | 输入手机号 13800001111 | 跳转首页 | P0 |\n"
        "| TC-LOGIN-001 | 编号重复的用例行 | 已注册账号 | 输入手机号 13900002222 | 跳转首页 | P1 |\n"
        "| TC-LOGIN-003 | 跳号后的用例 | 已注册账号 | 输入手机号 13700003333 | 跳转首页 | P1 |\n"
    )
    report = _run(table)
    assert "FAIL" in report
    assert "重复" in report
    assert "跳号" in report


def test_duplicate_body_fails():
    row = "| TC-A-00{n} | 名称{n} | 已登录 | 点击查询 | 列表显示 10 条数据 | P2 |"
    table = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n"
        + row.replace("{n}", "1")
        + "\n"
        + row.replace("{n}", "2")
        + "\n"
    )
    report = _run(table)
    assert "完全相同" in report


def test_abnormal_ratio_fails():
    rows = "\n".join(
        f"| TC-Q-00{i} | 正常查询场景{i} | 已登录且有数据 | 输入关键词 keyword{i};点击查询 | 列表显示匹配数据 {i} 条 | P2 |"
        for i in range(1, 6)
    )
    table = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n" + rows + "\n"
    )
    report = _run(table)
    assert "FAIL" in report
    assert "异常场景占比" in report


def test_broken_row_fails():
    table = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n"
        "| TC-LOGIN-001 | 单元格混入竖线 | 已登录 | 输入 a | b | 提示错误 | P1 |\n"
    )
    report = _run(table)
    assert "FAIL" in report
    assert "单元格数" in report


def test_module_coverage_blocks():
    table = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n"
        "| TC-LOGIN-001 | 正确账号密码登录成功 | 已注册账号 | 输入手机号 13800001111 和密码 Abc123 | 跳转首页,显示 138****1111 | P0 |\n"
    )
    tp = "TP-LOGIN-001 登录功能\nTP-PAY-001 支付功能\n"
    report = lint_testcases.func(markdown_table=table, test_points_content=tp)
    assert "FAIL" in report
    assert "模块PAY" in report


def test_appendix_tables_ignored():
    # 「设计过程」附录里的判定表不应被当作用例表解析
    content = (
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|---|---|---|---|---|---|\n"
        "| TC-LOGIN-001 | 正确账号密码登录成功 | 已注册账号 | 输入手机号 13800001111 和密码 Abc123 | 跳转首页,显示 138****1111 | P0 |\n"
        "\n## 设计过程\n\n"
        "| 条件 | R1 | R2 |\n|---|---|---|\n| 账号存在 | T | F |\n"
    )
    report = _run(content)
    assert "单元格数" not in report


# ---------------------------------------------------------------------------
# 追溯矩阵检查
# ---------------------------------------------------------------------------

_MINIMAL_TABLE = (
    "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
    "|---|---|---|---|---|---|\n"
    "| TC-LOGIN-001 | 正确账号密码登录成功 | 已注册账号 | 输入手机号 13800001111 和密码 Abc123;点击登录 | 跳转首页,右上角显示 138****1111 | P0 |\n"
    "| TC-LOGIN-002 | 密码错误统一提示 | 已注册账号 | 输入错误密码 Wrong99;点击登录 | 提示「手机号或密码错误」,登录失败 | P1 |\n"
)

_MATRIX = (
    "\n## 设计过程\n\n"
    "| 测试点编号 | 测试点摘要 | 覆盖用例编号 | 设计技术 |\n"
    "|---|---|---|---|\n"
    "| TP-LOGIN-001 | 合法凭证登录 | TC-LOGIN-001 | 等价类 |\n"
    "| TP-LOGIN-002 | 错误凭证提示 | TC-LOGIN-002 | 判定表 |\n"
)


def test_full_file_with_matrix_passes():
    report = _run(_MINIMAL_TABLE + _MATRIX)
    assert report.startswith("lint_testcases 结果:PASS"), report


def test_missing_matrix_fails():
    report = _run(_MINIMAL_TABLE)
    assert "FAIL" in report
    assert "缺少追溯矩阵" in report


def test_matrix_uncovered_tp_blocks():
    tp = "TP-LOGIN-001 登录功能\nTP-LOGIN-009 找回密码\n"
    report = lint_testcases.func(
        markdown_table=_MINIMAL_TABLE + _MATRIX, test_points_content=tp
    )
    assert "FAIL" in report
    assert "TP-LOGIN-009" in report


def test_matrix_dangling_tc_fails():
    matrix = _MATRIX.replace("TC-LOGIN-002 | 判定表", "TC-LOGIN-099 | 判定表")
    report = _run(_MINIMAL_TABLE + matrix)
    assert "不存在的用例编号" in report
    assert "TC-LOGIN-099" in report


def test_matrix_empty_row_blocks():
    matrix = _MATRIX.replace("TC-LOGIN-002 | 判定表", "— | 判定表")
    report = _run(_MINIMAL_TABLE + matrix)
    assert "没有覆盖用例" in report


def test_orphan_tc_warns_but_passes():
    # 用例表多一条 TC-LOGIN-003,矩阵未引用 → 一般级提醒,不影响 PASS
    extra = (
        "| TC-LOGIN-003 | 手机号为空提交 | 已打开登录页 | 手机号留空;点击登录 | 提示「请输入手机号」,不发起请求 | P1 |\n"
    )
    report = _run(_MINIMAL_TABLE + extra + _MATRIX)
    assert "未被任何测试点引用" in report
    assert report.startswith("lint_testcases 结果:PASS"), report
