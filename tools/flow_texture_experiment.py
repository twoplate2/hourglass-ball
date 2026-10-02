"""Opt-in endpoint texture batching; cap geometry and palette order are retained."""

from array import array
import math
from struct import Struct

from kivy.graphics import BindTexture, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
from kivy.graphics.texture import Texture

import flow_batch_experiment


FLOAT32 = Struct("<f")

VERTEX_SHADER = """
$HEADER$
uniform sampler2D endpoints;
float read_float(float u, float row) {
    vec4 b = floor(texture2D(endpoints, vec2(u, row)) * 255.0 + 0.5);
    float exponent = mod(b.a, 128.0) * 2.0 + floor(b.b / 128.0);
    if (exponent == 0.0) {
        return 0.0;
    }
    float fraction = b.r + b.g * 256.0 + mod(b.b, 128.0) * 65536.0;
    float sign_value = b.a >= 128.0 ? -1.0 : 1.0;
    return sign_value * (1.0 + fraction / 8388608.0) * exp2(exponent - 127.0);
}
void main(void) {
    float x = read_float(vTexCoords0.x, 0.125);
    float bottom = read_float(vTexCoords0.x, 0.375);
    float top = read_float(vTexCoords0.x, 0.625);
    vec2 position = vec2(x, mix(bottom, top, vTexCoords0.y)) + vPosition;
    frag_color = color * vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
void main(void) {
    gl_FragColor = frag_color;
}
"""


class TextureFlowBatch(flow_batch_experiment.FlowBatch):
    def __init__(self, group, width, reserve=0):
        super().__init__(group, width)
        for chunk in range(math.ceil(reserve / self.CHUNK)):
            self._ensure_part(chunk, min(self.CHUNK, reserve - chunk * self.CHUNK))

    def _ensure_part(self, chunk, count):
        if chunk == len(self.parts):
            binding = BindTexture(index=1)
            mesh = Mesh(mode=self.mode)
            self.group.add(binding)
            self.group.add(mesh)
            self.parts.append([mesh, array("f"), array("H"), 0, 0, None, None, binding])
        part = self.parts[chunk]
        if part[3] < count:
            capacity = min(self.CHUNK, 1 << (max(32, count) - 1).bit_length())
            data = bytearray(capacity * 4 * 4)
            texture = Texture.create(size=(capacity, 4), colorfmt="rgba")
            texture.mag_filter = texture.min_filter = "nearest"

            def reload_data(target):
                target.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")

            reload_data(texture)
            texture.add_reload_observer(reload_data)
            vertices = array("f", (
                value for i in range(capacity) for dx, dy, end in self.template
                for value in (dx, dy, (i + 0.5) / capacity, end)))
            indices = array("H", (
                index + i * len(self.template)
                for i in range(capacity) for index in self.indices))
            part[1:4] = vertices, indices, capacity
            part[5:7] = texture, data
            part[7].texture = texture
            part[0].vertices = vertices
        return part

    def update(self, particles, top_limit, motion_scale=1):
        pack = FLOAT32.pack_into
        total = len(particles)
        chunks = -(-total // self.CHUNK)
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = total - start
            if count > self.CHUNK:
                count = self.CHUNK
            part = self._ensure_part(chunk, count)
            mesh, _vertices, indices, capacity, previous, texture, data, _binding = part
            row = capacity * 4
            row2 = row * 2
            # 逐颗粒只做 3 次 pack_into;max/min 与下标乘法都换成条件与累加,
            # 数值与原来逐字相同(见 README 经验教训:改热循环必须先过像素一致性)。
            offset = 0
            for i in range(start, start + count):
                particle = particles[i]
                bottom = particle["y"]
                vy = particle["vy"]
                if vy < 0:
                    vy = -vy
                trail = vy * particle.get("trail_time", 0.08) / motion_scale
                if trail < 2:
                    trail = 2
                top = bottom + trail
                if top > top_limit:
                    top = top_limit
                pack(data, offset, particle["x"])
                pack(data, row + offset, bottom)
                pack(data, row2 + offset, top)
                offset += 4
            texture.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
            if previous != count:
                mesh.indices = indices[:count * len(self.indices)]
                part[4] = count
        for part in self.parts[chunks:]:
            if part[4]:
                part[0].indices = array("H")
                part[4] = 0


def install(widget_class):
    build = widget_class._build_dynamic_canvas

    def build_texture_batches(self):
        build(self)
        units = glGetIntegerv(GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)[0]
        if units < 1:
            raise RuntimeError("Vertex texture sampling is unavailable")
        context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        context.shader.vs = VERTEX_SHADER
        context.shader.fs = FRAGMENT_SHADER
        if not context.shader.success:
            raise RuntimeError("Endpoint texture shader failed to compile")
        context["endpoints"] = 1
        first_group = next(iter(self._stream_pools.values()))[0]
        position = self.canvas.children.index(first_group)
        self._flow_batches = {}
        for key, (group, color, pool) in self._stream_pools.items():
            reserve = len(pool)
            self.canvas.remove(group)
            group.clear()
            group.add(color)
            pool.clear()
            context.add(group)
            self._flow_batches[key] = TextureFlowBatch(group, key[1], reserve)
        self.canvas.insert(position, context)
        self._flow_texture_context = context

    def draw_texture_batches(self):
        for key, bucket in self._group_stream_particles().items():
            self._flow_batches[key].update(
                bucket, self._taper["y_bot"], self._particle_motion_scale)

    widget_class._build_dynamic_canvas = build_texture_batches
    widget_class._draw_stream = draw_texture_batches
    widget_class.flow_renderer = "mesh_endpoint_texture"
