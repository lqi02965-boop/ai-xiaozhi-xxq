"""test_speaker —— 喇叭单次试音（硬件调试用）。

用途：连上设备播一声"叮"（chime_success），跑一次听一次——
     调整接线/引脚后反复运行，直到听到声音。
注意：运行前先停守护进程（它占用串口）：scripts\\stop_daemon.bat
用法：python scripts/test_speaker.py
"""
import json
import sys
import time

sys.path.insert(0, r"D:\ai-xxq")
import serial
from serial.tools import list_ports


def find_port():
    for p in list_ports.comports():
        if p.vid and p.pid and (f"{p.vid:04X}", f"{p.pid:04X}") == ("303A", "1001"):
            return p.device
    raise SystemExit("❌ 没找到 ESP32（303A:1001）——检查 USB 线插的是板子『USB』口")


port = find_port()
ser = serial.Serial(port, 115200, timeout=0.2)
print(f"已连接 {port}，2 秒后播放『叮』…")
time.sleep(2)
ser.reset_input_buffer()
ser.write((json.dumps({"v": 1, "type": "play_sound", "seq": 1,
                       "data": {"sound": "chime_success",
                                "interrupt": True}}) + "\n").encode())
end = time.time() + 3
buf = b""
resp = None
while time.time() < end:
    c = ser.read(64)
    if c:
        buf += c
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        line = line.decode("utf-8", "replace").strip()
        if line.startswith("{"):
            resp = json.loads(line)
ser.close()
print("设备回执:", json.dumps(resp, ensure_ascii=False) if resp else "无")
print("→ 回执 ok=true 但没声音 = 硬件链路问题"
      "（音箱线→功放供电→SD 引脚→信号线，见排查清单）")
