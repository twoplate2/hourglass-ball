"""One-shot: print what the neck column ACTUALLY samples at runtime.

Reasoning kept giving (215,161,94) while the capture reads (230,184,112), so read
the live tex_coords / geometry instead of deriving them.
"""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="neck-uv-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from PIL import Image
        import random
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
                while w.elapsed < 2.5:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                w.redraw = real
                w.redraw()
                print("PROBE material grain=%r size=%r mode=%r shade=%r"
                      % (module.SAND_MATERIAL_GRAIN, module.SAND_MATERIAL_SIZE,
                         module.SAND_MATERIAL, module.SAND_MATERIAL_SHADE))
                print("PROBE geom  neck_y=%.2f R_inner=%.2f upper_sand_bot=%.2f "
                      "y_bot=%.2f t_in=%.2f nw=%.2f ow=%.2f"
                      % (w._neck_y, w._R_inner, w._upper_sand_bot,
                         w._taper["y_bot"], w._taper["t_in"], w.neck_w, w._ow))
                print("PROBE widget pos=%s size=%s  窗口=%s"
                      % (tuple(w.pos), tuple(w.size), tuple(Window.size)))
                side = w._neck_sand_side()
                print("PROBE side top=%r bottom=%r  n=%d" % (side[0], side[-1], len(side)))
                su = 1.0 / (2 * w._R_inner)
                for i in (0, len(side) // 2, max(0, len(side) - 2)):
                    q = w._neck_quads[i]
                    print("PROBE quad[%d] points=%s" % (i, tuple(round(v, 1) for v in q.points)))
                    print("              uv=%s" % (tuple(round(v, 4) for v in q.tex_coords),))
                print("PROBE 期望 uv: u=0.5±%.4f  v=%.4f..%.4f (su=%.5f)"
                      % (w._taper["t_in"] * su,
                         (w._upper_sand_bot - max(p[1] for p in side)) * su,
                         (w._upper_sand_bot - min(p[1] for p in side)) * su, su))
                print("PROBE neck_solid tex_coords=%s a=%.2f"
                      % (tuple(round(v, 4) for v in w._neck_solid_rect.tex_coords),
                         w._neck_solid_color.a))
                print("PROBE neck_fade  tex_coords=%s"
                      % (tuple(round(v, 4) for v in w._neck_fade_rect.tex_coords),))
                print("PROBE neck material is same object as sand: %s"
                      % (w._neck_quads[0].texture is w._sand_chords[0][1].texture))
                self.stop()

        ProbeApp().run()


main()
