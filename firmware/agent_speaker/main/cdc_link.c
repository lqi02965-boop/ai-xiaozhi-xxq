/*
 * cdc_link —— USB-CDC 指令通道实现（协议 v1.1）
 *
 * 帧格式：一行一个 UTF-8 JSON + '\n'，单帧 ≤512 字节。
 * 收发 API：usb_serial_jtag_read_bytes / usb_serial_jtag_write_bytes（带超时）。
 * V1-104 将把 play_sound 接到真实播放器；当前阶段先回 ack 并打日志。
 */
#include "cdc_link.h"
#include "i2s_player.h"
#include "mic_in.h"
#include "oled_display.h"
#include "ultra.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "driver/usb_serial_jtag.h"
#include "cJSON.h"

static const char *TAG = "cdc_link";

#define CDC_RX_BUF_SIZE  4096   /* USB-Serial-JTAG 硬件 FIFO 仅 128B，靠驱动环形缓冲吸收突发 */
#define CDC_TX_BUF_SIZE  4096   /* mic_data 单帧 ~2.7KB，1024 会逼 write_bytes 跨超时拆写 */
#define CDC_LINE_MAX     512
#define CDC_RAW_STALE_MS 3000   /* 音频流卡死自救：3 秒没字节就放弃并恢复通道 */

static uint32_t s_seq;          /* 设备侧自增序号 */
static volatile bool s_raw_mode = false;   /* V1-303：音频流消费中，字节直达 I2S */
static uint32_t s_raw_total = 0;
static uint32_t s_chunk_size = 4096;   /* 停等流控的单块大小（PC 在 audio_start 里声明） */
static uint32_t s_raw_got = 0;
static int s_raw_seq = 0;       /* audio_start 的对端 seq，收满后 ack 用 */
static TickType_t s_last_rx_tick = 0;   /* 最近一次收到流字节的时刻 */
static uint8_t s_pcm_buf[4096]; /* 停等流控的单块 PCM 缓冲 */
static size_t s_chunk_used = 0;
static size_t s_pcm_len = 0;
static char s_acc[CDC_LINE_MAX + 1];   /* JSON 行缓冲（提升到文件级，audio_start 需清空） */
static size_t s_acc_len = 0;
static SemaphoreHandle_t s_tx_mux;     /* 多任务并发写 CDC 的互斥（mic 任务 / 指令应答） */

/* ---------- 发送 ---------- */

/* 写满全部字节：write_bytes 单次调用超时即返回已写数，必须循环补齐。
 * mic_data 单帧 ~2.7KB > TX 缓冲，不循环就只剩前 1KB（实测零帧到达的根因）。 */
static void cdc_write_all(const char *data, size_t len)
{
    size_t off = 0;
    while (off < len) {
        int w = usb_serial_jtag_write_bytes(data + off, len - off,
                                            pdMS_TO_TICKS(500));
        if (w <= 0) {
            ESP_LOGW(TAG, "CDC TX 停滞，放弃 %u 字节", (unsigned)(len - off));
            return;
        }
        off += (size_t)w;
    }
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
    char *line = cJSON_PrintUnformatted(obj);
    cJSON_Delete(obj);
    if (!line) {
        return;
    }
    size_t len = strlen(line);
    if (s_tx_mux && xSemaphoreTake(s_tx_mux, pdMS_TO_TICKS(500)) == pdTRUE) {
        cdc_write_all(line, len);
        cdc_write_all("\n", 1);
        xSemaphoreGive(s_tx_mux);
    }
    ESP_LOGD(TAG, "TX: %s", line);
    cJSON_free(line);
}

/* ack / error 统一封装 */
static void cdc_send_ack(int ref_seq, bool ok)
{
    cJSON *data = cJSON_CreateObject();
    cJSON_AddNumberToObject(data, "ref_seq", ref_seq);
    cJSON_AddBoolToObject(data, "ok", ok);
    cdc_send_frame("ack", data);
}

