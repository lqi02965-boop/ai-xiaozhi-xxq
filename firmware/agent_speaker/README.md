# agent_speaker 固件（小智 v1）

**任务用途**：v1「Agent 监视语音提示器」的 ESP32-S3 端固件。PC 守护进程发现
zcode/workbuddy 有动静后，通过 USB-CDC 发指令，本固件驱动 MAX98357A 喇叭出声。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `CMakeLists.txt` | 工程声明（项目名 agent_speaker） |
| `main/main.c` | 固件入口：版本日志、任务骨架，后续任务在此挂载音频与 CDC |
| `main/CMakeLists.txt` | main 组件注册（后续在此追加组件依赖） |
| `sdkconfig.defaults` | 编译默认配置（esp32s3、4MB、日志级别） |

## 编译烧录（命令行）

```bash
cd D:\ai-xxq\firmware\agent_speaker
python D:\ai-xxq\scripts\idf_run.py set-target esp32s3   # 仅首次
python D:\ai-xxq\scripts\idf_run.py build
python D:\ai-xxq\scripts\idf_run.py flash monitor -p COM口   # V1-100 接线后
```

## 里程碑对应

| 里程碑 | 内容 |
| --- | --- |
| V1-M1（本工程） | CDC 指令 → 本地提示音播放 |
| V1-M3 | + PCM 音频流播放（Edge-TTS 合成语音） |
