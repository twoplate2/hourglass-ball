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
import os
# ---------------------------------------------------------------------------
# 诊断开关一律用**标记文件**(不是环境变量)!
#   🔴 **安卓上的 app 读不到宿主 shell 的环境变量** —— `HG_*` 那一整套开关在桌面有效,
#      在设备上**一律取默认值**。2026-10-07 我拿 `HG_NO_BLIT=1 bash tools/_one_bench.sh`
#      量了半天"砍光纹理上传能省多少", 量出来的差 (2.89→2.83ms) **全是噪声** ——
#      那次上传**根本没被跳过**(变量只存在于 Windows 的 shell 里)。
#      要在设备上做单变量对照, 只能写**标记文件**(app 私有目录, 与 `prof.on` 同一套):
#          adb shell touch /data/data/org.shalou.hourglass/files/app/blit.off   # 开(要重启)
#          adb shell rm    /data/data/org.shalou.hourglass/files/app/blit.off   # 关
#   ⚠️ 桌面**两种都认**(环境变量优先), 免得改一次桌面流程。
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _flag(env_name, file_name):
    if os.environ.get(env_name):
        return True
    try:
        return os.path.exists(os.path.join(_APP_DIR, file_name))
    except Exception:
        return False

import sys
from array import array

try:
    import numpy as _np
except ImportError:                      # 兜底: 逐颗 pack_into
    _np = None

import flow_batch_experiment
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
        # ★ 切片拼(同 `flow_batch_experiment.build_vertices`; 原来逐元素生成器)
        vertices = flow_batch_experiment.build_vertices(
            [(0.0, 0.0, sel) for sel in _SELECTORS], CHUNK,
            TEXELS_PER_SPLASH, span)
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
            flow_batch_experiment.blit_texture(
                texture, data, _upload * TEXELS_PER_SPLASH)
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
                blk = _np.empty((n, 4), dtype="<f4")
                x = xs[start:start + n]
                y = ys[start:start + n]
                hw = hws[start:start + n]
                hh = hhs[start:start + n]
                blk[:, 0] = x - hw
                blk[:, 1] = y - hh
                blk[:, 2] = x + hw
                blk[:, 3] = y + hh
                data[:n * 16] = blk.tobytes()
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
                flow_batch_experiment.blit_texture(
                    texture, data, _upload * TEXELS_PER_SPLASH)
        for part in self.parts[chunks:]:
            if part[5]:
                part[0].indices = array("H")
                part[5] = 0

    def update_bounds_np(self, count, l, b, r, t):
        """与 `update_bounds` 同语义, 但四个边界是 **numpy 数组** —— 打包也走 numpy。

        颈部颗粒用(`update_arrays` 那套是"读数组算边界", 这里是"边界已经是数组")。
        分块/索引只增不减/`PAD` 中性化/局部上传全部与 `update_bounds` 一致。
        """
        n = count if count <= CHUNK else CHUNK
        if not self.parts and n == 0:
            return
        part = self._ensure_part(0)
        mesh, _vertices, indices, texture, data, previous = part
        if n == 0 and previous == 0:
            return
        if n:
            blk = _np.empty((n, 4), dtype="<f4")
            blk[:, 0] = l[:n]
            blk[:, 1] = b[:n]
            blk[:, 2] = r[:n]
            blk[:, 3] = t[:n]
            data[:n * 16] = blk.tobytes()
        _upload = n
        if n > previous:
            mesh.indices = indices[:n * len(_INDICES)]
            part[5] = n
        elif n < previous:
            data[n * 16:previous * 16] = PAD * (previous - n)
            _upload = previous
        if _upload:
            # 上传走共用助手(`blit.rep`/`blit.wide` 两个量具旋钮; 默认等价于老写法)
            flow_batch_experiment.blit_texture(
                texture, data, _upload * TEXELS_PER_SPLASH)

    def write_bounds_raw(self, raw, off, count):
        """把**已经算好**的边界字节(`raw`, 每条 16 字节)从第 `off` 条起写 `count` 条。

        与 `update_bounds_np` 同语义, 只是四个边界由调用方**一次算完**。
        理由与沙流那边一样: 颈部每帧有 ~11 个非空色调桶, 逐桶各建一个 `(n,4)` 数组、
        各做 4 次列赋值、各 `tobytes()` 一遍 —— 而这四列本来就是同一次算出来的。
        实测(桌面)这一段的省下量见 `_neck_sink` 的注释。
        """
        n = count if count <= CHUNK else CHUNK
        if not self.parts and n == 0:
            return
        part = self._ensure_part(0)
        mesh, _vertices, indices, texture, data, previous = part
        if n == 0 and previous == 0:
            return
        if n:
            src = off * 16
            data[:n * 16] = raw[src:src + n * 16]
        _upload = n
        if n > previous:
            mesh.indices = indices[:n * len(_INDICES)]
            part[5] = n
        elif n < previous:
            data[n * 16:previous * 16] = PAD * (previous - n)
            _upload = previous
        if _upload:
            # 上传走共用助手(`blit.rep`/`blit.wide` 两个量具旋钮; 默认等价于老写法)
            flow_batch_experiment.blit_texture(
                texture, data, _upload * TEXELS_PER_SPLASH)

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
            flow_batch_experiment.blit_texture(
                texture, data, upload * TEXELS_PER_SPLASH)


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


