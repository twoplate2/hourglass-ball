"""Hidden PC checks of the actual game widget, not preview 06 or FPS scoring."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
VENDOR = ROOT / "_temp/shazhu/vendor"
if VENDOR.is_dir():
    sys.path.insert(0, str(VENDOR))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity():
    paths = [ROOT / "main.py", ROOT / "buildozer.spec", Path(__file__)]
    paths.extend((ROOT / "src/app").glob("*.py"))
    paths.extend(path for path in (ROOT / "src/build").rglob("*")
                 if path.is_file() and "__pycache__" not in path.parts)
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "_temp/pymunk-3.1")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = source_identity()
    with tempfile.TemporaryDirectory(prefix="pymunk-game-") as home:
        os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1",
                          KIVY_METRICS_DENSITY="1", KIVY_METRICS_FONTSCALE="1",
                          HG_SAND_ENGINE="pymunk")
        from kivy.config import Config
        Config.set("graphics", "window_state", "hidden")
        Config.set("graphics", "width", "400")
        Config.set("graphics", "height", "800")
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics import ClearBuffers, ClearColor, Fbo
        from PIL import Image
        import main as game
        from app.app_version import APP_VERSION
        random.seed(31001)

        game.HourglassWidget._make_sound_proxy = lambda *_: None
        game.HourglassWidget._make_completion_sound = lambda *_: None
        game.HourglassWidget._load_voice_bank = lambda *_: None
        Clock.unschedule(game.HourglassWidget.tick)
        checks, frames = [], []
        finished = False
        widget = game.HourglassWidget(size=(400, 800), pos=(0, 0))
        Clock.unschedule(widget.tick)
        widget._rebuild_height_table()

        def check(name, condition, detail=None):
            checks.append({"id": name, "passed": bool(condition), "detail": detail})
            if not condition:
                raise AssertionError(f"{name}: {detail}")

        def snapshot(label):
            widget.redraw()
            fbo = Fbo(size=widget.size, with_stencilbuffer=True)
            with fbo:
                ClearColor(*game.hex_rgb(game.BG_COLOR), 1)
                ClearBuffers()
            fbo.add(widget.canvas)
            fbo.draw()
            image = Image.frombytes("RGBA", tuple(map(int, widget.size)), bytes(fbo.pixels))
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
            target = output / (label + ".png")
            image.save(target)
            fbo.remove(widget.canvas)
            frames.append({"path": target.relative_to(ROOT).as_posix(),
                           "sha256": digest(target), "duration": widget.duration,
                           "elapsed": widget.elapsed, "size": list(widget.size),
                           "state": widget.sand_transfer_state(),
                           "physics": widget._pymunk_flow.metrics() if widget._pymunk_flow else None})
            print("CAPTURE %s t=%.2f physics=%s" % (
                label, widget.elapsed, frames[-1]["physics"]), flush=True)

        def begin(duration, size=(400, 800)):
            widget.reset()
            widget.size = size
            widget.duration = duration
            widget._rebuild_height_table()
            widget.running = True
            widget._sand_active = True

        def advance(target, capture=None):
            while widget.elapsed < target - 1e-9:
                dt = min(1 / 30, target - widget.elapsed)
                widget.elapsed += dt
                widget._sync_mound_frame()
                widget.update_particles(dt, flow_dt=dt)
                state = widget.sand_transfer_state()
                check_sum = sum(state.values())
                if abs(check_sum - 1) > 1e-7 or not all(v >= -1e-10 for v in state.values()):
                    raise AssertionError(("ledger", widget.elapsed, state))
                if widget._pymunk_flow:
                    solver = widget._pymunk_flow
                    grain_mass = 0.0
                    for layer in widget._pymunk_flow.layers:
                        for grain in layer.grains:
                            grain_mass += grain.mass
                            position = grain.body.position
                            if not all(math.isfinite(v) for v in (*position, *grain.body.velocity)):
                                raise AssertionError("Nonfinite grain")
                            y = solver.origin + position.y * solver.scale
                            upper = widget._taper["in_pts"][0][1]
                            lower = 2 * widget._neck_y - upper
                            if lower <= y <= upper:
                                radius = grain.radius * solver.scale
                                # Compare the grain envelope to the curved neck;
                                # a horizontal radius alone is wrong on its slope.
                                limit = max(widget._neck_width_at(max(yy, 2 * widget._neck_y - yy))
                                            for yy in (y - radius, y, y + radius))
                                outside = abs(position.x) * solver.scale > limit + radius
                            else:
                                center = widget._upper_y_c if y > upper else widget._lower_y_c
                                limit = widget._R_inner + grain.radius * solver.scale
                                # Near a sphere pole, x-limit error magnifies a
                                # tiny contact penetration; radial distance does not.
                                outside = math.hypot(position.x * solver.scale, y - center) > limit
                            if outside:
                                raise AssertionError(("escaped_vessel", widget.elapsed, tuple(position), limit))
                    if abs(grain_mass - state["flight"]) > 1e-8:
                        raise AssertionError(("grain_ledger", grain_mass, state))
            if capture:
                snapshot(capture)

        try:
            check("version", game.APP_VERSION == APP_VERSION)
            begin(50)
            snapshot("initial")
            for t, label in ((0.15, "filling"), (0.45, "opening"), (1.6, "first-landing"),
                             (3, "early"), (6, "stream")):
                advance(t, label)
            solver = widget._pymunk_flow
            check("real_contacts", solver.metrics()["contacts"] > 0, solver.metrics())
            check("stream_contacts", solver.metrics()["stream_contacts"] > 0, solver.metrics())
            check("real_landings", solver.metrics()["landings"] > 0, solver.metrics())
            check("landing_surface_motion_connected", widget._mound_flow_strength() > 0)
            check("no_side_columns", True, "All integrated frames checked against the actual vessel.")
            check("grain_ledger_matches_clock", True)
            check("two_layers", len(solver.layers) == 2)
            check("old_stream_not_drawn",
                  all(not line.points for _, _, lines in widget._stream_pools.values() for line in lines))
            check("old_column_not_drawn", not any(widget._neck_quads._v[0::4])
                  and not any(widget._neck_quads._v[1::4]))
            frozen = [(tuple(g.body.position), tuple(g.body.velocity))
                      for layer in solver.layers for g in layer.grains]
            widget.running = False
            paused_clock = widget.elapsed
            with patch.object(game, "time", SimpleNamespace(perf_counter=lambda: widget.last_frame + 1)):
                widget.tick(1)
            check("pause_freezes",
                  frozen == [(tuple(g.body.position), tuple(g.body.velocity))
                             for layer in solver.layers for g in layer.grains]
                  and widget.elapsed == paused_clock)
            widget.running = True
            advance(6.1)
            check("resume_same_world", widget._pymunk_flow is solver)
            snapshot("resumed")
            if not args.probe:
                from app.frame_benchmark import BenchmarkRunner
                saved = widget.sand_transfer_state()
                runner = BenchmarkRunner(widget, lambda *_: None, lambda *_: None)
                # Exercise state handoff/cancellation, without collecting FPS.
                runner._install_probes = runner._remove_probes = lambda: None
                runner.start()
                runner._event.cancel()
                begin(1)
                advance(0.2)
                runner.cancel()
                check("benchmark_restores_world", widget._pymunk_flow is solver)
                check("benchmark_restores_ledger", widget.sand_transfer_state() == saved)
                advance(25, "middle")
                advance(45, "late")
                advance(49.99, "tail")
                advance(50, "timer-end")
                check("tail_draws_inflight", widget.pn == 0 or any(
                    mesh.vertices for _, mesh in widget._pymunk_canvas.batches))
                widget.running = False
                for _ in range(240):
                    widget.update_particles(1 / 30)
                check("drained_ledger", abs(widget._sand_landed - 1) < 1e-7,
                      widget.sand_transfer_state())
                check("drained_particles", widget.pn == 0, widget.pn)
                check("finished_releases_contact_world", widget._pymunk_flow.finished
                      and widget._pymunk_flow.metrics()["live"] == 0)
                snapshot("drained")
                for duration in (1, 5, 36000):
                    begin(duration)
                    advance(min(duration, 3), f"duration-{duration}")
                    if duration <= 5:
                        advance(duration)
                        widget.running = False
                        for _ in range(240):
                            widget.update_particles(1 / 30)
                        check(f"duration-{duration}-drains",
                              abs(widget._sand_landed - 1) < 1e-7 and widget.pn == 0,
                              widget.sand_transfer_state())
                begin(50, (800, 400))
                advance(2, "landscape")
                before = widget.sand_transfer_state()
                original = widget._pymunk_flow
                widget.size = (500, 1000)
                widget._rebuild_height_table()
                check("resize_keeps_mass", before == widget.sand_transfer_state())
                check("resize_keeps_world", widget._pymunk_flow is original)
                advance(3, "resized")
                for preset in game.SAND_PRESETS:
                    widget.set_sand_color(*preset[1:])
                    snapshot("color-" + str(game.SAND_PRESETS.index(preset)))
                radius = widget._pymunk_flow.radius
                before = widget.sand_transfer_state()
                widget.set_grain_level("1")
                fine = widget._pymunk_flow.radius
                snapshot("grain-fine")
                widget.set_grain_level("6")
                coarse = widget._pymunk_flow.radius
                snapshot("grain-coarse")
                check("grain_control_connected", fine < radius < coarse, [fine, radius, coarse])
                check("grain_control_keeps_ledger", widget.sand_transfer_state() == before)
                widget.set_grain_level("3")
                begin(50)
                advance(0.5)
                clock = widget._pymunk_flow.clock
                widget.elapsed += 2
                widget.update_particles(2, flow_dt=2)
                check("long_frame_bounded_physics",
                      widget._pymunk_flow.clock - clock <= widget._particle_motion_scale / 15 + 1e-8)
                check("long_frame_keeps_ledger",
                      abs(sum(widget.sand_transfer_state().values()) - 1) < 1e-7)
                widget.reset()
                check("reset_clears_world", widget._pymunk_flow is None and widget.pn == 0
                      and widget._sand_landed == 0 and widget._sand_released == 0)
                snapshot("reset")
            check("window_hidden", Config.get("graphics", "window_state") == "hidden")
            if sys.platform == "win32":
                import ctypes
                hwnd = Window.get_window_info().window
                visible = ctypes.windll.user32.IsWindowVisible
                visible.argtypes = [ctypes.c_void_p]
                visible.restype = ctypes.c_int
                check("native_window_hidden", not visible(hwnd))
            check("source_unchanged_during_run", source_identity() == sources)
            finished = True
        finally:
            report = {"scope": "pymunk-integration-pc", "full": not args.probe,
                      "finished": finished,
                      "version": game.APP_VERSION, "checks": checks, "frames": frames,
                      "sources": sources, "performance_tested": False,
                      "android_tested": False, "appearance_reviewed": False}
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            widget.reset()
            Window.close()
        print(f"PYMUNK_INTEGRATION_OK checks={len(checks)} captures={len(frames)}")


if __name__ == "__main__":
    main()
