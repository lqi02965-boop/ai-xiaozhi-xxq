/*
 * oled_display —— SSD1306 128x64 I2C OLED 状态屏（v1.6 新增，小智的脸 v0）
 *
 * 文件用途：PC 下发 {"type":"oled","data":{"lines":[...]}}，屏幕显示 ASCII 文字
 * （最多 8 行，每行 21 字符，6x8 字库由 scripts/gen_oled_font.py 生成）。
 * 中文不在字库内——状态文字用英文/拼音（v2 圆屏 GC9A01 再上中文与表情动画）。
 *
 * 硬件接线（V2-100）：SDA→GPIO8  SCL→GPIO9  VCC→3.3V  GND→GND，地址 0x3C。
 */
#ifndef _OLED_DISPLAY_H_
#define _OLED_DISPLAY_H_

#include <stdbool.h>
#include "cJSON.h"

/* I2C 总线 + SSD1306 初始化（含设备探测）；成功即清屏并点亮 */
bool oled_init(void);

/* 初始化是否成功（status 指令回显用） */
bool oled_ready(void);

/* 清帧缓冲（不刷屏） */
void oled_clear(void);

/* 画一行 ASCII 文本（line 0~7；超宽截断，非 ASCII 显示空格） */
void oled_text(int line, const char *s);

/* 帧缓冲 → 屏幕 */
void oled_flush(void);

/* 便捷接口：清屏 + 逐行画 + 刷。lines = JSON 字符串数组（最多取 8 行） */
void oled_show_lines(const cJSON *lines);

#endif /* _OLED_DISPLAY_H_ */
