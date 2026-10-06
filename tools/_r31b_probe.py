# -*- coding: utf-8 -*-
"""r31-2号: 量各区域的代表色 / 区域统计。

用法:
  python tools/_r31b_probe.py colors <img> [<img> ...]
  python tools/_r31b_probe.py surface <img> [...]      # 上球沙面轮廓 (每列最上方的沙像素)
  python tools/_r31b_probe.py grain <img> [...]        # 沙体颗粒度 (高频能量)
  python tools/_r31b_probe.py neck <img> [...]         # 颈部宽度
"""
import sys
import numpy as np
from PIL import Image

BG = np.array([253, 246, 227])


def load(p):
    return np.array(Image.open(p).convert("RGB")).astype(np.int16)


# --- 固定区域 (物理像素 1080x1920) ---
UP_BALL = (400, 700, 700, 950)      # 上球沙体内部(暂停态沙面下的位置)
MOUND = (380, 1500, 700, 1620)      # 下球沙堆内部
NECKCOL = (525, 1150, 560, 1350)    # 颈部沙柱
NECK_BAND = (1080, 1220)            # 颈部纵向范围(y)


def reg(im, box):
    x0, y0, x1, y1 = box
    return im[y0:y1, x0:x1]


def cmd_colors(paths):
    for p in paths:
        im = load(p)
        u = reg(im, UP_BALL).reshape(-1, 3).mean(axis=0)
        m = reg(im, MOUND).reshape(-1, 3).mean(axis=0)
        n = reg(im, NECKCOL).reshape(-1, 3).mean(axis=0)
        print("%-24s up=(%3.0f,%3.0f,%3.0f) neck=(%3.0f,%3.0f,%3.0f) mound=(%3.0f,%3.0f,%3.0f)"
              % (p.split("/")[-1], u[0], u[1], u[2], n[0], n[1], n[2], m[0], m[1], m[2]))


def cmd_surface(paths):
    """上球沙面: 对 x 区间内每列, 找从上往下的第一个"非背景且非玻璃"像素 y。"""
    for p in paths:
        im = load(p)
        cols = range(300, 790, 20)
        ys = []
        for x in cols:
            col = im[300:1100, x]
            d = np.abs(col - BG).sum(axis=1)
            idx = np.where(d > 60)[0]
            ys.append(idx[0] + 300 if len(idx) else -1)
        ys = np.array(ys)
        ok = ys > 0
        if ok.sum() > 2:
            prof = ys[ok]
            print("%-28s surface y: min=%d max=%d span=%d std=%.2f  | %s"
                  % (p.split("/")[-1], prof.min(), prof.max(), prof.max() - prof.min(),
                     prof.std(), " ".join(str(int(v)) for v in ys)))
        else:
            print("%-28s no sand column" % p.split("/")[-1])


def cmd_grain(paths):
    """颗粒度: 沙体区域的高频能量 (相邻像素差的均值)。"""
    for p in paths:
        im = load(p)
        a = reg(im, UP_BALL).astype(np.float64)
        g = np.abs(np.diff(a, axis=1)).mean() + np.abs(np.diff(a, axis=0)).mean()
        print("%-28s grain_hf=%.3f" % (p.split("/")[-1], g / 2))


def cmd_neck(paths):
    """颈部: 在直筒段量沙柱的横向宽度 —— 找与背景不同的像素跨度。"""
    for p in paths:
        im = load(p)
        widths = []
        for y in range(1090, 1210, 10):
            row = im[y]
            d = np.abs(row - BG).sum(axis=1)
            idx = np.where(d > 60)[0]
            if len(idx):
                # 排除玻璃外壁: 只取中轴附近连续段
                cx = 540
                l = cx
                while l > 0 and d[l - 1] > 60:
                    l -= 1
                r = cx
                while r < 1079 and d[r + 1] > 60:
                    r += 1
                widths.append(r - l + 1)
        print("%-28s neck widths @1090..1200: %s" % (p.split("/")[-1], widths))


if __name__ == "__main__":
    c = sys.argv[1]
    {"colors": cmd_colors, "surface": cmd_surface,
     "grain": cmd_grain, "neck": cmd_neck}[c](sys.argv[2:])
