#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 ASR 结果组装成"文稿 + 摘要"。

和书稿报告同样的分工：**逐句文稿由脚本搬运**（时间码与文本一一对应，
不做任何"顺一下句子"的改写），**判断放在 notes 文件里**——
分节标题、语音识别勘误、画面文字、摘要与要点。

之所以把勘误做成显式的 index → 正确文本映射，而不是让模型整体重写：
重写会顺带改掉说话人原本的措辞和语序，那些恰恰是口语稿的价值所在；
只改确知错的专名（书名、地名、人名），其余一概不动。

notes 结构（全部可选）：
{
  "title": "提升文笔的唯一正确方法：精读+仿写",
  "source": "https://www.bilibili.com/video/BV16q4y1S7Ap/",
  "duration": 420,
  "summary": "整段视频在讲什么……",
  "key_points": ["...", "..."],
  "sections": [{"from": 0, "to": 30, "title": "开场：精读加仿写"}],
  "corrections": {"2": "改正后的整句文本"},
  "screen_text": [{"time": 255.6, "title": "画面文字卡", "text": "逐字抄录"}],
  "asr_notes": ["识别勘误说明……"]
}

用法：
    python video_report.py --asr asr.json --notes notes.json --out 文稿.md
"""
from __future__ import annotations

import argparse
import json
import os


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asr", required=True)
    ap.add_argument("--notes", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", default=None, help="video_fetch.py 的 manifest.json，用于补全视频信息")
    args = ap.parse_args()

    segments = load(args.asr)
    notes = load(args.notes) if args.notes else {}
    manifest = load(args.manifest) if args.manifest else {}

    corrections = {str(k): v for k, v in (notes.get("corrections") or {}).items()}
    for seg in segments:
        fixed = corrections.get(str(seg["index"]))
        if fixed:
            seg["text"] = fixed
    sections = notes.get("sections") or []

    def section_of(start: float) -> str:
        for sec in sections:
            if sec["from"] <= start < sec["to"]:
                return sec["title"]
        return ""

    out: list[str] = []
    title = notes.get("title") or manifest.get("page_title") or "视频文稿"
    out.append(f"# {title}\n")
    duration = notes.get("duration") or manifest.get("total_duration") or (segments[-1]["end"] if segments else 0)
    speech = sum(s["end"] - s["start"] for s in segments)
    out.append(
        f"\n- 来源：{notes.get('source') or manifest.get('source_url') or '（本地文件）'}\n"
        f"- 时长：{fmt(duration)}　语音段：{len(segments)} 段／{fmt(speech)} 有声\n"
        f"- 识别：本地 SenseVoice（ONNX，CPU），非人工听写\n"
    )
    if notes.get("summary"):
        out.append(f"\n## 这段视频在讲什么\n\n{notes['summary']}\n")
    if notes.get("key_points"):
        out.append("\n## 要点\n\n")
        for i, point in enumerate(notes["key_points"], 1):
            out.append(f"{i}. {point}\n")

    out.append("\n---\n\n## 完整文稿\n")
    current = None
    for seg in segments:
        name = section_of(seg["start"])
        if name and name != current:
            current = name
            out.append(f"\n### {name}\n")
        out.append(f"\n**{fmt(seg['start'])}**　{seg['text']}\n")

    if notes.get("screen_text"):
        out.append("\n---\n\n## 画面文字（逐字抄录画面上的文字卡）\n")
        for card in notes["screen_text"]:
            out.append(f"\n### {fmt(card['time'])}　{card.get('title', '文字卡')}\n\n")
            for line in card["text"].split("\n"):
                out.append(f"> {line}\n>\n")

    if notes.get("asr_notes"):
        out.append("\n---\n\n## 识别勘误说明\n\n")
        for note in notes["asr_notes"]:
            out.append(f"- {note}\n")

    out.append("\n---\n\n## 逐句时间轴（SRT 见 asr.srt）\n\n")
    for seg in segments:
        out.append(f"{fmt(seg['start'])}–{fmt(seg['end'])}　{seg['text']}\n\n")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("".join(out))
    print(json.dumps({"ok": True, "out": os.path.abspath(args.out), "chars": sum(len(x) for x in out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
