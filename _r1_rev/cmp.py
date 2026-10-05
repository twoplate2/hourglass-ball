import sys, numpy as np
from PIL import Image
from pathlib import Path

A = Path("benchmark_logs/r1_"+sys.argv[1]); B = Path("benchmark_logs/r1_"+sys.argv[2])
for fa in sorted(A.glob("period-*.png")):
    fb = B / fa.name
    a = np.array(Image.open(fa).convert("RGB")).astype(int)
    b = np.array(Image.open(fb).convert("RGB")).astype(int)
    d = np.abs(a-b).max(axis=2)
    n = int((d>0).sum())
    print(f"{fa.name:32s} diffpx={n:7d}  max={int(d.max()):3d}  meandiff={d.mean():.4f}")
    if n:
        ys, xs = np.nonzero(d)
        print(f"     bbox x[{xs.min()},{xs.max()}] y[{ys.min()},{ys.max()}]")
