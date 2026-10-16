#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描件 OCR：给没有文本层的 PDF 逐页认字，产出与 pdf_extract.py 同构的段落文件。

为什么单独一个脚本：文字版与扫描件的"取字"是两件不同的事。
文字版读的是 PDF 里的文字对象（pdf_extract.py，毫秒级）；
扫描件只有图，得先渲染成位图再交给识别模型（这一份，一页三四秒）。
但**下游完全一样**——产出的 paragraphs.json / paragraphs.md 与 pdf_extract.py 同构，
digest.py、curate.py、book_report.py 一行都不用改。

哪一页走哪条路是**逐页决定的**：某页有文字对象就直接读它，没有才送去识别。
所以"前面是文字、后面是扫描"的混合书也只需跑这一遍。

全程本地：RapidOCR（PP-OCRv4 的 ONNX 模型，随包分发）跑在 CPU 上，不联网、不上传。

速度（本机 i7-11800H，200 DPI，中文书页约 20 行）：
    单线程约 5.0 s/页，4 线程约 3.9 s/页（识别占八成，检测一成）。
    所以默认 4 线程；`--workers` 与 `--intra` 可调。逐页缓存让中断后能接着跑。

产物：
    paragraphs.json / paragraphs.md   与 pdf_extract.py 同构，另带 "ocrSource" 标记该页来源
    ocr.json                          参数、逐页耗时与平均置信度
    ocr_pages/page_0007.json          逐页缓存（重跑同一本书时跳过已识别的页）

退出码：
    0  成功
    2  这份 PDF 本来就有文本层，用不着 OCR（加 --force 才强行识别）
    3  一页都没取出文字（原图太糊或整本都是插图）

用法：
    python pdf_ocr.py --pdf scan.pdf --out d --chapters "文化与人类的差异性"
    python pdf_ocr.py --pdf scan.pdf --out d --pages 28-29 --dpi 200 --workers 4
    python pdf_ocr.py --pdf scan.pdf --out d --list          # 只列目录，不识别
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import re
import sys
import threading
import time

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf_extract as px  # noqa: E402  同目录的兄弟脚本，复用章节解析与信号计算

NUM_ONLY_RE = px.NUM_ONLY_RE
ASCII_TAIL = re.compile(r"[A-Za-z0-9,;:)\]]$")
ASCII_HEAD = re.compile(r"^[A-Za-z0-9(\[]")
# 一页至少有这么多字，就认为它自带文本层，不必识别
TEXT_PAGE_MIN_CHARS = 30


