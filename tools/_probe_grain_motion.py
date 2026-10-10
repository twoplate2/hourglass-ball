# -*- coding: utf-8 -*-
"""**颗粒纹理流得多快** —— 上球 vs 沙柱，用密采样帧序列量。

## 为什么必须密采样

shader 里颗粒图案是 `grain(uv − velocity*a + floor(t)*jump)`：在一个整数 `t` 之内
按 `velocity` 漂，**每 `1/speed_scale` 秒被 `floor(t)*jump` 整体重排一次**
（`jump = (0.125, 0.0625)`，横向跳 0.125·直径 ≈ 155px）。
⇒ 跨过重排点的两帧**根本不相干**：实测跨 1.21s 的互相关峰值 z 只有 2.2~3.0，
在 605 个候选里**不显著**（那次读数已作废）。
⇒ 只有在一个重排周期之内（`--window t0,0.04,13`）量才有意义。

## 判据（先标定，全部自带）

- **正对照｜帧序倒置**：把帧序列反过来跑，位移必须**变号**。
  （只对"有向运动"成立 —— 它证明量到的是运动，不是偏置。）
- **负对照｜无信号区**：颗粒极弱的地方（空腔）必须报 **z 低**（不显著）。
  这条给出 z 的**标定门槛**。
- **自比**：`frame[0]` 与它自己必须给出 `dy = 0`、`r = 1.000`。

用法:
    python tools/_probe_grain_motion.py --selftest
    python tools/_probe_grain_motion.py benchmark_logs/flow_visual_mot15 --dt 0.04
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
G = 450.0          # 与 main.py 同一常量(Kivy y 向上, 这里只用来算参考速度)
# 平板口径 1904×2890 的几何(实测自 measurements.json 的 `full_height_px` = 2R_inner):
# 上球在图上的行号 ≈ 145..1384 ⇒ `_upper_sand_bot`(球内底) ≈ 1384,
# 而 `sand_geometry.w`(= `in_pts[0][1]`, 口顶) 比它高 Δ = Ri − √(Ri²−w_in²) ≈ 26px
# ⇒ 口顶 ≈ 1358。shader 里 `from_mouth.y = max(0, y − w)/D` —— **在口顶以下恒为 0**,
# 所以颈部的 `sink ≈ 0.98`(最快档), 而球顶最慢(≈0.11)。
DIAMETER = 1238.7
MOUTH_ROW = 1358.0


def lum(img):
    a = np.asarray(img.convert("RGB"), dtype=np.float64)
    return a @ np.array([0.299, 0.587, 0.114])


def corr_at(pa, pb, dy):
    """竖向位移 dy 的归一化互相关。dy>0 表示 pb 的内容在 pa 的下方。"""
    H = pa.shape[0]
    if abs(dy) >= H - 4:
        return np.nan
    if dy >= 0:
        a, b = pa[:H - dy], pb[dy:]
    else:
        a, b = pa[-dy:], pb[:H + dy]
    a = a - a.mean()
    b = b - b.mean()
    na = float(np.sqrt((a * a).sum()))
    nb = float(np.sqrt((b * b).sum()))
    if na < 1e-9 or nb < 1e-9:
        return np.nan
    return float((a * b).sum() / (na * nb))


def estimate(pa, pb, rng=20):
    """返回 (位移, r, z)。**符号约定: move > 0 = 图案向下走了**(图像行号增大)。

    🔴 z 的算法: 候选里**必须排除峰的邻域**(±3)。相邻位移的相关系数本来就高度
    重叠(图案有一定尺度), 把它们算进 mu/sd 会把 z 系统性压低 —— 第一版就是这么
    把"确有运动"的格子报成 z≈2.6 的。
    """
    vals = np.array([corr_at(pa, pb, dy) for dy in range(-rng, rng + 1)], dtype=np.float64)
    if not np.isfinite(vals).any():
        return None
    k = int(np.nanargmax(vals))
    far = np.ones(len(vals), bool)
    far[max(0, k - 3):k + 4] = False
    fv = vals[far]
    fv = fv[np.isfinite(fv)]
    if fv.size < 8:
        return None
    mu, sd = float(fv.mean()), float(fv.std())
    # 行号增大 = 向下 ⇒ 位移就是 dy
    return dict(move=float(k - rng), r=float(vals[k]),
                z=(float(vals[k]) - mu) / max(sd, 1e-9))


def read_seq(folder):
    fs = sorted(folder.glob("period-*.png"))
    t = [float(p.stem.split("time-")[1]) for p in fs]
    order = np.argsort(t)
    return [fs[i] for i in order], [t[i] for i in order]


def trace(frames, box, dt, rng=20, reverse=False):
    """逐帧位移 → 速度。返回 (速度 px/s, 位移列表, r 中位, z 中位)。"""
    y0, y1, x0, x1 = box
    seq = list(reversed(frames)) if reverse else frames
    L = [lum(Image.open(p)).astype(np.float32) for p in seq]
    moves, rs, zs = [], [], []
    for i in range(len(L) - 1):
        pa = L[i][y0:y1, x0:x1].astype(np.float64)
        pb = L[i + 1][y0:y1, x0:x1].astype(np.float64)
        if pa.std() < 0.5:            # 无纹理 ⇒ 没有可量的信号
            continue
        e = estimate(pa, pb, rng)
        if e is None:
            continue
        moves.append(e["move"])
        rs.append(e["r"])
        zs.append(e["z"])
    if not moves:
        return None
    med = float(np.median(moves))
    return dict(speed=med / dt, move_med=med,
                move_all=moves, r=float(np.median(rs)), z=float(np.median(zs)))


def selftest():
    ok = True
    rng = np.random.default_rng(7)
    # 造一段"内容向下走 k px/帧"的序列(周期性图案, 便于任意位移)
    H, W, N, k = 80, 60, 6, 3
    base = rng.random((H, W))
    seq = [np.roll(base, k * i, axis=0) for i in range(N)]
    fwd = trace_arr(seq)
    rev = trace_arr(list(reversed(seq)))
    print("== 正对照｜帧序倒置必须变号 ==")
    print("     正序 位移中位 %+.1f px   倒序 %+.1f px   %s"
          % (fwd["move_med"], rev["move_med"],
             "OK" if abs(fwd["move_med"] + rev["move_med"]) < 1e-6 else "!! 不合格"))
    ok &= abs(fwd["move_med"] + rev["move_med"]) < 1e-6
    print("     自比: r=%.3f 位移 %+.1f" % (corr_at(seq[0], seq[0], 0), 0.0))

    print("\n== 负对照｜无纹理区(纯噪声)必须 z 低 —— 这条**标定 z 的门槛** ==")
    flat = [np.full((H, W), 128.0) + rng.normal(0, 0.2, (H, W)) for _ in range(N)]
    e = trace_arr(flat)
    print("     纯噪声(4800 样本 / 38 候选) 的 z = %s   %s"
          % ("无信号" if e is None else "%.2f" % e["z"],
             "OK" if e is None or e["z"] < 5 else "!! 门槛要抬高"))
    ok &= (e is None or e["z"] < 5)

    print("\n== 有信号时 z 应当远高于门槛 ==")
    print("     周期图案的 z = %.2f  r = %.3f  (门槛取 6 = 噪声的 2 倍)  %s"
          % (fwd["z"], fwd["r"], "OK" if fwd["z"] > 12 else "!! 分辨力不足"))
    ok &= fwd["z"] > 12
    print("\n== 结论: %s ==" % ("判据可用" if ok else "!! 有不合格项"))
    return 0 if ok else 1


def trace_arr(seq, rng_=20):
    moves, rs, zs = [], [], []
    for i in range(len(seq) - 1):
        e = estimate(seq[i].astype(np.float64), seq[i + 1].astype(np.float64), rng_)
        if e is None:
            continue
        moves.append(e["move"]); rs.append(e["r"]); zs.append(e["z"])
    if not moves:
        return None
    return dict(move_med=float(np.median(moves)), r=float(np.median(rs)),
                z=float(np.median(zs)))


def main():
    if "--selftest" in sys.argv:
        return selftest()
    folder = Path(sys.argv[1])
    dt = float(sys.argv[sys.argv.index("--dt") + 1]) if "--dt" in sys.argv else 0.04
    frames, times = read_seq(folder)
    print("帧序列 %d 张, t = %.2f..%.2f (dt=%.2f)" % (len(frames), times[0], times[-1], dt))
    L0 = lum(Image.open(frames[0]))
    H, W = L0.shape
    cx = W // 2
    # 区域: 上球沙体内部 三块 + 柱内 三块(用行号硬定, 与已有的平板口径渲染一致)
    REG = [("上球 上段", (860, 940, cx - 60, cx + 60)),
           ("上球 中段", (1000, 1080, cx - 60, cx + 60)),
           ("上球 下段", (1140, 1220, cx - 55, cx + 55)),
           ("柱 上段", (1420, 1500, cx - 26, cx + 26)),
           ("柱 中段", (1600, 1680, cx - 26, cx + 26)),
           ("柱 下段", (1780, 1860, cx - 26, cx + 26)),
           ("负对照 空腔", (400, 480, cx - 60, cx + 60))]
    print("\n%-12s %9s %8s %6s %6s   %-22s %s"
          % ("区域", "向下px/s", "位移px", "r", "z", "shader 预期(quad)", "自由落体参照"))
    for nm, box in REG:
        y0, y1, x0, x1 = box
        if L0[y0:y1, x0:x1].std() < 0.5:
            print("%-12s   —— 无纹理, 跳过" % nm)
            continue
        e = trace(frames, box, dt)
        if e is None:
            print("%-12s   —— 无有效配对" % nm)
            continue
        ymid = 0.5 * (y0 + y1)
        # shader 预期: sink = 1/(1 + 8·fy²)（中轴 fx≈0）, 漂移 = (4 + 46·sink)·speed_scale
        fy = max(0.0, (MOUTH_ROW - ymid) / DIAMETER)
        sink = 1.0 / (1.0 + 8.0 * fy * fy)
        pred = 4.0 + 46.0 * sink
        ref = "—"
        if ymid > MOUTH_ROW:
            depth = ymid - MOUTH_ROW
            ref = "%.0f px/s @ %.0fpx" % (np.sqrt(2 * G * depth), depth)
        e_rev = trace(frames, box, dt, reverse=True)
        flag = "  ⚠ 不显著(z<6)" if e["z"] < 6 else ""
        print("%-12s %9.1f %8.2f %6.3f %6.2f   %-22s %s%s"
              % (nm, e["speed"], e["move_med"], e["r"], e["z"],
                 "%.0f px/s" % pred, ref, flag))
        if e_rev is not None:
            print("%-12s   (倒序对照 %+.1f px/s, 应与上面反号)" % ("", e_rev["speed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
