# main 组件注册
#
# 用途：声明固件主组件；后续 V1-103 音频资源、V1-105 CDC 组件会在此追加依赖。
idf_component_register(SRCS "main.c"
                       INCLUDE_DIRS ".")
