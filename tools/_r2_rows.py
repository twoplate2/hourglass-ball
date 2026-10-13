# -*- coding: utf-8 -*-
"""第二轮对抗审查专用: 逐行"背景孔洞"量具(真机帧 / 桌面渲染通用)。

输出每一行: 背景样像素数 / 最长连续背景游程 / 非背景像素的平均灰度。
用途: 判"缝隙"到底在哪一行、有多宽、是不是整柱宽。

判据不用颜色硬编码: 背景参考色从**同一行**的左右画外区域现采(逐行中位),
所以对录像的色彩偏移免疫; 分类容差 --tol。

用法:
    python tools/_r2_rows.py <image.png> <x0> <x1> <y0> <y1> [--tol 14] [--step 1]
"""
import argparse
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
from PIL import Image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("x0", type=int)
    ap.add_argument("x1", type=int)
    ap.add_argument("y0", type=int)
    ap.add_argument("y1", type=int)
    ap.add_argument("--tol", type=int, default=14)
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--bg-x", default="40,180", help="背景采样列区间")
    ap.add_argument("--spans", action="store_true", help="另打印非背景的水平跨度")
    a = ap.parse_args()
    im = np.asarray(Image.open(a.image).convert("RGB"), dtype=np.float32)
    H, W, _ = im.shape
    bx0, bx1 = (int(v) for v in a.bg_x.split(","))
    print("# %s  size=%dx%d  band x[%d,%d) y[%d,%d)  tol=%d"
          % (Path(a.image).name, W, H, a.x0, a.x1, a.y0, a.y1, a.tol))
    print("# bg sample cols x[%d,%d)" % (bx0, bx1))
    rows = []
    for y in range(a.y0, min(a.y1, H), a.step):
        ref = np.median(im[y, bx0:bx1], axis=0)
        seg = im[y, a.x0:a.x1]
        d = np.max(np.abs(seg - ref), axis=1)
        bg = d <= a.tol
        nbg = int(bg.sum())
        # 最长连续背景游程
        run = best = 0
        for v in bg:
            run = run + 1 if v else 0
            best = max(best, run)
        gray = seg.mean(axis=1)
        nb = ~bg
        mlum = float(gray[nb].mean()) if nb.any() else float("nan")
        lo = hi = None
        if nb.any():
            idx = np.flatnonzero(nb)
            lo, hi = int(idx[0]) + a.x0, int(idx[-1]) + a.x0
        rows.append((y, nbg, best, mlum, lo, hi))
    for (y, nbg, best, mlum, lo, hi) in rows:
        mark = ""
        if best >= 6:
            mark = "  <<< run>=6"
        print("y=%4d  bg=%3d/%3d  run=%3d  lum=%6.1f  span=[%s,%s]%s"
              % (y, nbg, a.x1 - a.x0, best, mlum, lo, hi, mark))
    # 汇总
    runs = [r[2] for r in rows]
    print("# summary: max_run=%d  rows_with_run>=6: %s"
          % (max(runs), [r[0] for r in rows if r[2] >= 6]))
    top = sorted(rows, key=lambda r: -r[2])[:8]
    print("# top rows by run: " + ", ".join("y=%d run=%d lum=%.0f" % (t[0], t[2], t[3]) for t in top))


if __name__ == "__main__":
    main()
