"""DoclingParseMiddleware:上传文档的自动解析中间件。

挂在 create_deep_agent(middleware=[...]) 里,在每次模型调用前(before_model)
扫描状态 files 中 /uploads/ 下的原始文档(PDF/Word/PPT/图片等),
调用本地 docling-serve 解析为 Markdown,经降噪清洗(去除页眉页脚/水印/页码等,
见 tools/denoise.py)后以 /uploads/<原名>.md 写回 files,
供 FileUploadMiddleware 登记、供模型用 read_file 读取。

与"前端解析"路线的区别:前端只需把原始文件以 FileData 注入 files
(文本用 utf-8,二进制用 base64),解析行为收敛在后端,不依赖前端能力。

解析结果写回后删除原始文件条目(base64 体积大,留在 state 里会撑爆上下文
与 checkpoint);已处理过的路径记录在自定义状态 parsed_uploads 中,
避免 before_model 每次调用重复解析。解析失败不中断 run,以 SystemMessage
告知模型,让其提示用户重新上传。

降噪产物:清洗后 md 写回 files;精简审计摘要写入 /uploads/<原名>.denoise.md
供模型/子代理自查;原始 md、审计 JSON 等完整产物落盘 outputs/(可下载)。
降噪遵循"宁留勿删"原则,删除占比超安全阈值时保留原文并在消息中说明。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import SystemMessage

from tools.denoise import render_audit_md
from tools.docling_tool import denoise_summary_line, parse_and_clean, save_parse_outputs

UPLOAD_PREFIX = "/uploads/"

# 交给 docling 解析的扩展名;.md/.txt 等纯文本不解析,直接留给 FileUploadMiddleware
DOC_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx",
    ".html", ".htm", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
}


class DoclingParseState(AgentState):
    # 已处理过(无论成败)的上传路径,防止重复解析
    parsed_uploads: NotRequired[list[str]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_upload(file_data: Any) -> bytes | None:
    """从 FileData 中取出原始字节;结构非法或解码失败返回 None。"""
    if not isinstance(file_data, dict):
        return None
    content = file_data.get("content")
    if not isinstance(content, str):
        return None
    if file_data.get("encoding") == "base64":
        # 兼容 data URI 形式(data:application/pdf;base64,....)
        payload = content.split(",", 1)[-1] if content.startswith("data:") else content
        try:
            return base64.b64decode(payload)
        except (binascii.Error, ValueError):
            return None
    return content.encode("utf-8")


class DoclingParseMiddleware(AgentMiddleware):
    """自动解析 /uploads/ 下新上传文档的中间件。"""

    state_schema = DoclingParseState

    def before_model(self, state: DoclingParseState, runtime) -> dict[str, Any] | None:
        return self._parse_pending(state)

    async def abefore_model(self, state: DoclingParseState, runtime) -> dict[str, Any] | None:
        # convert_document 是同步阻塞调用(轮询 docling-serve,可能耗时数分钟),
        # 在 langgraph dev 的 ASGI 事件循环里会被 blockbuster 拦截,放到线程里执行
        return await asyncio.to_thread(self._parse_pending, state)

    # ------------------------------------------------------------------
    # 扫描并解析所有待处理的上传文档
    # ------------------------------------------------------------------
    def _parse_pending(self, state: DoclingParseState) -> dict[str, Any] | None:
        files = state.get("files") or {}
        done = set(state.get("parsed_uploads") or [])

        pending = [
            path
            for path in files
            if path.startswith(UPLOAD_PREFIX)
            and Path(path).suffix.lower() in DOC_EXTENSIONS
            and path not in done
        ]
        if not pending:
            return None

        files_update: dict[str, Any] = {}
        messages: list[SystemMessage] = []
        parsed = [*done]

        for path in pending:
            parsed.append(path)
            name = path.rsplit("/", 1)[-1]
            stem = Path(name).stem

            raw = _decode_upload(files[path])
            if raw is None:
                messages.append(
                    SystemMessage(
                        content=f"上传文件 {name} 的内容无法读取(非法的 FileData 或 base64 编码),"
                        "已跳过解析。请在回复中提示用户重新上传。"
                    )
                )
                continue

            tmp_path = None
            try:
                # docling-serve 走 multipart 上传,需要真实文件
                with tempfile.NamedTemporaryFile(
                    suffix=Path(name).suffix, delete=False
                ) as tmp:
                    tmp.write(raw)
                    tmp_path = tmp.name
                parsed = parse_and_clean(tmp_path)
            except Exception as e:  # noqa: BLE001 - 解析失败不能中断 run
                messages.append(
                    SystemMessage(
                        content=f"上传文档 {name} 自动解析失败:{type(e).__name__}: {e}。"
                        "请在回复中告知用户解析失败,建议检查 docling-serve 服务或重新上传。"
                    )
                )
                continue
            finally:
                if tmp_path:
                    Path(tmp_path).unlink(missing_ok=True)

            # 完整产物(原始 md、审计 JSON 等)落盘 outputs/,可供用户下载核对
            try:
                save_parse_outputs(stem, parsed)
            except OSError:
                pass  # 落盘失败不阻断主流程,files 里已有清洗结果

            md_path = f"{UPLOAD_PREFIX}{stem}.md"
            files_update[md_path] = {
                "content": parsed.cleaned_markdown,
                "encoding": "utf-8",
                "created_at": _now_iso(),
                "modified_at": _now_iso(),
            }
            # 删除原始文件条目:base64 体积大,且后续流程只需要解析后的 Markdown
            files_update[path] = None

            if parsed.denoise is None:
                # 降噪关闭(DOCLING_DENOISE=false):保持原有消息
                messages.append(
                    SystemMessage(
                        content=f"上传文档 {name} 已自动解析为 Markdown:{md_path},"
                        "后续请按需求文档处理流程读取该文件。"
                    )
                )
                continue

            # 审计摘要写回 files,供模型/子代理 read_file 核对删除明细
            denoise_path = f"{UPLOAD_PREFIX}{stem}.denoise.md"
            files_update[denoise_path] = {
                "content": render_audit_md(parsed.denoise, stem),
                "encoding": "utf-8",
                "created_at": _now_iso(),
                "modified_at": _now_iso(),
            }
            summary = denoise_summary_line(parsed)
            if parsed.denoise.aborted:
                messages.append(
                    SystemMessage(
                        content=f"上传文档 {name} 已自动解析为 Markdown:{md_path}。{summary}"
                        f"删除明细见 {denoise_path}。后续请按需求文档处理流程读取该文件。"
                    )
                )
            else:
                messages.append(
                    SystemMessage(
                        content=f"上传文档 {name} 已自动解析为 Markdown 并完成降噪:{md_path}。{summary}"
                        f"删除明细见 {denoise_path};若后续发现需求内容缺失,可在该明细中核对,"
                        f"原始未清洗版本已保存到服务器 outputs/{stem}.raw.md。"
                        "后续请按需求文档处理流程读取该文件。"
                    )
                )

        update: dict[str, Any] = {"parsed_uploads": parsed}
        if files_update:
            update["files"] = files_update
        if messages:
            update["messages"] = messages
        return update
