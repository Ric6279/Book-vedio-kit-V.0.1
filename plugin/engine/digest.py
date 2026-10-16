#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用重点提取：不需要任何预设，对任何书都能跑。

为什么要有它：「演示／说明／批注」是从《大师们的写作课》里抽出来的角色，
判定它得先读过那本书——于是每换一本书就要写一份预设。《金枝》是
人类学著作，根本没有"引一段原文再点评"的结构，那套角色完全不适用。

所以默认走这条路：**不判断体裁，只看段落本身的通用特征**——
长度是否适中、是不是章节的开头或结尾、有没有引文与数字、
连接词密度、以及它对本章词汇的代表性。再把最像重点的几段挑出来，
逐字保留原文与页码，并写清它为什么被挑中。

总结部分是**抽取式**的：每章从打分最高的段落里挑几句话作为"要点摘句"，
加上按 n-gram 词频算出的关键词。不是模型写的概述——那是另一回事，
做不了就不假装能做。

产物：
    digest.json   结构化结果
    digest.md     「关键词 + 要点摘句 + 重点段落原文」的可读报告

用法：
    python digest.py --paragraphs paragraphs.json --out <dir> [--per-chapter 6] [--title 书名]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import sys

# 论说性的连接词：它们的密度高，通常说明这一段在承担论证而不是在铺垫
CONNECTORS = (
    "因此", "所以", "总之", "综上", "可见", "换言之", "也就是说", "首先", "其次", "最后",
    "例如", "比如", "这说明", "由此", "正是", "关键在于", "一方面", "另一方面", "正因为",
    "不仅", "而且", "然而", "但是", "相反", "据此", "原因是", "结果是",
)

# 高频虚词：只由这些字构成的候选词一律不做关键词
FUNCTION_CHARS = set("的了是在和与也就都而及其这那有不我你他她它们个之以为于被把从对向并且但或如果虽然因为所以可以已经还更最很一二三四五六七八九十")

# 由非虚词字组成、但仍然只是话头话尾的词。没有分词器时靠这张表挡，
# 否则「认为」「这种」「可能」「一样」会挤满关键词位。
STOPWORDS = {
    "认为", "以为", "这种", "那种", "这样", "那样", "这些", "那些", "这个", "那个",
    "可能", "也许", "或许", "一样", "同样", "一些", "许多", "很多", "非常", "已经",
    "就是", "还是", "没有", "什么", "怎么", "为什么", "我们", "他们", "它们", "你们",
    "自己", "同时", "此外", "另外", "其实", "因此", "所以", "但是", "不过", "而且",
    "或者", "如果", "虽然", "因为", "由于", "关于", "对于", "通过", "根据", "方面",
    "时候", "地方", "东西", "事情", "问题", "情况", "表示", "说明", "出现", "具有",
    "进行", "成为", "作为", "存在", "发生", "可以", "能够", "应该", "必须", "需要",
}

CJK_RUN = re.compile(r"[\u4e00-\u9fa5]{2,}")
SENT_SPLIT = re.compile(r"(?<=[。！？；])")


def load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def is_content_term(term: str) -> bool:
    """候选词里至少得有一个非虚词字，否则「的是」「在这一」这类会挤满关键词位。"""
    return any(ch not in FUNCTION_CHARS for ch in term)


def normalize_term(term: str) -> str:
    """削掉首尾虚词字：「的槲寄生」→「槲寄生」，「橡树的」→「橡树」。"""
    while len(term) > 2 and term[0] in FUNCTION_CHARS:
        term = term[1:]
    while len(term) > 2 and term[-1] in FUNCTION_CHARS:
        term = term[:-1]
    return term


def extract_keywords(texts: list[str], book_df: collections.Counter, chapter_count: int, limit: int) -> list[dict]:
    """按 n-gram 词频找章节关键词。

    没有分词器，就用 2~4 字连续汉字段的频次代替：中文里的实词大多落在
    这个长度区间。再拿全书当参照算一遍区分度（只在本书出现的词才算这本书的关键词），
    最后做一次子串抑制，让「槲寄生」赢过「槲寄」。
    """
    counts: collections.Counter = collections.Counter()
    for text in texts:
        for run in CJK_RUN.findall(text):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    gram = normalize_term(run[i : i + n])
                    if len(gram) >= 2 and gram not in STOPWORDS and is_content_term(gram):
                        counts[gram] += 1

    scored = []
    for term, tf in counts.items():
        if tf < 3:
            continue
        df = book_df.get(term, 1)
        idf = math.log(1 + chapter_count / max(1, df))
        # 长词略加权重：同样的频次下，「槲寄生」比「橡树」更具体
        scored.append((tf * idf * (1 + 0.12 * (len(term) - 2)), term, tf))
    scored.sort(key=lambda x: (-x[0], -len(x[1]), x[1]))

    kept: list[str] = []
    for _, term, tf in scored:
        if any(term in other for other in kept):
            continue
        kept.append(term)
        if len(kept) >= limit:
            break
    return [{"term": t, "count": counts[t]} for t in kept]


