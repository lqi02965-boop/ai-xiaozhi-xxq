# -*- coding: utf-8 -*-
"""capture_ui.py —— 截取云小小 GUI 窗口（含标题栏）存 PNG，验证图标用。"""
import ctypes
from ctypes import wintypes
from pathlib import Path

import PIL.Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 2


def find_window():
    """枚举顶层窗口，找标题含『云小小』的可见窗口。"""
    result = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if "云小小" in buf.value and user32.IsWindowVisible(hwnd):
                result.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return result[0] if result else None


def capture(hwnd, out_path: str) -> None:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    info = ctypes.create_string_buffer(40)
    ctypes.memset(info, 0, 40)
    import struct
    struct.pack_into("<iiiHHIIiiII", info, 0, 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    gdi32.GetDIBits(mem, bmp, 0, h, None, info, 0)  # 填充默认调色板
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, info, 0)
    img = PIL.Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    img.convert("RGB").save(out_path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    print(f"printwindow={ok} size={w}x{h} -> {out_path}")


if __name__ == "__main__":
    found = find_window()
    if not found:
        raise SystemExit("未找到云小小窗口")
    hwnd, title = found
    out = Path(__file__).resolve().parent.parent / "pc_daemon" / "logs" / "ui_icon_check.png"
    capture(hwnd, str(out))
    print("窗口标题:", title)
