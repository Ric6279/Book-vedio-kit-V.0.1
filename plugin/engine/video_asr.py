#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地语音识别：SenseVoice（ONNX）+ Silero VAD，全离线，不需要联网、不需要 GPU。

为什么用它而不是 faster-whisper：
  * 机器上已经有现成的 SenseVoice int8 模型（DSH 自带的本地语音识别就是这个），
    不用再下载 1.5GB 权重；
  * 中文识别质量好，自带标点与逆文本归一化（数字、日期会写成人读形式）；
  * 纯 CPU int8，7 分钟音频约十几秒。

产物（落在 --out 目录）：
    asr.json    [{index, start, end, text}, ...]
    asr.srt     同内容字幕
    asr.txt     纯文本（按时间顺序，段落化）

用法：
    python video_asr.py --wav <audio_16k.wav> --out <dir> [--model-dir <sensevoice-onnx>] [--threads 8]
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import wave

DEFAULT_MODEL_DIRS = [
    os.path.join(os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh")), "speech-to-text", "sensevoice", "models"),
    r"C:\Users\win10\.dsh\speech-to-text\sensevoice\models",
]


def resolve_models(model_dir: str | None) -> tuple[str, str, str]:
    """返回 (sensevoice.onnx, tokens.txt, silero_vad.onnx)。"""
    roots = [model_dir] if model_dir else DEFAULT_MODEL_DIRS
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        sv = os.path.join(root, "sensevoice-onnx")
        model = os.path.join(sv, "model.int8.onnx")
        if not os.path.isfile(model):
            model = os.path.join(sv, "model.onnx")
        tokens = os.path.join(sv, "tokens.txt")
        vad = os.path.join(root, "silero", "silero_vad.onnx")
        if os.path.isfile(model) and os.path.isfile(tokens) and os.path.isfile(vad):
            return model, tokens, vad
    raise SystemExit(f"找不到 SenseVoice 模型，请用 --model-dir 指定（已试：{roots}）")


def read_wav(path: str) -> tuple[list[float], int]:
    with wave.open(path, "rb") as fh:
        if fh.getsampwidth() != 2:
            raise SystemExit("只支持 16-bit PCM wav，请用 video_fetch.py 抽出的 audio_16k.wav")
        rate = fh.getframerate()
        raw = fh.readframes(fh.getnframes())
    count = len(raw) // 2
    samples = struct.unpack(f"<{count}h", raw[: count * 2])
    return [s / 32768.0 for s in samples], rate


def fmt_ts(seconds: float, sep: str = ",") -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model-dir", default=None)
    ap.add_argument("--threads", type=int, default=max(4, (os.cpu_count() or 8) // 2))
    ap.add_argument("--language", default="zh")
    ap.add_argument("--min-silence", type=float, default=0.35, help="VAD 断句的最小静音秒数")
    args = ap.parse_args()

    import sherpa_onnx

    model, tokens, vad_model = resolve_models(args.model_dir)
    samples, rate = read_wav(args.wav)
    print(f"[asr] 音频 {len(samples) / rate:.1f}s @ {rate}Hz，模型 {os.path.basename(os.path.dirname(model))}/{os.path.basename(model)}")

    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=model, tokens=tokens, num_threads=args.threads, use_itn=True, language=args.language, debug=False,
    )

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = vad_model
    cfg.silero_vad.min_silence_duration = args.min_silence
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.sample_rate = rate
    window = cfg.silero_vad.window_size
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)

    started = time.time()
    results: list[dict] = []
    consumed = 0
    pending: list = []

    def flush_segments() -> None:
        while not vad.empty():
            seg = vad.front
            pending.append((seg.start / rate, seg.samples))
            vad.pop()

    for i in range(0, len(samples), window):
        chunk = samples[i : i + window]
        if len(chunk) < window:
            chunk = chunk + [0.0] * (window - len(chunk))
        vad.accept_waveform(chunk)
        consumed += window
        flush_segments()
    vad.flush()
    flush_segments()

    for start, seg_samples in pending:
        stream = recognizer.create_stream()
        stream.accept_waveform(rate, seg_samples)
        recognizer.decode_stream(stream)
        text = (stream.result.text or "").strip()
        end = start + len(seg_samples) / rate
        if text:
            results.append({"index": len(results) + 1, "start": round(start, 3), "end": round(end, 3), "text": text})

    elapsed = time.time() - started
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "asr.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)

    with open(os.path.join(args.out, "asr.srt"), "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(f"{r['index']}\n{fmt_ts(r['start'])} --> {fmt_ts(r['end'])}\n{r['text']}\n\n")

    with open(os.path.join(args.out, "asr.txt"), "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(f"[{fmt_ts(r['start'], '.')[:11]}] {r['text']}\n")

    total_speech = sum(r["end"] - r["start"] for r in results)
    print(
        json.dumps(
            {
                "ok": True,
                "segments": len(results),
                "speech_seconds": round(total_speech, 1),
                "elapsed_seconds": round(elapsed, 1),
                "realtime_factor": round((len(samples) / rate) / max(elapsed, 1e-6), 1),
                "out": os.path.abspath(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
