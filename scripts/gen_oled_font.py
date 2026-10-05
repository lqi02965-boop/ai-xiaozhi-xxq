# -*- coding: utf-8 -*-
"""gen_oled_font.py —— 生成 SSD1306 的 6x8 ASCII 字库（C 数组）+ 预览图

用 PIL 内置字体渲染 0x20~0x7E，阈值化后按列打包（LSB=顶行，与 SSD1306 页
寻址的字节位序一致），输出 oled_font.h 和预览 PNG（人工目检字形用）。

用法：python scripts/gen_oled_font.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT_H = BASE / "firmware" / "agent_speaker" / "main" / "oled_font.h"
OUT_PNG = BASE / "pc_daemon" / "logs" / "oled_font_preview.png"

CELL_W, CELL_H = 6, 8
FIRST, LAST = 0x20, 0x7E


def render_char(font, ch: str) -> list[int]:
    """渲染单字符 → 6 字节列数据（bit0=顶行）。

    用 PIL 内置位图字体（经典 6x11 点阵）：绘制后裁出第 2~9 行（8 行主体），
    本身就是纯黑白，无抗锯齿，字形最干净（三方案对比实测选中）。
    """
    img = Image.new("L", (CELL_W, CELL_H + 4), 0)
    d = ImageDraw.Draw(img)
    d.text((0, 2), ch, font=font, fill=255)
    img = img.crop((0, 2, CELL_W, 2 + CELL_H)).point(lambda v: 255 if v > 140 else 0)
    px = img.load()
    cols = []
    for x in range(CELL_W):
        b = 0
        for y in range(CELL_H):
            if px[x, y]:
                b |= 1 << y
        cols.append(b)
    return cols


def main() -> None:
    font = ImageFont.load_default()                  # 经典位图字体（6x11 点阵）
    glyphs = {}
    for code in range(FIRST, LAST + 1):
        glyphs[code] = render_char(font, chr(code))

    lines = ["/* oled_font.h —— SSD1306 6x8 ASCII 字库（自动生成，勿手改）",
             " *",
             " * 生成：python scripts/gen_oled_font.py（PIL 内置字体渲染+阈值化）",
             " * 布局：每字符 6 字节列数据，LSB=顶行，与 SSD1306 页字节位序一致。",
             " * 覆盖 0x20~0x7E；越界字符显示空格。中文不在字库内（OLED v1）。",
             " */",
             "#ifndef _OLED_FONT_H_",
             "#define _OLED_FONT_H_",
             "",
             "#define OLED_FONT_FIRST 0x20",
             "#define OLED_FONT_LAST  0x7E",
             "#define OLED_CHAR_W     6",
             "",
             "static const unsigned char FONT6X8[96][6] = {"]
    for code in range(FIRST, LAST + 1):
        g = glyphs[code]
        esc = chr(code).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    /* {esc} */ {{{", ".join(f"0x{b:02X}" for b in g)}}},')
    lines += ["};", "", "#endif /* _OLED_FONT_H_ */", ""]
    OUT_H.write_text("\n".join(lines), encoding="utf-8")

    # 预览图：拼一行样例字
    sample = "YunXiaoXiao v1.6 ready! ABCxyz 0123"
    prev = Image.new("L", (CELL_W * len(sample) + 8, CELL_H + 8), 0)
    for i, ch in enumerate(sample):
        g = glyphs.get(ord(ch), glyphs[0x20])
        for x in range(CELL_W):
            for y in range(CELL_H):
                if g[x] >> y & 1:
                    prev.putpixel((4 + i * CELL_W + x, 4 + y), 255)
    prev = prev.resize((prev.width * 3, prev.height * 3), Image.NEAREST)
    prev.save(OUT_PNG)
    print(f"字库已生成: {OUT_H}")
    print(f"预览图: {OUT_PNG}")


if __name__ == "__main__":
    main()
