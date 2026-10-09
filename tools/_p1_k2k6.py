# -*- coding: utf-8 -*-
"""【1号】K2 指令数不变性 / K6 死层"一个像素不画" —— 各一段微实验。

跑法: python tools/_p1_k2k6.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
ROOT_OK = True

print("")
print("=== K2a: `_QuadBand(n)` 的**画布指令条数**与 n 无关？===")
from kivy.graphics import InstructionGroup, Color          # noqa: E402
import main as m                                           # noqa: E402

for n in (25, 50, 100):
    g = InstructionGroup()
    g.add(Color(1, 1, 1, 1))
    band = m._QuadBand(n, texture=None)
    g.add(band.bind)
    g.add(band.mesh)

    def count(node, acc):
        for ch in getattr(node, "children", ()):
            acc.append(type(ch).__name__)
            count(ch, acc)
    acc = []
    count(g, acc)
    print("  n=%-4d 画布指令 %d 条 %s ; 顶点浮点数 %d ; 索引 %d"
          % (n, len(acc) + 1, acc, len(band._v), len(band.mesh.indices)))

print("")
print("=== K2b: 每帧 `set_uv` 的 Python 成本(25 vs 50 条) ===")
for n in (25, 50):
    band = m._QuadBand(n, texture=None)
    pts = [0, 0, 1, 0, 1, 1, 0, 1]
    uvs = [0, 0, .5, 0, .5, .5, 0, .5]
    t0 = time.perf_counter()
    for _ in range(2000):
        for i in range(n):
            band.set_uv(i, pts, uvs)
        band.flush()
    t1 = time.perf_counter()
    print("  n=%-4d 每帧 %.1f µs (2000 帧均值)" % (n, (t1 - t0) / 2000 * 1e6))
