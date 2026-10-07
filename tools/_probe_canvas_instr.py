# -*- coding: utf-8 -*-
"""**画布指令的精确计数**(2026-10-07) —— 按"族"和"类型"两个维度数, 不猜。

## 为什么需要它

`_probe_canvas_cost.py` 是**消融法**(摘掉一族量时间差), 它有两个毛病:
① 时间差有噪声, 小的族量不出来; ② 1.204 之后 `_mound_*` 被并成 `Mesh`,
**摘不到任何指令却照常打印一个数**。

而"指令条数"是**逐条确定的**: 同一份代码跑几次完全一样。凡优化是"把 N 条并成 1 条",
**条数的下降就是最硬的证据**(与 `prof_android.py` 的 `calls/frame` 同理)。

## 数什么

从 `widget.canvas` 递归展开 `children`, 按**指令类型**和**它挂在哪个顶层族里**分别计数。
Kivy 的 `Mesh` / `Line` / `Ellipse` / `Rectangle` 是叶子; `InstructionGroup` / `RenderContext`
是容器(本身也各算一条 —— 它们每帧也要走 `apply()`)。

跑法: python tools/_probe_canvas_instr.py [周期=15]
"""
import collections
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"
    os.environ.setdefault("HG_SPLASH_RENDERER", "batch")
    os.environ.setdefault("HG_NECK_RENDERER", "batch")
    sys.argv = sys.argv[:1] + ["_instr"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

    box = {}

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            hg.set_duration(PERIOD)
            hg._rebuild_height_table()
            hg.reset()
            hg.toggle()
            for _ in range(int(PERIOD * 60 * 0.5)):
                hg.elapsed += 1 / 60.0
                hg.tick(1 / 60.0)

            def walk(node, top, ty, fam):
                for ch in getattr(node, "children", ()):
                    name = type(ch).__name__
                    ty[name] = ty.get(name, 0) + 1
                    fam[top][name] += 1
                    walk(ch, top, ty, fam)

            ty = collections.Counter()
            fam = collections.defaultdict(collections.Counter)
            # each top-level child of widget.canvas is counted as its own "family"
            def label(ch, i):
                nm = type(ch).__name__
                for attr, tag in (("_flow_texture_context", "flow"),
                                  ("_neck_context", "neck"),
                                  ("_surface_marker_group", "marker"),
                                  ("_flare_group", "flare"),
                                  ("_splash_group", "splash"),
                                  ("_dust_group", "dust"),
                                  ("_mound_carve", "_mound_carve"),
                                  ("_mound_band", "_mound_band"),
                                  ("_upper_carve", "_upper_carve"),
                                  ("_upper_band", "_upper_band")):
                    if getattr(hg, attr, None) is ch:
                        return tag
                for attr in ("_sand_chords", "_sand_bands", "_flare_rects"):
                    for pair in (getattr(hg, attr, None) or ()):
                        if len(pair) > 1 and pair[1] is ch:
                            return attr.lstrip("_")
                return "%s#%d" % (nm, i)

            for i, ch in enumerate(hg.canvas.children):
                top = label(ch, i)
                ty[type(ch).__name__] = ty.get(type(ch).__name__, 0) + 1
                fam[top][type(ch).__name__] += 1
                walk(ch, top, ty, fam)
            # canvas.before (玻璃壳) 也数一份
            fam["canvas.before"] = collections.Counter()
            for ch in hg.canvas.before.children:
                fam["canvas.before"][type(ch).__name__] += 1
            box["ty"], box["fam"] = ty, fam
            self.stop()

    P().run()
    ty, fam = box["ty"], box["fam"]
    total = sum(sum(c.values()) for c in fam.values())
    print("")
    print("  === 画布指令按**族**(周期 %.0fs) ===" % PERIOD)
    for k, c in sorted(fam.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        if n:
            print("  %-18s %-6d  %s" % (k, n, dict(c.most_common(6))))
    print("")
    print("  === 按**类型**合计 ===")
    for k, v in ty.most_common(12):
        print("  %-18s %d" % (k, v))
    print("")
    print("  **总计 %d 条**(族内合计 = %d)" % (total, total))
    return 0


sys.exit(run())
