"""GPU grain advection for the reservoir, neck and lower contact surface.

Two-phase flow follows Catlike Coding's Texture Distortion / Valve flow-map approach.
Only grain detail is advected; the palette and broad lighting stay in world space.
"""

import math
import os

from kivy.graphics import Color, Mesh, RenderContext


VERTEX_SHADER = """
$HEADER$
varying vec2 sand_position;
void main(void) {
    sand_position = vPosition;
    tex_coord0 = vTexCoords0;
    frag_color = color * vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(vPosition, 0.0, 1.0);
}
"""

GRAIN_FRAGMENT_HEADER = """
$HEADER$
varying vec2 sand_position;
uniform vec4 sand_geometry; // center x, radius, upper bottom, mouth top
uniform vec3 sand_base;
uniform vec3 sand_dark;
uniform vec3 sand_light;
uniform vec2 sand_lighting; // shade, vertical gradient
uniform float sand_clock;
uniform float sand_mix;
uniform vec3 sand_tail; // mean top, grain-front amplitude, tube half-width
uniform vec4 sand_free; // outlet y, 边缘松散度, tube half-width, 前沿 y (<=0 关闭)
uniform float sand_seam_band;   // 交界过渡半带宽(**球直径的比例**); <=0 ⇒ 阶跃(旧行为)
uniform float sand_neck_anchor; // 颈部那套 UV 的 v 锚点(= main.py 的 NECK_UV_ANCHOR)

float luma(vec3 c) {
    return dot(c, vec3(0.299, 0.587, 0.114));
}
float lighting(vec2 uv) {
    float x = uv.x * 2.0 - 1.0;
    float edge = clamp((x * x - 0.64) / 0.36, 0.0, 1.0);
    edge = edge * edge * (3.0 - 2.0 * edge);
    return sand_lighting.x * (sand_lighting.y * (uv.y - 0.5)
                              - 0.07 * x - 0.16 * edge);
}
float grain(vec2 uv) {
    vec2 sample_uv = fract(uv);
    vec3 c = texture2D(texture0, sample_uv).rgb;
    float delta = luma(c) - luma(sand_base);
    float span = delta >= 0.0 ? luma(sand_light) - luma(sand_base)
                             : luma(sand_base) - luma(sand_dark);
    return delta / max(span, 0.001) - lighting(sample_uv);
}
"""

