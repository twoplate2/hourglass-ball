# -*- coding: utf-8 -*-
"""r31-2号: 分带逐像素比对 —— 回答"哪一层没跟上"。

用法: python tools/_r31b_bands.py A.png B.png
两个 png 必须同尺寸(1080x1920)。打印每个带里的差异像素数 / 最大通道差。
"""
import sys
import numpy as np
from PIL import Image

BANDS = [
    ("顶栏色块",   0, 62, 1080, 240),
    ("倒计时",     0, 240, 1080, 350),
    ("上球空腔",   0, 350, 1080, 640),
    ("上球沙体",   0, 640, 1080, 1000),
    ("上喇叭口",   0, 960, 1080, 1060),
    ("颈直筒",     0, 1060, 1080, 1160),
    ("下喇叭口",   0, 1160, 1080, 1260),
    ("下球上部",   0, 1260, 1080, 1420),
    ("下沙堆",     0, 1420, 1080, 1640),
    ("下球空腔",   0, 1640, 1080, 1780),
    ("底部控件",   0, 1780, 1080, 1920),
]


def load(p):
    return np.array(Image.open(p).convert("RGB")).astype(np.int16)


def main():
    A, B = load(sys.argv[1]), load(sys.argv[2])
    if A.shape != B.shape:
        print("SHAPE MISMATCH", A.shape, B.shape)
        return
    print("== %s  vs  %s" % (sys.argv[1].split("/")[-1], sys.argv[2].split("/")[-1]))
    print("%-12s %10s %10s %8s" % ("带", "差异像素", "占比%", "最大差"))
    for name, x0, y0, x1, y1 in BANDS:
        a = A[y0:y1, x0:x1]
        b = B[y0:y1, x0:x1]
        mx = np.abs(a - b).max(axis=2)
        n = int((mx > 0).sum())
        tot = mx.size
        print("%-12s %10d %9.2f%% %8d" % (name, n, 100.0 * n / tot, int(mx.max())))


main()
