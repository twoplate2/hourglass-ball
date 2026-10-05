"""Probe (reviewer #2): is there any 8-60px *structure* in the sand material,
and what does a 3:1 vertical stretch actually act on?  Also: alpha arithmetic
for the A9 band on GLASS_FILL.

Run: python tools/_probe_material_spectrum.py
"""
import sys
import numpy as np
sys.path.insert(0, '.')
import main

def detrended(lum):
    x = np.linspace(-1, 1, lum.shape[0]); X, Y = np.meshgrid(x, x)
    A = np.stack([np.ones_like(X), X, Y, X * X], -1).reshape(-1, 4)
    coef, *_ = np.linalg.lstsq(A, lum.reshape(-1), rcond=None)
    return lum - (A @ coef).reshape(lum.shape)

def main_():
    for nm, hexes in (("金沙", ('#d9a360', '#b88040', '#e6b870')),
                      ("蓝沙", ('#4a8ec4', '#2e5e87', '#6dabd4'))):
        rgb = main._sand_material_rgba(512, *[main.hex_rgb(h) for h in hexes], grain=0.35)
        a = np.frombuffer(rgb, np.uint8).reshape(512, 512, 4).astype(np.float32)
        lum = a[..., :3].mean(axis=2)
        r = detrended(lum)
        print(f"== {nm} == total std {lum.std():.2f}  after removing macro x/y/x^2 fit: {r.std():.2f}")
        k = np.abs(np.fft.fftshift(np.fft.fftfreq(512)) * 512)
        for axis, anm in ((0, 'along-Y (vertical)'), (1, 'along-X (horizontal)')):
            F = np.fft.fftshift(np.abs(np.fft.fft(r, axis=axis)) ** 2, axes=axis).mean(axis=1 - axis)
            tot = F.sum()
            bands = []
            for lo, hi, lab in ((8, 16, '8-16px'), (4, 8, '4-8px'), (2, 4, '2-4px')):
                m = (k >= 512 / hi) & (k < 512 / lo)
                bands.append(f"{lab}:{100 * F[m].sum() / tot:.1f}%")
            print("   ", anm, " ".join(bands))
        st = np.repeat(r[::3], 3, axis=0)     # naive 3:1 vertical stretch of the noise
        print(f"    3:1 stretch -> |d/dx| {np.abs(np.diff(r,axis=1)).mean():.2f} -> {np.abs(np.diff(st,axis=1)).mean():.2f}"
              f" ; |d/dy| {np.abs(np.diff(r,axis=0)).mean():.2f} -> {np.abs(np.diff(st,axis=0)).mean():.2f}")
    print()
    GF = np.array([234., 243., 248.])          # GLASS_FILL
    for nm, t in (("GLASS_HL_TINT(237,247,255)", np.array([237., 247., 255.])),
                  ("cooler(230,242,255)", np.array([230., 242., 255.])),
                  ("pure white", np.array([255., 255., 255.]))):
        for al in (0.06, 0.32, 0.55):
            d = al * t + (1 - al) * GF - GF
            print(f"A9 {nm:26s} alpha={al:.2f} -> dRGB {d.round(2)}  max|d|={np.abs(d).max():.2f}")

if __name__ == '__main__':
    main_()
