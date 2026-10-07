"""Opt-in endpoint texture batching; cap geometry and palette order are retained."""

import bisect
from array import array
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

# 诊断开关: **只跳过端点纹理上传**。画面会停在上一帧的沙流上(其余一切照旧),
# 所以**只能用来量"这一笔上传值多少毫秒"**, 不许当出货配置。用法:
#     HG_NO_BLIT=1 tools/_one_bench.sh noblit
# 实测(2026-10-07, MuMu): 15s 图元 2.89 → 2.83 / 5s 2.86 → 2.70 / 1s 3.76 → 3.60
# ⇒ **上传只值 0.06~0.16ms, 远不是 `update()` 那 0.81ms 的大头** —— 钱在 numpy 上。
# 上传的开关**已经挪进 `flow_batch_experiment.blit_texture`**(那里还有 `blit.rep` /
# `blit.wide` 两个量具旋钮, 用来分"每次调用的固定开销"与"每字节带宽" —— 见那边的注释)。
# 老的 `blit.off` 标记文件语义不变(等价于 rep=0)。

# ★ **把所有桶拼成一条再算**(2026-10-07 性能)。
# 原写法每桶各跑一遍算式: 每帧 9 桶 × ~15 次 numpy 调用 = ~135 次**固定开销**
# (每次 2~5µs, 与数组长度无关), 而每桶只有一百多颗 ⇒ 开销盖过数据本身。
# 拼起来后同一段算式只对 ~1600 个元素跑一遍, 调用数塌到 ~15 次。
# 置 0 退回逐桶老路(A/B 用, 两边必须逐像素相同)。
FUSE_BUCKETS = os.environ.get("HG_FLOW_FUSE", "1") != "0"

