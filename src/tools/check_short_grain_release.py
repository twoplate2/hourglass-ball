"""Release gate for reviewed short-grain geometry, not the old color-only contract."""
import hashlib
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import zipfile

import numpy as np
from PIL import Image

from check_material_release import source_equal
from check_short_grain_visual import inspect
from compare_material_frames import scene_geometry, surface_columns
from record_short_grain import validate_recording

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "design/checks/records/v3.5/short-grain-review.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_capture(report, count, renderer=None):
    if not all(report.get(k) is True for k in ("completed", "passed", "source_unchanged")):
        raise ValueError("Capture incomplete or source changed")
    if len(report.get("frames", [])) != count:
        raise ValueError("Missing frames")
    if not report.get("cases") or len(report["cases"]) != len(report.get("normalized_cases", [])):
        raise ValueError("Missing case matrix")
    for row in [report.get("hidden_at_creation", {}), report.get("hidden_at_finish", {})] + [
            f.get("native_hidden", {}) for f in report["frames"]]:
        if row.get("IsWindow") is not True or row.get("IsWindowVisible") is not False or row.get("IsIconic") is not False:
            raise ValueError("Window not truly hidden")
    for frame in report["frames"]:
        if frame.get("completed") is not True or frame.get("passed") is not True:
            raise ValueError("Incomplete or failed frame")
        if renderer and frame.get("renderer") != renderer:
            raise ValueError("Wrong active renderer")
    if any(c.get("completed") is not True or c.get("passed") is not True for c in report.get("cases", [])):
        raise ValueError("Incomplete case")


def compare_preserved(before_path, after_path, allow_buried_flares=False):
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    frames = {f["name"]: f for f in after["frames"]}
    checked = 0
    for a in before["frames"]:
        b = frames[a["name"]]
        if (a["physics"]["arrays"]["main"] != b["physics"]["arrays"]["main"]
                or a["transfer"] != b["transfer"] or a["geometry"] != b["geometry"]):
            raise ValueError("Main particles, ledger or glass geometry changed: " + a["name"])
        if allow_buried_flares:
            states=[deepcopy(f['physics']) for f in (a,b)]
            for state in states:
                state.pop('sha256',None);state['effects'].pop('flares',None)
            if states[0]!=states[1]:
                raise ValueError('Physics or random state changed outside flare visibility: '+a['name'])
            old={tuple(sorted(f.items())) for f in a['physics']['effects']['flares']}
            if any(tuple(sorted(f.items())) not in old for f in b['physics']['effects']['flares']):
                raise ValueError('Flare filtering introduced or moved a flash: '+a['name'])
        aa = np.asarray(Image.open(before_path.parent / a["file"]).convert("RGB"))
        bb = np.asarray(Image.open(after_path.parent / b["file"]).convert("RGB"))
        h, w = aa.shape[:2]
        scene = scene_geometry(a, (w, h))
        surface = surface_columns(scene, np.arange(w) + .5)[0]
        ys = h - np.arange(h)[:, None] - .5
        # Stream and airborne/settling impact grains may change. Upper chamber,
        # glass/outside and deep mound are protected, including in paused frames.
        affected = ((ys <= scene["outlet"] + 2)
                    & (ys >= surface[None, :] - .2 * scene["diameter"] - 3)
                    & (np.abs(np.arange(w)[None, :] - scene["cx"]) < scene["radius"] + 2))
        # Old strokes reached back into the neck by their 20px density padding.
        # Removing that overpaint is part of the explicitly scoped mouth join.
        affected |= ((ys <= scene["outlet"] + max(24, 2 * scene["half"]))
                     & (ys >= scene["outlet"] - 2)
                     & (np.abs(np.arange(w)[None, :] - scene["cx"]) <= scene["half"] + 2))
        # Short timers can overtake still-visible impact grains as the mound
        # grows. Permit only their recorded tiny rectangles, not a blanket
        # exemption for the static mound body.
        for frame in (a, b):
            rects = frame.get("effect_rects", [])
            if rects and len(rects) != frame["splashes"]:
                raise ValueError("Effect geometry count mismatch")
            for x0, y0, x1, y1 in rects:
                if (not np.isfinite([x0,y0,x1,y1]).all() or x1 < x0 or y1 < y0
                        or max(x1-x0,y1-y0) > 6 * max(1,scene["radius"]/140)):
                    raise ValueError("Invalid effect rectangle")
                left, right = max(0,int(np.floor(x0))-1), min(w,int(np.ceil(x1))+1)
                top, bottom = max(0,int(np.floor(h-y1))-1), min(h,int(np.ceil(h-y0))+1)
                affected[top:bottom,left:right] = True
        if allow_buried_flares:
            clock=a['physics']['scalars']['_mound_flow_clock']
            for flash in a['physics']['effects']['flares']:
                life=max(0.0,flash['end']-clock)/.08
                fw,fh=2+2*life,.8+.4*life
                left=max(0,int(np.floor(flash['x']-fw/2))-1)
                right=min(w,int(np.ceil(flash['x']+fw/2))+1)
                top=max(0,int(np.floor(h-flash['y']-fh/2))-1)
                bottom=min(h,int(np.ceil(h-flash['y']+fh/2))+1)
                affected[top:bottom,left:right]=True
        if np.any(np.any(aa != bb, axis=2) & ~affected):
            raise ValueError("Pixels changed outside stream/contact/splash scope: " + a["name"])
        checked += 1
    return checked


