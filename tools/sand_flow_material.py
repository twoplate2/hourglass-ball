"""GPU texture advection for the upper reservoir and neck, not the lower mound.

Two-phase flow follows Catlike Coding's Texture Distortion / Valve flow-map approach.
Only grain detail is advected; the palette and broad lighting stay in world space.
"""

from kivy.graphics import RenderContext


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

FRAGMENT_SHADER = """
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
void main(void) {
    vec4 original = texture2D(texture0, tex_coord0);
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
    gl_FragColor = frag_color * vec4(mix(original.rgb, flowing, sand_mix),
                                     original.a * coverage);
}
"""


class SandFlowContext(RenderContext):
    def __init__(self, geometry):
        super().__init__(use_parent_projection=True, use_parent_modelview=True)
        self.shader.vs = VERTEX_SHADER
        self.shader.fs = FRAGMENT_SHADER
        if not self.shader.success:
            raise RuntimeError("sand flow shader failed to compile")
        self["sand_geometry"] = tuple(map(float, geometry))
        self["sand_clock"] = 0.0
        self["sand_mix"] = 0.0
        self["sand_tail"] = (0.0, 0.0, 1.0)
        self._material_key = None
        self._clock_key = None
        self._tail_key = None

    def update_flow(self, elapsed, speed_scale, material, palette, tail=(0.0, 0.0, 1.0)):
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
