"""lint_testcases 工具:对 Markdown 用例表做确定性的机械质量检查。

与 testcase-review 子代理的分工:评审子代理负责语义判断(覆盖合理性、
技术选型、业务正确性),本工具负责不需要智能就能判定的硬规则——
表格结构、编号连续性、优先级枚举、模糊预期、抽象测试数据、异常场景占比。
规则来源:skills/test-design/SKILL.md「质量红线」与
skills/testcase-review/SKILL.md「量化指标」。

由 FileUploadMiddleware 注册到主 agent;子代理不持有本工具。
主 agent 应在两个时点调用:designer 产出后(打回修订)、导出 Excel 前(门禁)。
"""

from __future__ import annotations

import os
import re
from collections import defaultdict

from langchain_core.tools import tool

from tools.excel_tool import EXPECTED_HEADERS

# 结果分级:阻断 > 严重 > 一般(与 testcase-review 的问题分级对齐)
LEVELS = ("阻断", "严重", "一般")

# 用例编号:TC-<模块缩写>-<三位序号>;模块缩写允许多段中划线(如 FRONT-HOME)
_ID_RE = re.compile(r"^TC-([A-Z0-9]+(?:-[A-Z0-9]+)*)-(\d{3})$")
_TP_RE = re.compile(r"TP-([A-Z0-9]+(?:-[A-Z0-9]+)*)-(\d{3})")

# 预期结果中的禁用模糊词:出现即违规(技能红线:禁止"正常""正确"这类词)
_VAGUE_BANNED = ("正常", "无误", "无异常", "符合预期", "符合要求", "没有问题")
_VAGUE_CORRECT_RE = re.compile(r"(?<![不与否])正确(?!率)")  # 「不正确」「正确率」放行
# 整条预期只是「登录成功」这类短句:没有可观察判定标准
_VAGUE_SHORT_RE = re.compile(r"(成功|失败|通过)[。!！]?$")

# 抽象测试数据:「一个合法手机号」「错误密码」这类没有具体值的描述
_ABSTRACT_RE = re.compile(
    r"(?:一个|某个|某些|任意)?"
    r"(?:合法|有效|非法|无效|错误|异常|超长|超短|较大|较小|特殊|随机)的?"
    r"(?:手机号|邮箱|密码|账号|用户名|用户|值|数据|输入|参数|字符串|金额|日期|数字|字符|文件|ID)"
)
# 「正确密码」是约定俗成的写法(具体值通常在前置条件给出),先遮蔽再扫描
_ABSTRACT_WHITELIST_RE = re.compile(r"正确(?:的)?(?:密码|口令|验证码|账号|账户|用户名)")
# 命中项后面紧跟具体值(数字/字母/引号串)时视为已具体化,如「错误密码 Wrong99」
_CONCRETE_AFTER_RE = re.compile(r"\d|[A-Za-z]{2,}|[「\"']")

# 异常场景关键词(命中名称/步骤/预期任一即计为异常用例),用于异常占比统计
_ABNORMAL_KEYWORDS = (
    "错误", "失败", "异常", "非法", "无效", "为空", "留空", "缺失", "超长",
    "超期", "越权", "未登录", "未认证", "未授权", "锁定", "拒绝", "拦截",
    "并发", "重复", "乱序", "超时", "不足", "超限", "超出", "注入", "空串",
    "null", "NULL", "断网", "断电", "弱网", "过期", "回滚", "降级", "篡改",
)

_VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
_ABNORMAL_RATIO_MIN = 0.30  # 技能红线:异常场景用例占比 ≥ 30%
_P0_RATIO_RANGE = (0.10, 0.30)  # 评审经验区间:P0 占比 10%-20%,超 30% 告警

# 追溯矩阵(「设计过程」附录):表头固定「测试点编号 | 测试点摘要 | 覆盖用例编号 | 设计技术」
_TRACE_FIRST_COL = "测试点编号"
_TRACE_TC_COL = "覆盖用例编号"
_TC_REF_RE = re.compile(r"TC-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}")


