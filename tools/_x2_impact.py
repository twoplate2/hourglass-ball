"""Impact-x distribution (A4 cross-check) + EMA p2p (A6 cross-check) + tube colour
shift under NECK_UV_ANCHOR raise (A1' side effect).  Physics-only; no redraw."""
from pathlib import Path
import os, random, sys, tempfile
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
def main():
    with tempfile.TemporaryDirectory(prefix="x2imp-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"; os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"; os.environ["KIVY_METRICS_FONTSCALE"] = "1"
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
            def on_start(self): Clock.schedule_once(self.setup, 1.0)
            def setup(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.prepare, 0.4)
            def prepare(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                for period in (5.0, 60.0, 1.0):
                    w.set_duration(period)
                    random.seed(23)
                    w.toggle()
                    real = w.redraw
                    w.redraw = lambda: None
                    hits = []
                    peaks = []
                    seen = set()
                    t_end = period
                    while w.elapsed < t_end:
                        now[0] += 1/120
                        w.tick(1/120)
                        for f in w.flares:
                            k = (round(f["x"], 4), round(f["end"], 6))
                            if k not in seen:
                                seen.add(k); hits.append(f["x"])
                        peaks.append(w.mound_peak_offset)
                    w.redraw = real
                    cx = w._cx
                    dist = sorted(abs(x - cx) for x in hits)
                    q = lambda f: dist[min(len(dist)-1, int(f*len(dist)))]
                    if dist:
                        print("IMPACT period=%.0f  flares=%d  med=%.2f p90=%.2f p95=%.2f max=%.2f"
                              % (period, len(dist), q(.5), q(.9), q(.95), dist[-1]))
                    else:
                        print("IMPACT period=%.0f  flares=0" % period)
                    if peaks:
                        print("   EMA peak p2p=%.2f min=%.2f max=%.2f  (last=%.2f)"
                              % (max(peaks)-min(peaks), min(peaks), max(peaks), peaks[-1]))
                    w.reset()
                self.stop()
        ProbeApp().run()
main()
