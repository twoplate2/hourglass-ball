# -*- coding: utf-8 -*-
"""**起跑后第一个"有内容"的帧为什么那么贵** —— 逐函数归因(2026-10-07)。

## 触发这条线的事实(用户设备 Lenovo TB323FU, 120Hz, v1.217)

用户报"有个帧必然很低"。他保存的 log (`D:/Temp/benchmark_20261007_102334_435279.txt`)
里, **每一档的最慢帧都落在沙柱注满那一刻**(`_neck_fill_time`):

| 档 | 最慢帧 | 粒子 | 图元 | 该帧 `flow_chunks` |
|---|---|---|---|---|
| 1s  | t=0.156s 总=48.87ms | **58**  | **43.88** | 0 → **7** |
| 5s  | t=0.27s  总=25.32ms | **31**  | **23.14** | — |
| 15s | t=0.25s  总=35.63ms | **5**   | **32.27** | — |

稳态 `图元` 只有 2.9ms。**粒子越少的那一帧越慢** ⇒ 与粒子数无关, 与"第一次有内容"有关:
沙柱注满前**不出粒子**(`沙柱注满后从直筒出口生成粒子`), 所以注满那一帧 = 所有桶第一次拿到内容,
= 所有 `_ensure_part` 第一次被调用。

## 这个探针回答什么

**那 ~44ms 具体花在哪几个批上**(沙流桶 / 颈部色调桶 / 飞溅 / 闪光), 每个 part 建一次多久。

跑法:
    python tools/_probe_first_content.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PERIOD = 15.0
FRAMES = 90
DT = 1.0 / 120.0


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"
    os.environ.setdefault("HG_SPLASH_RENDERER", "batch")
    os.environ.setdefault("HG_NECK_RENDERER", "batch")
    os.environ.setdefault("HG_FLARE_RENDERER", "batch")
    sys.argv = sys.argv[:1] + ["_fc"]

    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

    # ---- 给每个 `_ensure_part` 装计时器 -----------------------------------------
    creates = []          # (帧号, 批名, 块号, ms)
    frame_no = [0]

    def wrap(cls, label):
        orig = cls._ensure_part
        def patched(self, *a, **kw):
            before = len(self.parts) if hasattr(self, "parts") else 0
            t0 = time.perf_counter()
            out = orig(self, *a, **kw)
            dt = (time.perf_counter() - t0) * 1000.0
            after = len(self.parts) if hasattr(self, "parts") else 0
            if after > before:            # 只有"真的新建了一块"才记账
                creates.append((frame_no[0], label, a[0] if a else -1, dt))
            return out
        cls._ensure_part = patched

    import flow_texture_experiment as fte
    import flow_splash_experiment as fse
    wrap(fte.TextureFlowBatch, "flow")
    wrap(fse.SplashBatch, "splash/neck/flare")   # 三类共用这个类, 靠调用栈区分

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
            # ---- 预热期(相当于真实使用里"改完周期到按下开始"之间的空闲帧) ----
            t0 = time.perf_counter()
            warm_frames = 0
            while hg._warm_queue or warm_frames == 0:
                hg._warm_batches_step()
                warm_frames += 1
                if warm_frames > 400:
                    break
            print("")
            print("  预热: %d 帧, 合计 %.1fms(均 %.2fms/帧) —— 这时**没在跑**"
                  % (warm_frames, (time.perf_counter() - t0) * 1000.0,
                     (time.perf_counter() - t0) * 1000.0 / max(1, warm_frames)))
            hg.toggle()
            print("")
            print("  === 起跑后逐帧 (周期 %.0fs, 120Hz 步长) ===" % PERIOD)
            print("  帧   t(s)    redraw  粒子  飞溅   本帧新建的 part")
            for i in range(FRAMES):
                frame_no[0] = i
                mark = len(creates)
                hg.elapsed += DT
                t0 = time.perf_counter()
                hg.tick(DT)
                dt = (time.perf_counter() - t0) * 1000.0
                new = creates[mark:]
                extra = ""
                if new:
                    extra = "  ".join("%s#%s %.2fms" % (lbl, ch, ms) for _f, lbl, ch, ms in new)
                    extra += "   => 合计 %.2fms" % sum(x[3] for x in new)
                if dt > 3.0 or new:
                    print("  %3d %6.3f  %7.2f %5d %5d   %s"
                          % (i, hg.elapsed, dt, hg.pn, hg._sn, extra))
            box["n_flow"] = len(getattr(hg, "_flow_batches", {}))
            self.stop()

    P().run()

    print("")
    print("  === 归因 ===")
    if not creates:
        print("  **没有记录到任何新建 part** —— 说明这次跑的不是批处理路径, 本次不作数")
        return 0
    by = {}
    for _f, lbl, _ch, ms in creates:
        d = by.setdefault(lbl, [0, 0.0])
        d[0] += 1
        d[1] += ms
    for lbl, (n, ms) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print("  %-18s 新建 %2d 块, 合计 %7.2fms, 单块中位 %.2fms"
              % (lbl, n, ms, ms / n))
    first = {}
    for f, lbl, _ch, ms in creates:
        first.setdefault(lbl, f)
    print("  首次新建发生在第几帧: %s" % first)
    print("  沙流桶数: %s" % box.get("n_flow"))
    return 0


sys.exit(run())
