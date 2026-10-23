"""Offline NS03 colour/texture measurements; these never approve appearance.

Use a capture_stream_motion motion.json to measure every original lossless frame.
With --overview, convert its first-frame geometry back through crop_box to the
full-size overview.png. No resize, smoothing or pixel synthesis is performed.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def measure(image, geometry):
    """Report the inner strip on each side of an image-space outlet (y down).

    The strip avoids glass and stream edges. RGB differences include real pore
    coverage; horizontal neighbour differences describe visible fine texture.
    They are diagnostics, not proof of a seam, speed, or temporal continuity.
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.float64)
    cx, outlet, half = (float(geometry[k]) for k in ("cx", "outlet_y", "half_width"))
    if not all(math.isfinite(x) for x in (cx, outlet, half)) or half <= 0:
        raise ValueError("Finite outlet geometry with positive half_width is required")
    width = 2 * half
    left, right = math.floor(cx - .55 * half), math.ceil(cx + .55 * half)
    band = max(2, math.ceil(.15 * width))
    reach = max(band, math.ceil(.75 * width))
    top, bottom = math.floor(outlet - reach), math.ceil(outlet + reach)
    if left < 0 or right > rgb.shape[1] or right-left < 2 or top < 0 or bottom > rgb.shape[0]:
        raise ValueError("Image does not contain the required uncropped inner outlet strip")
    strip = rgb[:, left:right]

    def stats(start, end):
        samples = strip[start:end]
        return (samples.mean(axis=(0, 1)), float(np.abs(np.diff(samples, axis=1)).mean()))

    def boundary(y):
        above, texture_above = stats(y-band, y)
        below, texture_below = stats(y, y+band)
        return dict(boundary_y=y, offset_in_widths=(y-outlet)/width,
                    rgb_above=above.tolist(), rgb_below=below.tolist(),
                    rgb_delta_255=float(np.linalg.norm(below-above)),
                    horizontal_texture_above_255=texture_above,
                    horizontal_texture_below_255=texture_below,
                    texture_ratio_below_over_above=(texture_below/texture_above
                                                   if texture_above > 1e-12 else None))

    boundaries = [boundary(y) for y in range(top+band, bottom-band+1)]
    near = [row for row in boundaries if abs(row["offset_in_widths"]) <= .2]
    if not near:
        near = [min(boundaries, key=lambda row:abs(row["offset_in_widths"]))]
    luma = strip[top:bottom] @ np.asarray([.2126, .7152, .0722])
    return dict(inner_strip=[left, top, right, bottom], band_pixels=band,
                outlet_y=outlet, neck_width_pixels=width,
                at_outlet=boundary(round(outlet)),
                max_near_outlet_rgb=max(near, key=lambda row:row["rgb_delta_255"]),
                boundaries=boundaries,
                rows=[dict(y=top+i, luma_mean_255=float(luma[i].mean()),
                           horizontal_texture_255=float(np.abs(np.diff(strip[top+i],axis=0)).mean()))
                      for i in range(bottom-top)])


def from_capture(report_path, overview=False):
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (not report.get("completed") or not report.get("frames") or not report.get("source_files")
            or report.get("source_files") != report.get("source_files_end")):
        raise ValueError("Capture must be complete with unchanged source identity")
    frames = report["frames"][:1] if overview else report["frames"]
    values = []
    for frame in frames:
        geometry = dict(frame["geometry"])
        name = "overview.png" if overview else frame["file"]
        path = report_path.parent / name
        if overview and "crop_box" in report:
            geometry["cx"] += report["crop_box"][0]
            geometry["outlet_y"] += report["crop_box"][1]
            if "surface_y" in geometry:
                geometry["surface_y"] += report["crop_box"][1]
        elif not overview and frame.get("sha256") != file_hash(path):
            raise ValueError("Captured frame hash differs: " + name)
        with Image.open(path) as image:
            expected = (report.get("canvas_dimensions", report["dimensions"])
                        if overview else report["dimensions"])
            if list(image.size) != expected:
                raise ValueError("Image dimensions differ from capture report: " + name)
            measured = measure(image, geometry)
            dimensions = list(image.size)
        values.append(dict(file=str(path.resolve()), sha256=file_hash(path),
                           dimensions=dimensions, time=frame["time"],
                           geometry=geometry, measurement=measured))
    return dict(schema=1, status="diagnostic_only", visual_verdict=None,
                note="Colour and texture statistics do not establish appearance, speed or continuity.",
                report_path=str(report_path.resolve()), report_sha256=file_hash(report_path),
                source_files=report["source_files"], overview=overview, frames=values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--motion", type=Path, required=True)
    parser.add_argument("--overview", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = from_capture(args.motion, args.overview)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("NS03_DIAGNOSTIC", len(result["frames"]), args.output)


if __name__ == "__main__":
    main()
