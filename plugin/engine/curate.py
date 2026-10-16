#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""段落角色归类：把"一段一段的原文"整理成"演示段落 + 说明 + 批注"的教学单元。

这一步是整套流程里唯一需要判断的地方，所以设计成"规则给初判、spec 给覆写"：

  * 规则负责结构性的判断——谁引出了谁、引用到哪儿结束、出处是哪本书；
  * spec（JSON）负责书与书之间不通用的一切：哪些词在本书里是作者的评论腔、
    哪些段落人工指定角色。换一本书，改 spec 即可，不用改代码。

spec 结构（全部可选）：
{
  "chapters": ["美食", "萌物"],              // 只保留这些章
  "roles": ["演示段落", "说明段落", "批注段落"],
  "lead_in_marks": ["——", "：", ":"],         // 段末出现这些记号，下一段多半是演示
  "lead_in_max_chars": 150,
  "author_voice": ["我想", "我发现", "读完", "看来"],   // 作者评论腔的标志词
  "demo_max_chars": 900,
  "annotation_after_demo_max": 260,           // 紧跟在演示之后、短于此长度的说明段落算批注
  "overrides": {                              // 人工指定，优先级最高
    "美食": {
      "demo_runs": [[6, 9], [13, 15]],
      "annotations": [10, 23],
      "explanations": [2, 3, 4]
    }
  }
}

产物：
  sections.json   [{chapter, unit_index, demo:{...}, explain:[...], annotations:[...]}]
  review.md       人类可读的初判结果，供确认或写覆写
