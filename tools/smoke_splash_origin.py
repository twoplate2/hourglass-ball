"""Short PC smoke for contact origin and width-dependent splash density."""

import os
import logging
import math
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN_MODE = os.environ.get("HG_SMOKE_ORIGIN", "smooth")
assert ORIGIN_MODE in ("flat", "linear", "smooth")
RANGE_RATIO = float(os.environ.get("HG_SMOKE_RANGE", str(1.0 / 3.0)))
assert 0.0 < RANGE_RATIO <= 1.0

with tempfile.TemporaryDirectory(prefix="splash-origin-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    logging.getLogger("PIL").setLevel(logging.WARNING)
    m.HourglassWidget.load_config = lambda *_: {"duration": 60000}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    class Smoke(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (480, 900)
            Clock.schedule_once(self.check, 0.5)

        def check(self, _dt):
            self.root.apply_orientation()
            self.root.do_layout()
            self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            w = self.hourglass
            w._rebuild_height_table()
            assert w._geom_ready
            born = []
            results = {}
            append = w._s_append
            def checked_append(x, y, vx, vy, hw, hh, gd):
                height = w._mound_profile.column(x - w._cx, w._mound_apex())[0]
                surface = w._lower_sand_bot + height
                roof = w._lower_sand_bot + w._mound_profile.bounds(x - w._cx)[1]
                expected = min(max(0.0, hh + (m.SPLASH_LIFT_PX - hh) * w._splash_origin_blend),
                               max(0.0, roof - surface - hh))
                assert abs(y - surface - expected) <= 1e-9
                if w._splash_origin_blend == 0.0:
                    assert y - hh <= surface + 1e-9, "thin-stream emission floats above its tip"
                assert math.atan2(vy, abs(vx)) <= math.radians(60) + 1e-12
                born.append(y - hh - surface)
                return append(x, y, vx, vy, hw, hh, gd)
            w._s_append = checked_append
            for period in (5, 30, 60000):
                w.set_duration(period)
                w.reset()
                blend = w._splash_origin_blend
                assert abs(w._splash_speed_scale ** 2 - (1.0 + 2.0 * blend) / 3.0) < 1e-12
                if abs(RANGE_RATIO - 1.0 / 3.0) > 1e-12:
                    w._splash_speed_scale = math.sqrt(RANGE_RATIO + (1.0 - RANGE_RATIO) * blend)
                if ORIGIN_MODE != "smooth":
                    narrow, wide = w._neck_width_limits()
                    narrow_inner, wide_inner = max(1.0, narrow - w._ow), max(1.0, wide - w._ow)
                    fraction = (w._taper["t_in"] - narrow_inner) / max(1e-9, wide_inner - narrow_inner)
                    w._splash_origin_blend = 0.0 if ORIGIN_MODE == "flat" else fraction
                random.seed(23)
                born.clear()
                w.toggle()
                for _ in range(240):
                    now[0] += 1 / 60
                    w.tick(1 / 60)
                assert born and w._sn > 0
                results[period] = dict(births=len(born), density=w._splash_density,
                                       inner_width=2 * w._taper["t_in"], cap=w._splash_cap,
                                       origin_blend=w._splash_origin_blend,
                                       speed_scale=w._splash_speed_scale,
                                       max_gap=max(born), live=w._sn)
                out = ROOT / "benchmark_logs" / ("splash_origin_%s_range_%03d_%s_2_10.png" %
                                                  (ORIGIN_MODE, round(1000 * RANGE_RATIO), period))
                out.parent.mkdir(exist_ok=True)
                w.export_to_png(str(out))
            assert results[5]["density"] == 1.5
            assert results[5]["origin_blend"] == (0.0 if ORIGIN_MODE == "flat" else 1.0)
            assert results[60000]["origin_blend"] == 0.0
            assert results[5]["speed_scale"] == 1.0
            assert abs(results[60000]["speed_scale"] ** 2 - RANGE_RATIO) < 1e-12
            assert (results[30]["origin_blend"] == 0.0 if ORIGIN_MODE == "flat"
                    else 0.0 < results[30]["origin_blend"] < 1.0)
            assert 0.15 <= results[60000]["density"] < results[30]["density"] < 1.5
            for period, row in results.items():
                expected = max(0.15, 1.5 * row["inner_width"] / results[5]["inner_width"])
                assert abs(row["density"] - expected) < 1e-12
                assert row["live"] <= row["cap"]
            assert results[60000]["births"] < results[5]["births"] * 0.20
            print("PASS origin + density smoke (%s, range %.3f):" % (ORIGIN_MODE, RANGE_RATIO), results)
            self.stop()

    Smoke().run()
