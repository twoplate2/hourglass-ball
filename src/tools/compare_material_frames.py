"""Compare saved material-probe frames without starting Kivy or a device.

Usage: python src/tools/compare_material_frames.py BEFORE/result.json AFTER/result.json --out DIR
Exit 0 means recorded invariants and the bounded change region passed, not that
the new appearance is approved. Exit 1 is a failed check; exit 2 is pending.
Missing geometry never expands the allowed region to the whole image.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image


MISSING = object()
RASTER_MARGIN = 3.0  # Image pixels, not a fraction of the screen.
SURFACE_BAND_DEPTH = 3.0
COLOR_DISTANCES = (2.0, 8.0, 16.0, 32.0)
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
MAIN_ARRAY_FIELDS = ("px", "py", "pvy", "pxo", "pwp", "pwa", "psz", "ptl", "pli", "pdt", "pmass")
SPLASH_ARRAY_FIELDS = ("sx", "sy", "svx", "svy", "sgd", "shw", "shh", "srest",
                       "sstill", "shas", "sdt", "stag", "saway", "srw", "srh")


def issue(target, field, message):
    target.append({"field": field, "message": message})


def at(value, path):
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def equal(before, after, field, errors):
    left, right = at(before, field), at(after, field)
    if left is MISSING or right is MISSING:
        side = "both" if left is right else "before" if left is MISSING else "after"
        issue(errors, field, "Required field missing: " + side)
    elif left != right:
        issue(errors, field, "Before/after values differ")


def digest_json(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sha_fields(value, prefix=""):
    found = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = prefix + "." + key if prefix else key
            if key.endswith("sha256"):
                found[path] = item
            else:
                found.update(sha_fields(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(sha_fields(item, "%s[%d]" % (prefix, index)))
    return found


def check_digest(section, label, errors):
    if not isinstance(section, dict) or "sha256" not in section:
        issue(errors, label + ".sha256", "Required digest missing")
        return
    actual = digest_json({key: value for key, value in section.items() if key != "sha256"})
    if actual != section["sha256"]:
        issue(errors, label + ".sha256", "Recorded digest does not match its JSON payload")


def unique_index(items, label, errors):
    result = {}
    if not isinstance(items, list) or not items:
        issue(errors, label, "A non-empty list is required")
        return result
    for item in items:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name:
            issue(errors, label, "Missing or invalid name")
        elif name in result:
            issue(errors, label + "." + name, "Duplicate name")
        else:
            result[name] = item
    return result


def check_report(report, side, errors):
    for key in ("completed", "passed", "source_unchanged"):
        if report.get(key) is not True:
            issue(errors, side + "." + key, "Capture must explicitly report true")
    for key, expected in (("frames", "expected_frames"), ("cases", "expected_cases")):
        if not isinstance(report.get(key), list) or report.get(expected) != len(report[key]):
            issue(errors, side + "." + key, "Actual and declared capture counts differ or are missing")


def check_frame(before, after, errors):
    fields = ("name", "case", "frame_index", "period", "color", "grain", "rough",
              "elapsed_requested", "elapsed_actual", "logical_elapsed", "fake_clock",
              "frame_offset", "dimensions", "running", "particles", "splashes",
              "transfer", "physics.pn", "physics._sn", "physics.ledger", "geometry",
              "radius", "half_width", "outlet_y")
    for field in fields:
        equal(before, after, field, errors)
    for side, frame in (("before", before), ("after", after)):
        for key in ("completed", "passed"):
            if frame.get(key) is not True:
                issue(errors, side + "." + key, "Frame capture is not explicitly complete")
        physics = frame.get("physics", {})
        check_digest(physics, side + ".physics", errors)
        check_digest(frame.get("geometry"), side + ".geometry", errors)
        for key in ("scalars", "effects", "random_sha256", "effect_random_sha256"):
            if key not in physics:
                issue(errors, side + ".physics." + key, "Required physics evidence missing")
        if not HASH_PATTERN.fullmatch(str(physics.get("random_sha256", ""))):
            issue(errors, side + ".physics.random_sha256", "Invalid random-state digest")
        for group, count_key in (("main", "pn"), ("splash", "_sn")):
            arrays = at(physics, "arrays." + group)
            if not isinstance(arrays, dict) or not arrays:
                issue(errors, side + ".physics.arrays." + group, "Particle array digests missing")
                continue
            required_arrays = MAIN_ARRAY_FIELDS if group == "main" else SPLASH_ARRAY_FIELDS
            for name in required_arrays:
                if name not in arrays:
                    issue(errors, side + ".physics.arrays." + group + "." + name,
                          "Required active particle array digest missing")
            for name, array in arrays.items():
                if (not isinstance(array, dict)
                        or array.get("count") != physics.get(count_key)
                        or not HASH_PATTERN.fullmatch(str(array.get("sha256", "")))):
                    issue(errors, side + ".physics.arrays." + group + "." + name,
                          "Invalid active array count or digest")
        if physics.get("pn", MISSING) != frame.get("particles", MISSING):
            issue(errors, side + ".particles", "Particle count disagrees with physics.pn")
        if physics.get("_sn", MISSING) != frame.get("splashes", MISSING):
            issue(errors, side + ".splashes", "Splash count disagrees with physics._sn")
        if physics.get("ledger", MISSING) != frame.get("transfer", MISSING):
            issue(errors, side + ".transfer", "Transfer ledger disagrees with physics.ledger")
    left, right = sha_fields(before.get("physics", {})), sha_fields(after.get("physics", {}))
    if not left or set(left) != set(right):
        issue(errors, "physics.sha256_fields", "Digest field inventory differs or is empty")
    mismatches = [key for key in sorted(set(left) | set(right)) if left.get(key, MISSING) != right.get(key, MISSING)]
    for key in mismatches:
        issue(errors, "physics." + key, "Physics digest differs or is missing")
    return {"digest_fields_compared": len(set(left) & set(right)), "digest_mismatches": mismatches}


def check_candidate_shaders(frame, errors):
    shaders = frame.get("shaders", {})
    for key in ("all_present_shaders_compiled", "mound_surface_shader_success"):
        if shaders.get(key) is not True:
            issue(errors, "shaders." + key, "Candidate shader must explicitly compile")
    contexts = shaders.get("contexts")
    if not isinstance(contexts, list) or not contexts or any(item.get("success") is not True for item in contexts):
        issue(errors, "shaders.contexts", "Compiled shader evidence is absent or contains a failure")
    if shaders.get("landing") != "gpu_surface":
        issue(errors, "shaders.landing", "Candidate landing effect did not use gpu_surface")
    for key in ("raw_mound_canvas_count", "unique_mound_canvas_count", "current_mound_canvas_count"):
        value = shaders.get(key, MISSING)
        if value is MISSING:
            issue(errors, "shaders." + key, "Required raw attachment-count evidence missing")
        elif isinstance(value, bool) or value != 1:
            issue(errors, "shaders." + key, "Expected exactly one attachment/object, got %r" % value)


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + " must be a finite number")
    return float(value)


def scene_geometry(frame, image_size):
    geometry = frame.get("geometry", {})
    required = ("_cx", "_R_inner", "_lower_y_c", "_lower_sand_bot", "mound_cols", "contact_diameter")
    missing = ["geometry." + key for key in required if key not in geometry]
    missing += [key for key in ("half_width", "outlet_y", "dimensions") if key not in frame]
    if missing:
        raise ValueError("Missing mask geometry: " + ", ".join(missing))
    if list(image_size) != frame["dimensions"]:
        raise ValueError("Image size differs from recorded coordinate dimensions; transform is unknown")
    cx, radius = finite(geometry["_cx"], "center"), finite(geometry["_R_inner"], "radius")
    half, outlet = finite(frame["half_width"], "half_width"), finite(frame["outlet_y"], "outlet_y")
    diameter = finite(geometry["contact_diameter"], "contact_diameter")
    floor = finite(geometry["_lower_sand_bot"], "lower_sand_bot")
    center_y = finite(geometry["_lower_y_c"], "lower_y_c")
    if min(radius, half, diameter) <= 0:
        raise ValueError("Non-positive radius, outlet width or contact diameter")
    if "flow_shrink_min" in geometry:
        expected = 2.0 * half * finite(geometry["flow_shrink_min"], "flow_shrink_min")
        if not math.isclose(diameter, expected, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("Recorded contact diameter disagrees with outlet width and shrink factor")
    raw_cols = geometry["mound_cols"]
    if not isinstance(raw_cols, list):
        raise ValueError("mound_cols must be a list of [absolute_x, absolute_y, free, thickness]")
    cols, coincident = [], []
    for row in raw_cols:
        if not isinstance(row, list) or len(row) != 4 or not isinstance(row[2], bool):
            raise ValueError("Invalid mound column structure")
        item = (finite(row[0], "column.x"), finite(row[1], "column.y"), row[2],
                finite(row[3], "column.thickness"))
        if cols and item[0] < cols[-1][0]:
            raise ValueError("Mound columns reverse x order; no sorting is allowed")
        if cols and item[0] == cols[-1][0]:
            previous = cols[-1]
            # cx + dx can round two adjacent knots to the same absolute x.
            # Validate at machine precision, retain both original vertices for
            # mesh reconstruction, and never treat a material-sized step as noise.
            scale = max(1.0, abs(cx), abs(item[0]), abs(item[1]), abs(item[3]),
                        abs(previous[1]), abs(previous[3]))
            budget = 8.0 * math.ulp(scale)
            if (item[2] != previous[2] or abs(item[1] - previous[1]) > budget
                    or abs(item[3] - previous[3]) > budget):
                raise ValueError("Coincident mound x has incompatible surface/free/thickness values")
            coincident.append({"indices": [len(cols) - 1, len(cols)], "x": item[0],
                               "delta_y": item[1] - previous[1],
                               "delta_thickness": item[3] - previous[3],
                               "machine_precision_budget": budget})
        cols.append(item)
    landed = at(frame, "physics.ledger.landed")
    if len(cols) < 2 and landed is not MISSING and finite(landed, "landed") > 0:
        raise ValueError("Landed sand exists but the visible mound boundary is unavailable")
    return {"cx": cx, "radius": radius, "half": half, "outlet": outlet,
            "diameter": diameter, "floor": floor, "center_y": center_y, "cols": cols,
            "coincident_knots": coincident}


def surface_columns(scene, xs):
    if len(scene["cols"]) < 2:
        dx = xs - scene["cx"]
        surface = scene["center_y"] - np.sqrt(np.maximum(0, scene["radius"] ** 2 - dx * dx))
        return surface, np.zeros_like(xs), np.zeros_like(xs, dtype=bool)
    # This interpolation is for the stream floor/metrics only. All original
    # nodes, including zero-width pairs, remain available to the triangle path.
    interpolation = []
    for row in scene["cols"]:
        if interpolation and row[0] == interpolation[-1][0]:
            interpolation[-1] = row
        else:
            interpolation.append(row)
    if len(interpolation) < 2:
        raise ValueError("Insufficient distinct mound x coordinates")
    cols = np.asarray(interpolation, dtype=float)
    surface = np.interp(xs, cols[:, 0], cols[:, 1])
    thickness = np.interp(xs, cols[:, 0], cols[:, 3])
    indices = np.clip(np.searchsorted(cols[:, 0], xs, side="right") - 1, 0, len(cols) - 2)
    free = ((xs >= cols[0, 0]) & (xs <= cols[-1, 0])
            & (cols[indices, 2] != 0) & (cols[indices + 1, 2] != 0))
    return surface, thickness, free


def smoothstep(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def contact_strength(frame):
    scalars = at(frame, "physics.scalars")
    keys = ("_first_impact_clock", "_last_impact_clock", "_mound_flow_clock")
    if not isinstance(scalars, dict) or any(key not in scalars for key in keys):
        raise ValueError("Missing contact timing; landing activity cannot be bounded")
    if scalars["_last_impact_clock"] is None:
        return 0.0
    clock = finite(scalars["_mound_flow_clock"], "mound_clock")
    first = finite(scalars["_first_impact_clock"], "first_impact")
    last = finite(scalars["_last_impact_clock"], "last_impact")
    return smoothstep((clock - first) / 0.08) * (1.0 - smoothstep((clock - last) / 0.2))


def landing_triangles(scene, strength):
    """Reconstruct actual mesh triangles and the changed old surface-band strips.

    MoundSurfaceFlowContext keeps one zero-depth neighbour outside the patch and
    interpolates coverage across triangles. Requiring both endpoints to be free
    discards real wedges where only one endpoint is active.
    """
    triangles, rows = [], []
    if strength <= 0.0:
        return triangles
    cols, cx, diameter = scene["cols"], scene["cx"], scene["diameter"]
    reach = 2.0 * diameter
    for index, (x, y, free, thickness) in enumerate(cols):
        if abs(x - cx) > reach:
            adjacent = ((index > 0 and abs(cols[index - 1][0] - cx) <= reach)
                        or (index + 1 < len(cols) and abs(cols[index + 1][0] - cx) <= reach))
            if not adjacent:
                continue
        u = abs(x - cx) / reach
        weight = 1.0 - smoothstep(u)
        depth = min(thickness, 0.16 * diameter * weight) if free and thickness > 0 and u < 1 else 0.0
        rows.append((index, x, y, depth, bool(free and depth > 0.0)))
    for left, right in zip(rows, rows[1:]):
        vertices = [(left[1], left[2]), (left[1], left[2] - left[3]),
                    (right[1], right[2]), (right[1], right[2] - right[3])]
        coverage = (left[4], left[4], right[4], right[4])
        for indices in ((0, 1, 2), (2, 1, 3)):
            if any(coverage[index] for index in indices):
                triangles.append({"kind": "surface_mesh", "columns": [left[0], right[0]],
                                  "points": [vertices[index] for index in indices]})
    for index, (left, right) in enumerate(zip(cols, cols[1:])):
        x0, y0, free0, thick0 = left
        x1, y1, free1, thick1 = right
        if not (free0 and free1):
            continue
        old_width = min(SURFACE_BAND_DEPTH, thick0, thick1)
        midpoint = abs(0.5 * (x0 + x1) - cx)
        new_width = old_width * (1.0 - strength * (1.0 - smoothstep(midpoint / (2.5 * diameter))))
        if old_width <= 0.0 or old_width <= new_width:
            continue
        # Only the removed strip changes: the upper part of the old band is
        # identical in both arms. Its vertices follow the same two surface knots.
        vertices = [(x0, y0 - old_width), (x1, y1 - old_width),
                    (x1, y1 - new_width), (x0, y0 - new_width)]
        for indices in ((0, 1, 2), (0, 2, 3)):
            triangles.append({"kind": "old_band_removed_strip", "columns": [index, index + 1],
                              "points": [vertices[item] for item in indices]})
    return triangles


def raster_triangle(mask, world_points, margin=RASTER_MARGIN):
    """Pixel-centre coverage plus the unchanged Euclidean 3px raster margin."""
    height, width = mask.shape
    # Kivy uploads vPosition as float32. Keep this rounding distinct from the
    # machine-precision validation of recorded double-precision geometry.
    points = np.asarray(world_points, dtype=np.float32).astype(float)
    points[:, 1] = height - points[:, 1]
    cross = lambda a, b: a[0] * b[1] - a[1] * b[0]
    area = cross(points[1] - points[0], points[2] - points[0])
    if area == 0.0:
        return False  # A zero-width knot pair produces no drawable triangle.
    x0 = max(0, int(math.floor(points[:, 0].min() - margin)))
    x1 = min(width, int(math.ceil(points[:, 0].max() + margin)) + 1)
    y0 = max(0, int(math.floor(points[:, 1].min() - margin)))
    y1 = min(height, int(math.ceil(points[:, 1].max() + margin)) + 1)
    if x0 >= x1 or y0 >= y1:
        return False
    xx, yy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
    positive = np.ones(xx.shape, dtype=bool)
    negative = np.ones(xx.shape, dtype=bool)
    near = np.zeros(xx.shape, dtype=bool)
    for index in range(3):
        a, b = points[index], points[(index + 1) % 3]
        vx, vy = b - a
        edge = vx * (yy - a[1]) - vy * (xx - a[0])
        positive &= edge >= 0.0
        negative &= edge <= 0.0
        length2 = vx * vx + vy * vy
        ratio = np.clip(((xx - a[0]) * vx + (yy - a[1]) * vy) / length2, 0, 1) if length2 else 0.0
        distance2 = (xx - a[0] - ratio * vx) ** 2 + (yy - a[1] - ratio * vy) ** 2
        near |= distance2 <= margin * margin
    mask[y0:y1, x0:x1] |= positive | negative | near
    return True


def raster_line(mask, world_points):
    """The width-one GL_LINES path, plus the same 3px raster margin."""
    height, width = mask.shape
    points = np.asarray(world_points, dtype=np.float32).astype(float)
    points[:, 1] = height - points[:, 1]
    a, b = points
    vx, vy = b - a
    length2 = vx * vx + vy * vy
    if length2 == 0.0:
        return False
    radius = 0.5 + RASTER_MARGIN
    x0 = max(0, int(math.floor(points[:, 0].min() - radius)))
    x1 = min(width, int(math.ceil(points[:, 0].max() + radius)) + 1)
    y0 = max(0, int(math.floor(points[:, 1].min() - radius)))
    y1 = min(height, int(math.ceil(points[:, 1].max() + radius)) + 1)
    if x0 >= x1 or y0 >= y1:
        return False
    xx, yy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
    ratio = np.clip(((xx - a[0]) * vx + (yy - a[1]) * vy) / length2, 0, 1)
    mask[y0:y1, x0:x1] |= (xx - a[0] - ratio * vx) ** 2 + (yy - a[1] - ratio * vy) ** 2 <= radius * radius
    return True


def validate_stream_draw(frame, label, errors):
    """Verify both JSON digests and the recorded float32/index byte digests."""
    draw = frame.get("stream_draw")
    if not isinstance(draw, dict):
        issue(errors, label, "Supplement lacks stream_draw")
        return
    check_digest(draw, label, errors)
    for key, expected in (("renderer", "mesh_endpoint_texture"), ("encoding", "little-endian float32"),
                          ("template_format", ["dx", "dy", "end_selector"]),
                          ("endpoint_format", ["x", "bottom", "top"])):
        if draw.get(key) != expected:
            issue(errors, label + "." + key, "Unsupported stream geometry format")
    batches = draw.get("batches")
    if not isinstance(batches, list) or not batches:
        issue(errors, label + ".batches", "Stream batch inventory is missing")
        return
    active = 0
    for batch_index, batch in enumerate(batches):
        name = "%s.batches[%d]" % (label, batch_index)
        check_digest(batch, name, errors)
        try:
            template = np.asarray(batch["template"], dtype="<f4")
            indices = batch["indices"]
            mode, width = batch["mode"], finite(batch["width"], "batch.width")
            if template.ndim != 2 or template.shape[1] != 3 or not len(template) or not np.isfinite(template).all():
                raise ValueError("Invalid float32 template")
            if mode not in ("lines", "triangles") or width not in (1.0, 2.0):
                raise ValueError("Unsupported primitive mode or width")
            if not isinstance(indices, list) or not indices or any(isinstance(i, bool) or not isinstance(i, int)
                    or i < 0 or i >= len(template) for i in indices):
                raise ValueError("Invalid local template indices")
            if len(indices) % (2 if mode == "lines" else 3):
                raise ValueError("Primitive index list is incomplete")
            if not np.isin(template[:, 2], (0.0, 1.0)).all():
                raise ValueError("Unknown endpoint selector")
            if hashlib.sha256(template.tobytes()).hexdigest() != batch.get("template_bytes_sha256"):
                raise ValueError("Template float32 bytes disagree with digest")
            if hashlib.sha256(np.asarray(indices, dtype="<u2").tobytes()).hexdigest() != batch.get("indices_bytes_sha256"):
                raise ValueError("Template index bytes disagree with digest")
            for part_index, part in enumerate(batch["parts"]):
                part_name = "%s.parts[%d]" % (name, part_index)
                check_digest(part, part_name, errors)
                endpoints = np.asarray(part["active_endpoints"], dtype="<f4").reshape((-1, 3))
                live, capacity = part["live"], part["capacity"]
                if not isinstance(live, int) or not isinstance(capacity, int) or not 0 <= live <= capacity:
                    raise ValueError("Invalid live/capacity count")
                if len(endpoints) != live or not np.isfinite(endpoints).all():
                    raise ValueError("Active endpoint prefix differs from live count")
                if hashlib.sha256(endpoints.tobytes()).hexdigest() != part.get("endpoint_bytes_sha256"):
                    raise ValueError("Active endpoint float32 bytes disagree with digest")
                submitted = part["submitted_index_count"]
                if not isinstance(submitted, int) or submitted % len(indices):
                    raise ValueError("Invalid submitted primitive count")
                slots = submitted // len(indices)
                if not live <= slots <= capacity:
                    raise ValueError("Submitted slots cannot cover the active prefix")
                expected_indices = (np.arange(slots, dtype=np.uint32)[:, None] * len(template)
                                    + np.asarray(indices, dtype=np.uint32)[None, :])
                if expected_indices.size and expected_indices.max() > 65535:
                    raise ValueError("Submitted indices exceed the recorded uint16 format")
                if hashlib.sha256(expected_indices.astype("<u2").tobytes()).hexdigest() != part.get("submitted_indices_sha256"):
                    raise ValueError("Submitted indices are not the captured template/slot topology")
                if (part["buffer_size_bytes"] < live * 12
                        or part["inactive_slots_excluded"] != part["buffer_size_bytes"] // 12 - live):
                    raise ValueError("Inactive/padded slot exclusion is inconsistent")
                active += live
        except (KeyError, ValueError, TypeError) as exc:
            issue(errors, name, str(exc))
    if active != draw.get("active_endpoint_count") or active > frame.get("particles", -1):
        issue(errors, label + ".active_endpoint_count", "Active slots differ from the declared count or exceed physical particles")


def load_stream_evidence(original_before, original_after, before_path, after_path, errors):
    """Bind optional render geometry to the original immutable capture frames."""
    bound, evidence_errors = {}, []
    left = json.loads(before_path.read_text(encoding="utf-8"))
    right = json.loads(after_path.read_text(encoding="utf-8"))
    supplemental = []
    for side, original, report, path in (("before", original_before, left, before_path),
                                         ("after", original_after, right, after_path)):
        check_report(report, "stream_evidence." + side, evidence_errors)
        for field in ("mode", "seed", "front_seed", "clock_epoch", "timing", "dimensions", "source_files", "env"):
            local = []
            equal(original, report, field, local)
            for entry in local:
                issue(evidence_errors, "stream_evidence." + side + "." + entry["field"], entry["message"])
        original_frames = unique_index(original.get("frames"), side + ".frames", evidence_errors)
        frames = unique_index(report.get("frames"), "stream_evidence." + side + ".frames", evidence_errors)
        original_cases = unique_index(original.get("cases"), side + ".cases", evidence_errors)
        cases = unique_index(report.get("cases"), "stream_evidence." + side + ".cases", evidence_errors)
        for name, frame in frames.items():
            if name not in original_frames:
                issue(evidence_errors, "stream_evidence." + side + "." + name, "Supplement frame is absent from the original matrix")
                continue
            local = []
            check_frame(original_frames[name], frame, local)
            equal(original_frames[name], frame, "sha256", local)
            case = frame.get("case")
            if case not in cases or case not in original_cases:
                issue(local, "case", "Supplement case is absent from original matrix")
            else:
                for field in ("request", "effective_config"):
                    equal(original_cases[case], cases[case], field, local)
            read_image(path, frame, local, "screenshot")
            validate_stream_draw(frame, "stream_draw", local)
            for entry in local:
                issue(evidence_errors, "stream_evidence." + side + "." + name + "." + entry["field"], entry["message"])
        supplemental.append(frames)
    if set(supplemental[0]) != set(supplemental[1]):
        issue(evidence_errors, "stream_evidence.frames", "Before/after supplemental frame names differ")
    for name in sorted(set(supplemental[0]) & set(supplemental[1])):
        local = []
        equal(supplemental[0][name], supplemental[1][name], "stream_draw", local)
        for entry in local:
            issue(evidence_errors, "stream_evidence." + name + "." + entry["field"], entry["message"])
    if not evidence_errors:
        for name in supplemental[0]:
            bound[name] = {"draw": supplemental[1][name]["stream_draw"],
                           "before_pixel_sha256": supplemental[0][name]["sha256"],
                           "after_pixel_sha256": supplemental[1][name]["sha256"]}
    errors.extend(evidence_errors)
    metadata = {"before": str(before_path.resolve()), "after": str(after_path.resolve()),
                "before_report_sha256": hashlib.sha256(before_path.read_bytes()).hexdigest(),
                "after_report_sha256": hashlib.sha256(after_path.read_bytes()).hexdigest(),
                "status": "failed" if evidence_errors else "bound", "bound_frames": sorted(bound),
                "binding": "Exact source files, configuration, time, physics, geometry and screenshot hashes; equal verified float32 endpoints/templates/index topology in both arms"}
    return bound, metadata


def stream_contact_geometry(scene, draw, image_size):
    """Only add the demonstrated unchanged stream strokes beside the contact."""
    width, height = image_size
    mask = np.zeros((height, width), dtype=bool)
    selected, primitives = 0, 0
    for batch in draw["batches"]:
        template = np.asarray(batch["template"], dtype=np.float32)
        indices = np.asarray(batch["indices"], dtype=int)
        dx0, dx1, min_dy = float(template[:, 0].min()), float(template[:, 0].max()), float(template[:, 1].min())
        for part in batch["parts"]:
            for endpoint in part["active_endpoints"]:
                x, bottom, top = map(np.float32, endpoint)
                surface = surface_columns(scene, np.asarray([float(x) + dx0, float(x), float(x) + dx1]))[0].max()
                if float(bottom) + min_dy > surface + RASTER_MARGIN:
                    continue
                selected += 1
                points = np.empty((len(template), 2), dtype=np.float32)
                points[:, 0] = x + template[:, 0]
                points[:, 1] = bottom * (np.float32(1) - template[:, 2]) + top * template[:, 2] + template[:, 1]
                stride = 2 if batch["mode"] == "lines" else 3
                for group in indices.reshape((-1, stride)):
                    if stride == 2:
                        primitives += raster_line(mask, points[group])
                    else:
                        primitives += raster_triangle(mask, points[group])
    xs = np.arange(width, dtype=float) + 0.5
    ys = height - (np.arange(height, dtype=float) + 0.5)
    surface = surface_columns(scene, xs)[0]
    guard = ((np.abs(xs[None, :] - scene["cx"]) <= scene["half"] + RASTER_MARGIN)
             & (ys[:, None] <= np.minimum(surface[None, :] + RASTER_MARGIN, scene["outlet"] + RASTER_MARGIN))
             & (ys[:, None] >= scene["floor"] - RASTER_MARGIN))
    guard &= ((xs[None, :] - scene["cx"]) ** 2 + (ys[:, None] - scene["center_y"]) ** 2
              <= (scene["radius"] + RASTER_MARGIN) ** 2)
    mask &= guard
    return mask, {"stream_draw_sha256": draw["sha256"], "selected_contact_endpoints": selected,
                  "rasterized_primitives": primitives, "raster_margin_pixels": RASTER_MARGIN,
                  "method": "Captured float32 template plus active endpoint primitives; clipped to the original column width, below/near the visible contact and inside the lower glass"}


def expected_region(frame, image_size):
    scene = scene_geometry(frame, image_size)
    width, height = image_size
    xs = np.arange(width, dtype=float) + 0.5
    ys = height - (np.arange(height, dtype=float) + 0.5)
    surface, _thickness, _free = surface_columns(scene, xs)
    dx = np.abs(xs - scene["cx"])
    stream = np.zeros((height, width), dtype=bool)
    if at(frame, "physics.pn") is MISSING:
        raise ValueError("Missing physics.pn for active stream boundary")
    if frame["physics"]["pn"] > 0:
        stream = ((dx[None, :] <= scene["half"] + RASTER_MARGIN)
                  & (ys[:, None] <= scene["outlet"] + RASTER_MARGIN)
                  & (ys[:, None] >= surface[None, :] - RASTER_MARGIN))
    strength = contact_strength(frame)
    contact_active = strength > 0.0
    landing = np.zeros_like(stream)
    triangles = landing_triangles(scene, strength)
    drawn_triangles = 0
    if contact_active:
        for triangle in triangles:
            drawn_triangles += raster_triangle(landing, triangle["points"])
        # Keep the original maximum horizontal budget and glass constraint.
        # Triangle interpolation must not become a new broad exemption.
        landing &= dx[None, :] <= 2.5 * scene["diameter"] + RASTER_MARGIN
        glass = ((xs[None, :] - scene["cx"]) ** 2
                 + (ys[:, None] - scene["center_y"]) ** 2 <= (scene["radius"] + RASTER_MARGIN) ** 2)
        landing &= glass
    return stream | landing, scene, {"stream_pixels": int(stream.sum()), "landing_pixels": int(landing.sum()),
        "contact_active": bool(contact_active), "contact_diameter": scene["diameter"],
        "raster_margin_pixels": RASTER_MARGIN, "landing_reach_diameters": 2.0,
        "legacy_surface_band_reach_diameters": 2.5, "landing_depth_diameters": 0.16,
        "contact_strength": strength, "drawn_local_triangles": drawn_triangles,
        "local_shape_method": "Actual float32 mesh topology and changed old-band strips, with Euclidean 3px raster margin",
        "coincident_knots": scene["coincident_knots"]}


def bbox(mask):
    rows, cols = np.nonzero(mask)
    if not len(rows):
        return None
    return [int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1]


def longest_run(values):
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def stream_statistics(image, frame, scene):
    if frame.get("particles", 0) <= 0:
        return {"status": "not_applicable", "reason": "No live stream particles"}
    if frame.get("running") is False:
        return {"status": "not_applicable", "reason": "Paused tint changes the cavity background; use running frames for grain coverage"}
    geometry = frame["geometry"]
    if "glass_fill_rgb" not in geometry or geometry.get("glass_fill_rgb_scale") != "0..1":
        return {"status": "pending", "reason": "Missing explicit 0..1 cavity background RGB"}
    background = np.asarray(geometry["glass_fill_rgb"], dtype=float)
    if background.shape != (3,) or not np.isfinite(background).all() or (background < 0).any() or (background > 1).any():
        return {"status": "pending", "reason": "Invalid cavity background RGB"}
    height, width = image.shape[:2]
    surface = surface_columns(scene, np.asarray([scene["cx"]]))[0][0]
    top = scene["outlet"] - max(3.0, 4.0 * scene["half"])
    bottom = surface + max(3.0, 0.2 * scene["diameter"])
    x0 = max(0, int(math.ceil(scene["cx"] - 0.70 * scene["half"])))
    x1 = min(width, int(math.floor(scene["cx"] + 0.70 * scene["half"])))
    y0 = max(0, int(math.ceil(height - top)))
    y1 = min(height, int(math.floor(height - bottom)))
    if x1 - x0 < 2 or y1 - y0 < 4:
        return {"status": "not_applicable", "reason": "No adequate free-stream interval outside both junctions"}
    pixels = image[y0:y1, x0:x1, :3].astype(float)
    distance = np.linalg.norm(pixels - background * 255.0, axis=2)
    thresholds = []
    for threshold in COLOR_DISTANCES:
        sand = distance > threshold
        empty_rows = ~sand.any(axis=1)
        thresholds.append({"rgb_distance": threshold, "coverage": float(sand.mean()),
                           "full_width_empty_rows": int(empty_rows.sum()),
                           "longest_full_width_empty_run": longest_run(empty_rows)})
    gray = pixels @ np.asarray([0.299, 0.587, 0.114])
    vertical = float(np.abs(np.diff(gray, axis=0)).mean())
    horizontal = float(np.abs(np.diff(gray, axis=1)).mean())
    return {"status": "reported", "roi": [x0, y0, x1, y1], "rgb_distance_unit": "Euclidean distance in 8-bit RGB",
            "coverage_sensitivity": thresholds, "mean_abs_vertical_gradient": vertical,
            "mean_abs_horizontal_gradient": horizontal,
            "vertical_to_horizontal_gradient_ratio": vertical / horizontal if horizontal > 0 else None,
            "note": "Descriptive only. Lower dy/dx may accompany long vertical strokes; holes and contrast also affect it. No acceptance threshold is inferred."}


def read_image(report_path, frame, errors, side):
    name, expected = frame.get("file"), frame.get("sha256")
    if not isinstance(name, str) or not name:
        issue(errors, side + ".file", "Screenshot path missing")
        return None
    path = (report_path.parent / name).resolve()
    if not path.is_relative_to(report_path.parent.resolve()):
        issue(errors, side + ".file", "Screenshot path escapes its evidence directory")
        return None
    if not path.is_file():
        issue(errors, side + ".file", "Screenshot file missing: " + name)
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        issue(errors, side + ".sha256", "Screenshot bytes do not match the captured digest")
    with Image.open(path) as image:
        pixels = np.asarray(image.convert("RGBA")).copy()
    return pixels


def compare_frame(before_path, after_path, before, after, output, index, stream_evidence=None):
    errors, pending = [], []
    invariants = check_frame(before, after, errors)
    check_candidate_shaders(after, errors)
    row = {"name": before["name"], "case": before.get("case"), "errors": errors,
           "pending": pending, "physics": invariants}
    left = read_image(before_path, before, errors, "before")
    right = read_image(after_path, after, errors, "after")
    if left is not None and right is not None:
        if left.shape != right.shape:
            issue(errors, "images", "Screenshot dimensions differ")
        else:
            delta = np.abs(right.astype(np.int16) - left.astype(np.int16))
            changed = np.any(delta != 0, axis=2)
            pixel = {"changed_pixels": int(changed.sum()), "raw_diff_bbox": bbox(changed),
                     "max_channel_delta": int(delta.max()), "mean_absolute_channel_delta": float(delta.mean()),
                     "outside_expected_pixels": None, "outside_expected_bbox": None}
            row["pixels"] = pixel
            stem = "%03d-%s" % (index, re.sub(r"[^A-Za-z0-9_.-]", "_", before["name"]))
            Image.fromarray(delta[:, :, :3].astype(np.uint8), "RGB").save(output / (stem + "-diff-raw.png"))
            row["raw_diff_image"] = stem + "-diff-raw.png"
            if np.any(delta[:, :, 3]):
                Image.fromarray(delta[:, :, 3].astype(np.uint8), "L").save(output / (stem + "-alpha-diff.png"))
                row["alpha_diff_image"] = stem + "-alpha-diff.png"
            try:
                mask, scene, region = expected_region(after, (right.shape[1], right.shape[0]))
                if stream_evidence is not None:
                    extra, extra_info = stream_contact_geometry(scene, stream_evidence["draw"], (right.shape[1], right.shape[0]))
                    explained = changed & extra & ~mask
                    extra_info["newly_explained_changed_pixels"] = int(explained.sum())
                    extra_info["newly_explained_changed_bbox"] = bbox(explained)
                    if explained.sum() <= 100:
                        extra_info["newly_explained_changed_coordinates"] = [[int(x), int(y)] for y, x in zip(*np.nonzero(explained))]
                    extra_info["extra_geometry_pixels"] = int((extra & ~mask).sum())
                    mask |= extra
                    region["supplemental_stream_geometry"] = extra_info
                    row["stream_evidence"] = {key: value for key, value in stream_evidence.items() if key != "draw"}
                    Image.fromarray(extra.astype(np.uint8) * 255, "L").save(output / (stem + "-stream-contact-mask.png"))
                    row["stream_contact_mask_image"] = stem + "-stream-contact-mask.png"
                pixel["expected_region"] = region
                outside = changed & ~mask
                pixel["outside_expected_pixels"] = int(outside.sum())
                pixel["outside_expected_bbox"] = bbox(outside)
                if outside.any():
                    issue(errors, "pixels.outside_expected", "%d changed pixels outside the declared local regions" % int(outside.sum()))
                Image.fromarray(mask.astype(np.uint8) * 255, "L").save(output / (stem + "-expected-mask.png"))
                row["expected_mask_image"] = stem + "-expected-mask.png"
                stats = {"before": stream_statistics(left, before, scene),
                         "after": stream_statistics(right, after, scene)}
                row["stream_statistics"] = stats
                for side, result in stats.items():
                    if result["status"] == "pending":
                        issue(pending, "stream_statistics." + side, result["reason"])
            except (ValueError, KeyError, TypeError) as exc:
                issue(pending, "pixels.expected_region", str(exc))
    row["status"] = "failed" if errors else "pending" if pending else "passed"
    return row


def compare(before_path, after_path, output, stream_before=None, stream_after=None):
    output.mkdir(parents=True, exist_ok=True)
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    errors = []
    check_report(before, "before", errors)
    check_report(after, "after", errors)
    for field in ("mode", "seed", "front_seed", "clock_epoch", "timing", "dimensions"):
        equal(before, after, field, errors)
    left_cases = unique_index(before.get("cases"), "before.cases", errors)
    right_cases = unique_index(after.get("cases"), "after.cases", errors)
    if set(left_cases) != set(right_cases):
        issue(errors, "cases.names", "Before/after case names differ")
    for name in sorted(set(left_cases) & set(right_cases)):
        case_errors = []
        for field in ("request", "effective_config", "frames"):
            equal(left_cases[name], right_cases[name], field, case_errors)
        for entry in case_errors:
            issue(errors, "cases." + name + "." + entry["field"], entry["message"])
    left_frames = unique_index(before.get("frames"), "before.frames", errors)
    right_frames = unique_index(after.get("frames"), "after.frames", errors)
    if set(left_frames) != set(right_frames):
        issue(errors, "frames.names", "Before/after frame names differ")
    bound, stream_metadata = {}, None
    if stream_before is not None and stream_after is not None:
        bound, stream_metadata = load_stream_evidence(before, after, stream_before, stream_after, errors)
    rows = []
    for index, (name, frame) in enumerate(left_frames.items()):
        if name in right_frames:
            rows.append(compare_frame(before_path, after_path, frame, right_frames[name], output, index, bound.get(name)))
    status = ("failed" if errors or any(row["status"] == "failed" for row in rows)
              else "pending" if any(row["status"] == "pending" for row in rows) else "passed")
    report = {"status": status, "completed": True, "passed": status == "passed",
              "before": str(before_path.resolve()), "after": str(after_path.resolve()),
              "before_report_sha256": hashlib.sha256(before_path.read_bytes()).hexdigest(),
              "after_report_sha256": hashlib.sha256(after_path.read_bytes()).hexdigest(),
              "scope": "Exact saved-state equality and local pixel-change containment; appearance still requires visual review. No audio/performance/device acceptance.",
              "coordinates": "Image bboxes are top-left origin, exclusive right/bottom. Captured geometry is bottom-left Kivy coordinates.",
              "errors": errors, "frames_compared": len(rows), "frames": rows,
              "stream_evidence": stream_metadata,
              "counts": {key: sum(row["status"] == key for row in rows) for key in ("passed", "pending", "failed")}}
    path = output / "comparison.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="Directory for comparison.json, raw differences and explicit region masks")
    parser.add_argument("--stream-evidence-before", type=Path, help="Optional exact baseline replay containing captured stream_draw geometry")
    parser.add_argument("--stream-evidence-after", type=Path, help="Matching exact candidate replay; both evidence arguments are required together")
    args = parser.parse_args()
    if (args.stream_evidence_before is None) != (args.stream_evidence_after is None):
        parser.error("Both --stream-evidence-before and --stream-evidence-after are required together")
    try:
        report, path = compare(args.before.resolve(), args.after.resolve(), args.out.resolve(),
                               args.stream_evidence_before.resolve() if args.stream_evidence_before else None,
                               args.stream_evidence_after.resolve() if args.stream_evidence_after else None)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, "Material comparison failed: %s\n" % exc)
    print("MATERIAL_COMPARISON %s frames=%d %s" % (report["status"], report["frames_compared"], path))
    return 1 if report["status"] == "failed" else 2 if report["status"] == "pending" else 0


if __name__ == "__main__":
    raise SystemExit(main())
