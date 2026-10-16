#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视频取回与拆解：把一个网络视频（B 站）或本地视频，变成"可以识别的素材"。

产物（全部落在 --out 目录）：
    source.mp4        合并后的视频（本地输入时直接复制引用）
    audio_16k.wav     16kHz 单声道，喂给 ASR
    frames/f_0001.jpg 等间隔关键帧，供画面识别
    frames/index.json 帧号 → 时间戳
    manifest.json     本次取回的元数据（标题/时长/清晰度/时间范围）

设计要点：
  * B 站网页端有反爬（HTTP 412），但 playurl / pagelist 接口带浏览器 UA + Referer 可用，
    因此这里不走 yt-dlp 的页面解析，直接走接口拿 DASH 流地址。
  * 未登录时清晰度上限通常是 480P；能用就用，够识别画面。
  * 画面识别不做本地模型推理，改为"抽帧 + 交给上层多模态判读"，省算力也省 token。

用法：
    python video_fetch.py --url https://www.bilibili.com/video/BV16q4y1S7Ap/ --out <dir>
    python video_fetch.py --url BV16q4y1S7Ap --out <dir> --start 60 --end 180 --frames 8
    python video_fetch.py --file D:/media/lesson.mp4 --out <dir> --frames 12
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

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
    raise SystemExit("找不到 ffmpeg：请设置环境变量 DSH_FFMPEG 指向 ffmpeg.exe")


def run(cmd: list[str], desc: str = "") -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-6:]
        raise SystemExit(f"{desc} 失败（exit {proc.returncode}）：\n" + "\n".join(tail))


# --------------------------------------------------------------------- B 站
def parse_bvid(url_or_id: str) -> str:
    m = re.search(r"(BV[0-9A-Za-z]{10})", url_or_id)
    if not m:
        raise SystemExit(f"无法从 {url_or_id!r} 解析出 BV 号")
    return m.group(1)


