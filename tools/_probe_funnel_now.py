# -*- coding: utf-8 -*-
"""量上球沙面漏斗: d(深度) / b(半宽) / 半弦宽 随进度的实际值。"""
import math, os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"
with tempfile.TemporaryDirectory(prefix="fun-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    class P(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(50.0); w.reset(); w._rebuild_height_table(); w.toggle()
            print("")
            print("  UPPER_FUNNEL_DEPTH=%.4f WIDTH=%.4f MAXH=%.3f MAXB=%.3f  R_inner=%.1f"
                  % (m.UPPER_FUNNEL_DEPTH, m.UPPER_FUNNEL_WIDTH,
                     m.UPPER_FUNNEL_MAXH, m.UPPER_FUNNEL_MAXB, w._R_inner))
            print("")
            print("    进度     沙层厚    d(陷深)   b(半宽)  半弦宽   d/厚   b/半弦")
            dt = 1.0 / 60.0
            for t in (1.0, 5.0, 12.5, 25.0, 37.5, 45.0, 48.0, 49.5):
                while w.elapsed < t - 1e-9:
                    now[0] += dt
                    w.tick(dt)
                p = 1.0 - w._upper_sand_fraction()
                h = w._upper_sand_height_px()
                d, b = w._upper_funnel_params(p, h)
                half_chord = math.sqrt(max(0.0, w._R_inner ** 2 - (w._R_inner - h) ** 2))
                print("   t=%5.1f   %7.1f   %7.2f   %7.1f   %7.1f  %5.2f  %5.2f"
                      % (t, h, d, b, half_chord, d / max(1e-6, h), b / max(1e-6, half_chord)))
            self.stop()
    P().run()
