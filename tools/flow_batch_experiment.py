"""Opt-in benchmark experiment; production keeps the original Line pools."""

from array import array
import math
import os

from kivy.graphics import Mesh


# ---------------------------------------------------------------------------
# 纹理上传的诊断旋钮 —— **只给量具用, 出货路径一字不动**。
#
# 2026-10-07 那次 `blit.off` 消融把**整条 `blit_buffer` 调用**(调用 + 字节)一起摘掉了,
# 所以手上只有"上传总共值 0.12~0.31ms" —— **分不开"每次调用的固定开销"与"每字节带宽"**。
# 而这两者的结论**相反**, 决定要不要做纹理图集:
#   per-call  ⇒ 把 13 次调用并成 ~4 次能拿到这笔钱 ⇒ 图集值得做;
#   per-byte  ⇒ 图集要传整行 padding, **反而更慢** ⇒ 原地不动。
# 先量清楚再动手。
#
#   blit.rep  = N    同一次上传**重复 N 遍**(载荷一字不变) ⇒ 与 rep=1 的差 / (N-1)
#                    = **每次调用的价**。N 放大是必要的: 单次可能只有 10~20µs,
#                    落在本机 ±0.2~0.4ms 的噪声里, 不放大根本读不出来。
#   blit.wide = 1    每次上传都传**满整条纹理**(CHUNK*4 纹素) ⇒ 与 rep=1 比,
#                   差 = **每字节的价**。两次调用次数完全相同 ⇒ 干净的单变量。
#   blit.off  = 1    完全不传(= rep 0), 老开关, 语义不变。
#
# ⚠️ 三个旋钮都**只影响画面的新鲜度**(纹理停在上一帧), 不动帧循环的其余部分 ——
#    与 `blit.off` 同理, **只能用来量钱, 不许当出货配置**。
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _flag(env_name, file_name):
    """环境变量优先(桌面方便), 其次标记文件(**设备上唯一能用的那条**)。

    🔴 安卓 app 读不到宿主 shell 的环境变量 —— 设备单变量对照只能写标记文件。
    """
    if os.environ.get(env_name):
        return True
    try:
        return os.path.exists(os.path.join(_APP_DIR, file_name))
    except Exception:
        return False


def _read_int(path, default):
    try:
        with open(path, "r") as fh:
            text = (fh.read() or "").strip()
        return int(text) if text else default
    except Exception:
        return default


def _blit_rep():
    if _flag("HG_NO_BLIT", "blit.off"):
        return 0
    env = os.environ.get("HG_BLIT_REP")
    if env is not None:
        try:
            return max(0, int(env))
        except ValueError:
            return 1
    return max(0, _read_int(os.path.join(_APP_DIR, "blit.rep"), 1))


BLIT_REP = _blit_rep()
BLIT_WIDE = _flag("HG_BLIT_WIDE", "blit.wide")


def blit_texture(texture, data, texels):
    """把 `data` 的前 `texels` 个纹素传给 `texture`。

    出货路径 = `texture.blit_buffer(data, size=(texels,1), colorfmt="rgba",
    bufferfmt="ubyte")` **恰好一次** —— 与本函数出现之前逐字相同。
    `BLIT_REP`/`BLIT_WIDE` 两个旋钮都取默认时, 行为与老代码**完全一致**
    (守卫: `tools/_render_golden.py`)。

    ⚠️ 传满宽(`BLIT_WIDE`)用的是 `texture.size[0]` —— 各层的"每颗粒纹素数"不同
       (沙流 3 / 飞溅 4 / 闪光 5), 写死一个数会越过纹理边界。`data` 本来就一直是
       **整块** capacity 大小(比 `texels` 多), 所以传满宽不会读越界。
    """
    rep = BLIT_REP
    if not rep:
        return
    if BLIT_WIDE:
        texels = texture.size[0]
    for _ in range(rep):
        texture.blit_buffer(data, size=(texels, 1),
                            colorfmt="rgba", bufferfmt="ubyte")



