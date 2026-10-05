"""Reviewer-1 probe #4: touchdown x distribution + mound_peak_offset motion."""
from pathlib import Path
import os, random, sys, tempfile
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory(prefix="r1d-") as home:
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
                offsets = []
                flare_x, splash_x = [], []
                while w.elapsed < 5.0:
                    now[0] += 1/120
                    w.tick(1/120)
                    if w.elapsed > 1.0:
                        offsets.append(w.mound_peak_offset)
                        flare_x += [f["x"] for f in w.flares]
                        splash_x += [s["x"] for s in w.splashes]
                        w.flares = []; w.splashes = []
                w.redraw = real
                import statistics as st
                def q(v, f): v=sorted(v); return v[min(len(v)-1,int(f*len(v)))]
                print("PROBE n_flares=%d n_splash=%d" % (len(flare_x), len(splash_x)))
                if flare_x:
                    print("PROBE flare x: min %d p5 %d med %d p95 %d max %d (cx=%d)"
                          % (min(flare_x), q(flare_x,.05), q(flare_x,.5), q(flare_x,.95), max(flare_x), w._cx))
                    print("PROBE |flare x - cx|: med %d p95 %d max %d"
                          % (q([abs(v-w._cx) for v in flare_x],.5), q([abs(v-w._cx) for v in flare_x],.95), max(abs(v-w._cx) for v in flare_x)))
                if splash_x:
                    print("PROBE |splash x - cx|: med %d p95 %d max %d (incl. bounce travel)"
                          % (q([abs(v-w._cx) for v in splash_x],.5), q([abs(v-w._cx) for v in splash_x],.95), max(abs(v-w._cx) for v in splash_x)))
                print("PROBE mound_peak_offset: min %.2f max %.2f p2p %.2f  n=%d"
                      % (min(offsets), max(offsets), max(offsets)-min(offsets), len(offsets)))
                # flares are consumed by the renderer at draw time; recount a fresh frame
                w.redraw()
                print("PROBE flares alive=%d splashes alive=%d mound_top=%.2f neck_grains=%d"
                      % (len(w.flares), len(w.splashes), w.get_mound_top_y(), w._neck_grain_count))
                print("PROBE done")
                self.stop()
        ProbeApp().run()

main()
