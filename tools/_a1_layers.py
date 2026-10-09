# -*- coding: utf-8 -*-
"""吐出冻结帧里各绘制族的**真实 y 范围**(从 _QuadBand._v 读, 不猜)。"""
import os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"
with tempfile.TemporaryDirectory(prefix="a1l-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": 600.0}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])
    import random as _rnd; _rnd.seed(20261009)
    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)
        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(600.0); w.reset(); w._rebuild_height_table()
            w.toggle()
            for _ in range(120):
                now[0] += 1.0/60.0; w.tick(1.0/60.0)
            w.running = False; w.redraw()
            outlet = 2*w._neck_y - w._taper["y_bot"]
            def band(name, qb, only_center=True):
                v = qb._v; ys = []; xs = []
                for k in range(0, len(v), 4):
                    x, y = v[k], v[k+1]
                    if x == 0.0 and y == 0.0: continue
                    ys.append(y); xs.append(abs(x - w._cx))
                if not ys: print("  %-14s 空" % name); return
                print("  %-14s n=%3d  y=[%.1f..%.1f] (depth %+.1f..%+.1f) 半宽[%.1f..%.1f]"
                      % (name, len(ys), min(ys), max(ys), outlet-max(ys), outlet-min(ys),
                         min(xs), max(xs)))
            print("")
            print("  出口 y=%.1f  t_in=%.2f  _upper_sand_bot=%.1f _upper_sand_top=%.1f"
                  % (outlet, w._taper["t_in"], w._upper_sand_bot, w._upper_sand_top))
            print("  _lower_sand_top=%.1f _lower_ball_cut=%.1f" % (w._lower_sand_top, w._lower_ball_cut))
            uh = w._upper_sand_height_px()
            p = 1.0 - w._upper_sand_fraction()
            d, b = w._upper_funnel_params(p, uh)
            lvl = w._upper_sand_bot + w._upper_level_for(uh)
            print("  上沙 height=%.1f  funnel d=%.2f b=%.2f  level=%.1f  drop(0)=%.1f"
                  % (uh, d, b, lvl, w._upper_surface_drop(0.0, d, b)))
            for nm, qb in (("_upper_carve", w._upper_carve), ("_upper_band", w._upper_band),
                           ("_neck_quads", w._neck_quads), ("_mound_carve", getattr(w, "_mound_carve", None)),
                           ("_mound_band", getattr(w, "_mound_band", None)),
                           ("_upper_markers", getattr(w, "_surface_marker_group", None))):
                if qb is None: continue
                if hasattr(qb, "_v"): band(nm, qb)
            print("  _neck_solid_rect pos=%s size=%s alpha=%.2f"
                  % (tuple(w._neck_solid_rect.pos), tuple(w._neck_solid_rect.size), w._neck_solid_color.a))
            print("  _neck_fade_rect  pos=%s size=%s alpha=%.2f"
                  % (tuple(w._neck_fade_rect.pos), tuple(w._neck_fade_rect.size), w._neck_fade_color.a))
            print("  _upper_band_color.a=%.3f" % w._upper_band_color.a)
            self.stop()
    Probe().run()
