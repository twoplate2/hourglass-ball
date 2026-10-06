# -*- coding: utf-8 -*-
"""颈部颗粒: **向量化路径 vs 标量兜底路径** 的逐分量等价守卫。

## 为什么要有它

`_draw_neck_grains` 的向量化分支(`_cand is not None and pv.nwp is not None and
not NECK_SCALAR`)是纯性能改写, **不许改变任何一个像素**。逐像素比对(`inspect_flow.py`)
能兜底, 但它有个盲区: 粒子数 `< _NUMPY_MIN` 时**两条臂都会退回标量**
(因为 `use_np` 为假 ⇒ `_cand is None`) ⇒ 那次比对是**空转的, 全绿也说明不了什么**。
本脚本直接比 `_draw_neck_grains` 写进池子的**几何量**(points / 颜色 / 线宽),
并且先断言"这一帧确实有候选"才比 —— 杜绝空转。

## 跑法

    python tools/_probe_neck_equiv.py [周期=15] [预热秒=6]

逐帧比, 任何一帧有差异就打印首处不符并退出 1。
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
WARM = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
FRAMES = int(sys.argv[3]) if len(sys.argv) > 3 else 40


def snapshot(widget):
    """把池子里**已画**的那一段抄下来(计数 + 每槽的几何量)。"""
    pool = widget._neck_grain_pool
    n = widget._neck_grain_count
    out = [n]
    for color, line in pool[:n]:
        out.append((tuple(round(v, 12) for v in line.points),
                    tuple(round(float(v), 12) for v in color.rgb),
                    float(line.width)))
    return out


def run():
    with tempfile.TemporaryDirectory(prefix="neck-equiv-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App                      # noqa: F401
        from kivy.clock import Clock
        from kivy.core.window import Window
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])

        state = {"bad": 0, "frames": 0, "empty": 0, "first": None}

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.setup, 1.0)

            def setup(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.go, 0.4)

            def go(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                w.set_duration(PERIOD)
                w._rebuild_height_table()
                w.reset()
                w.toggle()
                self.w = w
                self.step(0)

            def step(self, _dt):
                w = self.w
                # 推进到目标时刻(与 inspect_flow 同法: 手摇 tick, 用假时钟)
                while w.elapsed < WARM:
                    now[0] += 1 / 60.0
                    w.tick(1 / 60.0)
                for _ in range(FRAMES):
                    now[0] += 1 / 60.0
                    w.tick(1 / 60.0)
                    side = w._neck_sand_side()
                    if side is None:
                        continue
                    # **同一个状态**上先后跑两条路径 —— 中间不推进任何时间,
                    # 所以两次读到的粒子完全一样, 差异只可能来自路径本身。
                    module.NECK_SCALAR = True
                    w._neck_grain_count = 0
                    w._draw_neck_grains(side)
                    a = snapshot(w)
                    module.NECK_SCALAR = False
                    w._neck_grain_count = 0
                    w._draw_neck_grains(side)
                    b = snapshot(w)
                    if a[0] == 0:
                        state["empty"] += 1
                        module.NECK_SCALAR = True
                        continue
                    state["frames"] += 1
                    if a != b:
                        state["bad"] += 1
                        if state["first"] is None:
                            for i in range(min(len(a), len(b))):
                                if a[i] != b[i]:
                                    state["first"] = (i, a[i], b[i])
                                    break
                            else:
                                state["first"] = ("长度", len(a), len(b))
                    module.NECK_SCALAR = True
                self.stop()

        ProbeApp().run()

        print("比了 %d 帧(另有 %d 帧无候选, 已跳过); 不一致 %d 帧"
              % (state["frames"], state["empty"], state["bad"]))
        if state["first"] is not None:
            print("首处不符: %r" % (state["first"],))
        if state["frames"] == 0:
            print("!! 一帧都没比到 —— 要么没流起来, 要么 `side` 一直为空; 这个『通过』不算数")
            return 1
        return 1 if state["bad"] else 0


sys.exit(run())
