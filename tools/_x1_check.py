# Cross-round measurement script v2 (panelist 1). Read-only; crops to benchmark_logs/_z1.
import os
import numpy as np
from PIL import Image

BASE = r"E:\AI_Tools\other\shalou_claude\pc\apk\benchmark_logs"
OUT = os.path.join(BASE, "_z1")
os.makedirs(OUT, exist_ok=True)

BG = np.array([253, 246, 227], np.float32)
GL = np.array([234, 243, 248], np.float32)
OL = np.array([95, 107, 112], np.float32)


def load(rel):
    return np.asarray(Image.open(os.path.join(BASE, rel)).convert("RGB")).astype(np.float32)


def classify(f):
    d_bg = np.abs(f - BG).sum(-1)
    d_gl = np.abs(f - GL).sum(-1)
    d_ol = np.abs(f - OL).sum(-1)
    warm = f[..., 0] - f[..., 2]
    cls = np.zeros(f.shape[:2], np.int8)
    cls[d_gl < 60] = 1
    cls[d_bg < 60] = 2
    cls[d_ol < 120] = 3
    cls[(warm > 60) & (d_bg > 90)] = 4
    return cls


def stats(win, name):
    lum = 0.299 * win[..., 0] + 0.587 * win[..., 1] + 0.114 * win[..., 2]
    hps = (lum - lum.mean(axis=1, keepdims=True)).std()
    vps = (lum - lum.mean(axis=0, keepdims=True)).std()
    print("%-22s mean=(%.1f,%.1f,%.1f) raw_sd=%.2f hp_h=%.2f hp_v=%.2f" % (
        name, win[..., 0].mean(), win[..., 1].mean(), win[..., 2].mean(),
        lum.std(), hps, vps))


def save_crop(f, box, scale, name):
    im = Image.fromarray(f[box[1]:box[3], box[0]:box[2]].astype(np.uint8))
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    im.save(os.path.join(OUT, name))
    print("saved", name, im.size)


f = load(r"flow_visual_shape3\period-5.0-time-3.11.png")
H, W, _ = f.shape
cls = classify(f)
print("=== texture stats on 3.11 ===")
stats(f[300:360, 188:212], "ball y300-360 x188-212")
stats(f[330:384, 185:215], "ball y330-384 (p2 win)")
stats(f[412:470, 193:207], "band y412-470 x193-207")
stats(f[480:555, 193:207], "band y480-555 x193-207")
stats(f[590:650, 160:240], "mound y590-650")
stats(f[275:295, 140:260], "surface y275-295")

print("=== per-row horizontal sd inside sand (x60..340, rows 285..580) ===")
for y in range(285, 581, 5):
    m = cls[y, 60:340] == 4
    if m.sum() > 10:
        win = f[y, 60:340][m]
        lum = 0.299 * win[:, 0] + 0.587 * win[:, 1] + 0.114 * win[:, 2]
        print("  y=%3d n=%3d sd=%5.2f mean=%6.1f" % (y, int(m.sum()), lum.std(), lum.mean()))

print("=== vertical cut x=150 (mound surface band), y 570..600 ===")
for y in range(570, 601):
    r, g, b = f[y, 150]
    print("   y=%3d  (%3d,%3d,%3d)" % (y, r, g, b))

print("=== vertical cut x=200 (impact), y 570..600 ===")
for y in range(570, 601):
    r, g, b = f[y, 200]
    print("   y=%3d  (%3d,%3d,%3d)" % (y, r, g, b))

save_crop(f, (140, 390, 260, 580), 4, "x1_neck_jet.png")
save_crop(f, (100, 260, 300, 320), 4, "x1_upper_surface.png")
save_crop(f, (60, 540, 340, 620), 4, "x1_mound_zone.png")

m = load(r"_AB_glass_hl_full.png")
Hm, Wm, _ = m.shape
panels = [(0, 400), (408, 808), (816, 1216)]
labels = ["OFF", "x1.0", "x2.2"]
print("=== montage alignment (panel0 vs others) ===")
base = m[:, 0:400]
for (x0, x1), lab in zip(panels[1:], labels[1:]):
    pan = m[:, x0:x1]
    best = None
    for dx in range(-8, 9):
        for dy in range(-3, 4):
            a = base[200:780]
            b = pan[200 + dy:780 + dy]
            if dx >= 0:
                aa = a[:, 0:400 - dx]
                bb = b[:, dx:400]
            else:
                aa = a[:, -dx:400]
                bb = b[:, 0:400 + dx]
            d = np.abs(aa - bb).mean()
            if best is None or d < best[0]:
                best = (d, dx, dy)
    d0 = np.abs(base[200:780] - pan[200:780]).mean()
    print("  OFF vs %s: best (mad=%.3f dx=%d dy=%d) | dx=0 mad=%.3f" % (lab, best[0], best[1], best[2], d0))

print("=== glass highlight cross-sections (darkest run at row y, per panel) ===")
for yy in (250, 300, 360):
    for (x0, x1), lab in zip(panels, labels):
        row = m[yy, x0:x0 + 200]
        s = row.sum(1)
        j = int(np.argmin(s))
        print("  y=%d %-4s darkest at relx=%3d rgb=%s  run=%s" % (
            yy, lab, j, row[j].astype(int),
            row[max(0, j - 2):j + 3].astype(int).tolist()))
