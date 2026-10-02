"""Opt-in endpoint texture batching; cap geometry and palette order are retained."""

from array import array
import math
from struct import Struct

from kivy.graphics import BindTexture, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
from kivy.graphics.texture import Texture

import flow_batch_experiment

def _load_flowcore():
    """原生打包(可选加速): 拿不到就退回下面的 Python 循环。

    本地跑像素闸门时产物在 <repo>/native/(构建脚本的 OUT), 顺手加进 sys.path;
    设备上它在 site-packages 里, 直接 import 就行。
    """
    try:
        import flowcore
    except ImportError:
        import os
        import sys
        # base: 本地跑像素闸门时是工程根(<repo>/pc/apk), 设备上就是 app 目录 ——
        # 设备端把 .so 放在 app 目录, 而 p4a 的 sys.path 主要指向 _python_bundle,
        # 不补这一条 import flowcore 会静默失败(实测没有 ON 提示, 一直走 Python 兜底)。
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for candidate in (os.path.join(base, "native"), base):
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.insert(0, candidate)
        try:
            import flowcore
        except Exception as exc:                        # 别静默: 设备上要靠 logcat 定位
            print("flowcore import failed: %r" % (exc,))
            return None
    print("flowcore native packing: ON")
    return flowcore


flowcore = _load_flowcore()


FLOAT32 = Struct("<f")
# 三个端点(x, bottom, top)连续放在同一个颗粒的 12 字节里 -> 一次 pack_into 写完。
# 每颗粒占 3 个纹素, u = (3i + k + 0.5) / (3 * CHUNK), 步长对所有分块都相同
# (capacity 恒为 CHUNK), 所以 shader 里只要一个 uniform。
FLOAT3 = Struct("<3f")
TEXELS_PER_PARTICLE = 3
TEXEL_STEP_UNIFORM = "texel_step"
CHUNK = flow_batch_experiment.FlowBatch.CHUNK
TEXEL_STEP = 1.0 / (CHUNK * TEXELS_PER_PARTICLE)


VERTEX_SHADER = """
$HEADER$
uniform sampler2D endpoints;
uniform float texel_step;
float read_float(float u) {
    vec4 b = floor(texture2D(endpoints, vec2(u, 0.5)) * 255.0 + 0.5);
    float exponent = mod(b.a, 128.0) * 2.0 + floor(b.b / 128.0);
    if (exponent == 0.0) {
        return 0.0;
    }
    float fraction = b.r + b.g * 256.0 + mod(b.b, 128.0) * 65536.0;
    float sign_value = b.a >= 128.0 ? -1.0 : 1.0;
    return sign_value * (1.0 + fraction / 8388608.0) * exp2(exponent - 127.0);
}
void main(void) {
    float x = read_float(vTexCoords0.x);
    float bottom = read_float(vTexCoords0.x + texel_step);
    float top = read_float(vTexCoords0.x + texel_step * 2.0);
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
            # 固定 capacity = CHUNK: u 步长与分块无关, shader 只需一个 uniform。
            capacity = self.CHUNK
            data = bytearray(capacity * TEXELS_PER_PARTICLE * 4)
            texture = Texture.create(
                size=(capacity * TEXELS_PER_PARTICLE, 1), colorfmt="rgba")
            texture.mag_filter = texture.min_filter = "nearest"

            def reload_data(target):
                target.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")

            reload_data(texture)
            texture.add_reload_observer(reload_data)
            span = capacity * TEXELS_PER_PARTICLE
            vertices = array("f", (
                value for i in range(capacity) for dx, dy, end in self.template
                for value in (dx, dy, (i * TEXELS_PER_PARTICLE + 0.5) / span, end)))
            indices = array("H", (
                index + i * len(self.template)
                for i in range(capacity) for index in self.indices))
            part[1:4] = vertices, indices, capacity
            part[5:7] = texture, data
            part[7].texture = texture
            part[0].vertices = vertices
        return part

    def update(self, particles, top_limit, motion_scale=1):
        pack = FLOAT3.pack_into
        total = len(particles)
        chunks = -(-total // self.CHUNK)
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = total - start
            if count > self.CHUNK:
                count = self.CHUNK
            part = self._ensure_part(chunk, count)
            mesh, _vertices, indices, capacity, previous, texture, data, _binding = part
            # 每颗粒只做 1 次 pack_into(x, bottom, top 连续); 数值与逐字相同。
            if flowcore is not None:
                # 与下面 Python 循环逐字节等价(见 tools/test_native_pack.py);
                # 传本块的切片, 让 C 侧从缓冲 0 偏移写起。
                flowcore.pack_stream(particles[start:start + count], data,
                                     top_limit, motion_scale)
                texture.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
                if previous != count:
                    mesh.indices = indices[:count * len(self.indices)]
                    part[4] = count
                continue
            offset = 0
            for i in range(start, start + count):
                particle = particles[i]
                bottom = particle["y"]
                vy = particle["vy"]
                if vy < 0:
                    vy = -vy
                trail = vy * particle["trail_time"] / motion_scale
                if trail < 2:
                    trail = 2
                top = bottom + trail
                if top > top_limit:
                    top = top_limit
                pack(data, offset, particle["x"], bottom, top)
                offset += 12
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
        # 用 RenderContext 的 __setitem__ 设 uniform(shader[...] 在 Kivy 2.3.0 上不支持
        # 下标赋值, 2.3.1 才加 —— 设备上是 2.3.0, 写 context.shader[...] 会 TypeError 崩)。
        context[TEXEL_STEP_UNIFORM] = TEXEL_STEP
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
        _tl = self._taper["y_bot"]
        _ms = self._particle_motion_scale
        _n = _sx = _sy = _st = 0.0
        _n = 0
        for _k, _b in self._group_stream_particles().items():
            for _q in _b:
                _bb = _q["y"]
                _v = _q["vy"]
                if _v < 0:
                    _v = -_v
                _tr = _v * _q["trail_time"] / _ms
                if _tr < 2:
                    _tr = 2
                _t = _bb + _tr
                if _t > _tl:
                    _t = _tl
                _sx += _q["x"]; _sy += _bb; _st += _t; _n += 1
        with open("ref_probe.log", "a", encoding="utf-8") as _f:
            _f.write("t=%.4f n=%d sx=%.4f sy=%.4f st=%.4f%s" % (
                self.elapsed, _n, _sx, _sy, _st, chr(10)))
        for key, bucket in self._group_stream_particles().items():
            self._flow_batches[key].update(bucket, _tl, _ms)

    widget_class._build_dynamic_canvas = build_texture_batches
    widget_class._draw_stream = draw_texture_batches
    widget_class.flow_renderer = "mesh_endpoint_texture"
