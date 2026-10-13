# -*- coding: utf-8 -*-
"""印**墨量覆盖率闸**(`_stream_ink_cover_q`)在几个口径上算出什么 —— 判"会不会误伤大窗口"。

判据(两条都要看):
  1. **平板口径 1904×2890 必须 `q == 1.0`** —— 那是用户没骂过的那一档, 闸不许动它;
  2. 1080×1920 应该把 `q` 压下来(`want < 1.85` ⇒ 2px 线占比变小) ⇒ 柱内墨量从
     **饱和(>100%)** 回到 ~`INK_COVER_TARGET`。

跑法: `python src/tools/_probe_ink_cover.py`
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock           # noqa: E402
from kivy.core.window import Window    # noqa: E402
import main as m                       # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
m.HourglassWidget.save_config = lambda *_: None


class Probe(m.HourglassApp):
    def on_start(self):
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        hg = self.hourglass
        cases = [(400, 800, 50.0), (1080, 1920, 50.0), (1080, 1920, 5.0),
                 (1904, 2890, 50.0), (1440, 3120, 50.0)]
        orig = m.HourglassWidget._stream_ink_cover_q
        for (W, H, D) in cases:
            seen = {}

            def patched(self, x_clip, _orig=orig, _seen=seen):
                q = _orig(self, x_clip)
                _seen["x_clip"] = x_clip
                _seen["q"] = q
                return q

            m.HourglassWidget._stream_ink_cover_q = patched
            hg.set_duration(D)
            hg.size = (W, H)
            hg._rebuild_height_table()
            hg.reset()
            hg.toggle()
            for _ in range(int(D * 60) // 2):        # 跑到中段(收尾那条闸还没咬合)
                hg.tick(1 / 60.0)
            hg.redraw()
            q = seen.get("q")
            xc = seen.get("x_clip")
            m.HourglassWidget._stream_ink_cover_q = orig
            if q is None:
                print("  %4dx%-5d D=%-5g 闸没被调到(这一档没出生粒子?)" % (W, H, D))
                continue
            colw = 2.0 * max(1.0, xc - 1.0)
            rate = m.FLOW_BASE_RATE * hg._particle_motion_scale
            want = m.INK_COVER_TARGET * colw / (rate * m.INK_TRAIL_MEAN)
            print("  %4dx%-5d D=%-5g | 出生带半宽 %5.2f -> 柱宽 %5.2f | rate %6.0f/s | "
                  "期望线宽 %.3f -> q=%.3f | 压后墨量 %.0f%% | 2px 占比 %.0f%%"
                  % (W, H, D, xc, colw, rate, want, q,
                     100.0 * (1.0 + 0.85 * q) * rate * m.INK_TRAIL_MEAN / colw,
                     100.0 * 0.85 * q))
        self.stop()


Probe().run()