def build_vertices(template, capacity, texels_per_particle, span):
    """拼"每槽 `len(template)` 个四边形顶点"的顶点表 —— **切片拼, 不逐元素跑生成器**。

    ## 为什么(2026-10-07)

    原来三个地方(沙流端点纹理 / 飞溅批 / 颈部批 / 闪光批)都写成三层嵌套生成器逐元素
    吐 `capacity × len(template) × 4` 个 float。实测 **0.74ms/块**(512 槽 / 6 顶点模板,
    真模板 20 顶点 ⇒ 约 2.2ms/块), 一次几何重建要建 **25 块 + 32 块** ⇒ **几十毫秒的卡顿**
    —— 改周期 / 转屏 / 分屏 / 拖窗都会撞上。

    而这张表里**只有 `u` 依赖槽位号**, 其余全是常量模式:
        (dx, dy, u_i, end) × len(template) × capacity
    ⇒ 常量部分用 `bytes * capacity` 铺一次(C 级), `u` 那一列用**步长切片**整体赋值。
    实测 **9.2×**, 且 `list(...) == list(...)` **逐个 float 相同**(守卫见下)。

    ⚠️ `u` 的算式必须**逐字**保持 `(i * texels_per_particle + 0.5) / span`
    (端点纹理那套 shader 靠 `texel_step` 恒定推位置, 这里少一个 bit 就错位)。
    守卫: `tools/_render_golden.py` + `tools/_probe_quadmesh_equiv.py`。
    """
    step = len(template) * 4
    base = array("f", (v for _dx, _dy, _end in template
                       for v in (_dx, _dy, 0.0, _end)))
    verts = array("f", base.tobytes() * capacity)
    col = array("f", ((i * texels_per_particle + 0.5) / span for i in range(capacity)))
    for k in range(len(template)):
        verts[k * 4 + 2::step] = col
    return verts

class DictFlowView:
    """老 main.py(粒子还是 dict 列表)的只读适配器。

    正式路径上 `_group_stream_particles()` 返**下标**、widget 上有 `_pv` 数组快照,
    走不到这里; 它只为 `tools/inspect_flow.py --source <main 分支的 main.py>` 那套
    对照闸门保留 —— 两侧用同一份渲染器, 只有被对照的 main.py 不同。
    """

    __slots__ = ("n", "x", "y", "vy", "tl", "sz", "light", "wp")

    def __init__(self, particles):
        self.n = len(particles)
        self.x = [p["x"] for p in particles]
        self.y = [p["y"] for p in particles]
        self.vy = [p["vy"] for p in particles]
        self.tl = [p.get("trail_time", 0.08) for p in particles]
        self.sz = [p["size"] for p in particles]
        self.light = [p["is_light"] for p in particles]
        self.wp = [p.get("wobble_phase", 0) for p in particles]


def flow_bucket(widget, bucket):
    """把 `_group_stream_particles()` 的桶翻成 (view, indices)。

    新版: 桶就是下标列表, view 是 widget 的 `_pv` 数组快照。
    老 main.py(`--source` 对照): 桶里是 dict, 现摊一份适配器 + `range`。
    """
    view = getattr(widget, "_pv", None)
    if view is None:
        return DictFlowView(bucket), range(len(bucket))
    return view, bucket