def _neck_sink_color(widget, k, color, tab):
    """更新第 k 个批的颜色(色调变了才写)。返回该批的 rgb(没有就 None)。"""
    rgb = tab[k] if (tab is not None and k < len(tab)) else None
    if widget._neck_batch_rgb[k] != rgb:
        # 必须连 alpha 一起写回 —— 下调成 0 之后只写 `.rgb` 是**恢复不了**的
        color.rgba = ((rgb[0], rgb[1], rgb[2], 1.0) if rgb is not None
                      else (1.0, 1.0, 1.0, 0.0))
        widget._neck_batch_rgb[k] = rgb
    return rgb


def _neck_sink(widget, cnt, xs, bs, ts, ji, sz):
    """`main._NECK_SINK` —— 向量化分支**直通**批处理。

    跳过"写记录器(283 次属性写) + 再收集成桶(283 次 dict 查找/append)"那两步
    (设备分段实测合计 **~0.35ms/帧**) —— 几何量本来就已经是 numpy 数组了。

    ⚠️ **桶内顺序必须与旧路径一致**: 旧路径按槽位 0.. 顺序 append, 所以同一色调内是
    槽位升序; 这里用 `argsort(kind="stable")` 同样给出升序 ✓。
    ⚠️ `cnt == 0`(颈部隐藏 / 没候选)时**也必须把每个批清空**, 否则上一帧的颗粒会留着。
    """
    widget._neck_sink_used = True
    batches = widget._neck_batches
    tab = widget._neck_tone_tab
    np = _np
    if cnt <= 0 or np is None:
        for k in range(len(batches)):
            color, batch = batches[k]
            _neck_sink_color(widget, k, color, tab)
            batch.update_bounds(())
        widget._neck_last_rects = []
        return
    # ★ `ji` 是色调档(0..NECK_TONES-1 < 32) —— **先压成 `uint8` 再排**: 整数
    #   `argsort(kind="stable")` 走基数排序, 轮数正比于 dtype 宽度(见 main.py 里
    #   `_group_stream_particles` 同一处注释)。稳定排序的排列由键唯一确定 ⇒ 逐位不变。
    order = np.argsort(ji.astype(np.uint8), kind="stable")
    counts = np.bincount(ji, minlength=NECK_TONES)
    hw = sz * 0.5
    # 外扩规则: Kivy 的 `Line` 只有 w>1 才带圆头帽, w==1 端头不外扩
    ext = np.where(sz > 1.0, hw, 0.0)
    xl = xs - hw
    xr = xs + hw
    yb = bs - ext
    yt = ts + ext
    # ★ **四列一次排好, 桶内只切片**(2026-10-07 性能): 原来每个非空桶各做 4 次花式索引,
    #   实测 ~11 个非空桶 ⇒ **每帧 44 次 gather**, 而每次只搬 ~30 个元素 ——
    #   固定开销(每次 2~5µs)盖过数据本身。按 `order` 排一次是 4 次 gather,
    #   之后 `xlo[pos:pos+c]` 是**视图**(不拷贝)。逐位等价: `xl[order[a:b]]` 与
    #   `xl[order][a:b]` 是同一组元素、同一顺序。
    xlo = xl[order]
    ybo = yb[order]
    xro = xr[order]
    yto = yt[order]
    # ★ **四列一次写成整块字节, 桶内只切片**(2026-10-07 性能, 手法与沙流端相同)。
    #   原来 ~11 个非空桶各建一个 `(n,4)` float32 数组、各做 4 次列赋值、各 `tobytes()`
    #   一遍 —— 而这四列本来就已经在同一次运算里算好了。现在 4 次列赋值 + 1 次
    #   `tobytes()`, 每桶只做一次**字节切片**(memcpy)。逐位不变: 同样的 float64 值
    #   写进同样的 float32 位置。
    total = xlo.size
    blk = np.empty((total, 4), dtype="<f4")
    blk[:, 0] = xlo
    blk[:, 1] = ybo
    blk[:, 2] = xro
    blk[:, 3] = yto
    raw = blk.tobytes()
    pos = 0
    log = []
    for k in range(len(batches)):
        color, batch = batches[k]
        rgb = _neck_sink_color(widget, k, color, tab)
        c = int(counts[k]) if k < counts.shape[0] else 0
        if not c or rgb is None:
            batch.update_bounds(())
            continue
        batch.write_bounds_raw(raw, pos, c)
        # 留给 `tools/_probe_neck_batch_equiv.py` 取证(几何量仍以数组形式存引用)
        log.append((rgb, xlo[pos:pos + c], ybo[pos:pos + c],
                    xro[pos:pos + c], yto[pos:pos + c]))
        pos += c
    # 留给 `tools/_probe_neck_batch_equiv.py` 取证(存的是引用, 不拷贝)
    widget._neck_last_rects = log


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
        # 🔴🔴 **32 个色调组"按需建"= 试了两次, 两次都像素不一致, 别再试**(2026-10-07)。
        #
        # 数值: 设备实测 neck 族 **98 条指令**, 其中 **64 条**是这 32 对
        # `InstructionGroup + Color`, 而一帧只有 **11 档非空** ⇒ 42 条(≈0.05ms)是空壳。
        # 看起来是个白捡的钱 —— 它**不是**。
        #
        # 第一次(1.215): 把 `_neck_batches` 压成"只有用到的项"的列表。60/60 帧不同、
        #   颈部约 13000px、最大差 77。
        # 第二次(2026-10-07, 本次): **先做了更严的版本** —— 保持 `batches` 是 **32 槽、
        #   按色调索引**(没建的填 `None`), 画布插入位置另用升序表算, **不可能再有下标错位**;
        #   而且 `tools/_probe_canvas_instr.py` 验证条数**确实**从 98 降到 56(改动生效)。
        #   ⇒ **`_render_golden --check` 照样翻红。**
        #   ⇒ **"压缩列表导致下标错位"那条假设被证伪了** —— 根因不在这里。
        #
        # **同一轮里, 沙流那一侧的同款改动(`TextureFlowBatch` 按需挂 group)逐图一致 ✓**
        # (flow 族 73 → 41 条)。两边的差别: 沙流是 **dict 键控**、颈部是 **32 档色调**,
        # 且颈部的 `Color` 会**逐帧被 `_neck_sink_color` 改 alpha**(空档写 0)。
        # ⇒ 剩下的怀疑方向是**"没建过 ⇒ 画布上没有这条 Color ⇒ 上一档的 GL 颜色状态
        #   被下一档继承"**这类**状态泄漏**, 而不是顺序或下标。
        # **要再碰, 先把"颜色状态是否跨组泄漏"量出来**(给每一组显式写色后再比),
        # 别直接照抄沙流那套。
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
        self._neck_sink_used = False
        draw(self, side)                  # 原逻辑照跑
        if getattr(self, "_neck_batches", None) is None:
            return
        if self._neck_sink_used:
            # 向量化分支已经**直通**喂完批处理了(钩子见 `_neck_sink`), 这里不要再收集一遍
            return
        # 没直通(函数提前 return / 走了标量兜底分支)时才走记录器 -> 桶的老路
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
    # ★ 装上**直通**钩子: 向量化分支从此不再走记录器 + 桶收集(省 ~0.35ms/帧)。
    #   ⚠️ 钩子挂在 `main` 模块上, 所以它只对**装了批处理**的进程生效;
    #      没装时 `_NECK_SINK is None`, 老路径原样保留(逐位不变)。
    mod = sys.modules.get(widget_class.__module__)
    if mod is not None and hasattr(mod, "_NECK_SINK"):
        mod._NECK_SINK = _neck_sink
    widget_class._build_dynamic_canvas = build_neck_batches
    widget_class._draw_neck_grains = draw_neck_batches
    widget_class.neck_renderer = "batch"
    return True


