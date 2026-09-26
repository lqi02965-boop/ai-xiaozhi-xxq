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


def main() -> None:
    port = find_port()
    ser = serial.Serial(port, 115200, timeout=0.2)
    print(f"已连接 {port}，等设备复位稳定…")
    time.sleep(1.0)

    seq = 0
    results = []
    seq += 1
    results.append(run_case(ser, "心跳", {"v": 1, "type": "ping", "seq": seq, "data": {}}, "pong"))
    seq += 1
    results.append(run_case(ser, "提示音指令",
                            {"v": 1, "type": "play_sound", "seq": seq,
                             "data": {"sound": "chime_success", "interrupt": True}}, "ack"))
    seq += 1
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
