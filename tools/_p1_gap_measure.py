# -*- coding: utf-8 -*-
"""量**画出来的**缝(图像像素): 中心带内逐行的"实心沙覆盖率"。

自标定(先拿已知的沙/背景各取一个参考色, 再分类 —— 不写死 RGB):
    ref_sand = 给定带内**最下面 10 行**的中位色(那里必然在沙堆内部)
    ref_bg   = 给定带内**像素最多的颜色**(背景是纯色, 沙是纹理)
判据: 该行里"离 ref_sand 比离 ref_bg 更近"的像素占比 ≥ solid_frac ⇒ 实心沙行。

用法: python tools/_p1_gap_measure.py x0 x1 ytop ybot A.png [B.png ...]
输出每个图: 实心段列表(行号) / 最长的空段 / "柱底行" 与 "堆顶行"。
"""
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

x0, x1, ytop, ybot = (int(v) for v in sys.argv[1:5])
files = sys.argv[5:]
SOLID = 0.95
GAP = 0.20

for f in files:
    im = np.asarray(Image.open(f).convert("RGB")).astype(np.int16)
    band = im[ytop:ybot + 1, x0:x1 + 1, :]
    h, w, _ = band.shape
    ref_sand = np.median(band[max(0, h - 10):, :, :].reshape(-1, 3), axis=0)
    flat = band.reshape(-1, 3)
    cnt = Counter(map(tuple, flat[::7]))
    ref_bg = np.array(cnt.most_common(1)[0][0], dtype=np.int16)
    ds = np.linalg.norm(band - ref_sand, axis=2)
    db = np.linalg.norm(band - ref_bg, axis=2)
    cov = (ds < db).mean(axis=1)
    runs = []
    i = 0
    while i < h:
        j = i
        while j + 1 < h and (cov[j + 1] >= SOLID) == (cov[i] >= SOLID):
            j += 1
        if cov[i] >= SOLID:
            runs.append((ytop + i, ytop + j))
        i = j + 1
    # 最长低覆盖段
    best = (0, None)
    i = 0
    while i < h:
        if cov[i] < GAP:
            j = i
            while j + 1 < h and cov[j + 1] < GAP:
                j += 1
            if j - i + 1 > best[0]:
                best = (j - i + 1, (ytop + i, ytop + j))
            i = j + 1
        i += 1
    print("\n== %s ==  ref_sand=%s ref_bg=%s" % (Path(f).name, tuple(int(v) for v in ref_sand), tuple(int(v) for v in ref_bg)))
    print("   实心段(行): " + (", ".join("%d-%d" % r for r in runs) if runs else "(无)"))
    print("   最长空段: %s (共 %d 行)" % (best[1], best[0]))
