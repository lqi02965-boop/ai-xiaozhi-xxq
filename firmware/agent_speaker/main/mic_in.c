/*
 * mic_in —— INMP441 I2S 数字麦克风采集实现（v1.6 新增耳朵）
 *
 * 协议侧：mic_data 事件帧由 cdc_link 的 cdc_send_event 发出（行式 JSON，
 * base64 PCM）。停止路径：mic_stop 指令 / 120s 看护超时 / 任务内异常自清。
 */
#include "mic_in.h"
#include "cdc_link.h"

#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "driver/i2s_std.h"
#include "mbedtls/base64.h"
#include "cJSON.h"

static const char *TAG = "mic_in";

/* 接线（V2-100）：SCK→GPIO4  WS→GPIO5  SD→GPIO6  L/R→GND */
#define MIC_PIN_BCLK   GPIO_NUM_4
#define MIC_PIN_WS     GPIO_NUM_5
#define MIC_PIN_DIN    GPIO_NUM_6

#define MIC_SR_DEFAULT 16000
#define MIC_SR_MIN     8000
#define MIC_SR_MAX     48000
#define MIC_SHIFT_DEFAULT 12      /* 32→16bit 定标：>>12 = +24dB 相对 >>16 */
#define MIC_SHIFT_MIN  8
#define MIC_SHIFT_MAX  16

#define MIC_SAMPLES_PER_CHUNK 480 /* 30ms @16kHz；帧小=延迟低，base64 行也不超 3KB */
#define MIC_WATCHDOG_MS (120 * 1000)   /* 看护：PC 失联 mic_stop 时自愈 */

typedef struct {
    int sr;
    int shift;
} mic_cfg_t;

static i2s_chan_handle_t s_rx;
static volatile bool s_running;
static TaskHandle_t s_task;

/* 32bit 帧 → 16bit PCM（右移定标 + 饱和钳位） */
static inline int16_t mic_scale(int32_t raw, int shift)
{
    int32_t v = raw >> shift;
    if (v > 32767) {
        v = 32767;
    } else if (v < -32768) {
        v = -32768;
    }
    return (int16_t)v;
}

static void mic_task(void *arg)
{
    mic_cfg_t cfg = *(mic_cfg_t *)arg;
    free(arg);

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &s_rx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S RX 通道创建失败: %s", esp_err_to_name(err));
        goto cleanup;
    }
    i2s_std_config_t std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(cfg.sr),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = MIC_PIN_BCLK,
            .ws   = MIC_PIN_WS,
            .dout = I2S_GPIO_UNUSED,
            .din  = MIC_PIN_DIN,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    /* 显式只取左声道：INMP441 L/R 接地 → 数据在左槽；不设的话 RX 会把左右
     * 两槽都收进缓冲，等效采样率翻倍、音频变速（实测踩中） */
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
    err = i2s_channel_init_std_mode(s_rx, &std_cfg);
    if (err == ESP_OK) {
        err = i2s_channel_enable(s_rx);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S RX 初始化失败: %s", esp_err_to_name(err));
        i2s_del_channel(s_rx);
        s_rx = NULL;
        goto cleanup;
    }
    ESP_LOGI(TAG, "INMP441 采集开始: %dHz shift=%d（BCLK=%d WS=%d DIN=%d）",
             cfg.sr, cfg.shift, MIC_PIN_BCLK, MIC_PIN_WS, MIC_PIN_DIN);

    static int32_t raw[MIC_SAMPLES_PER_CHUNK];
    static int16_t pcm[MIC_SAMPLES_PER_CHUNK];
    static unsigned char b64[((MIC_SAMPLES_PER_CHUNK * 2) + 2) / 3 * 4 + 1];
    TickType_t t0 = xTaskGetTickCount();

    while (s_running) {
        size_t br = 0;
        esp_err_t rerr = i2s_channel_read(s_rx, raw, sizeof(raw), &br,
                                          pdMS_TO_TICKS(200));
        if (rerr != ESP_OK || br == 0) {
            static int s_bad;
            if (++s_bad % 10 == 1) {
                ESP_LOGW(TAG, "read 失败: %s br=%u（第 %d 次）",
                         esp_err_to_name(rerr), (unsigned)br, s_bad);
            }
            continue;    /* 超时醒来看停止旗标 */
        }
        int n = (int)(br / sizeof(int32_t));
        for (int i = 0; i < n; i++) {
            pcm[i] = mic_scale(raw[i], cfg.shift);
        }
        size_t olen = 0;
        if (mbedtls_base64_encode(b64, sizeof(b64), &olen,
                                  (const unsigned char *)pcm,
                                  (size_t)n * sizeof(int16_t)) != 0) {
            ESP_LOGW(TAG, "base64 编码失败");
            continue;
        }
        cJSON *data = cJSON_CreateObject();
        cJSON_AddStringToObject(data, "pcm", (const char *)b64);
        cJSON_AddNumberToObject(data, "bytes", (double)(n * (int)sizeof(int16_t)));
        cJSON_AddNumberToObject(data, "sr", cfg.sr);
        cdc_send_event("mic_data", data);
        static int s_sent;
        if (++s_sent % 50 == 1) {
            ESP_LOGI(TAG, "已发送 %d 帧（本帧 %d 样本峰值 %d）", s_sent, n,
                     n ? pcm[0] : 0);
        }

        if ((xTaskGetTickCount() - t0) > pdMS_TO_TICKS(MIC_WATCHDOG_MS)) {
            ESP_LOGW(TAG, "采集超过 %d 秒未收到 mic_stop，看护超时自停",
                     (int)(MIC_WATCHDOG_MS / 1000));
            break;
        }
    }

    if (s_rx) {
        i2s_channel_disable(s_rx);
        i2s_del_channel(s_rx);
        s_rx = NULL;
    }
cleanup:
    ESP_LOGI(TAG, "INMP441 采集结束，通道已释放");
    s_running = false;
    s_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t mic_in_start(int sample_rate, int gain_shift)
{
    if (s_running || s_task) {
        return ESP_ERR_INVALID_STATE;
    }
    mic_cfg_t *cfg = malloc(sizeof(mic_cfg_t));
    if (!cfg) {
        return ESP_ERR_NO_MEM;
    }
    cfg->sr = (sample_rate >= MIC_SR_MIN && sample_rate <= MIC_SR_MAX)
              ? sample_rate : MIC_SR_DEFAULT;
    cfg->shift = (gain_shift >= MIC_SHIFT_MIN && gain_shift <= MIC_SHIFT_MAX)
                 ? gain_shift : MIC_SHIFT_DEFAULT;
    s_running = true;
    if (xTaskCreate(mic_task, "mic_in", 4096, cfg, 5, &s_task) != pdPASS) {
        free(cfg);
        s_running = false;
        return ESP_FAIL;
    }
    return ESP_OK;
}

void mic_in_stop(void)
{
    if (!s_running && !s_task) {
        return;
    }
    s_running = false;
    /* 等任务退场（read 超时 200ms + 清理），最多 ~1s */
    for (int i = 0; i < 20 && s_task; i++) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

bool mic_in_busy(void)
{
    return s_running || s_task != NULL;
}
