"""Persistent short sprites following the existing real particle trajectories.

HourglassWidget owns births, motion, contact and mass accounting. Rendering adds
no animated envelope or time-dependent mask that could delete airborne grains.
The pure field helpers below remain for historical 3.6 evidence tools.
"""
import os
import math


def grain_pitch(radius, coarse):
    return max(0.6, radius / 150.0 * (0.7 + 0.35 * coarse))


def stream_pitch(radius,coarse,half):
    return min(grain_pitch(radius,coarse),max(.8,.22*half))


def sprite_radius(pitch,size):
    return 1.6*max(.8,pitch)*(1.0+.15*(size>1))


def source_half_width(half,pitch):
    return max(0.0,half-sprite_radius(pitch,2)-1.0)


def emission_scale(radius,half):
    # Match the previous wide stream's line density as the aperture grows.
    return max(1.0,half/max(1e-6,.07*radius))


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
    radius = sprite_radius(pitch,sizes)
    out[:,:,0] = xs[:,None] + radius[:,None] * corners[None,:,0]
    out[:,:,1] = ys[:,None] + radius[:,None] * corners[None,:,1] * 1.08
    out[:,:,2:4] = corners[None,:,:]
    # Two disjoint bands preserve the immutable seed AND size in one float.
    # The shader can reconstruct the actual pixel radius without a larger Mesh.
    out[:,:,4] = (((phases / math.tau) % 1.0)*.5+(sizes>1))[:,None]
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


def visible_splash_sizes(count,xs,ys,hws,hhs,carve_vertices,floor):
    """Hide fully buried decals using the drawn surface; keep physical arrays intact."""
    if not count:return hws,hhs
    try:
        import numpy as np
    except ImportError:
        from bisect import bisect_right
        points=[]
        for i in range(0,len(carve_vertices),16):
            q=carve_vertices[i:i+16]
            if len(q)==16 and q[4]>q[0]:points.extend(((q[0],q[1]),(q[4],q[5])))
        nodes=sorted(dict(points).items());axis=[p[0] for p in nodes]
        def surface(x):
            if not nodes:return floor
            i=bisect_right(axis,x)
            if i==0:return nodes[0][1]
            if i==len(nodes):return nodes[-1][1]
            a,b=nodes[i-1],nodes[i]
            return a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])
        visible=[ys[i]+hhs[i]>=min(surface(xs[i]-hws[i]),surface(xs[i]),surface(xs[i]+hws[i]))
                 for i in range(count)]
        return ([hws[i] if visible[i] else 0.0 for i in range(count)],
                [hhs[i] if visible[i] else 0.0 for i in range(count)])
    x,y,hw,hh=[np.asarray(a[:count]) for a in (xs,ys,hws,hhs)]
    samples=np.column_stack((x-hw,x,x+hw))
    surface=mound_surface_at(samples,carve_vertices,floor).min(axis=1)
    visible=y+hh>=surface
    return np.where(visible,hw,0.0),np.where(visible,hh,0.0)


def splash_colours(count,xs,ys,vys,rests,stills,seeds,half_heights,
                   carve_vertices,floor,base,light,grain):
    """Keep bright airborne grains; merge supported grains into the pile palette.

    Gravity variation is immutable for a grain, including after array compaction.
    Colours have one shared RGBA8 representation for Rectangle and GPU drawing.
    No position, count, lifetime, alpha or random state is changed here.
    """
    strength=max(0.0,min(1.5,grain/.35))
    try:
        import numpy as np
    except ImportError:
        from bisect import bisect_right
        nodes=[]
        for i in range(0,len(carve_vertices),16):
            q=carve_vertices[i:i+16]
            if len(q)==16 and q[4]>q[0]:nodes.extend(((q[0],q[1]),(q[4],q[5])))
        nodes=sorted(dict(nodes).items());axis=[p[0] for p in nodes]
        def surface(x):
            if not nodes:return floor
            i=bisect_right(axis,x)
            if i==0:return nodes[0][1]
            if i==len(nodes):return nodes[-1][1]
            a,b=nodes[i-1],nodes[i]
            return a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])
        result=[]
        for i in range(count):
            gap=max(0.0,ys[i]-surface(xs[i]))
            t=max(0.0,min(1.0,gap/(2.0*max(1.0,half_heights[i]))))
            blend=(1.0 if rests[i]>0.0 or stills[i]>0.0 else
                   1.0-t*t*(3.0-2.0*t) if vys[i]<=0.0 else 0.0)
            detail=((seeds[i]*997.0)%1.0-.5)*.04*strength
            rgb=[max(0.0,min(1.0,base[k]+detail)) for k in range(3)]
            result.append(tuple(round(max(0.0,min(1.0,light[k]+(rgb[k]-light[k])*blend))*255)
                                for k in range(3))+(255,))
        return result
    if not count:return np.empty((0,4),dtype=np.uint8)
    x,y,vy,rest,still,seed,hh=[np.asarray(a[:count]) for a in
        (xs,ys,vys,rests,stills,seeds,half_heights)]
    surface=(mound_surface_at(x,carve_vertices,floor) if carve_vertices else
             np.full(count,floor))
    t=np.clip(np.maximum(0.0,y-surface)/(2.0*np.maximum(1.0,hh)),0.0,1.0)
    blend=np.where((rest>0.0)|(still>0.0),1.0,np.where(vy<=0.0,1.0-t*t*(3.0-2.0*t),0.0))
    detail=((seed*997.0)%1.0-.5)*.04*strength
    ground=np.clip(np.asarray(base)[None,:]+detail[:,None],0.0,1.0)
    rgb=np.asarray(light)[None,:]+(ground-np.asarray(light)[None,:])*blend[:,None]
    rgba=np.empty((count,4),dtype=np.uint8)
    rgba[:,:3]=np.rint(np.clip(rgb,0.0,1.0)*255).astype(np.uint8)
    rgba[:,3]=255
    return rgba


