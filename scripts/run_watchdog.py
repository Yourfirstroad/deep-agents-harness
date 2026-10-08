#!/usr/bin/env python3
"""运行看门狗:自动清理 langgraph dev 本地队列里的僵尸运行。

背景:langgraph_runtime_inmem 单 worker + enqueue 策略下,被杀/断流/重启
留下的 "running" 状态僵尸会堵死后续所有运行。本脚本每 60 秒巡检一次:
- 找出所有处于 running/pending 超过 STUCK_MINUTES 分钟、且消息数无增长的运行;
- 调用 cancel API 将其终止,释放队列。

判定规则(用户已确认):某运行的消息数连续 30 分钟零增长才清理;
正常推进中的运行(designer 分模块写入、lint、评审都会推进消息)不会误伤。

用法(跨平台,与 langgraph dev 并行运行):

    前台(实时打印到终端,Ctrl+C 停止):
            python scripts/run_watchdog.py
            # 或 scripts/run_watchdog.sh            (macOS / Linux)
            # 或 scripts\run_watchdog.bat           (Windows,也支持双击)

    后台(自动 fork/脱离,日志落到系统临时目录的 watchdog.log):
            python scripts/run_watchdog.py --detach
            # 或 scripts/run_watchdog.sh --detach
            # 或 scripts\run_watchdog.bat --detach

    停止 / 查看后台实例:
            python scripts/run_watchdog.py --stop
            python scripts/run_watchdog.py --status

    自定义 LangGraph API / 日志 / PID 路径:
            python scripts/run_watchdog.py --detach \\
                --api http://127.0.0.1:2024 \\
                --log-file /path/to/watchdog.log \\
                --pid-file /path/to/watchdog.pid
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:2024"
STUCK_MINUTES = 30  # 消息数连续不增长超过该时长 → 判僵尸(用户确认的阈值)
POLL_SECONDS = 60
THREAD_LIMIT = 20


# ---------------------------------------------------------------------------
# 跨平台路径与进程工具
# ---------------------------------------------------------------------------
def _default_paths() -> tuple[Path, Path]:
    """默认日志/PID 路径:落到系统临时目录,macOS/Linux 是 /tmp,Windows 是 %TEMP%。"""
    tmp = Path(tempfile.gettempdir())
    return (
        tmp / "deep-agents-harness-watchdog.log",
        tmp / "deep-agents-harness-watchdog.pid",
    )


def _read_pid(pid_path: Path) -> int | None:
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """跨平台判断进程是否还活着。"""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _attach_logging(log_path: Path) -> None:
    """把 stdout/stderr 重定向到日志文件(只在 --detach 子进程里调用)。

    用 sys.stdout = log_f 而非 os.dup2,是因为 Windows 下 DETACHED_PROCESS
    派生出的子进程可能没有合法的 fd 1/2,但 Python 级 sys.stdout 不受影响。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8", buffering=1)  # 行缓冲
    sys.stdout = log_f
    sys.stderr = log_f


def _spawn_detached(args: list[str], pid_path: Path) -> int:
    """跨平台后台启动子进程,返回子进程 PID,并把 PID 写入 pid_path。"""
    if sys.platform == "win32":
        # DETACHED_PROCESS(0x08) + CREATE_NEW_PROCESS_GROUP(0x200)
        flags = 0x08 | 0x200
        proc = subprocess.Popen(
            args,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=False,
        )
    else:
        proc = subprocess.Popen(
            args,
            start_new_session=True,  # POSIX setsid,脱开控制终端
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
    return proc.pid


# ---------------------------------------------------------------------------
# LangGraph API 调用 + 巡检
# ---------------------------------------------------------------------------
def _get(path: str):
    return json.load(urllib.request.urlopen(f"{API}{path}", timeout=15))


def _post(path: str, payload: dict):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=15))


def _msg_count(thread_id: str) -> int:
    try:
        state = _get(f"/threads/{thread_id}/state")
        return len((state.get("values") or {}).get("messages", []))
    except Exception:
        return -1


def scan_and_clean(
    tracker: dict[str, tuple[int, float]],
) -> dict[str, tuple[int, float]]:
    """一轮巡检。tracker: run_id -> (上次消息数, 最后一次有增长的时间戳)。"""
    now = time.time()
    try:
        threads = _post("/threads/search", {"limit": THREAD_LIMIT})
    except Exception as e:
        print(f"[watchdog] 服务不可达: {e}", flush=True)
        return tracker

    active_ids: set[str] = set()
    for t in threads:
        tid = t["thread_id"]
        try:
            runs = _get(f"/threads/{tid}/runs?limit=5")
        except Exception:
            continue
        for r in runs:
            if r.get("status") not in ("running", "pending"):
                continue
            rid = r["run_id"]
            active_ids.add(rid)
            count = _msg_count(tid)
            if rid not in tracker:
                tracker[rid] = (count, now)
                continue
            prev_count, last_change = tracker[rid]
            if count != prev_count:
                tracker[rid] = (count, now)  # 有推进,重置计时
                continue
            stagnant_min = (now - last_change) / 60
            if stagnant_min >= STUCK_MINUTES:
                print(
                    f"[watchdog] 清理僵尸运行 {rid[:8]}(线程 {tid[:8]},"
                    f"消息数 {count} 已 {stagnant_min:.0f} 分钟零增长)",
                    flush=True,
                )
                try:
                    _post(f"/threads/{tid}/runs/{rid}/cancel", {"action": "interrupt"})
                    tracker[rid] = (count, now)  # 避免每分钟重复取消
                except Exception as e:
                    print(f"[watchdog] 取消失败 {rid[:8]}: {e}", flush=True)

    # 清理已结束运行的跟踪记录
    for rid in [k for k in tracker if k not in active_ids]:
        del tracker[rid]
    return tracker


