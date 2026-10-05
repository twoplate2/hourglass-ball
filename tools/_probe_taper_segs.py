# -*- coding: utf-8 -*-
"""TAPER_SEGS: PC v4 = 10, Android = 24 —— 这 14 段的差在屏幕上值几像素?

背景: 模块级常量对比发现两边**只有这一个数不同**(K/FILL/`_bezier2` 实现都逐字相同),
      而文档里只记了"收缩下限"那一条偏离 ⇒ 这一条是**没人记过、也没人量过**的。

判据 (先标定): 折线顶点**本来就在真曲线上**, 误差全在"弦切掉的那块角"里 ⇒
      量 = 每段中点的真曲线点到该段直线的垂距, 取全程最大。
      参照: **0.5px 是亚像素** → 看不见; 2px 以上才可能改轮廓。
      负对照: 段数从 24 → 2 (明显不够), 这个量必须显著变大, 否则量具是坏的。

跑法: python tools/_probe_taper_segs.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURS = [1, 5, 15, 60, 300, 600, 3600]


def bez(P0, P1, P2, t):
    u = 1.0 - t
    return (u*u*P0[0] + 2*u*t*P1[0] + t*t*P2[0],
            u*u*P0[1] + 2*u*t*P1[1] + t*t*P2[1])


def fit_ctrl(pts, n):
    """从采样点反解控制点 —— 用端点 + 第 1 个内部点, 因为 B(t) 是精确在曲线上的。"""
    P0, P2 = pts[0], pts[-1]
    t = 1.0 / n
    u = 1.0 - t
    B = pts[1]
    P1 = ((B[0] - u*u*P0[0] - t*t*P2[0]) / (2*u*t),
          (B[1] - u*u*P0[1] - t*t*P2[1]) / (2*u*t))
    return P0, P1, P2


def max_chord_err(P0, P1, P2, n, samples=400):
    """n 段折线相对真曲线的最大偏差(段中点采样)。"""
    poly = [bez(P0, P1, P2, i / n) for i in range(n + 1)]
    worst = 0.0
    for i in range(n):
        a, b = poly[i], poly[i + 1]
        ex, ey = b[0] - a[0], b[1] - a[1]
        elen = (ex*ex + ey*ey) ** 0.5
        if elen < 1e-12:
            continue
        for k in range(samples + 1):
            t = (i + k / samples) / n
            p = bez(P0, P1, P2, t)
            # 点到直线 a→b 的距离
            d = abs(ex * (p[1] - a[1]) - ey * (p[0] - a[0])) / elen
            if d > worst:
                worst = d
    return worst


def main():
    with tempfile.TemporaryDirectory(prefix="tapersegs-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.save_config = lambda *_: None

        print("  先看生产值: 本仓库 TAPER_SEGS = %d, _bezier2 采样点数 = %d"
              % (m.TAPER_SEGS, len(m._bezier2((0, 0), (1, 1), (2, 0), m.TAPER_SEGS))))
        print("")
        print("  负对照(必须是坏的): 同一根曲线用 2 段采样")
        print("")
        head = "  %-8s %-9s %-9s | %-11s %-11s %s"
        print(head % ("周期", "neck_w", "w_out", "10段最大弦差", "24段最大弦差", "差(24-10)"))
        print("  " + "-" * 76)

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                for d in DURS:
                    w.set_duration(d)
                    tp = w._taper
                    outs = tp["out_pts"]
                    n = len(outs) - 1
                    P0, P1, P2 = fit_ctrl(outs, n)

                    # 量具自检: 反解出来的控制点必须能重建出原采样点
                    rebuilt = [bez(P0, P1, P2, i / n) for i in range(n + 1)]
                    resid = max(((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5
                                for a, b in zip(rebuilt, outs))

                    e10 = max_chord_err(P0, P1, P2, 10)
                    e24 = max_chord_err(P0, P1, P2, 24)
                    e2 = max_chord_err(P0, P1, P2, 2)
                    print("  %-8s %-9.2f %-9.2f | %-11.3f %-11.3f %+.3f   "
                          "[2段负对照 %.2f; 反解残差 %.1e]"
                          % ("%ds" % d, w.neck_w, P0[0], e10, e24, e24 - e10, e2, resid))
                print("")
                Clock.schedule_once(lambda d: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
