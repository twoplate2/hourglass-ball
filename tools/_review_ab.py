# -*- coding: utf-8 -*-
"""改前/改后并排 + 逐像素差分(找茬回合的固定交付物)。

用法:
    python tools/_review_ab.py before.png after.png out.png [--crop x0,y0,x1,y1] [--zoom N]

输出三格(竖排): 改前 / 改后 / 差异图(只把变化的像素画出来, 其余压灰),
外加一行统计: 变化像素数、最大通道差、变化区域的包围盒。
⚠️ 两张图必须同尺寸; 不同版本号那几行像素一定会变, 用 --mask 排除。
"""
import sys
from PIL import Image, ImageChops, ImageDraw, ImageFont

FONT = r"C:\Windows\Fonts\msyh.ttc"


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    before, after, out = argv[1], argv[2], argv[3]
    crop = zoom = None
    mask = None
    i = 4
    while i < len(argv):
        if argv[i] == "--crop":
            crop = tuple(int(v) for v in argv[i + 1].split(",")); i += 2
        elif argv[i] == "--zoom":
            zoom = int(argv[i + 1]); i += 2
        elif argv[i] == "--mask":
            mask = tuple(int(v) for v in argv[i + 1].split(",")); i += 2
        else:
            print("未知参数", argv[i]); return 2

    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    if a.size != b.size:
        print("!! 尺寸不同: %s vs %s" % (a.size, b.size)); return 1
    if crop:
        a = a.crop(crop); b = b.crop(crop)
    if zoom:
        a = a.resize((a.width * zoom, a.height * zoom), Image.NEAREST)
        b = b.resize((b.width * zoom, b.height * zoom), Image.NEAREST)

    diff = ImageChops.difference(a, b)
    if mask:
        ImageDraw.Draw(diff).rectangle(
            (mask[0] * (zoom or 1), mask[1] * (zoom or 1),
             mask[2] * (zoom or 1), mask[3] * (zoom or 1)), fill=(0, 0, 0))
    bbox = diff.getbbox()
    changed = sum(1 for p in diff.getdata() if p != (0, 0, 0))
    maxd = max((max(p) for p in diff.getdata()), default=0)

    heat = Image.new("RGB", a.size, (60, 60, 60))
    hp = heat.load(); dp = diff.load()
    for y in range(a.height):
        for x in range(a.width):
            d = max(dp[x, y])
            if d:
                hp[x, y] = (min(255, 80 + d * 3), 40, 40)
    W, H = a.size
    strip = 46
    canvas = Image.new("RGB", (W, H * 3 + strip * 3 + 8), (250, 250, 250))
    d = ImageDraw.Draw(canvas)
    try:
        f = ImageFont.truetype(FONT, 22)
    except Exception:
        f = ImageFont.load_default()
    for k, (img, t) in enumerate((
            (a, "改前  %s" % before),
            (b, "改后  %s" % after),
            (heat, "差异  %d 个像素变了 (最大通道差 %d)%s"
                   % (changed, maxd, "" if bbox is None else "  包围盒 %s" % (bbox,))))):
        y = k * (H + strip)
        d.text((8, y + 10), t, fill=(0, 0, 0), font=f)
        canvas.paste(img, (0, y + strip))
        if k:
            d.line([(0, y), (W, y)], fill=(180, 180, 180), width=3)
    canvas.save(out)
    print("变化像素 %d / %d = %.3f%%   最大通道差 %d   包围盒 %s"
          % (changed, a.width * a.height, 100.0 * changed / (a.width * a.height), maxd, bbox))
    print("-> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
