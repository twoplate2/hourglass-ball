"""Offline comparison of matched MuMu frames; never runs the app or rewrites a baseline."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

import numpy as np
from PIL import Image, ImageDraw


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def performance(root, output):
    runs = []
    for request_path in sorted(root.glob("*/request.json")):
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if request["mode"] != "benchmark":
            continue
        path = request_path.parent / "device/result.json"
        if not path.is_file():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("exception") or result.get("cancelled") or len(result.get("results", [])) != 4:
            raise ValueError("Incomplete benchmark: " + str(path))
        label = request["id"].split("-", 2)[2]
        if "-r" not in label:
            continue
        round_number = int(label.rsplit("-r", 1)[1])
        runs.append({"arm": request["arm"], "round": round_number, "path": str(path),
                     "sha256": digest(path), "settle_timeouts": result["settle_timeouts"],
                     "periods": result["results"]})
    rows = []
    for period in (1, 5, 15, 50):
        paired = []
        rounds = sorted({run["round"] for run in runs})
        for round_number in rounds:
            pair = {}
            for run in runs:
                if run["round"] == round_number:
                    if run["arm"] in pair:
                        raise ValueError("Duplicate benchmark round")
                    pair[run["arm"]] = next(row for row in run["periods"] if row["period"] == period)
            if set(pair) != {"current", "apk296"}:
                raise ValueError("Missing paired benchmark round")
            a, b = pair["apk296"], pair["current"]
            for key in ("window_pixels", "refresh_hz", "flow_rate", "splash_max", "maxfps",
                        "vsync", "warm", "blit_rep", "blit_wide"):
                if a["environment"].get(key) != b["environment"].get(key):
                    raise ValueError("Incomparable benchmark environment: " + key)
            if a["flow_renderer"] != b["flow_renderer"]:
                raise ValueError("Incomparable renderers")
            paired.append({
                "round": round_number,
                "before_low_fps": a["one_percent_low_fps"],
                "after_low_fps": b["one_percent_low_fps"],
                "low_delta_fps": b["one_percent_low_fps"] - a["one_percent_low_fps"],
                "before_max_ms": max(a["slowest_five_ms"]),
                "after_max_ms": max(b["slowest_five_ms"]),
                "max_delta_ms": max(b["slowest_five_ms"]) - max(a["slowest_five_ms"]),
                "before_draw_ms": a["stage_mean_ms"]["update_draw_ms"],
                "after_draw_ms": b["stage_mean_ms"]["update_draw_ms"],
                "before_canvas_ms": a["stage_mean_ms"]["canvas_ms"],
                "after_canvas_ms": b["stage_mean_ms"]["canvas_ms"],
            })
        rows.append({"period": period, "pairs": paired,
                     "median_low_delta_fps": statistics.median(r["low_delta_fps"] for r in paired),
                     "median_max_delta_ms": statistics.median(r["max_delta_ms"] for r in paired)})
    summary = {"runs": [{k: v for k, v in run.items() if k != "periods"} for run in runs],
               "periods": rows,
               "limitations": ["Three pairs indicate direction, not a conclusive performance gate.",
                               "MuMu results do not establish real-device refresh-rate performance.",
                               "Settling timeouts remain failures of the settling precondition."]}
    output.mkdir(parents=True, exist_ok=False)
    (output / "performance.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for row in rows:
        print("PERF period=%s pairs=%s median_low_delta=%+.2f median_max_delta=%+.2fms" %
              (row["period"], len(row["pairs"]), row["median_low_delta_fps"],
               row["median_max_delta_ms"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--bench-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.bench_root:
        performance(args.bench_root, args.output)
        return
    if not args.before or not args.after:
        parser.error("Frame comparison requires both --before and --after")
    args.output.mkdir(parents=True, exist_ok=False)
    before_path, after_path = args.before / "result.json", args.after / "result.json"
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    originals = {frame["name"]: frame for frame in before["frames"]}
    if set(originals) != {frame["name"] for frame in after["frames"]}:
        raise ValueError("The two matrix runs do not cover the same cases")
    rows = []
    for current in after["frames"]:
        previous = originals[current["name"]]
        for key in ("period", "color", "elapsed_actual", "dimensions", "renderer",
                    "radius", "half_width", "outlet_y"):
            if previous[key] != current[key]:
                raise ValueError("Incomparable %s in %s" % (key, current["name"]))
        a_path, b_path = args.before / previous["file"], args.after / current["file"]
        if digest(a_path) != previous["sha256"] or digest(b_path) != current["sha256"]:
            raise ValueError("Frame changed after collection")
        with Image.open(a_path) as im:
            a = np.asarray(im.convert("RGB"))
        with Image.open(b_path) as im:
            b = np.asarray(im.convert("RGB"))
        height, width = a.shape[:2]
        if a.shape != b.shape or [width, height] != current["dimensions"]:
            raise ValueError("Incorrect matrix image dimensions")
        changed = np.any(a != b, axis=2)
        expected = np.zeros(changed.shape, dtype=bool)
        half = math.ceil(current["half_width"]) + 4
        cx = width // 2
        top = max(0, math.floor(height - current["outlet_y"] - 2 * half - 4))
        bottom = min(height, math.ceil(height - current["outlet_y"]
                                       + 2 * current["radius"] + 4 * half))
        expected[top:bottom, cx - half:cx + half + 1] = True
        # Version glyphs differ by design. Do not mask either bulb or the controls.
        expected[math.floor(height * 0.93):, math.floor(width * 0.44):math.ceil(width * 0.60)] = True
        outside = changed & ~expected
        row = {"name": current["name"], "period": current["period"],
               "color": current["color"], "elapsed": current["elapsed_actual"],
               "particles_before": previous["particles"], "particles_after": current["particles"],
               "ledger_same": previous["transfer"] == current["transfer"],
               "changed_pixels": int(changed.sum()), "outside_expected_pixels": int(outside.sum()),
               "expected_stream_box": [cx - half, top, cx + half + 1, bottom],
               "before_sha256": previous["sha256"], "after_sha256": current["sha256"]}
        rows.append(row)
    report = {
        "before_result_sha256": digest(before_path), "after_result_sha256": digest(after_path),
        "frames": rows, "cases": len(rows),
        "same_particle_counts": all(r["particles_before"] == r["particles_after"] for r in rows),
        "same_sand_ledgers": all(r["ledger_same"] for r in rows),
        "outside_expected_pixels": sum(r["outside_expected_pixels"] for r in rows),
        "limitations": [
            "Matched fixed-step screenshots, not real-time dynamics or performance.",
            "The expected rectangle covers the nominal stream width, neck overlap and impact area.",
            "Zero differences outside the mask do not certify the expected region is correct.",
            "No thresholds or historical reference images were changed."
        ]
    }
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    names = ["p50-stage2", "p50-stage4", "p50-stage5", "p50-stage6",
             "color2-t12", "color5-t450"]
    pairs = Image.new("RGB", (400, len(names) * 330), "white")
    draw = ImageDraw.Draw(pairs)
    for i, name in enumerate(names):
        for j, folder in enumerate((args.before, args.after)):
            frame = originals[name] if j == 0 else next(r for r in after["frames"] if r["name"] == name)
            w, h = frame["dimensions"]
            half = max(50, math.ceil(frame["half_width"]) + 20)
            top = math.floor(h - frame["outlet_y"] - 70)
            bottom = min(h, top + 300)
            with Image.open(folder / frame["file"]) as image:
                crop = image.crop((w // 2 - half, top, w // 2 + half, bottom))
                pairs.paste(crop, (j * 200 + (200 - crop.width) // 2, i * 330 + 25))
            draw.text((j * 200 + 4, i * 330 + 5),
                      ("APK 2.96" if j == 0 else "source 2.97") + " | " + name, fill="black")
    pairs.save(args.output / "before-after.png")

    frames = [r for r in after["frames"] if r["name"].startswith("long64-")]
    sheet = Image.new("RGB", (140 * len(frames), 430), "white")
    draw = ImageDraw.Draw(sheet)
    for i, frame in enumerate(frames):
        w, h = frame["dimensions"]
        top = math.floor(h - frame["outlet_y"] - 10)
        with Image.open(args.after / frame["file"]) as image:
            crop = image.crop((w // 2 - 70, top, w // 2 + 70, top + 400))
            sheet.paste(crop, (i * 140, 30))
        draw.text((i * 140 + 4, 7), "%.2fs" % frame["elapsed_actual"], fill="black")
    sheet.save(args.output / "long64-contact.png")
    print("MUMU_COMPARE cases=%s counts_equal=%s ledger_equal=%s unexpected_pixels=%s" %
          (report["cases"], report["same_particle_counts"], report["same_sand_ledgers"],
           report["outside_expected_pixels"]))


if __name__ == "__main__":
    main()
