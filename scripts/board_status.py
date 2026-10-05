# -*- coding: utf-8 -*-
"""board_status.py —— 直连板子问 status（含 oled/mic 标志）并透显板端日志。

⚠️ 守护进程占用 COM11 时先跑 scripts/stop_daemon.bat。
用法：python scripts/board_status.py [--port COM11]
"""
import argparse
import json
import threading
import time

import serial


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM11")
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, 115200, timeout=0.1)
    except Exception as e:
        print(f"打开 {args.port} 失败：{e}")
        return

    stop_flag = threading.Event()

    def reader() -> None:
        buf = b""
        while True:
            buf += ser.read(4096)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                txt = line.decode("utf-8", "replace").strip()
                if not txt:
                    continue
                try:
                    frame = json.loads(txt)
                    if frame.get("type") == "status":
                        print("板子 status:", json.dumps(frame.get("data", {}),
                                                        ensure_ascii=False))
                    else:
                        print("板子帧:", txt[:120])
                except Exception:
                    print("[板]", txt[:130])

    threading.Thread(target=reader, daemon=True).start()
    ser.write((json.dumps({"v": 1, "type": "status", "seq": 1, "data": {}})
               + "\n").encode())
    time.sleep(2)
    stop_flag.set()
    ser.close()


if __name__ == "__main__":
    main()
