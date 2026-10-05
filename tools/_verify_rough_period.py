# -*- coding: utf-8 -*-
"""验 1.111 那两个**观感**数字, 在这份代码状态下真的成立吗。

1.107 报过两条(E1), 但随后被 1.108 回退、这次原样放回 —— **不能拿
"参数一样所以结果一样"当验证**(那是 E3)。这里在**当前代码**上重量一次:

  ① 上球图案的**一轮回**是不是 48 秒(而不是 8 秒);
  ② 下球轮廓的**步进频率**是不是 2.7Hz(而不是 7.9Hz)。

两条都直接驱动真 widget 取真值, 不重写一遍公式。

跑法: HG_MOTION_MODE 无关; python tools/_verify_rough_period.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="roughper-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 60}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w._rebuild_height_table()
                self.measure_loop(w)
                self.measure_step(w)
                self.measure_cost(w)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

            @staticmethod
            def measure_cost(w):
                """代价: 帧数 64→128 是**翻倍**, 而每帧一个 `_MoundProfile`(要建面积表)。
                用户提过"启动速度好像有点慢了" ⇒ 这笔账必须在**同一进程内**两边各量一次。"""
                import time as _t
                print("\n=== ③ 代价: `_rebuild_height_table()` 在 64 帧 vs 128 帧 ===")
                for frames, harm in ((64, (1, 2, 3)), (128, (5, 8, 13))):
                    m._build_rough_frames.__defaults__ = (frames, harm)
                    ts = []
                    for _ in range(3):
                        t0 = _t.perf_counter()
                        w._rebuild_height_table()
                        ts.append((_t.perf_counter() - t0) * 1000.0)
                    ts.sort()
                    print("  frames=%3d  重建 %6.1f ms (3 次中位; %s)"
                          % (frames, ts[1], " ".join("%.0f" % v for v in ts)))

            @staticmethod
            def measure_loop(w):
                import numpy as np
                w.set_duration(60)
                w.running = False
                base = None
                print("\n=== ① 上球图案: 一轮回是多少秒 ===")
                print("(对 t=0 的数组做相关; r≈1 ⇒ 图案回到了老样子)")
                for t in (0.0, 2.0, 4.0, 6.0, 8.0, 12.0, 24.0, 36.0, 48.0, 56.0):
                    w.elapsed = t
                    w._upper_rough_cache_t = None
                    arr = w._upper_rough_now()
                    a = np.asarray(arr, dtype=float)
                    if base is None:
                        base = a
                        print("  t=%5.1fs  (基准)" % t)
                        continue
                    r = float(np.corrcoef(base, a)[0, 1])
                    print("  t=%5.1fs  r = %+.4f   %s"
                          % (t, r, "**回到老样子**" if r > 0.99 else ""))

            @staticmethod
            def measure_step(w):
                print("\n=== ② 下球轮廓: 步进频率 (Hz) ===")
                print("(按 60fps 推进 elapsed, 数 `_mound_frame_k`(帧号) 跳了几次)")
                w.set_duration(60)
                w.elapsed = 0.0
                w._mound_frame_k = None
                w.running = False
                prev = None
                changes = []
                dt = 1.0 / 60.0
                steps = int(20.0 / dt)          # 走 20 秒
                t = 0.0
                for i in range(steps):
                    t = i * dt
                    w.elapsed = t
                    w._sync_mound_frame()
                    k = w._mound_frame_k
                    if prev is not None and k != prev:
                        changes.append(t)
                    prev = k
                span = (changes[-1] - changes[0]) if len(changes) > 1 else 0.0
                hz = (len(changes) - 1) / span if span > 0 else 0.0
                print("  20 秒里跳了 %d 次 ⇒ **%.2f Hz**（一跳最高 1/(周期/帧数) Hz）"
                      % (len(changes), hz))
                print("  帧数 frames=%d, 周期 period=%.1fs ⇒ 理论 %.2f Hz"
                      % (len(w._mound_frames), m.UPPER_ROUGH_PERIOD,
                         len(w._mound_frames) / m.UPPER_ROUGH_PERIOD))

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
