"""Validate scoped PC contact-engine evidence; never treat it as Android/FPS QA."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    "version", "real_contacts", "stream_contacts", "real_landings", "two_layers",
    "old_stream_not_drawn", "old_column_not_drawn", "pause_freezes", "resume_same_world",
    "benchmark_restores_world", "benchmark_restores_ledger", "drained_ledger",
    "drained_particles", "duration-1-drains", "duration-5-drains",
    "resize_keeps_mass", "resize_keeps_world", "grain_control_connected",
    "grain_control_keeps_ledger", "reset_clears_world", "window_hidden",
    "no_side_columns", "long_frame_bounded_physics", "long_frame_keeps_ledger",
    "source_unchanged_during_run",
    "tail_draws_inflight", "finished_releases_contact_world",
    "landing_surface_motion_connected",
    "grain_ledger_matches_clock",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError("Missing evidence or path outside workspace: " + relative)
    return path


def validate(review, committed=True):
    if not review.get("reviewer") or review.get("scope") != "pymunk-integration-pc":
        raise ValueError("Missing scoped PC appearance review")
    if review.get("appearance") != "passed" or not review.get("notes"):
        raise ValueError("Appearance was not reviewed")
    path = local(review["report"])
    if digest(path) != review["report_sha256"]:
        raise ValueError("Changed or outdated integration report")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not report.get("finished") or not report.get("full"):
        raise ValueError("Interrupted or partial integration run")
    if report.get("scope") != review["scope"]:
        raise ValueError("Wrong verification scope")
    checks = report["checks"]
    if (not REQUIRED.issubset({c["id"] for c in checks})
            or any(c["passed"] is not True for c in checks)):
        raise ValueError("Missing or failed integration checks")
    frames = report["frames"]
    expected = {"initial", "opening", "first-landing", "stream", "middle", "late",
                "tail", "timer-end", "drained", "duration-1", "duration-5",
                "duration-36000", "landscape", "resized", "reset", "grain-fine", "grain-coarse"}
    if not expected.issubset({Path(frame["path"]).stem for frame in frames}):
        raise ValueError("Missing affected appearance stages")
    from PIL import Image
    for frame in frames:
        image = local(frame["path"])
        if digest(image) != frame["sha256"]:
            raise ValueError("Missing or changed captured frame")
        with Image.open(image) as decoded:
            decoded.load()
            if min(decoded.size) < 100 or len(decoded.convert("RGB").getcolors(2) or []) == 1:
                raise ValueError("Blank or invalid capture")
    sources = report["sources"]
    expected_sources = {"main.py", "buildozer.spec", "src/tools/verify_pymunk_integration.py"}
    expected_sources.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "src/app").glob("*.py"))
    expected_sources.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "src/build").rglob("*.py"))
    if not expected_sources.issubset(sources):
        raise ValueError("Missing runtime/build source identity")
    for name, value in sources.items():
        source = local(name)
        if digest(source) != value:
            raise ValueError("Code changed after verification: " + name)
        if committed:
            content = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT)
            if content.replace(b"\r\n", b"\n") != source.read_bytes().replace(b"\r\n", b"\n"):
                raise ValueError("Reviewed source is not in pushed commit: " + name)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, default=ROOT / "_temp/pymunk-3.1/review.json")
    parser.add_argument("--working-tree", action="store_true")
    args = parser.parse_args()
    report = validate(json.loads(args.review.read_text(encoding="utf-8")), not args.working_tree)
    print("PYMUNK_RELEASE_OK version=%s scope=PC contacts+appearance; no FPS/audio/Android claim"
          % report["version"])


if __name__ == "__main__":
    main()
