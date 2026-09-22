"""自定义 HTTP 路由:文件上传与产物下载(前后端分离契约)。

挂载方式:langgraph.json 配置 "http": {"app": "./server_ext.py:app"},
langgraph dev 启动时会把本 app 的路由与 LangGraph API 路由合并对外服务。

对外契约(前端只需这两个接口):
- POST /api/upload   multipart 表单:file(文件二进制)、thread_id(会话线程 ID)
  原始字节流落盘到 uploads/ 目录,同时以统一的 FileData 形式(文本 utf-8,
  二进制 base64)注入该线程的 agent files 状态;后续解析、登记由
  DoclingParseMiddleware / FileUploadMiddleware 在对话中自动完成。
- GET  /api/download?file=<产物文件名>   从 outputs/ 目录下载交付物
  (Excel / XMind / Markdown / HTML 预览),与注入提示词中的下载链接对应。

服务地址默认读 LANGGRAPH_API_URL(缺省 http://127.0.0.1:2024),
即本服务自身;跨进程部署时指到 LangGraph API 地址即可。
"""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from langgraph_sdk import get_client
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from middleware.docling_parse import DOC_EXTENSIONS, UPLOAD_PREFIX

PROJECT_ROOT = Path(__file__).resolve().parent
# 原始上传文件的落盘目录
UPLOAD_DIR = PROJECT_ROOT / "uploads"
# 交付物目录,与 tools/excel_tool.py、tools/docling_tool.py 的 OUTPUT_DIR 一致
OUTPUT_DIR = PROJECT_ROOT / "outputs"

# 除 docling 可解析的格式外,纯文本直接放行(中间件跳过解析、直接登记)
ALLOWED_EXTENSIONS = DOC_EXTENSIONS | {".md", ".markdown", ".txt"}
TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
# 单个上传文件大小上限(原始字节数)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

LANGGRAPH_API_URL = os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:2024")


def _safe_filename(name: str) -> str:
    """去掉路径成分与非法字符;空名兜底为 upload。"""
    base = Path(name).name  # 防 multipart 文件名带目录
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", Path(base).stem).strip("._") or "upload"
    return f"{stem}{Path(base).suffix.lower()}"


def _dedup_path(directory: Path, filename: str) -> Path:
    """目标已存在时追加时间戳后缀,避免覆盖他人上传。"""
    path = directory / filename
    if not path.exists():
        return path
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return directory / f"{path.stem}-{stamp}{path.suffix}"


async def upload(request: Request) -> JSONResponse:
    """接收前端上传的文件:落盘 uploads/ 并注入线程 files 状态。"""
    form = await request.form()
    file = form.get("file")
    thread_id = form.get("thread_id")

    if not isinstance(thread_id, str) or not thread_id.strip():
        return JSONResponse({"error": "缺少 thread_id(会话线程 ID)"}, status_code=400)
    if file is None or not getattr(file, "filename", None):
        return JSONResponse({"error": "缺少 file(上传文件)"}, status_code=400)

    filename = _safe_filename(file.filename)
    if Path(filename).suffix not in ALLOWED_EXTENSIONS:
        return JSONResponse(
            {"error": f"不支持的文件类型:{Path(filename).suffix or '(无扩展名)'},"
                      f"支持:{', '.join(sorted(ALLOWED_EXTENSIONS))}"},
            status_code=400,
        )

    raw = await file.read()
    if not raw:
        return JSONResponse({"error": "文件内容为空"}, status_code=400)
    if len(raw) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限"},
            status_code=400,
        )

    # 1. 原始字节流原样落盘,服务器本地留档
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    disk_path = _dedup_path(UPLOAD_DIR, filename)
    disk_path.write_bytes(raw)

    # 2. 以统一 FileData 形式注入 agent 对话状态:
    #    文本走 utf-8,二进制统一 base64(中间件按 encoding 解码)
    state_path = f"{UPLOAD_PREFIX}{disk_path.name}"
    now = datetime.now(timezone.utc).isoformat()
    if disk_path.suffix in TEXT_EXTENSIONS:
        content, encoding = raw.decode("utf-8", errors="replace"), "utf-8"
    else:
        content, encoding = base64.b64encode(raw).decode("ascii"), "base64"

    client = get_client(url=LANGGRAPH_API_URL)
    try:
        await client.threads.update_state(
            thread_id.strip(),
            values={
                "files": {
                    state_path: {
                        "content": content,
                        "encoding": encoding,
                        "created_at": now,
                        "modified_at": now,
                    }
                }
            },
        )
    except Exception as e:  # noqa: BLE001
        disk_path.unlink(missing_ok=True)  # 注入失败不留孤儿文件
        return JSONResponse(
            {"error": f"写入会话状态失败(线程是否存在?):{type(e).__name__}: {e}"},
            status_code=502,
        )

    return JSONResponse(
        {"path": state_path, "name": disk_path.name, "size": len(raw)},
        status_code=200,
    )


async def download(request: Request) -> FileResponse | JSONResponse:
    """从 outputs/ 目录下载交付物,防路径穿越。"""
    name = request.query_params.get("file", "")
    if not name:
        return JSONResponse({"error": "缺少 file 参数"}, status_code=400)
    path = (OUTPUT_DIR / name).resolve()
    if OUTPUT_DIR.resolve() not in path.parents or not path.is_file():
        return JSONResponse({"error": f"文件不存在:{name}"}, status_code=404)
    return FileResponse(path, filename=path.name)


app = Starlette(
    routes=[
        Route("/api/upload", upload, methods=["POST"]),
        Route("/api/download", download, methods=["GET"]),
    ]
)
