# -*- coding: utf-8 -*-
"""颈部色调批的**规模复核** —— 决定"懒建空壳色调组"值不值得做(2026-10-07)。

## 背景

1.215 试过一次"32 个色调组改成按需建", 结果 **60/60 帧逐像素不同、颈部约 13000 个
像素、最大差 77**, 而 `_probe_neck_batch_equiv` 报"几何+颜色一致" => 说不清原因, 回退了。

**现在有一条可检验的假设**: 那一版把 `_neck_batches` **压缩成了"只有用到的项"的列表**,
于是 `_neck_sink` 里 `batches[k]` / `counts[k]` / `_neck_batch_rgb[k]` 的 **`k` 是压缩后
的下标, 而 `counts[k]` 是色调下标** —— 两个下标域错位 => **颗粒被喂进了错误的色调桶**,
颜色/位置全乱 => 大面积像素差。这与"几何与颜色单独看都对得上"并不矛盾。

## 本探针只回答两个数(不猜):

1. 颈部那个 `RenderContext` 现在挂着**多少条指令**;
2. 每帧真正**非空**的色调档有几个(即"懒建"能省掉几条)。

跑法: python tools/_probe_neck_batches_size.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"
    os.environ.setdefault("HG_SPLASH_RENDERER", "batch")
    os.environ.setdefault("HG_NECK_RENDERER", "batch")
    sys.argv = sys.argv[:1] + ["_neckbatches"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 15}
    m.HourglassWidget.save_config = lambda *_: None

    box = {"rows": []}

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            hg.set_duration(15.0)
            hg._rebuild_height_table()
            hg.reset()
            hg.toggle()
            for _ in range(int(15.0 * 60 * 0.5)):      # 跑到中期
                hg.elapsed += 1 / 60.0
                hg.tick(1 / 60.0)
            ctx = getattr(hg, "_neck_context", None)
            batches = getattr(hg, "_neck_batches", None)
            n_instr = len(ctx.children) if ctx is not None else -1
            # 每批挂了多少条指令(空壳 = 只有 InstructionGroup + Color 两条)
            per = []
            nonempty = 0
            if batches:
                for k, entry in enumerate(batches):
                    if entry is None:          # 懒建: 这一档还没建过(不占画布指令)
                        per.append((k, 0, 0))
                        continue
                    color, batch = entry
                    grp = color.parent if hasattr(color, "parent") else None
                    n = len(grp.children) if grp is not None else 0
                    parts = len(getattr(batch, "parts", ()))
                    per.append((k, n, parts))
                    if parts:
                        nonempty += 1
            box["rows"].append((n_instr, len(batches) if batches else 0,
                                nonempty, per))
            self.stop()

    P().run()
    for n_instr, n_batches, nonempty, per in box["rows"]:
        print("")
        print("  颈部 RenderContext 指令数 = %d (含 %d 个色调组)"
              % (n_instr, n_batches))
        print("  本帧**非空**色调组 = %d  => 空壳 %d 个"
              % (nonempty, sum(1 for e in (batches or ()) if e is None)))
        print("  每组指令数分布: %s"
              % sorted(set(p[1] for p in per)))
        print("  每组已建 part 数分布: %s"
              % sorted(set(p[2] for p in per)))
        print("")
        print("  => 懒建能省 ≈ (每组空壳 2 条) × 空壳数 = %d 条" % (2 * (sum(1 for e in (batches or ()) if e is None))))
    return 0


sys.exit(run())
