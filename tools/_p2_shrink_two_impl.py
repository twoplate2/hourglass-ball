# -*- coding: utf-8 -*-
"""【2号】"柱粒同一条式子"的两个**实现**到底差多少 ---- 纯数值 + 实画对照。

背景: `_free_width_ratio`(沙柱)与 `update_particles`(粒子)被文档称作"同一条式子"。
本探针把两者的**深度原点**与**钳位方式**分开量:
  - 沙柱的 depth 从 **直筒下端(outlet)** 起算
  - 粒子的 below_tube 从 `_lower_ball_cut` 起算 => shift = outlet − lower_ball_cut
并打印: 函数对照表 / 自由段节点表 / 实画的四边形半宽 / 粒子云的横向包络。

跑法: python tools/_p2_shrink_two_impl.py [窗口宽 窗口高 density] [周期] [进度]
  默认 400 800 1.0 50 0.04   (平板: 1904 2890 2.75 50 0.02)
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

WW = int(sys.argv[1]) if len(sys.argv) > 1 else 400
HH = int(sys.argv[2]) if len(sys.argv) > 2 else 800
DENS = sys.argv[3] if len(sys.argv) > 3 else "1.0"
PERIOD = float(sys.argv[4]) if len(sys.argv) > 4 else 50.0
PROGRESS = float(sys.argv[5]) if len(sys.argv) > 5 else 0.04
os.environ.setdefault("KIVY_METRICS_DENSITY", DENS)
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="p2shr-") as home:
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
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD); w.reset(); w._rebuild_height_table()
            tp = w._taper
            t_in = tp["t_in"]
            outlet = 2 * w._neck_y - tp["y_bot"]
            inlet = tp["y_bot"]
            cut = w._lower_ball_cut
            shift = outlet - cut
            print("")
            print("  == %dx%d d=%.2f  周期 %.0fs ==" % (WW, HH, float(DENS), PERIOD))
            print("  窗口 %.0fx%.0f  widget %.0fx%.0f  R=%.1f Ri=%.1f ball_h=%.1f tube_h=%.1f"
                  % (Window.size[0], Window.size[1], w.width, w.height,
                     w._R, w._R_inner, w._ball_h, w._tube_h))
            print("  neck_w=%.2f  t_in=%.2f (全宽 %.2f)  半宽/画布宽=%.2f%%"
                  % (w.neck_w, t_in, 2 * t_in, 100 * 2 * t_in / w.width))
            print("  inlet(直筒上端)=%.1f  outlet(直筒下端)=%.1f  直筒高=%.1f"
                  % (inlet, outlet, inlet - outlet))
            print("  _lower_ball_cut=%.1f  (neck_y=%.1f, tube_h/2=%.1f, y_bot-neck_y=%.1f)"
                  % (cut, w._neck_y, w._tube_h / 2.0, tp["y_bot"] - w._neck_y))
            print("  => **粒子式子的零点比沙柱的零点低 shift=%.1f px**(outlet 以下)" % shift)
            print("  球内顶=%.1f(离 outlet %.1f)  球内底=%.1f(离 outlet %.1f)"
                  % (w._lower_sand_top, outlet - w._lower_sand_top,
                     w._lower_sand_bot, outlet - w._lower_sand_bot))
            span_max = outlet - w._lower_sand_bot

            # ---- 1) 函数对照: 同一物理深度 db(离 outlet) ----
            ms = w._particle_motion_scale
            v0 = 60.0 * ms
            g_abs = 450.0 * ms * ms
            smin = m.FLOW_SHRINK_MIN

            def particle_ratio(db):
                below = db - shift
                if below <= 0.0:
                    return 1.0
                v_at = (v0 * v0 + 2.0 * g_abs * below) ** 0.5
                target = (v0 / v_at) ** 0.5
                if target <= smin:
                    target = smin
                if below < 40.0:
                    return 1.0 + (target - 1.0) * (below / 40.0)
                return target

            print("")
            print("  -- 同一物理深度 db(离 outlet)下两条式子 --")
            print("  %8s %10s %12s %10s" % ("db(px)", "沙柱ratio", "粒子ratio", "差"))
            worst = (0.0, None)
            for db in (0.0, 5.0, 10.0, 12.0, 12.66, 15.0, 20.0, 25.0, 30.0,
                       40.0, 50.0, 60.0, 80.0, 100.0, 150.0, 200.0):
                if db > span_max + 1.0:
                    continue
                a = w._free_width_ratio(db)
                b = particle_ratio(db)
                if abs(a - b) > worst[0]:
                    worst = (abs(a - b), db)
                print("  %8.1f %10.4f %12.4f %10.4f" % (db, a, b, a - b))
            print("  => 最大差 %.4f x t_in = **%.2f px**(半宽, 在 db=%.1f 处)"
                  % (worst[0], worst[0] * t_in, worst[1] if worst[1] is not None else -1))

            # ---- 2) 自由段节点表(空堆最大落程) ----
            print("")
            print("  -- 自由段节点(空堆, span=%.1f): 节点 d 与 ratio --" % span_max)
            nodes = []
            for k in range(1, m.NECK_TAPER_SEGS + 1):
                d = 40.0 * k / float(m.NECK_TAPER_SEGS)
                if d < span_max - 1.0:
                    nodes.append((d, w._free_width_ratio(d)))
            nodes.append((span_max, w._free_width_ratio(span_max)))
            print("  " + "  ".join("d=%.0f:%.4f" % (d, r) for d, r in nodes))
            half = [0.0] + [d for d, _ in nodes]
            rn = [1.0] + [r for _, r in nodes]
            cov = [0.0]
            for i in range(1, len(half)):
                cov.append(half[i - 1] + (1.0 - rn[i - 1]) / max(1e-9, 1.0 - rn[-1])
                           * (half[i] - half[i - 1]))
            print("  (节点数 %d; 前 40px 只占落程的 %.1f%%)"
                  % (len(nodes), 100.0 * 40.0 / span_max))

            # ---- 3) 跑到进度, 冻结, 量**实画**的柱与云 ----
            w.toggle()
            for _ in range(max(30, int(PERIOD * PROGRESS * 60))):
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False
            w.redraw()
            cx_local = w._cx - w.x
            front = w._falling_front()
            span = outlet - front
            print("")
            print("  -- 冻结态: elapsed=%.2f 前沿=%.1f (离 outlet %.1f) 沙堆面=%.1f pn=%d --"
                  % (w.elapsed, front, span, w.get_mound_top_y(), w.pn))

            qv = w._neck_quads._v
            rows = {}
            for k in range(0, len(qv), 4):
                x, y = qv[k], qv[k + 1]
                if x == 0.0 and y == 0.0:
                    continue
                if y > outlet + 0.5:
                    continue
                db = outlet - y
                hw = abs(x - cx_local)
                rows.setdefault(round(db, 1), set()).add(round(hw, 2))
            print("  -- 实画四边形下缘(outlet 以下; 半宽从 4 个角里取最大) --")
            for db in sorted(rows):
                print("    db=%7.1f  半宽=%s" % (db, sorted(rows[db])))

            # 粒子云包络
            px, py = w.px[:w.pn], w.py[:w.pn]
            buckets = {}
            for i in range(w.pn):
                db = outlet - py[i]
                if db < 0.0:
                    continue
                b = int(db // 20.0) * 20
                buckets.setdefault(b, []).append(abs(px[i] - w._cx))
            print("  -- 粒子云横向包络(20px 桶; 列: 颗数 / 中位 / p90 / max 半宽) --")
            for b in sorted(buckets):
                v = sorted(buckets[b])
                n = len(v)
                print("    db=%4d-%4d  n=%4d  med=%6.2f p90=%6.2f max=%6.2f   (柱 t_in=%.2f)"
                      % (b, b + 20, n, v[n // 2], v[min(n - 1, int(0.9 * n))], v[-1], t_in))
            self.stop()

    Probe().run()
