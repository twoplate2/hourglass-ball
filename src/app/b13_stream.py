"""Live B13 grains in reference-glass coordinates, drawn at native resolution."""
import math
import logging
import os
import sys
from functools import lru_cache
from typing import NamedTuple

import numpy as np
from grain_style import (B13_REFERENCE_RADIUS as REFERENCE_RADIUS, b13_birth_recess,
                         b13_depth, b13_flight, b13_launch_speed, b13_neck_ratio, b13_tail_span)


# The B13 reference frame has a 417.69 px inner diameter and a 15% opening.
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
    launch = b13_launch_speed(lanes, jitter)
    inward = .08 * np.sin(np.pi * lanes)
    # Some moving outer grains continue along the lip before drifting inward.
    # The centre and outermost lane keep their original positions.
    lip_shift = .40 * np.sign(lanes) * np.maximum(0., np.abs(lanes) - .40) * (1. - np.abs(lanes))
    outer = np.clip((np.abs(lanes) - .48) / .46, 0., 1.) ** 1.3
    radius += .13 * np.maximum(0., 1. - (lanes / .68)**2)
    return offsets, lanes, jitter, tone, radius, drift, rank, launch, inward, outer, lip_shift


def sample_grains(time, end, half, gap, grain_scale=1., max_depth=None, attached=0.):
    """Project only births that can still be in the exposed part of this glass.

    Time and depth are measured in the B13 reference units. Identities continue
    across blocks and are independent of draw cadence, pause and skipped frames.
    """
    (offsets, lanes, jitter, tones, radii, edge_drift, rank, launch,
     inward, outer, lip_shift) = _birth_material()
    width_ratio = half / REFERENCE_HALF
    # The slower stream spends longer in each row. Match the B13 grain
    # spacing in the established fall as well as at the filled lip.
    rate = .67 * RATE * width_ratio / grain_scale**2
    limit = max(0., gap if max_depth is None else max_depth) + 2. * grain_scale
    lifetime = b13_flight(limit + 6., 14.)
    first = max(0, math.floor((time - lifetime) * rate))
    last = math.ceil(min(time, end) * rate)
    serial = np.arange(first, max(first, last), dtype=np.int64)
    block, slot = np.divmod(serial, BLOCK)
    ids = (slot + block * 24317) % BLOCK
    birth = (serial + offsets[slot]) / rate
    lane = lanes[ids]
    age = np.maximum(0., time - birth)
    depth = b13_depth(age, launch[ids])
    flowing = birth <= time
    tail_span = b13_tail_span(end)
    if tail_span > 0. and time > end - tail_span:
        # The final grains start inside the draining neck, with the outer
        # lanes giving way to the centre as the packed layer runs out.
        remaining = end - birth
        tail_time = remaining * (.65 / tail_span)
        supply = np.clip((tail_time - .10 - .18 * np.abs(lane)**.9) / .18, 0., 1.)
        depth -= b13_birth_recess(remaining, tail_span)
        remaining_lanes = rank[ids] < supply**2
        if attached > 0.:
            d = np.maximum(0., depth) / width_ratio
            width = half * attached - (.46 * d + .055 * d**2) * width_ratio
            join = np.clip((width + 1. - np.abs(lane) * half) / 2., 0., 1.)
            join *= np.clip((15. - d) / 7., 0., 1.) * attached
            remaining_lanes |= rank[ids] < join
        flowing &= remaining_lanes
    if time < lifetime + .16:
        opening = np.minimum(1., birth / .16)
        flowing &= rank[ids] < .18 + .82 * opening
    keep = flowing & (depth <= limit)
    serial, ids, lane, depth = (v[keep] for v in (serial, ids, lane, depth))
    released = np.maximum(depth, 0.)
    narrow = 1. - .13 * (1. - np.exp(-released / 54.))
    settle = np.clip((released - 24.) / 145., 0., 1.)
    lateral = lane - inward[ids] * settle
    near_lip = np.clip(1. - released / max(1., 1.10 * half), 0., 1.)
    lateral += lip_shift[ids] * near_lip**2 * (3. - 2. * near_lip)
    x = lateral * max(0., half - .9 * grain_scale) * narrow
    travel = 1. - np.exp(-np.maximum(0., depth - 12.) / 53.)
    contact = np.clip((gap - depth) / 21., 0., 1.)
    x += (edge_drift[ids] * width_ratio * 4.8 + jitter[ids] * 1.35) * outer[ids] * travel * contact
    return Grains(serial, x, depth, radii[ids] * grain_scale, tones[ids])


