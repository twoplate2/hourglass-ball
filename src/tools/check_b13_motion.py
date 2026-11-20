"""Check B13's 540 x 924 gold reference windows in real PC captures.

Use capture_stream_motion.py at 30 fps, grain 3. Capture 0–5.5 s of the
5-second timer and 2.5–10.5 s of the 50-second timer. Pass those directories
in that order. Numbers describe these reference shots only.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("five", type=Path)
    parser.add_argument("fifty", type=Path)
    args = parser.parse_args()
    heights, tail, core, rim = measure(args.five, args.fifty)
    print(f"Pile heights {heights} px; mid-gap width at 5.10 s {tail} px; "
          f"50 s core/rim luma jumps {core:.2f}% / {rim:.2f}%")
    errors = []
    if not (heights[0] <= 2 and 5 <= heights[1] < heights[2] < heights[3] <= 65
            and 20 <= heights[2] <= 50 and 130 <= heights[4] <= 160):
        errors.append("First pile does not follow the B13 contact window")
    if not 1 <= tail <= 18:
        errors.append("Last grains disappeared early or form a broad plug")
    if core > 4 or not 20 <= rim <= 45:
        errors.append("The core flickers or the moving B13 fringe was lost")
    if errors:
        parser.exit(1, "\n".join(errors) + "\n")


if __name__ == "__main__":
    main()
