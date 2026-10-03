# AGENTS.md —— 云小小项目 · ZCode 会话自动加载指令

> 本文件由 ZCode 在每次会话自动读取。**会话开始先做三件事**：
> 1. 读 `D:\esp\esp32\实战项目\人工智能小智\00-AI上下文索引.md`（项目记忆入口）
> 2. 遇到历史问题先查 `12-问题排查手册.md`
> 3. 本会话上下文超过约 60% 时，主动提醒用户开新会话（索引会兜住一切）

## 项目

**云小小**（原名小智）v1.5 —— 桌面 AI 陪伴智能体。
ESP32-S3 小音箱（手脚）+ PC 智能体 Python（大脑）+ GLM（云端模型）。
仓库：github.com/lqi02965-boop/ai-xiaozhi-xxq（本地 D:\ai-xxq，main 分支）。

## 关键路径

| 什么 | 在哪 |
| --- | --- |
| 代码仓库 | D:\ai-xxq |
| PC 智能体 | pc_daemon/（main 守护、companion 聊天、companion_ui GUI、tts、serial_bridge、agent_monitor） |
| 固件 | firmware/agent_speaker（ESP-IDF，编译用 scripts/idf_run.py） |
| 用户档案库（Obsidian） | D:\esp\esp32\实战项目\人工智能小智\（00-AI上下文索引 / 02-计划表 / 10-播报词库 / 12-问题排查手册 / 进度记录 / 对话记录） |
| 密钥 | 环境变量 GLM_API_KEY / DEEPSEEK_API_KEY / SEARCH_API_KEY（+ pc_daemon/secrets.json 兜底，gitignore） |
| 运行日志 | pc_daemon/logs/ |

## 铁律（违反过的地方，重点盯）

1. **先验证再声称完成**——改完必须实际运行/读回确认，禁止只凭"Edit 成功"就汇报生效
2. **UI/布局改动**：改完重启 GUI 并请用户目视确认；pack 顺序：底部控件先 `side="bottom"`，主内容最后 expand
3. **密钥绝不入库**：所有 key 走环境变量 + secrets.json（gitignore）；提交前跑密钥自检 grep
4. **含 `\n` 的代码修改用 Edit 工具**，禁止 heredoc 脚本写转义（已三次写坏文件）
5. **heredoc 里多段 replace 必须每段 assert**，否则静默跳过
6. **GLM-4-flash 特性**：工具调用会偷懒（有三重兜底）、星期必错（时间本地直出）、长任务别用
7. **每日收尾**：更新 00-AI上下文索引 的状态与待办；对话记录追加当日条目

## 常用命令速查

```bash
# 固件编译烧录
python D:\ai-xxq\scripts\idf_run.py build / flash -p COM11
# 串口验收 / 喇叭试音（先停守护进程）
python D:\ai-xxq\scripts\cdc_probe.py / test_speaker.py
# 守护进程启停
wscript.exe "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\小智守护进程.vbs"
D:\ai-xxq\scripts\stop_daemon.bat
# 陪伴 GUI
D:\ai-xxq\scripts\companion_ui.bat   # 或桌面"云小小.lnk"
```

## 环境

- 网络：GitHub 必须 VPN；PyPI 按 VPN 状态选源（开=官方/关=阿里云或清华）；必应/Open-Meteo/Tavily API/ModelScope 国内直连
- ESP-IDF：v6.1 @ D:\esp\v6.1，命令行只走 scripts/idf_run.py
- 串口：COM11 = 板子 USB 口；守护进程占用时烧录前先 stop_daemon
- 麦克风：input_device=3（Realtek，电平低需 Windows 加强 +20dB——用户侧待做）
