# -*- coding: utf-8 -*-
"""把 **闪光层的批处理渲染器** 与 **逐 `Color`+`Rectangle`** 放在同一个画面里对拍。

用途: 生产代码里两者是**互相替换**的, 只能用两次渲染间接比。这里**同屏各画一批**,
差异一眼可见 —— 是几何、是 alpha、还是根本没差别。

跑法: python tools/_probe_flare_equiv.py
产出: benchmark_logs/quadmesh_equiv/flare_iso.png(左 逐 Rectangle / 中 FlareBatch / 右 差异×8)
"""
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1")

import numpy as np                                            # noqa: E402
from PIL import Image                                         # noqa: E402
from kivy.app import App                                      # noqa: E402
from kivy.clock import Clock                                  # noqa: E402
from kivy.core.window import Window                           # noqa: E402
from kivy.graphics import (BindTexture, Color, InstructionGroup, Mesh,  # noqa: E402
                           Rectangle, RenderContext)
import flow_splash_experiment as fse                          # noqa: E402

BG = (0.72, 0.55, 0.28, 1.0)          # 近似沙色
SAND = (0.949, 0.804, 0.541)
FLARES = [(30.5, 40.25, 2 + 1.4, 0.8 + 0.28, 0.315),
          (60.0, 70.0, 4.0, 1.2, 0.45),
          (95.25, 55.75, 2.0, 0.8, 0.09),
          (120.0, 100.0, 3.0, 1.0, 0.225)]


def flare_rects(g):
    for x, y, w, h, a in FLARES:
        c = Color(*SAND, a)
        g.add(c)
        g.add(Rectangle(pos=(x - w / 2, y - h / 2), size=(w, h)))


def flare_batch(g):
    ctx = RenderContext(use_parent_projection=True, use_parent_modelview=True)
    ctx.shader.vs = fse.FLARE_VERTEX_SHADER
    ctx.shader.fs = fse.FRAGMENT_SHADER
    print("FLARE shader success:", ctx.shader.success)
    ctx["texel_step"] = fse.FLARE_STEP
    ctx["bounds"] = 1
    ctx.add(Color(*SAND, 1.0))
    g.add(ctx)
    b = fse.FlareBatch(ctx)
    n = len(FLARES)
    blk = np.empty((n, fse.FLARE_TEXELS), dtype="<f4")
    for i, (x, y, w, h, a) in enumerate(FLARES):
        left = x - w / 2
        bottom = y - h / 2
        blk[i, 0] = left
        blk[i, 1] = bottom
        blk[i, 2] = left + w
        blk[i, 3] = bottom + h
        blk[i, 4] = a
    b.update_raw(blk.tobytes(), n)


class P(App):
    def on_start(self):
        Window.size = (160, 130)
        Clock.schedule_once(self.a, 0.7)

    def a(self, _dt):
        g = InstructionGroup()
        g.add(Color(*BG))
        g.add(Rectangle(pos=(0, 0), size=Window.size))
        flare_rects(g)
        self.g = g
        Window.canvas.add(g)
        Clock.schedule_once(lambda d: (Window.screenshot(name="qa_fa.png"), self.b_(None)), 0.6)

    def b_(self, _dt):
        Window.canvas.remove(self.g)
        g = InstructionGroup()
        g.add(Color(*BG))
        g.add(Rectangle(pos=(0, 0), size=Window.size))
        flare_batch(g)
        self.g = g
        Window.canvas.add(g)
        Clock.schedule_once(lambda d: (Window.screenshot(name="qa_fb.png"), self.stop()), 0.6)


def latest(stem):
    c = sorted(glob.glob(stem + "*.png"))
    return Path(c[-1]) if c else None


P().run()
a, b = latest("qa_fa"), latest("qa_fb")
ia = np.asarray(Image.open(a).convert("RGB")).astype(int)
ib = np.asarray(Image.open(b).convert("RGB")).astype(int)
d = np.abs(ia - ib).max(axis=2)
ys, xs = np.nonzero(d)
print("RECT vs BATCH: 差异像素 %d / %d, 最大通道差 %d"
      % (len(xs), d.size, int(d.max()) if len(xs) else 0))
for k in range(min(8, len(xs))):
    yy, xx = int(ys[k]), int(xs[k])
    print("   (%d,%d) A=%s B=%s" % (xx, yy, tuple(ia[yy, xx]), tuple(ib[yy, xx])))
OUT = ROOT / "benchmark_logs" / "quadmesh_equiv"
OUT.mkdir(parents=True, exist_ok=True)
strip = np.concatenate([ia, np.full((ia.shape[0], 4, 3), 255, int), ib,
                        np.full((ia.shape[0], 4, 3), 255, int),
                        np.stack([np.clip(d * 8, 0, 255)] * 3, axis=2)], axis=1)
im = Image.fromarray(strip.astype(np.uint8))
im = im.resize((im.size[0] * 4, im.size[1] * 4), Image.NEAREST)
im.save(OUT / "flare_iso.png")
for f in glob.glob("qa_f*.png"):
    os.remove(f)
print("->", OUT / "flare_iso.png")
raise SystemExit(0 if len(xs) == 0 else 1)
