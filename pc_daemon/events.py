"""事件定义与事件总线。"""
from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field


class _Skip:
    """超时哨兵：区分"没事件"和"总线关闭"。"""

    def __repr__(self) -> str:  # pragma: no cover
        return "<SKIP>"


SKIP = _Skip()


@dataclass
class AgentEvent:
    """一个 Agent 状态变化事件。"""

    kind: str            # "done" | "error"
    agent: str           # "zcode" | "workbuddy" | ...
    session_id: str = ""
    detail: str = ""     # 原始日志摘要，供日志排查
    ts: float = field(default_factory=time.time)

    @property
    def cooldown_key(self) -> tuple[str, str, str]:
        return (self.agent, self.session_id, self.kind)


class EventBus:
    """单生产者多消费者的简易事件队列（v1 单 worker 足够）。"""

    def __init__(self) -> None:
        self._q: "queue.Queue[AgentEvent | None]" = queue.Queue()

    def put(self, event: AgentEvent) -> None:
        self._q.put(event)

    def close(self) -> None:
        self._q.put(None)

    def get(self, timeout: float = 1.0) -> AgentEvent | None:
        """取事件；None 表示总线已关闭，超时返回 SKIP 哨兵。"""
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return SKIP
        return item


