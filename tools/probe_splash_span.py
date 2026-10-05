# -*- coding: utf-8 -*-
"""下球沙面的飞溅 —— 拍连续几帧, 看它到底铺开没有。

用户 2026-10-05:「下面的沙子, 那个 ai 和你说下沙子的飞溅效果, 现在也还是没有
(目前只有沙柱和下面沙堆的尖头那一块有轻微的飞溅效果)」。

机制(代码事实): splash **只在"粒子触底那一刻"生成** —— 而沙流落在**一个点**上,
⇒ 飞溅必然只出现在落点附近。斜坡上没有任何生成源。

本工具: 排到进度 ~0.5, 然后**小步推进时钟**连拍 6 帧, 裁下球整块, 拼成时间条。
另附一个粗量: 下球内 `sand_light` 像素的**横向分布**(飞溅是 sand_light 小方块),
按离中轴的距离分箱 —— 看它是不是全挤在中心。

跑法: python tools/probe_splash_span.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "splash"
PIX = (1096, 2214)
DURATION = 60.0
AT = 0.50           # 先排到这里
STEPS = [0.0, 0.06, 0.12, 0.18, 0.24, 0.30]   # 再小步推进, 连拍


def main():
    with tempfile.TemporaryDirectory(prefix="splash-") as home:
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
        m.HourglassWidget.load_config = lambda *_: {"duration": int(DURATION)}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])
        OUT.mkdir(parents=True, exist_ok=True)
        frames = []

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(PIX[0] / ratio), round(PIX[1] / ratio))
                Clock.schedule_once(self.begin, 0.6)

            def begin(self, _dt):
                self.hg = self.hourglass
                w = self.hg
                w.set_duration(int(DURATION))
                w.completion_enabled = False
                if not w.running:
                    w.toggle()
                w._done_at = None
                import random
                random.seed(23)
                guard = 0
                target = DURATION * AT
                while w.elapsed + 1e-9 < target and w.running:
                    st = min(1.0 / 120.0, target - w.elapsed)
                    now[0] += st
                    w.tick(st)
                    guard += 1
                    if guard > 400000:
                        break
                w.elapsed = target
                self.k = 0
                Clock.schedule_once(self.step, 0.3)

            def step(self, _dt):
                if self.k >= len(STEPS):
                    return self.finish()
                w = self.hg
                dt = 0.0 if self.k == 0 else (STEPS[self.k] - STEPS[self.k - 1])
                if dt > 0:
                    now[0] += dt
                    w.tick(dt)          # ⚠️ 只推时钟, **不清粒子** —— 要看的就是飞溅
                Clock.schedule_once(lambda d: self.grab(), 0.25)

            def grab(self):
                w = self.hg
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (ww, hh), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                cx = w.to_window(w._cx, 0)[0]
                Ri = w._R_inner
                apex_y = hh - w.to_window(w._cx, w.get_mound_top_y())[1]
                crop = img.crop((int(cx - Ri - 10), int(apex_y - 60),
                                 int(cx + Ri + 10), int(apex_y + 430)))
                frames.append((STEPS[self.k], crop, img, w, hh))
                self.k += 1
                Clock.schedule_once(self.step, 0.2)

            def finish(self):
                montage(frames)
                measure(frames)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


def measure(frames):
    """下球内 sand_light 像素按"离中轴多远"分箱 —— 飞溅全挤在中心就一眼看出来。"""
    import numpy as np
    _dt, _c, img, w, hh = frames[-1]
    a = np.asarray(img, dtype=np.int16)
    light = np.array(w.sand_light) * 255.0
    cx = w.to_window(w._cx, 0)[0]
    Ri = w._R_inner
    lo_top = hh - w.to_window(w._cx, w._lower_y_c + Ri)[1]
    lo_bot = lo_top + 2 * Ri
    sub = a[int(lo_top):int(lo_bot), :, :]
    hit = np.abs(sub - light.reshape(1, 1, 3)).max(axis=2) < 46
    ys, xs = np.nonzero(hit)
    print("\n下球内 sand_light(飞溅/亮色颗粒)像素: %d 个" % xs.size)
    if xs.size == 0:
        print("  —— 一个都没有")
        return
    dist = np.abs(xs - cx)
    print("  离中轴的距离分箱 (下球内径 %.0fpx):" % (2 * Ri))
    for lo, hi in ((0, 50), (50, 100), (100, 200), (200, 300), (300, 445)):
        n = int(((dist >= lo) & (dist < hi)).sum())
        print("    %3d~%3dpx : %5d 个  %s" % (lo, hi, n, "█" * min(40, n // 5)))
    print("  ⇒ 若几乎全在 0~50px 箱里, 就说明飞溅**只堆在落点**, 斜坡上没有。")


def montage(frames):
    from PIL import Image, ImageDraw
    pad, lab = 6, 18
    w0 = frames[0][1].width
    out = Image.new("RGB", (w0 + pad * 2,
                            sum(c.height + lab for _d, c, *_r in frames) + pad * (len(frames) + 1)),
                    (28, 28, 28))
    d = ImageDraw.Draw(out)
    y = pad
    for dt, c, *_rest in frames:
        out.paste(c, (pad, y + lab))
        d.text((pad + 2, y + 3), "t + %.2fs" % dt, fill=(255, 220, 120))
        y += c.height + lab
    out.save(OUT / "splash_over_time.png")
    print("-> %s (%dx%d)" % (OUT / "splash_over_time.png", out.width, out.height))


if __name__ == "__main__":
    sys.exit(main())
