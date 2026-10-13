"""Real polygon contacts at the neck outlet, stream and landing region.

Bulk reservoirs keep the timer's volume model. Mobile grains are independent
2D contact layers (as in preview 06), not a 3D solver or a prescribed outline.
"""

from dataclasses import dataclass
import math
import random

import pymunk


@dataclass
class Grain:
    body: object
    shape: object
    radius: float
    vertices: tuple
    tone: int
    mass: float = 0.0
    released: bool = False
    landed_at: object = None
    landing_speed: float = 0.0


class ContactLayer:
    def __init__(self, owner, seed):
        self.owner = owner
        self.rng = random.Random(seed)
        self.space = pymunk.Space()
        self.space.iterations = 12
        self.space.collision_slop = owner.radius * 0.06
        self.space.gravity = (0, -owner.gravity)
        self.grains = []
        self.by_shape = {}
        self.contacts = self.stream_contacts = self.landings = 0
        self.clock = self.supply_acc = 0.0
        self.surface_shapes = []
        self._walls(owner.walls)
        self.gate = self._segment(
            (-owner.half_width, owner.outlet), (owner.half_width, owner.outlet))
        self.space.on_collision(1, 1, begin=self._contact)
        self.space.on_collision(1, 4, begin=self._contact)
        self.space.on_collision(4, 4, begin=self._stream_contact)
        self.space.on_collision(1, 3, begin=self._landing)
        self.space.on_collision(4, 3, begin=self._landing)
        pitch = owner.radius * 2.40
        row_height = pitch * math.sqrt(3) / 2
        row = 0
        y = owner.outlet + pitch
        while y < owner.source_y:
            half = owner.width_at(y) - owner.radius * 1.15
            x = -half + (row % 2) * pitch * 0.5
            while x < half:
                px = x + self.rng.uniform(-0.08, 0.08) * owner.radius
                wall = self.space.point_query_nearest(
                    (px, y), owner.radius * 1.2, pymunk.ShapeFilter(mask=2))
                if wall is None:
                    self._grain(px, y)
                x += pitch
            row += 1
            y += row_height

    def _segment(self, a, b, collision_type=2):
        shape = pymunk.Segment(self.space.static_body, a, b, self.owner.radius * 0.60)
        shape.friction, shape.elasticity = 0.8, 0.03
        shape.collision_type = collision_type
        shape.filter = pymunk.ShapeFilter(categories=2)
        self.space.add(shape)
        return shape

    def _walls(self, walls):
        for points in walls:
            for i, (a, b) in enumerate(zip(points, points[1:])):
                shape = self._segment(a, b)
                shape.set_neighbors(points[max(0, i - 1)], points[min(len(points) - 1, i + 2)])

    def _grain(self, x, y, feeding=False):
        rng = self.rng
        radius = self.owner.radius * rng.uniform(0.84, 1.18)
        vertices = tuple(
            (math.cos(a) * radius * r, math.sin(a) * radius * r)
            for a, r in ((i * math.tau / 9, rng.uniform(0.91, 1.0)) for i in range(9)))
        mass = radius * radius
        body = pymunk.Body(mass, pymunk.moment_for_poly(mass, vertices))
        body.position = (x, y)
        body.angle = rng.uniform(0, math.tau)
        if feeding:
            body.velocity = (0, -self.owner.feed_speed)
        shape = pymunk.Poly(body, vertices)
        shape.friction, shape.elasticity = 0.90, 0.03
        shape.collision_type = 1
        shape.filter = pymunk.ShapeFilter(categories=1)
        grain = Grain(body, shape, radius, vertices, rng.randrange(12))
        self.space.add(body, shape)
        self.grains.append(grain)
        self.by_shape[shape] = grain
        return grain

    def _contact(self, arbiter, space, data):
        self.contacts += 1

    def _stream_contact(self, arbiter, space, data):
        self.contacts += 1
        self.stream_contacts += 1

    def _landing(self, arbiter, space, data):
        shape = next(shape for shape in arbiter.shapes if shape.collision_type in (1, 4))
        grain = self.by_shape[shape]
        if grain.landed_at is None:
            grain.landed_at = self.clock
            grain.landing_speed = abs(grain.body.velocity.y)
            self.landings += 1

    def set_surface(self, points):
        def solid(a, b):
            return [(a[0], -20.0), (b[0], -20.0), b, a]

        if not self.surface_shapes:
            for a, b in zip(points, points[1:]):
                shape = pymunk.Poly(self.space.static_body, solid(a, b))
                shape.friction, shape.elasticity = 0.90, 0.03
                shape.collision_type = 3
                shape.filter = pymunk.ShapeFilter(categories=2)
                self.space.add(shape)
                self.surface_shapes.append(shape)
        else:
            for shape, a, b in zip(self.surface_shapes, points, points[1:]):
                shape.unsafe_set_vertices(solid(a, b))
                self.space.reindex_shape(shape)

    def open_gate(self):
        if self.gate is not None:
            self.space.remove(self.gate)
            self.gate = None
            for grain in self.grains:
                grain.body.activate()

    def step(self, dt, supplying):
        self.clock += dt
        if supplying:
            # Fill independent rows at staggered times, not frame-sized packets.
            pitch = self.owner.radius * 2.40
            half = self.owner.width_at(self.owner.source_y) - self.owner.radius * 1.2
            count = max(1, int(2 * half / pitch))
            self.supply_acc += dt * self.owner.source_speed / pitch
            while self.supply_acc >= 1:
                self.supply_acc -= 1
                spacing = 2 * half / count
                for i in range(count):
                    x = -half + (i + 0.5) * spacing
                    x += self.rng.uniform(-0.15, 0.15) * spacing
                    y = self.owner.source_y + self.rng.uniform(-0.2, 0.2) * pitch
                    occupied = self.space.point_query_nearest(
                        (x, y), self.owner.radius * 1.2, pymunk.ShapeFilter(mask=3))
                    if occupied is None:
                        self._grain(x, y, feeding=True)
        self.space.step(dt)

    def remove(self, grain):
        self.space.remove(grain.shape, grain.body)
        del self.by_shape[grain.shape]


