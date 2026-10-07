# -*- coding: utf-8 -*-
"""直筒那两个填充矩形(``_neck_solid_rect``/``_neck_fade_rect``)到底有没有被摆上去。

中轴剖面实测(平板口径, PC 1.237): 正常运行时沙柱止于出口**上方 ~34px**, 下面一段是背景色
⇒ 用户报的"颈部的沙和其他地方分成 2 团"。本探针在 ``redraw()`` **之后**读那两个矩形的
size/pos, 以及沙柱四边形的端点, 判它们有没有覆盖 [outlet, inlet]。
"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"
from kivy.clock import Clock
from kivy.core.window import Window
import main as m
m.HourglassWidget._make_sound_proxy = lambda *a, **kw: None
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
m.HourglassWidget.save_config = lambda *_: None


class P(m.HourglassApp):
    def on_start(self):
        Window.size = (1904, 2890)
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        hg = self.hourglass
        hg.set_duration(15.0)
        print("")
        print("   R=%.1f  canvas=%.0fx%.0f" % (hg._R, hg.width, hg.height))
        for t in (2.0, 14.2, 14.6):
            hg.reset(); hg.elapsed = t; hg._done_at = None
            hg.redraw()
            inlet = hg._taper["y_bot"]
            outlet = 2 * hg._neck_y - inlet
            print("   t=%5.1f  inlet=%7.1f outlet=%7.1f (Kivy y, 高=上)" % (t, inlet, outlet))
            for nm in ("_neck_solid_rect", "_neck_fade_rect"):
                r = getattr(hg, nm)
                print("        %-18s pos=(%.1f,%.1f) size=(%.1f,%.1f)  ⇒ y 覆盖 [%.1f,%.1f]"
                      % (nm, r.pos[0], r.pos[1], r.size[0], r.size[1],
                         r.pos[1], r.pos[1] + r.size[1]))
            side = hg._neck_sand_side()
            print("        side: 首点y=%.1f 末点y=%.1f  点数=%d  末点-出口=%+.1f"
                  % (side[0][1], side[-1][1], len(side), side[-1][1] - outlet))
        self.stop()


P().run()
