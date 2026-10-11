# -*- coding: utf-8 -*-
"""**缝的结构量具** —— "有连续有断续"到底长什么样, 用数字说, 不靠眼睛。

## 为什么必须新建它

2026-10-11 的对抗面板查出: 全项目**没有任何量具能看见**"有连续有断续"。
唯一在用的 α(双背景差分, `_probe_column_alpha.py`)对这件事**结构性失明** ——
实测 `clumpgap` 0.15→0.65 只让 p10 动 **0.007**; 原因是**缝的面积只占柱带 2~9.4%**,
低于 p10 的 10% 分位质量。而用户为了这件事已经把版本否了两轮(2.65 → 2.68 → 2.69)。

## 定义(全部只用"两臂差分", 与背景色/沙色/纹理无关)

`缝掩码 = |ON − OFF| > 阈` 且**在柱带内**(ON = 带聚团的臂, OFF = 同状态关掉聚团)。
输出四个量:
  · **面积占比**  缝像素 / 柱带像素
  · **独立洞数**  连通域(≥`--min-px`)
  · **单行最大开口** 逐行缝像素的水平跨度(px); 另报 **跨宽断开行数**(开口 > 0.7·柱宽)
  · **游程**      沿中轴的"连续沙"段长分布(中位/最长), 以及**每柱长有几个断点**

## 判据(先标定 —— 两条对照都必须过)

1. **正对照(合成)**: 在一张纯背景图上画 k 个已知半径的洞 + 一条已知长度的横断 ⇒
   面积/洞数/最大开口/断开行数**必须逐个报对**(容差: 面积 ±3%, 洞数 ±1, 开口 ±2px)。
2. **负对照(真图)**: 同一臂自比(ON vs ON)⇒ **洞数 0、面积 0**;
   关掉聚团的臂 vs 自己 ⇒ 同样 0。**过不了这两条, 读数一律不作数。**

## 用法

    python tools/_probe_gap_structure.py --selftest
    python tools/_probe_gap_structure.py <ON标签> <OFF标签> [--time 8.77] [--band 40,300]
"""
import argparse
import sys
from collections import deque
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load(label, t):
    d = ROOT / "benchmark_logs" / ("flow_visual_" + label)
    p = d / ("period-%s-time-%s.png" % (label_period(d), t))
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.int16)


def label_period(d):
    """从目录里第一张图的名字里取周期(如 `period-50.0-time-8.77.png` → `50.0`)。"""
    for f in sorted(d.glob("period-*-time-*.png")):
        return f.name.split("-")[1]
    raise FileNotFoundError("目录里没有 period-*.png: %s" % d)


