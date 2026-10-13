# -*- coding: utf-8 -*-
"""按运行时代码白名单生成 source.md，供没有仓库的读者查阅。

用法: python src/tools/_pack_source_md.py
输出: docs/generated/source.md；可通过 --output 指定其他输出位置。

不含文档、二进制素材、测量产物和历史代码副本。
"""
import argparse
import os
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = Path(ROOT) / "docs" / "generated" / "source.md"
LANG = {".py": "python", ".sh": "bash", ".spec": "ini", ".yml": "yaml", ".yaml": "yaml",
        ".toml": "toml", ".cfg": "ini", ".ini": "ini"}

# **只打包运行期真正会执行的文件**(按调用链顺序)。
#   main.py 启动 → import app_version / frame_benchmark
#             → _install_flow_renderer() 按 importlib 动态装载 tools/flow_texture_experiment
#             → 它再 import tools/flow_batch_experiment
#             → 粒子数 >= 800 时 main.py 调 tools/flow_numpy.step()
# 其余 tools/*.py 都是宿主机上的测量/取图/测试工具, 不在设备上跑。
CODE_ORDER = ["main.py", "src/app/__init__.py", "src/app/app_version.py",
              "src/app/stream_rendering.py",
              "src/app/frame_benchmark.py", "src/app/flow_numpy.py",
              "src/app/flow_texture_experiment.py", "src/app/flow_batch_experiment.py",
              "src/app/sand_flow_material.py", "src/app/flow_splash_experiment.py",
              "src/app/flow_gpu_experiment.py", "src/app/marker_batch_experiment.py",
              "src/app/splash_motion.py", "src/app/prof_android.py",
              "src/app/_tail_selfcheck.py"]


def discover_code():
    for rel in CODE_ORDER:
        if not (Path(ROOT) / rel).is_file():
            raise FileNotFoundError(rel)
    return list(CODE_ORDER)


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
    return (Path(ROOT) / rel).read_text(encoding="utf-8", errors="replace")




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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    output = parser.parse_args(argv).output
    docs = discover_docs()
    code = discover_code()
    parts = []
    parts.append("# 沙漏 App · 完整资料包（source.md）\n\n")
    parts.append(
        "> 由 `src/tools/_pack_source_md.py` 自动生成：把**运行期代码**逐字打进这一个文件，\n"
        "> 供没有代码仓库的读者查阅。\n"
        "> **不含任何 .md 文档** —— 求助信是单独一份，内部复盘是我们的东西（见 `discover_docs`）。\n"
        ">\n"
        "> **不含**：二进制资源（`*.wav` / `*.png` / `*.otf`）、测量产物\n"
        "> （`_temp/benchmark_logs/`）、构建与测量工具。\n"
        "> **界面不需要截图**——UI 完全由 `main.py::HourglassApp.build()` 定义，可自行还原。\n"
        ">\n"
        "> 一帧的调用链：`main.py::tick()` → `update_particles()`（物理）→\n"
        "> `redraw()` → `_group_stream_particles()`（打包）→\n"
        "> `src/app/flow_texture_experiment.py::TextureFlowBatch.update()`（上传）。\n\n")

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

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(parts), encoding="utf-8")
    print("打包 %d 文档 + %d 代码 = %d 文件 / %d 行 -> %.0f KB"
          % (len(docs), len(code), len(docs) + len(code), tn, output.stat().st_size / 1024.0))
    print("文档:", ", ".join(docs))
    print("输出:", output)


if __name__ == "__main__":
    main()
