# -*- coding: utf-8 -*-
"""读 A/B 基准日志, 出配对对比。

用法:
  python tools/analyze_ab.py benchmark_logs/ab
  python tools/analyze_ab.py benchmark_logs/ab --arm-a v1.2 --arm-b v1.21

日志文件名形如 `<arm>_r<n>__<时间戳>.txt`(由 tools/ab_device_benchmark.sh 产出)。

判据(项目纪律, 别改成看平均):
  * 比 **1% low / p90 / p99**, **不比平均** —— apk/README 记档"平均帧率在这台设备上不是
    有效指标", 同机同配置跑间 ±12%。
  * **中位差要大于组内极差**才算可分辨(见 apk/CLAUDE.md 的 M 跑间极差一节)。
  * 逐轮核对日志里的 `Flow renderer:` 字段 —— 若某轮退化成 line 池, 那一轮不可比。
"""
import argparse
import glob
import os
import re
import statistics as st
import sys

PERIOD_RE = re.compile(r"^(\d+)\s*秒\s*·\s*(\d+)\s*帧", re.M)
AVG_RE = re.compile(r"平均\s*([\d.]+)\s*FPS\s+1%\s*low\s*([\d.]+)\s*FPS")
QUANT_RE = re.compile(r"p50=([\d.]+)\s+p90=([\d.]+)\s+p99=([\d.]+)\s+max=([\d.]+)")
REND_RE = re.compile(r"Flow renderer:\s*(\S+)")
VER_RE = re.compile(r"app_version=([\d.]+)")
# "越高越好"的指标; 其余越低越好
HIGHER_IS_BETTER = {"avg", "low1"}


def parse(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    rend = REND_RE.search(text)
    ver = VER_RE.search(text)
    out = {"renderer": rend.group(1) if rend else "?",
           "version": ver.group(1) if ver else "?",
           "periods": {}}
    blocks = list(PERIOD_RE.finditer(text))
    for i, m in enumerate(blocks):
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        seg = text[m.end():end]
        a, q = AVG_RE.search(seg), QUANT_RE.search(seg)
        if not a:
            continue
        out["periods"][int(m.group(1))] = {
            "frames": int(m.group(2)),
            "avg": float(a.group(1)), "low1": float(a.group(2)),
            "p50": float(q.group(1)) if q else float("nan"),
            "p90": float(q.group(2)) if q else float("nan"),
            "p99": float(q.group(3)) if q else float("nan"),
        }
    return out


def load(directory, arm_a, arm_b):
    runs = {}
    for f in sorted(glob.glob(os.path.join(directory, "*.txt"))):
        base = os.path.basename(f)
        if base.startswith("logcat_"):
            continue
        arm = arm_a if base.startswith(arm_a + "_") else arm_b if base.startswith(arm_b + "_") else None
        if arm is None:
            continue
        runs.setdefault(arm, []).append((base, parse(f)))
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory", help="ab_device_benchmark.sh 的产物目录")
    ap.add_argument("--arm-a", default="v1.2")
    ap.add_argument("--arm-b", default="v1.21")
    ap.add_argument("--show-average", action="store_true",
                    help="额外打印平均帧率(默认不比 —— 项目纪律: 平均在本机非有效指标)")
    args = ap.parse_args()

    runs = load(args.directory, args.arm_a, args.arm_b)
    if not runs:
        sys.exit(f"{args.directory} 下没有可解析的 A/B 日志")

    for arm in sorted(runs):
        print(f"\n===== {arm} =====")
        for base, r in sorted(runs[arm]):
            ps = "  ".join(f"{p}s: low1={d['low1']:.1f} p90={d['p90']:.2f} avg={d['avg']:.1f}"
                           for p, d in sorted(r["periods"].items()))
            print(f"  {base[:40]:<40} ver={r['version']:<5} {r['renderer']:<22} {ps}")
    rends = {r["renderer"] for v in runs.values() for _, r in v}
    if len(rends) > 1:
        print(f"\n⚠️ 渲染器不一致 {sorted(rends)} —— 退化成 line 池的轮次不可比, 先剔除再下结论。")

    if len(runs) < 2:
        print("\n⚠️ 只有一个臂有数据, 无法配对比较。")
        return

    keys = [("low1", "1%low"), ("p90", "p90(ms)"), ("p99", "p99(ms)")]
    if args.show_average:
        keys.append(("avg", "avg"))

    periods = sorted({p for v in runs.values() for _, r in v for p in r["periods"]})
    print(f"\n===== 配对比较 (中位; {args.arm_a} n={len(runs.get(args.arm_a, []))}, "
          f"{args.arm_b} n={len(runs.get(args.arm_b, []))}) =====")
    for p in periods:
        print(f"\n--- {p}s ---")
        for key, label in keys:
            a = [r["periods"][p][key] for _, r in runs.get(args.arm_a, []) if p in r["periods"]]
            b = [r["periods"][p][key] for _, r in runs.get(args.arm_b, []) if p in r["periods"]]
            if not a or not b:
                continue
            ma, mb = st.median(a), st.median(b)
            spread = max(max(a) - min(a), max(b) - min(b))
            better = "A更好" if ((ma > mb) == (key in HIGHER_IS_BETTER)) else "B更好"
            verdict = "可分辨" if abs(ma - mb) > spread else "**在噪声内, 不可分辨**"
            print(f"  {label:>8}: {args.arm_a}={ma:8.2f}  {args.arm_b}={mb:8.2f}  "
                  f"Δ={ma - mb:+7.2f}  组内极差={spread:6.2f}  {better}  {verdict}")
            print(f"           逐轮 {args.arm_a}={[round(x, 1) for x in a]}  "
                  f"{args.arm_b}={[round(x, 1) for x in b]}")
    print("\n读法: 1%low / avg 越大越好; p90 / p99 越小越好。"
          "\n判据: |中位差| > 组内极差 才算可分辨。")


if __name__ == "__main__":
    main()
