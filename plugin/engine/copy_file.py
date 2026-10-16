#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""复制文件。存在的唯一理由：作业链条里需要"把字幕另存一份"这一步，
而 runScript 只会调 Python 脚本——与其在 JS 里为一步文件操作开个后门，
不如让所有步骤走同一条路，日志和错误处理就都是统一的。

用法：
    python copy_file.py --from a.srt --to b.srt
"""
from __future__ import annotations

import argparse
import json
import os
import shutil


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        print(json.dumps({"ok": False, "message": f"源文件不存在：{args.src}"}, ensure_ascii=False))
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(args.dst)), exist_ok=True)
    shutil.copyfile(args.src, args.dst)
    print(json.dumps({"ok": True, "from": os.path.abspath(args.src), "to": os.path.abspath(args.dst)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
