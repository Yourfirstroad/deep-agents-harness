"""FileUploadMiddleware:后端"接收文件"逻辑的统一入口。

前端(deep-agents-ui)上传文件的机制:
  原始文件(PDF/Word 等)以 FileData 对象注入 agent 状态的 `files` 字典
  (路径为 /uploads/<文件名>),由 DoclingParseMiddleware 自动解析为
  /uploads/<原名>.md;.md/.txt 等纯文本则可由前端直接注入。

本中间件负责:
1. before_model  —— 感知 /uploads/ 下的新文件,校验归一化,记录到自定义状态 `uploads`。
2. wrap_model_call —— 每次模型调用前,把"当前可用文档清单 + 处理流程指令"
   动态注入 system message,行为契约收敛在后端,不依赖前端指令措辞。
3. wrap_tool_call —— 包住 generate_testcase_excel,兜底异常并把错误以
   结构化提示返回给模型,让其自愈重试而不是中断 run。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import SystemMessage, ToolMessage

from middleware.docling_parse import DOC_EXTENSIONS
from tools.excel_tool import generate_testcase_excel
from tools.lint_tool import lint_testcases
from tools.xmind_tool import generate_xmind

UPLOAD_PREFIX = "/uploads/"
# 单个上传文件内容长度上限(字符数),防御异常大的注入
MAX_UPLOAD_CHARS = 2_000_000

# LangGraph 服务地址(与 server_ext.py 一致):下载链接必须是绝对 URL,
# 相对路径会解析到前端 UI 自己的源(如 localhost:3000)导致 404
LANGGRAPH_API_URL = os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024").rstrip("/")


class UploadRecord(TypedDict):
    path: str  # /uploads/需求文档.md
    name: str  # 原始文件名
    size: int  # 内容字符数
    uploaded_at: str  # ISO 时间


class FileUploadState(AgentState):
    uploads: NotRequired[list[UploadRecord]]
    # 校验失败的文件路径,避免每次 before_model 重复告警
    rejected_uploads: NotRequired[list[str]]


def _extract_content(file_data: Any) -> str | None:
    """从 files 状态值中提取文本内容;非法结构返回 None。"""
    if isinstance(file_data, dict):
        content = file_data.get("content")
        return content if isinstance(content, str) else None
    return None


class FileUploadMiddleware(AgentMiddleware):
    """测试用例生成 agent 的文件接收中间件。"""

    state_schema = FileUploadState
    # 工具随中间件注册,agent.py 无需显式传 tools
    # lint_testcases:designer 产出后与导出前的机械质量门禁(结构/编号/模糊词/占比)
    tools = [generate_testcase_excel, generate_xmind, lint_testcases]

    # ------------------------------------------------------------------
    # 1. 感知并登记新上传的文件
    # ------------------------------------------------------------------
    def before_model(self, state: FileUploadState, runtime) -> dict[str, Any] | None:
        files = state.get("files") or {}
        known = {u["path"] for u in state.get("uploads") or []}
        rejected = set(state.get("rejected_uploads") or [])

        new_records: list[UploadRecord] = []
        invalid: list[str] = []
        for path, file_data in files.items():
            if not path.startswith(UPLOAD_PREFIX) or path in known or path in rejected:
                continue
            # 原始文档(PDF/Word 等)由 DoclingParseMiddleware 解析成 .md 后再登记,
            # 这里跳过,避免把 base64 内容当文本入账
            if path.lower().endswith(tuple(DOC_EXTENSIONS)):
                continue
            content = _extract_content(file_data)
            if content is None:
                invalid.append(f"{path}(值不是合法的 FileData 对象,无法读取)")
                continue
            if len(content) > MAX_UPLOAD_CHARS:
                invalid.append(f"{path}(内容超过 {MAX_UPLOAD_CHARS} 字符上限)")
                continue
            new_records.append(
                UploadRecord(
                    path=path,
                    name=path.rsplit("/", 1)[-1],
                    size=len(content),
                    uploaded_at=datetime.now(timezone.utc).isoformat(),
                )
            )

        if not new_records and not invalid:
            return None

        update: dict[str, Any] = {}
        if new_records:
            update["uploads"] = [*(state.get("uploads") or []), *new_records]
        if invalid:
            update["rejected_uploads"] = [
                *(state.get("rejected_uploads") or []),
                *[p.split("(")[0] for p in invalid],
            ]
            # 让模型能看到哪些文件注入失败,主动告知用户
            update.setdefault("messages", []).append(
                SystemMessage(
                    content="以下上传文件未通过校验,已忽略:"
                    + ";".join(invalid)
                    + "。请在回复中提示用户重新上传。"
                )
            )
        return update

    # ------------------------------------------------------------------
    # 2. 动态注入"可用文档清单 + 处理流程"到 system message
    # ------------------------------------------------------------------
    def _inject_uploads_context(self, request):
        """把当前可用文档清单和处理流程指令拼进 system message。"""
        uploads = (request.state.get("uploads") or []) if request.state else []
        if not uploads:
            return request
        doc_list = "\n".join(
            f"- {u['path']}(原始文件:{u['name']},约 {u['size']} 字符)"
            for u in uploads
        )
        extra = (
            "\n\n# 当前已接收的需求文档\n"
            f"{doc_list}\n\n"
            "对用户最新提到的文档,严格按以下流程处理(需求分析/用例设计/评审"
            "必须委派子代理执行,子代理看不到对话历史,task 的 description "
            "必须写全文档路径、doc_name 和产出路径):\n"
            "1. 用 read_file 读取对应路径的文件,确认文档已解析、内容完整,"
            "并确定 doc_name(去掉扩展名的文档名);\n"
            "2. 用 task 委派 requirement-analyzer:阅读该文档并提取功能点与"
            "测试点,产出写入 /analysis/<doc_name>-test-points.md;"
            "返回后用 read_file 检查产出,并向用户汇报测试点统计与需求疑问;\n"
            "2.5 调用 request_approval 请求人工审核测试点清单(stage 传「测试点清单」,"
            "summary 写模块数/测试点总数/需要重点核对的事项,file_path 传测试点文档路径);"
            "被驳回时把驳回意见作为上下文重新委派 requirement-analyzer 修订,"
            "修订后重新提请审核,直到批准;\n"
            "3. 用 task 委派 testcase-designer:基于测试点文档设计测试用例"
            "(六列 Markdown 表格),产出写入 /testcases/<doc_name>-testcases.md;\n"
            "4. 用 read_file 读取用例文件和测试点文档,调用 lint_testcases 做"
            "机械质量检查(结构/编号/模糊预期/抽象数据/异常占比);结果为 FAIL 时,"
            "把报告原文作为上下文委派 testcase-designer 修订,修订后重新 lint,"
            "直至 PASS(无阻断/严重项)再进入评审;\n"
            "5. 用 task 委派 testcase-reviewer:审查用例的覆盖度、正确性与"
            "可执行性,评审报告写入 /review/<doc_name>-review.md;"
            "若结论为「需修订」,把修订意见清单作为上下文再次委派 "
            "testcase-designer 修订用例文件,然后重新评审,"
            "直到通过或达到 2 轮修订上限;\n"
            "6. 用 read_file 读取定稿的 /testcases/<doc_name>-testcases.md,"
            "先再跑一次 lint_testcases 确认定稿仍为 PASS,然后调用 request_approval "
            "请求人工审核用例终稿(stage 传「用例终稿」,summary 写用例总数/优先级分布/"
            "精简模式裁剪说明,file_path 传用例文件路径);批准后才允许导出,"
            "被驳回时按驳回意见委派 testcase-designer 修订并重新走评审与审核;\n"
            "7. 审核通过后,把完整内容传给 "
            "generate_testcase_excel 工具(doc_name 同上),"
            "它会同时生成 <doc_name>-testcases.xlsx 和 Markdown 终稿 "
            "<doc_name>-testcases.md;不要用 write_file 保存用例表;\n"
            "8. 把测试点组织成思维导图(markmap 兼容的 Markdown:# 标题做根节点,"
            "下级用 ##/### 标题或 - 开头的缩进列表),然后调用 generate_xmind 工具"
            "(doc_name 同上),它会一次生成 .xmind、浏览器预览 .html 和 .md 三个文件;"
            "不要用 write_file 保存思维导图。\n"
            "9. 两个导出工具都成功后,在回复末尾输出「交付物下载」小节,"
            "用 Markdown 链接给出以下四项,文件名中的 doc_name 与上面一致"
            "(这是前端下载接口,必须原样输出完整的绝对 URL,不要改成磁盘路径,"
            "也不要省略主机地址):\n"
            f"   - [Markdown 终稿]({LANGGRAPH_API_URL}/api/download?file=<doc_name>-testcases.md)\n"
            f"   - [Excel 用例表]({LANGGRAPH_API_URL}/api/download?file=<doc_name>-testcases.xlsx)\n"
            f"   - [XMind 思维导图]({LANGGRAPH_API_URL}/api/download?file=<doc_name>-mindmap.xmind)\n"
            f"   - [思维导图在线预览]({LANGGRAPH_API_URL}/api/download?file=<doc_name>-mindmap.html)"
        )
        base = request.system_message or SystemMessage(content="")
        return request.override(
            system_message=SystemMessage(content=str(base.content) + extra)
        )

    def wrap_model_call(self, request, handler):
        return handler(self._inject_uploads_context(request))

    async def awrap_model_call(self, request, handler):
        # langgraph dev 走 astream,必须提供异步变体;逻辑与同步版一致
        return await handler(self._inject_uploads_context(request))

    # ------------------------------------------------------------------
    # 3. 工具调用兜底:异常不中断 run,转成可自愈的错误提示
    # ------------------------------------------------------------------
    @staticmethod
    def _tool_error_message(request, e: Exception) -> ToolMessage:
        tool_call = getattr(request, "tool_call", None) or {}
        name = tool_call.get("name", "unknown")
        return ToolMessage(
            content=(
                f"ERROR: 工具 {name} 执行时发生未预期异常:"
                f"{type(e).__name__}: {e}。请检查入参后重试;"
                f"若是表格格式问题,修正 markdown_table 后重新调用。"
            ),
            tool_call_id=tool_call.get("id", ""),
            name=name,
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        try:
            return handler(request)
        except Exception as e:  # noqa: BLE001
            return self._tool_error_message(request, e)

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except Exception as e:  # noqa: BLE001
            return self._tool_error_message(request, e)
