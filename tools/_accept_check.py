# -*- coding: utf-8 -*-
"""`shalou_acceptance.md` 的 A1–A7 判据 —— 一个脚本跑完。

## 🔴 这个脚本本身**必须先过标定**（M1/M2）

第一版就栽了，三处：
- A4 的"边缘"取的是**整行的最左/最右沙色像素** ⇒ 量到的是玻璃壁交点，两边都报 62px（恒"OK"）
- A5 的 t=0 图加载失败后**静默退回到 t=3** ⇒ 拿运动中的图判"未开始"
- A1/A2 的"段"写死行 430–700，跨了沙堆 ⇒ 平填被大量正常行稀释，中位数看不出问题

**现在**:"段"按**几何**自适应 = 行内沙色跨度 **< 60px** 的那些行(= 柱子/沙流; 沙堆那条会迅速变宽)。
边缘只在**该段内**取, 且要求该行确实是细柱。

用法:
    python tools/_accept_check.py --calibrate      # 已知对(pc_now) vs 已知错(pc_matover2)
    python tools/_accept_check.py <标签> ...
"""
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BALL = (330, 390, 160, 240)      # 上球沙体(同质对照, 宽窗口)
WMAX = 60                        # "细柱"的最大沙色跨度
GAP = 1                          # 柱边再内收几px(去边)


def _load(label, t):
    """**只按精确文件名加载, 找不到就返回 None** —— 不许静默退回到别的时刻(第一版的坑)。"""
    p = ROOT / ("benchmark_logs/flow_visual_%s/period-10.0-time-%s.png" % (label, t))
    return np.asarray(Image.open(p).convert("RGB"), dtype=float) if p.exists() else None


def _sand_mask(row):
    return (row[:, 0] - row[:, 2] > 40) & (row[:, 2] < 215)


def _stream_rows(X, y0=280, y1=900):
    """自适应取"细柱"段: 每行沙色跨度 < WMAX 且 ≥6px。"""
    rows, spans, edges = [], [], []
    for y in range(y0, min(y1, X.shape[0])):
        xs = np.nonzero(_sand_mask(X[y]))[0]
        if xs.size < 6:
            continue
        w = int(xs[-1] - xs[0] + 1)
        if w < WMAX:
            rows.append(y); spans.append(w); edges.append((int(xs[0]), int(xs[-1])))
    return np.array(rows), np.array(spans), edges


def _rowstd(X, rows):
    out = []
    for y, (l, r) in zip(rows, _stream_rows(X)[2]):
        x0, x1 = l + GAP, r - GAP
        if x1 - x0 < 6:
            continue
        seg = X[y, x0:x1 + 1] @ np.array([0.299, 0.587, 0.114])
        k = min(5, max(3, (seg.size // 3) | 1))
        hp = seg - np.convolve(seg, np.ones(k) / k, "same")
        out.append(float(hp[k // 2:-(k // 2)].std()))
    return np.array(out)


def _rowstd_win(X, y0, y1, x0, x1):
    out = []
    for y in range(y0, min(y1, X.shape[0])):
        seg = X[y, x0:x1 + 1] @ np.array([0.299, 0.587, 0.114])
        k = 5
        hp = seg - np.convolve(seg, np.ones(k) / k, "same")
        out.append(float(hp[k // 2:-(k // 2)].std()))
    return np.array(out)


def check(label, t="3.00", t0="0.00"):
    X = _load(label, t)
    if X is None:
        print("  %-16s 缺 t=%s 的图" % (label, t)); return
    rows, spans, edges = _stream_rows(X)
    if rows.size < 20:
        print("  %-16s 细柱段只有 %d 行, 不作数" % (label, rows.size)); return
    seg = _rowstd(X, rows)
    ball = _rowstd_win(X, *BALL)
    a1 = float(np.median(seg) / max(np.median(ball), 1e-6))
    a2 = int((seg < 1.0).sum())
    L = np.array([e[0] for e in edges], dtype=float)
    R = np.array([e[1] for e in edges], dtype=float)
    a4 = min(float(np.std(L)), float(np.std(R)))
    X0 = _load(label, t0)
    if X0 is None:
        a5 = "缺图"
    else:
        col = X0[560:900, 200].astype(int)
        n = int((((col[:, 0] - col[:, 2]) > 40) & (col[:, 2] < 215)).sum())
        a5 = "OK(0)" if n == 0 else "!! 沙色 %d px" % n
    print("  %-16s 细柱 %3d 行(宽 %d~%d) | A1 比=%.2f %s | A2 平填行=%2d %s | "
          "A4 毛度 %.2f %s | A5 %s"
          % (label, rows.size, spans.min(), spans.max(), a1,
             "OK" if 0.6 <= a1 <= 1.5 else "!!", a2, "OK" if a2 <= 2 else "!!",
             a4, "OK" if a4 >= 1.0 else "!!", a5))


if __name__ == "__main__":
    if "--calibrate" in sys.argv:
        print("=== M1 标定(同一把尺, 已知对 vs 已知错) ===")
        check("pc_now")         # 已知对: 原始(粒子可见, 但有那条平亮分层)
        check("pc_matover2")    # 已知错: 2.33 铺满材质(平填没了, 但把粒子盖住了)
        print("  => 两者必须在**不同项**上红, 否则门槛没有分辨力")
    else:
        for lab in sys.argv[1:]:
            check(lab)
