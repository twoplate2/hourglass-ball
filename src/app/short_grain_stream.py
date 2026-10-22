"""Dense short grains in a live stream envelope, independent of physics state.

The field replaces the old long-stroke coverage, not just its RGB. All timers,
emitters and mass accounting remain owned by HourglassWidget.
"""
import os
import math


def grain_pitch(radius, coarse):
    return max(0.6, radius / 150.0 * (0.7 + 0.35 * coarse))


def flow_phase(elapsed, rate=12.0):
    # Match the birth-id hash period. Only ids wrap; physical ages never do.
    return (max(0.0, elapsed) * rate) % 4096.0


def motion_parameters(half_width, pitch, max_depth, motion_scale=1.0):
    """Pixel velocities follow canvas scale, independently of grain coarseness."""
    width = max(2.0, 2.0 * half_width)
    speed = min(1.4, max(1.0, motion_scale) ** 0.25)
    v0, gravity = 3.0 * width * speed, 2.0 * width * speed * speed
    # Use the fixed empty-bulb depth, not the changing pile height. Include the
    # fastest lane, so coarse grains do not leave gaps near the bottom.
    rate = math.sqrt((v0 * 1.1) ** 2 + 2.0 * gravity * 1.1 * max(0.0, max_depth)) / max(.6, pitch)
    return v0, gravity, rate


def stream_limits(ys, outlet, surface, pitch, touching):
    if len(ys) == 0:
        return None
    top = min(outlet, max(ys) + 1.5 * pitch)
    bottom = max(surface - 0.6 * pitch, min(ys) - 0.8 * pitch)
    if touching:
        bottom = surface - 0.6 * pitch
    return (top, bottom) if top > bottom else None


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

