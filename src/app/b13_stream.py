"""Live B13 grains in reference-glass coordinates, drawn at native resolution."""
import math
import os
import sys
from functools import lru_cache
from typing import NamedTuple

import numpy as np


# The B13 reference frame has a 417.69 px inner diameter and a 15% opening.
REFERENCE_RADIUS = 208.845
REFERENCE_HALF = .15 * REFERENCE_RADIUS
RATE = 14500
BLOCK = 5 * RATE


class Grains(NamedTuple):
    serial: np.ndarray
    x: np.ndarray
    depth: np.ndarray
    radius: np.ndarray
    tone: np.ndarray


@lru_cache(maxsize=1)
def _birth_material():
    # Same sample order as the B11/B13 study. These RNGs never touch game state.
    rng = np.random.default_rng(319)
    offsets = rng.random(BLOCK)
    lanes = rng.triangular(-1., 0., 1., BLOCK)
    broad = rng.random(BLOCK) < .37
    lanes[broad] = 2. * rng.beta(1.65, 1.65, broad.sum()) - 1.
    jitter = rng.uniform(-1., 1., BLOCK)
    tone = rng.uniform(-1., 1., BLOCK)
    radius = rng.uniform(.51, .94, BLOCK)
    keys = np.arange(0., 5.4, .075)
    signals = np.random.default_rng(808).uniform(-1., 1., (2, len(keys)))
    birth = (np.arange(BLOCK) + offsets) / RATE
    drift = np.where(lanes < 0., np.interp(birth, keys, signals[0]),
                     np.interp(birth, keys, signals[1]))
    rank = np.random.default_rng(1209).random(BLOCK)
    return offsets, lanes, jitter, tone, radius, drift, rank


def sample_grains(time, end, half, gap, grain_scale=1., max_depth=None, attached=0.):
    """Project only births that can still be in the exposed part of this glass.

    Time and depth are measured in the B13 reference units. Identities continue
    across blocks and are independent of draw cadence, pause and skipped frames.
    """
    offsets, lanes, jitter, tones, radii, edge_drift, rank = _birth_material()
    width_ratio = half / REFERENCE_HALF
    rate = RATE * width_ratio / grain_scale**2
    limit = max(0., gap if max_depth is None else max_depth) + 2. * grain_scale
    lifetime = ((limit + 6.) / 148.) ** (1. / 2.3)
    first = max(0, math.floor((time - lifetime) * rate))
    last = math.ceil(min(time, end) * rate)
    serial = np.arange(first, max(first, last), dtype=np.int64)
    block, slot = np.divmod(serial, BLOCK)
    ids = (slot + block * 24317) % BLOCK
    birth = (serial + offsets[slot]) / rate
    lane = lanes[ids]
    remaining = end - birth
    # The last grains are released just inside the neck. Each outer lane stops
    # earlier than the centre; already falling grains keep their trajectory.
    supply = np.clip((remaining - .10 - .18 * np.abs(lane)**.9) / .18, 0., 1.)
    flowing = rank[ids] < supply**2
    age = np.maximum(0., time - birth)
    edge = np.clip((np.abs(lane) - .30) / .70, 0., 1.)
    depth = (28. + 5. * jitter[ids] - 9. * edge**2) * age + 148. * age**2.3
    inside = np.clip((.65 - remaining) / .18, 0., 1.)
    depth -= 6. * inside**2 * (3. - 2. * inside)
    if attached > 0. and time > end - .65:
        # While there is still packed sand at the lip, the outer lanes turn
        # inward across the first few rows instead of exposing a flat shelf.
        d = np.maximum(0., depth) / width_ratio
        width = half - (.46 * d + .055 * d**2) * width_ratio
        join = np.clip((width + 1. - np.abs(lane) * half) / 2., 0., 1.)
        join *= np.clip((15. - d) / 7., 0., 1.) * attached
        flowing |= rank[ids] < join
    keep = (birth <= time) & flowing & (depth <= limit)
    serial, ids, lane, depth = (v[keep] for v in (serial, ids, lane, depth))
    released = np.maximum(depth, 0.)
    narrow = 1. - .13 * (1. - np.exp(-released / 54.))
    settle = np.clip((released - 24.) / 145., 0., 1.)
    lateral = lane - .08 * np.sin(np.pi * lane) * settle
    x = lateral * max(0., half - .9 * grain_scale) * narrow
    outer = np.clip((np.abs(lane) - .48) / .46, 0., 1.) ** 1.3
    travel = 1. - np.exp(-np.maximum(0., depth - 12.) / 53.)
    contact = np.clip((gap - depth) / 21., 0., 1.)
    x += (edge_drift[ids] * width_ratio * 4.8 + jitter[ids] * 1.35) * outer * travel * contact
    core = .13 * np.maximum(0., 1. - (lane / .68)**2)
    return Grains(serial, x, depth, (radii[ids] + core) * grain_scale, tones[ids])


