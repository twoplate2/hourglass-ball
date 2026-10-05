import numpy as np
from PIL import Image
from PIL import ImageDraw
base = "benchmark_logs/r1_mat_ab/period-5.0-time-2.550"
full = np.array(Image.open(base + ".png").convert("RGB")).astype(int)
prev = np.array(Image.open(base + "-prev.png").convert("RGB")).astype(int)
d = np.abs(full-prev).max(axis=2)
print("pixels differing:", int((d>0).sum()), "of", d.size, " max level diff:", int(d.max()),
      " p50 of diff:", float(np.percentile(d[d>0],50)) if (d>0).any() else 0,
      " p95:", float(np.percentile(d[d>0],95)) if (d>0).any() else 0)
# region: upper ball sand, lower mound
for name,(y0,y1,x0,x1) in {"upper_sand":(230,340,120,260),"lower_mound":(620,700,110,280)}.items():
    sub = d[y0:y1,x0:x1]
    print(f"  {name}: differ={int((sub>0).sum())}/{sub.size}  max={int(sub.max())}  mean={sub.mean():.2f}")
# crops side by side at 3x
x0,x1,y0,y1 = 110,290,600,700
a=Image.fromarray(full[y0:y1,x0:x1].astype('uint8')).resize(((x1-x0)*3,(y1-y0)*3), Image.NEAREST)
b=Image.fromarray(prev[y0:y1,x0:x1].astype('uint8')).resize(((x1-x0)*3,(y1-y0)*3), Image.NEAREST)
st=Image.new("RGB",(a.width*2+8,a.height+18),(20,20,20)); st.paste(a,(0,18)); st.paste(b,(a.width+8,18))
dr=ImageDraw.Draw(st); dr.text((4,3),"FULL 512x512",fill=(255,220,0)); dr.text((a.width+12,3),"PREVIEW 128x128",fill=(0,220,255))
st.save("benchmark_logs/r1_A2_mat3x.png"); print("saved r1_A2_mat3x.png")