class _Issue:
    def __init__(self, level: str, case_id: str, problem: str, fix: str):
        self.level = level
        self.case_id = case_id
        self.problem = problem
        self.fix = fix

    def render(self, idx: int) -> str:
        return f"{idx}. [{self.case_id}] {self.problem} → {self.fix}"


def _row_cells(line: str, min_cols: int = len(EXPECTED_HEADERS)) -> list[str] | None:
    """把一行解析为表格单元格;不像表格行(竖线数不够)返回 None。

    行首/行尾的 `|` 可有可无(模型时常省略),但列数不能少。
    """
    s = line.strip()
    if s.count("|") < min_cols - 1:
        return None
    return [c.strip() for c in s.strip("|").split("|")]


def _extract_case_table(content: str) -> tuple[list[list[str]] | None, str | None]:
    """从用例文件中定位并解析六列用例表。

    文件里可能还有判定表、状态图等附录表格,只有表头与 EXPECTED_HEADERS
    完全一致的那张才是用例表。返回 (数据行, 错误说明)。
    """
    lines = content.splitlines()
    header_idx = None
    for i, ln in enumerate(lines):
        cells = _row_cells(ln)
        if cells and cells == EXPECTED_HEADERS:
            header_idx = i
            break
    if header_idx is None:
        return None, (
            f"未找到表头为「{' | '.join(EXPECTED_HEADERS)}」的用例表"
            "(行首/行尾的 `|` 可有可无,但列名与顺序必须完全一致;判定表等附录表格不算)"
        )

    rows: list[list[str]] = []
    for ln in lines[header_idx + 1 :]:
        cells = _row_cells(ln)
        if cells is None:
            if rows:  # 表格结束
                break
            continue  # 表头与首行数据之间允许隔着分隔行/空行
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # |---|---| 分隔行
        rows.append(cells)
    if not rows:
        return None, "找到用例表表头,但表内没有数据行"
    return rows, None


def _check_structure(rows: list[list[str]], issues: list[_Issue]) -> None:
    """列数、空单元格、编号格式/重复/跳号、优先级枚举。"""
    seen_ids: dict[str, int] = {}
    module_nums: dict[str, list[int]] = defaultdict(list)

    for i, cells in enumerate(rows, start=1):
        label = f"第{i}行"
        if len(cells) != len(EXPECTED_HEADERS):
            issues.append(_Issue(
                "阻断", label,
                f"单元格数为 {len(cells)},应为 6(多半是单元格内混入了 `|` 或列缺失)",
                "拆出混入的 `|`(用中文逗号代替),补齐缺失列后重排该行",
            ))
            continue
        case_id, name, pre, steps, expected, priority = cells
        label = case_id or label

        m = _ID_RE.match(case_id)
        if not m:
            issues.append(_Issue(
                "阻断", label,
                f"用例编号「{case_id}」不符合 TC-<模块缩写>-<三位序号> 格式",
                "改为如 TC-LOGIN-001;模块缩写用大写字母,序号三位补零",
            ))
        else:
            if case_id in seen_ids:
                issues.append(_Issue(
                    "阻断", label,
                    f"编号 {case_id} 与第 {seen_ids[case_id]} 行重复",
                    "重新分配编号,保证全表唯一",
                ))
            seen_ids[case_id] = i
            module_nums[m.group(1)].append(int(m.group(2)))

        for col, value in zip(EXPECTED_HEADERS[1:5], (name, pre, steps, expected)):
            if not value:
                issues.append(_Issue(
                    "严重", label, f"「{col}」为空",
                    f"补齐{col};空单元格导出 Excel 后无法执行",
                ))

        if priority.strip().upper() not in _VALID_PRIORITIES:
            issues.append(_Issue(
                "严重", label,
                f"优先级「{priority}」非法,只允许 P0/P1/P2/P3",
                "按风险定级矩阵改为 P0-P3 之一",
            ))

    for module, nums in sorted(module_nums.items()):
        missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
        if missing:
            issues.append(_Issue(
                "严重", f"模块{module}",
                f"编号跳号:缺 {', '.join(f'{n:03d}' for n in missing)}",
                "补齐用例或重排序号,保持连续(跳号会让评审怀疑有用例被误删)",
            ))