VERTEX_SHADER = """
$HEADER$
attribute vec3 vGrain;
uniform vec4 jet;  // centre x, outlet y, glass scale, reference half-width
uniform vec4 target;  // native-pixel bounds of the optical accumulation texture
varying vec2 local_offset;
varying vec2 jet_position;
varying vec3 grain;
void main(void) {
    local_offset = vTexCoords0;
    jet_position = vec2(vPosition.x-jet.x, jet.y-vPosition.y) / jet.z;
    grain = vec3(vGrain.xy, (vPosition.y-vGrain.z) / jet.z);
    gl_Position = vec4(2.0*(vPosition-target.xy)/target.zw-1.0, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
uniform vec4 jet;
varying vec2 local_offset;
varying vec2 jet_position;
varying vec3 grain;
void main(void) {
    float depth = jet_position.y;
    if (grain.z < 0.0 || (depth < 0.0 && abs(jet_position.x) > jet.w)) discard;
    float ink = clamp(grain.x - length(local_offset), 0.0, .86);
    float extinction = 1.65 * (2.0 - smoothstep(4.0, 43.0, depth));
    // R and G accumulate the positive and negative portions of the same dye.
    // One exposure per pixel keeps both sums in RGBA8 range even at the lip.
    float inner = max(0.0, 1.0 - abs(jet_position.x)/jet.w);
    float range = 5.0 + 72.0 * inner / (1.0 + .10 * max(0.0, depth));
    vec2 weights = .5 * ink / range * vec2(1.0+grain.y, 1.0-grain.y);
    gl_FragColor = vec4(weights, 0.0, 1.0-exp(-extinction*ink));
}
"""

COMPOSITE_VERTEX = """
$HEADER$
uniform vec4 jet;
varying vec2 jet_position;
void main(void) {
    jet_position = vec2(vPosition.x-jet.x, jet.y-vPosition.y) / jet.z;
    tex_coord0 = vTexCoords0;
    frag_color = vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(vPosition, 0.0, 1.0);
}
"""

COMPOSITE_FRAGMENT = """
$HEADER$
uniform vec4 jet;
uniform vec3 pigment;
uniform float gap;
varying vec2 jet_position;
void main(void) {
    vec4 sample_value = texture2D(texture0, tex_coord0);
    float sum = sample_value.r + sample_value.g;
    float tone = (sample_value.r-sample_value.g) / max(sum, 1.0/255.0);
    float inner = max(0.0, 1.0 - pow(abs(jet_position.x)/(.75*jet.w), 2.0));
    float body = .48 * inner * inner * clamp((jet_position.y-38.0)/150.0, 0.0, 1.0)
                              * clamp((gap-jet_position.y-11.0)/27.0, 0.0, 1.0);
    float alpha = sample_value.a;
    alpha += (1.0-alpha) * alpha * body;
    gl_FragColor = vec4(pigment + tone * vec3(18.0,20.0,17.0)/255.0, alpha * frag_color.a);
}
"""


