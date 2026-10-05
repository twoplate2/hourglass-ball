"""Reviewer-1 round-1.5: recount flare/splash events with explicit windows.

Same deterministic setup as _r1_probe4.py (5 s period, dt=1/120, seed 23), but
this time every event is stamped with the simulated `elapsed` at which it was
observed, so the counts can be re-bucketed into any window. Also prints:
  - speed_factor / rate / neck_fill_time / motion_scale
  - per-0.5 s histogram of flares and splashes
  - which particle path (numpy / scalar) was active
"""
from pathlib import Path
import collections
import os
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="r15-") as home:
        os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1",
                          KIVY_METRICS_DENSITY="1", KIVY_METRICS_FONTSCALE="1")
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

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.setup, 1.0)

            def setup(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.prepare, 0.4)

            def prepare(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                w.set_duration(5.0)
                random.seed(23)
                w.toggle()
                real = w.redraw
                w.redraw = lambda: None
                flare_t, splash_t, hit_n = [], [], [0]
                # exact hit count via the numpy replay hook
                orig_replay = w._replay_hits

                def replay(idx, dt_, mt, ms, nw_, _o=orig_replay):
                    hit_n[0] += len(idx)
                    return _o(idx, dt_, mt, ms, nw_)

                w._replay_hits = replay
                print("PROBE speed_factor=%.3f rate=%.0f fill=%.3f motion_scale=%.3f "
                      "numpy=%s min=%d"
                      % (w.speed_factor, 600 * w.speed_factor, w._neck_fill_time,
                         w._particle_motion_scale, module._flow_numpy is not None,
                         module._NUMPY_MIN))
                while w.elapsed < 5.0:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                    e = w.elapsed
                    if w.flares:
                        flare_t += [e] * len(w.flares)
                        w.flares = []
                    if w.splashes:
                        splash_t += [e] * len(w.splashes)
                        w.splashes = []
                w.redraw = real
                w._replay_hits = orig_replay

                def cnt(v, a, b):
                    return sum(1 for t in v if a < t <= b)

                print("PROBE flares  [0,5]=%d  [1,5]=%d  [1.5,5]=%d  [0.5,5]=%d"
                      % (len(flare_t), cnt(flare_t, 1.0, 5.0),
                         cnt(flare_t, 1.5, 5.0), cnt(flare_t, 0.5, 5.0)))
                print("PROBE splash  [0,5]=%d  [1,5]=%d  [1.5,5]=%d  [0.5,5]=%d"
                      % (len(splash_t), cnt(splash_t, 1.0, 5.0),
                         cnt(splash_t, 1.5, 5.0), cnt(splash_t, 0.5, 5.0)))
                print("PROBE hits(numpy replay) = %d" % hit_n[0])
                for name, v in (("flare", flare_t), ("splash", splash_t)):
                    hist = collections.Counter(int(t * 2) / 2.0 for t in v)
                    print("PROBE %s per 0.5s: %s" % (
                        name, " ".join("%.1f:%d" % (k, hist[k]) for k in sorted(hist))))
                if flare_t:
                    print("PROBE flare first=%.3f last=%.3f  splash first=%.3f last=%.3f"
                          % (flare_t[0], flare_t[-1], splash_t[0], splash_t[-1]))
                print("PROBE done")
                self.stop()

        ProbeApp().run()


main()