# 诊断旋钮(A/B 用, 默认关): 恢复"`TextureFlowBatch.update` 开头无条件读那四个惰性
# list 快照"的旧行为。四个都是 `_FlowView` 的**惰性属性**, 第一次读就 `tolist()`
# 一整份(n≈1500), 而**安卓走的 numpy 分支一个都不读**。设备单变量对照要用**标记文件**。
LAZY_VIEW_OFF = _flag("HG_LAZY_VIEW_OFF", "lazyview.off")


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
    def __init__(self, group, width, order=0, attach=None):
        super().__init__(group, width)
        # 🔴 **空壳桶不再挂进画布**(2026-10-07)。24 个桶里每帧只有 ~8~10 个有内容,
        #    而每个桶无论空不空都占 **`InstructionGroup` + `Color` 两条指令**
        #    (设备实测: flow 族 73 条里 **48 条**是这 24 对) —— 那 48 条每帧都要走一次
        #    `apply()`, 而它们**画不出任何像素**。
        #    ⚠️ **顺序必须保持**: 沙流是不透明线, 不同色调重叠时"谁后画谁赢" ⇒
        #    改成按需挂之后, 必须**插到与该桶原次序一致的位置**(`attach` 回调里做),
        #    不能简单 append。守卫: `tools/_render_golden.py --check` 逐图一致 +
        #    `tools/_probe_canvas_instr.py` 的**指令条数**必须相应下降。
        #    🔴 **2026-10-07 补充**: `warm()` 会为**每个**桶建块 ⇒ 24 个组都会挂上
        #    (比"只挂有内容的 ~8 个"多 48 条指令 ≈ 0.06ms/帧)。这是**故意换的** ——
        #    省下的是"第一次有内容那一帧建 7 块"的设备实测 43.88ms。
        self._order = order
        self._attach = attach
        self._attached = attach is None          # None ⇒ 老行为(建时就已挂好)
        # 每块**上一帧真正写了多少条**(≠ `part[4]` 的"画多少条")。见 `update` 里那段。
        self.live = []

    def _ensure_part(self, chunk, count):
        if not self._attached:
            self._attached = True
            self._attach(self._order, self.group)      # 保序插进 context
        # ⚠️ **不要在这里预建 parts**(2026-10-07, 1号专家实测空壳占一半)。
        #    桶按 (色档, 线宽) 有 **24** 个, 每帧真正有内容的只有 **~8** 个; 预建的那些
        #    每个都带 `BindTexture + (Mesh 自己那条) + Mesh` **三条指令**, 空桶照样被遍历。
        #    实测摘掉 16 个空 part: on_draw −0.090ms/帧(桌面)。
        #    ⚠️ 之前**不敢**懒建: 建一块要跑三层生成器吐 12288 个 float(~2.2ms), 懒建等于
        #       把卡顿挪进帧里。现在 `build_vertices` 只要 ~0.24ms(见 1.214) ⇒ 可以懒建了。
        #    🔴 **但懒建的代价在 2026-10-07 被用户抓到了**: 建块落在"**所有桶同时第一次有
        #       内容**"那一帧(沙柱注满, 沙柱注满前不出粒子)⇒ 设备上那一帧 `图元` **43.88ms**,
        #       就是"必然有一帧很低"。⇒ 现在由 `warm()` 在**没在跑**的闲帧里一帧一块地建好,
        #       这里仍保持"不预建"(运行中才建的那几块照旧按需)。
        #    `_ensure_part` 本来就是按需建的; capacity 恒为 CHUNK ⇒ u 步长与分块无关不变。
        #    `reserve` 保留在签名里只为兼容调用方, 已经不预分配任何东西。
        if chunk == len(self.parts):
            binding = BindTexture(index=1)
            mesh = Mesh(mode=self.mode)
            self.group.add(binding)
            self.group.add(mesh)
            self.parts.append([mesh, array("f"), array("H"), 0, 0, None, None, binding])
            self.live.append(0)
        part = self.parts[chunk]
        if part[3] < count:
            # 固定 capacity = CHUNK: u 步长与分块无关, shader 只需一个 uniform。
            capacity = self.CHUNK
            data = bytearray(capacity * TEXELS_PER_PARTICLE * 4)
            # 🔴 **先整块中性化**(2026-10-07): 索引马上要一次给满(见下), 用不到的槽位
            #    **会被画出来** —— 零端点在画布原点留一簇小方块(不是"看不见")。
            data[:] = PAD_ENDPOINT * capacity
            texture = Texture.create(
                size=(capacity * TEXELS_PER_PARTICLE, 1), colorfmt="rgba")
            texture.mag_filter = texture.min_filter = "nearest"

            def reload_data(target):
                target.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")

            reload_data(texture)
            texture.add_reload_observer(reload_data)
            span = capacity * TEXELS_PER_PARTICLE
            # ★ 顶点表改用切片拼(原来三层生成器逐元素 ⇒ 0.74~2.2ms/块 × 25 块)
            vertices = flow_batch_experiment.build_vertices(
                self.template, capacity, TEXELS_PER_PARTICLE, span)
            indices = array("H", (
                index + i * len(self.template)
                for i in range(capacity) for index in self.indices))
            part[1:4] = vertices, indices, capacity
            part[5:7] = texture, data
            part[7].texture = texture
            part[0].vertices = vertices
            # 🔴 **索引在建块时就一次给满**(2026-10-07, 用户平板 165Hz 的 log 查出)。
            #    原来只有 `warm()` 过的那几块才给满, **运行期才建的块** `part[4]` 从
            #    "当时的颗数"起步 ⇒ 桶还在长大 ⇒ `count > previous` **每帧都成立** ⇒
            #    每帧重赋一次索引(每次整块顶点表重建 160 KiB)。平板实测: 15s 档
            #    **91 次**赋值 / 14.5 MB 顶点数据, 每次都打在 `图元` 上 —— 那一档最慢的
            #    几帧(9.7 / 9.2ms)恰好就是 `ia=1, KiB=160` 的帧。
            #    ⚠️ 而**预热在 165Hz 上跑不完**: 0.5s 宽限 ÷ 每块 ≈6ms ⇒ 一帧只塞得下
            #       一块 ⇒ 只有 ~41 块赶得上, 后面颈部/飞溅的块全在运行期建 ⇒
            #       **光靠"预热"救不了**, 必须让"运行期建的块"与"预热过的块"行为一致。
            part[0].indices = indices
            part[4] = capacity
        return part

    def warm(self, chunk=0):
        """**把第 `chunk` 块现在就建好、索引一次给满** —— 让运行期第一次有内容时不必再付这笔钱。

        为什么要有它(2026-10-07, 用户设备 Lenovo TB323FU 的 log 定位):

        沙柱注满前**不出粒子** ⇒ 注满那一帧**所有桶同时**第一次拿到内容 ⇒ 那一帧要建 7 块。
        设备实测那一帧的 `图元` 是 **43.88ms**(稳态只有 2.9ms), 且 1s/5s/15s 三档分别落在
        t=0.156/0.25/0.25s —— **正好是 `_neck_fill_time`**。之后每帧还有 ~8 次"索引重赋"
        (每次把整块 ~90 KiB 顶点表重走一遍)一路衰减到 0.7 次/帧。
        ⇒ 用户看到的"必然有一帧很低"(1% low 63~75 / 最慢帧 28~35ms)就是它。

        **索引给满**这一半同样重要: `update` 只在 `count > part[4]` 时重赋索引(那次要
        `clear_data + add_vertex_data` 整块顶点表, 是"只增不减"规则下**唯一**还会付的
        重建), 一旦 `part[4] = CHUNK` 就**永远不会再发生**; 用不到的槽位靠 `PAD_ENDPOINT`
        推到画面外 ⇒ **一个像素都不多画**。

        ⚠️ 代价: 每块恒定画 `CHUNK` 个四边形(而不是"历史最高")。多出来的是**顶点着色器**
        的活(片元全被裁掉), 实测档位下可忽略; 换来的是运行期**一次重建都不发生**。
        ⚠️ 只在**没在跑**的时候调(见 `main.py:_warm_batches_step`) —— 设备上建一块 ≈ 6ms。
        """
        part = self._ensure_part(chunk, self.CHUNK)
        data = part[6]
        # 整块先中性化: 索引给满之后, 用不到的槽位**会被画出来**, 零端点会在画布原点
        # 留下一簇小方块(不是"看不见")。
        data[:] = PAD_ENDPOINT * self.CHUNK
        flow_batch_experiment.blit_texture(
            part[5], data, self.CHUNK * TEXELS_PER_PARTICLE)
        if part[4] != self.CHUNK:
            part[0].indices = part[2]          # 全量索引(容量个四边形)
            part[4] = self.CHUNK
        self.live[chunk] = 0
        return part

    def update(self, view, indices, top_limit, motion_scale=1):
        """`view` 是 widget 的 `_pv`(本帧 list 快照), `indices` 是本桶的粒子下标。

        按下标读原生 float, 不再逐颗粒取 numpy 标量。
        """
        STATS["buckets"] += 1
        total = len(indices)
        chunks = -(-total // self.CHUNK)
        # ⚠️ `ys/vys/trails/xs` 原来**无条件**写在函数开头。那四个是 `_FlowView` 的**惰性
        #    属性** —— 第一次读就把整个 numpy 数组 `tolist()` 一份(n≈1500, 6 个字段
        #    桌面实测 0.11ms)。而安卓走的是**下面的 numpy 分支**, 那里读的是
        #    `view.nx/ny/nvy/ntl`, **这四个 list 一个都不读**; 融合路径更是只对**空桶**
        #    调 `update`(空桶 `chunks == 0` ⇒ 循环体一次都不跑 ⇒ 照样不读)。
        #    ⇒ 每帧白建 4×~1500 个 float。挪进唯一读它们的那个分支(纯删无用功, 逐位不变)。
        if LAZY_VIEW_OFF:
            # 诊断臂: 恢复"无条件读四个惰性 list 快照"的旧行为(A/B 用, 默认关)。
            ys = view.y
            vys = view.vy
            trails = view.tl
            xs = view.x
        else:
            ys = vys = trails = xs = None
        # 向量化: 本桶所有颗粒的 (x, 底端, 顶端) 一次算完, 再 astype('<f4') 出字节。
        # 逐位等价已实测: astype('<f4') 与 struct.pack('<f') 对 30 万样本(含 0/-0/inf/
        # denormal/float32 极值)完全相同, 整段公式的字节输出也完全相同
        # —— 见 tools/test_pack_equiv.py。
        # 阈值由 _FlowView.use_np 统一决定(见 main.py:_NUMPY_MIN): 粒子少时
        # numpy 的逐桶固定开销盖过收益。
        use_np = np is not None and total > 0 and view.use_np
        if use_np:
            # `asarray` 而不是 `array`: 上游已经给 numpy 数组时不再拷一份
            nidx = np.asarray(indices, dtype=np.intp)
        else:
            pack = FLOAT3.pack_into
            if total:
                # 只有**标量**分支读这四个; 空桶连它也省掉。
                ys = view.y
                vys = view.vy
                trails = view.tl
                xs = view.x
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
                # ★ **直接建 float32 块**: 原来先建 float64 再 `astype("<f4")`,
                #   那是多一次分配 + 多一趟遍历。往 float32 数组里赋 float64 走的
                #   同样是 IEEE 就近舍入 ⇒ 结果与 `astype` **逐位相同**。
                blk = np.empty((count, 3), dtype="<f4")
                blk[:, 0] = view.nx[idx]
                blk[:, 1] = bottom
                blk[:, 2] = top
                # 尾部(count*12 之后)保持上一帧的陈旧字节, 与逐颗粒写法一致:
                # 那部分不渲染(mesh.indices 已按 count 截断)。
                data[:count * 12] = blk.tobytes()
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
            live = self.live[chunk]
            _upload = count
            if count > previous:
                # **只在这里赋值** —— 每块的索引只增不减, 暖机之后基本不再发生。
                # 旧写法是 `previous != count` 就赋值 ⇒ 粒子数一变, 整块 160 KiB 顶点
                # 就走一遍 clear_data+add_vertex_data 并把整个 VBO 标脏。
                # 实测(5s 档, 1529 颗): 24 块全变 ⇒ 24 次赋值 / 2112 KiB / 帧。
                # (`warm()` 过的那一块 `previous` 已经是 `CHUNK` ⇒ 这里一次都不会进。)
                mesh.indices = _indices[:count * len(self.indices)]
                part[4] = count
                STATS["index_assigns"] += 1
                STATS["vertex_bytes"] += len(_vertices) * 4
            if count < live:
                # 缩了: **不动 indices**(动了又触发整块重建), 改把用不到的槽位在端点
                # 纹理里推到画面外 —— shader 里 x 直接决定横向位置, -1e5 时整条线被裁掉。
                # 🔴 **只补"上一帧还在写"的那一段(`live`), 不是 `previous`(历史最高)**
                #    (2026-10-07): 原来按 `previous` 补 ⇒ 一个桶只要曾经高过, **之后每一帧**
                #    都要重写并重传那一段(`previous` 是历史最高、不会回落), "只传用到的纹素"
                #    被这条抵消掉了。≥ `live` 的槽位在**上一次收缩时就已经是 PAD**, 保持即可。
                #    已下标定: `_probe_warm_equiv.py` 逐帧逐像素比。
                data[count * 12:live * 12] = PAD_ENDPOINT * (live - count)
                STATS["neutralized"] += live - count
                if live > _upload:
                    _upload = live
            self.live[chunk] = count
            # **只传用到的纹素**。原来一律整块 `CHUNK*3` 纹素(6KB/块) —— 而每块实际常只有
            # 几百颗 ⇒ 白传的部分比用到的还多。MuMu 上 `glTexSubImage2D` 实测 ~80µs/次,
            # 8 块就是 ~0.6ms/帧, 这是"只传用到那点"最直接的一笔。
            # 上传走共用助手(它带 `blit.rep` / `blit.wide` 两个量具旋钮, 默认等价于
            # 下面这一行的老写法)。`HG_NO_BLIT` 的老语义(整条不传)= rep 0, 由它接管。
            flow_batch_experiment.blit_texture(
                texture, data, _upload * TEXELS_PER_PARTICLE)
            STATS["chunks"] += 1
        for chunk in range(chunks, len(self.parts)):
            # 整块这一帧不用了。**不再清索引** —— 清空(`mesh.indices = array("H")`)本身便宜
            # (`icount==0` 时 `build()` 直接 `clear_data()`, 不重走顶点表), 但**下次这块
            # 再有内容时就要重赋索引** ⇒ 那次是整块顶点表重建(设备实测 ~118 KiB/次,
            # 稳态 0.708 次/帧)。改成把**上一帧还在写的那一段**中性化: 一次性写完传完,
            # 索引与 `part[4]` 原样留着 ⇒ **再也不会重赋**。
            live = self.live[chunk]
            if not live:
                continue
            part = self.parts[chunk]
            data = part[6]
            data[:live * 12] = PAD_ENDPOINT * live
            flow_batch_experiment.blit_texture(
                part[5], data, live * TEXELS_PER_PARTICLE)
            self.live[chunk] = 0
            STATS["neutralized"] += live
            STATS["chunk_clears"] += 1        # 语义: "整块置空"(现在是中性化, 不是清索引)

    def write_raw(self, raw, off, total):
        """把**已经算好**的端点字节(`raw`, 每条 12 字节)从第 `off` 条起写 `total` 条。

        `raw` 由 `draw_texture_batches` 对所有桶**一次算完**(见那里的注释)。
        这里只剩"切片 + 索引 + 上传", 与 `update()` 的尾部逐字相同 ——
        唯一的差别是字节从 `raw` 的对应区间 memcpy 过来, 而不是现算。
        """
        src = off * 12
        chunks = -(-total // self.CHUNK)
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = total - start
            if count > self.CHUNK:
                count = self.CHUNK
            part = self._ensure_part(chunk, count)
            mesh, _vertices, _indices, _capacity, previous, texture, data, _binding = part
            data[:count * 12] = raw[src + start * 12:src + (start + count) * 12]
            live = self.live[chunk]
            _upload = count
            if count > previous:
                mesh.indices = _indices[:count * len(self.indices)]
                part[4] = count
                STATS["index_assigns"] += 1
                STATS["vertex_bytes"] += len(_vertices) * 4
            if count < live:
                # 只补上一帧还在写的那一段(口径与 `update` 逐字相同)。
                data[count * 12:live * 12] = PAD_ENDPOINT * (live - count)
                STATS["neutralized"] += live - count
                if live > _upload:
                    _upload = live
            self.live[chunk] = count
            # 上传走共用助手(它带 `blit.rep` / `blit.wide` 两个量具旋钮, 默认等价于
            # 下面这一行的老写法)。`HG_NO_BLIT` 的老语义(整条不传)= rep 0, 由它接管。
            flow_batch_experiment.blit_texture(
                texture, data, _upload * TEXELS_PER_PARTICLE)
            STATS["chunks"] += 1
        for chunk in range(chunks, len(self.parts)):
            live = self.live[chunk]          # 口径与 `update` 的尾部逐字相同(见那里的注释)
            if not live:
                continue
            part = self.parts[chunk]
            data = part[6]
            data[:live * 12] = PAD_ENDPOINT * live
            flow_batch_experiment.blit_texture(
                part[5], data, live * TEXELS_PER_PARTICLE)
            self.live[chunk] = 0
            STATS["neutralized"] += live
            STATS["chunk_clears"] += 1


def install(widget_class):
    build = widget_class._build_dynamic_canvas

    def build_texture_batches(self):
        build(self)
        self._stream_np_only = True      # 上游只建下标数组桶(见 `_group_stream_particles`)
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
        # 🔴 **按需挂进 context, 但要保住原次序**(见 `TextureFlowBatch.__init__` 的注释)。
        #    `placed` 是"已经挂进去的 order", 升序; 新桶插在**序号比我小的那些之后**
        #    ⇒ 画布里的相对次序与 `_stream_pools` 的迭代序**完全一致**。
        placed = []

        def _place(order, group):
            pos = bisect.bisect_left(placed, order)
            placed.insert(pos, order)
            context.insert(pos, group)

        for order, (key, (group, color, pool)) in enumerate(self._stream_pools.items()):
            self.canvas.remove(group)
            group.clear()
            group.add(color)
            pool.clear()
            self._flow_batches[key] = TextureFlowBatch(group, key[1], order, _place)
        self.canvas.insert(position, context)
        self._flow_texture_context = context

    def draw_texture_batches(self):
        # `_stream_np_only` 在装配时置位 ⇒ `_group_stream_particles` 只建**下标数组**桶,
        # 这里直接拿去用(省掉 tolist/array 的往返); 粒子数低于 `_NUMPY_MIN` 时它退回
        # Python list 桶, `update` 的逐颗分支照样吃 list ⇒ 两条都安全。
        view = self._pv
        top_limit = self._taper["y_bot"]
        motion = self._particle_motion_scale
        buckets = self._group_stream_particles()
        batches = self._flow_batches
        if np is None or not view.use_np or not FUSE_BUCKETS:
            for key, bucket in buckets.items():
                batches[key].update(view, bucket, top_limit, motion)
            return
        # ---- 融合路径: 所有桶拼成一条, 算式只跑一遍(见模块头 FUSE_BUCKETS) ----
        # ⚠️ 桶的**顺序**就是 dict 的插入序(`_group_stream_particles` 按桶码升序填),
        #    与逐桶调用完全一致; 桶内顺序仍是 `order` 的升序切片。
        keys = [k for k in buckets if len(buckets[k])]
        for key in buckets:
            if not len(buckets[key]):
                # 空桶: 走老路把它的块清掉(update 里 chunks=0 那段), 与原来逐字相同。
                # 🔴 **但只有"上一帧还在画"的块才需要清**(2026-10-07): 24 个桶里每帧只有
                #    ~10 个有内容, 另外 14 个**每帧都白调一次 `update`**(函数入口 +
                #    `STATS` + 逐 part 走一遍尾部循环)。`warm()` 之后每块都建好了,
                #    而"没写过"的块 `live == 0` ⇒ 尾部循环本来就什么都不做 ⇒ **纯白调**。
                #    判据是**调用次数**(`prof_android` 的 `calls/frame`), 不是时间 ——
                #    这台设备分辨不出 <0.5ms 的时间差, 但这个调用是**逐次确定**地消失的。
                _b = batches[key]
                if any(_b.live):
                    _b.update(view, [], top_limit, motion)
        if not keys:
            return
        if len(keys) == 1:
            allidx = np.asarray(buckets[keys[0]], dtype=np.intp)
        else:
            allidx = np.concatenate([np.asarray(buckets[k], dtype=np.intp)
                                     for k in keys])
        # 以下算式与 `update()` 的 numpy 分支**逐字相同**, 只是作用在拼接后的长数组上
        # (逐元素运算与数组长度无关 ⇒ 每位相同)。
        bottom = view.ny[allidx]
        vy = np.abs(view.nvy[allidx])
        trail = vy * view.ntl[allidx] / motion
        np.maximum(trail, 2.0, out=trail)
        top = bottom + trail
        np.minimum(top, top_limit, out=top)
        blk = np.empty((allidx.size, 3), dtype="<f4")
        blk[:, 0] = view.nx[allidx]
        blk[:, 1] = bottom
        blk[:, 2] = top
        raw = blk.tobytes()
        off = 0
        for key in keys:
            c = len(buckets[key])
            batches[key].write_raw(raw, off, c)
            off += c

    widget_class._build_dynamic_canvas = build_texture_batches
    widget_class._draw_stream = draw_texture_batches
    widget_class.flow_renderer = "mesh_endpoint_texture"
