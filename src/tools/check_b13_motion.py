"""Check B13's 540 x 924 gold reference windows in real PC captures.

Use capture_stream_motion.py at 30 fps, grain 3. Capture 0–5.5 s of the
5-second timer and 2.5–10.5 s of the 50-second timer. An optional full
20-second capture compares first impact between timers. Use --original-b13
to reproduce the earlier B13 onset checks.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def capture(folder, period, first, last):
    report = json.loads((folder / "motion.json").read_text(encoding="utf-8"))
    if (not report["completed"] or report["period"] != period or report["sample_rate"] != 30
            or report["dimensions"] != [540, 924] or report["color"] != 0 or report["grain"] != 3):
        raise ValueError(f"Unexpected capture configuration in {folder}")
    frames = {round(f["time"] * 30): f for f in report["frames"]}
    for i in range(first, last + 1):
        frame = frames[i]
        if abs(frame["time"] - i/30) > 1e-6 or frame["native_hidden"]["IsWindowVisible"]:
            raise ValueError(f"Unexpected time or visible window in {frame['file']}")
    return frames


def pixels(folder, frame, box):
    with Image.open(folder / frame["file"]) as image:
        return np.asarray(image.convert("RGB").crop(box), dtype=np.float32)


def measure(five, fifty):
    short = capture(five, 5, 0, 165)
    stream = capture(fifty, 50, 75, 315)
    floor = short[0]["geometry"]["surface_y"]
    heights = [round(floor - short[i]["geometry"]["surface_y"], 1) for i in (45, 46, 47, 48, 55)]
    # Inspect the exposed gap only. The last settled frame supplies the actual
    # glass background so sloping mound pixels cannot count as hanging grains.
    g = short[153]["geometry"]
    x, y = round(g["cx"]), round((g["outlet_y"] + g["surface_y"]) / 2)
    box = (x-32, y-1, x+33, y+2)
    diff = np.max(np.abs(pixels(five, short[153], box) - pixels(five, short[165], box)), axis=2)
    tail = int(np.median((diff > 40).sum(axis=1)))

    g = stream[75]["geometry"]
    x, y, half = round(g["cx"]), round(g["outlet_y"]), g["half_width"]
    box = (x-22, y+40, x+23, y+115)
    luma = [pixels(fifty, stream[i], box) @ [.2126, .7152, .0722] for i in range(75, 316)]
    jump = np.abs(np.diff(luma, axis=0)) >= 20.
    dx = np.abs(np.arange(-22, 23))
    core = 100. * jump[:, :, dx <= .30*half].mean()
    rim = 100. * jump[:, :, (dx >= .67*half) & (dx <= half)].mean()
    return heights, tail, float(core), float(rim)


def landing(folder, period):
    """Locate real gold at the floor, then measure the first slope and the draining lip."""
    frames = capture(folder, period, 0, round(period * 30) + 6)
    g = frames[0]["geometry"]
    cx, floor, lip = (round(g[k]) for k in ("cx", "surface_y", "outlet_y"))
    half, radius = g["half_width"], g["radius"]
    floor_box = (cx-6, floor-5, cx+7, floor-1)
    start = next(i for i in range(1, min(110, len(frames)))
                 if gold_columns(folder, frames[i], floor_box, 2).sum() >= 3)
    first = frames[start]
    early = frames[start+6]  # 0.20 seconds after first visible contact.
    base = (round(cx-radius), round(floor-.45*radius), round(cx+radius), floor+1)
    width = float(gold_columns(folder, early, base, 4).sum()) / (2. * radius)
    growth = g["surface_y"] - early["geometry"]["surface_y"]
    mouth = (int(cx-half), lip-2, int(cx+half)+1, lip+1)
    widths = [gold_columns(folder, frames[i], mouth, 2).sum() / (2. * half)
              for i in range(round(period * 30) - 10, round(period * 30) + 1)]
    if not (1.80 <= first["time"] <= 2.20 and 0. < growth < half * 1.60 and .08 < width < .48):
        raise ValueError(f"{period:g}s landing at {first['time']:.3f}s, "
                         f"rise={growth:.1f}px, footprint={width:.1%} of the chamber")
    if sum(.03 < w < .67 for w in widths) < 2:
        raise ValueError(f"{period:g}s packed lip does not visibly narrow: {widths}")
    return first["time"], growth, width


def gold_columns(folder, frame, box, min_rows):
    rgb = pixels(folder, frame, box)
    gold = (rgb[:, :, 0] - rgb[:, :, 2] > 65.) & (rgb[:, :, 0] - rgb[:, :, 1] > 25.)
    return gold.sum(axis=0) >= min_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("five", type=Path)
    parser.add_argument("fifty", type=Path)
    parser.add_argument("--twenty", type=Path, help="Full 20 s timer at 30 fps, including its tail")
    parser.add_argument("--original-b13", action="store_true", help="Check the historic 3.31–3.33 timing")
    args = parser.parse_args()
    heights, tail, core, rim = measure(args.five, args.fifty)
    print(f"Mid-gap width at 5.10 s {tail} px; "
          f"50 s core/rim luma jumps {core:.2f}% / {rim:.2f}%")
    errors = []
    if args.original_b13:
        print("Original B13 pile heights:", heights)
        if not (heights[0] <= 2 and 5 <= heights[1] < heights[2] < heights[3] <= 65
                and 20 <= heights[2] <= 50 and 130 <= heights[4] <= 160):
            errors.append("First pile does not follow the original B13 contact window")
    else:
        short = landing(args.five, 5)
        print("5 s contact, rise and footprint after 0.20 s:", short)
        if args.twenty:
            long = landing(args.twenty, 20)
            print("20 s contact, rise and footprint after 0.20 s:", long)
            if abs(long[0] - short[0]) > .075:
                errors.append("Changing the timer changed the first flight time")
    if not 1 <= tail <= 18:
        errors.append("Last grains disappeared early or form a broad plug")
    if core > 4 or not 20 <= rim <= 45:
        errors.append("The core flickers or the moving B13 fringe was lost")
    if errors:
        parser.exit(1, "\n".join(errors) + "\n")


if __name__ == "__main__":
    main()