/* v1.6：设备侧主动事件帧（mic_data 等任务上下文调用，与指令应答共用发送口） */
void cdc_send_event(const char *type, cJSON *data)
{
    cdc_send_frame(type, data);
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
    const cJSON *itp   = data ? cJSON_GetObjectItem(data, "interrupt") : NULL;

    if (!cJSON_IsString(sound) || sound->valuestring[0] == '\0') {
        cdc_send_error(peer_seq, 402, "missing field: sound");
        return;
    }
    if (cJSON_IsTrue(itp)) {
        i2s_player_abort();                     /* 打断当前播放/流 */
    }
    if (i2s_player_busy()) {                    /* TTS 流进行中且未允许打断 */
        cdc_send_error(peer_seq, 403, "player busy");
        return;
    }
    i2s_player_play_named(sound->valuestring);  /* 阻塞至播完/被打断 */
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
    } else if (strcmp(type->valuestring, "audio_start") == 0) {
        const cJSON *data = cJSON_GetObjectItem(root, "data");
        const cJSON *bytes = data ? cJSON_GetObjectItem(data, "bytes") : NULL;
        if (!cJSON_IsNumber(bytes)) {
            cdc_send_error(peer_seq, 402, "missing field: bytes");
            return;
        }
        const cJSON *chunk = data ? cJSON_GetObjectItem(data, "chunk") : NULL;
        if (cJSON_IsNumber(chunk) && chunk->valuedouble > 0 &&
            chunk->valuedouble <= sizeof(s_pcm_buf)) {
            s_chunk_size = (uint32_t)chunk->valuedouble;
        } else {
            s_chunk_size = 512;   /* PC 未声明时用保守值 */
        }
        if (cJSON_IsTrue(cJSON_GetObjectItem(data, "interrupt"))) {
            i2s_player_abort();
        }
        if (i2s_player_stream_begin((uint32_t)bytes->valuedouble) != ESP_OK) {
            cdc_send_error(peer_seq, 403, "player busy");
            return;
        }
        s_raw_total = (uint32_t)bytes->valuedouble;
        s_raw_got = 0;
        s_pcm_len = 0;
        s_raw_seq = peer_seq;
        s_last_rx_tick = xTaskGetTickCount();   /* 重置停滞计时（否则开机 3s 后必误杀） */
        s_acc_len = 0;          /* 清掉半行残留，防止 PCM 字节混进行缓冲 */
        s_raw_mode = true;      /* 之后字节直达 I2S，不再当 JSON 解析 */
        ESP_LOGI(TAG, "audio_start: %u bytes", (unsigned)s_raw_total);
    } else if (strcmp(type->valuestring, "audio_stop") == 0) {
        i2s_player_abort();
        cdc_send_ack(peer_seq, true);
    } else if (strcmp(type->valuestring, "mic_start") == 0) {
        const cJSON *data = cJSON_GetObjectItem(root, "data");
        const cJSON *sr = data ? cJSON_GetObjectItem(data, "sr") : NULL;
        const cJSON *shift = data ? cJSON_GetObjectItem(data, "shift") : NULL;
        if (mic_in_busy()) {
            cdc_send_error(peer_seq, 403, "mic busy");
            return;
        }
        if (mic_in_start(cJSON_IsNumber(sr) ? (int)sr->valuedouble : 16000,
                         cJSON_IsNumber(shift) ? (int)shift->valuedouble : 12) != ESP_OK) {
            cdc_send_error(peer_seq, 403, "mic start failed");
            return;
        }
        cdc_send_ack(peer_seq, true);
    } else if (strcmp(type->valuestring, "mic_stop") == 0) {
        mic_in_stop();
        cdc_send_ack(peer_seq, true);
    } else if (strcmp(type->valuestring, "oled") == 0) {
        const cJSON *data = cJSON_GetObjectItem(root, "data");
        oled_show_lines(data ? cJSON_GetObjectItem(data, "lines") : NULL);
        cdc_send_ack(peer_seq, true);
    } else if (strcmp(type->valuestring, "status") == 0) {
        cJSON *data = cJSON_CreateObject();
        cJSON_AddStringToObject(data, "state", "idle");
        cJSON_AddNumberToObject(data, "uptime_s", xTaskGetTickCount() / configTICK_RATE_HZ);
        cJSON_AddStringToObject(data, "fw", FW_VERSION);
        cJSON_AddNumberToObject(data, "proto", 1);
        cJSON_AddBoolToObject(data, "mic", mic_in_busy());
        cJSON_AddBoolToObject(data, "oled", oled_ready());
        cJSON_AddNumberToObject(data, "dist", ultra_last_cm());
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
    char buf[512];
    TickType_t last_raw_rx = 0;

    ESP_LOGI(TAG, "CDC 指令通道就绪（协议 v1.1，音频流已启用）");
    while (1) {
        int n = usb_serial_jtag_read_bytes(buf, sizeof(buf), pdMS_TO_TICKS(20));
        for (int i = 0; i < n; i++) {
            char c = buf[i];

            /* V1-303 停等模式：攒满一块（或收完全部）→ 写 I2S → ack，PC 再发下一块。
             * 流控由 ack 同步，天然无溢出。 */
            if (s_raw_mode) {
                s_last_rx_tick = xTaskGetTickCount();
                s_pcm_buf[s_pcm_len++] = (uint8_t)c;
                s_raw_got++;
                if (s_raw_got >= s_raw_total) {
                    /* 全部收完：写入最后一块，结束会话，回最终 ack */
                    int w = i2s_player_feed_pcm(s_pcm_buf, s_pcm_len);
                    s_pcm_len = 0;
                    i2s_player_stream_end();
                    s_raw_mode = false;
                    cJSON *fin = cJSON_CreateObject();
                    cJSON_AddNumberToObject(fin, "ref_seq", s_raw_seq);
                    cJSON_AddBoolToObject(fin, "ok", w >= 0);
                    cJSON_AddNumberToObject(fin, "got", (double)s_raw_got);
                    cdc_send_frame("ack", fin);
                    ESP_LOGI(TAG, "audio stream 完成: %u bytes", (unsigned)s_raw_got);
                    continue;
                }
                bool chunk_done = (s_pcm_len >= s_chunk_size);
                if (chunk_done) {
                    i2s_player_feed_pcm(s_pcm_buf, s_pcm_len);
                    s_pcm_len = 0;
                    cJSON *ack_data = cJSON_CreateObject();
                    cJSON_AddNumberToObject(ack_data, "ref_seq", s_raw_seq);
                    cJSON_AddBoolToObject(ack_data, "ok", true);
                    cJSON_AddNumberToObject(ack_data, "got", (double)s_raw_got);
                    cdc_send_frame("ack", ack_data);
                }
                continue;
            }

            if (c == '\n') {
                if (s_acc_len > 0) {
                    s_acc[s_acc_len] = '\0';
                    handle_line(s_acc);
                    s_acc_len = 0;
                }
                continue;   /* 忽略空行与 \r 之外的处理（\r 由下面剔除） */
            }
            if (c == '\r') {
                continue;
            }
            if (s_acc_len < CDC_LINE_MAX) {
                s_acc[s_acc_len++] = c;
            } else {
                ESP_LOGW(TAG, "超长帧丢弃（>%d）", CDC_LINE_MAX);
                cdc_send_error(0, 402, "frame too long");
                s_acc_len = 0;
                break;
            }
        }
        /* 音频流卡死自救：字节计数凑不齐且 3 秒无数据 → 放弃并恢复 JSON 通道 */
        if (s_raw_mode && n == 0 &&
            (xTaskGetTickCount() - s_last_rx_tick) > pdMS_TO_TICKS(CDC_RAW_STALE_MS)) {
            ESP_LOGW(TAG, "音频流 %u/%u 字节停滞超时，放弃并恢复通道",
                     (unsigned)s_raw_got, (unsigned)s_raw_total);
            i2s_player_stream_end();
            s_raw_mode = false;
            s_pcm_len = 0;
            {
                cJSON *ack_data = cJSON_CreateObject();
                cJSON_AddNumberToObject(ack_data, "ref_seq", s_raw_seq);
                cJSON_AddBoolToObject(ack_data, "ok", false);
                cJSON_AddNumberToObject(ack_data, "got", (double)s_raw_got);
                cdc_send_frame("ack", ack_data);
            }
        }
    }
}

int cdc_link_start(void)
{
    s_tx_mux = xSemaphoreCreateMutex();
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