class StreamMesh:
    """One native-resolution disc per visible grain, in reusable GLES2 batches."""
    chunk = 4096
    corners = np.array([[-1., -1.], [1., -1.], [1., 1.], [-1., 1.]], dtype=np.float32)

    def __init__(self, widget):
        from kivy.graphics import Callback, ClearBuffers, ClearColor, Fbo, Rectangle, RenderContext
        from kivy.graphics.opengl import (GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA,
                                          glBlendFuncSeparate)
        scale = widget._R_inner / REFERENCE_RADIUS
        outlet = 2. * widget._neck_y - widget._taper["y_bot"]
        extent = widget._taper["t_in"] + 8. * scale
        left = math.floor(widget._cx - extent)
        bottom = math.floor(widget._lower_sand_bot - 2. * scale)
        size = (math.ceil(widget._cx + extent) - left, math.ceil(outlet + 9. * scale) - bottom)
        self.surface_x = np.arange(left, left + size[0] + 1, dtype=float)
        dx = self.surface_x - widget._cx
        self.bowl = widget._lower_y_c - np.sqrt(np.maximum(0., widget._R_inner**2 - dx**2))
        self.context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        self.context.shader.vs = COMPOSITE_VERTEX
        self.context.shader.fs = COMPOSITE_FRAGMENT
        with self.context:
            self.field = Fbo(size=size)
            Rectangle(pos=(left,bottom), size=size, texture=self.field.texture)
        self.field.shader.vs = VERTEX_SHADER
        self.field.shader.fs = FRAGMENT_SHADER
        if not self.context.shader.success or not self.field.shader.success:
            raise RuntimeError("B13 grain shader could not compile")
        self.field["target"] = (float(left), float(bottom), float(size[0]), float(size[1]))
        geometry = (widget._cx, outlet, scale, widget._taper["t_in"]/scale)
        self.context["jet"] = self.field["jet"] = geometry
        with self.field.before:
            ClearColor(0., 0., 0., 0.)
            ClearBuffers()
            Callback(lambda _: glBlendFuncSeparate(GL_ONE, GL_ONE, GL_ONE, GL_ONE_MINUS_SRC_ALPHA))
        with self.field.after:
            Callback(lambda _: glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA,
                                                   GL_ONE, GL_ONE_MINUS_SRC_ALPHA))
        self.indices = (4 * np.arange(self.chunk, dtype=np.uint16)[:, None]
                        + np.array([0, 1, 2, 0, 2, 3], dtype=np.uint16)).ravel()
        self.batches = []
        self.add_batch()

    def add_batch(self):
        from kivy.graphics import Callback, Mesh
        from kivy.graphics.opengl import (GL_CURRENT_PROGRAM, glGetIntegerv,
                                          glGetAttribLocation, glEnableVertexAttribArray)

        def restore_attributes(_instruction):
            # A previous Kivy RenderContext can disable the shared GL attributes.
            program = glGetIntegerv(GL_CURRENT_PROGRAM)[0]
            for name in (b"vPosition", b"vTexCoords0", b"vGrain"):
                location = glGetAttribLocation(program, name)
                if location >= 0:
                    glEnableVertexAttribArray(location)

        vertices = np.zeros((self.chunk, 4, 7), dtype=np.float32)
        with self.field:
            Callback(restore_attributes)
            mesh = Mesh(vertices=[], indices=[], mode="triangles",
                        fmt=[(b"vPosition", 2, "float"), (b"vTexCoords0", 2, "float"),
                             (b"vGrain", 3, "float")])
        self.batches.append((mesh, vertices))

    def update(self, grains, cx, outlet, scale, surface_x, surface_y):
        count = len(grains.x)
        while count > self.chunk * len(self.batches):
            self.add_batch()
        for i, (mesh, buf) in enumerate(self.batches):
            first = self.chunk * i
            n = min(self.chunk, max(0, count - first))
            if len(mesh.indices) != n * 6:
                mesh.indices = self.indices[:n * 6]
            if n == 0:
                continue
            block = slice(first, first + n)
            v = buf[:n]
            support = grains.radius[block] + .48
            v[:, :, 2:4] = support[:, None, None] * self.corners
            v[:, :, 0] = (cx + scale * grains.x[block, None]) + scale * v[:, :, 2]
            v[:, :, 1] = (outlet - scale * grains.depth[block, None]) + scale * v[:, :, 3]
            v[:, :, 4] = support[:, None]
            v[:, :, 5] = grains.tone[block, None]
            v[:, :, 6] = np.interp(v[:, :, 0], surface_x, surface_y)
            mesh.vertices = v.ravel()

    def clear(self):
        for mesh, _ in self.batches:
            if len(mesh.indices):
                mesh.indices = []


