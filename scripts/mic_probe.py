# -*- coding: utf-8 -*-
"""mic_probe.py —— 实测麦克风电平：录 3 秒，报 RMS/峰值，判断信号是否够。

用法：python scripts/mic_probe.py [设备号]   （默认用 companion_config.json 的 input_device）
"""
import json
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd

CFG = Path(__file__).resolve().parent.parent / "pc_daemon" / "companion_config.json"
device = None
if len(sys.argv) > 1:
    device = int(sys.argv[1])
else:
    try:
        device = json.loads(CFG.read_text(encoding="utf-8")).get("input_device")
    except Exception:
        pass

print("== 输入设备列表 ==")
print(sd.query_devices())
info = sd.query_devices(device, "input") if device is not None else sd.query_devices(None, "input")
print(f"\n== 用设备 {device}: {info['name']} ==")
print("请对着麦克风正常说一句话（3 秒）…")
audio = sd.rec(int(3 * 16000), samplerate=16000, channels=1,
               dtype="float32", device=device)
sd.wait()
audio = audio[:, 0]
rms = float(np.sqrt((audio ** 2).mean()))
peak = float(np.abs(audio).max())
print(f"RMS={rms:.4f}  峰值={peak:.4f}")
print("噪声门=0.012（RMS 低于它判『没说话』）")
if peak < 0.1:
    print("❌ 信号太弱（峰值<0.1）：模型再好也识别不准 —— Windows 麦克风加强 +20~30dB")
elif peak < 0.3:
    print("⚠️ 信号偏低（0.1~0.3）：建议开 Windows 麦加强，或调高自动增益")
else:
    print("✅ 信号电平正常（峰值>=0.3），瓶颈不在采集端")
