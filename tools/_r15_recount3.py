"""Reviewer-1 round-1.5c: exact spawn/hit/arrival accounting over the 5 s cycle.

Counts, per tick:
  spawns  = pn_end - pn_start + hits_this_tick
  hits    = numpy-replay hits + scalar-path hits (processed - survivors)
Also buckets by 0.5 s, and reports which physics path handled each bucket.
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
    with tempfile.TemporaryDirectory(prefix="r15c-") as home:
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

                acc = {"nhit": 0, "proc": 0, "surv": 0}
                orig_replay = w._replay_hits
                orig_to = w._p_to_dicts
                orig_from = w._p_from_dicts

                def replay(idx, dt_, mt, ms, nw_, _o=orig_replay):
                    acc["nhit"] += len(idx)
                    return _o(idx, dt_, mt, ms, nw_)

                def to_d(new_from=None, _o=orig_to):
                    out = _o(new_from)
                    acc["proc"] += len(out)
                    return out

                def from_d(particles, _o=orig_from):
                    acc["surv"] += len(particles)
                    return _o(particles)

                w._replay_hits = replay
                w._p_to_dicts = to_d
                w._p_from_dicts = from_d

                rows = []
                while w.elapsed < 5.0:
                    now[0] += 1 / 120
                    acc.update(nhit=0, proc=0, surv=0)
                    w.flares = []
                    w.splashes = []
                    pn0 = w.pn
                    w.tick(1 / 120)
                    pn1 = w.pn
                    hits = acc["nhit"] + max(0, acc["proc"] - acc["surv"])
                    spawns = pn1 - pn0 + hits
                    rows.append((w.elapsed, spawns, hits, len(w.flares),
                                 len(w.splashes), pn0, pn1,
                                 acc["nhit"] > 0 or acc["proc"] > 0,
                                 acc["proc"] > 0))
                w.redraw = real
                w._replay_hits = orig_replay
                w._p_to_dicts = orig_to
                w._p_from_dicts = orig_from

                TS = sum(r[1] for r in rows)
                TH = sum(r[2] for r in rows)
                TF = sum(r[3] for r in rows)
                TSpl = sum(r[4] for r in rows)
                print("PROBE totals spawns=%d hits=%d flares=%d splash=%d pn_end=%d"
                      % (TS, TH, TF, TSpl, rows[-1][6]))
                print("PROBE rate: flares/hits=%.4f splash/hits=%.4f hits/spawns=%.4f"
                      % (TF / TH, TSpl / TH, TH / TS))
                b = collections.Counter()
                bsp = collections.Counter()
                bh = collections.Counter()
                bf = collections.Counter()
                bpath = collections.Counter()
                for (e, sp, hi, fl, spl, _p0, _p1, _a, scalar) in rows:
                    k = int(e * 2) / 2
                    bsp[k] += sp
                    bh[k] += hi
                    bf[k] += fl
                    b[k] += spl
                    if scalar:
                        bpath[k] = 1
                for k in sorted(bh):
                    print("PROBE t=%.1f spawns=%4d hits=%4d flares=%3d splash=%4d scalar=%d"
                          % (k, bsp[k], bh[k], bf[k], b[k], bpath[k]))
                # pn trace
                for r in rows[::60]:
                    print("PROBE pn t=%.2f pn0=%d pn1=%d" % (r[0], r[5], r[6]))
                print("PROBE done")
                self.stop()

        ProbeApp().run()


main()
