# -*- coding: utf-8 -*-
"""收缩饱和分支(外部评审 §4.2)的**数值对照** —— 不许口头说"等价"。

原式:  v = sqrt(v0² + 2gb);  target = max(m, sqrt(v0/v))
新式:  b >= b_sat + GUARD ⇒ target = m; 否则走原式
       b_sat = v0²(m⁻⁴ - 1) / (2g)

扫描: below_tube(含阈值两侧 + 恰好阈值) × g_abs × source_speed × FLOW_SHRINK_MIN,
逐点比较两式, 报**最大绝对差**与落在缓冲带内的点数。

跑法: python tools/_probe_shrink_equiv.py
"""
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
sys.argv = [sys.argv[0], "_shrink"]
import main as m                                             # noqa: E402

GUARD = 1.0


def orig(b, v0sq, g, mn):
    v = math.sqrt(v0sq + 2.0 * g * b)
    t = math.sqrt(math.sqrt(v0sq) / v)
    return mn if t <= mn else t


def new(b, v0sq, g, mn):
    m4 = mn ** -4.0
    b_sat = v0sq * (m4 - 1.0) / (2.0 * g)
    if b >= b_sat + GUARD:
        return mn
    return orig(b, v0sq, g, mn)


worst = 0.0
worst_at = None
n = 0
band = 0
for scale in (1.0, 2.0, 6.44):
    v0 = 60.0 * scale
    v0sq = v0 * v0
    for g in (450.0 * scale * scale,):
        for mn in (0.50, 0.60, 0.70, 0.75, 0.80, 0.90):
            m4 = mn ** -4.0
            b_sat = v0sq * (m4 - 1.0) / (2.0 * g)
            # 阈值两侧 + 恰好阈值 + 一大片范围内的点
            pts = [b_sat - 5.0, b_sat - 1.5, b_sat - GUARD - 1e-9, b_sat - GUARD,
                   b_sat - 1e-12, b_sat, b_sat + 1e-12, b_sat + GUARD,
                   b_sat + 1.0 + 1e-9, b_sat + 5.0, 0.0, 1.0, 39.9, 40.0, 40.1]
            pts += [b_sat * f for f in (0.25, 0.5, 0.9, 1.1, 1.5, 2.0, 10.0)]
            for b in pts:
                if b < 0.0:
                    continue
                a0, a1 = orig(b, v0sq, g, mn), new(b, v0sq, g, mn)
                d = abs(a0 - a1)
                n += 1
                if abs(b - b_sat) <= GUARD:
                    band += 1
                if d > worst:
                    worst, worst_at = d, (scale, g, mn, b, b_sat, a0, a1)

print("")
print("  === 收缩饱和分支: 数值对照 ===")
print("  对照点 %d 个(其中 %d 个落在 ±%.1f px 缓冲带内)" % (n, band, GUARD))
print("  **最大绝对差 = %.3e**" % worst)
if worst_at:
    print("  最差点: scale=%.3g g=%.3g m=%.2f b=%.9g b_sat=%.9g  原式=%.12g 新式=%.12g"
          % worst_at)
tol = 1e-15
print("  判据(最大绝对差 <= %g): %s" % (tol, "**通过**" if worst <= tol else "**不通过**"))
print("")
