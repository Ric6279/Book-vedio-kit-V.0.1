#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""画面取样：把"看视频"变成"看几张拼图"，省的是多模态读图的次数。

两种模式：
  strip   按固定间隔截取画面的某一条带（比如烧进画面的字幕），纵向拼成一张长图。
          一次能看几十个时间点的字幕，比逐帧读图便宜一个数量级。
  unique  按固定间隔取样某个区域，用变化检测挑出"确实变了"的帧去重输出。
          用于抓取中途才出现的文字卡片/板书，而不是把同一个卡片读一百遍。

用法：
  python frame_sheets.py strip  --video a.mp4 --out d --every 2.5 --band 0.55,0.70 --per-sheet 28
  python frame_sheets.py unique --video a.mp4 --out d --every 1.0 --box 0.0,0.28,0.62,0.55 --threshold 12
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

FFMPEG_CANDIDATES = [
    os.environ.get("DSH_FFMPEG", ""),
    r"C:\Program Files (x86)\FormatFactory\ffmpeg.exe",
    r"C:\Program Files\SteelSeries\GG\moments\ffmpeg.exe",
    "ffmpeg",
]


def find_ffmpeg() -> str:
    for cand in FFMPEG_CANDIDATES:
        if not cand:
            continue
        if cand == "ffmpeg":
            found = shutil.which("ffmpeg")
            if found:
                return found
            continue
        if os.path.isfile(cand):
            return cand
    raise SystemExit("找不到 ffmpeg：设置 DSH_FFMPEG")


def grab(ffmpeg: str, video: str, t: float, dest: str, scale: int | None = None) -> bool:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1"]
    if scale:
        cmd += ["-vf", f"scale={scale}:-2"]
    cmd += ["-q:v", "2", dest]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0


