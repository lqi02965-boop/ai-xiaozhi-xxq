# PC ↔ ESP32-S3 通信协议 v1.1

> 任务来源：M0-05 ｜ 状态：v1.1（2026-09-26 增加音频流，适配 v1「Agent 监视语音提示器」）
> 关联：V1-105（CDC 指令通道）、V1-302/303（音频流两端实现）

## 0. v1.1 变更摘要

- 新增 `play_sound`（本地提示音）与音频流三帧 `audio_start` / 原始 PCM / `audio_end`
- `agent_event` 保留给 v2 全量版；v1 使用 `play_sound` + 音频流组合
- 错误码新增 403（设备忙可打断语义见 3.4）

## 1. 传输层

| 项 | 规定 |
| --- | --- |
| 物理链路 | ESP32-S3 `USB` 口（原生 USB-Serial-JTAG CDC），PC 侧枚举为虚拟 COM 口 |
| 备用链路 | `COM` 口 CH343（UART0，留烧录/日志；若 CDC 掉线问题严重，协议本body不变仅换端口） |
| 帧格式 | 行分隔 JSON：一帧 = 一行 UTF-8 JSON + `\n`（0x0A），**不带 BOM、无 CRLF** |
| 帧上限 | 单帧 ≤ 512 字节（含 `\n`），超长直接丢弃并回 `error 402` |
| 波特率 | CDC 忽略波特率，pyserial 固定填 115200 保持兼容 |
| 字符集 | UTF-8；`text` 字段最长 64 字节 |

## 2. 帧信封（所有帧统一）

```json
{"v":1, "type":"<消息类型>", "seq":<发送方自增序号>, "data":{ <负载> }}
```

- `v`：协议版本，当前 1。收到不认识的 `v` 或 `type`，回 `error 401`，不中断连接。
- `seq`：uint32 自增，用于 `ack` 回执关联与丢帧检测。
- 接收方对需要确认的帧回 `ack`（带 `ref_seq`）；简单帧（ping/心跳）可不回。

## 3. PC → ESP32 指令集

### 3.1 `agent_event` — Agent 事件播报（原子组合指令）
```json
{"v":1,"type":"agent_event","seq":1,"data":{
  "text":"代码写完啦，快来看看！",
  "emotion":"happy",
  "servo_action":"nod",
  "sound":"chime_success"
}}
```
语义：ESP32 收到后按「表情 → 动作 → 音效 → 屏幕滚动文本」编排执行（M2-20）。任一字段可省略。

### 3.2 单项控制指令
```json
{"v":1,"type":"emotion","seq":2,"data":{"emotion":"thinking"}}
{"v":1,"type":"servo_action","seq":3,"data":{"action":"shake","speed":2}}
{"v":1,"type":"volume","seq":4,"data":{"level":60}}
{"v":1,"type":"brightness","seq":5,"data":{"level":80}}
{"v":1,"type":"persona_mode","seq":6,"data":{"mode":"default"}}
{"v":1,"type":"sleep","seq":7,"data":{}}
{"v":1,"type":"wake","seq":8,"data":{}}
```

### 3.3 `play_sound` — 本地提示音（v1 主用）
```json
{"v":1,"type":"play_sound","seq":9,"data":{"sound":"chime_success","interrupt":true}}
```
`sound` 枚举见第 7 节；`interrupt=true` 时打断当前播放立即换曲。

### 3.4 音频流（v1.1 新增，TTS 语音播报用）
**三步走**：先 JSON 宣告 → 切原始字节流 → JSON 收尾。

```json
{"v":1,"type":"audio_start","seq":10,"data":{"format":"pcm_16k_16bit_mono","bytes":152048,"interrupt":true}}
```
随后 PC **直接写原始 PCM 字节**（不再是 JSON、无分隔符），设备按 `bytes` 计数消费，收满后自动回到 JSON 行模式并回：
```json
{"v":1,"type":"ack","seq":11,"data":{"ref_seq":10,"ok":true}}
```
中途取消：PC 发 `{"type":"audio_stop"}`（JSON 行随时有效，设备在流中检测到该行也接受——实现上设备在流消费循环中旁路扫描 `\n` 结尾的 `audio_stop` 行）。`interrupt=true` 表示打断当前播放。

