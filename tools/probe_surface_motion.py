# -*- coding: utf-8 -*-
"""上球沙面：**随时间连续拍一串**，看那些凹凸到底动不动。

用户 2026-10-05 反馈:「所谓沙面起伏, 目前是沙面粘合剂(完全没有起伏, 是静止不动的)」。

机制上确实如此 —— `_upper_rough_at(index, height)` 取的是 `arr[index]`,
index = **固定节点号**(固定 x)；包络只随沙量缩放 ⇒ **凹凸图案钉死在固定的 x 上**,
从满到空既不移动也不变形, 只有整体高度在降。

本工具拍一串帧并**逐帧量"图案有没有动"**:
  把每帧沙面轮廓减掉自己的中位高度(去趋势) → 得到"凹凸指纹" → 相邻帧做相关。
  相关≈1 ⇒ 图案完全没动; 明显<1 ⇒ 图案在演化。

⚠️ 顺带:**求解器 `_upper_area(level,...)` 把绝对 y 坐标当"高度"传给了 `_upper_rough_at`**,
   而绘制传的是真高度 `upper_height` ⇒ 两边的包络不一致(求解器恒为 1)。本工具一并量出来。

跑法: python tools/probe_surface_motion.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "surfmotion"
PIX = (1096, 2214)
DURATION = 60.0
# 两种模式:
#   drain  = 跨整段下漏(帧间隔几秒) —— 问"图案到底动没动"
#   smooth = 同一进度附近、帧间隔 ~0.05s —— 问"动得平不平滑"(逐帧乱跳 = 1.60/1.61 那种闪)
MODE = os.environ.get("HG_MOTION_MODE", "drain")
if MODE == "smooth":
    PROGRESS = [0.50 + k * 0.0008 for k in range(8)]     # ×60s ⇒ 每帧 ~0.048s
else:
    PROGRESS = [0.10, 0.16, 0.22, 0.30, 0.40, 0.50, 0.62, 0.75]


def main():
    with tempfile.TemporaryDirectory(prefix="surfm-") as home:
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
        OUT.mkdir(parents=True, exist_ok=True)
        bands, profs = [], []

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(PIX[0] / ratio), round(PIX[1] / ratio))
                Clock.schedule_once(self.begin, 0.6)

            def begin(self, _dt):
                self.hg = self.hourglass = self.hourglass
                w = self.hg
                w.set_duration(int(DURATION))
                w.completion_enabled = False
                if not w.running:
                    w.toggle()
                w._done_at = None
                self.k = 0
                Clock.schedule_once(self.step, 0.3)

            def step(self, _dt):
                if self.k >= len(PROGRESS):
                    return self.finish()
                p = PROGRESS[self.k]
                w = self.hg
                import random
                random.seed(23)
                t = DURATION * p
                # 从 0 一路跑到 t（保证粒子/状态自然），再把粒子清掉只留沙体
                guard = 0
                while w.elapsed + 1e-9 < t and w.running:
                    st = min(1.0 / 120.0, t - w.elapsed)
                    now[0] += st
                    w.tick(st)
                    guard += 1
                    if guard > 400000:
                        break
                w.elapsed = t
                w.particles[:] = []
                w.splashes[:] = []
                w.flares[:] = []
                w.dusts[:] = []
                w.redraw()
                Clock.schedule_once(lambda dt: self.grab(p), 0.4)

            def grab(self, p):
                w = self.hg
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (ww, hh), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                cx = w.to_window(w._cx, 0)[0]
                Ri = w._R_inner
                # 把"当前沙面"找出来：从代码模型要个大概, 再在图上精修
                lvl = w._upper_sand_bot + w._upper_level_for(w._upper_sand_height_px())
                iy = hh - w.to_window(w._cx, lvl)[1]
                band = img.crop((int(cx - Ri - 10), int(iy - 90),
                                 int(cx + Ri + 10), int(iy + 90)))
                bands.append(("上球沙面 p=%.2f" % p, band))
                # ---- 下球沙堆的斜面(用户点名: "下面沙漏中斜面中的沙子的起伏") ----
                my = hh - w.to_window(w._cx, w.get_mound_top_y())[1]
                band2 = img.crop((int(cx - Ri - 10), int(my - 40),
                                  int(cx + Ri + 10), int(my + 420)))
                bands.append(("下球斜面 p=%.2f" % p, band2))
                # 逐列量沙面上边缘
                a = np.asarray(img, dtype=np.int16)
                base = np.array(w.sand_base) * 255.0
                d = np.abs(a - base.reshape(1, 1, 3)).max(axis=2)
                is_sand = d < 90
                y0 = max(0, int(iy - 90)); y1 = min(img.height, int(iy + 90))
                prof = {}
                for x in range(max(0, int(cx - Ri + 8)), min(img.width, int(cx + Ri - 8))):
                    col = is_sand[y0:y1, x]
                    hit = np.nonzero(col)[0]
                    if hit.size < 7:
                        continue
                    gaps = np.nonzero(np.diff(hit) > 1)[0]
                    starts = np.concatenate(([0], gaps + 1))
                    for st in starts:
                        if hit.size - st >= 7 and hit[st + 6] - hit[st] == 6:
                            prof[x] = y0 + int(hit[st])
                            break
                profs.append((p, prof))
                self.k += 1
                Clock.schedule_once(self.step, 0.3)

            def finish(self):
                report(profs)
                montage(bands)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


def report(profs):
    import numpy as np
    print("\n各帧的沙面轮廓(二次去趋势后的凹凸指纹):")
    fps = []
    for p, prof in profs:
        if len(prof) < 100:
            print("  p=%.2f  列数不足 %d" % (p, len(prof)))
            continue
        xs = np.array(sorted(prof), dtype=float)
        ys = np.array([prof[x] for x in sorted(prof)], dtype=float)
        c = np.polyfit(xs, ys, 2)              # 上球是漏斗, 用二次去趋势
        r = ys - np.polyval(c, xs)
        fps.append((p, xs, r))
        print("  p=%.2f  列数 %4d  去趋势残差: RMS %.2fpx  p95 %.2fpx  max %.2fpx"
              % (p, xs.size, float(np.sqrt((r ** 2).mean())),
                 float(np.percentile(np.abs(r), 95)), float(np.abs(r).max())))
    print("\n相邻两帧的'凹凸指纹'相关系数(1.00 = 图案完全没动):")
    for i in range(len(fps) - 1):
        p0, x0, r0 = fps[i]
        p1, x1, r1 = fps[i + 1]
        lo = max(x0.min(), x1.min()); hi = min(x0.max(), x1.max())
        m0 = (x0 >= lo) & (x0 <= hi); m1 = (x1 >= lo) & (x1 <= hi)
        a = np.interp(np.linspace(lo, hi, 300), x0[m0], r0[m0])
        b = np.interp(np.linspace(lo, hi, 300), x1[m1], r1[m1])
        cc = float(np.corrcoef(a, b)[0, 1])
        print("  p=%.2f -> %.2f   r = %+.3f   %s"
              % (p0, p1, cc, "**图案没动**" if cc > 0.95 else
                 ("图案在演化" if cc < 0.8 else "略有变化")))


def montage(bands):
    from PIL import Image, ImageDraw
    pad, lab = 6, 18
    w = bands[0][1].width + pad * 2
    h = sum(b.height + lab for _t, b in bands) + pad * (len(bands) + 1)
    out = Image.new("RGB", (w, h), (28, 28, 28))
    d = ImageDraw.Draw(out)
    y = pad
    for t, b in bands:
        out.paste(b, (pad, y + lab))
        d.text((pad + 2, y + 3), t, fill=(255, 220, 120))
        y += b.height + lab
    out.save(OUT / "surface_over_time.png")
    print("\n-> %s  (%dx%d)" % (OUT / "surface_over_time.png", out.width, out.height))


if __name__ == "__main__":
    sys.exit(main())
