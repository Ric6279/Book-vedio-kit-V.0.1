#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Markdown → Word（.docx），按中文文稿的排版习惯来。

字号按中文字号制给（三号 16pt / 小三 15pt / 四号 14pt / 小四 12pt / 五号 10.5pt）：

  标题一级   三号  微软雅黑 加粗 深青（提亮）
  标题二级   小三  微软雅黑 加粗 深青
  标题三级   四号  微软雅黑 加粗 墨色
  正文       小四  宋体   行距 1.5
  引用（>）  小四  宋体   左缩进 + 浅底纹  ← 原文一律走这里
  行内 **粗**      同段落字号、加深并提亮，用来标重点

之所以自己写而不调 pandoc：一是这台机器上没有 pandoc；二是中文 Word 排版的关键
在 w:eastAsia 字体设置与中文字号，通用转换器给出的往往是西文字号与默认宋体，
打开还要手改一遍。

用法：
    python md_to_docx.py --md 文稿.md --out 文稿.docx [--title "标题"]
"""
from __future__ import annotations

import argparse
import json
import os
import re

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

SERIF = "宋体"
SANS = "微软雅黑"
ACCENT = RGBColor(0x1F, 0x4E, 0x63)
INK = RGBColor(0x1A, 0x1A, 0x1A)
QUOTE_INK = RGBColor(0x2B, 0x2B, 0x2B)
MUTED = RGBColor(0x6B, 0x6B, 0x6B)

SZ_H1, SZ_H2, SZ_H3, SZ_H4 = 16, 15, 14, 12
SZ_BODY = 12
SZ_SMALL = 10.5


def style_run(run, *, font=SERIF, size=SZ_BODY, bold=False, color=INK, italic=False):
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), font)


def shade(paragraph, fill="F5F3EF"):
    ppr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    ppr.append(shd)


def left_bar(paragraph, color="C9C2B6"):
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), color)
    borders.append(left)
    ppr.append(borders)


INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+?`)")


def add_inline(paragraph, text, *, font=SERIF, size=SZ_BODY, color=INK):
    """把 **粗体** 与 `代码` 拆成 run；粗体顺便提亮，充当"重点"。"""
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            run = paragraph.add_run(piece[2:-2])
            style_run(run, font=font, size=size, bold=True, color=ACCENT)
        elif piece.startswith("`") and piece.endswith("`"):
            run = paragraph.add_run(piece[1:-1])
            style_run(run, font="Consolas", size=size - 1, color=MUTED)
        else:
            run = paragraph.add_run(piece)
            style_run(run, font=font, size=size, color=color)


def setup(doc):
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.4)
    sec.left_margin = sec.right_margin = Cm(2.6)
    normal = doc.styles["Normal"]
    normal.font.name = SERIF
    normal.font.size = Pt(SZ_BODY)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), SERIF)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(4)


def heading(doc, text, level):
    sizes = {1: SZ_H1, 2: SZ_H2, 3: SZ_H3, 4: SZ_H4}
    size = sizes.get(level, SZ_H4)
    p = doc.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_before = Pt(16 if level == 1 else (12 if level == 2 else 9))
    fmt.space_after = Pt(6 if level <= 2 else 4)
    fmt.line_spacing = 1.3
    add_inline(p, text, font=SANS, size=size, color=ACCENT if level <= 3 else INK)
    for run in p.runs:
        run.font.bold = True
    return p


def flush_table(doc, rows):
    if not rows:
        return
    cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r, row in enumerate(rows):
        for c in range(cols):
            cell = table.cell(r, c)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing = 1.25
            p.paragraph_format.space_after = Pt(0)
            add_inline(p, row[c] if c < len(row) else "", size=SZ_SMALL, color=ACCENT if r == 0 else INK)
            if r == 0:
                for run in p.runs:
                    run.font.bold = True
    doc.add_paragraph()


def convert(md_path: str, out_path: str, title: str | None) -> dict:
    with open(md_path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    doc = Document()
    setup(doc)

    stats = {"h": 0, "p": 0, "quote": 0, "list": 0, "table": 0}
    table_rows: list[list[str]] = []

    def flush():
        nonlocal table_rows
        if table_rows:
            flush_table(doc, table_rows)
            stats["table"] += 1
            table_rows = []

    for raw in lines:
        line = raw.rstrip()

        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            table_rows.append(cells)
            continue
        flush()

        if not line.strip():
            continue
        if re.fullmatch(r"-{3,}", line.strip()):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            heading(doc, m.group(2).strip(), min(len(m.group(1)), 4))
            stats["h"] += 1
            continue

        if line.startswith(">"):
            text = line.lstrip(">").strip()
            p = doc.add_paragraph()
            fmt = p.paragraph_format
            fmt.left_indent = Cm(0.75)
            fmt.right_indent = Cm(0.3)
            fmt.space_before = Pt(3)
            fmt.space_after = Pt(3)
            fmt.line_spacing = 1.5
            add_inline(p, text, font=SERIF, size=SZ_BODY, color=QUOTE_INK)
            shade(p)
            left_bar(p)
            stats["quote"] += 1
            continue

        m = re.match(r"^\s*[-*·]\s+(.*)$", line)
        if m:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.85)
            p.paragraph_format.first_line_indent = Cm(-0.45)
            p.paragraph_format.space_after = Pt(3)
            add_inline(p, "·　" + m.group(1).strip(), size=SZ_BODY)
            stats["list"] += 1
            continue

        m = re.match(r"^\s*(\d+)[.、)]\s+(.*)$", line)
        if m:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.95)
            p.paragraph_format.first_line_indent = Cm(-0.55)
            p.paragraph_format.space_after = Pt(3)
            add_inline(p, f"{m.group(1)}.　{m.group(2).strip()}", size=SZ_BODY)
            stats["list"] += 1
            continue

        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(5)
        p.paragraph_format.line_spacing = 1.5
        add_inline(p, line.strip())
        stats["p"] += 1

    flush()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path)
    return {"ok": True, "out": os.path.abspath(out_path), "counts": stats, "paragraphs": len(doc.paragraphs)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()
    print(json.dumps(convert(args.md, args.out, args.title), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
