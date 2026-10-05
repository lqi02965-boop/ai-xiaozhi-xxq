"""串口桥：自动识别 CDC 口、退避重连、JSON 帧收发（V1-206）。

dry_run=True 时不碰真实串口，把待发帧打印到日志——供无硬件自测（V1-M2 验收）。
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time

log = logging.getLogger("serial_bridge")


class SerialBridge:
    def __init__(self, cfg: dict, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self._q: "queue.Queue[dict | None]" = queue.Queue()
        self._lock = threading.Lock()
        self._seq = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="serial-bridge")
        self._connected = threading.Event()
        self._ser = None
        self._in_stream = False             # 音频流传输中（暂停心跳）
        # 板麦采集（v1.6）：mic_start/mic_stop 期间收 mic_data 帧并缓存 PCM
        self._mic_active = False
        self._mic_buf = bytearray()
        self._mic_lock = threading.Lock()
        self._rx_tail = b""                 # 会话内半行残留

    # ---- 板麦采集 ---------------------------------------------------------
    def mic_begin(self, sample_rate: int = 16000, shift: int = 14) -> None:
        """开始板麦录音：清缓存、标记活跃、向设备发 mic_start。"""
        with self._mic_lock:
            self._mic_buf.clear()
            self._mic_active = True
        self.send({"v": 1, "type": "mic_start",
                   "data": {"sr": sample_rate, "shift": shift}})

    def mic_end(self) -> bytes:
        """结束录音：停设备流、返回缓存的全量 PCM（16k/16bit/mono）。"""
        self.send({"v": 1, "type": "mic_stop", "data": {}})
        with self._mic_lock:
            self._mic_active = False
            data = bytes(self._mic_buf)
            self._mic_buf.clear()
        log.info("板麦录音结束: %d bytes ≈ %.1fs", len(data),
                 len(data) / 32000)
        return data

    def _handle_board_line(self, line: bytes) -> None:
        """解析设备上行帧：mic_data 缓存 PCM，其余（pong/ack）忽略。"""
        if not self._mic_active:
            return
        line = line.strip()
        if not line.startswith(b"{"):
            return
        try:
            frame = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return
        if frame.get("type") != "mic_data":
            return
        import base64

        try:
            pcm = base64.b64decode(frame.get("data", {}).get("pcm", ""))
        except Exception:
            return
        with self._mic_lock:
            if not self._mic_active:
                return
            if len(self._mic_buf) + len(pcm) > 2_000_000:   # 60s 上限防失控
                return
            self._mic_buf.extend(pcm)

    # ---- 对外接口 -------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)

    def send(self, frame: dict) -> None:
        """线程安全地投递一帧 JSON（seq 自动补齐递增）。"""
        with self._lock:
            self._seq += 1
            frame.setdefault("v", 1)
            frame["seq"] = self._seq
        self._q.put(frame)

    def send_audio_stream(self, pcm: bytes, chunk_size: int = 4096) -> None:
        """按协议 v1.2 停等流控下发音频：audio_start（含 chunk 大小）→
        每发一块等设备 ack → 下一块。流控由 ack 同步，天然无溢出。
        在 bridge 线程内同步执行（借用 _emit 机制），期间读写同一串口。"""
        with self._lock:
            self._seq += 1
            seq = self._seq
        header = {"v": 1, "type": "audio_start", "seq": seq,
                  "data": {"format": "pcm_16k_16bit_mono", "bytes": len(pcm),
                           "chunk": chunk_size, "interrupt": True}}

        if self.dry_run:
            log.info("[DRY-RUN] 音频流 %d bytes（audio_start + %d 块停等，数据略）",
                     len(pcm), (len(pcm) + chunk_size - 1) // chunk_size)
            return

        def _emit() -> None:
            self._in_stream = True              # 暂停心跳（ping 会污染音频流）
            try:
                self._write_raw((json.dumps(header) + "\n").encode("utf-8"))
                time.sleep(0.3)    # 让设备先处理 JSON 头（设备 20ms 轮询 + 余量）
                offset = 0
                while offset < len(pcm):
                    block = pcm[offset:offset + chunk_size]
                    self._write_raw(block)
                    got = self._wait_stream_ack(offset + len(block))
                    if got is None:
                        log.warning("块 ack 异常（offset=%d），中止本次语音下发", offset)
                        return
                    offset = got
                log.info("音频流下发完成: %d bytes", offset)
            finally:
                self._in_stream = False
                time.sleep(0.2)                # 缓冲，避免心跳立刻涌入

        self._q.put(("__raw__", _emit))   # 队列里放一个动作，保持发送串行化

    def _wait_stream_ack(self, expect_got: int, timeout: float = 5.0):
        """停等流控：等设备块 ack，返回实际收到字节数；失步返回 None。
        心跳 pong 等无关帧跳过继续等（不作为失败）。"""
        end = time.time() + timeout
        buf = b""
        while time.time() < end:
            try:
                c = self._ser.read(128) if self._ser else b""
            except Exception:
                return None
            if c:
                buf += c
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.decode("utf-8", "replace").strip()
                if line.startswith("{"):
                    try:
                        resp = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rtype = resp.get("type")
                    if rtype == "ack":
                        data = resp.get("data", {})
                        if data.get("ok"):
                            got = data.get("got", expect_got)
                            if got != expect_got:
                                log.warning("ack got=%s 与期望 %s 不符（字节失步）",
                                            got, expect_got)
                                return None
                            return got
                        log.warning("设备报告流失败: %s", line[:80])
                        return None
                    if rtype in ("pong", "status", "error"):
                        log.debug("流控等待期间收到 %s 帧，跳过", rtype)
                        continue
                    log.debug("流控忽略未知帧: %s", line[:60])
        log.warning("流控等待 ack 超时（%.1fs）", timeout)
        return None

    def _write_raw(self, data: bytes) -> None:
        if self._ser is not None:
            self._ser.write(data)
        else:
            log.debug("RAW >> %d bytes", len(data))

    def wait_connected(self, timeout: float | None = None) -> bool:
        return self._connected.wait(timeout)

    # ---- 内部实现 -------------------------------------------------------
    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _find_port(self) -> str | None:
        if self.cfg.get("port"):
            return self.cfg["port"]
        try:
            from serial.tools import list_ports
        except ImportError:
            log.error("未安装 pyserial，无法枚举串口")
            return None
        wanted = {(v.upper(), p.upper()) for v, p in self.cfg.get("vid_pid", [])}
        for p in list_ports.comports():
            vid, pid = f"{p.vid:04X}" if p.vid else "", f"{p.pid:04X}" if p.pid else ""
            if (vid, pid) in wanted:
                log.info("识别到设备: %s (VID:PID=%s:%s)", p.device, vid, pid)
                return p.device
        return None

    def _run(self) -> None:
        backoffs = self.cfg.get("reconnect_backoff_sec", [1, 2, 4, 8])
        attempt = 0
        while not self._stop.is_set():
            if self.dry_run:
                log.info("[DRY-RUN] 串口桥空转（不打开真实端口）")
                self._connected.set()
                self._drain_queue(lambda f: log.info("[DRY-RUN] 发送 >> %s",
                                                     json.dumps(f, ensure_ascii=False)))
                return
            port = self._find_port()
            if port is None:
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                log.debug("未找到设备串口，%ss 后重试", wait)
                self._stop.wait(wait)
                attempt += 1
                continue
            attempt = 0
            try:
                self._session(port)
            except Exception:
                # 会话内任何异常（拔线/写失败）都必须回到重连循环，
                # 否则串口线程整体死亡、永不重连（V1-401 实测教训）
                log.exception("串口会话异常退出，%ss 后重连", 2)
            self._stop.wait(2)

    def _session(self, port: str) -> None:
        """一次完整连接：打开串口、心跳、发送队列，异常退出走重连。"""
        import serial

        try:
            ser = serial.Serial(port, self.cfg.get("baud", 115200), timeout=0.2)
        except Exception as e:
            log.warning("打开 %s 失败: %s", port, e)
            self._stop.wait(2)
            return
        self._ser = ser
        log.info("串口已连接: %s", port)
        self._connected.set()
        try:
            ser.set_buffer_size(rx_size=131072)   # 板麦 90KB/s 流，默认 4K 会溢出
        except Exception:
            pass
        last_hb = 0.0
        try:
            while not self._stop.is_set():
                now = time.time()
                if now - last_hb >= self.cfg.get("heartbeat_sec", 2) \
                        and not self._in_stream:
                    try:
                        self._write(ser, {"v": 1, "type": "ping",
                                          "seq": self._next_seq(), "data": {}})
                    except Exception as e:
                        log.warning("心跳写入失败（疑似断线）: %s", e)
                        break       # 退出会话 → 上层重连
                    last_hb = now
                # 收线路：读设备上行（板麦 mic_data / pong / ack），残留半行留缓冲
                waiting = ser.in_waiting
                if waiting:
                    self._rx_tail += ser.read(waiting)
                    while b"\n" in self._rx_tail:
                        line, self._rx_tail = self._rx_tail.split(b"\n", 1)
                        self._handle_board_line(line)
                try:
                    item = self._q.get(timeout=0.05 if self._mic_active else 0.2)
                except queue.Empty:
                    continue
                if item is None:
                    break
                if isinstance(item, tuple):   # ("__raw__", 动作)
                    item[1]()
                else:
                    self._write(ser, item)
        finally:
            ser.close()
            self._ser = None
            self._connected.clear()
            log.warning("串口断开: %s", port)

    def _write(self, ser, frame: dict) -> None:
        data = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        ser.write(data)
        log.debug("发送 >> %s", data.decode("utf-8").strip())

    def _drain_queue(self, emit) -> None:
        """dry-run 模式：把队列里的帧交给 emit 打印。"""
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            if isinstance(item, tuple):
                item[1]()
            else:
                emit(item)
