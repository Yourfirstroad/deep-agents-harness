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
        """把当前可用文档清单和处理流程指令拼进 system message。

        流程是状态机推进的(由 /analysis/、/testcases/、/review/ 下的产物文件
        存在性决定下一步);每份上传文档独立判断当前阶段,主 agent 根据注入的
        「当前阶段 → 下一步动作」推进。多份文档时,逐份并行处理。
        """
        uploads = (request.state.get("uploads") or []) if request.state else []
        if not uploads:
            return request
        files = (request.state.get("files") or {}) if request.state else {}
        doc_list = "\n".join(
            f"- {u['path']}(原始文件:{u['name']},约 {u['size']} 字符)"
            for u in uploads
        )
        stages_block = self._doc_stages_block(uploads, files)
        extra = (
            "\n\n# 当前已接收的需求文档\n"
            f"{doc_list}\n\n"
            "# 各文档的当前处理阶段\n"
            f"{stages_block}\n\n"
            "# 完整流程参考(从当前阶段往后读)\n\n"
            "## 阶段 A:需求粗扫(广度优先)\n"
            "- read_file 读上传文档,确认内容完整,确定 doc_name(去掉扩展名);\n"
            "- task 委派 requirement-rough-scanner:广度优先列候选模块,**只列模块,不展开** F-points/TP,\n"
            "  描述写:文档路径 + doc_name + 产出路径 /analysis/<doc_name>-rough-scan.md;\n"
            "- task 返回后 read_file 检查粗扫产出,把候选模块以简洁表格呈现给用户并明确询问 scope。\n\n"
            "## 阶段 B:scope 确认(主 agent 与用户对话,见 BASE_PROMPT「scope 确认规则」)\n"
            "- 用户明确列出模块名 → 直接采用,scope=<模块列表>;\n"
            "- 用户说「全部/都测/你来定」 → 反问一次:「我准备覆盖 X 个模块:[列表],确认吗?」;\n"
            "  用户确认后 scope=全部,用户纠正按纠正后执行;\n"
            "- 用户含糊(「几个重要的」「你看着办」「差不多就行」) → 调用 request_scope_selection 工具,\n"
            "  options=粗扫出的候选模块列表, stage=「测试范围」,触发前端多选弹窗;\n"
            "  弹窗返回的选中列表就是 scope;被驳回则回到对话重新询问。\n\n"
            "## 阶段 C:需求深度分析(只做 scope 内模块)\n"
            "- task 委派 requirement-analyzer,**description 必须显式包含**:\n"
            "  「用户选定的 scope:<模块列表或\"全部\">」\n"
            "- analyzer 产出会自动写入「未覆盖模块」段列出 scope 外的模块。\n\n"
            "## 阶段 D:测试点清单审核\n"
            "- task 返回后 read_file 检查,调用 request_approval(stage=「测试点清单」,\n"
            "  summary 写 scope 内/外模块数、scope 内测试点总数、需要重点核对的事项,\n"
            "  file_path=/analysis/<doc_name>-test-points.md);\n"
            "- 被驳回时把驳回意见作为上下文重新委派 requirement-analyzer 修订,\n"
            "  修订后重新提请审核,直到批准。\n\n"
            "## 阶段 E:用例设计(scope 限定)\n"
            "- task 委派 testcase-designer,**description 必须显式包含**:\n"
            "  「用户选定的 scope:<值>」"
            "  产出写入 /testcases/<doc_name>-testcases.md(六列 Markdown 用例表);\n"
            "- 跑 lint_testcases 做机械质量检查(FAIL 则把报告原文委派 designer 修订并重新 lint,\n"
            "  直至 PASS 无阻断/严重项再进入评审)。\n\n"
            "## 阶段 F:用例评审\n"
            "- task 委派 testcase-reviewer,产出 /review/<doc_name>-review.md;\n"
            "- 「需修订」时把修订意见清单委派 testcase-designer 修订,然后重新评审,\n"
            "  直到通过或达到 2 轮修订上限。\n\n"
            "## 阶段 G:终稿审核 + 导出\n"
            "- 评审通过后,read_file 读取定稿用例,再跑一次 lint 确认仍 PASS;\n"
            "- request_approval(stage=「用例终稿」, summary 写用例总数/优先级分布/裁剪说明,\n"
            "  file_path=/testcases/<doc_name>-testcases.md);批准后才允许导出;\n"
            "- read_file 读取定稿内容,调用 generate_testcase_excel 生成\n"
            "  <doc_name>-testcases.xlsx 与 <doc_name>-testcases.md;\n"
            "- 把测试点组织成 markmap 兼容 Markdown 大纲,调用 generate_xmind 生成\n"
            "  <doc_name>-mindmap.xmind / .html / .md。\n\n"
            "## 阶段 H:输出交付物下载\n"
            "两个导出工具都成功后,在回复末尾输出「交付物下载」小节\n"
            "(文件名 doc_name 与上面一致;这是前端下载接口,必须原样输出绝对 URL):\n"
            f"- [Markdown 终稿]({LANGGRAPH_API_URL}/api/download?file=<doc_name>-testcases.md)\n"
            f"- [Excel 用例表]({LANGGRAPH_API_URL}/api/download?file=<doc_name>-testcases.xlsx)\n"
            f"- [XMind 思维导图]({LANGGRAPH_API_URL}/api/download?file=<doc_name>-mindmap.xmind)\n"
            f"- [思维导图在线预览]({LANGGRAPH_API_URL}/api/download?file=<doc_name>-mindmap.html)\n\n"
            "# 通用规则\n"
            "- 子代理产出的文件与主 agent 共享文件系统;task 返回后 read_file 检查产出;\n"
            "- 子代理的最终消息只是简报,**不要把简报当作用例正文**;\n"
            "- 委派 analyzer / designer 时,description 必须显式写出「用户选定的 scope」段;\n"
            "- 多份文档**逐份独立**跑粗扫 → scope 询问 → 详细分析 → 导出,不要混在一起问 scope。"
        )
        base = request.system_message or SystemMessage(content="")
        return request.override(
            system_message=SystemMessage(content=str(base.content) + extra)
        )

    @staticmethod
    def _doc_stages_block(uploads: list[dict], files: dict) -> str:
        """为每份上传文档生成「当前阶段 + 下一步动作」小节。

        状态判定(由产物文件存在性):
        - 阶段 A: /analysis/<doc_name>-rough-scan.md 不存在 → 未开始
        - 阶段 B: rough-scan 已存在,test-points 不存在 → 粗扫完,等 scope
        - 阶段 C: test-points 已存在,testcases 不存在 → 已分析,等用例设计
        - 阶段 D: testcases 已存在,review 不存在 → 已设计,等评审
        - 阶段 E: review 已存在 → 已评审,等导出收尾
        """
        stage_to_action = {
            "A": (
                "**先 read_file 读上传文档,确认解析完整;然后 task 委派 requirement-rough-scanner** "
                "(广度优先列候选模块),产出路径 /analysis/<doc_name>-rough-scan.md;"
                "完成后 read_file 检查,把候选模块以简洁表格呈现给用户并明确询问 scope。"
            ),
            "B": (
                "**read_file 读 /analysis/<doc_name>-rough-scan.md,把候选模块以简洁表格呈现给用户并明确询问 scope** "
                "(用户列模块名 → 直接采用;说「全部」 → 反问确认;含糊 → 调 request_scope_selection 工具);"
                "scope 确定后再委派 requirement-analyzer(scope=<值>)。"
            ),
            "C": (
                "**request_approval(stage=「测试点清单」, file_path=/analysis/<doc_name>-test-points.md)** "
                "等批准;批准后 task 委派 testcase-designer(scope=<值>),"
                "产出 /testcases/<doc_name>-testcases.md;然后 lint_testcases 检查。"
            ),
            "D": (
                "**跑 lint_testcases 确认 PASS(无阻断/严重项)**;然后 task 委派 testcase-reviewer "
                "产出 /review/<doc_name>-review.md;「需修订」时把意见委派 designer 修订,"
                "然后重新评审,直到通过或达到 2 轮修订上限。"
            ),
            "E": (
                "**read_file 读取定稿用例,再跑一次 lint 确认仍 PASS;然后 "
                "request_approval(stage=「用例终稿」, file_path=/testcases/<doc_name>-testcases.md)** "
                "等批准;批准后 generate_testcase_excel + generate_xmind 导出,"
                "最后输出「交付物下载」链接。"
            ),
        }
        lines: list[str] = []
        for u in uploads:
            doc_name = u["name"].rsplit(".", 1)[0]
            rough = f"/analysis/{doc_name}-rough-scan.md"
            tp = f"/analysis/{doc_name}-test-points.md"
            tc = f"/testcases/{doc_name}-testcases.md"
            rv = f"/review/{doc_name}-review.md"
            if rv in files:
                stage = "E"
                stage_name = "已评审,等导出收尾"
            elif tc in files:
                stage = "D"
                stage_name = "已设计用例,待 lint + 评审"
            elif tp in files:
                stage = "C"
                stage_name = "已做详细分析,待用例设计"
            elif rough in files:
                stage = "B"
                stage_name = "已粗扫,待询问 scope"
            else:
                stage = "A"
                stage_name = "未开始"
            action = stage_to_action[stage]
            lines.append(
                f"### {u['name']}(doc_name=`{doc_name}`)\n"
                f"- 当前阶段:**{stage_name}**(状态 {stage})\n"
                f"- 下一步动作:{action}"
            )
        return "\n\n".join(lines)

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
