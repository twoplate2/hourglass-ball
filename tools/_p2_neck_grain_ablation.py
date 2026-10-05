"""2 号评审 · A1 取证: 颈部 128 根 grain Line 到底改变了什么?

做法(受控 A/B):
  1. 冻结到某个 elapsed(5s 档 2.5s / 15s 档 7.5s),关掉 tick,只留 redraw。
  2. 状态 S 下 redraw() 一次 -> 抓 "on"。
  3. **同一状态**再 redraw() 一次 -> 抓 "on2"(对照: on vs on2 必须 0 差,否则方法不可信)。
  4. 同一状态 redraw() -> `_hide_neck_grains()` -> 抓 "off"。
  5. 输出: 逐像素差 / 颈部带统计 / 4x·8x 裁图。

输出目录 benchmark_logs/_p2_neck_grains/
"""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "_p2_neck_grains"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p2-neck-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
        import random
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.setup, 1.0)

            def setup(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.prepare, 0.4)

            def prepare(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                w.set_duration(5.0)
                random.seed(23)
                w.toggle()
                real = w.redraw
                w.redraw = lambda: None
                while w.elapsed < 2.5:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                w.redraw = real
                self.saved = {}
                self.steps = ["on", "on2", "off"]
                self.next_step(0)

            def next_step(self, _dt):
                if not self.steps:
                    self.finish()
                    return
                step = self.steps.pop(0)
                w = self.hourglass
                w.redraw()
                if step == "off":
                    w._hide_neck_grains()
                Clock.schedule_once(lambda _dt, s=step: self.take(s), 0.10)

            def grab(self, name):
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                img.save(OUT / ("abl_%s.png" % name))
                return np.asarray(img).astype(int)

            def take(self, step):
                self.saved[step] = self.grab(step)
                Clock.schedule_once(self.next_step, 0.10)

            def finish(self):
                w = self.hourglass
                a, a2, b = self.saved["on"], self.saved["on2"], self.saved["off"]
                ctl = np.abs(a - a2).max(axis=2)
                d = np.abs(a - b).max(axis=2)
                print("PROBE control  on vs on2 : changed px = %d  max = %d"
                      % (int((ctl > 0).sum()), int(ctl.max())))
                print("PROBE grains   on vs off : changed px = %d  max = %d  mean = %.2f"
                      % (int((d > 0).sum()), int(d.max()),
                         float(d[d > 0].mean()) if (d > 0).any() else 0.0))
                ys, xs = np.nonzero(d > 0)
                if len(ys):
                    print("PROBE bbox y %d..%d  x %d..%d" % (ys.min(), ys.max(),
                                                             xs.min(), xs.max()))
                # 颈部带: x 中心 ±30, 从孔口向上 120px
                cx = w._cx
                outlet = 2 * w._neck_y - w._taper["y_bot"]
                y0 = int(w.height - outlet)          # 图像 y (向下)
                band = (slice(max(0, y0 - 130), y0 + 6), slice(int(cx) - 30, int(cx) + 30))
                for nm, P in (("on", a), ("off", b)):
                    blk = P[band]
                    print("PROBE neck band %s: mean (%.1f,%.1f,%.1f) std %.2f  "
                          "row-std median %.2f  col-std median %.2f"
                          % (nm, *blk.reshape(-1, 3).mean(axis=0), blk.std(),
                             np.median(blk.std(axis=1)), np.median(blk.std(axis=0))))
                # 逐行 std 的剖面(找横向分界线)
                prof_on = a[band].std(axis=(1, 2))
                prof_off = b[band].std(axis=(1, 2))
                np.save(OUT / "rowstd_on.npy", prof_on)
                np.save(OUT / "rowstd_off.npy", prof_off)
                for i in range(0, len(prof_on), 6):
                    print("PROBE row %3d  y=%4d  std on %6.2f  off %6.2f"
                          % (i, y0 - 130 + i, prof_on[i], prof_off[i]))
                # 裁图: 颈部 + 过渡, 4x / 8x
                im_on = Image.open(OUT / "abl_on.png")
                im_off = Image.open(OUT / "abl_off.png")
                box = (int(cx) - 60, max(0, y0 - 150), int(cx) + 60, y0 + 40)
                pair = Image.new("RGB", ((box[2] - box[0]) * 2 + 12, box[3] - box[1]),
                                 (0, 0, 0))
                pair.paste(im_on.crop(box), (0, 0))
                pair.paste(im_off.crop(box), ((box[2] - box[0]) + 12, 0))
                pair = pair.resize((pair.width * 4, pair.height * 4), Image.NEAREST)
                pair.save(OUT / "neck_pair4x.png")
                print("PROBE grains used = %d / pool %d" % (w._neck_grain_count, len(w._neck_grain_pool)))
                print("PROBE neck_y=%.1f y_bot=%.1f outlet=%.1f h=%.1f" % (w._neck_y, w._taper["y_bot"], outlet, w.height))
                print("PROBE wrote", OUT)
                self.stop()

        ProbeApp().run()


main()