def _check_expected(rows: list[list[str]], issues: list[_Issue]) -> None:
    """预期结果:模糊词、过短无判定标准。"""
    for cells in rows:
        if len(cells) != len(EXPECTED_HEADERS):
            continue
        case_id, expected = cells[0], cells[4]
        if not expected:
            continue  # 空值已在结构检查里报过
        for word in _VAGUE_BANNED:
            if word in expected:
                issues.append(_Issue(
                    "严重", case_id,
                    f"预期结果含禁用模糊词「{word}」",
                    "改为可观察判定:页面文案、跳转地址、状态变化、落库字段",
                ))
                break
        else:
            if _VAGUE_CORRECT_RE.search(expected):
                issues.append(_Issue(
                    "严重", case_id,
                    "预期结果含模糊词「正确」",
                    "写明正确的具体内容(如「金额为 100.00」而非「金额正确」)",
                ))
        stripped = expected.strip()
        if len(stripped) <= 10 and _VAGUE_SHORT_RE.search(stripped):
            issues.append(_Issue(
                "严重", case_id,
                f"预期结果「{stripped}」过短,没有可观察的判定标准",
                "补充成功/失败后的具体表现:提示文案、页面跳转、状态或数据变化",
            ))


def _check_data(rows: list[list[str]], issues: list[_Issue]) -> None:
    """测试数据具体化:前置条件与步骤里禁止「一个合法手机号」式抽象描述。"""
    for cells in rows:
        if len(cells) != len(EXPECTED_HEADERS):
            continue
        case_id = cells[0]
        text = _ABSTRACT_WHITELIST_RE.sub("", f"{cells[2]} {cells[3]}")
        for m in _ABSTRACT_RE.finditer(text):
            window = text[m.end() : m.end() + 12]
            if _CONCRETE_AFTER_RE.search(window):
                continue  # 后面紧跟具体值,如「错误密码 Wrong99」
            issues.append(_Issue(
                "严重", case_id,
                f"测试数据抽象:「{m.group()}」没有具体值",
                "写到可直接输入的值,如「手机号 13800001111」;不同用例用不同的值",
            ))


def _check_duplicates(rows: list[list[str]], issues: list[_Issue]) -> None:
    """重复用例:名称重复或步骤+预期完全相同(技能红线:不得重复或互相包含)。"""
    seen_names: dict[str, str] = {}
    seen_bodies: dict[tuple[str, str], str] = {}
    for cells in rows:
        if len(cells) != len(EXPECTED_HEADERS):
            continue
        case_id, name, steps, expected = cells[0], cells[1], cells[3], cells[4]
        if name and name in seen_names:
            issues.append(_Issue(
                "严重", case_id,
                f"用例名称与 {seen_names[name]} 重复:「{name}」",
                "若为不同场景,改名体现差异;若为同一场景,删除其一",
            ))
        seen_names.setdefault(name, case_id)
        body = (steps, expected)
        if steps and body in seen_bodies:
            issues.append(_Issue(
                "严重", case_id,
                f"步骤与预期和 {seen_bodies[body]} 完全相同,疑似重复用例",
                "合并或删除;若前置条件不同导致结果不同,预期应体现该差异",
            ))
        seen_bodies.setdefault(body, case_id)


