"""AgentsWindow —— Agent 管理器窗口（扫描/列表/启用/停用/添加自定义）。

数据来源：守护进程控制端口 scan_agents（agent_scan.py 的已知特征扫描），
启用/停用/添加通过 add_agent / agent_switch / remove_agent 命令实时生效并持久化。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox


class AgentsWindow:
    def __init__(self, root: tk.Tk, ui) -> None:
        self.ui = ui
        self.win = tk.Toplevel(root)
        self.win.title("🤖 Agent 管理器")
        self.win.geometry("640x430")
        self.win.minsize(560, 380)
        self.rows = {}                     # name -> 扫描返回的完整配置

        head = tk.Frame(self.win)
        head.pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(head, text="自动扫描电脑上已安装的 AI Agent，勾选启用后小智会监视它的状态",
                 fg="#666666").pack(side="left")

        bar = tk.Frame(self.win)
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
        self.tree = ttk.Treeview(self.win, columns=cols, show="headings", height=12)
        for cid, text, w in (("name", "Agent", 90), ("desc", "说明", 190),
                             ("detected", "已安装", 60), ("log_ready", "日志就绪", 70),
                             ("monitored", "监视中", 60)):
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=w, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)

        self.status = tk.Label(self.win, text="💡 选中一行后可启用/停用；"
                               "“➕添加自定义”可接入任意有日志的 Agent", fg="#666666")
        self.status.pack(anchor="w", padx=8, pady=(0, 6))

        self.refresh()

    # ---------- 工具 ----------
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

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        return self.rows.get(sel[0]) if sel else None

    # ---------- 操作 ----------
    def enable_selected(self) -> None:
        row = self._selected()
        if not row:
            self._set_status("先选中一行 Agent", ok=False)
            return
        if not row.get("log_ready"):
            self._set_status(f"❌ {row['name']} 的日志目录不存在，无法启用", ok=False)
            return
        payload = {"name": row["name"], "log_dir": row["log_dir"],
                   "log_pattern": row.get("pattern", "*.jsonl"),
                   "event_field": row.get("event_field", "event"),
                   "event_match": row.get("event_match", {}),
                   "detail_field": row.get("detail_field", "")}
        resp = self.ui._monitor_cmd("add_agent", agent=payload)
        if resp and resp.get("ok"):
            self.refresh()
            self._set_status(f"✅ 已启用 {row['name']} 监视")
        else:
            self._set_status("❌ 启用失败（守护进程未连接或已存在）", ok=False)

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
            self._set_status("❌ 停用失败", ok=False)

    def add_custom(self) -> None:
        """弹窗添加自定义 Agent（名称 + 日志目录 + 文件匹配）。"""
        form = tk.Toplevel(self.win)
        form.title("添加自定义 Agent")
        form.geometry("460x180")
        form.grab_set()

        tk.Label(form, text="名称：").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        e_name = tk.Entry(form, width=34); e_name.grid(row=0, column=1, pady=6)
        tk.Label(form, text="日志目录：").grid(row=1, column=0, sticky="e", padx=6)
        e_dir = tk.Entry(form, width=34); e_dir.grid(row=1, column=1, pady=6)
        tk.Label(form, text="文件匹配：").grid(row=2, column=0, sticky="e", padx=6)
        e_pat = tk.Entry(form, width=34); e_pat.insert(0, "*.jsonl")
        e_pat.grid(row=2, column=1, pady=6)

        def submit() -> None:
            name, log_dir = e_name.get().strip(), e_dir.get().strip()
            if not name or not log_dir:
                messagebox.showwarning("小智", "名称和日志目录都要填", parent=form)
                return
            resp = self.ui._monitor_cmd("add_agent", agent={
                "name": name, "log_dir": log_dir,
                "log_pattern": e_pat.get().strip() or "*.jsonl"})
            form.destroy()
            if resp and resp.get("ok"):
                self.refresh()
                self._set_status(f"✅ 已添加自定义 Agent：{name}")
            else:
                self._set_status("❌ 添加失败（名称重复或守护进程未连接）", ok=False)

        tk.Button(form, text="添加", width=10, command=submit).grid(
            row=3, column=1, sticky="w", padx=6, pady=10)
