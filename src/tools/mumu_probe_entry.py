"""Device-only entry used by run_mumu_checks.py; never packaged into the APK."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import sys
import time
import traceback
import unittest

ROOT = Path(__file__).resolve().parent
REQUEST = json.loads((ROOT / "_qa/request.json").read_text(encoding="utf-8"))
OUTPUT = ROOT / "_qa" / REQUEST["id"]
OUTPUT.mkdir(exist_ok=False)
RESULT = OUTPUT / "result.json"
sys.dont_write_bytecode = True
os.environ["KIVY_NO_ARGS"] = "1"


def finish(result):
    result.update(id=REQUEST["id"], mode=REQUEST["mode"], arm=REQUEST["arm"],
                  at=time.time(), environment="reserved MuMu Android Python runtime")
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MUMU_PROBE_DONE " + str(RESULT), flush=True)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def units():
    sys.path.insert(0, str(ROOT / "src"))
    module = load("mumu_stream_tests", ROOT / "_qa/test_stream_rendering.py")
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(module))
    finish({"tests": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped),
            "passed": result.wasSuccessful(), "log": stream.getvalue()})


def application():
    if REQUEST["arm"] == "apk296":
        directory = ROOT / "_qa/baseline"
        sys.path[:0] = [str(directory), str(directory / "tools")]
        source = directory / "main.pyc"
    else:
        source = ROOT / "qa_runtime.py"
    for key, value in REQUEST.get("env", {}).items():
        os.environ[key] = value
    module = load("hourglass_probe", source)
    module._benchmark_source_path = str(source)
    from kivy.clock import Clock
    from kivy.core.window import Window
    now = [1000.0]

    class ProbeApp(module.HourglassApp):
        def on_start(self):
            self._stable = 0
            self._geometry = None
            self._started = time.monotonic()
            Clock.schedule_interval(self.settle, 0.1)

        def settle(self, _dt):
            widget = self.hourglass
            if time.monotonic() - self._started > 15:
                raise RuntimeError("MuMu layout did not settle")
            if not widget._geom_ready:
                return
            key = (tuple(Window.size), widget._cx, widget._R_inner)
            self._stable = self._stable + 1 if key == self._geometry else 0
            self._geometry = key
            if widget._geom_ready and self._stable >= 3:
                Clock.unschedule(self.settle)
                if not widget._sand_flow_contexts:
                    raise RuntimeError("GPU reservoir/neck material did not start")
                if REQUEST["mode"] == "benchmark":
                    Clock.schedule_once(self.benchmark, 2.0)
                else:
                    self.begin_matrix()

        def benchmark(self, _dt):
            random.seed(297)
            self._benchmark_closing = True
            self.settle_timeouts = []
            timeouts = self.settle_timeouts

            class CheckedRunner(module.BenchmarkRunner):
                def _await_settle(self, dt):
                    if (self._settle_start is not None
                            and time.perf_counter() - self._settle_start > 15
                            and not self._flow_settled()):
                        timeouts.append(self._index)
                    super()._await_settle(dt)

            self.runner = CheckedRunner(
                self.hourglass,
                lambda period, index: print("MUMU_BENCH_CASE %s %s" % (period, index),
                                            flush=True),
                self.benchmark_finished, periods=tuple(REQUEST["periods"]))
            self.runner.start()

        def benchmark_finished(self, results, cancelled):
            finish({"cancelled": cancelled, "settle_timeouts": self.settle_timeouts,
                    "source_sha256": hashlib.sha256(
                        source.read_bytes()).hexdigest(), "results": results})
            self.stop()

        def begin_matrix(self):
            Clock.unschedule(self.hourglass.tick)
            self.hourglass.save_config = lambda *_: None
            self.hourglass.completion_enabled = False
            self.on_completed = lambda *_: None
            self.targets = list(REQUEST["cases"])
            self.frames = []
            self._config = None
            original = module.time

            class FakeTime:
                perf_counter = staticmethod(lambda: now[0])

                def __getattr__(self, name):
                    return getattr(original, name)

            module.time = FakeTime()
            self.next_frame(0)

        def next_frame(self, _dt):
            if not self.targets:
                finish({"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "timing": "Logical fixed-step progression, NOT real-time waiting",
                        "frames": self.frames})
                self.stop()
                return
            self.case = self.targets.pop(0)
            widget = self.hourglass
            config = (self.case["period"], self.case["color"])
            if config != self._config or self.case["elapsed"] < widget.elapsed:
                self._config = config
                name, base, dark, light = module.SAND_PRESETS[self.case["color"]]
                widget.set_sand_color(base, dark, light)
                self._mark_selected(name)
                widget.set_duration(self.case["period"])
                widget.reset()
                random.seed(297)
                widget._front_seed = 0.75
                widget.completion_enabled = False
                for _ in range(400):
                    before = len(widget._warm_queue)
                    widget._warm_batches_step()
                    if len(widget._warm_queue) >= before:
                        break
                widget.toggle()
            target = self.case["elapsed"]
            draw = widget.redraw
            widget.redraw = lambda: None
            try:
                while widget.elapsed + 1e-8 < target and widget.running:
                    step = min(1 / 120, target - widget.elapsed)
                    now[0] += step
                    widget.tick(step)
            finally:
                widget.redraw = draw
            widget.redraw()
            self.duration_btn.text = module._fmt_duration(widget.duration)
            self.update_time(widget.duration - widget.elapsed, widget.duration)
            self.on_run_state_changed()
            Clock.schedule_once(self.capture, 0.05)

        def capture(self, _dt):
            from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
            from kivy.core.image import Image as CoreImage
            width, height = map(int, Window.size)
            pixels = bytes(glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE))
            path = OUTPUT / (self.case["name"] + ".png")
            # Kivy's SDL image saver avoids requiring Pillow in the installed APK.
            from kivy.graphics.texture import Texture
            texture = Texture.create(size=(width, height), colorfmt="rgba")
            texture.blit_buffer(pixels, colorfmt="rgba", bufferfmt="ubyte")
            CoreImage(texture).save(str(path), flipped=True)
            widget = self.hourglass
            row = dict(self.case, elapsed_actual=widget.elapsed,
                       dimensions=list(Window.size), particles=widget.pn,
                       transfer=widget.sand_transfer_state(),
                       renderer=getattr(widget, "flow_renderer", "line_pool"),
                       half_width=widget._taper["t_in"],
                       outlet_y=2 * widget._neck_y - widget._taper["y_bot"],
                       radius=widget._R_inner,
                       file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.frames.append(row)
            print("MUMU_MATRIX_FRAME " + self.case["name"], flush=True)
            Clock.schedule_once(self.next_frame, 0.01)

    ProbeApp().run()


try:
    if REQUEST["mode"] == "units":
        units()
    else:
        application()
except BaseException:
    finish({"passed": False, "exception": traceback.format_exc()})
    raise
