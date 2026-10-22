"""Independent pixel witnesses for falling speed and non-rectangular stream shape.

Input: {completed: true, frames: [{time, file, geometry: {cx, outlet_y, surface_y, half_width,
radius, background_rgb}}]}. Geometry uses image pixels, origin at TOP LEFT.
Shared geometry may be placed at report level. Shader uniforms are not read.
--sample-hz selects patch origins per second; --pair-hz selects the target
reciprocal time gap. Historical defaults remain 10 origins/s and 20 pairs/s.
Old video.json files can be inspected with --geometry-report result.json;
that adapter interpolates old stage geometry and is diagnostic, not acceptance.
"""
import argparse
from functools import lru_cache
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


def longest_run(values):
    best = run = 0
    for value in values:
        run = run + 1 if value else 0
        best = max(best, run)
    return best


def geometry(raw):
    keys = ("cx", "outlet_y", "surface_y", "half_width", "radius")
    result = {key: float(raw[key]) for key in keys}
    if not all(math.isfinite(v) for v in result.values()):
        raise ValueError("Non-finite geometry")
    if min(result["half_width"], result["radius"]) <= 0:
        raise ValueError("Non-positive stream geometry")
    result["background_rgb"] = np.asarray(raw["background_rgb"], dtype=float)
    if (result["background_rgb"].shape != (3,)
            or not np.isfinite(result["background_rgb"]).all()
            or (result["background_rgb"] < 0).any()
            or (result["background_rgb"] > 255).any()):
        raise ValueError("background_rgb must contain three 0..255 components")
    return result


def shape_metrics(image, g):
    """Measure the silhouette after suppressing individual grain-scale edge bites."""
    h, w = image.shape[:2]
    cx, half = g["cx"], g["half_width"]
    start = int(math.ceil(g["outlet_y"] + .7 * half))
    end = int(math.floor(g["surface_y"] - 1.2 * half))
    if end - start < 5 * half or start < 0 or end >= h:
        return {"applicable": False, "reason": "Free stream too short for macro shape"}
    x0, x1 = max(0, int(cx - 1.5 * half)), min(w, int(cx + 1.5 * half) + 1)
    mask = np.linalg.norm(image[start:end, x0:x1, :3].astype(float)
                          - g["background_rgb"], axis=2) > 8.0
    widths, centers, coverage = [], [], []
    core = np.zeros_like(mask)
    for i, row in enumerate(mask):
        xs = np.flatnonzero(row)
        if len(xs) < max(2, half * .4):
            widths.append(0.0); centers.append(cx - x0); coverage.append(0.0)
            continue
        left, right = np.percentile(xs, [5, 95])
        width = right - left + 1
        center = (right + left) / 2
        widths.append(width); centers.append(center)
        a = max(0, int(math.ceil(center - .32 * width)))
        b = min(len(row), int(math.floor(center + .32 * width)) + 1)
        core[i, a:b] = True
        coverage.append(float(row[a:b].mean()) if b > a else 0.0)
    widths = np.asarray(widths)
    # About one full outlet width; tiny edge grains cannot satisfy macro shape.
    window = max(3, int(2 * half) | 1)
    smooth = np.convolve(widths, np.ones(window) / window, mode="valid")
    variation = float((np.percentile(smooth, 90) - np.percentile(smooth, 10))
                      / max(1e-6, np.median(smooth)))
    thirds = [float(np.mean(chunk)) for chunk in np.array_split(smooth, 3)]
    holes = core & ~mask
    return {"applicable": True, "roi": [x0, start, x1, end],
            "macro_width_variation": variation, "mean_width": float(np.mean(smooth)),
            "width_bands": thirds, "width_smoothing_pixels": window,
            "mean_core_coverage": float(np.mean(coverage)),
            "worst_row_coverage": float(min(coverage)),
            "sparse_band": longest_run(np.asarray(coverage) < .65),
            "longest_core_hole": max(longest_run(col) for col in holes.T),
            "hole_budget": max(4.0, .025 * g["radius"])}


