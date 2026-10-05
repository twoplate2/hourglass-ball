# -*- coding: utf-8 -*-
"""量清 `up_draw` 修复**到底改了什么像素** —— 换快照前必须先说清 diff(闸门注释的要求)。

同一几何、同一 elapsed, 参照物 = `../backup/android_main_20261005.py`(= 修复前), 逐像素比。
只报: 差异像素数 / 最大通道差 / 包围盒 / 差在哪条带。
跑法: python tools/_probe_cap_diff.py
"""
import importlib.util
import math
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT.parent / "backup" / "android_main_20261005.py"


def main():
    with tempfile.TemporaryDirectory(prefix="capdiff-") as home:
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
        m.HourglassWidget.load_config = lambda *_: {"duration": 60}
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
                spec = importlib.util.spec_from_file_location("ref", REF)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.HourglassWidget._make_sound_proxy = lambda *_: None
                old = mod.HourglassWidget(size=w.size, pos=w.pos)
                Clock.unschedule(old.tick)
                for o in (old, w):
                    o.duration = 60
                    o._rebuild_height_table()
                    o.elapsed = 24
                    o.running = True
                    o.particles = []
                    o.splashes = []
                    o.flares = []
                    o.dusts = []
                old._mound_height_px = w._mound_height_px
                old._upper_sand_height_px = w._upper_sand_height_px
                self.w, self.old = w, old
                self.rough = m.current_rough_level()
                w.set_rough_level(self.rough)
                old.set_rough_level(self.rough)
                print("")
                print("  档位 %s; Ri=%.2f; crest(HEAD)=%+.2f"
                      % (self.rough, w._R_inner, w._upper_rough_crest(w._upper_sand_height_px())))
                w.redraw()
                Clock.schedule_once(self.takeA, 0.35)

            def takeA(self, _dt):
                self.A = shot()
                self.old.redraw()
                Clock.schedule_once(self.takeB, 0.35)

            def takeB(self, _dt):
                B = shot()
                d = np.abs(self.A.astype(int) - B.astype(int)).max(axis=2)
                ys, xs = np.nonzero(d > 0)
                print("  差异像素 >0 : %d ; >8 : %d ; 最大通道差 %d"
                      % (len(ys), int((d > 8).sum()), int(d.max())))
                if len(ys):
                    print("  包围盒 row %d~%d, col %d~%d" % (ys.min(), ys.max(), xs.min(), xs.max()))
                    rows = np.bincount(ys, minlength=d.shape[0])
                    top = np.argsort(rows)[-5:][::-1]
                    print("  最密 5 行:", [(int(y), int(rows[y])) for y in sorted(top)])
                    w = self.w
                    uh = w._upper_sand_height_px()
                    lvl = w._upper_sand_bot + w._upper_level_for(uh)
                    HH = int(Window.size[1])
                    r_lvl = HH - 1 - int(w.to_window(0, lvl)[1])
                    print("  矩形上沿(修复前) 在 row %d ⇒ 差异应集中在其上方" % r_lvl)
                from PIL import Image as I
                OUT = ROOT / "_shot" / "bandcap"
                OUT.mkdir(parents=True, exist_ok=True)
                for tag, arr in (("ref", B), ("head", self.A)):
                    I.fromarray(arr.astype("uint8")).save(OUT / ("mid_%s.png" % tag))
                print("  -> _shot/bandcap/mid_ref.png / mid_head.png")
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
