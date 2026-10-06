# -*- coding: utf-8 -*-
"""r31-2号: 裁带拼图 —— 把 N 张图的同一区域裁出来横排, 便于"看连续多帧/多档"。

用法:
  python tools/_r31b_montage.py out.png x0 y0 x1 y1 img1 img2 ...
  python tools/_r31b_montage.py out.png x0 y0 x1 y1 --in-dir DIR pat
"""
import sys
import glob
import numpy as np
from PIL import Image, ImageDraw


def main():
    out = sys.argv[1]
    x0, y0, x1, y1 = (int(v) for v in sys.argv[2:6])
    if sys.argv[6] == "--in-dir":
        d, pat = sys.argv[7], sys.argv[8]
        imgs = sorted(glob.glob(d.rstrip("/") + "/" + pat))
    else:
        imgs = sys.argv[6:]
    tiles = []
    for p in imgs:
        im = Image.open(p).convert("RGB").crop((x0, y0, x1, y1))
        canvas = Image.new("RGB", (im.width, im.height + 22), (30, 30, 30))
        canvas.paste(im, (0, 22))
        dr = ImageDraw.Draw(canvas)
        name = p.replace("\\", "/").split("/")[-1].replace(".png", "")
        dr.text((4, 5), name, fill=(255, 255, 120))
        tiles.append(canvas)
    W = sum(t.width for t in tiles) + (len(tiles) - 1) * 4
    H = max(t.height for t in tiles)
    m = Image.new("RGB", (W, H), (0, 0, 0))
    x = 0
    for t in tiles:
        m.paste(t, (x, 0))
        x += t.width + 4
    m.save(out)
    print("->", out, m.size)


main()
