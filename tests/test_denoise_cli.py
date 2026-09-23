"""denoise_cli 的单元测试:JSON 进、JSON 出,参数覆盖与刹车。"""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI = PROJECT_ROOT / "tools" / "denoise_cli.py"

# 样例:孤立页码行 + 空图片占位符(后随标题才算空,R4 的约定) + 正文
SAMPLE_MD = "# 需求文档\n\n正文第一段,包含业务规则。\n\n12\n\n正文第二段。\n\n<!-- image -->\n\n## 下一节\n\n正文第三段。\n"


def _run_cli(payload: dict) -> tuple[dict, int]:
    proc = subprocess.run(
        [sys.executable, str(CLI)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=PROJECT_ROOT,
    )
    return json.loads(proc.stdout), proc.returncode


def test_cli_basic_clean():
    out, code = _run_cli({"markdown": SAMPLE_MD})
    assert code == 0
    assert "cleaned_markdown" in out
    cleaned = out["cleaned_markdown"]
    assert "12" not in [ln.strip() for ln in cleaned.splitlines()]  # R3 页码删了
    assert "<!-- image -->" not in cleaned  # R4 空占位符删了
    assert "正文第一段" in cleaned and "正文第二段" in cleaned  # 正文保留
    assert out["aborted"] is False
    assert out["stats"]["R3"] == 1 and out["stats"]["R4"] == 1
    assert len(out["removals"]) == 2


def test_cli_params_override():
    # 关掉页码与空占位符规则后,同样的输入什么都不删
    out, code = _run_cli({
        "markdown": SAMPLE_MD,
        "params": {"page_number": False, "empty_image": False},
    })
    assert code == 0
    assert out["removals"] == []
    assert "12" in out["cleaned_markdown"]


def test_cli_safety_brake():
    # 删除占比上限调到接近 0,任何删除都触发刹车、保留原文
    out, code = _run_cli({
        "markdown": SAMPLE_MD,
        "params": {"max_delete_ratio": 0.001},
    })
    assert code == 0
    assert out["aborted"] is True
    assert out["cleaned_markdown"] == SAMPLE_MD


def test_cli_bad_input():
    proc = subprocess.run(
        [sys.executable, str(CLI)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=60,
        cwd=PROJECT_ROOT,
    )
    assert proc.returncode == 2
    assert "error" in json.loads(proc.stdout)
