# V1-104 / V1-303 接入指南（待焊接后执行）

> 状态：`i2s_player.h/c` 已写好（盲写，未编译未上板），本文件是接入现有代码的
> **精确改动清单**。执行时按顺序粘贴 → 编译 → 烧录 → `cdc_probe` 验收。
> 预计耗时：编译 3 分钟 + 验收 5 分钟。

## 1. main/CMakeLists.txt（2 处）

```cmake
idf_component_register(SRCS "main.c" "cdc_link.c" "cJSON.c" "i2s_player.c"
                       ...
                       REQUIRES esp_driver_usb_serial_jtag esp_app_format esp_driver_i2s)
```

## 2. main/main.c（1 处）

在 `cdc_link_start()` 之前加：

```c
#include "i2s_player.h"
...
    if (i2s_player_init() != ESP_OK) {          /* V1-102：I2S+功放就绪 */
        ESP_LOGE(TAG, "I2S 播放器初始化失败");
    }
```

## 3. main/cdc_link.c（3 处）

### 3.1 头部

```c
#include "i2s_player.h"
static volatile bool s_raw_mode = false;        /* V1-303 音频流消费中 */
static uint32_t s_raw_total = 0;
```

### 3.2 handle_play_sound() 替换为

```c
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
    i2s_player_play_named(sound->valuestring);  /* 阻塞 ~0.3s，可接受 */
    cdc_send_ack(peer_seq, true);
}
```

### 3.3 新增 audio_start / audio_stop 处理 + RX 循环分流

在 handle_line 的分发链里加：

```c
    } else if (strcmp(type->valuestring, "audio_start") == 0) {
        const cJSON *data = cJSON_GetObjectItem(root, "data");
        uint32_t bytes = (uint32_t)cJSON_GetObjectItem(data, "bytes")->valuedouble;
        if (cJSON_IsTrue(cJSON_GetObjectItem(data, "interrupt"))) i2s_player_abort();
        if (i2s_player_stream_begin(bytes) != ESP_OK) {
            cdc_send_error(peer_seq, 403, "player busy");
            return;
        }
        s_raw_total = bytes;
        s_raw_mode = true;                      /* 之后字节直达 I2S，不再当 JSON */
        ESP_LOGI(TAG, "audio_start: %lu bytes", (unsigned long)bytes);
    } else if (strcmp(type->valuestring, "audio_stop") == 0) {
        i2s_player_abort();
        cdc_send_ack(peer_seq, true);
```

cdc_rx_task 的字节循环里，`if (c == '\n')` 之前加分流：

```c
            if (s_raw_mode) {                   /* V1-303：原始 PCM 直通 I2S */
                int w = i2s_player_feed_pcm(&c, 1);
                if (w < 0 || i2s_player_stream_consumed() >= s_raw_total) {
                    bool done = (w > 0) && i2s_player_stream_consumed() >= s_raw_total;
                    i2s_player_stream_end();
                    s_raw_mode = false;
                    cdc_send_ack(0, done);      /* 收满=ack，中止=ack ok:false */
                }
                continue;
            }
```

> 性能注：逐字节 feed 太碎，联调时若 CPU 吃紧改为"攒 256 字节再喂"（~8ms 块）。

## 4. 验收（cdc_probe.py 加两个用例）

1. `play_sound` → ack（**接了功放会出声**，这就是 V1-102 的"第一声"）
2. `audio_start(bytes=N)` + 发 N 字节正弦 PCM → ack ok=true（接了功放会听到 TTS 语音）

## 5. 里程碑判定

- 上述全绿 = V1-102/104/303 完成 → `audio_output` 切 `"device"` → V1-304 全自动闭环 → v1 收官
