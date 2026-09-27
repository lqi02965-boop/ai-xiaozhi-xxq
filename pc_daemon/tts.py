"""tts —— 播报词转 16k/16bit/mono PCM（V1-301 + V1-302 PC 侧）。

用途：让小智说"人话"。三级降级链，保证任何环境都有声音：
  A. edge-tts（微软晓晓，音质最好）→ MP3 → 需 ffmpeg 转 PCM（本机未装则自动跳过）
  B. pyttsx3（Windows SAPI Huihui，离线）→ WAV → audioop 重采样 PCM（本机验证通过）
  C. 全部失败 → 返回 None，调用方降级为本地提示音 chime

输出统一 16000Hz / 16bit / 单声道，与固件 I2S 播放参数一致。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import threading
import wave

import audioop

log = logging.getLogger("tts")

TARGET_RATE = 16000


class TTSEngine:
    def __init__(self, cfg: dict) -> None:
        self.voice = cfg.get("edge_voice", "zh-CN-XiaoxiaoNeural")
        self.rate = cfg.get("rate", "+8%")
        self.pitch = cfg.get("edge_pitch", "")     # 变调：如 "+20Hz"（少女）/-20Hz（御姐）
        self.sapi_rate = cfg.get("sapi_rate", 180)
        self._lock = threading.Lock()   # pyttsx3 非线程安全，串行化
        self._ffmpeg = self._find_ffmpeg(cfg.get("ffmpeg_path", ""))
        log.info("TTS 初始化：voice=%s pitch=%s ffmpeg=%s（A 路线 %s）",
                 self.voice, self.pitch or "默认", self._ffmpeg or "未安装",
                 "启用" if self._ffmpeg else "跳过")

    @staticmethod
    def _find_ffmpeg(configured: str) -> str | None:
        """定位 ffmpeg：config 全路径 → PATH → winget Links → imageio-ffmpeg 自带二进制。"""
        if configured and os.path.isfile(configured):
            return configured
        found = shutil.which("ffmpeg")
        if found:
            return found
        links_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links")
        candidate = os.path.join(links_dir, "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            return None

    def synthesize(self, text: str) -> bytes | None:
        """播报词 → PCM 字节；失败返回 None（调用方降级提示音）。"""
        if not text:
            return None
        if self._ffmpeg:
            pcm = self._via_edge_tts(text)
            if pcm:
                return pcm
        pcm = self._via_pyttsx3(text)
        if pcm:
            return pcm
        log.warning("TTS 全链路失败，交由调用方降级为提示音")
        return None

    # ---- A 路线：edge-tts + ffmpeg --------------------------------------
    def _via_edge_tts(self, text: str) -> bytes | None:
        try:
            import edge_tts

            async def _synth(out: str) -> None:
                # edge-tts 要求 pitch 必须形如 "+0Hz"，不能传 None/空串
                await edge_tts.Communicate(text, voice=self.voice, rate=self.rate,
                                           pitch=self.pitch or "+0Hz").save(out)

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                mp3_path = f.name
            asyncio.run(_synth(mp3_path))
            import subprocess

            proc = subprocess.run(
                [self._ffmpeg, "-v", "error", "-i", mp3_path, "-f", "s16le",
                 "-acodec", "pcm_s16le", "-ar", str(TARGET_RATE), "-ac", "1", "-"],
                capture_output=True, timeout=20)
            os.unlink(mp3_path)
            if proc.returncode != 0 or not proc.stdout:
                log.warning("ffmpeg 转码失败: %s", proc.stderr[:120])
                return None
            log.info("A 路线（edge-tts+ffmpeg）: %d bytes PCM", len(proc.stdout))
            return proc.stdout
        except Exception:
            log.warning("A 路线异常，转 B 路线", exc_info=True)
            return None

    # ---- B 路线：pyttsx3（Windows SAPI） ---------------------------------
    def _via_pyttsx3(self, text: str) -> bytes | None:
        try:
            with self._lock:
                import pyttsx3

                engine = pyttsx3.init()
                engine.setProperty("rate", self.sapi_rate)
                fd, wav_path = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                try:
                    engine.save_to_file(text, wav_path)
                    engine.runAndWait()
                finally:
                    engine.stop()
                with wave.open(wav_path, "rb") as w:
                    rate, ch, width = (w.getframerate(), w.getnchannels(),
                                       w.getsampwidth())
                    raw = w.readframes(w.getnframes())
                os.unlink(wav_path)
            if width != 2:
                log.warning("SAPI 输出 %d bit，非 16bit，放弃", width * 8)
                return None
            if rate != TARGET_RATE:
                raw, _ = audioop.ratecv(raw, 2, ch, rate, TARGET_RATE, None)
            if ch == 2:
                raw, _ = audioop.tomono(raw, 2, 0.5, 0.5)
            log.info("B 路线（pyttsx3 SAPI）: %d bytes PCM（约 %.1f 秒）",
                     len(raw), len(raw) / (TARGET_RATE * 2))
            return raw
        except Exception:
            log.warning("B 路线异常", exc_info=True)
            return None
