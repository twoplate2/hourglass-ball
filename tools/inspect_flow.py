"""Deterministic flow screenshots and measurements; never used for FPS scoring."""

import argparse
import importlib.util
import json
import math
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
    parser.add_argument("--chunk-flow", action="store_true")
    parser.add_argument("--gpu-flow", action="store_true")
    parser.add_argument("--texture-flow", action="store_true")
    parser.add_argument("--dense-neck", action="store_true")
    parser.add_argument("--landscape", action="store_true")
    # 密度三臂实验用: 固定周期、密采样稳态、可换沙色、可覆写粒子率
    parser.add_argument("--sand", default=None, help="沙色预设名(金沙/红沙/...)")
    parser.add_argument("--speed-factor", type=float, default=None,
                        help="覆写 speed_factor(仅测量脚手架, 不改几何)")
    parser.add_argument("--steady-period", type=float, default=None)
    parser.add_argument("--steady-frames", type=int, default=30)
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
        from kivy.graphics.transformation import Matrix
        from PIL import Image
        import main  # Register the bundled font before loading a baseline copy.

        spec = importlib.util.spec_from_file_location("flow_visual_source", args.source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if args.speed_factor is not None:
            module.HourglassWidget.speed_factor = property(
                lambda self, _v=args.speed_factor: _v)
        if args.chunk_flow:
            chunk_spec = importlib.util.spec_from_file_location(
                "flow_chunk_experiment", ROOT / "tools" / "flow_chunk_experiment.py")
            chunk_module = importlib.util.module_from_spec(chunk_spec)
            chunk_spec.loader.exec_module(chunk_module)
            chunk_module.install(module.HourglassWidget)
        if args.gpu_flow:
            gpu_spec = importlib.util.spec_from_file_location(
                "flow_gpu_experiment", ROOT / "tools" / "flow_gpu_experiment.py")
            gpu_module = importlib.util.module_from_spec(gpu_spec)
            gpu_spec.loader.exec_module(gpu_module)
            gpu_module.install(module.HourglassWidget)
        if args.texture_flow:
            texture_spec = importlib.util.spec_from_file_location(
                "flow_texture_experiment", ROOT / "tools" / "flow_texture_experiment.py")
            texture_module = importlib.util.module_from_spec(texture_spec)
            texture_spec.loader.exec_module(texture_module)
            texture_module.install(module.HourglassWidget)
        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        # 完成弹窗 auto_dismiss=False: 用例跑过第 1 秒档就会弹出并**永不关闭**,
        # 把后面所有裁图盖住。取证工具必须屏蔽它, 与上面的音效桩同理。
        module.HourglassApp.on_completed = lambda *_: None
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
        if args.dense_neck:
            extra = {
                1: (0.125, 0.15, 0.175, 0.225, 0.25, 0.3, 0.35),
                5: (0.25, 0.275, 0.3, 0.325, 0.35, 0.4, 0.45, 0.5, 0.7, 0.8),
                15: (0.25, 0.275, 0.3, 0.325, 0.35, 0.4, 0.45, 0.5, 0.55, 0.7, 0.8),
            }
            cases = [(period, tuple(sorted(set(times) | set(extra.get(period, ())))))
                     for period, times in cases]
        targets = [(period, elapsed) for period, times in cases for elapsed in times]
        if args.steady_period:
            _p, _n = float(args.steady_period), max(2, args.steady_frames)
            _lo, _hi = 0.5, _p * 0.92          # 切掉前 0.5s 注满与末 8% 收尾
            targets = [(_p, _lo + (_hi - _lo) * i / (_n - 1)) for i in range(_n)]
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
                if args.sand:
                    for _nm, _b, _d, _l in module.SAND_PRESETS:
                        if _nm == args.sand:
                            self.hourglass.set_sand_color(_b, _d, _l)
                self.first_hit = None   # 周期与 load_config 桩相同(60)时下面不会初始化
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
                # 注意: load_config 桩返回 duration=60, 与 --steady-period 60 相同 ⇒
                # 只判 duration 会跳过启动, running 一直 False, 粒子一个都不生成。
                if widget.duration != period or not widget.running:
                    if widget.duration != period:
                        widget.set_duration(period)
                    widget.completion_enabled = False
                    if not widget.running:
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
                # 量这一帧的"索引重建"触发情况 —— 只统计触发条件, 不是实测 GL 流量。
                import sys as _sys
                _flow = _sys.modules.get("flow_texture_experiment")
                if _flow is not None:
                    _flow.stats_reset()
                widget.redraw()
                flow_stats = dict(_flow.STATS) if _flow is not None else {}
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
                    "flow_stats": flow_stats,
                    "neck_grains": widget._neck_grain_count,
                    "neck_outlet_y": 2 * widget._neck_y - widget._taper["y_bot"],
                    "neck_inlet_y": widget._taper["y_bot"],
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
                center_x, center_y = widget._cx, widget._neck_y
                if self.root.angle:
                    origin_x, origin_y = self.root._rot.origin[:2]
                    rotation = Matrix().rotate(math.radians(self.root.angle), 0, 0, 1)
                    dx, dy, _ = rotation.transform_point(
                        center_x - origin_x, center_y - origin_y, 0)
                    center_x, center_y = origin_x + dx, origin_y + dy
                    logical = self.root._to_eq(center_x, center_y)
                    if not all(math.isclose(a, b, abs_tol=1e-4)
                               for a, b in zip(logical, (widget._cx, widget._neck_y))):
                        raise AssertionError("Neck crop rotation disagrees with the application")
                crop = image.crop((
                    round((center_x - 52) * x_scale),
                    round((height - center_y - 62) * y_scale),
                    round((center_x + 52) * x_scale),
                    round((height - center_y + 62) * y_scale)))
                crop.resize((416, 496), Image.Resampling.NEAREST).save(
                    output / f"neck-{period}-time-{elapsed:.2f}.png")

        VisualApp().run()
        print("Visual measurements:", output)


if __name__ == "__main__":
    main()
