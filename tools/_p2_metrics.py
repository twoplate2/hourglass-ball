"""2 号评审 · 一次跑完 A2 / A4 / A5 的实测(v2)。

流程: set_duration -> seed -> toggle -> 只推进 tick(redraw 打桩)
      elapsed 首次 >= GRAB 时: 恢复 redraw -> redraw() -> glReadPixels 存 frame.png
      继续推进到整轮结束, 顺路用 flares 采落点 x。
"""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "_p2_metrics"
SAND_RGB = np.array([217, 163, 96])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p2-metrics-") as home:
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
                Clock.schedule_once(self.prepare, 0.4)

            def grab_frame(self, name):
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                img.save(OUT / name)
                return img

            def prepare(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                w.set_duration(float(os.environ.get("P2_PERIOD", "5")))
                random.seed(23)
                w.toggle()
                self.hits = []
                self._step_to(w, float(os.environ.get("P2_GRAB", "2.5")))
                print("PROBE grabbed at elapsed=%.3f running=%s dur=%.1f"
                      % (w.elapsed, w.running, w.duration))
                Clock.schedule_once(self.show, 0.10)

            def _step_to(self, w, target):
                steps = 0
                w.redraw = lambda: None
                while w.elapsed < target and steps < 1400:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                    steps += 1
                    if float(os.environ.get("P2_COLLECT", "0")) == "1":
                        for f in w.flares:
                            self.hits.append((w.elapsed, f["x"],
                                              w.get_mound_top_y(),
                                              w._lower_y_c, w._lower_sand_bot))
                        w.flares = []

            def show(self, _dt):
                """恢复 redraw 并重画; 必须等下一帧 on_draw 把树渲进 framebuffer。"""
                w = self.hourglass
                w.redraw = type(w).redraw.__get__(w)
                w.redraw()
                Clock.schedule_once(self.take, 0.15)

            def take(self, _dt):
                self.grab_frame("frame.png")
                w = self.hourglass
                print("PROBE frame saved at elapsed=%.3f running=%s dur=%.1f"
                      % (w.elapsed, w.running, w.duration))
                # 二跑: 收落点(不抓图, 只推进物理)
                now[0] += 0.5
                w.reset()
                random.seed(23)
                w.toggle()
                os.environ["P2_COLLECT"] = "1"
                self._step_to(w, w.duration)
                print("PROBE hits=%d after full cycle (dur=%.1f)"
                      % (len(self.hits), w.duration))
                Clock.schedule_once(self.analyse, 0.2)

            def analyse(self, _dt):
                w = self.hourglass
                img = Image.open(OUT / "frame.png")
                A = np.asarray(img.convert("RGB")).astype(int)
                cx = w._cx
                # ---- A4: 落点分布 ----
                xs = np.array([h[1] for h in self.hits])
                if len(xs):
                    dists = np.abs(xs - cx)
                    print("A4 hits n=%d  |x-cx|: p50=%.1f p90=%.1f p99=%.1f max=%.1f"
                          % (len(xs), np.percentile(dists, 50), np.percentile(dists, 90),
                             np.percentile(dists, 99), dists.max()))
                    chords = np.array([w._sand_half_w(h[2], h[3]) for h in self.hits])
                    ok = chords > 1
                    frac = dists[ok] / chords[ok]
                    print("A4 chord p50=%.1f max=%.1f" % (np.percentile(chords, 50),
                                                          chords.max()))
                    for thr in (0.5, 0.6, 0.7, 0.8, 0.9):
                        print("A4   frac(|x-cx| > %.2f*chord) = %.5f"
                              % (thr, float((frac > thr).mean())))
                    print("A4   frac(chord-|x-cx| < 10px) = %.5f"
                          % float((chords[ok] - dists[ok] < 10).mean()))
                # ---- A2: 出口以下的带 ----
                sand = (np.abs(A - SAND_RGB).max(axis=2) < 70)
                W = sand.sum(axis=1)
                rows = [y for y in range(A.shape[0]) if 6 <= W[y] <= 46]
                runs = []
                if rows:
                    s = prev = rows[0]
                    for y in rows[1:]:
                        if y - prev > 2:
                            runs.append((s, prev)); s = y
                        prev = y
                    runs.append((s, prev))
                print("A2 narrow runs:", runs, " (row width>200 = mound; 6..46 = stream)")
                for (y0, y1) in runs:
                    if y1 - y0 < 10:
                        continue
                    adjR, widths = [], []
                    for y in range(y0, y1 + 1):
                        xs2 = np.nonzero(sand[y])[0]
                        if len(xs2) < 4:
                            continue
                        a, b = xs2.min(), xs2.max()
                        seg = A[y, a:b + 1, 0]
                        adjR.extend(np.abs(np.diff(seg)).tolist())
                        widths.append(b - a + 1)
                    adjR = np.array(adjR)
                    if not len(adjR):
                        continue
                    print("A2 band y %d..%d width p50=%.0f max=%.0f | horizontal "
                          "|dR| p50=%.1f p90=%.1f p99=%.1f max=%d  >15:%.3f  =0:%.3f"
                          % (y0, y1, np.percentile(widths, 50), max(widths),
                             np.percentile(adjR, 50), np.percentile(adjR, 90),
                             np.percentile(adjR, 99), adjR.max(),
                             float((adjR > 15).mean()), float((adjR == 0).mean())))
                    cols = [int(np.nonzero(sand[y])[0].mean()) for y in range(y0, y1 + 1)
                            if sand[y].any()]
                    colR = np.array([A[y, c, 0] for y, c in zip(range(y0, y1 + 1), cols)])
                    print("A2   center-col R: min=%d max=%d p10=%.0f p90=%.0f  "
                          "vertical |dR| p50=%.1f p90=%.1f"
                          % (colR.min(), colR.max(), np.percentile(colR, 10),
                             np.percentile(colR, 90),
                             np.percentile(np.abs(np.diff(colR)), 50),
                             np.percentile(np.abs(np.diff(colR)), 90)))
                if runs:
                    best = max(runs, key=lambda r: r[1] - r[0])
                    box = (int(cx) - 32, best[0] - 24, int(cx) + 32, best[1] + 24)
                    img.crop(box).resize(((box[2] - box[0]) * 4,
                                          (box[3] - box[1]) * 4),
                                         Image.NEAREST).save(OUT / "band4x.png")
                # ---- A5: 上球沙面 ----
                # 上球: 在 x=cx 附近先找最上面的宽沙行(排除顶栏色块 y<120)
                rows_sand = [y for y in range(120, A.shape[0]) if W[y] > 150]
                if rows_sand:
                    ysurf = rows_sand[0]
                    print("A5 上球沙体首行(宽>150) y=%d" % ysurf)
                    prof = []
                    for x in range(A.shape[1]):
                        col = np.nonzero(sand[120:ysurf + 30, x])[0]
                        prof.append(120 + col.min() if len(col) else -1)
                    prof = np.array(prof)
                    # 只取连续有效段
                    valid = prof > 0
                    runs2 = []
                    s = prev = None
                    for x in range(len(valid)):
                        if valid[x]:
                            if s is None:
                                s = x
                            prev = x
                        elif s is not None:
                            runs2.append((s, prev)); s = None
                    if s is not None:
                        runs2.append((s, prev))
                    run = max(runs2, key=lambda r: r[1] - r[0]) if runs2 else None
                    if run:
                        seg = prof[run[0]:run[1] + 1]
                        steps = np.diff(seg)
                        import collections
                        print("A5 沙面 x %d..%d  n=%d" % (run[0], run[1], len(seg)))
                        print("A5 逐列台阶分布:", dict(collections.Counter(steps.tolist())))
                        n = len(seg)
                        print("A5 段边界(均分16):",
                              [run[0] + round(i * n / 16) for i in range(1, 16)])
                        nz = [run[0] + i + 1 for i in np.nonzero(np.abs(steps) >= 1)[0]]
                        print("A5 |台阶|>=1 的 x:", nz)
                        print("A5 沙面 y: min=%d max=%d 峰谷差=%d" %
                              (seg.min(), seg.max(), seg.max() - seg.min()))
                        box2 = (run[0], seg.min() - 12, run[1] + 1, seg.max() + 4)
                        img.crop(box2).resize(((box2[2] - box2[0]) * 6,
                                               (box2[3] - box2[1]) * 6),
                                              Image.NEAREST).save(OUT / "surface6x.png")
                        np.save(OUT / "surface_prof.npy", seg)
                print("PROBE wrote", OUT)
                self.stop()

        ProbeApp().run()


main()