FRAGMENT_BODY = """
uniform vec4 short_geometry; // center, outlet, half-width, pitch
uniform vec4 short_limits;   // top, front, sparse-tail packing, phase
uniform float short_contrast;
uniform float short_contact;
uniform vec4 short_motion; // initial speed, acceleration, birth rate, shape phase
uniform float short_shape_gain;
float cell_hash(vec2 p) {
    return clump_hash(mod(p, 4096.0));
}
float flight_age(float depth, float v0, float g) {
    return 2.0 * max(0.0, depth) / (sqrt(v0*v0 + 2.0*g*max(0.0, depth)) + v0);
}
float edge_noise(float y, float side) {
    float cell = floor(y), f = fract(y);
    f = f*f*(3.0-2.0*f);
    return mix(clump_hash(vec2(side, mod(cell, 256.0))),
               clump_hash(vec2(side, mod(cell+1.0, 256.0))), f);
}
void main(void) {
    float pitch = short_geometry.w;
    float depth = short_geometry.y - sand_position.y;
    float qx = (sand_position.x - short_geometry.x) / pitch;
    float column = floor(qx);
    float nearest = 9.0;
    float second = 9.0;
    float shade = 0.5;
    float tone_sum = 0.0;
    float tone_weight = 0.0;
    vec2 chosen = vec2(column, 0.0);
    // Move grain centres ballistically, but evaluate their shape in pixels.
    // Mapping the grain SHAPE through inverse flight time would stretch it.
    for (int i = -1; i <= 1; i++) {
        float lane = column + float(i);
        float variation = 0.90 + 0.20 * cell_hash(vec2(lane, 117.0));
        float v0 = short_motion.x * variation;
        float g = short_motion.y * variation;
        float age0 = flight_age(depth, v0, g);
        float row = floor(short_limits.w - age0 * short_motion.z);
        // Dense neck grains overlap; nine birth rows prevent nearest-centre
        // switches caused by undersampling that packed entrance.
        for (int j = -4; j <= 4; j++) {
            vec2 id = vec2(lane, row + float(j));
            float born = id.y + 0.5 + 0.64 * (cell_hash(id + vec2(71.0, 19.0)) - 0.5);
            float age = (short_limits.w - born) / short_motion.z;
            float center_depth = v0*age + 0.5*g*age*age;
            float center_x = lane + 0.5 + 0.64 * (cell_hash(id) - 0.5);
            vec2 d = vec2(qx - center_x, (depth - center_depth) / pitch);
            d.y *= 0.90;
            float dist = dot(d, d);
            // Subpixel grains must not flash when their nearest centre changes.
            // Blend the moving grains' colours within one compact grain radius;
            // geometry and pore coverage still use the nearest short grain.
            float weight = max(0.0, 1.0 - dist / 0.90);
            weight = weight * weight;
            tone_sum += weight * cell_hash(id + vec2(17.0, 103.0));
            tone_weight += weight;
            if (dist < nearest) {
                second = nearest;
                nearest = dist;
                shade = cell_hash(id + vec2(17.0, 103.0));
                chosen = id;
            } else {
                second = min(second, dist);
            }
        }
    }
    float join = smoothstep(0.0, 3.5 * short_geometry.z, max(depth, 0.0));
    float travel = flight_age(depth, short_motion.x, short_motion.y);
    float shape_y = travel * 3.0 - short_motion.w;
    float side = sand_position.x < short_geometry.x ? 11.7 : 37.3;
    float shape = 2.0 * edge_noise(shape_y, side) - 1.0;
    float edge = short_geometry.z * (1.0 - 0.04*join
                       + 0.18 * join * short_shape_gain * shape);
    float distance = abs(sand_position.x - short_geometry.x);
    // A few compact outlying grains break the hard boundary without a haze.
    float halo = pitch * join * 0.9;
    if (distance > edge - pitch * join * (0.12 + 0.45 * shade)) {
        if (distance > edge + halo || shade < 0.88 || nearest > 0.20) discard;
    }
    // Small isolated inter-grain pores; never reuse the old transparent lanes.
    if (nearest > mix(1.1, 0.52, join)) discard;
    if (cell_hash(chosen + vec2(29.0, 41.0)) > short_limits.z) discard;
    if (short_contact < 0.5 && sand_position.y < short_limits.y + pitch * shade) discard;
    if (short_limits.x < short_geometry.y - 3.0 * pitch
        && sand_position.y > short_limits.x - pitch * shade) discard;
    float diameter = max(2.0, 2.0 * sand_geometry.y);
    vec2 uv = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                  (sand_position.y - sand_geometry.z) / diameter);
    float tone = tone_sum / max(0.0001, tone_weight);
    float detail = (tone - 0.5) * 0.80;
    float strength = max(0.0, short_contrast / 0.35);
    vec3 rgb = grain_tone_color(detail * min(1.5, strength), lighting(uv));
    // RGB continuity at the mouth, not an alpha gradient or rectangular mask.
    vec2 mouth = vec2((sand_position.x - sand_geometry.x) / diameter,
                     max(0.0, sand_position.y - sand_geometry.w) / diameter);
    float sink = 1.0 / (1.0 + 20.0 * mouth.x * mouth.x + 8.0 * mouth.y * mouth.y);
    vec2 velocity = vec2(-(sand_position.x - sand_geometry.x) * 0.09 * sink,
                         -(4.0 + 46.0 * sink)) / diameter;
    float t = sand_clock + 0.2 * sin(mouth.x * 3.1 + mouth.y * 5.2);
    float a = fract(t), b = fract(t + 0.5);
    float wa = 1.0 - abs(1.0 - 2.0 * a), wb = 1.0 - wa;
    vec2 jump = vec2(0.125, 0.0625 * sand_jump_y);
    float ga = grain(uv - velocity * a + floor(t) * jump);
    float gb = grain(uv - velocity * b + floor(t + 0.5) * jump + vec2(0.5));
    vec3 neck = grain_tone_color((ga * wa + gb * wb)
                     * inversesqrt(wa * wa + wb * wb), lighting(uv));
    rgb = mix(sand_base, mix(neck, rgb, join), sand_mix);
    gl_FragColor = vec4(rgb, frag_color.a);
}
"""


