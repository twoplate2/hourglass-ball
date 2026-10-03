"""Endpoint texture batching: 24 个桶共享**一块**端点纹理, 每帧 1 次 blit、0 次索引上传。

为什么改(设备实测, MuMu 15 秒档 2900 颗粒)
--------------------------------------------
旧实现是"每个桶每 CHUNK 一块纹理 + 一个 Mesh": 峰值约 25 次 `texture.blit_buffer`
+ 25 次 `mesh.indices = ...`。两者都不便宜:

* `blit_buffer` 当场就是一次 GL 上传(redraw 里真正的 GL 调用);
* `mesh.indices = ...` 在 Kivy 里走 `Mesh.build() -> VertexBatch.set_data()` ——
  **它会把整份顶点数组重新拷进 VBO 再全量重传**(见 kivy/graphics/vbo.pyx:
  `set_data` = `clear_data()` + `append_data()` + `V_NEEDUPLOAD`, 上传时
  `glBufferSubData(GL_ARRAY_BUFFER, 0, self.data.size(), ...)`)。所以这一栏的成本
  随**块数**线性增长、与粒子数无关, 正是 `图元` = 3.29ms 的主因。

合并后每帧只剩: **1 次 blit + N 次 draw**, 顶点/索引缓冲是**静态的**(只在建几何
或槽位容量变化时上传一次)。顶点的每帧更新在新方案里不复存在。

为什么**没有**按线宽合并成 2 个 Mesh(实测否定, 别再试第二次)
--------------------------------------------------------------
"24 个桶合并成 2 个 Mesh"做不到像素一致 —— 不是败在纹理/索引, 而是败在**绘制顺序**:
屏幕上 alpha=1 (所有桶的 Color 都是 3 元组) 时混合是**不透明覆写, 后画的赢**, 所以两颗
不同色调的颗粒重叠处, 谁在上取决于绘制顺序。旧实现 24 个桶的顺序是**按颜色档交替线宽**:
(0,1),(0,2),(1,1),(1,2),...; 按线宽合并后必然变成"所有线宽 1 在前、所有线宽 2 在后",
重叠处就会翻面。实测(400x800, `inspect_flow --dense-neck` 104 帧):

* 合并成 2 个 Mesh: 88 帧不同、合计 36436 像素、最大通道差 18/255, 全部落在重叠像素上;
* 对照实验: 把**旧实现自己**的 24 个桶绘制顺序反转, 差异反而更大(571277 像素) ——
  证明这就是顺序效应, 不是新实现的 bug;
* 端点数据本身逐字节相同(同场景 dump `(x, bottom, top)` 字节 md5 一致), 激活集合也一致。

因此默认 `MERGE_WIDTHS = False`(每桶一块 Mesh, 顺序与旧实现逐字一致, 闸门 0 差异)。
想换那 22 次 draw 的人可以打开它, 但要接受上面那份重叠像素差异。

怎么做
------
* 一块 2D 端点纹理 `(ROW_TEXELS=CHUNK*4, 行数)`; 每个桶的每个 CHUNK 占**一行**。
  每颗粒 4 个纹素: `x / bottom / top / 激活标记`(只读第 4 个纹素的 a 通道)。
  激活标记让"这一槽位本帧有没有粒子"变成**着色器里的事**, 于是索引缓冲可以静态:
  顶点着色器把没粒子的槽位顶点送到裁剪体外 —— 整块图元被丢弃, 与旧实现里把
  `mesh.indices` 截断到 count 的可见结果一致。
* `u` 步长是全局唯一的 `texel_step = 1/ROW_TEXELS`(uniform, 建上下文时设一次),
  所以**所有桶可以共用一块纹理**; 行号由每顶点属性 `vRow` 传进去
  (`vTexCoords0.y` 已被用来做端点插值, 不能占用)。
* 沙色按 **每顶点属性 `vColor`** 走(合并成一个 Mesh 时一个 Mesh 里会有多种颜色,
  只能逐顶点带)。vColor 与原来 `color` uniform 是**同一个 float32 值**, 且两者都经
  "顶点着色器算 frag_color -> 插值"这条同一路径, 所以像素不变(闸门已证)。
* 顶点/索引缓冲静态化后容量必须**预留**: 某一槽位真的超出容量(极罕见, 预留本来就
  是上界)时重建一次纹理与网格 —— 见 `_grow`。

像素闸门: `tools/inspect_flow.py --texture-flow --dense-neck` 104 张图与旧实现逐像素 0 差异。
"""

