"""单 Mesh 沙流渲染器(实验): 把"11 档颜色 x 2 种线宽 = 24 个 Mesh"压成 2 个。

动机(2026-10-02 消融实测, 1.2 为底, 2525 颗粒/帧): 关掉粒子批后帧时间
16.35ms -> 8.61ms, 粒子管线独占 7.7ms; 其中 GL 侧 ~3.1ms 花在 **24 次状态切换 +
24 次纹理上传 + 24 次 draw call**; CPU 侧另有一趟 `_group_stream_particles`
给每个粒子 append 进颜色桶。

做法(比"顶点颜色"更省):
- **颜色也写进端点纹理**: 每颗粒占 4 个纹素 —— x / bottom / top / color(RGBA8)。
  前三个是 float32 的字节模式(shader 里按现有 read_float 解码), 第 4 个直接当
  归一化颜色(texture2D 返回 0..1)。
- 于是**顶点数组完全静态**(只有 (i+0.5)/span 与端点选择), 每帧只写纹理:
  每颗粒 **1 次 `struct.pack_into("<3fI", ...)`**, 16 字节连续。
- 不再按颜色分桶 -> **省掉 `_group_stream_particles`**; Mesh 数 = 线宽数 = 2。

接线方式与 tools/flow_texture_experiment.py 同构(它已在线验证过), 只是把
"每色一个 Mesh"换成"每线宽一个 Mesh + 颜色进纹理"。
"""

from array import array
import math
from struct import Struct

from kivy.graphics import BindTexture, Callback, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
from kivy.graphics.texture import Texture

import flow_batch_experiment


FLOAT6 = Struct("<6f")     # x, bottom, top, r, g, b —— 全 float, 逐位精确
TEXELS_PER_PARTICLE = 6
SINGLE_CHUNK = 2048      # 每块颗粒数上限(Kivy Mesh 16 位索引)
TEXEL_STEP_UNIFORM = "texel_step"

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
    float u = vTexCoords0.x;
    float x = read_float(u);
    float bottom = read_float(u + texel_step);
    float top = read_float(u + texel_step * 2.0);
    float cr = read_float(u + texel_step * 3.0);
    float cg = read_float(u + texel_step * 4.0);
    float cb = read_float(u + texel_step * 5.0);;
    vec2 position = vec2(x, mix(bottom, top, vTexCoords0.y)) + vPosition;
    frag_color = vec4(cr, cg, cb, 1.0) * vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