def _playback_rate(widget, scale, distance):
    # Match the physical first impact even on a one-second timer or a rotated
    # glass. At the reference size and normal speed this reduces to one.
    def flight(d):
        return 2. * d / (60. + math.sqrt(60.**2 + 900. * d))
    reference = .25 + flight(distance / scale)
    actual = widget._neck_fill_time + flight(distance) / widget._particle_motion_scale
    return reference / actual


def install(widget_class):
    from short_grain_stream import mound_surface_at
    old_build, old_draw = widget_class._build_dynamic_canvas, widget_class._draw_stream

    def build(w):
        w._stream_batched = True
        try:
            old_build(w)
        finally:
            w._stream_batched = False
        w._b13_mesh = w._short_flow_context = None
        w._short_material_mode = None
        try:
            if os.environ.get("HG_FLOW_FORCE_FAIL"):
                raise RuntimeError("forced B13 fallback")
            stream = StreamMesh(w)
            # Packed sand and the original impact/splash layers occlude the jet.
            front = w._sand_flow_contexts[1] if w._sand_flow_contexts else w._splash_group
            w.canvas.insert(w.canvas.children.index(front), stream.context)
        except Exception as exc:
            w._reserve_stream_lines()
            w.flow_renderer = "line_pool"
            print("B13 unavailable, using Line: %s" % exc)
            return
        w._b13_mesh = stream
        w._short_flow_context = stream.context
        w._short_material_mode = "b13"
        w.flow_renderer = "b13_grains"

    def draw(w):
        stream = w._b13_mesh
        if stream is None:
            return old_draw(w)
        game = sys.modules[widget_class.__module__]
        cx, radius = w._cx, w._R_inner
        scale = radius / REFERENCE_RADIUS
        half = w._taper["t_in"] / scale
        outlet = 2. * w._neck_y - w._taper["y_bot"]
        gap = (outlet - w.get_mound_top_y()) / scale
        distance = outlet - w._lower_sand_bot
        speed = _playback_rate(w, scale, distance)
        time = (w.elapsed + w._stream_settle_age) * speed
        end = w.duration * speed
        grain_scale = (game.SAND_MATERIAL_COARSE / 1.8) ** .5
        opened = w._sand_active and w.elapsed > w._neck_fill_time
        # The event loop keeps redrawing idle and completed timers. Stop
        # allocating vertices once even the slowest last grain passed the floor.
        if not opened or time > end + ((distance / scale + 10.) / 148.) ** (1. / 2.3):
            stream.clear()
            w._short_flow_state = dict(active=False, particles=w.pn, rendered_particles=0,
                                       material_mode="b13", pitch=scale * grain_scale, position_style="b13")
            return
        surface_x = stream.surface_x
        surface_y = np.maximum(stream.bowl, mound_surface_at(surface_x, w._mound_carve._v, w._lower_sand_bot))
        deepest = (outlet - surface_y.min()) / scale
        attached = 0.
        if end - .65 < time < end:
            reserve = w._transfer_timing()[2]
            neck_top = w._neck_surface_y(min(1., (1. - w._sand_released) / reserve))
            attached = max(0., min(1., (neck_top - outlet) / (3. * scale)))
        grains = sample_grains(time, end, half, gap, grain_scale, deepest, attached)
        stream.context["gap"] = float(gap)
        stream.context["pigment"] = (w.sand_base[0]-1./255., w.sand_base[1]-1./255., w.sand_base[2])
        stream.update(grains, cx, outlet, scale, surface_x, surface_y)
        w._short_flow_state = dict(active=bool(len(grains.x)), particles=w.pn,
                                   rendered_particles=len(grains.x), material_mode="b13",
                                   pitch=scale * grain_scale, position_style="b13")

    widget_class._build_dynamic_canvas = build
    widget_class._draw_stream = draw
