/*
 * agent_speaker —— 小智 v1 主固件入口
 *
 * 文件用途：固件启动入口与任务骨架。V1-101 阶段只验证"工程能编译、板子能跑、
 *          日志能看"；后续任务在此骨架上逐个挂载：
 *            V1-102  I2S 音频输出（MAX98357A 点响）
 *            V1-103  提示音资源打包（flash 分区）
 *            V1-104  本地音效播放接口
 *            V1-105  USB-CDC JSON 指令通道（play_sound / ping / ack / pong）
 *            V1-303  PCM 音频流式播放（接收 PC 端 Edge-TTS 合成语音）
 *
 * 数据流：PC 守护进程 --USB-CDC(JSON+音频流)--> 本固件 --I2S--> MAX98357A --> 喇叭
 */
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_app_desc.h"
#include "cdc_link.h"
#include "i2s_player.h"

static const char *TAG = "agent_speaker";

void app_main(void)
{
    const esp_app_desc_t *app = esp_app_get_description();
    ESP_LOGI(TAG, "==============================");
    ESP_LOGI(TAG, "小智 v1 Agent 语音提示器 %s", FW_VERSION);
    ESP_LOGI(TAG, "项目: %s 版本: %s", app->project_name, app->version);
    ESP_LOGI(TAG, "编译时间: %s %s", app->date, app->time);
    ESP_LOGI(TAG, "==============================");

    /* V1-102：初始化 I2S 播放器（MAX98357A），随后 CDC 指令即可出声 */
    if (i2s_player_init() != ESP_OK) {
        ESP_LOGE(TAG, "I2S 播放器初始化失败");
    }
    /* 启动 USB-CDC 指令通道（V1-105）：PC 可发 ping/play_sound/status */
    if (cdc_link_start() != ESP_OK) {
        ESP_LOGE(TAG, "CDC 通道启动失败");
    }

    /* V1-102/104 将在此初始化 I2S 播放器。
     * 当前骨架阶段仅保活，输出心跳日志便于确认固件活着。 */
    while (1) {
        ESP_LOGD(TAG, "alive");
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
