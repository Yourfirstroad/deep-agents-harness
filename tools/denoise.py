"""Markdown 降噪模块:docling 解析结果的清洗管线(方案 B + C)。

两条降噪路径:
- 方案 B(JSON 引导):利用 docling-serve 返回的 DoclingDocument JSON
  (texts[] 的 label / content_layer / prov.page_no)定位噪音文本,
  在 Markdown 中整行删除。R1(页眉页脚 label)、R2(跨页重复短文本)走这条路。
- 方案 C(纯规则兜底):R3(孤立页码行)、R4(空图片占位符)、R5(连续空行压缩),
  不依赖 JSON,json_content 缺失时仍生效。

核心原则:宁留勿删。误删需求 = 漏测,代价远高于留一点噪音:
- JSON 噪音文本在 md 中只删"整行匹配",出现在段落中间不删;
- 所有删除记录审计(RemovalRecord),可回溯;
- 删除行占比超过 DOCLING_DENOISE_MAX_DELETE_RATIO 时整单放弃,返回原文。

配置从项目根 .env 读取:
    DOCLING_DENOISE=true                  总开关(false 时调用方应跳过本模块)
    DOCLING_DENOISE_REPEAT_MIN_PAGES=3    R2 判定跨页重复的最少不同页数
    DOCLING_DENOISE_REPEAT_MAXLEN=60      R2 短文本长度上限(字符)
    DOCLING_DENOISE_PAGE_NUMBER=true      R3 孤立页码行开关
    DOCLING_DENOISE_EMPTY_IMAGE=true      R4 空图片占位符开关
    DOCLING_DENOISE_MAX_DELETE_RATIO=0.3  安全刹车:删除行数占原总行数上限
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field

# docling 版面标签:页眉页脚类 furniture
_FURNITURE_LABELS = {"page_header", "page_footer"}
# R2 中允许跨页重复的标签:章节标题在多页出现是合法的(如"修订记录"分节)
_R2_ALLOWED_LABELS = {"section_header", "title"}

_PAGE_NUMBER_PATTERNS = [
    re.compile(r"^\d{1,4}$"),
    re.compile(r"^-\s*\d{1,4}\s*-$"),
    re.compile(r"^第\s*\d{1,4}\s*页$"),
    re.compile(r"^\d{1,4}\s*/\s*\d{1,4}$"),
]

_IMAGE_PLACEHOLDER = "<!-- image -->"

_MAX_TEXT_LOG = 120  # 审计中保留的被删文本长度上限


@dataclass
class RemovalRecord:
    """一条删除记录(审计用)。"""

    rule_id: str
    rule_name: str
    text: str  # 被删文本,截断到 _MAX_TEXT_LOG 字符
    line_no: int | None  # 在原始 md 中的行号(1-based),批量定位不到时为 None
    page_no: int | None  # 来自 JSON prov,纯规则为 None


@dataclass
class DenoiseResult:
    """降噪结果。aborted=True 表示触发安全刹车,markdown 为原文。"""

    markdown: str
    removals: list[RemovalRecord] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    aborted: bool = False


@dataclass
class _Config:
    repeat_min_pages: int = 3
    repeat_maxlen: int = 60
    page_number: bool = True
    empty_image: bool = True
    max_delete_ratio: float = 0.3


def _load_config(overrides: dict | None = None) -> _Config:
    cfg = _Config()

    def _int(key: str, default: int) -> int:
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            return default

    def _float(key: str, default: float) -> float:
        try:
            return float(os.getenv(key, str(default)))
        except ValueError:
            return default

    def _bool(key: str, default: bool) -> bool:
        val = os.getenv(key)
        if val is None:
            return default
        return val.lower() == "true"

    cfg.repeat_min_pages = _int("DOCLING_DENOISE_REPEAT_MIN_PAGES", cfg.repeat_min_pages)
    cfg.repeat_maxlen = _int("DOCLING_DENOISE_REPEAT_MAXLEN", cfg.repeat_maxlen)
    cfg.page_number = _bool("DOCLING_DENOISE_PAGE_NUMBER", cfg.page_number)
    cfg.empty_image = _bool("DOCLING_DENOISE_EMPTY_IMAGE", cfg.empty_image)
    cfg.max_delete_ratio = _float("DOCLING_DENOISE_MAX_DELETE_RATIO", cfg.max_delete_ratio)

    # 按次调用覆盖(前端降噪参数调节经 CLI 传入);env 只是默认值
    if overrides:
        if overrides.get("repeat_min_pages") is not None:
            cfg.repeat_min_pages = int(overrides["repeat_min_pages"])
        if overrides.get("repeat_maxlen") is not None:
            cfg.repeat_maxlen = int(overrides["repeat_maxlen"])
        if overrides.get("page_number") is not None:
            cfg.page_number = bool(overrides["page_number"])
        if overrides.get("empty_image") is not None:
            cfg.empty_image = bool(overrides["empty_image"])
        if overrides.get("max_delete_ratio") is not None:
            cfg.max_delete_ratio = float(overrides["max_delete_ratio"])

    cfg.repeat_min_pages = max(2, cfg.repeat_min_pages)
    cfg.repeat_maxlen = max(10, cfg.repeat_maxlen)
    return cfg


def _normalize(s: str) -> str:
    """折叠所有空白为单空格并去首尾,用于跨来源文本对齐。"""
    return re.sub(r"\s+", " ", s).strip()


def _truncate(s: str) -> str:
    return s if len(s) <= _MAX_TEXT_LOG else s[:_MAX_TEXT_LOG] + "…"


def _iter_json_texts(json_content: dict | None) -> list[dict]:
    """从 DoclingDocument JSON 取 texts 列表;结构不符时返回空。"""
    if not isinstance(json_content, dict):
        return []
    texts = json_content.get("texts")
    return texts if isinstance(texts, list) else []


def _text_pages(item: dict) -> set[int]:
    """取一个 JSON text 项出现的页码集合;prov 缺失返回空集。"""
    pages: set[int] = set()
    prov = item.get("prov")
    if isinstance(prov, list):
        for p in prov:
            if isinstance(p, dict) and isinstance(p.get("page_no"), int):
                pages.add(p["page_no"])
    return pages


def _collect_furniture_texts(json_content: dict | None) -> dict[str, tuple[int, int | None]]:
    """R1:收集 furniture/页眉页脚文本。

    Returns:
        {normalize(text): (JSON 中出现次数, 首个页码)}
    """
    found: dict[str, list[int | None]] = {}
    for item in _iter_json_texts(json_content):
        label = item.get("label")
        content_layer = item.get("content_layer")
        if label not in _FURNITURE_LABELS and content_layer != "furniture":
            continue
        text = _normalize(str(item.get("text") or item.get("orig") or ""))
        if not text:
            continue
        pages = _text_pages(item)
        found.setdefault(text, []).append(min(pages) if pages else None)
    return {t: (len(pgs), pgs[0]) for t, pgs in found.items()}


def _collect_cross_page_repeats(json_content: dict | None, cfg: _Config) -> dict[str, int | None]:
    """R2:收集跨 >= repeat_min_pages 个不同页重复的短文本(水印/漏判页眉页脚)。

    按"不同页数"而非出现次数计数(同页正文重复引用术语不算);
    跳过纯数字(留给 R3)和章节标题类标签。

    Returns:
        {normalize(text): 首个页码}
    """
    pages_by_text: dict[str, set[int]] = {}
    for item in _iter_json_texts(json_content):
        label = item.get("label")
        if label in _R2_ALLOWED_LABELS:
            continue
        text = _normalize(str(item.get("text") or item.get("orig") or ""))
        if not (2 <= len(text) <= cfg.repeat_maxlen):
            continue
        if text.isdigit():
            continue
        pages = _text_pages(item)
        if pages:
            pages_by_text.setdefault(text, set()).update(pages)
    return {
        t: min(pages)
        for t, pages in pages_by_text.items()
        if len(pages) >= cfg.repeat_min_pages
    }


def _remove_whole_lines(
    lines: list[str],
    noise_texts: dict[str, tuple[str, str, int | None, int | None]],
    removed_idx: set[int],
    removed_keys: set[str],
) -> None:
    """整行匹配删除:行 normalize 后与噪音文本相等才删。

    noise_texts: {normalize(text): (rule_id, rule_name, page_no, max_allowed)}
        max_allowed 为该文本允许删除的最大行数(防正文同名文本误删),None 表示不限。
    删除结果写入 removed_idx(行下标),命中的噪音文本记入 removed_keys。
    """
    for idx, line in enumerate(lines):
        if idx in removed_idx:
            continue
        norm = _normalize(line)
        if not norm or norm not in noise_texts:
            continue
        rule_id, rule_name, page_no, max_allowed = noise_texts[norm]
        # 同一文本的删除行数超限说明它很可能也是正文内容,整体跳过
        already = sum(1 for i in removed_idx if _normalize(lines[i]) == norm)
        if max_allowed is not None and already >= max_allowed:
            continue
        removed_idx.add(idx)
        removed_keys.add(norm)


def denoise_markdown(
    markdown: str,
    json_content: dict | None = None,
    overrides: dict | None = None,
) -> DenoiseResult:
    """清洗 docling 解析出的 Markdown。

    Args:
        markdown: docling-serve 返回的 md_content。
        json_content: 同一次解析的 DoclingDocument JSON;为 None 时
            R1/R2 自动跳过,仅执行纯规则 R3-R5。
        overrides: 按次调用传入的参数覆盖(键:_Config 字段名),
            优先级高于 .env;供 CLI/前端调参使用。

    Returns:
        DenoiseResult;触发安全刹车时 aborted=True 且 markdown 为原文。
    """
    cfg = _load_config(overrides)
    lines = markdown.split("\n")
    total_lines = len(lines)
    removals: list[RemovalRecord] = []
    stats: dict[str, int] = {}
    removed_idx: set[int] = set()

    # ---- R1: JSON furniture/页眉页脚引导删除 ----
    # docling md 导出默认已排除 furniture,此规则多数情况 0 命中;
    # 命中的是漏网项。允许删除上限 = JSON 中出现次数(放宽到 2 倍)。
    if json_content is not None:
        furniture = _collect_furniture_texts(json_content)
        r1_map = {
            text: ("R1", "页眉页脚(JSON label)", page, count * 2)
            for text, (count, page) in furniture.items()
        }
        r1_before = len(removed_idx)
        hit_keys: set[str] = set()
        _remove_whole_lines(lines, r1_map, removed_idx, hit_keys)
        for idx in sorted(removed_idx):
            norm = _normalize(lines[idx])
            if norm in hit_keys:
                _, name, page, _ = r1_map[norm]
                removals.append(RemovalRecord("R1", name, _truncate(norm), idx + 1, page))
        stats["R1"] = len(removed_idx) - r1_before

        # ---- R2: 跨页重复短文本(水印/漏判页眉页脚兜底) ----
        # 版面模型把页眉页脚误判为 text 时 R1 抓不到,R2 不看 label 只看跨页分布
        repeats = _collect_cross_page_repeats(json_content, cfg)
        r2_map = {
            text: ("R2", "跨页重复文本(疑似水印/页眉页脚)", page, None)
            for text, page in repeats.items()
            if text not in r1_map  # R1 已处理的不重复统计
        }
        r2_before = len(removed_idx)
        hit_keys2: set[str] = set()
        _remove_whole_lines(lines, r2_map, removed_idx, hit_keys2)
        for idx in sorted(removed_idx):
            norm = _normalize(lines[idx])
            if norm in hit_keys2:
                _, name, page, _ = r2_map[norm]
                removals.append(RemovalRecord("R2", name, _truncate(norm), idx + 1, page))
        stats["R2"] = len(removed_idx) - r2_before
    else:
        stats["R1"] = 0
        stats["R2"] = 0

    # ---- R3: 孤立页码行 ----
    stats["R3"] = 0
    if cfg.page_number:
        for idx, line in enumerate(lines):
            if idx in removed_idx:
                continue
            stripped = line.strip()
            if not stripped or not any(p.match(stripped) for p in _PAGE_NUMBER_PATTERNS):
                continue
            # 孤立性检查:前后均为空行/已删行/文档边界
            prev_ok = idx == 0 or not lines[idx - 1].strip() or (idx - 1) in removed_idx
            next_ok = idx == total_lines - 1 or not lines[idx + 1].strip() or (idx + 1) in removed_idx
            if prev_ok and next_ok:
                removed_idx.add(idx)
                removals.append(RemovalRecord("R3", "孤立页码行", _truncate(stripped), idx + 1, None))
                stats["R3"] += 1

    # ---- R4: 空 image 占位符(其后无描述文本时删除) ----
    # docling 的图片描述格式固定为:占位符 + 1 个空行 + 描述段落。
    # 因此仅当 1 个空行内出现非标题文本时才视为有描述;隔着更多空行的
    # 正文不算描述,占位符删除。
    stats["R4"] = 0
    if cfg.empty_image:
        for idx, line in enumerate(lines):
            if idx in removed_idx or line.strip() != _IMAGE_PLACEHOLDER:
                continue
            has_desc = False
            for nxt in (idx + 1, idx + 2):
                if nxt >= total_lines:
                    break
                if nxt in removed_idx or not lines[nxt].strip():
                    continue
                stripped_nxt = lines[nxt].strip()
                has_desc = stripped_nxt != _IMAGE_PLACEHOLDER and not stripped_nxt.startswith("#")
                break
            if not has_desc:
                removed_idx.add(idx)
                removals.append(RemovalRecord("R4", "空图片占位符", _IMAGE_PLACEHOLDER, idx + 1, None))
                stats["R4"] += 1

    # ---- 安全刹车:删除占比超限则整单放弃 ----
    if total_lines > 0 and len(removed_idx) / total_lines > cfg.max_delete_ratio:
        stats["aborted"] = 1
        return DenoiseResult(markdown=markdown, removals=removals, stats=stats, aborted=True)

    kept = [line for idx, line in enumerate(lines) if idx not in removed_idx]

    # ---- R5: 连续空行压缩(3+ 压为 2) ----
    compressed: list[str] = []
    blank_run = 0
    r5_removed = 0
    for line in kept:
        if line.strip():
            blank_run = 0
            compressed.append(line)
        else:
            blank_run += 1
            if blank_run <= 2:
                compressed.append("")
            else:
                r5_removed += 1
    stats["R5"] = r5_removed

    return DenoiseResult(markdown="\n".join(compressed), removals=removals, stats=stats)


def render_audit_json(result: DenoiseResult) -> str:
    """机器可读审计:每条删除的 rule/页码/行号/文本 + 统计 + 刹车标志。"""
    return json.dumps(
        {
            "aborted": result.aborted,
            "stats": result.stats,
            "removals": [asdict(r) for r in result.removals],
        },
        ensure_ascii=False,
        indent=2,
    )


def render_audit_md(result: DenoiseResult, doc_name: str) -> str:
    """精简审计摘要(Markdown 表格),写入对话状态供模型/子代理自查。"""
    lines = [
        f"# {doc_name} 降噪审计",
        "",
    ]
    if result.aborted:
        lines.append("**删除量超过安全阈值,已保留原文(降噪未生效)。**")
        lines.append("")
    total = len(result.removals)
    lines.append(f"共删除 {total} 处。统计: " + ", ".join(f"{k}={v}" for k, v in result.stats.items()))
    lines.append("")
    if result.removals:
        lines.append("| 规则 | 页码 | 行号 | 删除文本 |")
        lines.append("|---|---|---|---|")
        for r in result.removals:
            text = r.text.replace("|", "\\|")
            lines.append(f"| {r.rule_id} {r.rule_name} | {r.page_no or '-'} | {r.line_no or '-'} | {text} |")
    return "\n".join(lines) + "\n"
