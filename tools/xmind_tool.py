"""generate_xmind 工具:把 markmap 兼容的 Markdown 大纲转换为思维导图文件。

一次产出三个文件到 agent 项目根下的 outputs/(真实磁盘,与 excel_tool 一致):
    <doc_name>-mindmap.xmind  XMind 2020+/Zen 格式(zip 包,可用 XMind 客户端打开编辑)
    <doc_name>-mindmap.html   markmap 交互式思维导图(浏览器直接打开,需联网加载 CDN)
    <doc_name>-mindmap.md     原始 Markdown 大纲(留档)

XMind Zen 格式无需第三方库:zip 内含 content.json(树结构)/metadata.json/manifest.json。
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

# 输出目录(agent 项目根下的 outputs/)
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"


class MindmapMarkdownError(ValueError):
    """Markdown 大纲不符合约定格式。"""


class _Node:
    """思维导图节点。"""

    def __init__(self, title: str):
        self.title = title
        self.children: list[_Node] = []


def parse_mindmap_markdown(markdown: str) -> _Node:
    """解析 markmap 兼容的 Markdown(# 标题 + 缩进列表)为树。

    规则:
    - ATX 标题(#/##/###…)按 # 数量定层级;
    - 列表行(-/*/+ 开头)层级 = 最近标题层级 + 缩进深度 + 1;
      缩进深度按"本标题段内出现过的缩进宽度排名"计算,兼容 2/4 空格或 tab;
    - 第一个节点为根,只接受单根(多个根时包一层公共根)。

    Raises:
        MindmapMarkdownError: 内容为空或未解析出任何节点。
    """
    lines = markdown.strip().splitlines()

    # (有效层级, 标题) 序列
    entries: list[tuple[int, str]] = []
    heading_level = 0  # 最近一次标题的层级
    indent_ranks: list[int] = []  # 当前标题段内出现过的缩进宽度(升序去重)
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            heading_level = len(m.group(1))
            indent_ranks = []
            entries.append((heading_level, m.group(2).strip()))
            continue
        m = re.match(r"^([ \t]*)[-*+]\s+(.*)$", line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            if indent not in indent_ranks:
                indent_ranks.append(indent)
                indent_ranks.sort()
            depth = indent_ranks.index(indent)
            entries.append((heading_level + 1 + depth, m.group(2).strip()))
            continue
        # 普通文本行:作为上一节点的延续忽略,避免噪音进图

    if not entries:
        raise MindmapMarkdownError("未在输入中找到标题(# 开头)或列表项(- 开头)")

    # 归一化:把最小层级压到 0
    min_level = min(level for level, _ in entries)
    entries = [(level - min_level, title) for level, title in entries]

    root = _Node(entries[0][1])
    stack: list[tuple[int, _Node]] = [(entries[0][0], root)]
    for level, title in entries[1:]:
        node = _Node(title)
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        stack[-1][1].children.append(node)
        stack.append((level, node))

    # 多根保护:根层级若出现多个节点,上面循环已把后续同级节点挂到根下,
    # 仅当根本身有兄弟时(第一行之前没有更小层级)才会出现,此处无需处理。
    return root


def _node_to_topic(node: _Node) -> dict:
    """转换为 XMind content.json 的 topic 结构。"""
    topic = {"id": uuid4().hex, "class": "topic", "title": node.title}
    if node.children:
        topic["children"] = {
            "attached": [_node_to_topic(c) for c in node.children],
        }
    return topic


def write_xmind(root: _Node, path: Path) -> None:
    """把节点树写成 XMind 2020+/Zen 格式的 .xmind 文件。"""
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    content = [{
        "id": uuid4().hex,
        "class": "sheet",
        "title": path.stem,
        "rootTopic": _node_to_topic(root),
        "topicPositioning": "fixed",
    }]
    metadata = {
        "creator": {"name": "testcase-agent", "version": "1.0.0"},
        "createTime": now,
        "modifyTime": now,
    }
    manifest = {
        "file-entries": {"content.json": {}, "metadata.json": {}},
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.json", json.dumps(content, ensure_ascii=False))
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False))
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))


_MARKMAP_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  html, body { margin: 0; padding: 0; }
  #mindmap { display: block; width: 100vw; height: 100vh; }
</style>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script src="https://cdn.jsdelivr.net/npm/markmap-lib"></script>
<script src="https://cdn.jsdelivr.net/npm/markmap-view"></script>
</head>
<body>
<svg id="mindmap"></svg>
<script>
const markdown = {markdown_json};
const { Markmap, Transformer } = window.markmap;
const { root } = new Transformer().transform(markdown);
Markmap.create("#mindmap", null, root);
</script>
</body>
</html>
"""


def write_markmap_html(markdown: str, path: Path, title: str) -> None:
    """生成浏览器可看的 markmap 交互式 HTML(打开时需联网加载 CDN)。"""
    html = _MARKMAP_HTML_TEMPLATE.replace("{title}", title).replace(
        "{markdown_json}", json.dumps(markdown, ensure_ascii=False),
    )
    path.write_text(html, encoding="utf-8")


def _to_file_data(content: str) -> dict:
    """构造 deepagents 虚拟文件系统的 FileData 对象(与前端 types.ts 结构一致)。"""
    now = datetime.now(timezone.utc).isoformat()
    return {"content": content, "encoding": "utf-8",
            "created_at": now, "modified_at": now}


@tool
def generate_xmind(
    mindmap_markdown: str,
    doc_name: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command | str:
    """把测试点思维导图(markmap 兼容的 Markdown 大纲)转换为 .xmind 文件和可在浏览器查看的 HTML。

    Args:
        mindmap_markdown: markmap 兼容的 Markdown 大纲——根节点用 # 标题,
            下级用 ##/### 标题或 - 开头的缩进列表(缩进用空格,层级即缩进深度)。
        doc_name: 需求文档名(不含扩展名),用于命名输出文件。

    Returns:
        成功时返回生成的三个文件路径(.xmind / .html / .md);
        失败时返回以 ERROR: 开头的错误说明。
    """
    try:
        root = parse_mindmap_markdown(mindmap_markdown)
    except MindmapMarkdownError as e:
        return f"ERROR: 思维导图 Markdown 格式错误:{e}。请按 # 标题 + 缩进列表的格式重新组织后重试。"

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", doc_name).strip() or "mindmap"
        xmind_path = OUTPUT_DIR / f"{safe_name}-mindmap.xmind"
        html_path = OUTPUT_DIR / f"{safe_name}-mindmap.html"
        md_path = OUTPUT_DIR / f"{safe_name}-mindmap.md"

        md_path.write_text(mindmap_markdown.strip() + "\n", encoding="utf-8")
        write_xmind(root, xmind_path)
        write_markmap_html(mindmap_markdown, html_path, safe_name)
    except Exception as e:  # noqa: BLE001 - 工具不能把异常抛给模型循环
        return f"ERROR: 生成思维导图失败:{type(e).__name__}: {e}"

    summary = (
        f"思维导图已生成:\n"
        f"- XMind 文件(可用 XMind 客户端打开编辑): {xmind_path}\n"
        f"- 浏览器预览(双击打开,需联网): {html_path}\n"
        f"- Markdown 大纲留档: {md_path}"
    )
    # 双写:除了落真实磁盘,同时写入 agent 虚拟文件系统的 /outputs/ 路径,
    # 前端 OutputsBar 从 state.files 读取 *-mindmap.md / *-mindmap.html 展示预览。
    # files 状态走合并 reducer,只追加这两个 key,不会影响 /uploads/ 下的文件。
    return Command(
        update={
            "files": {
                f"/outputs/{safe_name}-mindmap.md": _to_file_data(mindmap_markdown.strip()),
                f"/outputs/{safe_name}-mindmap.html": _to_file_data(
                    html_path.read_text(encoding="utf-8")
                ),
            },
            "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
        }
    )