def score_paragraph(para: dict, keywords: list[str], chapter_len: int, index_in_chapter: int, total: int) -> tuple[float, list[str]]:
    """给一段打分，并记下命中了哪些信号——报告里要写清楚为什么选它。"""
    text = para["text"]
    n = len(text)
    score = 0.0
    hits: list[str] = []

    if 200 <= n <= 800:
        score += 1.0
        hits.append("长度适中")
    elif 80 <= n < 200 or 800 < n <= 1200:
        score += 0.6
        hits.append("长度尚可")
    else:
        score += 0.2

    if index_in_chapter == 0:
        score += 0.5
        hits.append("章首")
    elif index_in_chapter == total - 1:
        score += 0.35
        hits.append("章末")

    if para.get("citations"):
        score += 0.4
        hits.append("含引文出处")
    if para.get("dialogue_like") or para.get("starts_with_quote"):
        score += 0.3
        hits.append("含引语")
    if re.search(r"\d", text):
        score += 0.3
        hits.append("含数字")

    found = [c for c in CONNECTORS if c in text]
    if found:
        score += min(0.75, 0.25 * len(found))
        hits.append("论证连接词（" + "、".join(found[:3]) + "）")

    if keywords:
        hits_kw = [k["term"] for k in keywords if k["term"] in text]
        if hits_kw:
            # 用关键词覆盖率而不是个数，避免长段落天然占便宜
            score += min(1.2, 1.2 * len(hits_kw) / max(1, len(keywords)) * 4)
            hits.append("本章关键词（" + "、".join(hits_kw[:4]) + "）")

    return score, hits


def bigrams(text: str) -> set[str]:
    clean = re.sub(r"[^\u4e00-\u9fa5]", "", text)
    return {clean[i : i + 2] for i in range(len(clean) - 1)}


def pick_passages(paras: list[dict], keywords: list[str], limit: int) -> list[dict]:
    """按分数挑选，并做一次去冗余——相邻段落讲同一件事时只留一段。"""
    scored = []
    for i, para in enumerate(paras):
        score, hits = score_paragraph(para, keywords, sum(len(p["text"]) for p in paras), i, len(paras))
        scored.append({"para": para, "score": score, "hits": hits, "index_in_chapter": i})
    if not scored:
        return []

    top = max(s["score"] for s in scored) or 1.0
    for item in scored:
        item["norm"] = item["score"] / top

    chosen: list[dict] = []
    pool = sorted(scored, key=lambda s: -s["norm"])
    while pool and len(chosen) < limit:
        best = None
        best_value = -1.0
        for item in pool:
            grams = bigrams(item["para"]["text"])
            overlap = 0.0
            for picked in chosen:
                other = bigrams(picked["para"]["text"])
                if not grams or not other:
                    continue
                inter = len(grams & other)
                union = len(grams | other)
                overlap = max(overlap, inter / union if union else 0.0)
            value = item["norm"] - 0.55 * overlap
            if value > best_value:
                best_value = value
                best = item
        chosen.append(best)
        pool.remove(best)
    chosen.sort(key=lambda s: s["index_in_chapter"])
    return chosen


