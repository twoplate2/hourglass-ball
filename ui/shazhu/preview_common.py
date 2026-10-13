"""Isolated sand-column illustrations; never imports or changes the real app.

Sprite size/rotation/atlas ideas: https://github.com/Alchemist0823/three.quarks
Cellular grain-field idea: https://github.com/stegu/webgl-noise
This CPU illustration code is original, not a port of either runtime.
The animated reservoirs, periodic supply and landing are visual references, not
an hourglass physics model or proof of Android appearance/performance.
"""

import argparse
from dataclasses import dataclass
import hashlib
import html
import json
import math
from pathlib import Path
import shutil
import subprocess
import webbrowser

import numpy as np
from PIL import Image, ImageDraw

from sand_motion import (
    CX, OUTLET, LANDING, NECK_HALF_WIDTH, NECK_BOTTOM, LOOP_SECONDS, SandMotion,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "_temp" / "shazhu" / "samples"
WIDTH, HEIGHT = 640, 900
HALF_WIDTH = NECK_HALF_WIDTH
BACKGROUND = (232, 240, 246)
SAND = np.array((211, 161, 85), dtype=np.float32)


@dataclass(frozen=True)
class Style:
    number: str
    label: str
    kind: str
    rate: int
    radius: float
    contrast: float


STYLES = {
    "fine_grains": Style("01", "\u7ec6\u5bc6\u6c99\u7c92", "fine", 14000, 1.45, 0.85),
    "faceted_grains": Style("02", "\u68f1\u89d2\u6c99\u7c92", "faceted", 7200, 2.1, 1.0),
    "small_clusters": Style("03", "\u8f7b\u5fae\u805a\u56e2", "cluster", 11000, 1.55, 0.82),
    "short_trails": Style("04", "\u77ed\u62d6\u5c3e\u6c99\u7c92", "trail", 7400, 1.40, 0.85),
    "worley_grains": Style("05", "\u80de\u5143\u6c99\u7c92", "worley", 14000, 1.65, 0.85),
}


def sand_texture():
    rng = np.random.default_rng(13030)
    coarse = Image.fromarray(rng.integers(0, 256, (225, 160), dtype=np.uint8))
    coarse = np.asarray(coarse.resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR))
    fine = rng.normal(0.0, 2.6, (HEIGHT, WIDTH))
    tone = (coarse.astype(np.float32) - 127.5) * 0.11 + fine
    return np.clip(SAND + tone[..., None], 0, 255).astype(np.uint8)


def upper_surface(x, t):
    return (25.0 + 0.018 * (x - CX)
            + 16.0 * np.exp(-((x - CX + 12.0) / 68.0)**2)
            + 1.5 * np.sin(x * 0.055 + 0.7))


def mound_surface(x, t):
    offset = x - (CX + 7.0)
    slope = np.where(offset < 0.0, 0.35, 0.40)
    # A rounded, uneven impact area, not a cone touching the stream at one tip.
    return (LANDING + slope * (np.sqrt(offset**2 + 32.0**2) - 32.0)
            + 1.1 * np.sin(x * 0.077 + 0.4)
            + 0.65 * np.sin(x * 0.19 + 1.1))


