# -*- coding: utf-8 -*-
"""`_splash_max_arms.sh` 的判读: **飞溅上限有没有把尾部压下来**。

判据(全部来自日志的逐帧 CSV, 与平均帧率无关):

1. `frame_ms > 9ms` 的帧数 —— 165Hz 上跨过 1.5 格(6.06ms)= 掉了一格(→12.1ms),
   这正是 1% low 里那 5.4% 的帧。**主判据**。
2. `图元` / `物理` 的 p50 / p90 —— 上限只该压**尾部**(p90), 不该动中位。
3. 存活飞溅峰值 —— 确认旋钮真的把飞溅数压住了(否则"没差别"可能只是空转)。
"""
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "benchmark_logs" / "splashmax"


def parse(path):
    lim = None
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("环境:"):
            for tok in line.split("/"):
                tok = tok.strip()
                if tok.startswith("splash_max="):
                    lim = tok.split("=", 1)[1]
            break
    rows = []
    for line in text.splitlines():
        if line[:1].isdigit() and line.count(",") == 18:
            rows.append([float(x) for x in line.split(",")])
    return lim, rows


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * q))]


def main():
    files = sorted(D.glob("s*.txt"))
    if not files:
        raise SystemExit("没有日志 —— 先跑 bash tools/_splash_max_arms.sh")
    print("")
    arms = {}
    for f in files:
        lim, rows = parse(f)
        if not rows:
            continue
        per = []
        # 按 t 回退切档
        secs = [[]]
        last = -1.0
        for r in rows:
            if r[0] < last and secs[-1]:
                secs.append([])
            last = r[0]
            secs[-1].append(r)
        for s in secs:
            per.append({
                "over9": sum(1 for r in s if r[1] > 9.0),
                "n": len(s),
                "ud_p50": st.median(r[4] for r in s),
                "ud_p90": pct([r[4] for r in s], 0.90),
                "ph_p50": st.median(r[3] for r in s),
                "ph_p90": pct([r[3] for r in s], 0.90),
                "sp_max": max(r[8] for r in s),
            })
        arms[f.stem] = (lim, per)
        print("  %-8s splash_max=%s  档数 %d" % (f.stem, lim, len(per)))
        for i, s in enumerate(per):
            print("     档%d 帧%4d | >9ms %3d (%.1f%%) | 图元 p50 %.2f p90 %.2f | 物理 p50 %.2f p90 %.2f | 飞溅峰值 %d"
                  % (i, s["n"], s["over9"], 100.0 * s["over9"] / max(1, s["n"]),
                     s["ud_p50"], s["ud_p90"], s["ph_p50"], s["ph_p90"], s["sp_max"]))
    print("")
    on = [v for v in arms.values() if v[0] not in (None, "0")]
    off = [v for v in arms.values() if v[0] in ("0",)]
    if not on or not off:
        print("!! 两臂不齐(需要 splash_max=0 与 非0 各至少一份) ⇒ 本次不作数")
        return 1
    print("  == 配对判读(每档: 关 → 开) ==")
    for i in range(min(len(off[0][1]), len(on[0][1]))):
        a = [v[1][i] for v in off]
        b = [v[1][i] for v in on]
        print("   档%d  >9ms 帧 %.0f → %.0f | 图元 p90 %.2f → %.2f | 物理 p90 %.2f → %.2f | 飞溅峰值 %.0f → %.0f"
              % (i,
                 sum(x["over9"] for x in a) / len(a), sum(x["over9"] for x in b) / len(b),
                 sum(x["ud_p90"] for x in a) / len(a), sum(x["ud_p90"] for x in b) / len(b),
                 sum(x["ph_p90"] for x in a) / len(a), sum(x["ph_p90"] for x in b) / len(b),
                 sum(x["sp_max"] for x in a) / len(a), sum(x["sp_max"] for x in b) / len(b)))
    return 0


sys.exit(main())
