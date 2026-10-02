"""Opt-in benchmark experiment; production keeps the original Line pools."""

from array import array
import math

from kivy.graphics import Mesh


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
            # Match Kivy Line's 10-segment round caps and its half-width convention.
            angle = math.pi / 2
            half_pi = float(array("f", [math.pi / 2])[0])
            a0, a1 = angle - half_pi, angle + half_pi
            right = (math.cos(a0) * width, math.sin(a0) * width)
            left = (math.cos(a1) * width, math.sin(a1) * width)
            self.template = [
                (*right, 0), (*right, 1), (*left, 1), (*left, 0)]
            self.indices = [0, 1, 2, 0, 2, 3]
            for end, direction, first, last in ((0, -1, 0, 3), (1, 1, 1, 2)):
                center = len(self.template)
                self.template.append((0, 0, end))
                for i in range(9):
                    a = a0 + direction * (a1 - a0) * i / 10
                    vertex = len(self.template)
                    self.template.append((math.cos(a) * width, math.sin(a) * width, end))
                    self.indices.extend((center, first if i == 0 else vertex - 1, vertex))
                self.indices.extend((center, len(self.template) - 1, last))
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
