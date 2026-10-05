/*
 * mic_in —— INMP441 I2S 数字麦克风采集（v1.6 新增耳朵）
 *
 * 文件用途：小智 v1.6 的"耳蜗驱动"。PC 发 mic_start 后：
 *   1. 惰性创建 I2S RX 通道（I2S_NUM_AUTO，会自动避开 i2s_player 已占的控制器）
 *   2. 32bit 帧 30ms 一读，右移定标为 16bit PCM（带饱和钳位，增益可调）
 *   3. base64 后以 {"type":"mic_data","data":{"pcm":...}} JSON 行回传 PC
 * PC 发 mic_stop（或 120s 看护超时）后关通道删任务，现场全清。
 *
 * 硬件前提（V2-100 接线）：INMP441
 *   VDD→3.3V  GND→GND  SCK→GPIO4  WS→GPIO5  SD→GPIO6  L/R→GND（左声道）
 *
 * ⚠️ 麦克风流与 PC→板子的 TTS 音频流共用 USB-CDC 带宽（两者合计 <100KB/s，
 *    USB CDC 富余），但建议聊天时序上错开：先听清→再回答。
 */
#ifndef _MIC_IN_H_
#define _MIC_IN_H_

#include <stdbool.h>
#include "esp_err.h"

/* 启动采集并回传（内部创建任务，立即返回）。sr=采样率(8k~48k，默认16k)，
 * shift=32bit→16bit 的右移定标（8~16，越小增益越大，默认 12）。 */
esp_err_t mic_in_start(int sample_rate, int gain_shift);

/* 停止采集并释放通道（最多阻塞 ~1s 等任务退场） */
void mic_in_stop(void);

/* 是否正在采集回传 */
bool mic_in_busy(void);

#endif /* _MIC_IN_H_ */