def components(mask, min_px=4):
    """4 邻域连通域。返回 (个数, 各域面积列表)。"""
    seen = np.zeros_like(mask, dtype=bool)
    ys, xs = np.where(mask)
    H, W = mask.shape
    out = []
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        n = 0
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            n += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                yy, xx = y + dy, x + dx
                if 0 <= yy < H and 0 <= xx < W and mask[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    q.append((yy, xx))
        if n >= min_px:
            out.append(n)
    return len(out), out


def structure(A, B, box, thr=6):
    """A=带聚团臂, B=关掉臂(同状态)。返回四个结构量。"""
    y0, y1, x0, x1 = box
    a = A[y0:y1, x0:x1].astype(np.int16)
    b = B[y0:y1, x0:x1].astype(np.int16)
    m = np.abs(a - b).max(axis=2) > thr
    band_px = m.size
    rows = []
    for y in range(m.shape[0]):
        xs = np.where(m[y])[0]
        rows.append(int(xs[-1] - xs[0] + 1) if len(xs) else 0)
    rows = np.array(rows)
    n, sizes = components(m)
    # 中轴那一列的"连续沙"游程(断点 = 缝像素连续段)
    cx = m.shape[1] // 2
    colmask = m[:, max(0, cx - 3):cx + 4].any(axis=1)
    runs, cur, breaks = [], 0, 0
    for v in colmask:
        if v:
            breaks += 1
            if cur:
                runs.append(cur)
            cur = 0
        else:
            cur += 1
    if cur:
        runs.append(cur)
    return dict(area_px=int(m.sum()), area_frac=float(m.sum()) / band_px,
                holes=n, holes_px=sorted(sizes, reverse=True)[:6],
                open_max=int(rows.max()) if rows.size else 0,
                open_frac_max=float(rows.max()) / m.shape[1] if rows.size else 0.0,
                wide_rows=int((rows > 0.7 * m.shape[1]).sum()),
                breaks_on_axis=breaks,
                run_med=float(np.median(runs)) if runs else 0.0,
                run_max=int(max(runs)) if runs else 0)


def synth(k_holes=5, r=9, wide_row=40, H=420, W=56, seed=3):
    """正对照底图: 纯背景 + k 个圆洞 + 一条宽 `wide_row` 的横断。"""
    rng = np.random.default_rng(seed)
    base = np.full((H, W, 3), 240, dtype=np.int16)
    on = base.copy()
    yy, xx = np.mgrid[0:H, 0:W]
    for _ in range(k_holes):
        cy = int(rng.integers(r + 2, H - r - 2))
        cxx = int(rng.integers(r + 2, W - r - 2))
        m = (yy - cy) ** 2 + (xx - cxx) ** 2 <= r * r
        on[m] = 30
    on[H // 2:H // 2 + 3, :] = 30          # 一条贯通横断(3 行)
    return on, base


def selftest():
    ok = True
    print("== 正对照(合成: 5 个 r=9 的洞 + 一条贯通横断) ==")
    on, off = synth()
    s = structure(on, off, (0, on.shape[0], 0, on.shape[1]), thr=6)
    exp_area = 5 * 3.14159 * 81 + 3 * on.shape[1]
    print("   面积 %d px (期望 ≈%d, 偏差 %.1f%%)" %
          (s["area_px"], exp_area, abs(s["area_px"] - exp_area) / exp_area * 100))
    print("   洞数 %d (期望 5±1)   单行最大开口 %d px (期望 %d=柱宽)   跨宽断开行 %d (期望 3)"
          % (s["holes"], s["open_max"], on.shape[1], s["wide_rows"]))
    ok &= abs(s["area_px"] - exp_area) / exp_area <= 0.03
    ok &= abs(s["holes"] - 5) <= 1
    ok &= s["open_max"] == on.shape[1]
    ok &= s["wide_rows"] == 3
    print("== 负对照(自比) ==")
    s2 = structure(on, on, (0, on.shape[0], 0, on.shape[1]), thr=6)
    print("   面积 %d  洞数 %d  %s" % (s2["area_px"], s2["holes"],
                                     "OK" if s2["area_px"] == 0 and s2["holes"] == 0 else "!! 不合格"))
    ok &= s2["area_px"] == 0 and s2["holes"] == 0
    print("== 负对照(纯背景 vs 纯背景) ==")
    blank = np.full_like(on, 240)
    s3 = structure(blank, blank, (0, on.shape[0], 0, on.shape[1]), thr=6)
    print("   面积 %d 洞数 %d  %s" % (s3["area_px"], s3["holes"],
                                     "OK" if s3["area_px"] == 0 else "!! 不合格"))
    ok &= s3["area_px"] == 0
    print("==> 标定", "通过" if ok else "**没过 —— 读数不许用**")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("on_label", nargs="?")
    ap.add_argument("off_label", nargs="?")
    ap.add_argument("--time", default="8.77")
    ap.add_argument("--band", default="40,300", help="深度带 y0,y1(相对出口)")
    ap.add_argument("--pixels", default="1080,1920")
    ap.add_argument("--thr", type=int, default=6)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.on_label or not args.off_label:
        ap.error("要么 --selftest, 要么给两个标签")

    W, H = (int(v) for v in args.pixels.split(","))
    row_outlet = int(0.518 * H)
    b0, b1 = (int(v) for v in args.band.split(","))
    box = (row_outlet + b0, row_outlet + b1, int(W / 2 - 28), int(W / 2 + 28))
    A, B = load(args.on_label, args.time), load(args.off_label, args.time)
    s = structure(A, B, box, thr=args.thr)
    print("标签 %s vs %s @ t=%s  柱带 %dx%d" %
          (args.on_label, args.off_label, args.time, box[1] - box[0], box[3] - box[2]))
    print("  面积占比 %.2f%%   独立洞数 %d   单行最大开口 %d px(%.0f%% 柱宽)  跨宽断开行 %d"
          % (s["area_frac"] * 100, s["holes"], s["open_max"], s["open_frac_max"] * 100,
             s["wide_rows"]))
    print("  中轴游程: 断点 %d 个, 连续沙段 中位 %.0f px / 最长 %d px"
          % (s["breaks_on_axis"], s["run_med"], s["run_max"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
