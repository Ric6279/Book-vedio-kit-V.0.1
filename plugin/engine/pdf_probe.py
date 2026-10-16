#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF 结构勘察：输出目录、字体/字号/颜色统计与页面首段文本。

用途：在写"章节目录 → 段落角色"的映射规则之前，先看清一本书的排版约定。
用法：
    python pdf_probe.py <book.pdf> [--pages A-B] [--fonts] [--head N]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover
    try:
        import fitz
    except ImportError:
        print("需要 PyMuPDF：pip install pymupdf", file=sys.stderr)
        raise


def parse_pages(spec: str | None, total: int) -> tuple[int, int]:
    if not spec:
        return 0, total
    a, _, b = spec.partition("-")
    start = max(0, int(a) - 1)
    end = int(b) if b else start + 1
    return start, min(total, end)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", default=None, help="1-based，形如 12-40")
    ap.add_argument("--fonts", action="store_true", help="只打印字体统计")
    ap.add_argument("--head", type=int, default=320, help="每页打印的字符数")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    total = doc.page_count
    start, end = parse_pages(args.pages, total)

    print(f"# {args.pdf}")
    print(f"pages={total}  metadata={json.dumps({k: v for k, v in (doc.metadata or {}).items() if v}, ensure_ascii=False)}")

    toc = doc.get_toc(simple=True)
    print(f"\n## TOC ({len(toc)} entries)")
    for level, title, page in toc:
        print(f"{'  ' * (level - 1)}[{level}] p{page}  {title}")

    font_counter: collections.Counter = collections.Counter()
    size_by_font: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    color_by_font: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    def walk(page):
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    font_counter[span["font"]] += len(text)
                    size_by_font[span["font"]][round(span["size"], 1)] += len(text)
                    color_by_font[span["font"]][f"#{span['color']:06x}"] += len(text)

    for pno in range(start, end):
        walk(doc[pno])

    print("\n## 字体统计（按字符数）")
    for font, count in font_counter.most_common():
        sizes = ", ".join(f"{s}×{c}" for s, c in size_by_font[font].most_common(6))
        colors = ", ".join(f"{s}×{c}" for s, c in color_by_font[font].most_common(6))
        print(f"{count:>7}  {font:<28} size[{sizes}]  color[{colors}]")

    if not args.fonts:
        for pno in range(start, end):
            page = doc[pno]
            text = " ".join(page.get_text("text").split())
            print(f"\n## p{pno + 1}\n{text[: args.head]}")

    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
