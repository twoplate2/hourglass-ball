# -*- coding: utf-8 -*-
"""把同一渲染集的两份裁图**左右并排** —— 改前/改后**给人看**的唯一正确形式。

## 为什么必须补这个工具

`NECK_REDESIGN.md` 事故报告点名过: 「**全程没有一步把改前/改后并排给人看**……
全仓库改前无任何 `montage/side_by_side/hstack`」。于是"改得对不对"只能由**统计量**裁决,
而统计量对"加抖动"是单调递增的 —— 加多少涨多少, 永远说不出"太多了",
最后把出口以下做成一串肠节, 用户是**出货之后**才被叫去评估的。

⇒ 凡改动**看得见的东西**(粒子密度、色调档数、几何), 都要先出这张图。
统计量只用来事后解释"为什么", 不许当"好不好"的判据。

## 用法

    python tools/_side_by_side.py <标签A> <标签B> <输出png> [放大=2] [周期过滤=15]

两个标签是 `tools/inspect_flow.py --label <标签>` 产出的目录名
(`benchmark_logs/flow_visual_<标签>/`)。**两边必须跑同一套参数**(周期/帧数/尺寸),
否则配不上帧 —— 脚本按**文件名**配对, 配不上的会报出来而不是静默跳过。
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def load(label, period_filter):
    d = ROOT / "benchmark_logs" / ("flow_visual_" + label)
    if not d.is_dir():
        raise SystemExit("找不到 %s —— 先跑 inspect_flow.py --label %s" % (d, label))
    out = {}
    for p in sorted(d.glob("*.png")):
        if period_filter and ("-%s-time-" % period_filter) not in p.name:
            continue
        out[p.name] = p
    return out


def run():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    a_lab, b_lab, out_png = sys.argv[1], sys.argv[2], sys.argv[3]
    scale = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    period = sys.argv[5] if len(sys.argv) > 5 else "15"

    a, b = load(a_lab, period), load(b_lab, period)
    names = sorted(set(a) & set(b))
    if not names:
        raise SystemExit("两个标签**没有一张配得上的图**(周期过滤=%s) —— "
                         "检查两边是不是同一套参数" % period)
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))

    pad, head = 6, 18
    tiles = []
    for name in names:
        ia = Image.open(a[name]).convert("RGB")
        ib = Image.open(b[name]).convert("RGB")
        if ia.size != ib.size:
            raise SystemExit("%s 两边尺寸不同: %s vs %s" % (name, ia.size, ib.size))
        tiles.append((name, ia, ib))

    w, h = tiles[0][1].size
    cw, ch = w * scale, h * scale
    cols = len(tiles)
    canvas = Image.new("RGB", (cols * cw + (cols + 1) * pad,
                              2 * ch + head + 3 * pad), (24, 24, 28))
    dr = ImageDraw.Draw(canvas)
    for i, (name, ia, ib) in enumerate(tiles):
        x = pad + i * (cw + pad)
        dr.text((x, 4), name.replace(".png", ""), fill=(220, 220, 220))
        dr.text((x, pad + head - 14), "A: " + a_lab, fill=(160, 220, 160))
        canvas.paste(ia.resize((cw, ch), Image.NEAREST), (x, pad + head))
        dr.text((x, pad + head + ch + pad - 14), "B: " + b_lab, fill=(220, 170, 160))
        canvas.paste(ib.resize((cw, ch), Image.NEAREST), (x, 2 * pad + head + ch))
    canvas.save(out_png)
    print("并排图 -> %s  (%d 帧, 上=A %s / 下=B %s)" % (out_png, len(tiles), a_lab, b_lab))
    if only_a or only_b:
        print("!! 没配上: A 独有 %d 张, B 独有 %d 张(各例: %s / %s)"
              % (len(only_a), len(only_b), only_a[:2], only_b[:2]))


if __name__ == "__main__":
    run()
