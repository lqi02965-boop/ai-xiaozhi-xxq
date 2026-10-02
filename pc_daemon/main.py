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
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .agent_monitor import AgentMonitor, ApprovalWatcher
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


def acquire_lock(lock_path: Path) -> bool:
    """单实例保护：daemon.lock 存在即认为已有实例在跑（停止脚本会清理）。"""
    if lock_path.exists():
        print(f"已有实例运行（{lock_path}），本次退出。"
              f"如确认没有实例，删除该文件后重试。")
        return False
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _control_port_loop(cfg, monitors, approval, stop: threading.Event) -> None:
    """本地控制端口（127.0.0.1:18765）：GUI 可远程开关 Agent 监视。
    仅监听回环地址，不暴露到网络。"""
    import socket

    port = cfg.get("control_port", 18765)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", port))
    except Exception:
        log.exception("控制端口 %s 绑定失败，远程开关停用", port)
        return
    srv.listen(4)
    srv.settimeout(1.0)
    log.info("控制端口就绪: 127.0.0.1:%s", port)
    while not stop.is_set():
        try:
            conn, _ = srv.accept()
        except (socket.timeout, OSError):
            continue
        with conn:
            try:
                conn.settimeout(2.0)
                data = conn.recv(4096).decode("utf-8", "replace").strip()
                cmd = json.loads(data.splitlines()[0]) if data else {}
            except Exception:
                continue
            action = cmd.get("cmd", "")
            on_expr = (action == "monitor_on" if action != "monitor_toggle"
                       else not all(m.enabled for m in monitors))
            if action in ("monitor_on", "monitor_off", "monitor_toggle"):
                on = on_expr
                for m in monitors:
                    m.enabled = on
                if approval:
                    approval.enabled = on
                log.info("Agent 监视已%s", "开启" if on else "关闭")
                try:
                    resp = json.dumps({"ok": True, "monitor": on}) + "\n"
                    conn.sendall(resp.encode("utf-8"))
                except Exception:
                    pass
            elif action == "status":
                try:
                    resp = json.dumps({"ok": True,
                                       "monitor": all(m.enabled for m in monitors)
                                       if monitors else False}) + "\n"
                    conn.sendall(resp.encode("utf-8"))
                except Exception:
                    pass
    srv.close()


def _briefing_loop(cfg, tts, bridge, stop: threading.Event) -> None:
    """每日定时播报（智能体主动行为）：天气 + 今日新闻 → 语音。"""
    bcfg = cfg.get("briefing", {})
    if not bcfg.get("enabled", True):
        return
    from datetime import datetime
    try:
        hh, mm = (bcfg.get("time") or "07:30").split(":")[:2]
        hh, mm = int(hh), int(mm)
    except Exception:
        hh, mm = 7, 30
    state_path = Path(__file__).parent / "briefing_state.json"
    last_done = ""
    try:
        last_done = state_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    try:
        from .companion import SearchSkill, WeatherSkill
    except Exception:
        log.exception("播报技能导入失败，定时播报停用")
        return
    weather = WeatherSkill(cfg)
    search = SearchSkill(cfg)
    log.info("定时播报已启用: 每天 %02d:%02d", hh, mm)

    while not stop.is_set():
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.hour == hh and now.minute >= mm and last_done != today:
            last_done = today
            state_path.write_text(today, encoding="utf-8")
            try:
                weekday = "一二三四五六日"[now.weekday()]
                parts = [f"早上好～今天是{now.strftime('%m月%d日')}星期{weekday}。"]
                w = weather.get()
                if w:
                    parts.append("天气：" + w + "。")
                news = search.search("今日新闻 热点")
                if news:
                    heads = "；".join(n["title"] for n in news[:3])
                    parts.append("今日新闻速览：" + heads + "。")
                text = " ".join(parts)
                log.info("定时播报: %s", text[:100])
                pcm = tts.synthesize(text)
                if pcm:
                    if cfg.get("audio_output") == "device":
                        bridge.send_audio_stream(pcm)
                    else:
                        pc_player.play_pcm(pcm)
            except Exception:
                log.exception("定时播报失败")
        stop.wait(15)


def main() -> None:
    ap = argparse.ArgumentParser(description="小智 v1 PC 守护进程")
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.json"))
    ap.add_argument("--dry-run", action="store_true", help="不打开串口，帧只打印")
    ap.add_argument("--demo", action="store_true", help="注入假事件测试链路")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(Path(args.config))
    lock_path = Path(__file__).parent / "daemon.lock"
    if not acquire_lock(lock_path):
        sys.exit(2)
    bus = EventBus()
    engine = PromptEngine(cfg.get("llm", []),
                          persona=cfg.get("persona", "语气可爱俏皮"),
                          max_chars=cfg.get("max_chars", 20),
                          library_path=cfg.get("prompt_library", ""),
                          use_llm=cfg.get("llm_for_phrases", False))
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
    approval = None
    if cfg.get("approval_watch", {}).get("enabled", True):
        approval = ApprovalWatcher(cfg.get("approval_watch", {}), bus)
        approval.start()
    t = threading.Thread(target=worker, daemon=True,
                         args=(bus, engine, tts, bridge, cfg["sounds"],
                               cfg.get("audio_output", "device"), stop))
    t.start()
    threading.Thread(target=_briefing_loop, daemon=True,
                     args=(cfg, tts, bridge, stop)).start()
    threading.Thread(target=_control_port_loop, daemon=True,
                     args=(cfg, monitors, approval, stop)).start()
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
            sleep(20)   # 给 DeepSeek+TTS+播放 留足时间，避免进程提前退出打断播放
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
        if approval:
            approval.stop()
        bridge.stop()
        bus.close()
        log.info("守护进程退出")
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
