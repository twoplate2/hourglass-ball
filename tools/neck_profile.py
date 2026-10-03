"""把"颈部像一个矩形"变成可测的数。

读 tools/inspect_flow.py 产出的颈部裁图(416x496 = 104x124 逻辑区的 4 倍放大,
**裁图中心 = (cx, neck_y)**), 逐行判定像素归属, 输出三张剖面:

  - 沙宽剖面: 每行沙色像素的横向范围。**恒宽 = 矩形**; 应随镜像轮廓单调变化。
  - 内腔剖面: 每行玻璃填充(GLASS_FILL)的横向范围 = 沙可以占据的全部空间。
  - 缝隙: 内腔宽 - 沙宽(每侧)。**这正是"喇叭口里的白缝"**, 也是"矩形悬在漏斗里"的量化。
  - 色调: 相邻行平均沙色的 RGB 距离。**突变 = 那条"平直线/色调台阶"**。

用法:
  python tools/neck_profile.py benchmark_logs/flow_visual_current/neck-15-time-7.50.png
  python tools/neck_profile.py <png> --neck-y 382.5 --from 360 --to 460
"""

import argparse
import os
import sys

from PIL import Image


CROP_W, CROP_H = 416, 496          # tools/inspect_flow.py 的固定输出尺寸
HALF_H = 62.0                      # 裁图纵向覆盖 neck_y ± 62 逻辑像素
SCALE = CROP_W / 104.0             # 4.0

GLASS_FILL = (0xea, 0xf3, 0xf8)
GLASS_LINE = (0x5f, 0x6b, 0x70)
BG = (0xfd, 0xf6, 0xe3)
SAND_PRESETS = {
    "金沙": (0xd9, 0xa3, 0x60), "红沙": (0xc4, 0x52, 0x3e), "蓝沙": (0x4a, 0x8e, 0xc4),
    "绿沙": (0x7b, 0xa8, 0x3e), "紫沙": (0x8e, 0x6d, 0xb0), "黑沙": (0x4a, 0x45, 0x40),
}


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _sand_family(rgb):
    return (rgb,
            tuple(min(255, round(c * 1.20)) for c in rgb),
            tuple(round(c * 0.76) for c in rgb))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--sand", default="金沙", choices=sorted(SAND_PRESETS))
    parser.add_argument("--neck-y", type=float, default=382.5,
                        help="裁图中心对应的逻辑 y(400x800 下约 382.5)")
    parser.add_argument("--from", dest="row_from", type=float, default=None,
                        help="起始逻辑 y")
    parser.add_argument("--to", dest="row_to", type=float, default=None,
                        help="结束逻辑 y")
    parser.add_argument("--every", type=int, default=2)
    args = parser.parse_args()

    if not os.path.isfile(args.image):
        sys.exit(f"找不到 {args.image}")
    family = _sand_family(SAND_PRESETS[args.sand])
    image = Image.open(args.image).convert("RGB")
    pixels = image.load()

    def logical_y(row):
        return args.neck_y + HALF_H - row / SCALE

    def row_of(y):
        return round((args.neck_y + HALF_H - y) * SCALE)

    lo = args.row_from if args.row_from is not None else args.neck_y - HALF_H
    hi = args.row_to if args.row_to is not None else args.neck_y + HALF_H

    print(f"{os.path.basename(args.image)}  沙={args.sand}  neck_y={args.neck_y}")
    print(f"{'y':>7} {'沙[左,右]':>13} {'沙宽':>5} {'腔宽':>5} {'缝/侧':>6} {'平均色':>9} {'Δ色':>6}")
    prev = None
    total_gap = []
    for row in range(max(0, row_of(hi)), min(CROP_H, row_of(lo) + 1)):
        y = logical_y(row)
        sand_xs, sand_cols, bore_xs = [], [], []
        for col in range(CROP_W):
            value = pixels[col, row]
            d_sand = min(_dist(value, c) for c in family)
            d_glass = _dist(value, GLASS_FILL)
            if d_sand < d_glass and d_sand < _dist(value, GLASS_LINE) and d_sand < _dist(value, BG):
                sand_xs.append(col)
                sand_cols.append(value)
            elif d_glass <= _dist(value, GLASS_LINE) and d_glass < _dist(value, BG):
                bore_xs.append(col)
        if not sand_xs:
            continue
        every = args.every if row not in (row_of(hi), row_of(lo)) else 1
        if (row - row_of(hi)) % every:
            continue
        mean = tuple(sum(c[i] for c in sand_cols) / len(sand_cols) for i in range(3))
        sand_w = sand_xs[-1] - sand_xs[0] + 1
        bore_w = (bore_xs[-1] - bore_xs[0] + 1) if len(bore_xs) > 8 else 0
        gap = (bore_w - sand_w) / 2.0 if bore_w else 0.0
        if bore_w:
            total_gap.append((y, gap))
        delta = "" if prev is None else f"{_dist(mean, prev):.0f}"
        color = "#%02x%02x%02x" % tuple(int(round(c)) for c in mean)
        print(f"{y:>7.1f} {sand_xs[0]:>5},{sand_xs[-1]:<5} {sand_w:>5} {bore_w:>5}"
              f" {gap:>6.1f} {color:>9} {delta:>6}")
        prev = mean

    if total_gap:
        worst = max(total_gap, key=lambda item: item[1])
        print(f"\n最大单侧缝隙 {worst[1]:.1f}px @ y={worst[0]:.1f}"
              f"(也就是喇叭口里那块没沙的楔形)")
    print("判读: 沙宽恒定 = 矩形; 沙宽随 y 单调变化 = 跟着玻璃走。"
          "缝/侧 应≈线宽一半, 明显大于它就是裸露的玻璃。")


if __name__ == "__main__":
    main()
