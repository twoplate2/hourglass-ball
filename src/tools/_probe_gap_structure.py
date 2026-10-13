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

    python src/tools/_probe_gap_structure.py --selftest
    python src/tools/_probe_gap_structure.py <ON标签> <OFF标签> [--time 8.77] [--band 40,300]
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

ROOT = Path(__file__).resolve().parents[2]


def load(label, t):
    d = ROOT / "_temp" / "benchmark_logs" / ("flow_visual_" + label)
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
    """A=带聚团臂, B=关掉臂(同状态)。返回结构量。

    🔴 **2026-10-11 独立验证查出的三个"标签会被读强"的坑, 这里逐个修**:
    1. 原来只有一个 `open_max` = 该行缝像素的**外跨度**(`xs[-1]-xs[0]+1`)。外跨度大
       **不等于**柱子被切断 —— 实测 t=21.18 报 46px(82% 柱宽), 而**最大连续开口只有 20px**,
       那 23 行是**两条边各自被收窄**、柱中心几乎没动。⇒ 现在**两个量分开报**:
       `span_*`(外跨度) 与 `run_*`(**连续开口**, 才是"断开"该看的)。
    2. `area_frac` 是**窗口量**(限定在 `box` 那几行里), 同一对臂换个窗口能从 2.4% 变 10%。
       ⇒ 现在一并打印**窗口的行范围**与**逐 100 行密度**(min/中位/max), 把口径摊开。
    3. 这个掩码是"两臂差分", **对机制不特异** —— 只改二值孔隙率(`HG_HOLE_TH`)也给出同量级。
       ⇒ 文档与调用处必须写清"这是**相对某条臂**的差", 不是"柱子断了多少"。
    """
    y0, y1, x0, x1 = box
    a = A[y0:y1, x0:x1].astype(np.int16)
    b = B[y0:y1, x0:x1].astype(np.int16)
    m = np.abs(a - b).max(axis=2) > thr
    band_px = m.size
    spans, runs = [], []
    for y in range(m.shape[0]):
        xs = np.where(m[y])[0]
        if not len(xs):
            spans.append(0)
            runs.append(0)
            continue
        spans.append(int(xs[-1] - xs[0] + 1))
        # 最长**连续**开口
        best = cur = 1
        for i in range(1, len(xs)):
            cur = cur + 1 if xs[i] == xs[i - 1] + 1 else 1
            best = max(best, cur)
        runs.append(int(best))
    spans = np.array(spans)
    runs = np.array(runs)
    W = m.shape[1]
    n, sizes = components(m)
    # 中轴那一列的"连续沙"游程(断点 = 缝像素连续段)
    cx = m.shape[1] // 2
    colmask = m[:, max(0, cx - 3):cx + 4].any(axis=1)
    segs, cur, breaks = [], 0, 0
    for v in colmask:
        if v:
            breaks += 1
            if cur:
                segs.append(cur)
            cur = 0
        else:
            cur += 1
    if cur:
        segs.append(cur)
    dens = [float(m[i:i + 100].mean()) for i in range(0, m.shape[0], 100)]
    return dict(area_px=int(m.sum()), area_frac=float(m.sum()) / band_px,
                holes=n, holes_px=sorted(sizes, reverse=True)[:6],
                span_max=int(spans.max()) if spans.size else 0,
                span_frac_max=float(spans.max()) / W if spans.size else 0.0,
                span_rows=int((spans > 0.7 * W).sum()),
                run_max=int(runs.max()) if runs.size else 0,
                run_frac_max=float(runs.max()) / W if runs.size else 0.0,
                run_rows=int((runs > 0.7 * W).sum()),
                breaks_on_axis=breaks,
                run_med=float(np.median(segs)) if segs else 0.0,
                run_longest=int(max(segs)) if segs else 0,
                dens_min=min(dens) if dens else 0.0,
                dens_med=float(np.median(dens)) if dens else 0.0,
                dens_max=max(dens) if dens else 0.0,
                band_rows=int(m.shape[0]))


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
    print("   洞数 %d (期望 5±1)   外跨度 %d px (期望 %d=柱宽)   连续开口 %d px"
          % (s["holes"], s["span_max"], on.shape[1], s["run_max"]))
    print("   外跨度>0.7柱宽的行 %d (期望 3)   连续开口>0.7柱宽的行 %d (期望 3)"
          % (s["span_rows"], s["run_rows"]))
    ok &= abs(s["area_px"] - exp_area) / exp_area <= 0.03
    ok &= abs(s["holes"] - 5) <= 1
    ok &= s["span_max"] == on.shape[1] and s["run_max"] == on.shape[1]
    ok &= s["span_rows"] == 3 and s["run_rows"] == 3

    # 🔴 **"外跨度 ≠ 连续开口" 的正对照**(独立验证查出的那个坑):
    #    同一行里**左右各一个洞、中间连着沙** ⇒ 外跨度 = 整行宽, 而连续开口只有单洞宽。
    print("== 正对照②(外跨度 ≠ 连续开口) ==")
    H, W = 60, 56
    base = np.full((H, W, 3), 240, dtype=np.int16)
    two = base.copy()
    two[30:33, 2:12] = 30          # 左洞(宽 10)
    two[30:33, W - 12:W - 2] = 30  # 右洞(宽 10), 中间 32px 连着沙
    s2 = structure(two, base, (0, H, 0, W), thr=6)
    # ⚠️ 期望值要**算出来**, 别写死: 左洞占 x 2..11, 右洞占 x W-12..W-3
    #    ⇒ 外跨度 = (W-3) - 2 + 1 = W - 4(不是 W —— 洞没顶到边缘)。
    #    (第一版把期望写成 W, 于是"标定没过"; 错的是期望, 不是代码。)
    exp_span, exp_run = W - 4, 10
    print("   外跨度 %d (期望 %d)   连续开口 %d (期望 %d)"
          % (s2["span_max"], exp_span, s2["run_max"], exp_run))
    print("   外跨度>0.7柱宽的行 %d   连续开口>0.7柱宽的行 %d (期望 0 —— **柱没被切断**)"
          % (s2["span_rows"], s2["run_rows"]))
    ok &= s2["span_max"] == exp_span and s2["run_max"] == exp_run
    ok &= s2["span_rows"] == 3 and s2["run_rows"] == 0

    print("== 负对照(自比) ==")
    s3 = structure(on, on, (0, on.shape[0], 0, on.shape[1]), thr=6)
    print("   面积 %d  洞数 %d  %s" % (s3["area_px"], s3["holes"],
                                     "OK" if s3["area_px"] == 0 and s3["holes"] == 0 else "!! 不合格"))
    ok &= s3["area_px"] == 0 and s3["holes"] == 0
    print("== 负对照(纯背景 vs 纯背景) ==")
    blank = np.full_like(on, 240)
    s4 = structure(blank, blank, (0, on.shape[0], 0, on.shape[1]), thr=6)
    print("   面积 %d 洞数 %d  %s" % (s4["area_px"], s4["holes"],
                                     "OK" if s4["area_px"] == 0 else "!! 不合格"))
    ok &= s4["area_px"] == 0
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
    # ⚠️ **口径必须一起报**: 这是**窗口量**(限定在 `box` 的行里), 同一对臂换窗口能从 2.4% 变 10%;
    #    而且它是**两臂差分**(改任何别的机制也会给同量级) —— 不是"柱子断了多少"。
    print("标签 %s vs %s @ t=%s  窗口: 行 %d..%d(相对出口 %d..%d), 列 %d..%d  共 %d 行"
          % (args.on_label, args.off_label, args.time, box[0], box[1],
             box[0] - row_outlet, box[1] - row_outlet, box[2], box[3], s["band_rows"]))
    print("  面积占比 %.2f%%(窗口量)  独立洞数 %d  |  逐100行密度 min %.2f%% / 中位 %.2f%% / max %.2f%%"
          % (s["area_frac"] * 100, s["holes"],
             s["dens_min"] * 100, s["dens_med"] * 100, s["dens_max"] * 100))
    print("  外跨度 max %d px(%.0f%% 柱宽), >0.7柱宽的行 %d   ← ⚠️ 外跨度大**不等于**断开"
          % (s["span_max"], s["span_frac_max"] * 100, s["span_rows"]))
    print("  连续开口 max %d px(%.0f%% 柱宽), >0.7柱宽的行 %d   ← 这个才是'断开'"
          % (s["run_max"], s["run_frac_max"] * 100, s["run_rows"]))
    print("  中轴游程: 断点 %d 个, 连续沙段 中位 %.0f px / 最长 %d px"
          % (s["breaks_on_axis"], s["run_med"], s["run_longest"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
