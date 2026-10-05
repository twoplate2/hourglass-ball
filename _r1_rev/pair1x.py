import numpy as np, sys
from PIL import Image, ImageDraw

def load(tag, name):
    return np.array(Image.open(f"benchmark_logs/r1_{tag}/{name}").convert("RGB"))

ontag, offtag, label = sys.argv[1], sys.argv[2], sys.argv[3]
scale = float(sys.argv[4]); times = sys.argv[5].split(",")
x0,x1 = (150,240) if len(sys.argv)<7 else tuple(map(int,sys.argv[6].split(",")))
tiles=[]
for t in times:
    name = f"period-5.0-time-{t}.png"
    a, b = load(ontag,name), load(offtag,name)
    # bottom of stream
    def mask(m):
        r,g,bl = m[...,0].astype(int), m[...,1].astype(int), m[...,2].astype(int)
        return (r>150)&(g>100)&(bl<185)&((r-bl)>55)
    ma, mb = mask(a), mask(b)
    def lastrow(m):
        rows = np.flatnonzero(m[380:700, x0:x1].any(axis=1))
        return 380 + rows.max()
    yb = max(lastrow(ma), lastrow(mb))
    y0, y1 = max(0,yb-46), min(800, yb+8)
    ca = Image.fromarray(a[y0:y1, x0:x1]); cb = Image.fromarray(b[y0:y1, x0:x1])
    w = int((x1-x0)*scale); h = int((y1-y0)*scale)
    ca = ca.resize((w,h), Image.NEAREST); cb = cb.resize((w,h), Image.NEAREST)
    st = Image.new("RGB",(w*2+6, h+16),(25,25,25)); st.paste(ca,(0,16)); st.paste(cb,(w+6,16))
    dr = ImageDraw.Draw(st)
    dr.text((3,3), f"FEATHER_ON  t={t}  y[{y0},{y1}]", fill=(255,220,0))
    dr.text((w+9,3), "FEATHER_OFF (pre-1.65)", fill=(0,220,255))
    tiles.append(st)
W = max(t.width for t in tiles); H = sum(t.height for t in tiles)
sheet = Image.new("RGB",(W,H),(25,25,25)); y=0
for t in tiles: sheet.paste(t,(0,y)); y+=t.height
sheet.save(f"benchmark_logs/r1_{label}.png"); print("saved", f"benchmark_logs/r1_{label}.png", sheet.size)
