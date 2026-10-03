"""asr_switch_test —— 识别引擎 A/B 对比测试（SenseVoice vs whisper）。

用途：同一段录音分别用两个引擎转写，对比耗时与文本质量，
     验证切换 SenseVoice 后的精度提升（用户反馈 whisper 精度低）。
用法：python scripts/asr_switch_test.py   # 运行后 3 秒开始录音 6 秒
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\ai-xxq")
import json

import sounddevice as sd

from pc_daemon.companion import Recorder

cfg = json.loads((Path(__file__).resolve().parent.parent / "pc_daemon" /
                  "companion_config.json").read_text(encoding="utf-8"))

print("🎙️ 3 秒后录音 6 秒，请清晰说：沈阳今天的天气怎么样", flush=True)
for i in (3, 2, 1):
    print(f"  {i}…", flush=True)
    time.sleep(1)
print("🔴 录音中…", flush=True)
mic = Recorder(cfg.get("sample_rate", 16000), device=cfg.get("input_device"))
mic.start()
time.sleep(6)
audio = mic.stop()
print("录音完成", flush=True)

# 引擎 A：SenseVoice（FunASR）
print("\n--- A: SenseVoice ---", flush=True)
t0 = time.time()
from funasr import AutoModel

model = AutoModel(model="iic/SenseVoiceSmall", device="cpu", disable_update=True)
t_load = time.time() - t0
t1 = time.time()
res = model.generate(input=audio, language="zh", use_itn=True)
t_sv = time.time() - t1
import re as _re

text_sv = _re.sub(r"<\|[^|]*\|>", "",
                  (res[0]["text"] if res else "")).strip()
print(f"加载 {t_load:.1f}s | 转写 {t_sv:.2f}s | 结果: {text_sv!r}", flush=True)

# 引擎 B：whisper small（现行）
print("\n--- B: whisper small（现行对照）---", flush=True)
t0 = time.time()
from pc_daemon.companion import Transcriber

ear = Transcriber(cfg.get("whisper_model", "small"),
                  vad_filter=cfg.get("vad_filter", False),
                  initial_prompt=cfg.get("initial_prompt", ""),
                  beam_size=cfg.get("beam_size", 3))
text_w = ear.transcribe(audio)
t_w = time.time() - t0
print(f"转写 {t_w:.2f}s | 结果: {text_w!r}", flush=True)

print("\n=== 对比结论 ===")
print(f"SenseVoice: {t_sv:.2f}s  |  whisper: {t_w:.2f}s")
print("（耗时含首次加载；稳态对比请看第二次起）")
