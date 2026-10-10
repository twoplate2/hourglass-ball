# -*- coding: utf-8 -*-
"""设备截图: 逐行「沙色跨度」+「竖/横自相关半长」——用来锁定直筒段/收口段的边界。"""
import sys, numpy as np
from PIL import Image
for s in (sys.stdout, sys.stderr):
    try: s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

def half_len(a, axis, maxlag=40):
    """ACF 降到 0.5 所需的 lag(子像素线性插值)。恒定列/行 -> 返回 maxlag。"""
    a = a - a.mean(axis=axis, keepdims=True)
    n = a.shape[axis]
    v = (a * a).mean()
    if v <= 1e-9:
        return float(maxlag)
    out = []
    prev = 1.0
    for L in range(1, maxlag + 1):
        if axis == 0: x, y = a[L:, :], a[:-L, :]
        else:         x, y = a[:, L:], a[:, :-L]
        c = float((x * y).mean()) / v
        if c <= 0.5:
            return float(L - 1) + (0.5 - prev) / (c - prev) if c != prev else float(L)
        prev = c
    return float(maxlag)

p = sys.argv[1]
img = np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)
lum = img @ np.array([0.299, 0.587, 0.114])
H, W = lum.shape
print("行   沙跨(起-止/宽)  竖半长  横半长  竖/横")
for y in range(960, 1290, 10):
    row = img[y]
    m = (row[:, 0] - row[:, 2] > 40) & (row[:, 2] < 215)
    xs = np.nonzero(m)[0]
    if xs.size < 4:
        print("%4d  --" % y); continue
    x0, x1 = int(xs[0]), int(xs[-1]); w = x1 - x0 + 1
    cx = (x0 + x1) // 2
    pad = max(2, int(w * 0.20)); a0, a1 = x0 + pad, x1 - pad
    if a1 - a0 < 6: 
        print("%4d  %4d-%4d w=%-4d  太窄" % (y, x0, x1, w)); continue
    # 竖向: 该 x 带、上下各 22 行
    v = lum[max(0,y-22):y+23, a0:a1+1]
    h = lum[y-7:y+8, a0:a1+1]
    ph = half_len(h.T, 0)     # 横: 沿 x
    pv = half_len(v, 0)       # 竖: 沿 y
    print("%4d  %4d-%4d w=%-4d  %6.2f  %6.2f  %6.2f" % (y, x0, x1, w, pv, ph, pv/max(ph,1e-6)))
