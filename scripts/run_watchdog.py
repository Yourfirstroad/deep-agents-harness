#!/usr/bin/env python3
"""运行看门狗:自动清理 langgraph dev 本地队列里的僵尸运行。

背景:langgraph_runtime_inmem 单 worker + enqueue 策略下,被杀/断流/重启
留下的 "running" 状态僵尸会堵死后续所有运行。本脚本每 60 秒巡检一次:
- 找出所有处于 running/pending 超过 STUCK_MINUTES 分钟、且消息数无增长的运行;
- 调用 cancel API 将其终止,释放队列。

用法(与 langgraph dev 并行运行):
    venv/bin/python scripts/run_watchdog.py            # 前台
    nohup venv/bin/python scripts/run_watchdog.py > /tmp/watchdog.log 2>&1 &  # 后台

判定规则(用户已确认):某运行的消息数连续 30 分钟零增长才清理;
正常推进中的运行(designer 分模块写入、lint、评审都会推进消息)不会误伤。
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

API = "http://127.0.0.1:2024"
STUCK_MINUTES = 30  # 消息数连续不增长超过该时长 → 判僵尸(用户确认的阈值)
POLL_SECONDS = 60
THREAD_LIMIT = 20


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


def main() -> None:
    print(
        f"[watchdog] 启动,每 {POLL_SECONDS}s 巡检,"
        f"消息数 {STUCK_MINUTES} 分钟零增长判定为僵尸",
        flush=True,
    )
    tracker: dict[str, tuple[int, float]] = {}
    while True:
        tracker = scan_and_clean(tracker)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
