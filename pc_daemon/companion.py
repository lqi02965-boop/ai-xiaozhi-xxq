"""companion —— 小智情感陪伴聊天（独立程序，纯新增，不影响守护进程）

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
                  "字幕由", "订阅转发", "謝謝觀看", "Subscribe to", "字幕组")


class Transcriber:
    def __init__(self, model_name: str, vad_filter: bool = False,
                 target_peak: float = 0.7, noise_gate: float = 0.012) -> None:
        self.model_name = model_name
        self.vad_filter = vad_filter
        self.target_peak = target_peak   # 低增益麦克风自动放大到该峰值
        self.noise_gate = noise_gate     # RMS 低于此值视为没说话
        self._model = None

    def _get_model(self):
        if self._model is None:
            print("（首次使用正在加载语音模型…）")
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_name, device="cpu",
                                       compute_type="int8")
        return self._model

    def transcribe(self, audio) -> str:
        import numpy as np

        rms = float(np.sqrt((audio ** 2).mean())) if len(audio) else 0.0
        if rms < self.noise_gate:
            return ""                        # 噪声门：没说话不浪费转写
        peak = float(np.abs(audio).max())
        if 0.0 < peak < 0.5:                 # 增益过低时自动放大
            audio = audio * (self.target_peak / peak)
        model = self._get_model()
        segments, _info = model.transcribe(audio, language="zh",
                                           vad_filter=self.vad_filter)
        text = " ".join(s.text.strip() for s in segments).strip()
        if any(h in text for h in HALLUCINATIONS):
            return ""                        # 幻觉文本按"没听清"处理
        return text


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
        import time as _t
        self._now = _t.time

    def _resolve_latlon(self):
        if self._latlon:
            return self._latlon
        import requests
        city = self.city_cfg or _detect_city()
        if not city:
            return None
        try:
            r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": city, "language": "zh", "count": 1},
                             timeout=6)
            hit = r.json().get("results", [None])[0]
            if hit:
                self._latlon = (hit["latitude"], hit["longitude"], hit["name"])
        except Exception:
            return None
        return self._latlon

    def get(self) -> str:
        now = self._now()
        if self._cache and now - self._cache[0] < 1800:
            return self._cache[1]
        loc = self._resolve_latlon()
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

    WEATHER_KEYWORDS = ("天气", "气温", "温度", "几度", "下雨", "下雪", "降雨",
                        "热不热", "冷不冷", "穿什么", "带伞", "湿度", "风力")
    TIME_KEYWORDS = ("星期几", "礼拜几", "几号", "多少号", "日期", "今天几号",
                     "几点", "时间")

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

    def chat(self, user_text: str) -> str:
        import random

        # 日期时间问题：本地直出（不让大模型算星期，实测必错）
        if any(k in user_text for k in self.TIME_KEYWORDS):
            from datetime import datetime
            now = datetime.now()
            weekday = "一二三四五六日"[now.weekday()]
            self.history.append({"role": "user", "content": user_text})
            body = random.choice([
                f"现在是 {now.strftime('%Y-%m-%d %H:%M')}，星期{weekday}～",
                f"看了下时间：{now.strftime('%m月%d日')} 星期{weekday}，"
                f"{now.strftime('%H:%M')}。",
            ])
            self.history.append({"role": "assistant", "content": body})
            self._save_memory()
            return body

        # 天气问题：本地模板直出真实数据（不让大模型编事实）
        if any(k in user_text for k in self.WEATHER_KEYWORDS):
            info = self.weather.get()
            if info:
                rainy = getattr(self.weather, "last_code", 0) >= 51
                if rainy:
                    body = random.choice([
                        f"刚帮你看了一眼：{info}。可能下雨，记得带伞哦～",
                        f"嗯…{info}。看着要下雨，伞带上稳妥。",
                    ])
                else:
                    body = random.choice([
                        f"刚帮你看了一眼：{info}。不用带伞，放心出门～",
                        f"看了下天气：{info}。天气不错，出门没问题～",
                    ])
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": body})
                self._save_memory()
                return body
            # 天气服务挂了才走 GLM（会坦诚说查不到）
            user_text += "\n【系统提示】天气服务暂不可用，请坦诚说明查不到实时天气。"

        import requests

        self.history.append({"role": "user", "content": user_text})
        cap = self.cfg.get("memory_rounds", 20) * 2
        messages = [{"role": "system", "content": self.system_prompt}] \
                   + self.history[-cap:]
        key = PromptEngine._resolve_key(self.cfg)
        if not key:
            print("（未找到 GLM_API_KEY，进入本地安静模式：只记录不回应）")
            self.history.append({"role": "assistant",
                                 "content": "（我在线下，晚点再聊）"})
            self._save_memory()
            return ""
        resp = requests.post(
            f"{self.cfg['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": self.cfg.get("model", "glm-4-flash"),
                  "messages": messages,
                  "max_tokens": 300, "temperature": 0.8},
            timeout=self.cfg.get("timeout_sec", 30))
        resp.raise_for_status()
        reply = resp.json()["choices"][0]["message"]["content"].strip()
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
                  vad_filter=cfg.get("vad_filter", False))
    mic = Recorder(cfg.get("sample_rate", 16000),
                  device=cfg.get("input_device"))
    muted = False

    print("=" * 56)
    print("  小智陪伴模式 🌙  说话聊天，/t 打字，/exit 退出")
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
        print(f"小智：{reply}")
        if not muted:
            pcm = tts.synthesize(sanitize_for_tts(reply))
            if pcm:
                pc_player.play_pcm(pcm)

    print("\n小智：下次再聊哦，我会记得我们的对话 🌙")


if __name__ == "__main__":
    main()
