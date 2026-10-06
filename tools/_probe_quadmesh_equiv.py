# -*- coding: utf-8 -*-
"""**Quad 带 → 一个 Mesh** 的像素等价试验(一次性验证, 不是常驻闸门)。

## 为什么先做这个

`tools/_probe_canvas_cost.py` 的消融说明: 画布上每条指令每帧 ~1.0~1.4µs, 而
`_mound_carve/_mound_band/_upper_carve/_upper_band/_neck_quads` 一共 381 条 `Quad`;
画布普查还显示**每条带纹理的顶点指令都跟着一条自己的 `BindTexture`** ⇒ 实际是 ~762 条。
把一个带换成 **一个 `Mesh`** 应当省掉这一整族。

但 `Quad` 与 `Mesh` 在 Kivy 里是两套顶点格式(`Quad` 走 `points` + 自动 `tex_coords`;
`Mesh` 要手写 `[x, y, u, v]` 与 `indices`)。**在动手改生产代码之前**, 先在这台机器上证明:

- **A 段**: 同一组 `points`, 逐 `Quad` 与单 `Mesh` 画出来是否**逐像素相同**。
- **B 段**: **原地改一个预分配的 list 再重新赋值**是否照样生效 ——
  Kivy 的 list 型属性有可能按**同一性**短路 ⇒ 那样顶点会**冻在第一次的值上**
  (静默失效: 画面不动, 但一行报错都没有)。生产代码要用预分配 list(否则每帧要多拷
  一份 3000+ 个 float), 所以这一条必须先证。

## 跑法

    python tools/_probe_quadmesh_equiv.py

产出 `_qa/quad_vs_mesh.png`(上排 A 段 / 下排 B 段; 每排左=Quad 中=Mesh 右=差异放大)。
退出码 0 = 两段都逐像素相同。
"""
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1")

import numpy as np                                    # noqa: E402
from PIL import Image                                 # noqa: E402
from kivy.app import App                              # noqa: E402
from kivy.clock import Clock                          # noqa: E402
from kivy.core.window import Window                   # noqa: E402
from kivy.graphics import BindTexture, Color, InstructionGroup, Mesh, Quad  # noqa: E402

OUT = ROOT / "benchmark_logs" / "quadmesh_equiv"
OUT.mkdir(exist_ok=True)
# 一批四边形: 前四个模拟 carve 带(共享边、连成长条), 最后一个**故意是凹的**
# —— 两个三角剖分在凹角处会给出不同结果, 少了它这个试验就漏掉最该验的那一种。
QUADS = (
    (10, 20, 60, 26, 60, 74, 10, 70),
    (60, 26, 110, 30, 110, 78, 60, 74),
    (110, 30, 160, 24, 160, 82, 110, 78),
    (160, 24, 210, 34, 210, 70, 160, 82),
    (30, 92, 170, 96, 80, 112, 200, 118),
)
# B 段用的"挪过位置"的一组(与 A 段不同, 才能看出那次重新赋值到底有没有生效)
QUADS2 = tuple(tuple(v + 6 for v in q) for q in QUADS)
CLR = (0.2, 0.5, 0.8, 1.0)


class P(App):
    def on_start(self):
        Window.size = (240, 140)
        self.cur = None
        Clock.schedule_once(lambda d: self.step(0), 0.7)

    # ---- 通用 ----
    def clear(self):
        if self.cur is not None:
            Window.canvas.remove(self.cur)
            self.cur = None

    def draw_quads(self, quads):
        self.clear()
        g = InstructionGroup()
        g.add(Color(*CLR))
        for pts in quads:
            g.add(Quad(points=list(pts)))
        self.cur = g
        Window.canvas.add(g)

    def make_mesh_state(self):
        probe = Quad(points=[0] * 8)          # 向 Kivy 要"默认纹理 + 默认 uv", 不猜
        self.tex = probe.texture
        self.uv = list(probe.tex_coords)
        self.v = [0.0] * (len(QUADS) * 16)
        idx = []
        for i in range(len(QUADS)):
            b = i * 4
            idx += [b, b + 1, b + 2, b, b + 2, b + 3]
        self.idx = idx

    def draw_mesh(self, quads):
        self.clear()
        for i, pts in enumerate(quads):
            for k in range(4):
                o = i * 16 + k * 4
                self.v[o] = float(pts[k * 2])
                self.v[o + 1] = float(pts[k * 2 + 1])
                self.v[o + 2] = float(self.uv[k * 2])
                self.v[o + 3] = float(self.uv[k * 2 + 1])
        g = InstructionGroup()
        g.add(Color(*CLR))
        g.add(BindTexture(texture=self.tex))
        # ★ **传同一个 list 对象**(生产代码要这么做, 才能省掉每帧一次 3000+ 元素的复制)
        g.add(Mesh(mode="triangles", vertices=self.v, indices=self.idx,
                   texture=self.tex))
        self.cur = g
        Window.canvas.add(g)

    # ---- 四个阶段 ----
    def step(self, i):
        if i == 0:
            self.draw_quads(QUADS)
            Clock.schedule_once(lambda d: self.step(1), 0.7)
        elif i == 1:
            Window.screenshot(name="qa_a1.png")
            self.make_mesh_state()
            self.draw_mesh(QUADS)
            Clock.schedule_once(lambda d: self.step(2), 0.7)
        elif i == 2:
            Window.screenshot(name="qa_a2.png")
            self.draw_quads(QUADS2)
            Clock.schedule_once(lambda d: self.step(3), 0.7)
        elif i == 3:
            Window.screenshot(name="qa_b1.png")
            self.draw_mesh(QUADS2)            # 原地改 self.v 后再赋值(同一对象)
            Clock.schedule_once(lambda d: self.step(4), 0.7)
        else:
            Window.screenshot(name="qa_b2.png")
            Clock.schedule_once(lambda d: self.stop(), 0.4)


