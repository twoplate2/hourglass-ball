# -*- coding: utf-8 -*-
"""峰值负载下 `update_particles` + `redraw` 的**函数级热点** —— 用户 2026-10-06
「benchmark 的帧率下降得厉害」。

⚠️ 桌面 GL 余量极大, **渲染成本在这里测不出来**(docs 早有记录)。本探针只测
**纯 Python 侧**(物理 + 每帧提交), 那部分与设备同源。

跑法: python tools/_prof_peak.py [周期=15] [预热秒=6] [采样帧=120]
"""
import cProfile
import io
import os
import pstats
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIOD = sys.argv[1] if len(sys.argv) > 1 else "15"
WARM = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
FRAMES = int(sys.argv[3]) if len(sys.argv) > 3 else 120


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "line"       # 排除批处理器, 只看应用侧
    sys.argv = [sys.argv[0], "_prof"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": float(PERIOD)}
    m.HourglassWidget.save_config = lambda *_: None

    seen = [0, None, 0]

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            self.hg = self.hourglass
            self.hg.set_duration(float(PERIOD))
            self.hg._rebuild_height_table()
            self.hg.reset()
            self.hg.toggle()

        def on_stop(self):
            pass

    app = P()

    class Probe(m.HourglassApp):
        pass

    # 用 tick 挂钩: 预热后开 profiler, 采够帧数就停
    orig_tick = m.HourglassWidget.tick
    pr = cProfile.Profile()
    state = {"on": False, "n": 0}

    def tick(self, dt):   # ⚠️ 名字必须是 tick —— Kivy 的 weakmethod 按 __name__ 回查属性
        if not state["on"] and self.elapsed >= WARM:
            state["on"] = True
            pr.enable()
        if state["on"]:
            state["n"] += 1
            if state["n"] >= FRAMES:
                pr.disable()
                print("  峰值时刻: 粒子 %d / 飞溅 %d / 尘埃 %d"
                      % (self.pn, len(self.splashes), len(self.dusts)))
                st = pstats.Stats(pr)
                st.sort_stats("tottime").print_stats(18)
                app.stop()
                return
        orig_tick(self, dt)

    m.HourglassWidget.tick = tick
    app.run()
    _ = seen


if __name__ == "__main__":
    run()