# =====================================================================================
# 触底闪光(flare)层: 一个 `Mesh` 取代 N 条 `Color` + `Rectangle`
# =====================================================================================
#
# ## 为什么
#
# `tools/_probe_canvas_cost.py` 的消融: 48 条 `Rectangle` 的池子值 **on_draw 0.105ms**
# (141 条指令: 每条还各带一条 Kivy 自动插的 `BindTexture`); 逐帧那 48×3 个 Kivy 属性写入
# 再值 **~0.11ms**。合计 ~0.21ms/帧(桌面), 设备约 0.34ms —— 是画布上**最大的单族**。
#
# ## 与飞溅批处理的唯一差别: 每颗一个 alpha
#
# 闪光要按剩余寿命淡出, 所以不能像飞溅那样全场共用一个 `Color`。做法是**每颗多存一个
# float32**(第 5 个纹素), 顶点着色器里
#     `frag_color = color * vec4(1, 1, 1, opacity * alpha)`
# —— 默认片元着色器就是 `gl_FragColor = frag_color`, 所以**不需要自定义 varying**
# (少一处 GLES2 变数)。`opacity` 是 Kivy `Color` 的不透明度(装配时保持 1.0)
# ⇒ `1.0 * alpha` 与原 `Color(..., alpha)` 那条路**逐位相同**。
# 🔴 **顶点的角点顺序必须与 Kivy 的 `Rectangle` 一模一样**, 否则**对角线不同** ——
#    而两个三角剖分的**外边缘填充规则**在"边正好落在像素边界上"时会给出不同结果
#    (实测: 12 个边缘像素差 1~16 级)。`_qa/flare_iso.py` 就是为这条写的。
#    Kivy `Rectangle` 的顶点序是 v0=左下 v1=右下 **v2=右上 v3=左上**;
#    而本文件那个着色器的解码表是 `mod(sel,2)` 取 x、`floor(sel*0.5)` 取 y
#    ⇒ sel=0/1/2/3 解成 **左下/右下/左上/右上**。要在**下标 2** 拿到"右上"、**下标 3**
#    拿到"左上", 就得把选择子的值写成 (0, 1, **3**, **2**)。
#    ⚠️ 飞溅那边用的是 (0,1,2,3) —— 它**与逐 Rectangle 路径本来就不同**(先于本次改动,
#    且是安卓出货路径), 不动它; 这里只保证闪光层与它要替换的那条路**逐像素相同**。
FLARE_SELECTORS = (0.0, 1.0, 3.0, 2.0)
# 诊断开关: **强制装配失败**, 用来走一遍"回退到逐 `Rectangle`"那条路。
# 兜底路径不自己走出来对一遍, 就不知道它到底还能不能画(2026-10-07 自查发现:
# 回滚时漏把 `_flare_group` 放回画布 ⇒ 闪光**无声消失**, 不崩不报错)。
#     HG_SPLASH_RENDERER=batch HG_FLARE_RENDERER=batch HG_FLARE_FORCE_FAIL=1 #         python tools/inspect_flow.py --label fb --steady-period 15 --steady-frames 30
FLARE_FORCE_FAIL = _flag("HG_FLARE_FORCE_FAIL", "flare_fail.on")
_FLARE_ACTIVE_LOGGED = False

