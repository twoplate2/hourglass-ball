# -*- coding: utf-8 -*-
"""设备四臂截图的**量化报告**: 沙柱宽度随深度的剖面 + 四臂是否真的生效。

为什么要它: 用户问「你确定比老版本更好?」+「给出量化标准」。先前我拿一个**不判别**的统计量
(sd, 两条带窗口宽度还不同) 当依据推了出去 —— 这次先有判据再判。

判据(三问, 按顺序):
  ① **四臂真的生效了吗** —— 若四个 `shrinkmin` 值给出的宽度剖面**互不相同**, 才算没空转。
     (项目记录过的坑: 标记文件没被读到 ⇒ 四臂全是基线, 会被误读成"没差别"。)
  ② **曲线形状对不对** —— 把每臂的 `半宽(d) / 孔径` 打出来, 与 `_probe_shrink_curve.py`
     的目标曲线比。**判据是"实测剖面 vs 目标曲线"的最大偏差**, 不是"看着像"。
  ③ 与老版本(0.70)比, 到底收窄了多少 —— 给出每个深度上的半宽比。

用法: python tools/_shrink_dev_report.py
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "benchmark_logs" / "_vid" / "shrink_dev"
DEPTHS = (0.05, 0.15, 0.35, 0.60, 0.90)      # 相对"从出口到落点"的归一化深度


def sand_runs(im):
    """逐行找沙色游程 (y, x0, x1, 宽度)。**不依赖任何坐标假设** —— 从图里认。"""
    W, H = im.size
    px = im.load()

    def is_sand(c):
        return c[0] - c[2] > 40 and c[2] < 215

    out = []
    for y in range(H):
        xs = [x for x in range(0, W, 1) if is_sand(px[x, y])]
        if xs:
            out.append((y, xs[0], xs[-1], xs[-1] - xs[0] + 1))
    return out


def column(im):
    """找出"细颈+自由柱"那一段连续窄行, 返回 (top_row, bottom_row, x_center, 孔径px)。"""
    runs = sand_runs(im)
    if not runs:
        return None
    widest = max(r[3] for r in runs)
    narrow = [r for r in runs if r[3] <= 0.16 * widest]
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
    top = best[0][0]
    bot = best[-1][0]
    # 孔径 = 最上面几行的宽度(还在管内, 未收缩)
    bore = max(r[3] for r in best[:max(1, len(best) // 12)])
    xc = (min(r[1] for r in best) + max(r[2] for r in best)) // 2
    return top, bot, xc, bore


def main():
    imgs = sorted(D.glob("*.png")) if D.exists() else []
    if not imgs:
        print("!! 没有截图(先跑 tools/_shrink_device_arms.sh)")
        return 1
    print("")
    print("  截图 %d 张" % len(imgs))
    rows = {}
    for f in imgs:
        im = Image.open(f).convert("RGB")
        c = column(im)
        if not c:
            print("  %-10s 认不出柱子" % f.stem)
            continue
        top, bot, xc, bore = c
        span = max(1, bot - top)
        prof = []
        for fr in DEPTHS:
            y = top + int(round(span * fr))
            best = None
            for yy in range(max(0, y - 2), min(im.size[1], y + 3)):
                r = [q for q in sand_runs(im) if q[0] == yy]
                if r:
                    w = r[0][3]
                    best = w if best is None else max(best, w)
            prof.append((best or 0) / float(bore))
        rows[f.stem] = (bore, span, prof)
        print("  %-10s 孔径=%3dpx 柱长=%3dpx  半宽/孔径 @%s = %s"
              % (f.stem, bore, span, DEPTHS, ["%.2f" % v for v in prof]))
    if len(rows) >= 2:
        vals = list(rows.values())
        distinct = any(abs(a[2][i] - b[2][i]) > 0.03
                       for i in range(len(DEPTHS))
                       for a in vals for b in vals)
        print("")
        print("  ① 四臂是否生效: %s" % ("是(剖面互不相同)" if distinct else
                                       "!! 否 —— 四臂一样, 标记文件多半没被读到(空转)"))
        print("  ② 目标曲线对照: 跑 tools/_probe_shrink_curve.py 拿表, 逐深度比上面的数")
    return 0


sys.exit(main())
