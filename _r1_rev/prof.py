import numpy as np
from PIL import Image

def sand_mask(a):
    r,g,b = a[...,0].astype(int), a[...,1].astype(int), a[...,2].astype(int)
    return (r>150)&(g>100)&(b<185)&((r-b)>55)

for name,ylim in [("flow_visual_flat/period-5.0-time-0.50.png",(380,520)),
                  ("flow_visual_flat/period-5.0-time-4.60.png",(380,520))]:
    a = np.array(Image.open("../benchmark_logs/"+name).convert("RGB"))
    m = sand_mask(a)
    print("==", name)
    for y in range(*ylim):
        row = m[y, 160:240]
        xs = np.flatnonzero(row)
        if xs.size:
            # contiguous runs
            runs = []
            s = xs[0]; p = xs[0]
            for x in xs[1:]:
                if x == p+1: p = x
                else: runs.append((s+160,p+160)); s = x; p = x
            runs.append((s+160,p+160))
            print(y, "n=%3d"%xs.size, runs[:6])
        else:
            print(y, "n=  0")
