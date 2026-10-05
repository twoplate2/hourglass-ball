# -*- coding: utf-8 -*-
"""把仓库里的代码 + 项目文档打包成单个 source.md(给外部专家看)。

用法: python tools/_pack_source_md.py
输出: 仓库根 source.md

排除: 二进制资源(*.wav/*.png/*.otf)、测量产物(benchmark_logs/)、
      一次性生成脚本(文件名以 _ 开头)、以及用户自己的两份备注
      (jiaojie.md / shengyin_lianxu.md —— 想加就把它们从 EXCLUDE_DOCS 里去掉)。
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "source.md")

SKIP_DIRS = {".git", "benchmark_logs", "__pycache__", "mp3", "sounds", "fonts", "ui",
             "bin", "dist", "build", ".kivy"}
CODE_EXT = {".py", ".sh", ".spec", ".yml", ".yaml", ".toml", ".cfg", ".ini"}
LANG = {".py": "python", ".sh": "bash", ".spec": "ini", ".yml": "yaml", ".yaml": "yaml",
        ".toml": "toml", ".cfg": "ini", ".ini": "ini"}

# 只要代码: 一份文档都不进包(求助信 help_fps.md 是单独一份, 也不进)。
# 只打包**还在用**的文档。其余(ICON.md 图标 / hengping.md 横屏 / AGENTS.md 工作规则 /
# HANDOFF.md 临时交接 / NECK_REDESIGN.md 已结案的视觉复盘 / jiaojie.md 与
# shengyin_lianxu.md 用户自己的备注)一律不进包。
EXCLUDE_DOCS = {"jiaojie.md", "shengyin_lianxu.md", "source.md",
                "meishu.md", "q1.md", "xingzhuang.md",   # 求助信: 单独发, 不进代码包
                "ICON.md", "hengping.md", "AGENTS.md",
                "HANDOFF.md", "NECK_REDESIGN.md",
                "help_fps.md", "CLAUDE.md", "README.md", "NUMPY_PLAN.md"}  # 只要代码

# 文档阅读顺序(求助简报放最前)
DOC_ORDER = ["help_fps.md", "CLAUDE.md", "README.md", "NECK_REDESIGN.md",
             "NUMPY_PLAN.md", "HANDOFF.md", "ICON.md", "hengping.md", "AGENTS.md"]

# **只打包运行期真正会执行的文件**(按调用链顺序)。
#   main.py 启动 → import app_version / frame_benchmark
#             → _install_flow_renderer() 按 importlib 动态装载 tools/flow_texture_experiment
#             → 它再 import tools/flow_batch_experiment
#             → 粒子数 >= 800 时 main.py 调 tools/flow_numpy.step()
# 其余 tools/*.py 都是宿主机上的测量/取图/测试工具, 不在设备上跑。
CODE_ORDER = ["main.py", "app_version.py", "frame_benchmark.py",
              "tools/flow_numpy.py", "tools/flow_texture_experiment.py",
              "tools/flow_batch_experiment.py"]
RUNTIME_ONLY = set(CODE_ORDER)


def discover_code():
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, ROOT).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in CODE_EXT or fn.startswith("_"):
                continue
            found.append((rel_dir + "/" + fn).lstrip("/"))
    wf = os.path.join(ROOT, ".github", "workflows")
    if os.path.isdir(wf):
        for fn in sorted(os.listdir(wf)):
            if os.path.splitext(fn)[1].lower() in CODE_EXT:
                found.append(".github/workflows/" + fn)

    found = [r for r in set(found) if r in RUNTIME_ONLY]

    def key(rel):
        return CODE_ORDER.index(rel)
    return sorted(found, key=key)


def discover_docs():
    """**一份文档都不打** —— 这个包只给"代码"(2026-10-04 用户裁决)。

    ⚠️ 原来会把根目录所有 *.md 都塞进来, 于是 `meishu.md`(我写给他的美术信,
    里面 §7 还是对他上一封回复的**逐条反驳**)与 `q1.md`(帧率续问, 与形状无关)
    都被打进"代码包"。用户原话:「加了一堆对方没有必要看的东西」。
    求助信(help_fps.md / q1.md / meishu.md / xingzhuang.md)是**单独发**的,
    内部复盘(CLAUDE.md / README.md / HANDOFF.md / NECK_REDESIGN.md ...)是我们的东西,
    都不该出现在给外部专家的"代码"包里。要改回带文档, 请显式给白名单。
    """
    return []


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()




def fence(text):
    """用比正文里最长反引号串更长的围栏, 避免嵌套围栏把文档截断。"""
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    n = max(3, longest + 1)
    return "`" * n


def emit(parts, rel, kind):
    text = read(rel).rstrip()
    lang = LANG.get(os.path.splitext(rel)[1].lower(), "") if kind == "code" else "markdown"
    n = text.count("\n") + 1
    nb = len(text.encode("utf-8"))
    f = fence(text)
    parts.append("## `%s`\n\n*%d 行 / %s 字节 —— 逐字原文, 未删改*\n\n" % (rel, n, format(nb, ",")))
    parts.append("%s%s\n%s\n%s\n\n" % (f, lang, text, f))
    return n, nb


def main():
    docs = discover_docs()
    code = discover_code()
    parts = []
    parts.append("# 沙漏 App · 完整资料包（source.md）\n\n")
    parts.append(
        "> 由 `tools/_pack_source_md.py` 自动生成：把**运行期代码**逐字打进这一个文件，\n"
        "> 供没有代码仓库的读者查阅。\n"
        "> **不含任何 .md 文档** —— 求助信是单独一份，内部复盘是我们的东西（见 `discover_docs`）。\n"
        ">\n"
        "> **不含**：二进制资源（`*.wav` / `*.png` / `*.otf`）、测量产物\n"
        "> （`benchmark_logs/`）、一次性生成脚本（文件名以 `_` 开头）。\n"
        "> **界面不需要截图**——UI 完全由 `main.py::HourglassApp.build()` 定义，可自行还原。\n"
        ">\n"
        "> 一帧的调用链：`main.py::tick()` → `update_particles()`（物理）→\n"
        "> `redraw()` → `_group_stream_particles()`（打包）→\n"
        "> `tools/flow_texture_experiment.py::TextureFlowBatch.update()`（上传）。\n\n")

    parts.append("## 目录\n\n")
    tn = tb = 0
    if docs:
        parts.append("### 第一部分 · 文档\n\n| # | 文件 | 行 | 字节 |\n|---|---|---|---|\n")
        for i, rel in enumerate(docs, 1):
            t = read(rel).rstrip()
            n, nb = t.count("\n") + 1, len(t.encode("utf-8"))
            tn += n; tb += nb
            parts.append("| %d | `%s` | %d | %s |\n" % (i, rel, n, format(nb, ",")))
        parts.append("\n")
    parts.append(("### 第二部分 · 代码\n\n" if docs else "")
                 + "| # | 文件 | 行 | 字节 |\n|---|---|---|---|\n")
    for i, rel in enumerate(code, 1):
        t = read(rel).rstrip()
        n, nb = t.count("\n") + 1, len(t.encode("utf-8"))
        tn += n; tb += nb
        parts.append("| %d | `%s` | %d | %s |\n" % (i, rel, n, format(nb, ",")))
    parts.append("\n**共 %d 个文件 / %d 行 / %s 字节**\n\n---\n\n"
                 % (len(docs) + len(code), tn, format(tb, ",")))

    if docs:
        parts.append("# 第一部分 · 文档\n\n")
        for rel in docs:
            emit(parts, rel, "doc")
    parts.append(("# 第二部分 · 代码\n\n" if docs else "# 代码\n\n"))
    for rel in code:
        emit(parts, rel, "code")

    io.open(OUT, "w", encoding="utf-8").write("".join(parts))
    print("打包 %d 文档 + %d 代码 = %d 文件 / %d 行 -> %.0f KB"
          % (len(docs), len(code), len(docs) + len(code), tn, os.path.getsize(OUT) / 1024.0))
    print("文档:", ", ".join(docs))
    print("排除:", ", ".join(sorted(EXCLUDE_DOCS)))


if __name__ == "__main__":
    main()
