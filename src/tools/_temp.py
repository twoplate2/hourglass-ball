# -*- coding: utf-8 -*-
"""`_temp/` —— **待删暂存区**(2026-10-13 立, 用户: 「临时暂时不能删的, 但是过几天就可以删的」)。

## 规矩(只四条)

1. **拿不准要不要留的东西, 一律扔 `_temp/`** —— 不要再散在根目录 / `tools/` / 仓库各处。
2. `_temp/` 不进 git，并由 buildozer.spec 显式排除 APK 打包。
3. **过几天再判**: `python src/tools/_temp.py list` 看有什么、多旧;
   `python src/tools/_temp.py sweep` **默认只删 7 天以上的**(干跑, 加 `--go` 才真删)。
4. benchmark_logs、retired-docs、retired-tools 留给用户审阅，不参与 sweep / clear。

## 用法

    python src/tools/_temp.py                  # = list: 列出内容 + 年龄 + 到期没到期
    python src/tools/_temp.py sweep            # 干跑: 列出 7 天以上、将被删的
    python src/tools/_temp.py sweep --go       # 真删(只删到期的)
    python src/tools/_temp.py sweep --days 3 --go
    python src/tools/_temp.py clear --go       # 清空(不问年龄)

判据是**目录项的 mtime**(一个子目录里只要还有新东西就整体不算到期), 不是里面每个文件。
"""
import argparse
import os
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMP = ROOT / "_temp"
PROTECTED_ENTRIES = frozenset({"benchmark_logs", "retired-docs", "retired-tools"})
README = """_temp/ = 待删暂存区

· 拿不准要不要留的东西扔这儿(别再散在根目录/tools/)
· 不进 git，也由 buildozer.spec 排除 APK 打包
· 过几天再判: python src/tools/_temp.py list / sweep [--days N] [--go]
· benchmark_logs、retired-docs、retired-tools 由用户审阅后手动删除，不参与清理
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


def cleanup_candidates(items, command, days):
    return [p for p in items if p.name not in PROTECTED_ENTRIES
            and (command == "clear" or age_days(p) >= days)]


def remove_entry(path):
    target = path.resolve()
    if target.parent != TEMP.resolve() or path.is_symlink():
        raise ValueError("Refusing cleanup outside the temporary directory")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def sweep_junk(go: bool) -> int:
    """顺手清**可再生垃圾**: 仓库里所有 `__pycache__/` 与 `*.pyc`(与 `_temp/` 无关)。

    它们 100% 可再生(下次 import 就回来); 测试配置由各探针的临时目录管理。
    """
    hits = []
    for dp, dn, fn in os.walk(ROOT):
        if ".git" in dp.split(os.sep) or "_temp" in dp.split(os.sep):
            continue
        for d in list(dn):
            if d == "__pycache__":
                hits.append(pathlib.Path(dp) / d)
                dn.remove(d)
        for f in fn:
            if f.endswith(".pyc"):
                hits.append(pathlib.Path(dp) / f)
    n = sum(size_of(p) for p in hits)
    print("可再生垃圾(__pycache__ / *.pyc): %d 项, %.1f MB" % (len(hits), n / 1e6))
    if go:
        for p in hits:
            if p.is_dir():
                if not p.resolve().is_relative_to(ROOT.resolve()) or p.is_symlink():
                    raise ValueError("Refusing cache cleanup outside the repository")
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    return len(hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="list", choices=("list", "sweep", "clear"))
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    ensure()
    junk = sweep_junk(a.go)
    items = entries()
    protected = [p for p in items if p.name in PROTECTED_ENTRIES]
    if protected:
        print("用户审阅保留(不清理): " + ", ".join(p.name for p in protected))
    if not items:
        print("_temp/ 是空的(规矩见 _temp/README.txt)")
        print("== 干跑。加 --go 真删 ==" if junk and not a.go else "")
        return 0
    if a.cmd == "list":
        print("_temp/ 共 %d 项, %.1f MB —— `sweep` 的到期线 = %g 天"
              % (len(items), sum(size_of(p) for p in items) / 1e6, a.days))
        for p in items:
            d = age_days(p)
            print("  %-38s %8.2f MB  %6.1f 天  %s"
                  % (p.name, size_of(p) / 1e6, d, "**已到期**" if d >= a.days else "留"))
        return 0
    todo = cleanup_candidates(items, a.cmd, a.days)
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
        remove_entry(p)
    print("已删。剩余 %d 项" % len(entries()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
