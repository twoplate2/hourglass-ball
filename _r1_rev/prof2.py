import numpy as np, glob, re
from PIL import Image
def topprof(tag, t):
    f = f"benchmark_logs/r1_{tag}/period-5.0-time-{t:.3f}.png"
    a = np.array(Image.open(f).convert("RGB")).astype(int)
    r,g,b = a[...,0],a[...,1],a[...,2]
    sand = (r>150)&(g>100)&(b<185)&((r-b)>55)
    out={}
    for dx in range(-140,141,10):
        col = sand[430:800, 199+dx]
        ys = np.flatnonzero(col)
        out[dx] = 430+ys.min() if len(ys) else None
    return out
ts=[2.20,2.60,3.00,3.60,4.00,4.40]
print("沙面顶行(相对该帧 dx=+40 处), 每 10px 一列; 正=比 dx40 更低")
for t in ts:
    on, off = topprof("lip_seq",t), topprof("lip_seq_off",t)
    ref_on, ref_off = on[40], off[40]
    print(f"\n-- t={t}  (基准 dx=40 的绝对行: on={ref_on} off={ref_off})")
    hdr = "  dx  : " + " ".join(f"{d:>5}" for d in range(40,141,10))
    print(hdr)
    print("  ON  : " + " ".join((f"{on[d]-ref_on:>5}" if on[d] is not None and ref_on is not None else "    .") for d in range(40,141,10)))
    print("  OFF : " + " ".join((f"{off[d]-ref_off:>5}" if off[d] is not None and ref_off is not None else "    .") for d in range(40,141,10)))
    print("  ON-OFF(裙边抬升): " + " ".join((f"{off[d]-on[d]:>5}" if (on[d] is not None and off[d] is not None) else "    .") for d in range(40,141,10)))