def bili_api(path: str, params: dict, headers: dict) -> dict:
    import requests

    resp = requests.get(f"https://api.bilibili.com{path}", params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise SystemExit(f"B 站接口 {path} 返回错误：{payload.get('code')} {payload.get('message')}")
    return payload["data"]


def download_stream(urls: list[str], dest: str, headers: dict, attempts: int = 4) -> int:
    """带断点续传与备用镜像的下载。

    B 站 CDN 会中途掐断连接（IncompleteRead / ChunkedEncodingError），
    所以每次失败都从已落盘的字节数继续，并把镜像列表轮一遍。
    """
    import requests

    expect = None
    with requests.head(urls[0], headers=headers, timeout=30, allow_redirects=True) as head:
        if head.status_code == 200 and head.headers.get("content-length"):
            expect = int(head.headers["content-length"])
    done = 0
    for attempt in range(attempts):
        url = urls[attempt % len(urls)]
        req_headers = dict(headers)
        mode = "wb"
        if done:
            req_headers["Range"] = f"bytes={done}-"
            mode = "ab"
        try:
            with requests.get(url, headers=req_headers, stream=True, timeout=60) as resp:
                if resp.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {resp.status_code}")
                with open(dest, mode) as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if chunk:
                            fh.write(chunk)
                            done += len(chunk)
        except Exception as exc:  # noqa: BLE001 - 续传重试是这里的正常控制流
            print(f"[fetch] 第 {attempt + 1} 次中断（已收 {done / 1e6:.1f} MB）：{type(exc).__name__}")
            time.sleep(1.5)
            continue
        if expect is None or done >= expect:
            return done
        print(f"[fetch] 第 {attempt + 1} 次提前结束（{done}/{expect}），继续续传")
        time.sleep(1.0)
    if expect is not None and done < expect:
        raise SystemExit(f"下载不完整：{done}/{expect} 字节")
    return done


def fetch_bilibili(bvid: str, out: str, quality: int) -> dict:
    pages = bili_api("/x/player/pagelist", {"bvid": bvid}, {"User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bvid}/"})
    page = pages[0]
    cid = page["cid"]
    headers = {"User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bvid}/", "Origin": "https://www.bilibili.com"}
    dash = bili_api(
        "/x/player/playurl",
        {"bvid": bvid, "cid": cid, "qn": quality, "fnval": 16, "fnver": 0, "fourk": 1},
        headers,
    )["dash"]

    videos = sorted(dash["video"], key=lambda v: (v["height"], v["bandwidth"]), reverse=True)
    audios = sorted(dash["audio"], key=lambda a: a["bandwidth"], reverse=True)
    vstream, astream = videos[0], audios[0]

    tmp_v = os.path.join(out, "_v.m4s")
    tmp_a = os.path.join(out, "_a.m4s")
    print(f"[fetch] 视频流 {vstream['width']}x{vstream['height']} {vstream['codecs']}")
    print(f"[fetch] 音频流 {astream['bandwidth'] // 1000} kbps")
    size_v = download_stream([vstream["baseUrl"], *vstream.get("backupUrl", [])], tmp_v, headers)
    size_a = download_stream([astream["baseUrl"], *astream.get("backupUrl", [])], tmp_a, headers)
    print(f"[fetch] 下载完成：video {size_v / 1e6:.1f} MB，audio {size_a / 1e6:.1f} MB")

    merged = os.path.join(out, "source.mp4")
    ffmpeg = find_ffmpeg()
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", tmp_v, "-i", tmp_a, "-c", "copy", merged], "合并音视频")
    os.remove(tmp_v)
    os.remove(tmp_a)

    return {
        "kind": "bilibili",
        "bvid": bvid,
        "cid": cid,
        "page_title": page.get("part"),
        "duration": float(page.get("duration") or 0),
        "width": vstream["width"],
        "height": vstream["height"],
        "quality_id": vstream["id"],
        "source_url": f"https://www.bilibili.com/video/{bvid}/",
    }


# --------------------------------------------------------------------- 本地
def probe_local(path: str) -> dict:
    ffmpeg = find_ffmpeg()
    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = proc.stderr or ""
    dur = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    w = h = 0
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    return {"kind": "local", "path": os.path.abspath(path), "duration": dur, "width": w, "height": h}


# --------------------------------------------------------------------- 音频/帧
def extract_audio(ffmpeg: str, src: str, dest: str, start: float | None, end: float | None) -> None:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if end:
        cmd += ["-t", f"{max(0.0, end - (start or 0.0)):.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dest]
    run(cmd, "抽取音频")


def extract_frames(ffmpeg: str, src: str, frames_dir: str, start: float, end: float, count: int) -> list[dict]:
    os.makedirs(frames_dir, exist_ok=True)
    span = max(0.5, end - start)
    index = []
    for i in range(count):
        t = start + span * (i + 0.5) / count
        name = f"f_{i + 1:04d}.jpg"
        run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", src,
             "-frames:v", "1", "-q:v", "3", os.path.join(frames_dir, name)],
            f"抽帧 @{t:.1f}s",
        )
        index.append({"file": name, "time": round(t, 3), "index": i + 1})
    return index


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="B 站视频地址或 BV 号")
    src.add_argument("--file", help="本地视频文件")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--start", type=float, default=None, help="起始秒（含），用于只分析某一段")
    ap.add_argument("--end", type=float, default=None, help="结束秒（不含）")
    ap.add_argument("--frames", type=int, default=12, help="等间隔抽帧数量，0 表示不抽")
    ap.add_argument("--quality", type=int, default=80, help="期望清晰度 qn（80=1080P，未登录会被降级）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    ffmpeg = find_ffmpeg()

    if args.url:
        meta = fetch_bilibili(parse_bvid(args.url), args.out, args.quality)
    else:
        meta = probe_local(args.file)
        if not meta["duration"]:
            raise SystemExit(f"无法读取时长：{args.file}")

    total = meta["duration"]
    start = max(0.0, args.start or 0.0)
    end = min(total, args.end) if args.end else total
    if end <= start:
        raise SystemExit(f"时间范围非法：start={start} end={end} (总时长 {total})")

    src_path = os.path.join(args.out, "source.mp4") if args.url else meta["path"]
    meta.update({"range": {"start": start, "end": end, "seconds": round(end - start, 3)}, "total_duration": total})

    extract_audio(ffmpeg, src_path, os.path.join(args.out, "audio_16k.wav"), start, end)

    if args.frames > 0:
        index = extract_frames(ffmpeg, src_path, os.path.join(args.out, "frames"), start, end, args.frames)
        with open(os.path.join(args.out, "frames", "index.json"), "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=1)
        meta["frames"] = index

    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)

    print(json.dumps({"ok": True, "out": os.path.abspath(args.out), "range": meta["range"], "frames": len(meta.get("frames", []))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
