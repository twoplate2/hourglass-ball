import numpy as np, sys
from PIL import Image, ImageDraw
tags=sys.argv[1].split(","); label=sys.argv[2]; specs=sys.argv[3].split(";"); zz=int(sys.argv[4])
rows=[]
for sp in specs:
    per,tt,y0,y1,x0,x1 = sp.split(",")
    ims=[np.array(Image.open(f"benchmark_logs/r1_{tag}/period-{per}-time-{tt}.png")) for tag in tags]
    cs=[Image.fromarray(i[int(y0):int(y1),int(x0):int(x1)]).resize(((int(x1)-int(x0))*zz,(int(y1)-int(y0))*zz), Image.NEAREST) for i in ims]
    W=sum(c.width for c in cs)+8*(len(cs)-1)
    st=Image.new("RGB",(W,cs[0].height+18),(20,20,20)); x=0
    for c,tag in zip(cs,tags):
        st.paste(c,(x,18)); dr=ImageDraw.Draw(st); dr.text((x+4,3),f"{tag} t={tt}",fill=(255,220,0)); x+=c.width+8
    rows.append(st)
W=max(r.width for r in rows); H=sum(r.height for r in rows)
sh=Image.new("RGB",(W,H),(20,20,20)); y=0
for r in rows: sh.paste(r,(0,y)); y+=r.height
sh.save(f"benchmark_logs/r1_{label}.png"); print("saved", f"r1_{label}.png", sh.size)
