"""companion —— 云小小情感陪伴聊天（独立程序，纯新增，不影响守护进程）

用法：
    python -m pc_daemon.companion

聊天流程：
    按回车开始说话 → 再按回车结束 → faster-whisper 本地转文字
    → GLM 生成温柔回应（记得聊过的内容）→ 晓晓音色从音箱说出

指令：
    /t 文字     打字兜底（不想说话时直接打字）
    /clear      清空对话记忆
    /mute       静音/取消静音（只看文字）
    /exit       退出（记忆自动保存，下次继续聊）

依赖：faster-whisper（语音转文字）、sounddevice（录音）、requests——见
companion_requirements.txt。首次转写会自动加载本地 whisper 模型。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from .tts import TTSEngine
    from . import pc_player
    from .prompt_engine import PromptEngine
except ImportError:                      # 支持直接 python pc_daemon/companion.py 运行
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pc_daemon.tts import TTSEngine, sanitize_for_tts, sanitize_for_tts
    from pc_daemon import pc_player
    from pc_daemon.prompt_engine import PromptEngine

log = logging.getLogger("companion")

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "companion_config.json"
MEMORY_PATH = BASE / "companion_memory.json"


# ---------- 录音（sounddevice） ----------

class Recorder:
    """按回车开始/结束的简易录音器：16kHz 单声道 float32。"""

    def __init__(self, sample_rate: int = 16000, device=None) -> None:
        import sounddevice as sd

        self._sd = sd
        self.sr = sample_rate
        self._frames: list = []
        self._active = False
        self.stream = sd.InputStream(samplerate=sample_rate, channels=1,
                                     dtype="float32", device=device,
                                     callback=self._cb)

    def _cb(self, indata, frames, _t, _status) -> None:
        if self._active:
            self._frames.append(indata.copy())

    def start(self) -> None:
        self._frames = []
        self._active = True
        if not self.stream.active:
            self.stream.start()

    def stop(self):
        """停止并返回音频数据（numpy 数组）或 None。"""
        import numpy as np

        self._active = False
        self.stream.stop()
        if not self._frames:
            return None
        return np.concatenate(self._frames)[:, 0]


# ---------- 转写（faster-whisper，懒加载） ----------

# whisper 对静音/噪声的著名幻觉文本（字幕组水印等），命中即视为没听清
HALLUCINATIONS = ("字幕by", "索兰娅", "请不吝点赞", "明镜与点点", "谢谢观看",
                  "字幕由", "订阅转发", "謝謝觀看", "Subscribe to", "字幕组",
                  "以下是普通话", "以下是普通")


class Transcriber:
    def __init__(self, model_name: str, vad_filter: bool = False,
                 target_peak: float = 0.7, noise_gate: float = 0.012,
                 initial_prompt: str = "", beam_size: int = 5,
                 asr_provider: str = "whisper",
                 sensevoice_model: str = "iic/SenseVoiceSmall") -> None:
        self.model_name = model_name
        self.vad_filter = vad_filter
        self.target_peak = target_peak   # 低增益麦克风自动放大到该峰值
        self.noise_gate = noise_gate     # RMS 低于此值视为没说话
        self.initial_prompt = initial_prompt
        self.beam_size = beam_size
        self.asr_provider = asr_provider  # "sensevoice"（推荐）| "whisper"
        self.sensevoice_model = sensevoice_model
        self._model = None                # whisper 懒加载
        self._sv_model = None             # SenseVoice 懒加载
        self._cc = None                  # 繁→简转换器（懒加载）

    def _get_model(self):
        if self._model is None:
            print("（首次使用正在加载 whisper 模型…）")
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_name, device="cpu",
                                       compute_type="int8")
        return self._model

    def _get_sensevoice(self):
        """加载 SenseVoice（FunASR）。

        sentencepiece 的 C++ 库无法读含中文用户名的路径，所以首次加载时把
        ModelScope 缓存**复制到纯 ASCII 路径**（pc_daemon/models/sensevoice/）
        再加载；模型缺失时先触发 ModelScope 下载（国内源）。"""
        if self._sv_model is None:
            import glob
            import os as _os
            import shutil

            print("（首次使用正在加载 SenseVoice 语音模型…）")
            target = BASE / "models" / "sensevoice"
            if not (target / "model.pt").exists():
                snaps = sorted(glob.glob(_os.path.expanduser(
                    "~/.cache/modelscope/models/iic--SenseVoiceSmall/snapshots/*")))
                if snaps:
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(snaps[-1], target, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns("example", "fig"))
                else:
                    from modelscope import snapshot_download

                    snap = snapshot_download("iic/SenseVoiceSmall")
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(snap, target, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns("example", "fig"))
            from funasr import AutoModel

            self._sv_model = AutoModel(model=str(target), device="cpu",
                                       disable_update=True)
        return self._sv_model

    def transcribe(self, audio) -> str:
        import numpy as np

        rms = float(np.sqrt((audio ** 2).mean())) if len(audio) else 0.0
        if rms < self.noise_gate:
            return ""                        # 噪声门：没说话不浪费转写
        peak = float(np.abs(audio).max())
        if 0.0 < peak < 0.5:                 # 增益过低时自动放大
            audio = audio * (self.target_peak / peak)
        if self.asr_provider == "sensevoice":
            text = self._sv_transcribe(audio)
        else:
            text = self._whisper_transcribe(audio)
        if any(h in text for h in HALLUCINATIONS):
            return ""                        # 幻觉文本按"没听清"处理
        return self._to_simplified(text)

    def _sv_transcribe(self, audio) -> str:
        model = self._get_sensevoice()
        res = model.generate(input=audio, language="zh", use_itn=True)
        import re as _re

        return _re.sub(r"<\|[^|]*\|>", "",
                       (res[0]["text"] if res else "")).strip()

    def _whisper_transcribe(self, audio) -> str:
        model = self._get_model()
        segments, _info = model.transcribe(
            audio, language="zh", vad_filter=self.vad_filter,
            beam_size=self.beam_size,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt or None)
        return " ".join(s.text.strip() for s in segments).strip()

    def _to_simplified(self, text: str) -> str:
        """whisper 偶尔输出繁体 → OpenCC 转简体（库缺失时原样返回）。"""
        if not text:
            return text
        try:
            if self._cc is None:
                from opencc import OpenCC
                self._cc = OpenCC("t2s")
            return self._cc.convert(text)
        except Exception:
            return text


class SearchSkill:
    """可插拔联网搜索：bing（免费攸底）/ bocha / tavily（API key）。"""

    def __init__(self, cfg) -> None:
        if isinstance(cfg, int):            # 兼容旧的 count 直传
            self.provider, self.count, self.cfg = "bing", cfg, {}
        else:
            self.provider = cfg.get("search_provider", "bing")
            self.count = cfg.get("search_count", 4)
            self.cfg = cfg
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    @staticmethod
    def clean_query(text: str) -> str:
        """把口语化消息洗成搜索引擎友好的关键词（中英文之间加空格）。"""
        for w in ("我要玩", "我要去", "我要", "你去", "帮我", "给我", "请问",
                  "找一下", "找一个", "找个", "查一下", "查查", "搜一下",
                  "搜索一下", "告诉我", "一下", "麻烦"):
            text = text.replace(w, " ")
        text = text.replace("，", " ").replace(",", " ").replace("。", " ")
        text = text.replace("的", " ").replace("是", " ")  # 的/是 隔开词组更利索
        text = text.replace("我需要", " ")
        # 中英文之间补空格（三角洲行动m14 → 三角洲行动 m14）
        import re as _re
        text = _re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", text)
        text = _re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff])", r"\1 \2", text)
        return " ".join(text.split()).strip()

    def search(self, query: str) -> list:
        """渐进式搜索：原查询 → 去掉尾词 → 再去尾词；每个候选先走
        配置的 provider，失败自动过必应兜底（解决语音转写带语气词搜不到的问题）。"""
        candidates = [query]
        words = query.split()
        if len(words) > 1:
            candidates.append(" ".join(words[:-1]))
        if len(words) > 2:
            candidates.append(" ".join(words[:-2]))
        import re as _re
        candidates = [_re.sub(r"[吗呢吧啊呀么]$", "", c).strip() for c in candidates]
        tried = set()
        for q in candidates:
            if not q or q in tried:
                continue
            tried.add(q)
            try:
                if self.provider == "bocha":
                    out = self._bocha(q)
                elif self.provider == "tavily":
                    out = self._tavily(q)
                else:
                    out = []
                if out:
                    return out
            except Exception:
                log.warning("搜索 provider %s 故障，回退必应", self.provider)
            out = self._bing(q)          # 每个候选都过必应兜底
            if out:
                return out
        return []

    def _api_key(self) -> str:
        """搜索 key 双通道：环境变量优先 → pc_daemon/secrets.json 兜底。"""
        import os as _os
        name = self.cfg.get("search_api_key_env", "SEARCH_API_KEY")
        key = _os.environ.get(name, "")
        if key:
            return key
        try:
            sec = json.loads((BASE / "secrets.json").read_text(encoding="utf-8"))
            return sec.get(name, "")
        except Exception:
            return ""

    def _bocha(self, query: str) -> list:
        import requests

        r = requests.post("https://api.bochaai.com/v1/web-search",
                          headers={"Authorization": f"Bearer {self._api_key()}"},
                          json={"query": query, "summary": True,
                                "count": self.count}, timeout=10)
        r.raise_for_status()
        out = []
        for v in r.json().get("data", {}).get("webPages", {}).get("value", []):
            out.append({"title": v.get("name", ""), "snippet": v.get("summary", ""),
                        "url": v.get("url", "")})
        return out

    def _tavily(self, query: str) -> list:
        import requests

        r = requests.post("https://api.tavily.com/search",
                          json={"api_key": self._api_key(), "query": query,
                                "max_results": self.count}, timeout=10)
        r.raise_for_status()
        return [{"title": v.get("title", ""), "snippet": v.get("content", ""),
                 "url": v.get("url", "")} for v in r.json().get("results", [])]

    def _bing(self, query: str) -> list:
        import html as _html
        import re as _re
        import requests

        try:
            r = requests.get("https://cn.bing.com/search",
                             params={"q": query, "count": self.count},
                             headers=self.headers, timeout=8)
            r.raise_for_status()
        except Exception:
            return []
        results = []
        for m in _re.finditer(
                r'<li class="b_algo".*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>(.*?)</li>',
                r.text, _re.S):
            url, title_html, rest = m.group(1), m.group(2), m.group(3)
            title = _html.unescape(_re.sub(r"<[^>]+>", "", title_html)).strip()
            snip = _re.search(r"<p[^>]*>(.*?)</p>", rest, _re.S)
            snippet = _html.unescape(_re.sub(r"<[^>]+>", "", snip.group(1))).strip()[:220] if snip else ""
            results.append({"title": title, "snippet": snippet, "url": url})
            if len(results) >= self.count:
                break
        return results


# ---------- 天气技能（Open-Meteo 免费 API，国内直连） ----------

WMO = {0: "晴", 1: "基本晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
       51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 61: "小雨", 63: "中雨",
       65: "大雨", 66: "冻雨", 67: "强冻雨", 71: "小雪", 73: "中雪", 75: "大雪",
       77: "雪粒", 80: "阵雨", 81: "中阵雨", 82: "强阵雨", 85: "小阵雪",
       86: "大阵雪", 95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹"}

def _detect_city() -> str:
    """IP 定位城市（免费接口，失败返回空）。"""
    import requests
    try:
        r = requests.get("http://ip-api.com/json/?lang=zh&fields=city", timeout=5)
        return r.json().get("city", "")
    except Exception:
        return ""

class WeatherSkill:
    """实时天气查询（30 分钟缓存）。城市自动 IP 定位，可在 config 指定。"""
    def __init__(self, cfg: dict) -> None:
        self.city_cfg = cfg.get("city", "")
        self.cache_path = BASE / "companion_weather.json"
        self._cache = None          # (时间戳, 文本)
        self._latlon = None
        self._latlon_city = None
        import time as _t
        self._now = _t.time

    def _resolve_latlon(self, city: str | None = None):
        city = city or self.city_cfg
        if city is None and self._latlon:
            return self._latlon
        if city == self._latlon_city and self._latlon:
            return self._latlon
        import requests
        city = city or _detect_city()
        if not city:
            return None
        try:
            r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": city, "language": "zh", "count": 1},
                             timeout=6)
            hit = r.json().get("results", [None])[0]
            if hit:
                self._latlon = (hit["latitude"], hit["longitude"], hit["name"])
                self._latlon_city = city
        except Exception:
            return None
        return self._latlon

    def get(self, city: str | None = None) -> str:
        now = self._now()
        if self._cache and now - self._cache[0] < 1800 and                 (city or "") == (self._latlon_city or ""):
            return self._cache[1]
        loc = self._resolve_latlon(city)
        if not loc:
            return ""
        lat, lon, name = loc
        try:
            import requests
            r = requests.get("https://api.open-meteo.com/v1/forecast",
                             params={"latitude": lat, "longitude": lon,
                                     "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                                     "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                                     "timezone": "auto", "forecast_days": 1},
                             timeout=8)
            cur, daily = r.json()["current"], r.json()["daily"]
            self.last_code = cur["weather_code"]
            desc = WMO.get(cur["weather_code"], "未知")
            text = (f"{name} 当前 {cur['temperature_2m']}℃（{desc}），"
                    f"湿度 {cur['relative_humidity_2m']}%，风速 {cur['wind_speed_10m']}km/h，"
                    f"今天 {daily['temperature_2m_min'][0]}~{daily['temperature_2m_max'][0]}℃")
            self._cache = (now, text)
            return text
        except Exception:
            return ""

# ---------- 对话（GLM + 记忆落盘） ----------

class Companion:
    def __init__(self, cfg: dict) -> None:
        from datetime import datetime
        self.cfg = cfg
        self.history: list[dict] = []          # [{"role","content"}]
        self.memory_path = BASE / "companion_memory.json"
        self._load_memory()
        # 系统提示 = 人设 + 实时日期时间（星期直接给中文，防模型算错）
        now = datetime.now()
        weekday = "一二三四五六日"[now.weekday()]
        self.system_prompt = (cfg["persona"] + "\n当前时间：" +
                              now.strftime("%Y-%m-%d %H:%M") + " 星期" + weekday)
        self.weather = WeatherSkill(cfg)
        self.search = SearchSkill(cfg)
        extra = tuple(cfg.get("search_keywords_extra", []))
        if extra:
            self.SEARCH_KEYWORDS = tuple(self.SEARCH_KEYWORDS) + extra

    WEATHER_KEYWORDS = ("天气", "气温", "温度", "几度", "下雨", "下雪", "降雨",
                        "热不热", "冷不冷", "穿什么", "带伞", "湿度", "风力")
    TIME_KEYWORDS = ("星期几", "礼拜几", "几号", "多少号", "日期", "今天几号",
                     "几点", "时间")
    SEARCH_KEYWORDS = ("搜索", "搜一下", "搜搜", "查一下", "查查", "帮我找",
                       "找一下", "找一个", "找个", "攻略", "新闻", "最新消息",
                       "改枪", "怎么改", "配装", "兑换码", "推荐一下")

    def _weather_note(self, user_text: str) -> str:
        """检测天气意图 → 返回要并入主系统提示的实时天气文本（无意图返回空）。"""
        if not any(k in user_text for k in self.WEATHER_KEYWORDS):
            return ""
        info = self.weather.get()
        if not info:
            return "\n（天气服务暂不可用：如被问到请坦诚说明查不到实时天气。）"
        return "\n实时天气（必须以此为准回答天气问题）：" + info

    def _load_memory(self) -> None:
        try:
            self.history = json.loads(self.memory_path.read_text(encoding="utf-8"))
            if self.history:
                print(f"（已恢复上次对话记忆，共 {len(self.history)//2} 轮）")
        except Exception:
            self.history = []

    def _save_memory(self) -> None:
        cap = self.cfg.get("memory_rounds", 20) * 2
        self.history = self.history[-cap:]
        self.memory_path.write_text(
            json.dumps(self.history, ensure_ascii=False, indent=1), encoding="utf-8")

    def clear(self) -> None:
        self.history = []
        self._save_memory()
        print("（记忆已清空，我们是新朋友啦）")

    # ---------- 智能体：工具定义与执行 ----------

    TOOLS = [
        {"type": "function", "function": {
            "name": "get_weather",
            "description": "查询用户所在城市的实时天气、今日温度范围。用户问天气/温度/是否下雨/要不要带伞/冷不热时调用。",
            "parameters": {"type": "object", "properties": {
                "city": {"type": "string", "description": "城市名，不填则用用户所在城市"}},
                "required": []}}},
        {"type": "function", "function": {
            "name": "get_time",
            "description": "获取当前日期、时间和星期。用户问时间/日期/星期几/几点时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "web_search",
            "description": "你必须使用的联网搜索工具。只要用户提到查找/获取/搜索任何信息、攻略、代码、新闻、价格等，就必须调用此工具——你没有独立联网能力，绝不能凭记忆回答这类问题，否则就是编造。",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"]}}},
    ]

    def _exec_tool(self, name: str, args: dict) -> str:
        if name == "get_weather":
            return self.weather.get(city=args.get("city") or None) \
                   or "天气服务暂时不可用"
        if name == "get_time":
            from datetime import datetime
            now = datetime.now()
            return (now.strftime("%Y-%m-%d %H:%M") +
                    " 星期" + "一二三四五六日"[now.weekday()])
        if name == "web_search":
            results = self.search.search(args.get("query", ""))
            if not results:
                return "搜索失败：没有找到相关结果"
            return "\n".join(f"{i + 1}. {r['title']}：{r['snippet']}"
                             for i, r in enumerate(results))
        return f"未知工具 {name}"

    # ---------- 离线兜底（无 key/断网时：关键词本地直出） ----------

    def _offline_reply(self, user_text: str) -> str:
        import random
        from datetime import datetime
        if any(k in user_text for k in self.TIME_KEYWORDS):
            now = datetime.now()
            weekday = "一二三四五六日"[now.weekday()]
            return f"现在是 {now.strftime('%Y-%m-%d %H:%M')}，星期{weekday}～"
        if any(k in user_text for k in self.WEATHER_KEYWORDS):
            info = self.weather.get()
            if info:
                rainy = getattr(self.weather, "last_code", 0) >= 51
                tail = "可能下雨，记得带伞哦～" if rainy else "不用带伞，放心出门～"
                return f"刚帮你看了一眼：{info}。{tail}"
            return "（天气服务暂不可用，等下再问我）"
        return ""

    def chat(self, user_text: str) -> str:
        import requests

        self.history.append({"role": "user", "content": user_text})
        key = PromptEngine._resolve_key(self.cfg)
        if not key:
            print("（未找到 LLM API Key，进入本地安静模式：只记录不回应）")
            self.history.append({"role": "assistant",
                                 "content": "（我在线下，晚点再聊）"})
            self._save_memory()
            return ""

        cap = self.cfg.get("memory_rounds", 20) * 2
        messages = [{"role": "system", "content": self.system_prompt}] \
                   + self.history[-cap - 1:]

        reply = ""
        tool_fired = False
        try:
            for _round in range(3):                    # 工具调用最多 3 跳
                resp = requests.post(
                    f"{self.cfg['base_url'].rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": self.cfg.get("model", "glm-4-flash"),
                          "messages": messages,
                          "tools": self.TOOLS,
                          "max_tokens": 300, "temperature": 0.8},
                    timeout=self.cfg.get("timeout_sec", 30))
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    reply = (msg.get("content") or "").strip()
                    # 修：GLM 偶尔把工具调用当可见文本输出（content 里带
                    # {"delta":{"tool_calls":[{"function":{"name":"web_search"...}}]}}）
                    # → 提取其中的 query，按普通工具执行
                    if '"tool_calls"' in reply and '"web_search"' in reply:
                        import re as _re
                        mq = _re.search(r'\\"query\\":\s*\\"((?:[^"\\]|\\.)*?)\\"', reply)
                        if mq:
                            try:
                                q2 = json.loads('"' + mq.group(1) + '"')
                            except Exception:
                                q2 = mq.group(1)
                            tool_fired = True
                            result = self._exec_tool("web_search", {"query": q2})
                            if result:
                                messages.append({"role": "user", "content":
                                    ("【系统搜索结果】基于以下真实搜索结果重新回答"
                                     "（不要输出任何 JSON 或代码）：\n" + result)})
                                continue               # 回到循环让模型基于结果作答
                    break
                tool_fired = True
                messages.append(msg)                    # assistant.tool_calls
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self._exec_tool(fn.get("name", ""), args)
                    log.info("智能体工具调用: %s(%s) → %s",
                             fn.get("name"), args, result[:60])
                    messages.append({"role": "tool",
                                     "tool_call_id": tc.get("id", ""),
                                     "content": result})
            # 兜底：GLM 没调工具时 → 三种信号强制搜索重答
            # ①消息含搜索意图关键词 ②回复出现拒答话术 ③模型主动提出"帮你搜索"（直接替用户答应）
            refuse = any(p in reply for p in ("无法访问互联网", "无法联网", "无法找到",
                                              "没有找到", "无法直接提供", "无法提供具体",
                                              "无法上网", "无法搜索", "无法确定"))
            offer = any(p in reply for p in ("帮你搜索", "帮你搜一下", "帮你查一下",
                                             "需要我帮你搜", "帮你查找", "我来搜索"))
            user_asks = any(k in user_text for k in self.SEARCH_KEYWORDS) or (
                ("什么" in user_text or "？" in user_text or "?" in user_text
                 or "怎么" in user_text) and
                any(u in reply for u in ("不确定", "不清楚", "可能是", "或许是", "不太确定")))
            if not tool_fired and reply and (
                    any(k in user_text for k in self.SEARCH_KEYWORDS) or refuse or offer
                    or user_asks):
                results = (self.search.search(self.search.clean_query(user_text))
                           or self.search.search(user_text))
                if results:
                    blob = "\n".join(f"{i + 1}. {r['title']}：{r['snippet']}"
                                     for i, r in enumerate(results))
                    inject = ("【系统强制注入搜索结果】基于以下真实搜索结果重新回答"
                              "你上一条没能回答的问题：\n" + blob)
                    messages.append({"role": "user", "content": inject})
                    resp = requests.post(
                        f"{self.cfg['base_url'].rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": self.cfg.get("model", "glm-4-flash"),
                              "messages": messages,
                              "max_tokens": 300, "temperature": 0.7},
                        timeout=self.cfg.get("timeout_sec", 30))
                    reply = resp.json()["choices"][0]["message"]["content"].strip()
                    log.info("拒答兜底：已强制搜索并重答")

            if not reply:                               # 3 跳没出结果
                reply = self._offline_reply(user_text) or "（我想了半天没想明白，换个说法问问？）"
        except Exception as e:
            log.exception("智能体对话异常")
            reply = self._offline_reply(user_text)
            if not reply:
                print(f"（网络开小差了：{e}）")
                self.history.pop()                      # 移除未回应的用户消息
                self._save_memory()
                return ""
        if not reply:
            reply = "（我走神了，再说一遍？）"
        self.history.append({"role": "assistant", "content": reply})
        self._save_memory()
        return reply


# ---------- 主循环 ----------

def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        filename=str(BASE / "logs" / "companion.log"),
                        encoding="utf-8")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    tts = TTSEngine(cfg.get("tts", {}))
    chat = Companion(cfg)
    ear = Transcriber(cfg.get("whisper_model", "small"),
                  vad_filter=cfg.get("vad_filter", False),
                  initial_prompt=cfg.get("initial_prompt", ""),
                  beam_size=cfg.get("beam_size", 5),
                  asr_provider=cfg.get("asr_provider", "whisper"),
                  sensevoice_model=cfg.get("sensevoice_model",
                                           "iic/SenseVoiceSmall"))
    mic = Recorder(cfg.get("sample_rate", 16000),
                  device=cfg.get("input_device"))
    muted = False

    print("=" * 56)
    print("  云小小陪伴模式 🌙  说话聊天，/t 打字，/exit 退出")
    print("  操作：按回车开始说 → 说完再按回车")
    print("=" * 56)

    while True:
        try:
            cmd = input("\n🎤 回车开始说话（或输入 /t 文字、/exit）> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if cmd == "/exit":
            break
        if cmd == "/clear":
            chat.clear()
            continue
        if cmd == "/mute":
            muted = not muted
            print(f"（语音{'已静音' if muted else '已开启'}）")
            continue
        if cmd.startswith("/t "):
            text = cmd[3:].strip()
        elif cmd == "":
            print("🔴 录音中…说完再按回车")
            mic.start()
            input()
            audio = mic.stop()
            print("🧠 转写中…")
            try:
                text = ear.transcribe(audio)
            except Exception as e:
                print(f"（转写失败：{e}，可用 /t 打字）")
                continue
        else:
            print("（没看懂，回车直接说话，或 /t 文字 /exit）")
            continue

        if not text:
            print("（没听清——靠近麦克风、说话声大一点再试；或 /t 打字）")
            continue
        print(f"你：{text}")

        try:
            reply = chat.chat(text)
        except Exception as e:
            print(f"（网络开小差了：{e}）")
            continue
        if not reply:
            continue
        print(f"云小小：{reply}")
        if not muted:
            pcm = tts.synthesize(sanitize_for_tts(reply))
            if pcm:
                pc_player.play_pcm(pcm)

    print("\n云小小：下次再聊哦，我会记得我们的对话 🌙")


if __name__ == "__main__":
    main()
