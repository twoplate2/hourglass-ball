"""Four focused PC checks for the v1.244-based splash adjustments."""

import json
import logging
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "contact_flow_2_9"


def run():
    with tempfile.TemporaryDirectory(prefix="contact-flow-") as home:
        os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
        sys.path.insert(0, str(ROOT))
        import main as m
        import numpy as np
        from kivy.clock import Clock
        from kivy.core.window import Window
        from PIL import Image
        logging.getLogger("PIL").setLevel(logging.WARNING)

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassWidget._play_completion_sound = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 50}
        m.HourglassWidget.save_config = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        now = [1000.0]
        m.time = SimpleNamespace(perf_counter=lambda: now[0])
        OUT.mkdir(parents=True, exist_ok=True)

        class Probe(m.HourglassApp):
            def on_start(self):
                Clock.unschedule(self.hourglass.tick)
                Window.system_size = (480, 900)
                Clock.schedule_once(self.check, 0.5)

            def advance(self, seconds):
                for _ in range(round(seconds * 60)):
                    now[0] += 1 / 60
                    self.hourglass.tick(1 / 60)
                    self.peak = max(self.peak, self.hourglass._sn)

            def shot(self, name):
                path = OUT / (name + ".png")
                self.hourglass.export_to_png(str(path))
                with Image.open(path) as image:
                    return image.convert("RGB").copy()

            def clip(self, name, frames=None, seconds=1.2):
                frames = [] if frames is None else frames
                prefix = len(frames)
                for k in range(round(seconds * 30)):
                    self.advance(1 / 30)
                    frames.append(self.shot(name + "_%02d" % k))
                durations = [100] * prefix + [30, 30, 40] * ((len(frames) - prefix + 2) // 3)
                frames[0].save(OUT / (name + ".gif"), save_all=True,
                               append_images=frames[1:], duration=durations[:len(frames)], loop=0)

            def check(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(50)
                w._rebuild_height_table()
                w.reset()
                self.peak = 0
                random.seed(23)
                w.toggle()
                self.advance(0.1)
                assert w._last_impact_clock is None and w._sn == 0
                before = []
                while w._last_impact_clock is None and w.elapsed < 3.0:
                    self.advance(0.1)
                    before.append(self.shot("before_hit"))
                assert w._last_impact_clock is not None
                assert w._splash_reference_speed is not None
                assert w._splash_stats["born_air"] >= 3, "first real contact produced no splash"
                self.clip("first_contact", before[-2:])
                self.advance(8.0 - w.elapsed)
                live = w._sn
                airborne = int(sum(w.shas[i] == 0 and
                                  w.sy[i] - w._mound_top_at(w.sx[i]) > 2.0 for i in range(live)))
                aloft = int(sum(w.sy[i] - w._mound_top_at(w.sx[i]) > 2.0 for i in range(live)))
                normal = self.shot("elapsed_8")
                w._sn = 0
                w.redraw()
                hidden = self.shot("no_splashes")
                w._sn = live
                w.redraw()
                difference = np.max(np.abs(np.asarray(normal).astype(int)
                                            - np.asarray(hidden).astype(int)), axis=2)
                visible = int(np.count_nonzero(difference >= 5))
                assert airborne > 700 and aloft > 700, "valid-direction splash population still too sparse"
                assert visible > 700, "splashes exist but are not visible in the rendered frame"
                pixels = np.asarray(normal).astype(int)
                sand = pixels[:, :, 0] - pixels[:, :, 2] > 45
                half = w._taper["t_in"] * m.FLOW_SHRINK_MIN * 0.7
                valid, total = 0, 0
                for px in range(round(w._cx - w.x - half), round(w._cx - w.x + half) + 1):
                    boundary = normal.height - (w._mound_top_at(px + w.x) - w.y)
                    for row in range(int(np.ceil(boundary - 2)), int(np.floor(boundary + 1)) + 1):
                        valid += bool(sand[row, px])
                        total += 1
                assert valid / total >= 0.95, "visible holes remain in the collision footprint"
                physical_y = w.py[:w.pn].copy()
                w._project_stream_contact()
                assert np.array_equal(physical_y, w.py[:w.pn]), "contact projection changed physics"
                cx, cy, *_ = w._mound_contact_curve()
                surface = np.interp(w.px[:w.pn], cx, cy)
                displayed = w._pv.ny
                assert np.min(displayed - surface) <= 0.01, "stream does not reach the contact surface"
                random_state = random.getstate()
                saved = w.splashes
                saved_stats = dict(w._splash_stats)
                effect_state = w._splash_random().getstate()
                w.splashes = []
                x = w._cx + 0.6 * w._contact_flow_diameter()
                velocities = []
                for impact in (400, 150):
                    w.splashes = []
                    w._splash_random().setstate(effect_state)
                    group = []
                    for _ in range(160):
                        i = w._eject_splash(x, w._mound_top_at(x), impact)
                        assert i >= 0
                        vx, vy = float(w.svx[i]), float(w.svy[i])
                        assert vx > 0 and vy > 0
                        assert np.arctan2(vy, abs(vx)) <= np.deg2rad(60) + 1e-12
                        group.append((vx, vy))
                    velocities.append(np.asarray(group))
                ratio = np.linalg.norm(velocities[1], axis=1) / np.linalg.norm(velocities[0], axis=1)
                assert np.min(ratio) >= 0.90, "late splash strength still collapses with impact speed"
                angles = np.arctan2(velocities[0][:, 1], velocities[0][:, 0])
                assert np.min(angles) >= np.deg2rad(0.01) - 1e-12
                assert np.ptp(angles) > np.deg2rad(20) and np.ptp(velocities[0][:, 0]) > 70
                saved_angles = m.SPLASH_ANGLE_MIN, m.SPLASH_ANGLE_MAX
                try:
                    for low, high in ((0.30, 1.15), (0.10, 0.30), (1.15, 0.30)):
                        m.SPLASH_ANGLE_MIN, m.SPLASH_ANGLE_MAX = low, high
                        w.splashes = []
                        for side in (-1.0, 1.0):
                            xx = w._cx + side * 0.6 * w._contact_flow_diameter()
                            for _ in range(80):
                                i = w._eject_splash(xx, w._mound_top_at(xx), 400)
                                assert i >= 0
                                assert side * w.svx[i] > 0
                                assert np.arctan2(w.svy[i], abs(w.svx[i])) <= np.deg2rad(60) + 1e-12
                finally:
                    m.SPLASH_ANGLE_MIN, m.SPLASH_ANGLE_MAX = saved_angles
                assert random.getstate() == random_state, "effect draws changed the main RNG"
                w.splashes = saved
                w._splash_stats = saved_stats
                w._splash_random().setstate(effect_state)
                w.toggle()
                state = tuple(tuple(getattr(w, field)[:w._sn]) for field in m._S_FIELDS)
                self.advance(0.2)
                assert state == tuple(tuple(getattr(w, field)[:w._sn]) for field in m._S_FIELDS)
                w.toggle()
                self.clip("steady")
                for fraction in (0.01, 0.1, 0.5, 0.9, 0.99, 1.0):
                    profile = w._mound_profile
                    apex = profile.apex_for_fraction(fraction)
                    assert abs(profile.heap.area_at(apex) / profile.heap.capacity - fraction) < 1e-9
                self.advance(45.5 - w.elapsed)
                self.clip("late")
                self.advance(48.8 - w.elapsed)
                self.clip("finish", seconds=1.8)
                self.advance(1.6)
                assert w.pn == 0 and w._sn == 0 and not w.running
                stats = dict(w._splash_stats)
                assert self.peak <= m.SPLASH_MAX
                w.reset()
                w.redraw()
                assert w._sn == 0 and not w._contact_hits and w._last_impact_clock is None
                report = dict(version=m.APP_VERSION, live=live, airborne=airborne,
                              above_surface_2px=aloft, visible_pixels_delta5=visible,
                              minimum_late_strength_ratio=float(np.min(ratio)),
                              max_initial_horizontal_angle_deg=float(np.rad2deg(np.max(angles))),
                              min_initial_horizontal_angle_deg=float(np.rad2deg(np.min(angles))),
                              contact_coverage=valid / total,
                              peak=self.peak, drops=stats)
                (OUT / "report.json").write_text(json.dumps(report, indent=2))
                print("PASS contact splashes:", report)
                self.stop()

        Probe().run()


if __name__ == "__main__":
    run()
