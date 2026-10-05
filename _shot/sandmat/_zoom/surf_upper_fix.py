# -*- coding: utf-8 -*-
# surf_upper: 3x3 grid, 8 cells + 1 black. Detect surface edge by "first sand run from top".
import os
import numpy as np
from PIL import Image

BASE = "E:/AI_Tools/other/shalou_claude/pc/apk/_shot/sandmat"
OUT = BASE + "/_zoom"


def load(name):
    im = Image.open(os.path.join(BASE, name)).convert("RGB")
    return np.asarray(im).astype(np.int32)


def sandmask(a):
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return (R > 170) & (G > 115) & ((R - B) > 45)


d = load("surf_upper.png")
H, W, _ = d.shape
rows = [(46, 1500), (1524, 2978), (3002, 4456)]
cols = [(8, 1462), (1470, 2924), (2932, 4386)]


def edge_profile_first(strip, x0, x1):
    Hh, Ww, _ = strip.shape
    sand = sandmask(strip)
    edge = np.full(Ww, np.nan)
    for x in range(x0, min(x1, Ww)):
        run = np.convolve(sand[:, x].astype(np.int8), np.ones(3, np.int8), "valid")
        idx = np.nonzero(run == 3)[0]
        if len(idx) == 0:
            continue
        y = idx[0] + 1
        if y < Hh - 12 and y > 10:
            edge[x] = y
    return edge


def strip_metrics(edge, tag):
    e = edge[~np.isnan(edge)]
    if len(e) < 50:
        print(tag, "too few", len(e)); return
    k = 9
    pad = np.pad(e, (k // 2, k // 2), mode="edge")
    med = np.convolve(pad, np.ones(k) / k, "valid")
    e = e[np.abs(e - med) < 22]
    rng = np.percentile(e, 97.5) - np.percentile(e, 2.5)
    k1 = 51
    pad1 = np.pad(e, (k1 // 2, k1 // 2), mode="edge")
    ma = np.convolve(pad1, np.ones(k1) / k1, "valid")
    jit = (e - ma).std()
    wave = ma.std()
    k2 = 9
    pad2 = np.pad(e, (k2 // 2, k2 // 2), mode="edge")
    ma2 = np.convolve(pad2, np.ones(k2) / k2, "valid")
    fjit = (e - ma2).std()
    print("%s n=%d p97.5-p2.5=%.2f waveStd=%.2f jitterStd(w51)=%.2f fineJit(w9)=%.2f med=%.1f" % (
        tag, len(e), rng, wave, jit, fjit, np.median(e)))


bands = []
idx = 0
for (ys, ye) in rows:
    for (xs, xe) in cols:
        cell = d[ys:ye, xs:xe]
        idx += 1
        if cell.mean() < 40:
            print("cell%d black" % idx)
            continue
        eh = edge_profile_first(cell, 560, cell.shape[1] - 60)
        strip_metrics(eh, "cell%d" % idx)
        yc = int(np.nanmedian(eh)) if np.isfinite(eh).any() else cell.shape[0] // 2
        y0 = max(0, yc - 110); y1 = min(cell.shape[0], yc + 160)
        bands.append(cell[y0:y1])

maxw = max(b.shape[1] for b in bands)
hsum = sum(b.shape[0] + 6 for b in bands)
sheet = np.zeros((hsum, maxw, 3), np.int32) + 20
yy = 0
for b in bands:
    sheet[yy:yy + b.shape[0], :b.shape[1]] = b
    yy += b.shape[0] + 6
Image.fromarray(sheet.astype(np.uint8)).save(OUT + "/surf_upper_bands.png")
print("saved surf_upper_bands.png")
