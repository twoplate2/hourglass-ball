# -*- coding: utf-8 -*-
"""颈部粗细(neck_w)的**设计表**: 上下限、插值曲线、以及实际可取到的档数。

设计(见 `main.py:_neck_width_for_duration` / `_neck_width_limits`):
    半宽 neck_w = lo + (hi - lo) * (1 - t),  t = (ln dur - ln 5) / (ln 36000 - ln 5)
    dur <= 5s      -> hi   (最粗)
    dur >= 36000s  -> lo   (最细, 36000s = 10 小时)
中间是 **log 插值**(不是分档), 但最后 `round()` 成整数半宽 ⇒ 实际只有 (hi-lo+1) 个取值。

跑法: python tools/_probe_neck_widths.py [窗口宽 窗口高]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

W_ARG = int(sys.argv[1]) if len(sys.argv) > 1 else 1904
H_ARG = int(sys.argv[2]) if len(sys.argv) > 2 else 2890
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75" if W_ARG > 1000 else "1.0")
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

SHOW = [1, 3, 4, 5, 6, 10, 15, 20, 30, 50, 60, 90, 120, 300, 600,
        1800, 3600, 7200, 18000, 36000, 72000, 360000]


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (W_ARG, H_ARG)
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        if (Window.size[0], Window.size[1]) != (W_ARG, H_ARG):
            print("  !! 窗口没拿到 %dx%d, 实得 %s ⇒ 本次不作数"
                  % (W_ARG, H_ARG, tuple(Window.size)))
            self.stop()
            return
        hg = self.hourglass
        lo, hi = hg._neck_width_limits()
        print("")
        print("  窗口 %dx%d  density=%.2f  画布宽 w=%.0f" % (W_ARG, H_ARG, dp(1), hg.width))
        print("  半宽 lo=%.0f hi=%.0f  ⇒  **全宽 %.0f ~ %.0f px**" % (lo, hi, 2 * lo, 2 * hi))
        print("  取值域: 半宽只取整数 ⇒ 最多 **%d 个不同粗细**" % (hi - lo + 1))
        print("  饱和: dur ≤ 5s 恒为最粗; dur ≥ 36000s(10 小时) 恒为最细")
        print("")
        print("  %10s %10s %12s %10s   %s" % ("时长", "半宽neck_w", "全宽(px)", "占画布宽", "阶段"))
        seen = {}
        for d in SHOW:
            nw = hg._neck_width_for_duration(float(d))
            t_in = max(1.0, nw - hg._ow)
            seen.setdefault(nw, d)
            tag = ""
            if d <= 5:
                tag = "最粗(饱和)"
            elif d >= 36000:
                tag = "最细(饱和)"
            print("  %10s %10d %12d %9.1f%%   %s"
                  % (_fmt(d), nw, 2 * nw, 100.0 * 2 * nw / hg.width, tag))
        # 实际可达的粗细数: 扫全时长域
        vals = set()
        d = 1.0
        while d <= 360000:
            vals.add(hg._neck_width_for_duration(d))
            d *= 1.02
        print("")
        print("  扫 1s→100h(×1.02 步进)实际取到 **%d 种半宽**(= lo..hi 全部整数)" % len(vals))
        print("  管内壁半宽 t_in = neck_w − 描边宽(ow≈%.0f) ⇒ %.0f ~ %.0f px"
              % (hg._ow, max(1.0, lo - hg._ow), max(1.0, hi - hg._ow)))
        # 2.10 里它还是飞溅缩放的输入
        for d in (5, 30, 60000):
            hg.set_duration(float(d))
            print("      dur=%-7s ⇒ t_in=%.2f  splash_density=%.3f  blend=%.3f  speed=%.3f"
                  % (_fmt(d), hg._taper["t_in"], hg._splash_density,
                     hg._splash_origin_blend, hg._splash_speed_scale))
        self.stop()


def _fmt(sec):
    if sec < 60:
        return "%gs" % sec
    if sec < 3600:
        return "%gm" % (sec / 60)
    return "%gh" % (sec / 3600)


Probe().run()
