# -*- coding: utf-8 -*-
"""横屏弹窗遮罩**逐列覆盖**：哪几列没被压暗(「带」有多宽)。

r32-1号 记过一个侧写、r34-1号 给了近方窗的读数(左右各 21px)，但**真 16:9 横屏几何
(2400x1080)** 一直没测到(设备上 `wm size 1920x1080` 会被 MuMu 转成竖逻辑屏)。
桌面能做 —— 本探针就补这个缺口。

判据(最直白): 同一列在「无弹窗」与「有弹窗」两张图里的**平均亮度之比**。
  被压暗 ≈ **0.30**（`overlay_color` 默认 alpha 0.7）; **未压暗 ≈ 1.00**。
  ⇒ 逐列算比值, 报出**连续 ratio > 0.9 的列区间**就是"带"。
⚠️ 空窗基线要与弹窗图**同几何、同滚动位置**，且取**弹窗盖不到的横条**(弹窗在竖直方向居中,
   所以取最上/最下若干行最稳)。

跑法: python tools/_probe_land_overlay.py 的兄弟 —— python tools/_probe_overlay_band.py [宽] [高]
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_shot" / "band"


def main():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    w = int(sys.argv[1]) if len(sys.argv) > 2 else 2400
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 1080
    # ⚠️ Kivy 解析器遇到第一个非 "-" 参数就停; 先塞位置参数再放 --landscape
    sys.argv = [sys.argv[0], "_band", "--landscape"]
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

    def shot():
        ww, hh = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
        return np.asarray(Image.frombytes("RGBA", (ww, hh), px)
                          .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")).astype(float)

    res = {}

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (w, h)
            Clock.schedule_once(self.s1, 1.4)

        def s1(self, _dt):
            res["got"] = (int(Window.width), int(Window.height))
            res["angle"] = m._land_layer().angle if m._land_layer() else None
            res["base"] = shot()
            OUT.mkdir(parents=True, exist_ok=True)
            Image.fromarray(res["base"].astype("uint8")).save(OUT / ("no_popup_%dx%d.png" % (w, h)))
            Clock.schedule_once(self.s2, 0.4)

        def s2(self, _dt):
            self.on_duration_picker(None)
            Clock.schedule_once(self.s3, 1.0)

        def s3(self, _dt):
            res["pop"] = shot()
            Image.fromarray(res["pop"].astype("uint8")).save(OUT / ("popup_%dx%d.png" % (w, h)))
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return res, (w, h)


if __name__ == "__main__":
    r, (w, h) = main()
    print("")
    print("  === 逐列覆盖 %dx%d ===" % r.get("got", (w, h)))
    print("  旋转角 = %s" % r.get("angle"))
    if "base" not in r or "pop" not in r:
        print("  !! 没抓到图")
        raise SystemExit(1)
    import numpy as np
    a, b = r["base"], r["pop"]
    hh, ww, _ = a.shape
    # 只用**弹窗盖不到的横条**: 取最上 6% 与最下 6% 的行(弹窗竖直居中, 上/下边缘是纯遮罩)
    band = np.concatenate([a[:max(1, int(hh * 0.06))], a[-max(1, int(hh * 0.06)):]], axis=0)
    bandp = np.concatenate([b[:max(1, int(hh * 0.06))], b[-max(1, int(hh * 0.06)):]], axis=0)
    lum_a = band.mean(axis=(0, 2))
    lum_b = bandp.mean(axis=(0, 2))
    ratio = np.where(lum_a > 1, lum_b / np.maximum(lum_a, 1e-6), 1.0)
    undim = ratio > 0.9
    runs, start = [], None
    for x in range(ww):
        if undim[x] and start is None:
            start = x
        elif not undim[x] and start is not None:
            runs.append((start, x - 1)); start = None
    if start is not None:
        runs.append((start, ww - 1))
    print("  被压暗的列比值中位 = %.3f （期望 ~0.30）" % np.median(ratio[~undim]) if undim.any()
          else "  全部被压暗（比值中位 %.3f）" % np.median(ratio))
    if runs:
        print("  **未压暗的列区间**：")
        for x0, x1 in runs:
            print("     x %4d ~ %4d   宽 %4d px" % (x0, x1, x1 - x0 + 1))
    else:
        print("  **没有未压暗的列** —— 整幅盖满")
    print("  图: _shot/band/{no_popup,popup}_%dx%d.png" % (w, h))
