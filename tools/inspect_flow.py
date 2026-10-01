"""Deterministic flow screenshots and measurements; never used for FPS scoring."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "main.py")
    parser.add_argument("--label", default="current")
    parser.add_argument("--pixels", default="400,800")
    args = parser.parse_args()
    output = ROOT / "benchmark_logs" / ("flow_visual_" + args.label)
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flow-visual-") as home:
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
        import main  # Register the bundled font before loading a baseline copy.

        spec = importlib.util.spec_from_file_location("flow_visual_source", args.source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])
        cases = [
            (1, (0, 0.1, 0.2, 0.4, 0.7, 0.98, 1)),
            (5, (0.2, 0.6, 1, 1.5, 2.5, 4.98, 5)),
            (15, (0.2, 0.6, 1.3, 2, 7.5, 14.98, 15)),
            (60, (30, 59.98, 60)),
        ]
        targets = [(period, elapsed) for period, times in cases for elapsed in times]
        records = []

        class VisualApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                width, height = map(int, args.pixels.split(","))
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(width / ratio), round(height / ratio))
                Clock.schedule_once(self.begin, 0.3)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                Clock.unschedule(self.hourglass.tick)
                self.real_draw = self.hourglass.redraw
                self.next_frame(0)

            def next_frame(self, _dt):
                if not targets:
                    (output / "measurements.json").write_text(
                        json.dumps(records, indent=2), encoding="utf-8")
                    self.stop()
                    return
                period, target = targets.pop(0)
                widget = self.hourglass
                if widget.duration != period:
                    widget.set_duration(period)
                    widget.completion_enabled = False
                    widget.toggle()
                    random.seed(23)
                    self.first_hit = None
                # Drawing is observational; advancing only physics keeps snapshots inexpensive.
                widget.redraw = lambda: None
                while widget.elapsed + 1e-8 < target and widget.running:
                    step = min(1 / 120, target - widget.elapsed)
                    now[0] += step
                    widget.tick(step)
                    if widget.flares and self.first_hit is None:
                        self.first_hit = widget.elapsed
                widget.redraw = self.real_draw
                widget.redraw()
                self.duration_btn.text = module._fmt_duration(period)
                self.on_run_state_changed()
                records.append({
                    "period": period, "elapsed": widget.elapsed,
                    "mound_px": widget._mound_height_px(),
                    "full_height_px": 2 * widget._R_inner,
                    "contact_gap_px": widget.get_mound_top_y() -
                                      (widget._lower_sand_bot + widget._mound_height_px()),
                    "first_hit_s": self.first_hit,
                    "particles": len(widget.particles), "splashes": len(widget.splashes),
                    "max_trail_px": max(
                        (widget._particle_trail(p) if hasattr(widget, "_particle_trail")
                         else abs(p["vy"]) * 0.08 for p in widget.particles), default=0),
                })
                Clock.schedule_once(
                    lambda _dt, p=period, t=target: self.capture(p, t), 0.05)
                Clock.schedule_once(self.next_frame, 0.12)

            def capture(self, period, elapsed):
                width, height = map(int, Window.size)
                # RGBA rows avoid RGB pack-alignment corruption on fractional-DPI windows.
                pixels = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                image = Image.frombytes("RGBA", (width, height), pixels)
                image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                requested = tuple(map(int, args.pixels.split(",")))
                if image.size != requested:
                    image = image.resize(requested, Image.Resampling.LANCZOS)
                image.save(output / f"period-{period}-time-{elapsed:.2f}.png")
                x_scale, y_scale = requested[0] / width, requested[1] / height
                widget = self.hourglass
                crop = image.crop((
                    round((widget._cx - 52) * x_scale),
                    round((height - widget._neck_y - 62) * y_scale),
                    round((widget._cx + 52) * x_scale),
                    round((height - widget._neck_y + 62) * y_scale)))
                crop.resize((416, 496), Image.Resampling.NEAREST).save(
                    output / f"neck-{period}-time-{elapsed:.2f}.png")

        VisualApp().run()
        print("Visual measurements:", output)


if __name__ == "__main__":
    main()
