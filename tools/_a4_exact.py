# -*- coding: utf-8 -*-
"""A4 一锤定音: 在**同一高度**上比较"画出来的柱边缘" vs "画出来的云最外墨迹"。

双方实测相反:
  - 一方: 每 20px 桶取云 |x-cx|+psz/2 最大值与柱折线比 -> 37/40 桶为正(云更宽)。
  - 另一方: core-钳后cloud = 平板 +1.87px(用 lim-2 当云缘, 未把线宽一半加回去)。

本量具:
  col(d)   = 柱多边形右边轮廓在该深度处的半宽。**按路径顺序分段**(不是按深度排序插值
             —— 桌面口径下末三节点会出现"侧端点比中轴点更深"的倒序, 按深度排序会算错),
             同一深度有多个交点时取 max。
  edge_i   = |px_i - cx| + psz_i/2      (该颗画出来的最外缘)
  墨迹窗   = 高度 d 处的可见墨迹来自 [d - trail(d), d] 内的颗粒(trail=条形拖尾,
             trail(d)=max(2, v(d)*0.08), v(d)=sqrt(60^2+900d)); 另打印 ±2px 窗对照。
  poke(d)  = col(d) - max{edge_i : 墨迹窗}
判据: max poke > ~0.5px 才算"柱从云外鼓出"; <=0 = 云盖住柱。

跑法: python tools/_a4_exact.py 1904 2890 2.75 600 2.0
"""
import math
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WW = int(sys.argv[1])
HH = int(sys.argv[2])
DENS = sys.argv[3]
PERIOD = float(sys.argv[4])
AT = float(sys.argv[5])
SEED = int(sys.argv[6]) if len(sys.argv) > 6 else 20261009
os.environ.setdefault("KIVY_METRICS_DENSITY", DENS)
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="a4-exact-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])
    import random as _rnd
    _rnd.seed(SEED)

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (WW, HH)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            if (Window.size[0], Window.size[1]) != (WW, HH):
                print("  !! 窗口没拿到 %dx%d, 实得 %s => 本次不作数"
                      % (WW, HH, tuple(Window.size)))
                self.stop()
                return
            self.root.apply_orientation()
            self.root.do_layout()
            self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD)
            w.reset()
            w._rebuild_height_table()
            w.toggle()
            steps = max(1, int(round(AT * 60)))
            for _ in range(steps):
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False
            w.redraw()

            outlet = 2 * w._neck_y - w._taper["y_bot"]
            t_in = w._taper["t_in"]
            side = w._neck_sand_side()
            pts = [(outlet - y, x) for x, y in side if outlet - y >= -0.5]
            segs = []
            for (d0, x0), (d1, x1) in zip(pts, pts[1:]):
                if max(d0, d1) < -0.5:
                    continue
                segs.append((d0, x0, d1, x1))

            def col(d):
                best = None
                for d0, x0, d1, x1 in segs:
                    lo, hi = min(d0, d1), max(d0, d1)
                    if lo - 1e-6 <= d <= hi + 1e-6:
                        t = 0.0 if hi <= lo else (d - lo) / (hi - lo)
                        # 按**路径顺序**插值(允许 d 倒序, 如桌面末三节点)
                        if d0 <= d1:
                            xx = x0 + (x1 - x0) * t
                        else:
                            xx = x1 + (x0 - x1) * t
                        best = xx if best is None else max(best, xx)
                return 0.0 if best is None else best

            def trail_at(d):
                return max(2.0, math.sqrt(60.0 ** 2 + 900.0 * max(0.0, d)) * 0.08)

            print("")
            print("  ===== A4-exact %dx%d d=%s  周期 %.1fs  t=%.2fs  seed=%d =====" %
                  (WW, HH, DENS, PERIOD, AT, SEED))
            print("  outlet=%.1f t_in=%.2f  pn=%d elapsed=%.3f" %
                  (outlet, t_in, w.pn, w.elapsed))
            print("  自由段节点(路径序): %s" %
                  ", ".join("(%.2f,%.3f)" % (d, x) for d, x in pts))

            ds, edges, trs, xos = [], [], [], []
            ms = w._particle_motion_scale
            tb = float(getattr(w._pv, "tail_blend", 0.0) or 0.0)
            have = hasattr(w, "pvy") and hasattr(w, "ptl")
            for i in range(w.pn):
                d = outlet - w.py[i]
                if d < -0.5:
                    continue
                ds.append(d)
                xos.append(abs(w.px[i] - w._cx))
                edges.append(abs(w.px[i] - w._cx) + w.psz[i] * 0.5)
                if have:
                    tr = max(2.0, abs(w.pvy[i]) * w.ptl[i] / ms)
                    tr = tr * (1.0 - tb) + tb
                else:
                    tr = max(2.0, math.sqrt(60.0 ** 2 + 900.0 * max(0.0, d)) * 0.08)
                trs.append(tr)
            n = len(ds)

            def winmax(d, mode):
                # 拖尾向上(y+trail) ⇒ 高度 d 的墨迹来自 d_i ∈ [d, d_i+trail_i]
                mm = None
                for i in range(n):
                    if mode == "ink":
                        if ds[i] >= d - 1e-9 and ds[i] - trs[i] <= d:
                            e = edges[i]
                            if mm is None or e > mm:
                                mm = e
                    else:
                        if d <= ds[i] <= d + 2.0:
                            e = edges[i]
                            if mm is None or e > mm:
                                mm = e
                return mm

            dmax = max(ds) if ds else 0.0
            grid = []
            d = 0.0
            while d <= min(dmax, 1200.0):
                mi = winmax(d, "ink")
                if mi is not None:
                    grid.append((d, col(d), mi, winmax(d, "p2")))
                d += 1.0

            for mode, idx in (("墨迹窗", 2), ("±2px窗", 3)):
                cand = [g for g in grid if g[idx] is not None]
                gp = max(cand, key=lambda g: g[1] - g[idx]) if cand else None
                if gp:
                    print("  poke(%s)=col-max_edge: 全深最大 %+.3fpx @ d=%.1f (col=%.3f, edge=%.3f)"
                          % (mode, gp[1] - gp[idx], gp[0], gp[1], gp[idx]))
            print("  顶部明细(d<=12): col | 墨迹窗edge | poke | ±2px edge")
            for d, c, mi, m2 in grid:
                if d > 12.0:
                    break
                print("     d=%5.1f  col=%7.3f  edge_ink=%7.3f  poke=%+7.3f  (±2px edge=%s)"
                      % (d, c, mi, c - mi, ("%.3f" % m2) if m2 is not None else "-"))
            bkt = {}
            for g in grid:
                bkt.setdefault(int(g[0] // 20.0) * 20, []).append(g)
            print("  20px 桶(墨迹窗): depth | col范围 | 云max(桶内) | 桶内最坏poke(d)")
            for b in sorted(bkt):
                gs = bkt[b]
                cols = [g[1] for g in gs]
                cmax = max(g[2] for g in gs)
                wg = max(gs, key=lambda g: g[1] - g[2])
                print("     %4d-%4d  col[%.3f,%.3f]  云max=%7.3f  最坏poke=%+7.3f @ d=%.0f"
                      % (b, b + 20, min(cols), max(cols), cmax,
                         wg[1] - wg[2], wg[0]))
            ft = outlet - w._falling_front()
            mt = outlet - w.get_mound_top_y()
            print("  前沿 depth=%.2f  堆面 depth=%.2f  dmax=%.2f" % (ft, mt, dmax))
            print("  头部 60px 明细(10px 子桶): depth | n | max|x| | n(|x|>15)")
            b = int(max(0.0, dmax - 60.0) // 10) * 10
            while b <= int(dmax) + 1:
                sub = [i for i in range(n) if b <= ds[i] < b + 10]
                if sub:
                    print("     %4d-%4d  n=%3d  max|x|=%6.2f  外圈(>15)=%d"
                          % (b, b + 10, len(sub), max(xos[i] for i in sub),
                             sum(1 for i in sub if xos[i] > 15.0)))
                b += 10
            self.stop()

    Probe().run()
