# 小智 Pro（三合一）项目工作区

桌面 AI 助手"小智"——PC 算力守护中心 + ESP32-S3 交互感知中枢的三合一实现。
规划与进度记录在 Obsidian 库：`D:\esp\esp32\实战项目\人工智能小智\`。

## 目录约定

| 目录 | 内容 | 说明 |
| --- | --- | --- |
| `pc_daemon/` | PC 端守护进程（Python） | Agent 监听、DeepSeek 播报词、视觉检测、串口桥（M3 阶段填充） |
| `firmware/` | ESP32-S3 自研固件代码 | 在 xiaozhi-esp32 基础上的二开组件（M2 阶段填充） |
| `docs/` | 工程文档 | 通信协议、供电规划、硬件记录等 |
| `third_party/` | 外部开源仓库（**不入库**，已 gitignore） | `xiaozhi-esp32` 固件、`xiaozhi-esp32-server` 后端 |

## 关联文档（Obsidian）

- 可行性评审：`01-可行性评审报告.md`
- 任务计划表：`02-项目计划表.md`（任务编号 M0-xx ~ M5-xx，本文档提交对应 M0-06）
- 进度日志：`进度记录/`（每天一篇）

## 技术栈速查

- **ESP32-S3**：ESP-IDF v5.5（评审决议；本机 v6.1 冒烟失败则并行安装）+ FreeRTOS + LVGL v8 + WebSocket
- **PC**：Python 3.10+，PySide6 / OpenCV / MediaPipe / YOLOv8 / pyserial / watchdog / DeepSeek API
- **后端**：先公用服务，后自建 xiaozhi-esp32-server（DeepSeek-V3 LLM + EdgeTTS）
