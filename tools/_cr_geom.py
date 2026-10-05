"""Cross-review (my own): dump geometry + analytic mound/lip curves for period 5 at 400x800."""
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="crgeom-") as home:
    os.environ["KIVY_HOME"] = home
    os.environ["KIVY_NO_ARGS"] = "1"
    os.environ["KIVY_NO_FILELOG"] = "1"
    os.environ["KIVY_METRICS_DENSITY"] = "1"
    os.environ["KIVY_METRICS_FONTSCALE"] = "1"
    sys.path.insert(0, str(ROOT))
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    import main as module

    module.HourglassWidget._make_sound_proxy = lambda *_: None
    module.HourglassWidget._make_completion_sound = lambda *_: None
    module.HourglassApp.on_completed = lambda *_: None
    module.HourglassWidget.load_config = lambda *_: {"duration": 60}
    module.HourglassWidget.save_config = lambda *_: None
    now = [1000.0]
    module.time = SimpleNamespace(perf_counter=lambda: now[0])
    out = {}

    class A(module.HourglassApp):
        def on_start(self):
            Clock.schedule_once(self.resize, 1)

        def resize(self, _dt):
            Window.system_size = (400, 800)
            Clock.schedule_once(self.begin, 0.3)

        def begin(self, _dt):
            r = self.root
            r.apply_orientation()
            r.do_layout()
            r._anchor.do_layout()
            self.hourglass.parent.do_layout()
            Clock.unschedule(self.hourglass.tick)
            w = self.hourglass
            w.set_duration(5.0)
            g = dict(
                window=list(Window.size), widget_size=list(w.size), widget_pos=list(w.pos),
                R=w._R, Ri=w._R_inner, ow=w._ow, neck_w=w.neck_w,
                t_out=w._taper["t_out"], t_in=w._taper["t_in"], y_bot=w._taper["y_bot"],
                neck_y=w._neck_y, outlet=2 * w._neck_y - w._taper["y_bot"],
                lower_y_c=w._lower_y_c, lower_sand_bot=w._lower_sand_bot,
                upper_y_c=w._upper_y_c, upper_sand_bot=w._upper_sand_bot,
                glass_bot=w._glass_bot, glass_top=w._glass_top,
                neck_fill=w._neck_fill_time, flight=w._natural_flight_time,
                fall_delay=w._fall_delay, motion_scale=w._particle_motion_scale,
                speed_factor=w.speed_factor, mound_appear=module.MOUND_APPEAR,
                lip_frac=module.SURFACE_LIP_FRAC, lip_start=module.SURFACE_LIP_START,
                feather_px=module.FLOW_FEATHER_PX, feather_min=module.FLOW_FEATHER_MIN,
            )
            lift = 2 * w._R_inner * module.SURFACE_LIP_FRAC
            g["lift_px"] = lift
            # analytic mound curve (no sim needed): uses only elapsed-driven helpers
            curve = []
            w.elapsed = 0.0
            w.running = True
            for i in range(0, 121):
                t = 1.10 + 1.20 * i / 120.0     # 1.10 .. 2.30
                w.elapsed = t
                h = w._mound_height_px()
                base = w.get_mound_top_y()
                chord = math.sqrt(max(0.0, w._R_inner ** 2 - (base - w._lower_y_c) ** 2))
                curve.append(dict(
                    t=round(t, 4), h=round(h, 3), eff=round(w._effective_fallen(), 5),
                    chord_half=round(chord, 2),
                    lip_start_px=round(module.SURFACE_LIP_START * chord, 2),
                    lip_full_px=round(0.5 * chord, 2),
                ))
            g["curve"] = curve
            out.update(g)
            Path(ROOT / "benchmark_logs" / "_cr_geom.json").write_text(
                json.dumps(out, indent=1))
            print(json.dumps({k: v for k, v in g.items() if k != "curve"}, indent=1))
            self.stop()

    A().run()