def _stats(rows: list[list[str]]) -> dict:
    """优先级分布、异常场景占比、按模块分布。"""
    priorities: dict[str, int] = defaultdict(int)
    modules: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "abnormal": 0})
    abnormal = 0
    valid = 0
    for cells in rows:
        if len(cells) != len(EXPECTED_HEADERS):
            continue
        valid += 1
        priorities[cells[5].strip().upper()] += 1
        text = f"{cells[1]} {cells[3]} {cells[4]}"
        is_abnormal = any(k in text for k in _ABNORMAL_KEYWORDS)
        if is_abnormal:
            abnormal += 1
        m = _ID_RE.match(cells[0])
        if m:
            modules[m.group(1)]["total"] += 1
            modules[m.group(1)]["abnormal"] += int(is_abnormal)
    return {
        "total": valid,
        "priorities": dict(priorities),
        "abnormal": abnormal,
        "abnormal_ratio": abnormal / valid if valid else 0.0,
        "modules": dict(modules),
    }


def _check_stats(stats: dict, issues: list[_Issue]) -> None:
    total = stats["total"]
    if not total:
        return
    ratio = stats["abnormal_ratio"]
    if ratio < _ABNORMAL_RATIO_MIN:
        issues.append(_Issue(
            "严重", "用例集",
            f"异常场景占比 {ratio:.0%}(红线 ≥ {_ABNORMAL_RATIO_MIN:.0%}),纯正向用例集一律打回",
            "按等价类无效类、非法状态迁移、并发/幂等、安全越权补齐异常用例",
        ))
    p0 = stats["priorities"].get("P0", 0)
    p0_ratio = p0 / total
    if p0_ratio > _P0_RATIO_RANGE[1]:
        issues.append(_Issue(
            "一般", "用例集",
            f"P0 占比 {p0_ratio:.0%} 超过 {_P0_RATIO_RANGE[1]:.0%},定级可能过松",
            "对照定级矩阵复核:只有资损/数据丢失/安全泄露/主流程阻断才是 P0",
        ))
    # 冒烟模式总量硬约束:超过 30 条直接 FAIL 打回(standard/full 档不设总量上限,
    # 覆盖率由追溯矩阵 + 评审兜底)
    scope = os.getenv("TESTCASE_SCOPE", "standard").lower()
    if scope == "slim":
        scope = "smoke"
    if scope == "smoke" and total > 30:
        issues.append(_Issue(
            "严重", "用例集",
            f"冒烟模式下用例总量 {total} 条,超过 30 条硬上限",
            "按 P0/P1 尺度直接删除低价值用例(同一测试点只留最高风险代表场景,"
            "每模块最多 5 条),被删测试点在追溯矩阵标注「—」",
        ))


def _extract_trace_matrix(content: str) -> list[tuple[str, list[str]]] | None:
    """解析「设计过程」附录中的追溯矩阵,返回 [(TP编号, [TC编号, ...])]。

    只认表头第一列为「测试点编号」且含「覆盖用例编号」列的表格;
    找不到返回 None。TC 引用用正则提取,兼容「、」,逗号等分隔写法。
    """
    lines = content.splitlines()
    header_idx, tc_col = None, None
    for i, ln in enumerate(lines):
        cells = _row_cells(ln, min_cols=4)
        if cells and cells[0] == _TRACE_FIRST_COL and _TRACE_TC_COL in cells:
            header_idx, tc_col = i, cells.index(_TRACE_TC_COL)
            break
    if header_idx is None:
        return None

    rows: list[tuple[str, list[str]]] = []
    for ln in lines[header_idx + 1 :]:
        cells = _row_cells(ln, min_cols=4)
        if cells is None:
            if rows:
                break
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # 分隔行
        m = _TP_RE.search(cells[0])
        tcs = _TC_REF_RE.findall(cells[tc_col]) if tc_col < len(cells) else []
        rows.append((m.group(0) if m else cells[0], tcs))
    return rows


