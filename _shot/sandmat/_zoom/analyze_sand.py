# -*- coding: utf-8 -*-
# Zoom/crop + objective metrics from the four allowed montages only.
import os
import numpy as np
from PIL import Image

BASE = "E:/AI_Tools/other/shalou_claude/pc/apk/_shot/sandmat"
OUT = BASE + "/_zoom"
os.makedirs(OUT, exist_ok=True)


def load(name):
    im = Image.open(os.path.join(BASE, name)).convert("RGB")
    return np.asarray(im).astype(np.int32)


def sandmask(a):
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return (R > 170) & (G > 115) & ((R - B) > 45)


def gaussblur(arr, sigma):
    a = arr.astype(np.float64)
    F = np.fft.fft2(a)
    fy = np.fft.fftfreq(a.shape[0])[:, None]
    fx = np.fft.fftfreq(a.shape[1])[None, :]
    g = np.exp(-2 * (np.pi ** 2) * (sigma ** 2) * (fx ** 2 + fy ** 2))
    return np.real(np.fft.ifft2(F * g))


def autocorr_radius(hp, maxr=14):
    n0, n1 = hp.shape
    F = np.fft.fft2(hp - hp.mean(), s=(2 * n0, 2 * n1))
    ac = np.real(np.fft.ifft2(np.abs(F) ** 2))
    ac = np.fft.fftshift(ac)
    yy, xx = np.mgrid[0:2 * n0, 0:2 * n1]
    rr = np.hypot(yy - n0, xx - n1)
    prof = []
    for r in range(0, maxr + 1):
        m = (rr >= r - 0.5) & (rr < r + 0.5)
        prof.append(ac[m].mean() if m.sum() else np.nan)
    prof = np.array(prof)
    if not np.isfinite(prof[0]) or prof[0] == 0:
        return -1, -1
    prof = prof / prof[0]
    r_half = r_tenth = -1
    for i in range(1, len(prof)):
        if r_half < 0 and prof[i] < 0.5:
            r_half = i
        if r_tenth < 0 and prof[i] < 0.1:
            r_tenth = i
    return r_half, r_tenth


def dark_bands(a, axis, thresh=45):
    prof = a.mean(axis=(1, 2)) if axis == 0 else a.mean(axis=(0, 2))
    dark = prof < thresh
    bands = []
    i = 0
    while i < len(dark):
        if dark[i]:
            j = i
            while j < len(dark) and dark[j]:
                j += 1
            bands.append((i, j))
            i = j
        else:
            i += 1
    return bands