def mmss(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def parse_floats(spec: str) -> tuple[float, ...]:
    return tuple(float(x) for x in spec.split(","))


def grab_all(ffmpeg: str, video: str, dest_dir: str, every: float, start: float) -> list[str]:
    """一次 ffmpeg 调用按固定间隔抽帧。

    逐帧起 ffmpeg（-ss 定位每帧一次）在几百帧的规模上要十几分钟，
    改成单次 fps 滤镜后是几秒钟——瓶颈从来不是解码，是进程启动。
    """
    os.makedirs(dest_dir, exist_ok=True)
    pattern = os.path.join(dest_dir, "f_%05d.jpg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", video, "-vf", f"fps=1/{every}", "-q:v", "2", "-start_number", "0", pattern]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return sorted(os.path.join(dest_dir, n) for n in os.listdir(dest_dir) if n.endswith(".jpg"))


def mode_strip(args) -> int:
    ffmpeg = find_ffmpeg()
    os.makedirs(args.out, exist_ok=True)
    tmp = os.path.join(args.out, "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    top, bottom = parse_floats(args.band)

    files = grab_all(ffmpeg, args.video, tmp, args.every, args.start)
    crops: list[tuple[float, Image.Image]] = []
    for i, path in enumerate(files):
        tm = args.start + i * args.every
        if tm >= args.end:
            break
        img = Image.open(path).convert("RGB")
        w, h = img.size
        crops.append((tm, img.crop((0, int(h * top), w, int(h * bottom)))))
    shutil.rmtree(tmp, ignore_errors=True)

    if not crops:
        raise SystemExit("没有取到任何帧")

    sheets = []
    per = max(1, args.per_sheet)
    cw = max(c.width for _, c in crops)
    ch = max(c.height for _, c in crops)
    label_h = 22
    for si in range(0, len(crops), per):
        group = crops[si : si + per]
        sheet = Image.new("RGB", (cw + 74, (ch + 4) * len(group) + 8), (250, 250, 250))
        draw = ImageDraw.Draw(sheet)
        for row, (tm, crop) in enumerate(group):
            y = 4 + row * (ch + 4)
            sheet.paste(crop, (74, y))
            draw.text((6, y + ch // 2 - 6), mmss(tm), fill=(20, 20, 20))
            draw.line([(0, y + ch + 2), (sheet.width, y + ch + 2)], fill=(210, 210, 210))
        name = f"strip_{si // per + 1:02d}.jpg"
        sheet.save(os.path.join(args.out, name), quality=88)
        sheets.append({"file": name, "from": round(group[0][0], 2), "to": round(group[-1][0], 2), "rows": len(group)})
    shutil.rmtree(tmp, ignore_errors=True)

    with open(os.path.join(args.out, "strips.json"), "w", encoding="utf-8") as fh:
        json.dump(sheets, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"ok": True, "sheets": len(sheets), "crops": len(crops), "out": os.path.abspath(args.out)}, ensure_ascii=False))
    return 0


def mode_cards(args) -> int:
    """抓取视频里中途才出现的文字卡片。

    用"变化检测"抓不准：人物一直在动，背景噪声比卡片出现还大。
    改用卡片本身的特征——白字黑边，区域内近白像素占比会显著抬升。
    先按白像素比例找"有字"的连续区间，再对区间取代表帧并按内容去重。
    """
    ffmpeg = find_ffmpeg()
    os.makedirs(args.out, exist_ok=True)
    tmp = os.path.join(args.out, "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    x0, y0, x1, y1 = parse_floats(args.box)

    files = grab_all(ffmpeg, args.video, tmp, args.every, args.start)
    samples: list[tuple[float, str, float, Image.Image]] = []
    for i, path in enumerate(files):
        tm = args.start + i * args.every
        if tm >= args.end:
            break
        img = Image.open(path).convert("RGB")
        w, h = img.size
        crop = img.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
        gray = crop.convert("L")
        pixels = list(gray.getdata())
        white = sum(1 for v in pixels if v > 235) / max(1, len(pixels))
        samples.append((tm, path, white, crop))

    runs: list[list[tuple[float, str, float, Image.Image]]] = []
    current: list = []
    for item in samples:
        if item[2] >= args.white:
            current.append(item)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    kept: list[dict] = []
    prev_sig = None
    for run in runs:
        if len(run) < args.min_run:
            continue
        tm, path, white, crop = run[len(run) // 2]
        sig = crop.convert("L").resize((64, 24))
        flat = list(sig.getdata())
        if prev_sig is not None:
            diff = sum(abs(a - b) for a, b in zip(flat, prev_sig)) / len(flat)
            if diff < args.threshold:
                prev_sig = flat
                continue
        prev_sig = flat
        name = f"card_{len(kept) + 1:02d}_{int(tm):04d}s.jpg"
        Image.open(path).convert("RGB").save(os.path.join(args.out, name), quality=90)
        kept.append(
            {
                "file": name,
                "time": round(tm, 2),
                "label": mmss(tm),
                "white_ratio": round(white, 4),
                "run_seconds": round(run[-1][0] - run[0][0] + args.every, 2),
            }
        )

    shutil.rmtree(tmp, ignore_errors=True)
    with open(os.path.join(args.out, "cards.json"), "w", encoding="utf-8") as fh:
        json.dump(kept, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"ok": True, "kept": len(kept), "runs": len(runs), "sampled": len(samples), "out": os.path.abspath(args.out)}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    for m in ("strip", "cards"):
        p = sub.add_parser(m)
        p.add_argument("--video", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--every", type=float, default=2.5)
        p.add_argument("--start", type=float, default=0.0)
        p.add_argument("--end", type=float, default=1e9)
        if m == "strip":
            p.add_argument("--band", default="0.55,0.70", help="纵向取带 top,bottom（0~1）")
            p.add_argument("--per-sheet", type=int, default=28)
        else:
            p.add_argument("--box", default="0.0,0.30,0.52,0.50", help="文字卡片区域 x0,y0,x1,y1（0~1）")
            p.add_argument("--white", type=float, default=0.03, help="近白像素占比阈值")
            p.add_argument("--min-run", type=int, default=2, help="至少连续多少个采样点才算一张卡片")
            p.add_argument("--threshold", type=float, default=6.0, help="与上一张卡片的差异阈值，用于去重")
    args = ap.parse_args()
    return mode_strip(args) if args.mode == "strip" else mode_cards(args)


if __name__ == "__main__":
    raise SystemExit(main())
