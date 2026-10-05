"""Reviewer-1 round-1.5b: per-tick accounting of hits vs flare/splash spawns.

Aims to explain why the [1,5] window shows 1688 flares / 3406 splashes against
only 5327 numpy-replayed hits (implied hit totals 6752 / 6812 at 25% / 50%).
"""
from pathlib import Path
import os
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="r15b-") as home:
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
                ticks = []
                orig_replay = w._replay_hits
                cur = [0]

                def replay(idx, dt_, mt, ms, nw_, _o=orig_replay):
                    cur[0] += len(idx)
                    return _o(idx, dt_, mt, ms, nw_)

                w._replay_hits = replay
                t = 0.0
                while w.elapsed < 5.0:
                    now[0] += 1 / 120
                    cur[0] = 0
                    w.flares = []
                    w.splashes = []
                    w.tick(1 / 120)
                    t += 1 / 120
                    ticks.append((round(w.elapsed, 4), cur[0], len(w.flares),
                                  len(w.splashes), w.pn,
                                  int(getattr(w, "_spawn_from", 0))))
                w.redraw = real
                w._replay_hits = orig_replay
                th = sum(x[1] for x in ticks)
                tf = sum(x[2] for x in ticks)
                ts = sum(x[3] for x in ticks)
                print("PROBE totals ticks=%d hits=%d flares=%d splash=%d  f25=%.3f s50=%.3f"
                      % (len(ticks), th, tf, ts, tf / max(1, th), ts / max(1, th)))
                # how many ticks show flares > hits?
                bad = [x for x in ticks if x[2] > x[1]]
                print("PROBE ticks with flares>hits: %d" % len(bad))
                if bad[:5]:
                    print("PROBE sample bad:", bad[:5])
                # pn excursion below 800 after t>1.0?
                low = [x for x in ticks if x[0] > 1.0 and x[4] < 800]
                print("PROBE ticks t>1 with pn<800: %d" % len(low))
                if low[:5]:
                    print("PROBE sample low:", low[:5])
                # coarse: hits/flares per 0.5s bucket
                import collections
                hb = collections.Counter()
                fb = collections.Counter()
                sb = collections.Counter()
                for (e, h, f, s, *_r) in ticks:
                    k = int(e * 2) / 2
                    hb[k] += h
                    fb[k] += f
                    sb[k] += s
                for k in sorted(hb):
                    if k >= 0.5:
                        print("PROBE t=%.1f hits=%4d flares=%4d splash=%4d  ratio f/h=%.3f"
                              % (k, hb[k], fb[k], sb[k], fb[k] / max(1, hb[k])))
                # compare with a control: is `w.flares` assignment (rebinding) missing
                # flares appended by a captured bound-method alias inside update_particles?
                print("PROBE done")
                self.stop()

        ProbeApp().run()


main()
