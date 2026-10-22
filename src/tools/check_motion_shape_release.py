"""Bind the 3.6 PC speed/shape review to real pixels, states and exact sources.

This gate does not certify Android devices, audio or frame-rate performance.
The 3.5 counterexample must still fail both speed and shape, independently.
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile

from PIL import Image

from check_material_release import source_equal
from check_release_version import parse_version
from check_short_grain_release import compare_preserved, require_capture, require_draining
from check_stream_motion_shape import analyze
from check_stream_wrap import inspect as inspect_wrap
from record_short_grain import validate_recording


ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "design/checks/records/v3.6/motion-shape-review.json"
CURRENT_ARMS = ("normal", "fine", "coarse", "middle", "high")
SAMPLINGS = ((10.0, 20.0), (30.0, 30.0), (60.0, 60.0))
REQUIRED_TOOLS = {
    "src/tools/check_motion_shape_release.py",
    "src/tools/check_stream_motion_shape.py",
    "src/tools/capture_stream_motion.py",
    "src/tools/verify_short_grain_runtime.py",
    "src/tools/check_short_grain_release.py",
    "src/tools/record_short_grain.py",
    "src/tools/check_material_release.py",
    "src/tools/check_stream_wrap.py",
}
SPEED_FAILURE = "flow_looks_slow_or_not_consistently_downward"
SHAPE_FAILURE = "near_constant_width_rectangle_after_smoothing"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object: " + str(path))
    return result


def digest_text(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def relative_file(root, name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("Invalid relative evidence path")
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts or str(parts) == ".":
        raise ValueError("Evidence path leaves its directory")
    target = (Path(root) / name).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise ValueError("Evidence path resolves outside its directory")
    return target


def safe_extract(archive, output):
    """Extract only ordinary files/directories; never apply archive permissions."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    seen = set()
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle:
            target = relative_file(output, member.name)
            if target in seen:
                raise ValueError("Duplicate archive destination")
            seen.add(target)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError("Unreadable regular archive member")
                with source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
            else:
                raise ValueError("Archive links, devices and special entries are forbidden")


def require_hidden(row):
    if (not isinstance(row, dict) or row.get("IsWindow") is not True
            or row.get("IsWindowVisible") is not False or row.get("IsIconic") is not False
            or row.get("window_state_config") != "hidden"):
        raise ValueError("Capture was not in a truly hidden PC window")


def require_manifest(report):
    manifest = report.get("source_files")
    if (not isinstance(manifest, dict) or "main.py" not in manifest
            or "src/app/app_version.py" not in manifest
            or not all(digest_text(v) for v in manifest.values())):
        raise ValueError("Missing or invalid source manifest")
    for name in manifest:
        relative_file(ROOT, name)
    if report.get("source_files_end") != manifest:
        raise ValueError("Runtime source changed during capture")
    return manifest


def check_image(folder, frame, dimensions):
    path = relative_file(folder, frame.get("file"))
    if not digest_text(frame.get("sha256")) or not path.is_file() or sha(path) != frame["sha256"]:
        raise ValueError("Missing or changed captured pixels")
    with Image.open(path) as image:
        if image.format != "PNG" or list(image.size) != list(dimensions):
            raise ValueError("Captured image dimensions/format disagree with report")
        image.verify()


