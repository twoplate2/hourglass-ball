"""**表层滑动标记(20 根斜短线)的批处理渲染器** —— 可选, 默认不启用(2026-10-07)。

## 为什么

普查(`tools/_probe_canvas_instr.py`, 周期 15s): 画布共 **358** 条指令, 其中
**marker 族 61 条** = `Color` 20 + `BindTexture` 20 + `Line` 20 + `InstructionGroup` 1
—— 是仅次于颈部(98)的第二大族, 而它只画 **20 根 4~6px 的短线**。
按"一条指令 ≈ 1.2µs/帧"折算 ≈ **0.073ms**; 另有每颗 2 次 Kivy 属性写(`col.rgba` /
`ln.points`, 每次 ~1µs)≈ **0.04ms**。合计 ≈ **0.11ms(2.1%)**。

## 关键几何(为什么这次能做, 而以前没做)

标记是 **`Line(points=(x0,y0,x1,y1), width=w)`** —— **斜的**, 而沙流/飞溅那两套批处理
的着色器只会 `mix(bottom, top, texcoord.y)`(**竖直**线), 所以一直没动它。

但 Kivy 的 `Line` 网格是**在线的局部坐标系里**造的: 顶点 = `(dx, dy, sel)`,
`sel ∈ {0,1}` 选端点、`(dx, dy)` 是**像素偏移**(`dx` 沿法线、`dy` 沿线方向)。
**`flow_batch_experiment.FlowBatch` 的 `template` 就是这份局部偏移**(它已经做到与
逐 `Line` 逐像素一致)。所以斜线只差**把局部坐标旋到该线自己的方向**:

    u = normalize(p1 - p0)        # 单位方向
    n = (-u.y, u.x)               # 单位法线
    pos = mix(p0, p1, sel) + n * dx + u * dy

⇒ **模板一个字不用改**, 只换一个顶点着色器 + 把 4 个端点值搬进纹理。

## 每颗要存的量

`x0, y0, x1, y1`(4 个 float32 = 4 个纹素)+ `r, g, b, a`(再 4 个)⇒ **8 纹素/颗**。
颜色必须**逐颗**存: 标记有两档色调 **且** 每颗的透明度不同(两端淡入淡出 × 远端衰减),
所以不能像沙流那样"按桶共用一条 `Color`"。

## ⚠️ 它会**改像素**(这正是"牺牲少量表现"那一档)

着色器里的旋转是 GPU 上算的, 而 Kivy 是在 CPU 上用 `math.cos/sin` 造网格 ——
两者在**端点/圆头帽的边缘**会差 ULP 级, 落在像素边界上就翻一个像素。
**因此必须出改前/改后并排图 + 逐像素差量交回用户**(项目红线: 视觉改动由用户裁决);
本模块只在 `HG_MARKER_RENDERER=batch` 时启用, 装不上/编译失败**自动回退**原 `Line` 路径。

## 用法

    HG_MARKER_RENDERER=batch python tools/inspect_flow.py --label mkbatch ...
    python tools/_probe_marker_equiv.py          # 批处理 vs 逐 Line 的逐像素对照
"""

import os
from array import array

try:
    import numpy as np
except ImportError:                      # 逐颗 pack_into 兜底
    np = None

import flow_batch_experiment
from flow_splash_experiment import (
    CHUNK, TEXELS_PER_SPLASH, TEXEL_STEP, TEXEL_STEP_UNIFORM, PAD)

from kivy.graphics import BindTexture, Color, InstructionGroup, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
from kivy.graphics.texture import Texture

# **负对照开关**: 强制失败, 用来证明"兜底路径真的会走"而非只是没跑到。
# 必须在**摘完 Line 之后**触发, 才真的压到那段 restore(见 `install` 里的注释)。
FORCE_FAIL = os.environ.get("HG_MARKER_FORCE_FAIL") == "1"

