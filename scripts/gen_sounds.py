"""gen_sounds —— 生成小智 v1 本地提示音 WAV 资源（V1-103）。

用途：v1 固件没有 TTS 前的"嗓子"——三段内置音效，嵌入固件 flash，
     PC 端发 play_sound 即可让小智出声（Agent 完成/报错/通知）。
     生成的 WAV 统一 16kHz 单声道 16bit（与 I2S 播放参数一致）。

用法：python scripts/gen_sounds.py
输出：firmware/agent_speaker/main/sounds/*.wav（经 CMake EMBED_FILES 嵌入固件）
"""
import math
import struct
import wave
from pathlib import Path

SR = 16000  # 采样率：与固件 I2S 配置一致
OUT = Path(__file__).resolve().parent.parent / "firmware" / "agent_speaker" / "main" / "sounds"


def note(freq: float, ms: int, vol: float = 0.6, decay: bool = True) -> list[float]:
    """生成一个带指数衰减的正弦音符。"""
    n = int(SR * ms / 1000)
    return [vol * math.sin(2 * math.pi * freq * i / SR) * (math.exp(-3.0 * i / n) if decay else 1.0)
            for i in range(n)]


def silence(ms: int) -> list[float]:
    return [0.0] * int(SR * ms / 1000)


def chime_success() -> list[float]:
    """完成音：上行双音 C5→G5，明亮轻快。"""
    return note(523.25, 110) + silence(15) + note(783.99, 180)


def chime_error() -> list[float]:
    """报错音：下行双音 A4→E4，低沉提醒。"""
    return note(440.0, 140) + silence(20) + note(329.63, 240)


def chime_notice() -> list[float]:
    """通知音：单短音 A5。"""
    return note(880.0, 160)


def write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)          # 16bit
        w.setframerate(SR)
        frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
        w.writeframes(frames)
    print(f"{path.name}: {len(samples)/SR*1000:.0f}ms {path.stat().st_size}B")


SOUNDS = {
    "chime_success.wav": chime_success,
    "chime_error.wav": chime_error,
    "chime_notice.wav": chime_notice,
}

if __name__ == "__main__":
    for name, fn in SOUNDS.items():
        write_wav(OUT / name, fn())
    print("提示音生成完毕 →", OUT)
