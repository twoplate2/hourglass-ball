import numpy as np, sys
from PIL import Image, ImageDraw
tags=sys.argv[1].split(","); label=sys.argv[2]; t=sys.argv[3]
bands=[[int(v) for v in b.split("-")] for b in sys.argv[4].split(",")]  # y0-y1 pairs
zz=int(sys.argv[5]); x0,x1=182,218
rows=[]
for y0,y1 in bands:
    ims=[np.array(Image.open(f"benchmark_logs/r1_{tag}/period-5.0-time-{t}.png")) for tag in tags]
    cs=[Image.fromarray(i[y0:y1,x0:x1]).resize(((x1-x0)*zz,(y1-y0)*zz), Image.NEAREST) for i in ims]
    W=sum(c.width for c in cs)+8*(len(cs)-1)
    st=Image.new("RGB",(W,cs[0].height+18),(20,20,20))
    x=0
    for c,tag in zip(cs,tags):
        st.paste(c,(x,18)); dr=ImageDraw.Draw(st); dr.text((x+4,3),f"{tag} y[{y0},{y1}]",fill=(255,220,0)); x+=c.width+8
    rows.append(st)
W=max(r.width for r in rows); H=sum(r.height for r in rows)
sh=Image.new("RGB",(W,H),(20,20,20)); y=0
for r in rows: sh.paste(r,(0,y)); y+=r.height
sh.save(f"benchmark_logs/r1_{label}.png"); print("saved", f"r1_{label}.png", sh.size)
