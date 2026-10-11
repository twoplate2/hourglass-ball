# -*- coding: utf-8 -*-
"""2.55 边界状态探针: 注满 / 末段 / 归零 / 重置 / 暂停 —— 看沙柱材质带在各处还在不在。

只做取证: 每个状态截一帧, 量"出口以下条带里有多少非背景像素" + 抓异常。
跑法: python tools/_t255_boundary_probe.py [--px 1080,1920]
"""
import argparse
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--px", default="1080,1920")
    ap.add_argument("--period", type=float, default=5.0)
    args = ap.parse_args()
    WANT = tuple(int(v) for v in args.px.split(","))
    outdir = ROOT / "benchmark_logs" / "_t255_boundary"
    outdir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="t255b-") as home:
        os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1",
                          KIVY_METRICS_DENSITY="1", KIVY_METRICS_FONTSCALE="1")
        sys.path.insert(0, str(ROOT))
        import random
        import numpy as np
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
        import main as m

        m.HourglassWidget.load_config = lambda *_: {"duration": args.period}
        m.HourglassWidget.save_config = lambda *_: None
        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        now = [1000.0]
        m.time = SimpleNamespace(perf_counter=lambda: now[0])

        class Probe(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                r = Window.width / Window.system_size[0]
                Window.system_size = (round(WANT[0] / r), round(WANT[1] / r))
                self._want = WANT
                self._prev, self._stable = None, 0
                Clock.schedule_interval(self._settle, 0.1)

            def _settle(self, _dt):
                w = getattr(self, "hourglass", None)
                if w is None:
                    return
                key = (round(Window.width), round(Window.height),
                       round(getattr(w, "_R_inner", 0.0), 3))
                if key == self._prev:
                    self._stable += 1
                else:
                    self._prev, self._stable = key, 0
                if self._stable >= 3 and round(Window.width) == self._want[0]:
                    Clock.unschedule(self._settle)
                    Clock.schedule_once(self.begin, 0.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(args.period)
                Clock.unschedule(w.tick)
                self.real_draw = w.redraw
                w.redraw = lambda: None
                w.toggle()                       # 开始
                random.seed(23)
                self.states = [
                    ("t0.2", 0.2, None),
                    ("t4.9", args.period - 0.1, None),
                    ("t_end", args.period, None),
                    ("reset", None, "reset"),
                    ("t2.5_pause", 2.5, "pause"),
                    ("t2.5_resume", None, "resume"),
                ]
                self.run_state(0)

            def advance_to(self, target):
                w = self.hourglass
                while w.elapsed + 1e-8 < target and w.running:
                    step = min(1 / 120, target - w.elapsed)
                    now[0] += step
                    w.tick(step)

            def run_state(self, idx):
                if idx >= len(self.states):
                    Clock.schedule_once(lambda _dt: self.stop(), 0.1)
                    return
                name, t, action = self.states[idx]
                w = self.hourglass
                w.redraw = lambda: None
                try:
                    if action == "reset":
                        w.reset()
                    elif action == "pause":
                        if not w.running:
                            w.toggle()        # 从停止态先启动
                        self.advance_to(t)
                        w.toggle()            # running -> paused
                    elif action == "resume":
                        w.toggle()            # paused -> running
                    elif t is not None:
                        self.advance_to(t)
                    self.err = None
                except Exception as exc:          # noqa: BLE001
                    self.err = repr(exc)
                w.redraw = self.real_draw
                try:
                    w.redraw()                    # 显式重绘(否则读到上一张帧缓冲)
                except Exception as exc:          # noqa: BLE001
                    self.err = repr(exc)
                Clock.schedule_once(lambda _dt, n=name: self.capture(n), 0.08)

            def capture(self, name):
                w = self.hourglass
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                im = Image.frombytes("RGBA", (width, height), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM)
                if im.size != WANT:
                    im = im.resize(WANT, Image.Resampling.LANCZOS)
                im.convert("RGB").save(outdir / ("state_%s.png" % name))
                a = np.asarray(im.convert("RGB"), dtype=np.int32)
                H, W = a.shape[:2]
                cx = w._cx
                outlet = 2 * w._neck_y - w._taper["y_bot"]
                row0 = int(round(H - outlet))
                # 出口以下 20..320 px 的条带; 参考背景取该条带右外侧
                r1, r2 = min(H - 1, row0 + 20), min(H - 1, row0 + 320)
                x1, x2 = int(cx - 40), int(cx + 40)
                ref = a[r1:r2, x2 + 60:x2 + 66].mean(axis=1, keepdims=True)
                band = a[r1:r2, x1:x2]
                dd = np.abs(band - ref).max(axis=2)
                frac = float((dd > 12).mean()) if band.size else float("nan")
                # 上球余量 / 粒子数 / 侧柱节点数
                side = None
                try:
                    side = len(w._neck_sand_side())
                except Exception as exc:          # noqa: BLE001
                    side = "ERR:%r" % exc
                print("STATE %-11s elapsed=%-6.2f running=%s band_rows=[%d,%d] "
                      "非背景=%.1f%%  side_nodes=%s pn=%d err=%s"
                      % (name, w.elapsed, w.running, r1, r2, 100 * frac,
                         side, w.pn, self.err))
                Clock.schedule_once(lambda _dt, i=None: self.next(idx=None), 0.12)

            def next(self, idx=None):
                self._i = getattr(self, "_i", 0) + 1
                self.run_state(self._i)

        Probe().run()
        print("输出: %s" % outdir)


if __name__ == "__main__":
    main()
