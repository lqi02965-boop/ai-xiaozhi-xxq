"""守护进程入口：把监听、播报词、串口桥串成闭环（V1-207）。

用法：
  python -m pc_daemon.main --dry-run      # 无硬件自测：真实监听，指令只打印
  python -m pc_daemon.main --demo         # 注入假事件，测试 播报词→下发 链路
  python -m pc_daemon.main                # 正式值守（需接设备）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .agent_monitor import AgentMonitor
from .events import SKIP, EventBus
from .prompt_engine import PromptEngine
from .serial_bridge import SerialBridge
from .tts import TTSEngine
from . import pc_player

log = logging.getLogger("main")


def setup_logging(verbose: bool) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
                            datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    file_h = RotatingFileHandler(log_dir / "daemon.log", maxBytes=1_000_000,
                                 backupCount=2, encoding="utf-8")
    file_h.setFormatter(fmt)
    root.addHandler(file_h)


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_frame(kind: str, text: str, sounds: dict) -> dict:
    return {
        "v": 1,
        "type": "play_sound",
        "data": {"sound": sounds.get(kind, "chime_notice"),
                 "interrupt": True,
                 "text": text},  # v1 固件暂不播 text，留字段给 V1-M3 音频流
    }


def worker(bus: EventBus, engine: PromptEngine, tts, bridge: SerialBridge,
           sounds: dict, output_mode: str, stop: threading.Event) -> None:
    """事件循环：事件 → 播报词 → 出声（PC 本机 / 小智设备，按配置切换）。"""
    while not stop.is_set():
        item = bus.get(timeout=1.0)
        if item is SKIP:
            continue
        if item is None:
            break
        text = engine.gen(item)
        pcm = tts.synthesize(text) if tts else None
        if output_mode == "pc":
            # 过渡模式：声音从 PC 音箱出（功放焊好后改 audio_output=device）
            if pcm and pc_player.play_pcm(pcm):
                log.info("播报(PC音箱) >> %s", text)
                continue
            log.info("PC 播放失败，降级提示音指令")
        if pcm:
            log.info("播报(语音流→小智) >> %s", text)
            bridge.send_audio_stream(pcm)
        else:
            log.info("播报(提示音) >> %s（%s）", text, sounds.get(item.kind))
            bridge.send({"v": 1, "type": "play_sound", "data": {
                "sound": sounds.get(item.kind, "chime_notice"),
                "interrupt": True, "text": text}})


def main() -> None:
    ap = argparse.ArgumentParser(description="小智 v1 PC 守护进程")
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.json"))
    ap.add_argument("--dry-run", action="store_true", help="不打开串口，帧只打印")
    ap.add_argument("--demo", action="store_true", help="注入假事件测试链路")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(Path(args.config))
    bus = EventBus()
    engine = PromptEngine(cfg["deepseek"])
    tts = TTSEngine(cfg.get("tts", {})) if cfg.get("tts", {}).get("enabled", True) else None
    bridge = SerialBridge(cfg["serial"], dry_run=args.dry_run or args.demo)

    monitors = []
    for name, acfg in cfg.get("agents", {}).items():
        if not acfg.get("log_dir"):
            log.info("跳过未配置的 agent: %s", name)
            continue
        m = AgentMonitor(name, acfg["log_dir"], acfg["log_pattern"], bus,
                         poll_interval=cfg.get("poll_interval_sec", 2.0),
                         cooldown_sec=cfg.get("cooldown_sec", 30))
        monitors.append(m)

    stop = threading.Event()
    bridge.start()
    for m in monitors:
        m.start()
    t = threading.Thread(target=worker, daemon=True,
                         args=(bus, engine, tts, bridge, cfg["sounds"],
                               cfg.get("audio_output", "device"), stop))
    t.start()
    log.info("守护进程启动（dry-run=%s demo=%s）", args.dry_run, args.demo)

    try:
        if args.demo:
            from time import sleep
            from .events import AgentEvent

            sleep(1)
            log.info("== 注入演示事件 ==")
            bus.put(AgentEvent(kind="done", agent="demo", session_id="demo-1",
                               detail="模拟任务完成"))
            sleep(3)
            bus.put(AgentEvent(kind="error", agent="demo", session_id="demo-1",
                               detail="模拟任务报错"))
            sleep(2)
        else:
            while True:
                sys.stdout.flush()
                sleep_for = 3600
                stop.wait(sleep_for)  # 值守；Ctrl+C 退出
    except KeyboardInterrupt:
        log.info("收到退出信号")
    finally:
        stop.set()
        for m in monitors:
            m.stop()
        bridge.stop()
        bus.close()
        log.info("守护进程退出")


if __name__ == "__main__":
    main()
