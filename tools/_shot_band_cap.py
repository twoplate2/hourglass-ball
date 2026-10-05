# -*- coding: utf-8 -*-
"""把"亮带画到玻璃上"这条**看出来**: 档6 上球沙面整幅 8x (修前/修后各一张)。

成因(已数值确认, `tools/_probe_rect_vs_surface.py`):
    沙体矩形上沿 `up_draw = upper_height + _up_lift` = **`level`**, 不含 rough
    亮带/沙面线  = **`level − drop(dx) + rough(i)`**
    ⇒ `rough(i) > drop(dx)` 的节点, 沙面线**高于矩形上沿** ⇒ 那一小段没有沙,
      而亮带仍沿沙面线画 ⇒ `sand_light @ 0.55` 合成到**玻璃**上 = 淡色小帽
      (实测色 (232,211,173) == sand_light(230,184,112) 与玻璃(234,243,248) 的 0.55 混合)

跑法: python tools/_shot_band_cap.py  [tag]
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "bandcap"
TAG = sys.argv[1] if len(sys.argv) > 1 else "before"
LEVEL = "6"
STEADY = 9.137


def main():
    with tempfile.TemporaryDirectory(prefix="bandcap-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
        import numpy as np

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 20}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        def shot():
            ww, hh = map(int, Window.size)
            px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
            return np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(20)
                w.running = True
                dt = 1.0 / 60.0
                while w.elapsed < STEADY:
                    now[0] += dt
                    w.tick(dt)
                w.running = False
                w.set_rough_level(LEVEL)
                self.w = w
                self.HH = int(Window.size[1])
                w.redraw()
                Clock.schedule_once(self.take, 0.35)

            def take(self, _dt):
                w = self.w
                im = shot()
                Ri = w._R_inner
                uh = w._upper_sand_height_px()
                lvl = w._upper_sand_bot + w._upper_level_for(uh)
                r0 = self.HH - 1 - int(w.to_window(0, lvl + 6)[1])
                r1 = self.HH - 1 - int(w.to_window(0, lvl - 14)[1])
                cx = int(w.to_window(w._cx, 0)[0])
                x0, x1 = max(0, cx - int(Ri) - 4), min(im.shape[1], cx + int(Ri) + 4)
                c = Image.fromarray(im).crop((x0, r0, x1, r1))
                c = c.resize((c.width * 8, c.height * 8), Image.NEAREST)
                OUT.mkdir(parents=True, exist_ok=True)
                p = OUT / ("surface_%s_8x.png" % TAG)
                c.save(p)
                print("  -> %s (%dx%d)  档%s, level=%.2f, 带 r0=%d..r1=%d"
                      % (p, c.width, c.height, LEVEL, lvl, r0, r1))
                Clock.schedule_once(lambda d: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
