"""在**固定几何窗**内量落沙密度 —— 回答"粒子率的变化在画面上可不可见"。

与 `tools/neck_profile.py` 的区别(那一版被对抗性审查指出的两个毛病):
  1. 它用"该行沙像素的最左/最右"做分母 ⇒ **自归一化**: 粒子越稀, span 越窄, 比值照样 100%。
     这里改用**固定窗**的填充率, 分子分母都不随密度缩。
  2. 它用固定距离阈值 40 分类, 换黑沙会把玻璃壁判成沙。这里改用**最近邻分类**
     (到沙色族的距离 < 到玻璃/背景/描边的距离), 无阈值, 且顺带报告"判定余量"当置信度。

用法:
  python tools/neck_density.py <裁图目录> --sand 金沙 --neck-y 382.5 --outlet-y 373.7
"""

import argparse
import glob
import json
import math
import os
import re
import sys

from PIL import Image

# 与 main.py 的 SAND_PRESETS 逐字一致
SAND_PRESETS = {
    "金沙": ("#d9a360", "#b88040", "#e6b870"),
    "红沙": ("#c4523e", "#8e3220", "#d97560"),
    "蓝沙": ("#4a8ec4", "#2e5e87", "#6dabd4"),
    "绿沙": ("#7ba83e", "#4d7820", "#97c45e"),
    "紫沙": ("#8e6db0", "#5d4280", "#a98ac4"),
    "黑沙": ("#4a4540", "#2a2520", "#6a6560"),
}
GLASS_FILL = "#eaf3f8"
GLASS_LINE = "#5f6b70"
BG = "#fdf6e3"

CROP_W, CROP_H, HALF_H, SCALE = 416, 496, 62.0, 4.0


def rgb(text):
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def sand_family(name):
    """沙色族: 基色沿 base→light / base→dark 各取若干档, 覆盖颗粒纹理的全部色调。"""
    base, dark, light = (rgb(c) for c in SAND_PRESETS[name])
    return ([lerp(base, light, t / 4) for t in range(5)]
            + [lerp(base, dark, t / 4) for t in range(1, 5)])


def dist2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def classify(pixel, family, others):
    """最近邻: 返回 (是否沙, 余量=到次近类的距离-到最近类的距离)。无阈值。"""
    d_sand = min(dist2(pixel, c) for c in family)
    d_other = min(dist2(pixel, c) for c in others)
    return d_sand < d_other, math.sqrt(d_other) - math.sqrt(d_sand)


