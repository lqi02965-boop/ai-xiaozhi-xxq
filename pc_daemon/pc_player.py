"""pc_player —— PC 本机播放 PCM（过渡模式，V1-302b）。

用途：功放还没焊好之前，让云小小的"声音"先从 PC 音箱出来，完整体验
     「Agent 事件 → DeepSeek 播报词 → TTS → 出声」闭环；焊好后把
     config.json 的 audio_output 从 "pc" 改成 "device" 即切换到云小小本体。

实现：把 16k/16bit/mono 原始 PCM 包上 WAV 头（纯内存，零临时文件），
     用 Windows 标准库 winsound 播放——阻塞式，播完才返回。
"""
from __future__ import annotations

import io
import logging
import wave

log = logging.getLogger("pc_player")


def pcm_to_wav(pcm: bytes, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    """裸 PCM → 内存 WAV（RIFF 头），供 winsound 播放。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def play_pcm(pcm: bytes) -> bool:
    """同步播放 PCM（约 len/32000 秒）；失败返回 False。
    任意线程调用 stop_playback() 可立即打断。"""
    try:
        import winsound

        wav = pcm_to_wav(pcm)
        winsound.PlaySound(wav, winsound.SND_MEMORY)
        log.info("PC 本机播放完成: %d bytes PCM", len(pcm))
        return True
    except Exception:
        log.warning("PC 播放失败", exc_info=True)
        return False


def stop_playback() -> None:
    """立即停止本进程当前播放（winsound 进程级打断）。"""
    try:
        import winsound

        winsound.PlaySound(None, 0)
    except Exception:
        pass
