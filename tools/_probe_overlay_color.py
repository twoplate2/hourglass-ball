# -*- coding: utf-8 -*-
"""`ModalView.overlay_color` 到底管不管用? (2026-10-06)

背景: `main.py` 的开发者菜单注释写「**把模态遮罩调到几乎透明** …… 留 10%」,
而同一段下面三行又写「已知未解决: 整窗暗底**关不掉**, 试过 `background_color=(0,0,0,0)`
与 `background=""` 两次都无效」。
⚠️ 但 `grep -n overlay_color main.py` ⇒ **全仓库 0 处** ——
**他们试的是另外两个属性, 从来没试过 ModalView 文档里管遮罩的那一个。**

判据(**先标定**): 同一个背景点在「无弹窗 / 有弹窗(默认) / 有弹窗(overlay 设为 0)」三种状态下的
RGB。若默认态明显变暗、置 0 后回到接近无弹窗的值 ⇒ **该属性有效**;
若置 0 后与默认态**逐像素相同** ⇒ 无效, "关不掉"成立。

跑法: python tools/_probe_overlay_color.py
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_shot" / "overlay"


def main():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_ovl"]        # ⚠️ 无位置参数时 Kivy 会解析 --landscape 并退出
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import numpy as np

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    res = []

    def shot(tag):
        ww, hh = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
        img = np.asarray(Image.frombytes("RGBA", (ww, hh), px)
                         .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))
        OUT.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img).save(OUT / ("%s.png" % tag))
        return img

    # 采样点: 沙漏之外的背景(弹窗盖不到的地方) —— 遮罩压的就是它
    def sample(img, tag):
        h, w, _ = img.shape
        pts = [(int(w * 0.06), int(h * 0.06)), (int(w * 0.94), int(h * 0.06)),
               (int(w * 0.06), int(h * 0.94)), (int(w * 0.5), int(h * 0.04))]
        vals = [tuple(int(v) for v in img[y, x]) for x, y in pts]
        res.append((tag, vals))

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (1080, 2400)
            Clock.schedule_once(self.s1, 1.2)

        def s1(self, _dt):
            sample(shot("A_no_popup"), "A 无弹窗")
            Clock.schedule_once(self.s2, 0.4)

        def s2(self, _dt):
            self.on_duration_picker(None)
            Clock.schedule_once(self.s3, 0.9)

        def s3(self, _dt):
            sample(shot("B_default_overlay"), "B 弹窗·默认 overlay")
            self._ovl_popup = None
            for c in Window.children:
                if type(c).__name__ == "_SandBgPopup":
                    self._ovl_popup = c
            if self._ovl_popup is None:
                res.append(("**抓不到弹窗**", []))
                Clock.schedule_once(lambda dt: self.stop(), 0.2)
                return
            res.append(("弹窗类", type(self._ovl_popup).__name__))
            res.append(("默认 overlay_color", [tuple(self._ovl_popup.overlay_color)]))
            self._ovl_popup.overlay_color = (0, 0, 0, 0)
            Clock.schedule_once(self.s4, 0.6)

        def s4(self, _dt):
            sample(shot("C_overlay_zero"), "C 弹窗·overlay 设 0")
            self._alphas = [0.0, 0.10, 0.25, 0.40, 0.55, 0.70]
            self._i = 0
            self._sweep()

        def _sweep(self, *_):
            if self._i >= len(self._alphas):
                Clock.schedule_once(lambda dt: self.stop(), 0.2)
                return
            a = self._alphas[self._i]
            self._i += 1
            self._ovl_popup.overlay_color = (0, 0, 0, a)
            Clock.schedule_once(lambda dt: self._grab(a), 0.45)

        def _grab(self, a):
            sample(shot("D_alpha_%03d" % int(a * 100)), "α=%.2f" % a)
            self._sweep()

    P().run()
    return res


if __name__ == "__main__":
    rows = main()
    print("")
    print("  === overlay_color 有效性 ===")
    base = None
    for tag, vals in rows:
        if not vals:
            print("  %s" % tag)
            continue
        if tag.startswith("A "):
            base = vals
        print("  %-24s %s" % (tag, "  ".join(str(v) for v in vals)))
    print("")
