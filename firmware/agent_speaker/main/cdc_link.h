/*
 * cdc_link —— USB-CDC 指令通道（V1-105）
 *
 * 文件用途：小智 v1 的"神经连接"。在 USB-Serial-JTAG CDC 上按协议 v1.1
 *          收发行分隔 JSON 帧：
 *            PC → 设备：ping / play_sound / status
 *            设备 → PC：pong / ack / error / status
 *          解析用 cJSON；未知类型回 error 401；坏 JSON 回 error 400。
 *          V1-303 将在此扩展音频流（audio_start → 原始 PCM → audio_end）。
 *
 * 协议详见：D:/ai-xxq/docs/protocol.md（Obsidian: 03-通信协议v1）
 */
#ifndef _CDC_LINK_H_
#define _CDC_LINK_H_

/* 固件版本：status 指令回传用；main.c 的启动横幅也引用它 */
#define FW_VERSION "v1.0.0-m1-cdc"

/* 创建 CDC 接收任务并安装驱动；非阻塞，失败时打日志返回错误码 */
int cdc_link_start(void);

#endif /* _CDC_LINK_H_ */
