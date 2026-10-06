"""飞溅层的**批处理渲染器** —— 外部评审 youhua1.md §3.1 的方案(2026-10-07)。

## 为什么

设备实测(临时把飞溅数封顶 400 跑同一轮 benchmark): 飞溅层值 **4.01ms/帧**
(物理 −1.35 / 图元 −1.04 / Canvas −1.62), 是当前最大的单点。
现状是 **1800~2600 个独立 `Rectangle`**, 每个每帧都要走
属性写入 → `flag_data_update` → `build()` → 顶点上传。

## 方案(与沙流同源的"参数纹理 + 静态网格")

飞溅是**同色、轴对齐的小矩形**, 只是尺寸不同 ⇒ 完全可以批处理:

- **静态顶点**: 每槽 4 顶点 / 6 索引, 顶点的 `(x, y)` 直接就是角点选择子
  `(ax, ay) ∈ {0,1}²` —— 拿去 `mix` 左右/上下边界。**索引和顶点都只建一次**。
- **动态数据**: 每颗 4 个 float(`left, bottom, right, top`), **16 字节**,
  **直接写 IEEE754 float32 的原始字节** —— 纹理是 RGBA8, 一个 float32 正好一个纹素。
  Python 侧**不需要编码器**(沙流就是这么做的: `astype('<f4').tobytes()`)。
- **着色器**: 从一个槽的 4 个纹素里还原四个边界, 再按 `vPosition` 选角。
- **分块**: 复用沙流的 `CHUNK = 512`。512 槽 × 4 纹素 = **2048×1** 的纹理。

## 与沙流方案的差异

| | 沙流 | 飞溅 |
|---|---|---|
| 每颗粒参数 | 3 个(`x, bottom, top`) | 4 个(`left, bottom, right, top`) |
| 顶点数/颗 | 20 / 54 索引(宽线圆头帽) | **4 / 6 索引** |
| 每颗动态字节 | 12 | 16 |

## 安全

- **默认不启用**。`HG_SPLASH_RENDERER=batch` 才装, 装不上/编译失败**自动退回**原 `Rectangle` 路径
  (与沙流渲染器的回退同理: 报错发生在**画布构建时**, 外面包不住, 所以先探测能力)。
- ⚠️ 设 uniform 必须用 `context["名字"] = 值` —— `context.shader[...]` 是 Kivy **2.3.1** 才有的,
  设备上是 **2.3.0**, 写了会在画布构建时 TypeError, 表现为 app 一启动就死。
"""

import math
from array import array

from kivy.graphics import BindTexture, Color, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_TEXTURE_SIZE
from kivy.graphics.texture import Texture

CHUNK = 512                 # 每块的槽位数(与沙流一致)
TEXELS_PER_SPLASH = 4       # left / bottom / right / top
_FLOAT4 = "<4f"
_FLT = __import__("struct").Struct(_FLOAT4)
TEXEL_STEP = 1.0 / (CHUNK * TEXELS_PER_SPLASH)
TEXEL_STEP_UNIFORM = "texel_step"

# 中性化: 把不用的槽位推到画面外(四边界全取一个大负数 ⇒ 退化成画外的点, 出不了像素)。
# 与沙流的 `PAD_ENDPOINT` 同一个思路 —— **索引只增不减**, 缩的时候不动索引。
PAD = _FLT.pack(-1e5, -1e5, -1e5, -1e5)