"""
from __future__ import annotations

import argparse
import json
import os
import re

CITATION_RE = re.compile(r"《([^》]{1,40})》")
LABEL_RE = re.compile(r"^[《〈]([^》〉]{1,40})[》〉][:：]?$")


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sources_in(text: str) -> list[str]:
    return CITATION_RE.findall(text)


def is_lead_in(text: str, marks: tuple[str, ...], max_chars: int) -> bool:
    """这个段落是不是在"领起下文"。

    判据是**段末的标记**，不是段落的长度。中文书里最常见的写法是一整段说明
    之后用「……：」把引文领起来，用长度去卡会把这类引出语全部漏掉——
    实测《金枝》第六十八章 33 段里唯一那处引出语（526 字的说明段）就是这么被漏的。

    max_chars 只作兜底：防止把明显不是引出语的长段落也算进来。
    """
    stripped = text.rstrip()
    if not stripped.endswith(marks):
        return False
    return len(stripped) <= max_chars


def sounds_authorial(text: str, hints: tuple[str, ...], demo_run_len: int) -> bool:
    """判断一段是不是"作者在说话了"。"""
    head = text[:24]
    if any(h in head for h in hints):
        return True
    # 演示段落极少以第一人称的编者口吻起头
    if demo_run_len >= 2 and head.startswith(("我想", "我说", "我读", "我看", "我又", "我再")):
        return True
    return False


def classify_chapter(paras: list[dict], spec: dict, chapter: str) -> list[dict]:
    marks = tuple(spec.get("lead_in_marks", ["——", "：", ":"]))
    hints = tuple(spec.get("author_voice", ["我想", "我发现", "我觉得", "读完", "读着", "看来", "不过", "其实"]))
    lead_max = int(spec.get("lead_in_max_chars", 800))
    demo_max = int(spec.get("demo_max_chars", 900))
    ann_max = int(spec.get("annotation_after_demo_max", 260))
    overrides: dict = {}
    for key, val in (spec.get("overrides") or {}).items():
        if key in chapter:
            overrides = val
            break

    roles: dict[int, str] = {}
    demo_runs: list[list[int]] = []

    forced_demos = [tuple(r) for r in overrides.get("demo_runs", [])]
    if forced_demos:
        demo_runs = forced_demos
    else:
        by_index = {p["index"]: p for p in paras}
        i = 0
        while i < len(paras):
            para = paras[i]
            nxt = paras[i + 1] if i + 1 < len(paras) else None
            label = LABEL_RE.match(para["text"].strip())
            starts = bool(nxt) and (is_lead_in(para["text"], marks, lead_max) or label is not None)
            if not starts:
                i += 1
                continue
            run = [nxt["index"]]
            total = len(nxt["text"])
            j = i + 1
            while j + 1 < len(paras):
                candidate = paras[j + 1]
                # demo_max_chars 是"引文累计长度"的上限。
                # 早先这里查的是最后一段自己的长度，于是一串三五百字的段落能累到三千余字，
                # 上限形同虚设——《金枝》第六十八章那段民谣就是这么涨到 3250 字的。
                if total > demo_max:
                    break
                if sounds_authorial(candidate["text"], hints, len(run)):
                    break
                if is_lead_in(paras[j]["text"], marks, lead_max) or LABEL_RE.match(paras[j]["text"].strip()):
                    break
                run.append(candidate["index"])
                total += len(candidate["text"])
                j += 1
            demo_runs.append((run[0], run[-1]))
            i = j + 1

    for start, end in demo_runs:
        for p in paras:
            if start <= p["index"] <= end:
                roles[p["index"]] = "演示段落"

    for forced in overrides.get("annotations", []):
        roles[forced] = "批注段落"
    for forced in overrides.get("explanations", []):
        roles[forced] = "说明段落"

    # 剩下的按位置落位：紧跟演示之后且较短的，判为批注；其余为说明
    for pos, para in enumerate(paras):
        idx = para["index"]
        if idx in roles:
            continue
        prev_in_demo = pos > 0 and roles.get(paras[pos - 1]["index"]) == "演示段落"
        if prev_in_demo and len(para["text"]) <= ann_max:
            roles[idx] = "批注段落"
        else:
            roles[idx] = "说明段落"

    by_index = {p["index"]: p for p in paras}
    src_override = overrides.get("sources") or {}
    units: list[dict] = []
    for start, end in demo_runs:
        demo_paras = [by_index[i] for i in range(start, end + 1) if i in by_index]
        if not demo_paras:
            continue
        lead = by_index.get(start - 1)
        sources: list[str] = []
        if lead:
            sources = sources_in(lead["text"])
        if not sources:
            for p in demo_paras:
                sources = sources_in(p["text"])
                if sources:
                    break
        after = [
            {"index": p["index"], "page": p["page_start"], "text": p["text"]}
            for p in paras
            if end < p["index"] <= end + 3 and roles.get(p["index"]) == "批注段落"
        ]
        unit_index = len(units) + 1
        # 引出语里的书名常常是上一段演示的，人工覆写优先
        if str(unit_index) in src_override:
            sources = [src_override[str(unit_index)]]
        units.append(
            {
                "unit_index": unit_index,
                "demo": {
                    "index_from": start,
                    "index_to": end,
                    "pages": [demo_paras[0]["page_start"], demo_paras[-1]["page_end"]],
                    "sources": sources,
                    "lead_in": lead["text"] if lead else None,
                    "text": "\n".join(p["text"] for p in demo_paras),
                    "chars": sum(p["chars"] for p in demo_paras),
                },
                "annotations": after,
            }
        )

    explain_blocks = [
        {"index": p["index"], "page": p["page_start"], "text": p["text"]}
        for p in paras
        if roles.get(p["index"]) == "说明段落"
    ]
    return units, explain_blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paragraphs", required=True, help="pdf_extract.py 产出的 paragraphs.json")
    ap.add_argument("--spec", default=None, help="角色规则与人工覆写")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    spec = load_json(args.spec) if args.spec else {}
    paragraphs = load_json(args.paragraphs)

    wanted = spec.get("chapters")
    by_chapter: dict[str, list[dict]] = {}
    for p in paragraphs:
        if wanted and not any(w in p["chapter"] for w in wanted):
            continue
        by_chapter.setdefault(p["chapter"], []).append(p)

    sections = []
    for chapter, paras in by_chapter.items():
        paras.sort(key=lambda p: p["index"])
        units, explain_blocks = classify_chapter(paras, spec, chapter)
        sections.append(
            {
                "chapter": chapter,
                "paragraph_count": len(paras),
                "chars": sum(p["chars"] for p in paras),
                "demo_units": len(units),
                "units": units,
                "explain_blocks": explain_blocks,
            }
        )

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "sections.json"), "w", encoding="utf-8") as fh:
        json.dump(sections, fh, ensure_ascii=False, indent=1)

    with open(os.path.join(args.out, "review.md"), "w", encoding="utf-8") as fh:
        for sec in sections:
            fh.write(f"\n\n# {sec['chapter']}（{sec['demo_units']} 个演示单元 / {sec['paragraph_count']} 段）\n")
            for unit in sec["units"]:
                d = unit["demo"]
                fh.write(f"\n## 单元 {unit['unit_index']}｜p{d['pages'][0]}-{d['pages'][1]}｜{d['chars']}字｜出处：{'/'.join(d['sources']) or '未标注'}\n")
                fh.write(f"引出语：{d['lead_in']}\n\n演示原文：\n{d['text']}\n")
                for a in unit["annotations"]:
                    fh.write(f"\n批注[{a['index']}] p{a['page']}：{a['text']}\n")
            fh.write("\n### 说明段落\n")
            for e in sec["explain_blocks"]:
                fh.write(f"\n[{e['index']}] p{e['page']}：{e['text']}\n")

    total_units = sum(s["demo_units"] for s in sections)
    print(json.dumps({"ok": True, "chapters": len(sections), "demo_units": total_units, "out": os.path.abspath(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