def _check_traceability(
    content: str,
    test_points_content: str,
    case_ids: set[str],
    issues: list[_Issue],
) -> list[str]:
    """追溯核对:模块级覆盖 + 追溯矩阵双向一致性(机械部分)。

    语义核对(所列用例是否真的验证了该测试点)仍由评审子代理负责。
    返回提示信息列表(不判级)。
    """
    hints: list[str] = []
    tp_ids: list[str] = []
    tp_modules: dict[str, list[str]] = defaultdict(list)
    if test_points_content.strip():
        for m in _TP_RE.finditer(test_points_content):
            tp_ids.append(m.group(0))
            tp_modules[m.group(1)].append(m.group(0))

    matrix = _extract_trace_matrix(content)
    if matrix is None:
        issues.append(_Issue(
            "严重", "设计过程附录",
            "缺少追溯矩阵(表头:测试点编号 | 测试点摘要 | 覆盖用例编号 | 设计技术)",
            "为每个测试点补一行映射;矩阵是评审覆盖核对的基准,缺失按输出契约打回",
        ))
        # 无矩阵时无法验证声明裁剪,模块级覆盖一律从严
        for module, tps in sorted(tp_modules.items()):
            if not re.search(rf"TC-{re.escape(module)}-\d{{3}}", content):
                issues.append(_Issue(
                    "阻断", f"模块{module}",
                    f"测试点文档中该模块有 {len(tps)} 个测试点,但用例表没有任何 TC-{module}-xxx 用例",
                    "为该模块补设计用例;若模块已改名,统一两处缩写",
                ))
        if tp_ids:  # 无矩阵时退化为全文粗查,给出提示
            uncovered = [t for t in tp_ids if t not in content]
            if uncovered:
                hints.append(
                    "以下测试点编号未在用例文件中出现(请确认是否有遗漏):"
                    + "、".join(uncovered[:10])
                    + (" 等" if len(uncovered) > 10 else "")
                )
        return hints

    empty_rows = [tp for tp, tcs in matrix if not tcs]
    # 区分「声明不覆盖」(精简模式:覆盖用例编号列写 —/不覆盖)与「漏覆盖」;
    # 前者合法,后者阻断。声明行需要从原始矩阵行文本里判断,这里重新扫一遍
    declared_skip: set[str] = set()
    if empty_rows:
        for ln in content.splitlines():
            cells = _row_cells(ln, min_cols=4)
            if not cells or cells[0] in ("", _TRACE_FIRST_COL):
                continue
            m = _TP_RE.search(cells[0])
            if not m:
                continue
            row_text = " ".join(cells)
            if not _TC_REF_RE.findall(row_text) and ("—" in row_text or "不覆盖" in row_text):
                declared_skip.add(m.group(0))
    undeclared = [tp for tp in empty_rows if tp not in declared_skip]
    if undeclared:
        issues.append(_Issue(
            "阻断", "追溯矩阵",
            f"{len(undeclared)} 个测试点没有覆盖用例:{'、'.join(undeclared[:10])}",
            "为这些测试点补设计用例;精简模式下主动裁剪的,覆盖用例编号列写「—」并注明原因",
        ))
    if declared_skip:
        hints.append(
            f"{len(declared_skip)} 个测试点声明不覆盖(精简模式裁剪):"
            + "、".join(sorted(declared_skip)[:10])
        )

    # 模块级覆盖:整模块无用例时,若该模块全部测试点都已声明不覆盖(裁剪),合法;
    # 否则阻断。模块裁剪判定依赖矩阵,矩阵缺失时退化为一律阻断
    for module, tps in sorted(tp_modules.items()):
        if re.search(rf"TC-{re.escape(module)}-\d{{3}}", content):
            continue
        if matrix is not None and tps and all(tp in declared_skip for tp in tps):
            continue  # 整模块声明裁剪
        issues.append(_Issue(
            "阻断", f"模块{module}",
            f"测试点文档中该模块有 {len(tps)} 个测试点,但用例表没有任何 TC-{module}-xxx 用例",
            "为该模块补设计用例;精简模式下裁剪的,在追溯矩阵逐行写「—」并注明原因",
        ))

    if tp_ids:
        tp_in_matrix = {tp for tp, _ in matrix}
        missing = [t for t in tp_ids if t not in tp_in_matrix]
        if missing:
            issues.append(_Issue(
                "阻断", "追溯矩阵",
                f"{len(missing)} 个测试点未出现在矩阵中:{'、'.join(missing[:10])}"
                + (" 等" if len(missing) > 10 else ""),
                "矩阵必须覆盖测试点文档中的每一条;补行或说明原因",
            ))

    referenced = {tc for _, tcs in matrix for tc in tcs}
    dangling = sorted(referenced - case_ids)
    if dangling:
        issues.append(_Issue(
            "严重", "追溯矩阵",
            f"引用了不存在的用例编号:{'、'.join(dangling[:10])}",
            "矩阵与用例表脱节(多半是改表后没同步矩阵),逐条核对修正",
        ))
    orphans = sorted(case_ids - referenced)
    if orphans:
        issues.append(_Issue(
            "一般", "追溯矩阵",
            f"{len(orphans)} 条用例未被任何测试点引用:{'、'.join(orphans[:10])}",
            "补进矩阵对应测试点行;若无来源,可能是臆测需求,交评审确认",
        ))
    return hints


