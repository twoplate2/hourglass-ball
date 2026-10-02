"""Opt-in benchmark experiment; production keeps the original Line pools."""

from array import array
import math

from kivy.graphics import Mesh


class FlowBatch:
    CHUNK = 512

    def __init__(self, group, width):
        self.group = group
        self.width = width
        self.parts = []
        if width == 1:
            self.template = [(0, 0, 0), (0, 0, 1)]
            self.indices = [0, 1]
            self.mode = "lines"
        else:
            # Match Kivy Line's 10-segment round caps and its half-width convention.
            angle = math.pi / 2
            half_pi = float(array("f", [math.pi / 2])[0])
            a0, a1 = angle - half_pi, angle + half_pi
            right = (math.cos(a0) * width, math.sin(a0) * width)
            left = (math.cos(a1) * width, math.sin(a1) * width)
            self.template = [
                (*right, 0), (*right, 1), (*left, 1), (*left, 0)]
            self.indices = [0, 1, 2, 0, 2, 3]
            for end, direction, first, last in ((0, -1, 0, 3), (1, 1, 1, 2)):
                center = len(self.template)
                self.template.append((0, 0, end))
                for i in range(9):
                    a = a0 + direction * (a1 - a0) * i / 10
                    vertex = len(self.template)
                    self.template.append((math.cos(a) * width, math.sin(a) * width, end))
                    self.indices.extend((center, first if i == 0 else vertex - 1, vertex))
                self.indices.extend((center, len(self.template) - 1, last))
            self.mode = "triangles"

    def update(self, particles, top_limit, motion_scale=1):
        stride = len(self.template) * 4
        index_stride = len(self.indices)
        chunks = math.ceil(len(particles) / self.CHUNK)
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = min(self.CHUNK, len(particles) - start)
            if chunk == len(self.parts):
                mesh = Mesh(mode=self.mode)
                self.group.add(mesh)
                self.parts.append([mesh, array("f"), array("H"), 0, 0])
            part = self.parts[chunk]
            mesh, vertices, indices, capacity, previous = part
            if capacity < count:
                capacity = min(self.CHUNK, max(32, count, capacity * 2))
                vertices = array("f", [0]) * (capacity * stride)
                indices = array("H", (
                    index + i * len(self.template)
                    for i in range(capacity) for index in self.indices))
                part[1:4] = vertices, indices, capacity
            for i in range(count):
                particle = particles[start + i]
                x, bottom = particle["x"], particle["y"]
                top = min(top_limit, bottom + max(
                    2, abs(particle["vy"]) * particle.get("trail_time", 0.08) / motion_scale))
                base = i * stride
                for vertex, (dx, dy, end) in enumerate(self.template):
                    offset = base + vertex * 4
                    vertices[offset] = x + dx
                    vertices[offset + 1] = (top if end else bottom) + dy
            mesh.vertices = vertices
            if previous != count:
                mesh.indices = indices[:count * index_stride]
                part[4] = count
        for part in self.parts[chunks:]:
            if part[4]:
                part[0].indices = array("H")
                part[4] = 0


def install(widget_class):
    build = widget_class._build_dynamic_canvas

    def build_batches(self):
        build(self)
        self._flow_batches = {}
        self._flow_reserve_counts = {
            key: len(pool) for key, (_group, _color, pool) in self._stream_pools.items()}
        for key, (group, color, pool) in self._stream_pools.items():
            group.clear()
            group.add(color)
            pool.clear()
            self._flow_batches[key] = FlowBatch(group, key[1])

    def draw_batches(self):
        group_particles = getattr(self, "_group_stream_particles", None)
        if group_particles is not None:
            group_particles()
            top_limit = self._taper["y_bot"]
        else:
            for bucket in self._stream_buckets.values():
                bucket.clear()
            div = max(1, self._neck_y - self._glass_bot) / len(self._color_table)
            top_limit = 2 * self._neck_y - self._taper["y_bot"]
            for particle in self.particles:
                if particle["y"] >= top_limit:
                    continue
                index = -1 if particle["is_light"] else max(
                    0, min(len(self._color_table) - 1, int((self._neck_y - particle["y"]) / div)))
                self._stream_buckets[index, particle["size"]].append(particle)
        for key, bucket in self._stream_buckets.items():
            self._flow_batches[key].update(
                bucket, top_limit, self._particle_motion_scale)

    widget_class._build_dynamic_canvas = build_batches
    widget_class._draw_stream = draw_batches
    widget_class.flow_renderer = "mesh_batch"