### 3.5 `ping` — 心跳
```json
{"v":1,"type":"ping","seq":9,"data":{}}
```

## 4. ESP32 → PC 上报集

### 4.1 `sensor_data` — 传感器快照（1Hz）
```json
{"v":1,"type":"sensor_data","seq":100,"data":{
  "temp_c":26.4, "humi":58, "distance_cm":35.2
}}
```
`distance_cm` 无效时填 `-1`；DHT11 读数失败时 `temp_c`/`humi` 填 `-1`。

### 4.2 `event` — 本地事件上报
```json
{"v":1,"type":"event","seq":101,"data":{"event":"near_enter"}}
```
事件枚举：`near_enter`（<20cm）/ `near_exit` / `dht_error`。

### 4.3 `status` — 设备状态（30s 一次 + 应答式）
```json
{"v":1,"type":"status","seq":102,"data":{
  "state":"idle","uptime_s":3600,"rssi":-52,"heap_free":180000,
  "fw":"0.1.0","proto":1
}}
```

### 4.4 `ack` / `error` / `pong`
```json
{"v":1,"type":"ack","seq":103,"data":{"ref_seq":1,"ok":true}}
{"v":1,"type":"error","seq":104,"data":{"ref_seq":5,"code":402,"msg":"missing field: level"}}
{"v":1,"type":"pong","seq":105,"data":{}}
```

## 5. 错误码

| code | 含义 | 处理 |
| --- | --- | --- |
| 400 | JSON 解析失败 | 丢弃该行，回 error，继续读下一行 |
| 401 | 未知 type 或版本 | 回 error，不中断 |
| 402 | 字段缺失/超限 | 回 error |
| 403 | 播放器忙且未允许打断 | 回 error，PC 可重发 interrupt=true |
| 500 | 内部执行失败 | 回 error，尽力保持服务 |

## 6. 心跳与重连约定

- PC 每 **2s** 发 `ping`；ESP32 必回 `pong`。PC 侧连续 3 次未收到 `pong`（≈6s）判离线。
- ESP32 每 **5s** 未收到任何 PC 帧即判 PC 离线，UI 切 `sleepy` 并停止动作。
- **重连由 PC 侧负责**：ESP32 的 USB-CDC 在芯片重启时端口会消失重现（风险 R3），PC 侧用 1s/2s/4s/8s 退避轮询端口重开（M3-02、M5-03）。
- 重连成功后 PC 先发 `status` 拉取请求（`{"type":"status"}`），再发当前音量/亮度同步。

## 7. 枚举表（两侧共用，改这里=改协议）

| 枚举 | 取值 |
| --- | --- |
| emotion | `idle` `listening` `thinking` `speaking` `happy` `sad` `sleepy` `surprised` |
| servo_action | `nod` `shake` `look_up` `look_down` `look_left` `look_right` `reset` |
| sound | `chime_success` `chime_error` `chime_notice` `none` |
| persona_mode | `default` `assistant` `cute` `silent` |

## 8. 设计权衡记录

- **为什么用行分隔 JSON 而非二进制帧**：原计划书 ESP32↔STM32 链路用二进制帧（带宽紧）；PC↔ESP32 场景带宽充裕（USB 2.0）、调试可读性优先，`python -m serial.tools.miniterm` 直接可看。日志即协议，问题定位成本最低。
- **为什么 agent_event 是组合指令**：避免 PC 连发 3 条指令产生时序竞争；ESP32 侧一个队列原子执行（对应 M2-20 编排任务）。
- **为什么 ack 只对指令类帧**：传感器上报是流式的，逐帧 ack 会让 1Hz 上报变成乒乓，无意义。
