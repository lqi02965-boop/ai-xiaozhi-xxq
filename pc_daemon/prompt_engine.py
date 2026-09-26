"""播报词生成：DeepSeek 优先，本地词库兜底（V1-205）。

人设从 config.json 的 deepseek.persona 读取——换性格只改配置，不动代码。
本地兜底词库也按 persona 分档：御姐模式用傲娇句式，默认模式用可爱句式。
"""
from __future__ import annotations

import logging
import os
import random

from .events import AgentEvent

log = logging.getLogger("prompt_engine")

SYSTEM_PROMPT_TEMPLATE = (
    "你是桌面机器人小智的播报词生成器。人设：{persona}。"
    "根据 Agent 工作事件，生成一句不超过20字的中文口语播报，"
    "要有情绪、符合人设、不要引号和句号结尾。只输出这一句话。"
)

# 无人设时的默认（可爱风）
DEFAULT_PERSONA = "语气可爱俏皮"

# 本地兜底词库按 persona 分档
LOCAL_PHRASES: dict[str, dict[str, list[str]]] = {
    "慵懒": {
        "done": [
            "……搞定啦，我去补个觉。",
            "活儿干完了……慢悠悠来验收吧。",
            "嗯……完成了，记得看一眼。",
            "搞定～没费什么力气……",
        ],
        "error": [
            "……出错了啊，等下再看。",
            "嗯……有点小问题，不急哈。",
            "翻车了……让我躺会儿再修。",
        ],
    },
    "御姐": {
        "done": [
            "哼，这种小事，早就搞定了。",
            "任务完成～勉为其难夸我一句吧。",
            "搞定了。看吧，还得是我。",
            "办妥了，下次记得先谢我。",
        ],
        "error": [
            "啧，出了点小差错，过来看着。",
            "别慌，本小姐马上查清楚。",
            "哼，翻车了……但也只是意外。",
        ],
    },
    "可爱": {
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
    },
}


class PromptEngine:
    def __init__(self, cfg: dict) -> None:
        self.base_url = cfg.get("base_url", "https://api.deepseek.com")
        self.model = cfg.get("model", "deepseek-chat")
        self.timeout = cfg.get("timeout_sec", 6)
        self.max_chars = cfg.get("max_chars", 20)
        self.api_key = os.environ.get(cfg.get("api_key_env", "DEEPSEEK_API_KEY"), "")
        self.persona = cfg.get("persona", DEFAULT_PERSONA)
        # 本地词库分档：人设含"御姐/慵懒/高傲"走御姐句式，否则可爱句式
        tone = ("慵懒" if "慵懒" in self.persona or "睡醒" in self.persona
                else "御姐" if any(w in self.persona for w in ("御姐", "高傲", "傲娇"))
                else "可爱")
        self._phrases = LOCAL_PHRASES[tone]
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(persona=self.persona)

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
                        {"role": "system", "content": self.system_prompt},
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
        return random.choice(self._phrases.get(event.kind, ["我有新消息！"]))

    def _clamp(self, text: str) -> str:
        text = text.replace("\n", " ").strip()
        return text[: self.max_chars] if text else ""
