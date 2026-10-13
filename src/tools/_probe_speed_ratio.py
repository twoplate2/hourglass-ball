# -*- coding: utf-8 -*-
"""**材料图案速度 vs 粒子速度律** —— 防止"两个速度差异大的层"回归。

## 为什么需要它

用户最早的判词就是「沙子下落的时候有 **2 个速度差异大的层**, 应该是**速度接近**」。
2026-10-11 对抗面板查出 + 本机实测确证: 材料坐标的时间项写成 `sand_time = elapsed·speed_scale`,
而速度律 `v_scaled(d) = √(v0p²+2·gp·d) = ms·√(60²+900d)` **已经是**粒子在屏幕上的真实速度
⇒ 图案速度 = `Ṫ/τ'(d) = ms²·√(…)`, 比粒子(**ms·√(…)**)快 **ms 倍**。
ms=1(长周期)时两者恰好相等 —— 所以 15s/50s/600s 档量到的 "560 vs 560" 是真的,
**缺陷只在 ms>1 的档**(平板口径: 3s≈2.09, 1s≈7.2)。金标准全是静态帧, 结构性看不见它。

## 判据(先标定)

实测材料图案速度(清粒子、关细颗粒) ÷ 解析律 `ms·√(60²+2·450·d)`,
**在 ms=1 档与 ms>1 档上必须相等**(极差 ≤ 1.15×)。

⚠️ **绝对值不是 1.0 而是 ≈1.2** —— 那个 1.2 是**这把尺自己的固定偏置**:
解析律取深度带**中点**的速度, 而互相关给的是带内按对比度加权的平均位移,
带内速度本身有 ~1.5 倍梯度。**两档一致才是判据**, 单看绝对值会误判。
(实测: 修前 3s 档 2.54 / 15s 档 1.20; 修后 3s 档 1.21 / 15s 档 1.26 ⇒ 极差 1.04 ✓)

⚠️ **位移必须落在搜索范围之内**: 修前 3s 档真位移 34px/帧, 而默认搜索半宽 20px
⇒ 峰被顶到边界, 读出一个**假的小值**(当时误读成"没有缺陷")。本探针会自检这一条。

## 用法

    python src/tools/_probe_speed_ratio.py                      # 跨档一致性(15s vs 3s)
    python src/tools/_probe_speed_ratio.py --period 3 --rng 45 --dt 0.01
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "tools"))
from _probe_grain_motion import trace, read_seq  # noqa: E402

V0, G = 60.0, 450.0


def render(label, period, pixels, extra_env, t0, dt):
    out = ROOT / "_temp" / "benchmark_logs" / ("flow_visual_" + label)
    if out.exists():
        shutil.rmtree(out)
    env = dict(os.environ)
    env.update(extra_env)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(ROOT / "src" / "tools" / "inspect_flow.py"), "--label", label,
           "--texture-flow", "--pixels", pixels,
           "--steady-period", str(period),
           "--window", "%.3f,%.4f,13" % (t0, dt)]
    r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True)
    txt = (r.stdout + r.stderr).decode("utf-8", "replace")
    ms = None
    # ⚠️ 优先取 `ms(真值)=` —— `_settle` 那次打印跑在**设周期之前**, 读到的恒为 1.0
    for pat in (r"ms\(真值\)=([\d.]+)", r"ms=([\d.]+)"):
        m = None
        for m in re.finditer(pat, txt):
            pass
        if m:
            ms = float(m.group(1))
            break
    return out, ms, txt


def measure_one(args, period=None):
    period = args.period if period is None else period
    W, H = (int(v) for v in args.pixels.split(","))
    cx = W / 2.0
    # 出口 ≈ 图高 − neck_outlet_y(Kivy y)。平板口径实测 1496(50s);
    # 用几何比例 row_outlet ≈ 0.518·H(与 measurements.json 的 neck_outlet_y 一致)。
    row_outlet = int(0.518 * H)
    b0, b1 = (int(v) for v in args.band.split(","))
    y0, y1 = row_outlet + b0, row_outlet + b1
    box = (y0, y1, int(cx - 28), int(cx + 28))
    dmid = ((y0 + y1) / 2.0) - row_outlet

    t0 = args.t0 if args.t0 is not None else (0.50 if period <= 2 else 1.20)
    dt = args.dt if args.dt is not None else (0.005 if period <= 2 else 0.01)
    mat_dir, ms, txt = render("speedratio_mat", period, args.pixels,
                              {"HG_FLOW_RATE": "0.001",
                               # 关掉细颗粒对比 ⇒ 自由段只剩**团缝**这一种结构,
                               # 互相关才不会被 3~6px 的沙纹理混叠带偏
                               "HG_FREE_GRAIN": "0",
                               "HG_CLUMP_SCALE": str(args.clump_scale)}, t0, dt)
    if ms is None:
        print("!! 没读到 ms —— 渲染没起来? 末尾几行:")
        print("\n".join(txt.strip().splitlines()[-6:]))
        return None

    frames, _t = read_seq(mat_dir)
    if len(frames) < 4:
        print("!! 帧太少(%d)" % len(frames))
        return None
    res = trace(frames, box, dt, rng=args.rng)
    rev = trace(frames, box, dt, rng=args.rng, reverse=True)
    if res is None or rev is None:
        print("!! 柱区没有可量的信号(纹理太平?)")
        return None

    law = ms * (V0 * V0 + 2.0 * G * dmid) ** 0.5
    ratio = res["speed"] / law
    print("周期 %.1fs  ms=%.3f  深度带 %d..%d (中位 %d px)" %
          (period, ms, y0 - row_outlet, y1 - row_outlet, int(dmid)))
    print("  解析律 ms·√(v0²+2gd) = %.0f px/s" % law)
    print("  材料实测            = %.0f px/s  (r 中位 %.3f, z 中位 %.1f, 位移中位 %.0f px)"
          % (res["speed"], res["r"], res["z"], res["move_med"]))
    print("  正对照｜帧序倒置     = %+.0f px/s  %s"
          % (rev["speed"], "OK(变号)" if res["speed"] * rev["speed"] < 0 else "!! 没变号"))
    if abs(res["move_med"]) >= args.rng - 1:
        print("  ⚠️ 位移顶到搜索边界 ⇒ **本次不作数**(加大 --rng 或减小 --dt)")
        return None
    print("  ===> 速度比 = **%.2f**" % ratio)
    return ratio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", type=float, default=None,
                    help="单档模式; 不给则跑 --periods 的跨档一致性模式")
    ap.add_argument("--periods", default="15,3",
                    help="跨档一致性: ms=1 档(默认 15s) 与 ms>1 档(默认 3s) 各量一次")
    ap.add_argument("--pixels", default="1904,2890")
    ap.add_argument("--clump-scale", type=float, default=1.0,
                    help="把团拉大(小尺度图案撞混叠限, 量不准)")
    ap.add_argument("--t0", type=float, default=None, help="密采样起点(默认按周期给)")
    ap.add_argument("--dt", type=float, default=None, help="帧间隔(默认按周期给)")
    ap.add_argument("--band", default="40,300", help="深度带 y0,y1(相对出口)")
    ap.add_argument("--rng", type=int, default=45,
                    help="互相关搜索半宽(px)。⚠️ 真位移超出它 ⇒ 峰被顶到边界, "
                         "读数变成**边界伪影**(实测过: 34px/帧 > 20 ⇒ 报了个假的小值)")
    args = ap.parse_args()

    if args.period is not None:
        r = measure_one(args)
        return 0 if r is not None else 2

    periods = [float(v) for v in args.periods.split(",")]
    ratios = []
    for P in periods:
        r = measure_one(args, period=P)
        if r is None:
            return 2
        ratios.append(r)
    spread = max(ratios) / min(ratios)
    print("\n===> 跨档一致性(判据): %s  极差 %.2f×  %s"
          % (["%.2f" % x for x in ratios], spread,
             "OK" if spread <= 1.15 else "!! 不合格 —— 材料与粒子**不同速**"))
    return 0 if spread <= 1.15 else 1


if __name__ == "__main__":
    sys.exit(main())
