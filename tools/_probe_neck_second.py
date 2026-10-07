# -*- coding: utf-8 -*-
"""**最后 1 秒逐帧**(≥30 张) —— 用户 2026-10-07 要求:「最后1秒截图至少30张去比较」。

为什么要这个量具: 末段的现象是**短时的**(0.25s 量级的排空动画), 稀疏取样(每隔 0.2~0.5s)
会把"事件"混叠掉 —— 本项目已经栽过一次(飞溅水位那次每 4 秒采一次, 把秒级变化读成缓变)。
⇒ 末段必须按**帧率**采(30fps ⇒ 1/30s 一步), 而且要在**平板口径**下采(桌面 400x800 的颈部
只有十几像素高, 什么也看不出来)。

跑法:
    python tools/_probe_neck_second.py                # 15s 档, 最后 1 秒, 31 帧
    python tools/_probe_neck_second.py 50 1.5         # 50s 档, 最后 1.5 秒
输出: _shot/<SHOTDIR>/sec_*.png + 一张拼图 `_shot/<SHOTDIR>/strip.png`
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TABLET_W, TABLET_H = 1904, 2890
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
import main as m                                 # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *a, **kw: None
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
m.HourglassWidget.save_config = lambda *_: None

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
WINDOW = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
SHOTDIR = os.environ.get("HG_SHOTDIR", "sec")
STEP = 1.0 / 30.0


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (TABLET_W, TABLET_H)
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        out = ROOT / "_shot" / SHOTDIR
        out.mkdir(parents=True, exist_ok=True)
        hg = self.hourglass
        if (Window.size[0], Window.size[1]) != (TABLET_W, TABLET_H):
            print("  !! 窗口没拿到 %dx%d, 实得 %s ⇒ 本次不作数"
                  % (TABLET_W, TABLET_H, tuple(Window.size)))
            self.stop()
            return
        hg.set_duration(PERIOD)
        n = int(round(WINDOW / STEP)) + 1
        times = [PERIOD - WINDOW + i * STEP for i in range(n)]
        print("  平板口径 %dx%d  R=%.1f  周期 %.0fs  末段 %.2fs  共 %d 帧(%.4fs 步长)"
              % (TABLET_W, TABLET_H, hg._R, PERIOD, WINDOW, n, STEP))
        # 每帧: 推 elapsed -> redraw -> 截图; 用 Kivy 时钟逐帧跑, 才拿得到真正的渲染结果
        self._times, self._i, self._out, self._shots = times, 0, out, []
        Clock.schedule_interval(self.step, 0.0)

    def step(self, _dt):
        if self._i >= len(self._times):
            Clock.unschedule(self.step)
            Clock.schedule_once(self.assemble, 0.3)
            return
        t = self._times[self._i]
        hg = self.hourglass
        hg.reset()
        hg.elapsed = t
        hg._done_at = None
        hg.redraw()
        name = "%s/sec_%02d_%06.3f.png" % (self._out, self._i, t)
        Window.screenshot(name=name)
        self._i += 1

    def assemble(self, _dt):
        from PIL import Image, ImageDraw
        import glob
        fs = sorted(glob.glob(str(self._out / "sec_*.png")))
        if not fs:
            print("  !! 没有截到图"); self.stop(); return
        im = Image.open(fs[0]).convert("RGB")
        w, h = im.size
        # 颈部+下球顶: 取中轴附近一条竖带
        box = (int(w * 0.30), int(h * 0.44), int(w * 0.70), int(h * 0.66))
        tiles = []
        for f in fs:
            c = Image.open(f).convert("RGB").crop(box)
            c = c.resize((c.width // 2, c.height // 2))
            d = ImageDraw.Draw(c)
            d.text((4, 2), Path(f).stem.split("_")[-1], fill=(200, 0, 0))
            tiles.append(c)
        cols = 6
        rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
        W = max(sum(t.width for t in r) + 4 * (len(r) - 1) for r in rows)
        H = sum(max(t.height for t in r) for r in rows) + 4 * (len(rows) - 1)
        sheet = Image.new("RGB", (W, H), (70, 70, 70))
        y = 0
        for r in rows:
            x = 0
            for t in r:
                sheet.paste(t, (x, y))
                x += t.width + 4
            y += max(t.height for t in r) + 4
        path = self._out / "strip.png"
        sheet.save(str(path))
        print("  ⇒ %d 帧, 拼图 %s  %s" % (len(fs), path, sheet.size))
        self.stop()


Probe().run()
