# -*- coding: utf-8 -*-
"""比较两个 `benchmark_logs/flow_visual_*` 目录的逐像素差异 —— **并屏蔽掉"必然不同"的区域**。

## 为什么必须有它(2026-10-07 踩过)

做"改前/改后"像素对比时, **底栏那三个字符的版本号**(`app_version` 从 `.187` 改成 `.190`)
会在固定位置产生**恒定**的差异。我拿它跟 40 帧比, 得到"40/40 帧都有差异, 最大通道差 79",
差点判定成渲染回归 —— 实际差的是版本号那 17×11 个像素。

**判据**: 如果差异在**每一帧都落在同一处、且幅度完全一样**, 那它就不是物理/渲染差异
(粒子每帧都在动, 不可能给出恒定差异)。本工具直接把这个区域屏蔽掉, 并把"恒定差异"
单独报出来 —— 恒定差异只剩版本号这一个已知来源。

## 跑法

    python tools/_pixdiff.py <目录A> <目录B> [--mask x0,y0,x1,y1]
    python tools/_pixdiff.py benchmark_logs/flow_visual_a benchmark_logs/flow_visual_b

退出码 0 = 屏蔽区之外**逐像素相同**。
"""
import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

# 底栏版本号所在区域(400x800 窗口)。改窗口尺寸要重量 —— 它贴在右下那一块。
DEFAULT_MASKS = ((196, 756, 232, 784),)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir_a")
    ap.add_argument("dir_b")
    ap.add_argument("--mask", action="append", default=None,
                    help="额外屏蔽区 x0,y0,x1,y1(可重复)")
    ap.add_argument("--only", default=None, help="只比文件名含该子串的图")
    args = ap.parse_args()

    A, B = Path(args.dir_a), Path(args.dir_b)
    names = sorted(f for f in os.listdir(A) if f.endswith(".png"))
    if args.only:
        names = [f for f in names if args.only in f]
    missing = [f for f in names if not (B / f).exists()]
    if missing:
        print("!! B 里缺 %d 个同名文件(比如 %s) —— 两次跑的目标集不同, 比对无意义"
              % (len(missing), missing[0]))
        return 2

    masks = list(DEFAULT_MASKS) + [
        tuple(int(v) for v in m.split(",")) for m in (args.mask or [])]

    diff_frames = 0
    sig = Counter()
    for f in names:
        a = Image.open(A / f).convert("RGB")
        b = Image.open(B / f).convert("RGB")
        if a.size != b.size:
            print("!! 尺寸不同 %s: %s vs %s" % (f, a.size, b.size))
            return 2
        pa, pb = a.load(), b.load()
        w, h = a.size
        rows = []
        for y in range(h):
            for x in range(w):
                if any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in masks):
                    continue
                ca, cb = pa[x, y], pb[x, y]
                if ca != cb:
                    rows.append((x, y, max(abs(ca[i] - cb[i]) for i in range(3))))
        if rows:
            diff_frames += 1
            # 差异的"签名": 位置范围 + 幅度, 用来判"是不是每帧都一样的恒定差异"
            xs = [r[0] for r in rows]
            ys = [r[1] for r in rows]
            sig[(len(rows), min(xs), max(xs), min(ys), max(ys),
                 max(r[2] for r in rows))] += 1

    print("比了 %d 帧(已屏蔽 %d 个区域: %s)"
          % (len(names), len(masks), ", ".join("%d,%d-%d,%d" % m for m in masks)))
    if not diff_frames:
        print("==> 屏蔽区之外**逐像素相同**")
        return 0
    print("有差异 %d / %d 帧" % (diff_frames, len(names)))
    for k, n in sig.most_common(3):
        cnt, x0, x1, y0, y1, mx = k
        print("   %d 帧共用同一签名: %d 个像素, bbox x %d..%d y %d..%d, 最大差 %d"
              % (n, cnt, x0, x1, y0, y1, mx))
    if len(sig) == 1:
        print("   ⚠️ 差异**每帧完全相同** ⇒ 不是粒子/物理(它们每帧都在动), "
              "先怀疑静态元素(版本号/角标)或裁剪区没盖住")
    return 1


sys.exit(main())
