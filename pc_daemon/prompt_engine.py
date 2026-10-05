"""播报词生成：多供应商 LLM 链 + 本地词库兜底（V1-205，V1-302c 扩展）。

供应商按 config.json 的 llm 列表顺序尝试（如 ZCode GLM → DeepSeek），
全部失败或未配 Key 时走本地词库。每个供应商的 Key 解析双通道：
环境变量优先，ZCode 套餐 token 允许直读其 provider_config.json（不进本仓库）。

人设从 config.json 顶层 persona 读取——换性格只改配置，不动代码。
"""
from __future__ import annotations

import json
import logging
import os
import random

from .events import AgentEvent

log = logging.getLogger("prompt_engine")

SYSTEM_PROMPT_TEMPLATE = (
    "你是桌面机器人云小小的播报词生成器。人设：{persona}。"
    "根据 Agent 工作事件，生成一句不超过20字的中文口语播报，"
    "开头要提到是哪个 Agent（如 zcode/workbuddy），"
    "要有情绪、符合人设、不要引号和句号结尾。只输出这一句话。"
)

# 无人设时的默认（可爱风）
DEFAULT_PERSONA = "语气可爱俏皮"

# 本地兜底词库按 persona 分档
LOCAL_PHRASES: dict[str, dict[str, list[str]]] = {
    "慵懒": {
        "done": [
            "搞定了，过来看一眼吧。",
            "任务完成，稳稳的。",
            "嗯，办妥了，不用谢。",
            "完成了，结果没毛病。",
        ],
        "error": [
            "出了点问题，不大，我看着。",
            "报错了，稍等，查一下就好。",
            "有点小意外，已记录，稍后处理。",
        ],
        "approval": [
            "{agent} 卡在审批了，过来点一下。",
            "需要你审批，去看看再走。",
            "有个确认框等你，别走远。",
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
    def __init__(self, providers: list[dict], persona: str = DEFAULT_PERSONA,
                 max_chars: int = 20, library_path: str = "",
                 use_llm: bool = False) -> None:
        self.use_llm = use_llm    # false=纯词库随机短语（零 token）；true=LLM 生成
        self.providers = [p for p in providers if p]
        self.max_chars = max_chars
        self.library_path = library_path
        self._lib_mtime = 0.0
        self._lib_tone = {}        # 状态 → 词库列表（来自 Obsidian 播报词库）
        self.persona = persona     # 会被词库文件的「人设」段覆盖
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(persona=persona)
        self._phrases = LOCAL_PHRASES["可爱"]
        self._refresh_library()
        tone = ("慵懒" if "慵懒" in self.persona or "睡醒" in self.persona
                else "御姐" if any(w in self.persona for w in ("御姐", "高傲", "傲娇"))
                else "可爱")
        if not self._lib_tone:     # 词库文件没提供该状态时用内置分档
            self._phrases = LOCAL_PHRASES[tone]
        names = [p.get("name", p.get("model", "?")) for p in self.providers]
        log.info("LLM 链: %s → 本地词库（%s档，词库文件=%s）",
                 " → ".join(names) or "(空)", tone,
                 "已加载" if self._lib_tone else "未启用")

    def _refresh_library(self) -> None:
        """Obsidian 播报词库热加载：文件变化即更新人设与词库（本地读文件零 token）。"""
        if not self.library_path:
            return
        try:
            mtime = os.path.getmtime(self.library_path)
        except OSError:
            return
        if mtime == self._lib_mtime:
            return
        self._lib_mtime = mtime
        try:
            persona_lines, phrases = [], {}
            cur = None
            with open(self.library_path, encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip()
                    if line.startswith("## "):
                        cur = line[3:].strip()
                        phrases.setdefault(cur, [])
                    elif cur and line.startswith("- "):
                        phrases[cur].append(line[2:].strip())
                    elif cur == "人设" and line.strip():
                        persona_lines.append(line.strip())
            if persona_lines:
                self.persona = "，".join(persona_lines)
                self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(persona=self.persona)
            self._lib_tone = phrases
            log.info("播报词库已加载: %s（状态: %s）",
                     os.path.basename(self.library_path),
                     "、".join(k for k in phrases if k != "人设"))
        except Exception:
            log.exception("播报词库解析失败，沿用上次内容")

    # ---- 对外 ------------------------------------------------------------
    def gen(self, event: AgentEvent) -> str:
        self._refresh_library()   # 每次事件先热加载词库（mtime 未变则零开销）
        # 默认直接用词库随机短语（零 token）；use_llm=true 时才走 LLM 链
        if not self.use_llm:
            return self._gen_local(event)
        user = f"Agent={event.agent}，事件={event.kind}，详情={event.detail}"
        for p in self.providers:
            text = self._gen_remote(p, user)
            if text:
                return text
            log.warning("供应商 %s 失败，尝试下一个", p.get("name", "?"))
        log.info("所有 LLM 不可用，本地词库兜底")
        return self._gen_local(event)

    # ---- 远端调用 ----------------------------------------------------------
    def _gen_remote(self, p: dict, user: str) -> str:
        key = self._resolve_key(p)
        if not key:
            log.info("%s 未配置 Key，跳过", p.get("name", "?"))
            return ""
        try:
            import requests

            resp = requests.post(
                f"{p['base_url'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": p["model"],
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": p.get("max_tokens", 50),
                    "temperature": 1.0,
                },
                timeout=p.get("timeout_sec", 6),
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip().strip('"“”')
            if not text:
                log.warning("%s 返回空 content（思考型模型 token 不足？）", p.get("name"))
                return ""
            return self._clamp(text)
        except Exception:
            log.exception("%s 调用异常", p.get("name", "?"))
            return ""

    @staticmethod
    def _resolve_key(p: dict) -> str:
        """Key 双通道：环境变量优先；ZCode 套餐 token 可直读其 provider 配置。"""
        key = os.environ.get(p.get("api_key_env", "LLM_API_KEY"), "")
        if key:
            return key
        if p.get("allow_zcode_fallback"):
            try:
                data = json.load(open(os.path.expanduser(
                    "~/.zcode/v2/provider_config.json"), encoding="utf-8"))
                for rule in data["config"]["providerConfigRules"]["providerRules"]:
                    k = (rule.get("config", {}).get("access", {}) or {}).get("apiKey", "")
                    if k:
                        return k
            except Exception:
                pass
        return ""

    def _gen_local(self, event: AgentEvent) -> str:
        # 播报词库段名（中文）↔ 事件种类（英文）映射
        section = {"done": "完成", "error": "报错", "approval": "审批提醒"}.get(
            event.kind, event.kind)
        pool = self._lib_tone.get(section) or self._phrases.get(event.kind)
        text = random.choice(pool or ["{agent}有新消息！"])
        text = text.replace("{agent}", event.agent or "")       # 词库可写 {agent} 占位
        # 词里没点名 agent 就补上（审批词常已自带，含名字则不重复）
        if event.agent and event.agent not in text:
            text = f"{event.agent} {text}"
        return text

    def _clamp(self, text: str) -> str:
        text = text.replace("\n", " ").strip()
        return text[: self.max_chars] if text else ""
