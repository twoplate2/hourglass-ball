# -*- coding: utf-8 -*-
"""读 A/B 基准日志, 出配对对比。

用法:
  python tools/analyze_ab.py benchmark_logs/ab
  python tools/analyze_ab.py benchmark_logs/ab --arm-a v1.2 --arm-b v1.21

日志文件名形如 `<arm>_r<n>__<时间戳>.txt`(由 tools/ab_device_benchmark.sh 产出)。

判据(项目纪律, 别改成看平均):
  * 比 **1% low / p90 / p99**, **不比平均** —— apk/README 记档"平均帧率在这台设备上不是
    有效指标", 同机同配置跑间 ±12%。
  * **主判据是按轮配对差**(A 的第 i 轮 − B 的第 i 轮): 相邻轮次把随时间漂移的噪声差掉。
    "中位差 > 组内极差"那条老判据在同码组内极差横跨 0.4~30 FPS 时并不可校准, 只作对照。
    n<=4 的配对只当方向提示, 不当结论。
  * 逐轮核对日志里的 `Flow renderer:` 字段 —— 若某轮退化成 line 池, 那一轮不可比。
  * 逐轮核对 `physics_ms` —— 两臂若在这栏上分不开, 说明某一臂没走到它该走的代码路径。
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
HASH_RE = re.compile(r"code_hash=([0-9a-f]+)")
STAGE_RE = re.compile(r"Stage means:\s*(.+)")
# ⚠️ 阶段耗时**不能**在「N 秒 · M 帧」的结果块里搜: 报告把三个 `Period: Ns / Stage means:`
# 段**全部排在三个结果块之后**, 所以 15s 那个块的范围内躺着 1s/5s/15s 三份 Stage means,
# 取第一个就把 **1s 的 physics 挂到了 15s 上**(而且 1s/5s 会拿到 None)。
# 必须按 `Period: Ns` → `Stage means:` 直接配对。
STAGE_BLOCK_RE = re.compile(r"Period: (\d+)s\s*\nFlow renderer: (\S+)\s*\nStage means: (.+)")
SPLASH_PEAK_RE = re.compile(r"飞溅峰值\s*(\d+)")
ROUND_RE = re.compile(r"_r(\d+)__")
# 跨轮可比性的判据: 窗口尺寸/刷新率/粒子峰值不同 ⇒ 两轮不是同一个基准, 不该进同一个中位数。
ENV_RE = re.compile(r"window_pixels=\(([^)]*)\).*?refresh_hz=([\d.]+)")
PEAK_RE = re.compile(r"峰值\s*(\d+)；飞溅")
# "越高越好"的指标; 其余越低越好
HIGHER_IS_BETTER = {"avg", "low1"}
# 阶段耗时(ms): 物理栏是 numpy 物理路径的直接证据 —— v1.21 走向量化时它约为 v1.2 的 0.4 倍。
# 两臂若在这栏上分不开, 说明对照组不是"真的那一版", 结论无效。
STAGE_KEYS = ("physics_ms", "update_draw_ms", "canvas_ms", "previous_swap_ms")
# 一段跑出低于这个平均帧率就判定该段没正常跑完(设备上出现过 n=1 的坏轮混在好轮里)
MIN_SANE_FPS = 30.0


def parse(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    rend = REND_RE.search(text)
    ver = VER_RE.search(text)
    chash = HASH_RE.search(text)
    env = ENV_RE.search(text)
    out = {"renderer": rend.group(1) if rend else "?",
           "version": ver.group(1) if ver else "?",
           "code_hash": chash.group(1) if chash else "?",
           "window": env.group(1).strip() if env else "?",
           "refresh": env.group(2) if env else "?",
           "peaks": [int(x) for x in PEAK_RE.findall(text)],
           "periods": {}}
    blocks = list(PERIOD_RE.finditer(text))
    for i, m in enumerate(blocks):
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        seg = text[m.end():end]
        a, q = AVG_RE.search(seg), QUANT_RE.search(seg)
        if not a:
            continue
        period = int(m.group(1))
        frames = int(m.group(2))
        avg = float(a.group(1))
        entry = {
            "frames": frames,
            "avg": avg, "low1": float(a.group(2)),
            "p50": float(q.group(1)) if q else float("nan"),
            "p90": float(q.group(2)) if q else float("nan"),
            "p99": float(q.group(3)) if q else float("nan"),
            "valid": avg >= MIN_SANE_FPS,
        }
        out["periods"][period] = entry
    # 阶段耗时/渲染器: 独立按 `Period: Ns` 配对, 别塞进上面的结果块(见 STAGE_BLOCK_RE 的注释)
    for m in STAGE_BLOCK_RE.finditer(text):
        period = int(m.group(1))
        entry = out["periods"].get(period)
        if entry is None:
            continue
        entry["renderer"] = m.group(2)
        for kv in m.group(3).split(", "):
            if "=" in kv:
                k, _, v = kv.partition("=")
                try:
                    entry[k.strip()] = float(v)
                except ValueError:
                    pass
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
        rm = ROUND_RE.search(base)
        runs.setdefault(arm, []).append((base, int(rm.group(1)) if rm else 0, parse(f)))
    return runs


def detect_arms(directory):
    """从 `<arm>_r<n>__<时间戳>.txt` 里认出实际用过的臂前缀。

    产物是按 git rev 命名的(7c8d094 / 94df8bc), 而这里的历史默认值是版本号(v1.2/v1.21),
    不带 --arm 直接跑会一个都匹配不上 —— 所以匹配失败时用它兜底, 而不是干瞪眼。
    """
    found = {}
    for f in sorted(glob.glob(os.path.join(directory, "*.txt"))):
        base = os.path.basename(f)
        if base.startswith("logcat_"):
            continue
        m = re.match(r"^(.*?)_r\d+__", base)
        if m:
            found[m.group(1)] = found.get(m.group(1), 0) + 1
    return sorted(found)


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
        cand = detect_arms(args.directory)
        if len(cand) >= 2:
            print(f"[提示] 没匹配到 `{args.arm_a}` / `{args.arm_b}`; 按文件名自动识别为 "
                  f"`{cand[0]}` / `{cand[1]}` (可用 --arm-a/--arm-b 覆盖)")
            args.arm_a, args.arm_b = cand[0], cand[1]
            runs = load(args.directory, args.arm_a, args.arm_b)
    if not runs:
        # 最常见的失败: 产物按 git rev 命名, 而这里默认按版本号匹配 ⇒ 一个都没命中。
        all_txt = [os.path.basename(f) for f in sorted(glob.glob(os.path.join(args.directory, "*.txt")))
                   if not os.path.basename(f).startswith("logcat_")]
        hint = ""
        if all_txt:
            hint = (f"\n目录里有 {len(all_txt)} 个 .txt 但都不以 `{args.arm_a}_` / `{args.arm_b}_` 开头, "
                    f"例如 {all_txt[0]}。\n按产物实际用的 rev 名再跑一次, 例如:\n"
                    f"  python tools/analyze_ab.py {args.directory} --arm-a <revA> --arm-b <revB>")
        sys.exit(f"{args.directory} 下没有可解析的 A/B 日志{hint}")

    for arm in sorted(runs):
        print(f"\n===== {arm} =====")
        for base, _rnd, r in sorted(runs[arm]):
            ps = "  ".join(
                f"{p}s: low1={d['low1']:.1f} p90={d['p90']:.2f} avg={d['avg']:.1f}"
                + (f" phys={d['physics_ms']:.2f}" if "physics_ms" in d else "")
                + ("" if d.get("valid", True) else " !!该段未正常跑")
                for p, d in sorted(r["periods"].items()))
            print(f"  {base[:40]:<40} ver={r['version']:<5} {r['renderer']:<22} "
                  f"hash={r.get('code_hash','?')} {ps}")
    rends = {r["renderer"] for v in runs.values() for _, _, r in v}
    if len(rends) > 1:
        print(f"\n[警告] 渲染器不一致 {sorted(rends)} —— 退化成 line 池的轮次不可比, 先剔除再下结论。")
    # 跨轮可比性: 窗口/刷新率/粒子峰值不同 ⇒ 不是同一个基准, 混进同一个中位数会静默失真。
    for field, label in (("window", "窗口尺寸"), ("refresh", "刷新率")):
        seen = {r[field] for v in runs.values() for _, _, r in v}
        if len(seen) > 1:
            print(f"[警告] {label}不一致 {sorted(seen)} —— 这些轮不是同一个基准, 不可比。")
    peaks = {}
    for arm, v in runs.items():
        for _, _, r in v:
            for p, pk in zip(sorted(r["periods"]), r["peaks"]):
                peaks.setdefault(p, {})[arm] = peaks.setdefault(p, {}).get(arm, []) + [pk]
    for p in sorted(peaks):
        vals = {a: (min(x), max(x)) for a, x in peaks[p].items()}
        rng = max(hi for lo, hi in vals.values()) - min(lo for lo, hi in vals.values())
        if len(vals) > 1 and rng > max(lo for lo, hi in vals.values()) * 0.1:
            print(f"[警告] {p}s 段粒子峰值两臂相差 {rng} —— 工作量不同, 不是同条件对照: {vals}")
    bad = [(b, p) for v in runs.values() for b, _, r in v
           for p, d in r["periods"].items() if not d.get("valid", True)]
    if bad:
        print(f"[警告] {len(bad)} 段平均帧率低于 {MIN_SANE_FPS:.0f}, 判为未正常跑并已剔除: {bad}")

    if len(runs) < 2:
        print("\n[警告] 只有一个臂有数据, 无法配对比较。")
        return

    keys = [("low1", "1%low"), ("p90", "p90(ms)"), ("p99", "p99(ms)")]
    keys += [(k, k) for k in STAGE_KEYS]
    if args.show_average:
        keys.append(("avg", "avg"))

    periods = sorted({p for v in runs.values() for _, _, r in v for p in r["periods"]})

    def vals(arm, period, key):
        return [r["periods"][period][key] for _, _, r in runs.get(arm, [])
                if period in r["periods"] and r["periods"][period].get("valid", True)
                and key in r["periods"][period]]

    print(f"\n===== 各自中位 ({args.arm_a} n={len(runs.get(args.arm_a, []))}, "
          f"{args.arm_b} n={len(runs.get(args.arm_b, []))}) =====")
    for p in periods:
        print(f"\n--- {p}s ---")
        for key, label in keys:
            a, b = vals(args.arm_a, p, key), vals(args.arm_b, p, key)
            if not a or not b:
                continue
            ma, mb = st.median(a), st.median(b)
            spread = max(max(a) - min(a), max(b) - min(b))
            better = "A更好" if ((ma > mb) == (key in HIGHER_IS_BETTER)) else "B更好"
            verdict = "可分辨" if abs(ma - mb) > spread else "在噪声内, 不可分辨"
            print(f"  {label:>14}: {args.arm_a}={ma:8.2f}  {args.arm_b}={mb:8.2f}  "
                  f"Δ={ma - mb:+7.2f}  组内极差={spread:6.2f}  {better}  {verdict}")
            print(f"           逐轮 {args.arm_a}={[round(x, 2) for x in a]}  "
                  f"{args.arm_b}={[round(x, 2) for x in b]}")

    # 按轮配对: A 的第 i 轮 vs B 的第 i 轮(时间上相邻 ⇒ 漂移被差掉)。
    # 比"各自中位数相减"抗噪得多: 项目自己的记录里, 同一份代码的组内极差能横跨 0.4~30 FPS,
    # 拿组内极差当标尺并不可校准, 所以配对数才是主判据; 上面那张表只作对照。
    by_round = {arm: {rnd: r for _, rnd, r in v} for arm, v in runs.items()}
    common = sorted(set(by_round.get(args.arm_a, {})) & set(by_round.get(args.arm_b, {})))
    print(f"\n===== 按轮配对差 (A−B; 可配 {len(common)} 轮: {common}) =====")
    if not common:
        print("  两臂没有同轮号的轮次, 无法配对 —— 检查每轮是否都成功产出。")
    for p in periods:
        print(f"\n--- {p}s ---")
        for key, label in keys:
            diffs, used = [], []
            for rnd in common:
                ra, rb = by_round[args.arm_a][rnd], by_round[args.arm_b][rnd]
                da, db = ra["periods"].get(p), rb["periods"].get(p)
                if not da or not db or not da.get("valid", True) or not db.get("valid", True):
                    continue
                if key not in da or key not in db:
                    continue
                diffs.append(da[key] - db[key])
                used.append(rnd)
            if not diffs:
                continue
            med = st.median(diffs)
            n = len(diffs)
            pos = sum(1 for d in diffs if d > 0)
            same = max(pos, n - pos)
            # 符号检验(双侧): n 对全同号时 p = 2·0.5^n
            pval = 2 * (0.5 ** n) if same == n else None
            winner = args.arm_a if (med > 0) == (key in HIGHER_IS_BETTER) else args.arm_b
            star = ""
            if same == n and n >= 5:
                star = f"  ← {n}/{n} 对同号(符号检验 p={pval:.3f})"
            elif same == n:
                star = f"  ← {n}/{n} 对同号(n 太小, p={pval:.3f}, 只能当方向提示)"
            print(f"  {label:>14}: 配对中位={med:+7.2f}  倾向={winner}  "
                  f"{same}/{n} 对同号{star}")
            print(f"           逐轮配对差(轮号 {used}) = {[round(d, 2) for d in diffs]}")

    print("\n读法: 1%low / avg 越大越好; p90 / p99 / 各阶段耗时(ms)越小越好。"
          "\n主判据是**按轮配对差**: 方向一致(同号对数多)且中位差不为零才算两臂可分;"
          "\n      n 小时(<=4)只当方向提示, 不要当结论。上面「各自中位」表里的组内极差只作对照。"
          "\n注意: physics_ms 是「对照组是不是真的那一版」的判别量 —— 两臂若在这栏上分不开,"
          "\n      说明某一臂没走到它该走的代码路径, 整轮结论无效(见 CLAUDE.md 的 flow_numpy 坑)。")


if __name__ == "__main__":
    main()
