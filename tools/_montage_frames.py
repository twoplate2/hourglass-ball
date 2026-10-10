# -*- coding: utf-8 -*-
"""把一串帧的**同一个窗口**裁出来、放大、横排成一条 —— 用来**看方向**与**看形状**。

为什么需要它: 统计量(互相关速度、z 值)能告诉你"动了多少", 但**看不出动的是什么**。
判"柱子里那个大颗粒是什么东西、往哪走"必须**把连续几帧并排放在眼前**。

用法:
    python tools/_montage_frames.py <文件夹> <y0 y1 x0 x1> <起始序号> <张数> <放大> <输出路径>
例:
    python tools/_montage_frames.py benchmark_logs/flow_visual_gm244 1400 1900 900 1000 0 6 3 _vid/col_k0.png

⚠️ 帧按文件名里的 `time-` 排序(与 `_probe_grain_motion.read_seq` 同一口径)。
⚠️ 放大用 NEAREST(不插值) —— 插值会把"这一像素是不是噪声颗粒"抹掉。
"""
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def frames(folder):
    fs = sorted(Path(folder).glob("period-*.png"),
                key=lambda p: float(p.stem.split("time-")[1]))
    return fs


def main():
    folder = ROOT / sys.argv[1]
    y0, y1, x0, x1 = (int(v) for v in sys.argv[2:6])
    i0, n, zoom = int(sys.argv[6]), int(sys.argv[7]), int(sys.argv[8])
    out = ROOT / sys.argv[9]
    fs = frames(folder)[i0:i0 + n]
    if not fs:
        print("没有帧"); return 2
    tiles = []
    for p in fs:
        a = np.asarray(Image.open(p).convert("RGB"))[y0:y1, x0:x1]
        im = Image.fromarray(a).resize(((x1 - x0) * zoom, (y1 - y0) * zoom),
                                       Image.NEAREST)
        tiles.append(np.asarray(im))
    gap = np.full((tiles[0].shape[0], 4, 3), 40, dtype=np.uint8)
    row = []
    for i, t in enumerate(tiles):
        row.append(t)
        if i != len(tiles) - 1:
            row.append(gap)
    sheet = np.concatenate(row, axis=1)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sheet).save(out)
    print("并排图 %d 帧 x%d -> %s  (%dx%d)"
          % (len(tiles), zoom, out, sheet.shape[1], sheet.shape[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
