# -*- coding: utf-8 -*-
"""交界判据(可对**任意图**) —— 把 `_probe_junction_seam.py` 的 metric 抽出来, 不再自己渲染。

为什么要有它: 原探针是配 `HG_WORLD_UV=0/1` 那对**已删除**的开关建的(标定参照没了 ⇒ 尺子过期);
而且它只量自己渲染的那一张, 量不了"改前/改后"两张不同来源的图(录像帧 vs 设备截图)。

判据(与原子相同, 逐字照搬):
  在**管顶(球↔柱交界)**上下各取一条横带, 各算
    mu  = 均值 luminance      —— 明暗对不对得上
    sd  = 标准差              —— 颗粒反差对不对得上
    L   = 水平自相关半衰长度  —— **颗粒尺度**对不对得上
  报 |dmu| / |dsd|/sd_上 / |dL|/L_上。

⚠️ **必须先标定**: 本脚本自带**正对照** —— 把下面那条带换成"从别处剪来的错配砂带",
   三个量必须显著变差。分不开 ⇒ 这个量具是废的, 不许拿它下结论。

用法: python tools/_probe_junction_img.py <图片路径> [标签]
"""
import math
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def is_sand(c):
    return c[0] - c[2] > 40 and c[2] < 215


def find_tube(im):
    """不依赖坐标假设: 按"沙色游程宽度"找出最细的那一段连续行 = 管子/沙柱。"""
    W, H = im.size
    px = im.load()
    rows = []
    for y in range(H):
        xs = [x for x in range(W) if is_sand(px[x, y])]
        if xs:
            rows.append((y, xs[0], xs[-1], xs[-1] - xs[0] + 1))
    if not rows:
        return None
    widest = max(r[3] for r in rows)
    narrow = [r for r in rows if r[3] <= 0.16 * widest]
    if len(narrow) < 5:
        return None
    best = cur = [narrow[0]]
    for r in narrow[1:]:
        if r[0] == cur[-1][0] + 1:
            cur.append(r)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [r]
    if len(cur) > len(best):
        best = cur
    return best[0][0], (min(r[1] for r in best) + max(r[2] for r in best)) // 2, best[-1][0]


def measure(im, cx, lo, hi):
    W, H = im.size
    px = im.load()
    xs = list(range(max(0, cx - 10), min(W, cx + 11)))
    ys = [y for y in range(int(lo), int(hi) + 1) if 0 <= y < H]
    lum = [[0.299 * px[x, y][0] + 0.587 * px[x, y][1] + 0.114 * px[x, y][2] for x in xs]
           for y in ys]
    flat = [v for row in lum for v in row]
    n = len(flat)
    if not n:
        return None
    mu = sum(flat) / n
    var = sum((v - mu) ** 2 for v in flat) / n
    sd = math.sqrt(var)
    ell = 0.0
    if sd > 1e-9:
        for lag in range(1, 9):
            num = cnt = 0
            for row in lum:
                rm = sum(row) / len(row)
                for i in range(len(row) - lag):
                    num += (row[i] - rm) * (row[i + lag] - rm)
                    cnt += 1
            r = (num / cnt) / var if cnt else 0.0
            if r < 0.5 and ell == 0.0:
                ell = float(lag)
                break
        if ell == 0.0:
            ell = 9.0
    return mu, sd, ell, n


def report(im, tag):
    found = find_tube(im)
    if not found:
        print("  %-28s 认不出管子 —— 判据不成立" % tag)
        return None
    top, cx, bot = found
    tlen = bot - top + 1
    gap = max(10, tlen // 6)
    bands = {"up(球里)": (top - 2 * gap, top - gap), "down(柱里)": (top + gap, top + 2 * gap)}
    st = {k: measure(im, cx, lo, hi) for k, (lo, hi) in bands.items()}
    a, b = st["up(球里)"], st["down(柱里)"]
    dmu = abs(a[0] - b[0])
    dsd = abs(a[1] - b[1]) / max(1e-6, a[1])
    dln = abs(a[2] - b[2]) / max(1e-6, a[2])
    print("  %-28s 管顶行=%d 轴x=%d 带宽=%d行 | up mu=%7.2f sd=%6.2f L=%4.1f | "
          "down mu=%7.2f sd=%6.2f L=%4.1f | **|dmu|=%.2f |dsd|/sd=%.3f |dL|/L=%.3f**"
          % (tag, top, cx, gap, a[0], a[1], a[2], b[0], b[1], b[2], dmu, dsd, dln))
    return dmu, dsd, dln


def main():
    path = Path(sys.argv[1])
    tag = sys.argv[2] if len(sys.argv) > 2 else path.stem
    im = Image.open(path).convert("RGB")
    print("  %s  %dx%d" % (path.name, im.size[0], im.size[1]))
    base = report(im, tag)

    # ---- 正对照: 把"下"那条带换成错配砂带(从图里另一高度的沙区剪来) ----
    found = find_tube(im)
    if found and base:
        top, cx, bot = found
        tlen = bot - top + 1
        gap = max(10, tlen // 6)
        W, H = im.size
        don_lo = top + gap
        src_lo = min(H - 2 * gap, top + 12 * gap)          # 从更深处的沙区剪
        ctrl = im.copy()
        ctrl.paste(im.crop((0, src_lo, W, src_lo + gap)), (0, don_lo))
        report(ctrl, tag + " [正对照:下带错配]")
        print("  ↑ 正对照两个数字必须**明显变差**(|dmu| 或 |dsd| 变大); 没变差 ⇒ 量具是废的")
    return 0


if __name__ == "__main__":
    sys.exit(main())
