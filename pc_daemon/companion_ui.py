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
from tkinter import scrolledtext, ttk, messagebox

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
ICON_ICO = BASE / "assets" / "yunxxq.ico"      # 窗口/任务栏图标（scripts/gen_app_icon.py 生成）
ICON_PNG = BASE / "assets" / "yunxxq.png"


def apply_app_icon(win) -> None:
    """窗口标题栏/任务栏换成云小小柑橘图标（ico 优先，png 兜底）。"""
    try:
        win.iconbitmap(str(ICON_ICO))
        return
    except Exception:
        pass
    try:
        photo = tk.PhotoImage(file=str(ICON_PNG))
        win._app_icon_ref = photo               # 持引用防 GC
        win.iconphoto(True, photo)
    except Exception:
        pass


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
        apply_app_icon(root)

        self.cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.chat = Companion(self.cfg)
        self.ear = Transcriber(self.cfg.get("whisper_model", "small"),
                               vad_filter=self.cfg.get("vad_filter", False),
                               asr_provider=self.cfg.get("asr_provider",
                                                         "whisper"),
                               sensevoice_model=self.cfg.get(
                                   "sensevoice_model",
                                   "iic/SenseVoiceSmall"))
        self.tts = TTSEngine(self.cfg.get("tts", {}))
        self.mic = Recorder(self.cfg.get("sample_rate", 16000),
                            device=self.cfg.get("input_device"))
        self.muted = False
        self.recording = False
        self.busy = False
        self.audio_out = None          # "pc" | "device"（启动后从守护进程查询）
        self.cancel_event = threading.Event()   # 打断对话的取消令牌
        self.ui_q: "queue.Queue[tuple]" = queue.Queue()

        self._build_widgets()
        self.root.after(600, self._sync_output_button)   # 守护进程就绪后同步显示
        self.root.after(100, self._poll_ui)
        self._sys("💡 点「🎤 说话」按钮开始语音聊天；或直接在下方打字（也支持 Win+H 语音听写）")

    # ---------- 界面构建 ----------
    # 小清新柑橘配色
    C_BG = "#FFFDF5"        # 奶白背景
    C_LEMON = "#FFF59D"     # 柠檬黄
    C_LEMON_DEEP = "#F9A825"
    C_POMELO = "#FFB74D"    # 柚子橙
    C_POMELO_DEEP = "#F57C00"
    C_LEAF = "#558B2F"      # 叶绿
    C_TEXT = "#37474F"

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}
        self.root.configure(bg=self.C_BG)

        # 顶栏：功能按钮（扁平小清新）
        topbar = tk.Frame(self.root, bg=self.C_BG)
        topbar.pack(side="top", fill="x")
        self.top_btn = tk.Button(topbar, text="📌 置顶", width=7,
                                 command=self.toggle_topmost, relief="flat",
                                 bd=0, bg=self.C_BG, fg=self.C_LEAF,
                                 activebackground=self.C_LEMON,
                                 font=("微软雅黑", 10))
        self.top_btn.pack(side="right", padx=6, pady=2)
        self.agents_btn = tk.Button(topbar, text="🤖 Agent", width=8,
                                    command=self.open_agents_window,
                                    relief="flat", bd=0, bg=self.C_BG,
                                    fg=self.C_LEAF,
                                    activebackground=self.C_LEMON,
                                    font=("微软雅黑", 10))
        self.agents_btn.pack(side="right", padx=2, pady=2)
        self.monitor_on = True
        self.mon_btn = tk.Button(topbar, text="🛡️ 监视:开", width=10,
                                 command=self.toggle_monitor, relief="flat",
                                 bd=0, bg=self.C_BG, fg=self.C_LEAF,
                                 activebackground=self.C_LEMON,
                                 font=("微软雅黑", 10))
        self.mon_btn.pack(side="right", padx=2, pady=2)

        # 聊天记录（柠檬奶白纸面）
        self.history = scrolledtext.ScrolledText(
            self.root, state="disabled", wrap="word", font=("微软雅黑", 11),
            bg="#FFFEF7", fg=self.C_TEXT, relief="flat", bd=0,
            highlightthickness=1, highlightbackground="#F0EBC8",
            selectbackground=self.C_LEMON, padx=12, pady=10,
            spacing1=4, spacing3=6)
        self._last_reply = ""
        # pack 在 _build_widgets 末尾：底部控件先占位，聊天区最后填剩余
        self.history.tag_config("you", foreground="#E65100")     # 柚子橙
        self.history.tag_config("ai", foreground="#33691E")      # 柠檬叶绿
        self.history.tag_config("sys", foreground="#B0A890")

        bar = tk.Frame(self.root, bg=self.C_BG)
        bar.pack(fill="x", **pad)
        self.voice_btn = tk.Button(bar, text="🍊 说话", font=("微软雅黑", 12),
                                   width=12, command=self.toggle_voice,
                                   bg=self.C_POMELO, fg="#5D4037",
                                   activebackground=self.C_LEMON,
                                   activeforeground="#5D4037",
                                   relief="flat", bd=0, cursor="hand2")
        self.voice_btn.pack(side="left", ipady=4)
        self.stop_btn = tk.Button(bar, text="⏹ 打断", width=10,
                                  command=self.interrupt, relief="flat", bd=0,
                                  bg="#FFF3E0", fg="#8D6E63",
                                  activebackground=self.C_LEMON, cursor="hand2")
        self.stop_btn.pack(side="left", padx=6, ipady=4)
        self.mute_btn = tk.Button(bar, text="🔊", width=4, relief="flat",
                                  bd=0, bg=self.C_BG, fg=self.C_LEAF,
                                  activebackground=self.C_LEMON, cursor="hand2",
                                  font=("微软雅黑", 11))
        self.mute_btn.pack(side="left")
        self.clear_btn = tk.Button(bar, text="🧹 清记忆", width=8,
                                   command=self.clear_memory, relief="flat",
                                   bd=0, bg=self.C_BG, fg="#8D6E63",
                                   activebackground=self.C_LEMON,
                                   font=("微软雅黑", 10))
        self.clear_btn.pack(side="right")
        self.status = tk.Label(bar, text="🍋 点「🍊 说话」开始", fg="#9E9D24",
                               bg=self.C_BG, font=("微软雅黑", 10))
        self.status.pack(side="right", padx=6)

        # 多行输入框（Ctrl+回车 发送）——先打包底部控件，保证可见
        input_frame = tk.Frame(self.root, bg=self.C_BG)
        input_frame.pack(side="bottom", fill="x", padx=8, pady=(0, 4))
        self.entry = tk.Text(input_frame, font=("微软雅黑", 11), height=4,
                             wrap="word", bg="#FFFEF7", fg=self.C_TEXT,
                             relief="flat", bd=0, highlightthickness=1,
                             highlightbackground="#F0EBC8", padx=10, pady=8)
        self.entry.pack(fill="both", expand=True)
        self.entry.bind("<Control-Return>", self.send_text)
        hint = tk.Label(self.root, text="Ctrl+回车 发送 ｜ 回车换行 🍋",
                        fg="#C0C0A0", bg=self.C_BG, font=("微软雅黑", 9))
        hint.pack(side="bottom", anchor="e", padx=10)
        input_bar = tk.Frame(self.root, bg=self.C_BG)
        input_bar.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        self.send_btn = tk.Button(input_bar, text="🍋 发送", width=10,
                                  command=self.send_text, bg=self.C_POMELO,
                                  fg="#5D4037", activebackground=self.C_LEMON,
                                  relief="flat", bd=0, cursor="hand2",
                                  font=("微软雅黑", 11))
        self.send_btn.pack(side="right", ipady=3)
        self.out_btn = tk.Button(input_bar, text="🔊 输出:查询中", width=14,
                                 command=self.cycle_audio_output,
                                 relief="flat", bd=0, bg="#FFF8E1",
                                 fg="#8D6E63", cursor="hand2",
                                 font=("微软雅黑", 10))
        self.out_btn.pack(side="left")
        # 聊天区最后打包：只吃剩余空间，任何窗口尺寸下底部控件都可见
        self.history.pack(fill="both", expand=True, **pad)

    # ---------- 声音输出切换 ----------
    def _query_audio_output(self) -> str | None:
        resp = self._monitor_cmd("status")
        if resp and resp.get("ok"):
            return resp.get("audio_output", "device")
        return None

    def cycle_audio_output(self) -> None:
        """🖥️ 电脑音箱 ↔ 🔊 小智喇叭 一键切换（写守护进程配置并即时生效）。"""
        cur = self._query_audio_output() or "device"
        new = "pc" if cur == "device" else "device"
        resp = self._monitor_cmd("set_audio_output", mode=new)
        if resp and resp.get("ok"):
            self.audio_out = new
            label = "🖥️ 电脑音箱" if new == "pc" else "🔊 小智喇叭"
            self.out_btn.config(text=f"🔊 输出:{label}")
            self._sys(f"（声音输出已切换：{label}）")
        else:
            self._set_status("⚠️ 切换失败：守护进程未连接")

    def _sync_output_button(self) -> None:
        mode = self._query_audio_output()
        if mode is None:
            return
        self.audio_out = mode
        label = "🖥️ 电脑音箱" if mode == "pc" else "🔊 小智喇叭"
        self.out_btn.config(text=f"🔊 输出:{label}")

    # ---------- 工具 ----------
    def _append(self, who: str, text: str) -> None:
        self.history.config(state="normal")
        tag = {"你": "you", "云小小": "ai", "": "sys"}.get(who, "sys")
        prefix = {"你": "🍊 你：", "云小小": "🍋 云小小：", "": ""}.get(who, "")
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
    def _monitor_cmd(self, action: str, **extra):
        """向守护进程控制端口(127.0.0.1:18765)发命令；失败返回 None。"""
        import socket

        try:
            with socket.create_connection(("127.0.0.1", 18765), timeout=3) as sck:
                payload = {"cmd": action, **extra}
                frame = json.dumps(payload, ensure_ascii=False) + "\n"
                sck.sendall(frame.encode("utf-8"))
                data = sck.recv(65536).decode("utf-8", "replace").strip()
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
                                fg=self.C_LEAF if new_on else "#BF8F30")
            self._sys(f"（Agent 监视已{'开启' if new_on else '关闭'}——zcode 完成与报错提醒暂停）")
        else:
            self._set_status("⚠️ 切换失败，见守护进程日志")

    # ---------- Agent 管理器窗口 ----------
    def open_agents_window(self) -> None:
        """弹出 Agent 管理器：自动扫描 + 列表 + 启用/停用/添加。"""
        win = getattr(self, "_agents_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            return
        self._agents_win = AgentsWindow(self.root, self)

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
            self.voice_btn.config(text="⏹ 结束", bg="#FFE0B2")
            self.recording = True
            self.mic.start()
            self._set_status("🎙️ 已打断，录音中…说完点「结束」")
            return
        if not self.recording:
            self.mic.start()
            self.recording = True
            self.voice_btn.config(text="⏹ 结束", bg="#FFE0B2")
            self._set_status("🎙️ 录音中…说完点「结束」")
        else:
            self.recording = False
            self.voice_btn.config(text="🍊 说话", bg=self.C_POMELO)
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
            mode = self.audio_out or self._query_audio_output() or "pc"
            if mode == "pc":
                pc_player.play_pcm(pcm)             # 电脑音箱
            else:
                import base64
                self._monitor_cmd("play_stream", pcm=base64.b64encode(pcm).decode())
                self._set_status("🔊 小智播放中…")
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
        self.mute_btn.config(text="🍋" if self.muted else "🔊")

    def stop_playback(self) -> None:
        """立即打断当前语音播放。"""
        pc_player.stop_playback()
        self._set_status("⏹ 已停止播放")

    def toggle_topmost(self) -> None:
        """窗口置顶开关。"""
        self.topmost = not getattr(self, "topmost", False)
        self.root.attributes("-topmost", self.topmost)
        self.top_btn.config(text="📌 已置顶" if self.topmost else "📌 置顶",
                            bg=self.C_LEMON if self.topmost else self.C_BG)

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


class AgentsWindow:
    """Agent 管理器窗口：自动扫描 + 列表 + 启用/停用/添加自定义。"""

    def __init__(self, root: tk.Tk, ui) -> None:
        self.ui = ui
        self.win = tk.Toplevel(root)
        self.win.title("🍋 Agent 管理器")
        self.win.geometry("640x430")
        self.win.minsize(560, 380)
        self.win.configure(bg="#FFFDF5")
        apply_app_icon(self.win)
        self.rows = {}

        head = tk.Frame(self.win, bg="#FFFDF5")
        head.pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(head, text="自动扫描电脑上已安装的 AI Agent，选中后可启用监视 🍋",
                 fg="#9E9D24", bg="#FFFDF5").pack(side="left")

        bar = tk.Frame(self.win, bg="#FFFDF5")
        bar.pack(fill="x", padx=8, pady=2)
        tk.Button(bar, text="🔄 重新扫描", width=12,
                  command=self.refresh).pack(side="left")
        tk.Button(bar, text="✅ 启用监视", width=12,
                  command=self.enable_selected).pack(side="left", padx=6)
        tk.Button(bar, text="⏹ 停用", width=8,
                  command=self.disable_selected).pack(side="left")
        tk.Button(bar, text="➕ 添加自定义", width=12,
                  command=self.add_custom).pack(side="right")

        cols = ("name", "desc", "detected", "log_ready", "monitored")
        style = ttk.Style(self.win)
        style.configure("Lemon.Treeview", rowheight=28, background="#FFFEF7",
                        fieldbackground="#FFFEF7", foreground="#37474F")
        style.configure("Lemon.Treeview.Heading", background="#FFF59D",
                        foreground="#5D4037")
        self.tree = ttk.Treeview(self.win, columns=cols, show="headings", height=12,
                                 style="Lemon.Treeview")
        for cid, text, w in (("name", "Agent", 90), ("desc", "说明", 190),
                             ("detected", "已安装", 60), ("log_ready", "日志就绪", 70),
                             ("monitored", "监视中", 60)):
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=w, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)

        self.status = tk.Label(self.win, text="🍋 选中一行后可启用/停用；"
                               "“➕添加自定义”可接入任意有日志的 Agent",
                               fg="#9E9D24", bg="#FFFDF5")
        self.status.pack(anchor="w", padx=8, pady=(0, 6))
        self.refresh()

    def _set_status(self, text: str, ok: bool = True) -> None:
        self.status.config(text=text, fg="#0a7a4a" if ok else "#a03030")

    def refresh(self) -> None:
        resp = self.ui._monitor_cmd("scan_agents")
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        if not resp or not resp.get("ok"):
            self._set_status("❌ 扫描失败：守护进程未连接", ok=False)
            return
        for a in resp.get("agents", []):
            name = a["name"]
            self.rows[name] = a
            self.tree.insert("", "end", iid=name, values=(
                name, a.get("desc", ""), "✅" if a.get("detected") else "—",
                "✅" if a.get("log_ready") else "—",
                "✅ 监视中" if a.get("monitored") else "—"))

    def _selected(self):
        sel = self.tree.selection()
        return self.rows.get(sel[0]) if sel else None

    def enable_selected(self) -> None:
        row = self._selected()
        if not row:
            self._set_status("先选中一行 Agent", ok=False)
            return
        if not row.get("log_ready"):
            self._set_status(f"{row['name']} 的日志目录不存在，无法启用", ok=False)
            return
        payload = {k: row.get(k) for k in ("name", "log_dir", "pattern",
                                           "event_field", "event_match",
                                           "detail_field")}
        resp = self.ui._monitor_cmd("add_agent", agent=payload)
        if resp and resp.get("ok"):
            self.refresh()
            self._set_status(f"✅ 已启用 {row['name']} 监视")
        else:
            self._set_status("启用失败（守护进程未连接或已存在）", ok=False)

    def disable_selected(self) -> None:
        row = self._selected()
        if not row:
            self._set_status("先选中一行 Agent", ok=False)
            return
        resp = self.ui._monitor_cmd("remove_agent", name=row["name"])
        if resp and resp.get("ok"):
            self.refresh()
            self._set_status(f"⏹ 已停用 {row['name']} 监视")
        else:
            self._set_status("停用失败", ok=False)

    def add_custom(self) -> None:
        form = tk.Toplevel(self.win)
        form.title("添加自定义 Agent")
        form.geometry("460x180")
        form.grab_set()
        tk.Label(form, text="名称：").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        e_name = tk.Entry(form, width=34)
        e_name.grid(row=0, column=1, pady=6)
        tk.Label(form, text="日志目录：").grid(row=1, column=0, sticky="e", padx=6)
        e_dir = tk.Entry(form, width=34)
        e_dir.grid(row=1, column=1, pady=6)
        tk.Label(form, text="文件匹配：").grid(row=2, column=0, sticky="e", padx=6)
        e_pat = tk.Entry(form, width=34)
        e_pat.insert(0, "*.jsonl")
        e_pat.grid(row=2, column=1, pady=6)

        def submit() -> None:
            name, log_dir = e_name.get().strip(), e_dir.get().strip()
            if not name or not log_dir:
                messagebox.showwarning("云小小", "名称和日志目录都要填", parent=form)
                return
            resp = self.ui._monitor_cmd("add_agent", agent={
                "name": name, "log_dir": log_dir,
                "log_pattern": e_pat.get().strip() or "*.jsonl"})
            form.destroy()
            if resp and resp.get("ok"):
                self.refresh()
                self._set_status(f"✅ 已添加自定义 Agent：{name}")
            else:
                self._set_status("添加失败（名称重复或守护进程未连接）", ok=False)

        tk.Button(form, text="添加", width=10, command=submit).grid(
            row=3, column=1, sticky="w", padx=6, pady=10)


if __name__ == "__main__":
    main()