def require_motion_capture(report, folder, version, grain, minimum_frames=241, minimum_seconds=4.0):
    if report.get("completed") is not True or report.get("passed") is False:
        raise ValueError("Motion recording is incomplete or failed")
    if report.get("version") != version or report.get("renderer") != "short_grain_field":
        raise ValueError("Wrong version or active motion renderer")
    if str(report.get("grain")) != str(grain):
        raise ValueError("Wrong grain setting in motion comparison")
    require_manifest(report)
    require_hidden(report.get("hidden_at_creation"))
    require_hidden(report.get("hidden_at_finish"))
    dimensions = report.get("dimensions")
    if (not isinstance(dimensions, list) or len(dimensions) != 2
            or any(type(n) is not int or n <= 0 for n in dimensions)):
        raise ValueError("Invalid image dimensions")
    frames = report.get("frames")
    if (not isinstance(frames, list) or len(frames) < minimum_frames
            or report.get("expected_frames") != len(frames)
            or report.get("sample_rate") != 60):
        raise ValueError("Missing complete 60-Hz motion sequence")
    times, files = [], set()
    for frame in frames:
        t = frame.get("time")
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
            raise ValueError("Invalid motion timestamp")
        if times and not 0 < t - times[-1] <= 1.0 / 59.0:
            raise ValueError("Non-monotonic or missing motion frame")
        times.append(t)
        if frame.get("file") in files:
            raise ValueError("Duplicate captured frame path")
        files.add(frame.get("file"))
        require_hidden(frame.get("native_hidden"))
        if not isinstance(frame.get("geometry"), dict):
            raise ValueError("Missing per-frame measured geometry")
        check_image(folder, frame, dimensions)
    if times[-1] - times[0] < minimum_seconds - 1e-8:
        raise ValueError("Motion sequence is shorter than required")
    return times[0], times[-1]


def require_pixel_result(result, baseline=False):
    if not isinstance(result, dict) or result.get("pending"):
        raise ValueError("Pixel analysis is missing or incomplete")
    failures = result.get("failures")
    if not isinstance(failures, list):
        raise ValueError("Pixel failure list missing")
    if baseline:
        if SPEED_FAILURE not in failures or SHAPE_FAILURE not in failures:
            raise ValueError("3.5 counterexample did not independently fail speed and shape")
        # The old image may also have fewer than 60% reliable matches. It is
        # still a FAILED control, not a passing recording: retain at least eight
        # real positive-speed witnesses so no-data cannot masquerade as slowness.
        pairs = result.get("reliable_pairs")
        if type(pairs) is not int or pairs < 8:
            raise ValueError("Baseline has too few reliable speed witnesses")
        for field in ("upper_pixels_per_second", "lower_pixels_per_second"):
            value = result.get(field)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError("Baseline speed is missing, non-finite or non-positive")
        allowed = {SPEED_FAILURE, SHAPE_FAILURE, "no_visible_speed_difference_by_height",
                   "insufficient_reliable_pixel_motion"}
        if set(failures) - allowed:
            raise ValueError("Baseline failed through missing data or unrelated defects")
        if result.get("passed") is not False:
            raise ValueError("Counterexample was incorrectly accepted")
    elif (result.get("passed") is not True or result.get("motion_shape_passed") is not True
          or failures):
        raise ValueError("Speed or shape goal failed: " + ", ".join(failures))


def require_cross_checks(analyses):
    """The user setting controls size, not speed; shape must retain body fullness."""
    for sampling in SAMPLINGS:
        fine, coarse = analyses["fine"][sampling], analyses["coarse"][sampling]
        for field in ("upper_pixels_per_second", "lower_pixels_per_second"):
            a, b = fine[field], coarse[field]
            if min(a, b) <= 0 or max(a, b) / min(a, b) > 1.15:
                raise ValueError("Grain coarseness changes apparent fall speed")
        before = analyses["baseline-motion"][sampling]["shape_samples"]
        after = analyses["normal"][sampling]["shape_samples"]
        if not before or not after:
            raise ValueError("Missing body-width witnesses")
        old_width = sum(s["mean_width"] for s in before) / len(before)
        new_width = sum(s["mean_width"] for s in after) / len(after)
        if old_width <= 0 or not .90 <= new_width / old_width <= 1.20:
            raise ValueError("Shape change substantially changes overall column width")


def stage_report(path, count, renderer, version):
    report = read_json(path)
    require_capture(report, count, renderer)
    require_manifest(report)
    if report.get("version") != version:
        raise ValueError("Stage capture version mismatch")
    names = [f.get("name") for f in report["frames"]]
    if None in names or len(set(names)) != len(names):
        raise ValueError("Missing or duplicate stage names")
    for frame in report["frames"]:
        check_image(path.parent, frame, report["dimensions"])
    return report


def source_digest_matches(data, expected, allow_newlines=False):
    candidates = {data}
    if allow_newlines:
        lf = data.replace(b"\r\n", b"\n")
        candidates.update((lf, lf.replace(b"\n", b"\r\n")))
    return any(hashlib.sha256(candidate).hexdigest() == expected for candidate in candidates)


