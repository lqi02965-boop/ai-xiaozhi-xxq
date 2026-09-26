# pc_daemon —— 小智 v1 PC 守护进程

监听 zcode/workbuddy 日志 → 生成播报词 → 通过 USB-CDC 下发指令给 ESP32-S3 出声。

## 运行

```bash
cd D:\ai-xxq
pip install -r pc_daemon/requirements.txt

# 无硬件自测（真实监听 zcode 日志，指令只打印不进串口）
python -m pc_daemon.main --dry-run -v

# 链路演示（注入假事件，验证 播报词→下发）
python -m pc_daemon.main --demo

# 正式值守（需接好小智）
python -m pc_daemon.main
```

## 配置（config.json）

- `agents.*.log_dir`：各 Agent 的日志目录（zcode 已填好；workbuddy 留空=不启用）
- `deepseek.api_key_env`：环境变量名，设 `DEEPSEEK_API_KEY` 后走大模型生成播报词，否则用本地词库
- `serial.vid_pid`：设备识别名单（ESP32-S3 原生 CDC=303A:1001，CH343=1A86:55D3/55DB）
- `cooldown_sec`：同一 Agent 同一会话同类事件的冷却秒数

## 事件规则（agent_monitor.py）

| 日志事件 | 映射 |
| --- | --- |
| `event == "model.request.completed"` | `done`（Agent 完成一轮响应） |
| `level == "error"` | `error` |

冷却窗口内重复事件只报一次（默认 30s/会话/类型）。
