# 云小小 v1 — 桌面 AI 陪伴智能体 🌙

> 一个跑在你电脑上的**情感陪伴智能体**：语音聊天、记得你说的话、主动播报天气与新闻、
> 盯着你的 AI 编程助手干活、卡在审批时喊你回来——声音从一个 ESP32-S3 小音箱里出来。

```text
🎙️ 你说话 ──▶ 🧠 PC 智能体（GLM function calling 自主决策）
                ├─ get_time     本地时间（中文星期，零幻觉）
                ├─ get_weather  Open-Meteo 实时天气（自动定位城市，任意城市可查）
                ├─ web_search   必应/Tavily/博查 可插拔联网搜索
                └─ 纯聊天       温柔体贴人设 + 20 轮记忆（落盘续聊）
🔊 ESP32-S3 小音箱 ◀── USB-CDC 停等流控音频（晓晓音色 TTS）
🛡️ 后台守护进程 ──▶ zcode 任务完成/报错/等待审批 → 主动语音提醒
⏰ 每天 07:30    ──▶ 天气 + 新闻热点 定时播报
```

## 功能一览

- **语音陪伴聊天**：按回车说话，本地 faster-whisper 转写（离线免 VPN），GLM 温柔回应，
  晓晓音色播放；记忆落盘，关机重开继续聊
- **智能体工具调用**：GLM function calling 自主决策调用工具，指定任意城市查天气、
  联网搜真实资料（杜绝 LLM 编造：事实类数据本地直出/搜索注入）
- **Agent 监视**：监听 zcode 日志——任务完成、报错、**等待审批**时主动语音提醒
  （30 秒冷却防连报，可配置）
- **每日定时播报**：07:30 自动播报实时天气 + 今日新闻热点（时间/开关可配）
- **设备端**：ESP32-S3 固件（USB-CDC 协议 v1.1：JSON 指令 + 停等流控音频流，
  MAX98357A I2S 播放，三段提示音，NVS 配置）

## 快速开始

### 1. 硬件（语音出声端，可选——先不接也能用 PC 音箱）

ESP32-S3-WROOM-1（N16R8）+ MAX98357A 功放 + 8Ω 喇叭，接线 5 根：

| MAX98357A | ESP32-S3 |
| --- | --- |
| VIN / GND | 5V / GND |
| BCLK / LRC / DIN | GPIO15 / GPIO16 / GPIO7 |

详见 `docs/硬件-供电规划.md`。

### 2. 固件（ESP-IDF v6.x）

```bash
cd firmware/agent_speaker
python scripts/idf_run.py set-target esp32s3   # 仅首次
python scripts/idf_run.py build
python scripts/idf_run.py flash monitor -p COM口
```

### 3. PC 守护进程（智能体大脑）

```bash
cd D:\ai-xxq
pip install -r pc_daemon/requirements.txt

# 密钥全部走环境变量（绝不入库）
setx GLM_API_KEY "你的智谱key"          # 聊天大脑（智谱开放平台，glm-4-flash 免费）
setx DEEPSEEK_API_KEY "你的key"         # 可选备用
setx SEARCH_API_KEY "tvly-你的key"      # 可选：Tavily 联网搜索（免费 1000 次/月）

# 启动
python -m pc_daemon.main                # 后台提醒 + 每日播报
python -m pc_daemon.companion_ui        # 陪伴聊天图形界面
```

### 4. 密钥安全

**所有密钥只存本地环境变量 / `secrets.json`（已 gitignore）**，代码与配置文件中
只有环境变量名。上传/分享仓库前跑一遍自检：

```bash
git grep -I -n -E "(tvly-|sk-[A-Za-z0-9]{20,})"   # 应无输出
```

## 目录结构

```
pc_daemon/        PC 智能体（守护进程 + 陪伴聊天 GUI + 技能）
firmware/         ESP32-S3 固件（agent_speaker，ESP-IDF）
docs/             通信协议 v1.1、硬件供电规划
scripts/          构建包装 / 探针验收 / 音效生成 / 启停脚本
third_party/      外部参考仓库（gitignore，不入库）
```

## Roadmap

- [x] v1：Agent 监视提醒 + 语音陪伴（PC 音箱）+ 智能体工具调用
- [ ] v1.1：设备喇叭硬件联调（软件已就绪，`audio_output: device` 一键切换）
- [ ] v2：小智本体语音对话（xiaozhi-esp32 固件 + INMP441 + GC9A01 表情，先公用后自建后端）
- [ ] v3：身体与感知（舵机云台、HC-SR04、DHT11）、PC 视觉（手势/离座）

## 隐私

- 对话记忆、天气缓存、日志全部**仅存本地**（已 gitignore），不上传任何服务器
- 语音识别本地完成；对话内容发送至你配置的 LLM API（当前为智谱 GLM）
