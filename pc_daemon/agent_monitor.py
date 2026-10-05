"""Agent 日志监听器：尾读 zcode CLI 的 jsonl 日志，产出 AgentEvent。

数据源（V1-201 调研结论）：
  ~/.zcode/cli/log/zcode-YYYY-MM-DD.jsonl
每行一个 JSON，关注两类：
  {"event": "model.request.completed", "sessionId": "...", ...}  → Agent 完成一轮响应
  {"level": "error", "message": "...", ...}                      → 运行报错

实现用轮询 tail（比 watchdog 更耐 Windows 文件追加/翻滚），依赖只有标准库。
"""
from __future__ import annotations

import glob
import json
import logging
import os
import threading
import time
from pathlib import Path

from .events import AgentEvent, EventBus

log = logging.getLogger("agent_monitor")


class _FileCursor:
    """单个 jsonl 文件的尾读游标。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = path.stat().st_size if path.exists() else 0

    def read_new_lines(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            self.offset = 0
            return []
        if size < self.offset:  # 文件被截断/重建
            self.offset = 0
        if size == self.offset:
            return []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.offset)
            data = f.read()
            self.offset = f.tell()
        return data.splitlines()


class _ZstdCursor:
    """zstd 压缩日志的游标（DSH session.v4.jsonl.zstd）。

    zstd 不支持追加，DSH 每次保存都是整文件重写 → 用「解压后行数」当游标：
    mtime/size 变了就整包解压，只取新增的行。文件行数变少视为重开，从头再来
    （重复行有冷却兜底，不会连报）。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sig = None          # (mtime_ns, size)
        self.lines_seen = 0

    def read_new_lines(self) -> list[str]:
        try:
            st = self.path.stat()
        except OSError:
            return []
        sig = (st.st_mtime_ns, st.st_size)
        if self.sig is None:
            # 首见：只记基线不回放历史（与 _FileCursor 的 offset=size 同语义），
            # 否则守护进程每次启动都会把所有会话的历史事件播报一遍
            self.sig = sig
            try:
                import zstandard

                with open(self.path, "rb") as f:
                    raw = zstandard.ZstdDecompressor().stream_reader(f).read()
                self.lines_seen = len(raw.decode("utf-8", "replace").splitlines())
            except Exception:
                log.exception("zstd 基线读取失败: %s", self.path)
                self.lines_seen = 0
            return []
        if sig == self.sig:
            return []
        self.sig = sig
        try:
            import zstandard

            with open(self.path, "rb") as f:
                raw = zstandard.ZstdDecompressor().stream_reader(f).read()
            lines = raw.decode("utf-8", "replace").splitlines()
        except Exception:
            log.exception("zstd 解压失败: %s", self.path)
            return []
        if len(lines) < self.lines_seen:    # 文件被重写成更短的版本 → 从头读
            self.lines_seen = 0
        new = lines[self.lines_seen:]
        self.lines_seen = len(lines)
        return new


