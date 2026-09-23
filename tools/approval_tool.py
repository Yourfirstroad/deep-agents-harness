"""request_approval 工具:关键节点的人工审核闸门。

本身不做事——真正的价值在于它被 agent.py 的 interrupt_on 配置拦截:
模型调用它时流程暂停,前端弹出 批准/编辑/驳回 审批框,
用户批准后才执行本函数(流程继续),驳回时驳回意见作为工具结果返回给模型
(模型据此修订后重新提交)。

与 HumanInTheLoopMiddleware 的协议(langchain 1.x):
- approve → 执行本函数;
- reject(message) → 不执行,message 作为工具结果返回;
- edit(edited_action) → 用修改后的参数执行本函数。
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def request_approval(stage: str, summary: str, file_path: str) -> str:
    """在关键节点请求人工审核,审核通过前流程暂停。

    调用时机(必须遵守):
    1. requirement-analyzer 产出测试点清单后、委派 testcase-designer 之前;
    2. 评审通过、执行导出交付之前。
    调用时把产出的关键统计和需要人工重点核对的事项写进 summary。

    Args:
        stage: 审核阶段名称,如「测试点清单」「用例终稿」。
        summary: 给审核人的简报:产出路径、数量统计、范围裁剪说明、
            需要重点核对的事项(如"请核对测试点是否有遗漏或臆测")。
        file_path: 待审核文件在虚拟文件系统中的路径,审核人可在文件面板查看。

    Returns:
        批准后的继续指令;若被驳回,收到的是驳回意见而非本返回值。
    """
    return f"「{stage}」已获人工批准,继续后续流程。"
