# -*- coding: utf-8 -*-
"""N1(色调量纲)修复的**观测量**: 出口以下柱带的"颗粒量"、均值色、以及两臂逐像素差。

判据(三条一起看, 缺一条就会读歪):
 1. `柱内高频 std`(= 每行去趋势后的标准差, 与 `_accept_check` 的 A1 同式):
    颗粒感 = 行内的明暗起伏。**修好之后应当升**(原状是"3 档平涂抹"或是"全饱和")。
 2. `均值亮度` —— 必须**贴着沙堆/上球的色**(设计: `_flow_table` 以 base 居中)。
    ⚠️ 只看 std 会漏掉"整体偏亮偏平"那一类(2026-10-10 踩过: 均值 174→181 而 std 掉)。
 3. 两臂逐像素差 + bbox: 确认改动**只落在柱带里**(不该动上球/玻璃/沙堆)。

跑法:
    python tools/_probe_n1_tone.py <dirA> <dirB> [口径 1904x2890]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BG = np.array([253.0, 246.0, 227.0])       # #fdf6e3
GLASS = np.array([234.0, 243.0, 248.0])    # #eaf3f8
LW = np.array([0.299, 0.587, 0.114])


def sand_mask(X, ref):
    """1-近邻: 离"沙"比离背景/玻璃更近 ⇒ 沙。与配色无关(见 `_accept_check` 的注释)。"""
    refs = np.vstack([ref.reshape(1, 3), BG.reshape(1, 3), GLASS.reshape(1, 3)])
    d = np.abs(X[:, :, None, :] - refs[None, None, :, :]).max(axis=3)
    return d.argmin(axis=2) == 0


def row_hp_std(seg):
    """行内去趋势(5 点移动平均)后的 std —— 高频起伏 = 颗粒。"""
    k = 5
    hp = seg - np.convolve(seg, np.ones(k) / k, "same")
    return float(hp[k // 2:-(k // 2)].std())


COL_HALF = 41          # 柱半宽(平板口径实测 82px 宽, 与管径一致 —— 见 2.83 那节)
COL_Y = (1560, 2200)   # 自由落体段(出口 ~1496 以下、沙堆面以上)
BALL_Y = (600, 900)    # 上球沙体(同质对照)


def analyze(png, W_hint=None):
    """**定窗**量柱带 —— 不用"行内沙色跨度"找行。

    ⚠️ 第一版按跨度找, 结果量到的是**玻璃管**那段(y1378..1533): 出口以下的每一行
    除了中央那根柱, 还撒着**飞溅颗粒**(平板 50s 档实测每帧 2278 颗)⇒ 跨度被撑到
    600~1300px ⇒ 被"细柱"判据排除。定窗(以画面中心为柱心)才是稳的。
    """
    X = np.asarray(Image.open(png).convert("RGB"), dtype=float)
    H, W = X.shape[:2]
    cx = W // 2
    x0, x1 = cx - COL_HALF, cx + COL_HALF
    out = {}
    for tag, (ya, yb) in (("col", COL_Y), ("ball", BALL_Y)):
        stds, means = [], []
        for y in range(ya, yb):
            seg = X[y, x0:x1 + 1] @ LW
            stds.append(row_hp_std(seg))
            means.append(float(seg.mean()))
        out[tag + "_std"] = float(np.median(stds))
        out[tag + "_mean"] = float(np.median(means))
    return out


def main():
    a = ROOT / "benchmark_logs" / ("flow_visual_" + sys.argv[1])
    b = ROOT / "benchmark_logs" / ("flow_visual_" + sys.argv[2])
    names = sorted(p.name for p in a.glob("period-*.png")
                   if (b / p.name).exists())
    print("== 两臂逐像素差 ==")
    for n in names:
        X = np.asarray(Image.open(a / n).convert("RGB"), dtype=np.int16)
        Y = np.asarray(Image.open(b / n).convert("RGB"), dtype=np.int16)
        d = np.abs(X - Y).max(axis=2)
        ys, xs = np.nonzero(d > 2)
        box = "无" if ys.size == 0 else "x %d..%d y %d..%d" % (xs.min(), xs.max(), ys.min(), ys.max())
        print("  %-26s 差>2: %6d px  最大通道差 %3d  bbox %s"
              % (n, int(ys.size), int(d.max()), box))
    print("\n== 柱带颗粒量(高频 std) / 均值亮度 —— 定窗 x=柱心±%d, y=%d..%d ==" % (COL_HALF, *COL_Y))
    print("  %-22s %10s %10s %10s %10s" % ("臂", "柱std中位", "柱均值", "上球std", "上球均值"))
    for lab, d in (("A " + sys.argv[1], a), ("B " + sys.argv[2], b)):
        got = [analyze(d / n) for n in names]
        print("  %-22s %10.3f %10.1f %10.3f %10.1f"
              % (lab,
                 float(np.median([g["col_std"] for g in got])),
                 float(np.median([g["col_mean"] for g in got])),
                 float(np.median([g["ball_std"] for g in got])),
                 float(np.median([g["ball_mean"] for g in got]))))


if __name__ == "__main__":
    main()
