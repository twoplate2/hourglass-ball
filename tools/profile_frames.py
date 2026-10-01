"""Measure real Kivy flip intervals without reading or changing user settings."""

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time


def summarize(samples):
    if not samples:
        return {}
    ordered = sorted(samples)
    slowest = ordered[-max(1, math.ceil(len(ordered) * 0.01)):]
    return {
        "frames": len(samples),
        "average_fps": round(1 / statistics.mean(samples), 2),
        "one_percent_low_fps": round(1 / statistics.mean(slowest), 2),
        "p99_frame_ms": round(ordered[math.ceil(len(ordered) * 0.99) - 1] * 1000, 3),
        "max_frame_ms": round(ordered[-1] * 1000, 3),
        "frames_over_25ms": sum(s > 0.025 for s in samples),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).resolve().parents[1] / "main.py")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--phase", type=float, default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--snapshots", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="hourglass-perf-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        from kivy.config import Config
        Config.set("graphics", "width", "380")
        Config.set("graphics", "height", "730")
        Config.set("graphics", "multisamples", "2")
        Config.set("graphics", "maxfps", "60")
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window

        spec = importlib.util.spec_from_file_location("hourglass_profile", args.source)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        Window.clearcolor = (*module.hex_rgb(module.BG_COLOR), 1)
        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        random.seed(23)
        flips, cpu = [], []
        start = previous = None

        class BenchmarkApp(App):
            def build(self):
                self.hourglass = module.HourglassWidget()
                return self.hourglass

            def update_time(self, *_):
                pass

            def on_run_state_changed(self):
                pass

            def on_start(self):
                Clock.schedule_once(self.begin, 1)

            def begin(self, _dt):
                nonlocal start, previous
                widget = self.hourglass
                widget.set_duration(args.duration)
                widget.elapsed = args.duration * args.phase
                widget.toggle()
                original_tick = widget.tick
                Clock.unschedule(widget.tick)

                def timed_tick(dt):
                    before = time.perf_counter()
                    original_tick(dt)
                    cpu.append(time.perf_counter() - before)

                Clock.schedule_interval(timed_tick, 0)
                start = previous = time.perf_counter()
                Window.bind(on_flip=self.record_flip)
                Clock.schedule_once(lambda _dt: self.stop(), args.seconds)
                if args.snapshots:
                    args.snapshots.mkdir(parents=True, exist_ok=True)
                    for delay in (0.05, 0.15, 0.3, 0.8, 1.3, 2.0):
                        Clock.schedule_once(
                            lambda _dt, d=delay: Window.screenshot(
                                name=str(args.snapshots / ("frame-%.2f.png" % d))),
                            delay)

            def record_flip(self, *_):
                nonlocal previous
                now = time.perf_counter()
                flips.append((now - start, now - previous))
                previous = now

        BenchmarkApp().run()
        report = {
            "source": str(args.source),
            "duration": args.duration,
            "phase": args.phase,
            "all": summarize([dt for _, dt in flips]),
            "startup": summarize([dt for elapsed, dt in flips if elapsed <= 2]),
            "steady": summarize([dt for elapsed, dt in flips if elapsed > 2]),
            "cpu_tick": summarize(cpu),
            "cpu_tick_mean_ms": round(statistics.mean(cpu) * 1000, 3) if cpu else 0,
        }
        text = json.dumps(report, indent=2)
        print(text)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
