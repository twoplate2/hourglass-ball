# -*- coding: utf-8 -*-
"""**飞溅的射程到底是不是固定的** —— 挂 `_s_append`, 在出生那一刻算出该颗粒的弹道射程。

射程(落回同一高度) = 2·vx·vy / g,  g = 450·motion_scale²·gd(每颗自己的重力倍率)。
这样不需要逐帧跟踪(粒子会被压缩/回收, 按下标跟踪不可靠), 而且**用的是出生时的真实量**。

同时报: 仰角分布(相对水平)、竖直速度被"天花板"截断的比例、以及 2.10 的 speed_scale 影响。

跑法: python tools/_probe_splash_range.py [tag]
"""
import math
import os
import random
import statistics as st
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TAG = sys.argv[1] if len(sys.argv) > 1 else "cur"
PERIODS = [float(v) for v in (sys.argv[2].split(",") if len(sys.argv) > 2
                                    else ["5", "30", "60000"])]
FRAMES = int(sys.argv[3]) if len(sys.argv) > 3 else 300

with tempfile.TemporaryDirectory(prefix="splash-range-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": 60000}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (480, 900)
            Clock.schedule_once(self.go, 0.5)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w._rebuild_height_table()
            rows = []
            print("")
            print("  tag=%s  窗口 480x900  R=%.0f  R_inner=%.0f  单颗尺寸=%s px"
                  % (TAG, w._R, w._R_inner,
                     tuple(getattr(w, "shh", [0])[0] if False else (0, 0))))
            for period in PERIODS:
                w.set_duration(period)
                w.reset()
                random.seed(23)
                rand = []
                angles = []
                capped = 0
                scale = w._particle_motion_scale
                base_g = 450.0 * (scale * scale if m.SPLASH_G_SCALE >= 1.0
                                  else scale ** m.SPLASH_G_SCALE)

                def hook(x, y, vx, vy, hw, hh, gd, _w=w, _g=base_g):
                    nonlocal capped
                    g = _g * gd
                    rand.append(2.0 * abs(vx) * vy / g)
                    angles.append(math.degrees(math.atan2(vy, abs(vx))))
                    # 竖直速度是否被天花板截断过(用 clearance 反推)
                    # ⚠️ 必须与 `_eject_splash` 用**同一个 clearance**(它扣掉了半高 hh):
                    #    clearance = roof - y_surface - lift - hh, 而 y = y_surface + lift
                    #    ⇒ 判据是 vy 恰好等于 sqrt(2 g clearance)(被 min() 钳住才会相等)
                    roof = _w._lower_sand_bot + _w._mound_profile.bounds(x - _w._cx)[1]
                    cap_v = math.sqrt(2.0 * g * max(0.0, roof - y - hh))
                    if vy >= cap_v - 1e-9:
                        capped += 1
                    return append(x, y, vx, vy, hw, hh, gd)

                append = w._s_append
                w._s_append = hook
                w.toggle()
                for _ in range(FRAMES):
                    now[0] += 1.0 / 60.0
                    w.tick(1.0 / 60.0)
                w._s_append = append
                if not rand:
                    print("  %8g  没有飞溅" % period)
                    continue
                rand.sort(); angles.sort()
                q = lambda a, p: a[min(len(a) - 1, int(len(a) * p))]
                rows.append((period, len(rand), q(rand, .5), q(rand, .9), rand[-1],
                             q(angles, .1), q(angles, .9), 100.0 * capped / len(rand)))
                print("  %8s  样本 %5d | 弹道射程 p50 %6.1f  p90 %6.1f  max %6.1f px"
                      "  (max/p50 = %.1f×)"
                      % ("%gs" % period, len(rand), q(rand, .5), q(rand, .9), rand[-1],
                         rand[-1] / max(1e-9, q(rand, .5))))
                print("           仰角(相对水平) p10 %4.1f°  p90 %4.1f°   | 竖直被天花板截断 %4.1f%%"
                      " | speed_scale=%.3f" % (q(angles, .1), q(angles, .9),
                                               100.0 * capped / len(rand),
                                                getattr(w, "_splash_speed_scale", 1.0)))
            print("")
            print("  参考: 下球内半径 R_inner=%.0f px  ⇒ 射程/内半径 的比值" % w._R_inner)
            for period, n, p50, p90, mx, a10, a90, cap in rows:
                print("     %8s  p50 %.3f  p90 %.3f  max %.3f" % ("%gs" % period,
                      p50 / w._R_inner, p90 / w._R_inner, mx / w._R_inner))
            self.stop()

    Probe().run()
