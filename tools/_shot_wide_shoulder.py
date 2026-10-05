# -*- coding: utf-8 -*-
"""宽而矮的窗口里, 颈部喇叭口会不会比"肩台"窄 ⇒ 扁平肩台重现?

数值侧(工具): `tools/_probe_geom_invariants.py` 在 2560x1600 / 2400x1080 / 2000x600
三个"宽而矮"的尺寸上报 `w_out < 肩台半宽`(差 2%~16%)。
按项目文档, `w_out >= shoulder*1.06` 正是**不让"扁平肩台 + ~84° 硬折角"重现**的条件。

本工具用 `export_as_image()` **离屏渲染**(不受桌面窗口尺寸限制), 把颈部裁出来给人看。
对照组 = 手机比例(1080x2400)同样位置 —— 同一把尺子, 已知没事的那一档。
跑法: python tools/_shot_wide_shoulder.py
"""
import math
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "wide"
CASES = [(1080, 2400, "手机(对照)"), (2400, 1080, "横过来"), (2000, 600, "很扁")]


def main():
    with tempfile.TemporaryDirectory(prefix="wide-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from PIL import Image

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 50}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                OUT.mkdir(parents=True, exist_ok=True)
                crops = []
                for (W, H, tag) in CASES:
                    w.size = (W, H)
                    w._rebuild_height_table()
                    w.elapsed = 25.0
                    w.running = True
                    w.redraw()
                    tex = w.export_as_image().texture
                    img = Image.frombytes("RGBA", tex.size, tex.pixels).convert("RGB")
                    # ⚠️ 裁图必须**统一尺度**才能三张互比: 第一版按 `R*0.55` 取半宽,
                    #    而 R 随尺寸差 3.5 倍 ⇒ 三张比例尺各不相同, 肉眼没法比。
                    #    改成: 纵向取**两球截口之间**(过渡段所在), 横向取固定倍数于喇叭口半宽。
                    tp0 = w._taper
                    y_hi = w._upper_ball_cut          # 上球截口(窗口 y)
                    y_lo = w._lower_ball_cut          # 下球截口
                    span = max(1.0, y_hi - y_lo)
                    pad_y = span * 0.35
                    r_top = tex.size[1] - int(y_hi + pad_y - w.y)
                    r_bot = tex.size[1] - int(y_lo - pad_y - w.y)
                    cx = int(w._cx - w.x)
                    half = int(max(p[0] for p in tp0["out_pts"]) * 1.9)
                    x0, x1 = max(0, cx - half), min(img.width, cx + half)
                    c = img.crop((x0, max(0, r_top), x1, min(img.height, r_bot)))
                    # 统一缩放到"颈部高度 = 300px"的比例尺
                    sc = 300.0 / max(1, c.height)
                    c = c.resize((max(1, int(c.width * sc)), 300), Image.LANCZOS)
                    crops.append((tag, c))
                    tp = w._taper
                    sh = math.sqrt(max(0.0, w._R ** 2 - w._R_inner ** 2))
                    wo = max(p[0] for p in tp["out_pts"])
                    print("  %-12s %dx%d  R=%.1f  肩台=%.1f  w_out=%.1f  %s"
                          % (tag, W, H, w._R, sh, wo,
                             "**喇叭口比肩台窄 %.1f**" % (sh - wo) if wo < sh else "盖得住"))
                from PIL import ImageDraw
                pad, lab = 8, 22
                Wm = max(c.width for _t, c in crops)
                Hm = sum(c.height + lab + pad for _t, c in crops) + pad
                out = Image.new("RGB", (Wm + pad * 2, Hm), (24, 24, 24))
                d = ImageDraw.Draw(out)
                y = pad
                for tag, c in crops:
                    d.text((pad + 4, y + 5), tag, fill=(255, 214, 110))
                    out.paste(c, (pad, y + lab))
                    y += c.height + lab + pad
                p = OUT / "shoulder_wide.png"
                out.save(p)
                print("  -> %s (%dx%d)" % (p, out.width, out.height))
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
