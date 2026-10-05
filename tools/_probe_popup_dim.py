# -*- coding: utf-8 -*-
"""弹窗后面那层**整窗暗底**到底是谁画的 —— 别再猜了。

现状(`CLAUDE.md` 记的): 打开任意弹窗后, **整个窗口**都被压暗 ——
    顶栏金沙 (54,41,24) vs 正常 (217,163,96)   ⇒ 比例 ≈ 0.25
    背景   (76,74,69) vs 奶油 (253,246,227)   ⇒ 比例 ≈ 0.30
看起来像一层 alpha≈0.7 的黑。试过 `background_color=(0,0,0,0)` 和 `background=""`
**两次都无效** —— 那说明画的不是那两个属性。

本探针**不猜**: 打开真弹窗, 把 `canvas.before` / `canvas` / `canvas.after` 三棵树里
**每一条** `Color` 的 rgba 和 `Rectangle` 的 size/pos 打出来, 找出"覆盖整窗 + 深色"的那一条,
并指出它的宿主是谁(哪个 widget / 哪个属性)。

跑法: python tools/_probe_popup_dim.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def walk(group, depth=0, out=None):
    from kivy.graphics import Color, Rectangle, InstructionGroup
    if out is None:
        out = []
    for ins in group.children:
        if isinstance(ins, InstructionGroup):
            out.append(("  " * depth + "<group %s>" % type(ins).__name__, None))
            walk(ins, depth + 1, out)
        elif isinstance(ins, Color):
            out.append(("  " * depth + "Color", tuple(round(v, 3) for v in ins.rgba)))
        elif isinstance(ins, Rectangle):
            out.append(("  " * depth + "Rectangle", (tuple(round(v, 1) for v in ins.pos),
                                                     tuple(round(v, 1) for v in ins.size))))
        else:
            out.append(("  " * depth + type(ins).__name__, None))
    return out


def main():
    with tempfile.TemporaryDirectory(prefix="popupdim-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 60}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self._open_dev_menu()
                Clock.schedule_once(self.dump, 0.6)

            def dump(self, _dt):
                from kivy.core.window import Window
                import numpy as np
                from PIL import Image
                pop = self._dev_popup
                print("")
                print("=== 根因(读属性, 不是猜) ===")
                print("  MRO: %s" % " -> ".join(c.__name__ for c in type(pop).__mro__[:5]))
                print("  **overlay_color = %s**   <-- ModalView 的整窗遮罩, 默认 alpha 0.7 的黑"
                      % (getattr(pop, "overlay_color", None),))
                print("  background_color = %s (%s)" % (pop.background_color, pop.background))
                print("  ⇒ 前两次改的是 background/background_color —— 那是**弹窗面板自己的背景**,")
                print("    跟这层整窗遮罩毫无关系, 所以怎么改都不动。")

                def sample():
                    ww, hh = map(int, Window.size)
                    from kivy.graphics.opengl import (glReadPixels, GL_RGBA,
                                                      GL_UNSIGNED_BYTE)
                    px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                    im = np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                        Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"), np.int16)
                    # 顶栏金沙色块 + 背景, 取几个点
                    pts = {"金沙色块": (44, 44), "背景": (200, 300), "底部按钮": (100, 760)}
                    return {k: tuple(int(v) for v in im[y, x]) for k, (x, y) in pts.items()}

                self._img_before = self.shot()
                self._before = sample()
                print("")
                print("=== 改前 ===")
                for k, v in self._before.items():
                    print("  %-8s RGB=%s" % (k, v))
                self._panels = []
                self._i = 0
                self._ALPHAS = (0.7, 0.3, 0.0)
                print("")
                print("=== 三档遮罩 ===")
                Clock.schedule_once(self.step, 0.3)

            @staticmethod
            def shot():
                from kivy.core.window import Window
                from kivy.graphics.opengl import (glReadPixels, GL_RGBA,
                                                  GL_UNSIGNED_BYTE)
                from PIL import Image
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                return Image.frombytes("RGBA", (ww, hh), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")

            def step(self, _dt=0):
                """三档: 0.7(现状) -> 0.3 -> 0.0, 每档渲一帧收进面板。"""
                import numpy as np
                from kivy.core.window import Window
                from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
                from PIL import Image
                pop = self._dev_popup
                if self._i >= len(self._ALPHAS):
                    return self.montage()
                al = self._ALPHAS[self._i]
                pop.overlay_color = (0, 0, 0, al)
                Clock.schedule_once(self.take, 0.4)

            def take(self, _dt):
                import numpy as np
                from kivy.core.window import Window
                from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
                from PIL import Image
                al = self._ALPHAS[self._i]
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                im = np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"), np.int16)
                sw = tuple(int(v) for v in im[44, 44])        # 顶栏金沙色块
                self._panels.append((u"overlay alpha = %.1f   (金沙色块 %s)"
                                     % (al, sw), self.shot()))
                print("  alpha=%.1f  金沙色块=%s" % (al, sw))
                self._i += 1
                Clock.schedule_once(self.step, 0.2)

            def montage(self):
                from PIL import Image, ImageDraw
                OUT = ROOT / "_shot" / "popupdim"
                OUT.mkdir(parents=True, exist_ok=True)
                pad, lab = 8, 26
                w0 = self._panels[0][1].width
                out = Image.new("RGB", (w0 * len(self._panels) + pad * (len(self._panels) + 1),
                                        self._panels[0][1].height + lab + pad * 2),
                                (26, 26, 26))
                d = ImageDraw.Draw(out)
                for i, (t, im) in enumerate(self._panels):
                    x = pad + i * (im.width + pad)
                    out.paste(im, (x, pad + lab))
                    d.text((x + 4, pad + 6), t, fill=(255, 214, 110))
                pp = OUT / "popup_dim_3way.png"
                out.save(pp)
                print("")
                print("  参照(不弹窗): 金沙=(217,163,96)")
                print("  -> %s  (%dx%d)" % (pp, out.width, out.height))
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
