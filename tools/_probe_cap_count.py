# -*- coding: utf-8 -*-
"""数"沙面上方的淡色小帽"像素 —— 设备上判"亮带露到矩形外"这条。

判据(在**修前**图上标定, 不在修后图上拍脑袋):
  沙   = b < 140 且 r > 190 (饱和橙)
  玻璃 = r>225 且 g>235 且 b>235 (近白蓝)
  小帽 = 都不是, 且 **b 落在两者之间** —— 即"暖而亮"的过渡色
         r27 实测 (232,211,173): b=173, 恰在 沙(<140) 与 玻璃(>235) 之间
做法: 逐列找**最上面的沙像素**, 再看它上方 15 行内的小帽像素数。
用法: python tools/_probe_cap_count.py <图...>
"""
import sys
from PIL import Image
import numpy as np

BODY_TOP, BODY_BOT = 260, 1950   # 沙漏本体的 y 范围(1080x2400 竖屏)


def count(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    sand = (b < 140) & (r > 190)
    sand[:BODY_TOP] = False          # 顶部色块按钮
    sand[BODY_BOT:] = False          # 底部控件
    cap = (b >= 140) & (b < 225) & (r > 200) & (g > 185)
    # 限定在上球区域(排除下球): 从上往下第一个"沙行数>100"的位置起, 往上 400 行内
    # ⚠️ 必须限定到**沙漏本体**: 第一版全图扫, 锁到了顶部那排色块按钮
    #    (三张完全不同的图都给同一个 169 ⇒ 判据是死的, 一眼可辨)。
    rows = sand.sum(axis=1)
    ys = np.nonzero(rows > 100)[0]
    if len(ys) == 0:
        return path, 0, 0, None
    top = ys.min()
    lo, hi = max(0, top - 400), top + 60
    n_cap = 0
    cols_hit = 0
    for x in range(a.shape[1]):
        col = sand[lo:hi, x]
        idx = np.nonzero(col)[0]
        if len(idx) == 0:
            continue
        y0 = lo + idx[0]
        seg = cap[max(0, y0 - 15):y0, x]
        c = int(seg.sum())
        if c:
            n_cap += c
            cols_hit += 1
    return path, n_cap, cols_hit, (lo, hi, top)


for p in sys.argv[1:]:
    path, n, cols, info = count(p)
    print("  %-46s 小帽像素 %5d, 涉及列 %4d   (窗口 %s)" % (path.split("/")[-1], n, cols, info))
