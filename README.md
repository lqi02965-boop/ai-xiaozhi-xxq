# 云小小 v1.5 — 桌面 AI 陪伴智能体 🌙🍊

> 一个跑在你电脑上的**情感陪伴智能体**：语音/文字聊天、记得你说的话、
> 主动播报天气与新闻、盯着你的 AI 编程助手干活、卡在审批时喊你回来。

> ## ✨ 纯电脑端版本
> **当前版本（v1.5）为纯电脑端版本：全部功能在一台 Windows 电脑上即可完整运行，
> 无需任何外设参与。** 麦克风、音箱用电脑自带的就行。
>
> ESP32-S3 小音箱是**可选外设**，定位只是一只"外接小喇叭"——
> 插上可通过 GUI 一键切换声音出口，拔掉不影响任何功能。
> （v2 规划中它将升级为独立语音终端，见文末 Roadmap）

## 架构一览

```text
🎙️ 电脑麦克风 ──▶ 🧠 PC 智能体（整颗大脑都在电脑上）
│                  ├─ 🎧 SenseVoice 本地语音识别（离线，可切 whisper）
│                  ├─ 🤖 GLM function calling 自主决策
│                  │    ├─ get_time     本地时间（中文星期，零幻觉）
│                  │    ├─ get_weather  Open-Meteo 实时天气（自动定位，任意城市）
│                  │    ├─ web_search   Tavily/必应/博查 联网搜索（渐进式重试）
│                  │    └─ 纯聊天       温柔体贴人设 + 20 轮记忆（落盘续聊）
│                  └─ 🗣️ EdgeTTS 语音合成（晓晓音色）
🔊 声音出口（GUI 一键切换）
    ├─ 🖥️ 电脑音箱          ← 纯电脑端默认，插耳机也行
    └─ 🔊 ESP32-S3 小音箱    ← 可选外设（USB-CDC 停等流控音频）
🛡️ 守护进程 ──▶ zcode/workbuddy 任务完成/报错/等待审批 → 主动语音提醒
⏰ 每天 07:30 ──▶ 实时天气 + 新闻热点 定时播报
```

## 功能一览

### 💬 陪伴聊天
- **语音聊天**：点「🍊 说话」录音 → 本地 SenseVoice 识别（CPU 推理 ~0.3s，精度较
  whisper 更高，可配置切回）→ GLM 回应 → TTS 播放
- **文字聊天**：多行输入框，`Ctrl+回车` 发送、回车换行
- **打断抢话**：她说话时点 🎤 或直接发新消息，立即停下听你说
- **长期记忆**：20 轮对话记忆落盘，关机重开接着聊；🧹 一键清记忆重新认识

### 🤖 智能体能力
- **工具调用**：GLM function calling 自主决策——事实类数据本地直出/搜索注入，
  杜绝 LLM 编造（时间星期零幻觉、天气真实数据、搜索真实结果）
- **联网搜索**：Tavily / 必应 / 博查可插拔；渐进式查询重试，口语化提问也能搜到

### 🛡️ Agent 监视（AI 编程助手盯梢）
- 监听 zcode、workbuddy 等本地 AI Agent 的日志：**任务完成 / 报错 / 等待审批**
  时主动语音提醒，人可以放心离开电脑
- GUI 内置 **Agent 管理器**：自动扫描 10 种已知 Agent，一键启停、添加自定义
  （任何有日志文件的 Agent 都能接，config 加一段规则即可，零代码）

### ⏰ 主动播报
- 每日定时播报：实时天气（IP 自动定位）+ 新闻热点，时间/开关可配
- Agent 事件提醒（见上）

### 🖥️ 陪伴 GUI（柑橘小清新主题 🍋）
- 📌 窗口置顶、📋 右键复制（选中/最新回复/全部对话）
- 🔊 声音输出一键切换：电脑音箱 ↔ ESP32 小智喇叭（即点即生效并记住）
- 🍊 自定义应用图标（窗口/任务栏/桌面快捷方式）

## 快速开始（纯电脑端，5 分钟）

只需一台 Windows 电脑 + Python 3.10+，无需任何硬件。

```bash
git clone https://github.com/lqi02965-boop/ai-xiaozhi-xxq.git
cd ai-xxq
pip install -r pc_daemon/requirements.txt

# 密钥全部走环境变量（绝不入库）
setx GLM_API_KEY "你的智谱key"          # 聊天大脑（智谱开放平台，glm-4-flash 免费）
setx DEEPSEEK_API_KEY "你的key"         # 可选备用
setx SEARCH_API_KEY "tvly-你的key"      # 可选：Tavily 联网搜索（免费 1000 次/月）

# 启动
python -m pc_daemon.main                # 守护进程：提醒 + 每日播报 + 控制端口
python -m pc_daemon.companion_ui        # 陪伴聊天 GUI
```