VERTEX_SHADER = """
$HEADER$
uniform sampler2D bounds;
uniform float texel_step;
float read_float(float u) {
    vec4 b = floor(texture2D(bounds, vec2(u, 0.5)) * 255.0 + 0.5);
    float exponent = mod(b.a, 128.0) * 2.0 + floor(b.b / 128.0);
    if (exponent == 0.0) {
        return 0.0;
    }
    float fraction = b.r + b.g * 256.0 + mod(b.b, 128.0) * 65536.0;
    float sign_value = b.a >= 128.0 ? -1.0 : 1.0;
    return sign_value * (1.0 + fraction / 8388608.0) * exp2(exponent - 127.0);
}
void main(void) {
    float left   = read_float(vTexCoords0.x);
    float bottom = read_float(vTexCoords0.x + texel_step);
    float right  = read_float(vTexCoords0.x + texel_step * 2.0);
    float top    = read_float(vTexCoords0.x + texel_step * 3.0);
    // 角点选择子放在 **texcoord 的 .y**(与沙流渲染器的 `end` 标记同一约定) ——
    // ⚠️ 别把选择子塞进 `vPosition`: 沙流那套是"位置属性只当偏移", 换用法在 Kivy 的
    //    Mesh 顶点格式下**画不出来**(实测: 整层消失)。照抄已验证的约定最稳。
    float sel = floor(vTexCoords0.y + 0.5);        // 0..3
    vec2 position = vec2(mix(left, right, mod(sel, 2.0)),
                         mix(bottom, top, floor(sel * 0.5)));
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

# 角点选择子 **放在 texcoord 的 `.y`**(0..3 = 左下/右下/右上/左上), 位置属性恒为 (0,0)。
# 与沙流渲染器用 `vTexCoords0.y` 当 `end` 标记完全同构 —— 那是已经跑通的约定。
_SELECTORS = (0.0, 1.0, 2.0, 3.0)
_INDICES = (0, 1, 2, 0, 2, 3)


class SplashBatch:
    """一块 = 最多 `CHUNK` 颗飞溅。多块自动扩展。"""

    __slots__ = ("group", "parts")

    def __init__(self, group):
        self.group = group
        self.parts = []

    def _ensure_part(self, chunk):
        if chunk < len(self.parts):
            return self.parts[chunk]
        binding = BindTexture(index=1)
        mesh = Mesh(mode="triangles")
        self.group.add(binding)
        self.group.add(mesh)
        data = bytearray(CHUNK * TEXELS_PER_SPLASH * 4)
        texture = Texture.create(size=(CHUNK * TEXELS_PER_SPLASH, 1),
                                 colorfmt="rgba")
        texture.mag_filter = texture.min_filter = "nearest"

        def reload_data(target, _data=data):
            target.blit_buffer(_data, colorfmt="rgba", bufferfmt="ubyte")
        texture.add_reload_observer(reload_data)
        # 🔴 **必须把纹理挂到 `BindTexture` 上** —— 沙流那边写的是 `part[7].texture = texture`,
        #    我第一版漏了这一行 ⇒ 着色器采样到的不是我们的数据、四个边界全是 0
        #    ⇒ 所有四边形退化到画布原点 ⇒ **整层飞溅不显示**(实测: 右边那张图一片喷雾都没有)。
        #    "建了 BindTexture" 不等于"绑了这张纹理"。
        binding.texture = texture

        span = CHUNK * TEXELS_PER_SPLASH
        vertices = array("f", (
            value
            for i in range(CHUNK)
            for sel in _SELECTORS
            for value in (0.0, 0.0, (i * TEXELS_PER_SPLASH + 0.5) / span, sel)))
        indices = array("H", (
            index + i * len(_SELECTORS)
            for i in range(CHUNK) for index in _INDICES))
        part = [mesh, vertices, indices, texture, data, 0]
        mesh.vertices = vertices
        self.parts.append(part)
        return part

    def update(self, splashes):
        """`splashes` = widget 的飞溅列表(list of dict)。

        每颗只做 **1 次 `pack_into`**(4 个 float 连续 16 字节), 与沙流同法。
        """
        total = len(splashes)
        chunks = -(-total // CHUNK) if total else 0
        pack = _FLT.pack_into
        for chunk in range(chunks):
            start = chunk * CHUNK
            count = total - start
            if count > CHUNK:
                count = CHUNK
            part = self._ensure_part(chunk)
            mesh, _vertices, indices, texture, data, previous = part
            offset = 0
            for k in range(start, start + count):
                s = splashes[k]
                x = s["x"]
                y = s["y"]
                sz = s["size"]
                # ⚠️ 口径必须与 `_sync_rects` **逐字一致**: `isinstance(sz, (tuple, list))`
                #    —— 用 `type(sz) is tuple` 会漏掉 list, 那时会被当成标量、
                #    `x - half_w` 直接 TypeError。
                if isinstance(sz, (tuple, list)):
                    w = float(sz[0])
                    h = float(sz[1])
                else:
                    w = h = float(sz)
                half_w = w * 0.5
                half_h = h * 0.5
                pack(data, offset, x - half_w, y - half_h,
                     x + half_w, y + half_h)
                offset += 16
            if count > previous:
                mesh.indices = indices[:count * len(_INDICES)]
                part[5] = count
            elif count < previous:
                # 缩了: **不动索引**(动了要整块重建顶点), 把多余槽位在纹理里推出画面。
                data[count * 16:previous * 16] = PAD * (previous - count)
            texture.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
        for part in self.parts[chunks:]:
            if part[5]:
                part[0].indices = array("H")
                part[5] = 0


def available():
    """能力探测 —— **必须建在画布之前**, 因为 shader 失败发生在画布构建时, 外面包不住。"""
    try:
        if glGetIntegerv(GL_MAX_TEXTURE_SIZE)[0] < CHUNK * TEXELS_PER_SPLASH:
            return False, "GL_MAX_TEXTURE_SIZE < %d" % (CHUNK * TEXELS_PER_SPLASH)
        from kivy.graphics.opengl import (glGetIntegerv as gi,
                                          GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)
        if gi(GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)[0] < 1:
            return False, "no vertex texture sampling"
    except Exception as exc:                      # pragma: no cover - 设备相关
        return False, str(exc)
    return True, ""


def install(widget_class):
    """把飞溅层换成批处理。**读改 `_splash_group` 里的内容, 不动 canvas 顺序。**"""
    build = widget_class._build_dynamic_canvas

    def build_splash_batches(self):
        build(self)
        group = self._splash_group
        # 组里此时只有一条 Color(见 `_build_dynamic_canvas`), 保留它。
        color = self._splash_color
        context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        context.shader.vs = VERTEX_SHADER
        context.shader.fs = FRAGMENT_SHADER
        if not context.shader.success:
            raise RuntimeError("splash batch shader failed to compile")
        context[TEXEL_STEP_UNIFORM] = TEXEL_STEP
        context["bounds"] = 1
        position = self.canvas.children.index(group)
        self.canvas.remove(group)
        group.clear()
        group.add(color)
        context.add(group)
        self.canvas.insert(position, context)
        self._splash_context = context
        self._splash_batch = SplashBatch(group)
        self._splash_rects = []

    def sync_splashes(self):
        self._splash_batch.update(self.splashes)

    widget_class._build_dynamic_canvas = build_splash_batches
    widget_class._sync_splashes = sync_splashes
    widget_class.splash_renderer = "batch"