def _patch_displacement(before, after, cx, cy, half, search):
    """Normalized patch correlation; positive displacement is DOWN in the image."""
    h, w = before.shape[:2]
    rx = max(3, int(.48 * half))
    ry = max(8, int(half))
    x0, x1 = int(cx) - rx, int(cx) + rx + 1
    y0, y1 = int(cy) - ry, int(cy) + ry + 1
    margin = int(search)
    if x0 < 0 or x1 > w or y0 - margin < 0 or y1 + margin > h:
        return {"reliable": False, "reason": "Patch search escapes image"}
    gray_a = np.asarray(before[y0:y1, x0:x1, :3], dtype=float).mean(axis=2)
    gray_b = np.asarray(after[y0-margin:y1+margin, x0:x1, :3], dtype=float).mean(axis=2)
    # Static vertical shading is not evidence of falling texture. Remove each
    # column's bias before matching; otherwise a distant similarly lit stripe
    # can beat the actual grain correspondence.
    a = (gray_a - gray_a.mean(axis=0, keepdims=True)).ravel()
    if np.linalg.norm(a) < 1e-6:
        return {"reliable": False, "reason": "No trackable texture"}
    windows = np.lib.stride_tricks.sliding_window_view(gray_b, gray_a.shape[0], axis=0)
    windows = windows.transpose(0, 2, 1)
    windows = (windows - windows.mean(axis=1, keepdims=True)).reshape(2 * margin + 1, -1)
    windows = windows - windows.mean(axis=1, keepdims=True)
    denom = np.linalg.norm(a) * np.linalg.norm(windows, axis=1)
    scores = (windows @ a) / np.maximum(denom, 1e-9)
    best = int(np.argmax(scores))
    shift = float(best - margin)
    subpixel = 0.0
    if 0 < best < len(scores) - 1:
        left, center, right = scores[best-1:best+2]
        curvature = left - 2 * center + right
        if abs(curvature) > 1e-9:
            subpixel = float(np.clip(.5 * (left - right) / curvature, -.5, .5))
    outside = np.abs(np.arange(len(scores)) - best) > 2
    second = float(scores[outside].max()) if outside.any() else -1.0
    quality = float(scores[best])
    reliable = quality >= .45 and quality - second >= .015 and 0 < best < len(scores)-1
    return {"reliable": bool(reliable), "dy": shift + subpixel,
            "correlation": quality, "peak_margin": quality - second}


def displacement(before, after, cx, cy, half, search):
    """Require texture correspondence in both directions; do not assume it falls."""
    forward = _patch_displacement(before, after, cx, cy, half, search)
    if not forward["reliable"]:
        return forward
    reverse = _patch_displacement(after, before, cx, cy + forward["dy"], half, search)
    error = abs(forward["dy"] + reverse.get("dy", 0.0))
    forward.update(reverse_correlation=reverse.get("correlation"),
                   reverse_reliable=reverse["reliable"], cycle_error=error)
    forward["reliable"] = reverse["reliable"] and error <= 1.5
    return forward


def breathing_metrics(times, width_bands):
    """Detect broad, synchronized periodic swelling, not local moving grains."""
    t = np.asarray(times, dtype=float)
    widths = np.asarray(width_bands, dtype=float)
    if len(t) < 20 or t[-1] - t[0] < 3.0:
        return {"evaluated": False, "reason": "At least 3 seconds of shape samples required"}
    uniform = np.linspace(t[0], t[-1], max(32, len(t)))
    bands = np.column_stack([np.interp(uniform, t, widths[:, i]) for i in range(3)])
    residual = bands.copy()
    for i in range(3):
        residual[:, i] -= np.polyval(np.polyfit(uniform, bands[:, i], 1), uniform)
    common = residual.mean(axis=1)
    amplitude = float((np.percentile(common, 95) - np.percentile(common, 5))
                      / max(1e-6, bands.mean()))
    corr = np.corrcoef(residual.T) if np.all(residual.std(axis=0) > 1e-8) else np.eye(3)
    coherence = float(np.mean(corr[np.triu_indices(3, 1)]))
    spectrum = np.abs(np.fft.rfft(common * np.hanning(len(common)))) ** 2
    freq = np.fft.rfftfreq(len(common), uniform[1]-uniform[0])
    selected = (freq >= .3) & (freq <= 4.0)
    energy = spectrum[selected]
    peak = float(energy.max() / max(1e-12, energy.sum())) if len(energy) else 0.0
    periodic = amplitude > .08 and coherence > .8 and peak > .40
    return {"evaluated": True, "amplitude_fraction": amplitude,
            "band_coherence": coherence, "spectral_peak_fraction": peak,
            "periodic_breathing": bool(periodic)}