from array import array
from struct import Struct

try:
    import numpy as np
except ImportError:                          # 兜底: 退回逐颗粒 pack_into / 逐顶点生成
    np = None

from kivy.graphics import BindTexture, Color, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
from kivy.graphics.texture import Texture

import flow_batch_experiment


FLOAT3 = Struct("<3f")
CHUNK = flow_batch_experiment.FlowBatch.CHUNK
# False = 每个桶一块 Mesh(绘制顺序与旧实现逐字一致 -> 像素闸门 0 差异, 默认);
# True  = 按线宽合并成 2 个 Mesh(每帧少 22 次 draw, 但重叠像素的压盖顺序会变,
#         实测 104 帧 36436 像素 / 最大 18/255 的差异 —— 见模块头)。
MERGE_WIDTHS = False
# 每颗粒 4 个纹素: x / bottom / top 各一个 float32, 第 4 个纹素只用到 a 通道做激活标记
TEXELS_PER_PARTICLE = 4
BYTES_PER_PARTICLE = TEXELS_PER_PARTICLE * 4
ROW_TEXELS = CHUNK * TEXELS_PER_PARTICLE
TEXEL_STEP = 1.0 / ROW_TEXELS
# Kivy 的 Mesh 走 unsigned short 索引, 且 build() 里有 65535 的硬上限
# (vertex_instructions.pyx: "Cannot upload more than 65535 indices")。超过就再切一块。
MAX_INDICES = 65535
ACTIVE_WORD = 0xFFFFFFFF       # RGBA = (255,255,255,255) -> a = 1.0
INACTIVE_WORD = 0x00FFFFFF     # RGBA = (255,255,255,0)   -> a = 0.0

VERTEX_FMT = [(b"vPosition", 2, "float"), (b"vTexCoords0", 2, "float"),
              (b"vRow", 1, "float"), (b"vColor", 4, "float")]
VERTEX_FLOATS = 9


VERTEX_SHADER = """
$HEADER$
attribute float vRow;
attribute vec4 vColor;
uniform sampler2D endpoints;
uniform float texel_step;
float read_float(vec2 uv) {
    vec4 b = floor(texture2D(endpoints, uv) * 255.0 + 0.5);
    float exponent = mod(b.a, 128.0) * 2.0 + floor(b.b / 128.0);
    if (exponent == 0.0) {
        return 0.0;
    }
    float fraction = b.r + b.g * 256.0 + mod(b.b, 128.0) * 65536.0;
    float sign_value = b.a >= 128.0 ? -1.0 : 1.0;
    return sign_value * (1.0 + fraction / 8388608.0) * exp2(exponent - 127.0);
}
void main(void) {
    vec2 uv = vec2(vTexCoords0.x, vRow);
    // 激活标记在每颗粒第 4 个纹素的 a 通道。未激活 -> 送到裁剪体外(整块图元被丢弃)。
    if (texture2D(endpoints, uv + vec2(texel_step * 3.0, 0.0)).a < 0.5) {
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
        return;
    }
    float x = read_float(uv);
    float bottom = read_float(uv + vec2(texel_step, 0.0));
    float top = read_float(uv + vec2(texel_step * 2.0, 0.0));
    vec2 position = vec2(x, mix(bottom, top, vTexCoords0.y)) + vPosition;
    frag_color = vColor * vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
void main(void) {
    gl_FragColor = frag_color;
}
"""


class _Slot:
    """一个桶在一个 CHUNK 内的槽位 —— 对应端点纹理的一行。"""

    __slots__ = ("key", "row", "capacity", "offset", "start", "count", "vstart")

    def __init__(self, key, row, capacity, start):
        self.key = key
        self.row = row
        self.capacity = capacity
        self.offset = row * ROW_TEXELS * 4
        self.start = start          # 该槽位在桶内下标列表里的起点
        self.count = 0              # 上一帧画了几颗(用于只在变化区间改激活标记)
        self.vstart = 0             # 在所属网格顶点数组里的起始顶点号


def _float_buffer(values):
    """numpy 数组 -> array("f")(Kivy Mesh 顶点接受的最保险的缓冲类型)。"""
    buf = array("f")
    buf.frombytes(values.tobytes())
    return buf


