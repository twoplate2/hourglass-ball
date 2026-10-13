# -*- coding: utf-8 -*-
"""仓库整理: 删"可再生 / 一次性 / 纯垃圾", 保留"不可再生的证据 + 活工具"。

用法:
    python tools/_tidy_repo.py            # 干跑(只打印, 不删)
    python tools/_tidy_repo.py --go       # 真删, 并写清单 _cleanup_manifest.txt

判据(每一条都能复算):
  · `benchmark_logs/flow_visual_*` = 渲染输出, **一条命令就能重跑** ⇒ 可再生, 删
  · `benchmark_logs/<根 .txt>` / `dev/` / `ab/` = **设备实测日志**, 不可再生 ⇒ 留
  · `_dl/ _vid/ _dev/ _refs/ _rec/` = 用户录像抽帧 + 设备截图 + 对照图, 不可再生 ⇒ 留
  · `_review/r*/ u1/ bug_completion/ shape/` = 已完结面板的整批截图 ⇒ 删(转写 .txt/.md 留)
  · `_shot/r19..r37` 等 = 设备的成批截图, **CLAUDE.md 点名的那些留**, 其余删
  · 根目录 `_p1*_main.py` 等 = 面板 agent 留下的 main.py 整份副本(已进 .gitignore) ⇒ 删
"""
import os
import shutil
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
GO = "--go" in sys.argv
manifest = []


def size_of(p):
    p = pathlib.Path(p)
    if p.is_file():
        return p.stat().st_size
    t = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                t += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return t


def drop(p, why):
    p = pathlib.Path(p)
    if not p.exists():
        return 0
    n = size_of(p)
    manifest.append((str(p).replace("\\", "/"), n, why))
    if GO:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    return n


# ---------------------------------------------------------------- 根目录
ROOT_JUNK = [
    "_tmp_probe_bottom.pyc", "_tmp_probe_end_sand.pyc",
    "_cr_v165.py", "_rev_nofeather.py", "_rev_nolip.py",
    "_p1_k9_main.py", "_p1_k9b_main.py", "_p1v2_main_up.py",
    "_p1v3_necklog_main.py", "_p1v3_proto_main.py",
    "_a1_fade_probe.py", "_probe_bottom.py", "_probe_end_sand.py",
    "luodi.md", "__pycache__",
]
# ---------------------------------------------------------------- benchmark_logs
BL_KEEP_DIRS = {"dev", "ab", "_dl", "_vid", "_dev", "_refs", "_rec"}
BL_KEEP_FLOW = ("flow_visual_N1", "flow_visual_TG", "flow_visual_n1")   # 本轮候选的证据
# ---------------------------------------------------------------- _shot 保留名单(CLAUDE.md 点名)
SHOT_KEEP = {"band", "bandcap", "r31b", "rehost", "rough",
             "sand_finish_5", "sandmat", "splash_ab", "splashpop",
             "devmenu_check.png", "r38"}
# `r37` 只有 `r37/ab/` 被点名 ⇒ 只留那个子目录
SHOT_KEEP_SUB = {"r37": {"ab"}}
# `_review` 里点名/不可再生的: 转写与设备剖面 JSON, 以及 r38_out
REVIEW_KEEP = {"r38_out"}
# benchmark_logs 根层被 CLAUDE.md 点名的图/页
BL_KEEP_FILES = {"_AB_neck_15_750.png", "marker_ab.png", "_neck_mold_4panel.png",
                 "_neck_ab.png", "_linew.png", "_ci_watch.html"}


def main():
    tot = 0
    # 根目录
    for n in ROOT_JUNK:
        tot += drop(ROOT / n, "根目录杂项(面板副本/pyc/一次性探针)")
    # benchmark_logs
    bl = ROOT / "benchmark_logs"
    if bl.exists():
        for q in bl.iterdir():
            if q.is_dir():
                if q.name in BL_KEEP_DIRS or q.name.startswith(BL_KEEP_FLOW):
                    continue
                if q.name.startswith("flow_visual"):
                    tot += drop(q, "渲染输出(可重跑)")
                elif q.name in ("_tmp", "_apk"):
                    tot += drop(q, "临时/旧 APK")
                else:
                    tot += drop(q, "一次性目录")
            elif q.suffix == ".png" or q.suffix == ".gif":
                if q.name in BL_KEEP_FILES:
                    continue
                tot += drop(q, "散落的图(未引用)")
    # _review: 留转写, 删截图批
    rv = ROOT / "_review"
    if rv.exists():
        for q in rv.iterdir():
            if q.is_dir() and q.name not in REVIEW_KEEP and (
                    q.name.startswith(("r", "u1", "bug_", "shape"))
                    or q.name == "__pycache__"):
                tot += drop(q, "已完结面板的截图批")
    # _shot: 只留点名的那几个
    sh = ROOT / "_shot"
    if sh.exists():
        for q in sh.iterdir():
            if q.name in SHOT_KEEP:
                continue
            if q.name in SHOT_KEEP_SUB and q.is_dir():
                for s in q.iterdir():                  # r37: 只留 ab/
                    if s.name not in SHOT_KEEP_SUB[q.name]:
                        tot += drop(s, "上几轮的设备截图批(未引用)")
                continue
            if q.name == "__pycache__":
                continue
            tot += drop(q, "上几轮的设备截图批(未引用)")
    print("== %s ==" % ("已删除" if GO else "将要删除(干跑)"))
    by = {}
    for path, n, why in manifest:
        by.setdefault(why, [0, 0])
        by[why][0] += 1
        by[why][1] += n
    for why, (c, n) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print("  %-34s %4d 项  %9.1f MB" % (why, c, n / 1e6))
    print("  %-34s %4d 项  %9.1f MB" % ("合计", len(manifest), tot / 1e6))
    if GO:
        out = ROOT / "benchmark_logs" / "_cleanup_manifest.txt"
        with open(out, "w", encoding="utf-8") as fh:
            for path, n, why in sorted(manifest, key=lambda r: -r[1]):
                fh.write("%12.3f MB  %-40s %s\n" % (n / 1e6, why, path))
        print("清单 ->", out)


main()
