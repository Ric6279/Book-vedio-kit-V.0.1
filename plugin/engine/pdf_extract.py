#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""书籍段落抽取：把 PDF 拆成"带页码的段落单元"，供后续按角色（演示/说明/批注）筛选。

这是整套流程的"接口"所在：调用方用 --chapters 选章节（支持模糊匹配），
用 --pages 选任意页范围，用 --roles 声明这一轮关心哪几类段落。
抽取本身不做语义判断——判断留给调用方（人或模型），
脚本只负责把"原文在哪一页、属于哪个段落"这件事做得准确、可复现。

产物：
    paragraphs.json   [{chapter, index, page_start, page_end, text, chars, signals}]
    paragraphs.md      同上，人类可读版

退出码：
    0  提取成功
    3  一个段落都没提出来。JSON 里的 message 会说明是"扫描件没有文本层"还是
       "选中的范围内没有文字"——这两种情况在 PDF 里长得完全不一样，别混成一句
       "先跑 pdf_extract.py"。（末尾那行 JSON 里同时带 hasTextLayer / imagePages / textPages）

用法：
    python pdf_extract.py --pdf book.pdf --out d --chapters "美食,萌物,情色,男色"
    python pdf_extract.py --pdf book.pdf --out d --pages 27-34
    python pdf_extract.py --pdf book.pdf --list        # 只列目录
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

# 引用引入语：这些短句出现在段末时，下一个段落多半是"演示段落"（大师原文）
QUOTE_LEADS = (
    "——", "：", ":", "如下", "是这样的", "一文", "一篇", "一段", "写道", "写着",
    "来看", "且看", "试看", "读来", "照录", "全文", "开头", "结尾", "中间",
)
# 批注/评点信号：作者对具体文句的即时点评
ANNOTATION_HINTS = ("批注", "按：", "注：", "点评", "所谓", "意思是", "即", "也就是说", "这句话", "这一句")
# 引用出处信号
CITATION_RE = re.compile(r"《[^》]{1,40}》")
NUM_ONLY_RE = re.compile(r"^[\s\-—–·•]*\d{1,4}[\s\-—–·•]*$")


