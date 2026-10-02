"""Windowless state equivalence and CPU-cost check, not a frame-rate benchmark."""

import argparse
import ast
import copy
import json
import math
from pathlib import Path
import random
import statistics
import time
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_methods(path, now):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    widget = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "HourglassWidget")
    names = {"update_particles", "get_remaining", "_sand_half_w"}
    body = [node for node in widget.body
            if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in body} != names:
        raise ValueError("Missing production particle methods")
    namespace = {"math": math, "random": random,
                 "time": SimpleNamespace(perf_counter=lambda: now[0])}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def summarize(samples):
    ordered = sorted(samples)
    return {"mean_ms": statistics.mean(samples),
            "p99_ms": ordered[math.ceil(len(ordered) * 0.99) - 1],
            "max_ms": ordered[-1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, default=ROOT / "main.py")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    now = [1000.0]
    before, after = [load_methods(path, now) for path in (args.before, args.after)]

    class Fixture:
        get_remaining = before["get_remaining"]
        _sand_half_w = before["_sand_half_w"]

        def get_mound_top_y(self):
            return self._surface

    cases = []
    for radius, thin in ((130, False), (240, False), (130, True)):
        for period in (1, 5, 15):
            now[0] = 1000.0
            random.seed(23)
            widget = Fixture()
            widget.__dict__.update(
                _geom_ready=True, _cx=200, _R_inner=radius,
                _lower_y_c=50 + radius, _lower_sand_bot=50,
                _lower_sand_top=50 + 2 * radius,
                _lower_ball_cut=70 + 2 * radius,
                _neck_y=90 + 2 * radius, _taper={"y_bot": 110 + 2 * radius},
                neck_w=5 if thin else 17, _ow=4, _particle_motion_scale=4 if period == 1 else 1,
                _neck_fill_time=min(0.25, period * 0.15), speed_factor=2.5,
                running=True, duration=period, elapsed=0, particle_acc=0,
                particles=[], splashes=[], flares=[], mound_peak_offset=0,
                dusts=[{"x": 200, "y": 100, "vx": 5, "vy": 30, "end": 1001}])
            samples = {"before": [], "after": []}
            frame, peak = 0, 0
            while widget.elapsed < period:
                dt = min((1 / 120, 1 / 60, 0.05)[frame % 3], period - widget.elapsed)
                now[0] += dt
                widget.elapsed += dt
                widget.running = widget.elapsed < period
                # Sweep legal collision-plane inputs; this fixture does not render a mound.
                widget._surface = 50 + 2 * radius * (widget.elapsed / period)
                state, rng = copy.deepcopy(widget.__dict__), random.getstate()
                results = {}
                order = ("before", "after") if frame % 2 else ("after", "before")
                for label in order:
                    widget.__dict__ = copy.deepcopy(state)
                    random.setstate(rng)
                    method = (before if label == "before" else after)["update_particles"]
                    start = time.perf_counter_ns()
                    method(widget, dt)
                    elapsed_ms = (time.perf_counter_ns() - start) / 1e6
                    results[label] = copy.deepcopy(widget.__dict__)
                    if len(state["particles"]) >= 100:
                        samples[label].append(elapsed_ms)
                if results["before"] != results["after"]:
                    fields = [key for key in results["before"]
                              if results["before"][key] != results["after"][key]]
                    raise AssertionError(f"State changed: radius={radius}, period={period}, "
                                         f"frame={frame}, fields={fields}")
                peak = max(peak, len(widget.particles))
                frame += 1
            cases.append({
                "radius": radius, "thin": thin, "period": period,
                "equivalent_samples": frame, "particle_peak": peak,
                "dense_samples": len(samples["before"]),
                **{label: summarize(values) for label, values in samples.items() if values},
            })
    report = {"scope": "Update wall-time only; no Canvas, Swap, Clock wait or FPS",
              "before": str(args.before), "after": str(args.after), "cases": cases}
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
