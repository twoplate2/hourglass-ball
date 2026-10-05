# -*- coding: utf-8 -*-
"""上球沙面 档6/档4/档1 并排放大 —— 看有没有"浮在沙面上的米白短条"(r27-1号 现象)。

数字侧(`tools/_probe_marker_float.py` 的受控 A/B)结论是标记深度 +3.8~+6.9px、全在沙面下。
数字与"看图"是两条独立的线 ⇒ 这一张专门给人看。
⚠️ 同机同法同 elapsed, 唯一变量 = 起伏档位。
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "marker"
DURATION = 20
STEADY = 9.137


def main():
    with tempfile.TemporaryDirectory(prefix="markerlv-") as home:
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
        m.HourglassWidget.load_config = lambda *_: {"duration": DURATION}
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
                w.set_duration(DURATION)
                w.running = True
                dt = 1.0 / 60.0
                while w.elapsed < STEADY:
                    now[0] += dt
                    w.tick(dt)
                w.running = False
                self.w = w
                self.HH = int(Window.size[1])
                self.shots = []
                self.i = 0
                Clock.schedule_once(self.nxt, 0.3)

            def row(self, y_kivy):
                return self.HH - 1 - int(self.w.to_window(0, y_kivy)[1])

            def nxt(self, _dt):
                if self.i >= 3:
                    return self.done()
                lvl = ("6", "4", "1")[self.i]
                self.w.set_rough_level(lvl)
                self.cur = lvl
                self.w.redraw()
                Clock.schedule_once(self.take, 0.30)

            def take(self, _dt):
                w = self.w
                im = shot()
                uh = w._upper_sand_height_px()
                lvl_k = w._upper_sand_bot + w._upper_level_for(uh)
                # ⚠️ `lvl_k` 是**漏斗扣减之前**的 level; 真正画出来的沙面还要减去
                #    `_upper_surface_drop`。第一版按 lvl_k 裁 ⇒ 只截到沙的一条边。
                _p = min(1.0, w.elapsed / w.duration)
                _d, _b = w._upper_funnel_params(_p, uh)
                y_surf = lvl_k - w._upper_surface_drop(0.0, _d, _b)
                # 沙面上下各留: 沙面上方 26px ~ 下方 10px
                band = (self.row(y_surf + 14), self.row(y_surf - 6))
                c = Image.fromarray(im).crop((0, band[0], im.shape[1], band[1]))
                c = c.resize((c.width * 4, c.height * 4), Image.NEAREST)
                self.shots.append((self.cur, c))
                self.i += 1
                Clock.schedule_once(self.nxt, 0.20)

            def done(self):
                from PIL import ImageDraw
                pad, lab = 8, 22
                cw, ch = self.shots[0][1].size
                out = Image.new("RGB", (cw + pad * 2,
                                        len(self.shots) * (ch + lab + pad) + pad),
                                (24, 24, 24))
                d = ImageDraw.Draw(out)
                for k, (lvl, c) in enumerate(self.shots):
                    y = pad + k * (ch + lab + pad)
                    out.paste(c, (pad, y + lab))
                    d.text((pad + 4, y + 5),
                           "沙面起伏 档 %s   (14px 上方 ~ 6px 下方, 4x)" % lvl,
                           fill=(255, 214, 110))
                OUT.mkdir(parents=True, exist_ok=True)
                p = OUT / "surface_levels_4x_tight.png"
                out.save(p)
                print("  -> %s (%dx%d)" % (p, out.width, out.height))
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