class PymunkSand:
    """Contact solver in radius-normalized game coordinates (Kivy y upwards)."""

    def __init__(self, widget, seed=None, coarse=1.8):
        if seed is None:
            seed = random.getrandbits(32)
        self.seed = seed
        self.coarse = coarse
        self._configure_geometry(widget)
        self.gravity = 450.0
        self.source_speed = 90.0
        self.feed_speed = 35.0
        self.motion_scale = widget._particle_motion_scale
        self.clock = 0.0
        self.finished = False
        self.passed = self.absorbed = self.returned_to_bulk = 0
        self.layers = [ContactLayer(self, seed + i * 19) for i in range(2)]
        self._surface = []

    def _configure_geometry(self, widget):
        self.scale = widget._R_inner / 166.0
        self.cx = widget._cx
        self.origin = widget._lower_sand_bot
        self.outlet = self.y(2 * widget._neck_y - widget._taper["y_bot"])
        self.half_width = widget._taper["t_in"] / self.scale
        self.radius = min(0.95, self.half_width * 0.075) * self.coarse / 1.8
        self.neck_points = [(x / self.scale, self.y(y))
                            for x, y in widget._taper["in_pts"]]
        # Start inside the bulk upper reservoir, so neck and column share grains.
        self.source_y = min(self.neck_points[0][1] - self.radius * 2,
                            self.outlet + self.half_width * 6)
        left = [(-self.neck_points[0][0], self.neck_points[0][1] + 8 * self.radius)]
        left.extend((-x, y) for x, y in self.neck_points)
        left.extend([(-self.half_width, self.y(widget._taper["y_bot"])),
                     (-self.half_width, self.outlet)])
        # Reflect the *flared* neck too; an uncut circular top seals the outlet.
        lower_neck = [(-x, self.y(2 * widget._neck_y - absolute_y))
                      for x, absolute_y in reversed(widget._taper["in_pts"])]
        left.extend([(x / self.scale, y) for x, y in lower_neck])
        cut_y = lower_neck[-1][1]
        lower = []
        for i in range(81):
            a = -math.pi / 2 + math.pi * i / 80
            point = (166.0 * math.cos(a),
                     self.y(widget._lower_y_c) + 166.0 * math.sin(a))
            if point[1] <= cut_y:
                lower.append(point)
        lower.append((-left[-1][0], cut_y))
        self.walls = [left, [(-x, y) for x, y in left], lower,
                      [(-x, y) for x, y in lower]]

    def y(self, value):
        return (value - self.origin) / self.scale

    def width_at(self, y):
        for (x0, y0), (x1, y1) in zip(self.neck_points, self.neck_points[1:]):
            if y1 <= y <= y0:
                return x0 + (x1 - x0) * (y0 - y) / max(1e-9, y0 - y1)
        return self.neck_points[0][0] if y > self.neck_points[0][1] else self.half_width

    def set_surface(self, widget):
        xs, ys, *_ = widget._mound_contact_curve()
        points = [((x - self.cx) / self.scale, self.y(y)) for x, y in zip(xs, ys)]
        self._surface = points
        for layer in self.layers:
            layer.set_surface(points)

    def surface_at(self, x):
        if not self._surface:
            return 0.0
        first, last = self._surface[0], self._surface[-1]
        z = max(0.0, min(len(self._surface) - 1.0,
                         (x - first[0]) / (last[0] - first[0]) * (len(self._surface) - 1)))
        i = min(len(self._surface) - 2, int(z))
        return self._surface[i][1] + (self._surface[i + 1][1] - self._surface[i][1]) * (z - i)

    def absorb_engulfed(self, widget):
        # A growing continuum pile must absorb proxies before its collision
        # surface moves into them, especially where the pile meets the glass.
        while True:
            changed = False
            for layer in self.layers:
                keep = []
                for grain in layer.grains:
                    x, y = grain.body.position
                    surface = self.surface_at(x)
                    roof = 166.0 + math.sqrt(max(0.0, 166.0 ** 2 - x ** 2))
                    filled_corner = roof - surface < 2.4 * grain.radius
                    engulfed = y < surface - grain.radius * 0.1
                    if grain.released and (engulfed or (
                            filled_corner and y <= surface + grain.radius)):
                        if grain.mass:
                            widget._sand_landed += grain.mass
                            widget._record_contact(self.cx + x * self.scale,
                                                   abs(grain.body.velocity.y) * self.scale * self.motion_scale)
                        layer.remove(grain)
                        self.absorbed += 1
                        changed = True
                    else:
                        keep.append(grain)
                layer.grains = keep
            if not changed:
                break
            widget._sand_landed = min(widget._sand_released, widget._sand_landed)
            widget._mound_shape_cache = widget._mound_curve_cache = None
            self.set_surface(widget)

    def advance(self, widget, dt):
        if dt <= 0 or self.finished:
            return
        self.set_surface(widget)
        self.absorb_engulfed(widget)
        start, end, _ = widget._transfer_timing()
        previous = max(0.0, widget.elapsed - dt)
        widget._sand_pending += max(0.0, widget._released_fraction_at(widget.elapsed)
                                     - widget._released_fraction_at(previous)) if widget.running else 0.0
        # Fixed contact substeps scale with short-duration playback, not frame rate.
        # Timer accounting uses the full elapsed delta; bounded visual catch-up
        # avoids a slow frame causing an ever-growing chain of physics work.
        physical_dt = min(dt, 1 / 15) * self.motion_scale
        contact_dt = min(1 / 120, self.radius / 60.0)
        count = max(1, math.ceil(physical_dt / contact_dt))
        sub_dt = physical_dt / count
        crossing = []
        for step in range(count):
            t = previous + dt * (step + 1) / count
            self.clock += sub_dt
            for layer in self.layers:
                if t >= start:
                    layer.open_gate()
                layer.step(sub_dt, widget.running and start <= t < end)
        # Landing callbacks retain every grain until the frame is accounted;
        # one crossing scan suffices even if it traverses the whole gap this frame.
        for layer in self.layers:
            keep = []
            for grain in layer.grains:
                if (not grain.released and grain.mass == 0.0
                        and grain.body.position.y > self.source_y + 3 * self.radius):
                    layer.remove(grain)
                    self.returned_to_bulk += 1
                    continue
                if not grain.released and grain.body.position.y <= self.outlet:
                    grain.released = True
                    grain.shape.collision_type = 4
                    crossing.append(grain)
                    self.passed += 1
                keep.append(grain)
            layer.grains = keep
        if crossing and widget._sand_pending > 0:
            mass = widget._sand_pending / len(crossing)
            for grain in crossing:
                grain.mass += mass
            widget._sand_released += widget._sand_pending
            widget._sand_pending = 0.0
        # At supply shutoff, attach residual ledger mass to existing mobile grains.
        if widget.elapsed >= end and widget._sand_pending > 0:
            remaining = [g for layer in self.layers for g in layer.grains if g.landed_at is None]
            if remaining:
                mass = widget._sand_pending / len(remaining)
                for grain in remaining:
                    grain.mass += mass
                widget._sand_released += widget._sand_pending
                widget._sand_pending = 0.0
        for layer in self.layers:
            keep = []
            for grain in layer.grains:
                if widget.elapsed >= end and not grain.released and grain.mass == 0.0:
                    layer.remove(grain)
                    self.returned_to_bulk += 1
                    continue
                x, y = grain.body.position
                # Moving bulk sand can engulf a grain between contact substeps.
                if grain.landed_at is None and y <= self.surface_at(x) + grain.radius * 0.2:
                    grain.landed_at = layer.clock
                    grain.landing_speed = abs(grain.body.velocity.y)
                    layer.landings += 1
                if grain.landed_at is not None and grain.mass:
                    widget._sand_landed += grain.mass
                    widget._record_contact(self.cx + x * self.scale,
                                           grain.landing_speed * self.scale * self.motion_scale)
                    grain.mass = 0.0
                buried = y < self.surface_at(x) - grain.radius
                settled = grain.landed_at is not None and layer.clock - grain.landed_at > 0.20
                if buried or settled:
                    # Never drop still-accounted sand on a bounds/cleanup path.
                    widget._sand_landed += grain.mass
                    grain.mass = 0.0
                    layer.remove(grain)
                    self.absorbed += 1
                else:
                    keep.append(grain)
            layer.grains = keep
        widget._sand_landed = min(widget._sand_released, widget._sand_landed)
        if (widget.elapsed >= widget.duration and widget._sand_pending == 0.0
                and widget._sand_released >= 1 - 1e-12 and widget._sand_landed >= 1 - 1e-12
                and all(grain.mass == 0.0 for layer in self.layers for grain in layer.grains)):
            # All timer sand is in the bulk pile; remove only zero-mass proxies.
            for layer in self.layers:
                for grain in layer.grains:
                    layer.remove(grain)
                layer.grains.clear()
            widget._sand_released = widget._sand_landed = 1.0
            self.finished = True
        widget._mound_shape_cache = widget._mound_curve_cache = None
        self._sync_arrays(widget)

    def _sync_arrays(self, widget):
        grains = [g for layer in self.layers for g in layer.grains
                  if g.released and g.landed_at is None]
        widget._p_grow(len(grains))
        widget.pn = len(grains)
        for i, grain in enumerate(grains):
            widget.px[i] = self.cx + grain.body.position.x * self.scale
            widget.py[i] = self.origin + grain.body.position.y * self.scale
            widget.pvy[i] = grain.body.velocity.y * self.scale * self.motion_scale
            widget.pmass[i] = grain.mass
            widget.psz[i] = grain.radius * 2 * self.scale
            widget.pwp[i] = grain.tone
            widget.ptl[i] = 0.0
        widget._p_refresh_view()

    def remap(self, widget):
        """Resize without rebuilding the worlds or losing in-flight ledger mass."""
        old_outlet, old_width, old_radius = self.outlet, self.half_width, self.radius
        self._configure_geometry(widget)
        sx, sy = self.half_width / old_width, self.outlet / old_outlet
        for layer in self.layers:
            static = [shape for shape in layer.space.shapes if shape not in layer.by_shape]
            layer.space.remove(*static)
            closed = layer.gate is not None
            layer.gate = None
            layer.surface_shapes = []
            layer._walls(self.walls)
            if closed:
                layer.gate = layer._segment(
                    (-self.half_width, self.outlet), (self.half_width, self.outlet))
            for grain in layer.grains:
                x, y = grain.body.position
                vx, vy = grain.body.velocity
                grain.body.position = (x * sx, y * sy)
                grain.body.velocity = (vx * sx, vy * sy)
                layer.space.reindex_shape(grain.shape)
        self._resize_grains(self.radius / old_radius)
        self.set_surface(widget)
        self._sync_arrays(widget)

    def _resize_grains(self, ratio):
        for layer in self.layers:
            layer.space.collision_slop = self.radius * 0.06
            for grain in layer.grains:
                grain.radius *= ratio
                grain.vertices = tuple((x * ratio, y * ratio) for x, y in grain.vertices)
                grain.shape.unsafe_set_vertices(grain.vertices)
                grain.body.mass = grain.radius * grain.radius
                grain.body.moment = pymunk.moment_for_poly(grain.body.mass, grain.vertices)
                layer.space.reindex_shape(grain.shape)
                grain.body.activate()

    def set_coarse(self, coarse):
        ratio = coarse / self.coarse
        self.coarse = coarse
        self.radius *= ratio
        self._resize_grains(ratio)

    def metrics(self):
        return {"engine": "Pymunk", "version": pymunk.version, "layers": len(self.layers),
                "seed": self.seed,
                "gravity": self.gravity, "feed_speed": self.feed_speed,
                "source_cadence_speed": self.source_speed,
                "contact_hz": 1.0 / min(1 / 120, self.radius / 60.0),
                "contacts": sum(layer.contacts for layer in self.layers),
                "stream_contacts": sum(layer.stream_contacts for layer in self.layers),
                "landings": sum(layer.landings for layer in self.layers),
                "passed": self.passed, "absorbed": self.absorbed,
                "returned_to_bulk": self.returned_to_bulk,
                "finished": self.finished,
                "live": sum(len(layer.grains) for layer in self.layers),
                "radius": self.radius, "half_width": self.half_width}


