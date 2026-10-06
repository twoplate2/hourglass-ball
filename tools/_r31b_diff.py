# -*- coding: utf-8 -*-
"""r31-2号: 两图逐像素比对(可裁区域), 打印差异统计 + 差异包围盒。"""
import sys
from PIL import Image
import numpy as np


def load(p):
    return np.array(Image.open(p).convert("RGB")).astype(np.int16)


def main():
    a, b = sys.argv[1], sys.argv[2]
    box = None
    if len(sys.argv) > 5:
        box = tuple(int(v) for v in sys.argv[3:7])
    A, B = load(a), load(b)
    if A.shape != B.shape:
        print("SHAPE MISMATCH", A.shape, B.shape)
        return
    if box:
        x0, y0, x1, y1 = box
        A = A[y0:y1, x0:x1]
        B = B[y0:y1, x0:x1]
    d = np.abs(A - B)
    mx = d.max(axis=2)
    n = int((mx > 0).sum())
    tot = mx.size
    print("pixels differing(>0): %d / %d (%.3f%%)" % (n, tot, 100.0 * n / tot))
    for t in (2, 8, 24, 64):
        print("  >%-3d : %d" % (t, int((mx > t).sum())))
    print("  max channel diff:", int(mx.max()))
    print("  mean abs diff: %.4f" % float(d.mean()))
    if n:
        ys, xs = np.where(mx > 0)
        print("  bbox x[%d..%d] y[%d..%d]" % (xs.min() + (box[0] if box else 0),
                                              xs.max() + (box[0] if box else 0),
                                              ys.min() + (box[1] if box else 0),
                                              ys.max() + (box[1] if box else 0)))


main()
