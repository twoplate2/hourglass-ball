# -*- coding: utf-8 -*-
"""量**画布指令的单价** —— 用消融法: 把某一族指令从画布上摘掉, 量 `Window.on_draw` 差多少。

## 为什么需要它

`frame_benchmark` 的 Canvas 栏只说"画布一共多少", 不说"每条指令值多少"。
而静态几何(carve/band 的 `Quad` 带)有 570+ 条指令, 每条每帧都要走一次 `apply()` ——
要判断"把它们合成 Mesh 值不值", 先得知道**单价**。

## 做法

同一个进程里跑两段, 中间只做一件事: 把目标指令从 `self.canvas` 上摘掉(画面会坏,
但**帧循环的其余部分一字未动**) ⇒ 差值是这条指令族的纯开销。

## 跑法

    python tools/_probe_canvas_cost.py [周期=15] [每段秒=3] [族=mound|upper|neck|all]
"""
import collections
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
SPAN = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
FAMILY = sys.argv[3] if len(sys.argv) > 3 else "mound"

FAMILIES = {
    "none": (),                      # 对照臂: 什么都不摘(量两段之间的漂移)
    "mound": ("_mound_carve", "_mound_band"),
    "upper": ("_upper_carve", "_upper_band"),
    "neck": ("_neck_quads",),
    "all": ("_mound_carve", "_mound_band", "_upper_carve", "_upper_band", "_neck_quads"),
}


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"
    os.environ["HG_SPLASH_RENDERER"] = "batch"
    os.environ["HG_NECK_RENDERER"] = "batch"
    sys.argv = [sys.argv[0], "_canvas"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

    box = {"on": 0.0, "n": 0, "phase": 0, "res": collections.defaultdict(list)}
    orig = Window.on_draw

    def probe(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            if box["phase"]:
                box["res"][box["phase"]].append((time.perf_counter() - t0) * 1000)

    Window.on_draw = probe

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
            Clock.schedule_once(lambda d: box.__setitem__("phase", 1), 3.0)
            Clock.schedule_once(lambda d: self.cut(), 3.0 + SPAN)
            Clock.schedule_once(lambda d: self.done(), 3.0 + 2 * SPAN)

        def cut(self):
            hg = self.hourglass
            removed = 0
            try:
                for nm in FAMILIES[FAMILY]:
                    for q in (getattr(hg, nm, None) or ()):
                        try:
                            hg.canvas.remove(q)
                            removed += 1
                        except Exception:
                            pass
            except Exception as exc:            # 出任何事都要往下走, 否则 app 永不退出
                print("   cut failed: %r" % (exc,))
            box["removed"] = removed
            box["phase"] = 2

        def done(self):
            box["phase"] = 0
            box["pn"] = self.hourglass.pn
            a, c = box["res"][1], box["res"][2]
            print("周期 %gs  族 %s  摘掉 %d 条指令" % (PERIOD, FAMILY, box.get("removed", 0)))
            print("  摘之前 on_draw %6.3f ms/frame (n=%d)" % (mean(a), len(a)))
            print("  摘之后 on_draw %6.3f ms/frame (n=%d)" % (mean(c), len(c)))
            print("  == 差值 %+.3f ms/frame = %.3f µs/条 ==" %
                  (mean(a) - mean(c),
                   (mean(a) - mean(c)) * 1000.0 / max(1, box.get("removed", 1))))
            sys.stdout.flush()
            self.stop()

    P().run()
    return box


def mean(v):
    return sum(v) / len(v) if v else float("nan")


if __name__ == "__main__":
    run()
