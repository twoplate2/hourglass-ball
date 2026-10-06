# -*- coding: utf-8 -*-
"""落沙流 vs 沙堆的**墨量**(覆盖率)随周期怎么变 —— 用户 2026-10-06。

用户: 「看看 169 版本沙流和沙堆的区别有多大? 我感觉还是**差距过大**」。
设备实测(1080x2400, MuMu): **50s 档沙流覆盖率 94.5%**(与沙堆几乎相同),
**600s 档沙流是一串断续小珠**、底色透出来 —— 与用户截图一致。

本探针不算图像, 只算**几何/物理量**, 用来回答"为什么会随周期恶化":
  · `rate`          —— 生成率(现为 `FLOW_BASE_RATE`, **与周期无关**)
  · `x_clip`        —— 沙流半宽(决定 `size` 走 1 还是 2)
  · 飞行时间 T      —— 出口到沙面
  · L               —— 自由落体段长度
  ⇒ **线密度** = rate × T / L     (每 px 落程几颗粒)
  ⇒ **面覆盖** = 线密度 × 颗粒宽 / 沙流宽
沙堆由实心多边形 + 颗粒材质构成, 面覆盖恒 = 1 —— 这就是"差距"的来源。

跑法: python tools/_probe_stream_ink.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PERIODS = (1.0, 5.0, 20.0, 50.0, 120.0, 600.0, 3600.0)


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_sink"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    rows = []

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            for d in PERIODS:
                hg.duration = d
                hg._rebuild_height_table()
                outlet = 2 * hg._neck_y - hg._taper["y_bot"]
                L = max(1.0, outlet - hg._lower_sand_bot)      # 自由落体段
                T = hg._natural_flight_time
                rate = m.FLOW_BASE_RATE * hg._particle_motion_scale
                clip = max(1.0, hg.neck_w - hg._ow)
                size = 2.0 if clip >= 3.0 else 1.0             # 代码里的短路
                lin = rate * T / L                             # 颗 / px
                areal = lin * size / (2.0 * clip)              # 横向覆盖(名义)
                rows.append((d, hg.neck_w, clip, size, rate, T, L, lin, areal))
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return rows


if __name__ == "__main__":
    rows = run()
    print("")
    print("  === 落沙流的'墨量'随周期 (桌面 400x800, 应用单位) ===")
    print("  %-8s %-8s %-8s %-6s %-8s %-8s %-8s %-10s %s"
          % ("周期s", "neck_w", "x_clip", "粒宽", "rate/s", "飞行T", "落程L",
             "线密度/px", "横向覆盖"))
    for (d, nw, clip, size, rate, T, L, lin, areal) in rows:
        flag = "  ← 强迫 1px" if size == 1.0 else ""
        print("  %-8.0f %-8d %-8.1f %-6.0f %-8.0f %-8.3f %-8.0f %-10.3f %.3f%s"
              % (d, nw, clip, size, rate, T, L, lin, areal, flag))
    base = rows[3][7]        # 以 50s 为基准
    print("")
    print("  相对 50s 的**线密度**倍数: ", end="")
    print(", ".join("%.0fs=%.2f" % (r[0], r[7] / base) for r in rows))
    print("")