# ------------------------------------------------------------------ 识别引擎
def load_engine(intra: int = 4):
    """RapidOCR 有两个包名（新版叫 rapidocr，老版叫 rapidocr_onnxruntime），都认。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:  # pragma: no cover
        from rapidocr import RapidOCR
    return RapidOCR(intra_op_num_threads=intra)


def run_engine(engine, image) -> list[dict]:
    """跑一页，返回 [{text, score, x0, y0, x1, y1}]，按阅读顺序排好。

    engine 在多个线程间共用：ORT 推理时会放开 GIL，同一个会话并发调用是安全的。
    """
    out = engine(image)
    if isinstance(out, tuple):  # 老版返回 (result, elapse)
        result = out[0]
    elif hasattr(out, "boxes"):  # 新版返回带 .boxes/.txts/.scores 的对象
        result = [[box, text, score] for box, text, score in zip(out.boxes, out.txts, out.scores)]
    else:
        result = out

    lines = []
    for item in result or []:
        try:
            box, text, score = item[0], item[1], item[2]
        except (TypeError, IndexError):
            continue
        text = str(text).strip()
        if not text:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        lines.append({
            "text": text,
            "score": round(float(score), 3),
            "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
        })
    lines.sort(key=lambda ln: (round(ln["y0"] / 8.0), ln["x0"]))  # 先上下，同行再左右
    return lines


def render_png(doc, pno: int, dpi: int) -> bytes:
    """渲染一页为 PNG 字节。PyMuPDF 的 Document 不是线程安全的，渲染只在主线程做。"""
    pix = doc[pno].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return pix.tobytes("png")


def page_text_lines(page) -> list[dict]:
    """这一页的文字层。取法与 pdf_extract.py 一致，只是这里要保留行坐标用来切段。"""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text or NUM_ONLY_RE.match(text):
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append({"text": text, "score": 1.0, "x0": float(x0), "x1": float(x1),
                          "y0": float(y0), "y1": float(y1)})
    lines.sort(key=lambda ln: (round(ln["y0"] / 8.0), ln["x0"]))
    return lines


# ------------------------------------------------------------------ 落盘缓存
def cache_path(cache_dir: str, pno: int) -> str:
    return os.path.join(cache_dir, f"page_{pno + 1:04d}.json")


def read_cache(cache_dir: str, pno: int):
    path = cache_path(cache_dir, pno)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def write_cache(cache_dir: str, pno: int, payload: dict) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    tmp = cache_path(cache_dir, pno) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, cache_path(cache_dir, pno))  # 中途断电也不会留下半截缓存


# ------------------------------------------------------------------ 组段
def join_lines(parts: list[str]) -> str:
    """把同段的多行接起来：中文直连，英文补空格，行末连字符接回。"""
    out = parts[0]
    for part in parts[1:]:
        if out.endswith("-") and part[:1].islower():
            out = out[:-1] + part
        elif ASCII_TAIL.search(out) and ASCII_HEAD.match(part):
            out += " " + part
        else:
            out += part
    return out


def strip_running_heads(pages: list[dict], ratio: float = 0.6) -> None:
    """去掉页眉页脚／书眉：同一行文字出现在多数页上、又很短，多半是版式残留。

    只在每页的头两行与末两行里找，免得把正文里反复出现的短句也删了。
    """
    if len(pages) < 5:
        return
    counter: dict[str, int] = {}
    for page in pages:
        lines = page["lines"]
        for line in lines[:2] + lines[-2:]:
            key = line["text"].strip()
            if 2 <= len(key) <= 30:
                counter[key] = counter.get(key, 0) + 1
    noisy = {text for text, count in counter.items() if count >= max(3, int(len(pages) * ratio))}
    if not noisy:
        return
    for page in pages:
        lines = page["lines"]
        page["lines"] = [
            line for i, line in enumerate(lines)
            if not ((i < 2 or i >= len(lines) - 2) and line["text"].strip() in noisy)
        ]


CJK_RE = re.compile(r"[\u4e00-\u9fff]")
CJK_PUNCT = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！"}
DASH_RUN = re.compile(r"-{2,}")
# 只用汉字数字的行：多半是页码（"四"、"九"）
CJK_NUM_ONLY_RE = re.compile(r"^[〇零一二三四五六七八九十百千\s]{1,4}$")
# 书眉／标题通常短于这个长度
HEAD_MAX_CHARS = 30
# 短于此长度的页眉直接丢掉（"文化论"、"四"）；再长的当作标题留着
HEAD_DROP_CHARS = 16


def normalize_ocr_text(text: str) -> str:
    """收拾 OCR 的常见小毛病：拉丁标点混进中文、破折号认成两三个连字符。

    只做确定性替换，且只在两侧都是汉字时替换——"3,000"、"Durkheim)" 这类不受影响。
    改动都在这一处，出了错好回退。
    """
    out = []
    for i, ch in enumerate(text):
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch in CJK_PUNCT and CJK_RE.match(prev or " ") and CJK_RE.match(nxt or " "):
            out.append(CJK_PUNCT[ch])
        else:
            out.append(ch)
    return DASH_RUN.sub("——", "".join(out))


def estimate_layout(flat: list[tuple[float, float, str]]) -> tuple[float, float, float, float]:
    """从行的左边界估出正文左边、缩进阈值、右边界与一个字宽。

    OCR 的行框会左右抖一两个像素，所以不能像 pdf_extract 那样拿"最小值 / 次小值"
    当左边界和缩进量——抖一下就会把每一行都切成一个段落。

    正文左边取的是**最早那个"有分量"的左边界**（同一位置至少占 15% 的行），
    不是出现次数最多的那个：一页里可能有整段引文缩进、或者半页换成更小的字号，
    众数会跑到那一边去；而"续行贴着正文左边"这件事对每页都成立。

    注意这个函数是**按页调用**的：《东方学》前十页里封面、版权、丛书序、
    正文的字号与边距各不相同，拿整段（10 页）的统计去套，p6 会变成一行一段。
    """
    import collections
    import statistics

    xs = [x0 for x0, _, _ in flat]
    if not xs:
        return 0.0, 6.0, 1.0, 30.0
    hist = collections.Counter(round(x / 3) * 3 for x in xs)
    # "有分量"的门槛别太低：一页里排在右上角的责任编辑、封面设计只有一两行，
    # 门槛若低到 2 行，它们就会把正文左边定到页边，正文反倒成了"每行都缩进"。
    need = max(3, int(len(xs) * 0.25))
    common = [x for x, count in hist.items() if count >= need]
    body_left = min(common) if common else hist.most_common(1)[0][0]
    body_right = max((x1 for _, x1, _ in flat), default=body_left + 1)
    widths = [(x1 - x0) / max(1, len(text)) for x0, x1, text in flat if len(text) >= 6 and x1 > x0]
    char_w = statistics.median(widths) if widths else 30.0
    # 缩进判定要留足余量：OCR 的行框会抖，续行也可能因为避头尾标点往右挪几像素。
    # 1.3 个字宽落在"续行最右"与"段首缩进"之间，实测比 0.8 稳得多。
    return body_left, body_left + max(6.0, char_w * 1.3), body_right, char_w


def is_running_head(line: dict, body_left: float, body_right: float, char_w: float) -> bool:
    """这一行像不像页眉／书眉／页码：短，而且没写到右边（居中或偏右）。

    正文段落的首行虽然也缩进，但一定写满到右边——这是区分两者的关键，
    单看左边界不够（长标题的左边界和缩进后的正文首行几乎重合）。
    """
    text = line["text"].strip()
    if len(text) > HEAD_MAX_CHARS:
        return False
    x1 = line.get("x1", line["x0"])
    return x1 < body_right - max(2.5 * char_w, 40.0)


def build_chapter(chapter: dict, pages: list[dict], min_chars: int,
                  chapter_of: dict[int, str] | None = None) -> list[dict]:
    """把逐页的行按缩进切成段落——思路与 pdf_extract.build_paragraphs 一致，判定改用估出的字宽。

    chapter_of 给的是"第几页属于哪一章"（整本跑时按目录算），没有就用 chapter['title']。
    下游的 digest 是按 chapter 字段分章汇总的，所以整本跑必须把章分对。
    """
    flat = []
    for page in pages:
        for order, line in enumerate(page["lines"]):
            text = line["text"].strip()
            if not text or NUM_ONLY_RE.match(text) or CJK_NUM_ONLY_RE.match(text):
                continue
            flat.append({
                "x0": line["x0"], "x1": line.get("x1", line["x0"]), "text": text,
                "page": page["page"], "order": order,
            })
    if not flat:
        return []

    # 版式**按页**估：同一本书里封面、版权、丛书序、正文的字号与边距各不相同，
    # 拿整段的统计去套所有页，《东方学》p6 会变成一行一段。
    # 一页行太少（标题页）就退回整段的估计，免得用两三行猜出来的边距说话。
    global_layout = estimate_layout([(ln["x0"], ln["x1"], ln["text"]) for ln in flat])
    by_page: dict[int, list[dict]] = {}
    for line in flat:
        by_page.setdefault(line["page"], []).append(line)
    page_layout = {
        pno: (estimate_layout([(ln["x0"], ln["x1"], ln["text"]) for ln in lines])
              if len(lines) >= 4 else global_layout)
        for pno, lines in by_page.items()
    }

    def layout_of(page: int):
        return page_layout.get(page, global_layout)

    # 页眉要在切段之前就拿掉：留着它会把后面那行正文吸进同一个段落
    # （书眉下面那行常常是上一段的续行，缩进上与书眉是同一段）。
    kept: list[dict] = []
    for line in flat:
        _left, _thr, body_right, char_w = layout_of(line["page"])
        head_like = line["order"] <= 1 and is_running_head(line, _left, body_right, char_w)
        if head_like and len(line["text"]) <= HEAD_DROP_CHARS:
            continue  # 书名、页码、小节名：丢掉
        line["head"] = head_like
        kept.append(line)

    # 小节标题（"丙语言"）常跟正文同一左边界，会被吸到上一段末尾。
    # 特征是：短、没有收尾标点、而下一行又是一个段首——那就让它单独成段。
    for i in range(len(kept) - 1):
        text = kept[i]["text"]
        if (
            len(text) <= 10
            and i + 1 < len(kept)
            and kept[i + 1]["x0"] > layout_of(kept[i + 1]["page"])[1]
            and not text.endswith(tuple("。，、；：！？…—-”」）)"))
        ):
            kept[i]["head"] = True
            kept[i]["break_before"] = True

    paras: list[list[dict]] = []
    for line in kept:
        after_head = bool(paras) and paras[-1][0].get("head")
        if line["x0"] > layout_of(line["page"])[1] or after_head or line.get("break_before") or not paras:
            paras.append([line])
        else:
            paras[-1].append(line)

    result = []
    for parts in paras:
        text = normalize_ocr_text(join_lines([p["text"] for p in parts]))
        if len(text) < min_chars and not text.rstrip().endswith(("——", "：", ":")):
            continue
        indented = parts[0]["x0"] > layout_of(parts[0]["page"])[1]
        result.append({
            "chapter": (chapter_of or {}).get(parts[0]["page"], chapter["title"]),
            "index": 0,
            "page_start": parts[0]["page"],
            "page_end": parts[-1]["page"],
            "text": text,
            "layout": {"indented": indented, "head": bool(parts[0].get("head"))},
            **px.analyse(text),
        })
    return result


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default="out", help="输出目录，默认 ./out（控制台会给它一个更具体的目录）")
    ap.add_argument("--chapters", default=None, help="逗号分隔，支持模糊匹配")
    ap.add_argument("--pages", default=None, help="1-based 页范围，如 28-29")
    ap.add_argument("--list", action="store_true", help="只列目录")
    ap.add_argument("--dpi", type=int, default=200, help="渲染清晰度，默认 200；小字可到 300")
    ap.add_argument("--min-chars", type=int, default=8)
    ap.add_argument("--workers", type=int, default=0, help="并行识别的线程数，0 = 按 CPU 自动")
    ap.add_argument("--intra", type=int, default=4, help="每个线程内 ONNX 用的核数")
    ap.add_argument("--force", action="store_true", help="整本都有文本层时也强行识别")
    ap.add_argument("--ocr-all", action="store_true", help="每一页都识别，连有文字层的页也不放过")
    ap.add_argument("--refresh", action="store_true", help="忽略逐页缓存，重新识别")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 页（试跑用）")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    chapters = px.chapter_ranges(doc)

    if args.list:
        stats = px.text_layer_stats(doc)
        for ch in chapters:
            print(f"L{ch['level']}  p{ch['start']:>3}-{ch['end']:<3}  {ch['title']}")
        payload = {"ok": True, **stats, "chapters": chapters, "ocrReady": True}
        payload["message"] = (
            px.scan_message(stats) if not stats["hasTextLayer"]
            else f"{stats['textPages']}/{stats['pages']} 页能直接取到字；剩下 {stats['noTextPages']} 页交给 OCR。"
        )
        print(json.dumps(payload, ensure_ascii=False))
        doc.close()
        return 0

    stats = px.text_layer_stats(doc)
    if stats["hasTextLayer"] and not args.force and stats["textPages"] >= stats["pages"] * 0.5 and not args.ocr_all:
        print(json.dumps({
            "ok": False,
            "message": f"这份 PDF 有文本层（{stats['textPages']}/{stats['pages']} 页能直接取到字），"
                       f"不用 OCR：走「通用重点提取」快得多。确实要识别就加 --force。",
            **stats,
        }, ensure_ascii=False))
        doc.close()
        return 2

    if args.chapters:
        targets = px.match_chapters(chapters, args.chapters.split(","))
    elif args.pages:
        a, _, b = args.pages.partition("-")
        targets = [{"title": f"p{a}-{b or a}", "start": int(a), "end": int(b or a), "level": 0}]
    else:
        targets = [{"title": "全书", "start": 1, "end": doc.page_count, "level": 0}]

    out_dir = args.out or "."
    cache_dir = os.path.join(out_dir, "ocr_pages")
    workers = args.workers or max(1, min(6, (os.cpu_count() or 4) // 4))
    cpu_total = os.cpu_count() or 4

    # 页 → 章节：整本跑时按目录把段落分到各章；目录读不出来（老扫描件常常没有）
    # 就都算"全书"。overrides 用不上：这里只认目录里的名字。
    chapter_of: dict[int, str] = {}
    for ch in chapters:
        for pno in range(ch["start"], min(ch["end"], doc.page_count) + 1):
            chapter_of[pno] = ch["title"]
    if not chapters:
        chapter_of = {}

    print(f"[ocr] {stats['pages']} 页：{stats['textPages']} 页直接读文字层，"
          f"{stats['noTextPages']} 页要识别；{workers} 线程 × 每线程 {args.intra} 核（共 {cpu_total} 逻辑核）；"
          f"目录 {len(chapters)} 条", flush=True)

    engine = None
    engine_lock = threading.Lock()

    def ensure_engine():
        nonlocal engine
        with engine_lock:
            if engine is None:
                print("[ocr] 加载识别模型（本地 ONNX，不联网）…", flush=True)
                engine = load_engine(args.intra)
        return engine

    result: list[dict] = []
    metas: list[dict] = []
    index = 0
    done = 0
    wall_start = time.time()
    total = sum(min(ch["end"], doc.page_count) - (ch["start"] - 1) for ch in targets)
    if args.limit:
        total = sum(min(min(ch["end"], doc.page_count), ch["start"] - 1 + args.limit) - (ch["start"] - 1) for ch in targets)

    for ch in targets:
        start = ch["start"] - 1
        end = min(ch["end"], doc.page_count)
        if args.limit:
            end = min(end, start + args.limit)

        pages: list[dict] = []
        pending: list[tuple[int, futures.Future]] = []

        def finish(pno: int, lines: list[dict], source: str, seconds: float) -> None:
            nonlocal done
            pages.append({"page": pno + 1, "lines": lines, "source": source})
            avg = round(sum(ln["score"] for ln in lines) / len(lines), 3) if lines else 0.0
            metas.append({"page": pno + 1, "lines": len(lines), "source": source,
                          "seconds": round(seconds, 2), "avgScore": avg})
            done += 1
            print(f"[ocr] {done}/{total}  p{pno + 1}  {len(lines):>3} 行  "
                  f"{'文字层' if source == 'text' else ('缓存' if source == 'cache' else f'识别 {seconds:.1f}s')}  "
                  f"置信 {avg:.2f}", flush=True)
            write_cache(cache_dir, pno, {"page": pno + 1, "dpi": args.dpi, "source": source, "lines": lines})

        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for pno in range(start, end):
                cached = None if args.refresh else read_cache(cache_dir, pno)
                if cached is not None:
                    finish(pno, cached["lines"], "cache", 0.0)
                    continue

                text_lines = [] if args.ocr_all else page_text_lines(doc[pno])
                if len("".join(ln["text"] for ln in text_lines)) >= TEXT_PAGE_MIN_CHARS:
                    finish(pno, text_lines, "text", 0.0)
                    continue

                # PyMuPDF 只能在主线程用：先把这一页渲染好，再把位图丢给线程池识别
                png = render_png(doc, pno, args.dpi)
                engine_ref = ensure_engine()

                def job(png_bytes=png, pno=pno):
                    t0 = time.time()
                    return pno, run_engine(engine_ref, png_bytes), time.time() - t0

                pending.append((pno, pool.submit(job)))
                while len(pending) >= workers * 2:  # 别一次把几百页的图像全堆在内存里
                    queued_pno, queued = pending.pop(0)
                    _, lines, seconds = queued.result()
                    finish(queued_pno, lines, "ocr", seconds)

            for queued_pno, queued in pending:
                _, lines, seconds = queued.result()
                finish(queued_pno, lines, "ocr", seconds)

        pages.sort(key=lambda p: p["page"])
        strip_running_heads(pages)
        for para in build_chapter(ch, pages, args.min_chars, chapter_of):
            index += 1
            para["index"] = index
            result.append(para)

    # 跨页续段：与 pdf_extract 同样的规则，但允许中间隔着一行标题——
    # 扫描件的书眉常常正好落在两页之间，不能因为它就断句。
    merged: list[dict] = []
    terminal = ("。", "！", "？", "…", "”", "」", "!", "?", ".", "：", ":")
    for para in result:
        prev = None
        if not para["layout"].get("head"):
            for cand in reversed(merged):
                if not cand["layout"].get("head"):
                    prev = cand
                    break
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
            prev.update(px.analyse(prev["text"]))
            continue
        merged.append(para)
    for i, para in enumerate(merged, 1):
        para["index"] = i

    chapter_brief = [{"title": c["title"], "pages": [c["start"], c["end"]]} for c in targets]
    ocr_pages = [m for m in metas if m["source"] == "ocr"]
    wall_seconds = round(time.time() - wall_start, 1)
    per_page = round(wall_seconds / len(ocr_pages), 2) if ocr_pages else None
    print(f"[ocr] 识别 {len(ocr_pages)} 页共 {wall_seconds}s（{per_page} s/页，"
          f"{workers} 线程）；文字层 {sum(1 for m in metas if m['source'] == 'text')} 页，"
          f"缓存命中 {sum(1 for m in metas if m['source'] == 'cache')} 页", flush=True)
    if not merged:
        print(json.dumps({
            "ok": False,
            "message": f"跑完了 {len(metas)} 页，但一页也没取出文字。原图可能太糊或整段都是插图——"
                       f"把 dpi 调高再试，或者换一本。",
            **stats, "chapters": chapter_brief, "paragraphs": 0, "chars": 0,
        }, ensure_ascii=False))
        doc.close()
        return 3

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
                fh.write(f"\n## [{p['index']}] p{p['page_start']}｜{p['chars']}字\n{p['text']}\n")
        with open(os.path.join(args.out, "ocr.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "pdf": os.path.abspath(args.pdf),
                "dpi": args.dpi,
                "workers": workers,
                "chapters": chapter_brief,
                "pages": metas,
                "wallSeconds": wall_seconds,
                "secondsPerPage": per_page,
            }, fh, ensure_ascii=False, indent=1)
        print(f"写出：{os.path.join(args.out, 'paragraphs.json')}", flush=True)

    print(json.dumps({
        "ok": True, **stats, "ocr": True,
        "chapters": chapter_brief,
        "paragraphs": len(merged),
        "chars": sum(p["chars"] for p in merged),
        "ocrPages": len(ocr_pages),
        "wallSeconds": wall_seconds,
        "secondsPerPage": per_page,
        "out": os.path.abspath(args.out) if args.out else None,
    }, ensure_ascii=False))
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
