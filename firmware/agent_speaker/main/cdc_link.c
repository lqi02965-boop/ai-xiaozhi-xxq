/*
 * cdc_link —— USB-CDC 指令通道实现（协议 v1.1）
 *
 * 帧格式：一行一个 UTF-8 JSON + '\n'，单帧 ≤512 字节。
 * 收发 API：usb_serial_jtag_read_bytes / usb_serial_jtag_write_bytes（带超时）。
 * V1-104 将把 play_sound 接到真实播放器；当前阶段先回 ack 并打日志。
 */
#include "cdc_link.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "driver/usb_serial_jtag.h"
#include "cJSON.h"

static const char *TAG = "cdc_link";

#define CDC_RX_BUF_SIZE  1024
#define CDC_TX_BUF_SIZE  1024
#define CDC_LINE_MAX     512

static uint32_t s_seq;          /* 设备侧自增序号 */

/* ---------- 发送 ---------- */

static void cdc_send_obj(cJSON *obj)
{
    if (!obj) {
        return;
    }
    char *line = cJSON_PrintUnformatted(obj);
    cJSON_Delete(obj);
    if (!line) {
        return;
    }
    size_t len = strlen(line);
    usb_serial_jtag_write_bytes((const char *)line, len, pdMS_TO_TICKS(200));
    usb_serial_jtag_write_bytes("\n", 1, pdMS_TO_TICKS(100));
    ESP_LOGD(TAG, "TX: %s", line);
    cJSON_free(line);
}

/* 带 seq 的通用帧：{"v":1,"type":T,"seq":N,"data":{...}} */
static void cdc_send_frame(const char *type, cJSON *data)
{
    cJSON *obj = cJSON_CreateObject();
    cJSON_AddNumberToObject(obj, "v", 1);
    cJSON_AddStringToObject(obj, "type", type);
    cJSON_AddNumberToObject(obj, "seq", ++s_seq);
    if (data) {
        cJSON_AddItemToObject(obj, "data", data);   /* 移交所有权 */
    } else {
        cJSON_AddItemToObject(obj, "data", cJSON_CreateObject());
    }
    cdc_send_obj(obj);
}

/* ack / error 统一封装 */
static void cdc_send_ack(int ref_seq, bool ok)
{
    cJSON *data = cJSON_CreateObject();
    cJSON_AddNumberToObject(data, "ref_seq", ref_seq);
    cJSON_AddBoolToObject(data, "ok", ok);
    cdc_send_frame("ack", data);
}

static void cdc_send_error(int ref_seq, int code, const char *msg)
{
    cJSON *data = cJSON_CreateObject();
    cJSON_AddNumberToObject(data, "ref_seq", ref_seq);
    cJSON_AddNumberToObject(data, "code", code);
    cJSON_AddStringToObject(data, "msg", msg ? msg : "");
    cdc_send_frame("error", data);
}

/* ---------- 指令分发 ---------- */

static void handle_play_sound(const cJSON *root, int peer_seq)
{
    const cJSON *data = cJSON_GetObjectItem(root, "data");
    const cJSON *sound = data ? cJSON_GetObjectItem(data, "sound") : NULL;

    if (!cJSON_IsString(sound) || sound->valuestring[0] == '\0') {
        cdc_send_error(peer_seq, 402, "missing field: sound");
        return;
    }
    /* V1-104：此处调用 player_play(sound) 播放真实音效。
     * 当前阶段播放器未就绪，先接受指令并记录，让 PC 端链路可独立验证。 */
    ESP_LOGI(TAG, "play_sound: %s (player 待 V1-104 接入)", sound->valuestring);
    cdc_send_ack(peer_seq, true);
}

static void handle_line(const char *line)
{
    ESP_LOGD(TAG, "RX: %s", line);

    cJSON *root = cJSON_Parse(line);
    if (!root) {
        cdc_send_error(0, 400, "bad json");
        return;
    }
    const cJSON *type = cJSON_GetObjectItem(root, "type");
    const cJSON *seq = cJSON_GetObjectItem(root, "seq");
    int peer_seq = cJSON_IsNumber(seq) ? (int)seq->valuedouble : 0;

    if (!cJSON_IsString(type)) {
        cdc_send_error(peer_seq, 402, "missing field: type");
        cJSON_Delete(root);
        return;
    }

    if (strcmp(type->valuestring, "ping") == 0) {
        cdc_send_frame("pong", NULL);
    } else if (strcmp(type->valuestring, "play_sound") == 0) {
        handle_play_sound(root, peer_seq);
    } else if (strcmp(type->valuestring, "status") == 0) {
        cJSON *data = cJSON_CreateObject();
        cJSON_AddStringToObject(data, "state", "idle");
        cJSON_AddNumberToObject(data, "uptime_s", xTaskGetTickCount() / configTICK_RATE_HZ);
        cJSON_AddStringToObject(data, "fw", FW_VERSION);
        cJSON_AddNumberToObject(data, "proto", 1);
        cdc_send_frame("status", data);
    } else {
        ESP_LOGW(TAG, "unknown type: %s", type->valuestring);
        cdc_send_error(peer_seq, 401, "unknown type");
    }
    cJSON_Delete(root);
}

/* ---------- 接收任务 ---------- */

static void cdc_rx_task(void *arg)
{
    char acc[CDC_LINE_MAX + 1];
    size_t acc_len = 0;
    char buf[64];

    ESP_LOGI(TAG, "CDC 指令通道就绪（协议 v1.1）");
    while (1) {
        int n = usb_serial_jtag_read_bytes(buf, sizeof(buf), pdMS_TO_TICKS(50));
        for (int i = 0; i < n; i++) {
            char c = buf[i];
            if (c == '\n') {
                if (acc_len > 0) {
                    acc[acc_len] = '\0';
                    handle_line(acc);
                    acc_len = 0;
                }
                continue;   /* 忽略空行与 \r 之外的处理（\r 由下面剔除） */
            }
            if (c == '\r') {
                continue;
            }
            if (acc_len < CDC_LINE_MAX) {
                acc[acc_len++] = c;
            } else {
                ESP_LOGW(TAG, "超长帧丢弃（>%d）", CDC_LINE_MAX);
                cdc_send_error(0, 402, "frame too long");
                acc_len = 0;
                break;
            }
        }
    }
}

int cdc_link_start(void)
{
    usb_serial_jtag_driver_config_t cfg = {
        .rx_buffer_size = CDC_RX_BUF_SIZE,
        .tx_buffer_size = CDC_TX_BUF_SIZE,
    };
    esp_err_t err = usb_serial_jtag_driver_install(&cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "usb_serial_jtag_driver_install 失败: %s", esp_err_to_name(err));
        return -1;
    }
    if (xTaskCreate(cdc_rx_task, "cdc_rx", 4096, NULL, 5, NULL) != pdPASS) {
        ESP_LOGE(TAG, "创建 cdc_rx 任务失败");
        return -1;
    }
    return 0;
}
