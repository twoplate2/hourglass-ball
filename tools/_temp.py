# -*- coding: utf-8 -*-
"""`_temp/` —— **待删暂存区**(2026-10-13 立, 用户: 「临时暂时不能删的, 但是过几天就可以删的」)。

## 规矩(只四条)

1. **拿不准要不要留的东西, 一律扔 `_temp/`** —— 不要再散在根目录 / `tools/` / 仓库各处。
2. `_temp/` **不进 git**(`.gitignore` 里有) ⇒ 也就**不会进 CI 的 APK**
   (⚠️ 但 buildozer 是 `source.dir = .` + `include_exts` 含 `py`: **本地**打包时它照样会被
   拷进去。CI 只认 git 里的东西, 所以线上无影响)。
3. **过几天再判**: `python tools/_temp.py list` 看有什么、多旧;
   `python tools/_temp.py sweep` **默认只删 7 天以上的**(干跑, 加 `--go` 才真删)。
4. 想**永久保留**的东西别放这儿 —— 放 `tools/`(工具) / `benchmark_logs/`(证据) /
   `design/`(给用户看的页)。`_temp/` 里的任何东西都可能被下一次 sweep 清掉。

## 用法

    python tools/_temp.py                  # = list: 列出内容 + 年龄 + 到期没到期
    python tools/_temp.py sweep            # 干跑: 列出 7 天以上、将被删的
    python tools/_temp.py sweep --go       # 真删(只删到期的)
    python tools/_temp.py sweep --days 3 --go
    python tools/_temp.py clear --go       # 清空(不问年龄)

判据是**目录项的 mtime**(一个子目录里只要还有新东西就整体不算到期), 不是里面每个文件。
"""
import argparse
import os
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMP = ROOT / "_temp"
README = """_temp/ = 待删暂存区

· 拿不准要不要留的东西扔这儿(别再散在根目录/tools/)
· 不进 git ⇒ 不进 CI 的 APK; 想永久保留的请放 tools/ 或 benchmark_logs/
· 过几天再判: python tools/_temp.py list / sweep [--days N] [--go]
"""


def size_of(p: pathlib.Path) -> int:
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


def age_days(p: pathlib.Path) -> float:
    return (time.time() - p.stat().st_mtime) / 86400.0


def ensure():
    TEMP.mkdir(exist_ok=True)
    r = TEMP / "README.txt"
    if not r.exists():
        r.write_text(README, encoding="utf-8")


def entries():
    if not TEMP.exists():
        return []
    return sorted((p for p in TEMP.iterdir() if p.name != "README.txt"),
                  key=lambda p: p.stat().st_mtime)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="list", choices=("list", "sweep", "clear"))
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    ensure()
    items = entries()
    if not items:
        print("_temp/ 是空的(规矩见 _temp/README.txt)")
        return 0
    if a.cmd == "list":
        print("_temp/ 共 %d 项, %.1f MB —— `sweep` 的到期线 = %g 天"
              % (len(items), sum(size_of(p) for p in items) / 1e6, a.days))
        for p in items:
            d = age_days(p)
            print("  %-38s %8.2f MB  %6.1f 天  %s"
                  % (p.name, size_of(p) / 1e6, d, "**已到期**" if d >= a.days else "留"))
        return 0
    todo = items if a.cmd == "clear" else [p for p in items if age_days(p) >= a.days]
    if not todo:
        print("没有到期的(线 = %g 天); 现有 %d 项都还新" % (a.days, len(items)))
        return 0
    print("%s %d 项, %.1f MB:" % ("清空" if a.cmd == "clear" else "删到期的",
                                  len(todo), sum(size_of(p) for p in todo) / 1e6))
    for p in todo:
        print("  %-38s %8.2f MB  %6.1f 天" % (p.name, size_of(p) / 1e6, age_days(p)))
    if not a.go:
        print("== 干跑。加 --go 真删 ==")
        return 0
    for p in todo:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    print("已删。剩余 %d 项" % len(entries()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
