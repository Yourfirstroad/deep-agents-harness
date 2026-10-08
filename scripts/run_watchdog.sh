#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# macOS / Linux 启动器:run_watchdog.py
#   scripts/run_watchdog.sh           前台运行
#   scripts/run_watchdog.sh --detach  后台运行
#   scripts/run_watchdog.sh --stop    停止后台实例
#   scripts/run_watchdog.sh --status  查看后台状态
# 需要先 chmod +x scripts/run_watchdog.sh 才能直接 ./ 执行。
# ---------------------------------------------------------------------------
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
python "$SCRIPT_DIR/run_watchdog.py" "$@"