def legacy_geometry(frame, stage_report):
    samples = [f for f in stage_report["frames"] if f.get("period") == 50
               and f.get("color") == 0 and f.get("grain") == "3"
               and f.get("running") is True and f.get("elapsed_actual", 0) > 0]
    if not samples:
        raise ValueError("No suitable stage geometry for old video")
    samples.sort(key=lambda f: f["elapsed_actual"])
    ref = samples[len(samples)//2]
    g = ref["geometry"]; height = ref["dimensions"][1]
    surface = np.interp(frame.get("elapsed", frame["time"]),
                        [f["elapsed_actual"] for f in samples],
                        [height-f["geometry"]["mound_top_y"] for f in samples])
    return geometry(dict(cx=g["_cx"], outlet_y=height-ref["outlet_y"], surface_y=surface,
                         half_width=ref["half_width"], radius=g["_R_inner"],
                         background_rgb=np.asarray(g["glass_fill_rgb"]) * 255))


def sample_pairs(times, sample_hz=10.0, pair_hz=20.0):
    """Select origins and partners; frame timestamps, never the requested rate, set dt."""
    if not all(math.isfinite(v) and v > 0 for v in (sample_hz, pair_hz)):
        raise ValueError("Sampling frequencies must be finite and positive")
    # Preserve the original default selection exactly so historical failures do
    # not turn green through floating-point boundary changes. Explicit rates
    # tolerate numerical timestamp noise when selecting their requested grid.
    epsilon = 0.0 if (sample_hz, pair_hz) == (10.0, 20.0) else 1e-9
    next_sample = -math.inf
    for i, time in enumerate(times):
        if time < next_sample - epsilon:
            continue
        next_sample = time + 1.0 / sample_hz
        j = int(np.searchsorted(times, time + 1.0 / pair_hz - epsilon))
        yield i, j if i < j < len(times) else None


def analyze(report_path, geometry_report=None, start=20.0, end=30.0,
            sample_hz=10.0, pair_hz=20.0):
    report_path = Path(report_path).resolve()
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    if raw.get("completed") is not True or raw.get("passed") is False:
        raise ValueError("Recording incomplete, cancelled or failed")
    legacy = json.loads(Path(geometry_report).read_text(encoding="utf-8")) if geometry_report else None
    frames = [f for f in raw["frames"] if start <= f["time"] <= end and f.get("running", True)]
    if len(frames) < 3 or any(b["time"] <= a["time"] for a, b in zip(frames, frames[1:])):
        raise ValueError("Insufficient or non-monotonic frame timestamps")

    @lru_cache(maxsize=12)
    def load(index):
        frame = frames[index]
        path = (report_path.parent / frame["file"]).resolve()
        if not path.is_file():
            path = (report_path.parent / "video-frames" / frame["file"]).resolve()
        if not path.is_relative_to(report_path.parent):
            raise ValueError("Frame path escapes recording directory")
        im = np.asarray(Image.open(path).convert("RGB"))
        g = (legacy_geometry(frame, legacy) if legacy else
             geometry(dict(raw.get("geometry", {}), **frame.get("geometry", {}))))
        return im, g

    shapes, motions, times = [], [], np.asarray([f["time"] for f in frames])
    for i, j in sample_pairs(times, sample_hz, pair_hz):
        frame = frames[i]
        im, g = load(i)
        shape = shape_metrics(im, g)
        if not shape["applicable"]:
            continue
        shapes.append(dict(shape, time=frame["time"], file=frame["file"]))
        if j is None:
            continue
        dt = frames[j]["time"] - frame["time"]
        if not .015 <= dt <= .15:
            continue
        after, ga = load(j)
        span = min(g["surface_y"]-g["outlet_y"], ga["surface_y"]-ga["outlet_y"])
        search = max(6, min(int(5 * g["half_width"]), int(.25 * span)))
        bands = []
        for depth in (.25, .70):
            cy = g["outlet_y"] + span * depth
            m = displacement(im, after, g["cx"], cy, g["half_width"], search)
            if m.get("reliable"):
                m["pixels_per_second"] = m["dy"] / dt
                m["outlet_widths_per_second"] = m["dy"] / dt / (2 * g["half_width"])
            bands.append(m)
        motions.append(dict(time=frame["time"], dt=dt, span=span, bands=bands))
    reliable = [m for m in motions if all(b["reliable"] for b in m["bands"])]
    failures = []
    if len(reliable) < 8 or len(reliable) < .6 * len(motions):
        failures.append("insufficient_reliable_pixel_motion")
    upper = float(np.median([m["bands"][0]["pixels_per_second"] for m in reliable])) if reliable else 0.0
    lower = float(np.median([m["bands"][1]["pixels_per_second"] for m in reliable])) if reliable else 0.0
    downward = float(np.mean([all(b["pixels_per_second"] > 0 for b in m["bands"]) for m in reliable])) if reliable else 0.0
    transit = float(np.median([m["span"] * .5 * (1/max(.01,m["bands"][0]["pixels_per_second"])
                         + 1/max(.01,m["bands"][1]["pixels_per_second"])) for m in reliable])) if reliable else math.inf
    speed_ratio = lower / upper if upper > .01 else 0.0
    if transit > 3.0 or downward < .75:
        failures.append("flow_looks_slow_or_not_consistently_downward")
    if speed_ratio < 1.12:
        failures.append("no_visible_speed_difference_by_height")
    macro = float(np.median([s["macro_width_variation"] for s in shapes])) if shapes else 0.0
    if macro < .08:
        failures.append("near_constant_width_rectangle_after_smoothing")
    if not shapes or any(s["mean_core_coverage"] < .85 or s["sparse_band"] > 1
                         or s["longest_core_hole"] > s["hole_budget"] for s in shapes):
        failures.append("sparse_band_or_long_core_hole")
    breathing = breathing_metrics([s["time"] for s in shapes], [s["width_bands"] for s in shapes]) if shapes else {"evaluated": False}
    if breathing.get("periodic_breathing"):
        failures.append("synchronized_periodic_breathing")
    pending = [] if breathing.get("evaluated") else ["periodic_breathing_needs_longer_sequence"]
    if legacy:
        pending.append("legacy_geometry_interpolated_diagnostic_only")
    return {"passed": not failures and not pending, "failures": failures, "pending": pending,
            "motion_shape_passed": not failures,
            "source": str(report_path), "window": [start,end],
            "sampling": {"origin_hz": sample_hz, "target_pair_hz": pair_hz,
                         "actual_pair_dt_median": float(np.median([m["dt"] for m in motions])) if motions else None,
                         "actual_pair_dt_range": [min(m["dt"] for m in motions), max(m["dt"] for m in motions)] if motions else None},
            "samples": len(shapes), "motion_pairs": len(motions), "reliable_pairs": len(reliable),
            "upper_pixels_per_second": upper, "lower_pixels_per_second": lower,
            "lower_to_upper_speed_ratio": speed_ratio, "downward_fraction": downward,
            "equivalent_transit_seconds": transit if math.isfinite(transit) else None,
            "macro_width_variation": macro, "breathing": breathing,
            "shape_samples": shapes, "motion_samples": motions,
            "scope": "pixel motion and shape only; no FPS, audio, physics or Android acceptance"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--geometry-report", type=Path)
    parser.add_argument("--start", type=float, default=20)
    parser.add_argument("--end", type=float, default=30)
    parser.add_argument("--sample-hz", type=float, default=10,
                        help="Patch-origin frequency; default 10 retains historical sampling")
    parser.add_argument("--pair-hz", type=float, default=20,
                        help="Target reciprocal pair interval; default 20 means approximately 0.05 seconds")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.report, args.geometry_report, args.start, args.end,
                     args.sample_hz, args.pair_hz)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    summary = {k:v for k,v in result.items() if k not in ("shape_samples","motion_samples")}
    print(json.dumps(summary, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
