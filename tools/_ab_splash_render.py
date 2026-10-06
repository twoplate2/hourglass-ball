# -*- coding: utf-8 -*-
"""飞溅改前/改后 **逐像素**对照 —— 固定状态回放(外部评审 §7.1 阶段一)。

⚠️ 为什么不能用两次跑进程去比: `elapsed` 走墙钟, 两条渲染路径的帧耗时不同
⇒ 抓图那一刻的**粒子位置本身就不同** ⇒ 比出来的是模拟分歧, 不是渲染分歧。
本探针在**同一个进程、同一份粒子状态**上渲染两遍。

做法: 跑到指定时刻 → 抓 batch 版 → 把批处理的索引清空(该层不出像素) →
      走原 `_sync_rects` 抓 rect 版 → 逐像素比 → 还原。

跑法: python tools/_ab_splash_render.py [周期=15] [抓图时刻=6.0]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
AT = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
OUT = ROOT / "_shot" / "splash_ab"


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_SPLASH_RENDERER"] = "batch"
    sys.argv = [sys.argv[0], "_absr"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import numpy as np
    from array import array

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

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
            hg.set_duration(PERIOD)
            hg._rebuild_height_table()
            hg.reset()
            hg.toggle()
            Clock.schedule_once(self.freeze, AT)

        def freeze(self, _dt):
            hg = self.hg
            hg.running = False                 # 冻结: 之后不再推进模拟
            Clock.schedule_once(self.shot_batch, 0.05)

        def shot_batch(self, _dt):
            hg = self.hg
            hg.redraw()
            Window.canvas.ask_update()
            Clock.schedule_once(self.shot_batch2, 0.0)

        def shot_batch2(self, _dt):
            got["batch"] = grab()
            # A/A 对照: 同一路径再抓一张 —— 量具本身抖不抖(外部评审 §7.2「先用 A/A 估噪声」)
            Window.canvas.ask_update()
            Clock.schedule_once(self.shot_batch3, 0.0)

        def shot_batch3(self, _dt):
            got["batch2"] = grab()
            self.rect_pass()
        def rect_pass(self):
            # 清空批处理索引 ⇒ 该层不出像素, 再把原 `_sync_rects` 画上去
            b = getattr(self.hg, "_splash_batch", None)
            saved = []
            for part in b.parts:
                saved.append((part[0], part[0].indices))
                part[0].indices = array("H")
            self._saved = saved
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
            Window.canvas.ask_update()
            Clock.schedule_once(self.shot_rect3, 0.0)

        def shot_rect3(self, _dt):
            got["rect2"] = grab()
            print("ABSPLASH splashes=%d" % len(self.hg.splashes))
            for mesh, idx in self._saved:
                mesh.indices = idx
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return got


if __name__ == "__main__":
    g = run()
    if "rect" not in g or "batch" not in g:
        print("  抓图失败"); raise SystemExit
    import numpy as np
    from PIL import Image
    OUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(g["rect"]).save(OUT / "rect.png")
    Image.fromarray(g["batch"]).save(OUT / "batch.png")
    def pair(x, y, label):
        d = np.abs(g[x].astype(int) - g[y].astype(int)).max(axis=2)
        print("  %-16s 最大差 %3d ; 差异像素 %6d (%.4f%%)"
              % (label, d.max(), int((d > 0).sum()), 100.0 * (d > 0).mean()))
        return d

    print("")
    print("  === 量具 A/A 对照(同一路径抓两张) ===")
    pair("rect", "rect2", "rect vs rect")
    pair("batch", "batch2", "batch vs batch")
    print("")
    print("  === 飞溅: 固定状态下 rect vs batch 逐像素 ===")
    d = pair("rect", "batch", "rect vs batch")
    for lo, hi in ((1, 2), (3, 8), (9, 32), (33, 255)):
        print("    差 %3d..%-3d : %6d px" % (lo, hi, int(((d >= lo) & (d <= hi)).sum())))
    print("  并排/差分图: _shot/splash_ab/")
    print("")
