# -*- coding: utf-8 -*-
"""**那条缝到底夹在谁和谁之间** —— 把候选几何线直接画进画面, 一眼定身份。

用户 2026-10-09:「50 秒档最后 3 秒, 沙流和下面沙堆的尖之间有明显空隙」。

本探针在 redraw() 之后往画布上补三条细线(只为这一次取证, 不进出货路径):
  * 红  = `get_mound_top_y()`    —— 沙堆在中轴能到的最高处
  * 蓝  = 直筒下端 outlet        —— 颈部沙柱画到哪
  * 绿  = `_lower_sand_top`      —— 下球内壁的顶(球内顶)
再导出 PNG。跑法: python tools/_probe_gap_mark.py 50 49.0
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
AT = float(sys.argv[2]) if len(sys.argv) > 2 else 49.0

with tempfile.TemporaryDirectory(prefix="gap-mark-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics import Color, Line
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
            print("")
            print("  %7s %10s %10s %10s %9s" % ("elapsed", "apex", "mound_top", "球内顶", "缝px"))
            last = None
            for _ in range(int(AT / dt)):
                now[0] += dt
                w.tick(dt)
                if abs(w.elapsed - round(w.elapsed)) < dt / 2:
                    mt = w.get_mound_top_y()
                    print("  %7.2f %10.1f %10.1f %10.1f %9.1f"
                          % (w.elapsed, w._mound_apex(), mt, w._lower_sand_top, mt - w._lower_sand_top))
            w.running = False
            w.redraw()
            mt = w.get_mound_top_y()
            outlet = 2 * w._neck_y - w._taper["y_bot"]
            x0, x1 = w._cx - w._R_inner, w._cx + w._R_inner
            with w.canvas:
                Color(1, 0, 0, 1);   Line(points=[x0, mt, x1, mt], width=1.4)
                Color(0, 0.3, 1, 1); Line(points=[x0, outlet, x1, outlet], width=1.4)
                Color(0, 0.8, 0, 1); Line(points=[x0, w._lower_sand_top, x1, w._lower_sand_top], width=1.4)
            out = ROOT / "benchmark_logs" / "_vid" / "gap" / ("mark_%.1f.png" % AT)
            out.parent.mkdir(parents=True, exist_ok=True)
            w.export_to_png(str(out))
            print("")
            print("  t=%.2f  R_inner=%.1f  _lower_sand_bot=%.1f  _lower_sand_top=%.1f"
                  % (w.elapsed, w._R_inner, w._lower_sand_bot, w._lower_sand_top))
            print("  get_mound_top_y=%.1f   outlet=%.1f   球内顶=%.1f" % (mt, outlet, w._lower_sand_top))
            print("  **缝(红↔蓝) = %.1f px**" % (outlet - mt))
            print("  图 -> %s" % out.relative_to(ROOT))
            self.stop()

    Probe().run()
