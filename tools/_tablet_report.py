# -*- coding: utf-8 -*-
"""**平板日志一页读**: 把用户设备那份 benchmark log 的尾部问题直接打出来(2026-10-07)。

## 为什么要有它

`benchmark_logs/dev/` 里那一堆是**模拟器**的, 而用户报的问题全在**真机**上 ——
模拟器与真机在这件事上差得很远(实测: 真机 165Hz 三栏和 p50 4.04ms, 模拟器同一档 p50 却更大,
而 `_draw_stream` 那一段在模拟器上 5.2~5.5ms、桌面只有 0.12ms/帧)。
⇒ 每拿到一份真机日志, 都要**当场**回答三件事:

1. **环境**: 实际刷新率/上限旋钮/预热是否生效(标记文件的自证);
2. **尾部有多大**: 以"该档的一格 vsync"为门槛(165Hz=6.06ms), 数**掉格的帧**;
3. **尾部花在哪**: 1.225 起日志里带 `Slowest frame segments`, 直接给逐段耗时。

跑法: python tools/_tablet_report.py <log.txt>      (默认取 D:/Temp 里最新那份)
"""
import re
import statistics as st
import sys
from pathlib import Path

DEFAULT_DIR = Path("D:/Temp")
MODE_HZ = {  # 档位 → 该档屏幕给的刷新率(从 `refresh_modes` 里解析不到时的兜底)
}


def load(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    env = {}
    for line in text.splitlines():
        if line.startswith("环境:"):
            for tok in line.split("/"):
                tok = tok.strip()
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    env[k] = v
            break
    rows = []
    for line in text.splitlines():
        if line[:1].isdigit() and line.count(",") == 18:
            rows.append([float(x) for x in line.split(",")])
    segs = []
    for m in re.finditer(r"^    redraw=([\d.]+)  (.*)$", text, re.M):
        segs.append((float(m.group(1)), m.group(2)))
    return env, rows, segs


def tiers(rows):
    out = [[]]
    last = -1.0
    for r in rows:
        if r[0] < last and out[-1]:
            out.append([])
        last = r[0]
        out[-1].append(r)
    return out


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        cands = sorted(DEFAULT_DIR.glob("benchmark_*.txt"), key=lambda p: p.stat().st_mtime)
        if not cands:
            raise SystemExit("D:/Temp 里没有 benchmark_*.txt; 用法: python tools/_tablet_report.py <log>")
        path = cands[-1]
    env, rows, segs = load(path)
    print("")
    print("  文件: %s" % path.name)
    for k in ("app_version", "maxfps", "refresh_hz", "warm", "splash_max",
              "splash_renderer", "flow_rate", "python", "code_hash"):
        if k in env:
            print("    %-16s %s" % (k, env[k]))
    if "refresh_modes" in env:
        print("    refresh_modes    %s" % env["refresh_modes"])
    hz = float(env.get("refresh_hz") or 0) or 120.0
    grid = 1000.0 / hz
    print("")
    print("  == 每档: 以 **一格 vsync = %.2fms** 为门槛 ==" % grid)
    print("  ⚠️ 门槛按日志里的 `refresh_hz` 算 —— **模拟器上呈现节拍被宿主锁死(实测恒 ~60), "
          "这一栏会虚高**, 只有真机的数才作数。")
    for i, s in enumerate(tiers(rows)):
        if not s:
            continue
        frame = [r[1] for r in s]
        work = [r[3] + r[4] + r[5] for r in s]
        over = [r for r in s if r[1] > grid * 1.5]        # 跨过 1.5 格 = 掉了一格
        print("   档%d 帧%5d | 帧 p50 %5.2f p90 %5.2f p99 %5.2f | 三栏和 p50 %5.2f p90 %5.2f"
              % (i, len(s), st.median(frame), sorted(frame)[int(len(frame)*0.9)],
                 sorted(frame)[min(len(frame)-1, int(len(frame)*0.99))],
                 st.median(work), sorted(work)[int(len(work)*0.9)]))
        print("        掉一格(>%.2fms) %4d 帧 = %5.2f%%  | 其中 图元>物理>Canvas 谁最大: "
              % (grid * 1.5, len(over), 100.0 * len(over) / len(s)), end="")
        if over:
            big_ud = sum(1 for r in over if r[4] >= max(r[3], r[5]))
            print("图元 %d / 物理 %d / Canvas %d" % (big_ud,
                  sum(1 for r in over if r[3] > max(r[4], r[5])),
                  sum(1 for r in over if r[5] > max(r[3], r[4]))))
        else:
            print("-")
    if segs:
        print("")
        print("  == 最慢那几帧的逐段耗时(相邻段首尾相接, 不要相加) ==")
        for ms, seg in segs[:5]:
            print("    redraw=%5.2f  %s" % (ms, seg))
        print("    ⚠️ 每个名字记的是**距上一次打点**的那一段 ⇒ 标签 = 刚结束的那一段")
    else:
        print("")
        print("  (这份日志没有 `Slowest frame segments` —— 1.225 起才有)")
    return 0


sys.exit(main())