def require_baseline_sources(folder, motion, matrices):
    baseline = folder / "baseline-source"
    manifest = motion["source_files"]
    inventory = {"main.py"} | {p.relative_to(baseline).as_posix() for p in (baseline / "src/app").glob("*.py")}
    if inventory != set(manifest):
        raise ValueError("Baseline source inventory differs from motion capture")
    for name, expected in manifest.items():
        raw = relative_file(baseline, name).read_bytes()
        if not source_digest_matches(raw, expected):
            raise ValueError("3.5 motion baseline source changed: " + name)
    for arm in ("baseline-high", "baseline-low"):
        if set(matrices[arm]["source_files"]) != inventory:
            raise ValueError("Historical baseline runtime inventory differs")
        for name, expected in matrices[arm]["source_files"].items():
            raw = relative_file(baseline, name).read_bytes()
            if not source_digest_matches(raw, expected, allow_newlines=True):
                raise ValueError("Historical baseline differs beyond line endings: " + name)
    if parse_version((baseline / "src/app/app_version.py").read_text(encoding="utf-8")) != (3, 5):
        raise ValueError("Saved baseline is not the 3.5 source")


def validate(record=RECORD):
    record = Path(record)
    review = read_json(record)
    if (review.get("scope") != "pc-stream-motion-shape" or review.get("appearance") != "passed"
            or not review.get("reviewer") or review.get("release_version") != "3.6"):
        raise ValueError("Missing current scoped motion/shape review")
    if not all(review.get("reviewed_goals", {}).get(k) is True
               for k in ("speed", "shape", "continuity", "states")):
        raise ValueError("Required visual goals remain unreviewed")
    validation = review.get("validation_sources")
    if not isinstance(validation, dict) or not REQUIRED_TOOLS.issubset(validation):
        raise ValueError("Acceptance tools are not bound to this review")
    for name, expected in validation.items():
        path = relative_file(ROOT, name)
        digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if not digest_text(expected) or digest != expected:
            raise ValueError("Validation source changed after review: " + name)
    build_digest = hashlib.sha256((ROOT / "buildozer.spec").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if not digest_text(review.get("build_spec_sha256_lf")) or build_digest != review["build_spec_sha256_lf"]:
        raise ValueError("Build configuration changed after review")
    reviewed_files = review.get("reviewed_files")
    if not isinstance(reviewed_files, dict) or "full-cycle.mp4" not in reviewed_files:
        raise ValueError("Reviewed recording is missing")
    if not any(name.endswith(".png") for name in reviewed_files):
        raise ValueError("Reviewed appearance images are missing")
    for name, expected in reviewed_files.items():
        path = relative_file(record.parent, name)
        if not digest_text(expected) or not path.is_file() or sha(path) != expected:
            raise ValueError("Reviewed artifact changed: " + name)
    archive = record.parent / "evidence.tar.xz"
    if not digest_text(review.get("archive_sha256")) or sha(archive) != review["archive_sha256"]:
        raise ValueError("Evidence archive changed")
    with tempfile.TemporaryDirectory(prefix="motion-shape-release-") as temporary:
        folder = Path(temporary)
        safe_extract(archive, folder)
        reports, analyses = {}, {}
        for arm in ("baseline-motion", *CURRENT_ARMS):
            path = folder / arm / "motion.json"
            report = read_json(path)
            grain = 1 if arm == "fine" else 6 if arm == "coarse" else 3
            start, end = require_motion_capture(report, path.parent, "3.5" if arm == "baseline-motion" else "3.6", grain)
            reports[arm] = report
            analyses[arm] = {}
            for sampling in SAMPLINGS:
                result = analyze(path, start=start, end=end, sample_hz=sampling[0], pair_hz=sampling[1])
                require_pixel_result(result, baseline=arm == "baseline-motion")
                analyses[arm][sampling] = result
        normal = reports["normal"]
        for arm in ("baseline-motion", "fine", "coarse", "middle"):
            if reports[arm]["dimensions"] != normal["dimensions"]:
                raise ValueError("Comparison dimensions do not match")
        for arm in ("baseline-motion", "fine", "coarse", "high"):
            if any(abs(reports[arm]["frames"][i]["time"] - normal["frames"][i]["time"]) > 1e-8
                   for i in (0, -1)):
                raise ValueError("Motion comparison windows do not match")
        if reports["high"]["dimensions"][0] <= normal["dimensions"][0]:
            raise ValueError("High-resolution motion capture is missing")
        require_cross_checks(analyses)
        current = [reports[arm] for arm in CURRENT_ARMS]
        for kind in ("birth", "shape"):
            path = folder / ("wrap-" + kind) / "motion.json"
            report = read_json(path)
            require_motion_capture(report, path.parent, "3.6", 3, minimum_frames=37, minimum_seconds=.6)
            if report.get("boundary_type") != kind or inspect_wrap(path).get("passed") is not True:
                raise ValueError("Visible phase boundary discontinuity: " + kind)
            current.append(report)
        matrices = {}
        for arm, count, renderer, version in (
                ("matrix-high", 55, "short_grain_field", "3.6"),
                ("matrix-low", 55, "short_grain_field", "3.6"),
                ("baseline-high", 55, "short_grain_field", "3.5"),
                ("baseline-low", 55, "short_grain_field", "3.5"),
                ("fallback", 4, "line_pool", "3.6"),
                ("full-cycle", 4, "short_grain_field", "3.6")):
            report = stage_report(folder / arm / "result.json", count, renderer, version)
            matrices[arm] = report
            if version == "3.6":
                current.append(report)
        require_baseline_sources(folder, reports["baseline-motion"], matrices)
        measured = normal["source_files"]
        expected_inventory = {"main.py"} | {p.relative_to(ROOT).as_posix() for p in (ROOT / "src/app").glob("*.py")}
        if set(measured) != expected_inventory or any(r["source_files"] != measured for r in current):
            raise ValueError("Current captures do not share the reviewed runtime inventory")
        for name, expected in measured.items():
            saved = relative_file(folder / "measured-source", name)
            current_path = relative_file(ROOT, name)
            if sha(saved) != expected or not source_equal(saved.read_bytes(), current_path.read_bytes()):
                raise ValueError("Runtime changed after acceptance: " + name)
        version = ".".join(map(str, parse_version((ROOT / "src/app/app_version.py").read_text(encoding="utf-8"))))
        if version != review["release_version"]:
            raise ValueError("Review belongs to another application version")
        for arm in ("matrix-high", "matrix-low"):
            frames = matrices[arm]["frames"]
            require_draining(frames)
            paused = [f for f in frames if f["case"] == "paused"]
            if len(paused) != 2 or paused[0]["sha256"] != paused[1]["sha256"]:
                raise ValueError("Paused pixels are not frozen")
            settled = [f for f in frames if f["case"] == "settled"]
            if len(settled) != 1 or settled[0]["particles"] or settled[0]["splashes"]:
                raise ValueError("Stage capture did not settle")
        preserved = [compare_preserved(folder / ("baseline-" + size) / "result.json",
                                       folder / ("matrix-" + size) / "result.json") for size in ("high", "low")]
        if preserved != [55, 55]:
            raise ValueError("Missing full paired state/scope comparison")
        video = read_json(folder / "full-cycle/video.json")
        validate_recording(video)
        for frame in video["frames"]:
            require_hidden(frame.get("hidden"))
        last = video["frames"][-1]
        if last.get("particles") != 0 or last.get("splashes") != 0 or last.get("running") is not False:
            raise ValueError("Full recording ends with active sand")
        movie_sha = sha(record.parent / "full-cycle.mp4")
        if movie_sha != video.get("sha256") or movie_sha != matrices["full-cycle"].get("video", {}).get("sha256"):
            raise ValueError("Reviewed movie differs from final-source capture")
    print("MOTION_SHAPE_RELEASE_OK motion=5x3 baseline=both-fail paired=55+55 scope=PC-visual-only")
    return {"passed": True, "motion_arms": len(CURRENT_ARMS), "samplings": len(SAMPLINGS),
            "paired_frames": sum(preserved), "scope": "PC speed, shape and adjacent states only"}


if __name__ == "__main__":
    validate()