# ---------------------------------------------------------------------------
# 命令分派
# ---------------------------------------------------------------------------
def cmd_stop(pid_path: Path) -> int:
    pid = _read_pid(pid_path)
    if pid is None:
        print(f"[watchdog] 未找到 PID 文件: {pid_path}(可能未启动或已手动清理)")
        return 1
    if not _pid_alive(pid):
        print(f"[watchdog] 进程 {pid} 已不在运行,清理残留 PID 文件")
        try:
            pid_path.unlink()
        except OSError:
            pass
        return 0
    if sys.platform == "win32":
        rc = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        if rc != 0:
            print(f"[watchdog] taskkill 退出码 {rc},进程可能已被外部终止")
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            print(f"[watchdog] 发送 SIGTERM 失败: {e}", flush=True)
            return 2
    print(f"[watchdog] 已停止后台进程 {pid}")
    return 0


def cmd_status(pid_path: Path) -> int:
    pid = _read_pid(pid_path)
    if pid is None:
        print("[watchdog] 未运行(PID 文件不存在)")
        return 1
    if _pid_alive(pid):
        print(f"[watchdog] 运行中 PID={pid}(PID 文件: {pid_path})")
        return 0
    print(f"[watchdog] 进程 {pid} 已退出,但 PID 文件残留,可手动删除 {pid_path}")
    return 1


def cmd_detach(args: argparse.Namespace) -> int:
    log_path = Path(args.log_file)
    pid_path = Path(args.pid_file)
    existing_pid = _read_pid(pid_path)
    if existing_pid is not None and _pid_alive(existing_pid):
        print(f"[watchdog] 已在运行(PID={existing_pid}),无需重复启动")
        return 0

    script = Path(__file__).resolve()
    child_args = [
        sys.executable, str(script),
        "--detach-child",
        "--log-file", str(log_path),
        "--pid-file", str(pid_path),
    ]
    if args.api and args.api != API:
        child_args += ["--api", args.api]

    pid = _spawn_detached(child_args, pid_path)
    print(f"[watchdog] 已后台启动 PID={pid}")
    print(f"[watchdog]   日志: {log_path}")
    print(f"[watchdog]   PID : {pid_path}")
    print(f"[watchdog]   停止: python {script} --stop")
    print(f"[watchdog]   状态: python {script} --status")
    return 0


def cmd_run(args: argparse.Namespace) -> None:
    if args.detach_child:
        # 我们是被 --detach 派生的子进程:把 stdout/stderr 重定向到日志
        _attach_logging(Path(args.log_file))
    pid_path = Path(args.pid_file)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    # 注册 SIGTERM 处理器:保证 finally(清理 PID 文件)确定性执行,
    # 否则 Python 默认 SIGTERM 走 C 级 SIG_DFL,finally 可能不跑。
    def _on_sigterm(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)

    print(
        f"[watchdog] 启动 PID={os.getpid()},每 {POLL_SECONDS}s 巡检,"
        f"消息数 {STUCK_MINUTES} 分钟零增长判定为僵尸",
        flush=True,
    )
    try:
        tracker: dict[str, tuple[int, float]] = {}
        while True:
            tracker = scan_and_clean(tracker)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n[watchdog] 收到 Ctrl+C,退出", flush=True)
    finally:
        # 退出时清理自己的 PID 文件(仅当里面还是我们的 PID 时)
        try:
            current = _read_pid(pid_path)
            if current == os.getpid():
                pid_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    default_log, default_pid = _default_paths()
    parser = argparse.ArgumentParser(
        description="langgraph dev 队列僵尸运行看门狗(跨平台)",
    )
    parser.add_argument("--api", default=API, help="LangGraph API 地址")
    parser.add_argument(
        "--log-file",
        default=str(default_log),
        help=f"日志文件路径(默认: {default_log})",
    )
    parser.add_argument(
        "--pid-file",
        default=str(default_pid),
        help=f"PID 文件路径(默认: {default_pid})",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="后台启动,跨平台 fork/脱离",
    )
    parser.add_argument(
        "--detach-child",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--stop", action="store_true", help="停止后台实例")
    parser.add_argument("--status", action="store_true", help="查看后台实例状态")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # --detach-child 必须最先处理,避免被外层 --detach 误判递归
    if args.detach_child:
        cmd_run(args)
        return 0
    if args.stop:
        return cmd_stop(Path(args.pid_file))
    if args.status:
        return cmd_status(Path(args.pid_file))
    if args.detach:
        return cmd_detach(args)
    cmd_run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())