首次语音聊天会自动从 ModelScope（国内直连）下载 SenseVoice 模型。

## 可选外设：ESP32-S3 小音箱

想让它从桌边小音箱出声才需要这部分。硬件：ESP32-S3-WROOM + MAX98357A 功放 +
8Ω 喇叭，接线 5 根（见 `docs/硬件-供电规划.md`）：

| MAX98357A | ESP32-S3 |
| --- | --- |
| VIN / GND | 5V / GND |
| BCLK / LRC / DIN | GPIO15 / GPIO16 / GPIO7 |

```bash
cd firmware/agent_speaker
python scripts/idf_run.py set-target esp32s3   # 仅首次
python scripts/idf_run.py build
python scripts/idf_run.py flash monitor -p COM口
```

烧录后 GUI 点 🔊 切换到「小智喇叭」即可。不接硬件时守护进程的串口重连警告无害。

## 目录结构

```
ai-xxq/
├─ pc_daemon/                ★ PC 智能体（大脑）——纯电脑端版本的全部功能
│  ├─ main.py                  守护进程入口：串口桥 / 每日播报 / Agent 提醒 / 控制端口 18765
│  ├─ companion.py             陪伴核心：录音 / 识别(SenseVoice·whisper) / 对话 / 搜索
│  ├─ companion_ui.py          陪伴 GUI：语音·文字聊天 / 打断 / 置顶 / 复制 / 输出切换 / Agent 管理器
│  ├─ agent_monitor.py         Agent 监视：日志监听 + 审批/完成/报错提醒
│  ├─ agent_scan.py            已知 Agent 自动扫描（数据驱动规则）
│  ├─ serial_bridge.py         USB-CDC 串口桥（协议 v1.1：心跳 + JSON 指令 + 音频流）
│  ├─ tts.py                   EdgeTTS 合成（晓晓音色，音量/语速/变调可配）
│  ├─ pc_player.py             PC 音箱播放与打断
│  ├─ prompt_engine.py         人设/播报词渲染（词库热加载）
│  ├─ companion_config.json    陪伴配置：人设 / 识别引擎 / 麦克风 / 搜索触发词
│  ├─ config.json              守护进程配置：声音出口 / 播报时间 / 搜索源
│  ├─ models/sensevoice/       SenseVoice 模型副本（本地，gitignore）
│  └─ logs/ secrets.json …     日志/记忆/密钥（全部本地，gitignore）
├─ firmware/agent_speaker/   ESP32-S3 固件（可选外设：小音箱）
│  └─ main/                    cdc_link(USB-CDC) + i2s_player(I2S 播放) + sounds(提示音)
├─ scripts/                  工具脚本
│  ├─ idf_run.py               固件命令行编译烧录
│  ├─ cdc_probe.py / test_speaker.py   串口验收 / 喇叭试音
│  ├─ mic_probe.py             麦克风电平实测
│  ├─ asr_switch_test.py       识别引擎 A/B 对比
│  ├─ gen_app_icon.py / capture_ui.py  应用图标生成 / GUI 窗口截图
│  └─ start/stop_daemon.bat · companion_ui.bat   启停脚本
├─ docs/                     通信协议 v1.1、硬件供电规划
├─ AGENTS.md                 ZCode 会话自动加载的项目指令
└─ README.md                 本文件
```

## Roadmap

- [x] **v1**：语音陪伴 + 智能体工具调用 + Agent 监视提醒
- [x] **v1.5**：纯电脑端完整体验（SenseVoice 识别、GUI 全套、输出切换、小清新主题）
- [ ] **v1.1+**：识别精度持续优化（麦克风增益 / LLM 转写纠错）
- [ ] **v2**：小智本体独立语音对话——脱离电脑（xiaozhi-esp32 固件 + INMP441 麦 +
  GC9A01 表情屏，片上唤醒 + 云端 ASR/LLM，先公用后自建后端）
- [ ] **v3**：身体与感知（舵机云台、HC-SR04、DHT11）、PC 视觉（手势/离座）

## 密钥安全

**所有密钥只存本地环境变量 / `secrets.json`（已 gitignore）**，代码与配置文件中
只有环境变量名。上传/分享仓库前跑一遍自检：

```bash
git grep -I -n -E "(tvly-|sk-[A-Za-z0-9]{20,})"   # 应无输出
```

## 隐私

- 对话记忆、天气缓存、日志全部**仅存本地**（已 gitignore），不上传任何服务器
- 语音识别本地完成；对话内容发送至你配置的 LLM API（当前为智谱 GLM）
