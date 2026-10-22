"""Persistent short sprites following the existing real particle trajectories.

HourglassWidget owns births, motion, contact and mass accounting. Rendering adds
no animated envelope or time-dependent mask that could delete airborne grains.
The pure field helpers below remain for historical 3.6 evidence tools.
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


def particle_vertices(xs, ys, phases, sizes, pitch):
    """One persistent short sprite per real particle; never changes its centre."""
    import numpy as np
    xs, ys, phases, sizes = [np.asarray(v) for v in (xs, ys, phases, sizes)]
    n = len(xs)
    out = np.empty((n, 4, 5), dtype=np.float32)
    corners = np.asarray(((-1,-1),(1,-1),(1,1),(-1,1)), dtype=np.float32)
    radius = 1.6 * max(.8, pitch) * (1.0 + .15 * (sizes > 1))
    out[:,:,0] = xs[:,None] + radius[:,None] * corners[None,:,0]
    out[:,:,1] = ys[:,None] + radius[:,None] * corners[None,:,1] * 1.08
    out[:,:,2:4] = corners[None,:,:]
    out[:,:,4] = ((phases / math.tau) % 1.0 - .5)[:,None] * .60
    return out


def mound_surface_at(xs, carve_vertices, floor):
    """Sample the same polyline that cuts the visible mound, including ripples."""
    import numpy as np
    xs = np.asarray(xs)
    quads = np.asarray(carve_vertices, dtype=np.float32).reshape(-1, 4, 4)
    valid = quads[quads[:, 1, 0] > quads[:, 0, 0]]
    if not len(valid):
        return np.full(xs.shape, floor, dtype=np.float32)
    surface = valid[:, :2, :2].reshape(-1, 2)
    return np.interp(xs, surface[:, 0], surface[:, 1]).astype(np.float32)


VERTEX_SHADER = """
$HEADER$
attribute float vTone;
attribute float vSurface;
varying vec2 sand_position;
varying vec2 grain_local;
varying float grain_tone;
varying float grain_surface;
void main(void) {
    sand_position = vPosition;
    grain_local = vTexCoords0;
    grain_tone = vTone;
    grain_surface = vSurface;
    frag_color = color * vec4(1.0,1.0,1.0,opacity);
    gl_Position = projection_mat * modelview_mat * vec4(vPosition,0.0,1.0);
}
"""

FRAGMENT_BODY = """
uniform vec4 short_geometry;
uniform float short_contrast;
varying vec2 grain_local;
varying float grain_tone;
varying float grain_surface;
void main(void) {
    // Clip only the aperture, this grain's shape and the actual pile occlusion.
    // No moving envelope, random deletion, or global-count opacity is applied.
    if (sand_position.y > short_geometry.y) discard;
    if (sand_position.y < grain_surface) discard;
    float r2 = dot(grain_local,grain_local);
    if (r2 >= 1.0) discard;
    float strength = min(1.5,max(0.0,short_contrast / .35));
    float detail = (grain_tone + .07*(1.0-r2) - .06*grain_local.x) * strength;
    vec2 uv = fract(vec2(grain_tone*17.1,grain_tone*29.7)
                    + grain_local * (1.6*short_geometry.w) / (2.0*sand_geometry.y));
    vec3 rgb = mix(grain_tone_color(detail,0.0),texture2D(texture0,uv).rgb,sand_mix);
    float coverage = 1.0-smoothstep(.80,1.0,r2);
    gl_FragColor = vec4(rgb,frag_color.a*coverage);
}
"""


def install(widget_class):
    import numpy as np
    from kivy.graphics import Color, Mesh, Callback
    from kivy.graphics.opengl import glGetIntegerv, GL_CURRENT_PROGRAM, glGetAttribLocation, glEnableVertexAttribArray
    from sand_flow_material import GRAIN_FRAGMENT_HEADER, SandFlowContext
    old_build, old_draw = widget_class._build_dynamic_canvas, widget_class._draw_stream
    chunk = 4096
    indices = (np.arange(chunk,dtype=np.uint16)[:,None]*4
               + np.asarray([0,1,2,0,2,3],dtype=np.uint16)).ravel()

    def build(self):
        self._stream_batched = True
        try: old_build(self)
        finally: self._stream_batched = False
        self._short_flow_context = None
        self._particle_meshes, self._particle_buffers, self._particle_counts = [], [], []
        try:
            if os.environ.get("HG_FLOW_FORCE_FAIL"):
                raise RuntimeError("forced particle mesh fallback")
            context = SandFlowContext((self._cx,self._R_inner,self._upper_sand_bot,
                self._taper["in_pts"][0][1]),VERTEX_SHADER,GRAIN_FRAGMENT_HEADER+FRAGMENT_BODY,tag=6)
            # Prepare the first Mesh before removing the working Line groups.
            # A driver/allocation failure must leave a drawable fallback.
            self._short_flow_context = context
            ensure(self,0)
            groups = [r[0] for r in self._stream_pools.values()]
            position = self.canvas.children.index(groups[0])
            for group in groups:self.canvas.remove(group)
            self.canvas.insert(position,context)
            self.flow_renderer = "particle_sprite_pool"
        except Exception as exc:
            self._short_flow_context = None
            self._particle_meshes, self._particle_buffers, self._particle_counts = [], [], []
            self._reserve_stream_lines()
            self.flow_renderer = "line_pool"
            print("particle mesh unavailable, using Line: %s" % exc)

    # Other RenderContexts can disable these global GL arrays while Kivy's
    # per-shader format cache still considers them enabled. Restore only the
    # locations actually used here; otherwise all grains vanish after frame 1.
    attribute_cache = {}
    def restore_attributes(_):
        program = glGetIntegerv(GL_CURRENT_PROGRAM)[0]
        if program not in attribute_cache:
            attribute_cache[program] = [glGetAttribLocation(program, name)
                for name in (b"vPosition",b"vTexCoords0",b"vTone",b"vSurface")]
        for location in attribute_cache[program]:
            if location >= 0:glEnableVertexAttribArray(location)

    def ensure(self, index):
        while len(self._particle_meshes) <= index:
            data = np.zeros((chunk,4,6),dtype=np.float32)
            data[:,:,:2] = -10000
            with self._short_flow_context:
                Color(1,1,1,1)
                Callback(restore_attributes)
                mesh = Mesh(vertices=data.ravel(),indices=indices,mode="triangles",
                    texture=self._sand_material.texture if self._sand_material else None,
                    fmt=[(b"vPosition",2,"float"),(b"vTexCoords0",2,"float"),
                         (b"vTone",1,"float"),(b"vSurface",1,"float")])
            self._particle_meshes.append(mesh)
            self._particle_buffers.append(data)
            self._particle_counts.append(0)

    def draw(self):
        context = self._short_flow_context
        if context is None:return old_draw(self)
        import sys
        module=sys.modules[widget_class.__module__]
        n=self.pn
        pitch=grain_pitch(self._R_inner,module.SAND_MATERIAL_COARSE)
        vertices=particle_vertices(self.px[:n],self.py[:n],self.pwp[:n],self.psz[:n],pitch)
        surfaces=mound_surface_at(vertices[:,:,0], self._mound_carve._v,
                                  self._lower_sand_bot)
        for i in range((n+chunk-1)//chunk):ensure(self,i)
        for i,mesh in enumerate(self._particle_meshes):
            count=min(chunk,max(0,n-i*chunk))
            previous=self._particle_counts[i]
            if not count and not previous:continue
            data=self._particle_buffers[i]
            if count:
                data[:count,:,:5]=vertices[i*chunk:i*chunk+count]
                data[:count,:,5]=surfaces[i*chunk:i*chunk+count]
            if count<previous:data[count:previous,:,:2]=-10000
            mesh.texture=self._sand_material.texture if self._sand_material else None
            mesh.vertices=data.ravel()
            self._particle_counts[i]=count
        context.update_flow(self.elapsed,self._particle_motion_scale,self._sand_material,
            (self.sand_base,self.sand_dark,self.sand_light))
        outlet=2*self._neck_y-self._taper["y_bot"]
        context["short_geometry"]=(self._cx,outlet,self._taper["t_in"],pitch)
        context["short_contrast"]=module.SAND_MATERIAL_GRAIN
        self._short_flow_state=dict(active=bool(n),particles=n,pitch=pitch,
            rendered_particles=sum(self._particle_counts),birth_identity="pwp+ptl")

    widget_class._build_dynamic_canvas=build
    widget_class._draw_stream=draw
