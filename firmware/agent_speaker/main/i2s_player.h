/*
 * i2s_player —— 固件音频播放器（V1-104 本地音效 + V1-303 PCM 流式播放）
 *
 * 文件用途：小智 v1 的"声带驱动"。两条腿：
 *   V1-104  play_named()      播放 EMBED_FILES 嵌入的提示音 WAV（成功/报错/通知）
 *   V1-303  feed_pcm()        接收 PC 端 Edge-TTS 合成的 16k/16bit/mono 裸 PCM 流式播放
 *
 * ⚠️ 本文件为"盲写待接入"状态：已按 IDF v6.1 API 编写，但尚未加入 CMakeLists、
 *    尚未与 cdc_link 接线、未上板验证。接入步骤见 INTEGRATION_V1-104-303.md。
 *
 * 硬件前提（V1-100 接线）：BCLK→GPIO15  LRC→GPIO16  DIN→GPIO7  VIN→5V  GND→GND
 * 输出策略：内部把单声道样本复制为立体声帧发送（MAX98357A SD 悬空时取 (L+R)/2，
 *           双声道同值可避免 -6dB 衰减，且不依赖 SD 引脚状态）。
 */
#ifndef _I2S_PLAYER_H_
#define _I2S_PLAYER_H_

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

/* 初始化 I2S TX 通道（V1-102 接线完成后调用一次） */
esp_err_t i2s_player_init(void);

/* 【V1-104】按名字播放嵌入提示音：chime_success / chime_error / chime_notice
 * 阻塞播放直到结束或被打断；返回实际播放的 PCM 字节数，-1 表示找不到该音效 */
int i2s_player_play_named(const char *sound);

/* 【V1-303】音频流会话：begin 占用播放器 → 循环 feed → end 释放。
 * 会话期间 play_named 会被拒（busy），保证 TTS 流不被音效打断。 */
esp_err_t i2s_player_stream_begin(uint32_t total_bytes);
int i2s_player_feed_pcm(const uint8_t *pcm, size_t len);   /* 会话内调用；被 abort 返回负值 */
esp_err_t i2s_player_stream_end(void);
uint32_t i2s_player_stream_consumed(void);                 /* 已消费 PCM 字节 */

/* 打断当前播放/音频流（play_sound interrupt=true 与 audio_stop 都走这里） */
void i2s_player_abort(void);

/* 播放器是否正在占用 */
bool i2s_player_busy(void);

#endif /* _I2S_PLAYER_H_ */