VERTEX_SHADER = """
#ifdef GL_ES
precision highp float;
#endif
// The same two attributes as an ordinary Kivy mesh, with extra components.
attribute vec4 vPosition;    // pixel position and local disc offset
attribute vec3 vTexCoords0;  // support radius, pigment and surface height
varying vec4 frag_color;
varying vec2 tex_coord0;
uniform vec4 jet;  // centre x, outlet y, glass scale, reference half-width
uniform vec4 target;  // native-pixel bounds of the optical accumulation texture
varying vec2 local_offset;
varying vec2 jet_position;
varying vec3 grain;
void main(void) {
    local_offset = vPosition.zw;
    jet_position = vec2(vPosition.x-jet.x, jet.y-vPosition.y) / jet.z;
    grain = vec3(vTexCoords0.xy, (vPosition.y-vTexCoords0.z) / jet.z);
    gl_Position = vec4(2.0*(vPosition.xy-target.xy)/target.zw-1.0, 0.0, 1.0);
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


class StreamArea:
    def __init__(self, widget):
        scale = widget._R_inner / REFERENCE_RADIUS
        outlet = 2. * widget._neck_y - widget._taper["y_bot"]
        extent = widget._taper["t_in"] + 8. * scale
        left = math.floor(widget._cx - extent)
        bottom = math.floor(widget._lower_sand_bot - 2. * scale)
        size = (math.ceil(widget._cx + extent) - left, math.ceil(outlet + 9. * scale) - bottom)
        self.surface_x = np.arange(left, left + size[0] + 1, dtype=float)
        dx = self.surface_x - widget._cx
        self.bowl = widget._lower_y_c - np.sqrt(np.maximum(0., widget._R_inner**2 - dx**2))
        self.origin = (left, bottom)
        self.size = size
        self.jet = (widget._cx, outlet, scale, widget._taper["t_in"] / scale)


class StreamMesh(StreamArea):
    """One native-resolution disc per visible grain, in reusable GLES2 batches."""
    chunk = 4096
    corners = np.array([[-1., -1.], [1., -1.], [1., 1.], [-1., 1.]], dtype=np.float32)

    def __init__(self, widget):
        from kivy.graphics import Callback, ClearBuffers, ClearColor, Fbo, Rectangle, RenderContext
        from kivy.graphics.opengl import (GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA,
                                          glBlendFuncSeparate)
        super().__init__(widget)
        self.context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        self.context.shader.vs = COMPOSITE_VERTEX
        self.context.shader.fs = COMPOSITE_FRAGMENT
        with self.context:
            self.field = Fbo(size=self.size)
            Rectangle(pos=self.origin, size=self.size, texture=self.field.texture)
        self.field.shader.vs = VERTEX_SHADER
        self.field.shader.fs = FRAGMENT_SHADER
        if not self.context.shader.success or not self.field.shader.success:
            raise RuntimeError("B13 grain shader could not compile")
        self.field["target"] = tuple(map(float, self.origin + self.size))
        self.context["jet"] = self.field["jet"] = self.jet
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

    def add_batch(self):
        from kivy.graphics import Mesh

        vertices = np.zeros((self.chunk, 4, 7), dtype=np.float32)
        with self.field:
            mesh = Mesh(vertices=vertices.ravel(), indices=[], mode="triangles",
                        fmt=[(b"vPosition", 4, "float"), (b"vTexCoords0", 3, "float")])
        self.batches.append((mesh, vertices))

    def update(self, grains, surface_y, pigment, gap):
        cx, outlet, scale, _ = self.jet
        self.context["gap"] = float(gap)
        self.context["pigment"] = pigment
        count = len(grains.x)
        while count > self.chunk * len(self.batches):
            self.add_batch()
        for i, (mesh, buf) in enumerate(self.batches):
            first = self.chunk * i
            n = min(self.chunk, max(0, count - first))
            if n == 0:
                if len(mesh.indices):
                    mesh.indices = []
                continue
            block = slice(first, first + n)
            v = buf[:n]
            support = grains.radius[block] + .48
            v[:, :, 2:4] = support[:, None, None] * self.corners
            v[:, :, 0] = (cx + scale * grains.x[block, None]) + scale * v[:, :, 2]
            v[:, :, 1] = (outlet - scale * grains.depth[block, None]) + scale * v[:, :, 3]
            v[:, :, 4] = support[:, None]
            v[:, :, 5] = grains.tone[block, None]
            v[:, :, 6] = np.interp(v[:, :, 0], self.surface_x, surface_y)
            # Publish data before its indices. Keep the backing size constant
            # across first draw, batch growth and shrinking tails.
            mesh.vertices = buf.ravel()
            if len(mesh.indices) != n * 6:
                mesh.indices = self.indices[:n * 6]

    def clear(self):
        for mesh, _ in self.batches:
            if len(mesh.indices):
                mesh.indices = []


class TextureStream(StreamArea):
    """Render the B13 optical image on the CPU and display one ordinary rectangle."""
    def __init__(self, widget):
        from kivy.graphics import Color, InstructionGroup, PopState, PushState, Rectangle
        from kivy.graphics.texture import Texture
        from b13_raster import GrainImage

        super().__init__(widget)
        self.image = GrainImage(self.origin, self.size, self.jet)
        self.source_count = 0
        self.texture = Texture.create(size=self.size, colorfmt="rgba")
        self.texture.add_reload_observer(self.upload)
        self.upload(self.texture)
        self.context = InstructionGroup()
        self.context.add(PushState("color"))
        self.context.add(Color(1., 1., 1., 1.))
        self.context.add(Rectangle(pos=self.origin, size=self.size, texture=self.texture))
        self.context.add(PopState("color"))

    def upload(self, texture):
        texture.blit_buffer(self.image.rgba.ravel(), colorfmt="rgba", bufferfmt="ubyte")

    def update(self, grains, surface_y, pigment, gap):
        if not len(grains.x):
            self.clear()
            return
        self.image.paint(grains, surface_y, pigment, gap)
        self.upload(self.texture)
        self.source_count = len(grains.x)
        self.context.flag_update()

    def clear(self):
        if self.source_count:
            self.image.rgba.fill(0)
            self.upload(self.texture)
            self.source_count = 0
            self.context.flag_update()


def stream_for_renderer(renderer):
    if isinstance(renderer, bytes):
        renderer = renderer.decode("ascii", "replace")
    name = "".join((renderer or "").lower().split()).replace("(tm)", "")
    return TextureStream if name == "adreno840" else StreamMesh


def install(widget_class):
    from short_grain_stream import mound_surface_at
    old_build, old_draw = widget_class._build_dynamic_canvas, widget_class._draw_stream

    def use_lines(w):
        if w._b13_mesh is not None:
            w.canvas.remove(w._b13_mesh.context)
        w._b13_mesh = w._short_flow_context = None
        w._short_material_mode = None
        w._short_flow_state = {}
        w._reserve_stream_lines()
        w.flow_renderer = "line_pool"

    def build(w):
        w._stream_batched = True
        try:
            old_build(w)
        finally:
            w._stream_batched = False
        w._b13_mesh = w._short_flow_context = None
        w._short_material_mode = None
        if getattr(w, "_b13_draw_failed", False):
            use_lines(w)
            return
        try:
            if os.environ.get("HG_FLOW_FORCE_FAIL"):
                raise RuntimeError("forced B13 fallback")
            from kivy.graphics.opengl import GL_RENDERER, glGetString
            stream = stream_for_renderer(glGetString(GL_RENDERER))(w)
            _birth_material()  # Prepare the grain identities before the first timed frame.
            # Packed sand and the original impact/splash layers occlude the jet.
            front = w._sand_flow_contexts[1] if w._sand_flow_contexts else w._splash_group
            w.canvas.insert(w.canvas.children.index(front), stream.context)
        except Exception as exc:
            use_lines(w)
            print("B13 unavailable, using Line: %s" % exc)
            return
        w._b13_mesh = stream
        w._short_flow_context = stream.context
        w._short_material_mode = "b13"
        w.flow_renderer = "b13_grains"

    def draw_grains(w, stream):
        game = sys.modules[widget_class.__module__]
        radius = w._R_inner
        scale = radius / REFERENCE_RADIUS
        half = w._taper["t_in"] / scale
        outlet = 2. * w._neck_y - w._taper["y_bot"]
        gap = (outlet - w.get_mound_top_y()) / scale
        distance = outlet - w._lower_sand_bot
        fill, cutoff, reserve = w._transfer_timing()
        time = w.elapsed + w._stream_settle_age - fill
        end = cutoff - fill
        grain_scale = (game.SAND_MATERIAL_COARSE / 1.8) ** .5
        opened = w._sand_active and time > 0.
        # The event loop keeps redrawing idle and completed timers. Stop
        # allocating vertices once even the slowest last grain passed the floor.
        if not opened or (time > end and time > end + b13_flight(distance / scale + 10., 14.)):
            stream.clear()
            w._short_flow_state = dict(active=False, particles=w.pn, rendered_particles=0,
                                       material_mode="b13", pitch=scale * grain_scale, position_style="b13")
            return
        surface_x = stream.surface_x
        surface_y = np.maximum(stream.bowl, mound_surface_at(surface_x, w._mound_carve._v, w._lower_sand_bot))
        deepest = (outlet - surface_y.min()) / scale
        attached = 0.
        if end - b13_tail_span(end) < time < end:
            released = w._released_fraction_at(time + fill)
            neck_top = w._neck_surface_y(min(1., (1. - released) / reserve))
            attached = b13_neck_ratio(neck_top - outlet, half * scale)
        grains = sample_grains(time, end, half, gap, grain_scale, deepest, attached)
        pigment = (w.sand_base[0]-1./255., w.sand_base[1]-1./255., w.sand_base[2])
        stream.update(grains, surface_y, pigment, gap)
        w._short_flow_state = dict(active=bool(len(grains.x)), particles=w.pn,
                                   rendered_particles=len(grains.x), material_mode="b13",
                                   pitch=scale * grain_scale, position_style="b13")

    def draw(w):
        if w._b13_mesh is not None:
            try:
                return draw_grains(w, w._b13_mesh)
            except Exception:
                logging.getLogger(__name__).exception("B13 draw failed at %.3f s", w.elapsed)
                w._b13_draw_failed = True
                use_lines(w)
        return old_draw(w)

    widget_class._build_dynamic_canvas = build
    widget_class._draw_stream = draw
