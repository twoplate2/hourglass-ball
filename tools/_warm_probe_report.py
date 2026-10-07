# -*- coding: utf-8 -*-
"""`tools/_warm_probe_arms.sh` 的判读: **预热有没有把"起跑那一帧"的建块代价拿掉**。

判据(全部来自日志里的逐帧 CSV, 与帧率无关 —— 这台模拟器的呈现节拍锁在 60):

1. **起跑后第一秒里 `图元` 的最大值** —— 预热前是 20~45ms(建块那一帧), 预热后应回到
   稳态量级(约 3ms)。主判据。
2. **`index_assigns` 之和** —— 预热把每块的索引一次给满(`part[4] = CHUNK`), 而"整块置空"
   也从"清索引"改成"中性化"(不清索引) ⇒ 运行期的**索引重赋(整块顶点表重建)应当≈0**。
3. **`chunk_clears`** —— 只该改"置空的方式", 不该改频率。

跑法: python tools/_warm_probe_report.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "benchmark_logs" / "warmprobe"

# CSV 列(见 frame_benchmark.py 的 "Frame trace:" 表头)
C_T, C_UD, C_PART, C_IA, C_CC = 0, 4, 7, 15, 16


def parse(path):
    """→ (warm 状态, {档位: [每帧行]})。

    ⚠️ **日志里的 `Frame trace:` 只有一份, 三档的帧首尾相接拼在一起**
    (实测行数 = 62 + 302 + 901 = 1265 = 三档帧数和), 而 `time_s` 每档**从头开始**
    ⇒ 靠 **t 回退**切档(那份 CSV 跟在最后一个 section 后面, 按 section 头行归属会全算到最后一档)。
    """
    warm = None
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("环境:"):
            for tok in line.split("/"):
                tok = tok.strip()
                if tok.startswith("warm="):
                    warm = tok.split("=", 1)[1]
            break
    periods = []
    for line in text.splitlines():
        if line[:2].strip().isdigit() and " 秒" in line[:8]:
            periods.append(line.split()[0])
    runs = {p: [] for p in periods}
    idx, last_t = 0, -1.0            # ⚠️ 从 0 起 —— 从 -1 起会把**第一档整段丢掉**
    for line in text.splitlines():
        if line[:1].isdigit() and line.count(",") == 18:
            row = [float(x) for x in line.split(",")]
            if row[0] < last_t:                      # 时间回退 ⇒ 进下一档
                idx = min(idx + 1, len(periods) - 1)
            last_t = row[0]
            runs[periods[idx]].append(row)
    return warm, runs


def summarize(name, path):
    warm, runs = parse(path)
    out = {"warm": warm, "name": name}
    print("  %-8s warm=%s" % (name, warm))
    for period in sorted(runs, key=lambda s: float(s)):
        rows = runs[period]
        if not rows:
            continue
        head = [r for r in rows if r[C_T] <= 1.0] or rows
        worst = max(head, key=lambda r: r[C_UD])
        first = next((r for r in rows if r[C_PART] > 0), rows[0])
        out[period] = {
            "ud_max": worst[C_UD], "ud_max_t": worst[C_T],
            "ud_first": first[C_UD], "first_t": first[C_T],
            "ia": sum(r[C_IA] for r in rows),
            "cc": sum(r[C_CC] for r in rows),
            "ia_per_frame": sum(r[C_IA] for r in rows) / len(rows),
        }
        print("    %4ss 帧%5d | 首秒 图元 max %6.2fms @t=%.3fs | **第一帧有内容** t=%.3fs 图元 %5.2fms"
              " | index_assigns %5d (%.2f/帧) | chunk_clears %4d"
              % (period, len(rows), worst[C_UD], worst[C_T], first[C_T], first[C_UD],
                 out[period]["ia"], out[period]["ia_per_frame"], out[period]["cc"]))
    return out


def main():
    files = sorted(D.glob("w*.txt"))
    if not files:
        raise SystemExit("没有日志 —— 先跑 bash tools/_warm_probe_arms.sh")
    print("")
    arms = [summarize(f.stem, f) for f in files]
    ons = [a for a in arms if a["warm"] == "1"]
    offs = [a for a in arms if a["warm"] == "0"]
    print("")
    if not ons or not offs:
        print("!! 两臂不齐(warm=1 与 warm=0 各需至少一份) ⇒ 本次不作数")
        return 1
    print("  == 配对判读(每档: 关 → 开) ==")
    for period in ("1", "5", "15"):
        a = [o[period] for o in offs if period in o]
        b = [o[period] for o in ons if period in o]
        if not a or not b:
            continue
        print("    %4ss  首秒图元max  %.2f → %.2f   |  第一帧有内容  %.2f → %.2f"
              "   |  index_assigns/帧  %.2f → %.2f"
              % (period,
                 sum(x["ud_max"] for x in a) / len(a), sum(x["ud_max"] for x in b) / len(b),
                 sum(x["ud_first"] for x in a) / len(a), sum(x["ud_first"] for x in b) / len(b),
                 sum(x["ia_per_frame"] for x in a) / len(a),
                 sum(x["ia_per_frame"] for x in b) / len(b)))
    print("")
    print("  ⚠️ 平均帧率/1% low 在这台模拟器上是死指标(呈现节拍锁 60), 判据只用上面两栏。")
    return 0


sys.exit(main())
