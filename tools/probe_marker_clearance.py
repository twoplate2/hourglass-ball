# -*- coding: utf-8 -*-
"""沙面起伏从 1.25px 提到 5.35px 之后, 表层标记还站得住吗？

## 为什么查这个
`SURFACE_MARKER_INSET = 4.0` / `SAND_SURFACE_BAND = 3.0` 是**按旧起伏(1.25px)调的**。
1.99 把上球起伏改成 D 档(峰值 5.35px / p95 4.20px) ⇒ 沙面在 ±5px 上下来回,
而标记只内移 4px —— **可能戳出沙面, 或者陷进 3px 亮带**(1.95/1.96 两次都是这么翻车的)。

## 口径
参考面用 **r14-1号 的 B 口径**: 从**渲染出来的像素**里逐列检出沙面上边缘,
不看代码模型 —— 因为"我以为的面"和"画出来的面"差过好几次。
标记坐标本身取 `_surface_marker_pool` 的 `line.points`(它就是画出去的那条线)。

## 内置对照(必须过, 否则本工具没有分辨力)
把起伏临时换回 A 档(0.0014)再测一遍。r14-1号 在**同一口径**下量到过
上球端点 min ≈ 3.75px / 0 个掉进 3px 带 —— 本工具应当复现同一量级。

跑法: python tools/probe_marker_clearance.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIX = (1096, 2214)
SEED = 23
DURATION = 60.0


def main():
    with tempfile.TemporaryDirectory(prefix="mkclr-") as home:
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

        grabs = {}

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(PIX[0] / ratio), round(PIX[1] / ratio))
                Clock.schedule_once(self.begin, 0.6)

            def begin(self, _dt):
                self.hg = w = self.hourglass
                w.set_duration(int(DURATION))
                w.completion_enabled = False
                if not w.running:
                    w.toggle()
                w._done_at = None
                self.cases = [(m.SURFACE_ROUGH_LEVEL_DEFAULT, 0.35),
                              (m.SURFACE_ROUGH_LEVEL_DEFAULT, 0.60),
                              ("1", 0.35)]          # 末项 = 对照(旧起伏)
                self.i = 0
                Clock.schedule_once(self.step, 0.3)

            def step(self, _dt):
                if self.i >= len(self.cases):
                    return self.finish()
                lb, p = self.cases[self.i]
                w = self.hg
                w.set_rough_level(lb)
                import random
                random.seed(SEED)
                t = DURATION * p
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
                w.splashes = []
                w.flares[:] = []
                w.dusts[:] = []
                w.redraw()
                Clock.schedule_once(lambda dt: self.grab(lb, p), 0.4)

            def grab(self, lb, p):
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (ww, hh), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                # ⚠️ **就地抄坐标**: 存 Line 对象的话, analyse 在最后才读 `ln.points`,
                #    那时已经被下一轮的 redraw 覆盖了(第一版就是这么错的: 三个用例
                #    读到同一批标记, p=0.60 那行算出 +148px 的假偏移)。
                w0 = self.hg
                neck_iy = hh - w0.to_window(w0._cx, w0._neck_y)[1]
                pts = []
                for _c, ln in list(w0._surface_marker_pool):
                    q = list(ln.points)
                    for k in range(0, len(q) - 1, 2):
                        wx, wy = w0.to_window(q[k], q[k + 1])
                        iy = hh - wy
                        if iy < neck_iy - 10:      # 颈部以上 = 上球
                            pts.append((int(round(wx)), iy))
                grabs[(lb, p)] = (img, pts, self.hg, hh)
                self.i += 1
                Clock.schedule_once(self.step, 0.3)

            def finish(self):
                for (lb, p), (img, pool, w, hh) in sorted(grabs.items()):
                    analyse(img, pool, w, hh, lb, p)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


def analyse(img, pts, w, hh, label, p):
    import numpy as np
    a = np.asarray(img, dtype=np.int16)
    base = np.array(w.sand_base) * 255.0
    cx_px = w.to_window(w._cx, 0)[0]
    Ri = w._R_inner
    band = 3.0

    # ---- B 口径: 从像素逐列检沙面上边缘 ----
    d = np.abs(a - base.reshape(1, 1, 3)).max(axis=2)
    is_sand = d < 90
    up_top = int(round(hh - w.to_window(w._cx, w._upper_y_c + Ri)[1])) + 6
    up_bot = up_top + int(2 * Ri)
    surf = {}
    for x in range(max(0, int(cx_px - 430)), min(img.width, int(cx_px + 430))):
        col = is_sand[up_top:up_bot, x]
        hit = np.nonzero(col)[0]
        if hit.size < 6:
            continue
        gaps = np.nonzero(np.diff(hit) > 1)[0]
        starts = np.concatenate(([0], gaps + 1))
        for st in starts:
            if hit.size - st >= 6 and hit[st + 5] - hit[st] == 5:
                surf[x] = up_top + int(hit[st])
                break

    if not surf or not pts:
        print("  %s p=%.2f  沙面列 %d / 标记端点 %d —— 样本不足, 跳过"
              % (label, p, len(surf), len(pts)))
        return

    sx = np.array(sorted(surf))
    sy = np.array([surf[x] for x in sx])
    depths = []
    for (x, iy) in pts:
        j = int(np.argmin(np.abs(sx - x)))
        if abs(sx[j] - x) > 6:
            continue
        depths.append(sy[j] - iy)             # 图像 y 向下 ⇒ 正 = 点在沙面**上方**
    depths = np.array(depths, dtype=float)
    if depths.size == 0:
        print("  %s p=%.2f  没有匹配上的端点" % (label, p))
        return
    # ⚠️ 符号约定(图像 y 向下): depth = 沙面y - 标记y
    #    depth < 0  ⇒ 标记在沙面**下方** = 埋在沙里(想要的)
    #    depth > 0  ⇒ 标记**浮在沙面之上**(坏的, 1.96 修的就是这个)
    inside = int((depths < 0).sum())
    in_band = int(((depths > -band) & (depths < 0)).sum())
    outside = int((depths > 0).sum())
    print("  %-3s p=%.2f  端点 %3d  埋深(正=越深): 中位 %6.2f  p05 %6.2f  min %6.2f"
          % (label, p, depths.size, -np.median(depths),
             -np.percentile(depths, 95), -depths.max()))
    print("        埋在沙里 %d / 落进 %.0fpx 亮带 %d / **浮在沙面之上 %d**"
          % (inside, band, in_band, outside))


if __name__ == "__main__":
    sys.exit(main())
