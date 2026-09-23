"""request_scope_selection 工具:向用户征求"测试范围(scope)"的结构化选择。

与 request_approval 的区别:
- request_approval 是二元闸门(批准/驳回),用于硬节点;
- request_scope_selection 是多选闸门,用于"从候选模块里挑要测哪些"。

机制相同——本身不做事,真正的价值在于它被 agent.py 的 interrupt_on 配置拦截:
模型调用它时流程暂停,前端弹出多选面板(复用 request_approval 的弹窗机制);
用户多选后,选中的项以 JSON 列表的形式作为工具结果返回给模型。
用户也可以驳回(等同于"我都不选/重新选"),驳回意见作为工具结果返回。

注意:本工具只在用户回复含糊时由主 agent 调用;用户用自然语言明确指明模块时
(如"测首页和购物车"),不要调用本工具,直接解析用户文本即可。
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def request_scope_selection(
    stage: str,
    summary: str,
    options: list[str],
    multi_select: bool = True,
) -> str:
    """在粗扫后向用户征求要详细分析的模块范围(多选)。

    调用时机:主 agent 读完粗扫文件、把候选模块展示给用户后,**只有用户回复含糊
    时**才调用(例如"几个重要的""你看着办""都行吧");用户明确说"测 A 和 B"
    时直接解析文本调用,不要触发本工具。

    Args:
        stage: 阶段名称,如「测试范围(scope)」「需求范围确认」。
        summary: 给审核人的简报:粗扫出的候选模块总数、用户上一轮含糊回复的
            原话、需要重点核对的事项。
        options: 候选模块名列表(粗扫产出的模块名,按文档顺序排列)。
        multi_select: 是否允许多选。True(默认)=可勾选多个,适合"测哪些模块";
            False=单选,适合"用哪一档模式"。

    Returns:
        选中的模块列表(JSON 数组);若被驳回,收到的是驳回意见而非本返回值。
    """
    # 工具被 interrupt_on 拦截,本函数体只在用户批准后执行。
    # 返回值作为工具结果送给模型;模型据此继续委派 requirement-analyzer
    # 并把选中的模块写入 task description。
    return (
        f"「{stage}」已获用户选择,scope={options if options else '空(用户全部驳回,需重新询问)'}。"
        "请将选中的模块作为 scope 写入下一步 requirement-analyzer 的 task description,"
        "未选中的模块在测试点文档的「未覆盖模块」一节列出。"
    )