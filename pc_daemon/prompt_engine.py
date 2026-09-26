"""播报词生成：DeepSeek 优先，本地词库兜底（V1-205）。"""
from __future__ import annotations

import logging
import os
import random

from .events import AgentEvent

log = logging.getLogger("prompt_engine")

SYSTEM_PROMPT = (
    "你是桌面机器人小智的播报词生成器。"
    "根据 Agent 工作事件，生成一句不超过20字的中文口语播报，"
    "语气可爱、有情绪、不要引号和标点结尾。只输出这一句话。"
)

LOCAL_PHRASES: dict[str, list[str]] = {
    "done": [
        "代码写完啦，快来看看！",
        "任务搞定，快去验收吧！",
        "搞定啦，夸夸我！",
        "活儿干完了，休息一下眼睛吧！",
    ],
    "error": [
        "哎呀，出错了，快看看日志！",
        "报错啦报错啦，需要你救场！",
        "呜呜，任务翻车了……",
    ],
}


class PromptEngine:
    def __init__(self, cfg: dict) -> None:
        self.base_url = cfg.get("base_url", "https://api.deepseek.com")
        self.model = cfg.get("model", "deepseek-chat")
        self.timeout = cfg.get("timeout_sec", 6)
        self.max_chars = cfg.get("max_chars", 20)
        self.api_key = os.environ.get(cfg.get("api_key_env", "DEEPSEEK_API_KEY"), "")

    def gen(self, event: AgentEvent) -> str:
        if self.api_key:
            text = self._gen_remote(event)
            if text:
                return text
            log.warning("DeepSeek 生成失败，用本地词库兜底")
        else:
            log.info("未配置 API Key，使用本地词库")
        return self._gen_local(event)

    def _gen_remote(self, event: AgentEvent) -> str:
        try:
            import requests

            user = f"Agent={event.agent}，事件={event.kind}，详情={event.detail}"
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 50,
                    "temperature": 1.0,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip().strip('"“”')
            return self._clamp(text)
        except Exception:
            log.exception("DeepSeek 调用异常")
            return ""

    def _gen_local(self, event: AgentEvent) -> str:
        return random.choice(LOCAL_PHRASES.get(event.kind, ["我有新消息！"]))

    def _clamp(self, text: str) -> str:
        text = text.replace("\n", " ").strip()
        return text[: self.max_chars] if text else ""
