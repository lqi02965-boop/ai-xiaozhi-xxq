# -*- coding: utf-8 -*-
"""gen_app_icon —— 生成云小小应用图标（GUI 窗口/任务栏/桌面快捷方式）

画法：小清新柑橘风——白云朵（呼应"云小小"）+ 渐变橙子 + 叶子 + 微笑表情，
配色与 companion_ui 的柚子橙/叶绿主题一致。

输出：
  pc_daemon/assets/yunxxq.ico （16~256 多尺寸，给窗口 iconbitmap）
  pc_daemon/assets/yunxxq.png （256px，给 iconphoto 兜底）

用法：python scripts/gen_app_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent.parent
ASSETS = BASE / "pc_daemon" / "assets"
SIZE = 512


def lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_orange_body(size: int) -> Image.Image:
    """垂直渐变橙子圆（柚子橙 #FFB74D → 深橙 #F57C00）。"""
    top, bottom = (255, 201, 102), (245, 124, 0)
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    cx, cy, r = size // 2, int(size * 0.58), int(size * 0.33)
    y0, y1 = cy - r, cy + r
    for y in range(size):
        t = min(max((y - y0) / (y1 - y0), 0.0), 1.0)
        color = lerp(top, bottom, t)
        for x in range(size):
            px[x, y] = color + (255,)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    body = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    body.paste(grad, (0, 0), mask)
    return body


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- 橙子主体 ---
    img.alpha_composite(make_orange_body(SIZE))
    r = int(SIZE * 0.33)
    cx, cy = SIZE // 2, int(SIZE * 0.58)

    # 高光（左上斜椭圆，半透明白）
    shine = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shine).ellipse((cx - r * 0.72, cy - r * 0.72,
                                   cx - r * 0.18, cy - r * 0.30),
                                  fill=(255, 255, 255, 95))
    img.alpha_composite(shine)

    # --- 表情（陪伴感） ---
    eye_y = cy - r * 0.05
    for ex in (cx - r * 0.34, cx + r * 0.34):
        d.ellipse((ex - 11, eye_y - 12, ex + 11, eye_y + 12), fill=(93, 64, 55))
    d.arc((cx - r * 0.30, eye_y + 8, cx + r * 0.30, eye_y + r * 0.52),
          start=25, end=155, fill=(93, 64, 55), width=9)
    for bx in (cx - r * 0.62, cx + r * 0.62):   # 腮红
        d.ellipse((bx - 16, eye_y + 26, bx + 16, eye_y + 48),
                  fill=(248, 187, 208, 150))

    # --- 叶子 + 果蒂（右上，从云朵后探出来） ---
    leaf = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ld = ImageDraw.Draw(leaf)
    ld.ellipse((cx + 40, cy - r - 66, cx + 176, cy - r - 2), fill=(85, 139, 47))
    leaf = leaf.rotate(-26, center=(cx + 108, cy - r - 34), resample=Image.BICUBIC)
    img.alpha_composite(leaf)
    d.line((cx + 52, cy - r - 16, cx + 88, cy - r + 22), fill=(109, 76, 65), width=10)

    # --- 白云朵（顶部，压住橙子顶边，呼应"云小小"） ---
    puff = [(cx - 108, 96, 46), (cx - 30, 72, 62), (cx + 52, 92, 48)]
    for (px_, py_, pr_) in puff:                 # 底部对齐成云底
        pr_ += 14
        d.ellipse((px_ - pr_, py_ + 26 - pr_, px_ + pr_, py_ + 26 + pr_),
                  fill=(236, 239, 241))          # 淡灰影子层
    for (px_, py_, pr_) in puff:
        d.ellipse((px_ - pr_, py_ - pr_, px_ + pr_, py_ + pr_), fill=(255, 255, 255))
    d.rounded_rectangle((cx - 122, 96, cx + 100, 150), radius=26, fill=(255, 255, 255))

    # --- 导出 ---
    png_path = ASSETS / "yunxxq.png"
    img.save(png_path)
    ico_path = ASSETS / "yunxxq.ico"
    img.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                              (64, 64), (128, 128), (256, 256)])
    print("已生成：", png_path, ico_path)


if __name__ == "__main__":
    main()
