"""单 Mesh 沙流渲染器(实验): 把"11 档颜色 x 2 种线宽 = 24 个 Mesh"合成 1 个。

动机(2026-10-02 消融实测, 1.2 为底, 2525 颗粒): 关掉粒子批后帧时间 16.35 -> 8.61ms,
即粒子管线独占 7.7ms, 其中 GL 侧 ~3.1ms 花在 **24 次状态切换 + 24 次纹理上传 +
24 次 draw call** 上。颜色改为顶点属性、全部粒子共用一个 Mesh 与一张端点纹理后,
这些"24 次"应当塌成"1 次"。

关键设计(与 tools/flow_texture_experiment.py 的差异):
- 顶点格式多一列 `v_color`(4 float): 颜色随粒子走, 不再按颜色分桶;
- 端点纹理只有一张, capacity 取 reserve 的 2 次幂(≈4096 -> 宽 3*4096=12288,
  设备报 texture max size 32768, 安全); 每颗粒仍占 3 个纹素(x/bottom/top 连续),
  所以打包仍是每颗粒 1 次 `struct.pack_into`;
- 线宽仍需分桶时, 用顶点里的 `v_tex_coord.y`(现有模板已用它区分上下端点) hmm
  —— 见下方 TODO。

TODO(下轮):
1. 线宽: 现在模板按 width 生成端帽几何。两种线宽要么各留一个 Mesh(2 次 draw call,
   仍远好于 24), 要么把端帽偏移也做成顶点属性(需要再扩顶点格式)。
   先做前者: 2 个 Mesh, 颜色走顶点属性 —— 这是风险最低、收益最大的第一步。
2. install(): 替换 `_build_dynamic_canvas` / `_draw_stream`, 与现有
   `flow_texture_experiment.install` 同构, 由 main.py 末尾的 FLOW_RENDERER 选择。
3. 验证: 本地 `python tools/inspect_flow.py --label single --dense-neck --pixels 400,800`
   与 1.2 的参考图逐像素比对, **必须 0 差异**(颜色/顺序/几何都不变, 只是不再分桶);
   然后推 MuMu 量 canvas 分项。
"""

from array import array
import math
from struct import Struct

from kivy.graphics import BindTexture, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
from kivy.graphics.texture import Texture

import flow_batch_experiment


FLOAT3 = Struct("<3f")
TEXELS_PER_PARTICLE = 3
TEXEL_STEP_UNIFORM = "texel_step"

VERTEX_SHADER = """
$HEADER$
uniform sampler2D endpoints;
uniform float texel_step;
attribute vec4 v_color;
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
    frag_color = v_color * vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
void main(void) {
    gl_FragColor = frag_color;
}
"""

# 顶点: v_pos(2) + v_tex_coord(2) + v_color(4)
VERTEX_FMT = [(b"v_pos", 2, "float"), (b"v_tex_coord", 2, "float"),
              (b"v_color", 4, "float")]
FLOATS_PER_VERTEX = 8


class SingleFlowBatch:
    """所有颜色共用一个 Mesh; 颜色写在顶点上。

    与 TextureFlowBatch 同一套端点纹理技巧(每颗粒 3 个纹素, 一次 pack_into),
    但不再按颜色分桶 —— 桶只按线宽分(2 个), 颜色的差异由 `v_color` 承担。
    """

    def __init__(self, group, width, reserve=0):
        self.width = width
        base = flow_batch_experiment.FlowBatch(None, width)   # 仅借用模板/索引生成
        self.template = base.template
        self.indices = base.indices
        self.mode = base.mode
        self.capacity = max(flow_batch_experiment.FlowBatch.CHUNK, reserve)
        self.capacity = 1 << (self.capacity - 1).bit_length()
        # TODO(下轮): 建 Mesh/纹理/绑定, 与 flow_texture_experiment._ensure_part 同构,
        #             但顶点每 8 个 float 一颗(v_color 由 set_particles 写入)。
        raise NotImplementedError("下轮接线: 建 Mesh + 共享端点纹理")

    def update(self, particles, colors, top_limit, motion_scale=1):
        raise NotImplementedError


def install(widget_class):
    """与 flow_texture_experiment.install 同构; 未完成前不要启用。"""
    raise NotImplementedError("单 Mesh 渲染器尚未接线")
