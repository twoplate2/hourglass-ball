# -*- coding: utf-8 -*-
"""柱区**纹理各向异性** —— "上球和颈部的过渡问题"的量化判据。

## 它量的是什么

把图像某个区域的亮度做自相关，分别沿**竖**与**横**算"降到 0.5 需要几个像素"：

    竖/横 = 1.0  ⇒ 各向同性（细颗粒，像上球沙体）
    竖/横 ≥ 2.0  ⇒ 明显竖着拉长（像一束纤维）

实测（1904×2890 / 15s / t=7.64，柱内 ±40px）：

    上球沙体(行1000~1300) 1.00 → 喇叭口 1.33 → 出口 1.50 → 柱中(1700~1900) 2.00~2.50

⇒ **"过渡问题"不是一条线，是纹理词汇沿深度在渐变。** 这就是这条判据存在的理由。

## 判据

- **柱区（1700~1900）竖/横 ≤ 1.5** —— 与上球(1.00)、喇叭口(1.33) 同档
- **跨交界不得出现阶跃**：逐行打表，看 1.00 → 目标值 是不是单调、有没有跳变

用法:
    python tools/_probe_texture_anisotropy.py <图或目录> [--rows 1000,2100] [--step 100]
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
BAND = 60          # 每个采样窗的高度
HALF = 40          # 柱内取 ±40px
MAXLAG = 40


def lum(a):
    return a @ np.array([0.299, 0.587, 0.114])


def acf(sig, maxlag=MAXLAG):
    s = sig - sig.mean()
    # 🔴 **沿这个方向完全不变的剖面 ⇒ 相关性无限长**, 必须返回全 1。
    #    漏了这一条会把"竖条纹"读成**各向同性**(自相关恒 0 ⇒ half=0 ⇒ 比值 0),
    #    方向正好读反 —— 自适应判据时必须先拿合成图案把两个方向都验一遍。
    if float(s.std()) < 1e-9:
        return np.ones(maxlag)
    n = s.size
    den = max(float((s[:n - 1] ** 2).sum()), 1e-9)
    return np.array([float((s[:n - k] * s[k:]).sum() / den) for k in range(maxlag)])


def half(r):
    """降到 0.5 的 lag，**线性插值到亚像素**。

    🔴 不插值的话这个指标只有 1~4 这几个整数档 ⇒ 比值在 2.00/3.00/1.50 之间跳，
    **分辨不了 2.0 与 2.4 的差别**(A3 那一版就因此报出过"柱下段 3.00"、与肉眼相反)。
    """
    for k, v in enumerate(r):
        if v < 0.5:
            if k == 0:
                return 0.0
            v0 = float(r[k - 1])
            return (k - 1) + (v0 - 0.5) / max(v0 - float(v), 1e-9)
    return float(len(r))


def aniso(L, y, cx):
    B = L[y:y + BAND, cx - HALF:cx + HALF]
    if B.std() < 0.5:
        return None
    v = half(np.mean([acf(B[:, j]) for j in range(B.shape[1])], axis=0))
    h = half(np.mean([acf(B[i, :]) for i in range(0, B.shape[0], 4)], axis=0))
    return v, h, v / max(h, 1e-6)   # ⚠️ 不能写成 max(h,1): 亚像素插值后 h 可以是 0.5


def main():
    if "--selftest" in sys.argv:
        rng = np.random.default_rng(3)
        H, W = 200, 400
        # 各向同性白噪声
        iso = rng.normal(0, 1, (H, W))
        # **竖条纹**: 每一列一个常数(沿 y 不变) ⇒ 竖着拉长
        vstr = np.repeat(rng.normal(0, 1, (1, W)), H, axis=0)
        # **横条纹**: 每一行一个常数(沿 x 不变) ⇒ 横着拉长
        hstr = np.repeat(rng.normal(0, 1, (H, 1)), W, axis=1)
        for nm, arr, want in (("白噪声(各向同性)", iso, "≈1"),
                              ("竖条纹", vstr, "≫1"),
                              ("横条纹", hstr, "≪1")):
            r = aniso(arr, 60, 200)
            print("  %-16s 竖 %5.2f  横 %5.2f  竖/横 %7.2f   期望 %s" % (nm, r[0], r[1], r[2], want))
        a1, a2, a3 = aniso(iso, 60, 200), aniso(vstr, 60, 200), aniso(hstr, 60, 200)
        ok = abs(a1[2] - 1) < 0.4 and a2[2] > 4 and a3[2] < 0.3
        print("  => %s" % ("判据可用（三个方向都分得开）" if ok else "!! 有不合格项"))
        return 0 if ok else 1

    src = Path(sys.argv[1])
    rows = (1000, 2100)
    step = 100
    if "--rows" in sys.argv:
        rows = tuple(int(v) for v in sys.argv[sys.argv.index("--rows") + 1].split(","))
    if "--step" in sys.argv:
        step = int(sys.argv[sys.argv.index("--step") + 1])
    files = sorted(src.glob("period-*.png")) if src.is_dir() else [src]
    for p in files:
        a = np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)
        L = lum(a)
        W = a.shape[1]
        cx = W // 2
        print("== %s ==" % p.name)
        print("   row    竖     横    竖/横")
        for y in range(rows[0], rows[1], step):
            r = aniso(L, y, cx)
            if r is None:
                continue
            print("  %4d  %5.2f %5.2f  %6.2f%s" % (y, r[0], r[1], r[2],
                                                 "   ← 柱区" if 1700 <= y <= 1900 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
