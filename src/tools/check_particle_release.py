"""Bind 3.7 persistent-particle PC review to captures, sources and work timings.

This replaces the historical wave-shaped-column goal; it does not certify
Android, audio or actual display FPS. Original 3.6 evidence is left untouched.
"""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from PIL import Image

from benchmark_particle_renderer import SUGGESTED_BUDGET, _meets_budget, summarize
from check_material_release import source_equal
from check_motion_shape_release import (check_image, digest_text, read_json,
    relative_file, require_hidden, require_manifest, safe_extract, sha, stage_report)
from check_short_grain_release import compare_preserved
from record_short_grain import validate_recording


ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "design/checks/records/v3.7/particle-review.json"
BASELINE = "42406fc3cef8684cf715449d3d35059d623c18a7"
TRACES = {"trace-normal": (3, 241, 8.0, 12.0),
          "trace-tail": (3, 91, 49.5, 51.0),
          "trace-fine": (1, 61, 25.0, 26.0),
          "trace-coarse": (6, 61, 25.0, 26.0)}
REQUIRED_TOOLS = {
    "src/tools/check_particle_release.py", "src/tools/test_particle_release.py",
    "src/tools/test_particle_continuity.py", "src/tools/capture_stream_motion.py",
    "src/tools/benchmark_particle_renderer.py", "src/tools/verify_short_grain_runtime.py",
    "src/tools/material_visual_probe.py", "src/tools/check_motion_shape_release.py",
    "src/tools/check_short_grain_release.py", "src/tools/check_material_release.py",
    "src/tools/compare_material_frames.py", "src/tools/record_short_grain.py",
}


def require_motion(report, folder, version, renderer, grain, count, start, end):
    if report.get("completed") is not True or report.get("passed") is False:
        raise ValueError("Incomplete particle capture")
    if report.get("version") != version or report.get("renderer") != renderer:
        raise ValueError("Wrong particle capture version/renderer")
    if report.get("grain") != grain or report.get("period") != 50:
        raise ValueError("Wrong capture settings")
    require_manifest(report)
    require_hidden(report.get("hidden_at_creation"))
    require_hidden(report.get("hidden_at_finish"))
    frames = report.get("frames", [])
    if len(frames) != count or report.get("expected_frames") != count or report.get("sample_rate") != 60:
        raise ValueError("Missing continuous capture frames")
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, list) or len(dimensions) != 2 or any(type(n) is not int or n < 1 for n in dimensions):
        raise ValueError("Invalid capture dimensions")
    names = set()
    for index, frame in enumerate(frames):
        timestamp = frame.get("time")
        expected = start + index / 60
        if (isinstance(timestamp, bool) or not isinstance(timestamp, (float, int))
                or not math.isfinite(timestamp) or abs(timestamp - expected) > 1e-7):
            raise ValueError("Missing or invalid continuous timestamp")
        if frame.get("file") in names:
            raise ValueError("Duplicate frame file")
        names.add(frame.get("file"))
        require_hidden(frame.get("native_hidden"))
        check_image(folder, frame, dimensions)
    if abs(frames[-1]["time"] - end) > 1e-7:
        raise ValueError("Capture ends before required interval")


def require_trace(report, folder, grain, count, start, end):
    require_motion(report, folder, "3.7", "particle_sprite_pool", grain, count, start, end)
    trace = report.get("continuity", {})
    if (trace.get("passed") is not True or trace.get("mesh_frames") != count
            or any(type(trace.get(k)) is not int or trace[k] <= 0
                   for k in ("births", "contacts", "survivor_steps"))):
        raise ValueError("Missing actual particle lifecycle and mesh trace")
    for frame in report["frames"]:
        state = frame.get("draw_state", {})
        count = state.get("particles")
        if (type(count) is not int or count < 0 or state.get("rendered_particles") != count
                or state.get("birth_identity") != "pwp+ptl" or state.get("active") != bool(count)):
            raise ValueError("Actual particle/mesh correspondence failed")
        require_stream_pixels(frame, folder)