def latest(stem):
    """⚠️ Kivy 的 `screenshot(name=...)` 要求 name **带扩展名**(它按最后一个 `.` 剁开再加
    4 位序号), 且**只存到当前工作目录**, 不认路径 —— 所以截图落在仓库根, 比完就删。"""
    c = sorted(glob.glob(stem + "*.png"))
    return Path(c[-1]) if c else None


def cleanup():
    for f in glob.glob("qa_*.png"):
        try:
            os.remove(f)
        except OSError:
            pass


P().run()

pairs = (("qa_a1", "qa_a2", "A: 逐 Quad vs 单 Mesh"),
         ("qa_b1", "qa_b2", "B: 预分配 list 原地改后再赋值"))
ok = True
rows = []
for sa, sb, label in pairs:
    a, b = latest(sa), latest(sb)
    if a is None or b is None:
        print("!! 缺图:", label, a, b)
        ok = False
        continue
    ia = np.asarray(Image.open(a).convert("RGB")).astype(int)
    ib = np.asarray(Image.open(b).convert("RGB")).astype(int)
    d = np.abs(ia - ib).max(axis=2)
    n = int((d > 0).sum())
    print("%-40s 差异像素 %d / %d, 最大通道差 %d"
          % (label, n, d.size, int(d.max()) if n else 0))
    ok = ok and n == 0
    rows.append((ia, ib, d))

# 另外: B 段必须**与 A 段不同** —— 否则"没差异"只是因为 Mesh 冻在旧值上而两排凑巧相同
a1, b2 = latest("qa_a1"), latest("qa_b2")
if a1 and b2:
    moved = int((np.abs(np.asarray(Image.open(a1).convert("RGB")).astype(int)
                        - np.asarray(Image.open(b2).convert("RGB")).astype(int)
                        ).max(axis=2) > 0).sum())
    print("对照: A 段原图 vs B 段结果 差异像素 %d(应为非 0, 证明赋值真的生效了)"
          % moved)
    if moved == 0:
        print("!! B 段那张与 A 段原来那张一模一样 ⇒ Mesh **冻住了**, 原地赋值没生效")
        ok = False

if rows:
    H = max(r[0].shape[0] for r in rows)
    strip = []
    for ia, ib, d in rows:
        pad = np.zeros((H - ia.shape[0], ia.shape[1], 3), int)
        strip.append(np.concatenate([
            np.vstack([ia, pad]), np.full((H, 6, 3), 255, int),
            np.vstack([ib, pad]), np.full((H, 6, 3), 255, int),
            np.vstack([np.stack([np.clip(d * 4, 0, 255)] * 3, axis=2),
                       np.zeros((H - d.shape[0], d.shape[1], 3), int)])], axis=1))
    img = np.concatenate(strip, axis=0)
    im = Image.fromarray(img.astype(np.uint8))
    im = im.resize((im.size[0] * 3, im.size[1] * 3), Image.NEAREST)
    im.save(OUT / "quad_vs_mesh.png")
    print("并排图 ->", OUT / "quad_vs_mesh.png")
cleanup()
print("==>", "全部逐像素相同" if ok else "**有差异**")
raise SystemExit(0 if ok else 1)
