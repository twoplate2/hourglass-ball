# -*- coding: utf-8 -*-
"""**出口那条横向分界线**的尺子 —— 逐行纵向纹理强度剖面 + 阶跃比。

## 它量什么

出口（沙离开玻璃那一行）上下，**同一横向窗口内的"纵向高通 std"**：
    柱心 ±25px，对每一行取 `lum[y, x-25..x+25]`，做一维高通（减去自身 7 点均值），
    取 std。行内 std 大 = 那一行有横向的明暗起伏（比上下行更"花"）。

**分界线**表现为：出口以上一段持平、出口那一行附近突然抬起来。
判据 = **阶跃比 = 刚过出口那几行的中位 / 出口以上那几行的中位**。

## 为什么要重写一把

AP 面板临时写的 `_atk_ablate.py` 里那个 std 剖面**绝对值与我的对不上**
（他给 1.13→3.04，我给 1.83→3.93；"高通"的定义没写清）。判据必须**先标定**：
所以这把尺自带①合成台阶的正/负对照，②口径断言。

## 自检（必过）

- **正对照**：在一条**均匀噪声**带上人为插一条"更花"的横带 ⇒ 阶跃比必须 > 2。
- **负对照**：同一张**均匀噪声**图 ⇒ 阶跃比必须 ≈ 1（< 1.3）。
- **口径断言**：图像必须是 1080×1920（真机物理像素）—— 1904×2890 是放大区，判据不许用它。

用法:
    python tools/_probe_outlet_step.py --selftest
    python tools/_probe_outlet_step.py <图> [出口行] [标签]
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

HALF = 25          # 横向窗口半宽
ABOVE = (12, 4)    # 出口以上取样：离出口 12..4 行
BELOW = (1, 9)     # 刚过出口：1..9 行


def row_window(row, cx, half=HALF):
    """🔴 窗口必须**跟着每行实际宽度走**，只取内部 —— 否则会量出假阶跃。

    踩过(2026-10-10, 设备截图 1080): 固定 ±25px 的窗口，在**收口段**(宽 400~200px)只采到
    内部一小块(低 std)，到了**直筒**(宽 48px)窗口比管子还宽、把"沙→背景"那条边也框了进去
    ⇒ std 假性从 2.21 抬到 5.33，读成一条 2.4× 的"阶跃"，而它只是**窗口跨过了柱子的边缘**。
    ⇒ 改成: 先认这一行的沙色跨度，再取中间 60%。
    """
    m = (row[:, 0] - row[:, 2] > 40) & (row[:, 2] < 215)
    xs = np.nonzero(m)[0]
    if xs.size < 6:
        return None
    x0, x1 = int(xs[0]), int(xs[-1])
    w = x1 - x0 + 1
    pad = max(2, int(w * 0.20))
    x0, x1 = x0 + pad, x1 - pad
    if x1 - x0 < 4:
        return None
    return x0, x1


def row_profile(a, lum, y0, y1, cx, half=HALF):
    """逐行的**行内**纵向高通 std（内部 60%，不碰边缘）。"""
    out = []
    for y in range(y0, y1):
        win = row_window(a[y], cx, half)
        if win is None:
            out.append(np.nan); continue
        seg = lum[y, win[0]:win[1] + 1]
        k = min(7, max(3, (len(seg) // 3) | 1))
        hp = seg - np.convolve(seg, np.ones(k) / k, "same")
        out.append(float(hp[k // 2:-(k // 2)].std()))
    return np.array(out)


def step_ratio(a_img, lum, outlet_row, cx, half=HALF):
    a = row_profile(a_img, lum, outlet_row - ABOVE[0], outlet_row - ABOVE[1], cx, half)
    b = row_profile(a_img, lum, outlet_row + BELOW[0], outlet_row + BELOW[1], cx, half)
    ma = float(np.nanmedian(a)); mb = float(np.nanmedian(b))
    return mb / max(ma, 1e-6), ma, mb


def selftest():
    rng = np.random.default_rng(11)
    H, W = 300, 200
    # 合成图必须是**沙色**(判窗口靠 r-b>40)，否则认不出"柱"⇒ 全 NaN。
    base = np.array([217.0, 163.0, 96.0])
    flat = base[None, None, :] + rng.normal(0, 1.0, (H, W, 1))
    r1, a1, b1 = step_ratio(flat, flat @ np.array([0.299,0.587,0.114]), 150, W // 2)
    band = flat.copy()
    band[150:, 75:126] += rng.normal(0, 3.0, (H - 150, 51, 1))   # 出口以下更"花"
    r2, a2, b2 = step_ratio(band, band @ np.array([0.299,0.587,0.114]), 150, W // 2)
    print("  负对照 均匀噪声     阶跃比 %.2f  (上 %.2f / 下 %.2f)   %s"
          % (r1, a1, b1, "OK" if r1 < 1.3 else "!! 门槛要抬高"))
    print("  正对照 人为插台阶   阶跃比 %.2f  (上 %.2f / 下 %.2f)   %s"
          % (r2, a2, b2, "OK" if r2 > 2.0 else "!! 分辨力不足"))
    ok = r1 < 1.3 and r2 > 2.0
    print("  => %s" % ("判据可用（负对照≈1、正对照>2）" if ok else "!! 不合格"))
    return ok


def main():
    if "--selftest" in sys.argv:
        return 0 if selftest() else 1
    p = Path(sys.argv[1])
    a = np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)
    H, W = a.shape[:2]
    lum = a @ np.array([0.299, 0.587, 0.114])
    # 口径断言：只接受 1080 宽（或 400/692 那几个真机档），1904 直接拒
    if W >= 1800:
        print("  !! 口径 %dx%d 是**放大区**（1890 Kivy 单位），不是真机 —— 本尺拒收。" % (W, H))
        print("     请用 --pixels 1080,1920 重渲。")
        return 1
    outlet = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if outlet is None:
        # 自动找：柱内窄行的**最上一行**（出口 = 沙柱开始的地方）
        rows = []
        for y in range(int(H * 0.40), int(H * 0.80)):
            row = a[y]
            m = (row[:, 0] - row[:, 2] > 40) & (row[:, 2] < 215)
            xs = np.nonzero(m)[0]
            if xs.size >= 3 and (xs[-1] - xs[0] + 1) < W * 0.25:
                rows.append(y)
        if not rows:
            print("  %s: 找不到柱段" % p.name)
            return 1
        outlet = min(rows)
    tag = sys.argv[3] if len(sys.argv) > 3 else p.stem
    r, ma, mb = step_ratio(a, lum, outlet, W // 2)
    print("  %-22s %dx%d  出口行(自动/给定) %d   阶跃比 **%.2f**  (上 %.2f / 下 %.2f)"
          % (tag, W, H, outlet, r, ma, mb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
