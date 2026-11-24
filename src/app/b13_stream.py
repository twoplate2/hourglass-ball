"""Live B13 grains in reference-glass coordinates, drawn at native resolution."""
import math
import logging
import os
import sys
from functools import lru_cache
from typing import NamedTuple

import numpy as np
from grain_style import B13_REFERENCE_RADIUS as REFERENCE_RADIUS, B13_TEXTURE_SPEED, hourglass_transit


# The B13 reference frame has a 417.69 px inner diameter and a 15% opening.
REFERENCE_HALF = .15 * REFERENCE_RADIUS
RATE = 14500
BLOCK = 5 * RATE
HEAD_ID_START = -(1 << 46)  # Disjoint from all ordinary material phase indices.


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
    # The same wall slip as the packed neck. Each grain keeps its lane variation.
    slip = 1. - .09 * lanes**2 - .012 * np.abs(jitter)
    inward = .08 * np.sin(np.pi * lanes)
    # Some moving outer grains continue along the lip before drifting inward.
    # The centre and outermost lane keep their original positions.
    # A packed cross-section carries grains up to both walls. Map the
    # centre-biased free-stream lanes to equal shares of the mouth, then fade
    # that offset downstream on the very same persistent grains.
    order = np.argsort(lanes)
    packed_lanes = np.empty_like(lanes)
    packed_lanes[order] = (np.arange(BLOCK) + .5) * (2. / BLOCK) - 1.
    lip_shift = packed_lanes - lanes
    # Neighbouring grains can start together, with individual grains leading
    # their neighbours. Keep those offsets attached to the material as it falls.
    release = np.roll(offsets, -541)
    patch = np.interp(packed_lanes, np.linspace(-1., 1., 9), offsets[:9])
    first_lag = (.70*release + .30*patch*np.minimum(1., 6.*release)) * (1. - .55*np.abs(packed_lanes))
    outer = np.clip((np.abs(lanes) - .48) / .46, 0., 1.) ** 1.3
    radius += .13 * np.maximum(0., 1. - (lanes / .68)**2)
    return offsets, lanes, jitter, tone, radius, drift, slip, inward, outer, lip_shift, first_lag


def _pigment_depth(age, duration):
    """Distance of visible grains in reference pixels, measured from the lip."""
    base = B13_TEXTURE_SPEED
    # The first ~12 px use the same wall-matched motion as the packed neck.
    lip_time, lip_acceleration = .15, 9.25
    lip_age = np.minimum(age, lip_time)
    lip_distance = lip_age * (base + .5 * lip_acceleration * lip_age)
    exit_speed = base + lip_acceleration * lip_time

    # Keep the one-second setting gentle. Longer settings share the same
    # visible gravity and limiting speed regardless of their aperture.
    u = min(1., max(0., (duration - 1.) / 4.))
    blend = u*u*(3. - 2.*u)
    acceleration = lip_acceleration + (36. - lip_acceleration) * blend
    terminal = base * (1.36 + .79 * blend)
    ramp = 2.4 - lip_time
    knee_speed = exit_speed + acceleration * ramp
    relax = (terminal - knee_speed) / acceleration
    free_age = age - lip_age
    early = np.minimum(free_age, ramp)
    later = free_age - early
    return (lip_distance + exit_speed * early + .5 * acceleration * early**2 + terminal * later
            + (terminal-knee_speed) * relax * np.expm1(-later/relax))


@lru_cache(maxsize=8)
def _opening_pattern(seed):
    rng = np.random.default_rng(seed)
    phase_a, phase_b = rng.uniform(0., 2.*np.pi, 2)
    return int(rng.integers(BLOCK)), phase_a, phase_b