class GrainCanvas:
    def __init__(self, canvas, before=None, bulk_canvas=None):
        from kivy.graphics import Color, InstructionGroup, Mesh
        self.group = InstructionGroup()
        self.bulk_group = InstructionGroup()
        self.bulk_batches = []
        for _ in range(5):
            color = Color(1, 1, 1, 1)
            mesh = Mesh(vertices=[], indices=[], mode="triangles")
            self.bulk_group.add(color)
            self.bulk_group.add(mesh)
            self.bulk_batches.append((color, mesh))
        if bulk_canvas is None:
            self.group.add(self.bulk_group)
        else:
            bulk_canvas.add(self.bulk_group)
        self.batches = []
        for _ in range(12):
            color = Color(1, 1, 1, 1)
            mesh = Mesh(vertices=[], indices=[], mode="triangles")
            self.group.add(color)
            self.group.add(mesh)
            self.batches.append((color, mesh))
        if before is None:
            canvas.add(self.group)
        else:
            canvas.insert(canvas.children.index(before), self.group)

    def draw(self, widget, solver):
        buckets = [([], []) for _ in self.batches]
        material = widget._current_material()
        for color, mesh in self.bulk_batches:
            mesh.vertices, mesh.indices = [], []
            mesh.texture = material.texture if material is not None else None
            color.rgb = (1, 1, 1) if material is not None else widget.sand_base
        if solver is not None:
            side = widget._neck_sand_side()
            top = side[0][1] if side else -math.inf
            start, _, _ = widget._transfer_timing()
            filling_front = side[-1][1] if side and widget.elapsed < start else -math.inf
            source = solver.origin + solver.source_y * solver.scale
            blend = solver.radius * solver.scale * 2
            if top > source - blend:
                stops = [top, source + blend]
                stops.extend(source + blend - 2 * blend * i / 4 for i in range(1, 5))
                for i, (high, low) in enumerate(zip(stops, stops[1:])):
                    high, low = min(top, high), max(low, filling_front)
                    if high <= low:
                        continue
                    ys = sorted({high, low, *(y for _, y in widget._taper["in_pts"]
                                             if low < y < high)}, reverse=True)
                    vertices, indices = [], []
                    diameter = widget._R_inner * 2
                    for y in ys:
                        half = widget._neck_width_at(y)
                        for x in (-half, half):
                            vertices.extend((widget._cx + x, y, 0.5 + x / diameter,
                                             0.5 + (widget._upper_sand_bot - y) / diameter))
                    for row in range(len(ys) - 1):
                        n = row * 2
                        indices.extend((n, n + 1, n + 2, n + 1, n + 3, n + 2))
                    color, mesh = self.bulk_batches[i]
                    color.a = 1 if i == 0 else 1 - (i - 0.5) / 4
                    mesh.vertices, mesh.indices = vertices, indices
            for layer in solver.layers:
                for grain in layer.grains:
                    body = grain.body
                    x = solver.cx + body.position.x * solver.scale
                    y = solver.origin + body.position.y * solver.scale
                    if y < filling_front or (
                            body.position.y > solver.outlet and y > top and grain.mass == 0.0) or (
                            grain.landed_at is not None
                                   and y < widget._mound_top_at(x) - grain.radius * solver.scale):
                        continue
                    vertices, indices = buckets[grain.tone]
                    n = len(vertices) // 4
                    c, s = math.cos(body.angle), math.sin(body.angle)
                    u, v = (grain.tone * 0.371 + 0.1) % 0.9, (grain.tone * 0.713 + 0.2) % 0.9
                    uv_scale = solver.scale / (2 * widget._R_inner)
                    vertices.extend((x, y, u, v))
                    for vx, vy in grain.vertices:
                        vertices.extend((x + (vx * c - vy * s) * solver.scale,
                                         y + (vx * s + vy * c) * solver.scale,
                                         u + vx * uv_scale, v + vy * uv_scale))
                    for j in range(len(grain.vertices)):
                        indices.extend((n, n + 1 + j, n + 1 + (j + 1) % len(grain.vertices)))
        for tone, ((color, mesh), (vertices, indices)) in enumerate(zip(self.batches, buckets)):
            mesh.texture = material.texture if material is not None else None
            if material is not None:
                color.rgb = (1, 1, 1)
            else:
                shade = (tone - 5.5) / 255.0 * 1.2
                color.rgb = tuple(max(0.0, min(1.0, value + shade)) for value in widget.sand_base)
            mesh.vertices, mesh.indices = vertices, indices


