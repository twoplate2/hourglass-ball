import numpy as np, sys, glob, re
from PIL import Image
tag = sys.argv[1]
rows=[]
for f in sorted(glob.glob(f"benchmark_logs/r1_{tag}/period-5.0-time-*.png"),
                 key=lambda p: float(re.search(r"time-([\d.]+)\.png",p).group(1))):
    t = float(re.search(r"time-([\d.]+)\.png",f).group(1))
    a = np.array(Image.open(f).convert("RGB")).astype(int)
    r,g,b = a[...,0],a[...,1],a[...,2]
    sand = (r>150)&(g>100)&(b<185)&((r-b)>55)
    cx = 199
    prof={}
    for dx in (0,20,40,60,80,100,120,133):
        col = sand[:, cx+dx]
        ys = np.flatnonzero(col[500:800])
        prof[dx] = (500+ys.min()) if len(ys) else None   # 沙面 image row (越小=越高)
    rows.append((t,prof))
print(f"{'t':>5} " + " ".join(f"{d:>5}" for d in (0,20,40,60,80,100,120,133)), "   (值=沙面行号, 小=高)")
for t,prof in rows:
    base = prof[0]
    cells=[]
    for d in (0,20,40,60,80,100,120,133):
        v=prof[d]
        cells.append(f"{v-base:>5}" if v is not None and base is not None else "    .")
    print(f"{t:5.2f} " + " ".join(cells))
