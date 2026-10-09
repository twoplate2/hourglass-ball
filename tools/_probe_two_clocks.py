# -*- coding: utf-8 -*-
"""末段两钟对账: 「没有沙子流动和飞溅, 但沙子在长高」。

用 `_p2_shrink_two_impl.py` **同一套 bootstrap**(那套在桌面是跑得通的)。

打的是 `sand_transfer_state()` 的四栏 + 在途粒子数 pn:
    upper / released / flight / landed
判据(纯计算, 不读图):
  · **`released` 到 1.0 的时刻** = 放完 = 不再生成粒子 ⇒ "没有沙流、没有飞溅"
  · **`landed` 到 1.0 的时刻** = 最后一粒落地 ⇒ 沙堆停止长高
  · 两者之差 = **"没沙在流但堆还在长"的时长** ← 这就是用户在报的那个数
再按 `pn`(在途粒子数) 交叉验证: released=1 之后 pn 是否还 >0。

跑法: python tools/_probe_two_clocks.py [周期] [起始秒]
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
T0 = float(sys.argv[2]) if len(sys.argv) > 2 else max(0.0, PERIOD - 8.0)
WW, HH, DENS = 800, 1600, "2.0"
os.environ.setdefault("KIVY_METRICS_DENSITY", DENS)
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="twoclock-") as home:
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

    class Probe(m.HourglassApp):
        def on_start(self):
            print("DIAG: on_start fired")
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (WW, HH)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            print("DIAG: go fired")
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
            print("  周期 %.0fs  平板口径 %dx%d" % (PERIOD, WW, HH))
            print("     t   | upper  | released | flight | landed |  pn | 说明")
            dt = 1.0 / 60.0
            step = 0.25
            nxt = T0
            release_full = landed_full = None
            while w.elapsed < PERIOD - 1e-9:
                now[0] += dt
                w.tick(dt)
                if w.elapsed >= nxt - 1e-9:
                    st = w.sand_transfer_state()
                    st["released"] = 1.0 - st["upper"] - st["neck"]
                    pn = getattr(w, "pn", -1)
                    if release_full is None and st["released"] >= 0.9995:
                        release_full = w.elapsed
                    if landed_full is None and st["landed"] >= 0.9995:
                        landed_full = w.elapsed
                    note = ""
                    if release_full is not None and landed_full is None:
                        note = "<<< 已放完但堆还在长"
                    print("  %6.2f | %6.3f | %8.3f | %6.3f | %6.3f | %3d | %s"
                          % (w.elapsed, st["upper"], st["released"], st["flight"],
                             st["landed"], pn, note))
                    nxt += step
            print("")
            print("  ==> released 到 1.0 于 t=%s ;  landed 到 1.0 于 t=%s"
                  % ("%.2f" % release_full if release_full else "未达",
                     "%.2f" % landed_full if landed_full else "未达"))
            if release_full is not None and landed_full is not None:
                print("  ==> 没沙在流但堆还在长的时长 = %.2f 秒"
                      % (landed_full - release_full))
            self.stop()

    Probe().run()
