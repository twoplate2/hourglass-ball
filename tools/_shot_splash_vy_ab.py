# -*- coding: utf-8 -*-
"""背景飞溅**高度轴**三档并排 —— 出给用户判的图(红线第 1 条)。

为什么是这条轴(2026-10-06 量出来的): 飞溅的瓶颈**不在分布**。三轮改动
(1.103 铺开 / 1.104 压平 / 1.109 抬高)之后实测仍是:
   · 全部飞溅**挤在沙堆轮廓线上**, 形成一道贴边的"绒毛", 没占住上方的空玻璃;
   · 平均离地只有 **10.5px**, 而堆本身有 ~100px 高;
   · 数量加到 4 倍(`_shot/splashpop/density_3way_zoom3x.png`)只是把这绒毛变密, 性质不变。

所以这里只动**初速倍率** `SPLASH_BG_VY`(同一份代码, 环境开关), 数量/分布/横向全不动。
⚠️ 倍率乘在 `rand_uniform` 之后 ⇒ 随机数流一字不动。

**图是给用户判的, 不是给我下结论的** —— 所以出两版(整球 + 坡面放大 3 倍),
并附一个"平均离地"的数**只用来解释为什么**, 不用它说"哪档好"。

跑法: python tools/_shot_splash_vy_ab.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "splashpop"
DURATION = 60.0
STEADY = 10.0          # 先排到稳态(此时堆已成形)
REBUILD = 1.6          # 每档重建种群用的时间(初速越大寿命越长, 1.6s 足够)
ARMS = (1.0, 1.6, 2.4)
ZOOM = 3


def main():
    with tempfile.TemporaryDirectory(prefix="splashvy-") as home:
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

        def shot(ww, hh):
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
                self.w = w
                self.rows = []
                self.i = 0
                Clock.schedule_once(self.arm, 0.3)

            def arm(self, _dt):
                if self.i >= len(ARMS):
                    return self.finish()
                w = self.w
                m.SPLASH_BG_VY = ARMS[self.i]
                w.splashes = []
                dt = 1.0 / 60.0
                t_end = w.elapsed + REBUILD
                while w.elapsed < t_end:
                    now[0] += dt
                    w.tick(dt)
                w.elapsed = STEADY          # 把堆钉回同一高度, 三档才可比
                w.redraw()
                Clock.schedule_once(self.grab, 0.35)

            def grab(self, _dt):
                w = self.w
                ww, hh = map(int, Window.size)
                img = shot(ww, hh)
                cx = w.to_window(w._cx, 0)[0]
                Ri = w._R_inner
                # 平均离地(只用来**解释**为什么, 不用来判好坏)
                gaps = [s["y"] - (w._lower_sand_bot + w._mound_contact_h(s["x"] - w._cx))
                        for s in w.splashes]
                self.rows.append({
                    "mult": ARMS[self.i], "img": img,
                    "n": len(w.splashes),
                    "mean_gap": (sum(gaps) / len(gaps)) if gaps else 0.0,
                    "max_gap": max(gaps) if gaps else 0.0,
                    "cx": cx, "Ri": Ri,
                })
                self.i += 1
                Clock.schedule_once(self.arm, 0.2)

            def finish(self):
                montage(self.rows)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


def montage(rows):
    from PIL import Image, ImageDraw
    print("")
    print("=== 高度轴三档 (只动 SPLASH_BG_VY, 数量/分布/横向全不动) ===")
    print("%-8s %8s %12s %12s" % ("初速x", "在途颗数", "平均离地", "最高"))
    for r in rows:
        print("%-8.1f %8d %10.1fpx %10.1fpx"
              % (r["mult"], r["n"], r["mean_gap"], r["max_gap"]))

    h = rows[0]["img"].shape[0]
    band = (int(h * 0.50), int(h * 0.95))
    cx = rows[0]["cx"]
    boxes = [
        ("vy_3way.png", lambda im: im.crop((0, band[0], im.width, band[1]))),
        ("vy_3way_zoom3x.png",
         lambda im: im.crop((int(cx - 175), band[0], int(cx - 5), band[1])).resize(
             ((170) * ZOOM, (band[1] - band[0]) * ZOOM), Image.NEAREST)),
    ]
    for tag, box in boxes:
        crops = [(u"初速 x%.1f  (%d 颗, 平均离地 %.0fpx)"
                  % (r["mult"], r["n"], r["mean_gap"]),
                  box(Image.fromarray(r["img"].astype("uint8")))) for r in rows]
        pad, lab = 8, 26
        wpx = crops[0][1].width + pad * 2
        hpx = sum(im.height + lab for _t, im in crops) + pad * (len(crops) + 1)
        out = Image.new("RGB", (wpx, hpx), (26, 26, 26))
        d = ImageDraw.Draw(out)
        y = pad
        for t, im in crops:
            out.paste(im, (pad, y + lab))
            d.text((pad + 4, y + 6), t, fill=(255, 214, 110))
            y += im.height + lab
        p2 = OUT / tag
        out.save(p2)
        print("  -> %s  (%dx%d)" % (p2, out.width, out.height))


if __name__ == "__main__":
    sys.exit(main())
