"""Opt-in Mesh experiment: retain cap geometry and move translations to GLSL."""

from array import array
import math

from kivy.graphics import Mesh, RenderContext

import flow_batch_experiment


VERTEX_SHADER = """
$HEADER$
attribute float end_y;
attribute vec2 cap_offset;
attribute float cap_end;
uniform vec4 sand_color;
void main(void) {
    vec2 position = vPosition + cap_offset;
    if (cap_end > 0.5) {
        position.y = end_y + cap_offset.y;
    }
    frag_color = sand_color;
    gl_Position = projection_mat * modelview_mat * vec4(position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
void main(void) {
    gl_FragColor = frag_color;
}
"""


class GPUFlowBatch(flow_batch_experiment.FlowBatch):
    FORMAT = [
        (b"vPosition", 2, "float"),
        (b"end_y", 1, "float"),
        (b"cap_offset", 2, "float"),
        (b"cap_end", 1, "float"),
    ]

    def __init__(self, group, color, reserve=0):
        super().__init__(group, 2)
        self.color = color
        self._last_color = None
        for chunk in range(math.ceil(reserve / self.CHUNK)):
            self._ensure_part(chunk, min(self.CHUNK, reserve - chunk * self.CHUNK))

    def _ensure_part(self, chunk, count):
        if chunk == len(self.parts):
            mesh = Mesh(fmt=self.FORMAT, mode=self.mode)
            self.group.add(mesh)
            self.parts.append([mesh, array("f"), array("H"), 0, 0])
        part = self.parts[chunk]
        if part[3] < count:
            capacity = min(self.CHUNK, max(32, count, part[3] * 2))
            template = array("f", (
                value for dx, dy, end in self.template
                for value in (0, 0, 0, dx, dy, end)))
            vertices = template * capacity
            indices = array("H", (
                index + i * len(self.template)
                for i in range(capacity) for index in self.indices))
            part[1:4] = vertices, indices, capacity
            part[0].vertices = vertices
        return part

    def update(self, particles, top_limit, motion_scale=1):
        rgba = tuple(self.color.rgba)
        if rgba != self._last_color:
            self.group["sand_color"] = rgba
            self._last_color = rgba
        vertex_count = len(self.template)
        stride = vertex_count * 6
        chunks = math.ceil(len(particles) / self.CHUNK)
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = min(self.CHUNK, len(particles) - start)
            part = self._ensure_part(chunk, count)
            mesh, vertices, indices, capacity, previous = part
            for i in range(count):
                particle = particles[start + i]
                x, bottom = particle["x"], particle["y"]
                top = min(top_limit, bottom + max(
                    2, abs(particle["vy"]) * particle.get("trail_time", 0.08) / motion_scale))
                base, end = i * stride, (i + 1) * stride
                # Native strided copies avoid translating every cap vertex in Python.
                vertices[base:end:6] = array("f", [x]) * vertex_count
                vertices[base + 1:end:6] = array("f", [bottom]) * vertex_count
                vertices[base + 2:end:6] = array("f", [top]) * vertex_count
            mesh.vertices = vertices
            if previous != count:
                mesh.indices = indices[:count * len(self.indices)]
                part[4] = count
        for part in self.parts[chunks:]:
            if part[4]:
                part[0].indices = array("H")
                part[4] = 0


def install(widget_class):
    flow_batch_experiment.install(widget_class)
    build = widget_class._build_dynamic_canvas

    def build_gpu_batches(self):
        build(self)
        for key, (group, color, _pool) in self._stream_pools.items():
            if key[1] != 2:
                continue
            context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
            context.shader.vs = VERTEX_SHADER
            context.shader.fs = FRAGMENT_SHADER
            if not context.shader.success:
                raise RuntimeError("Flow vertex shader failed to compile")
            group.add(context)
            self._flow_batches[key] = GPUFlowBatch(
                context, color, self._flow_reserve_counts[key])

    widget_class._build_dynamic_canvas = build_gpu_batches
    widget_class.flow_renderer = "mesh_gpu_reserved"
