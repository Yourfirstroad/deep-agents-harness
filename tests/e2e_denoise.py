#!/usr/bin/env python3
"""文档降噪端到端验证:用真实 PDF 跑 parse_and_clean 完整链路。

用法:
    python tests/e2e_denoise.py [pdf路径]   # 默认 docling-demo/test.pdf

需要本地 docling-serve 可达(DOCLING_SERVE_URL);不可达时打印 SKIP 并退出 0。
断言:清洗后行数 <= 原始行数;审计中每条被删文本确实不在清洗结果中。
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from tools.docling_tool import parse_and_clean  # noqa: E402
from tools.denoise import render_audit_json  # noqa: E402


def _server_reachable() -> bool:
    url = urlparse(os.getenv("DOCLING_SERVE_URL", "http://localhost:5001"))
    try:
        with socket.create_connection((url.hostname, url.port or 80), timeout=2):
            return True
    except OSError:
        return False


def main() -> int:
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "docling-demo" / "test.pdf"
    if not pdf.is_file():
        print(f"ERROR: 文件不存在: {pdf}")
        return 1
    if not _server_reachable():
        print(f"SKIP: docling-serve 不可达({os.getenv('DOCLING_SERVE_URL', 'http://localhost:5001')})")
        return 0

    print(f"解析并降噪: {pdf} (多模态图片描述耗时较长,请耐心等待)")
    parsed = parse_and_clean(pdf)

    raw_lines = parsed.raw_markdown.split("\n")
    clean_lines = parsed.cleaned_markdown.split("\n")
    print(f"\n原始 {len(raw_lines)} 行 -> 清洗后 {len(clean_lines)} 行")

    assert len(clean_lines) <= len(raw_lines), "清洗后行数不应多于原始行数"

    if parsed.denoise is None:
        print("降噪未开启(DOCLING_DENOISE=false),内容应与原始一致")
        assert parsed.cleaned_markdown == parsed.raw_markdown
        return 0

    d = parsed.denoise
    print(f"stats: {d.stats}, aborted: {d.aborted}, removals: {len(d.removals)}")

    if not d.aborted:
        # 审计中每条整行删除的文本,不应再作为独立行出现在清洗结果中
        clean_norm = {" ".join(line.split()) for line in clean_lines}
        leaked = [
            r for r in d.removals
            if r.rule_id in ("R1", "R2") and " ".join(r.text.rstrip("…").split()) in clean_norm
        ]
        assert not leaked, f"被删文本仍出现在清洗结果中: {leaked[:3]}"

    print("\n--- 审计(前 20 条) ---")
    audit = json.loads(render_audit_json(d))
    for r in audit["removals"][:20]:
        print(f"  [{r['rule_id']}] p{r['page_no'] or '-'} L{r['line_no'] or '-'}: {r['text']}")
    if len(audit["removals"]) > 20:
        print(f"  ... 共 {len(audit['removals'])} 条")

    print("\nOK: 端到端断言通过,请人工核对上面的删除清单是否合理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
