# -*- coding: utf-8 -*-
"""飞溅(背景 splash)数量 vs 下落沙粒数量 —— 比例到底漂多少。

用户 2026-10-06: 「飞溅基本对了，但是数量有点少了 **理论上他等于下落的沙子的总数的一个比例**」。

代码事实(先读):
  · 主流生成率 `rate = 600 * speed_factor * motion_scale`   (main.py:2988)
  · `speed_factor = clamp(60/duration, 0.5, 2.5)`            (main.py:2017)
  · `motion_scale = max(1.0, 自然飞行时间 / 可用时间)`        (main.py:2207)
  · 背景飞溅 `SPLASH_BG_RATE = 520`  —— **常量, 与 duration 无关** (main.py:355)

⇒ 若按"比例"要求, 这个常量在 duration 跨度上必然漂移。本探针量两个分母:
   ① `rate`      —— 每秒生成的沙粒数(发射率)
   ② `在途粒子`  —— 同一时刻**看得见**在落的有多少(用户说的"下落的沙子的总数"更像这个)
   ③ `在途飞溅`  —— 同一时刻看得见的飞溅数
  比值 ③/② = "看起来飞溅占下落沙子的几分之几"。

跑法: python tools/_probe_splash_ratio.py            # 全套
      python tools/_probe_splash_ratio.py 5          # 单档
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PERIODS = (1.0, 5.0, 15.0, 60.0, 120.0)
# 采样窗: 跳过开局(注沙柱/预热), 取中段稳定区
SKIP_FRAC = 0.35
TAKE_FRAC = 0.30


def run_one(duration):
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_splash_probe"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    samples = []

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            hg.duration = duration
            hg._rebuild_height_table()
            hg.toggle()                      # 开始

            def sample(*_a):
                # `pn` = 已提交的粒子数(在途主流); `splashes` = 在途飞溅列表
                samples.append((float(hg.elapsed), int(hg.pn), len(hg.splashes)))
                if hg.elapsed >= duration * 0.999 or not hg.running:
                    Clock.unschedule(sample)
                    Clock.schedule_once(lambda dt: self.stop(), 0.1)

            Clock.schedule_interval(sample, 1.0 / 60.0)

    P().run()

    if not samples:
        return None
    t_end = samples[-1][0]
    lo, hi = t_end * SKIP_FRAC, t_end * (SKIP_FRAC + TAKE_FRAC)
    win = [s for s in samples if lo <= s[0] <= hi] or samples
    part = sorted(s[1] for s in win)
    spl = sorted(s[2] for s in win)
    mid = len(part) // 2
    return {
        "duration": duration,
        "rate": 600 * max(0.5, min(2.5, 60.0 / max(0.1, duration))),  # 不含 motion_scale
        "part_med": part[mid], "part_max": part[-1],
        "spl_med": spl[mid], "spl_max": spl[-1],
        "n": len(win),
    }


def main():
    periods = [float(sys.argv[1])] if len(sys.argv) > 1 else list(PERIODS)
    rows = []
    for d in periods:
        try:
            r = run_one(d)
        except Exception as exc:
            r = None
            print("  %-8s 异常 %r" % (d, exc))
        if r:
            rows.append(r)
    print("")
    print("  === 在途飞溅 / 在途主流  (采样窗 = 中段 %.0f%%~%.0f%%) ==="
          % (SKIP_FRAC * 100, (SKIP_FRAC + TAKE_FRAC) * 100))
    print("  %-8s %-10s %-12s %-10s %-10s %s"
          % ("周期(s)", "发射率/s", "在途主流", "在途飞溅", "飞溅/主流", "样本"))
    for r in rows:
        print("  %-8.0f %-10.0f %-12d %-10d %-10.3f %d"
              % (r["duration"], r["rate"], r["part_med"], r["spl_med"],
                 r["spl_med"] / max(1, r["part_med"]), r["n"]))
    print("")
    print("  (「在途」= 同一时刻看得见的数量 = 用户说的『下落的沙子的总数』)")
    print("  ⚠️ 本探针在桌面 Kivy 上跑 —— 量的是**计数逻辑**, 与渲染后端无关;")
    print("     但「看不看得见」的结论仍须回设备。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