# 诊断: **砍掉本模块所有"每帧上传"**(颈部批 / 飞溅 / 闪光)。只用来量"上传一共值多少毫秒",
# 画面会停在上一帧的数据上 ⇒ **不许当出货配置**。
# 上传的开关**已经挪进 `flow_batch_experiment.blit_texture`**(那里还有 `blit.rep` /
# `blit.wide` 两个量具旋钮, 用来分"每次调用的固定开销"与"每字节带宽" —— 见那边的注释)。
# 老的 `blit.off` 标记文件语义不变(等价于 rep=0)。

FLARE_TEXELS = 5                 # left / bottom / right / top / alpha
FLARE_STEP = 1.0 / (CHUNK * FLARE_TEXELS)
_F5 = __import__("struct").Struct("<5f")
FLARE_PAD = _F5.pack(-1e5, -1e5, -1e5, -1e5, 0.0)

FLARE_VERTEX_SHADER = """
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
    float alpha  = read_float(vTexCoords0.x + texel_step * 4.0);
    float sel = floor(vTexCoords0.y + 0.5);
    vec2 position = vec2(mix(left, right, mod(sel, 2.0)),
                         mix(bottom, top, floor(sel * 0.5)));
    frag_color = color * vec4(1.0, 1.0, 1.0, opacity * alpha);
    gl_Position = projection_mat * modelview_mat * vec4(position, 0.0, 1.0);
}
"""


