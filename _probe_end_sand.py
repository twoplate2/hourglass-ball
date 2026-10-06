# -*- coding: utf-8 -*-
"""基准测试三档**归零那一刻**的沙量 —— 用户 2026-10-06 报的 bug。

用户原话:「在基准测试中, 三档测试中**至少有两档**, 当**时间归零**的时候…
沙漏里**还存在很多沙子**」。

本探针逐档把 `elapsed` 推到 `duration`(及之后), 量**上球还剩多少沙**:
  · `_upper_sand_height_px()` —— 上球沙面高度(应用自己的量)
  · 渲染出来的**上球沙像素数** —— 实际画出来的量(QA 规矩: 验画出来的, 不是我以为的)

⚠️ **"上球沙像素"只能看相对变化, 不能当绝对沙量**: 它按**窗口上半部**计数, 而顶部那排
色块按钮里「金砂」本身就是沙色(约 2000px) ⇒ 有个恒定的大底数。判"上球还剩下没有沙"
要看**它相对同一周期其它时刻的落差**, 或者直接看 `_upper_sand_height_px()`。

🔴 **2026-10-06 重写(原版读数全是假的)**: 原版在**同一个回调里**循环
`hg.elapsed = t; hg.redraw(); glReadPixels(...)` —— Kivy 在同一次回调里**只改指令、
不重绘**, 读到的是**上一帧的帧缓冲**。于是所有样本都是**同一张**"起跑前"的画面,
"上球沙像素"几乎不随状态变。**那个"读数"曾被用来支撑一条真实存在的结论** ——
结论本身后来由正确的量具独立复现了, 但**原版的数字一个都不能引用**。
正确姿势: 改状态 → `Canvas.ask_update()` → **让出到下一帧** → 再读。

⚠️ 另外两条同族陷阱(都踩过):
  · `running=False` 会叠暂停遮罩(`_pause_color.a=0.55`), 沙色掩膜直接失效 ⇒ 量之前
    `running=True`;
  · **不要硬设 `KIVY_METRICS_DENSITY`** —— 那是"模拟设备密度", 会让桌面布局整体放大,
    量出来的窗口与真实桌面不是一回事。要复现设备请**显式**在环境变量里给。

跑法: python _probe_end_sand.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

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
# (elapsed 相对 duration 的位置, 标签)
CASES = ((0.999, "t=duration"), (1.0, "t=exact"))
ROWS = []


def run():
    from PIL import Image
    import numpy as np

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.setup, 1.3)

        def setup(self, _dt):
            self.hg = self.hourglass
            self.hg._rebuild_height_table()
            self.base = np.asarray([int(m.hex_rgb(m.SAND_PRESETS[0][1])[i] * 255)
                                    for i in range(3)])
            self.queue = [(p, f, tag) for p in PERIODS for f, tag in CASES]
            self.step()

        def step(self, *_a):
            """**两个时钟步**: 先摆状态 + 让帧, 下一步再读像素。"""
            Clock.unschedule(self.step)
            if not self.queue:
                Clock.schedule_once(lambda dt: self.stop(), 0.3)
                return
            period, frac, tag = self.queue[0]
            hg = self.hg
            if abs(hg.duration - float(period)) > 1e-9:
                hg.set_duration(float(period))
                hg._rebuild_height_table()
            hg.elapsed = period * frac
            hg.running = True          # 不叠暂停遮罩(0.55 alpha 会把沙色冲淡到掩膜失效)
            hg.redraw()
            hg.running = False
            Window.canvas.ask_update()
            Clock.schedule_once(self.read, 0.0)

        def read(self, *_a):
            import numpy as np
            period, frac, tag = self.queue.pop(0)
            hg = self.hg
            w, h = int(Window.width), int(Window.height)
            px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
            img = np.asarray(Image.frombytes("RGBA", (w, h), px)
                             .transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                             .convert("RGB")).astype(int)
            # 上球在画布上半部; 用"接近沙色"的像素数当沙量
            near = (np.abs(img - self.base).max(axis=2) < 40)
            top = near[: h // 2]
            bot = near[h // 2:]
            ROWS.append((period, tag, hg._upper_sand_height_px(),
                         hg._mound_height_px(), int(top.sum()), int(bot.sum())))
            out = ROOT / "_shot" / "r38"
            out.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img.astype("uint8")).save(
                out / ("end_%ds_%s.png" % (period, tag.replace("=", ""))))
            Clock.schedule_once(self.step, 0.05)

    P().run()


if __name__ == "__main__":
    run()
    print("")
    print("  === 归零那一刻的沙量 ===")
    print("  %-6s %-11s %-14s %-14s %-12s %s"
          % ("周期", "时刻", "上球高(px)", "下堆高(px)", "上球沙像素", "下球沙像素"))
    for period, tag, uh, mh, up, mp in ROWS:
        flag = "  **上球还有沙**" if uh > 1.0 else ""
        print("  %-6s %-11s %-14.1f %-14.1f %-12d %d%s"
              % (period, tag, uh, mh, up, mp, flag))
    print("")
