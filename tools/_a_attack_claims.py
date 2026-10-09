# -*- coding: utf-8 -*-
"""攻击用取证探针: A1(硬断口) / A2(节点数) / A4(云-柱包络) / A5(口径归一)。

只跑、只量, 不下结论。跑法:
  python tools/_a_attack_claims.py 1904 2890 2.75 600 2.0
  python tools/_a_attack_claims.py 400 800 1.0 50 5.0
参数: 窗口宽 窗口高 density 周期 目标秒数
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
os.environ.setdefault("KIVY_METRICS_DENSITY", DENS)
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="a-attack-") as home:
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
    _rnd.seed(20261009)

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

            tp = w._taper
            t_in = tp["t_in"]
            outlet = 2 * w._neck_y - tp["y_bot"]
            x_clip = max(1.0, w.neck_w - w._ow)
            ms = w._particle_motion_scale
            print("")
            print("  ========== %dx%d d=%s  周期 %.1fs  目标 t=%.2fs =========="
                  % (WW, HH, DENS, PERIOD, AT))
            print("  widget %.1fx%.1f  R=%.2f Ri=%.2f 球径=%.2f 球高=%.2f 直筒高=%.2f"
                  % (w.width, w.height, w._R, w._R_inner, 2 * w._R, 2 * w._R_inner,
                     tp["y_bot"] - (2 * w._neck_y - tp["y_bot"])))
            print("  neck_w=%.2f  ow=%.1f  t_in=%.2f(全宽 %.2f)  x_clip=%.2f"
                  % (w.neck_w, w._ow, t_in, 2 * t_in, x_clip))
            print("  outlet=%.1f  ball_cut=%.1f  sand_top=%.1f  sand_bot=%.1f"
                  % (outlet, w._lower_ball_cut, w._lower_sand_top, w._lower_sand_bot))
            print("  shift=outlet-ball_cut=%.2f   outlet->ball_bot=%.1f"
                  % (outlet - w._lower_ball_cut, outlet - w._lower_sand_bot))
            print("  ms=%.4f  b_sat(柱)=%.4f  FLOW_SHRINK_MIN=%.3f"
                  % (ms, (60.0 * ms) ** 2 * (m.FLOW_SHRINK_MIN ** -4 - 1) / (2 * 450 * ms * ms),
                     m.FLOW_SHRINK_MIN))

            # ---------- A1: 两条式子的细采样 ----------
            def col_fn(d):
                return w._free_width_ratio(d)

            def par_fn(db):
                """逐字照抄 update_particles 标量分支(below_tube 口径)。"""
                y = outlet - db
                if y > w._lower_ball_cut:
                    return 1.0
                below = w._lower_ball_cut - y
                v0 = 60.0 * ms
                g_abs = 450.0 * ms * ms
                b_sat = v0 * v0 * (m.FLOW_SHRINK_MIN ** -4.0 - 1.0) / (2.0 * g_abs)
                if below >= b_sat + 1.0:
                    target = m.FLOW_SHRINK_MIN
                else:
                    v_at = math.sqrt(v0 * v0 + 2.0 * g_abs * below)
                    target = math.sqrt(v0 / v_at)
                    if target <= m.FLOW_SHRINK_MIN:
                        target = m.FLOW_SHRINK_MIN
                if below < 40.0:
                    return 1.0 + (target - 1.0) * (below / 40.0)
                return target

            for name, fn in (("沙柱 _free_width_ratio", col_fn),
                             ("粒子 update_particles 式", par_fn)):
                jump = (0.0, 0.0, 0.0, 0.0)
                d = 0.0
                prev = fn(0.0)
                while d < 60.0:
                    d2 = d + 0.001
                    cur = fn(d2)
                    if abs(cur - prev) > jump[0]:
                        jump = (abs(cur - prev), d, prev, cur)
                    prev = cur
                    d = d2
                print("  [A1] %-26s 最大相邻差 |Δ|=%.5f  @ d=%.3f  (%.5f -> %.5f)"
                      % (name, jump[0], jump[1], jump[2], jump[3]))
                print("       %s  @12.63/12.65/12.66/12.67/20 = %.5f %.5f %.5f %.5f %.5f"
                      % (" " * 0, fn(12.63), fn(12.65), fn(12.66), fn(12.67), fn(20.0)))

            # ---------- 跑到目标秒, 冻结 ----------
            w.toggle()
            steps = max(1, int(round(AT * 60)))
            for _ in range(steps):
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False
            w.redraw()
            print("")
            print("  冻结: elapsed=%.3f pn=%d  前沿=%.2f(离outlet %.2f) 堆面=%.2f"
                  % (w.elapsed, w.pn, w._falling_front(), outlet - w._falling_front(),
                     w.get_mound_top_y()))

            # ---------- A2: 自由段节点 ----------
            side = w._neck_sand_side()
            print("")
            print("  [A2] _neck_sand_side(): 共 %d 个节点" % len(side))
            for i, (x, y) in enumerate(side):
                mark = "  <= 出口以下(自由段)" if y <= outlet + 1e-6 else ""
                print("       #%2d  x=%8.3f (ratio=%.4f)  y=%9.2f  depth=%8.3f%s"
                      % (i, x, x / t_in if t_in else 0.0, y, outlet - y, mark))
            free = [(outlet - y, x) for x, y in side if y <= outlet + 1e-6]
            deep = [(d, x) for d, x in free if d > 40.0]
            nd = [d for d, x in deep if x > 1e-9]
            print("       >40px 的节点: %d 个(其中半宽>0 的 %d 个)  深度=%s"
                  % (len(deep), len(nd), ["%.2f" % d for d, _ in deep]))
            if free:
                ds = sorted(d for d, _ in free)
                gaps = [(ds[i + 1] - ds[i], ds[i], ds[i + 1]) for i in range(len(ds) - 1)]
                g = max(gaps) if gaps else (0, 0, 0)
                print("       自由段最大节点间隔 = %.2f px (在 %.2f -> %.2f 之间)"
                      % g)

            # ---------- 实画四边形(从 _QuadBand 顶点读回) ----------
            qv = w._neck_quads._v
            rows = {}
            cx_local = w._cx - w.x
            for k in range(0, len(qv), 4):
                qx, qy = qv[k], qv[k + 1]
                if qx == 0.0 and qy == 0.0:
                    continue
                if qy > outlet + 0.5:
                    continue
                db = outlet - qy
                rows.setdefault(round(db, 2), set()).add(round(abs(qx - cx_local), 3))
            print("  [A1-drawn] 实画四边形顶点(出口以下, 深度 -> 半宽集合):")
            for db in sorted(rows):
                print("       depth=%8.2f  半宽=%s" % (db, sorted(rows[db])))

            # ---------- 解析式 vs 实画折线 ----------
            def drawn_ratio(db):
                pts = [(outlet - y, x / t_in) for x, y in side if y <= outlet + 1e-6]
                pts.sort()
                if not pts or db < pts[0][0]:
                    return 1.0
                for (d0, r0), (d1, r1) in zip(pts, pts[1:]):
                    if d0 <= db <= d1:
                        return r0 + (r1 - r0) * (db - d0) / max(1e-9, d1 - d0)
                return pts[-1][1]

            worst = (0.0, 0.0)
            d = 0.0
            while d <= 60.0:
                a = col_fn(d)
                b = drawn_ratio(d)
                if abs(a - b) > worst[0]:
                    worst = (abs(a - b), d)
                d += 0.05
            print("  [A1] 解析式 vs 实画折线: 最大差 %.4f (在 d=%.2f)  [代理口径 ratio]"
                  % worst)
            print("       折线在 12.65 / 12.66 处 = %.5f / %.5f (解析式 %.5f / %.5f)"
                  % (drawn_ratio(12.65), drawn_ratio(12.66), col_fn(12.65), col_fn(12.66)))

            # ---------- A4: 云缘 vs 柱半宽 ----------
            print("")
            print("  [A4] 20px 桶: 柱半宽(折线) vs 粒子云 |x-cx| (含线宽一半)")
            print("       %-14s %6s %9s %9s %9s %9s %9s"
                  % ("depth", "n", "col_half", "云min", "云med", "云p90", "云max"))
            bkt = {}
            for i in range(w.pn):
                db = outlet - w.py[i]
                if db < 0.0:
                    continue
                b = int(db // 20.0) * 20
                half = w.psz[i] * 0.5
                bkt.setdefault(b, []).append(abs(w.px[i] - w._cx) + half)
            for b in sorted(bkt):
                v = sorted(bkt[b])
                n = len(v)
                col = drawn_ratio(b + 10.0) * t_in
                print("       %4d-%4dpx  %5d %9.2f %9.2f %9.2f %9.2f %9.2f"
                      % (b, b + 20, n, col, v[0], v[n // 2], v[min(n - 1, int(0.9 * n))],
                         v[-1]))
                del v
            # 解析边界(用代码里两条式子, 不依赖抽样)
            print("       解析: 云最外 = x_clip*shrink + amp*(1-0.4*shrink), amp∈[0.4,1]")
            for db in (0.0, 5.0, 10.0, 12.65, 15.0, 20.0, 25.0, 30.0, 40.0, 60.0, 120.0):
                y = outlet - db
                below = w._lower_ball_cut - y
                sh = par_fn(db)
                col = drawn_ratio(db) * t_in
                lo = x_clip * sh + 0.4 * (1 - 0.4 * sh) + 0.5
                hi = x_clip * sh + 1.0 * (1 - 0.4 * sh) + 1.0
                print("       db=%6.2f  below=%7.2f  shrink=%.4f  柱半宽=%7.3f  "
                      "云最内颗<=%7.3f  云最外颗>=%7.3f  差(外-柱)=%+7.3f"
                      % (db, below, sh, col, lo, hi, hi - col))
            self.stop()

    Probe().run()
