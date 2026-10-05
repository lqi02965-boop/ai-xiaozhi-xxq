/*
 * oled_display —— SSD1306 128x64 I2C OLED 状态屏实现（v1.6）
 *
 * 数据流：帧缓冲（1024B）→ 水平寻址模式 → 256B 一段 I2C 推送。
 * 接线：SDA→GPIO8  SCL→GPIO9  地址 0x3C。字库见 oled_font.h（自动生成）。
 */
#include "oled_display.h"
#include "oled_font.h"

#include <string.h>
#include "esp_log.h"
#include "driver/i2c_master.h"

static const char *TAG = "oled";

#define OLED_SDA   GPIO_NUM_8
#define OLED_SCL   GPIO_NUM_9
#define OLED_ADDR  0x3C
#define I2C_HZ     400000

#define FB_W 128
#define FB_H 64
#define FB_SIZE (FB_W * FB_H / 8)

static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_dev;
static uint8_t s_fb[FB_SIZE];
static bool s_ready;

/* 发命令（控制字节 0x00 + 命令流，单次 ≤31 字节命令） */
static esp_err_t oled_cmd(const uint8_t *cmds, size_t len)
{
    uint8_t buf[32];
    if (len > sizeof(buf) - 1) {
        return ESP_ERR_INVALID_SIZE;
    }
    buf[0] = 0x00;
    memcpy(buf + 1, cmds, len);
    return i2c_master_transmit(s_dev, buf, len + 1, 100);
}

/* 发显存数据（控制字节 0x40 + 数据，256B 一段） */
static esp_err_t oled_data(const uint8_t *data, size_t len)
{
    static uint8_t buf[257];

    while (len > 0) {
        size_t n = len > 256 ? 256 : len;
        buf[0] = 0x40;
        memcpy(buf + 1, data, n);
        esp_err_t err = i2c_master_transmit(s_dev, buf, n + 1, 100);
        if (err != ESP_OK) {
            return err;
        }
        data += n;
        len -= n;
    }
    return ESP_OK;
}

bool oled_init(void)
{
    i2c_master_bus_config_t bcfg = {
        .i2c_port = 0,
        .sda_io_num = OLED_SDA,
        .scl_io_num = OLED_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,   /* 模块自带排阻，内部上拉兜底 */
    };
    if (i2c_new_master_bus(&bcfg, &s_bus) != ESP_OK) {
        ESP_LOGE(TAG, "I2C 总线初始化失败");
        return false;
    }
    i2c_device_config_t dcfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = OLED_ADDR,
        .scl_speed_hz = I2C_HZ,
    };
    if (i2c_master_bus_add_device(s_bus, &dcfg, &s_dev) != ESP_OK) {
        return false;
    }
    /* 设备探测：总线上没有 0x3C 就直接认输，status 里如实上报 */
    if (i2c_master_probe(s_bus, OLED_ADDR, 100) != ESP_OK) {
        ESP_LOGW(TAG, "I2C 0x%02X 无应答（检查 SDA=%d SCL=%d 接线）",
                 OLED_ADDR, OLED_SDA, OLED_SCL);
        return false;
    }
    /* SSD1306 标准初始化序列（128x64，电荷泵，水平寻址） */
    static const uint8_t init_cmds[] = {
        0xAE,             /* display off */
        0xD5, 0x80,       /* clock div */
        0xA8, 0x3F,       /* multiplexer 64 */
        0xD3, 0x00,       /* display offset */
        0x40,             /* start line 0 */
        0x8D, 0x14,       /* charge pump on */
        0x20, 0x00,       /* 水平寻址模式（flush 连续推 1KB 不用翻页） */
        0xA1, 0xC8,       /* 段重映射 + COM 扫描反向（不翻转安装方向） */
        0xDA, 0x12,       /* COM pins */
        0x81, 0xCF,       /* contrast */
        0xD9, 0xF1,       /* precharge */
        0xDB, 0x40,       /* VCOM detect */
        0xA4, 0xA6,       /* RAM 内容 + 正常显示 */
        0xAF,             /* display on */
    };
    if (oled_cmd(init_cmds, sizeof(init_cmds)) != ESP_OK) {
        ESP_LOGW(TAG, "SSD1306 初始化命令失败");
        return false;
    }
    s_ready = true;
    oled_clear();
    oled_flush();
    ESP_LOGI(TAG, "SSD1306 就绪: 128x64 @ 0x%02X（SDA=%d SCL=%d）",
             OLED_ADDR, OLED_SDA, OLED_SCL);
    return true;
}

bool oled_ready(void)
{
    return s_ready;
}

void oled_clear(void)
{
    memset(s_fb, 0, sizeof(s_fb));
}

void oled_text(int line, const char *s)
{
    if (!s_ready || line < 0 || line > 7 || !s) {
        return;
    }
    uint8_t *page = s_fb + line * FB_W;
    int col = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p && col <= FB_W - 6; p++) {
        int idx = (*p >= OLED_FONT_FIRST && *p <= OLED_FONT_LAST)
                  ? *p - OLED_FONT_FIRST : (' ' - OLED_FONT_FIRST);
        memcpy(page + col, FONT6X8[idx], 6);
        col += 6;
    }
    for (; col < FB_W; col++) {
        page[col] = 0;    /* 清行尾残留 */
    }
}

void oled_flush(void)
{
    if (!s_ready) {
        return;
    }
    static const uint8_t range[] = {0x22, 0x00, 0x07};   /* 全屏窗口 */
    if (oled_cmd(range, sizeof(range)) != ESP_OK) {
        return;
    }
    oled_data(s_fb, sizeof(s_fb));
}

void oled_show_lines(const cJSON *lines)
{
    oled_clear();
    if (cJSON_IsArray(lines)) {
        int i = 0;
        const cJSON *it;
        cJSON_ArrayForEach(it, lines) {
            if (i >= 8) {
                break;
            }
            if (cJSON_IsString(it)) {
                oled_text(i++, it->valuestring);
            }
        }
    }
    oled_flush();
}