class FlowBatch:
    CHUNK = 512

    def __init__(self, group, width):
        self.group = group
        self.width = width
        self.parts = []
        if width == 1:
            self.template = [(0, 0, 0), (0, 0, 1)]
            self.indices = [0, 1]
            self.mode = "lines"
        else:
            # Kivy Line 的 10 段圆头帽 + 它的半宽约定。**每颗粒 20 顶点 / 54 索引**
            # (原 24 / 66), 覆盖像素不变:
            #   ① 帽的扇形从**矩形角**起扇, 不从平边中点 (0,0) —— 后者是边界上的点、
            #      不是顶点, 白白多一个顶点;
            #   ② 弧的第 0 个采样点与矩形角**逐位相同** —— 原写法里
            #      (center, first, arc0) 是零面积退化三角形, 一并去掉。
            #   凸多边形从任一顶点起扇覆盖的区域完全相同, 所以可见像素一致。
            angle = math.pi / 2
            half_pi = float(array("f", [math.pi / 2])[0])
            a0, a1 = angle - half_pi, angle + half_pi
            right = (math.cos(a0) * width, math.sin(a0) * width)
            left = (math.cos(a1) * width, math.sin(a1) * width)
            self.template = [
                (*right, 0), (*right, 1), (*left, 1), (*left, 0)]
            self.indices = [0, 1, 2, 0, 2, 3]
            for end, direction, first, last in ((0, -1, 0, 3), (1, 1, 1, 2)):
                base = len(self.template)
                # 弧采样点取 i=1..8 (i=0 与 first 重合, i=10 就是 last)
                for i in range(1, 9):
                    a = a0 + direction * (a1 - a0) * i / 10
                    self.template.append(
                        (math.cos(a) * width, math.sin(a) * width, end))
                pts = [first] + [base + k for k in range(8)] + [last]
                for k in range(8):          # 10 个点的扇形 = 8 个三角形
                    self.indices.extend((pts[0], pts[k + 1], pts[k + 2]))
            self.mode = "triangles"

    def update(self, view, indices, top_limit, motion_scale=1):
        """`view` 是粒子快照(`_pv` 或 dict 适配器), `indices` 是本桶的粒子下标。

        按下标读原生 float —— 不再逐颗粒取 numpy 标量(那比读 dict 还慢)。
        """
        stride = len(self.template) * 4
        index_stride = len(self.indices)
        ys = view.y
        vys = view.vy
        trails = view.tl
        total = len(indices)
        chunks = math.ceil(total / self.CHUNK)
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = min(self.CHUNK, total - start)
            if chunk == len(self.parts):
                mesh = Mesh(mode=self.mode)
                self.group.add(mesh)
                self.parts.append([mesh, array("f"), array("H"), 0, 0])
            part = self.parts[chunk]
            mesh, vertices, indices_arr, capacity, previous = part
            if capacity < count:
                capacity = min(self.CHUNK, max(32, count, capacity * 2))
                vertices = array("f", [0]) * (capacity * stride)
                indices_arr = array("H", (
                    index + i * len(self.template)
                    for i in range(capacity) for index in self.indices))
                part[1:4] = vertices, indices_arr, capacity
            for i in range(count):
                pi = indices[start + i]
                x, bottom = view.x[pi], ys[pi]
                top = min(top_limit, bottom + max(
                    2, abs(vys[pi]) * trails[pi] / motion_scale))
                base = i * stride
                for vertex, (dx, dy, end) in enumerate(self.template):
                    offset = base + vertex * 4
                    vertices[offset] = x + dx
                    vertices[offset + 1] = (top if end else bottom) + dy
            mesh.vertices = vertices
            if previous != count:
                mesh.indices = indices_arr[:count * index_stride]
                part[4] = count
        for part in self.parts[chunks:]:
            if part[4]:
                part[0].indices = array("H")
                part[4] = 0


def install(widget_class):
    build = widget_class._build_dynamic_canvas

    def build_batches(self):
        build(self)
        self._flow_batches = {}
        self._flow_reserve_counts = {
            key: len(pool) for key, (_group, _color, pool) in self._stream_pools.items()}
        for key, (group, color, pool) in self._stream_pools.items():
            group.clear()
            group.add(color)
            pool.clear()
            self._flow_batches[key] = FlowBatch(group, key[1])

    def draw_batches(self):
        for key, bucket in self._group_stream_particles().items():
            view, indices = flow_bucket(self, bucket)
            self._flow_batches[key].update(
                view, indices, self._taper["y_bot"], self._particle_motion_scale)

    widget_class._build_dynamic_canvas = build_batches
    widget_class._draw_stream = draw_batches
    widget_class.flow_renderer = "mesh_batch"
