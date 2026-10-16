#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 sections.json 组装成一份"原文 + 总结"的书籍摘要报告。

分工是刻意的：**原文由脚本逐字搬运**，保证不掺一个字的转述误差；
**判断由 notes 文件提供**——每章的章旨、技能点、每个演示单元的要点。
这样换一本书时，只有 notes 需要重写，原文永远不会被模型改写。

notes 结构（全部可选，缺省则该段落只出原文不出点评）：
{
  "chapters": {
    "美食": {
      "title": "美食：各种普通食物最好吃的时刻",
      "gist": "这一章在讲什么……",
      "skills": ["技能点一", "技能点二"],
      "explain_gists": {"25": "这一段的一句话概括"},
      "units": {"1": {"note": "这个演示单元的关键在哪", "source": "《安乐居》汪曾祺"}}
    }
  }
}

用法：
    python book_report.py --sections sections.json --notes notes.json --out report.md --title "大师们的写作课"
"""
from __future__ import annotations

import argparse
import json
import os


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def chapter_key(chapter: str, notes: dict) -> str:
    for key in (notes.get("chapters") or {}):
        if key in chapter:
            return key
    return chapter


def compose_source(sources: list[str], authors: dict) -> str:
    parts = []
    for work in sources:
        if work.startswith("="):  # "=" 前缀表示原样输出，不再套书名号
            parts.append(work[1:])
            continue
        if "《" in work:  # 已经是「作者《作品》」形式，直接就位
            parts.append(work)
            continue
        author = authors.get(work)
        parts.append(f"{author}《{work}》" if author else f"《{work}》")
    return "、".join(parts)


def render_unit(out: list[str], unit: dict, note: dict, authors: dict) -> None:
    d = unit["demo"]
    source = note.get("source")
    if not source:
        joined = compose_source(d["sources"], authors)
        source = joined if joined else "出处待补"
    pages = f"p{d['pages'][0]}" if d["pages"][0] == d["pages"][1] else f"p{d['pages'][0]}-{d['pages'][1]}"
    out.append(f"\n### {unit['unit_index']}. {source}　（{pages}，{d['chars']} 字）\n")
    if d.get("lead_in"):
        out.append(f"\n**作者是怎么引出来的**\n\n> {d['lead_in'].strip()}\n")
    out.append("\n**演示原文**\n\n")
    for line in d["text"].split("\n"):
        out.append(f"> {line.strip()}\n>\n")
    for a in unit["annotations"]:
        out.append(f"\n**作者批注**（原书 p{a['page']}）\n\n> {a['text'].strip()}\n")
    if note.get("note"):
        out.append(f"\n**要点**　{note['note']}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sections", required=True)
    ap.add_argument("--notes", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="书籍关键段落摘要")
    ap.add_argument("--explain-limit", type=int, default=0, help="每章最多列出多少条说明段落原文，0 表示不限")
    args = ap.parse_args()

    sections = load(args.sections)
    notes = load(args.notes) if args.notes else {}

    total_units = sum(s["demo_units"] for s in sections)
    total_chars = sum(s["chars"] for s in sections)

    lines: list[str] = []
    lines.append(f"# {args.title}·关键段落摘要\n")
    lines.append(
        f"覆盖 {len(sections)} 章、{total_units} 个演示单元、原文 {total_chars} 字。"
        "演示原文与批注逐字取自原书排版，未作改写；**要点**与概括为整理者所加。\n"
    )
    lines.append("\n## 目录\n\n")
    for sec in sections:
        key = chapter_key(sec["chapter"], notes)
        title = (notes.get("chapters", {}).get(key, {}) or {}).get("title") or sec["chapter"]
        anchor = title.replace(" ", "-")
        lines.append(f"- {title}　{sec['demo_units']} 个演示单元\n")
    lines.append("")

    for sec in sections:
        key = chapter_key(sec["chapter"], notes)
        cfg = (notes.get("chapters") or {}).get(key, {}) or {}
        title = cfg.get("title") or sec["chapter"]
        lines.append(f"\n---\n\n# {title}\n")

        units = sec.get("units") or []
        if units:
            starts = sorted({u["demo"]["pages"][0] for u in units})
            ends = [u["demo"]["pages"][1] for u in units]
            lines.append(f"原书位置：p{starts[0]}–p{max(ends)}　"
                         f"演示单元 {sec['demo_units']} 个　说明段落 {len(sec['explain_blocks'])} 段\n")
        else:
            # 一章里一个演示单元都没判出来，不算失败：说明段落的原文照样要出，
            # 同时把"为什么会这样"讲清楚，用户才知道下一步该调什么。
            pages = [p["page"] for p in sec.get("explain_blocks") or []]
            where = f"p{min(pages)}–p{max(pages)}" if pages else "（无段落）"
            lines.append(f"原书位置：{where}　演示单元 0 个　说明段落 {len(sec['explain_blocks'])} 段\n")
            lines.append(
                "\n> **这一章没有判出演示段落。** 当前的规则靠「上一段以 `——`／`：` 收尾」来找引文，"
                "这一段里没有出现这样的收尾，所以整章都被算作说明段落。"
                "如果这一章其实引了原文，请在本章的角色预设里用 `demo_runs` 指定段落号"
                "（段落号见 paragraphs.md），或放宽 `lead_in_marks`。\n"
            )
        if cfg.get("gist"):
            lines.append(f"\n## 这一章在讲什么\n\n{cfg['gist']}\n")
        if cfg.get("skills"):
            lines.append("\n## 可迁移的技能点\n\n")
            for i, skill in enumerate(cfg["skills"], 1):
                lines.append(f"{i}. {skill}\n")

        if sec["units"]:
            lines.append("\n## 演示段落\n")
            authors = cfg.get("authors") or {}
            for unit in sec["units"]:
                note = (cfg.get("units") or {}).get(str(unit["unit_index"]), {}) or {}
                render_unit(lines, unit, note, authors)

        lines.append("\n## 说明段落（作者的解说）\n")
        shown = sec["explain_blocks"]
        if args.explain_limit:
            shown = shown[: args.explain_limit]
        gists = cfg.get("explain_gists") or {}
        for e in shown:
            gist = gists.get(str(e["index"]))
            head = f"\n**[{e['index']}] p{e['page']}**" + (f"　〔{gist}〕" if gist else "")
            lines.append(head + "\n\n")
            lines.append(f"> {e['text'].strip()}\n")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))
    print(json.dumps({"ok": True, "out": os.path.abspath(args.out), "chars": sum(len(x) for x in lines)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
