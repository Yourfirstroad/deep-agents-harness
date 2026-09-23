"""denoise_cli: 给前端(Next.js /api/parse-document)spawn 调用的降噪命令行入口。

用法:
    echo '{"markdown": "...", "json_content": null, "params": {"max_delete_ratio": 0.3}}' \
        | python tools/denoise_cli.py

约定:
- stdin: 一个 JSON 对象。markdown 必填;json_content 可空(空则 R1/R2 跳过);
  params 可空,键为降噪参数名(repeat_min_pages / repeat_maxlen / page_number /
  empty_image / max_delete_ratio),按次覆盖 .env 默认值。
- stdout: 一个 JSON 对象:{cleaned_markdown, aborted, stats, removals[]}
  (结构与 tools/denoise.py 的 render_audit_json 一致)。
- 出错时 stdout 输出 {"error": "..."} 且退出码非 0。

可从任意目录调用(自动把项目根加入 sys.path)。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.denoise import denoise_markdown, render_audit_md  # noqa: E402


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"stdin 不是合法 JSON: {e}"}, ensure_ascii=False))
        return 2

    markdown = payload.get("markdown")
    if not isinstance(markdown, str):
        print(json.dumps({"error": "缺少 markdown 字段(应为字符串)"}, ensure_ascii=False))
        return 2

    params = payload.get("params")
    if params is not None and not isinstance(params, dict):
        print(json.dumps({"error": "params 应为对象"}, ensure_ascii=False))
        return 2

    try:
        result = denoise_markdown(
            markdown,
            payload.get("json_content"),
            overrides=params,
        )
    except Exception as e:  # noqa: BLE001 - CLI 边界,把异常变成结构化输出
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1

    doc_name = payload.get("doc_name") or "文档"
    print(json.dumps({
        "cleaned_markdown": result.markdown,
        "aborted": result.aborted,
        "stats": result.stats,
        "removals": [asdict(r) for r in result.removals],
        # 人读审计报告(Markdown 表格),前端落盘为可下载交付物
        "audit_markdown": render_audit_md(result, doc_name),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
