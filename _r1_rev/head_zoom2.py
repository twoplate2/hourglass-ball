import numpy as np, sys
from PIL import Image

def load(tag, name):
    return np.array(Image.open(f"benchmark_logs/r1_{tag}/{name}").convert("RGB")).astype(int)

def sand(a):
    r,g,b = a[...,0], a[...,1], a[...,2]
    return (r>150)&(g>100)&(b<185)&((r-b)>55)

ontag, offtag = sys.argv[1], sys.argv[2]
for t in sys.argv[3].split(","):
    name = f"period-5.0-time-{t}.png"
    A, B = load(ontag, name), load(offtag, name)
    sa, sb = sand(A), sand(B)
    # find lowest sand row in the stream column band
    band = slice(186, 214)
    ya = np.flatnonzero(sa[:, band].any(axis=1))
    yb = np.flatnonzero(sb[:, band].any(axis=1))
    ylo = max(ya.max(), yb.max())
    y0, y1 = ylo-26, ylo+3
    print(f"\n### t={t}  lowest-sand-row ON={ya.max()} OFF={yb.max()}  (crop y[{y0},{y1}])")
    print("    " + "".join(str((x//10)%10) for x in range(186,214)))
    print("    " + "".join(str(x%10) for x in range(186,214)))
    for y in range(y0, y1+1):
        ra = "".join("#" if sa[y,x] else "." for x in range(186,214))
        rb = "".join("#" if sb[y,x] else "." for x in range(186,214))
        mark = "  <<" if (sa[y,186:214]!=sb[y,186:214]).any() else ""
        print(f"{y:4d} {ra} | {rb}{mark}")
