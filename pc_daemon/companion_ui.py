"""companion_ui —— 云小小陪伴聊天图形界面（Tkinter，纯新增，v1 零改动）

与控制台版 companion.py 共用同一份配置（companion_config.json）与记忆
（companion_memory.json），两个版本可互换使用。

启动：python -m pc_daemon.companion_ui   或   scripts/companion_ui.bat

布局：
  ┌──────────────────────────┐
  │  聊天记录（滚动）          │
  │  …                        │
  ├──────────────────────────┤
  │ [🎤 说话] [🔇]  状态灯     │
  │ [输入框____________] [发送]│
  └──────────────────────────┘
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import scrolledtext

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))

try:
    from pc_daemon.companion import Companion, Recorder, Transcriber
    from pc_daemon import pc_player
    from pc_daemon.tts import TTSEngine, sanitize_for_tts
except ImportError:                      # 直接双击/单文件运行兜底
    sys.path.insert(0, str(BASE))
    from companion import Companion, Recorder, Transcriber
    import pc_player
    from tts import TTSEngine, sanitize_for_tts

CONFIG_PATH = BASE / "companion_config.json"
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    filename=str(BASE / "logs" / "companion_ui.log"),
                    encoding="utf-8")
log = logging.getLogger("companion_ui")


class ChatUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("云小小 · 陪伴模式 🌙")
        root.geometry("520x640")
        root.minsize(420, 520)

        self.cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.chat = Companion(self.cfg)
        self.ear = Transcriber(self.cfg.get("whisper_model", "small"),
                               vad_filter=self.cfg.get("vad_filter", False))
        self.tts = TTSEngine(self.cfg.get("tts", {}))
        self.mic = Recorder(self.cfg.get("sample_rate", 16000),
                            device=self.cfg.get("input_device"))
        self.muted = False
        self.recording = False
        self.busy = False
        self.cancel_event = threading.Event()   # 打断对话的取消令牌
        self.ui_q: "queue.Queue[tuple]" = queue.Queue()

        self._build_widgets()
        self.root.after(100, self._poll_ui)
        self._sys("💡 点「🎤 说话」按钮开始语音聊天；或直接在下方打字（也支持 Win+H 语音听写）")

    # ---------- 界面构建 ----------
    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # 顶栏：置顶按钮常驻窗口顶端右侧
        topbar = tk.Frame(self.root)
        topbar.pack(side="top", fill="x")
        self.top_btn = tk.Button(topbar, text="📌 置顶", width=7,
                                 command=self.toggle_topmost, relief="flat",
                                 fg="#555555")
        self.top_btn.pack(side="right", padx=6, pady=2)
        self.monitor_on = True
        self.mon_btn = tk.Button(topbar, text="🛡️ 监视:开", width=10,
                                 command=self.toggle_monitor, relief="flat",
                                 fg="#0a7a4a")
        self.mon_btn.pack(side="right", padx=2, pady=2)

        self.history = scrolledtext.ScrolledText(self.root, state="disabled",
                                                 wrap="word", font=("微软雅黑", 11),
                                                 selectbackground="#b5d5ff")
        self._last_reply = ""
        self.history.pack(fill="both", expand=True, **pad)
        self.history.tag_config("you", foreground="#2050a0")
        self.history.tag_config("ai", foreground="#0a7a4a")
        self.history.tag_config("sys", foreground="#888888")

        bar = tk.Frame(self.root)
        bar.pack(fill="x", **pad)
        self.voice_btn = tk.Button(bar, text="🎤 说话", font=("微软雅黑", 12),
                                   width=12, command=self.toggle_voice,
                                   bg="#e8f4ff")
        self.voice_btn.pack(side="left")
        self.stop_btn = tk.Button(bar, text="⏹ 打断", width=10,
                                  command=self.interrupt)
        self.stop_btn.pack(side="left", padx=6)
        self.mute_btn = tk.Button(bar, text="🔊", width=4,
                                  command=self.toggle_mute)
        self.mute_btn.pack(side="left")
        self.clear_btn = tk.Button(bar, text="🧹 清记忆", width=8,
                                   command=self.clear_memory)
        self.clear_btn.pack(side="right")
        self.status = tk.Label(bar, text="💡 点「🎤 说话」开始", fg="#666666")
        self.status.pack(side="right", padx=6)

        # 多行输入框（Ctrl+回车 发送）
        input_frame = tk.Frame(self.root)
        input_frame.pack(fill="both", padx=8, pady=(0, 4))
        self.entry = tk.Text(input_frame, font=("微软雅黑", 11), height=4,
                             wrap="word")
        self.entry.pack(fill="both", expand=True)
        self.entry.bind("<Control-Return>", self.send_text)
        self.entry.insert("1.0", "")   # 占位
        hint = tk.Label(self.root, text="Ctrl+回车 发送 ｜ 回车换行", fg="#999999",
                        font=("微软雅黑", 9))
        hint.pack(anchor="e", padx=10)
        input_bar = tk.Frame(self.root)
        input_bar.pack(fill="x", padx=8, pady=(0, 8))
        self.send_btn = tk.Button(input_bar, text="发送", width=10,
                                  command=self.send_text, bg="#e8f4ff")
        self.send_btn.pack(side="right")

    # ---------- 工具 ----------
    def _append(self, who: str, text: str) -> None:
        self.history.config(state="normal")
        tag = {"你": "you", "云小小": "ai", "": "sys"}.get(who, "sys")
        prefix = {"你": "你：", "云小小": "云小小：", "": ""}.get(who, "")
        self.history.insert("end", f"{prefix}{text}\n\n", tag)
        self.history.see("end")
        self.history.config(state="disabled")

    def _sys(self, text: str) -> None:
        self._append("", text)

    def _set_status(self, text: str) -> None:
        self.status.config(text=text)

    def _poll_ui(self) -> None:
        """后台线程 → UI 线程的消息泵。"""
        try:
            while True:
                item = self.ui_q.get_nowait()
                if item[0] == "chat":
                    self._append(item[1], item[2])
                elif item[0] == "status":
                    self._set_status(item[1])
                elif item[0] == "voice_btn":
                    self.voice_btn.config(text=item[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_ui)

    # ---------- Agent 监视开关（跨进程控制守护进程） ----------
    def _monitor_cmd(self, action: str):
        """向守护进程控制端口(127.0.0.1:18765)发命令；失败返回 None。"""
        import socket

        try:
            with socket.create_connection(("127.0.0.1", 18765), timeout=2) as sck:
                frame = json.dumps({"cmd": action}) + "\n"
                sck.sendall(frame.encode("utf-8"))
                data = sck.recv(1024).decode("utf-8", "replace").strip()
                if data:
                    return json.loads(data.splitlines()[0])
        except Exception:
            pass
        return None

    def toggle_monitor(self) -> None:
        resp = self._monitor_cmd("status")
        if resp is None:
            self._set_status("⚠️ 守护进程未连接，无法切换监视")
            return
        new_on = not resp.get("monitor", True)
        r2 = self._monitor_cmd("monitor_on" if new_on else "monitor_off")
        if r2 and r2.get("ok"):
            self.monitor_on = new_on
            self.mon_btn.config(text=f"🛡️ 监视:{'开' if new_on else '关'}",
                                fg="#0a7a4a" if new_on else "#a03030")
            self._sys(f"（Agent 监视已{'开启' if new_on else '关闭'}——zcode 完成与报错提醒暂停）")
        else:
            self._set_status("⚠️ 切换失败，见守护进程日志")

    # ---------- 复制功能 ----------
    def _show_ctx_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        try:
            has_sel = bool(self.history.get("sel.first", "sel.last"))
        except Exception:
            has_sel = False
        menu.add_command(label="复制选中文字" if has_sel else "复制选中文字（先拖选）",
                         command=self.copy_selection,
                         state="normal" if has_sel else "disabled")
        menu.add_command(label="复制云小小最新回复", command=self.copy_last_reply)
        menu.add_command(label="复制全部对话", command=self.copy_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def copy_selection(self) -> None:
        try:
            text = self.history.get("sel.first", "sel.last")
        except Exception:
            text = ""
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status("📋 已复制选中文字")

    def copy_last_reply(self) -> None:
        if self._last_reply:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._last_reply)
            self._set_status("📋 已复制云小小最新回复")

    def copy_all(self) -> None:
        text = self.history.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("📋 已复制全部对话")

    # ---------- 中断（打断对话） ----------
    def interrupt(self) -> None:
        """立即打断云小小：停掉当前播放、取消还没播的内容。
        旧流程持有旧取消令牌，打断后换新令牌给下一轮。"""
        old = self.cancel_event
        old.set()
        pc_player.stop_playback()
        self.cancel_event = threading.Event()
        self.busy = False

    # ---------- 语音 ----------
    def toggle_voice(self) -> None:
        # 云小小正在说话/思考 → 点 🎤 = 打断并直接开始录音（抢话）
        if self.busy:
            self.interrupt()
            self.voice_btn.config(text="⏹ 结束", bg="#ffe8e8")
            self.recording = True
            self.mic.start()
            self._set_status("🎙️ 已打断，录音中…说完点「结束」")
            return
        if not self.recording:
            self.mic.start()
            self.recording = True
            self.voice_btn.config(text="⏹ 结束", bg="#ffe8e8")
            self._set_status("🎙️ 录音中…说完点「结束」")
        else:
            self.recording = False
            self.voice_btn.config(text="🎤 说话", bg="#e8f4ff")
            audio = self.mic.stop()
            threading.Thread(target=self._voice_pipeline, args=(audio,),
                             daemon=True).start()

    def _voice_pipeline(self, audio) -> None:
        """转写 → 对话 → 播放（后台线程，UI 不卡）。"""
        ce = self.cancel_event
        if audio is None or len(audio) == 0:
            self.ui_q.put(("status", "💡 没录到内容，再试一次"))
            return
        self.ui_q.put(("status", "🧠 转写中…（首次加载模型稍慢）"))
        text = self.ear.transcribe(audio)
        if ce.is_set():
            return
        if not text:
            self.ui_q.put(("chat", "", "（没听清——靠近麦克风大声点，或打字）"))
            self.ui_q.put(("status", "💡 点「🎤 说话」开始"))
            return
        self._pipeline_text(text, ce)

    def _pipeline_text(self, text: str, ce=None) -> None:
        ce = ce or self.cancel_event
        self.busy = True
        self.ui_q.put(("chat", "你", text))
        self.ui_q.put(("status", "💭 思考中…"))
        try:
            reply = self.chat.chat(text)
        except Exception as e:
            self.ui_q.put(("chat", "", f"（网络开小差了：{e}）"))
            self.ui_q.put(("status", "💡 点「🎤 说话」开始"))
            self.busy = False
            return
        if ce.is_set():                          # 等待期间被打断 → 不播
            self.busy = False
            self.ui_q.put(("status", "💡 点「🎤 说话」开始"))
            return
        if not reply:
            self.ui_q.put(("status", "💡 点「🎤 说话」开始"))
            self.busy = False
            return
        self.ui_q.put(("chat", "云小小", reply))
        if self.muted:
            self.ui_q.put(("status", "💡 静音中，点🔊恢复"))
            self.busy = False
            return
        self.ui_q.put(("status", "🔊 播放中…（点🎤可打断）"))
        pcm = self.tts.synthesize(sanitize_for_tts(reply))
        if pcm and not ce.is_set():
            pc_player.play_pcm(pcm)
        self.ui_q.put(("status", "💡 点「🎤 说话」开始"))
        self.busy = False

    # ---------- 文字 ----------
    def send_text(self, *_event) -> None:
        text = self.entry.get("1.0", "end-1c").strip()
        if not text:
            return
        self.entry.delete("1.0", "end")
        if self.busy:
            self.interrupt()   # 新消息打断当前对话
        if text == "/clear":
            self.clear_memory()
            return
        if text == "/mute":
            self.toggle_mute()
            return
        threading.Thread(target=self._pipeline_text, args=(text,),
                         daemon=True).start()

    def toggle_mute(self) -> None:
        self.muted = not self.muted
        self.mute_btn.config(text="🔇" if self.muted else "🔊")

    def stop_playback(self) -> None:
        """立即打断当前语音播放。"""
        pc_player.stop_playback()
        self._set_status("⏹ 已停止播放")

    def toggle_topmost(self) -> None:
        """窗口置顶开关。"""
        self.topmost = not getattr(self, "topmost", False)
        self.root.attributes("-topmost", self.topmost)
        self.top_btn.config(text="📌 已置顶" if self.topmost else "📌 置顶",
                            bg="#fff7d6" if self.topmost else "SystemButtonFace")

    def clear_memory(self) -> None:
        self.chat.clear()
        self._sys("（记忆已清空，我们是新朋友啦）")


def main() -> None:
    try:
        root = tk.Tk()
        ChatUI(root)
        root.mainloop()
    except Exception as e:
        log.exception("界面启动失败")
        try:
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("云小小", "启动失败：" + str(e) +
                                 "\n详见 logs/companion_ui.log")
        except Exception:
            pass


if __name__ == "__main__":
    main()