def require_stream_pixels(frame, folder):
    """Reject an active but invisible stream, not its aesthetic density.

    Use the central 20%-70% of the actual outlet-to-pile gap. Short gaps have
    no independent airborne region, so they are left to contact review.
    """
    if frame.get("draw_state", {}).get("active") is not True:
        return None
    geometry = frame.get("geometry", {})
    fields = ("cx", "outlet_y", "surface_y", "half_width")
    if any(isinstance(geometry.get(k), bool) or not isinstance(geometry.get(k), (int, float))
           or not math.isfinite(geometry[k]) for k in fields):
        raise ValueError("Missing finite stream geometry for pixel visibility")
    cx, outlet, surface, half = (geometry[k] for k in fields)
    if half <= 0:
        raise ValueError("Invalid stream half width")
    depth = surface - outlet
    if .5 * depth < 3 * half:
        return None
    background = np.asarray(geometry.get("background_rgb"), dtype=float)
    if background.shape != (3,) or not np.isfinite(background).all():
        raise ValueError("Missing stream background color")
    with Image.open(relative_file(folder, frame.get("file"))) as image:
        pixels = np.asarray(image.convert("RGB"), dtype=np.int16)
    height, width = pixels.shape[:2]
    x0, x1 = max(0, math.floor(cx-half)), min(width, math.ceil(cx+half))
    y0, y1 = max(0, math.floor(outlet+.2*depth)), min(height, math.ceil(outlet+.7*depth))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Active stream visibility region is outside capture")
    region = pixels[y0:y1, x0:x1]
    coverage = float(np.mean(np.max(np.abs(region-background), axis=2) > 3))
    if coverage < .05:
        raise ValueError("Active stream is invisible in actual pixels: %s coverage=%.6f" %
                         (frame.get("file"), coverage))
    return coverage


def require_timing(report, *, candidate):
    if (report.get("completed") is not True or report.get("status") != "completed"
            or report.get("completion_marker") != "PARTICLE_RENDERER_BENCHMARK_COMPLETE"):
        raise ValueError("Work benchmark incomplete")
    protocol = report.get("protocol", {})
    if (protocol.get("rounds") != 3 or protocol.get("frames_per_round") != 300
            or protocol.get("logical_step_seconds") != 1 / 60):
        raise ValueError("Wrong work benchmark protocol")
    if report.get("suggested_budget") != SUGGESTED_BUDGET:
        raise ValueError("Work budget differs from reviewed protocol")
    runs = report.get("runs", [])
    if len(runs) != 3:
        raise ValueError("Missing benchmark rounds")
    all_frames, starts = [], []
    for index, run in enumerate(runs):
        frames = run.get("frame_ms", [])
        if run.get("completed") is not True or run.get("round") != index + 1 or len(frames) != 300:
            raise ValueError("Incomplete benchmark round")
        calculated = summarize(frames)
        if run.get("summary") != calculated:
            raise ValueError("Benchmark summary disagrees with raw wall timings")
        if candidate and (run.get("passed") is not True or not _meets_budget(calculated)):
            raise ValueError("Candidate exceeds the PC work budget")
        start = run.get("start_state", {})
        if (start.get("dimensions") != [1080.0, 1848.0] or start.get("duration") != 50
                or not math.isclose(start.get("elapsed", -1), 25, abs_tol=1e-7)
                or start.get("running") is not True):
            raise ValueError("Wrong benchmark starting state")
        starts.append(start)
        all_frames.extend(frames)
    if any(start != starts[0] for start in starts):
        raise ValueError("Benchmark rounds start at different states")
    calculated = summarize(all_frames)
    if report.get("summary") != calculated or (candidate and report.get("passed") is not True):
        raise ValueError("Incomplete or inconsistent overall work result")
    return calculated


def require_sources(folder, manifest, *, baseline=False):
    inventory = {"main.py"} | {p.relative_to(ROOT).as_posix() for p in (ROOT / "src/app").glob("*.py")}
    if set(manifest) != inventory:
        raise ValueError("Runtime source inventory changed")
    for name, expected in manifest.items():
        saved = relative_file(folder, name).read_bytes()
        if hashlib.sha256(saved).hexdigest() != expected:
            raise ValueError("Saved source differs from captured source: " + name)
        current = (subprocess.check_output(["git", "show", BASELINE + ":" + name], cwd=ROOT)
                   if baseline else relative_file(ROOT, name).read_bytes())
        if not source_equal(saved, current):
            raise ValueError("Source differs from reviewed revision: " + name)


