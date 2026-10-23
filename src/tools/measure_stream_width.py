"""Recompute visible stream widths from original, hash-bound PC PNG frames.

The isolated stream image is a same-state GL capture, not a fabricated mask.
Old gold references without it remain readable, explicitly marked raw. These
measurements do not approve motion, naturalness, frame rate or a release.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image


SCHEMA = "stream-visible-width-v1"
GOLD_R_MINUS_B = 45.0
GOLD_G_MINUS_B = 25.0
BAND_ROWS = 3
COLUMN_THRESHOLD = 0.5


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _manifest(value, name):
    _require(isinstance(value, dict) and bool(value), name + " must be a nonempty manifest")
    for path, digest in value.items():
        _require(isinstance(path, str) and bool(path), name + " has an invalid path")
        _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                 name + " has an invalid SHA256")


def _hidden(value, name):
    _require(isinstance(value, dict) and value.get("IsWindow") is True
             and value.get("IsWindowVisible") is False
             and value.get("IsIconic") is False, name + " does not prove a truly hidden window")


def _safe_image(root, name):
    _require(isinstance(name, str) and bool(name) and "\\" not in name,
             "image path must be a nonempty portable relative path")
    relative = Path(name)
    _require(not relative.is_absolute() and ":" not in name and ".." not in relative.parts,
             "image path must stay within the capture folder")
    path = (root / relative).resolve()
    _require(path.is_relative_to(root.resolve()) and path.is_file(),
             "capture image is missing or escapes its capture folder: " + name)
    return path


def _load_rgb(root, name, digest, dimensions):
    _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
             "missing or invalid image SHA256: " + str(name))
    raw = _safe_image(root, name).read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == digest, "image SHA256 mismatch: " + name)
    with Image.open(io.BytesIO(raw)) as image:
        _require(image.format == "PNG", "original capture must be PNG: " + name)
        _require(list(image.size) == dimensions, "capture dimensions mismatch: " + name)
        return np.asarray(image.convert("RGB"), dtype=np.float64)


def read_motion(path):
    """Read and strictly validate a completed capture; no trust in saved metrics."""
    path = Path(path).resolve()
    if path.is_dir():
        path = path / "motion.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(report, dict) and report.get("completed") is True,
             "motion capture is incomplete")
    _manifest(report.get("source_files"), "source_files")
    _manifest(report.get("source_files_end"), "source_files_end")
    _require(report["source_files"] == report["source_files_end"],
             "runtime source identity changed during capture")
    dimensions = report.get("dimensions")
    _require(isinstance(dimensions, list) and len(dimensions) == 2
             and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in dimensions),
             "invalid image dimensions")
    frames = report.get("frames")
    _require(isinstance(frames, list) and len(frames) >= 2, "multi-frame capture required")
    _require(report.get("expected_frames") == len(frames), "capture frame count is incomplete")
    rate = report.get("sample_rate")
    _require(_finite(rate) and rate > 0, "invalid sample rate")
    _hidden(report.get("hidden_at_creation"), "hidden_at_creation")
    _hidden(report.get("hidden_at_finish"), "hidden_at_finish")
    root = path.parent
    previous = None
    normal_names, stream_names = set(), set()
    for index, frame in enumerate(frames):
        _require(isinstance(frame, dict), "invalid frame")
        elapsed = frame.get("elapsed")
        moment = frame.get("time")
        _require(_finite(elapsed) and _finite(moment) and abs(elapsed - moment) <= 1e-6,
                 "frame lacks its actual matching logical time")
        if previous is not None:
            _require(abs(moment - previous - 1.0 / rate) <= 1e-6,
                     "missing, duplicated or mistimed frame")
        previous = moment
        _hidden(frame.get("native_hidden"), "frame[%d].native_hidden" % index)
        geometry = frame.get("geometry")
        _require(isinstance(geometry, dict), "frame geometry missing")
        for key in ("cx", "outlet_y", "surface_y", "half_width", "radius"):
            _require(_finite(geometry.get(key)), "invalid frame geometry: " + key)
        _require(geometry["half_width"] > 0 and geometry["radius"] > 0,
                 "invalid aperture or bulb radius")
        _require(0 <= geometry["cx"] < dimensions[0]
                 and 0 <= geometry["outlet_y"] < dimensions[1]
                 and 0 < geometry["surface_y"] <= dimensions[1],
                 "frame geometry does not match the captured coordinate space")
        background = geometry.get("background_rgb")
        _require(isinstance(background, list) and len(background) == 3
                 and all(_finite(v) and 0 <= v <= 255 for v in background),
                 "invalid glass background")
        name = frame.get("file")
        _require(isinstance(name, str) and name not in normal_names, "duplicate or missing normal frame")
        normal_names.add(name)
        _load_rgb(root, name, frame.get("sha256"), dimensions)
        has_stream = "stream_file" in frame or "stream_sha256" in frame
        if has_stream:
            stream_name = frame.get("stream_file")
            _require(isinstance(stream_name, str) and stream_name not in stream_names
                     and stream_name != name, "duplicate or invalid isolated stream frame")
            stream_names.add(stream_name)
            _load_rgb(root, stream_name, frame.get("stream_sha256"), dimensions)
    _require(not stream_names or len(stream_names) == len(frames),
             "partial isolated stream capture is not acceptable")
    _require(not normal_names.intersection(stream_names), "normal and isolated frame paths overlap")
    report = copy.deepcopy(report)
    report["_motion_root"] = str(root)
    return report


def _gold_pixels(image):
    return ((image[:, :, 0] - image[:, :, 2] > GOLD_R_MINUS_B)
            & (image[:, :, 1] - image[:, :, 2] > GOLD_G_MINUS_B))


def _percentiles(values):
    if not values:
        return {"p10": None, "p50": None, "p90": None}
    return {name: float(np.percentile(values, percentile))
            for name, percentile in (("p10", 10), ("p50", 50), ("p90", 90))}


def _body(strength, x0, cx, diameter):
    """Central contiguous body after 3-row voting; never a distant lone grain."""
    occupied = strength >= COLUMN_THRESHOLD
    # Bridge only a one-pixel interior pore, without inventing any edge pixels.
    bridged = occupied.copy()
    if len(occupied) > 2:
        bridged[1:-1] |= occupied[:-2] & occupied[2:]
    edges = np.flatnonzero(np.diff(np.r_[False, bridged, False].astype(np.int8)))
    components = []
    for left, right in edges.reshape(-1, 2):
        width = int(right - left)
        if width < max(3.0, 0.3 * diameter):
            continue
        # A stream body must meet the central 20% of its expected aperture.
        if x0 + right <= cx - 0.1 * diameter or x0 + left > cx + 0.1 * diameter:
            continue
        components.append((float(strength[left:right].sum()), width, int(left), int(right)))
    if not components:
        return {"width": 0, "left": None, "right": None, "body_strength": 0.0}
    _, width, left, right = max(components)
    return {"width": width, "left": x0 + left, "right": x0 + right,
            "body_strength": float(strength[left:right].mean())}


def _profile(normal, stream, geometry):
    height, width = normal.shape
    cx, outlet, surface = (float(geometry[k]) for k in ("cx", "outlet_y", "surface_y"))
    diameter = 2.0 * geometry["half_width"]
    x0 = max(0, int(math.floor(cx - 0.65 * diameter - 2)))
    x1 = min(width, int(math.ceil(cx + 0.65 * diameter + 2)))
    y0 = max(1, int(math.floor(outlet - 0.35 * diameter)))
    y1 = min(height - 1, int(math.ceil(surface)))
    combined = stream.copy()
    neck_rows = np.arange(height) + 0.5 < outlet
    combined[neck_rows] = normal[neck_rows]
    result = []
    for y in range(y0, y1):
        strength = combined[y - 1:y + 2, x0:x1].mean(axis=0)
        body = _body(strength, x0, cx, diameter)
        central = np.abs(np.arange(x0, x1) + 0.5 - cx) <= diameter / 2.0
        result.append({"row": y, "depth_d": float((y + 0.5 - outlet) / diameter),
                       "above_surface_px": float(surface - y - 0.5),
                       **body, "aperture_strength": float(strength[central].mean()),
                       "column_strength": [round(float(v), 6) for v in strength]})
    return result, x0, x1


def _select_profile(profile, low, high):
    return [row for row in profile if low <= row["depth_d"] <= high]


def _local_waist(profile, diameter):
    # All three windows must stay away from the neck/head and actual pile top.
    short = max(3, int(round(0.2 * diameter)))
    neighbor = max(3, int(round(0.35 * diameter)))
    margin = max(3.0, 0.35 * diameter)
    candidates = []
    by_row = {r["row"]: r for r in profile}
    for center in profile:
        start = center["row"] - short // 2
        groups = ([by_row.get(y) for y in range(start - neighbor, start)],
                  [by_row.get(y) for y in range(start, start + short)],
                  [by_row.get(y) for y in range(start + short, start + short + neighbor)])
        if any(row is None for group in groups for row in group):
            continue
        if any(row["depth_d"] < 2.0 or row["above_surface_px"] < margin
               for group in groups for row in group):
            continue
        upper, local, lower = [float(np.median([r["width"] for r in group])) for group in groups]
        reference = 0.5 * (upper + lower)
        if reference <= 0:
            continue
        candidates.append({"row": center["row"], "depth_d": center["depth_d"],
                           "above_surface_px": center["above_surface_px"],
                           "upper_width": upper, "local_width": local, "lower_width": lower,
                           "ratio_to_neighbors": local / reference,
                           "drop_px": reference - local})
    if not candidates:
        return None
    return min(candidates, key=lambda row: (row["ratio_to_neighbors"], -row["drop_px"]))


def measure_report(report):
    """Return reproducible widths and strength profiles, with no visual verdict."""
    _require(isinstance(report, dict) and "_motion_root" in report,
             "use read_motion before measure_report")
    root = Path(report["_motion_root"])
    frames, profiles = [], []
    isolated = all("stream_file" in frame for frame in report["frames"])
    for frame in report["frames"]:
        geometry = frame["geometry"]
        diameter = 2.0 * geometry["half_width"]
        normal = _gold_pixels(_load_rgb(root, frame["file"], frame["sha256"], report["dimensions"]))
        stream = (_gold_pixels(_load_rgb(root, frame["stream_file"], frame["stream_sha256"],
                                        report["dimensions"])) if isolated else normal)
        profile, x0, x1 = _profile(normal, stream, geometry)
        neck = _select_profile(profile, -0.25, -0.1)
        near = _select_profile(profile, 0.1, 0.25)
        neck_width = float(np.median([row["width"] for row in neck])) if neck else 0.0
        near_width = float(np.median([row["width"] for row in near])) if near else 0.0
        # Three-row voting needs a row on either side, in addition to the band.
        complete = (geometry["outlet_y"] - 0.25 * diameter >= 1.0
                    and geometry["outlet_y"] + 0.25 * diameter + 1.0 < report["dimensions"][1]
                    and geometry["surface_y"] > geometry["outlet_y"] + 0.25 * diameter + 1.0
                    and geometry["cx"] - diameter / 2.0 >= 0.0
                    and geometry["cx"] + diameter / 2.0 < report["dimensions"][0])
        valid = bool(complete and neck and near and neck_width > 0 and near_width > 0)
        frames.append({"time": float(frame["time"]), "file": frame["file"],
                       "normal_sha256": frame["sha256"],
                       "stream_file": frame.get("stream_file"),
                       "stream_sha256": frame.get("stream_sha256"),
                       "aperture_px": diameter, "nearfield_complete": bool(complete),
                       "valid_body": valid,
                       "neck_width": _percentiles([row["width"] for row in neck]),
                       "near_width": _percentiles([row["width"] for row in near]),
                       "ratio": near_width / neck_width if neck_width > 0 else 0.0,
                       "local_waist": _local_waist(profile, diameter)})
        profiles.append({"time": float(frame["time"]), "x0": x0, "x1": x1,
                         "rows": profile})
    ratios = [frame["ratio"] for frame in frames]
    waists = [frame["local_waist"] for frame in frames if frame["local_waist"] is not None]
    return {"schema": SCHEMA, "completed": True, "visual_verdict": None,
            "scope": "Visible gold body width and local-waist diagnostics only; not motion or release acceptance",
            "version": report.get("version"), "period": report.get("period"),
            "grain": report.get("grain"), "frame_count": len(frames),
            "sample_rate": report["sample_rate"], "dimensions": report["dimensions"],
            "source_files": report["source_files"],
            "body_source": "isolated_stream_gl" if isolated else "raw_gold_reference",
            "plain": not isolated,
            "method": {"palette": "gold", "r_minus_b_gt": GOLD_R_MINUS_B,
                       "g_minus_b_gt": GOLD_G_MINUS_B, "row_band": BAND_ROWS,
                       "column_threshold": COLUMN_THRESHOLD,
                       "body": "largest central component, bridge one interior pixel; no isolated-maximum extent",
                       "neck_depth_d": [-0.25, -0.1], "near_depth_d": [0.1, 0.25]},
            "nearfield": {"ratio": _percentiles(ratios),
                          "neck_width": _percentiles([f["neck_width"]["p50"] or 0.0 for f in frames]),
                          "stream_width": _percentiles([f["near_width"]["p50"] or 0.0 for f in frames]),
                          "valid_frames": sum(f["valid_body"] for f in frames)},
            "waist": {"diagnostic_only": True, "splashes_excluded": isolated,
                      "eligible_frames": len(waists),
                      "ratio_to_neighbors": _percentiles([r["ratio_to_neighbors"] for r in waists]),
                      "drop_px": _percentiles([r["drop_px"] for r in waists])},
            "frames": frames, "profiles": profiles}


def report_metrics(folder):
    return measure_report(read_motion(folder))


def require_nearfield(metrics, min_ratio=0.92, pixel_slack=0):
    """Check only the stated median near-field width, never overall appearance."""
    assert metrics.get("schema") == SCHEMA and metrics.get("completed") is True, "invalid width metrics"
    assert _finite(min_ratio) and 0 < min_ratio <= 1, "invalid near-field ratio threshold"
    assert _finite(pixel_slack) and 0 <= pixel_slack <= 1, "pixel slack must be predeclared within 0..1"
    frames = metrics.get("frames", [])
    assert frames and metrics["nearfield"]["valid_frames"] == len(frames), "missing or truncated near-field body"
    adjusted = []
    for frame in frames:
        neck = frame["neck_width"]["p50"]
        stream = frame["near_width"]["p50"]
        assert frame["valid_body"] and frame["nearfield_complete"] and neck and stream, "invalid near-field frame"
        # A large normal-size aperture never receives a raster-error allowance.
        allowed = pixel_slack if neck < 20 else 0.0
        adjusted.append((stream + allowed) / neck)
    median = float(np.median(adjusted))
    assert median >= min_ratio, "near-field median ratio %.6f is below %.6f" % (median, min_ratio)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("motion", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-nearfield", action="store_true")
    parser.add_argument("--min-ratio", type=float, default=0.92)
    parser.add_argument("--pixel-slack", type=float, default=0)
    args = parser.parse_args()
    metrics = report_metrics(args.motion)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    if args.require_nearfield:
        require_nearfield(metrics, args.min_ratio, args.pixel_slack)
    print(json.dumps({"schema": SCHEMA, "body_source": metrics["body_source"],
                      "frame_count": metrics["frame_count"], "nearfield": metrics["nearfield"],
                      "waist": metrics["waist"], "visual_verdict": None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
