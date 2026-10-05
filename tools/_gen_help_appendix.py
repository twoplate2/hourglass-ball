# -*- coding: utf-8 -*-
"""一次性脚本: 重新生成 help_fps.md 的 §11 核心代码附录(逐字, 按 AST 截取)。

不用 `python -c`: 那段文本里有反引号, 会被 shell 当成命令替换吃掉。
用法: python tools/_gen_help_appendix.py
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(ROOT, "help_fps.md")
MARK = "## 11. 附录"


def spans(path, names):
    src = io.open(os.path.join(ROOT, path), encoding="utf-8").read()
    lines = src.splitlines()
    tree = ast.parse(src)
    found = {}

    def walk(node):
        for n in node.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(n.name, []).append((n.lineno, n.end_lineno))
            elif isinstance(n, ast.ClassDef):
                found.setdefault(n.name, []).append((n.lineno, n.end_lineno))
                walk(n)

    walk(tree)
    return lines, [sp for k in names for sp in found.get(k, [])]


def block(path, names, title):
    if not names:                       # names 为空 = 整个文件逐字内嵌
        lines = io.open(os.path.join(ROOT, path), encoding="utf-8").read().splitlines()
        sps = [(1, len(lines))]
    else:
        lines, sps = spans(path, names)
    if not sps:
        return "### %s\n\n*(未找到)*\n\n" % title
    body = []
    for a, b in sps:
        body.extend(lines[a - 1:b])
        body.append("")
    where = ", ".join("%d-%d" % (a, b) for a, b in sps)
    return ("### %s\n\n*来源: `%s` 第 %s 行(逐字原文, 未删改)*\n\n"
            "```python\n%s\n```\n\n" % (title, path, where, "\n".join(body).rstrip()))


def build():
    p = []
    p.append("## 11. 附录：核心代码（逐字原文）\n\n")
    p.append(
        "> **对方手上没有代码仓库**，所以这里把决定每帧成本的全部代码**逐字内嵌**\n"
        "> （按 AST 精确截取，未做任何删改或简化）。省略的只有 UI 布局、音效、弹窗、\n"
        "> 配置读写 —— 那些不在每帧路径上。\n"
        ">\n"
        "> 文件对应：`main.py` 3155 行 / `frame_benchmark.py` 590 行 /\n"
        "> `tools/flow_numpy.py` 125 行 / `tools/flow_texture_experiment.py` 213 行。\n"
        ">\n"
        "> 一帧的调用链：`tick()` → `update_particles()`（物理）→ `redraw()` →\n"
        "> `_group_stream_particles()`（打包）→ `flow_texture_experiment.update()`（上传）→\n"
        "> 下一帧 `tick()`。\n\n")
    p.append(block("main.py", ["tick", "_spawn_dust"],
                   "11.1 帧循环 `tick()` 与完成尘埃"))
    p.append(block("main.py", ["update_particles"],
                   "11.2 粒子物理 `update_particles()` —— **占比最大的一栏（1.46ms）**"))
    p.append(block("tools/flow_numpy.py", [],
                   "11.3 numpy 物理内核 `tools/flow_numpy.py` —— **全文**"))
    p.append(block("tools/flow_texture_experiment.py", [],
                   "11.4 端点纹理渲染器 `tools/flow_texture_experiment.py` —— **全文**"))
    p.append(block("main.py", ["_group_stream_particles", "_draw_stream", "redraw",
                               "_build_dynamic_canvas", "_reserve_stream_lines", "_sync_rects"],
                   "11.5 每帧提交 `redraw()` 及其帮手"))
    p.append(block("main.py", ["_draw_neck_grains", "_hide_neck_grains"],
                   "11.6 颈部颗粒（固定 128 图元池）"))
    p.append(block("main.py", ["_newbuf", "_p_alloc", "_p_grow", "_p_refresh_view"],
                   "11.7 并行数组与 numpy 视图"))
    p.append(block("main.py", ["_bezier2", "neck_w", "speed_factor", "_rebuild_height_table",
                               "_raw_height_ratio", "_fall_delay", "_particle_motion_scale",
                               "_effective_fallen", "_mound_floor", "_mound_height_px",
                               "_neck_sand_side", "_sand_half_w"],
                   "11.8 几何与“假物理”（含**几何随 size 缩放**）"))
    p.append(block("frame_benchmark.py", ["benchmark_environment", "frame_statistics",
                                          "BenchmarkRunner"],
                   "11.9 基准装置（**请审这一节的测法**）"))
    p.append(block("main.py", ["_install_flow_renderer"],
                   "11.10 渲染器装载与回退路径"))
    p.append(block("main.py", ["_FlowView", "HourglassWidget.__init__"],
                   "11.11 状态与缓冲区初始化"))
    return "".join(p)


def main():
    text = io.open(MD, encoding="utf-8").read()
    idx = text.find(MARK)
    body = text[:idx].rstrip() if idx >= 0 else text.rstrip()
    appendix = build()
    io.open(MD, "w", encoding="utf-8").write(body + "\n\n---\n\n" + appendix)
    print("正文 %d 行 + 附录 %d 行" % (body.count("\n") + 1, appendix.count("\n")))


if __name__ == "__main__":
    main()
