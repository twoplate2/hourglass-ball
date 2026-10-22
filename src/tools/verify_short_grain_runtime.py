"""Hidden Windows-only material evidence, with no device access or application edits.

python pc_hidden_capture.py --runtime SOURCE_TREE --output NEW_DIRECTORY
Add --cases acceptance-cases.json to reuse the existing case matrix.
--renderer texture enables the production texture/batch paths through HG_* flags;
--renderer native leaves those flags unset and defaults to three PC fallback cases.
The report is compatible with material_visual_probe's offline frame comparator.
"""

import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import traceback


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "src/tools/material_visual_probe.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def default_cases(renderer):
    phases = [("initial", 0), ("early", 5), ("middle", 25), ("late", 45),
              ("tail", 49), ("after-zero", 51), ("settled", 56)]
    if renderer == "native":
        phases = [("initial", 0), ("middle", 25), ("late", 45)]
    cases = [{"name": name, "period": 50, "elapsed": elapsed, "frame_offsets": [0],
              "state": "initial" if elapsed == 0 else "running"} for name, elapsed in phases]
    if renderer == "native":
        return cases
    cases += [
        {"name": "paused", "period": 50, "elapsed": 25, "state": "paused",
         "pause_seconds": 3, "frame_offsets": [0, 1]},
        {"name": "resume", "period": 50, "elapsed": 25, "pause_seconds": 3,
         "frame_offsets": [0, .2, 2]},
    ]
    cases += [{"name": "color%d-grain%d" % (color, grain), "period": 50,
               "elapsed": 25, "color": color, "grain": str(grain), "frame_offsets": [0]}
              for color in range(6) for grain in (1, 6)]
    cases += [{"name": "short-%d" % period, "period": period, "elapsed": period / 2,
               "frame_offsets": [0, 1 / 30, 2 / 30]} for period in (1, 5)]
    return cases


def native_hidden_check(window, config):
    if sys.platform != "win32":
        raise RuntimeError("This capture requires Windows native hidden-window validation")
    hwnd = window.get_window_info().window
    user32 = ctypes.windll.user32
    values = {}
    for name in ("IsWindow", "IsWindowVisible", "IsIconic"):
        function = getattr(user32, name)
        function.argtypes, function.restype = [ctypes.c_void_p], ctypes.c_int
        values[name] = bool(function(hwnd))
    values["window_state_config"] = config.get("graphics", "window_state")
    if (not values["IsWindow"] or values["IsWindowVisible"] or values["IsIconic"]
            or values["window_state_config"] != "hidden"):
        window.close()
        raise RuntimeError("Window is not truly hidden: " + json.dumps(values))
    return values


def source_manifest(runtime, helper):
    paths = [runtime / "main.py"] + sorted((runtime / "src/app").glob("*.py"))
    return {path.relative_to(runtime).as_posix(): helper.file_hash(path) for path in paths}


def loaded_manifest(runtime, helper):
    wanted = {path.stem for path in (runtime / "src/app").glob("*.py")}
    result = {}
    for name, module in sorted(sys.modules.items()):
        if not (name == "pc_material_runtime" or name == "app"
                or name.startswith("app.") or name in wanted):
            continue
        location = getattr(module, "__file__", None)
        if not location:
            continue
        path = Path(location).resolve()
        if not path.is_relative_to(runtime):
            raise RuntimeError("Runtime module escaped selected source: %s=%s" % (name, path))
        result[name] = {"path": path.relative_to(runtime).as_posix(), "sha256": helper.file_hash(path)}
    return result


