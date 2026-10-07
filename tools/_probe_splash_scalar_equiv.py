# -*- coding: utf-8 -*-
"""飞溅积分器: numpy 向量化版与标量兜底版的数值一致性检查。

## 为什么不能用"跑一遍 `_np = None`"来验

试过(`tools/_splash_golden.py` 的 `nonumpy` 臂), **验不了** —— 关掉 numpy 会把
**粒子**的物理也切到标量兜底路径, 而那条路与 numpy 路的随机数消耗本来就不同:
实测 1s 档**第 25 帧**首次分叉, 而那一帧 `n=0`(**一颗飞溅都没有**) ⇒ 差异纯粹来自
粒子, 与飞溅无关。**这是项目既有的性质, 不是本次改动引入的。**

所以本脚本把两个积分器**单独拎出来对拍**: 同一个状态、同一组参数, 各跑一次,
比对存活数与结果数组, 绝对容差 1e-9 个绘制单位。
短跳/滚落使用 exp/log 积分, NumPy 与 libm 允许末位舍入差, 不要求逐位相同。

## 跑法

    python tools/_probe_splash_scalar_equiv.py [周期=15] [预热秒=6] [帧数=60]

退出码 0 = 在容差内一致。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
WARM = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
FRAMES = int(sys.argv[3]) if len(sys.argv) > 3 else 60


def run():
    with tempfile.TemporaryDirectory(prefix="splash-se-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App                            # noqa: F401
        from kivy.clock import Clock
        from kivy.core.window import Window
        from types import SimpleNamespace
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])

        state = {"bad": 0, "frames": 0, "empty": 0, "first": None,
                 "maxn": 0, "hit_stop": 0, "hit_slide": 0}

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
                import random
                random.seed(23)
                w.toggle()
                while w.elapsed < WARM:
                    now[0] += 1 / 60.0
                    w.tick(1 / 60.0)
                fields = module._S_FIELDS
                vec = w._update_splashes
                sca = w._update_splashes_scalar
                for _ in range(FRAMES):
                    now[0] += 1 / 60.0
                    w.tick(1 / 60.0)
                    if w._sn == 0:
                        state["empty"] += 1
                        continue
                    # 抓这一帧真实传进来的参数(在 spy 里存下来, 再原样喂给两个积分器)
                    box = {}

                    def spy(*a, **k):
                        box["a"] = a
                        return vec(*a, **k)

                    w._update_splashes = spy
                    now[0] += 1 / 60.0
                    w.tick(1 / 60.0)
                    w._update_splashes = vec
                    args = box["a"]
                    n0 = w._sn
                    state["maxn"] = max(state["maxn"], n0)
                    snap = [list(getattr(w, nm)[:n0]) for nm in fields]
                    has0 = list(w.shas[:n0])
                    state["hit_stop"] += sum(1 for v in has0 if v != 0.0)
                    # A: numpy 版(上面那次 tick 已经用 spy->vec 跑过, 这里重跑一遍保证干净)
                    for nm, sv in zip(fields, snap):
                        getattr(w, nm)[:n0] = sv
                    w._sn = n0
                    vec(*args)
                    a_n = w._sn
                    a_val = [list(getattr(w, nm)[:a_n]) for nm in fields]
                    # B: 标量版(回到同一状态)
                    for nm, sv in zip(fields, snap):
                        getattr(w, nm)[:n0] = sv
                    w._sn = n0
                    sca(*args)
                    b_n = w._sn
                    b_val = [list(getattr(w, nm)[:b_n]) for nm in fields]
                    state["frames"] += 1
                    import numpy as np
                    equal = a_n == b_n and all(
                        np.allclose(va, vb, rtol=0.0, atol=1e-9)
                        for va, vb in zip(a_val, b_val))
                    if not equal:
                        state["bad"] += 1
                        if state["first"] is None:
                            if a_n != b_n:
                                state["first"] = ("存活数", a_n, b_n)
                            else:
                                for nm, va, vb in zip(fields, a_val, b_val):
                                    if not np.allclose(va, vb, rtol=0.0, atol=1e-9):
                                        for k in range(len(va)):
                                            if abs(va[k] - vb[k]) > 1e-9:
                                                state["first"] = (nm, k, va[k], vb[k])
                                                break
                                        break
                self.stop()

        ProbeApp().run()
        print("比了 %d 帧(另有 %d 帧无飞溅已跳过); 最大存活 %d"
              % (state["frames"], state["empty"], state["maxn"]))
        print("覆盖: 出现过「已停稳」标志的颗粒-帧数 = %d(若为 0 说明没走到那条分支)"
              % state["hit_stop"])
        if state["first"] is not None:
            print("首处不符: %r" % (state["first"],))
        if state["frames"] == 0:
            print("!! 一帧都没比到 —— 这个『通过』不算数")
            return 1
        print("==> 两条路径", "有差异" if state["bad"] else "数值一致(绝对容差 1e-9)")
        return 1 if state["bad"] else 0


sys.exit(run())
