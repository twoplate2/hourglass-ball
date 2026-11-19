"""Exercise the real hidden PC renderer at the apertures seen on the Y700."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image


PERIODS = (20, 50, 6000, 60000)
TOOLS = Path(__file__).resolve().parent


def wall_gaps(image, center, lip, half, stroke, glass):
    """Fraction of rows with two exposed glass pixels touching each straight wall."""
    rows = image[round(lip) - round(.30 * half):round(lip)]
    result = []
    for side in (-1, 1):
        gaps = []
        # Walk from the solid glass rim into the stream, on the same row.
        x = center + side * np.arange(round(half + stroke / 2), round(half * .6), -1)
        for row in rows[:, x]:
            wall = np.max(np.abs(row - row[0]), axis=1) <= 8
            edge = np.flatnonzero(wall)[-1] + 1
            if edge + 2 > len(row):
                raise ValueError("Could not locate inner glass wall")
            gaps.append(np.max(np.abs(row[edge:edge + 2] - glass)) <= 20)
        result.append(float(np.mean(gaps)))
    return result


def jet_profile(image, center, lip, half, surface, glass):
    """Measure the gold jet's colour coverage in two bands strictly above the pile."""
    offset = round(1.8 * half)
    strip = image[:, center - offset:center + offset + 1]
    colour = strip[:, :, 0] - strip[:, :, 2]
    gold = np.median(colour[round(lip - half / 2):round(lip), offset - 1:offset + 2])
    background = glass[0] - glass[2]
    if gold - background < 60 or surface - lip < 4 * half:
        raise ValueError("A supplied gold jet in free fall is required")
    coverage = np.clip((colour - background) / (gold - background), 0, 1)

    def band(lo, hi):
        return coverage[round(lip + lo * (surface - lip)):round(lip + hi * (surface - lip))]

    def width(region, threshold):
        profile = region.mean(axis=0)
        indices = np.flatnonzero(profile >= threshold)
        if not len(indices):
            return 0.
        left, right = indices[0], indices[-1]
        if left == 0 or right + 1 == len(profile):
            raise ValueError("Jet extends outside the measurement")
        def border(i, j):
            return i + (threshold - profile[i]) * (j-i) / (profile[j] - profile[i])
        return border(right, right + 1) - border(left, left - 1)

    near, low = band(.045, .095), band(.75, .87)
    body = width(low, .50)
    if body <= 0 or width(near, .50) <= 0:
        raise ValueError("Missing continuous jet")
    core = np.abs(np.arange(-offset, offset + 1)) < max(1., .23 * body)
    return [body / width(near, .50), (width(low, .20) - width(low, .80)) / body,
            float(low[:, core].mean())]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=TOOLS.parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    offsets = [0., .1, .2, .3, .4]
    cases = [dict(name=f'jet-{p}', period=p, elapsed=4., frame_offsets=offsets) for p in PERIODS]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'cases.json'
        path.write_text(json.dumps(cases), encoding='utf-8')
        subprocess.run([sys.executable, str(TOOLS / 'verify_short_grain_runtime.py'),
                        '--runtime', str(args.runtime), '--output', str(args.output),
                        '--renderer', 'texture', '--width', '1602', '--height', '2160',
                        '--cases', str(path)], check=True)
    report = json.loads((args.output / 'result.json').read_text(encoding='utf-8'))
    if not (report['completed'] and report['passed'] and report['source_unchanged']):
        raise RuntimeError('Incomplete capture')
    tapers = []
    failures = []
    for p in PERIODS:
        values = []
        for frame in report['frames']:
            if frame['period'] != p:
                continue
            with Image.open(args.output / frame['file']) as source:
                image = np.asarray(source.convert('RGB'), dtype=np.int16)
            geom = frame['geometry']
            glass = np.rint(255 * np.asarray(geom['glass_fill_rgb'])).astype(np.int16)
            args_px = (image, round(geom['_cx']), image.shape[0] - frame['outlet_y'], frame['half_width'])
            if p == 20:
                values.append(wall_gaps(*args_px, geom['_ow'], glass))
            else:
                values.append(jet_profile(*args_px, image.shape[0] - geom['mound_top_y'], glass))
        if len(values) != len(offsets):
            raise RuntimeError(f'Missing frames for {p} s')
        if p == 20:
            left, right = np.mean(values, axis=0)
            print(f'{p} s wall gaps L/R {left:.1%} / {right:.1%}')
            if max(left, right) > .12:
                failures.append('Steady sand exposes the straight wall')
        else:
            taper, fringe, core = np.median(values, axis=0)
            print(f'{p} s lower/near width {taper:.2f}, fringe/body {fringe:.2f}, core coverage {core:.2f}')
            tapers.append(taper)
            if not (.46 <= taper <= .88 and .06 <= fringe <= .55 and core >= .82):
                failures.append(f'Free jet profile out of range at {p} s')
    if np.ptp(tapers) > .20:
        failures.append('Taper changes abruptly between apertures')
    if failures:
        raise SystemExit('\n'.join(failures))


if __name__ == '__main__':
    main()