class _StateCursor:
    """状态文件游标（watch=state）：整个文件是一份 JSON 状态快照
    （如 workbuddy 的 tasks/N.json，status 字段标记任务阶段）。

    mtime/size 变化 → 整文件重读、作为一条记录交规则分类（event_field 指
    status 之类字段）。首见只记基线不报（与 _ZstdCursor 同语义，防启动误报）。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sig = None          # (mtime_ns, size)

    def read_new_lines(self) -> list[str]:
        try:
            st = self.path.stat()
        except OSError:
            return []
        sig = (st.st_mtime_ns, st.st_size)
        if self.sig is None:
            self.sig = sig       # 基线
            return []
        if sig == self.sig:
            return []
        self.sig = sig
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return [text] if text.strip() else []


class ApprovalWatcher(threading.Thread):
    """审批/提问等待监控（轮询 ZCode 的 db.sqlite part 表）。

    原理（2026-09-27 活体实验确认）：AskUserQuestion/ExitPlanMode 等交互类工具
    在等待用户回应期间，part.state.status 停在 'running'；工具权限审批等待期
    则可能是 'pending'。出现即提醒，消失即复位；同一部件持续等待时按
    repeat_sec 间隔重复提醒。
    """

    INTERACTIVE_TOOLS = {"AskUserQuestion", "ExitPlanMode"}

    def __init__(self, cfg: dict, bus: EventBus) -> None:
        super().__init__(daemon=True, name="approval-watcher")
        self.db_path = os.path.expanduser(cfg.get("db_path", "~/.zcode/cli/db/db.sqlite"))
        self.poll_interval = cfg.get("poll_interval_sec", 2.0)
        self.repeat_sec = cfg.get("repeat_sec", 180)
        self.bus = bus
        self.enabled = True                 # GUI 可远程开关
        self._stop = threading.Event()
        self._last_part = ""          # 已提醒的 part 标识（tool+callID）
        self._last_emit = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        import sqlite3

        log.info("审批监控启动: %s", self.db_path)
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True,
                                  check_same_thread=False)
            con.row_factory = sqlite3.Row   # 不设这行，r["data"] 是元组会全部静默跳过
        except Exception:
            log.exception("审批监控打不开数据库，功能停用")
            return
        while not self._stop.is_set():
            try:
                self._poll_once(con)
            except Exception:
                log.exception("审批轮询异常，忽略本轮")
            self._stop.wait(self.poll_interval)
        con.close()
        log.info("审批监控退出")

    def _poll_once(self, con) -> None:
        if not self.enabled:
            return
        waiting = []   # (key, tool)
        # 用 json_extract 直接筛"运行中/待审批"的工具部件，不受 id 排序噪音影响
        cur = con.execute(
            "SELECT id, data FROM part "
            "WHERE json_extract(data,'$.type')='tool' "
            "AND json_extract(data,'$.state.status') IN ('pending','running') "
            "ORDER BY time_updated DESC LIMIT 20")
        for r in cur:
            try:
                d = json.loads(r["data"])
            except Exception:
                continue
            state = d.get("state") or {}
            st, tool = state.get("status"), d.get("tool", "")
            key = str(r["id"])
            if st == "pending" or (st == "running" and tool in self.INTERACTIVE_TOOLS):
                waiting.append((key, tool))
        now = time.time()
        if waiting:
            key, tool = waiting[0]
            if key != self._last_part or now - self._last_emit >= self.repeat_sec:
                self._last_part = key
                self._last_emit = now
                self.bus.put(AgentEvent(kind="approval", agent="zcode",
                                        session_id="", detail=f"{tool} 等待用户回应"))
        else:
            self._last_part = ""   # 已解除，允许下次重新提醒


class AgentMonitor(threading.Thread):
    """扫描日志目录，把新事件解析后投递到 EventBus（带冷却去抖）。

    支持两类规则（按 acfg 自动选择）：
    - zcode 型（默认）：event 字段名 + 内置规则（turn.completed→done、error→报错）
    - 数据驱动型：event_field 指定事件字段名，event_match 指定 {kind: [事件值子串]}，
      detail_field 指定详情来源——任意 agent 的 JSONL 日志都能接（如 workbuddy 审计日志）
    """

    def __init__(self, agent: str, acfg: dict,
                 bus: EventBus, poll_interval: float = 2.0,
                 cooldown_sec: float = 30.0) -> None:
        super().__init__(daemon=True, name=f"monitor-{agent}")
        self.agent = agent
        self.display = acfg.get("display", agent)    # 播报用名（默认同 key）
        self.acfg = dict(acfg)              # 本 agent 完整配置（扫描回显用）
        # 多数据源：sources 数组每项可覆盖 log_dir/pattern/事件规则；
        # 单数据源的旧格式（log_dir+log_pattern/pattern）自动降为单项。
        srcs = acfg.get("sources") or [{"log_dir": acfg["log_dir"]}]
        self.sources: list[dict] = []
        for s in srcs:
            merged = {**acfg, **s}
            self.sources.append({
                "dir": Path(merged["log_dir"]).expanduser(),
                "pattern": merged.get("log_pattern",
                                      merged.get("pattern", "*.jsonl")),
                "watch": merged.get("watch", ""),
                "event_field": merged.get("event_field", "event"),
                "event_match": merged.get("event_match") or {},
                "data_match": merged.get("data_match") or {},
                "detail_field": merged.get("detail_field", ""),
            })
        self.dir = self.sources[0]["dir"]            # 兼容旧引用
        self.pattern = self.sources[0]["pattern"]
        self.bus = bus
        self.poll_interval = poll_interval
        self.cooldown_sec = cooldown_sec
        # 每个源独立游标表——若共用一张表，A 源的"消失文件清理"会删掉 B 源的游标
        self._cursors: list[dict] = [dict() for _ in self.sources]
        self._last_emit: dict[tuple[str, str, str], float] = {}
        self._stop = threading.Event()
        self.enabled = not acfg.get("disabled", False)   # GUI 可远程开关

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _dig(rec: dict, path: str):
        """按点路径取嵌套字段（data.reason.kind → rec['data']['reason']['kind']）。"""
        cur = rec
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur

    # ---- 事件规则 -------------------------------------------------------
    def _classify(self, rec: dict, src: dict) -> tuple[str, str] | None:
        """返回 (kind, detail)；不关心的事件返回 None。

        数据驱动模式（workbuddy / dsh 等）：event_match 命中即候选，
        data_match 可选地对嵌套字段做值校验（如 turn/end 的 reason.kind）。
        规则字段按数据源读取（多源 agent 的每个源可各自指定）。
        zcode 型（默认规则）：turn.completed→done、error 级→报错；
        用户要求（2026-09-27）：只提醒「最终任务完成 / 报错」，轮询小任务不提醒。
        """
        ev = rec.get(src["event_field"], "")
        if src["event_match"]:
            for kind, patterns in src["event_match"].items():
                if not any(p in ev for p in patterns):
                    continue
                dm = src["data_match"].get(kind)
                if dm:
                    ok = all(str(self._dig(rec, path)) in (vals if isinstance(vals, list) else [vals])
                             for path, vals in dm.items())
                    if not ok:
                        continue    # 事件值命中但数据条件不符（如 aborted 的 turn/end）
                detail = str(rec.get(src["detail_field"], "") or ev)[:80]
                return kind, detail
            return None
        if ev == "turn.completed":
            return "done", f"{rec.get('sessionId', '')[:13]} 一轮任务完成"
        if ev in ("model.request.failed", "model.sdk.stream.failed"):
            return "error", str(rec.get("message", "模型请求失败"))[:80]
        if rec.get("level") == "error":
            return "error", str(rec.get("message", "未知错误"))[:80]
        return None

    def _emit(self, kind: str, session_id: str, detail: str) -> None:
        key = (self.agent, session_id, kind)
        now = time.time()
        last = self._last_emit.get(key, 0.0)
        if now - last < self.cooldown_sec:
            log.debug("冷却中，丢弃: %s %s", key, detail)
            return
        self._last_emit[key] = now
        self.bus.put(AgentEvent(kind=kind, agent=self.display,
                                session_id=session_id, detail=detail))
        log.info("事件: [%s/%s] %s %s", self.display, kind, session_id[:13], detail)

    # ---- 主循环 ---------------------------------------------------------
    def run(self) -> None:
        for src in self.sources:
            log.info("监听启动: %s/%s%s", src["dir"], src["pattern"],
                     "（状态文件）" if src["watch"] == "state" else "")
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:  # 监听器绝不许死
                log.exception("监听轮询异常，忽略本轮")
            self._stop.wait(self.poll_interval)
        log.info("监听退出: %s", self.agent)

    def _poll_once(self) -> None:
        if not self.enabled:
            return
        for idx, src in enumerate(self.sources):
            self._poll_source(idx, src)

    def _poll_source(self, idx: int, src: dict) -> None:
        cur_map = self._cursors[idx]
        recursive = "**" in src["pattern"]
        files = {Path(p) for p in glob.glob(str(src["dir"] / src["pattern"]),
                                            recursive=recursive)}
        # 只保留普通文件，且清理已消失文件（仅在本源自己的游标表里）
        files = {p for p in files if p.is_file()}
        for gone in set(cur_map) - files:
            cur_map.pop(gone, None)
        for path in files:
            if src["watch"] == "state":
                cls = _StateCursor
            elif path.suffix == ".zstd":
                cls = _ZstdCursor
            else:
                cls = _FileCursor
            cur = cur_map.setdefault(path, cls(path))
            for line in cur.read_new_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 半行/损坏行，跳过
                if not isinstance(rec, dict):
                    continue
                kind_detail = self._classify(rec, src)
                if kind_detail:
                    kind, detail = kind_detail
                    self._emit(kind, str(rec.get("sessionId", "")), detail)