def build_paragraphs(page, margin: float | None, indent: float | None) -> tuple[list[str], list[dict]]:
    """把一页拆成段落，返回 (段落文本列表, 每段的版式信号)。"""
    lines: list[tuple[float, str]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"])
            stripped = text.strip()
            if not stripped or NUM_ONLY_RE.match(stripped):
                continue
            lines.append((round(line["bbox"][0], 1), stripped))
    if not lines:
        return [], []

    if margin is None:
        margin = min(x for x, _ in lines)
    if indent is None:
        xs = sorted({x for x, _ in lines if x > margin + 1})
        indent = xs[0] if xs else margin + 30.0

    paras: list[list[str]] = []
    flags: list[dict] = []
    for x, text in lines:
        starts = x > margin + max(4.0, (indent - margin) * 0.5)
        if starts or not paras:
            paras.append([text])
            flags.append({"indented": bool(starts)})
        else:
            paras[-1].append(text)
    joined = ["".join(p) for p in paras]
    return joined, flags


def chapter_ranges(doc) -> list[dict]:
    toc = doc.get_toc(simple=True)
    entries = [{"level": lv, "title": t.strip(), "page": pg} for lv, t, pg in toc if t and t.strip()]
    out = []
    for i, e in enumerate(entries):
        end = doc.page_count
        for nxt in entries[i + 1 :]:
            if nxt["level"] <= e["level"]:
                end = nxt["page"] - 1
                break
        out.append({**e, "start": e["page"], "end": max(e["page"], end)})
    return out


def match_chapters(chapters: list[dict], wanted: list[str]) -> list[dict]:
    picked = []
    for want in wanted:
        key = want.strip().strip("【】[]")
        hit = None
        for ch in chapters:
            if not ch["title"].strip("【】"):
                continue
            if key and key in ch["title"]:
                hit = ch
                break
        if hit is None:
            raise SystemExit(f"目录里找不到章节：{want}")
        picked.append(hit)
    return picked


def text_layer_stats(doc, min_chars: int = 30) -> dict:
    """量一下这份 PDF 里到底有没有文字层。

    扫描件（每页都是一张图）在 PDF 里没有任何文字对象，pymupdf 一个字符也取不到。
    这不是"提取失败"，而是"没有东西可提取"——两者的处置完全不同：
    前者要修，后者要 OCR。所以先量再说话，别把限制说成使用者的顺序错误。
    """
    text_pages = 0
    no_text_pages = 0
    image_pages = 0
    probe_chars = 0
    for page in doc:
        raw = re.sub(r"\s+", "", page.get_text("text") or "")
        probe_chars += len(raw)
        if len(raw) >= min_chars:
            text_pages += 1
        else:
            no_text_pages += 1
            if page.get_images(full=True):
                image_pages += 1
    return {
        "pages": doc.page_count,
        "textPages": text_pages,
        "noTextPages": no_text_pages,
        "imagePages": image_pages,
        "probeChars": probe_chars,
        "hasTextLayer": text_pages > 0,
    }


def scan_message(stats: dict) -> str:
    return (
        f"这份 PDF 没有文本层：{stats['pages']} 页里 {stats['noTextPages']} 页是图像，"
        f"提取到 {stats['probeChars']} 个字符。它是扫描件，需要先做 OCR 才能提取文字。"
    )


def analyse(text: str) -> dict:
    """给段落打几个"它像什么"的信号，供调用方判断角色。"""
    return {
        "chars": len(text),
        "citations": CITATION_RE.findall(text),
        "ends_with_lead": text.rstrip().endswith(("——", "：", ":")),
        "has_ellipsis": "……" in text or "..." in text,
        "annotation_hint": any(h in text[:12] for h in ANNOTATION_HINTS),
        "dialogue_like": bool(re.search(r"[“”]", text)),
        "starts_with_quote": text.lstrip().startswith(("“", "「")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default=None, help="输出目录；不给则只打印")
    ap.add_argument("--chapters", default=None, help="逗号分隔，支持模糊匹配，如 美食,萌物")
    ap.add_argument("--pages", default=None, help="1-based 页范围，如 27-34；与 --chapters 二选一")
    ap.add_argument("--list", action="store_true", help="只列出目录")
    ap.add_argument("--min-chars", type=int, default=8, help="短于此长度的段落丢弃（页眉页脚残留）")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    chapters = chapter_ranges(doc)

    if args.list:
        for ch in chapters:
            print(f"L{ch['level']}  p{ch['start']:>3}-{ch['end']:<3}  {ch['title']}")
        stats = text_layer_stats(doc)
        payload = {"ok": True, **stats, "chapters": chapters}
        if not chapters:
            # 读不出目录有两种原因，说清楚是哪一种——不然后面每一步都要被它拖累
            payload["message"] = (
                (scan_message(stats) + "目录也读不出来，勾「全文识别」也没用。")
                if not stats["hasTextLayer"]
                else "这份 PDF 没有内置目录，勾「全文识别」按整本提取即可。"
            )
        # 末尾补一行 JSON：宿主半的工具靠它拿到结构化结果，而不是一坨文本
        print(json.dumps(payload, ensure_ascii=False))
        doc.close()
        return 0

    stats = text_layer_stats(doc)

    targets: list[dict] = []
    if args.chapters:
        targets = match_chapters(chapters, args.chapters.split(","))
    elif args.pages:
        a, _, b = args.pages.partition("-")
        targets = [{"title": f"p{a}-{b or a}", "start": int(a), "end": int(b or a), "level": 0}]
    else:
        targets = [{"title": "全书", "start": 1, "end": doc.page_count, "level": 0}]

    result = []
    for ch in targets:
        # 用全章统计出的左边界与缩进量，避免个别页把缩进判错
        margin = indent = None
        probe = []
        for pno in range(ch["start"] - 1, min(ch["end"], doc.page_count)):
            for block in doc[pno].get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    txt = "".join(s["text"] for s in line["spans"]).strip()
                    if txt and not NUM_ONLY_RE.match(txt):
                        probe.append(round(line["bbox"][0], 1))
        if probe:
            margin = min(probe)
            bigger = sorted({x for x in probe if x > margin + 1})
            indent = bigger[0] if bigger else margin + 30.0

        index = 0
        for pno in range(ch["start"] - 1, min(ch["end"], doc.page_count)):
            paras, flags = build_paragraphs(doc[pno], margin, indent)
            for text, flag in zip(paras, flags):
                # 短段落通常是"《讲用》："这类引出语标签，不能按长度一丢了之
                is_label = bool(re.fullmatch(r"[《〈][^》〉]{1,40}[》〉][:：]?", text)) or text.rstrip().endswith(("——", "：", ":"))
                if len(text) < args.min_chars and not is_label:
                    continue
                index += 1
                result.append(
                    {
                        "chapter": ch["title"],
                        "index": index,
                        "page_start": pno + 1,
                        "page_end": pno + 1,
                        "text": text,
                        "layout": flag,
                        **analyse(text),
                    }
                )

    # 跨页续段：上一段末尾停在半句（没有句末标点），且本页首行是顶格的续行
    merged: list[dict] = []
    terminal = ("。", "！", "？", "…", "”", "」", "!", "?", ".", "：", ":")
    for para in result:
        prev = merged[-1] if merged else None
        continues = (
            prev is not None
            and prev["page_end"] == para["page_start"] - 1
            and prev["chapter"] == para["chapter"]
            and para["layout"]["indented"] is False
            and not prev["text"].rstrip().endswith(terminal)
            and len(prev["text"]) > 12
        )
        if continues:
            prev["text"] += para["text"]
            prev["page_end"] = para["page_end"]
            prev.update(analyse(prev["text"]))
            continue
        merged.append(para)

    for i, para in enumerate(merged, 1):
        para["index"] = i

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "paragraphs.json"), "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=1)
        with open(os.path.join(args.out, "paragraphs.md"), "w", encoding="utf-8") as fh:
            current = None
            for p in merged:
                if p["chapter"] != current:
                    current = p["chapter"]
                    fh.write(f"\n\n# {current}\n")
                sig = []
                if p["ends_with_lead"]:
                    sig.append("引语在前")
                if p["citations"]:
                    sig.append("出处:" + "/".join(p["citations"][:2]))
                if p["has_ellipsis"]:
                    sig.append("含省略号")
                fh.write(f"\n## [{p['index']}] p{p['page_start']}｜{p['chars']}字 {' '.join(sig)}\n{p['text']}\n")
        print(f"写出：{os.path.join(args.out, 'paragraphs.json')}")

    chapter_brief = [{"title": c["title"], "pages": [c["start"], c["end"]]} for c in targets]

    # 一个段落都没提出来：这不是"跑完了但结果为空"，是这一步根本没成。
    # 说清楚是输入的问题还是范围的问题，别让后续步骤去撞一个空文件。
    if not merged and stats["pages"] > 0:
        scope = "、".join(c["title"] for c in targets[:3]) or "整本"
        message = (
            scan_message(stats)
            if not stats["hasTextLayer"]
            else f"「{scope}」这一段范围内没有提取到文字段落（这份 PDF 有文本层，"
                 f"{stats['textPages']}/{stats['pages']} 页能取到字；这一段可能是插图页或缺页，换个范围试试）。"
        )
        print(json.dumps({
            "ok": False, "message": message, **stats,
            "chapters": chapter_brief, "paragraphs": 0, "chars": 0,
        }, ensure_ascii=False))
        doc.close()
        return 3

    print(json.dumps({
        "ok": True, **stats,
        "chapters": chapter_brief,
        "paragraphs": len(merged),
        "chars": sum(p["chars"] for p in merged),
    }, ensure_ascii=False))
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
