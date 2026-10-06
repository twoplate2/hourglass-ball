# -*- coding: utf-8 -*-
"""桌面端**热点排行** —— 在稳态负载下 cProfile 一段真实帧循环。

## 为什么用它(cProfile 不是被禁了吗)

`_probe_frame_cost.py` 顶部的告诫是"**别用 cProfile 的量级**" —— 它按函数调用计开销,
本项目每帧上百万次微调用, 绝对值会被放大好几倍(曾把 −49% 错报成设备 −9%)。
**但排名仍然可信**: 找出"哪一段占大头"是本脚本唯一的用途, 定案一律回到
墙钟(`_probe_frame_cost.py`)或设备剖面(`prof_android.py`)。

## 跑法

    python tools/_hot_desktop.py [周期=15] [预热秒=4] [采样秒=4]

打印: 按 tottime 的前 25 名 + 按 cumtime 的前 15 名。
"""
import cProfile
import io
import os
import pstats
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
WARM = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
SPAN = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"
    sys.argv = [sys.argv[0], "_hot"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

    pr = cProfile.Profile()
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
            Clock.schedule_once(lambda d: pr.enable(), WARM)
            Clock.schedule_once(lambda d: self.done(), WARM + SPAN)

        def done(self):
            pr.disable()
            hg = self.hourglass
            box["pn"] = hg.pn
            box["sp"] = hg._sn
            box["frame_ms"] = getattr(hg, "_last_frame_ms", None)
            self.stop()

    P().run()
    return pr, box


def main():
    pr, box = run()
    print("稳态: 主粒子 %s / 飞溅 %s (周期 %gs, 桌面 400x800, texture)"
          % (box.get("pn"), box.get("sp"), PERIOD))
    st = pstats.Stats(pr)
    s = io.StringIO()
    st.stream = s
    st.sort_stats("tottime").print_stats(25)
    print("=== tottime 前 25 ===")
    print(s.getvalue())
    s = io.StringIO()
    st.stream = s
    st.sort_stats("cumtime").print_stats(15)
    print("=== cumtime 前 15 ===")
    print(s.getvalue())
    for fn in ("_draw_neck_grains", "redraw", "update_particles"):
        s = io.StringIO()
        st.stream = s
        st.print_callees(fn)
        print("=== %s 调用了谁 ===" % fn)
        print(s.getvalue())


main()