def pick_sentences(chosen: list[dict], keywords: list[str], limit: int) -> list[dict]:
    """抽取式要点：从打分最高的几段里挑句子，不做改写。"""
    pool = []
    for item in chosen[: max(3, limit)]:
        for sent in SENT_SPLIT.split(item["para"]["text"]):
            sent = sent.strip()
            if not (18 <= len(sent) <= 130):
                continue
            score = 0.0
            if any(c in sent for c in CONNECTORS):
                score += 1.0
            kw = [k["term"] for k in keywords if k["term"] in sent]
            score += min(1.5, 0.5 * len(kw))
            score += min(0.6, len(sent) / 200.0)
            pool.append({"text": sent, "score": score, "page": item["para"]["page_start"]})
    pool.sort(key=lambda s: -s["score"])
    picked: list[dict] = []
    for cand in pool:
        if len(picked) >= limit:
            break
        grams = bigrams(cand["text"])
        if any(grams and bigrams(p["text"]) and len(grams & bigrams(p["text"])) / max(1, len(grams | bigrams(p["text"]))) > 0.5 for p in picked):
            continue
        picked.append(cand)
    return picked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paragraphs", required=True, help="pdf_extract.py 产出的 paragraphs.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-chapter", type=int, default=6, help="每章保留几段重点段落")
    ap.add_argument("--sentences", type=int, default=4, help="每章几句要点摘句")
    ap.add_argument("--keywords", type=int, default=10, help="每章几个关键词")
    ap.add_argument("--name", default="digest", help="产物文件名（不含扩展名）")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    try:
        paragraphs = load(args.paragraphs)
    except FileNotFoundError:
        print(json.dumps({
            "ok": False,
            "message": f"找不到段落文件：{args.paragraphs}。提取那一步没有产出它，往回看那一步为什么没成。",
        }, ensure_ascii=False))
        return 1
    except json.JSONDecodeError as error:
        print(json.dumps({
            "ok": False,
            "message": f"段落文件不是合法 JSON（{error}）：{args.paragraphs}",
        }, ensure_ascii=False))
        return 1

    if not paragraphs:
        # 这里以前写的是"先跑 pdf_extract.py"——用户刚跑过，而且跑"成功"了。
        # 空段落的真实原因几乎总是没有文本层，直说。
        print(json.dumps({
            "ok": False,
            "message": "这份书的段落是空的，没有可提取的内容。最常见的原因是这份 PDF 是扫描件"
                       "（没有文本层，每页都是一张图），需要先做 OCR 才能取到文字。",
        }, ensure_ascii=False))
        return 1

    chapters: dict[str, list[dict]] = {}
    order: list[str] = []
    for para in paragraphs:
        if para["chapter"] not in chapters:
            chapters[para["chapter"]] = []
            order.append(para["chapter"])
        chapters[para["chapter"]].append(para)

    # 全书参照：每个候选词出现在几章里
    book_df: collections.Counter = collections.Counter()
    for name in order:
        seen = set()
        for para in chapters[name]:
            for run in CJK_RUN.findall(para["text"]):
                for n in (2, 3, 4):
                    for i in range(len(run) - n + 1):
                        gram = normalize_term(run[i : i + n])
                        if len(gram) >= 2 and gram not in STOPWORDS and is_content_term(gram):
                            seen.add(gram)
        for term in seen:
            book_df[term] += 1

    result = []
    for name in order:
        paras = sorted(chapters[name], key=lambda p: p["index"])
        keywords = extract_keywords([p["text"] for p in paras], book_df, len(order), args.keywords)
        chosen = pick_passages(paras, keywords, args.per_chapter)
        sentences = pick_sentences(chosen, keywords, args.sentences)
        result.append(
            {
                "chapter": name,
                "paragraphs": len(paras),
                "pages": [paras[0]["page_start"], paras[-1]["page_end"]],
                "chars": sum(p["chars"] for p in paras),
                "keywords": keywords,
                "sentences": sentences,
                "passages": [
                    {
                        "pages": [c["para"]["page_start"], c["para"]["page_end"]],
                        "chars": c["para"]["chars"],
                        "score": round(c["norm"], 3),
                        "hits": c["hits"],
                        "text": c["para"]["text"],
                    }
                    for c in chosen
                ],
            }
        )

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, f"{args.name}.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)

    title = args.title or "本书"
    out: list[str] = []
    total_chars = sum(r["chars"] for r in result)
    total_passages = sum(len(r["passages"]) for r in result)
    out.append(f"# {title}·通用重点提取\n")
    out.append(
        f"\n覆盖 {len(result)} 章、{len(paragraphs)} 段、原文 {total_chars} 字；"
        f"挑出 {total_passages} 段重点段落。\n\n"
        "**这一段没有做任何配置就跑了。** 排序依据是段落的通用特征——长度是否适中、"
        "是不是章节的开头或结尾、有没有引文与数字、连接词密度、以及它对本章词汇的代表性；"
        "相同内容只留一段。\n\n"
        "下面的原文与页码**逐字取自原书，未作改写**；"
        "「要点摘句」是从打分最高的段落里抽出来的原句，不是模型写的概述——"
        "想要那样一份概述，请在本章加注解预设，或把这份提取结果交给会话里的助手。\n"
    )

    out.append("\n## 目录\n\n")
    for item in result:
        kw = "、".join(k["term"] for k in item["keywords"][:5])
        out.append(f"- {item['chapter']}　{len(item['passages'])} 段　〔{kw}〕\n")

    for item in result:
        out.append(f"\n---\n\n# {item['chapter']}\n")
        out.append(f"\n原书位置：p{item['pages'][0]}–p{item['pages'][1]}　"
                   f"段落 {item['paragraphs']} 段　原文 {item['chars']} 字\n")
        if item["keywords"]:
            out.append("\n**关键词**　" + "、".join(f"{k['term']}（{k['count']}）" for k in item["keywords"]) + "\n")
        if item["sentences"]:
            out.append("\n## 要点摘句\n\n")
            for i, sent in enumerate(item["sentences"], 1):
                out.append(f"{i}. {sent['text']}　〔p{sent['page']}〕\n")
        if item["passages"]:
            out.append("\n## 重点段落\n")
            for i, passage in enumerate(item["passages"], 1):
                pages = f"p{passage['pages'][0]}" if passage["pages"][0] == passage["pages"][1] else f"p{passage['pages'][0]}–{passage['pages'][1]}"
                why = "、".join(passage["hits"]) or "综合评分"
                out.append(f"\n### {i}. {pages}　{passage['chars']} 字　选中理由：{why}\n\n")
                for line in passage["text"].split("\n"):
                    out.append(f"> {line.strip()}\n>\n")

    with open(os.path.join(args.out, f"{args.name}.md"), "w", encoding="utf-8") as fh:
        fh.write("".join(out))

    print(json.dumps({
        "ok": True,
        "chapters": len(result),
        "passages": total_passages,
        "out": os.path.abspath(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
