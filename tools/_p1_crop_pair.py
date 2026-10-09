# -*- coding: utf-8 -*-
"""裁图并排: 用法 python tools/_p1_crop_pair.py A.png B.png x0 y0 x1 y1 out.png [scale]
坐标是**图像像素**(左上为原点)。"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

a_p, b_p, x0, y0, x1, y1, out = sys.argv[1:8]
scale = float(sys.argv[8]) if len(sys.argv) > 8 else 1.0
a, b = Image.open(a_p).convert("RGB"), Image.open(b_p).convert("RGB")
box = (int(x0), int(y0), int(x1), int(y1))
ca, cb = a.crop(box), b.crop(box)
w, h = ca.size
W, H = int(w * scale), int(h * scale)
ca = ca.resize((W, H), Image.NEAREST)
cb = cb.resize((W, H), Image.NEAREST)
pad = 8
canvas = Image.new("RGB", (W * 2 + pad, H), (255, 0, 0))
canvas.paste(ca, (0, 0))
canvas.paste(cb, (W + pad, 0))
d = ImageDraw.Draw(canvas)
d.text((4, 4), "A " + Path(a_p).name, fill=(255, 0, 0))
d.text((W + pad + 4, 4), "B " + Path(b_p).name, fill=(255, 0, 0))
canvas.save(out)
print("saved", out, canvas.size)
