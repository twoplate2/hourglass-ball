# -*- coding: utf-8 -*-
"""流量守恒**收缩下限** 0.70(现状) vs 0.50(PC v4) —— 出并排图给用户判。

背景: 这是**压在这个项目铁律上的一条偏离**（「PC v4 是唯一真理……参数不变」）:
    pc/hourglass_v4.py:1090    max(0.5, ...)
    apk/main.py(标量) / tools/flow_numpy.py   硬钳位 0.70   ← 两条路径互相一致, 一起偏离 v4
文档记着"有意/无意地偏离……**要改这个数得两边一起决定**", 但**没记原因**, 也没人问过用户。

⚠️ 为什么这个 A/B 是**受控**的: 粒子的 x 是**每帧从 `x_offset` 重算**的
   (`x = cx + x_offset * shrink + sin(...)`), **不是积分出来的** ⇒ 改常量**当帧生效**,
   两臂之间**只有这一个数不同**, 粒子、沙面、RNG 流全部同一份。

跑法: python tools/_shot_shrink_ab.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "shrink"
DURATION = 60.0
STEADY = 30.0            # 跑到中段(粒子满场)
ARMS = (("现状 0.70", 0.70), ("PC v4 的 0.50", 0.50))


def main():
    with tempfile.TemporaryDirectory(prefix="shrinkab-") as home:
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
        m.HourglassWidget.load_config = lambda *_: {"duration": int(DURATION)}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        def shot():
            ww, hh = map(int, Window.size)
            px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
            return np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"), np.int16)

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(int(DURATION))
                if not w.running:
                    w.toggle()
                w._done_at = None
                dt = 1.0 / 60.0
                while w.elapsed < STEADY:
                    now[0] += dt
                    w.tick(dt)
                w.running = False          # 冻住物理: 两臂之间**连粒子都不动**
                self.w = w
                self.panels = []
                self.i = 0
                Clock.schedule_once(self.arm, 0.3)

            def arm(self, _dt):
                if self.i >= len(ARMS):
                    return self.montage()
                label, val = ARMS[self.i]
                m.FLOW_SHRINK_MIN = val
                # ⚠️ **必须让物理跑一步** —— `x` 是在 `update_particles` 里由
                #    `x_offset × shrink` 算出来的, 只调 `redraw()` 的话常量根本不生效。
                #    第一版就是这么写出"两臂逐位相同"这个**假结论**的。
                #    `dt=0.0` ⇒ y/vy 不变、也不生成粒子, **只有 x 被重算** —— 最干净的受控。
                self.w.update_particles(0.0)
                self.w.redraw()
                Clock.schedule_once(lambda d: self.take(label, val), 0.35)

            def take(self, label, val):
                w = self.w
                img = shot()
                # 量: 落点以下那一段沙流的**横向包络半宽**（取最宽处）
                cxw = w.to_window(w._cx, 0)[0]
                base = np.array(w.sand_base) * 255.0
                light = np.array(w.sand_light) * 255.0
                lo, hi = np.minimum(base, light) - 34, np.maximum(base, light) + 34
                sand = ((img >= lo.reshape(1, 1, 3)).all(axis=2)
                        & (img <= hi.reshape(1, 1, 3)).all(axis=2))
                floor = int(img.shape[0] - 1 - w.to_window(w._cx, w.get_mound_top_y())[1])
                outlet_w = w.to_window(w._cx, 2 * w._neck_y - w._taper["y_bot"])[1]
                r_top = int(img.shape[0] - 1 - outlet_w)
                widths = []
                for r in range(max(0, r_top), min(img.shape[0], floor)):
                    idx = np.nonzero(sand[r])[0]
                    if idx.size >= 2:
                        widths.append((idx.max() - idx.min() + 1))
                wmax = max(widths) if widths else 0
                print("  %-16s  FLOW_SHRINK_MIN=%.2f  出管→落点最宽处 **%d px**"
                      % (label, val, wmax))
                self.panels.append((label, val, img, wmax))
                self.i += 1
                Clock.schedule_once(self.arm, 0.2)

            def montage(self):
                from PIL import Image, ImageDraw
                w0 = self.panels[0][2]
                h = w0.shape[0]
                band = (int(h * 0.52), int(h * 0.95))
                OUT.mkdir(parents=True, exist_ok=True)
                for tag, box, z in (("shrink_ab.png", lambda im: im.crop((0, band[0], im.width, band[1])), 1),
                                    ("shrink_ab_zoom2x.png",
                                     lambda im: im.crop((int(self.w.to_window(self.w._cx, 0)[0] - 120),
                                                         band[0],
                                                         int(self.w.to_window(self.w._cx, 0)[0] + 120),
                                                         band[1])).resize((480, (band[1]-band[0])*2),
                                                                          Image.NEAREST), 2)):
                    crops = [(u"%s（最宽 %d px）" % (lb, wm),
                              box(Image.fromarray(im.astype("uint8"))))
                             for lb, _v, im, wm in self.panels]
                    pad, lab = 8, 26
                    out = Image.new("RGB", (crops[0][1].width * len(crops) + pad * (len(crops)+1),
                                            crops[0][1].height + lab + pad*2), (26, 26, 26))
                    d = ImageDraw.Draw(out)
                    for k, (t, im) in enumerate(crops):
                        x = pad + k * (im.width + pad)
                        out.paste(im, (x, pad + lab))
                        d.text((x + 4, pad + 6), t, fill=(255, 214, 110))
                    p = OUT / tag
                    out.save(p)
                    print("  -> %s  (%dx%d)" % (p, out.width, out.height))
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
