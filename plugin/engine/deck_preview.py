#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""课件预览：把生成的 pptx 读回来，用 PIL 画成 PNG，用于交付前自检。

为什么要自己画一遍：这台机器上 LibreOffice 是个空壳、PowerPoint 的 COM 又被沙箱挡着，
看不到成品就只能靠"我算过了"来自证，那不是验证。渲染器只实现本插件自己用到的几种
文本框（左对齐、自动换行、行距、字号），足够回答两个问题：

  1. 文字有没有溢出所在文本框／页面；
  2. 字号是否小到看不清。

报告里给出每页每个文本框的"需要高度 / 可用高度"比值，比值 > 1 就是溢出。

用法：
    python deck_preview.py --pptx deck.pptx --out preview_dir [--only 5,6,11]
"""
from __future__ import annotations

import argparse
import json
import os

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Emu

EMU_PER_INCH = 914400
DPI = 110

FONT_FILES = {
    ("微软雅黑", False): (r"C:\Windows\Fonts\msyh.ttc", 0),
    ("微软雅黑", True): (r"C:\Windows\Fonts\msyhbd.ttc", 0),
    ("宋体", False): (r"C:\Windows\Fonts\simsun.ttc", 0),
    ("宋体", True): (r"C:\Windows\Fonts\simsun.ttc", 0),
}


def load_font(name: str, bold: bool, size_px: int) -> ImageFont.FreeTypeFont:
    path, index = FONT_FILES.get((name, bold)) or FONT_FILES[("微软雅黑", bold)]
    try:
        return ImageFont.truetype(path, size_px, index=index)
    except Exception:  # noqa: BLE001 - 缺字体时退回默认字体，预览仍可用
        return ImageFont.load_default()


def font_px(size_pt: float) -> int:
    return max(6, int(round(size_pt * DPI / 72.0)))


def wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        buf = ""
        for ch in raw:
            probe = buf + ch
            if draw.textlength(probe, font=font) > max_w and buf:
                lines.append(buf)
                buf = ch
            else:
                buf = probe
        lines.append(buf)
    return lines


def render_slide(slide, width_px: int, height_px: int) -> tuple[Image.Image, list[dict]]:
    img = Image.new("RGB", (width_px, height_px), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    report: list[dict] = []

    for shape in slide.shapes:
        x = int(shape.left / EMU_PER_INCH * DPI)
        y = int(shape.top / EMU_PER_INCH * DPI)
        w = int(shape.width / EMU_PER_INCH * DPI)
        h = int(shape.height / EMU_PER_INCH * DPI)

        if shape.has_table:
            table = shape.table
            col_w = w / max(1, len(table.columns))
            row_h = h / max(1, len(table.rows))
            for r, row in enumerate(table.rows):
                for c, cell in enumerate(row.cells):
                    cx, cy = int(x + c * col_w), int(y + r * row_h)
                    draw.rectangle([cx, cy, cx + int(col_w), cy + int(row_h)], outline=(214, 218, 220))
                    text = cell.text_frame.text
                    size = cell.text_frame.paragraphs[0].runs[0].font.size if cell.text_frame.paragraphs[0].runs else None
                    fpx = font_px(size.pt if size else 11)
                    font = load_font("微软雅黑", r == 0, fpx)
                    lines = wrap(draw, text, font, int(col_w) - int(0.24 * DPI))
                    ty = cy + int(0.1 * DPI)
                    for line in lines[: max(1, int(row_h / (fpx * 1.3)))]:
                        draw.text((cx + int(0.12 * DPI), ty), line, font=font, fill=(31, 78, 99) if r == 0 else (74, 74, 74))
                        ty += int(fpx * 1.3)
            continue

        has_text = shape.has_text_frame and shape.text_frame.text.strip() != ""

        if not has_text:
            # 分隔线／色块：没有文字的自动图形
            try:
                color = shape.fill.fore_color.rgb
                draw.rectangle([x, y, x + max(1, w), y + max(1, h)], fill=(color[0], color[1], color[2]))
            except Exception:  # noqa: BLE001
                pass
            continue

        if not shape.has_text_frame:
            continue
        frame = shape.text_frame
        paras = []
        for para in frame.paragraphs:
            text = "".join(run.text for run in para.runs)
            if not text:
                continue
            run = para.runs[0]
            size = run.font.size.pt if run.font.size else 18.0
            name = run.font.name or "微软雅黑"
            bold = bool(run.font.bold)
            color = run.font.color.rgb if run.font.color and run.font.color.type is not None else None
            rgb = (color[0], color[1], color[2]) if color else (26, 26, 26)
            spacing = para.line_spacing if isinstance(para.line_spacing, float) else 1.3
            paras.append((text, size, name, bold, rgb, spacing))

        needed = 0
        for text, size, name, bold, rgb, spacing in paras:
            font = load_font(name, bold, font_px(size))
            lines = wrap(draw, text, font, w)
            needed += len(lines) * font_px(size) * spacing
        if w > 0 and h > 0:
            report.append(
                {
                    "box": [round(x / DPI, 2), round(y / DPI, 2), round(w / DPI, 2), round(h / DPI, 2)],
                    "text": paras[0][0][:26] if paras else "",
                    "size_pt": paras[0][1] if paras else None,
                    "needed_px": int(needed),
                    "avail_px": h,
                    "overflow": needed > h * 1.02 or (y + needed) > height_px,
                }
            )

        ty = y
        for text, size, name, bold, rgb, spacing in paras:
            font = load_font(name, bold, font_px(size))
            step = font_px(size) * spacing
            for line in wrap(draw, text, font, w):
                if ty + step > height_px:
                    draw.text((x, ty), "▼ 溢出", font=load_font("微软雅黑", True, font_px(9)), fill=(200, 40, 40))
                    break
                draw.text((x, ty), line, font=font, fill=rgb)
                ty += step

    return img, report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default=None, help="逗号分隔的页码，只渲染这些页")
    args = ap.parse_args()

    prs = Presentation(args.pptx)
    os.makedirs(args.out, exist_ok=True)
    width_px = int(prs.slide_width / EMU_PER_INCH * DPI)
    height_px = int(prs.slide_height / EMU_PER_INCH * DPI)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    problems = []
    rendered = []
    for i, slide in enumerate(prs.slides, 1):
        img, report = render_slide(slide, width_px, height_px)
        name = f"slide_{i:02d}.png"
        if only is None or i in only:
            img.save(os.path.join(args.out, name))
            rendered.append(name)
        for item in report:
            if item["overflow"]:
                problems.append({"slide": i, **item})

    print(
        json.dumps(
            {
                "ok": True,
                "slides": len(prs.slides._sldIdLst),
                "rendered": rendered,
                "overflow_count": len(problems),
                "problems": problems[:12],
                "out": os.path.abspath(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
