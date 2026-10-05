# -*- coding: utf-8 -*-
"""board_mic_test.py —— 上板验收 INMP441 麦克风（v1.6 固件 mic_start/mic_data）

流程：连 COM11 → mic_start → 采 N 秒 mic_data 帧 → mic_stop →
      报电平（峰值/RMS）→ 存 WAV →（可选 --transcribe）走 SenseVoice 转写看识别效果。

⚠️ 守护进程占用 COM11，先跑 scripts/stop_daemon.bat 再测。
用法：python scripts/board_mic_test.py --port COM11 --secs 5 [--shift 12] [--transcribe]
"""
import argparse
import audioop  # 标准库；如 Python 3.13+ 移除则改用 numpy 计算
import base64
import json
import sys
import threading
import time
import wave
from pathlib import Path

import serial

BASE = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM11")
    ap.add_argument("--secs", type=float, default=5.0)
    ap.add_argument("--shift", type=int, default=12, help="32→16bit 增益定标（越小越响）")
    ap.add_argument("--sr", type=int, default=16000)
    ap.add_argument("--transcribe", action="store_true", help="采完用 SenseVoice 转写")
    args = ap.parse_args()

    pcm = bytearray()
    stats = {"frames": 0, "errors": []}

    try:
        ser = serial.Serial(args.port, 115200, timeout=0.1)
    except Exception as e:
        print(f"❌ 打开 {args.port} 失败：{e}\n   先跑 scripts/stop_daemon.bat 再测")
        sys.exit(1)

    def reader() -> None:
        buf = b""
        while not stop_flag.is_set() or ser.in_waiting:
            buf += ser.read(4096)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    frame = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    txt = line.decode("utf-8", "replace").strip()
                    if txt:
                        print(f"  [板] {txt[:150]}")
                    continue
                t = frame.get("type")
                if t == "mic_data":
                    pcm.extend(base64.b64decode(frame["data"]["pcm"]))
                    stats["frames"] += 1
                elif t in ("ack", "error", "pong", "status"):
                    print(f"  ◀ {t}: {json.dumps(frame.get('data', {}), ensure_ascii=False)[:120]}")
                    if t == "error":
                        stats["errors"].append(frame.get("data", {}).get("msg"))

    stop_flag = threading.Event()
    th = threading.Thread(target=reader, daemon=True)
    th.start()

    def send(cmd: dict) -> None:
        line = (json.dumps(cmd, ensure_ascii=False) + "\n").encode("utf-8")
        ser.write(line)
        print(f"  ▶ {cmd['type']}")

    seq = 0
    print(f"== 开始采集 {args.secs}s（sr={args.sr} shift={args.shift}），请对着 INMP441 说话 ==")
    seq += 1
    send({"v": 1, "type": "mic_start", "seq": seq,
          "data": {"sr": args.sr, "shift": args.shift}})
    time.sleep(args.secs)
    seq += 1
    send({"v": 1, "type": "mic_stop", "seq": seq})
    time.sleep(0.8)
    stop_flag.set()
    th.join(timeout=2)
    ser.close()

    if not pcm:
        print(f"❌ 没收到任何 mic_data（frames=0）{stats['errors']}——检查接线/固件版本")
        sys.exit(2)
    peak = audioop.max(bytes(pcm), 2) / 32768
    rms = audioop.rms(bytes(pcm), 2) / 32768
    print(f"== 收到 {stats['frames']} 帧 {len(pcm)} 字节 ≈ {len(pcm)/2/args.sr:.1f}s 音频")
    print(f"   峰值={peak:.3f} RMS={rms:.4f} " +
          ("✅ 电平正常" if peak > 0.2 else "⚠️ 电平偏低：调小 shift（如 10）或检查 L/R 接地"))

    out = BASE / "pc_daemon" / "logs" / "mic_test.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(args.sr)
        w.writeframes(bytes(pcm))
    print(f"   已存 {out}")

    if args.transcribe:
        sys.path.insert(0, str(BASE))
        from pc_daemon.companion import Transcriber

        cfg = json.loads((BASE / "pc_daemon" / "companion_config.json").read_text(encoding="utf-8"))
        ear = Transcriber(cfg.get("whisper_model", "small"),
                          asr_provider=cfg.get("asr_provider", "sensevoice"),
                          sensevoice_model=cfg.get("sensevoice_model", "iic/SenseVoiceSmall"))
        import numpy as np

        audio = np.frombuffer(bytes(pcm), dtype=np.int16).astype(np.float32) / 32768
        print("转写中…")
        print("📝 识别结果:", ear.transcribe(audio) or "（空）")


if __name__ == "__main__":
    main()