FRAGMENT_SHADER = GRAIN_FRAGMENT_HEADER + """
void main(void) {
    // 🔴 **2026-10-09: 交界处做过渡。** 用户报的:「上面沙漏瓶子沙子的渲染, 和沙流沙柱的
    //    渲染, 这2个渲染是不同的, 所以可以看到界限」—— 放大看确认为**质地变了**(球那头
    //    颗粒明显、细柱那头平), 不是一条硬线。用户要「做个过渡」。
    //    做法: 把**两边各自的取样式逐字写出来**, 按到交界的距离交叉淡入淡出 ——
    //      · 球那套: 矩形 `tex_coords` = 按高度截的完整纹理 ⇒ `v = (y − 上球沙底)/直径`
    //      · 颈那套: `v = NECK_UV_ANCHOR + (上球沙底 − y)/直径`
    //    交界 = `sand_geometry.z`(= 上球沙底), 正好是 shader 已有的量。
    //    ⚠️ **`SAND_SEAM_BAND = 0` 时 `smoothstep` 退化成阶跃 ⇒ 逐字等于旧行为**
    //      (球区取球那套、颈区取颈那套), 所以这是个纯"过渡"改动, 可无损回退。
    //    ⚠️ 颈那侧 `v` 会为负 ⇒ 必须 `fract`(纹理本来就按可平铺噪声用, `grain()` 里也 fract)。
    vec2 uv_ball = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                        (sand_position.y - sand_geometry.z) / diameter);
    vec2 uv_neck = vec2(uv_ball.x, SAND_NECK_UV_ANCHOR - uv_ball.y);
    // ⚠️ 带宽是**球直径的比例**不是像素 —— 桌面 400×800(直径≈196) 与平板 1904×2890
    //    (直径≈1132) 差 5.8 倍, 写死像素会在平板上缩得看不见。
    float seam_b = SAND_SEAM_BAND * diameter;
    float seam_w = (seam_b > 0.0)
        ? smoothstep(-seam_b, seam_b, sand_position.y - sand_geometry.z)
        : step(0.0, sand_position.y - sand_geometry.z);
    vec4 original = mix(texture2D(texture0, fract(uv_neck)),
                        texture2D(texture0, uv_ball), seam_w);
    float diameter = max(2.0, sand_geometry.y * 2.0);
    vec2 uv = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                   (sand_position.y - sand_geometry.z) / diameter);
    vec2 from_mouth = vec2((sand_position.x - sand_geometry.x) / diameter,
                          max(0.0, sand_position.y - sand_geometry.w) / diameter);
    float sink = 1.0 / (1.0 + 20.0 * from_mouth.x * from_mouth.x
                           + 8.0 * from_mouth.y * from_mouth.y);
    vec2 velocity = vec2(-(sand_position.x - sand_geometry.x) * 0.09 * sink,
                         -(4.0 + 46.0 * sink)) / diameter;
    float jitter = 0.2 * sin(from_mouth.x * 3.1 + from_mouth.y * 5.2);
    float t = sand_clock + jitter;
    float a = fract(t);
    float b = fract(t + 0.5);
    float wa = 1.0 - abs(1.0 - 2.0 * a);
    float wb = 1.0 - wa;
    vec2 jump = vec2(0.125, 0.0625);
    float ga = grain(uv - velocity * a + floor(t) * jump);
    float gb = grain(uv - velocity * b + floor(t + 0.5) * jump + vec2(0.5));
    float detail = (ga * wa + gb * wb) * inversesqrt(wa * wa + wb * wb);
    float tone = clamp(lighting(uv) + detail, -1.0, 1.0);
    vec3 target = tone >= 0.0 ? sand_light : sand_dark;
    vec3 flowing = mix(sand_base, target, abs(tone));
    float coverage = 1.0;
    if (sand_tail.y > 0.0) {
        float u = 0.5 + (sand_position.x - sand_geometry.x) / (2.0 * sand_tail.z);
        // Mirrored grain noise is odd across the tube: no net added cap volume.
        float left = grain(vec2(u, sand_clock * 0.25 + 0.37));
        float right = grain(vec2(1.0 - u, sand_clock * 0.25 + 0.37));
        float top = sand_tail.x + sand_tail.y * clamp((left - right) * 0.7, -1.0, 1.0);
        coverage = 1.0 - smoothstep(top - 0.5, top + 0.5, sand_position.y);
    }
    if (sand_free.x > 0.0 && sand_position.y < sand_free.x) {
        // 出口以下那股沙**不再被玻璃约束** —— 它现在是"落下来的一把沙"。
        // 用户判词: 「沙柱看起来是个规整的矩形」 —— 两条边死直、平行、通体实心。
        // 两条边**各用各的噪声**(不是镜像: 玻璃没了, 没有"平均宽度不变"这条约束),
        // 于是宽度会随深度呼吸, 边缘毛掉; 内 62% 保持实心。
        float u = 0.5 + (sand_position.x - sand_geometry.x) / (2.0 * sand_free.z);
        float n = grain(vec2(u, sand_clock * 0.25 + 0.53));
        float n2 = grain(vec2(1.0 - u, sand_clock * 0.25 + 0.29));
        float rim = smoothstep(0.62, 1.0, abs(u) * 2.0);
        coverage *= 1.0 - rim * (0.25 + 0.75 * clamp(u < 0.5 ? n : n2, 0.0, 1.0))
                          * sand_free.y;
    }
    if (sand_free.w > 0.0 && sand_position.y < sand_free.w) {
        // 前沿(在途沙的最低点): 不许切成一刀平。1~5px 的零均值锯齿。
        float l2 = grain(vec2(sand_position.x * 0.35, sand_clock * 0.22 + 0.11));
        coverage *= smoothstep(0.0, 1.0,
                               clamp((sand_free.w - sand_position.y) / (1.0 + 4.0 * l2),
                                     0.0, 1.0));
    }
    gl_FragColor = frag_color * vec4(mix(original.rgb, flowing, sand_mix),
                                     original.a * coverage);
}
"""


class SandFlowContext(RenderContext):
    def __init__(self, geometry, vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER):
        super().__init__(use_parent_projection=True, use_parent_modelview=True)
        self.shader.vs = vertex_shader
        self.shader.fs = fragment_shader
        if not self.shader.success:
            raise RuntimeError("sand flow shader failed to compile")
        self["sand_geometry"] = tuple(map(float, geometry))
        self["sand_clock"] = 0.0
        self["sand_mix"] = 0.0
        self["sand_tail"] = (0.0, 0.0, 1.0)
        self["sand_free"] = (0.0, 0.0, 1.0, 0.0)
        self["sand_seam_band"] = float(os.environ.get("HG_SEAM_BAND", "0"))
        self["sand_neck_anchor"] = float(os.environ.get("HG_SEAM_ANCHOR", "0.021"))
        self._material_key = None
        self._clock_key = None
        self._tail_key = None
        self._free_key = None

    def update_flow(self, elapsed, speed_scale, material, palette,
                    tail=(0.0, 0.0, 1.0), free=(0.0, 0.0, 1.0, 0.0)):
        key = (material, palette)
        if key != self._material_key:
            self._material_key = key
            self["sand_base"], self["sand_dark"], self["sand_light"] = palette
            self["sand_lighting"] = (
                (material.flow_shade, material.flow_grad) if material else (0.0, 0.0))
        clock = (elapsed * speed_scale) % 16.0
        blend = min(1.0, max(0.0, elapsed / 0.3)) if material else 0.0
        blend = blend * blend * (3.0 - 2.0 * blend)
        if (clock, blend) != self._clock_key:
            self._clock_key = (clock, blend)
            self["sand_clock"] = float(clock)
            self["sand_mix"] = float(blend)
        if tail != self._tail_key:
            self._tail_key = tail
            self["sand_tail"] = tuple(map(float, tail))
        if free != self._free_key:
            self._free_key = free
            self["sand_free"] = tuple(map(float, free))


