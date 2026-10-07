# -*- coding: utf-8 -*-
"""marker 批处理的**兜底路径负对照** —— 逼它失败, 再确认那 20 条 `Line` **真的回来了**
(2026-10-07)。

## 为什么必须单独验这个

项目栽过两次同类 bug, 都是"回退路径平时没人走":
`install_flares` 回滚时**漏把 `_flare_group` 放回画布** ⇒ 闪光**无声地整层消失**;
修成"无条件放回"之后又变成**挂两份**、每帧 `apply` 两次 ⇒ 半透明层整体亮 +2~3 级。
⇒ **兜底路径必须自己走出来对一遍**, 而且**先确认"失败"真的发生了** ——
否则"0 差异"可能只是两条路径都没跑(项目也踩过这条)。

## 判据(两条都必须成立, 且都是**逐条确定**的量, 不受调度噪声影响)

1. **失败确实发生了**: 强制失败后 `_marker_batch is None`(即装不上, 走了回退);
2. **`Line` 真的回来了**: 画布指令总数**与"从未装过批处理"时逐条相同**
   —— 少一条都说明 restore 漏放了。

跑法: HG_MARKER_FORCE_FAIL=1 python tools/_probe_marker_fallback.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def count_instr(node):
    n = 0
    for ch in getattr(node, "children", ()):
        n += 1 + count_instr(ch)
    return n


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"
    os.environ.setdefault("HG_SPLASH_RENDERER", "batch")
    os.environ.setdefault("HG_NECK_RENDERER", "batch")
    os.environ["HG_MARKER_FORCE_FAIL"] = "1"          # 负对照: 必须失败
    sys.argv = sys.argv[:1] + ["_mkfb"]
    import main as m
    import marker_batch_experiment as mb
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
    m.HourglassWidget.save_config = lambda *_: None

    box = {}

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
            for _ in range(int(15 * 60 * 0.4)):
                hg.elapsed += 1 / 60.0
                hg.tick(1 / 60.0)
            box["before"] = count_instr(hg.canvas)        # 从未装过批处理
            box["wired"] = mb.install(type(hg))
            hg._build_dynamic_canvas()                    # 强制失败会在这里触发
            for _ in range(6):
                hg.tick(1 / 60.0)
            box["after"] = count_instr(hg.canvas)
            box["batch"] = getattr(hg, "_marker_batch", "MISSING")
            box["n"] = getattr(hg, "_surface_marker_n", None)
            self.stop()

    P().run()
    b, a = box["before"], box["after"]
    fell_back = box["batch"] is None
    print("")
    print("  === marker 兜底路径负对照 (HG_MARKER_FORCE_FAIL=1) ===")
    print("  install() 接线            : %s" % box["wired"])
    print("  1) 失败确实发生了         : _marker_batch = %r  %s"
          % (box["batch"], "OK" if fell_back else "!! 没回退"))
    print("  2) Line 真的回来了        : 指令 %d -> %d  %s"
          % (b, a, "OK" if a == b else "!! 少了 %d 条" % (b - a)))
    print("  画布上的标记数            : %s" % box["n"])
    print("")
    ok = fell_back and (a == b)
    print("  ==> %s" % ("通过: 兜底路径真的会走, 且原 Line 原样放回"
                       if ok else "**翻红** —— 兜底路径有问题"))
    return 0 if ok else 1


sys.exit(run())