def scene(texture, t):
    mask = Image.new("L", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(mask)
    xs = np.arange(95, 546)
    top = list(zip(xs, upper_surface(xs, t)))
    draw.polygon(top + [(545, 40), (490, 85), (415, 125),
                  (348, 158), (CX + NECK_HALF_WIDTH, 182),
                  (CX + NECK_HALF_WIDTH, NECK_BOTTOM),
                  (CX - NECK_HALF_WIDTH, NECK_BOTTOM),
                  (CX - NECK_HALF_WIDTH, 182), (292, 158),
                  (225, 125), (150, 85), (95, 40)], fill=255)
    xs = np.arange(WIDTH + 1)
    mound = [(0, HEIGHT)] + list(zip(xs, mound_surface(xs, t)))
    mound.append((WIDTH, HEIGHT))
    draw.polygon(mound, fill=255)
    base = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    base.paste(Image.fromarray(texture), (0, 0), mask)
    glass = ImageDraw.Draw(base)
    left = [(77, 0), (77, 43), (138, 96), (211, 133), (275, 165),
            (284, 181), (284, 214), (277, 231), (217, 263), (150, 293)]
    right = [(WIDTH - x, y) for x, y in left]
    glass.line(left, fill=(95, 111, 118), width=12, joint="curve")
    glass.line(right, fill=(95, 111, 118), width=12, joint="curve")
    return np.asarray(base).copy()


def sprite_bank(style):
    """Small irregular raster sprites, not a solid backing strip."""
    rng = np.random.default_rng(32030)
    bank = []
    for index in range(48):
        radius = style.radius * rng.uniform(0.75, 1.25)
        stretch = rng.uniform(1.0, 1.32)
        if style.kind == "trail":
            stretch = rng.uniform(1.65, 2.5)
        angle = rng.uniform(-math.pi, math.pi)
        if style.kind == "trail":
            angle = rng.uniform(-0.13, 0.13)
        bitmap = Image.new("L", (48, 48))
        draw = ImageDraw.Draw(bitmap)
        points = []
        sides = 5 if style.kind == "faceted" else 7
        for step in range(sides):
            phase = angle + step * math.tau / sides
            edge = rng.uniform(0.74, 1.1)
            points.append((24 + 4 * radius * edge * math.cos(phase),
                           24 + 4 * radius * stretch * edge * math.sin(phase)))
        draw.polygon(points, fill=255)
        if style.kind == "worley":
            yy, xx = np.indices((48, 48))
            central = (xx - 24.0)**2 + (yy - 24.0)**2
            nearest = central.copy()
            for _ in range(10):
                phase = rng.uniform(0.0, math.tau)
                distance = rng.uniform(5.5, 11.0) * radius / 1.65
                sx, sy = 24.0 + distance * math.cos(phase), 24.0 + distance * math.sin(phase)
                nearest = np.minimum(nearest, (xx - sx)**2 + (yy - sy)**2)
            bitmap = Image.fromarray(
                ((nearest == central) & (central < (6.0 * radius)**2)).astype(np.uint8) * 255)
        alpha = np.asarray(bitmap.resize((12, 12), Image.Resampling.LANCZOS))
        yy, xx = np.nonzero(alpha > 80)
        dy, dx = yy - 6, xx - 6
        shade = rng.normal(0.0, 7.0) * style.contrast
        rgb = np.clip(SAND + shade + (dx - dy)[:, None] * 1.1, 0, 255).astype(np.uint8)
        bank.append((dy, dx, rgb))
    return bank


class Column:
    def __init__(self, name):
        self.name = name
        self.style = STYLES[name]
        self.texture = sand_texture()
        self.motion = SandMotion(self.style.rate, self.style.radius, mound_surface,
                                 1.35 if self.style.kind == "cluster" else 1.0)
        rng = np.random.default_rng(32031)
        count = len(self.motion.birth)
        self.bank_ids = rng.integers(0, 48, count)
        self.sprites = sprite_bank(self.style)

    def _reservoir_motion(self, image, t):
        yy, xx = np.indices((232, WIDTH))
        near_neck = np.exp(-((xx - CX) / (24.0 + (232 - yy) * 0.65))**2)
        pull = np.clip((yy - 90) / 120.0, 0.0, 1.0) * near_neck
        # The texture wraps after ten seconds; displacement is local to the
        # funnel, so the packed reservoir differs from the accelerating stream.
        source_y = (yy - int(t * 18.0)) % 180 + 45
        patch = image[:232]
        sand = ((patch[..., 0] > 150) & (patch[..., 1] > 125)
                & (patch[..., 2] < 180) & (pull > 0.05))
        moving = self.texture[source_y, xx]
        mix = (0.65 * pull)[..., None]
        blended = (patch * (1.0 - mix) + moving * mix).astype(np.uint8)
        patch[sand] = blended[sand]

    def _particles(self, image, t):
        active, x, y = self.motion.airborne(t)
        x, y = np.rint(x).astype(np.int32), np.rint(y).astype(np.int32)
        ids = self.bank_ids[active]
        for bank_id, (dy, dx, rgb) in enumerate(self.sprites):
            selected = ids == bank_id
            px = x[selected, None] + dx
            py = y[selected, None] + dy
            good = (px >= 0) & (px < WIDTH) & (py >= int(OUTLET)) & (py < HEIGHT)
            good &= ((py > NECK_BOTTOM)
                     | (np.abs(px - CX) <= NECK_HALF_WIDTH))
            ix, iy = px[good], py[good]
            colors = np.broadcast_to(rgb, px.shape + (3,))[good]
            # Each grain reaches its own surface height across a wide footprint.
            above_mound = iy < mound_surface(ix, t) + 2.0
            image[iy[above_mound], ix[above_mound]] = colors[above_mound]

    def _outlet_blend(self, image, base):
        # The reservoir grain palette is shared, and the transition has no flat
        # solid strip. Blend colours only where a candidate actually drew sand.
        top, bottom = int(OUTLET), int(OUTLET + 42)
        left, right = int(CX - HALF_WIDTH - 6), int(CX + HALF_WIDTH + 7)
        patch = image[top:bottom, left:right].astype(np.float32)
        is_sand = np.any(patch != base[top:bottom, left:right], axis=2)
        mix = np.linspace(0.82, 0.0, bottom - top)[:, None, None]
        texture = self.texture[top:bottom, left:right]
        blended = (patch * (1.0 - mix) + texture * mix).astype(np.uint8)
        target = image[top:bottom, left:right]
        target[is_sand] = blended[is_sand]
        # Preserve the actual neck shape and colour where the stream overlaps it.
        end = int(NECK_BOTTOM) + 1
        image[top:end] = base[top:end]

    def _splash(self, image, t):
        motion = self.motion
        parents = motion.splash_parent
        age = (t - motion.hit_time[parents]) % LOOP_SECONDS
        active = age < 0.65
        age = age[active]
        origin_x = motion.hit_x[parents[active]]
        x = np.rint(origin_x + motion.splash_vx[active] * age).astype(np.int32)
        y = np.rint(motion.hit_y[parents[active]] + motion.splash_vy[active] * age
                    + 220.0 * age**2).astype(np.int32)
        good = (x >= 0) & (x < WIDTH) & (y >= 0) & (y < HEIGHT)
        x, y = x[good], y[good]
        above = y < mound_surface(x, t)
        image[y[above], x[above]] = (211, 158, 78)

    def _surface_roll(self, image, t):
        motion = self.motion
        parents = motion.roll_parent
        age = (t - motion.hit_time[parents]) % LOOP_SECONDS
        active = age < 0.9
        age = age[active]
        x = np.rint(motion.hit_x[parents[active]] + motion.roll_side[active]
                    * motion.roll_speed[active] * (age - 0.3 * age**2)).astype(np.int32)
        y = np.rint(mound_surface(x, t) - 1.0).astype(np.int32)
        # Short settling grains run down both slopes, with unequal trajectories.
        for dx, dy in ((0, 0), (1, 0), (0, 1)):
            image[y + dy, x + dx] = (202 + dx * 12, 150 + dx * 10, 77)

    def frame(self, elapsed):
        t = float(elapsed) % LOOP_SECONDS
        image = scene(self.texture, t)
        self._reservoir_motion(image, t)
        base = image.copy()
        self._particles(image, t)
        self._outlet_blend(image, base)
        self._splash(image, t)
        self._surface_roll(image, t)
        return Image.fromarray(image)


def ffmpeg_executable():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError("Export needs ffmpeg or the imageio-ffmpeg package.") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def player(name, directory):
    title = html.escape(STYLES[name].number + " " + STYLES[name].label)
    output = directory / (name + ".html")
    output.write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title><style>body{{margin:0;background:#f2f4f5;'
        'font:16px system-ui;text-align:center}}h1{font-size:20px}'
        'video{display:block;width:min(100%,640px);max-height:85vh;margin:auto}'
        f'</style><h1>{title}</h1><video controls muted autoplay loop playsinline '
        f'poster="{name}.png" src="{name}.mp4"></video></html>', encoding="utf-8")
    return output


def export(name, directory, fps=30, poster_only=False):
    directory.mkdir(parents=True, exist_ok=True)
    column = Column(name)
    column.frame(2.0).save(directory / (name + ".png"))
    detail = column.frame(2.0).crop((270, 210, 370, 780))
    detail.save(directory / (name + "-detail.png"))
    if poster_only:
        return directory / (name + ".png")
    target = directory / (name + ".mp4")
    # No desktop window, and low-priority, single-threaded video encoding.
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    command = [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
               "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-threads", "1",
               "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(target)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, creationflags=flags)
    try:
        for index in range(LOOP_SECONDS * fps):
            process.stdin.write(column.frame(index / fps).tobytes())
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("Video encoder failed: " + str(target))
    except BaseException:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
        process.wait()
        raise
    manifest = {
        "candidate": name, "label": STYLES[name].label, "duration_seconds": LOOP_SECONDS,
        "fps": fps, "frames": LOOP_SECONDS * fps, "width": WIDTH, "height": HEIGHT,
        "seed": 30030, "illustration_only": True, "application_test": False,
        "periodic_supply": True, "fixed_reservoirs": False,
        "reservoir_motion": "local_funnel_pull_and_surface_settling",
        "edge_motion": "advected_local_emission_packets",
        "source_packet_count": len(column.motion.packet_time),
        "upstream_packet_inheritance": True, "impact_parent_tracking": True,
        "chaotic_physics_solver": False,
        "regular_cell_edge_mask": False, "depth_widening": False,
        "neck_half_width": NECK_HALF_WIDTH, "column_half_width": HALF_WIDTH,
        "fixed_mean_column_axis": True, "hard_column_envelope": False,
        "whole_column_sway": False, "distributed_landing": True,
        "video_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "motion_sha256": hashlib.sha256(
            Path(__file__).with_name("sand_motion.py").read_bytes()).hexdigest(),
        "source_references": ["https://github.com/Alchemist0823/three.quarks",
                              "https://github.com/stegu/webgl-noise"],
    }
    target.with_suffix(".json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return player(name, directory)


def main(name=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-only", action="store_true", help="Do not open a browser.")
    parser.add_argument("--poster-only", action="store_true", help="Draw still illustrations only.")
    parser.add_argument("--fps", type=int, choices=(15, 30, 60), default=30)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    for candidate in ([name] if name else STYLES):
        output = export(candidate, args.output.resolve(), args.fps, args.poster_only)
        print(str(output), flush=True)
    if not args.export_only and not args.poster_only:
        target = output if name else Path(__file__).with_name("index.html")
        webbrowser.open(target.as_uri())


if __name__ == "__main__":
    main()