TEXELS_PER_MARKER = 8        # x0,y0,x1,y1 + r,g,b,a
FLT = __import__("struct").Struct("<8f")
# 中性化: 两端都推到画面外 ⇒ 该槽位的四边形退化到看不见(与沙流的 `PAD_ENDPOINT` 同理)
PAD8 = FLT.pack(-1e5, -1e5, -1e5, -1e5, 0.0, 0.0, 0.0, 0.0)

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
    // 一颗 = 连续 8 个纹素: x0,y0,x1,y1,r,g,b,a
    float u0 = vTexCoords0.x;
    vec2 p0 = vec2(read_float(u0),               read_float(u0 + texel_step));
    vec2 p1 = vec2(read_float(u0 + texel_step*2.0), read_float(u0 + texel_step*3.0));
    vec4 rgba = vec4(read_float(u0 + texel_step*4.0), read_float(u0 + texel_step*5.0),
                     read_float(u0 + texel_step*6.0), read_float(u0 + texel_step*7.0));
    vec2 d = p1 - p0;
    float L = length(d);
    // 退化线(两端重合)时给一个确定的单位方向, 避免 0/0 变 NaN
    vec2 u = L > 1e-6 ? d / L : vec2(0.0, 1.0);
    vec2 n = vec2(-u.y, u.x);
    float sel = vTexCoords0.y;                 // 0/1 选端点(与 FlowBatch 的约定一致)
    vec2 pos = mix(p0, p1, sel) + n * vPosition.x + u * vPosition.y;
    frag_color = rgba;
    gl_Position = projection_mat * modelview_mat * vec4(pos, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
void main(void) {
    gl_FragColor = frag_color;
}
"""


class MarkerBatch:
    """一块 = 最多 `CHUNK` 根标记。顶点/索引只建一次, 每帧只写端点纹理。"""

    def __init__(self, group, width):
        self.group = group
        self.parts = []
        # 复用 FlowBatch 的局部偏移模板(它与逐 `Line` 逐像素一致)
        probe = flow_batch_experiment.FlowBatch(group, width)
        self.template = probe.template
        self.indices = probe.indices
        self.mode = probe.mode

    def _ensure_part(self, chunk):
        if chunk < len(self.parts):
            return self.parts[chunk]
        binding = BindTexture(index=1)
        mesh = Mesh(mode=self.mode)
        self.group.add(binding)
        self.group.add(mesh)
        capacity = CHUNK
        data = bytearray(capacity * TEXELS_PER_MARKER * 4)
        texture = Texture.create(size=(capacity * TEXELS_PER_MARKER, 1),
                                 colorfmt="rgba")
        texture.mag_filter = texture.min_filter = "nearest"

        def reload_data(target, _data=data):
            target.blit_buffer(_data, colorfmt="rgba", bufferfmt="ubyte")
        texture.add_reload_observer(reload_data)
        binding.texture = texture
        span = capacity * TEXELS_PER_MARKER
        vertices = flow_batch_experiment.build_vertices(
            self.template, capacity, TEXELS_PER_MARKER, span)
        idx = array("H", (index + i * len(self.template)
                          for i in range(capacity) for index in self.indices))
        part = [mesh, vertices, idx, texture, data, 0]
        mesh.vertices = vertices
        self.parts.append(part)
        return part

    def set(self, rows):
        """`rows` = 本帧的 `(x0, y0, x1, y1, r, g, b, a)` 列表(最多 `CHUNK` 条)。

        写入三件套与 `TextureFlowBatch.write_raw` **逐条同源**(那三条都是踩过的):
        ① **索引只增不减** —— 缩的时候不动 `indices`(动一次 = 整块顶点表重走一遍并标脏上传),
           改把用不到的槽位在端点纹理里写成 `PAD8`(两端都推到画面外 ⇒ 出不了像素);
        ② 只传**用到的**纹素, 不是整块 `CHUNK*8`;
        ③ 顺序: 先把纹理内容(含中性化)写完, 再上传。
        """
        total = len(rows)
        if total > CHUNK:
            total = CHUNK
        chunks = -(-total // CHUNK) if total else 0
        pack = FLT.pack_into
        for chunk in range(chunks):
            part = self._ensure_part(chunk)
            mesh, _vertices, indices, texture, data, previous = part
            start = chunk * CHUNK
            count = total - start
            if count > CHUNK:
                count = CHUNK
            off = 0
            for k in range(start, start + count):
                pack(data, off, *rows[k])
                off += 32
            upload = count
            if count > previous:
                mesh.indices = indices[:count * len(self.indices)]
                part[5] = count
            elif count < previous:
                data[count * 32:previous * 32] = PAD8 * (previous - count)
                upload = previous
            if upload:
                flow_batch_experiment.blit_texture(
                    texture, data, upload * TEXELS_PER_MARKER)
        for part in self.parts[chunks:]:
            if part[5]:
                part[0].indices = array("H")
                part[5] = 0


def install(widget_class):
    """把 20 条 `(Color, Line)` 换成一批 `Mesh`。**默认不启用**, 由调用方按开关决定。

    结构逐条照抄 `flow_splash_experiment.install_neck`(那套踩过坑、已验证):
    `RenderContext` + 编译检查 + `context[名字]=` 设 uniform(Kivy 2.3.0 不支持
    `shader[...]` 下标赋值) + **装载前先探测顶点纹理能力**(装不上要能回退)。

    ⚠️ **必须把原 `(Color, Line)` 从画布上摘掉** —— 只清 `points` 的话它们照样各占
    `Color+BindTexture+Line` 三条指令, 那 61 条一条都省不下来。

    🔴 **已知缺口: 回退路径还没走通过(2026-10-07, 接管前必须先补)**。
    现在的顺序是"先 `context.shader.success` 检查(228 行) → 再摘 Line(244 行)",
    但本项目已经查明: **纹理/着色器的报错发生在"建画布时", 不在 `install()` 时**
    (`CLAUDE.md` 里那条 ⚠️)⇒ `success` 可能是**乐观的**。真在建画布时编译失败的话:
      · 异常从 `_build_dynamic_canvas` 抛出去(外层 `try/except` 只包了 `install()`, 包不住);
      · 而**那 20 条 Line 已经被摘掉了** ⇒ **标记无声地整层消失**。
    这正是 `install_flares` 当初的那个坑("回滚时漏把 `_flare_group` 放回画布")。
    **要接线到出货, 先补两件**(照 `install_flares` 的 `removed` 标志那套):
      ① 摘 Line 时记下**摘了什么、从哪个父节点摘的**, 任何异常/失败都**原样放回**;
      ② 写 `tools/_probe_marker_fallback.sh`(**强制失败**跑一遍, 再与 `HG_MARKER_RENDERER=line`
         逐像素比) —— 并**先确认那句失败日志真的印出来了**, 否则"0 差异"可能只是两条路都没跑
         (这条也踩过, 见 `CLAUDE.md` 的「兜底路径必须自己走出来对一遍」)。
    """
    build = widget_class._build_dynamic_canvas
    draw = widget_class._draw_surface_markers
    if getattr(draw, "_marker_batched", False):
        return False

    def build_marker_batch(self):
        build(self)
        pool = getattr(self, "_surface_marker_pool", None)
        if not pool:
            self._marker_batch = None
            return
        units = glGetIntegerv(GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)[0]
        if units < 1:
            raise RuntimeError("Vertex texture sampling is unavailable")
        # 把原来的 20 对 (Color, Line) 从画布上摘掉(见 docstring 的 ⚠️)
        first = pool[0][1]
        root = None
        for node in self.canvas.children:
            try:
                if first in node.children:
                    root, position = node, node.children.index(first)
                    break
            except Exception:
                continue
        if root is None:
            self._marker_batch = None
            return                       # 结构不是预期的样子 ⇒ 装不上就保持原路
        # 🔴 **摘下来的东西要记着, 出事原样放回** —— 照 `install_flares` 的 `removed` 标志那套。
        #    这里的风险比别处大: 那 20 条 `Line` **就是兜底路径本身**, 摘掉以后如果
        #    后面任何一步失败, 标记会**无声地整层消失**(项目栽过两次的那类 bug)。
        removed = []
        added_context = False
        try:
            context = RenderContext(use_parent_projection=True,
                                    use_parent_modelview=True)
            context.shader.vs = VERTEX_SHADER
            context.shader.fs = FRAGMENT_SHADER
            if not context.shader.success:
                raise RuntimeError("marker batch shader failed to compile")
            context[TEXEL_STEP_UNIFORM] = TEXEL_STEP
            context["endpoints"] = 1
            # 🔴 **必须按线宽分档建批** —— 标记有 `SURFACE_MARKER_SIZE` 与 `_SIZE_BIG` 两档,
            #    模板(圆头帽半径 = 线宽)是**按线宽造的** ⇒ 用 `max(width)` 一个批会把细的那批
            #    整根画粗 1px(这是设计时就该想到的, 2026-10-07 自查出来)。
            batches = {}
            for width in sorted({int(round(ln.width)) for _c, ln in pool}):
                group = InstructionGroup()
                context.add(group)
                batches[width] = MarkerBatch(group, width)
            root.insert(position, context)
            # ⚠️ **插进去的 context 也要记着** —— 负对照(`_probe_marker_fallback.py`)实测:
            #    只把 Line 放回去、不把 context 摘掉的话, 画布会**多出 context + 它里面的组**
            #    (319 -> 321), 即"回了退但留了垃圾"。这是那个负对照抓出来的真 bug。
            added_context = True
            for color, line in pool:
                for node in (color, line):
                    for cand in self.canvas.children:
                        try:
                            if node in cand.children:
                                cand.remove(node)
                                removed.append((cand, node))
                                break
                        except Exception:
                            continue
            if FORCE_FAIL:
                # 放在**摘完之后** —— 这样才真的走到下面的 restore, 而不是"没摘成所以没事"
                raise RuntimeError("forced failure (HG_MARKER_FORCE_FAIL)")
        except Exception as exc:
            if added_context:
                try:
                    root.remove(context)        # 先把插进去的 context 摘掉, 再放回 Line
                except Exception:
                    pass
            for cand, node in removed:          # **原样放回** ⇒ 退回原 `Line` 路径
                try:
                    cand.add(node)
                except Exception:
                    pass
            print("marker batch failed (%s); restored %d per-Line instruction(s)"
                  % (exc, len(removed)))
            self._marker_context = None
            self._marker_batch = None
            return
        self._marker_context = context
        self._marker_batch = batches
        # 原池子留着(热函数照跑), 但**画布上没有它们了** ⇒ 出不了像素
        self._marker_batch_rgb = [None] * len(pool)

    def draw_batched(self, upper_height, h_mound):
        draw(self, upper_height, h_mound)
        batches = getattr(self, "_marker_batch", None)
        if not batches:
            return
        rows = {}
        for color, line in self._surface_marker_pool[:self._surface_marker_n]:
            pts = line.points
            if not pts:
                continue
            rgba = color.rgba
            rows.setdefault(int(round(line.width)), []).append(
                (pts[0], pts[1], pts[2], pts[3],
                 rgba[0], rgba[1], rgba[2], rgba[3]))
            line.points = []             # 池子复用, 下次从头填
        for width, batch in batches.items():
            batch.set(rows.get(width, ()))

    build_marker_batch.__name__ = "_build_dynamic_canvas"
    widget_class._build_dynamic_canvas = build_marker_batch
    draw_batched._marker_batched = True
    draw_batched._orig = draw            # 取证脚本要拿它跑"原路径"做对照
    widget_class._draw_surface_markers = draw_batched
    widget_class.marker_renderer = "batch"
    return True
