"""Reviewer-2 metrics #2: crops provenance, mound profile, glass-hl partitions, menu."""
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "benchmark_logs"
R1 = LOG / "_r1"

for name in ("z_surf_step7x.png", "z_surf_centre7x.png", "z_moundL6x.png",
             "z_neck_1to1.png", "z_upsurf5x.png", "_p2_A5_1to1_vs_6x.png"):
    p = R1 / name if (R1 / name).exists() else LOG / name
    if p.exists():
        im = Image.open(p)
        print("%-24s %s %s" % (name, im.size, im.mode))
    else:
        print(name, "MISSING")

# ---- the surface crop: measure its pale slivers
im = np.asarray(Image.open(R1 / "z_surf_step7x.png").convert("RGB")).astype(int)
h, w = im.shape[:2]
print("\nz_surf_step7x: %dx%d" % (w, h))
cnt = Counter(map(tuple, im.reshape(-1, 3)))
print("distinct colors:", len(cnt))
print("top colors:", cnt.most_common(12))

# find rows: classify each pixel
def classify(px):
    r, g, b = px
    if abs(r - 234) <= 2 and abs(g - 243) <= 2 and abs(b - 248) <= 2:
        return "cav"
    if r - b > 40:
        return "sand"
    if r - b > 10:
        return "warm"
    return "?"

for y in range(0, h, max(1, h // 24)):
    row = im[y]
    s = "".join({"cav": "C", "sand": "S", "warm": "w", "?": "."}[classify(px)]
                for px in row[:: max(1, w // 120)])
    print("y=%3d %s" % (y, s))

# ---- base.png mound top row per column (A4)
BASE = np.asarray(Image.open(R1 / "base.png").convert("RGB")).astype(int)
tops = []
for x in range(60, 341):
    col = BASE[580:640, x]
    idx = None
    for i, px in enumerate(col):
        if px[0] - px[2] > 10:
            idx = 580 + i
            break
    tops.append((x, idx))
vals = [t for _x, t in tops if t is not None]
print("\nmound top row: distinct", sorted(set(vals)))
print("counts:", {v: vals.count(v) for v in sorted(set(vals))})
missing = [x for x, t in tops if t is None]
print("columns with no warm px:", missing[:10], "..." if len(missing) > 10 else "")

# mound top row *including* the surface-band pixels (brighter than sand)
tops2 = []
for x in range(60, 341):
    col = BASE[576:640, x]
    idx = None
    for i, px in enumerate(col):
        r, g, b = px
        if (r - b) > 10 and not (abs(r - 234) <= 3 and abs(g - 243) <= 3 and abs(b - 248) <= 3):
            idx = 576 + i
            break
    tops2.append((x, idx))
vals2 = [t for _x, t in tops2 if t is not None]
print("mound top (warm) distinct", sorted(set(vals2)),
      {v: vals2.count(v) for v in sorted(set(vals2))})

# ---- glass-hl full panel layout
g = Image.open(LOG / "_AB_glass_hl_full.png")
print("\n_AB_glass_hl_full: %s %s" % (g.size, g.mode))
