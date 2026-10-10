# -*- coding: utf-8 -*-
"""三版沙柱并排: v1.2 / 一周前 1.51 / 现在 —— 用户判"偏实心"用。"""
from PIL import Image, ImageDraw

SRC = [
    (r"E:\AI_Tools\other\shalou_claude\_wt_12\benchmark_logs\flow_visual_v12\period-15-time-7.50.png",
     "v1.2  (10-02)"),
    (r"E:\AI_Tools\other\shalou_claude\_wt_old\benchmark_logs\flow_visual_old151\period-15-time-7.50.png",
     "1.51  (a week ago)"),
    (r"E:\AI_Tools\other\shalou_claude\pc\apk\benchmark_logs\flow_visual_new226\period-15-time-7.50.png",
     "2.26  (now)"),
]
OUT = r"E:\AI_Tools\other\shalou_claude\pc\apk\benchmark_logs\_vid\column_3way.png"


def is_sand(c):
    return c[0] - c[2] > 40 and c[2] < 215


def narrow_rows(p):
    im = Image.open(p).convert("RGB")
    W, H = im.size
    px = im.load()
    out = []
    for y in range(int(H * 0.30), int(H * 0.80), 2):
        xs = [x for x in range(W) if is_sand(px[x, y])]
        if xs and 8 < xs[-1] - xs[0] + 1 < W * 0.20:
            out.append(y)
    return im, out, W, H


panels = []
for p, tag in SRC:
    im, rows, W, H = narrow_rows(p)
    y0, y1 = min(rows), max(rows)
    h = max(80, int((y1 - y0) * 0.62))
    crop = im.crop((W // 2 - 70, y0, W // 2 + 70, min(H, y0 + h)))
    panels.append((crop, tag, len(rows), y1 - y0))

S = 640.0 / max(1, max(c.size[1] for c, _, _, _ in panels))
panels = [(c.resize((int(c.size[0] * S), int(c.size[1] * S)), Image.LANCZOS), t, n, r)
          for c, t, n, r in panels]

Wt = sum(c.size[0] for c, _, _, _ in panels) + 20 * (len(panels) + 1)
Ht = max(c.size[1] for c, _, _, _ in panels) + 60
cv = Image.new("RGB", (Wt, Ht), (250, 248, 244))
d = ImageDraw.Draw(cv)
x = 20
for c, tag, n, r in panels:
    cv.paste(c, (x, 40))
    d.text((x, 10), tag, fill=(150, 30, 20))
    d.text((x, 24), "narrow rows=%d span=%d" % (n, r), fill=(90, 60, 30))
    x += c.size[0] + 20
cv.save(OUT)
print("saved", OUT, cv.size)
for c, tag, n, r in panels:
    print("  %-22s 窄行 %d 跨度 %d" % (tag, n, r))