def percentile(values, q):
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil(q * len(ordered))) - 1))
    return ordered[idx]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", help="裁图目录(inspect_flow 的产物)")
    parser.add_argument("--sand", default="金沙", choices=sorted(SAND_PRESETS))
    parser.add_argument("--neck-y", type=float, default=None,
                        help="缺省读目录下 measurements.json 的 neck_outlet_y+? ")
    parser.add_argument("--outlet-y", type=float, default=None)
    parser.add_argument("--period", type=int, default=None, help="只量该周期")
    parser.add_argument("--win-below", type=float, default=10.0, help="窗上缘距出口(px)")
    parser.add_argument("--win-height", type=float, default=40.0, help="窗高(px)")
    parser.add_argument("--win-half-w", type=float, default=30.0, help="窗半宽(px)")
    parser.add_argument("--json", default=None, help="把结果写到该文件")
    args = parser.parse_args()

    meta_path = os.path.join(args.directory, "measurements.json")
    outlet = args.outlet_y
    neck_y = args.neck_y
    if os.path.isfile(meta_path):
        rec = json.load(open(meta_path, encoding="utf-8"))
        if outlet is None:
            outlet = float(rec[0]["neck_outlet_y"])
        if neck_y is None:
            # 锥形上下对称: outlet = 2*neck_y - y_bot, inlet = y_bot ⇒ 2*neck_y = outlet + inlet
            neck_y = (float(rec[0]["neck_outlet_y"]) + float(rec[0]["neck_inlet_y"])) / 2.0
    if outlet is None or neck_y is None:
        sys.exit("需要 --outlet-y/--neck-y(或目录里有 measurements.json)")

    family = sand_family(args.sand)
    others = [rgb(GLASS_FILL), rgb(GLASS_LINE), rgb(BG)]

    # 裁图纵向覆盖 neck_y ± 62 逻辑px, row 248 ↔ neck_y, 每逻辑 px 4 行, y 向下递减
    y_top = outlet - args.win_below
    y_bot = y_top - args.win_height
    anchor_row = 248 + (neck_y - outlet) * SCALE      # 出口所在行

    def row_of(y):
        return int(round(anchor_row + (outlet - y) * SCALE))

    r0, r1 = row_of(y_top), row_of(y_bot)
    # 裁图只覆盖 neck_y ± 62 逻辑px ⇒ 窗口必须落在其中, 越界就钳制并告警
    r0, r1 = max(0, min(r0, CROP_H)), max(0, min(r1, CROP_H))
    if r1 - r0 < 8:
        sys.exit(f"窗口落在裁图外(r0={r0} r1={r1}); 裁图只覆盖 y∈"
                 f"[{neck_y-62:.1f}, {neck_y+62:.1f}], 请调小 --win-below/--win-height")
    c0 = int(round(208 - args.win_half_w * SCALE))
    c1 = int(round(208 + args.win_half_w * SCALE))
    area = (r1 - r0) * (c1 - c0)

    files = sorted(glob.glob(os.path.join(args.directory, "neck-*-time-*.png")),
                   key=lambda p: (float(re.search(r"neck-([\d.]+)-time-", p).group(1)),
                                  float(re.search(r"time-([\d.]+)\.png$", p).group(1))))
    results = {}
    for path in files:
        period = int(float(re.search(r"neck-([\d.]+)-time-", path).group(1)))
        if args.period and period != args.period:
            continue
        px = Image.open(path).convert("RGB").load()
        filled, ambiguous, spans = 0, 0, []
        bottom_rows = []
        for row in range(r0, r1):
            xs = []
            for col in range(c0, c1):
                ok, margin = classify(px[col, row], family, others)
                if abs(margin) < 25:
                    ambiguous += 1
                if ok:
                    xs.append(col)
            filled += len(xs)
            bottom_rows.append(len(xs) / (c1 - c0))
            if len(xs) >= 3:
                spans.append((percentile(xs, 0.95) - percentile(xs, 0.05)) / SCALE)
        if not spans:
            continue
        # 沙堆帧: 窗口底部整行被填满 ⇒ 下沙堆已升进窗口, 不是自由射流, 丢弃
        if sum(bottom_rows[-5:]) / 5.0 > 0.95:
            continue
        results.setdefault(period, {"fill": [], "span": [], "ambig": []})
        results[period]["fill"].append(filled / area)
        results[period]["span"].append(sum(spans) / len(spans))
        results[period]["ambig"].append(ambiguous / area)

    print(f"沙色={args.sand}  固定窗 {2*args.win_half_w:.0f}x{args.win_height:.0f} 逻辑px"
          f"(出口以下 {args.win_below - args.win_height:.0f}–{args.win_below:.0f}px)")
    print(f"{'周期':>6} {'帧数':>5} {'填充率中位':>10} {'帧间σ':>8} {'p5-p95跨度':>10} {'模糊像素':>9}")
    out = {}
    for period in sorted(results):
        r = results[period]
        fill = r["fill"]
        mean = sum(fill) / len(fill)
        sigma = math.sqrt(sum((x - mean) ** 2 for x in fill) / len(fill)) if len(fill) > 1 else 0.0
        med = percentile(fill, 0.5)
        span = sum(r["span"]) / len(r["span"])
        print(f"{period:>5}s {len(fill):>5} {100*med:>9.2f}% {100*sigma:>7.2f}%"
              f" {span:>9.2f}px {100*sum(r['ambig'])/len(r['ambig']):>8.2f}%")
        out[str(period)] = {"frames": len(fill), "fill_median": med, "fill_sigma": sigma,
                            "span_px": span,
                            "ambig_frac": sum(r["ambig"]) / len(r["ambig"])}
    if args.json:
        open(args.json, "w", encoding="utf-8").write(json.dumps(out, indent=2))
        print("写出", args.json)


if __name__ == "__main__":
    main()