def validate(record=RECORD):
    record = Path(record)
    review = read_json(record)
    if (review.get("scope") != "pc-persistent-particle-stream" or review.get("release_version") != "3.7"
            or review.get("appearance") != "passed" or not review.get("reviewer")):
        raise ValueError("Missing scoped 3.7 particle review")
    goals = ("stable_axis", "particle_continuity", "neck", "contact", "pause_and_settle")
    if not all(review.get("reviewed_goals", {}).get(k) is True for k in goals):
        raise ValueError("Visual goals remain unreviewed")
    validation = review.get("validation_sources", {})
    if not REQUIRED_TOOLS.issubset(validation):
        raise ValueError("Validation tools are not bound to review")
    for name, expected in validation.items():
        digest = hashlib.sha256(relative_file(ROOT, name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if not digest_text(expected) or digest != expected:
            raise ValueError("Validation source changed: " + name)
    build_digest = hashlib.sha256((ROOT / "buildozer.spec").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if build_digest != review.get("build_spec_sha256_lf"):
        raise ValueError("Build configuration changed")
    reviewed = review.get("reviewed_files", {})
    if "full-cycle.mp4" not in reviewed or not any(name.endswith(".png") for name in reviewed):
        raise ValueError("Reviewed video/images missing")
    for name, digest in reviewed.items():
        if not digest_text(digest) or sha(relative_file(record.parent, name)) != digest:
            raise ValueError("Reviewed artifact changed: " + name)
    archive = record.parent / "evidence.tar.xz"
    if sha(archive) != review.get("archive_sha256"):
        raise ValueError("Evidence archive changed")
    with tempfile.TemporaryDirectory(prefix="particle-release-") as temporary:
        folder = Path(temporary)
        safe_extract(archive, folder)
        matrices = {}
        for arm, count, renderer, version in (
                ("matrix-before", 55, "short_grain_field", "3.6"),
                ("matrix-after", 55, "particle_sprite_pool", "3.7"),
                ("fallback", 4, "line_pool", "3.7"),
                ("full-cycle", 4, "particle_sprite_pool", "3.7")):
            matrices[arm] = stage_report(folder / arm / "result.json", count, renderer, version)
        before, after = matrices["matrix-before"], matrices["matrix-after"]
        if (before["normalized_cases"] != after["normalized_cases"]
                or before["dimensions"] != [400, 800] or before["dimensions"] != after["dimensions"]):
            raise ValueError("Stage comparison settings differ")
        preserved = compare_preserved(folder / "matrix-before/result.json", folder / "matrix-after/result.json")
        paused = [f for f in after["frames"] if f["case"] == "paused"]
        if len(paused) != 2 or paused[0]["sha256"] != paused[1]["sha256"]:
            raise ValueError("Paused sand image changes")
        measured = after["source_files"]
        require_sources(folder / "measured-source", measured)
        require_sources(folder / "baseline-source", before["source_files"], baseline=True)
        candidates = [after, matrices["fallback"], matrices["full-cycle"]]
        for arm, (grain, count, start, end) in TRACES.items():
            report = read_json(folder / arm / "motion.json")
            require_trace(report, folder / arm, grain, count, start, end)
            candidates.append(report)
        performances, benchmark_captures = {}, {}
        for arm, candidate in (("perf-before", False), ("perf-after", True)):
            report = read_json(folder / arm / "motion.json")
            require_motion(report, folder / arm, "3.7" if candidate else "3.6",
                           "particle_sprite_pool" if candidate else "short_grain_field", 3, 1, 25, 25)
            timing = read_json(folder / arm / "timing.json")
            if report.get("benchmark") != timing:
                raise ValueError("Work report differs from captured result")
            performances[arm] = require_timing(timing, candidate=candidate)
            benchmark_captures[arm] = timing
            if candidate:
                candidates.append(report)
            elif report["source_files"] != before["source_files"]:
                raise ValueError("Benchmark baseline does not match visual baseline")
        a, b = (benchmark_captures[k] for k in ("perf-before", "perf-after"))
        if (a["runs"][0]["start_state"] != b["runs"][0]["start_state"]
                or a["metadata"].get("seed") != b["metadata"].get("seed")):
            raise ValueError("Before/after work benchmark conditions differ")
        if any(r["source_files"] != measured for r in candidates):
            raise ValueError("Candidate evidence comes from different sources")
        video = read_json(folder / "full-cycle/video.json")
        if video.get("completed") is not True:
            raise ValueError("Full-cycle video incomplete")
        validate_recording(video)
        for frame in video["frames"]:
            require_hidden(frame.get("hidden"))
        movie = relative_file(folder / "full-cycle", video.get("movie"))
        if sha(movie) != video.get("sha256") or sha(movie) != reviewed["full-cycle.mp4"]:
            raise ValueError("Full-cycle movie differs from reviewed video")
    return {"passed": True, "preserved_stage_frames": preserved, "traces": len(TRACES),
            "performance": performances, "scope": "PC appearance, continuity and offscreen work budget"}


if __name__ == "__main__":
    print("PARTICLE_RELEASE_OK", json.dumps(validate(), ensure_ascii=False))
