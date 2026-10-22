"""Device-only deterministic material capture; install as the temporary app main.py.

Read ``<entry dir>/_material_qa/request.json`` and load runtime_dir/main.py plus
runtime_dir/src/app.  No PC app or device connection is started by this file.
Each case starts independently at fake clock 1000, seed 297 and front seed .75.
``elapsed`` is logical wall time from starting, so it may exceed the timer period.
Frames default to offsets 0, 1/120 and 2/120 after that state.  ``pause_seconds``
freezes there for the requested logical time, checks the freeze, then resumes
unless state is "paused".  State "initial" requires elapsed == 0.

Example request (assets must also be available inside runtime_dir):
  {"id":"gold-middle","mode":"matrix","runtime_dir":"/absolute/runtime",
   "cases":[{"name":"p50-t25","period":50,"color":0,"grain":"3",
             "rough":"3","elapsed":25,"frame_offsets":[0,0.008333333333333333]}]}

``passed`` means capture/state-integrity assertions passed, not visual acceptance,
real-time behavior, audio, performance, or proof that an optional GPU feature ran.
Optional shader absence is explicitly reported as a fallback, including baseline.
"""

from collections import deque
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import re
import struct
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parent
STEP = 1.0 / 120.0
EPOCH = 1000.0
SEED = 297
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def json_value(value):
    """A stable numerical representation; never fall back to address-bearing repr."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite value in captured state")
        return value
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, deque)):
        return [json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return json_value(value.tolist())
    if hasattr(value, "item"):
        return json_value(value.item())
    raise TypeError("Unsupported captured state type: " + type(value).__name__)


def digest_json(value):
    raw = json.dumps(json_value(value), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def named_noop(method_name):
    """Kivy WeakMethod resolves scheduled bound methods by their original name."""
    def disabled_method(_self, *_args, **_kwargs):
        return None
    disabled_method.__name__ = method_name
    disabled_method.__qualname__ = "HourglassWidget." + method_name
    return disabled_method


def number(value, label, minimum=0.0):
    if isinstance(value, bool):
        raise ValueError(label + " must be numerical")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(label + " is out of range")
    return result


def normalize_cases(request):
    cases = request.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("A non-empty cases list is required")
    names, normalized = set(), []
    defaults = request.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("defaults must be an object")
    for raw in cases:
        if not isinstance(raw, dict):
            raise ValueError("Every case must be an object")
        case = dict(defaults, **raw)
        name = case.get("name")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name in names:
            raise ValueError("Case names must be unique safe filename components")
        names.add(name)
        case["period"] = number(case["period"], "period", 1e-9)
        case["elapsed"] = number(case.get("elapsed", 0), "elapsed")
        case["grain"] = str(case.get("grain", case.get("grain_level", "3")))
        case["rough"] = str(case.get("rough", case.get("rough_level", "3")))
        case["sand_grain"] = number(case.get("sand_grain", 0.35), "sand_grain")
        case["color"] = case.get("color", 0)
        case["state"] = case.get("state", "running")
        if case["state"] not in ("running", "initial", "paused"):
            raise ValueError("state must be running, initial or paused")
        case["pause_seconds"] = number(case.get("pause_seconds", 0), "pause_seconds")
        if case["state"] == "initial" and (case["elapsed"] or case["pause_seconds"]):
            raise ValueError("initial requires elapsed=0 and no pause_seconds")
        offsets = case.get("frame_offsets", request.get("frame_offsets", [0, STEP, 2 * STEP]))
        if not isinstance(offsets, list) or not offsets:
            raise ValueError("frame_offsets must be non-empty")
        offsets = [number(value, "frame_offset") for value in offsets]
        if offsets[0] != 0 or any(b <= a for a, b in zip(offsets, offsets[1:])):
            raise ValueError("frame_offsets must start at zero and strictly increase")
        case["frame_offsets"] = offsets
        normalized.append(case)
    return normalized


def array_state(widget, fields, count):
    result = {}
    for field in fields:
        values = getattr(widget, field)
        if len(values) < count:
            raise ValueError("Active array is shorter than its count: " + field)
        data = bytearray()
        for item in values[:count]:
            value = float(item)
            if not math.isfinite(value):
                raise ValueError("Non-finite active array element: " + field)
            data.extend(struct.pack("<d", value))
        result[field] = {"count": count, "encoding": "active-prefix-float64-le",
                         "sha256": hashlib.sha256(data).hexdigest()}
    return result


def physical_state(widget, module):
    """Hash all declared live particle arrays; dead capacity is intentionally excluded."""
    pfields = tuple(module._P_FIELDS)
    sfields = tuple(dict.fromkeys(tuple(module._S_FIELDS)
                                 + tuple(getattr(module, "_S_MOTION_FIELDS", ()))))
    scalars = ("duration", "elapsed", "running", "particle_acc", "_sand_released",
               "_sand_landed", "_sand_pending", "_sand_active", "_sand_timing",
               "_sand_resized", "_completion_triggered", "_done_at", "_bg_splash_acc",
               "_mound_flow_clock", "_first_impact_clock", "_last_impact_clock",
               "mound_peak_offset", "_splash_reference_speed", "_splash_density",
               "_splash_cap", "_splash_origin_blend", "_splash_speed_scale")
    state = {"pn": int(widget.pn), "_sn": int(widget._sn),
             "ledger": json_value(widget.sand_transfer_state()),
             "arrays": {"main": array_state(widget, pfields, widget.pn),
                        "splash": array_state(widget, sfields, widget._sn)},
             "scalars": {key: json_value(getattr(widget, key)) for key in scalars
                         if hasattr(widget, key)},
             "effects": {key: json_value(getattr(widget, key)) for key in
                         ("flares", "dusts", "_contact_hits", "_splash_stats")
                         if hasattr(widget, key)},
             "random_sha256": digest_json(random.getstate())}
    effect_rng = getattr(widget, "_effect_random", None)
    state["effect_random_sha256"] = (digest_json(effect_rng.getstate())
                                     if effect_rng is not None else None)
    state["sha256"] = digest_json(state)
    return state


def geometry_state(widget, module=None):
    names = ("_cx", "_R_inner", "_ow", "_neck_y", "neck_w", "_upper_y_c",
             "_lower_y_c", "_upper_sand_bot", "_upper_sand_top", "_lower_sand_bot",
             "_lower_sand_top", "_lower_ball_cut", "_taper")
    result = {key: json_value(getattr(widget, key)) for key in names
              if hasattr(widget, key)}
    result["widget_pos"] = list(widget.pos)
    result["widget_size"] = list(widget.size)
    result["contact_diameter"] = (float(widget._contact_flow_diameter())
                                  if hasattr(widget, "_contact_flow_diameter") else None)
    result["contact_flow_diameter"] = result["contact_diameter"]
    result["glass_fill_rgb"] = (json_value(module.hex_rgb(module.GLASS_FILL))
                                if module is not None else None)
    result["glass_fill_rgb_scale"] = "0..1"
    result["flow_shrink_min"] = (float(module.FLOW_SHRINK_MIN)
                                 if module is not None and hasattr(module, "FLOW_SHRINK_MIN") else None)
    result["neck_side"] = json_value(widget._neck_sand_side())
    result["mound_top_y"] = float(widget.get_mound_top_y())
    profile = getattr(widget, "_mound_profile", None)
    result["mound_cols"] = []
    result["mound_cols_format"] = ["absolute_x", "absolute_y", "free_surface", "thickness"]
    result["mound_cols_coordinates"] = "Kivy widget coordinates, origin bottom-left"
    result["mound_apex"] = None
    if profile is not None:
        apex = float(widget._mound_apex())
        result["mound_apex"] = apex
        for dx in widget._mound_knots(apex):
            height, free, thickness = profile.column(dx, apex)
            result["mound_cols"].append([float(widget._cx + dx),
                                         float(widget._lower_sand_bot + height),
                                         bool(free), float(thickness)])
    result["derived_arrays"] = {key: digest_json(getattr(widget, key)) for key in
                                ("_mound_shape", "_upper_rough", "_neck_mass_table")
                                if hasattr(widget, key)}
    result["sha256"] = digest_json(result)
    return result


def shader_state(widget):
    contexts, seen = [], set()

    def add(value, label):
        shader = getattr(value, "shader", None)
        if shader is None or id(value) in seen:
            return
        seen.add(id(value))
        tag = None
        try:
            tag = json_value(value["sand_ctx_tag"])
        except (KeyError, TypeError, IndexError):
            pass
        uniforms, missing = {}, []
        for name in ("sand_time", "sand_clock", "sand_mix", "stream_texture_phase", "surface_phase"):
            try:
                uniforms[name] = json_value(value[name])
            except (KeyError, TypeError, IndexError):
                uniforms[name] = None
                missing.append(name)
        contexts.append({"path": label, "class": type(value).__name__, "tag": tag,
                         "success": bool(shader.success),
                         "uniforms": uniforms, "uniforms_missing": missing,
                         "vertex_sha256": hashlib.sha256(str(shader.vs).encode()).hexdigest(),
                         "fragment_sha256": hashlib.sha256(str(shader.fs).encode()).hexdigest()})

    for name, value in widget.__dict__.items():
        if isinstance(value, (list, tuple)) and ("flow" in name or "context" in name):
            for index, item in enumerate(value):
                add(item, "%s[%d]" % (name, index))
        elif "flow" in name or "context" in name:
            add(value, name)

    visited = set()

    def walk(value, path, depth=0):
        if id(value) in visited or depth > 16:
            return
        visited.add(id(value))
        add(value, path)
        for index, child in enumerate(getattr(value, "children", ())):
            walk(child, "%s/%d" % (path, index), depth + 1)

    walk(widget.canvas, "canvas")
    flow = getattr(widget, "_sand_flow_contexts", ())
    material = getattr(widget, "_sand_material", None)
    mound = getattr(widget, "_mound_surface_flow", None)
    # Count the raw direct canvas entries BEFORE deduplication: the same context
    # can be attached twice and drawn twice even though it has one Python id.
    children = list(widget.canvas.children)
    mound_children = [child for child in children
                      if type(child).__name__ == "MoundSurfaceFlowContext"]
    raw_mound_count = len(mound_children)
    unique_mound_count = len({id(child) for child in mound_children})
    current_mound_count = sum(child is mound for child in children)
    expected_mound_count = 1 if mound is not None else 0
    lower_gpu = any(row["tag"] == 3.0 or ("mound" in row["path"].lower()
                    and "surface" not in row["path"].lower()) for row in contexts)
    fallback = "static_texture" if material is not None else "flat_color"
    return {"contexts": contexts,
            "raw_mound_canvas_count": raw_mound_count,
            "unique_mound_canvas_count": unique_mound_count,
            "current_mound_canvas_count": current_mound_count,
            "mound_canvas_consistent": (raw_mound_count == unique_mound_count
                                         == current_mound_count == expected_mound_count),
            "uniform_source": "RenderContext Python-side uniform cache; not GPU uniform readback",
            "all_present_shaders_compiled": all(row["success"] for row in contexts),
            "flow_shader_success": [bool(item.shader.success) for item in flow],
            "reservoir_neck": "gpu" if len(flow) >= 2 else fallback,
            "lower_body": "gpu" if lower_gpu else fallback,
            "landing": "gpu_surface" if mound is not None else "particle_contact_only",
            "mound_surface_shader_success": bool(mound.shader.success) if mound is not None else None,
            "stream_renderer": getattr(widget, "flow_renderer", "line_pool"),
            "splash_renderer": getattr(widget, "splash_renderer", "rect"),
            "note": "An absent optional context is a reported fallback, not proof the new feature ran."}


def stream_draw_state(widget):
    """Optional snapshot of the actual float32 template/endpoint submission buffers.

    Texture batches draw their allocated capacity; only the first live[chunk]
    endpoint slots are active. Remaining slots use offscreen PAD_ENDPOINT values
    and are excluded from active_endpoints, never guessed from particle physics.
    """
    batches = getattr(widget, "_flow_batches", None)
    if not isinstance(batches, dict):
        raise RuntimeError("Stream geometry capture requires installed texture batches")
    rows = []
    float3 = struct.Struct("<fff")
    for order, (key, batch) in enumerate(batches.items()):
        if not hasattr(batch, "live") or len(batch.live) != len(batch.parts):
            raise RuntimeError("Unsupported stream batch endpoint layout: " + str(key))
        template_bytes = b"".join(float3.pack(*(float(v) for v in point))
                                   for point in batch.template)
        template = [list(point) for point in struct.iter_unpack("<fff", template_bytes)]
        indices = [int(index) for index in batch.indices]
        index_bytes = struct.pack("<%dH" % len(indices), *indices)
        row = {"key": json_value(key), "order": order,
               "class": type(batch).__name__, "width": float(batch.width), "mode": str(batch.mode),
               "template": template, "indices": indices,
               "template_bytes_sha256": hashlib.sha256(template_bytes).hexdigest(),
               "indices_bytes_sha256": hashlib.sha256(index_bytes).hexdigest(), "parts": []}
        for chunk, part in enumerate(batch.parts):
            count = int(batch.live[chunk])
            if count < 0 or len(part) < 7:
                raise RuntimeError("Invalid live count or endpoint part layout")
            data = part[6]
            if data is None and count:
                raise RuntimeError("Active stream part has no endpoint buffer")
            raw = bytes(data) if data is not None else b""
            if len(raw) < count * float3.size:
                raise RuntimeError("Endpoint texture buffer is shorter than its active prefix")
            active = raw[:count * float3.size]
            endpoints = [list(point) for point in struct.iter_unpack("<fff", active)]
            if any(not math.isfinite(v) for point in endpoints for v in point):
                raise RuntimeError("Non-finite submitted stream endpoint")
            submitted_indices = [int(index) for index in part[0].indices]
            submitted_bytes = struct.pack("<%dH" % len(submitted_indices), *submitted_indices)
            part_row = {"chunk": chunk, "live": count, "capacity": int(part[3]),
                        "active_endpoints": endpoints,
                        "endpoint_bytes_sha256": hashlib.sha256(active).hexdigest(),
                        "buffer_size_bytes": len(raw),
                        "submitted_index_count": len(submitted_indices),
                        "submitted_indices_sha256": hashlib.sha256(submitted_bytes).hexdigest(),
                        "inactive_slots_excluded": max(0, len(raw) // float3.size - count)}
            part_row["sha256"] = digest_json(part_row)
            row["parts"].append(part_row)
        row["sha256"] = digest_json(row)
        rows.append(row)
    result = {"renderer": getattr(widget, "flow_renderer", "line_pool"),
              "encoding": "little-endian float32",
              "template_format": ["dx", "dy", "end_selector"],
              "endpoint_format": ["x", "bottom", "top"],
              "position_rule": "(x + dx, bottom * (1 - end_selector) + top * end_selector + dy)",
              "coordinates": "Kivy widget coordinates, origin bottom-left",
              "active_slot_rule": "Per part, slots [0, live) only; inactive padded slots are excluded",
              "data_source": "CPU float32 mesh template and last submitted endpoint texture buffer; not GPU readback",
              "batches": rows,
              "active_endpoint_count": sum(part["live"] for row in rows for part in row["parts"])}
    result["sha256"] = digest_json(result)
    return result


class CaptureSession:
    def __init__(self, root):
        self.root, self.output = root, None
        self.request, self.report = {}, {}
        self.finished = False

    def write(self):
        path = self.output / "result.json"
        temporary = self.output / "result.json.tmp"
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    def fail(self, exception):
        if self.output is None:
            self.output = self.root / "_material_qa" / ("bootstrap-error-%d-%d" %
                                                       (time.time_ns(), os.getpid()))
            self.output.mkdir(parents=True, exist_ok=False)
        self.report.update(completed=False, passed=False, exception=exception,
                           finished_at=time.time())
        self.finished = True
        self.write()
        print("MATERIAL_PROBE_FAILED " + str(self.output / "result.json"), flush=True)

    def prepare(self):
        path = self.root / "_material_qa/request.json"
        self.request = json.loads(path.read_text(encoding="utf-8"))
        name = self.request.get("id")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise ValueError("id must be a safe directory component")
        output = self.root / "_material_qa" / name
        output.mkdir(parents=True, exist_ok=False)
        self.output = output
        self.report = {"id": name, "mode": self.request.get("mode"), "completed": False,
                       "passed": False, "started_at": time.time(), "cases": [], "frames": [],
                       "probe_sha256": file_hash(Path(__file__)), "request_sha256": file_hash(path),
                       "scope": "Deterministic device capture and state integrity, NOT visual acceptance",
                       "timing": "Logical 1/120 steps including post-completion ticks; NOT real-time",
                       "seed": SEED, "front_seed": 0.75, "clock_epoch": EPOCH,
                       "audio": "Construction, playback and completion speech disabled by probe",
                       "optional_context_policy": "Absence is recorded as fallback; no new context required"}
        self.write()
        if self.request.get("mode") != "matrix":
            raise ValueError("Only mode=matrix is supported")
        if not isinstance(self.request.get("capture_stream_geometry", False), bool):
            raise ValueError("capture_stream_geometry must be boolean")
        self.cases = normalize_cases(self.request)
        runtime = Path(self.request.get("runtime_dir", str(self.root / "_material_qa/runtime")))
        self.runtime = (runtime if runtime.is_absolute() else self.root / runtime).resolve()
        paths = [self.runtime / "main.py"] + sorted((self.runtime / "src/app").glob("*.py"))
        if not paths[0].is_file() or not (self.runtime / "src/app/__init__.py").is_file():
            raise ValueError("runtime_dir must contain main.py and src/app source")
        self.report["runtime_dir"] = str(self.runtime)
        self.report["source_files"] = {p.relative_to(self.runtime).as_posix(): file_hash(p) for p in paths}
        self.report["source_sha256"] = file_hash(paths[0])
        self.report["expected_cases"] = len(self.cases)
        self.report["expected_frames"] = sum(len(c["frame_offsets"]) for c in self.cases)
        self.report["cases"] = [{"name": c["name"], "request": c, "status": "pending",
                                 "completed": False, "passed": False, "frames": []} for c in self.cases]
        self.config_dir = self.output / "isolated-config"
        self.config_dir.mkdir()
        self.report["config_dir"] = str(self.config_dir)
        (self.output / "request.json").write_bytes(path.read_bytes())
        self.write()

    def module_manifest(self):
        result = {}
        wanted = {p.stem for p in (self.runtime / "src/app").glob("*.py")}
        for name, loaded in sorted(sys.modules.items()):
            if not (name == "material_qa_runtime" or name == "app"
                    or name.startswith("app.") or name in wanted):
                continue
            location = getattr(loaded, "__file__", None)
            if location is None:
                continue
            path = Path(location).resolve()
            if not path.is_relative_to(self.runtime):
                raise RuntimeError("Runtime module escaped chosen source: %s=%s" % (name, path))
            result[name] = {"path": path.relative_to(self.runtime).as_posix(),
                            "sha256": file_hash(path)}
        return result

    def run(self):
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_HOME"] = str(self.config_dir / "kivy")
        os.environ["KIVY_NO_CONFIG"] = "1"
        for key, value in self.request.get("env", {}).items():
            if not isinstance(key, str) or not key.startswith("HG_") or not isinstance(value, str):
                raise ValueError("env accepts only string HG_* overrides")
            os.environ[key] = value
        self.report["env"] = {k: v for k, v in os.environ.items() if k.startswith("HG_")}
        sys.dont_write_bytecode = True
        from kivy.utils import platform
        if platform != "android":
            raise RuntimeError("This entry is device-only; do not run it as a PC app")
        wanted = {p.stem for p in (self.runtime / "src/app").glob("*.py")}
        for name in tuple(sys.modules):
            if name == "app" or name.startswith("app.") or name in wanted:
                del sys.modules[name]
        sys.path[:0] = [str(self.runtime / "src/app"), str(self.runtime / "src"), str(self.runtime)]
        spec = importlib.util.spec_from_file_location("material_qa_runtime", self.runtime / "main.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.module = module
        from kivy.clock import Clock
        from kivy.core.window import Window
        self.now = EPOCH
        session = self

        class FakeTime:
            @staticmethod
            def perf_counter():
                return session.now

            def __getattr__(self, name):
                return getattr(time, name)

        module.time = FakeTime()
        module.config_path = lambda: str(self.config_dir / ".hourglass_config.json")
        initial = {"duration": 50.0, "color_name": module.SAND_PRESETS[0][0],
                   "sound_name": module.SILENT_NAME, "sand_mode": "grain", "sand_grain": 0.35,
                   "grain_level": "3", "rough_level": "3", "grain_rev": 3}
        Path(module.config_path()).write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
        widget_class = module.HourglassWidget
        for method in ("_make_sound_proxy", "_make_completion_sound", "_load_voice_bank",
                       "_play_sound", "_play_completion_sound", "_voice_say",
                       "_schedule_completion_popup"):
            setattr(widget_class, method, named_noop(method))

        def silent_sound(widget, _name):
            widget.sound_name, widget._sound = module.SILENT_NAME, None

        silent_sound.__name__ = "_set_sound"
        silent_sound.__qualname__ = "HourglassWidget._set_sound"
        widget_class._set_sound = silent_sound

        class ProbeApp(module.HourglassApp):
            @property
            def user_data_dir(self):
                return str(session.config_dir)

            def get_application_config(self, defaultpath="%(appdir)s/%(appname)s.ini"):
                return str(session.config_dir / "probe.ini")

            def build(self):
                result = super().build()
                Clock.unschedule(self.hourglass.tick)
                self.hourglass.completion_enabled = False
                return result

            def completion_popup_allowed(self, _duration):
                return False

            def on_completed(self, _duration):
                return None

            def on_start(self):
                Clock.unschedule(self.hourglass.tick)
                self._stable, self._geometry, self._case_index = 0, None, -1
                self._started = time.monotonic()
                self._failed = False
                Window.bind(on_flip=self._hide_startup_screen)
                self.schedule(self.settle, 0.1)

            def schedule(self, method, delay=0):
                def protected(dt):
                    if self._failed:
                        return
                    try:
                        method(dt)
                    except BaseException:
                        self._failed = True
                        if 0 <= self._case_index < len(session.report["cases"]):
                            row = session.report["cases"][self._case_index]
                            row.update(status="failed", exception=traceback.format_exc())
                        session.fail(traceback.format_exc())
                        self.stop()
                Clock.schedule_once(protected, delay)

            def settle(self, _dt):
                widget = self.hourglass
                if time.monotonic() - self._started > 20:
                    raise RuntimeError("Device layout did not settle")
                geometry = (tuple(Window.size), tuple(widget.pos), tuple(widget.size),
                            getattr(widget, "_cx", None), getattr(widget, "_R_inner", None))
                self._stable = self._stable + 1 if geometry == self._geometry else 0
                self._geometry = geometry
                if not widget._geom_ready or self._stable < 3:
                    self.schedule(self.settle, 0.1)
                    return
                session.report["module_sources"] = session.module_manifest()
                session.report["dimensions"] = list(Window.size)
                session.write()
                self.schedule(self.next_case)

            def next_case(self, _dt):
                self._case_index += 1
                if self._case_index >= len(session.cases):
                    session.report["module_sources"] = session.module_manifest()
                    source_files_end = {name: file_hash(session.runtime / name)
                                        for name in session.report["source_files"]}
                    session.report["source_files_end"] = source_files_end
                    session.report["source_unchanged"] = source_files_end == session.report["source_files"]
                    session.report.update(completed=True,
                        passed=(all(c["passed"] for c in session.report["cases"])
                                and len(session.report["frames"]) == session.report["expected_frames"]
                                and session.report["source_unchanged"]),
                        finished_at=time.time())
                    session.finished = True
                    session.write()
                    print("MATERIAL_PROBE_COMPLETE " + str(session.output / "result.json"), flush=True)
                    self.stop()
                    return
                self.case = session.cases[self._case_index]
                self.case_report = session.report["cases"][self._case_index]
                self.case_report.update(status="running")
                session.write()
                widget = self.hourglass
                widget.reset()
                session.now, self.logical_time = EPOCH, 0.0
                random.seed(SEED)
                if getattr(module, "_np", None) is not None:
                    module._np.random.seed(SEED)
                colors = [item[0] for item in module.SAND_PRESETS]
                color = self.case["color"]
                if isinstance(color, str):
                    color = colors.index(color)
                if isinstance(color, bool) or not isinstance(color, int) or not 0 <= color < len(colors):
                    raise ValueError("Unknown color: " + str(color))
                if self.case["grain"] not in [str(row[0]) for row in module.SAND_GRAIN_LEVELS]:
                    raise ValueError("Unknown grain level")
                if self.case["rough"] not in [str(row[0]) for row in module.SURFACE_ROUGH_LEVELS]:
                    raise ValueError("Unknown rough level")
                if self.case["period"] > module.MAX_DURATION or self.case["sand_grain"] > module.SAND_GRAIN_MAX:
                    raise ValueError("Requested settings exceed application limits")
                name, base, dark, light = module.SAND_PRESETS[color]
                widget.set_sand_color(base, dark, light)
                widget.set_grain_level(self.case["grain"])
                widget.set_rough_level(self.case["rough"])
                widget.set_duration(self.case["period"])
                widget.reset()
                widget._rebuild_height_table()
                if widget.set_sand_grain(self.case["sand_grain"]) is False:
                    raise RuntimeError("Requested sand material could not be applied")
                if widget._sand_material is None:
                    raise RuntimeError("Requested sand material was not created")
                widget._front_seed = 0.75
                widget.last_frame, widget.last_tick = EPOCH, None
                widget.completion_enabled = False
                self._mark_selected(name)
                self.duration_btn.text = module._fmt_duration(widget.duration)
                self.case_report["effective_config"] = {
                    "color": color, "color_name": name, "period": widget.duration,
                    "grain": module.current_grain_level(), "rough": module.current_rough_level(),
                    "sand_grain": module.SAND_MATERIAL_GRAIN}
                effective = self.case_report["effective_config"]
                if (str(effective["grain"]) != self.case["grain"]
                        or str(effective["rough"]) != self.case["rough"]
                        or effective["period"] != self.case["period"]
                        or effective["sand_grain"] != self.case["sand_grain"]):
                    raise RuntimeError("Applied configuration differs from requested case")
                for _ in range(2000):
                    if not widget._warm_queue:
                        break
                    widget._warm_batches_step()
                if widget._warm_queue:
                    raise RuntimeError("Batch warm-up did not finish")
                random.seed(SEED)
                if getattr(module, "_np", None) is not None:
                    module._np.random.seed(SEED)
                if self.case["state"] != "initial":
                    widget.toggle()
                self.advance_to(self.case["elapsed"])
                self.case_report["pause_check"] = None
                if self.case["pause_seconds"] or self.case["state"] == "paused":
                    if not widget.running:
                        raise ValueError("Pause requires an active unfinished timer")
                    widget.toggle()
                    before = physical_state(widget, module)
                    self.advance_to(self.logical_time + self.case["pause_seconds"])
                    after = physical_state(widget, module)
                    self.case_report["pause_check"] = {
                        "seconds": self.case["pause_seconds"], "before_sha256": before["sha256"],
                        "after_sha256": after["sha256"], "frozen": before["sha256"] == after["sha256"]}
                    if self.case["state"] != "paused":
                        widget.toggle()
                self._frame_index, self._frame_origin = 0, self.logical_time
                self.draw_frame()

            def advance_to(self, target):
                widget = self.hourglass
                if target < self.logical_time - 1e-10:
                    raise ValueError("Logical clock cannot go backwards inside a case")
                remaining = max(0.0, target - self.logical_time)
                max_steps = int(session.request.get("max_steps_per_advance", 2000000))
                if math.ceil(remaining / STEP) > max_steps:
                    raise ValueError("Requested logical advance exceeds explicit step budget")
                draw = widget.redraw
                widget.redraw = lambda: None
                try:
                    while self.logical_time + 1e-10 < target:
                        previous = self.logical_time
                        self.logical_time = min(target, previous + STEP)
                        session.now = EPOCH + self.logical_time
                        # Intentionally tick even after running=False: drain real tail/effects.
                        widget.tick(self.logical_time - previous)
                finally:
                    widget.redraw = draw

            def draw_frame(self):
                widget = self.hourglass
                self._before_draw = physical_state(widget, module)
                widget.redraw()
                self.update_time(max(0.0, widget.duration - widget.elapsed), widget.duration)
                self.on_run_state_changed()
                # Two independently scheduled UI frames flush Canvas at unchanged fake time.
                self.schedule(lambda _dt: self.schedule(self.capture, 0.05), 0.05)

            def capture(self, _dt):
                from kivy.core.image import Image as CoreImage
                from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
                from kivy.graphics.texture import Texture
                widget = self.hourglass
                width, height = map(int, Window.size)
                pixels = bytes(glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE))
                if len(pixels) != width * height * 4:
                    raise RuntimeError("Incorrect framebuffer length")
                if all(len(set(pixels[channel::4])) <= 1 for channel in (0, 1, 2)):
                    raise RuntimeError("Framebuffer is a single flat color, not a captured app frame")
                name = "%s-f%03d" % (self.case["name"], self._frame_index)
                path = session.output / (name + ".png")
                texture = Texture.create(size=(width, height), colorfmt="rgba")
                texture.blit_buffer(pixels, colorfmt="rgba", bufferfmt="ubyte")
                CoreImage(texture).save(str(path), flipped=True)
                if not path.is_file() or path.stat().st_size == 0:
                    raise RuntimeError("Framebuffer PNG was not saved")
                physical = physical_state(widget, module)
                geometry = geometry_state(widget, module)
                shaders = shader_state(widget)
                after_geometry = physical_state(widget, module)
                geometry_same = physical["sha256"] == after_geometry["sha256"]
                draw_same = self._before_draw["sha256"] == physical["sha256"]
                frame = {"name": name, "case": self.case["name"], "frame_index": self._frame_index,
                         "period": self.case["period"], "color": self.case_report["effective_config"]["color"],
                         "grain": self.case["grain"], "rough": self.case["rough"],
                         "elapsed_requested": self.case["elapsed"], "elapsed_actual": widget.elapsed,
                         "logical_elapsed": self.logical_time, "fake_clock": session.now,
                         "frame_offset": self.case["frame_offsets"][self._frame_index],
                         "dimensions": [width, height], "running": bool(widget.running),
                         "particles": widget.pn, "splashes": widget._sn,
                         "transfer": physical["ledger"], "physics": physical,
                         "draw_preserved_physics": draw_same,
                         "geometry_probe_preserved_physics": geometry_same,
                         "before_draw_sha256": self._before_draw["sha256"],
                         "geometry": geometry, "shaders": shaders,
                         "renderer": shaders["stream_renderer"], "radius": widget._R_inner,
                         "half_width": widget._taper["t_in"],
                         "outlet_y": 2 * widget._neck_y - widget._taper["y_bot"],
                         "file": path.name, "sha256": file_hash(path), "completed": True,
                         "passed": (draw_same and geometry_same
                                    and shaders["all_present_shaders_compiled"]
                                    and shaders["mound_canvas_consistent"])}
                if session.request.get("capture_stream_geometry", False):
                    frame["stream_draw"] = stream_draw_state(widget)
                    frame["stream_draw_preserved_physics"] = (
                        physical_state(widget, module)["sha256"] == physical["sha256"])
                    frame["passed"] = frame["passed"] and frame["stream_draw_preserved_physics"]
                session.report["frames"].append(frame)
                self.case_report["frames"].append(name)
                session.write()
                print("MATERIAL_PROBE_FRAME %s logical=%.9f timer=%.9f pn=%d sn=%d" %
                      (name, self.logical_time, widget.elapsed, widget.pn, widget._sn), flush=True)
                self._frame_index += 1
                if self._frame_index < len(self.case["frame_offsets"]):
                    target = self._frame_origin + self.case["frame_offsets"][self._frame_index]
                    self.advance_to(target)
                    self.draw_frame()
                    return
                frames = [row for row in session.report["frames"] if row["case"] == self.case["name"]]
                pause = self.case_report["pause_check"]
                self.case_report.update(status="completed", completed=True,
                    passed=all(row["passed"] for row in frames) and (pause is None or pause["frozen"]))
                session.write()
                print("MATERIAL_PROBE_CASE_COMPLETE " + self.case["name"], flush=True)
                self.schedule(self.next_case)

        ProbeApp().run()
        if not self.finished:
            raise RuntimeError("Application stopped before all requested cases completed")


def main():
    session = CaptureSession(ROOT)
    try:
        session.prepare()
        session.run()
    except BaseException:
        session.fail(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