void main(void) {
    gl_FragColor = frag_color;
}
"""


class SingleFlowBatch:
    """一种线宽一个 Mesh; 端点与颜色都放纹理里, 顶点数组静态。"""

    def __init__(self, group, width, capacity):
        base = flow_batch_experiment.FlowBatch(group, width)
        self.template = base.template
        self.index_template = base.indices
        self.mode = base.mode
        self.capacity = capacity
        span = capacity * TEXELS_PER_PARTICLE
        data = bytearray(span * 4)
        texture = Texture.create(size=(span, 1), colorfmt="rgba")
        texture.mag_filter = texture.min_filter = "nearest"

        def reload_data(target):
            target.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")

        reload_data(texture)
        texture.add_reload_observer(reload_data)
        vertices = array("f", (
            value for i in range(capacity)
            for dx, dy, end in self.template
            for value in (dx, dy, (i * TEXELS_PER_PARTICLE + 0.5) / span, end)))
        # Kivy 的 Mesh 只支持 16 位索引(_ensure_ushort_view), 所以每块颗粒数上限
        # = 65536 / len(template); 由 build_single 保证 capacity(=每块) 不超过 2048。
        indices = array("H", (
            index + i * len(self.template)
            for i in range(capacity) for index in self.index_template))
        self.data = data
        self.texture = texture
        self.index_array = indices
        self.mesh = Mesh(mode=self.mode, vertices=vertices, indices=indices)
        group.add(BindTexture(index=1, texture=texture))
        group.add(self.mesh)
        self.count = 0

    def write(self, entries, top_limit):
        """entries: [(x, bottom, top, r, g, b), ...] 按绘制顺序; 每颗粒 1 次 pack_into。"""
        pack = FLOAT6.pack_into
        data = self.data
        offset = 0
        for x, bottom, top, r, g, b in entries:
            if top > top_limit:
                top = top_limit
            pack(data, offset, x, bottom, top, r, g, b)
            offset += 24
        count = len(entries)
        self.texture.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
        if self.count != count:
            self.mesh.indices = self.index_array[:count * len(self.index_template)]
            self.count = count


import os as _os

def _probe(msg):
    try:
        path = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), "single_flow_probe.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + chr(10))
    except OSError:
        pass


def install(widget_class):
    _probe("install() called")
    build = widget_class._build_dynamic_canvas

    def build_single(self):
        _probe("build_single() called")
        build(self)
        units = glGetIntegerv(GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)[0]
        if units < 1:
            raise RuntimeError("Vertex texture sampling is unavailable")
        context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        context.shader.vs = VERTEX_SHADER
        context.shader.fs = FRAGMENT_SHADER
        if not context.shader.success:
            raise RuntimeError("single-mesh shader failed to compile")
        context["endpoints"] = 1
        first_group = next(iter(self._stream_pools.values()))[0]
        position = self.canvas.children.index(first_group)
        # 每块固定 2048 颗粒: Kivy Mesh 的 16 位索引上限决定(26 顶点 x 2048 = 53248)。
        reserve = sum(len(pool) for _g, _c, pool in self._stream_pools.values())
        chunks = max(1, -(-max(64, reserve) // SINGLE_CHUNK))
        self._flow_single_capacity = SINGLE_CHUNK
        context[TEXEL_STEP_UNIFORM] = 1.0 / (SINGLE_CHUNK * TEXELS_PER_PARTICLE)
        for _key, (group, _color, _pool) in list(self._stream_pools.items()):
            self.canvas.remove(group)
            group.clear()
        self._flow_single = {}
        for width in (1, 2):
            self._flow_single[width] = [SingleFlowBatch(context, width, SINGLE_CHUNK)
                                        for _ in range(chunks)]
        self.canvas.insert(position, context)
        self._flow_single_context = context
        self._flow_single_draw = draw_single

    def draw_single(self):
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        top_limit = self._taper["y_bot"]
        scale = self._particle_motion_scale
        table = self._color_table
        last = len(table) - 1
        div = max(1.0, self._neck_y - self._glass_bot) / len(table)
        tone_scale = 5 / math.tau
        light = self.sand_light
        thin, thick = [], []
        for p in self.particles:
            y = p["y"]
            if y >= outlet:
                continue
            vy = p["vy"]
            if vy < 0:
                vy = -vy
            trail = vy * p["trail_time"] / scale
            if trail < 2:
                trail = 2
            top = y + trail
            if p["is_light"]:
                r, g, b = light
            else:
                w = int(p["wobble_phase"] * tone_scale)
                if w > 4:
                    w = 4
                index = int((self._neck_y - y) / div) + w - 2
                if index < 0:
                    index = 0
                elif index > last:
                    index = last
                r, g, b = table[index]
            (thin if p["size"] == 1 else thick).append((p["x"], y, top, r, g, b))
        _rn = 0
        _rsx = _rsy = _rst = 0.0
        for _k, _b in self._group_stream_particles().items():
            for _q in _b:
                _bb = _q["y"]
                _v = _q["vy"]
                if _v < 0:
                    _v = -_v
                _tr = _v * _q["trail_time"] / scale
                if _tr < 2:
                    _tr = 2
                _t = _bb + _tr
                if _t > top_limit:
                    _t = top_limit
                _rsx += _q["x"]
                _rsy += _bb
                _rst += _t
                _rn += 1
        _probe("REF t=%.4f n=%d sx=%.2f sy=%.2f st=%.2f" % (
            self.elapsed, _rn, _rsx, _rsy, _rst))
        _sx = _sy = _st = 0.0
        try:
            from kivy.app import App as _App
            _app = _App.get_running_app()
            _live = getattr(_app, "hourglass", None) if _app else None
            _probe("SELF id=%s live id=%s live_n=%s self_n=%d" % (
                id(self), id(_live),
                len(getattr(_live, "particles", [])) if _live is not None else "n/a",
                len(self.particles)))
        except Exception as _e:
            _probe("SELF probe failed: %r" % (_e,))
        _below = sum(1 for _q in self.particles if _q["y"] < outlet)
        _probe("t=%.4f len(particles)=%d below_outlet=%d outlet=%.2f neck_y=%.2f y_bot=%.2f" % (
            self.elapsed, len(self.particles), _below, outlet,
            self._neck_y, self._taper["y_bot"]))
        for _e in thin + thick:
            _sx += _e[0]; _sy += _e[1]; _st += _e[2]
        _probe("t=%.4f n=%d sx=%.4f sy=%.4f st=%.4f" % (
            self.elapsed, len(thin) + len(thick), _sx, _sy, _st))
        for width, entries in ((1, thin), (2, thick)):
            batches = self._flow_single[width]
            for i, batch in enumerate(batches):
                batch.write(entries[i * SINGLE_CHUNK:(i + 1) * SINGLE_CHUNK], top_limit)

    # 挂在 update_particles 上: 基准探针证明它每帧必被调用(而 _draw_stream 的类级
    # 替换在这里不触发, canvas Callback 也不执行 —— 原因未明, 先绕开)。
    # update_particles 在 redraw 之前跑, 所以纹理在本帧 GL 绘制前就写好了。
    original_update = widget_class.update_particles

    def update_and_draw(self, dt):
        original_update(self, dt)
        draw_single(self)

    def safe_draw(self):
        try:
            draw_single(self)
        except Exception:
            import traceback
            _probe("draw_single EXC: " + traceback.format_exc()[-500:])
            raise

    widget_class._build_dynamic_canvas = build_single
    widget_class._draw_stream = safe_draw
    _probe("after patch: _draw_stream.__name__=%r" % (
        getattr(widget_class._draw_stream, "__name__", None),))
    original_redraw = widget_class.redraw

    def redraw_and_draw(self):
        original_redraw(self)
        _probe("redraw_and_draw() ran")
        draw_single(self)

    widget_class.redraw = redraw_and_draw
    _probe("redraw patched")
    widget_class._flow_single_capacity = 64
    widget_class.flow_renderer = "single_mesh"
