import sys, numpy as np
from PIL import Image
from pathlib import Path

A = Path("benchmark_logs/r1_"+sys.argv[1]); B = Path("benchmark_logs/r1_"+sys.argv[2])
label = sys.argv[3]
scale = int(sys.argv[4]) if len(sys.argv)>4 else 6
pad = int(sys.argv[5]) if len(sys.argv)>5 else 26
files = [f for f in sorted(A.glob("period-*.png"))]
tiles = []
for fa in files:
    a = np.array(Image.open(fa).convert("RGB"))
    b = np.array(Image.open(B / fa.name).convert("RGB"))
    d = np.abs(a.astype(int)-b.astype(int)).max(axis=2)
    ys, xs = np.nonzero(d)
    if not len(ys):
        cy = 460
    else:
        cy = (ys.min()+ys.max())//2
    cx0, cx1 = 190-pad, 190+pad
    y0, y1 = max(0,cy-pad), min(a.shape[0], cy+pad)
    ca = Image.fromarray(a[y0:y1, cx0:cx1]).resize(((cx1-cx0)*scale,(y1-y0)*scale), Image.NEAREST)
    cb = Image.fromarray(b[y0:y1, cx0:cx1]).resize(((cx1-cx0)*scale,(y1-y0)*scale), Image.NEAREST)
    strip = Image.new("RGB", (ca.width*2+8, ca.height+18), (20,20,20))
    strip.paste(ca, (0,18)); strip.paste(cb, (ca.width+8,18))
    from PIL import ImageDraw
    dr = ImageDraw.Draw(strip)
    dr.text((4,3), f"A={sys.argv[1]}  {fa.stem}  y={cy}", fill=(255,255,0))
    dr.text((ca.width+12,3), f"B={sys.argv[2]}", fill=(0,255,255))
    tiles.append(strip)
wmax = max(t.width for t in tiles); hsum = sum(t.height for t in tiles)
sheet = Image.new("RGB", (wmax, hsum), (20,20,20))
y = 0
for t in tiles:
    sheet.paste(t, (0,y)); y += t.height
sheet.save(f"benchmark_logs/r1_{label}.png")
print("saved", f"benchmark_logs/r1_{label}.png", sheet.size)
