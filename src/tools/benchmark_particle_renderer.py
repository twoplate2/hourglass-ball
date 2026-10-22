"""Small PC offscreen workload benchmark for an existing hidden GL capture.

The caller owns the truly hidden PC window, isolated settings directory, fixed
random seeds/parameters, and source-identity checks.  This module opens no window.
It measures work with the real wall clock; it does not measure display refresh,
audio, Android performance, or visual correctness.

Integration::

    def prepare_round(index):
        # Reset to identical seeds/settings and advance to the same middle state.
        # Update now[0], widget.last_frame and widget.last_tick consistently.
        ...

    def draw_frame():
        fbo.add(widget.canvas)
        try:
            fbo.draw()
        finally:
            fbo.remove(widget.canvas)

    report = run_benchmark(widget, draw_frame, now, 1 / 60, output / "timing.json",
                           prepare_round=prepare_round,
                           metadata={"seed": 1729, "period": 50, "grain": 3})

``draw_frame`` must only submit the existing FBO: no redraw, pixel readback,
PNG, sleep, or window event loop.  This function performs exactly one tick and
one redraw per measured work frame, then waits for glFinish.  Reuse identical
parameters and this harness for before/after comparisons.  The default is three
300-frame rounds (five logical seconds each at 60 steps/s), not a full cycle.
"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from time import perf_counter_ns


SCOPE = "PC offscreen work budget; not actual screen FPS or Android acceptance"
SUGGESTED_BUDGET = {
    "average_equivalent_fps_min": 60.0,
    "one_percent_low_equivalent_fps_min": 50.0,
    "max_frame_ms_max": 50.0,
}


def summarize(frame_ms):
    """Return nearest-rank p99 and FPS from work cost, never logical time."""
    if not frame_ms:
        return {"frames": 0}
    if any(not math.isfinite(value) or value <= 0 for value in frame_ms):
        raise ValueError("Frame wall durations must be positive and finite")
    ordered = sorted(frame_ms)
    count = len(ordered)
    average = math.fsum(ordered) / count
    slow_count = max(1, math.ceil(count * .01))
    slow_average = math.fsum(ordered[-slow_count:]) / slow_count
    return {
        "frames": count,
        "measured_wall_seconds": math.fsum(ordered) / 1000,
        "average_frame_ms": average,
        "average_equivalent_fps": 1000 / average,
        "one_percent_low_frame_ms": slow_average,
        "one_percent_low_equivalent_fps": 1000 / slow_average,
        "slowest_one_percent_frames": slow_count,
        "p99_frame_ms": ordered[math.ceil(count * .99) - 1],
        "max_frame_ms": ordered[-1],
    }


def _meets_budget(summary):
    return bool(summary.get("frames")) and (
        summary["average_equivalent_fps"] >= SUGGESTED_BUDGET["average_equivalent_fps_min"]
        and summary["one_percent_low_equivalent_fps"] >= SUGGESTED_BUDGET["one_percent_low_equivalent_fps_min"]
        and summary["max_frame_ms"] <= SUGGESTED_BUDGET["max_frame_ms_max"]
    )


def _save(path, report):
    temporary = path.with_name(path.name + ".writing")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def run_benchmark(widget, draw_frame, now, step, report_path, *, prepare_round,
                  metadata=None, rounds=3, frames=300, warmup_draws=3):
    """Measure real tick + redraw + FBO.draw + glFinish without image readback.

    ``now`` is the one-element mutable clock used by the existing capture's
    fake application time.  ``prepare_round(index)`` restores the same running
    state for each round; its untimed work may reset and advance the widget.
    Source hashes and native-hidden checks belong in the outer capture report.

    ``completed`` means every requested work frame finished.  ``passed`` means
    only that every completed round meets the stated *suggested* PC work budget;
    it is not a release/appearance/performance acceptance.  Errors including
    KeyboardInterrupt are saved as incomplete and re-raised.  A hard process
    kill leaves the most recent checkpoint incomplete, never passed.
    """
    if not math.isfinite(step) or step <= 0:
        raise ValueError("step must be positive and finite")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < minimum
           for value, minimum in ((rounds, 1), (frames, 1), (warmup_draws, 0))):
        raise ValueError("rounds/frames must be positive integers; warmup_draws must be nonnegative")
    if len(now) != 1 or not math.isfinite(now[0]):
        raise ValueError("now must contain one finite mutable application-clock value")
    path = Path(report_path)
    if path.exists():
        raise FileExistsError("Preserve prior benchmark evidence: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "particle-renderer-pc-workload-v1",
        "scope": SCOPE,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "completed": False, "passed": False, "status": "running",
        "passed_scope": "suggested PC offscreen work budget only; external evidence checked by caller",
        "external_requirements": ["native window truly hidden throughout", "isolated settings directory",
                                  "same fixed seeds and parameters", "runtime sources unchanged"],
        "metadata": dict(metadata or {}),
        "protocol": {
            "rounds": rounds, "frames_per_round": frames, "logical_step_seconds": step,
            "logical_seconds_per_round": frames * step, "warmup_draws_per_round": warmup_draws,
            "wall_clock": "time.perf_counter_ns; independent of application logical clock",
            "timed_operations": ["advance logical clock", "widget.tick", "widget.redraw", "FBO.draw", "glFinish"],
            "excluded": ["round preparation", "warmup", "pixel readback", "image encoding", "report writes"],
            "one_percent_low": "1000 / mean of slowest ceil(frame_count * 0.01) wall durations in ms",
            "p99": "nearest-rank percentile of per-frame wall milliseconds",
        },
        "suggested_budget": dict(SUGGESTED_BUDGET),
        "budget_note": "Advisory 60 FPS work budget, not a product acceptance threshold. For 60 Hz, target 16.67 ms/frame.",
        "runs": [],
    }
    _save(path, report)
    wall_started = perf_counter_ns()
    all_frame_ms = []
    reference = None
    try:
        # Lazy import: importing or analyzing this helper never creates a GL window.
        from kivy.graphics.opengl import glFinish

        for index in range(rounds):
            prepare_round(index)
            start = float(now[0])
            state = {
                "application_clock": start,
                "elapsed": float(widget.elapsed), "duration": float(widget.duration),
                "dimensions": [float(widget.width), float(widget.height)],
                "running": bool(widget.running),
            }
            if not math.isfinite(start) or not state["running"]:
                raise ValueError("Each round must begin in the same finite running middle state")
            if reference is None:
                reference = state
            elif state != reference:
                raise ValueError("Round preparation changed the starting clock, progress, size, or duration")
            run = {"round": index + 1, "completed": False, "passed": False,
                   "start_state": state, "frame_ms": []}
            report["runs"].append(run)
            _save(path, report)
            for _ in range(warmup_draws):
                widget.redraw()
                draw_frame()
                glFinish()
            # Even with no warmup, do not charge prior queued GL work to frame 1.
            glFinish()
            redraw = widget.redraw
            widget.redraw = lambda: None
            try:
                for frame in range(frames):
                    began = perf_counter_ns()
                    now[0] = start + (frame + 1) * step
                    widget.tick(step)
                    redraw()
                    draw_frame()
                    glFinish()
                    milliseconds = (perf_counter_ns() - began) / 1_000_000
                    run["frame_ms"].append(milliseconds)
                    all_frame_ms.append(milliseconds)
                    # Checkpoints are outside measured work and remain incomplete.
                    if (frame + 1) % 60 == 0:
                        _save(path, report)
            finally:
                widget.redraw = redraw
            if not widget.running:
                raise ValueError("Measured interval must stay in the running middle of the cycle")
            run["end_clock"] = float(now[0])
            run["end_elapsed"] = float(widget.elapsed)
            run["summary"] = summarize(run["frame_ms"])
            run["completed"] = len(run["frame_ms"]) == frames
            run["passed"] = run["completed"] and _meets_budget(run["summary"])
            _save(path, report)
        report["completed"] = len(report["runs"]) == rounds and all(run["completed"] for run in report["runs"])
        report["passed"] = report["completed"] and all(run["passed"] for run in report["runs"])
        report["status"] = "completed" if report["completed"] else "incomplete"
        report["completion_marker"] = "PARTICLE_RENDERER_BENCHMARK_COMPLETE"
    except BaseException as error:
        report.update(completed=False, passed=False,
                      status="cancelled" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
                      error={"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        report["summary"] = summarize(all_frame_ms)
        report["harness_wall_seconds"] = (perf_counter_ns() - wall_started) / 1_000_000_000
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        _save(path, report)
    print("PARTICLE_RENDERER_BENCHMARK_COMPLETE", path, flush=True)
    return report