def capture(args, helper, report, write_report):
    runtime, output = args.runtime, args.output
    if "kivy.core.window" in sys.modules:
        raise RuntimeError("Kivy Window was imported before hidden configuration")
    with tempfile.TemporaryDirectory(prefix="pc-material-config-", dir=output) as isolated:
        config_dir = Path(isolated)
        report["isolated_config_dir"] = str(config_dir)
        report["cleared_inherited_hg_keys"] = sorted(key for key in os.environ if key.startswith("HG_"))
        for key in tuple(os.environ):
            if key.startswith("HG_"):
                del os.environ[key]
        os.environ.update(KIVY_HOME=str(config_dir / "kivy"), KIVY_NO_ARGS="1",
                          KIVY_NO_CONFIG="1", KIVY_NO_FILELOG="1", KIVY_WINDOW="sdl2",
                          KIVY_METRICS_DENSITY="1", KIVY_METRICS_FONTSCALE="1")
        if args.renderer in ("texture", "legacy", "fallback"):
            os.environ.update(HG_FLOW_RENDERER="grain", HG_SPLASH_RENDERER="batch",
                              HG_NECK_RENDERER="batch", HG_SAND_MATERIAL="grain")
        if args.renderer == "legacy":
            os.environ["HG_FLOW_RENDERER"] = "texture"
        if args.renderer == "fallback":
            os.environ["HG_FLOW_FORCE_FAIL"] = "1"
        report["env"] = {key: value for key, value in os.environ.items() if key.startswith("HG_")}
        sys.dont_write_bytecode = True
        from kivy.config import Config
        Config.set("graphics", "window_state", "hidden")
        Config.set("graphics", "width", str(args.width))
        Config.set("graphics", "height", str(args.height))
        Config.set("graphics", "resizable", "0")
        # SDL receives HIDDEN at creation, before any runtime/application import.
        from kivy.core.window import Window
        try:
            report["hidden_at_creation"] = native_hidden_check(Window, Config)
            from kivy.clock import Clock
            from kivy.graphics import ClearBuffers, ClearColor, Fbo
            from kivy.graphics.transformation import Matrix
            from kivy.graphics.opengl import glGetString, GL_VENDOR, GL_RENDERER, GL_VERSION
            from PIL import Image
            report["opengl"] = {}
            for label, constant in (("vendor", GL_VENDOR), ("renderer", GL_RENDERER), ("version", GL_VERSION)):
                value = glGetString(constant)
                report["opengl"][label] = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
            names = {path.stem for path in (runtime / "src/app").glob("*.py")}
            for name in tuple(sys.modules):
                if name == "app" or name.startswith("app.") or name in names:
                    del sys.modules[name]
            sys.path[:0] = [str(runtime / "src/app"), str(runtime / "src"), str(runtime)]
            game = load_module("pc_material_runtime", runtime / "main.py")
            if game.platform != "win":
                raise RuntimeError("Runtime platform must remain native Windows, not a simulated Android value")
            now = [helper.EPOCH]

            class FakeTime:
                @staticmethod
                def perf_counter():
                    return now[0]

                def __getattr__(self, name):
                    return getattr(time, name)

            game.time = FakeTime()
            game.config_path = lambda: str(config_dir / ".hourglass_config.json")
            config = {"duration": 50, "color_name": game.SAND_PRESETS[0][0],
                      "sound_name": game.SILENT_NAME, "sand_mode": "grain", "sand_grain": .35,
                      "grain_level": "3", "rough_level": "3", "grain_rev": game.SAND_GRAIN_REV}
            config_text = json.dumps(config, ensure_ascii=False, indent=2)
            Path(game.config_path()).write_text(config_text, encoding="utf-8")
            (output / "config-used.json").write_text(config_text, encoding="utf-8")
            for method in ("_make_sound_proxy", "_make_completion_sound", "_load_voice_bank",
                           "_play_sound", "_play_completion_sound", "_voice_say",
                           "_schedule_completion_popup"):
                setattr(game.HourglassWidget, method, helper.named_noop(method))
            random.seed(helper.SEED)
            widget = game.HourglassWidget(size=(args.width, args.height), pos=(0, 0))
            Clock.unschedule(widget.tick)
            Clock.unschedule(widget._load_voice_bank)
            Clock.unschedule(widget._on_size)
            pending = getattr(widget, "_geom_pending", None)
            if pending is not None:
                pending.cancel()
                widget._geom_pending = None
            widget.sound_name = game.SILENT_NAME
            widget.completion_enabled = False
            widget._rebuild_height_table()
            if not widget._geom_ready:
                raise RuntimeError("Real widget geometry was not created")
            report["module_sources"] = loaded_manifest(runtime, helper)
            report["version"] = game.APP_VERSION
            report["runtime_platform"] = game.platform
            write_report()

            logical_time = 0.0

            def advance_to(target):
                nonlocal logical_time
                if target < logical_time - 1e-10:
                    raise ValueError("Logical clock cannot move backwards")
                draw = widget.redraw
                widget.redraw = lambda: None
                try:
                    while logical_time + 1e-10 < target:
                        previous = logical_time
                        logical_time = min(target, previous + helper.STEP)
                        now[0] = helper.EPOCH + logical_time
                        # Continue the real tick after running=False to observe actual tail drainage.
                        widget.tick(logical_time - previous)
                finally:
                    widget.redraw = draw

            def frame_image(path):
                fbo = Fbo(size=(args.width, args.height), with_stencilbuffer=True)
                fbo["projection_mat"] = Matrix().view_clip(0, args.width, 0, args.height, -1, 1, 0)
                fbo["modelview_mat"] = Matrix()
                with fbo:
                    ClearColor(*game.hex_rgb(game.BG_COLOR), 1)
                    ClearBuffers()
                fbo.add(widget.canvas)
                try:
                    fbo.draw()
                    data = bytes(fbo.pixels)
                finally:
                    fbo.remove(widget.canvas)
                if len(data) != args.width * args.height * 4:
                    raise RuntimeError("FBO pixel buffer has the wrong size")
                if all(len(set(data[channel::4])) <= 1 for channel in (0, 1, 2)):
                    raise RuntimeError("FBO produced a uniform/blank frame")
                image = Image.frombytes("RGBA", (args.width, args.height), data)
                image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                image.save(path)

            for index, case in enumerate(report["normalized_cases"]):
                case_report = report["cases"][index]
                case_report["status"] = "running"
                write_report()
                widget.reset()
                logical_time, now[0] = 0.0, helper.EPOCH
                random.seed(helper.SEED)
                if game._np is not None:
                    game._np.random.seed(helper.SEED)
                color_names = [row[0] for row in game.SAND_PRESETS]
                color = color_names.index(case["color"]) if isinstance(case["color"], str) else case["color"]
                if isinstance(color, bool) or not isinstance(color, int) or not 0 <= color < len(color_names):
                    raise ValueError("Unknown case color")
                if case["grain"] not in [str(row[0]) for row in game.SAND_GRAIN_LEVELS]:
                    raise ValueError("Unknown grain level")
                if case["rough"] not in [str(row[0]) for row in game.SURFACE_ROUGH_LEVELS]:
                    raise ValueError("Unknown rough level")
                if case["period"] > game.MAX_DURATION or case["sand_grain"] > game.SAND_GRAIN_MAX:
                    raise ValueError("Case parameters exceed application limits")
                name, base, dark, light = game.SAND_PRESETS[color]
                widget.set_sand_color(base, dark, light)
                widget.set_grain_level(case["grain"])
                widget.set_rough_level(case["rough"])
                widget.set_duration(case["period"])
                widget.reset()
                widget._rebuild_height_table()
                if widget.set_sand_grain(case["sand_grain"]) is False:
                    raise RuntimeError("Requested material failed to initialize")
                widget._front_seed = .75
                widget.last_frame, widget.last_tick = helper.EPOCH, None
                widget.completion_enabled = False
                effective = {"color": color, "color_name": name, "period": widget.duration,
                             "grain": game.current_grain_level(), "rough": game.current_rough_level(),
                             "sand_grain": game.SAND_MATERIAL_GRAIN}
                case_report["effective_config"] = effective
                if (str(effective["grain"]) != case["grain"] or str(effective["rough"]) != case["rough"]
                        or effective["period"] != case["period"] or effective["sand_grain"] != case["sand_grain"]):
                    raise RuntimeError("Effective settings do not match the request")
                for _ in range(2000):
                    if not widget._warm_queue:
                        break
                    widget._warm_batches_step()
                if widget._warm_queue:
                    raise RuntimeError("Warm queue did not complete")
                if args.renderer == "texture" and getattr(widget, "flow_renderer", None) != "short_grain_field":
                    raise RuntimeError("Requested texture path fell back to another renderer")
                if args.renderer == "native" and getattr(widget, "flow_renderer", "line_pool") != "short_grain_field":
                    raise RuntimeError("Native PC default short-grain renderer did not activate")
                if args.renderer == "fallback" and widget.flow_renderer != "line_pool":
                    raise RuntimeError("Forced fallback did not activate")
                if args.renderer == "legacy" and widget.flow_renderer != "mesh_endpoint_texture":
                    raise RuntimeError("Legacy baseline did not activate")
                if not widget._sand_flow_contexts:
                    raise RuntimeError("Default upper/neck GPU material did not initialize")
                random.seed(helper.SEED)
                if game._np is not None:
                    game._np.random.seed(helper.SEED)
                if case["state"] != "initial":
                    widget.toggle()
                advance_to(case["elapsed"])
                case_report["pause_check"] = None
                if case["pause_seconds"] or case["state"] == "paused":
                    if not widget.running:
                        raise ValueError("Pause requires an unfinished timer")
                    widget.toggle()
                    before_pause = helper.physical_state(widget, game)
                    advance_to(logical_time + case["pause_seconds"])
                    after_pause = helper.physical_state(widget, game)
                    case_report["pause_check"] = {"seconds": case["pause_seconds"],
                        "before_sha256": before_pause["sha256"], "after_sha256": after_pause["sha256"],
                        "frozen": before_pause["sha256"] == after_pause["sha256"]}
                    if case["state"] != "paused":
                        widget.toggle()
                origin = logical_time
                for frame_index, offset in enumerate(case["frame_offsets"]):
                    advance_to(origin + offset)
                    hidden = native_hidden_check(Window, Config)
                    before_draw = helper.physical_state(widget, game)
                    widget.redraw()
                    name = "%s-f%03d" % (case["name"], frame_index)
                    path = output / (name + ".png")
                    frame_image(path)
                    physical = helper.physical_state(widget, game)
                    geometry = helper.geometry_state(widget, game)
                    shaders = helper.shader_state(widget)
                    after_geometry = helper.physical_state(widget, game)
                    same_draw = before_draw["sha256"] == physical["sha256"]
                    same_geometry = after_geometry["sha256"] == physical["sha256"]
                    frame = {"name": name, "case": case["name"], "frame_index": frame_index,
                        "period": case["period"], "color": color, "grain": case["grain"], "rough": case["rough"],
                        "elapsed_requested": case["elapsed"], "elapsed_actual": widget.elapsed,
                        "logical_elapsed": logical_time, "fake_clock": now[0], "frame_offset": offset,
                        "dimensions": [args.width, args.height], "running": bool(widget.running),
                        "particles": widget.pn, "splashes": widget._sn, "transfer": physical["ledger"],
                        "physics": physical, "geometry": geometry, "shaders": shaders,
                        "renderer": shaders["stream_renderer"], "radius": widget._R_inner,
                        "half_width": widget._taper["t_in"],
                        "outlet_y": 2 * widget._neck_y - widget._taper["y_bot"],
                        "before_draw_sha256": before_draw["sha256"], "draw_preserved_physics": same_draw,
                        "geometry_probe_preserved_physics": same_geometry, "native_hidden": hidden,
                        "file": path.name, "sha256": helper.file_hash(path), "completed": True,
                        "passed": (same_draw and same_geometry and shaders["all_present_shaders_compiled"]
                                   and shaders["mound_canvas_consistent"])}
                    frame["effect_rects"] = [[float(widget.sx[i] - widget.shw[i]),
                        float(widget.sy[i] - widget.shh[i]), float(widget.sx[i] + widget.shw[i]),
                        float(widget.sy[i] + widget.shh[i])] for i in range(widget._sn)]
                    if args.renderer in ("texture", "native"):
                        frame["stream_draw"] = dict(getattr(widget, "_short_flow_state", {}))
                        frame["stream_draw_preserved_physics"] = helper.physical_state(widget, game)["sha256"] == physical["sha256"]
                        frame["passed"] = frame["passed"] and frame["stream_draw_preserved_physics"]
                    report["frames"].append(frame)
                    case_report["frames"].append(name)
                    write_report()
                    print("PC_HIDDEN_FRAME %s elapsed=%.9f pn=%d sn=%d" %
                          (name, widget.elapsed, widget.pn, widget._sn), flush=True)
                frame_rows = [row for row in report["frames"] if row["case"] == case["name"]]
                paused = case_report["pause_check"]
                case_report.update(status="completed", completed=True,
                    passed=all(row["passed"] for row in frame_rows) and (paused is None or paused["frozen"]))
                write_report()
            report["module_sources"] = loaded_manifest(runtime, helper)
            if args.video:
                from record_short_grain import record
                widget.set_duration(50)
                widget.reset()
                widget._rebuild_height_table()
                logical_time, now[0] = 0.0, helper.EPOCH
                widget.last_frame, widget.last_tick = helper.EPOCH, None
                widget.toggle()
                report["video"] = record(widget, output, advance_to, frame_image,
                    lambda: native_hidden_check(Window, Config))
            report["hidden_at_finish"] = native_hidden_check(Window, Config)
            widget.reset()
            report["source_files_end"] = source_manifest(runtime, helper)
            report["source_unchanged"] = report["source_files_end"] == report["source_files"]
            report.update(completed=True, passed=(report["source_unchanged"]
                          and all(row["passed"] for row in report["cases"])
                          and len(report["frames"]) == report["expected_frames"]))
        finally:
            Window.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--renderer", choices=("native", "texture", "legacy", "fallback"), default="texture")
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--width", type=int, default=400)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--video", action="store_true")
    args = parser.parse_args()
    args.runtime, args.output = args.runtime.resolve(), args.output.resolve()
    if sys.platform != "win32":
        parser.error("Windows-only; this tool never connects to devices")
    if args.width <= 0 or args.height <= 0:
        parser.error("Capture size must be positive")
    if not (args.runtime / "main.py").is_file() or not (args.runtime / "src/app/__init__.py").is_file():
        parser.error("runtime must be a complete source directory")
    args.output.mkdir(parents=True, exist_ok=False)
    helper = load_module("pc_material_probe_helpers", HELPER)
    report = {"mode": "matrix", "scope": "Hidden PC fixed-step material verification only",
              "renderer_profile": args.renderer, "runtime_dir": str(args.runtime),
              "completed": False, "passed": False, "source_unchanged": False,
              "seed": helper.SEED, "front_seed": .75, "clock_epoch": helper.EPOCH,
              "timing": "Logical 1/120 steps including post-completion ticks; NOT real-time",
              "dimensions": [args.width, args.height], "started_at": time.time(),
              "source_files": source_manifest(args.runtime, helper),
              "source_sha256": helper.file_hash(args.runtime / "main.py"),
              "probe_sha256": helper.file_hash(Path(__file__)), "helper_sha256": helper.file_hash(HELPER),
              "android_tested": False, "audio_tested": False, "performance_tested": False,
              "appearance_reviewed": False, "frames": [], "cases": []}

    def write_report():
        temporary = args.output / "result.json.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(args.output / "result.json")

    try:
        raw = json.loads(args.cases.read_text(encoding="utf-8")) if args.cases else default_cases(args.renderer)
        request = {"cases": raw} if isinstance(raw, list) else raw
        cases = helper.normalize_cases(request)
        report["normalized_cases"] = cases
        report["expected_cases"] = len(cases)
        report["expected_frames"] = sum(len(case["frame_offsets"]) for case in cases)
        report["cases"] = [{"name": case["name"], "request": case, "status": "pending",
                            "completed": False, "passed": False, "frames": []} for case in cases]
        write_report()
        capture(args, helper, report, write_report)
    except BaseException:
        report.update(completed=False, passed=False, exception=traceback.format_exc())
        for case in report["cases"]:
            if case["status"] == "running":
                case.update(status="failed", exception=report["exception"])
        raise
    finally:
        report["finished_at"] = time.time()
        write_report()
    print("PC_HIDDEN_CAPTURE_COMPLETE " + str(args.output / "result.json"), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
