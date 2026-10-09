# -*- coding: utf-8 -*-
"""上球沙面漏斗: 改前/改后取帧(**上球**裁图, 给用户判)。

按 `HG_FUNNEL_SHAPE` / `HG_FUNNEL_DEPTH` / `HG_FUNNEL_MAXH` 出图。
    HG_FUNNEL_SHAPE=parabola HG_FUNNEL_DEPTH=0.018 HG_FUNNEL_MAXH=0.25 \
        python tools/_probe_funnel_ab.py before 12.5 25 37.5
    python tools/_probe_funnel_ab.py after 12.5 25 37.5
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
TAG = sys.argv[1] if len(sys.argv) > 1 else "funnel"
TIMES = [float(a) for a in sys.argv[2:]] or [12.5, 25.0, 37.5]

with tempfile.TemporaryDirectory(prefix="funnel-ab-") as home:
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
            out = ROOT / "benchmark_logs" / "_vid" / "funnel"
            out.mkdir(parents=True, exist_ok=True)
            print("")
            print("  TAG=%s shape=%s depth=%s maxh=%s tan=%.2f"
                  % (TAG, m.UPPER_FUNNEL_SHAPE, m.UPPER_FUNNEL_DEPTH,
                     m.UPPER_FUNNEL_MAXH, m.UPPER_FUNNEL_REPOSE_TAN))
            print("")
            print("     t    沙层厚   陷深d   半宽b   d/厚   可见坡度")
            dt = 1.0 / 60.0
            for t in TIMES:
                while w.elapsed < t - 1e-9:
                    now[0] += dt
                    w.tick(dt)
                w.running = False
                w.redraw()
                p = 1.0 - w._upper_sand_fraction()
                h = w._upper_sand_height_px()
                d, b = w._upper_funnel_params(p, h)
                slope = (m.UPPER_FUNNEL_REPOSE_TAN * 180.0 / 3.14159265
                         if m.UPPER_FUNNEL_SHAPE == "cone" else float("nan"))
                path = out / ("%s_%05.1f.png" % (TAG, t))
                w.export_to_png(str(path))
                print("   t=%5.1f %7.1f %7.2f %7.1f  %5.2f   %s"
                      % (t, h, d, b, d / max(1e-6, h),
                         ("%.1f°(直壁)" % slope) if slope == slope else "抛物线"))
                w.running = True
            print("  上球沙面: _upper_sand_bot=%.1f  _upper_sand_top=%.1f  cx=%.1f"
                  % (w._upper_sand_bot, w._upper_sand_top, w._cx))
            print("  图 ->", out.relative_to(ROOT))
            self.stop()

    P().run()
