# -*- coding: utf-8 -*-
"""飞溅**同时在世数量**的峰值 —— 隔离 `SPLASH_STILL_LIFE`(停住后滞留)那一改的代价。

用户 2026-10-06 报的"流动一会会就不动了"改成"停住后原地滞留 SPLASH_STILL_LIFE 秒"
(旧版是 `|vx| < MIN_VX` 直接删除)。滞留必然抬高**稳态在世数**。
本探针只回答一件事: 抬高了多少倍。

跑法: python tools/_probe_splash_count.py [周期]
      HG_SPLASH_STILL=0 python tools/_probe_splash_count.py 15    # 退回旧行为(对照臂)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_spc"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None
    peak = [0, 0, []]

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            self.hg = self.hourglass
            self.hg.set_duration(PERIOD)
            self.hg._rebuild_height_table()
            self.hg.reset()
            self.hg.toggle()
            Clock.schedule_interval(self.sample, 1.0 / 30.0)
            Clock.schedule_once(lambda dt: self.stop(), PERIOD)

        def sample(self, _dt):
            hg = self.hg
            n = len(hg.splashes)
            peak[0] = max(peak[0], n)
            if hg.elapsed > PERIOD * 0.4:
                peak[1] = max(peak[1], n)      # 稳态段(避开起步)
                peak[2].append(n)

    P().run()
    return peak


if __name__ == "__main__":
    pk, steady, arr = run()
    import statistics
    print("")
    print("  === 飞溅在世数 (周期 %gs) ===" % PERIOD)
    print("  SPLASH_STILL_LIFE = %s / SLOPE_GAIN = %s / SLIDE_DAMP = %s"
          % (os.environ.get("HG_SPLASH_STILL", "0.40(默认)"),
             os.environ.get("HG_SPLASH_SLOPE", "0.0(默认)"),
             os.environ.get("HG_SPLASH_SLIDE", "4.0(默认)")))
    print("  全程峰值 %d ; 稳态段峰值 %d ; 稳态均值 %.0f"
          % (pk, steady, statistics.mean(arr) if arr else 0))
    print("")
