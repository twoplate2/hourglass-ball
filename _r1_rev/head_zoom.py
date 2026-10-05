import numpy as np
from PIL import Image

def load(tag, name):
    return np.array(Image.open(f"benchmark_logs/r1_{tag}/{name}").convert("RGB")).astype(int)

def sand(a):
    r,g,b = a[...,0], a[...,1], a[...,2]
    return (r>150)&(g>100)&(b<185)&((r-b)>55)

import sys
for t in sys.argv[1].split(","):
    name = f"period-5.0-time-{t}.png"
    A, B = load("feath_on", name), load("feath_off", name)
    d = np.abs(A-B).max(axis=2)
    ys,xs = np.nonzero(d)
    if len(ys):
        y0, y1 = ys.min()-6, ys.max()+7
    else:
        y0, y1 = 430, 470
    x0, x1 = 186, 214
    print(f"\n### t={t}   crop y[{y0},{y1}] x[{x0},{x1}]   (left=FEATHER_ON, right=FEATHER_OFF)")
    print("    " + "".join(str((x//10)%10) for x in range(x0,x1)))
    print("    " + "".join(str(x%10) for x in range(x0,x1)))
    for y in range(y0, y1):
        ra = "".join("#" if sand(A)[y,x] else "." for x in range(x0,x1))
        rb = "".join("#" if sand(B)[y,x] else "." for x in range(x0,x1))
        mark = "  <<" if d[y,x0:x1].any() else ""
        print(f"{y:4d} {ra} | {rb}{mark}")
