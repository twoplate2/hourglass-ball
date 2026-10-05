# -*- coding: utf-8 -*-
"""找茬活动时长 —— **按取证产物的 mtime 算, 不按墙钟**(用户 2026-10-05 指出中间停过)。

判据: 相邻两个产物间隔 ≥10 分钟 ⇒ 判定为"停了", 不计入活动时长。
产物 = _review/ 下的 png/mp4/txt(评审真在跑才会产生)。
跑法: python tools/_review_clock.py [目标小时数, 默认 6]
"""
import glob
import os
import sys
from datetime import datetime

GAP = 10 * 60


def main():
    target = float(sys.argv[1]) if len(sys.argv) > 1 else 9.0   # 用户 2026-10-05: 6 -> 9
    files = []
    for pat in ("_review/**/*.png", "_review/**/*.mp4", "_review/**/*.txt"):
        files += glob.glob(pat, recursive=True)
    ts = sorted(os.path.getmtime(f) for f in files if "/_work/" not in f)
    if not ts:
        print("没有取证产物"); return 1
    gaps, prev = [], ts[0]
    for t in ts[1:]:
        if t - prev >= GAP:
            gaps.append((prev, t, t - prev))
        prev = t
    span = ts[-1] - ts[0]
    idle = sum(g[2] for g in gaps)
    act = span - idle
    print("产物 %d 个   首 %s   末 %s"
          % (len(files), datetime.fromtimestamp(ts[0]).strftime("%m-%d %H:%M"),
             datetime.fromtimestamp(ts[-1]).strftime("%m-%d %H:%M")))
    print("墙钟 %.1f h   空档 %.1f h(%d 段)   实际活动 %.1f h"
          % (span / 3600, idle / 3600, len(gaps), act / 3600))
    for a, b, d in sorted(gaps, key=lambda g: -g[2])[:5]:
        print("   停 %5.1f h: %s -> %s" % (d / 3600,
              datetime.fromtimestamp(a).strftime("%m-%d %H:%M"),
              datetime.fromtimestamp(b).strftime("%m-%d %H:%M")))
    print("目标 %.1f h -> %s (差 %.1f h)"
          % (target, "已达标" if act >= target * 3600 else "未达标", max(0.0, target - act / 3600)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
