/*
 * ultra —— HC-SR04 超声波测距实现（v1.6 感官）
 */
#include "ultra.h"
#include "cdc_link.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "driver/gpio.h"
#include "cJSON.h"

static const char *TAG = "ultra";

#define ULTRA_TRIG  GPIO_NUM_1
#define ULTRA_ECHO  GPIO_NUM_17

#define ULTRA_POLL_MS     250     /* 测量周期 */
#define ULTRA_NEAR_CM     60      /* 近距阈值：进入即报 */
#define ULTRA_COOLDOWN_MS (30 * 1000)   /* 近距事件冷却 */
#define ULTRA_ECHO_TIMEOUT_US 30000    /* 单次回波等待（≈5m 盲区上限） */

static volatile int s_last_cm = -1;

/* 单次测量：返回 cm，-1 = 无回波/超时 */
static int32_t ultra_measure_once(void)
{
    gpio_set_level(ULTRA_TRIG, 0);
    esp_rom_delay_us(4);
    gpio_set_level(ULTRA_TRIG, 1);
    esp_rom_delay_us(10);
    gpio_set_level(ULTRA_TRIG, 0);

    int64_t t0 = esp_timer_get_time();
    while (gpio_get_level(ULTRA_ECHO) == 0) {
        if (esp_timer_get_time() - t0 > ULTRA_ECHO_TIMEOUT_US) {
            return -1;    /* 没人听（未接线/超远） */
        }
    }
    int64_t t1 = esp_timer_get_time();
    while (gpio_get_level(ULTRA_ECHO) == 1) {
        if (esp_timer_get_time() - t1 > ULTRA_ECHO_TIMEOUT_US) {
            break;        /* 回波过宽按超远处理 */
        }
    }
    int64_t width = esp_timer_get_time() - t1;
    return (int32_t)(width / 58);    /* 声速 340m/s：us/58 ≈ cm */
}

static void ultra_task(void *arg)
{
    bool was_near = false;
    int64_t last_report = 0;
    ESP_LOGI(TAG, "HC-SR04 测距启动: TRIG=%d ECHO=%d 近距阈值 %dcm",
             ULTRA_TRIG, ULTRA_ECHO, ULTRA_NEAR_CM);
    while (1) {
        int cm = ultra_measure_once();
        s_last_cm = cm;
        bool near = (cm > 0 && cm <= ULTRA_NEAR_CM);
        if (near && !was_near) {
            int64_t now = esp_timer_get_time() / 1000;
            if (now - last_report >= ULTRA_COOLDOWN_MS) {
                last_report = now;
                cJSON *data = cJSON_CreateObject();
                cJSON_AddNumberToObject(data, "cm", cm);
                cdc_send_event("dist", data);
                ESP_LOGI(TAG, "有人靠近: %d cm", cm);
            }
        }
        was_near = near;
        vTaskDelay(pdMS_TO_TICKS(ULTRA_POLL_MS));
    }
}

void ultra_start(void)
{
    gpio_config_t out_cfg = {
        .pin_bit_mask = 1ULL << ULTRA_TRIG,
        .mode = GPIO_MODE_OUTPUT,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
    };
    gpio_config(&out_cfg);
    gpio_config_t in_cfg = {
        .pin_bit_mask = 1ULL << ULTRA_ECHO,
        .mode = GPIO_MODE_INPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,   /* 分压到 GND，下拉兜底防悬空 */
        .pull_up_en = GPIO_PULLUP_DISABLE,
    };
    gpio_config(&in_cfg);
    if (xTaskCreate(ultra_task, "ultra", 3072, NULL, 3, NULL) != pdPASS) {
        ESP_LOGE(TAG, "测距任务创建失败");
    }
}

int ultra_last_cm(void)
{
    return s_last_cm;
}
