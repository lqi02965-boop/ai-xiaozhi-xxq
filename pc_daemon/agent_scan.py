"""agent_scan —— 电脑上已装 AI Agent 的自动发现（纯文件系统检测，无依赖）。

维护一张已知 Agent 特征目录清单，scan() 返回检测与监视状态，
供守护进程控制端口（GUI Agent 管理器）使用。
"""
from __future__ import annotations

import os
from pathlib import Path

# 已知 Agent 特征：name / 描述 / 标志目录 / 默认日志目录 / 默认 pattern
# （event_field/event_match 等解析规则仅在接入时需要，见各条目）
KNOWN_AGENTS = [
    {"name": "zcode", "desc": "ZCode CLI", "check": "~/.zcode",
     "log_dir": "~/.zcode/cli/log", "pattern": "zcode-*.jsonl",
     "event_field": "event"},
    {"name": "workbuddy", "desc": "WorkBuddy 桌面版（Genie）", "check": "~/.workbuddy/audit-log",
     "log_dir": "~/.workbuddy/audit-log", "pattern": "2*.jsonl",
     "event_field": "eventType", "detail_field": "commandPreview",
     "event_match": {"error": ["command-safety.rejected",
                               "command-safety.system-tool-blocked"]}},
    {"name": "claude", "desc": "Claude Code", "check": "~/.claude/projects",
     "log_dir": "~/.claude/projects", "pattern": "**/*.jsonl",
     "event_field": "type"},
    {"name": "copilot", "desc": "GitHub Copilot CLI", "check": "~/.copilot"},
    {"name": "qwen", "desc": "Qwen Code", "check": "~/.qwen"},
    {"name": "gemini", "desc": "Gemini CLI", "check": "~/.gemini"},
    {"name": "cursor", "desc": "Cursor", "check": "~/.cursor"},
    {"name": "trae", "desc": "Trae", "check": "~/.trae"},
    {"name": "codebuddy", "desc": "CodeBuddy", "check": "~/.codebuddy"},
    {"name": "iflow", "desc": "iFlow CLI", "check": "~/.iflow"},
    {"name": "dsh", "desc": "DeepSeek Harness（DeepSeek 官方 Agent）", "check": "~/.dsh",
     "log_dir": "~/.dsh/sessions", "pattern": "**/*.jsonl.zstd",
     "note": "会话为 zstd 压缩 JSONL 且目前仅元数据，监视规则待其日志格式成熟后配置"},
]


def scan(monitored: dict | None = None) -> list[dict]:
    """扫描已知 Agent：返回检测状态与监视状态列表。

    monitored：守护进程当前活跃监视器 {agent 名: acfg}，用于标记监视中。
    """
    monitored = monitored or {}
    out = []
    for item in KNOWN_AGENTS:
        home = Path(os.path.expanduser(item["check"]))
        detected = home.exists()
        log_dir = item.get("log_dir", "")
        log_exists = bool(log_dir) and Path(os.path.expanduser(log_dir)).exists()
        mon = monitored.get(item["name"])
        out.append({
            "name": item["name"],
            "desc": item["desc"],
            "home": str(home),
            "log_dir": str(Path(os.path.expanduser(log_dir))) if log_dir else "",
            "pattern": item.get("pattern", "*.jsonl"),
            "event_field": item.get("event_field", "event"),
            "event_match": item.get("event_match", {}),
            "detail_field": item.get("detail_field", ""),
            "detected": detected,
            "log_ready": log_exists,
            "monitored": mon is not None,
        })
    return out
