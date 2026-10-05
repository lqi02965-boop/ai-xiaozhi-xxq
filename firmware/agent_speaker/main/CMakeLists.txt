# main 组件注册
#
# 用途：声明固件主组件与依赖：
#   - cdc_link.c     USB-CDC 指令通道（V1-105）
#   - cJSON.c        内置 JSON 解析（IDF v6.1 移除了内置 json 组件，
#                    源码从 xiaozhi-esp32 的 managed_components 中复制，MIT 许可）
#   - EMBED_FILES    三段提示音 WAV 直接嵌入固件（V1-103，播放代码在 V1-104 接入）
#   - esp_driver_usb_serial_jtag   USB-Serial-JTAG CDC 驱动
idf_component_register(SRCS "main.c" "cdc_link.c" "cJSON.c" "i2s_player.c" "mic_in.c"
                       INCLUDE_DIRS "."
                       EMBED_FILES "sounds/chime_success.wav"
                                   "sounds/chime_error.wav"
                                   "sounds/chime_notice.wav"
                       REQUIRES esp_driver_usb_serial_jtag esp_app_format esp_driver_i2s mbedtls)
