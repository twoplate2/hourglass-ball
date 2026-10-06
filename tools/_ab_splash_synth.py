# -*- coding: utf-8 -*-
"""合成飞溅场: 用手写坐标喂给两条渲染路径, 逐像素比 —— 分开"光栅化"与"结构性"。

如果**合成场也差**, 那是光栅化(shader vs Kivy Rectangle); 如果**只有真实场差**,
那是结构性(槽位/PAD/尺寸组合)。坐标刻意覆盖: 整数/半像素/1x1/1x2/2x2/2x3/边界。

跑法: python tools/_ab_splash_synth.py
"""
import os
import sys
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_shot" / "splash_ab"


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_SPLASH_RENDERER"] = "batch"
    sys.argv = [sys.argv[0], "_abss"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import numpy as np

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 15}
    m.HourglassWidget.save_config = lambda *_: None

    # 合成场: (x, y, size) —— size 覆盖 标量 / (w,h) 两种形态
    SYNTH = [(120.0, 500.0, 1), (124.0, 500.0, 2), (128.5, 500.0, 1),
             (132.0, 500.5, (1, 2)), (136.0, 500.0, (2, 2)), (140.0, 500.0, (2, 3)),
             (144.0, 500.0, 1), (148.3, 503.7, 2), (152.0, 500.0, (1, 1)),
             (200.0, 520.0, 2), (204.0, 520.0, 2), (208.0, 520.0, 2)]
    got = {}

    def grab():
        w, h = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
        return np.asarray(Image.frombytes("RGBA", (w, h), px)
                          .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            self.hg = self.hourglass
            hg = self.hg
            hg.set_duration(15.0)
            hg._rebuild_height_table()
            hg.reset()
            hg.running = False
            # 冻结成合成场
            hg.splashes = [{"x": x, "y": y, "size": s, "_still": None}
                           for x, y, s in SYNTH]
            Clock.schedule_once(self.shot_batch, 0.05)

        def shot_batch(self, _dt):
            self.hg.redraw()
            Window.canvas.ask_update()
            Clock.schedule_once(self.shot_batch2, 0.0)

        def shot_batch2(self, _dt):
            got["batch"] = grab()
            b = getattr(self.hg, "_splash_batch", None)
            self._saved = []
            for part in b.parts:
                self._saved.append((part[0], part[0].indices))
                part[0].indices = array("H")
            Window.canvas.ask_update()
            Clock.schedule_once(self.shot_rect, 0.0)

        def shot_rect(self, _dt):
            hg = self.hg
            hg._splash_rects = []
            hg._sync_rects(hg._splash_group, hg._splash_rects, hg.splashes)
            Window.canvas.ask_update()
            Clock.schedule_once(self.shot_rect2, 0.0)

        def shot_rect2(self, _dt):
            got["rect"] = grab()
            for mesh, idx in self._saved:
                mesh.indices = idx
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return got


if __name__ == "__main__":
    g = run()
    import numpy as np
    from PIL import Image
    OUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(g["rect"]).save(OUT / "synth_rect.png")
    Image.fromarray(g["batch"]).save(OUT / "synth_batch.png")
    a, b = g["rect"].astype(int), g["batch"].astype(int)
    d = np.abs(a - b).max(axis=2)
    print("")
    print("  === 合成飞溅场(12 颗, 手写坐标): rect vs batch ===")
    print("  最大通道差 = %d ; 差异像素 %d" % (d.max(), int((d > 0).sum())))
    ys, xs = np.nonzero(d > 0)
    if len(ys):
        print("  差异点坐标(前 20):", list(zip(xs[:20].tolist(), ys[:20].tolist())))
    print("  图: _shot/splash_ab/synth_{rect,batch}.png")
    print("")
