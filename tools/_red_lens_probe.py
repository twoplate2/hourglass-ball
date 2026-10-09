# -*- coding: utf-8 -*-
"""红队量具(透镜=适用边界/量纲口径)。一次一个口径:
  python tools/_red_lens_probe.py desktop
  python tools/_red_lens_probe.py tablet
打印: A1 断口在两个渲染器上的表现 / A2 自由段锚点真实序列 / A4 核心 vs 云 / A5 口径比。
"""
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODE = (sys.argv[1] if len(sys.argv) > 1 else "tablet")
if MODE == "tablet":
    W, H, DENS = 1904, 2890, "2.75"
else:
    W, H, DENS = 400, 800, "1.0"
os.environ.setdefault("KIVY_METRICS_DENSITY", DENS)
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
from kivy.metrics import dp                      # noqa: E402
import main as m                                 # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *a, **kw: None
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
m.HourglassWidget.save_config = lambda *_: None
for cls, nm in ((m.HourglassApp, "_voice_say"), (m.HourglassWidget, "_voice_say")):
    if hasattr(cls, nm):
        setattr(cls, nm, lambda *a, **kw: None)

PERIODS = [(600.0, 2.0), (50.0, 20.0), (5.0, 2.5), (1800.0, 30.0)]


def particle_shrink(hg, d):
    ms = hg._particle_motion_scale
    v0, gb, m_ = 60.0 * ms, 450.0 * ms * ms, m.FLOW_SHRINK_MIN
    b_sat = v0 * v0 * (m_ ** -4.0 - 1.0) / (2.0 * gb)
    if d >= b_sat + 1.0:
        t = m_
    else:
        t = (v0 / math.sqrt(v0 * v0 + 2 * gb * d)) ** 0.5
        if t < m_:
            t = m_
    return (1.0 + (t - 1.0) * (d / 40.0)) if d < 40.0 else t


def clamp_lim(hg, d, tube_lim, hs=2.0):
    y = hg._lower_ball_cut - d
    if y >= hg._lower_sand_top:
        return tube_lim
    dy = y - hg._lower_y_c
    r = hg._R_inner ** 2 - dy * dy
    raw_ball = math.sqrt(r) if r > 0.0 else 0.0
    t = min(1.0, max(0.0, (hg._lower_sand_top - y) / 30.0))
    return tube_lim + (raw_ball - tube_lim) * t


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (W, H)
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        got = (int(Window.size[0]), int(Window.size[1]))
        hg = self.hourglass
        print("")
        print("=" * 78)
        print("== %s 请求 %dx%d 实得 %s  dp(1)=%.2fpx  画布 %.0fx%.0f"
              % (MODE, W, H, got, dp(1), hg.width, hg.height))
        print("=" * 78)
        for period, tset in PERIODS:
            try:
                hg.set_duration(period)
            except Exception as e:
                print("  set_duration(%s) 失败: %r" % (period, e))
                return
            hg.running = False
            hg._last_frame = hg._last_tick = None
            hg.elapsed = tset
            hg.update_particles(0.0)
            try:
                side = hg._neck_sand_side()
            except Exception as e:
                print("  period=%.0f: _neck_sand_side 抛: %r" % (period, e))
                continue
            outlet = 2 * hg._neck_y - hg._taper["y_bot"]
            t_in = hg._taper["t_in"]
            tube_lim = max(1.0, hg.neck_w - hg._ow)
            free = [(x, y) for x, y in side if y < outlet - 1e-6]
            print("")
            print("  -- p=%.0fs t=%.1f  t_in=%.2fpx(neck_w=%d ow=%.2f)  出口y=%.1f"
                  % (period, tset, t_in, hg.neck_w, hg._ow, outlet))
            ds = [outlet - y for x, y in free]
            print("     自由段节点 d=%s" % ",".join("%.2f" % d for d in ds))
            for x, y in free:
                print("       d=%8.2f  w/t_in=%6.4f" % (outlet - y, x / t_in))
            if len(free) >= 2:
                a, b = free[-2], free[-1]
                print("     最后两节点: 半宽差=%.4f(0=竖弦)  锚点数=%d"
                      % (abs(a[0] - b[0]), len(free)))
            span = ds[-1] if ds else 0.0
            if span:
                print("     span=%.1fpx  40px=span的%.1f%%  12.66px=span的%.2f%%"
                      % (span, 100 * 40 / span, 100 * 12.66 / span))
            print("     A1: 顶点d≈12.66? %s ⇒ 柱面被采样弦%s"
                  % (any(abs(d - 12.66) < 0.5 for d in ds),
                     "画出" if any(abs(d - 12.66) < 0.5 for d in ds) else "掩盖"))
            # A4 深度扫描
            print("     A4: 深度扫描(t_in=%.1f)" % t_in)
            envs = []
            worst_unc = worst_cl = 0.0
            for d in [2, 5, 10, 12.65, 12.67, 15, 20, 30, 40, 60, 100, 200, 400]:
                col = hg._free_width_ratio(d)
                sh = particle_shrink(hg, d)
                cloud = t_in * sh + 1.0 * (1 - 0.4 * sh)
                lim = clamp_lim(hg, d, tube_lim) - 2.0
                drawn = min(cloud, max(0.0, lim))
                worst_unc = max(worst_unc, t_in * col - cloud)
                worst_cl = max(worst_cl, t_in * col - drawn)
                envs.append((d, cloud))
                if d <= 60:
                    print("       d=%7.2f core=%6.2f cloud=%6.2f 钳后=%6.2f core-cloud=%+6.2f core-钳=%+6.2f"
                          % (d, t_in * col, cloud, drawn, t_in * col - cloud, t_in * col - drawn))
            mono = all(envs[i][1] >= envs[i + 1][1] - 1e-9 for i in range(len(envs) - 1))
            print("     A4: max(core-未钳cloud)=%+.3fpx  max(core-钳后cloud)=%+.3fpx  包络单调收窄=%s"
                  % (worst_unc, worst_cl, "是(先张不成立)" if mono else "否"))
            print("     A5: R_inner=%.1f  Ri/40=%.1f  t_in/40=%.3f  断口宽跳=%.2fpx  "
                  "粒子落地耗时(60..出口到内底)~%.2fs"
                  % (hg._R_inner, hg._R_inner / 40.0, t_in / 40.0, 0.2051 * t_in,
                     (-60 + math.sqrt(3600 + 2 * 450 * max(1.0, hg._lower_ball_cut - hg._lower_sand_bot))) / 450.0))
        self.stop()


Probe().run()
