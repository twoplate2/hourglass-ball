import numpy as np, sys
from PIL import Image, ImageDraw
base="benchmark_logs/r1_"
tags=sys.argv[1].split(","); label=sys.argv[2]; times=sys.argv[3].split(",")
zz=int(sys.argv[4]); half=int(sys.argv[5]) if len(sys.argv)>5 else 90
tiles=[]
for t in times:
    per = "5.0" if len(t.split(":"))==1 else t.split(":")[0]
    tt  = t if len(t.split(":"))==1 else t.split(":")[1]
    name=f"period-{per}-time-{tt}.png"
    ims=[np.array(Image.open(f"{base}{tag}/{name}").convert("RGB")) for tag in tags]
    # mound top: find sand rows near center
    def lastrow(m):
        r,g,b=m[...,0].astype(int),m[...,1].astype(int),m[...,2].astype(int)
        mm=(r>150)&(g>100)&(b<185)&((r-b)>55)
        rows=np.flatnonzero(mm[400:799,190:210].any(axis=1))
        return 400+rows.max() if len(rows) else 700
    yb=max(lastrow(i) for i in ims)
    y0,y1=max(0,yb-half), min(800, yb+14)
    x0,x1=199-half,199+half
    crops=[Image.fromarray(i[y0:y1,x0:x1]).resize(((x1-x0)*zz,(y1-y0)*zz), Image.NEAREST) for i in ims]
    W=sum(c.width for c in crops)+8*(len(crops)-1)
    st=Image.new("RGB",(W,crops[0].height+18),(20,20,20))
    x=0; dr=None
    for c,tag in zip(crops,tags):
        st.paste(c,(x,18)); dr=ImageDraw.Draw(st)
        dr.text((x+4,3),f"{tag}  t={tt}  y[{y0},{y1}]",fill=(255,220,0)); x+=c.width+8
    tiles.append(st)
W=max(t.width for t in tiles); H=sum(t.height for t in tiles)
sh=Image.new("RGB",(W,H),(20,20,20)); y=0
for t in tiles: sh.paste(t,(0,y)); y+=t.height
sh.save(f"benchmark_logs/r1_{label}.png"); print("saved", f"r1_{label}.png", sh.size)
