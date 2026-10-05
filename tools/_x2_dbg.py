from pathlib import Path
import os, random, sys, tempfile
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
def main():
    with tempfile.TemporaryDirectory(prefix="x2dbg-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"; os.environ["KIVY_NO_FILELOOG"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
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
                w.set_duration(5.0)
                random.seed(23)
                w.toggle()
                w.redraw = lambda: None
                fl = []
                class LogList(list):
                    def append(self, item):
                        fl.append(item["x"]); super().append(item)
                w.flares = LogList()
                nfl = 0
                for k in range(600):
                    now[0] += 1/120; w.tick(1/120)
                    if k % 60 == 0:
                        print("DBG t=%.2f pn=%d flares_logged=%d flares_len=%d mound_top=%.1f running=%s"
                              % (w.elapsed, w.pn, len(fl), len(w.flares), w.get_mound_top_y(), w.running))
                ys = w._pv.y[:w.pn]
                print("DBG min y=%.1f  mound_top=%.1f  n=%d" % (min(ys) if ys else -1, w.get_mound_top_y(), w.pn))
                self.stop()
        ProbeApp().run()
main()
