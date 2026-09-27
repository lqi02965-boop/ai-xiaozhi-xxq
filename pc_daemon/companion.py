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
    from pc_daemon.tts import TTSEngine
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


# ---------- 对话（GLM + 记忆落盘） ----------

class Companion:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.history: list[dict] = []          # [{"role","content"}]
        self.memory_path = BASE / "companion_memory.json"
        self._load_memory()
        self.system_prompt = cfg["persona"]

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
            pcm = tts.synthesize(reply)
            if pcm:
                pc_player.play_pcm(pcm)

    print("\n小智：下次再聊哦，我会记得我们的对话 🌙")


if __name__ == "__main__":
    main()