def install(widget_class):
    from kivy.graphics import Color, Mesh
    from sand_flow_material import GRAIN_FRAGMENT_HEADER, SandFlowContext

    old_build = widget_class._build_dynamic_canvas
    old_draw = widget_class._draw_stream

    def build(self):
        self._stream_batched = True
        try:
            old_build(self)
        finally:
            self._stream_batched = False
        self._short_flow_context = None
        self._short_flow_mesh = None
        try:
            if os.environ.get("HG_FLOW_FORCE_FAIL"):
                raise RuntimeError("forced short-grain fallback")
            context = SandFlowContext((self._cx, self._R_inner, self._upper_sand_bot,
                                       self._taper["in_pts"][0][1]),
                                      VERTEX_SHADER, GRAIN_FRAGMENT_HEADER + FRAGMENT_BODY, tag=5)
            with context:
                Color(1, 1, 1, 1)
                mesh = Mesh(mode="triangles", vertices=[], indices=[],
                            texture=self._sand_material.texture if self._sand_material else None)
            groups = [row[0] for row in self._stream_pools.values()]
            position = self.canvas.children.index(groups[0])
            for group in groups:
                self.canvas.remove(group)
            self.canvas.insert(position, context)
            self._short_flow_context, self._short_flow_mesh = context, mesh
            self.flow_renderer = "short_grain_field"
        except Exception as exc:
            self._reserve_stream_lines()
            self.flow_renderer = "line_pool"
            print("short grains unavailable; keeping Line fallback: %s" % exc)

    def draw(self):
        context, mesh = self._short_flow_context, self._short_flow_mesh
        if context is None:
            return old_draw(self)
        n = self.pn
        if not n:
            mesh.vertices, mesh.indices = [], []
            self._short_flow_state = {"active": False, "particles": 0}
            return
        import sys
        module = sys.modules[widget_class.__module__]
        pitch = grain_pitch(self._R_inner, module.SAND_MATERIAL_COARSE)
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        surface = self.get_mound_top_y()
        touching = (self._last_impact_clock is not None
                    and self._mound_flow_clock - self._last_impact_clock <= 0.05)
        limits = stream_limits(self.py[:n], outlet, surface, pitch, touching)
        if limits is None:
            mesh.vertices, mesh.indices = [], []
            self._short_flow_state = {"active": False, "particles": n}
            return
        top, front = limits
        half = self._taper["t_in"]
        # Exact surface columns, including a small overlap; no flat bottom cap.
        vertices, indices = [], []
        for i in range(17):
            x = self._cx + 1.24 * half * (i / 8.0 - 1.0)
            ground = self._mound_top_at(x)
            bottom = ground - 0.6 * pitch if touching else max(ground, front)
            bottom = min(top, bottom)
            vertices.extend((x, bottom, 0, 0, x, top, 0, 1))
            if i:
                k = 2 * (i - 1)
                indices.extend((k, k + 2, k + 1, k + 1, k + 2, k + 3))
        mesh.vertices, mesh.indices = vertices, indices
        material = self._sand_material
        mesh.texture = material.texture if material else None
        context.update_flow(self.elapsed, self._particle_motion_scale, material,
                            (self.sand_base, self.sand_dark, self.sand_light))
        context["short_geometry"] = (self._cx, outlet, half, pitch)
        context["short_contrast"] = module.SAND_MATERIAL_GRAIN
        context["short_contact"] = float(touching)
        # The timer stops at duration, but this pause-aware clock continues until
        # in-flight grains settle. Do not freeze their texture at timer zero.
        v0, gravity, rate = motion_parameters(half, pitch,
            outlet - (self._lower_y_c - self._R_inner), self._particle_motion_scale)
        phase = flow_phase(self._mound_flow_clock, rate)
        context["short_motion"] = (v0, gravity, rate, (self._mound_flow_clock * 3.0) % 256.0)
        context["short_shape_gain"] = min(1.0, max(0.0, (outlet-surface) / (4.0*half)))
        context["short_limits"] = (top, front, min(1.0, n / 24.0), phase)
        self._short_flow_state = {"active": True, "particles": n, "top": top,
                                  "front": front, "pitch": pitch, "touching": touching,
                                  "phase": phase, "v0": v0, "gravity": gravity,
                                  "birth_rate": rate}

    widget_class._build_dynamic_canvas = build
    widget_class._draw_stream = draw
