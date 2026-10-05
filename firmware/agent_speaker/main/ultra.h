/*
 * ultra —— HC-SR04 超声波测距（v1.6 新增感官：靠近问候）
 *
 * 文件用途：250ms 一测，算出距离（cm）。进入近距区（默认 60cm）→
 * 向 PC 发 {"type":"dist","data":{"cm":N}} 事件（固件侧 30s 冷却防连报），
 * 守护进程收到后走播报管线说"你回来啦"。上次测量值挂在 status 里。
 *
 * 硬件接线（V2-100）：VCC→VIN(5V)  GND→GND  TRIG→GPIO1  ECHO→GPIO17。
 * ⚠️ ECHO 输出 5V，必须分压后进 GPIO17：ECHO—1kΩ—GPIO17，GPIO17—2kΩ—GND。
 */
#ifndef _ULTRA_H_
#define _ULTRA_H_

#include <stdbool.h>

/* 初始化 GPIO 并启动测距任务（未接线时安全：每次测量超时返回 -1） */
void ultra_start(void);

/* 最近一次测量值（cm），-1 = 未接线/无回波 */
int ultra_last_cm(void);

#endif /* _ULTRA_H_ */
