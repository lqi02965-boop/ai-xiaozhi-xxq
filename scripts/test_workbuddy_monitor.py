# -*- coding: utf-8 -*-
"""test_workbuddy_monitor.py —— workbuddy 双源监视回归

源1 tasks 状态文件：in_progress→completed 报 done；首见基线不报；改回 in_progress 不报。
源2 audit-log：rejected 报 error。
多源单 agent：一个 workbuddy 监视器同时挂两个源。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pc_daemon.agent_monitor import AgentMonitor  # noqa: E402
from pc_daemon.events import EventBus  # noqa: E402

ACFG = {
    "name": "workbuddy",
    "sources": [
        {"log_dir": ".", "log_pattern": "audit-*.jsonl",
         "event_field": "eventType", "detail_field": "commandPreview",
         "event_match": {"error": ["command-safety.rejected",
                                   "command-safety.system-tool-blocked"]}},
        {"log_dir": ".", "log_pattern": "tasks/**/*.json", "watch": "state",
         "event_field": "status", "event_match": {"done": ["completed"]},
         "detail_field": "subject"},
    ],
}


def main() -> None:
    bus = EventBus()
    m = AgentMonitor("workbuddy", ACFG, bus, cooldown_sec=0)
    root = Path(tempfile.mkdtemp())
    (root / "tasks").mkdir()
    m.dir = root
    for s in m.sources:
        s["dir"] = root

    def drain():
        out = []
        while True:
            try:
                out.append(bus._q.get_nowait())
            except Exception:
                return out

    audit = root / "audit-2026-10-05.jsonl"
    task = root / "tasks" / "1.json"

    def write_task(status, subject="阶段1 · GPIO"):
        task.write_text(json.dumps({"subject": subject, "status": status,
                                    "id": "1"}, ensure_ascii=False),
                        encoding="utf-8")

    # ① 空目录基线
    m._poll_once()
    assert drain() == []

    # ② 首见状态文件（in_progress）：基线不报
    write_task("in_progress")
    audit.write_text("", encoding="utf-8")
    m._poll_once()
    assert drain() == [], "② 首见状态文件却报了"

    # ③ in_progress → completed：报 1 个 done，detail=subject
    write_task("completed")
    m._poll_once()
    r3 = drain()
    assert len(r3) == 1 and r3[0].kind == "done" and r3[0].agent == "workbuddy", \
        f"③ 应报 1 个 done，实际 {[(e.kind, e.agent) for e in r3]}"
    assert "阶段1" in r3[0].detail, f"③ detail 应含任务名，实际 {r3[0].detail!r}"

    # ④ completed 改回 in_progress（重开任务）：不报
    write_task("in_progress")
    m._poll_once()
    assert drain() == [], "④ 重开任务不该报"

    # ⑤ audit-log 溂 rejected：报 error
    audit.write_text('{"eventType":"command-safety.rejected",'
                     '"commandPreview":"rm -rf /"}\n', encoding="utf-8")
    m._poll_once()
    r5 = drain()
    assert len(r5) == 1 and r5[0].kind == "error", f"⑤ 应报 1 个 error，实际 {r5}"

    # ⑥ task 文件跳到 pending（新任务）：不报
    (root / "tasks" / "2.json").write_text(
        json.dumps({"subject": "阶段2", "status": "pending", "id": "2"},
                   ensure_ascii=False), encoding="utf-8")
    m._poll_once()
    assert drain() == [], "⑥ 新 pending 任务不该报"

    print("workbuddy 双源监视回归 6/6 通过 ✅")


if __name__ == "__main__":
    main()
