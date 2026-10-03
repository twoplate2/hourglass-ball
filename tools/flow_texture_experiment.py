"""Opt-in endpoint texture batching; cap geometry and palette order are retained."""

from array import array
import math
from struct import Struct

try:
    import numpy as np
except ImportError:                          # 兜底: 退回逐颗粒 pack_into
    np = None

from kivy.graphics import BindTexture, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
from kivy.graphics.texture import Texture

import flow_batch_experiment


FLOAT32 = Struct("<f")
# 三个端点(x, bottom, top)连续放在同一个颗粒的 12 字节里 -> 一次 pack_into 写完。
# 每颗粒占 3 个纹素, u = (3i + k + 0.5) / (3 * CHUNK), 步长对所有分块都相同
# (capacity 恒为 CHUNK), 所以 shader 里只要一个 uniform。
FLOAT3 = Struct("<3f")
TEXELS_PER_PARTICLE = 3
TEXEL_STEP_UNIFORM = "texel_step"
CHUNK = flow_batch_experiment.FlowBatch.CHUNK

# ---- 诊断计数(每帧由 update() 清零) -------------------------------------------
# 为什么需要: `mesh.indices = ...` 的 setter 会 `flag_data_update()` →
# `VertexInstruction.apply()`(每帧) 看到 GI_NEEDS_UPDATE 就 `build()` →
# `Mesh.build()` 拿**整个 512 槽顶点数组**去 `VertexBatch.set_data()` →
# `clear_data()` + `add_vertex_data(全部顶点)` + `flags |= V_NEEDUPLOAD`。
# **改一次索引 = 整块顶点重新走一遍并标脏上传**, 不是"只改个数字"。
# (源码: vertex_instructions.pyx:485/460, instructions.pyx:429, vbo.pyx:170, Kivy 2.3.0)
# 这里只统计**触发条件**(赋值次数 × 顶点表字节), 不等于实测 GL 上传流量。
STATS = {"index_assigns": 0, "vertex_bytes": 0, "chunk_clears": 0, "chunks": 0,
         "buckets": 0, "neutralized": 0}

# 中性化用的端点: x 推到画面外, 该槽位的线整条被裁掉(出不了像素)。
# shader: position = vec2(x, mix(bottom, top, vTexCoords0.y)) + vPosition
PAD_ENDPOINT = FLOAT3.pack(-1e5, 0.0, 0.0)


def stats_reset():
    for key in STATS:
        STATS[key] = 0
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

    def update(self, view, indices, top_limit, motion_scale=1):
        """`view` 是 widget 的 `_pv`(本帧 list 快照), `indices` 是本桶的粒子下标。

        按下标读原生 float, 不再逐颗粒取 numpy 标量。
        """
        STATS["buckets"] += 1
        ys = view.y
        vys = view.vy
        trails = view.tl
        xs = view.x
        total = len(indices)
        chunks = -(-total // self.CHUNK)
        # 向量化: 本桶所有颗粒的 (x, 底端, 顶端) 一次算完, 再 astype('<f4') 出字节。
        # 逐位等价已实测: astype('<f4') 与 struct.pack('<f') 对 30 万样本(含 0/-0/inf/
        # denormal/float32 极值)完全相同, 整段公式的字节输出也完全相同
        # —— 见 tools/test_pack_equiv.py。
        # 阈值由 _FlowView.use_np 统一决定(见 main.py:_NUMPY_MIN): 粒子少时
        # numpy 的逐桶固定开销盖过收益。
        use_np = np is not None and total > 0 and view.use_np
        if use_np:
            nidx = np.array(indices, dtype=np.intp)
        else:
            pack = FLOAT3.pack_into
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = total - start
            if count > self.CHUNK:
                count = self.CHUNK
            part = self._ensure_part(chunk, count)
            mesh, _vertices, _indices, _capacity, previous, texture, data, _binding = part
            if use_np:
                idx = nidx[start:start + count]
                bottom = view.ny[idx]
                # 算式与逐字相同: (-vy) / (vy*tl)/ms / 下限 2 / 上限 top_limit
                vy = np.abs(view.nvy[idx])
                trail = vy * view.ntl[idx] / motion_scale
                np.maximum(trail, 2.0, out=trail)
                top = bottom + trail
                np.minimum(top, top_limit, out=top)
                blk = np.empty((count, 3), dtype=np.float64)
                blk[:, 0] = view.nx[idx]
                blk[:, 1] = bottom
                blk[:, 2] = top
                # 尾部(count*12 之后)保持上一帧的陈旧字节, 与逐颗粒写法一致:
                # 那部分不渲染(mesh.indices 已按 count 截断)。
                data[:count * 12] = blk.astype("<f4").tobytes()
            else:
                # 每颗粒只做 1 次 pack_into(x, bottom, top 连续); 数值与逐字相同。
                offset = 0
                for k in range(start, start + count):
                    i = indices[k]
                    bottom = ys[i]
                    vy = vys[i]
                    if vy < 0:
                        vy = -vy
                    trail = vy * trails[i] / motion_scale
                    if trail < 2:
                        trail = 2
                    top = bottom + trail
                    if top > top_limit:
                        top = top_limit
                    pack(data, offset, xs[i], bottom, top)
                    offset += 12
            # ⚠️ 顺序要紧: 先把纹理内容(含下面的"中性化")写完, 再上传。
            if count > previous:
                # **只在这里赋值** —— 每块的索引只增不减, 暖机之后基本不再发生。
                # 旧写法是 `previous != count` 就赋值 ⇒ 粒子数一变, 整块 160 KiB 顶点
                # 就走一遍 clear_data+add_vertex_data 并把整个 VBO 标脏。
                # 实测(5s 档, 1529 颗): 24 块全变 ⇒ 24 次赋值 / 2112 KiB / 帧。
                mesh.indices = _indices[:count * len(self.indices)]
                part[4] = count
                STATS["index_assigns"] += 1
                STATS["vertex_bytes"] += len(_vertices) * 4
            elif count < previous:
                # 缩了: **不动 indices**(动了又触发整块重建), 改把用不到的槽位在端点
                # 纹理里推到画面外 —— shader 里 x 直接决定横向位置, -1e5 时整条线被裁掉。
                # 代价是每帧多处理"历史最大 − 当前"那几个顶点, 换来不重走顶点表。
                data[count * 12:previous * 12] = PAD_ENDPOINT * (previous - count)
                STATS["neutralized"] += previous - count
            texture.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
            STATS["chunks"] += 1
        for part in self.parts[chunks:]:
            if part[4]:
                # 整块不用了: 这里仍清空索引 —— icount==0 时 build() 直接 clear_data(),
                # **不会**重走顶点表, 而且实测只有 0~4 次/帧。
                part[0].indices = array("H")
                part[4] = 0
                STATS["chunk_clears"] += 1


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
        for key, bucket in self._group_stream_particles().items():
            view, indices = flow_batch_experiment.flow_bucket(self, bucket)
            self._flow_batches[key].update(
                view, indices, self._taper["y_bot"], self._particle_motion_scale)

    widget_class._build_dynamic_canvas = build_texture_batches
    widget_class._draw_stream = draw_texture_batches
    widget_class.flow_renderer = "mesh_endpoint_texture"
