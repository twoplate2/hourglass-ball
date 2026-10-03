"""颈部 A/B: 把两次 inspect_flow 的裁图并排、放大、叠差, 并算出"宽度剖面"。

现有工具的问题(本文件就是为了补它):
  neck_profile.py / neck_density.py 只 **print 数字**, 人看不到; inspect_flow.py 出单张图,
  但没有任何步骤把"改前"和"改后"摆在一起给人看。评审的终点因此停在统计量上。

本工具做三件它们都没做的事:
  1. 并排 (side-by-side) 同一时刻的两张颈部裁图, 贴到同一张 PNG, 单独存一张 diff。
  2. 直接测**轮廓形状**: 逐行按最近邻分类取沙的左/右边缘, 输出宽度剖面。
     这才是"像不像真沙漏"能落到数上的那一列 —— 真沙漏的射流边缘随 y 单调收/扩,
     任何**沿 y 的相干摆动**(1.24 的 JET_EDGE)都会在二阶差分上冒出来。
  3. 复算评审当初用的指标(去趋势残差) —— 让"残差↑"和"剖面非单调"同时摆在眼前。

用法:
  python tools/neck_ab.py <before_dir> <after_dir> --period 15 --time 7.50
  python tools/neck_ab.py A B --period 15 --time 7.50 --out benchmark_logs/_neck_ab.png
"""

import argparse
import os
import sys

from PIL import Image

CROP_W, CROP_H, HALF_H, SCALE = 416, 496, 62.0, 4.0
GLASS_FILL = "#eaf3f8"
GLASS_LINE = "#5f6b70"
BG = "#fdf6e3"
SAND_PRESETS = {
    "金沙": ("#d9a360", "#b88040", "#e6b870"),
    "红沙": ("#c4523e", "#8e3220", "#d97560"),
    "蓝沙": ("#4a8ec4", "#2e5e87", "#6dabd4"),
    "绿沙": ("#7ba83e", "#4d7820", "#97c45e"),
    "紫沙": ("#8e6db0", "#5d4280", "#a98ac4"),
    "黑沙": ("#4a4540", "#2a2520", "#6a6560"),
}


def rgb(text):
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def sand_family(name):
    base, dark, light = (rgb(c) for c in SAND_PRESETS[name])
    return ([lerp(base, light, t / 4) for t in range(5)]
            + [lerp(base, dark, t / 4) for t in range(1, 5)])


def dist2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def load_edges(path, family, others):
    """逐行取沙的左/右边缘(裁图像素列)。返回 {row: (left, right)}。"""
    px = Image.open(path).convert("RGB").load()
    edges = {}
    for row in range(CROP_H):
        xs = [col for col in range(CROP_W)
              if min(dist2(px[col, row], c) for c in family)
              < min(dist2(px[col, row], c) for c in others)]
        if len(xs) >= 3:
            edges[row] = (xs[0], xs[-1])
    return edges


def detrended_residual(profile):
    """复算 1.24 用的指标: 宽度序列减去线性趋势后的 RMS('去趋势残差')。"""
    n = len(profile)
    if n < 4:
        return float("nan")
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(profile) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, profile)) / denom
    inter = my - slope * mx
    return (sum((y - (slope * x + inter)) ** 2
                for x, y in zip(xs, profile)) / n) ** 0.5


