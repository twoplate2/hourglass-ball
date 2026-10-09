# -*- coding: utf-8 -*-
"""沙柱粗细律的**纯几何**量具(不渲染、不读图)。

为什么要有它: 文献与用户都问过"该收多细", 而**答案取决于口径** —— 桌面预览 400×800
(球径≈196px) 与用户平板 1904×2890(球径≈1132px) 差 **5.8 倍**。任何写死像素的参数
(现在的 `min(1.0, depth/40.0)`) 在两端口径上表现差 5.8 倍。这个脚本把候选律在**两种
口径**上的实际曲线并排打出来, 好让"选哪个数"这个决定有一张共同的表。

跑法: python tools/_probe_shrink_curve.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

V0 = 60.0          # 出口初速(px/s) —— 与 `update_particles` 同源
G = 450.0          # 重力(px/s^2) —— 同上
RAMP = 40.0        # 现有: 出管后 40px 线性过渡(**写死像素**)


def shrink(d, floor=None, send=None, dmax=None, v0=V0, g=G, ramp=RAMP):
    """`d` 处的半宽比。`send`/`dmax` 给定 ⇒ 反解强度 `S` 使"落到底恰好收到 send"。"""
    if send is not None and dmax:
        S = (v0 * v0) * (send ** -4 - 1.0) / dmax
    else:
        S = 2.0 * g
    t = (v0 * v0 / (v0 * v0 + S * d)) ** 0.25
    if floor is not None:
        t = max(t, floor)
    return 1.0 + (t - 1.0) * min(1.0, d / ramp)


ARMS = (("现状  floor=0.70", dict(floor=0.70)),
        ("P2    floor=0.50 (pc v4)", dict(floor=0.50)),
        ("P1    到底 0.55", dict(send=0.55)),
        ("P1    到底 0.45", dict(send=0.45)),
        ("P1    到底 0.24 (纯物理)", dict()))

CALIBERS = (("桌面 400x800", 196.4, 12.38), ("平板 1904x2890", 1132.2, 40.63))
FRACS = (0.01, 0.05, 0.15, 0.35, 0.6, 1.0)


def main():
    for name, ball, tin in CALIBERS:
        dmax = ball
        print("\n== %s  球径 %.0fpx  孔径 %.1fpx  落程 %.0fpx  (ramp %.0fpx = 落程的 %.1f%%) =="
              % (name, ball, 2 * tin, dmax, RAMP, 100.0 * RAMP / dmax))
        print("     d(px):  " + "".join("%9.0f" % (dmax * f) for f in FRACS))
        print("     d/落程: " + "".join("%9.0f%%" % (100 * f) for f in FRACS))
        for label, kw in ARMS:
            kw = dict(kw)
            if "send" in kw:
                kw["dmax"] = dmax
            print("   %-24s" % label + "".join("%9.3f" % shrink(dmax * f, **kw) for f in FRACS))
    print("")
    print("  [read] same row across the two calibers differs in SHAPE = caliber dependence")
    print("  [read] the `send` family is normalized by fall length -> same shape on both")


if __name__ == "__main__":
    main()