def sample_grains(time, end, half, gap, grain_scale=1., max_depth=None, stride=1, inlet_lift=0.,
                  *, opening_seed=0):
    """Carry the first material with the nose; advect packed pigment behind it."""
    (offsets, lanes, jitter, tones, radii, edge_drift, slip,
     inward, outer, lip_shift, first_lag) = _birth_material()
    width_ratio = half / REFERENCE_HALF
    settling = max(0., time - end)
    time = min(time, end)
    transit = hourglass_transit(end, half, time)
    # At a shared speed a wider mouth emits more visible grains per second.
    rate = 100. * B13_TEXTURE_SPEED * width_ratio / grain_scale**2
    limit = max(0., gap if max_depth is None else max_depth) + inlet_lift + 2. * grain_scale
    carrier_tip = transit.depth_at(time * transit.lane_factor(0., 1.))
    limit = min(limit, carrier_tip + grain_scale)
    lifetime = (limit + 3.) / (B13_TEXTURE_SPEED * (1. - .09 - .012))
    # These are material coordinates of the established texture. Each also
    # has a moving carrier constraint, so it cannot enter ahead of the first batch.
    first = math.floor((time - lifetime) * rate)
    last = math.ceil(time * rate)
    first += (-first) % stride
    serial = np.arange(first, max(first, last), stride, dtype=np.int64)
    block, slot = np.divmod(serial, BLOCK)
    ids = (slot + block * 24317) % BLOCK
    birth = (serial + offsets[slot]) / rate
    lane = lanes[ids]
    lane_speed = transit.lane_factor(lane, jitter[ids])
    age = np.maximum(0., time - birth)
    depth = _pigment_depth(age * slip[ids], end)
    # A short packet crosses the lip in longitudinal order. A lag is a
    # distance behind the first grain on its lane, not a new random draw per frame.
    head_length = 16. + .85 * half
    leading_depth = transit.depth_at(time * transit.lane_factor(1., -1.))
    # Separate the first grains gradually after they leave the packed lip.
    # The first packet and the following pigment share the same handover.
    opened = min(1., max(0., (leading_depth-12.)/(2.5*head_length+half)))
    opened *= opened * (3. - 2.*opened)
    # Local slip below already opens gaps at the nose. Keep the rest of the
    # packet closer together so the falling column carries its visible volume.
    stretch = 1. + .08*opened
    stagger = .80*half*opened
    moving_length = stretch*head_length
    width = .23*moving_length
    pigment_count = len(serial)
    if leading_depth <= limit + 4.*head_length + 3.*half:
        # Match each patch of interior pigment onto its advancing carrier
        # continuously where the packed first batch opens into the free jet.
        empty_flight = hourglass_transit(end, half).age_at(2.*REFERENCE_RADIUS)
        release_span = min(head_length, 1.2*half) * min(1., empty_flight/.55)
        join = moving_length * (.48 + .52 * offsets[ids]) + stagger * first_lag[ids]
        join += .38*release_span*opened
        edge = transit.depth_at(time * lane_speed)
        remaining = edge - join - depth
        blend = np.clip(.5 + .5 * remaining / width, 0., 1.)
        depth -= width * (1. - blend)**2 + np.maximum(-width - remaining, 0.)
        visible = offsets[(ids+127) % BLOCK] < blend

        density = rate / B13_TEXTURE_SPEED
        packet_size = math.ceil(head_length*density)
        front_ids = np.arange((-HEAD_ID_START) % stride, packet_size, stride, dtype=np.int64)
        first_slot, phase_a, phase_b = _opening_pattern(opening_seed)
        # Change the release order of the same material. A new round has the
        # same number, sizes and lane distribution of available grains.
        keys = ((front_ids + first_slot) % max(1, packet_size) + 14717) % BLOCK
        lag = (front_ids + offsets[keys]) / density
        # Nearby leading grains share some slip but can overtake each other.
        # Travel sets their phase continuously, including on dropped frames.
        phase = max(0., leading_depth - 12.) / (12. + .34*half)
        across = lanes[keys] + lip_shift[keys]
        scatter = jitter[keys]
        shift = (.16*np.sin(7.4*across + 1.30*scatter + phase + phase_a)
                 + .10*np.sin(12.3*across - 1.10*scatter - .83*phase + phase_b)
                 + .12*across*np.sin(.58*phase + phase_b))
        # Give the first layers room to separate in free flight. Material
        # spacing is independent of lane speed; nearby grains share some slip.
        spacing = offsets[(keys+919) % BLOCK]
        order = np.clip(lag/head_length, 0., 1.)
        front_weight = (1.-order)**2
        radial = abs(across)
        release_lag = first_lag[keys] * (1. - .30*(1.-order)*radial)
        release_lag += front_weight*((1.40 - 1.32*radial)*spacing + .11*radial**2)
        delay = release_lag + front_weight*shift*np.where(shift > 0., 1., np.minimum(1.1, 1.8*release_lag))
        interval = .60*min(head_length, 1.2*half)*(np.sqrt(order)-order)
        separation = (1. - .80*radial)*opened*interval
        front_lane = lanes[keys]
        front_speed = transit.lane_factor(front_lane, jitter[keys])
        # The earliest grains already have open space ahead of them. Let their
        # neighbours fall progressively behind, without delaying first contact.
        neighbours = np.minimum(1., lag/(grain_scale*(1.5 - .4*spacing)))
        front = transit.depth_at(time*front_speed) - stretch*lag - neighbours*(separation + stagger*delay)
        # Each grain keeps its place in the initial release. After it clears
        # the lip the leading neighbours pull apart before those behind them.
        material_lag = lag + neighbours*(.80*half*release_lag + interval)
        face_weight = np.maximum(0., 1.-(material_lag/(3.8*half))**2)**3
        spread = .35 + .48*radial + .20*offsets[(keys+143) % BLOCK]
        extra_lag = material_lag*face_weight*spread + .82*release_span*order
        # A monotone function of this grain's own travel keeps it falling
        # forward even when the leading surface is much faster than the texture.
        free = np.maximum(front-12., 0.)
        opened_grain = free/(free + .30*half + .35*extra_lag)
        front -= extra_lag*opened_grain**2
        fraction = np.clip((head_length - lag) / (.52*head_length), 0., 1.)
        active = ((front >= 0.) & (front <= limit) & (offsets[(keys+193) % BLOCK] < fraction))
        pigment = serial[visible]
        pigment_count = len(pigment)
        serial = np.concatenate((pigment, HEAD_ID_START + front_ids[active]))
        ids = np.concatenate((ids[visible], keys[active]))
        depth = np.concatenate((depth[visible], front[active]))
        # Only grains at the exposed face can fan out. Those behind them merge
        # back into the jet rather than piling into a wider head as they overlap.
        behind = (carrier_tip - front) / max(2.*grain_scale, .95*half)
        exposed = np.exp(-behind**2)
        splay = .28*np.sin(np.pi*front_lane) + .18*front_lane
        front_lane = front_lane + opened*(1.-order**2)*(exposed*splay - .075*(1.-exposed)*front_lane)
        lane = np.concatenate((lane[visible], front_lane[active]))
        lane_speed = np.concatenate((lane_speed[visible], front_speed[active]))
    flight_age = transit.age_at(depth)
    lip_time = time - flight_age / lane_speed
    released = lip_time >= -1.e-9
    # The first packet already crossed its forward trajectory's lip test.
    # Inverting an interpolated clock here can hide its very first grain for a frame.
    released[pigment_count:] = True
    keep = (depth > inlet_lift) & released & (lip_time < end) & (depth <= limit)
    serial, ids, lane, depth = (v[keep] for v in (serial, ids, lane, depth))
    if settling > 0. and depth.size:
        # Continue the exact grains that were in flight at cutoff. Their depth
        # at that instant fixes their place on the existing bulk trajectory.
        advance = transit.depth_at(flight_age[keep] + settling*lane_speed[keep]) - depth
        # The receding surface crosses the lip at the packed volume rate Q/A.
        # Ease onto free travel over the first fraction of a mouth radius.
        packed_travel = settling * (4.*REFERENCE_RADIUS**3 / (3.*end*half**2))
        advance -= np.maximum(0., advance-packed_travel) * np.exp(-advance / (.38*half))
        distance = advance / (.34*half + 2.*grain_scale)
        spread = -.50 * half * np.expm1(-distance**2)
        # The shoulders of the rear surface separate ahead of its centre.
        # Use each grain's fixed lane at the packed lip, also on coarse sand.
        radial = np.abs(lane + lip_shift[ids])
        scatter = .47*jitter[ids] + .35*(2.*offsets[ids]-1.) + .90*(radial*(2.-radial) - .10)
        # Only the last packed layer has this extra lag. Let its slow grains
        # catch the established flow as they travel beyond the lip.
        lag_limit = .50 * np.exp(-(.60 * distance)**4)
        depth += advance + spread * np.maximum(scatter, -lag_limit)
        visible = depth <= limit
        serial, ids, lane, depth = (v[visible] for v in (serial, ids, lane, depth))
    depth -= inlet_lift
    released = np.maximum(depth, 0.)
    narrow = 1. - .13 * (1. - np.exp(-released / 54.))
    settle = np.clip((released - 24.) / 145., 0., 1.)
    lateral = lane - inward[ids] * settle
    near_lip = np.clip(1. - released / max(12., 3.0 * half), 0., 1.)
    packed = near_lip**2 * (3. - 2.*near_lip)
    after_contact = max(0., (leading_depth-gap)/(3.4*half))
    if after_contact < 1.:
        # The sand feeding the free face still occupies the mouth's lanes.
        # Blend into that existing distribution as one goes up the column.
        behind = np.clip((carrier_tip-depth-inlet_lift)/(2.*half), 0., 1.)
        behind *= behind*(3.-2.*behind)
        recovery = (1.-after_contact)**2*(1.+2.*after_contact)
        packed += .48*(1.-packed)*behind*recovery
    lateral += lip_shift[ids] * packed
    x = lateral * max(0., half - .40 * grain_scale) * narrow
    travel = 1. - np.exp(-np.maximum(0., depth - 12.) / 53.)
    contact = np.clip((gap - depth) / 21., 0., 1.)
    # Let individual edge grains fan out without a whole band swaying together.
    x += (edge_drift[ids] * width_ratio * 1.6 + jitter[ids] * 2.6) * outer[ids] * travel * contact
    size = radii[ids] * grain_scale
    return Grains(serial, x, depth, size, tones[ids])


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


