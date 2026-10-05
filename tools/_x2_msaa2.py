from pathlib import Path
import os, random, sys, tempfile
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "_x2"
def main():
    ms = sys.argv[1] if len(sys.argv) > 1 else "0"
    out_name = sys.argv[2] if len(sys.argv) > 2 else ("ms%s" % ms)
    with tempfile.TemporaryDirectory(prefix="x2ms2-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"; os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"; os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.config import Config
        Config.set('graphics', 'multisamples', ms)
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import (glReadPixels, glGetIntegerv, GL_RGBA,
                                          GL_UNSIGNED_BYTE, GL_SAMPLES)
        from PIL import Image
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
                real = w.redraw
                w.redraw = lambda: None
                while w.elapsed < 2.5:
                    now[0] += 1/120; w.tick(1/120)
                w.redraw = real
                w.redraw()
                print("PROBE state elapsed=%.2f dur=%.1f pn=%d GL_SAMPLES=%s"
                      % (w.elapsed, w.duration, w.pn, glGetIntegerv(GL_SAMPLES)))
                self.n = 0
                Clock.schedule_once(self.grab, 0.3)
            def grab(self, _dt):
                w = self.hourglass
                w.redraw()                     # draw again right before reading
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB").save(
                    OUT / ("%s_%d.png" % (out_name, self.n)))
                # is it the right state?  check pixel (263,200)
                chk = img.convert("RGB").getpixel((200, 263))
                print("PROBE grab%d pixel(200,263)=%s" % (self.n, chk))
                self.n += 1
                if self.n < 3:
                    Clock.schedule_once(self.grab, 0.3)
                else:
                    self.stop()
        ProbeApp().run()
main()
