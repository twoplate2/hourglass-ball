# -*- coding: utf-8 -*-
"""旧 α 尺的**判别性负对照**：把柱身整体调亮、不动任何 alpha —— 旧尺读数必须纹丝不动，
动了就说明它分不开"亮色不透明"与"真半透明"（= 是废尺，不许拿它下结论）。

背景（AP 两名专家各自复现）: 旧尺把像素投影到 (背景→纯沙色) 连线上, 而 `sand_light` 的
投影恰 ≈ 0.854, 正好等于它当时当作 p10 目标的 0.855 => 调亮就会"达标"。

做法: 只改图, 不改渲染 —— 取 2.26 的柱子那一段, 逐像素 +10 灰阶, 其余原样; 两张图各跑一次旧判据。

用法: python tools/_probe_oldruler_control.py <图> [标签]
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def col_mask(im, cx):
    px = im.load()
    W, H = im.size

    def is_sand(c):
        return c[0] - c[2] > 40 and c[2] < 215

    m = np.zeros((H, W), bool)
    for y in range(H):
        xs = [x for x in range(W) if is_sand(px[x, y])]
        if xs and 4 < xs[-1] - xs[0] + 1 < W * 0.18:
            m[y, xs[0]:xs[-1] + 1] = True
    return m


def run_probe(path, tag):
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "_probe_column_alpha.py"),
                          str(path), tag],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    got = False
    for ln in (out.stdout or "").splitlines():
        if "柱内 alpha" in ln or "正对照最大偏差" in ln:
            print("   " + ln.strip()); got = True
    if not got:
        print("   !! 子进程没给出可识别输出; stderr 尾部:")
        for ln in (out.stderr or "").splitlines()[-4:]:
            print("      " + ln)


def main():
    src = Path(sys.argv[1])
    im = Image.open(src).convert("RGB")
    W, H = im.size
    cx = W // 2
    a = np.asarray(im, dtype=np.int16)
    m = col_mask(im, cx)
    print("  %s  %dx%d  柱带内像素 %d" % (src.name, W, H, int(m.sum())))

    bright = a.copy()
    bright[m] = np.clip(bright[m] + 10, 0, 255)      # **只调亮, alpha 一个像素没动**
    bp = ROOT / "benchmark_logs" / "_vid" / "colruler_bright.png"
    bp.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(bright.astype("uint8")).save(bp)

    print("\n  == 原图 ==")
    run_probe(src, "orig")
    print("\n  == 柱身 +10 灰阶(alpha 未动) ==")
    run_probe(bp, "bright")
    print("""
  判据: 两组的 alpha 读数**必须相同**。
        不同 => 这个量具把"调亮"读成了"变透", 是**非判别性**的 —— 不许拿它下任何结论。""")


if __name__ == "__main__":
    main()
