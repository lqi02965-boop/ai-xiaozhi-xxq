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


class AgentMonitor(threading.Thread):
    """扫描日志目录，把新事件解析后投递到 EventBus（带冷却去抖）。"""

    def __init__(self, agent: str, log_dir: str, pattern: str,
                 bus: EventBus, poll_interval: float = 2.0,
                 cooldown_sec: float = 30.0) -> None:
        super().__init__(daemon=True, name=f"monitor-{agent}")
        self.agent = agent
        self.dir = Path(log_dir).expanduser()
        self.pattern = pattern
        self.bus = bus
        self.poll_interval = poll_interval
        self.cooldown_sec = cooldown_sec
        self._cursors: dict[Path, _FileCursor] = {}
        self._last_emit: dict[tuple[str, str, str], float] = {}
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    # ---- 事件规则 -------------------------------------------------------
    def _classify(self, rec: dict) -> tuple[str, str] | None:
        """返回 (kind, detail)；不关心的事件返回 None。"""
        ev = rec.get("event", "")
        if ev == "model.request.completed":
            return "done", f"{rec.get('sessionId', '')[:13]} 一轮响应完成"
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
        self.bus.put(AgentEvent(kind=kind, agent=self.agent,
                                session_id=session_id, detail=detail))
        log.info("事件: [%s/%s] %s %s", self.agent, kind, session_id[:13], detail)

    # ---- 主循环 ---------------------------------------------------------
    def run(self) -> None:
        log.info("监听启动: %s/%s", self.dir, self.pattern)
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:  # 监听器绝不许死
                log.exception("监听轮询异常，忽略本轮")
            self._stop.wait(self.poll_interval)
        log.info("监听退出: %s", self.agent)

    def _poll_once(self) -> None:
        files = {Path(p) for p in glob.glob(str(self.dir / self.pattern))}
        # 只保留普通文件，且清理已消失文件
        files = {p for p in files if p.is_file()}
        for gone in set(self._cursors) - files:
            self._cursors.pop(gone, None)
        for path in files:
            cur = self._cursors.setdefault(path, _FileCursor(path))
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
                kind_detail = self._classify(rec)
                if kind_detail:
                    kind, detail = kind_detail
                    self._emit(kind, str(rec.get("sessionId", "")), detail)