MOUND_VERTEX_SHADER = """
$HEADER$
attribute vec2 vFlow;
attribute float vCoverage;
varying vec2 sand_position;
varying vec2 surface_velocity;
varying float surface_coverage;
void main(void) {
    sand_position = vPosition;
    surface_velocity = vFlow;
    surface_coverage = vCoverage;
    tex_coord0 = vTexCoords0;
    frag_color = color * vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(vPosition, 0.0, 1.0);
}
"""

# Keep grain contrast and lighting identical to the existing flow material.
MOUND_FRAGMENT_SHADER = GRAIN_FRAGMENT_HEADER + """
varying vec2 surface_velocity;
varying float surface_coverage;
void main(void) {
    float diameter = max(2.0, sand_geometry.y * 2.0);
    vec2 uv = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                  (sand_position.y - sand_geometry.z) / sand_geometry.w);
    vec4 original = texture2D(texture0, uv);
    vec2 velocity = surface_velocity / vec2(diameter, sand_geometry.w);
    float t = sand_clock + 0.18 * sin(tex_coord0.x * 5.1);
    float a = fract(t);
    float b = fract(t + 0.5);
    float wa = 1.0 - abs(1.0 - 2.0 * a);
    float wb = 1.0 - wa;
    vec2 jump = vec2(0.125, 0.0625);
    float ga = grain(uv - velocity * a + floor(t) * jump);
    float gb = grain(uv - velocity * b + floor(t + 0.5) * jump + vec2(0.5));
    float detail = (ga * wa + gb * wb) * inversesqrt(wa * wa + wb * wb);
    float tone = clamp(lighting(uv) + detail, -1.0, 1.0);
    vec3 flowing = mix(sand_base, tone >= 0.0 ? sand_light : sand_dark, abs(tone));
    float depth_mix = 1.0 - smoothstep(0.45, 1.0, tex_coord0.y);
    float coverage = sand_mix * surface_coverage * depth_mix;
    gl_FragColor = frag_color * vec4(flowing, original.a * coverage);
}
"""


class MoundSurfaceFlowContext(SandFlowContext):
    """One fixed mesh inside the shared mound surface; no additional sand volume."""

    def __init__(self, geometry, max_nodes, texture):
        super().__init__(geometry, MOUND_VERTEX_SHADER, MOUND_FRAGMENT_SHADER)
        self._max_nodes = max_nodes
        self._vertices = [0.0] * (max_nodes * 2 * 7)
        self._indices = []
        for i in range(max_nodes - 1):
            k = i * 2
            self._indices.extend((k, k + 1, k + 2, k + 2, k + 1, k + 3))
        self._node_count = 0
        with self:
            Color(1, 1, 1, 1)
            self._mesh = Mesh(
                vertices=self._vertices, indices=[], texture=texture, mode="triangles",
                fmt=[(b"vPosition", 2, "float"), (b"vTexCoords0", 2, "float"),
                     (b"vFlow", 2, "float"), (b"vCoverage", 1, "float")])

    def update_surface(self, clock, speed_scale, material, palette, cols, diameter, strength):
        self.update_flow(clock, speed_scale, material, palette)
        self["sand_mix"] = float(strength)
        if strength <= 0.0 or not cols:
            if self._node_count:
                self._mesh.indices = []
                self._node_count = 0
            return
        self._mesh.texture = material.texture
        cx = self["sand_geometry"][0]
        reach = 2.5 * diameter
        vertices = self._vertices
        count = 0
        visible = False
        for i, (x, y, free, thick) in enumerate(cols):
            if abs(x - cx) > reach:
                continue
            before = cols[max(0, i - 1)]
            after = cols[min(len(cols) - 1, i + 1)]
            slope = (after[1] - before[1]) / max(1e-6, after[0] - before[0])
            norm = math.sqrt(1.0 + slope * slope)
            u = min(1.0, abs(x - cx) / reach)
            fade = u * u * (3.0 - 2.0 * u)
            depth = min(0.14 * diameter * (1.0 - fade) * norm, thick)
            coverage = (1.0 - fade) * min(1.0, depth) if free else 0.0
            visible = visible or coverage > 0.0
            side = -1.0 if x < cx else 1.0 if x > cx else 0.0
            speed = diameter * (1.5 + 2.5 * fade)
            vx = side * speed / norm
            vy = slope * vx
            k = count * 14
            vertices[k:k + 14] = (
                x, y, (x - cx) / diameter, 0.0, vx, vy, coverage,
                x, y - depth, (x - cx) / diameter, 1.0, vx, vy, coverage)
            count += 1
        if count > self._max_nodes:
            raise ValueError("mound flow mesh capacity exceeded")
        if not visible:
            self._mesh.indices = []
            self._node_count = 0
            return
        self._mesh.vertices = vertices
        if count != self._node_count:
            self._mesh.indices = self._indices[:max(0, count - 1) * 6]
            self._node_count = count
