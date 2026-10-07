"""Deterministic PC transport regression and rendered before/after filmstrips."""

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import types


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "main.py")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--period", type=float, default=5)
    parser.add_argument("--pixels", default="400,800")
    parser.add_argument("--density", default="1")
    parser.add_argument("--out", type=Path, default=ROOT / "_shot" / "sand_finish")
    parser.add_argument("--expect-defect", action="store_true")
    parser.add_argument("--scalar", action="store_true")
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sand-finish-") as home:
        os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1",
                          KIVY_METRICS_DENSITY=args.density)
        source = args.source.resolve()
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        sys.path.insert(0, str(source.parent))
        spec = importlib.util.spec_from_file_location("sand_finish_source", source)
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        from kivy.clock import Clock
        from kivy.core.window import Window

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassWidget._play_completion_sound = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": args.period}
        m.HourglassWidget.save_config = lambda *_: (_ for _ in ()).throw(
            AssertionError("Regression must not write user configuration"))
        m.HourglassApp.on_completed = lambda *_: None
        if args.scalar:
            m._np = None
            m._flow_numpy = None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])
        failures = set()

        def check(condition, label):
            if not condition:
                failures.add(label)

        def advance(w, target, step=1 / 60):
            guard = 0
            while w.elapsed < target - 1e-10 and w.running:
                interval = min(step, target - w.elapsed)
                now[0] += interval + 1e-12
                w.tick(interval)
                guard += 1
                if guard > 100000:
                    raise AssertionError("Simulation did not advance")

        def inspect(w, label):
            side = w._neck_sand_side()
            outlet = 2 * w._neck_y - w._taper["y_bot"]
            if w.elapsed >= w._neck_fill_time and side:
                check(abs(side[-1][1] - outlet) < 1e-6, label + ": outlet stays connected")
            if hasattr(w, "sand_transfer_state"):
                state = w.sand_transfer_state()
                check(abs(sum(state.values()) - 1) < 1e-9, label + ": mass conserved")
                mass = math.fsum(float(v) for v in w.pmass[:w.pn])
                check(abs(state["flight"] - mass) < 1e-9, label + ": live particle mass")
                check(state["landed"] < 1 - 1e-12 or w.pn == 0,
                      label + ": no full mound with in-flight sand")
                if state["upper"] == 0 and side:
                    check(side[0][1] <= w._upper_sand_bot + 1e-6,
                          label + ": drain does not rise into upper ball")
                if state["neck"] <= 1e-12:
                    check(not side, label + ": empty neck has no projected grains")
                return state
            return None

        def completed(w, label):
            check(not w.running and abs(w.elapsed - w.duration) < 1e-8,
                  label + ": countdown completed")
            check(w.pn == 0, label + ": all grains landed at zero")
            check(not w._neck_sand_side(), label + ": neck empty at zero")
            check(w._upper_sand_height_px() == 0, label + ": upper ball empty")
            check(abs(w._effective_fallen() - 1) < 1e-9, label + ": lower ball full")
            inspect(w, label)

        def suite():
            from frame_benchmark import BenchmarkRunner
            rows = []
            dimensions = ((400, 650), (1000, 2200), (1600, 1000))
            for width, height in dimensions:
                for duration in (1, 5, 15, 50, 3600):
                    random.seed(23)
                    w = m.HourglassWidget(size=(width, height))
                    Clock.unschedule(w.tick)
                    w.duration = duration
                    w._rebuild_height_table()
                    w.redraw = lambda: None
                    w.toggle()
                    label = "%sx%s/%ss" % (width, height, duration)
                    if duration > 50:
                        advance(w, 3.0)
                        # The long-period tail is seeded from a valid conserved steady state.
                        w.elapsed = duration - 3
                        w.last_tick = w.last_frame = now[0]
                        if hasattr(w, "_sand_released"):
                            released = w._released_fraction_at(w.elapsed)
                            live = math.fsum(float(v) for v in w.pmass[:w.pn])
                            w._sand_released = released
                            w._sand_landed = released - live
                            w._sand_pending = 0.0
                            w._mound_shape_cache = w._mound_curve_cache = None
                    peak = 0
                    last_top = float("inf")
                    while w.running:
                        # An occasional 180ms frame crosses emission/finish boundaries.
                        step = 0.18 if int(w.elapsed * 60) % 97 == 11 else 1 / 60
                        advance(w, min(duration, w.elapsed + step), step)
                        peak = max(peak, w.pn)
                        state = inspect(w, label)
                        side = w._neck_sand_side()
                        if state and state["upper"] == 0 and side:
                            check(side[0][1] <= last_top + 1e-6,
                                  label + ": free surface drains downward")
                            last_top = side[0][1]
                    completed(w, label)
                    for _ in range(30):
                        now[0] += 1 / 60
                        w.tick(1 / 60)
                        inspect(w, label + "/post")
                    w.redraw = types.MethodType(m.HourglassWidget.redraw, w)
                    w.reset()
                    check(w.pn == 0 and w.elapsed == 0, label + ": reset clears transfer")
                    rows.append({"case": label, "peak": peak})

            w = m.HourglassWidget(size=(400, 650))
            Clock.unschedule(w.tick)
            w.duration = 5
            w._rebuild_height_table()
            w.toggle()
            advance(w, 4.6)
            w.toggle()
            saved = copy.deepcopy(w.particles)
            transfer = copy.deepcopy(w.sand_transfer_state())
            now[0] += 4
            w.tick(0)
            check(w.particles == saved and w.sand_transfer_state() == transfer,
                  "paused transport is frozen")
            for cancelled in (False, True):
                runner = BenchmarkRunner(w, lambda *_: None, lambda *_: None)
                runner.start()
                runner._finish(cancelled)
                check(w.particles == saved and w.sand_transfer_state() == transfer,
                      "benchmark restore cancelled=%s" % cancelled)
            w.size = (650, 900)
            w._rebuild_height_table()
            check(w.sand_transfer_state() == transfer, "resize preserves mass ledger")
            w.toggle()
            advance(w, 5)
            completed(w, "pause/benchmark/resize")
            w.reset()
            w.toggle()
            advance(w, 5)
            completed(w, "restart")
            args.out.mkdir(parents=True, exist_ok=True)
            report = {"cases": rows, "failures": sorted(failures), "source": str(source),
                      "code_hash": source_hash}
            (args.out / "suite.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            for row in rows:
                print("CASE", row)
            for failure in sorted(failures):
                print("FAIL", failure)
            print("SUMMARY cases=%s failures=%s" % (len(rows), len(failures)))

        def render():
            from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
            from PIL import Image, ImageDraw
            width, height = map(int, args.pixels.split(","))
            args.out.mkdir(parents=True, exist_ok=True)
            images, states = [], []

            class RenderApp(m.HourglassApp):
                def on_start(self):
                    Clock.unschedule(self.hourglass.tick)
                    Window.size = (width, height)
                    Clock.schedule_once(self.begin, 1.0)

                def begin(self, _dt):
                    if tuple(map(int, Window.size)) != (width, height):
                        raise AssertionError("Requested framebuffer size was not applied")
                    self.root.apply_orientation()
                    self.root.do_layout()
                    self.root._anchor.do_layout()
                    self.hourglass.parent.do_layout()
                    w = self.hourglass
                    w.duration = args.period
                    w._rebuild_height_table()
                    random.seed(23)
                    w.reset()
                    w.toggle()
                    first = max(0, args.period - 1.25)
                    redraw = w.redraw
                    w.redraw = lambda: None
                    advance(w, first, 1 / 120)
                    w.redraw = redraw
                    self.targets = [first + i / 30 for i in
                                    range(round((args.period + 0.5 - first) * 30) + 1)]
                    self.targets.append(args.period)
                    if hasattr(w, "_transfer_timing"):
                        start, end, reserve = w._transfer_timing()
                        drain = start + (1 - reserve) * (end - start)
                        self.targets.extend(t for t in
                                            (drain, drain + 1 / 120, end, end + 1 / 120)
                                            if first <= t <= args.period)
                    self.targets = sorted(set(self.targets))
                    self.index = 0
                    self.post_time = args.period
                    self.waiting = False
                    Window.bind(on_flip=self.capture)
                    Clock.schedule_once(self.step, 0)

                def step(self, _dt):
                    if self.index >= len(self.targets):
                        Window.unbind(on_flip=self.capture)
                        self.finish()
                        self.stop()
                        return
                    w = self.hourglass
                    target = self.targets[self.index]
                    if target <= args.period:
                        advance(w, target, 1 / 120)
                    else:
                        interval = target - self.post_time
                        now[0] += interval
                        w.tick(interval)
                        self.post_time = target
                    w.redraw()
                    inspect(w, "render")
                    self.waiting = True
                    self._capture_age = 0
                    Window.canvas.ask_update()

                def capture(self, *_):
                    if not self.waiting:
                        return
                    # Read only after a full draw/flip following the state change.
                    self._capture_age += 1
                    if self._capture_age < 2:
                        Window.canvas.ask_update()
                        return
                    self.waiting = False
                    w = self.hourglass
                    target = self.targets[self.index]
                    pixels = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                    image = Image.frombytes("RGBA", (width, height), pixels).transpose(
                        Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                    side = w._neck_sand_side()
                    if w.elapsed >= w._neck_fill_time and side:
                        half = max(1, int(w._taper["t_in"] * 0.2))
                        pixels_rgb = image.load()
                        for y in range(int(side[-1][1]) + 2, int(side[0][1]) - 2):
                            samples = []
                            for dx in range(-half, half + 1):
                                xw, yw = w.to_window(w._cx + dx, y)
                                xw, row = int(xw), height - 1 - int(yw)
                                if 0 <= xw < width and 0 <= row < height:
                                    samples.append(pixels_rgb[xw, row])
                            gold = sum(r > b + 30 and g > b + 15 for r, g, b in samples)
                            check(bool(samples) and gold >= len(samples) * 0.5,
                                  "render: painted neck has no internal glass band")
                    image.save(args.out / ("frame_%03d.png" % self.index))
                    images.append(image)
                    state = inspect(w, "render") or {}
                    state.update(time=target, elapsed=w.elapsed, particles=w.pn,
                                 neck_grains=w._neck_grain_count,
                                 side=w._neck_sand_side())
                    states.append(state)
                    if abs(target - args.period) < 1e-7:
                        completed(w, "render/zero")
                    self.index += 1
                    Clock.schedule_once(self.step, 0)

                def finish(self):
                    w = self.hourglass
                    outlet = 2 * w._neck_y - w._taper["y_bot"]
                    upper = w._taper["in_pts"][0][1]
                    corners = [w.to_window(w._cx + dx, y) for dx in
                               (-w._R_inner * 0.65, w._R_inner * 0.65) for y in
                               (upper + w._R_inner * 0.4, outlet - w._R_inner * 0.25)]
                    box = (max(0, int(min(p[0] for p in corners))),
                           max(0, height - int(max(p[1] for p in corners))),
                           min(width, int(max(p[0] for p in corners))),
                           min(height, height - int(min(p[1] for p in corners))))
                    crops = [im.crop(box) for im in images]
                    cell_w = 240
                    cell_h = round(crops[0].height * cell_w / crops[0].width)
                    sheet = Image.new("RGB", (cell_w * 6, (cell_h + 24) *
                                             math.ceil(len(crops) / 6)), "white")
                    draw = ImageDraw.Draw(sheet)
                    for i, image in enumerate(crops):
                        x, y = (i % 6) * cell_w, (i // 6) * (cell_h + 24)
                        sheet.paste(image.resize((cell_w, cell_h)), (x, y + 24))
                        draw.text((x + 4, y + 4), "%.3fs" % states[i]["time"], fill="black")
                    sheet.save(args.out / "strip.png")
                    if args.compare:
                        before = json.loads((args.compare / "states.json").read_text(
                            encoding="utf-8"))
                        if before["pixels"] != [width, height]:
                            raise AssertionError("Comparison framebuffer dimensions differ")
                        before_times = {round(s["time"], 6): i for i, s in
                                        enumerate(before["states"])}
                        common = [(i, before_times[round(s["time"], 6)]) for i, s in
                                  enumerate(states) if round(s["time"], 6) in before_times]
                        selected = []
                        for offset in (-0.25, -0.15, -1 / 12, -1 / 60, 0, 0.15):
                            pair = min(common, key=lambda p: abs(
                                states[p[0]]["time"] - args.period - offset))
                            if pair not in selected:
                                selected.append(pair)
                        paired = Image.new("RGB", (cell_w * len(selected),
                                                   2 * (cell_h + 24)), "white")
                        draw_pair = ImageDraw.Draw(paired)
                        for col, (after_i, before_i) in enumerate(selected):
                            old_image = Image.open(args.compare /
                                                   ("frame_%03d.png" % before_i))
                            for row, (label, image) in enumerate(
                                    (("Before", old_image.crop(box)),
                                     ("After", crops[after_i]))):
                                x, y = col * cell_w, row * (cell_h + 24)
                                paired.paste(image.resize((cell_w, cell_h)), (x, y + 24))
                                draw_pair.text((x + 4, y + 4), "%s %.3fs" %
                                               (label, states[after_i]["time"]), fill="black")
                        paired.save(args.out / "comparison.png")
                    (args.out / "states.json").write_text(
                        json.dumps({"states": states, "failures": sorted(failures),
                                    "source": str(source), "code_hash": source_hash,
                                    "pixels": [width, height]},
                                   indent=2), encoding="utf-8")
                    print("RENDER frames=%s failures=%s output=%s" %
                          (len(images), len(failures), args.out))
                    for failure in sorted(failures):
                        print("FAIL", failure)

            RenderApp().run()

        if args.render:
            render()
        else:
            suite()
        if args.expect_defect:
            if not failures:
                raise AssertionError("Negative control did not detect the old defect")
        elif failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