def reversals(profile):
    """单调性: 相邻差变号(拐点)的次数占行数的比例。真沙漏收/扩应≈0。"""
    if len(profile) < 3:
        return 0.0
    signs = []
    for a, b in zip(profile, profile[1:]):
        if b > a:
            signs.append(1)
        elif b < a:
            signs.append(-1)
    turns = sum(1 for a, b in zip(signs, signs[1:]) if a * b < 0)
    return turns / max(1, len(profile))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--period", default="15")
    ap.add_argument("--time", default="7.50")
    ap.add_argument("--sand", default="金沙", choices=sorted(SAND_PRESETS))
    ap.add_argument("--out", default=None)
    ap.add_argument("--zoom", type=int, default=3)
    ap.add_argument("--row-from", type=int, default=None,
                    help="剖面起始像素行(缺省: 出口行, 见 --outlet-y)")
    ap.add_argument("--row-to", type=int, default=None,
                    help="剖面结束像素行(缺省: 出口以下 45 逻辑px)")
    ap.add_argument("--outlet-y", type=float, default=None,
                    help="出口逻辑 y(缺省读 before 目录的 measurements.json)")
    args = ap.parse_args()

    # 缺省只在"出口以下"量轮廓: 真沙漏的形状问题就长在那根射流上,
    # 计入整条上喇叭口会把坡度淹没掉(整列去趋势残差 ~90px, 什么都看不出来)。
    import json
    outlet, inlet = args.outlet_y, None
    meta = os.path.join(args.before, "measurements.json")
    if os.path.isfile(meta):
        rec = json.load(open(meta, encoding="utf-8"))[0]
        if outlet is None:
            outlet = float(rec["neck_outlet_y"])
        inlet = float(rec["neck_inlet_y"])
    if outlet is None:
        outlet, inlet = 373.7, 391.3
    neck_y = (outlet + inlet) / 2.0
    anchor_row = 248 + (neck_y - outlet) * SCALE        # 出口所在像素行
    row_from = args.row_from if args.row_from is not None else int(round(anchor_row))
    row_to = args.row_to if args.row_to is not None else row_from + int(45 * SCALE)

    name = f"neck-{args.period}-time-{args.time}.png"
    bp, apth = os.path.join(args.before, name), os.path.join(args.after, name)
    for p in (bp, apth):
        if not os.path.isfile(p):
            sys.exit(f"缺文件: {p}")
    bimg = Image.open(bp).convert("RGB")
    aimg = Image.open(apth).convert("RGB")

    family = sand_family(args.sand)
    others = [rgb(GLASS_FILL), rgb(GLASS_LINE), rgb(BG)]
    be, ae = load_edges(bp, family, others), load_edges(apth, family, others)

    rows = sorted(set(be) & set(ae))
    rows = [r for r in rows if row_from <= r <= row_to]
    bw = [be[r][1] - be[r][0] + 1 for r in rows]
    aw = [ae[r][1] - ae[r][0] + 1 for r in rows]
    print(f"{name}  沙={args.sand}  剖面像素行=[{row_from},{row_to}] 有效行={len(rows)}"
          f"  (出口逻辑 y={outlet}, 1 逻辑px={SCALE:.0f} 像素行)")
    print(f"{'':>10}{'宽度中位':>10}{'去趋势残差':>12}{'拐点占比':>10}")
    for tag, prof in (("改前", bw), ("改后", aw)):
        med = sorted(prof)[len(prof) // 2]
        print(f"{tag:>10}{med:>10}{detrended_residual(prof):>12.3f}"
              f"{reversals(prof):>10.3f}")

    # ---- 并排 + 叠差, 存成一张人能看的 PNG ----
    z = args.zoom
    bz = bimg.resize((CROP_W * z, CROP_H * z), Image.Resampling.NEAREST)
    az = aimg.resize((CROP_W * z, CROP_H * z), Image.Resampling.NEAREST)
    diff = Image.new("RGB", bimg.size)
    dp, dq = diff.load(), None
    bpx, apx = bimg.load(), aimg.load()
    for y in range(CROP_H):
        for x in range(CROP_W):
            d = sum(abs(bpx[x, y][i] - apx[x, y][i]) for i in range(3))
            v = min(255, d)
            dp[x, y] = (255, 255 - v, 255 - v) if v else (255, 255, 255)
    gap = 12
    canvas = Image.new("RGB", (CROP_W * z * 3 + gap * 2, CROP_H * z), (255, 255, 255))
    canvas.paste(bz, (0, 0))
    canvas.paste(az, (CROP_W * z + gap, 0))
    canvas.paste(diff.resize((CROP_W * z, CROP_H * z), Image.Resampling.NEAREST),
                 (CROP_W * z * 2 + gap * 2, 0))
    out = args.out or os.path.join(os.path.dirname(args.after),
                                   f"_neck_ab-{args.period}-{args.time}.png")
    canvas.save(out)
    print(f"\n并排图(左=改前 中=改后 右=叠差): {out}")
    print("看图方式: 中图射流边缘若随 y 一鼓一瘪('串珠'/'绳结'), 真沙漏不该有;"
          "右图应只在边缘一条线有红, 若整条射流都红 = 整体变宽/变窄, 是包络改了不是抖动。")


if __name__ == "__main__":
    main()