def region_metrics(a, tag, region=None, want_grad=True):
    if region is not None:
        x0, y0, x1, y1 = region
        a = a[y0:y1, x0:x1]
    m = sandmask(a)
    ys, xs = np.nonzero(m)
    if len(ys) == 0:
        print(tag, "no sand")
        return None
    bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
    L = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    cy, cx = (by0 + by1) // 2, (bx0 + bx1) // 2
    res = {}
    if want_grad:
        rows = []
        for y in range(by0, by1 + 1):
            xsm = np.nonzero(m[y])[0]
            if len(xsm) < 0.4 * (bx1 - bx0):
                continue
            rows.append((y, L[y, xsm].mean()))
        if rows:
            ys2 = np.array([r[0] for r in rows], float)
            vs = np.array([r[1] for r in rows], float)
            sl = np.polyfit(ys2, vs, 1)
            n = len(rows)
            t = max(1, n // 3)
            res["dL_over_ball"] = sl[0] * (by1 - by0)
            res["meanL"] = vs.mean()
            res["grad_pct"] = 100 * res["dL_over_ball"] / res["meanL"]
            res["dTop_minus_Bot"] = vs[:t].mean() - vs[-t:].mean()
        # quadrants
        ymid, xmid = (by0 + by1) // 2, (bx0 + bx1) // 2
        for qn, (qy0, qy1, qx0, qx1) in {
            "TL": (by0, ymid, bx0, xmid), "TR": (by0, ymid, xmid, bx1),
            "BL": (ymid, by1, bx0, xmid), "BR": (ymid, by1, xmid, bx1)}.items():
            qm = m[qy0:qy1, qx0:qx1]
            if qm.sum() > 100:
                res["q" + qn] = round(float(L[qy0:qy1, qx0:qx1][qm].mean()), 1)
    side = int(min(bx1 - bx0, by1 - by0) * 0.40)
    xs0, ys0 = cx - side // 2, cy - side // 2
    sub = L[ys0:ys0 + side, xs0:xs0 + side]
    sm = m[ys0:ys0 + side, xs0:xs0 + side]
    if sm.mean() > 0.995:
        hp = sub - gaussblur(sub, 1.5)
        cp = sub - gaussblur(sub, 4.0)
        r5, r1 = autocorr_radius(hp[6:-6, 6:-6])
        res["fine_std"] = round(float(hp.std()), 2)
        res["coarse_std"] = round(float(cp.std()), 2)
        res["ratio"] = round(float(cp.std() / hp.std()), 2) if hp.std() else -1
        res["autocorr_r50"] = r5
        res["autocorr_r10"] = r1
        res["meanRGB"] = tuple(int(v) for v in a[ys0:ys0 + side, xs0:xs0 + side][sm].mean(axis=0))
    print(tag, res)
    return res


# ---------------- 1) sandmat_full : 6 full-state balls ----------------
print("=" * 30, "sandmat_full.png")
a = load("sandmat_full.png")
H, W, _ = a.shape
print("size", W, H)
print("dark row bands", dark_bands(a, 0))
print("dark col bands", dark_bands(a, 1))
cw, ch = W / 3, H / 4
labels6 = ["A_current", "B_grad_mid", "C_grad_strong", "D_grain2", "E_grain3", "F_grain2_grad"]
for r in range(2):  # rows 0,1 = full
    for c in range(3):
        reg = (int(c * cw), int(r * ch), int((c + 1) * cw), int((r + 1) * ch))
        region_metrics(a, "full[%s]" % labels6[r * 3 + c], region=reg)

# ---------------- 2) sandmat_upper_1to1 : texture detail ----------------
print("=" * 30, "sandmat_upper_1to1.png")
b = load("sandmat_upper_1to1.png")
H2, W2, _ = b.shape
print("size", W2, H2)
print("dark row bands", dark_bands(b, 0))
print("dark col bands", dark_bands(b, 1))
cellw, cellh = W2 / 3, H2 / 4
# texture sheet: rows 0..1 (full state), center 470 crop, 1.8x nearest
tiles = []
for r in range(2):
    rowt = []
    for c in range(3):
        cx = int((c + 0.5) * cellw)
        cy = int((r + 0.5) * cellh)
        cell = b[cy - 235:cy + 235, cx - 235:cx + 235]
        region_metrics(b, "crop1to1[%s]" % labels6[r * 3 + c],
                       region=(cx - 235, cy - 235, cx + 235, cy + 235), want_grad=True)
        im = Image.fromarray(cell.astype(np.uint8)).resize((846, 846), Image.NEAREST)
        rowt.append(np.asarray(im).astype(np.int32))
    tiles.append(rowt)
gap = 8
sheet = np.zeros((2 * 846 + gap, 3 * 846 + 2 * gap, 3), np.int32) + 20
for r in range(2):
    for c in range(3):
        sheet[r * (846 + gap):r * (846 + gap) + 846, c * (846 + gap):c * (846 + gap) + 846] = tiles[r][c]
Image.fromarray(sheet.astype(np.uint8)).save(OUT + "/sandmat_tex_1p8x.png")

# singles at 2x for the most contested
for name, (r, c) in {"A_current": (0, 0), "D_grain2": (1, 0), "E_grain3": (1, 1), "B_grad_mid": (0, 1)}.items():
    cx = int((c + 0.5) * cellw); cy = int((r + 0.5) * cellh)
    cell = b[max(0, cy - 295):cy + 295, max(0, cx - 285):cx + 285]
    im = Image.fromarray(cell.astype(np.uint8)).resize((cell.shape[1] * 2, cell.shape[0] * 2), Image.NEAREST)
    im.save(OUT + "/single_%s_2x.png" % name)

# ---------------- 3) surf_surface_1to1 : edge relief ----------------
print("=" * 30, "surf_surface_1to1.png")


def edge_profile(strip):
    Hh, Ww, _ = strip.shape
    R, G, B = strip[:, :, 0], strip[:, :, 1], strip[:, :, 2]
    sky = (B > 200) & (B >= R)
    sand = sandmask(strip)
    edge = np.full(Ww, np.nan)
    for x in range(Ww):
        if sky[:6, x].mean() < 0.6:
            continue
        run = np.convolve(sand[:, x].astype(np.int8), np.ones(3, np.int8), "valid")
        idx = np.nonzero(run == 3)[0]
        if len(idx) == 0:
            continue
        y = idx[0] + 1
        if y < Hh - 12:
            edge[x] = y
    return edge


def strip_metrics(edge, tag):
    e = edge[~np.isnan(edge)]
    if len(e) < 50:
        print(tag, "too few cols", len(e)); return None, None
    # drop outliers vs median filter
    k = 9
    pad = np.pad(e, (k // 2, k // 2), mode="edge")
    med = np.convolve(pad, np.ones(k) / k, "valid")
    keep = np.abs(e - med) < 22
    e = e[keep]
    if len(e) < 50:
        print(tag, "too few after clean", len(e)); return None, None
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
    print("%s n=%d p97.5-p2.5=%.2f  waveStd(w51)=%.2f  jitterStd(w51)=%.2f  fineJit(w9)=%.2f  med=%.1f" % (
        tag, len(e), rng, wave, jit, fjit, np.median(e)))
    return e, (rng, wave, jit, fjit)


c = load("surf_surface_1to1.png")
H3, W3, _ = c.shape
print("size", W3, H3)
rb = dark_bands(c, 0, thresh=60)
print("dark row bands", rb)
# build content strips from dark bands
strips = []
prev = 0
for (s, t) in rb:
    if s - prev > 100:
        strips.append((prev, s))
    prev = t
if H3 - prev > 100:
    strips.append((prev, H3))
print("content strips", [(s, t, t - s) for (s, t) in strips])
slabels = ["s035|A_cur_14", "s035|B_r25", "s035|C_r40", "s035|D_r60",
           "s060|A_cur_14", "s060|B_r25", "s060|C_r40", "s060|D_r60"]
edges = []
edge_metrics = []
for i, (s, t) in enumerate(strips):
    strip = c[s:t]
    e = edge_profile(strip)
    tag = slabels[i] if i < len(slabels) else "row%d" % i
    _, met = strip_metrics(e, tag)
    edges.append(e)
    edge_metrics.append(met)

# stacked native bands
bands = []
for i, (s, t) in enumerate(strips):
    strip = c[s:t]
    e = edges[i]
    yc = int(np.nanmedian(e)) if np.isfinite(e).any() else strip.shape[0] // 2
    y0 = max(0, yc - 70); y1 = min(strip.shape[0], yc + 100)
    band = strip[y0:y1]
    if i < len(slabels):
        # white 3px line at top of band as index marker handled by order only
        pass
    bands.append(band)
maxw = max(b.shape[1] for b in bands)
toppad = 4
hsum = sum(b.shape[0] + toppad for b in bands)
sheet = np.zeros((hsum, maxw, 3), np.int32) + 20
yy = 0
for bnd in bands:
    sheet[yy:yy + bnd.shape[0], :bnd.shape[1]] = bnd
    yy += bnd.shape[0] + toppad
Image.fromarray(sheet.astype(np.uint8)).save(OUT + "/surf_bands_native.png")

# 2.5x zoom of the four s035 edge bands (left-center 520 px window)
z = []
for i in range(4):
    strip = c[strips[i][0]:strips[i][1]]
    e = edges[i]
    yc = int(np.nanmedian(e))
    x0 = 210; x1 = 730
    y0 = max(0, yc - 46); y1 = min(strip.shape[0], yc + 74)
    band = strip[y0:y1, x0:x1]
    im = Image.fromarray(band.astype(np.uint8)).resize(((x1 - x0) * 5 // 2, (y1 - y0) * 5 // 2), Image.NEAREST)
    z.append(np.asarray(im).astype(np.int32))
maxw = max(t.shape[1] for t in z)
hsum = sum(t.shape[0] + 6 for t in z)
sheet = np.zeros((hsum, maxw, 3), np.int32) + 20
yy = 0
for t in z:
    sheet[yy:yy + t.shape[0], :t.shape[1]] = t
    yy += t.shape[0] + 6
Image.fromarray(sheet.astype(np.uint8)).save(OUT + "/surf_zoom_AtoD_s035.png")

# ---------------- 4) surf_upper : 1.6x closeup ----------------
print("=" * 30, "surf_upper.png")
d = load("surf_upper.png")
H4, W4, _ = d.shape
print("size", W4, H4)
rb4 = dark_bands(d, 0, thresh=45)
cb4 = dark_bands(d, 1, thresh=45)
print("dark row bands", rb4)
print("dark col bands", cb4)


def content_segments(bands, total, minlen=100):
    segs = []
    prev = 0
    for (s, t) in bands:
        if s - prev > minlen:
            segs.append((prev, s))
        prev = t
    if total - prev > minlen:
        segs.append((prev, total))
    return segs


rs = content_segments(rb4, H4)
cs = content_segments(cb4, W4)
print("row segs", rs, "col segs", cs)
cells = []
for (ys, ye) in rs:
    for (xs, xe) in cs:
        cell = d[ys:ye, xs:xe]
        cells.append((xs, ys, cell))
print("cell count", len(cells))
ubands = []
for ii, (xs, ys, cell) in enumerate(cells):
    if cell.mean() < 40:
        print("cell", ii, "is black")
        continue
    e = edge_profile(cell)
    met = strip_metrics(e, "upper_cell%d(size %dx%d)" % (ii, cell.shape[1], cell.shape[0]))
    yc = int(np.nanmedian(e)) if np.isfinite(e).any() else cell.shape[0] // 2
    y0 = max(0, yc - 100); y1 = min(cell.shape[0], yc + 145)
    ubands.append(cell[y0:y1])
# stacked native
maxw = max(b.shape[1] for b in ubands)
hsum = sum(b.shape[0] + 6 for b in ubands)
sheet = np.zeros((hsum, maxw, 3), np.int32) + 20
yy = 0
for bnd in ubands:
    sheet[yy:yy + bnd.shape[0], :bnd.shape[1]] = bnd
    yy += bnd.shape[0] + 6
Image.fromarray(sheet.astype(np.uint8)).save(OUT + "/surf_upper_bands.png")
# label strips 2.5x
lab = []
for ii, (xs, ys, cell) in enumerate(cells):
    l = cell[0:44, 0:520]
    im = Image.fromarray(l.astype(np.uint8)).resize((1300, 110), Image.LANCZOS)
    lab.append(np.asarray(im).astype(np.int32))
hsum = sum(t.shape[0] + 6 for t in lab)
sheet = np.zeros((hsum, 1300, 3), np.int32) + 20
yy = 0
for t in lab:
    sheet[yy:yy + t.shape[0]] = t
    yy += t.shape[0] + 6
Image.fromarray(sheet.astype(np.uint8)).save(OUT + "/surf_upper_labels.png")
print("done. outputs in", OUT)