class MergedFlow:
    """24 个桶共享的端点纹理 + 每桶一块(或按线宽合并的 2 块)静态 Mesh。"""

    def __init__(self, widget):
        self.widget = widget
        self.context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        self.context.shader.vs = VERTEX_SHADER
        self.context.shader.fs = FRAGMENT_SHADER
        if not self.context.shader.success:
            raise RuntimeError("Endpoint texture shader failed to compile")
        # uniform 只在建上下文时设一次: Kivy 的 context[...] 在 2.3.0 上可用,
        # context.shader[...] 是 2.3.1 才有的(写了会在画布构建时崩)。
        self.context["texel_step"] = TEXEL_STEP
        self.context["endpoints"] = 1

        pools = widget._stream_pools
        self._order = list(pools)
        self.reserves = {key: len(pool) for key, (_group, _color, pool) in pools.items()}
        groups = [pools[key][0] for key in self._order]
        position = widget.canvas.children.index(groups[0])
        for group in groups:
            widget.canvas.remove(group)
        # 逐桶 Color 指令没了; 保留下同一条(alpha=1), 使 opacity uniform 与旧实现一致。
        self.color = Color(*widget.sand_light)
        self.context.add(self.color)
        self.binding = BindTexture(index=1)
        self.context.add(self.binding)
        widget.canvas.insert(position, self.context)
        self._colors = None
        self.mesh_records = []
        self.meshes = []
        self.slots = {}
        self._build()

    # ---------------- 建几何 ----------------

    def _build(self):
        rows = 0
        self.slots = {}
        for key in self._order:
            capacity = max(1, self.reserves[key])
            slots = []
            start = 0
            while True:
                size = min(CHUNK, capacity - start)
                if size <= 0:
                    break
                slots.append(_Slot(key, rows, size, start))
                rows += 1
                start += size
            self.slots[key] = slots
        self.rows = rows

        for mesh in self.meshes:
            self.context.remove(mesh)
        self.meshes = []
        self.mesh_records = []
        self.words = None          # 先松开旧的 np.frombuffer 视图再换缓冲

        data = bytearray(ROW_TEXELS * 4 * rows)
        self.data = data

        def reload_data(target, data=data):
            target.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")

        texture = Texture.create(size=(ROW_TEXELS, rows), colorfmt="rgba")
        texture.mag_filter = texture.min_filter = "nearest"
        reload_data(texture)
        texture.add_reload_observer(reload_data)
        self.texture = texture
        self.binding.texture = texture
        if np is not None:
            self.words = np.frombuffer(data, dtype=np.uint32).reshape(rows, ROW_TEXELS)

        colors = self._bucket_colors(self.widget)
        self._colors = (self.widget.sand_base, self.widget.sand_light)
        if MERGE_WIDTHS:
            # 按线宽合并成 2 个 Mesh(上限 65535 索引, 太大时同一线宽再切几块)
            layout = [(width, [slot for key in self._order if key[1] == width
                               for slot in self.slots[key]]) for width in (1, 2)]
        else:
            # 每个桶一块 —— 绘制顺序与旧实现逐字一致(见 MERGE_WIDTHS 注释), 像素闸门 0 差异
            layout = [(key[1], self.slots[key]) for key in self._order]
        for width, slots in layout:
            if not slots:
                continue
            base = flow_batch_experiment.FlowBatch(None, width)
            pending = []
            used = 0
            for slot in slots:
                if pending and (used + slot.capacity) * len(base.indices) > MAX_INDICES:
                    self._make_mesh(base, pending, colors)
                    pending, used = [], 0
                pending.append(slot)
                used += slot.capacity
            if pending:
                self._make_mesh(base, pending, colors)

    def _bucket_colors(self, widget):
        table = widget._color_table
        return {key: (tuple(table[key[0]]) if key[0] >= 0 else tuple(widget.sand_light))
                for key in self._order}

    def _make_mesh(self, base, slots, colors):
        template = base.template
        index_template = base.indices
        vpp, ipc = len(template), len(index_template)
        total = sum(slot.capacity for slot in slots)
        mesh = Mesh(fmt=VERTEX_FMT, mode=base.mode)
        if np is not None:
            verts = np.empty((total * vpp, VERTEX_FLOATS), dtype=np.float32)
            index = np.empty(total * ipc, dtype=np.uint32)
            tpl = np.array(template, dtype=np.float32)
            tpl_idx = np.array(index_template, dtype=np.uint32)
            vpos = 0
            ipos = 0
            for slot in slots:
                count = slot.capacity
                slot.vstart = vpos
                verts[vpos:vpos + count * vpp, 0] = np.tile(tpl[:, 0], count)
                verts[vpos:vpos + count * vpp, 1] = np.tile(tpl[:, 1], count)
                u = (np.arange(count, dtype=np.float32) * TEXELS_PER_PARTICLE + 0.5) * TEXEL_STEP
                verts[vpos:vpos + count * vpp, 2] = np.repeat(u, vpp)
                verts[vpos:vpos + count * vpp, 3] = np.tile(tpl[:, 2], count)
                verts[vpos:vpos + count * vpp, 4] = (slot.row + 0.5) / self.rows
                verts[vpos:vpos + count * vpp, 5:8] = colors[slot.key]
                verts[vpos:vpos + count * vpp, 8] = 1.0
                index[ipos:ipos + count * ipc] = (
                    np.repeat(np.arange(count, dtype=np.uint32) * vpp, ipc)
                    + np.tile(tpl_idx, count) + vpos)
                vpos += count * vpp
                ipos += count * ipc
            mesh.vertices = _float_buffer(verts)
            index_buf = array("H")
            index_buf.frombytes(index.astype("uint16").tobytes())
        else:
            vertex_values = array("f")
            index_values = array("H")
            vpos = 0
            for slot in slots:
                count = slot.capacity
                slot.vstart = vpos
                row_v = (slot.row + 0.5) / self.rows
                color = colors[slot.key]
                block = array("f", (
                    value
                    for i in range(count)
                    for dx, dy, end in template
                    for value in (dx, dy, (i * TEXELS_PER_PARTICLE + 0.5) * TEXEL_STEP, end,
                                  row_v, color[0], color[1], color[2], 1.0)))
                vertex_values.extend(block)
                # 每颗粒的顶点基址 i*vpp 不能漏 —— 漏了就变成"整桶都画第 0 颗粒的顶点"
                index_values.extend(array("H", (
                    i * vpp + value + vpos
                    for i in range(count) for value in index_template)))
                vpos += count * vpp
            mesh.vertices = vertex_values
            index_buf = index_values
        mesh.indices = index_buf
        self.context.add(mesh)
        self.meshes.append(mesh)
        self.mesh_records.append((mesh, slots, vpp, None if np is None else verts))

    # ---------------- 每帧 ----------------

    def active_counts(self):
        return {key: sum(slot.count for slot in slots) for key, slots in self.slots.items()}

    def capacity(self, key):
        return sum(slot.capacity for slot in self.slots[key])

    def packed_endpoints(self, key, limit=1):
        """读回某桶前 limit 个槽位头颗粒的 (x, bottom, top) —— 只给验证工具用。"""
        out = []
        for slot in self.slots[key]:
            if not slot.count or len(out) >= limit:
                continue
            raw = bytes(self.data[slot.offset:slot.offset + 12])
            out.append(Struct("<3f").unpack(raw))
        return out

    def update(self, widget, buckets):
        view = widget._pv
        top_limit = widget._taper["y_bot"]
        motion_scale = widget._particle_motion_scale
        colors = (widget.sand_base, widget.sand_light)
        if colors != self._colors:
            self._sync_colors(widget)
        needs = {key: len(indices) for key, indices in buckets.items()
                 if len(indices) > self.capacity(key)}
        if needs:
            self._grow(widget, needs)
        for key, indices in buckets.items():
            self._write_bucket(view, indices, self.slots[key], top_limit, motion_scale)
        self.texture.blit_buffer(self.data, colorfmt="rgba", bufferfmt="ubyte")

    def _write_bucket(self, view, indices, slots, top_limit, motion_scale):
        """`view` 是 widget 的 `_pv`(本帧 list 快照), `indices` 是本桶的粒子下标。"""
        use_np = np is not None and len(indices) > 0 and view.use_np
        nidx = np.array(indices, dtype=np.intp) if use_np else None
        xs = view.x
        ys = view.y
        vys = view.vy
        trails = view.tl
        total = len(indices)
        start = 0
        for slot in slots:
            count = total - start
            if count > slot.capacity:
                count = slot.capacity
            elif count < 0:
                count = 0
            previous = slot.count
            if count:
                if use_np:
                    self._write_slot_np(view, nidx, start, slot, count,
                                        previous, top_limit, motion_scale)
                else:
                    pack = FLOAT3.pack_into
                    data = self.data
                    offset = slot.offset
                    for k in range(count):
                        i = indices[start + k]
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
                        pack(data, offset + k * BYTES_PER_PARTICLE, xs[i], bottom, top)
                    if count != previous:
                        self._flag_range(slot, min(count, previous), max(count, previous),
                                         count > previous)
            elif previous:
                # 该槽位这一帧没有粒子: 只清激活标记(端点字节留给下一次覆写)
                if np is not None:
                    self.words[slot.row, 3:previous * TEXELS_PER_PARTICLE:4] = INACTIVE_WORD
                else:
                    self._flag_range(slot, 0, previous, False)
            slot.count = count
            start += count

    def _write_slot_np(self, view, nidx, start, slot, count, previous, top_limit, motion_scale):
        idx = nidx[start:start + count]
        bottom = view.ny[idx]
        # 算式与逐颗粒路径逐字相同: |vy| -> trail -> 下限 2 -> top -> 上限 top_limit
        vy = np.abs(view.nvy[idx])
        trail = vy * view.ntl[idx] / motion_scale
        np.maximum(trail, 2.0, out=trail)
        top = bottom + trail
        np.minimum(top, top_limit, out=top)
        blk = np.empty((count, 3), dtype=np.float64)
        blk[:, 0] = view.nx[idx]
        blk[:, 1] = bottom
        blk[:, 2] = top
        row = self.words[slot.row]
        seg = row[:count * TEXELS_PER_PARTICLE].reshape(count, TEXELS_PER_PARTICLE)
        seg[:, :3] = blk.astype("<f4").view(np.uint32)
        # 激活标记必须写在**整行**的视图上: seg 只覆盖前 count 颗粒, 用它去切片
        # [count, previous) 是空切片, 曾导致收缩的槽位留着上一帧的陈旧端点被继续绘制。
        if count > previous:
            row[previous * TEXELS_PER_PARTICLE + 3:count * TEXELS_PER_PARTICLE:4] = ACTIVE_WORD
        elif count < previous:
            row[count * TEXELS_PER_PARTICLE + 3:previous * TEXELS_PER_PARTICLE:4] = INACTIVE_WORD

    def _flag_range(self, slot, low, high, active):
        data = self.data
        offset = slot.offset
        mark = 255 if active else 0
        for k in range(low, high):
            data[offset + k * BYTES_PER_PARTICLE + 15] = mark

    def _sync_colors(self, widget):
        """换沙色: 顶点带的是颜色, 只能改顶点缓冲(比逐桶改 Color uniform 贵, 但换色是罕见操作)。"""
        colors = self._bucket_colors(widget)
        for mesh, slots, vpp, verts in self.mesh_records:
            if verts is not None:
                for slot in slots:
                    verts[slot.vstart:slot.vstart + slot.capacity * vpp, 5:8] = colors[slot.key]
                mesh.vertices = _float_buffer(verts)
            else:
                buf = mesh.vertices
                for slot in slots:
                    color = colors[slot.key]
                    base = slot.vstart * VERTEX_FLOATS
                    for i in range(slot.capacity * vpp):
                        offset = base + i * VERTEX_FLOATS + 5
                        buf[offset] = color[0]
                        buf[offset + 1] = color[1]
                        buf[offset + 2] = color[2]
                mesh.vertices = buf
        self._colors = (widget.sand_base, widget.sand_light)

    def _grow(self, widget, needs):
        """槽位真的超出预留(罕见)时扩容并重建 —— 顶点/索引是静态的, 只能整体重来。"""
        for key, wanted in needs.items():
            capacity = self.reserves[key]
            while capacity < wanted:
                capacity += CHUNK if capacity < CHUNK else capacity
            self.reserves[key] = capacity
        self._build()
        self._colors = (widget.sand_base, widget.sand_light)


def install(widget_class):
    build = widget_class._build_dynamic_canvas

    def build_texture_batches(self):
        build(self)
        units = glGetIntegerv(GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)[0]
        if units < 1:
            raise RuntimeError("Vertex texture sampling is unavailable")
        self._flow_merged = MergedFlow(self)
        self._flow_texture_context = self._flow_merged.context

    def draw_texture_batches(self):
        self._flow_merged.update(self, self._group_stream_particles())

    widget_class._build_dynamic_canvas = build_texture_batches
    widget_class._draw_stream = draw_texture_batches
    widget_class.flow_renderer = "mesh_endpoint_texture"
