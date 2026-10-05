"""Reviewer-2 metrics: independent pixel measurements on the EXISTING captures.

Targets: A1 (grain layer / UV band), A2 (jet band variance), A5 (surface steps +
pale wedges), A7 (button fg/bg).  No Kivy needed -- pure PIL/numpy on PNGs.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "benchmark_logs"
R1 = LOG / "_r1"

BASE = np.asarray(Image.open(R1 / "base.png").convert("RGB")).astype(int)
NOG = np.asarray(Image.open(R1 / "nograins.png").convert("RGB")).astype(int)
NOC = np.asarray(Image.open(R1 / "nocolumn.png").convert("RGB")).astype(int)
NOS = np.asarray(Image.open(R1 / "nosurface.png").convert("RGB")).astype(int)
print("shapes", BASE.shape, NOG.shape, NOC.shape, NOS.shape)

# geometry: window 400x800, image row = 800 - kivy_y  (row 0 = top)
#   outlet  kivy 373.70 -> img 426.3     inlet/y_bot kivy 391.30 -> img 408.7
#   funnel top (side[0]) kivy 410.16 -> img 389.8
#   upper_sand_bot  kivy 404.90 -> img 395.1
#   tube_lim x: cx=200 +- t_in=10.94  -> x[189.1, 210.9]


def region(img, y0, y1, x0, x1):
    return img[y0:y1, x0:x1]


def stats(a, name):
    r = a[:, :, 0]
    print("%-28s n=%5d  R mean %7.2f  std %6.2f  min %3d max %3d"
          % (name, a.shape[0] * a.shape[1], r.mean(), r.std(), r.min(), r.max()))


print("\n=== A1: tube interior (img y400..426, x190..210) base vs nograins vs nocolumn")
for nm, img in (("base", BASE), ("nograins", NOG), ("nocolumn", NOC)):
    stats(region(img, 400, 426, 190, 210), nm + " tube")
for nm, img in (("base", BASE), ("nograins", NOG), ("nocolumn", NOC)):
    stats(region(img, 408, 419, 190, 210), nm + " tube_deep")

print("\n=== A1b: funnel-top UV band (img y389..396, x190..210)")
for nm, img in (("base", BASE), ("nograins", NOG), ("nocolumn", NOC)):
    stats(region(img, 389, 396, 190, 210), nm + " band")

d = np.abs(BASE - NOG).max(axis=2)
print("base vs nograins: total diff px (any chan>0):", int((d > 0).sum()))
print("  diff in y389..396 x190..210:", int((d[389:396, 190:210] > 0).sum()))
np.save(ROOT / "benchmark_logs" / "_x2" / "d_base_nog.npy", d) if False else None
ys, xs = np.where(d > 0)
if len(ys):
    print("  diff bbox y[%d,%d] x[%d,%d]" % (ys.min(), ys.max(), xs.min(), xs.max()))
d2 = np.abs(BASE - NOC).max(axis=2)
ys2, xs2 = np.where(d2 > 0)
print("base vs nocolumn: total diff px:", int((d2 > 0).sum()),
      " bbox y[%d,%d] x[%d,%d]" % (ys2.min(), ys2.max(), xs2.min(), xs2.max()))

# column-constant structure inside the band: row-to-row |diff| vs col-to-col |diff|
def structure(a, name):
    r = a[:, :, 0].astype(float)
    row_d = np.abs(np.diff(r.mean(axis=1))).mean()   # vertical variation
    col_d = np.abs(np.diff(r.mean(axis=0))).mean()   # horizontal variation
    print("%-28s rowDetr %6.2f  colDetr %6.2f" % (name, row_d, col_d))

print("\n=== A1c: band structure (y389..396, x190..210)")
structure(region(BASE, 389, 396, 190, 210), "base band")
structure(region(NOG, 389, 396, 190, 210), "nograins band")
print("=== A1c: below band (y396..404, x190..210)")
structure(region(BASE, 396, 404, 190, 210), "base below")
structure(region(NOG, 396, 404, 190, 210), "nograins below")

print("\n=== A2: jet band rows (img y from 440 to 616 step 8, x 170..230)")
for y in range(440, 617, 8):
    row = BASE[y, 160:240]
    # "sand/warm" = R > B + 10
    warm = row[(row[:, 0] - row[:, 2]) > 10]
    if len(warm) == 0:
        print("  y=%3d  no warm px" % y)
        continue
    rr = warm[:, 0]
    adj = np.abs(np.diff(rr))
    print("  y=%3d warm n=%2d  R mean %6.1f std %5.2f  |dR|adj mean %4.1f max %3d"
          % (y, len(warm), rr.mean(), rr.std(), adj.mean() if len(adj) else 0,
             adj.max() if len(adj) else 0))

# vertical: does every 8px band have warm pixels (no gaps)?
print("\n=== A2b: vertical continuity y426..620: warm-px count per row (x160..240)")
counts = []
for y in range(426, 621):
    row = BASE[y, 160:240]
    warm = ((row[:, 0] - row[:, 2]) > 10).sum()
    counts.append(warm)
print("  rows with 0 warm px:", sum(1 for c in counts if c == 0),
      "  min", min(counts), " max", max(counts), " median", int(np.median(counts)))

print("\n=== A5: upper sand surface top row per column (x 63..337)")
# sand = warm (R>B+10) and not pale-blue
top_rows = []
for x in range(63, 338):
    col = BASE[240:280, x]
    idx = None
    for i, px in enumerate(col):
        if px[0] - px[2] > 10:          # warm sand
            idx = 240 + i
            break
    top_rows.append(idx)
tops = np.array([t if t is not None else -1 for t in top_rows])
print("  top row min %d max %d   distinct values %s"
      % (tops[tops > 0].min(), tops.max(), sorted(set(tops[tops > 0].tolist()))))
print("  counts per value:", {v: int((tops == v).sum()) for v in sorted(set(tops[tops > 0].tolist()))})

print("\n=== A5b: pale pixels along the surface (bright, not sand, not pure cavity)")
GLASS_FILL = np.array([234, 243, 248])
BG = np.array([253, 246, 227])
pale = []
for y in range(246, 272):
    for x in range(60, 340):
        px = BASE[y, x]
        if px[0] - px[2] > 10:
            continue                      # sand
        if np.abs(px - GLASS_FILL).max() <= 2:
            continue                      # cavity
        if np.abs(px - BG).max() <= 6:
            continue                      # page bg
        # must be adjacent to sand (within 2px below)
        below = BASE[y + 1:y + 3, x]
        issand = any(p[0] - p[2] > 10 for p in below)
        if issand:
            pale.append((y, x, tuple(px)))
print("  pale px count:", len(pale))
from collections import Counter
cnt = Counter(p[2] for p in pale)
print("  top colors:", cnt.most_common(8))
xs_p = sorted(set(p[1] for p in pale))
if xs_p:
    print("  x span:", xs_p[0], xs_p[-1])
    # runs
    runs = []
    start = prev = xs_p[0]
    for x in xs_p[1:]:
        if x == prev + 1:
            prev = x
        else:
            runs.append((start, prev))
            start = prev = x
    runs.append((start, prev))
    print("  runs:", runs)

print("\n=== A7: top color row (from _p2_toprow3x.png, 3x upscale)")
TOP3 = np.asarray(Image.open(LOG / "_p2_toprow3x.png").convert("RGB")).astype(int)
print("  size", TOP3.shape)
# sample a 40x40 patch inside the first cell and histogram
h, w = TOP3.shape[:2]
patch = TOP3[h // 4: h // 2, 10:60].reshape(-1, 3)
c = Counter(map(tuple, patch))
print("  gold cell patch top colors:", c.most_common(4))
