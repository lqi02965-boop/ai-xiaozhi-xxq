# 小智 v1 极简固件工程（agent_speaker）
#
# 用途：本工程是 v1「Agent 监视语音提示器」的 ESP32-S3 固件底座。
#      v1 只做一件事：收 PC 的 USB-CDC 指令 → 从喇叭出声（提示音/TTS 语音流）。
#      不使用 xiaozhi-esp32 基座（那是 v2 对话版用的）。
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(agent_speaker)
