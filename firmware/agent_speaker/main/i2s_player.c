/*
 * i2s_player 实现（IDF v6.1，esp_driver_i2s 组件）
 *
 * 设计要点：
 *  - 互斥锁串行化"音效播放"与"音频流"两条路径（同一 TX 通道）
 *  - abort 标志在块间检查，打断延迟 ≈ 一个 DMA 块（~32ms）；
 *    彻底掐断用 disable/enable 重建通道（清空 DMA 残留数据）
 *  - 单声道 → 立体声帧复制在块缓冲内完成，I2S 始终跑 16k/16bit/双槽
 */
#include "i2s_player.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "driver/i2s_std.h"

static const char *TAG = "i2s_player";

/* 与接线表/协议一致 */
#define PIN_BCLK  GPIO_NUM_15
#define PIN_LRC   GPIO_NUM_16
#define PIN_DOUT  GPIO_NUM_7
#define SAMPLE_RATE 16000

/* EMBED_FILES 嵌入的三段提示音（V1-103 已在 CMakeLists 注册） */
extern const uint8_t _binary_sounds_chime_success_wav_start[] asm("_binary_sounds_chime_success_wav_start");
extern const uint8_t _binary_sounds_chime_success_wav_end[]   asm("_binary_sounds_chime_success_wav_end");
extern const uint8_t _binary_sounds_chime_error_wav_start[]   asm("_binary_sounds_chime_error_wav_start");
extern const uint8_t _binary_sounds_chime_error_wav_end[]     asm("_binary_sounds_chime_error_wav_end");
extern const uint8_t _binary_sounds_chime_notice_wav_start[]  asm("_binary_sounds_chime_notice_wav_start");
extern const uint8_t _binary_sounds_chime_notice_wav_end[]    asm("_binary_sounds_chime_notice_wav_end");

typedef struct {
    const char *name;
    const uint8_t *data;
    size_t len;
} sound_item_t;

static const sound_item_t SOUNDS[] = {
    { "chime_success", _binary_sounds_chime_success_wav_start,
      _binary_sounds_chime_success_wav_end - _binary_sounds_chime_success_wav_start },
    { "chime_error",   _binary_sounds_chime_error_wav_start,
      _binary_sounds_chime_error_wav_end - _binary_sounds_chime_error_wav_start },
    { "chime_notice",  _binary_sounds_chime_notice_wav_start,
      _binary_sounds_chime_notice_wav_end - _binary_sounds_chime_notice_wav_start },
};

static i2s_chan_handle_t s_tx = NULL;
static SemaphoreHandle_t s_lock = NULL;
static volatile bool s_abort = false;
static bool s_inited = false;
static uint32_t s_stream_total = 0;     /* audio_start 宣告的字节数 */
static uint32_t s_stream_consumed = 0;  /* 已写入 I2S 的字节数 */

/* ---------- 内部工具 ---------- */

static esp_err_t channel_reset(void)
{
    /* disable/enable 清空 DMA 残留，保证打断立即生效 */
    esp_err_t err = i2s_channel_disable(s_tx);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;
    err = i2s_channel_enable(s_tx);
    return err;
}

/* 把 mono 16bit 样本复制成双声道帧写入 I2S；返回写入的 mono 字节数（负=中止） */
static int write_mono_as_stereo(const uint8_t *mono, size_t mono_len)
{
    /* 块缓冲：256 样本 → 512 样本帧 = 2048 字节，约 16ms@16k */
    static int16_t frame_buf[512];
    const size_t samples_per_block = 256;
    size_t done = 0;

    while (done < mono_len) {
        if (s_abort) {
            return -(int)done;
        }
        size_t n = mono_len - done;
        if (n > samples_per_block * 2) n = samples_per_block * 2;   /* n 为字节数 */
        size_t samples = n / 2;
        for (size_t i = 0; i < samples; i++) {
            int16_t s;
            memcpy(&s, mono + done + i * 2, 2);
            frame_buf[i * 2] = s;      /* L */
            frame_buf[i * 2 + 1] = s;  /* R 同值，满音量 */
        }
        size_t bytes_written = 0;
        esp_err_t err = i2s_channel_write(s_tx, frame_buf,
                                          samples * 2 * sizeof(int16_t),
                                          &bytes_written, pdMS_TO_TICKS(500));
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "i2s write 失败: %s", esp_err_to_name(err));
            return -(int)done;
        }
        done += samples * 2;
    }
    return (int)done;
}

