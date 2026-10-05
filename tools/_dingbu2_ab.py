# -*- coding: utf-8 -*-
"""dingbu2.md 第 3 节的 **改前/改后对照图** —— 不改 main.py, 运行期替换漏斗参数函数。

外部评审的判据(dingbu2.md §3):
    b = 0.35 × C   (C = 当前目标沙量对应的**参考自由面可见全宽**)
    d_peak = 0.010 × D
    最大坡度 ≈ 1.54 × d / b ≈ 0.045 (≈2.6°) 作为"缓缓下倾"的起点
当前实现: d = 0.03D, b = 0.10D~0.14D ⇒ 坡度 ≈0.47 —— 他叫"局部挖槽"。

产出一张并排图: 左 = 当前(v1.85) / 右 = 提案(b=0.35C, d=0.010D), 同一 elapsed、同一沙色。

跑法:
    python tools/_dingbu2_ab.py                 # 默认 p=0.5, 金沙, 400x800
    python tools/_dingbu2_ab.py 0.25 0.75       # 任意 p 列表
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "dingbu2"


def main():
    ps = [float(v) for v in sys.argv[1:]] or [0.30, 0.50, 0.75]
    with tempfile.TemporaryDirectory(prefix="dingbu2-") as home:
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

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 5}
        m.HourglassWidget.save_config = lambda *_: None

        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}

        def proposed_params(self, p, height):
            """按 dingbu2.md §3 的 b=0.35C / d=0.010D。C = 2×半弦宽。"""
            D = 2.0 * self._R_inner
            s1 = m._smoothstep(0.0, 0.25, p)
            s2 = 1.0 - m._smoothstep(0.85, 1.0, p)
            d = 0.010 * D * s1 * s2
            half_chord = (self._R_inner ** 2 - (self._R_inner - height) ** 2)
            half_chord = (half_chord ** 0.5) if half_chord > 0 else 0.0
            b = 0.35 * (2.0 * half_chord)
            return min(d, 0.25 * height), min(b, 0.80 * half_chord)

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.go, 1)

            def go(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.set_duration(5)
                w.completion_enabled = False
                if not w.running:
                    w.toggle()
                w._done_at = None
                self.w = w
                self.i = 0
                Clock.schedule_once(self.step, 0.4)

            def step(self, _dt):
                if self.i >= len(ps):
                    self.finish()
                    return
                p = ps[self.i]
                w = self.w
                w.elapsed = 5.0 * p
                w._mound_shape_cache = None
                # 当前 —— 删掉实例属性即回落到类方法(直接把类方法赋给实例会变成未绑定函数)
                if "_upper_funnel_params" in w.__dict__:
                    del w.__dict__["_upper_funnel_params"]
                w.redraw()
                d0, b0 = w._upper_funnel_params(p, w._upper_sand_height_px())
                self.grab("cur")
                # 提案
                import types
                w._upper_funnel_params = types.MethodType(proposed_params, w)
                w._mound_shape_cache = None
                w.redraw()
                d1, b1 = w._upper_funnel_params(p, w._upper_sand_height_px())
                self.grab("new")
                self.report(p, d0, b0, d1, b1)
                self.i += 1
                Clock.schedule_once(self.step, 0.35)

            def grab(self, tag):
                W, H = map(int, Window.size)
                px = glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE)
                shots[tag] = Image.frombytes("RGBA", (W, H), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")

            def report(self, p, d0, b0, d1, b1):
                def slope(d, b):
                    return 1.54 * d / b if b > 1e-6 else 0.0
                print("p=%.2f  当前 d=%5.2f b=%6.2f 坡度=%.3f (%4.1f deg)  |  提案 d=%5.2f b=%6.2f 坡度=%.3f (%4.1f deg)"
                      % (p, d0, b0, slope(d0, b0),
                         __import__("math").degrees(__import__("math").atan(slope(d0, b0))),
                         d1, b1, slope(d1, b1),
                         __import__("math").degrees(__import__("math").atan(slope(d1, b1)))))
                a = shots["cur"].crop((60, 250, 340, 300)).resize((280 * 3, 50 * 3), Image.Resampling.NEAREST)
                b = shots["new"].crop((60, 250, 340, 300)).resize((280 * 3, 50 * 3), Image.Resampling.NEAREST)
                c = Image.new("RGB", (a.width, a.height * 2 + 8), (40, 40, 40))
                c.paste(a, (0, 0))
                c.paste(b, (0, a.height + 8))
                c.save(OUT / ("funnel_p%02d.png" % int(p * 100)))

            def finish(self):
                if "_upper_funnel_params" in self.w.__dict__:
                    del self.w.__dict__["_upper_funnel_params"]
                print("对照图 -> %s/funnel_p*.png  (上=当前 / 下=提案)" % OUT)
                self.stop()

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
