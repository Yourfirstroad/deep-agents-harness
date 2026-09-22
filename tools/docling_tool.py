"""parse_document 工具:调用本地 docling-serve 解析文档(PDF 等),返回 Markdown。

走「路线 A」:标准管线(版面/OCR) + 图片描述,即文档中的图片由多模态大模型
生成中文描述,插入到输出 Markdown 的 <!-- image --> 占位符下方。

配置从项目根 .env 读取(本项目自定义的客户端变量,非 docling-serve 官方变量):
    DOCLING_SERVE_URL             docling-serve 服务地址,默认 http://localhost:5001
    DOCLING_SERVE_API_KEY         服务端开了鉴权时填,对应请求头 X-Api-Key
    DOCLING_PICTURE_DESCRIPTION   true 时启用多模态图片描述
    QWEN_API_KEY                  千问(DashScope)API key,图片描述的首选模型配置
    QWEN_BASE_URL                 OpenAI 兼容端点(不含 /chat/completions),
                                  默认 https://dashscope.aliyuncs.com/compatible-mode/v1
    QWEN_VL_MODEL                 千问 VL 模型名,默认 qwen-vl-max
    DOCLING_VLM_MODEL             (回退项)未配置 QWEN_API_KEY 时使用的多模态模型名
    DOCLING_VLM_BASE_URL          (回退项)OpenAI 兼容端点
    DOCLING_VLM_API_KEY           (回退项)多模态模型的 API key
    DOCLING_DENOISE               true(默认)时解析后对 Markdown 降噪,见 tools/denoise.py
    DOCLING_SAVE_JSON             true 时把原始 DoclingDocument JSON 落盘到 outputs/(调试)

QWEN_* 与 DOCLING_VLM_* 的优先关系:DOCLING_PICTURE_DESCRIPTION=true 时,
若 QWEN_API_KEY 已配置则三组 QWEN_* 生效(模型/端点/key 各自独立回退),
否则回退到 DOCLING_VLM_*(老配置不破坏)。

服务端要求:docling-serve 容器以 DOCLING_SERVE_ENABLE_REMOTE_SERVICES=true 和
DOCLING_SERVE_ALLOW_CUSTOM_PICTURE_DESCRIPTION_CONFIG=true 启动(已配置)。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from langchain_core.tools import tool

from tools.denoise import DenoiseResult, denoise_markdown, render_audit_json, render_audit_md

# Markdown 输出目录(agent 项目根下的 outputs/,与 excel_tool 一致)
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"

_POLL_INTERVAL = 2.0
_MAX_WAIT = 3600.0


class DoclingServeError(RuntimeError):
    """docling-serve 调用失败。"""


@dataclass
class DoclingResult:
    """docling-serve 一次解析的原始结果。"""

    markdown: str
    # DoclingDocument JSON;服务未返回(旧版本/未请求)时为 None,降噪退化为纯规则
    json_content: dict | None = None


@dataclass
class CleanedParse:
    """解析 + 降噪的完整结果。"""

    raw_markdown: str
    cleaned_markdown: str
    denoise: DenoiseResult | None  # 降噪关闭时为 None
    json_content: dict | None


def _denoise_enabled() -> bool:
    return os.getenv("DOCLING_DENOISE", "true").lower() == "true"


# DashScope 官方 OpenAI 兼容端点(qwen-vl 系列)
_QWEN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_QWEN_DEFAULT_MODEL = "qwen-vl-max"


def _vlm_config() -> tuple[str, str, str]:
    """解析图片描述用的多模态模型配置:(model, base_url, api_key)。

    QWEN_API_KEY 配置后三组 QWEN_* 生效(各自独立带回退);
    未配置时整体回退到 DOCLING_VLM_*(向后兼容老 .env)。
    """
    if os.getenv("QWEN_API_KEY"):
        return (
            os.getenv("QWEN_VL_MODEL") or _QWEN_DEFAULT_MODEL,
            os.getenv("QWEN_BASE_URL") or _QWEN_DEFAULT_BASE_URL,
            os.environ["QWEN_API_KEY"],
        )
    return (
        os.environ["DOCLING_VLM_MODEL"],
        os.environ["DOCLING_VLM_BASE_URL"],
        os.environ["DOCLING_VLM_API_KEY"],
    )


# 图片描述提示词:面向需求文档场景做结构化抽取,描述会直接进需求分析流程,
# 只"简要描述"会丢流程/表格信息,影响下游测试点提取
_PICTURE_PROMPT = (
    "请用中文描述这张图片的内容,供需求分析使用。"
    "若是流程图/时序图/状态图,逐步列出节点、分支条件与流转关系;"
    "若是表格,用 Markdown 表格完整转录;"
    "若是界面截图或原型图,列出页面上的关键字段、按钮、文案与交互说明;"
    "若是纯装饰图片,只回答「装饰图」。"
    "直接输出描述,不要输出思考过程或前缀。"
)


def _build_options() -> dict:
    """按 .env 配置组装 docling-serve 的 ConvertDocumentsOptions。

    结构依据运行中服务 /openapi.json 的 PictureDescriptionVlmEngineOptions schema:
    model_spec 必填 name/default_repo_id/prompt/response_format,
    模型名经 api_overrides.api_openai.params 传给 OpenAI 兼容端点。

    降噪开启时 to_formats 附带 json:利用 DoclingDocument 的 label/prov.page_no
    做 JSON 引导的页眉页脚/水印过滤(见 tools/denoise.py)。
    """
    options: dict = {
        "to_formats": ["md", "json"] if _denoise_enabled() else ["md"],
        "include_images": True,
        "image_export_mode": "placeholder",
        "images_scale": 2.0,
    }

    if os.getenv("DOCLING_PICTURE_DESCRIPTION", "").lower() == "true":
        model, base_url, api_key = _vlm_config()
        base_url = base_url.rstrip("/")
        prompt = _PICTURE_PROMPT
        options["do_picture_description"] = True
        # 小于页面面积 2% 的图片(图标、logo 等)跳过描述
        options["picture_description_area_threshold"] = 0.02
        options["picture_description_custom_config"] = {
            "model_spec": {
                "name": model,
                # schema 必填字段,API 引擎下不会被真正使用
                "default_repo_id": model,
                "prompt": prompt,
                "response_format": "plaintext",
                "api_overrides": {
                    "api_openai": {
                        # 用 max_tokens 而非 max_completion_tokens:
                        # DashScope 兼容模式对后者支持不稳定
                        "params": {"model": model, "max_tokens": 500},
                    },
                },
            },
            "engine_options": {
                "engine_type": "api_openai",
                "url": f"{base_url}/chat/completions",
                "headers": {"Authorization": f"Bearer {api_key}"},
                "timeout": 120.0,
                "concurrency": 4,
            },
            "prompt": prompt,
            "scale": 2.0,
            "picture_area_threshold": 0.02,
        }

    return options


def convert_document(file_path: str | Path) -> DoclingResult:
    """解析文档并返回 Markdown(+ DoclingDocument JSON)。

    流程:异步提交 -> 轮询状态 -> 拉取结果(图片描述走远程多模态模型,耗时较长)。

    Raises:
        DoclingServeError: 文件不存在、服务返回错误或任务失败。
    """
    path = Path(file_path)
    if not path.is_file():
        raise DoclingServeError(f"文件不存在: {path}")

    server = os.getenv("DOCLING_SERVE_URL", "http://localhost:5001").rstrip("/")
    serve_key = os.getenv("DOCLING_SERVE_API_KEY") or ""
    headers = {"X-Api-Key": serve_key} if serve_key else {}

    # multipart 表单:列表字段拆成多个同名字段,对象字段用 JSON 字符串
    form: list[tuple[str, str]] = []
    for key, value in _build_options().items():
        if isinstance(value, list):
            form.extend((key, str(item)) for item in value)
        elif isinstance(value, dict):
            form.append((key, json.dumps(value)))
        elif isinstance(value, bool):
            form.append((key, "true" if value else "false"))
        else:
            form.append((key, str(value)))

    with path.open("rb") as f:
        resp = requests.post(
            f"{server}/v1/convert/file/async",
            files={"files": (path.name, f)},
            data=form,
            headers=headers,
            timeout=60,
        )
    if resp.status_code >= 400:
        raise DoclingServeError(f"提交任务失败 HTTP {resp.status_code}: {resp.text[:500]}")
    task_id = resp.json()["task_id"]

    deadline = time.time() + _MAX_WAIT
    while time.time() < deadline:
        status = requests.get(
            f"{server}/v1/status/poll/{task_id}", headers=headers, timeout=30,
        ).json().get("task_status")
        if status in ("success", "failure"):
            break
        time.sleep(_POLL_INTERVAL)
    else:
        raise DoclingServeError(f"任务 {task_id} 等待超时")

    result = requests.get(
        f"{server}/v1/result/{task_id}", headers=headers, timeout=60,
    ).json()
    if status != "success":
        raise DoclingServeError(f"任务失败: {json.dumps(result.get('errors', result))[:500]}")

    document = result.get("document", {})
    markdown = document.get("md_content", "")
    if not markdown:
        raise DoclingServeError("服务返回成功但 md_content 为空")
    # json_content 允许缺失(旧版本服务或未请求 json 格式),降噪会退化为纯规则
    json_content = document.get("json_content")
    if not isinstance(json_content, dict):
        json_content = None
    return DoclingResult(markdown=markdown, json_content=json_content)


def parse_and_clean(file_path: str | Path) -> CleanedParse:
    """解析文档并按配置降噪,返回原始/清洗后文本与审计结果。"""
    res = convert_document(file_path)
    if not _denoise_enabled():
        return CleanedParse(res.markdown, res.markdown, None, res.json_content)
    denoise = denoise_markdown(res.markdown, res.json_content)
    return CleanedParse(res.markdown, denoise.markdown, denoise, res.json_content)


def safe_doc_name(doc_name: str) -> str:
    """文件名清洗:去掉路径非法字符,与 parse_document 原有逻辑一致。"""
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in doc_name).strip() or "document"


def save_parse_outputs(doc_name: str, parsed: CleanedParse) -> dict[str, Path]:
    """把解析产物落盘到 outputs/,返回 {用途: 路径}。

    约定(与 server_ext.py 的 /api/download 配合,均可下载):
        <doc>.md            清洗后 Markdown(文件名保持不变,向后兼容)
        <doc>.raw.md        原始 Markdown(降噪开启时)
        <doc>.denoise.json  删除审计(机器可读)
        <doc>.docling.json  原始 DoclingDocument JSON(仅 DOCLING_SAVE_JSON=true)
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    name = safe_doc_name(doc_name)
    saved: dict[str, Path] = {}

    md_path = OUTPUT_DIR / f"{name}.md"
    md_path.write_text(parsed.cleaned_markdown, encoding="utf-8")
    saved["md"] = md_path

    if parsed.denoise is not None:
        raw_path = OUTPUT_DIR / f"{name}.raw.md"
        raw_path.write_text(parsed.raw_markdown, encoding="utf-8")
        saved["raw_md"] = raw_path

        audit_path = OUTPUT_DIR / f"{name}.denoise.json"
        audit_path.write_text(render_audit_json(parsed.denoise), encoding="utf-8")
        saved["audit"] = audit_path

    if parsed.json_content is not None and os.getenv("DOCLING_SAVE_JSON", "").lower() == "true":
        json_path = OUTPUT_DIR / f"{name}.docling.json"
        json_path.write_text(
            json.dumps(parsed.json_content, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        saved["docling_json"] = json_path

    return saved


def denoise_summary_line(parsed: CleanedParse) -> str:
    """一行降噪摘要,供工具返回文本与中间件 SystemMessage 复用。"""
    d = parsed.denoise
    if d is None:
        return ""
    if d.aborted:
        return "降噪检测到删除量异常(超过安全阈值),已保留原始 Markdown 未做清洗。"
    total = len(d.removals)
    if total == 0:
        return "降噪检查完成,未发现可删除的噪音内容。"
    parts = "、".join(f"{k} {v} 处" for k, v in d.stats.items() if k.startswith("R") and v > 0)
    return f"降噪删除了 {total} 处疑似噪音({parts});遵循宁留勿删原则,删除明细见审计文件。"


@tool
def parse_document(file_path: str, doc_name: str) -> str:
    """解析本地文档(PDF/Word/图片等)为 Markdown 并保存到本机,内容中的图片会由多模态大模型生成中文描述;解析后自动降噪(去除页眉页脚/水印/页码等噪音)。

    Args:
        file_path: 待解析文档的绝对路径。
        doc_name: 文档名(不含扩展名),用于命名输出的 Markdown 文件。

    Returns:
        成功时返回清洗后的 Markdown 全文,并注明保存路径与降噪摘要;失败时返回以 ERROR: 开头的错误说明。
    """
    try:
        parsed = parse_and_clean(file_path)
        saved = save_parse_outputs(doc_name, parsed)
    except DoclingServeError as e:
        return f"ERROR: 解析文档失败:{e}"
    except Exception as e:  # noqa: BLE001 - 工具不能把异常抛给模型循环
        return f"ERROR: 解析文档失败:{type(e).__name__}: {e}"

    summary = denoise_summary_line(parsed)
    header = f"(已保存到 {saved['md']})"
    if summary:
        header += f"\n({summary}原始版本: {saved.get('raw_md', '-')},审计: {saved.get('audit', '-')})"
    return f"{header}\n\n{parsed.cleaned_markdown}"