/* 跳过 WAV 头，返回 PCM 起始偏移（标准 44 字节头，兼容 chunk 扫描） */
static size_t wav_pcm_offset(const uint8_t *wav, size_t len)
{
    if (len < 44 || memcmp(wav, "RIFF", 4) != 0) {
        return 0;   /* 没有合法头就当裸 PCM 用 */
    }
    for (size_t i = 12; i + 8 <= len; ) {
        if (!memcmp(wav + i, "data", 4)) {
            return i + 8;
        }
        uint32_t sz = (uint32_t)wav[i + 4] | ((uint32_t)wav[i + 5] << 8)
                    | ((uint32_t)wav[i + 6] << 16) | ((uint32_t)wav[i + 7] << 24);
        i += 8 + sz + (sz & 1);
    }
    return 44;
}

/* ---------- 对外接口 ---------- */

esp_err_t i2s_player_init(void)
{
    if (s_inited) {
        return ESP_OK;
    }
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 8;
    chan_cfg.dma_frame_num = 511;          /* 每描述 ~32ms（双槽16bit） */
    esp_err_t err = i2s_new_channel(&chan_cfg, &s_tx, NULL);
    if (err != ESP_OK) return err;

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_BCLK,
            .ws   = PIN_LRC,
            .dout = PIN_DOUT,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    err = i2s_channel_init_std_mode(s_tx, &std_cfg);
    if (err != ESP_OK) return err;
    err = i2s_channel_enable(s_tx);
    if (err != ESP_OK) return err;

    s_lock = xSemaphoreCreateMutex();
    s_inited = true;
    ESP_LOGI(TAG, "I2S 播放器就绪: %dHz/16bit/stereo, BCLK=%d LRC=%d DIN=%d",
             SAMPLE_RATE, PIN_BCLK, PIN_LRC, PIN_DOUT);
    return ESP_OK;
}

int i2s_player_play_named(const char *sound)
{
    if (!s_inited) {
        ESP_LOGW(TAG, "播放器未初始化");
        return -1;
    }
    const sound_item_t *item = NULL;
    for (size_t i = 0; i < sizeof(SOUNDS) / sizeof(SOUNDS[0]); i++) {
        if (strcmp(SOUNDS[i].name, sound) == 0) {
            item = &SOUNDS[i];
            break;
        }
    }
    if (!item) {
        ESP_LOGW(TAG, "未知音效: %s", sound);
        return -1;
    }
    if (i2s_player_stream_begin(0) != ESP_OK) {
        return -1;                     /* 播放器忙（如正在播 TTS 流） */
    }
    size_t off = wav_pcm_offset(item->data, item->len);
    int played = write_mono_as_stereo(item->data + off, item->len - off);
    i2s_player_stream_end();
    ESP_LOGI(TAG, "音效 %s 播放%s（%d/%u 字节）", sound,
             played < 0 ? "被中止" : "完成", played < 0 ? -played : played,
             (unsigned)(item->len - off));
    return played;
}

/* ---------- V1-303 音频流会话 ---------- */

esp_err_t i2s_player_stream_begin(uint32_t total_bytes)
{
    if (!s_inited) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;        /* 播放器忙 */
    }
    s_abort = false;
    s_stream_total = total_bytes;
    s_stream_consumed = 0;
    channel_reset();
    return ESP_OK;
}

int i2s_player_feed_pcm(const uint8_t *pcm, size_t len)
{
    if (!s_inited || s_stream_total && s_stream_consumed >= s_stream_total) {
        return -1;
    }
    int played = write_mono_as_stereo(pcm, len);
    if (played > 0) {
        s_stream_consumed += (uint32_t)played;
    }
    return played;                     /* 被中止时为负 */
}

esp_err_t i2s_player_stream_end(void)
{
    s_stream_total = 0;
    s_stream_consumed = 0;
    if (s_lock) {
        xSemaphoreGive(s_lock);
    }
    return ESP_OK;
}

uint32_t i2s_player_stream_consumed(void)
{
    return s_stream_consumed;
}

void i2s_player_abort(void)
{
    s_abort = true;
}

bool i2s_player_busy(void)
{
    return s_lock && xSemaphoreGetMutexHolder(s_lock) != NULL;
}
