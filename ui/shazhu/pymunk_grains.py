"""Offline Pymunk hourglass: real contacts, no prescribed sand-column outline.

Each layer is an independent 2D rigid-grain simulation. Overlaying layers is
a projection approximation, not a 3D solver. The ten-second recording repeats
with a cut: a draining, closed hourglass is not a periodic physical system.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import webbrowser

import numpy as np
from PIL import Image, ImageDraw

from preview_common import (
    ROOT, WIDTH, HEIGHT, BACKGROUND, SAND, CX, OUTLET, NECK_BOTTOM,
    NECK_HALF_WIDTH, mound_surface, ffmpeg_executable,
)

VENDOR = ROOT / "_temp" / "shazhu" / "vendor"
if VENDOR.is_dir():
    sys.path.insert(0, str(VENDOR))
try:
    import pymunk
except ImportError as exc:
    raise SystemExit(
        "Install preview-only dependencies: python -m pip install --target "
        "_temp/shazhu/vendor -r ui/shazhu/requirements-pymunk.txt") from exc


OUTPUT = ROOT / "_temp" / "shazhu" / "samples"
NAME = "pymunk_grains"
FPS, GRAVITY, SUBSTEPS = 30, 400.0, 16
SEED = 30101
LEFT_WALL = [(95, -100), (95, 40), (150, 85), (225, 125),
             (292, 158), (CX - NECK_HALF_WIDTH, 182),
             (CX - NECK_HALF_WIDTH, NECK_BOTTOM)]


def grain_bank():
    rng = np.random.default_rng(SEED)
    bank = []
    for _ in range(48):
        radius = rng.uniform(1.35, 1.9)
        bitmap = Image.new("L", (32, 32))
        points = []
        for phase in np.linspace(0, math.tau, 9, endpoint=False):
            edge = rng.uniform(0.91, 1.0) * radius * 4.0
            points.append((16 + edge * math.cos(phase), 16 + edge * math.sin(phase)))
        ImageDraw.Draw(bitmap).polygon(points, fill=255)
        alpha = np.asarray(bitmap.resize((8, 8), Image.Resampling.LANCZOS))
        yy, xx = np.nonzero(alpha > 65)
        dy, dx = yy - 4, xx - 4
        shade = rng.normal(0.0, 6.0)
        rgb = np.clip(SAND + shade + (dx - dy)[:, None] * 1.2, 0, 255).astype(np.uint8)
        vertices = [((x - 16.0) / 4.0, (y - 16.0) / 4.0) for x, y in points]
        bank.append((radius, dy, dx, rgb, vertices))
    return bank


class PhysicsLayer:
    def __init__(self, seed, bank):
        rng = np.random.default_rng(seed)
        self.space = pymunk.Space()
        self.space.gravity = (0.0, GRAVITY)
        self.space.iterations = 12
        self.space.collision_slop = 0.10
        self.space.sleep_time_threshold = 0.6
        self.bodies, self.shapes, self.bank_ids, self.upper = [], [], [], []
        self.grain_contacts, self.stream_contacts = 0, 0
        self.lower_source_contacts = set()
        self.wall_shapes = []
        for points in (LEFT_WALL, [(WIDTH - x, y) for x, y in LEFT_WALL]):
            for a, b in zip(points, points[1:]):
                self._wall(a, b)
        for a, b in (((0, 240), (0, HEIGHT + 2)),
                     ((WIDTH, 240), (WIDTH, HEIGHT + 2)),
                     ((0, HEIGHT + 2), (WIDTH, HEIGHT + 2))):
            self._wall(a, b)
        self.gate = self._wall(
            (CX - NECK_HALF_WIDTH, NECK_BOTTOM), (CX + NECK_HALF_WIDTH, NECK_BOTTOM))
        # Packed starting reservoirs are initial conditions, not drawing masks
        # or moving collision surfaces. Every seeded grain remains dynamic.
        pitch = 3.9
        row_pitch = pitch * math.sqrt(3.0) / 2.0
        wall_y = np.array([p[1] for p in LEFT_WALL])
        wall_x = np.array([p[0] for p in LEFT_WALL])
        for row, y in enumerate(np.arange(-90.0, NECK_BOTTOM - 3.0, row_pitch)):
            left = np.interp(y, wall_y, wall_x) + 3.0
            right = WIDTH - left
            for x in np.arange(left + (row % 2) * pitch / 2.0, right, pitch):
                self._grain((x + rng.uniform(-0.12, 0.12), y), rng, bank, True)
        for row, y in enumerate(np.arange(748.0, HEIGHT - 2.0, row_pitch)):
            for x in np.arange(3.0 + (row % 2) * pitch / 2.0, WIDTH - 3.0, pitch):
                if y > mound_surface(x, 0.0) + 2.0:
                    self._grain((x + rng.uniform(-0.12, 0.12), y), rng, bank, False)
        self.shape_indices = {shape: i for i, shape in enumerate(self.shapes)}
        self.space.on_collision(1, 1, begin=self._grain_contact)

    def _wall(self, a, b):
        shape = pymunk.Segment(self.space.static_body, a, b, 1.0)
        shape.friction, shape.elasticity = 0.8, 0.03
        shape.collision_type = 2
        self.space.add(shape)
        self.wall_shapes.append(shape)
        return shape

    def _grain(self, position, rng, bank, upper):
        bank_id = int(rng.integers(0, len(bank)))
        radius = bank[bank_id][0]
        mass = radius * radius
        vertices = bank[bank_id][4]
        body = pymunk.Body(mass, pymunk.moment_for_poly(mass, vertices))
        body.position = position
        shape = pymunk.Poly(body, vertices)
        shape.friction, shape.elasticity = 0.90, 0.03
        shape.collision_type = 1
        self.space.add(body, shape)
        self.bodies.append(body)
        self.shapes.append(shape)
        self.bank_ids.append(bank_id)
        self.upper.append(upper)

    def _grain_contact(self, arbiter, space, data):
        self.grain_contacts += 1
        a, b = [self.shape_indices[shape] for shape in arbiter.shapes]
        if not (self.upper[a] or self.upper[b]):
            return
        ya, yb = self.bodies[a].position.y, self.bodies[b].position.y
        if NECK_BOTTOM + 5 < min(ya, yb) and max(ya, yb) < 650.0:
            self.stream_contacts += 1
        for i, y in ((a, ya), (b, yb)):
            if self.upper[i] and y > 650.0:
                self.lower_source_contacts.add(i)

    def step(self, dt):
        self.space.step(dt)

    def open_gate(self):
        self.space.remove(self.gate)
        self.wall_shapes.remove(self.gate)
        # Static gate removal must wake its resting contact islands.
        for body in self.bodies:
            body.activate()

    def positions(self):
        return np.array([tuple(body.position) for body in self.bodies], dtype=np.float32)


def source_identity():
    return {
        "pymunk": pymunk.version,
        "renderer": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "shared": hashlib.sha256(Path(__file__).with_name("preview_common.py").read_bytes()).hexdigest(),
    }


def simulate(directory, layers=2, seconds=10, refresh=False):
    directory.mkdir(parents=True, exist_ok=True)
    cache, record = directory / (NAME + ".npz"), directory / (NAME + "-physics.json")
    identity = source_identity()
    if not refresh and cache.exists() and record.exists():
        metadata = json.loads(record.read_text(encoding="utf-8"))
        if (metadata["source"] == identity and metadata["layers"] == layers
                and metadata["seconds"] == seconds
                and metadata["cache_sha256"] == hashlib.sha256(cache.read_bytes()).hexdigest()):
            return cache, metadata
    bank = grain_bank()
    worlds = [PhysicsLayer(SEED + i * 19, bank) for i in range(layers)]
    dt = 1.0 / (FPS * SUBSTEPS)
    for _ in range(round(1.4 / dt)):
        for world in worlds:
            world.step(dt)
    for world in worlds:
        world.open_gate()
    for _ in range(round(1.8 / dt)):
        for world in worlds:
            world.step(dt)
    count = sum(len(world.bodies) for world in worlds)
    positions = np.empty((FPS * seconds, count, 2), dtype=np.float32)
    for frame in range(FPS * seconds):
        positions[frame] = np.concatenate([world.positions() for world in worlds])
        for _ in range(SUBSTEPS):
            for world in worlds:
                world.step(dt)
        if frame % FPS == 0:
            print(f"Physics {frame // FPS + 1}/{seconds} seconds; {count} grains", flush=True)
    bank_ids = np.concatenate([np.asarray(w.bank_ids, dtype=np.uint8) for w in worlds])
    upper = np.concatenate([np.asarray(w.upper, dtype=bool) for w in worlds])
    np.savez_compressed(cache, positions=positions, bank_ids=bank_ids, upper=upper)
    metadata = {
        "engine": "Pymunk", "source": identity, "seconds": seconds,
        "fps": FPS, "frames": len(positions), "layers": layers, "grains": count,
        "independent_2d_layers": True, "three_dimensional_collisions": False,
        "seed": SEED, "gravity": GRAVITY, "physics_substeps": SUBSTEPS,
        "particle_collision_shape": "irregular_convex_polygon",
        "particle_collision_friction": 0.90, "particle_restitution": 0.03,
        "particle_count_conserved": all(len(w.bodies) == len(w.space.bodies) for w in worlds),
        "grain_contact_begins": sum(w.grain_contacts for w in worlds),
        "stream_contact_begins": sum(w.stream_contacts for w in worlds),
        "source_grains_contacting_lower_region": sum(len(w.lower_source_contacts) for w in worlds),
        "source_grains_below_outlet_at_end": int(np.count_nonzero(upper & (positions[-1, :, 1] > NECK_BOTTOM))),
        "prescribed_column_outline": False, "artificial_splash_particles": False,
        "cut_on_replay": True, "seamless_loop": False, "application_test": False,
        "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
    }
    record.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return cache, metadata


def draw_frame(positions, bank_ids, bank):
    image = np.asarray(Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)).copy()
    x, y = np.rint(positions).astype(np.int32).T
    for bank_id, (_, dy, dx, rgb, _) in enumerate(bank):
        selected = bank_ids == bank_id
        px, py = x[selected, None] + dx, y[selected, None] + dy
        good = (px >= 0) & (px < WIDTH) & (py >= 0) & (py < HEIGHT)
        colors = np.broadcast_to(rgb, px.shape + (3,))[good]
        image[py[good], px[good]] = colors
    result = Image.fromarray(image)
    draw = ImageDraw.Draw(result)
    left = [(77, 0), (77, 43), (138, 96), (211, 133), (275, 165),
            (284, 181), (284, 214), (277, 231), (217, 263), (150, 293)]
    draw.line(left, fill=(95, 111, 118), width=12, joint="curve")
    draw.line([(WIDTH - x, y) for x, y in left],
              fill=(95, 111, 118), width=12, joint="curve")
    return result


def export(directory, layers=2, seconds=10, refresh=False, simulate_only=False):
    cache, metadata = simulate(directory, layers, seconds, refresh)
    with np.load(cache, allow_pickle=False) as data:
        positions, bank_ids = data["positions"], data["bank_ids"]
    bank = grain_bank()
    poster = draw_frame(positions[min(60, len(positions) - 1)], bank_ids, bank)
    poster.save(directory / (NAME + ".png"))
    poster.crop((235, 200, 405, 850)).save(directory / (NAME + "-detail.png"))
    if simulate_only:
        return cache
    target = directory / (NAME + ".mp4")
    command = [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
               "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-threads", "1",
               "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(target)]
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    process = subprocess.Popen(command, stdin=subprocess.PIPE, creationflags=flags)
    try:
        for frame in positions:
            process.stdin.write(draw_frame(frame, bank_ids, bank).tobytes())
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("Video encoding failed")
    except BaseException:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
        process.wait()
        raise
    metadata["video_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    player = directory / (NAME + ".html")
    player.write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Pymunk \u9897\u7c92\u78b0\u649e</title>'
        '<style>body{margin:0;background:#f3f5f5;color:#222b30;font:15px system-ui;'
        'text-align:center}h1{font-size:20px;margin:16px 0 8px}'
        'p{margin:0 0 12px}video{display:block;width:min(100%,640px);'
        'max-height:85vh;margin:auto}</style>'
        '<h1>06 Pymunk \u9897\u7c92\u78b0\u649e</h1>'
        f'<p>{seconds} \u79d2\u5b9e\u7b97\u7247\u6bb5 \u00b7 '
        '\u9996\u5c3e\u91cd\u64ad \u00b7 \u672a\u63a5\u5165\u5e94\u7528</p>'
        f'<video controls autoplay muted loop playsinline poster="{NAME}.png" '
        f'src="{NAME}.mp4"></video></html>', encoding="utf-8")
    return player


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--simulate-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--layers", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--seconds", type=int, choices=(1, 3, 10), default=10)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = export(args.output.resolve(), args.layers, args.seconds,
                    args.refresh, args.simulate_only)
    print(str(output), flush=True)
    if not args.export_only and not args.simulate_only:
        webbrowser.open(output.as_uri())


if __name__ == "__main__":
    main()