class LineStream(StreamArea):
    """Use the existing emergency line pools with short optical strokes.

    One stroke covers several grain footprints. Keep a fixed subset of optical
    identities across frames so the mass carriers cannot draw long vertical bars.
    """
    stride = 3

    def __init__(self, widget):
        super().__init__(widget)
        self.pools = widget._stream_pools
        self.counts = widget._stream_counts

    def clear(self):
        for key, count in self.counts.items():
            for line in self.pools[key][2][:count]:
                line.points = []
            self.counts[key] = 0

    def update(self, grains, surface_y, pigment, gap):
        from kivy.graphics import Line
        cx, lip, scale, _ = self.jet
        x = cx + scale * grains.x
        y = lip - scale * grains.depth
        floor = np.interp(x, self.surface_x, surface_y)
        width = np.maximum(1., scale * grains.radius)
        length = self.stride * np.pi * (scale * grains.radius)**2 / (2. * width)
        top = np.minimum(lip, y + .5 * length)
        bottom = np.maximum(floor, y - .5 * length)
        shades = np.rint(5. + 2. * grains.tone).astype(int)
        for shade in range(3, 8):
            key = (shade, 1)
            group, _, pool = self.pools[key]
            ids = np.flatnonzero((shades == shade) & (top > bottom))
            for i, j in enumerate(ids):
                coords = (x[j], bottom[j], x[j], top[j])
                if i == len(pool):
                    line = Line(points=coords, width=width[j], cap="none")
                    group.add(line)
                    pool.append(line)
                else:
                    pool[i].width = width[j]
                    pool[i].points = coords
            for line in pool[len(ids):self.counts[key]]:
                line.points = []
            self.counts[key] = len(ids)


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
        w._b13_lines = None
        w._b13_lines_failed = False
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
        time = w.elapsed + w._stream_settle_age
        end = w.duration
        grain_scale = (game.SAND_MATERIAL_COARSE / 1.8) ** .5
        opened = w._sand_active and time > 0.
        if not opened or (time >= end and not w.pn):
            stream.clear()
            w._short_flow_state = dict(active=False, particles=w.pn, rendered_particles=0,
                                       material_mode="b13", pitch=scale * grain_scale, position_style="b13")
            return
        surface_x = stream.surface_x
        surface_y = np.maximum(stream.bowl, mound_surface_at(surface_x, w._mound_carve._v, w._lower_sand_bot))
        deepest = (outlet - surface_y.min()) / scale
        _, opening, lift = w._sand_neck.opening(time)
        grains = sample_grains(time, end, half, gap, grain_scale, deepest,
                               getattr(stream, "stride", 1), lift/scale, opening_seed=w._opening_seed)
        if opening and opening[-1][0] < w._taper["t_in"]:
            inside = np.abs(grains.x) <= opening[-1][0]/scale
            grains = Grains(*(values[inside] for values in grains))
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

    def draw_lines(w):
        if w._b13_lines is None:
            w._b13_lines = LineStream(w)
        return draw_grains(w, w._b13_lines)

    widget_class._build_dynamic_canvas = build
    widget_class._draw_stream = draw
    widget_class._draw_b13_lines = draw_lines
