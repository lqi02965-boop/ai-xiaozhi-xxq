"""cdc_probe —— CDC 指令通道探针（V1-105 验收工具）。

用途：不经 PC 守护进程，直接对固件发协议帧做单元验收：
  1. ping        → 期望 pong
  2. play_sound  → 期望 ack ok=true（V1-104 前不出声属正常）
  3. status      → 期望回固件版本
  4. 坏 JSON     → 期望 error 400
  5. 未知类型    → 期望 error 401

用法：python scripts/cdc_probe.py [COM口]   # 省略则自动识别 303A:1001
"""
import json
import struct
import sys
import time

import serial
from serial.tools import list_ports

EXPECT_VID_PID = ("303A", "1001")


def find_port() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    for p in list_ports.comports():
        if p.vid and p.pid and (f"{p.vid:04X}", f"{p.pid:04X}") == EXPECT_VID_PID:
            return p.device
    raise SystemExit("未找到 ESP32-S3 CDC 口（303A:1001），确认 USB 线插的是板子『USB』口")


def read_frame(ser, timeout=2.0) -> dict | None:
    """读一行 JSON；跳过非 JSON 行（启动日志等）。"""
    end = time.time() + timeout
    buf = b""
    while time.time() < end:
        chunk = ser.read(64)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.decode("utf-8", errors="replace").strip()
                if line.startswith("{"):
                    return json.loads(line)
    return None


def run_case(ser, name: str, frame: dict | bytes, expect: str, check=None) -> bool:
    raw = frame if isinstance(frame, bytes) else (json.dumps(frame) + "\n").encode("utf-8")
    ser.reset_input_buffer()
    ser.write(raw)
    resp = read_frame(ser)
    ok = bool(resp) and resp.get("type") == expect
    if ok and check:
        ok = check(resp)
    print(f"{'✅' if ok else '❌'} {name}: → 回复 {json.dumps(resp, ensure_ascii=False) if resp else '无响应'}")
    return ok


def make_sine_pcm(seconds: float = 0.6, freq: int = 440) -> bytes:
    """生成 16k/16bit/mono 正弦 PCM（带淡出防爆音）。"""
    import math

    sr = 16000
    n = int(sr * seconds)
    out = bytearray()
    for i in range(n):
        env = min(1.0, (n - i) / (sr * 0.05))   # 末尾 50ms 淡出
        v = 0.9 * math.sin(2 * math.pi * freq * i / sr) * env
        out += struct.pack("<h", int(v * 32767))
    return bytes(out)


def audio_stream_case(ser, seq_start: int) -> bool:
    """V1-303 验收：audio_start → 原始 PCM → 期望 ack ok=true（会出 0.6 秒 440Hz 音）。"""
    pcm = make_sine_pcm(0.6)
    seq_start += 1
    ser.reset_input_buffer()
    ser.write((json.dumps({"v": 1, "type": "audio_start", "seq": seq_start,
                           "data": {"format": "pcm_16k_16bit_mono",
                                    "bytes": len(pcm), "interrupt": True}}) + "\n").encode())
    time.sleep(0.3)
    for i in range(0, len(pcm), 256):
        ser.write(pcm[i:i + 256])
        time.sleep(0.008)
    resp = read_frame(ser, timeout=4.0)
    ok = bool(resp) and resp.get("type") == "ack" and resp.get("data", {}).get("ok") is True
    print(f"{'✅' if ok else '❌'} 音频流: {len(pcm)} bytes PCM → 回复 {json.dumps(resp, ensure_ascii=False) if resp else '无响应'}")
    return ok


def main() -> None:
    port = find_port()
    ser = serial.Serial(port, 115200, timeout=0.2)
    print(f"已连接 {port}，等设备复位稳定…")
    time.sleep(2.5)

    seq = 0
    results = []
    seq += 1
    results.append(run_case(ser, "心跳", {"v": 1, "type": "ping", "seq": seq, "data": {}}, "pong"))
    seq += 1
    results.append(run_case(ser, "音效播放",
                            {"v": 1, "type": "play_sound", "seq": seq,
                             "data": {"sound": "chime_success", "interrupt": True}}, "ack"))
    results.append(audio_stream_case(ser, seq))
    seq += 2
    results.append(run_case(ser, "状态查询", {"v": 1, "type": "status", "seq": seq, "data": {}}, "status"))
    results.append(run_case(ser, "坏 JSON", b"{broken json\n", "error",
                            check=lambda r: r.get("data", {}).get("code") == 400))
    seq += 1
    results.append(run_case(ser, "未知类型",
                            {"v": 1, "type": "hello", "seq": seq, "data": {}}, "error"))

    ser.close()
    passed = sum(results)
    print(f"\n验收结果: {passed}/{len(results)} 通过")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
