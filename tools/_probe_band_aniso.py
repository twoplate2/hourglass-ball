# -*- coding: utf-8 -*-
"""**同质性门控**的纹理各向异性 —— 修掉"固定窗口跨越玻璃/沙边界"这个伪影。

## 为什么要有它(2026-10-10 教训)

我用固定窗口(行 430–466 × x 190–211)量颈部, 报了"各向异性 3.3", 并在两条消息里
拿它当判据。后来把颈部两带清空后发现: **那个窗口里有一半行是玻璃(234,243,248)**
⇒ 那个数是"平玻璃 + 沙"的混合统计, 不是纹理。
**固定窗口必须逐行验同质性, 否则量到的是窗口形状, 不是画面。**

## 判据

逐行把窗口内像素分成三类(沙 / 玻璃 / 其它), **只保留沙占比 ≥ `--min-sand` 的行**;
按"绘制者"分区太复杂, 先用颜色分区 —— 玻璃色 `GLASS_FILL` 是已知的单一来源。
再在留下的子块上算 ACF 半长(先做二维箱式高通, 核两向同尺寸)。

## 自检(必过, 两种状态各跑一次)

- **正对照(已知同质)**: 上球沙体内部 ⇒ 必须报"全行通过", 且 竖/横 落在 0.5~2.0。
- **负对照(已知跨界)**: 给该块人为塞入 1/3 行玻璃色 ⇒ 必须**丢掉那些行**,
  且剩下的子块与"原块只取沙行"逐位一致。

用法:
    python tools/_probe_band_aniso.py <png> <y0> <y1> <x0> <x1> [--min-sand 0.95] [--selftest]
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

GLASS = np.array([234.0, 243.0, 248.0])
K = 25
MAXLAG = 25


def classify(a):
    """0=其它, 1=沙, 2=玻璃。"""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    sand = (r - b > 40) & (b < 215)
    glass = (np.abs(a - GLASS).max(axis=2) < 12)
    out = np.zeros(a.shape[:2], dtype=np.int8)
    out[sand] = 1
    out[glass] = 2
    return out


def hp2d(p, k=None):
    # 🔴 核宽必须**随窗口缩放**: 固定 25px 核作用在 21px 宽的窗口上会把横向自相关整个吃掉,
    #    读出来的"横半长 0.5"是滤波器给的, 不是画面 (2026-10-10 栽过, 报了个假的 6.6)。
    if k is None:
        k = max(3, min(K, (min(p.shape) // 3) | 1))
    h, w = p.shape
    c = np.cumsum(np.cumsum(p, 0), 1)
    c = np.pad(c, ((1, 0), (1, 0)))
    y0 = np.clip(np.arange(h) - k // 2, 0, h); y1 = np.clip(np.arange(h) + k // 2 + 1, 0, h)
    x0 = np.clip(np.arange(w) - k // 2, 0, w); x1 = np.clip(np.arange(w) + k // 2 + 1, 0, w)
    s = c[np.ix_(y1, x1)] - c[np.ix_(y0, x1)] - c[np.ix_(y1, x0)] + c[np.ix_(y0, x0)]
    n = (y1 - y0)[:, None] * (x1 - x0)[None, :]
    return p - s / np.maximum(n, 1)


def half_len(a, axis, maxlag=MAXLAG):
    a = a - a.mean(axis=axis, keepdims=True)
    v = float((a * a).mean())
    if v <= 1e-9:
        return float("nan")
    prev, prevL = 1.0, 0
    for L in range(1, maxlag + 1):
        x, y = (a[L:, :], a[:-L, :]) if axis == 0 else (a[:, L:], a[:, :-L])
        c = float((x * y).mean()) / v
        if c <= 0.5:
            return prevL + (0.5 - prev) / (c - prev) * (L - prevL) if c != prev else float(L)
        prev, prevL = c, L
    return float(maxlag)


def measure(img, y0, y1, x0, x1, min_sand=0.95, label=""):
    cls = classify(img[y0:y1, x0:x1])
    keep = (cls == 1).mean(axis=1) >= min_sand
    # 🔴 **纹理门控**: 颜色是沙但**是平的**(常数)的行必须丢掉 —— 平坦行在纵向自相关里
    #    是"完美相关", 会把竖半长整个顶上去 (2026-10-10: 颈部窗口混进 3 行 lum≡177 的
    #    平沙, 就把比从 ~1 顶到 5.9)。**颜色同质 ≠ 纹理同质。**
    lum0 = img[y0:y1, x0:x1] @ np.array([0.299, 0.587, 0.114])
    kk = min(5, max(3, (lum0.shape[1] // 3) | 1))
    ker = np.ones(kk) / kk
    # **逐行**沿 x 做高通(上一版错在对"行均值"卷积 ⇒ 门控形同虚设)
    hp0 = lum0 - np.apply_along_axis(lambda r: np.convolve(r, ker, "same"), 1, lum0)
    hp0 = hp0[:, kk // 2:-(kk // 2)]
    keep &= hp0.std(axis=1) >= 1.0
    n = int(keep.sum())
    if n < 8:
        return None, n, (y1 - y0)
    lum = img[y0:y1, x0:x1] @ np.array([0.299, 0.587, 0.114])
    P = lum[keep]
    Q = hp2d(P)
    pv, ph = half_len(Q, 0), half_len(Q, 1)
    return (pv, ph, pv / max(ph, 1e-6)), n, (y1 - y0)


def run(p, y0, y1, x0, x1, minsand):
    img = np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)
    res, n, tot = measure(img, y0, y1, x0, x1, minsand)
    tag = Path(p).stem
    if res is None:
        print("  %-22s y[%d..%d] x[%d..%d]  同质行 %d/%d ⇒ **样本不足, 不作数**"
              % (tag, y0, y1, x0, x1, n, tot))
        return
    pv, ph, r = res
    print("  %-22s y[%d..%d] x[%d..%d]  同质行 %d/%d ⇒ 竖%.2f 横%.2f **比 %.2f**"
          % (tag, y0, y1, x0, x1, n, tot, pv, ph, r))


def selftest():
    """🔴 **必须在目标窗口尺寸上标定** —— 宽窗口上正常的尺, 窄窗口上可能整个失效。"""
    img = np.asarray(Image.open(_SELF_PNG).convert("RGB"), dtype=np.float64)
    ok = True
    lum = img @ np.array([0.299, 0.587, 0.114])

    # ① 已知各向同性, **宽**窗口(80px): 应 ≈1.0
    r, n, tot = measure(img, 600, 700, 160, 240, 0.95)
    print("  ① 沙堆 80px 宽窗口      同质 %d/%d  比 %.2f   %s" % (n, tot, r[2], "OK" if 0.5 <= r[2] <= 2.0 else "!!"))
    ok &= 0.5 <= r[2] <= 2.0

    # ② **同样各向同性, 但窗口压到 21px**(= 颈部的实际宽度): 仍应 ≈1.0
    r2, n2, t2 = measure(img, 600, 700, 199, 220, 0.95)
    print("  ② 沙堆 21px 宽窗口      同质 %d/%d  比 %.2f   %s  ← **这条决定尺在颈部能不能用**"
          % (n2, t2, r2[2], "OK" if 0.5 <= r2[2] <= 2.0 else "!! 窄窗口会假报各向异性"))
    ok &= 0.5 <= r2[2] <= 2.0

    # ③b **平坦行门控**: 同一窗口, 把一半行换成常数 ⇒ 必须丢掉那些行
    flat = img.copy(); flat[650:680, 160:240] = np.array([217.0, 163.0, 96.0])
    _, n3, t3 = measure(flat, 600, 700, 160, 240, 0.95)
    print("  ③b 塞入 30 行平沙        同质 %d/%d   %s" % (n3, t3, "OK 丢掉了" if n3 <= t3 - 25 else "!! 没拦住平沙"))
    ok &= n3 <= t3 - 25

    # ③ 负对照: 同一 21px 窗口, 把画面纵向拉伸 3 倍 ⇒ 必须报出来
    st = np.repeat(lum[600:650, 199:220], 3, axis=0)
    Q = hp2d(st)
    pv, ph = half_len(Q, 0), half_len(Q, 1)
    print("  ③ 同窗口 纵向拉伸 3x     比 %.2f   %s" % (pv / max(ph, 1e-6),
          "OK 有分辨力" if pv / max(ph, 1e-6) > 2.0 else "!! 分辨力不足"))
    ok &= pv / max(ph, 1e-6) > 2.0

    # ④ 跨界行必须被丢掉
    bad = img.copy(); bad[640:660, 160:240] = GLASS
    _, n4, t4 = measure(bad, 600, 700, 160, 240, 0.95)
    print("  ④ 塞入 20 行玻璃        同质 %d/%d   %s" % (n4, t4, "OK 丢掉了" if n4 <= t4 - 18 else "!! 没拦住"))
    ok &= n4 <= t4 - 18
    print("  => %s" % ("尺可用(四档全过)" if ok else "!! 尺不可用"))
    return ok


_SELF_PNG = "benchmark_logs/flow_visual_pc_now/period-10.0-time-8.00.png"

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    p = sys.argv[1]
    y0, y1, x0, x1 = (int(v) for v in sys.argv[2:6])
    ms = 0.95
    if "--min-sand" in sys.argv:
        ms = float(sys.argv[sys.argv.index("--min-sand") + 1])
    _SELF_PNG = p
    run(p, y0, y1, x0, x1, ms)