class FlareBatch:
    """一块 = 最多 `CHUNK` 颗闪光。多块自动扩展。"""

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
        data = bytearray(CHUNK * FLARE_TEXELS * 4)
        texture = Texture.create(size=(CHUNK * FLARE_TEXELS, 1), colorfmt="rgba")
        texture.mag_filter = texture.min_filter = "nearest"

        def reload_data(target, _data=data):
            target.blit_buffer(_data, colorfmt="rgba", bufferfmt="ubyte")
        texture.add_reload_observer(reload_data)
        # ⚠️ **必须把纹理挂到 `BindTexture` 上** —— "建了 BindTexture" 不等于"绑了纹理",
        #    漏了它着色器采样到的是别的纹理、四个边界全 0 ⇒ 整层退化到画布原点(踩过)。
        binding.texture = texture

        span = CHUNK * FLARE_TEXELS
        vertices = flow_batch_experiment.build_vertices(
            [(0.0, 0.0, sel) for sel in FLARE_SELECTORS], CHUNK,
            FLARE_TEXELS, span)
        indices = array("H", (
            index + i * len(_SELECTORS)
            for i in range(CHUNK) for index in _INDICES))
        part = [mesh, vertices, indices, texture, data, 0]
        mesh.vertices = vertices
        self.parts.append(part)
        return part

    def update_raw(self, raw, count):
        """`raw` = 全部闪光的 `left/bottom/right/top/alpha` 字节(**每条 20 字节**)。

        与飞溅那边同一套"索引只增不减 + `PAD` 中性化 + 只传用到的纹素"。
        """
        chunks = -(-count // CHUNK) if count else 0
        for chunk in range(chunks):
            start = chunk * CHUNK
            n = count - start
            if n > CHUNK:
                n = CHUNK
            part = self._ensure_part(chunk)
            mesh, _vertices, indices, texture, data, previous = part
            if n:
                src = start * 20
                data[:n * 20] = raw[src:src + n * 20]
            _upload = n
            if n > previous:
                mesh.indices = indices[:n * len(_INDICES)]
                part[5] = n
            elif n < previous:
                data[n * 20:previous * 20] = FLARE_PAD * (previous - n)
                _upload = previous
            if _upload:
                flow_batch_experiment.blit_texture(
                    texture, data, _upload * FLARE_TEXELS)
        for part in self.parts[chunks:]:
            if part[5]:
                part[0].indices = array("H")
                part[5] = 0


def install_flares(widget_class):
    """把触底闪光从 N 条 `Color`+`Rectangle` 换成一批 `Mesh`。成功返回 True。

    与 `install_neck` 同一形状: 包 `_build_dynamic_canvas`, 把 `_flare_group` 摘下来换成
    `RenderContext` + `FlareBatch`, 再换掉 `_draw_flares`。
    """
    if getattr(widget_class._draw_flares, "_flare_batched", False):
        return False
    draw = widget_class._draw_flares
    build = widget_class._build_dynamic_canvas

    def build_wrapper(self):
        build(self)
        group = getattr(self, "_flare_group", None)
        if group is None:
            return
        # ⚠️ `position` 必须在 `try` **之前**取 —— 失败回滚时要用它把 group 放回去。
        try:
            position = self.canvas.children.index(group)
        except ValueError:
            self._flare_batches = None
            return
        removed = False
        try:
            self.canvas.remove(group)
            removed = True
            group.clear()                   # 扔掉那些 (Color, Rectangle) 对
            if FLARE_FORCE_FAIL:            # 诊断: 在**会回滚的那段里**炸, 走一遍回退
                raise RuntimeError("forced failure (HG_FLARE_FORCE_FAIL)")
            context = RenderContext(use_parent_projection=True,
                                    use_parent_modelview=True)
            context.shader.vs = FLARE_VERTEX_SHADER
            context.shader.fs = FRAGMENT_SHADER
            if not context.shader.success:
                raise RuntimeError("flare batch shader failed to compile")
            # ⚠️ 必须用 `context[名字] = 值` —— `context.shader[...]` 是 Kivy **2.3.1**
            #    才有的, 设备上是 2.3.0, 写了会在画布构建时 TypeError(app 一启动就死)。
            context["texel_step"] = FLARE_STEP
            context["bounds"] = 1
            color = Color(1.0, 1.0, 1.0, 1.0)
            context.add(color)
            self.canvas.insert(position, context)
            self._flare_context = context
            self._flare_color = color
            self._flare_batches = FlareBatch(context)
            self._flare_rgb = None
            self._flare_rects = []          # 批处理路径不再用, 置空免得误读
            # ★ **成功信号必须打在这里**(建画布时), 不能打在 `install()` 那一刻。
            #   3号专家实测(2026-10-07): 强制失败时 stdout 里**两句同时出现** ——
            #       batch renderers: splash=batch neck=batch flare=batch   ← 假的
            #       flare batch failed, keeping per-Rectangle: RuntimeError(...)
            #   因为 `install_flares()` 只要**把 wrapper 挂上**就返回 True, 而 shader 是
            #   **建画布时**才编译的 ⇒ 那句打印区分的是"接线了 / 没接线",
            #   **不是"生效了 / 静默回退了"** —— 它会主动误导性能对比。
            #   现在: 成功打这句, 失败打上面那句, 二者互斥且都在**建画布**之后。
            global _FLARE_ACTIVE_LOGGED
            if not _FLARE_ACTIVE_LOGGED:
                _FLARE_ACTIVE_LOGGED = True
                print("flare batch active (shader compiled)")
        except Exception as exc:            # 装不上就退回逐 Rectangle, 不要连累整幅画
            print("flare batch failed, keeping per-Rectangle: %r" % (exc,))
            self._flare_batches = None
            # 🔴 **必须把 group 放回画布**(2026-10-07 自查发现): 上面已经 `remove` +
            #    `clear` 了, 而 `draw_flares_batched` 的回退是**去写那个 group** ——
            #    不放回去, 闪光就**无声地整层消失**(不崩、不报错、只是没画面)。
            #    这正是"兜底路径必须自己走出来对一遍"那条教训的又一例。
            self._flare_rects = []          # `_draw_flares` 会按需重建 (Color, Rectangle)
            # ⚠️ **只有真的摘下来过才放回去** —— 无条件 insert 会把同一个 group
            #    在画布列表里挂**两份** ⇒ Kivy 每帧 apply 两次 ⇒ 半透明的闪光被混合
            #    两遍、明显变亮(实测逐像素 +2~3 级, 14 个像素)。
            #    这是**负对照**(`HG_FLARE_FORCE_FAIL=1`)抓出来的: 没有它就只会看到
            #    "回退路径不崩", 看不出它在悄悄画两遍。
            if removed:
                try:
                    self.canvas.insert(position, group)
                except Exception as exc2:
                    print("flare fallback re-insert failed: %r" % (exc2,))

    def draw_flares_batched(self, now):
        if getattr(self, "_flare_batches", None) is None:
            return draw(self, now)          # 没装上 ⇒ 原路
        flares = self.flares
        n = len(flares)
        if self._flare_rgb != self.sand_light:
            self._flare_color.rgb = self.sand_light
            self._flare_rgb = self.sand_light
        if n == 0:
            self._flare_batches.update_raw(b"", 0)
            return
        np = _np
        if np is None:
            return draw(self, now)
        # ⚠️ 每个算式都要与逐颗版**逐字对齐** —— 尤其 `right = left + w`
        #    (不是 `x + w/2`): Kivy 的 `Rectangle` 就是按 `pos + size` 算角点的, 而
        #    `(x - w/2) + w` 与 `x + w/2` 在浮点下**不保证相等**。
        fx = np.fromiter((f["x"] for f in flares), dtype=np.float64, count=n)
        fy = np.fromiter((f["y"] for f in flares), dtype=np.float64, count=n)
        fe = np.fromiter((f["end"] for f in flares), dtype=np.float64, count=n)
        life = fe - now
        np.maximum(life, 0.0, out=life)
        life /= 0.08
        w = 2.0 + life * 2.0
        h = 0.8 + life * 0.4
        left = fx - w * 0.5
        bottom = fy - h * 0.5
        blk = np.empty((n, FLARE_TEXELS), dtype="<f4")
        blk[:, 0] = left
        blk[:, 1] = bottom
        blk[:, 2] = left + w
        blk[:, 3] = bottom + h
        blk[:, 4] = 0.45 * life
        self._flare_batches.update_raw(blk.tobytes(), n)

    draw_flares_batched._flare_batched = True
    widget_class._build_dynamic_canvas = build_wrapper
    widget_class._draw_flares = draw_flares_batched
    return True
