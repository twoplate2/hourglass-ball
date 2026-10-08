# -*- coding: utf-8 -*-
"""**版本无关**的飞溅对照取帧: 同一状态下, 每个周期各出一张图, 供 2.9/2.10 并排判。

为什么要单独一个探针(而不是直接用 `smoke_splash_origin.py`):
那个烟测**会断言** 2.10 才有的 `_splash_density` / `_splash_origin_blend` / `_splash_speed_scale`
—— 在 2.9 的树上会直接 AttributeError。本探针**一个新增属性都不读**, 只做
"设周期 → 重置 → 跑固定帧数 → 存图", 所以两棵树都能跑、条件完全相同。

跑法:
    python tools/_splash_ab_frames.py <tag>            # 默认三档 5 / 30 / 60000 秒
输出: benchmark_logs/_ab_splash/<tag>_p<周期>.png  + 一行可比对的计数
"""
import os
import random
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TAG = sys.argv[1] if len(sys.argv) > 1 else "cur"
PERIODS = [float(v) for v in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["5", "30", "60000"])]
FRAMES = 240                       # 4 秒 @60fps, 与烟测同长
OUT = ROOT / "benchmark_logs" / "_ab_splash"

with tempfile.TemporaryDirectory(prefix="splash-ab-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": 60000}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (480, 900)
            Clock.schedule_once(self.go, 0.5)

        def go(self, _dt):
            self.root.apply_orientation()
            self.root.do_layout()
            self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            w = self.hourglass
            w._rebuild_height_table()
            OUT.mkdir(parents=True, exist_ok=True)
            print("")
            print("  tag=%s  等价属性(2.9 无) density=%s blend=%s speed=%s"
                  % (TAG,
                     getattr(w, "_splash_density", "n/a"),
                     getattr(w, "_splash_origin_blend", "n/a"),
                     getattr(w, "_splash_speed_scale", "n/a")))
            print("  %8s %10s %10s %12s   %s" % ("周期", "出生累计", "在世飞溅", "接触点", "图"))
            for period in PERIODS:
                w.set_duration(period)
                w.reset()
                random.seed(23)
                w.toggle()
                for _ in range(FRAMES):
                    now[0] += 1.0 / 60.0
                    w.tick(1.0 / 60.0)
                # 出生累计 = 现存 + 已消亡的各路统计(与具体版本无关的四个计数器)
                st = dict(getattr(w, "_splash_stats", {}))
                born = st.get("born_air", 0) + st.get("born_roll", 0)
                path = OUT / ("%s_p%g.png" % (TAG, period))
                w.export_to_png(str(path))
                print("  %8g %10d %10d %12s   %s"
                      % (period, born, w._sn, len(getattr(w, "_contact_hits", ())), path.name))
            self.stop()

    Probe().run()
