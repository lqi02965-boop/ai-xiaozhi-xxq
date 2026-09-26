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

    def send_audio_stream(self, pcm: bytes) -> None:
        """按协议 v1.1 下发音频流：audio_start JSON → 原始 PCM 字节。

        设备按 audio_start 里的 bytes 计数消费，收满自动回 ack。
        分块写入 + 微延迟，避免塞满设备端 CDC 接收缓冲。
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
        header = {"v": 1, "type": "audio_start", "seq": seq,
                  "data": {"format": "pcm_16k_16bit_mono", "bytes": len(pcm),
                           "interrupt": True}}
        chunk = 1024
        delay = 0.001

        if self.dry_run:
            log.info("[DRY-RUN] 音频流 %d bytes（audio_start 头 + 原始 PCM %d 块，数据略）",
                     len(pcm), (len(pcm) + chunk - 1) // chunk)
            return

        def _emit() -> None:
            self._write_raw((json.dumps(header) + "\n").encode("utf-8"))
            time.sleep(0.05)   # 让设备先处理 JSON 头
            total = 0
            for i in range(0, len(pcm), chunk):
                self._write_raw(pcm[i:i + chunk])
                total += min(chunk, len(pcm) - i)
                time.sleep(delay)
            log.info("音频流下发完成: %d bytes（%d 块）", total, (len(pcm) + chunk - 1) // chunk)

        self._q.put(("__raw__", _emit))   # 队列里放一个动作，保持发送串行化

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
            self._session(port)

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
        last_hb = 0.0
        try:
            while not self._stop.is_set():
                now = time.time()
                if now - last_hb >= self.cfg.get("heartbeat_sec", 2):
                    self._write(ser, {"v": 1, "type": "ping",
                                      "seq": self._next_seq(), "data": {}})
                    last_hb = now
                try:
                    item = self._q.get(timeout=0.2)
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
