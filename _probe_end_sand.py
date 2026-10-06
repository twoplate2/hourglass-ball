# -*- coding: utf-8 -*-
"""基准测试三档**归零那一刻**的沙量 —— 用户 2026-10-06 报的 bug。

用户原话:「在基准测试中, 三档测试中**至少有两档**, 当**时间归零**的时候…
沙漏里**还存在很多沙子**」。

本探针逐档把 `elapsed` 推到 `duration`(及之后), 量**上球还剩多少沙**:
  · `_upper_sand_height_px()` —— 上球沙面高度(应用自己的量)
  · 渲染出来的上球沙像素数 —— 实际画出来的量(QA 规矩: 验画出来的, 不是我以为的)

跑法: python _probe_end_sand.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")

import main as m                                     # noqa: E402
from kivy.clock import Clock                         # noqa: E402
from kivy.core.window import Window                  # noqa: E402
from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE   # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50}
m.HourglassWidget.save_config = lambda *_: None

PERIODS = (1, 5, 15)
PROBE = {
    "canvas_h": 651.0,
    "upper": [],
    "mound": [],
    "upper_px": [],
    "mound_px": [],
}


def run():
    from PIL import Image
    import numpy as np

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.3)

        def go(self, _dt):
            hg = self.hourglass
            hg._rebuild_height_table()
            base = np.asarray([int(m.hex_rgb(m.SAND_PRESETS[0][1])[i] * 255)
                               for i in range(3)])
            for period in PERIODS:
                hg.set_duration(float(period))
                hg._rebuild_height_table()
                for frac, tag in ((0.999, "t=duration"), (1.0, "t=exact")):
                    hg.elapsed = period * frac
                    hg.running = False
                    hg.redraw()
                    w, h = int(Window.width), int(Window.height)
                    px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
                    img = np.asarray(Image.frombytes("RGBA", (w, h), px)
                                     .transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                                     .convert("RGB")).astype(int)
                    # 上球在画布上半部; 用"接近沙色"的像素数当沙量
                    near = (np.abs(img - base).max(axis=2) < 40)
                    top = near[: h // 2]
                    bot = near[h // 2:]
                    PROBE["upper"].append((period, tag, hg._upper_sand_height_px()))
                    PROBE["mound"].append((period, tag, hg._mound_height_px()))
                    PROBE["upper_px"].append((period, tag, int(top.sum())))
                    PROBE["mound_px"].append((period, tag, int(bot.sum())))
                    Image.fromarray(img.astype("uint8")).save(
                        ROOT / "_shot" / "r37" / ("end_%ds_%s.png"
                                                  % (period, tag.replace("=", ""))))
            Clock.schedule_once(lambda dt: self.stop(), 0.3)

    P().run()


if __name__ == "__main__":
    # ⚠️ 不走 App 主循环的 readPixels 需要 GL —— 用 App 跑, 与闸门同法。
    run()
    print("")
    print("  === 归零那一刻的沙量 ===")
    print("  %-6s %-11s %-14s %-14s %-12s %s"
          % ("周期", "时刻", "上球高(px)", "下堆高(px)", "上球沙像素", "下球沙像素"))
    for i in range(len(PROBE["upper"])):
        p, tag, uh = PROBE["upper"][i]
        _, _, mh = PROBE["mound"][i]
        _, _, up = PROBE["upper_px"][i]
        _, _, mp = PROBE["mound_px"][i]
        flag = "  **上球还有沙**" if uh > 1.0 else ""
        print("  %-6s %-11s %-14.1f %-14.1f %-12d %d%s"
              % (p, tag, uh, mh, up, mp, flag))
