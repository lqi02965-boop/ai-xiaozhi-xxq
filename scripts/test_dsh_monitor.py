# -*- coding: utf-8 -*-
"""test_dsh_monitor.py —— DSH zstd 监视回归

语义：zstd 文件首见只记基线（不回放历史，防启动误报）；之后每次整包重写，
只把解压后新增的行走事件规则；aborted/interrupted 不报，completed→done、error→报错。
"""
import sys
import tempfile
from pathlib import Path

import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pc_daemon.agent_monitor import AgentMonitor  # noqa: E402
from pc_daemon.events import EventBus  # noqa: E402

ACFG = {
    "log_dir": ".", "log_pattern": "*.jsonl.zstd",
    "event_field": "type",
    "event_match": {"done": ["turn/end"], "error": ["turn/end"]},
    "data_match": {"done": {"data.reason.kind": ["completed"]},
                   "error": {"data.reason.kind": ["error"]}},
    "detail_field": "data",
}


def main() -> None:
    bus = EventBus()
    root = Path(tempfile.mkdtemp())
    cfg = dict(ACFG, log_dir=str(root))
    m = AgentMonitor("dsh", cfg, bus, cooldown_sec=0)   # 单测关冷却
    zf = root / "session.v4.jsonl.zstd"

    def write(lines):
        zf.write_bytes(zstandard.ZstdCompressor().compress(
            "\n".join(lines).encode("utf-8")))

    def drain():
        out = []
        while True:
            try:
                out.append(bus._q.get_nowait())
            except Exception:
                return out

    # ① 首见已有文件：只记基线，不回放历史（防启动误报）
    write(['{"type":"turn/start"}',
           '{"type":"turn/end","data":{"turn":1,"reason":{"kind":"completed"}}}',
           '{"type":"tool/call"}'])
    m._poll_once()
    assert drain() == [], "① 首见已有文件却回放了历史"

    # ② 整包重写（DSH 每次保存）：新增 completed → 报 1 个 done
    write(['{"type":"turn/start"}',
           '{"type":"turn/end","data":{"turn":1,"reason":{"kind":"completed"}}}',
           '{"type":"tool/call"}',
           '{"type":"turn/end","data":{"turn":2,"reason":{"kind":"completed"}}}'])
    m._poll_once()
    r2 = [(e.kind, e.agent) for e in drain()]
    assert r2 == [("done", "dsh")], f"② 应报 1 个 done，实际 {r2}"

    # ③ 原样再轮询：无变化不报
    m._poll_once()
    assert drain() == [], "③ 无变化却报了事件"

    # ④ 新增 completed + aborted + error（保留历史行——DSH 文件只增不减）：报 done+error
    write(['{"type":"turn/start"}',
           '{"type":"turn/end","data":{"turn":1,"reason":{"kind":"completed"}}}',
           '{"type":"tool/call"}',
           '{"type":"turn/end","data":{"turn":2,"reason":{"kind":"completed"}}}',
           '{"type":"turn/end","data":{"turn":3,"reason":{"kind":"completed"}}}',
           '{"type":"turn/end","data":{"turn":4,"reason":{"kind":"aborted"}}}',
           '{"type":"turn/end","data":{"turn":5,"reason":{"kind":"error"}}}'])
    m._poll_once()
    r4 = [(e.kind, e.agent) for e in drain()]
    assert ("done", "dsh") in r4 and ("error", "dsh") in r4 and len(r4) == 2, \
        f"④ 应报 done+error（aborted 不报），实际 {r4}"

    # ⑤ 文件行数变少（重开）：不炸，重读后正常再报
    write(['{"type":"turn/end","data":{"turn":1,"reason":{"kind":"completed"}}}'])
    m._poll_once()
    m._poll_once()
    r5 = [(e.kind, e.agent) for e in drain()]
    assert r5 == [("done", "dsh")], f"⑤ 重开应重读并再报 1 个 done，实际 {r5}"

    print("DSH zstd 监视回归 5/5 通过 ✅")


if __name__ == "__main__":
    main()
