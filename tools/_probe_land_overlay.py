# -*- coding: utf-8 -*-
"""横屏(反旋转层)下, 弹窗遮罩有没有盖满整屏 —— 只看**屏幕四角**。

r29-2号 2026-10-06 设备实测: 旋转布局下开弹窗, 屏**四角**没被压暗(亮角)。
机制: 弹窗挂旋转层, 而 `ModalView` 的 overlay 铺满宿主 ⇒ 宿主是 `w×h`,
绕中心转 90° 后只覆盖 `h×w` ⇒ 四角露出来。

**判据(最干净的那个)**: 弹窗在正中 ⇒ 四角必然是纯遮罩。
  未修: 四角比值 ≈ **1.00**(完全没变) ; 四边中点 ≈ 0.30(正常压暗)
  修后: 四角与中点**都**应被压暗(比值同量级)
桌面复现靠 `--landscape`(源码: `land` 需要 `platform=="android" or "--landscape" in sys.argv`)。

跑法: python tools/_probe_land_overlay.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W, H = 1080, 1008


def main():
    with tempfile.TemporaryDirectory(prefix="landov-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        sys.argv.append("--landscape")
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
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        def shot():
            ww, hh = map(int, Window.size)
            px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
            return np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"), np.float64)

        def L(img, x, y, r=6):
            p = img[max(0, y - r):y + r, max(0, x - r):x + r]
            return (0.299 * p[:, :, 0] + 0.587 * p[:, :, 1] + 0.114 * p[:, :, 2]).mean()

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                layer = self.root
                print("")
                print("  层: angle=%s  size=%s  center=%s   窗口=%s"
                      % (getattr(layer, "angle", "?"), tuple(layer.size),
                         tuple(layer.center), tuple(Window.size)))
                w.redraw()
                Clock.schedule_once(self.before, 0.35)

            def before(self, _dt):
                self.B = shot()
                from PIL import Image as _I
                out = ROOT / "_shot" / "landov"
                out.mkdir(parents=True, exist_ok=True)
                _I.fromarray(self.B.astype("uint8")).save(out / "before.png")
                self.on_duration_picker(None)
                Clock.schedule_once(self.after, 0.6)

            def after(self, _dt):
                A = shot()
                from PIL import Image as _I
                out = ROOT / "_shot" / "landov"
                out.mkdir(parents=True, exist_ok=True)
                _I.fromarray(A.astype("uint8")).save(out / "after.png")
                hh, ww, _ = A.shape
                pts = [("左上", 8, 8), ("左下", 8, hh - 8), ("右上", ww - 8, 8),
                       ("右下", ww - 8, hh - 8),
                       ("左中", 8, hh // 2), ("右中", ww - 8, hh // 2),
                       ("上中", ww // 2, 8), ("下中", ww // 2, hh - 8)]
                print("")
                print("  点      开弹窗前  开弹窗后   比值   判定")
                corners, edges = [], []
                for nm, x, y in pts:
                    a, b = L(self.B, x, y), L(A, x, y)
                    r = b / max(a, 1)
                    (corners if len(nm) == 2 and nm[0] in "左右" and nm[1] in "上下" else edges).append(r)
                    print("  %-5s  %7.1f  %7.1f   %.2f   %s"
                          % (nm, a, b, r, "**没被压暗**" if r > 0.85 else ("压暗" if r < 0.5 else "?")))
                print("")
                print("  ⇒ 四角比值 %s ; 边中点比值 %s"
                      % (["%.2f" % v for v in corners], ["%.2f" % v for v in edges]))
                bad = [v for v in corners if v > 0.85]
                print("  ⇒ %s" % ("**四角没盖住(%d/4)**" % len(bad) if bad else "四角都被压暗 ✓ 遮罩盖满"))
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
