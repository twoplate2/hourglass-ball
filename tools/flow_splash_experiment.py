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

try:
    import numpy as _np
except ImportError:                      # 兜底: 逐颗 pack_into
    _np = None

from kivy.graphics import BindTexture, Color, InstructionGroup, Mesh, RenderContext
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

    def update(self, splashes, uniform_half=None):
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
            # ★ **单一尺寸时把半宽提到循环外**(2026-10-07 性能, 设备实测本层共 **0.85ms**)。
            #   `uniform_half` 由**调用方按配置**给出(main.py 的 `_splash_uniform_half`),
            #   不在这里逐颗验证 —— 实测过: 逐颗验一遍的代价**正好等于省下的**, 白干。
            #   ⇒ `len(SPLASH_SIZE_MIX) == 1` 时按构造**每颗尺寸必然相同**, O(1) 即可判定。
            #   混合尺寸(该常量支持多项)时传 None, 自动退回逐颗读 `size`。
            offset = 0
            if uniform_half is not None:
                hw, hh = uniform_half
                for k in range(start, start + count):
                    s = splashes[k]
                    x = s["x"]
                    y = s["y"]
                    pack(data, offset, x - hw, y - hh, x + hw, y + hh)
                    offset += 16
            else:
                for k in range(start, start + count):
                    s = splashes[k]
                    x = s["x"]
                    y = s["y"]
                    sz = s["size"]
                    # ⚠️ 口径必须与 `_sync_rects` **逐字一致**: `isinstance(sz, (tuple, list))`
                    #    —— 用 `type(sz) is tuple` 会漏掉 list, 那时会被当成标量、直接 TypeError。
                    if isinstance(sz, (tuple, list)):
                        w = float(sz[0])
                        h = float(sz[1])
                    else:
                        w = h = float(sz)
                    hw = w * 0.5
                    hh = h * 0.5
                    pack(data, offset, x - hw, y - hh, x + hw, y + hh)
                    offset += 16
            _upload = count
            if count > previous:
                mesh.indices = indices[:count * len(_INDICES)]
                part[5] = count
            elif count < previous:
                # 缩了: **不动索引**(动了要整块重建顶点), 把多余槽位在纹理里推出画面。
                data[count * 16:previous * 16] = PAD * (previous - count)
                _upload = previous
            # **只传用到的纹素**(同上: 整块传 8KB 而每块常只用到几百颗)
            texture.blit_buffer(data, size=(_upload * TEXELS_PER_SPLASH, 1),
                                colorfmt="rgba", bufferfmt="ubyte")
        for part in self.parts[chunks:]:
            if part[5]:
                part[0].indices = array("H")
                part[5] = 0


    def update_arrays(self, count, xs, ys, hws, hhs):
        """与 `update` **同语义**, 但数据源是**并行数组** —— 每颗省掉 3 次 dict 查找。

        `xs/ys/hws/hhs` 同长(numpy 数组或 list 都行); 只有前 `count` 个有效。
        分块、"索引只增不减"、`PAD` 中性化、局部上传, 全部与 `update` 一致 ——
        见那两处的注释(每一条都是踩过的)。

        ⚠️ 浮点转换用 `astype("<f4")`, 与 `struct.pack("<f")` **逐位相同**
        (项目里 `tools/test_pack_equiv.py` 对 30 万样本证过, 含 0/-0/inf/denormal)。
        """
        total = count
        chunks = -(-total // CHUNK) if total else 0
        pack = _FLT.pack_into
        use_np = _np is not None and hasattr(xs, "dtype")
        for chunk in range(chunks):
            start = chunk * CHUNK
            n = total - start
            if n > CHUNK:
                n = CHUNK
            part = self._ensure_part(chunk)
            mesh, _vertices, indices, texture, data, previous = part
            if use_np:
                blk = _np.empty((n, 4), dtype=_np.float64)
                x = xs[start:start + n]
                y = ys[start:start + n]
                hw = hws[start:start + n]
                hh = hhs[start:start + n]
                blk[:, 0] = x - hw
                blk[:, 1] = y - hh
                blk[:, 2] = x + hw
                blk[:, 3] = y + hh
                data[:n * 16] = blk.astype("<f4").tobytes()
            else:
                off = 0
                for k in range(start, start + n):
                    hw = hws[k]
                    hh = hhs[k]
                    pack(data, off, xs[k] - hw, ys[k] - hh, xs[k] + hw, ys[k] + hh)
                    off += 16
            _upload = n
            if n > previous:
                mesh.indices = indices[:n * len(_INDICES)]
                part[5] = n
            elif n < previous:
                # 缩的时候**必须传到 previous** —— 索引只增不减, 那些槽位仍在被画
                data[n * 16:previous * 16] = PAD * (previous - n)
                _upload = previous
            if _upload:
                texture.blit_buffer(data, size=(_upload * TEXELS_PER_SPLASH, 1),
                                    colorfmt="rgba", bufferfmt="ubyte")
        for part in self.parts[chunks:]:
            if part[5]:
                part[0].indices = array("H")
                part[5] = 0

    def update_bounds(self, bounds):
        """`bounds` = 可迭代的 `(left, bottom, right, top)`(颈部颗粒用)。

        与 `update` 同一套"索引只增不减"的约定, 只是**不经过 dict** —— 颈部那边每帧
        要喂 ~320 个矩形, 逐个建 dict 是一笔白钱。
        """
        total = len(bounds)
        if total > CHUNK:
            bounds = bounds[:CHUNK]
            total = CHUNK
        # 🔴 **空桶一个 GL 调用都不做**。颈部是按 32 个色调档建的批, 每帧只有 ~11 档非空 ——
        #    第一版对 32 个批**每个都整块上传 8KB**, 合计 256KB/帧 + 32 次 `glTexSubImage2D`,
        #    结果 Canvas 是省下来了(1.48ms), **图元却一点没降**(实测 3.97 → 3.94),
        #    好处被这堆白上传吃掉了一半。
        if not self.parts and total == 0:
            return
        part = self._ensure_part(0)
        mesh, _vertices, indices, texture, data, previous = part
        if total == 0 and previous == 0:
            return
        offset = 0
        pack = _FLT.pack_into
        for left, bottom, right, top in bounds:
            pack(data, offset, left, bottom, right, top)
            offset += 16
        if total > previous:
            mesh.indices = indices[:total * len(_INDICES)]
            part[5] = total
            upload = total
        elif total < previous:
            # ⚠️ 缩的时候**必须传到 previous** —— 索引只增不减, 那些槽位仍在被画,
            #    只把 `total` 之前传上去的话, 后面那些槽位会留着**上一帧的旧矩形**。
            data[total * 16:previous * 16] = PAD * (previous - total)
            upload = previous
        else:
            upload = total
        if upload:
            # 只传用到的纹素(第一版整块 2048 纹素全传, 这里最多 320 颗 × 4)
            texture.blit_buffer(data, size=(upload * TEXELS_PER_SPLASH, 1),
                                colorfmt="rgba", bufferfmt="ubyte")


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


# ======================= 颈部颗粒: 批处理(2026-10-07) =======================
# ## 为什么要做
# 颈部颗粒原本是 **320 个独立的 Kivy `Line`**(每帧各自一次 `Color.rgb` / `Line.points`
# 写属性 + 一次独立的 draw call)。实测(池子 320→1 的消融, 15s 档):
#
#   | | 图元 | Canvas |
#   |---|---|---|
#   | 池子 320 | 3.97 | **2.85** |
#   | 池子 1   | 3.19 | **1.37** |
#
# ⇒ 这一层值 **~2.3ms/帧**(1.48 在 Canvas 的绘制调用上, 0.78 在 Python 的属性写上),
#   而它只画了 320 个小矩形。飞溅层同规模只要 0.7ms —— 差别就是"有没有批处理"。
#
# ## 怎么做的(关键: **不改 main.py 的热函数**)
# `_draw_neck_grains` 的逻辑(含两趟候选筛选、二分求半宽、色调量化)是反复调过的,
# 单为性能重写风险太大。这里只做一件事: **把池子里的 `(Color, Line)` 换成纯 Python
# 记录器** —— 原函数一字不动地照跑, 只是属性写进记录器(普通属性赋值 ~0.05µs)而不是
# Kivy 描述符(~1µs, 还要派发事件)。
#
# 然后按**色调档**把矩形分桶喂给 `SplashBatch`: 每档一个批(自带一个 `Color`),
# 320 次绘制塌成"本帧真正用到的档数"次(实测 ~11)。
#
# ## 与原来的差异(如实记录)
# 原来 `Line(width=w, points=(x,b,t,x,t))` 在 **w>1** 时是"带宽 w 的条 + 两端半径 w/2 的
# **圆头帽**"(Kivy 自建三角网格); 这里画的是**方角**矩形, 但把两端各外扩 w/2
# ⇒ 只差四个角的圆/方。1px 的颗粒(w=1)两边完全一致。
NECK_TONES = 32          # 与 main.py 的 `_neck_tone_tab` 同长

# 池子槽位数 —— 必须与 main.py `_build_dynamic_canvas` 里 `for _ in range(320)` 一致,
# 否则 `_neck_tone_last` 的长度校验会反复重建(功能不受影响, 只是白干)。
NECK_POOL = 320


class _RecColor:
    """冒充 `Color` —— 只记 rgb。"""
    __slots__ = ("rgb",)

    def __init__(self):
        self.rgb = None


class _RecLine:
    """冒充 `Line` —— 只记 points / width。"""
    __slots__ = ("points", "width")

    def __init__(self):
        self.points = ()
        self.width = 1


def install_neck(widget_class):
    """把颈部颗粒从 320 个 `Line` 换成"按色调分桶的批处理"。成功返回 True。

    🔴 **别在类上查 `_neck_grain_group`** —— 那是 `_build_dynamic_canvas` 里建的**实例**
    属性, 安装这一刻(模块导入期)必然不存在 ⇒ `hasattr` 恒为假 ⇒ **静默什么都不装**,
    而外面只看到"跑完了"。2026-10-07 我因此比对了 80 帧 0 差异、**全是空转**。
    ⇒ 判断挪进 wrapper(那时 `build` 已经跑过), 并**回填 `neck_renderer` 供外部断言**。
    """
    build = widget_class._build_dynamic_canvas
    draw = widget_class._draw_neck_grains
    if getattr(draw, "_neck_batched", False):
        return False

    def build_neck_batches(self):
        build(self)
        group = getattr(self, "_neck_grain_group", None)
        if group is None:                 # 这个 widget 没有颈部颗粒层 ⇒ 保持原样
            self._neck_batches = None
            return
        position = self.canvas.children.index(group)
        self.canvas.remove(group)
        group.clear()                     # 扔掉那 320 对 (Color, Line)
        context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        context.shader.vs = VERTEX_SHADER
        context.shader.fs = FRAGMENT_SHADER
        if not context.shader.success:
            raise RuntimeError("neck batch shader failed to compile")
        # 与飞溅共用 `TEXEL_STEP`: 一样是 CHUNK 槽 × 4 纹素 ⇒ u 步长相同。
        context[TEXEL_STEP_UNIFORM] = TEXEL_STEP
        context["bounds"] = 1
        batches = []
        for _k in range(NECK_TONES):
            slot = InstructionGroup()
            color = Color(1.0, 1.0, 1.0, 1.0)
            slot.add(color)
            context.add(slot)
            batches.append((color, SplashBatch(slot)))
        self.canvas.insert(position, context)
        self._neck_context = context
        self._neck_batches = batches
        self._neck_batch_rgb = [None] * NECK_TONES
        # 池子换成记录器 —— 原 `_draw_neck_grains` 完全不知道这件事。
        self._neck_grain_pool = [(_RecColor(), _RecLine()) for _ in range(NECK_POOL)]
        self._neck_grain_count = 0
        self._neck_tone_last = [None] * NECK_POOL

    def draw_neck_batches(self, side):
        draw(self, side)                  # 原逻辑照跑, 只是写进了记录器
        if getattr(self, "_neck_batches", None) is None:
            return
        count = self._neck_grain_count
        pool = self._neck_grain_pool
        buckets = {}
        if count:
            append = None
            for i in range(count):
                color, line = pool[i]
                pts = line.points
                if not pts:
                    continue
                # ⚠️ **只有 `width > 1` 才外扩** —— Kivy 的 `Line` 在 w>1 时**自建三角网格**
                #    并带两端半径 w/2 的圆头帽; **w==1 走 `glLineWidth`, 端头不外扩**。
                #    一律按 w/2 外扩的写法**实测错了**(1px 的颗粒被撑高 1px ⇒ 逐像素比对
                #    满屏单像素点 + 玻璃肩线上一条细线, 最大通道差 133)。
                w = line.width
                hw = w * 0.5
                ext = hw if w > 1.0 else 0.0
                rgb = color.rgb
                lst = buckets.get(rgb)
                if lst is None:
                    lst = buckets[rgb] = []
                lst.append((pts[0] - hw, pts[1] - ext, pts[0] + hw, pts[3] + ext))
        tab = self._neck_tone_tab
        for k, (color, batch) in enumerate(self._neck_batches):
            rgb = tab[k] if (tab is not None and k < len(tab)) else None
            if self._neck_batch_rgb[k] != rgb:
                # ⚠️ 必须连 alpha 一起写回 —— 下调成 0 之后只写 `.rgb` 是**恢复不了**的
                #    (Kivy 的 r/g/b 与 a 是各自独立的属性), 那档会永久透明。
                color.rgba = ((rgb[0], rgb[1], rgb[2], 1.0) if rgb is not None
                              else (1.0, 1.0, 1.0, 0.0))
                self._neck_batch_rgb[k] = rgb
            batch.update_bounds(buckets.get(rgb, ()))
        # 留给取证 (`tools/_probe_neck_batch_equiv.py`): 本帧各色调桶的矩形
        self._neck_last_buckets = buckets
        self._neck_last_tab = tab
        self._neck_grain_count = 0        # 记录器已消费完, 下一帧从 0 开始

    draw_neck_batches._neck_batched = True
    draw_neck_batches._orig = draw          # 取证脚本要拿它跑"原路径"做对照
    widget_class._build_dynamic_canvas = build_neck_batches
    widget_class._draw_neck_grains = draw_neck_batches
    widget_class.neck_renderer = "batch"
    return True