def update_widget(widget, dt, coarse=1.8):
    if widget._pymunk_flow is None:
        widget._pymunk_flow = PymunkSand(widget, coarse=coarse)
    widget._sand_active = True
    before = widget._sand_landed
    widget._mound_flow_clock += dt
    widget._pymunk_flow.advance(widget, dt)
    if widget._sand_landed > before:
        if widget._first_impact_clock is None:
            widget._first_impact_clock = widget._mound_flow_clock
        widget._last_impact_clock = widget._mound_flow_clock
    # No fabricated splash trajectory: contact response is rendered by the solver.
    widget._sn = 0
    widget.flares = []
    widget.dusts = []


def draw_widget(widget, colors, remaining):
    widget._pymunk_canvas.draw(widget, widget._pymunk_flow)
    for i, context in enumerate(widget._sand_flow_contexts or ()):
        context.update_flow(widget.elapsed, widget._particle_motion_scale,
                            widget._sand_material, colors,
                            (0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 0.0))
        context["sand_free_ramp"] = 0.0
        context["sand_up_scale"] = 1.0
    widget._bore_color.a = 1 if remaining <= 0.001 else 0
    widget._pause_color.a = 0.55 if not widget.running and 0 < widget.elapsed < widget.duration else 0
    widget._pause_rect.size = widget.size if widget._pause_color.a else (0, 0)
