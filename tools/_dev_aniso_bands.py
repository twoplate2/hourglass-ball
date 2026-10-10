# -*- coding: utf-8 -*-
"""设备截图: **带级** 纹理各向异性 (竖/横 ACF 半长) —— 出口分界线的判据尺。

## 为什么是"带级"而不是"逐行"
逐行只有 15 行窗, ACF 半长落在 1~4px 上 ⇒ 比值被**量化**得乱跳(0.85~3.79, 没有结构)。
带级用整块 2D patch, 沿 y 的 ACF 对**全部列**求平均、沿 x 的对**全部行**求平均
⇒ 估计量稳定, 且两个方向走**同一条代码路径**(任何方向性偏差都会同时落在两边、在比值里抵消)。

## 🔴 必须先高通
画面沿 y 有**低频亮度梯度**(材质的 lighting 项、沙面渐层), 不滤掉的话纵向 ACF 会被那条趋势
主导, 任何一块都读成"竖着拉长"。高通用**二维**箱式均值相减(核在两个方向同尺寸)
—— 一维滤波会自己制造出各向异性。
同时它也**去掉行间均值差**: 沙柱左右边缘的暗边不会污染。

## 🔴 标定: 用同一张图里的**下球沙体**当同位素对照
它是同一份 grain 材质、同一种渲染, 但几何上没有任何纵向拉伸的理由。
判据 = **tube / mound 的比值之比** —— 对照就在图里, 不靠合成自检, 也不依赖跨版本。
（若 mound 自身不是 ≈1.0, 说明尺子坏了, 直接报红。）

用法:
    python tools/_dev_aniso_bands.py <目录或png> [--bands 名=y0,y1,x0,x1 ...]
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

# 设备 1080×1920 实测几何(见 _dev_rowscan.py 的行扫描):
#   玻璃直筒 w=48, 行 1000~1095; 出口 ≈ 1105; 下喇叭口/自由射流 1115~1200
BANDS = {
    "tube":  (1012, 1092, 524, 556),    # 直筒段(玻璃管内), 取沙跨 516-563 的内 60%
    "flare": (1116, 1196, 524, 556),    # 出口以下的下喇叭口/自由射流
    "mound": (1570, 1750, 400, 680),    # 对照: 下球沙体(同位素, 无纵向拉伸理由)
}
K = 25          # 二维箱式高通核
MAXLAG = 30


def hp2d(p, k=K):
    """二维箱式均值相减(核两向同尺寸 ⇒ 不引入各向异性)。"""
    h, w = p.shape
    c = np.cumsum(np.cumsum(p, 0), 1)
    c = np.pad(c, ((1, 0), (1, 0)))
    def box(sy, sx):
        y0 = np.clip(np.arange(h) - k // 2, 0, h)
        y1 = np.clip(np.arange(h) + k // 2 + 1, 0, h)
        x0 = np.clip(np.arange(w) - k // 2, 0, w)
        x1 = np.clip(np.arange(w) + k // 2 + 1, 0, w)
        s = c[np.ix_(y1, x1)] - c[np.ix_(y0, x1)] - c[np.ix_(y1, x0)] + c[np.ix_(y0, x0)]
        n = (y1 - y0)[:, None] * (x1 - x0)[None, :]
        return s / np.maximum(n, 1)
    return p - box(0, 0)


def half_len(a, axis, maxlag=MAXLAG):
    """把 a 沿 axis 的 ACF 降到 0.5 所需的 lag(子像素线性插值)。"""
    a = a - a.mean(axis=axis, keepdims=True)
    n = a.shape[axis]
    v = float((a * a).mean())
    if v <= 1e-9:
        return float("nan")
    prev, prevL = 1.0, 0
    for L in range(1, maxlag + 1):
        x, y = (a[L:, :], a[:-L, :]) if axis == 0 else (a[:, L:], a[:, :-L])
        c = float((x * y).mean()) / v
        if c <= 0.5:
            if c == prev:
                return float(L)
            return prevL + (0.5 - prev) / (c - prev) * (L - prevL)
        prev, prevL = c, L
    return float(maxlag)


def one(path):
    img = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)
    lum = img @ np.array([0.299, 0.587, 0.114])
    out = {}
    for name, (y0, y1, x0, x1) in BANDS.items():
        P = lum[y0:y1, x0:x1]
        if P.size < 400:
            out[name] = float("nan"); continue
        Q = hp2d(P)
        pv = half_len(Q, 0)     # 竖
        ph = half_len(Q, 1)     # 横
        out[name] = pv / ph if ph and ph == ph and ph > 0 else float("nan")
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    p = Path(sys.argv[1])
    files = sorted(p.glob("*.png")) if p.is_dir() else [p]
    if not files:
        print("no png"); return 1
    acc = {k: [] for k in BANDS}
    for f in files:
        r = one(f)
        for k, v in r.items():
            if v == v:
                acc[k].append(v)
    print("  %-16s %s   (n=%d)" % (p.name, "竖/横 中位 [IQR]", len(files)))
    med = {}
    for k in BANDS:
        a = np.array(acc[k])
        if not a.size:
            print("  %-8s  --" % k); continue
        med[k] = float(np.median(a))
        print("  %-8s  **%.2f**  [%.2f-%.2f]" % (k, med[k], np.percentile(a, 25), np.percentile(a, 75)))
    if "tube" in med and "mound" in med and med["mound"] > 0:
        print("  => tube/mound = **%.2f**   (对照 mound 应 ≈1.0; 越接近 1.0 = 与沙体同词汇)"
              % (med["tube"] / med["mound"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