@tool
def lint_testcases(markdown_table: str, test_points_content: str = "") -> str:
    """对测试用例 Markdown 文件做机械质量检查(结构/编号/模糊预期/抽象数据/占比/追溯矩阵)。

    在 testcase-designer 产出后、以及导出 Excel 前各调用一次;有「阻断」或
    「严重」项时,把本报告原文作为修订上下文委派 designer 修订后重新检查。

    Args:
        markdown_table: 用例文件的完整内容(六列用例表 + 「设计过程」附录,
            附录必须含追溯矩阵:测试点编号 | 测试点摘要 | 覆盖用例编号 | 设计技术)。
        test_points_content: 可选,测试点文档内容;提供时会做覆盖核对。

    Returns:
        PASS/FAIL 结论 + 分级问题清单(含修订建议)+ 量化统计。
    """
    issues: list[_Issue] = []
    rows, err = _extract_case_table(markdown_table)
    if rows is None:
        return f"lint_testcases 结果:FAIL\n\n[阻断]\n1. [文件] {err}"

    _check_structure(rows, issues)
    _check_expected(rows, issues)
    _check_data(rows, issues)
    _check_duplicates(rows, issues)
    stats = _stats(rows)
    _check_stats(stats, issues)
    case_ids = {
        cells[0] for cells in rows
        if len(cells) == len(EXPECTED_HEADERS) and _ID_RE.match(cells[0])
    }
    hints = _check_traceability(markdown_table, test_points_content, case_ids, issues)

    by_level: dict[str, list[_Issue]] = {lv: [] for lv in LEVELS}
    for issue in issues:
        by_level[issue.level].append(issue)
    blocking = len(by_level["阻断"]) + len(by_level["严重"])
    verdict = "PASS" if blocking == 0 else "FAIL"

    parts = [
        f"lint_testcases 结果:{verdict} — "
        + " / ".join(f"{lv} {len(by_level[lv])}" for lv in LEVELS)
    ]
    for lv in LEVELS:
        if not by_level[lv]:
            continue
        parts.append(f"\n[{lv}]")
        parts.extend(issue.render(i) for i, issue in enumerate(by_level[lv], 1))

    prio = stats["priorities"]
    parts.append(
        f"\n统计:用例 {stats['total']} 条;优先级 "
        + " / ".join(f"{p} {prio.get(p, 0)}" for p in ("P0", "P1", "P2", "P3"))
        + f";异常场景占比 {stats['abnormal_ratio']:.0%}(红线 ≥30%)"
    )
    if stats["modules"]:
        parts.append(
            "按模块: "
            + " / ".join(
                f"{mod} {d['total']} 条(异常 {d['abnormal'] / d['total']:.0%})"
                for mod, d in sorted(stats["modules"].items())
            )
        )
    if hints:
        parts.append("\n提示:")
        parts.extend(f"- {h}" for h in hints)
    if verdict == "FAIL":
        parts.append(
            "\n建议:把本报告原文作为上下文委派 testcase-designer 修订,"
            "修订后重新调用本工具确认。"
        )
    return "\n".join(parts)