VERTEX_SHADER = """
$HEADER$
attribute float vTone;
attribute float vSurface;
uniform vec4 short_geometry;
varying float grain_radius;
varying vec2 sand_position;
varying vec2 grain_local;
varying float grain_tone;
varying float grain_surface;
void main(void) {
    sand_position = vPosition;
    grain_local = vTexCoords0;
    // vTone is now the seed/size payload; keep the historical attribute layout.
    float large=step(.75,vTone);
    grain_tone=(2.0*(vTone-large)-.5)*.60;
    grain_radius=1.6*max(.8,short_geometry.w)*(1.0+.15*large);
    grain_surface = vSurface;
    frag_color = color * vec4(1.0,1.0,1.0,opacity);
    gl_Position = projection_mat * modelview_mat * vec4(vPosition,0.0,1.0);
}
"""

FRAGMENT_BODY = """
uniform vec4 short_geometry;
uniform float short_coarse;
varying float grain_radius;
varying vec2 grain_local;
varying float grain_tone;
varying float grain_surface;
float micro_hash(vec2 p) {
    return clump_hash(p);
}
void main(void) {
    // Clip only the aperture, this grain's shape and the actual pile occlusion.
    // No moving envelope, random deletion, or global-count opacity is applied.
    if (sand_position.y > short_geometry.y) discard;
    if (sand_position.y < grain_surface) discard;
    // Each cluster has an immutable orientation and irregular local placement.
    // No time noise: its details travel with the same real particle for life.
    float angle=clump_hash(vec2(grain_tone*13.1,grain_tone*31.7))*6.283185;
    vec2 domain=mat2(cos(angle),-sin(angle),sin(angle),cos(angle))*grain_local;
    vec2 cell=floor((domain+1.0)*1.5);
    float seed=micro_hash(cell+grain_tone*31.7);
    vec2 local=fract((domain+1.0)*1.5)-.5;
    local-=vec2(seed-.5,micro_hash(cell+17.0+grain_tone*13.1)-.5)*.34;
    float micro_r=length(local);
    // Coarse grains keep pores without replacing their texture with large dots.
    float coarse=smoothstep(1.8,3.0,short_coarse);
    float micro=1.0-smoothstep(mix(.38,.30,coarse),mix(.51,.41,coarse),micro_r);
    vec2 uv = fract(vec2(grain_tone*17.1,grain_tone*29.7)
                    + grain_local * grain_radius * vec2(1.0,1.08) / (2.0*sand_geometry.y));
    vec3 sample_rgb=texture2D(texture0,uv).rgb;
    vec3 rgb=clamp(sand_base+(sample_rgb-sand_base)*1.6,0.0,1.0);
    // Only feather the quad border. A circular parent mask would make the
    // entire cluster read as a large pebble even when its interior is fine.
    float coverage = (1.0-smoothstep(.75,1.0,abs(grain_local.x)))
        *(1.0-smoothstep(.75,1.0,abs(grain_local.y)))*micro;
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
            if self._sand_material is None or self._sand_material.texture is None:
                raise RuntimeError("particle material unavailable")
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
    def restore_attributes(_):
        program = glGetIntegerv(GL_CURRENT_PROGRAM)[0]
        # Query the currently linked program; context loss may reuse a GL id.
        for name in (b"vPosition",b"vTexCoords0",b"vTone",b"vSurface"):
            location=glGetAttribLocation(program,name)
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
        pitch=stream_pitch(self._R_inner,module.SAND_MATERIAL_COARSE,self._taper['t_in'])
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
        context["short_coarse"]=module.SAND_MATERIAL_COARSE
        self._short_flow_state=dict(active=bool(n),particles=n,pitch=pitch,
            rendered_particles=sum(self._particle_counts),birth_identity="pwp+ptl")

    widget_class._build_dynamic_canvas=build
    widget_class._draw_stream=draw
