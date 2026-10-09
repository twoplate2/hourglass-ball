# -*- coding: utf-8 -*-
"""**周期弹窗里那 40 个预设, 飞溅响应落在哪一段**(2026-10-09)。

背景: 2.10 的四项飞溅响应(数量/上限/射程/贴底)都由 `t_in` 相对本窗口最粗/最细内宽算出,
而 `neck_w` 相对**时长是对数**、相对**内宽是线性** ⇒ 响应曲线是"前段死、后段陡"。
用户看到的现象是「1 秒 / 10 秒 / 1 分钟 的飞溅看起来一样」。

本探针只算几何与派生量(不渲染), 把弹窗的 4 基础 × 10 倍数 = 40 个预设逐个铺开。

跑法: python tools/_probe_tier_response.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
import main as m                                 # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *a, **kw: None
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
m.HourglassWidget.save_config = lambda *_: None

MULTIPLIERS = [1, 2, 3, 5, 10, 20, 30, 50, 70, 100]


def _fmt(sec):
    if sec < 60:
        return "%g秒" % sec
    if sec < 3600:
        return "%g分" % (sec / 60)
    return "%g时" % (sec / 3600)


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (1904, 2890)
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        hg = self.hourglass
        base5 = hg._neck_width_for_duration(5.0) - hg._ow        # 最粗内宽
        print("")
        print("  平板口径: 最粗内宽 %.1fpx, 最细内宽 %.1fpx"
              % (base5, max(1.0, hg._neck_width_for_duration(36000.0) - hg._ow)))
        print("")
        print("  == 弹窗 40 个预设: 射程/数量各是多少(都以 1 秒 = 100% 为基准) ==")
        print("  %-8s %-6s %10s %8s %10s %9s %9s %9s" %
              ("基础", "倍数", "时长", "内宽px", "数量density", "射程现状", "去smooth", "跨度6x"))
        buckets = {}
        for _name, base in (("1秒", 1), ("10秒", 10), ("1分", 60), ("10分", 600)):
            for mult in MULTIPLIERS:
                dur = base * mult
                nw = hg._neck_width_for_duration(float(dur))
                t_in = max(1.0, nw - hg._ow)
                dens = max(0.15, min(1.5, 1.5 * t_in / base5))
                blend = hg._smoothstep_for(t_in, base5) if hasattr(hg, "_smoothstep_for") else None
                # 与 `_rebuild_height_table` 同一算式
                narrow = max(1.0, hg._neck_width_for_duration(36000.0) - hg._ow)
                wf = max(0.0, min(1.0, (t_in - narrow) / max(1e-9, base5 - narrow)))
                blend = wf * wf * (3.0 - 2.0 * wf)
                rng = 1.0 / 3.0 + (2.0 / 3.0) * blend
                key = round(rng * 20)                     # 5% 一档的观感桶
                buckets.setdefault(key, []).append(_fmt(dur))
                lin = 1.0 / 3.0 + (2.0 / 3.0) * wf          # 去掉 smoothstep
                wide = 1.0 / 6.0 + (5.0 / 6.0) * wf         # 把总跨度从 3x 扩到 6x
                print("  %-8s %-6d %10s %8.1f %10.3f %9.0f%% %8.0f%% %8.0f%%"
                      % (_name, mult, _fmt(dur), t_in, dens, 100.0 * rng,
                         100.0 * lin, 100.0 * wide))
        print("")
        print("  == 40 个预设按射程分桶(每桶 5% 宽) ==")
        for k in sorted(buckets, reverse=True):
            v = buckets[k]
            print("     %3d%%: %2d 个   %s" % (k * 5, len(v), " / ".join(v[:9]) + (" ..." if len(v) > 9 else "")))
        big = [k for k in buckets if len(buckets[k]) >= 5]
        print("     ⇒ %d 个桶里装了 ≥5 个预设(即**同一观感**); 共 %d 个桶"
              % (len(big), len(buckets)))
        self.stop()


Probe().run()