def require_draining(frames):
    rows = [f for f in frames if f["case"] == "draining" and f["particles"] > 0]
    phases = {f.get("stream_draw", {}).get("phase") for f in rows}
    if len(rows) < 2 or len(phases) < 2 or None in phases:
        raise ValueError("In-flight grain animation froze at timer zero")
    first = rows[0]["stream_draw"]["phase"]
    if max(abs((phase-first+128) % 256 - 128) for phase in phases) < .05:
        raise ValueError("Floating-point timer noise is not visible grain motion")


def validate(record=RECORD):
    review = json.loads(record.read_text(encoding="utf-8"))
    if review.get("scope") != "short-grain-pc-visual" or review.get("appearance") != "passed":
        raise ValueError("Missing short-grain visual acceptance")
    if not all(review.get("reviewed_goals", {}).get(k) is True for k in
               ("short_grains", "no_long_lanes", "contact", "compact_splash", "full_cycle")):
        raise ValueError("Visual goals remain unreviewed")
    for name, expected in review["validation_sources"].items():
        actual = hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if actual != expected:
            raise ValueError("Validation source changed after review: " + name)
    if hashlib.sha256((ROOT / "buildozer.spec").read_bytes().replace(b"\r\n", b"\n")).hexdigest() != review["build_spec_sha256_lf"]:
        raise ValueError("Build configuration changed")
    archive = record.parent / "evidence.zip"
    if sha(archive) != review["archive_sha256"]:
        raise ValueError("Evidence archive changed")
    with tempfile.TemporaryDirectory(prefix="short-grain-release-") as temp:
        folder = Path(temp)
        with zipfile.ZipFile(archive) as bundle:
            if any(not (folder / name).resolve().is_relative_to(folder.resolve()) for name in bundle.namelist()):
                raise ValueError("Unsafe evidence path")
            bundle.extractall(folder)
        reports = {}
        for arm, count, renderer in (("final-high", 55, "short_grain_field"),
                                     ("final-low", 55, "short_grain_field"),
                                     ("fallback", 4, "line_pool"),
                                     ("video-after2", 4, "short_grain_field")):
            path = folder / arm / "result.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            require_capture(report, count, renderer)
            for frame in report["frames"]:
                if sha(path.parent / frame["file"]) != frame["sha256"]:
                    raise ValueError("Captured pixels changed")
            reports[arm] = report
        measured = reports["final-high"]["source_files"]
        paths = {"main.py"} | {p.relative_to(ROOT).as_posix() for p in (ROOT / "src/app").glob("*.py")}
        if set(measured) != paths:
            raise ValueError("Runtime inventory differs from acceptance")
        for name, digest in measured.items():
            saved = folder / "measured-source" / name
            if sha(saved) != digest or not source_equal(saved.read_bytes(), (ROOT / name).read_bytes()):
                raise ValueError("Runtime changed after acceptance: " + name)
        for arm in ("before-high", "before-low"):
            baseline = json.loads((folder / arm / "result.json").read_text(encoding="utf-8"))
            require_capture(baseline, 4 if arm == "before-high" else 50)
            for name, digest in baseline["source_files"].items():
                if sha(folder / "baseline-source" / arm / name) != digest:
                    raise ValueError("Baseline source differs from captured source")
            for frame in baseline["frames"]:
                if sha(folder / arm / frame["file"]) != frame["sha256"]:
                    raise ValueError("Baseline image changed")
        if any(r["source_files"] != measured for r in reports.values()):
            raise ValueError("Captures ran different runtime sources")
        for name, expected in review["reviewed_files"].items():
            if sha(record.parent / name) != expected:
                raise ValueError("Reviewed artifact changed: " + name)
        for arm in ("final-high", "final-low"):
            require_draining(reports[arm]["frames"])
            if not inspect(folder / arm / "result.json")["passed"]:
                raise ValueError("Short grain pixel goals failed: " + arm)
            paused = [f for f in reports[arm]["frames"] if f["case"] == "paused"]
            if len(paused) != 2 or paused[0]["sha256"] != paused[1]["sha256"]:
                raise ValueError("Paused image changes")
        if inspect(folder / "before-high/result.json")["passed"]:
            raise ValueError("Known 3.4 long-lane counterexample was not rejected")
        high = compare_preserved(folder / "before-high/result.json", folder / "final-high/result.json")
        low = compare_preserved(folder / "before-low/result.json", folder / "final-low/result.json")
        for arm in ("video-before2", "video-after2"):
            video = json.loads((folder / arm / "video.json").read_text(encoding="utf-8"))
            validate_recording(video)
            if sha(record.parent / (arm + ".mp4")) != video["sha256"]:
                raise ValueError("Reviewed video differs from capture")
    print("SHORT_GRAIN_RELEASE_OK frames=110+4+4 preserved=%d+%d video=2 scope=PC-appearance" % (high, low))


if __name__ == "__main__":
    validate()
