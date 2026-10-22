"""Measure actual GL pixels, including the lower stream and live contact surface."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def longest_run(values):
    best = run = 0
    for value in values:
        run = run + 1 if value else 0
        best = max(best, run)
    return best


def metrics(image, frame):
    g = frame["geometry"]
    h, w = image.shape[:2]
    cx, half = g["_cx"], frame["half_width"]
    top = frame["outlet_y"] - half * 3.5
    bottom = g["mound_top_y"] + half * 0.3
    x0, x1 = max(0, int(cx - half * .8)), min(w, int(cx + half * .8))
    y0, y1 = max(0, int(h - top)), min(h, int(h - bottom))
    if y1 - y0 < 6 or x1 - x0 < 3:
        return {"applicable": False}
    roi = image[y0:y1, x0:x1, :3].astype(float)
    background = np.array(g["glass_fill_rgb"]) * 255
    holes = np.linalg.norm(roi - background, axis=2) < 8
    # Unlike the old empty-row count, one thread cannot make a sparse row pass.
    row_coverage = 1 - holes.mean(axis=1)
    pitch = frame.get("stream_draw", {}).get("pitch", max(1, g["_R_inner"] / 150 * 1.33))
    # Sample each contact column against its actual interpolated mound height.
    cols = np.asarray(g["mound_cols"])
    contact_holes = []
    if len(cols):
        for x in range(x0, x1):
            sy = np.interp(x, cols[:, 0], cols[:, 1])
            yy = int(h - sy)
            strip = image[max(0, yy - int(2 * pitch)):min(h, yy + 1), x, :3]
            contact_holes.append(longest_run(np.linalg.norm(strip - background, axis=1) < 8))
    return {"applicable": True, "roi": [x0, y0, x1, y1], "pitch": pitch,
            "coverage": float(1 - holes.mean()),
            "worst_row_coverage": float(row_coverage.min()),
            "long_sparse_band": longest_run(row_coverage < .65),
            "longest_vertical_hole": max(longest_run(col) for col in holes.T),
            "max_contact_hole": max(contact_holes, default=0)}


def meets_short_grain_goal(row):
    return (row.get("applicable") is True
            and row["coverage"] >= .90
            and row["worst_row_coverage"] >= .65
            and row["longest_vertical_hole"] <= 3 * row["pitch"]
            and row["long_sparse_band"] == 0
            and row["max_contact_hole"] <= 3 * row["pitch"])


def inspect(report_path):
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = []
    for frame in report["frames"]:
        if not (frame["name"].startswith(("middle-", "early-", "color"))
                and frame["running"] and frame["splashes"]):
            continue
        im = np.asarray(Image.open(report_path.parent / frame["file"]).convert("RGB"))
        row = metrics(im, frame)
        rows.append(dict(row, name=frame["name"], passed=meets_short_grain_goal(row)))
    return {"samples": rows, "passed": bool(rows) and all(row["passed"] for row in rows),
            "note": "Pixel checks supplement full-cycle visual review; they cannot certify aesthetics."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.report)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("SHORT_GRAIN_PIXELS samples=%d passed=%s" % (len(result["samples"]), result["passed"]))
    raise SystemExit(0 if result["passed"] else 1)
