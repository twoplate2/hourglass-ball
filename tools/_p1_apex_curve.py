# -*- coding: utf-8 -*-
"""【1号】末段"虚顶 vs 天花板 vs 出口"随 t 的数值轨迹 —— 为"缝能不能靠堆自己长合"取证。

不画图, 只打印三个绝对高度(Kivy y 向上):
  raw0   = apex + shape_at(0)        未夹的中轴堆面
  H0     = contact(0, apex)          夹过的接触面(画出来的)
  ball   = 球内顶(_lower_sand_top)   现天花板
  outlet = 直筒下端                  颈部沙柱画到哪
另打 `_mound_height_px()` 与 `_effective_fallen()`。

跑法: python tools/_p1_apex_curve.py [周期] [起] [止] [步长]
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
T0 = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0
T1 = float(sys.argv[3]) if len(sys.argv) > 3 else PERIOD
STEP = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5

with tempfile.TemporaryDirectory(prefix="p1apex-") as home:
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
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD); w.reset(); w._rebuild_height_table(); w.toggle()
            dt = 1.0 / 60.0
            outlet = 2 * w._neck_y - w._taper["y_bot"]
            prof = w._mound_profile
            shape0 = prof.shape[len(prof.shape) // 2]
            print("")
            print("  R_inner=%.1f  球内顶=%.1f  outlet=%.1f  shape[中心]=%.2f"
                  % (w._R_inner, w._lower_sand_top, outlet, shape0))
            print("  %7s %10s %10s %10s %10s %9s %9s"
                  % ("elapsed", "h_mound", "raw0", "H0", "ball", "raw0-out", "H0-out"))
            nxt = T0
            tmax = int(T1 / dt)
            for k in range(tmax):
                now[0] += dt
                w.tick(dt)
                if w.elapsed >= nxt - 1e-9:
                    nxt += STEP
                    apex = w._mound_apex()
                    raw0 = w._lower_sand_bot + apex + shape0
                    H0 = w.get_mound_top_y()
                    print("  %7.2f %10.2f %10.1f %10.1f %10.1f %9.1f %9.1f"
                          % (w.elapsed, w._mound_height_px(), raw0, H0,
                             w._lower_sand_top, raw0 - outlet, H0 - outlet))
            self.stop()

    Probe().run()
