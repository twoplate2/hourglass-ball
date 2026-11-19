"""Persistent short sprites with a depth-only art offset from real trajectories.

HourglassWidget owns births, motion, contact and mass accounting. Rendering
translates grains inward and rounds the exposed rear edge after supply ends;
size, pigment and lifetime remain unchanged.
There is no cycle-phase envelope or mask that could delete airborne grains.
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
    return max(0.0,half-sprite_radius(pitch,2))


def sprite_aspect(pitch,reference_pitch):
    # Fine clusters need the same support area, while their individual texture
    # grains remain fine. Bound exceptional inputs so this cannot become a line.
    ratio=max(.8,reference_pitch)/max(.8,pitch)
    return min(2.4,1.45*max(1.0,ratio*ratio))


def emission_scale(radius,half):
    # Match the previous wide stream's line density as the aperture grows.
    return max(1.0,half/max(1e-6,.07*radius))


def coherent_birth(index, lateral_random, speed_random, shift=.5):
    """Bound short-window gaps while keeping each grain's trajectory persistent.

    A golden-angle sequence has no fixed lanes. Small immutable jitter avoids
    exact repeats; a narrow speed spread keeps that coverage after free fall.
    Inputs consume the existing birth RNG draws, never draw or move live grains.
    """
    u=(shift+index*.6180339887498949)%1.0
    u=min(1.0,max(0.0,u+(lateral_random-.5)*.012))
    x=2.0*u-1.0
    # Slightly more support at the edges, without widening the aperture or
    # moving a grain after birth. The monotone map has no fixed side lanes.
    # The first few dozen grains need an irregular advancing front. Fade their
    # original speed scatter at birth; never reshape or respawn live grains.
    onset=max(0.0,1.0-index/96.0)
    speed_span=.5+24.5*onset*onset
    return x*(1.3-.3*x*x),47.5+speed_span*(speed_random-.5)


def contact_upper_bound(top, curve, cx, half):
    """Safe cheap contact gate over the stream corridor, including bowl sides."""
    xs,ys,x0,scale,n1=curve
    if len(xs)<2:return top
    lo=max(0,int(math.floor((cx-half-x0)*scale)))
    hi=min(int(n1),int(math.ceil((cx+half-x0)*scale)))
    return max(top,max(ys[lo:hi+1]))



# Art direction only: translate whole existing sprites, keeping their size,
# pigment and lifetime. Physics and contact positions remain unchanged.
ART_STREAM_STRENGTH = 1.7

def art_stream_positions(xs, ys, phases, *, center, outlet, half,
                         pitch, strength=ART_STREAM_STRENGTH):
    """One geometric styling rule for every grain throughout the cycle.

    No global time, birth time, first-impact switch or pile-height gate.
    Whole sprites move inward smoothly with depth, retaining their material,
    size and front detail. Actual pile clipping is handled by the renderer.
    """
    import numpy as np
    x = np.asarray(xs)
    if not len(x) or strength <= 0 or half <= 5*pitch:
        return x
    def smooth(a, b, value):
        t = np.clip((value-a)/(b-a), 0.0, 1.0)
        return t*t*(3.0-2.0*t)
    y = np.asarray(ys)
    active = smooth(5.0, 8.0, half/max(.8, pitch))
    active = active*smooth(1.5*half, 8.0*half, outlet-y)
    radial = np.minimum(1.0, np.abs(x-center)/max(1.0, .87*half))
    edge = smooth(.40, .95, radial)
    # Preserve a continuous radial distribution instead of independently
    # pulling random edge carriers inward and opening short empty notches.
    pull = strength*active*(.075 + .075*edge)
    result=np.where(active > 0.0, center+(x-center)*(1.0-pull), x)
    thin=max(0.,min(1.,(10.-half/max(.8,pitch))/3.))
    if thin<=0.:return result
    # A monotone redistribution supports the fine stream's rim without adding
    # particles. It preserves its outer extent and introduces no random lumps.
    bound=max(pitch,source_half_width(half,pitch))
    edge_pull=strength*active*(.075+.075*smooth(.40,.95,bound/max(1.,.87*half)))
    extent=bound*(1.-edge_pull)
    u=np.clip(np.abs(result-center)/extent,0.,1.)
    fill=.45*thin*smooth(.25*half,1.5*half,outlet-y)
    return center+(result-center)*(1.+fill*(1.-u*u))


def flow_phase(elapsed, rate=12.0):
    # Match the birth-id hash period. Only ids wrap; physical ages never do.
    return (max(0.0, elapsed) * rate) % 4096.0


def steady_profile_mix(half,radius):
    """Keep the validated fine-outlet field; transition by fixed geometry only."""
    if radius is None:return 1.
    t=max(0.,min(1.,(half/max(radius,1e-6)-.100)/.003))
    return t*t*(3.-2.*t)


def cohort_stream_positions(xs, ys, births, scales, *, center, outlet, half, pitch,
                            radius=None):
    """A gentle irregular contour carried by neighbouring birth cohorts.

    Each grain retains its coordinates within the same material cohort; no
    screen-fixed waves, timer phases, independent lump ejection or frame noise.
    """
    import numpy as np
    x,y,birth,scale=np.broadcast_arrays(xs,ys,births,scales)
    if not x.size or half<=0:return x
    material=birth*scale*4.0
    cell=np.floor(material);t=material-cell;t=t*t*(3.-2.*t)
    def value(index,side):
        return np.remainder(np.sin(index*12.9898+side)*43758.5453,1.)
    def signal(side):
        return 2.*(value(cell,side)*(1.-t)+value(cell+1.,side)*t)-1.
    release=np.clip((outlet-y-.25*half)/(1.25*half),0.,1.)
    release=release*release*(3.-2.*release)
    left,right=signal(17.13),signal(63.71)
    # Separate bulk inflation from contour motion. Both signals belong to
    # immutable birth cohorts, so they travel down with the existing grains.
    width=(left+right)*.5
    sway=(right-left)*.5
    available=max(0.,half/max(pitch,.8)-6.)*.04
    # Fine apertures express life through the moving axis and independent rim;
    # bulk inflation there is easily confused with a one-grain pinch/bulge.
    width_amount=min(.0125,available)*steady_profile_mix(half,radius)
    axis_amount=min(.034,.40*available)
    # All apertures share a 2.5% peak-to-peak bulk-width budget. The wider
    # independent micro-grain fringe remains unchanged; no grain is removed.
    return (center+(x-center)*(1.+width_amount*release*width)
            +half*axis_amount*release*sway)


def advected_edge_positions(xs, ys, births, *, center, outlet, half, surface):
    """Move only the outer part of real grains, with birth-stable side signals."""
    import numpy as np
    x, y, birth = map(np.asarray, (xs, ys, births))
    if not x.size or half <= 0:
        return x
    lane = (x - center) / half
    depth = outlet - y
    edge = np.clip((np.abs(lane) - .48) / .46, 0., 1.)
    release = np.clip((depth - .35 * half) / (1.8 * half), 0., 1.)
    landing = np.clip((y - surface - 2.) / (.7 * half), 0., 1.)
    cell = birth / .075
    key = np.floor(cell)
    t = cell - key
    t *= t * (3. - 2. * t)
    side = np.where(lane < 0., 17.13, 63.71)
    a = np.remainder(np.sin(key * 12.9898 + side) * 43758.5453, 1.)
    b = np.remainder(np.sin((key + 1.) * 12.9898 + side) * 43758.5453, 1.)
    return x + min(4., .12 * half) * edge**1.3 * release * landing * (2. * (a + (b-a)*t) - 1.)


def rounded_front_positions(xs, ys, *, center, outlet, half, surface, pitch):
    """Round the airborne leading cohort without changing its collision path."""
    import numpy as np
    y = np.asarray(ys)
    if y.size < 2 or half <= 0:
        return y
    front = float(np.min(y))
    clearance = np.clip((front - surface - 2.*pitch) / max(1.,half), 0., 1.)
    amplitude = min(.32*half,.3*(outlet-front))*clearance
    if amplitude <= 0.:
        return y
    lane = np.clip(np.abs(np.asarray(xs)-center)/half,0.,1.)
    cap = np.clip(1.-(y-front)/(2.*amplitude),0.,1.)
    return y+amplitude*lane**2*cap**2


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


def birth_material_payload(birth_time, birth_scale, source_x, jump_y=1.0):
    """Immutable sampling state; no current time or falling position is used.

    Bound shader clocks/offsets before float32 upload, including 100-hour runs.
    Birth motion scale is stored separately so resizing cannot retime a grain.
    """
    import numpy as np
    times, scales, xs = np.broadcast_arrays(birth_time, birth_scale, source_x)
    out = np.empty((times.size, 5), dtype=np.float32)
    out[:,0] = xs.ravel()
    out[:,1] = np.remainder(times * scales, 16.0).ravel()
    out[:,2] = np.remainder(times * .125, 1.0).ravel()
    out[:,3] = np.remainder(times * (.0625 * jump_y), 1.0).ravel()
    blend = np.clip(times / .3, 0.0, 1.0)
    out[:,4] = (blend * blend * (3.0 - 2.0 * blend)).ravel()
    return out


def particle_vertices(xs, ys, phases, sizes, pitch, aspect=1.45, *, aperture_half=None,
                      fringe=False, center=None):
    """One persistent short sprite per real particle; never changes its centre."""
    import numpy as np
    xs, ys, phases, sizes = [np.asarray(v) for v in (xs, ys, phases, sizes)]
    n = len(xs)
    out = np.empty((n, 4, 5), dtype=np.float32)
    corners = np.asarray(((-1,-1),(1,-1),(1,1),(-1,1)), dtype=np.float32)
    radius = sprite_radius(pitch,sizes)
    seed = (phases / math.tau) % 1.0
    support = radius
    if aperture_half is not None:
        transition = np.clip((aperture_half / radius - 2.0) / 2.0, 0.0, 1.0)
        narrow = 1.0 - transition * transition * (3.0 - 2.0 * transition)
        support = radius + narrow * np.maximum(0.0, 2.0 * aperture_half - radius)
    if fringe:
        support = np.maximum(support, 2.2*radius)
        if center is not None and aperture_half is not None and aperture_half>8.:
            # Every selected birth can contribute, including central parents.
            # Side is frozen in the seed; a contracting/swaying parent cannot
            # move a fine grain across the stream or make it disappear.
            cohort=np.floor(seed*10.).astype(np.int32)
            side=np.where(cohort%2==0,-1.,1.)
            tiny_radius=np.maximum(.82,.22*radius)
            inner=center+side*.72*aperture_half
            outer_edge=center+side*np.where(seed<.2,1.36,1.72)*aperture_half
            required=np.maximum(np.abs(inner-xs),np.abs(outer_edge-xs))+tiny_radius+.25
            boundary=np.abs(seed*10.-np.rint(seed*10.))<1e-4
            required=np.where(boundary,np.abs(xs-center)+1.72*aperture_half+tiny_radius+.25,required)
            outer=np.maximum(support,required)
            selected=(seed<.202)|((seed*10.-cohort)<.652)|boundary
            support=np.where(selected,outer,support)
    out[:,:,0] = xs[:,None] + support[:,None] * corners[None,:,0]
    out[:,:,1] = ys[:,None] + radius[:,None] * corners[None,:,1] * aspect
    out[:,:,2:4] = corners[None,:,:]
    # Extend only static glyph support; local material remains in actual grain
    # pixels, and physical source positions/radii and trajectories stay intact.
    if aperture_half is not None or fringe:
        out[:,:,2] *= (support / radius)[:,None]
    # Two disjoint bands preserve the immutable seed AND size in one float.
    # The shader can reconstruct the actual pixel radius without a larger Mesh.
    out[:,:,4] = (seed*.5+(sizes>1))[:,None]
    return out


def neck_clip_geometry(points, mirror, lower_center):
    """Recover the actual quadratic control and mirror it into the lower cup."""
    i=len(points)//2
    t=i/(len(points)-1);u=1.-t
    control=(points[i][1]-u*u*points[0][1]-t*t*points[-1][1])/(2*u*t)
    return (points[0][0],mirror-control,mirror-points[0][1],lower_center)


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
                   carve_vertices,floor,base,light,grain,*,contact=None):
    """Keep bright airborne grains; merge supported grains into the pile palette.

    Gravity variation is immutable for a grain, including after array compaction.
    Colours have one shared RGBA8 representation for Rectangle and GPU drawing.
    No position, count, lifetime, alpha or random state is changed here.
    """
    strength=max(0.0,min(1.5,grain/.35))
    contact_strength=0.
    if contact is not None:
        center,half,clearance=contact
        contact_strength=max(0.,min(1., (2.1*half-clearance)/max(1.,.65*half)))
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
            if contact_strength:
                radial=max(0., min(1., (half-abs(xs[i]-center))/max(1.,.25*half)))
                vertical=max(0., 1.-gap/(3.*max(1.,half_heights[i])))
                blend=max(blend,contact_strength*radial*vertical)
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
    if contact_strength:
        radial=np.clip((half-np.abs(x-center))/max(1.,.25*half),0.,1.)
        vertical=np.clip(1.-np.maximum(0.,y-surface)/(3.*np.maximum(1.,hh)),0.,1.)
        blend=np.maximum(blend,contact_strength*radial*vertical)
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
attribute vec4 vBirth;
attribute float vBirthBlend;
varying vec4 grain_birth;
varying float grain_birth_blend;
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
    grain_birth = vBirth;
    grain_birth_blend = vBirthBlend;
    frag_color = color * vec4(1.0,1.0,1.0,opacity);
    gl_Position = projection_mat * modelview_mat * vec4(vPosition,0.0,1.0);
}
"""

FRAGMENT_BODY = """
uniform vec4 short_geometry;
uniform float short_coarse;
uniform float short_join_overlap;
uniform float short_join_enabled;
uniform float short_aspect;
uniform vec4 short_glass;
uniform float short_strength;
uniform vec2 short_texture_size;
varying float grain_radius;
varying vec2 grain_local;
varying float grain_tone;
varying float grain_surface;
varying vec4 grain_birth;
varying float grain_birth_blend;
float micro_hash(vec2 p) {
    return clump_hash(p);
}
vec3 micro_material_rgb(vec2 uv,vec2 local) {
    vec3 colour=texture2D(texture0,uv).rgb;
    vec3 weights=vec3(.299,.587,.114);
    float delta=dot(colour-sand_base,weights);
    float span=dot(delta >= 0.0 ? sand_light-sand_base : sand_base-sand_dark,weights);
    float x=uv.x*2.0-1.0;
    float edge=clamp((x*x-.64)/.36,0.0,1.0);
    edge=edge*edge*(3.0-2.0*edge);
    float baked=sand_lighting.x*(sand_lighting.y*(uv.y-.5)-.07*x-.16*edge);
    float pigment=short_strength > 0.0 ? delta/max(span,.001)-baked : 0.0;
    // Few-pixel streams need stronger fixed grain differences, rather than
    // lower coverage. The cue belongs to each micro-grain and moves with it.
    float narrow=1.0-smoothstep(2.0,4.0,short_geometry.z/grain_radius);
    float contrast=mix(1.4,3.0,narrow);
    float facet=short_strength*.15*clamp(dot(local,vec2(-.35,.7)),-1.0,1.0);
    float outlet_uv=(short_geometry.y-sand_geometry.z)/(2.0*sand_geometry.y);
    float illumination=sand_lighting.x*sand_lighting.y*(fract(outlet_uv)-.5);
    float tone=clamp(pigment*contrast+facet+illumination,-.9,.9);
    return mix(sand_base,tone >= 0.0 ? sand_light : sand_dark,abs(tone));
}
float lower_glass_ceiling(float radial) {
    if (radial<=short_geometry.z) return short_geometry.y;
    if (radial<short_glass.x) {
        float s=sqrt(clamp((radial-short_geometry.z)/(short_glass.x-short_geometry.z),0.0,1.0));
        float u=1.0-s;
        return u*u*short_geometry.y+2.0*u*s*short_glass.y+s*s*short_glass.z;
    }
    return short_glass.w+sqrt(max(0.0,sand_geometry.y*sand_geometry.y-radial*radial));
}
void main(void) {
    // Clip only the aperture, this grain's shape and the actual pile occlusion.
    // No moving envelope, random deletion, or global-count opacity is applied.
    if (sand_position.y > short_geometry.y) discard;
    if (sand_position.y < grain_surface) discard;
    // Each cluster has an immutable orientation and irregular local placement.
    // No time noise: its details travel with the same real particle for life.
    float angle=clump_hash(vec2(grain_tone*13.1,grain_tone*31.7))*6.283185;
    // More overlap between real moving clusters prevents short empty notches.
    // Compensate the local domain so the fine grains keep their pixel shape.
    vec2 domain=mat2(cos(angle),-sin(angle),sin(angle),cos(angle))
                    *(grain_local*vec2(1.0,short_aspect/1.08));
    // Keep the visible grains above subpixel size in the thinnest aperture.
    // A lower cell count enlarges grain marks, never the real particle quad.
    float narrow=1.0-smoothstep(2.0,4.0,short_geometry.z/grain_radius);
    float cell_scale=mix(1.5,.5,narrow);
    vec2 cell=floor((domain+1.0)*cell_scale);
    float seed=micro_hash(cell+grain_tone*31.7);
    vec2 local=fract((domain+1.0)*cell_scale)-.5;
    float seed_y=micro_hash(cell+17.0+grain_tone*13.1);
    local-=vec2(seed-.5,seed_y-.5)*.34;
    float micro_r=length(local);
    // Coarse grains keep pores without replacing their texture with large dots.
    float coarse=smoothstep(1.8,3.0,short_coarse);
    float depth=max(0.0,short_geometry.y-sand_position.y);
    float open_flow=smoothstep(.5*short_geometry.z,2.0*short_geometry.z,depth);
    float outer=smoothstep(.42*short_geometry.z,.96*short_geometry.z,
                          abs(sand_position.x-short_geometry.x));
    float pore=open_flow*mix(.12,.23,outer)
        *smoothstep(5.0,9.0,short_geometry.z/max(.8,short_geometry.w));
    float micro=1.0-smoothstep(mix(.42,.36,coarse)-pore,
                               mix(.55,.47,coarse)-pore,micro_r);
    // One baked pigment per visible grain; sample at a texel centre so linear
    // filtering cannot average four different grains into a soft parent patch.
    // The weak local facet supplies the grain's own surface relief.
    vec2 pigment_uv=vec2(micro_hash(cell+grain_tone*53.1+73.7),
                         micro_hash(cell+grain_tone*91.3+vec2(37.9,3.1)));
    vec2 uv=(floor(pigment_uv*short_texture_size)+.5)/short_texture_size;
    vec3 rgb=micro_material_rgb(uv,local);
    // Carry a small upstream texture patch with this real grain for its life.
    // Both colour details now use local coordinates and move with one particle.
    float join_length=short_join_overlap+max(4.0,1.5*short_geometry.z);
    if (short_join_enabled > .5 && depth < join_length) {
        vec2 source=vec2(grain_birth.x,short_geometry.y)
            +grain_local*grain_radius*vec2(1.0,short_aspect);
        vec3 inherited=neck_flow_rgb_at(source,grain_birth.y,grain_birth.zw,
                                       grain_birth_blend);
        float released=smoothstep(short_join_overlap,join_length,depth);
        // Keep the birth patch only at the hand-off. A permanent soft blend
        // averages away the moving grains' contrast and reads as a filament.
        rgb=mix(inherited,rgb,released);
    }
    // Few-pixel streams need lateral glyph overlap as well as vertical
    // overlap. Only the central part receives extra moving micro-grains;
    // the original particle silhouette still supplies the entire outer edge.
    float support=1.0+narrow*max(0.0,2.0*short_geometry.z/grain_radius-1.0);
    float core=1.0-smoothstep(.90*short_geometry.z,.94*short_geometry.z,
                              abs(sand_position.x-short_geometry.x));
    float vertical=1.0-smoothstep(.75,1.0,abs(grain_local.y));
    // Small overlap between existing micro-grain carriers keeps the coherent
    // contour from acquiring one-row holes; micro-grain pixel size is unchanged.
    // Fine/medium mouths need a little more glyph overlap to avoid short
    // notches; wide mouths already have dense coverage. Keep this geometric,
    // never tied to a timer phase or the first collision.
    float wide_mouth=smoothstep(.11,.13,short_geometry.z/sand_geometry.y);
    float original=(1.0-smoothstep(mix(.95,.88,wide_mouth),
                                  mix(1.30,1.23,wide_mouth),abs(grain_local.x)))*vertical*micro;
    float extra=(1.0-smoothstep(.75,1.0,abs(grain_local.x/support)))*vertical*micro;
    // Each contribution still has the same fixed local grains and real
    // particle lifetime. There is no background strip or animated envelope.
    float coverage=max(original,narrow*core*extra);
    // Independent micro-grains soften the outer density, not whole carrier
    // blocks. Cell identity is fixed for life and moves with the real grain.
    // Retain the original dense core and share pigment/size with its grains.
    float rim_depth=max(0.0,short_geometry.y-sand_position.y);
    float rim_release=smoothstep(.25*short_geometry.z,1.25*short_geometry.z,rim_depth);
    float rim_side=smoothstep(.45*short_geometry.z,.80*short_geometry.z,
                             abs(sand_position.x-short_geometry.x));
    // Keep edge detail close to its moving grain: overlapping broad fringes
    // otherwise read as another temporary layer of bulk stream width.
    float rim_falloff=1.0-smoothstep(.65,1.75,abs(grain_local.x));
    // Hash output can be exactly zero: step(0,0) must not create grains
    // outside the fringe when a selected quad spans the opposite side.
    float rim_pick=rim_falloff>0.0 ? step(seed_y,.24*rim_falloff*rim_falloff) : 0.0;
    float fringe=rim_pick*micro*vertical*rim_side*rim_release;
    coverage=max(coverage,fringe);
    float wall=lower_glass_ceiling(abs(sand_position.x-short_geometry.x));
    if (abs(sand_position.x-short_geometry.x)>short_geometry.z)
        coverage*=smoothstep(.25,1.0,wall-sand_position.y);
    gl_FragColor = vec4(rgb,frag_color.a*coverage);
}
"""


# Compatibility keeps the same moving local grains, without the birth material
# or its additional vertex attributes. A carrier failure must not become silk.
BASIC_VERTEX_SHADER = (VERTEX_SHADER
    .replace('attribute vec4 vBirth;\n','')
    .replace('attribute float vBirthBlend;\n','')
    .replace('varying vec4 grain_birth;\n','')
    .replace('varying float grain_birth_blend;\n','')
    .replace('    grain_birth = vBirth;\n','')
    .replace('    grain_birth_blend = vBirthBlend;\n',''))
BASIC_FRAGMENT_HEADER = """
$HEADER$
varying vec2 sand_position;
uniform vec4 sand_geometry;
uniform vec3 sand_base;
uniform vec3 sand_dark;
uniform vec3 sand_light;
uniform vec2 sand_lighting;
float clump_hash(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
"""
BASIC_FRAGMENT_BODY = ((FRAGMENT_BODY[:FRAGMENT_BODY.index('    float depth=')]
    + FRAGMENT_BODY[FRAGMENT_BODY.index('    // Few-pixel streams need lateral'):])
    .replace('varying vec4 grain_birth;\n','')
    .replace('varying float grain_birth_blend;\n',''))


def install(widget_class):
    import numpy as np
    from kivy.graphics import Color, Mesh, Callback
    from kivy.graphics.opengl import glGetIntegerv, GL_CURRENT_PROGRAM, glGetAttribLocation, glEnableVertexAttribArray
    from sand_flow_material import GRAIN_FRAGMENT_HEADER, SandFlowContext
    import granular_material as grain_material
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
        self._short_material_mode = None
        self._short_material_error = None
        try:
            if os.environ.get("HG_FLOW_FORCE_FAIL"):
                raise RuntimeError("forced particle mesh fallback")
            if self._sand_material is None or self._sand_material.texture is None:
                raise RuntimeError("particle material unavailable")
            for mode, vertex, fragment in (
                    ('carrier',VERTEX_SHADER,GRAIN_FRAGMENT_HEADER+FRAGMENT_BODY),
                    ('basic_grains',BASIC_VERTEX_SHADER,BASIC_FRAGMENT_HEADER+BASIC_FRAGMENT_BODY)):
                try:
                    if mode == 'carrier' and os.environ.get('HG_FLOW_FORCE_CARRIER_FAIL'):
                        raise RuntimeError('forced carrier material failure')
                    context = SandFlowContext((self._cx,self._R_inner,self._upper_sand_bot,
                        self._taper["in_pts"][0][1]),vertex,fragment,tag=6)
                    self._short_material_mode = mode
                    self._short_flow_context = context
                    self._particle_meshes, self._particle_buffers, self._particle_counts = [], [], []
                    context["short_aspect"]=1.45
                    # Only retire the working Line pool after a Mesh succeeds.
                    ensure(self,0)
                    break
                except Exception as exc:
                    self._short_flow_context = None
                    if mode == 'basic_grains':
                        raise RuntimeError('%s; basic grains: %s' % (self._short_material_error,exc)) from exc
                    self._short_material_error = str(exc)
            if self._short_material_mode == 'basic_grains':
                print('carrier material unavailable, using basic grains: %s' % self._short_material_error)
            groups = [r[0] for r in self._stream_pools.values()]
            position = self.canvas.children.index(groups[0])
            for group in groups:self.canvas.remove(group)
            self.canvas.insert(position,context)
            self.flow_renderer = "particle_sprite_pool"
        except Exception as exc:
            self._short_flow_context = None
            self._particle_meshes, self._particle_buffers, self._particle_counts = [], [], []
            self._short_material_mode = None
            self._short_material_error = str(exc)
            self._reserve_stream_lines()
            self.flow_renderer = "line_pool"
            print("particle mesh unavailable, using Line: %s" % exc)

    # Other RenderContexts can disable these global GL arrays while Kivy's
    # per-shader format cache still considers them enabled. Restore only the
    # locations actually used here; otherwise all grains vanish after frame 1.
    def restore_attributes(_, carrier):
        program = glGetIntegerv(GL_CURRENT_PROGRAM)[0]
        # Query the currently linked program; context loss may reuse a GL id.
        names=(b"vPosition",b"vTexCoords0",b"vTone",b"vSurface")
        if carrier:names+=(b"vBirth",b"vBirthBlend")
        for name in names:
            location=glGetAttribLocation(program,name)
            if location >= 0:glEnableVertexAttribArray(location)

    def ensure(self, index):
        while len(self._particle_meshes) <= index:
            carrier = self._short_material_mode == 'carrier'
            data = np.zeros((chunk,4,11 if carrier else 6),dtype=np.float32)
            data[:,:,:2] = -10000
            with self._short_flow_context:
                Color(1,1,1,1)
                Callback(lambda instruction,carrier=carrier:restore_attributes(instruction,carrier))
                fmt=[(b"vPosition",2,"float"),(b"vTexCoords0",2,"float"),
                     (b"vTone",1,"float"),(b"vSurface",1,"float")]
                if carrier:fmt.extend(((b"vBirth",4,"float"),(b"vBirthBlend",1,"float")))
                mesh = Mesh(vertices=data.ravel(),indices=indices,mode="triangles",
                    texture=self._sand_material.texture if self._sand_material else None,
                    fmt=fmt)
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
        reference=stream_pitch(self._R_inner,module._grain_level(module.SAND_GRAIN_LEVEL_DEFAULT)[1],
                               self._taper['t_in'])
        aspect=sprite_aspect(pitch,reference)
        # Fine streams need more overlap along the flow, not larger grains.
        # The shader's aspect compensation keeps each micro-grain round.
        thin=max(0.,min(1.,(10.-self._taper['t_in']/max(.8,pitch))/3.))
        mix=steady_profile_mix(self._taper['t_in'],self._R_inner)
        aspect*=1.+.60*thin+.12*mix*(1.-thin)
        order=grain_material.stable_grain_order(self.pwp[:n],self.ptl[:n])
        self._particle_draw_order=order
        original_x=np.asarray(self.px[:n])
        drawn_x=art_stream_positions(original_x,self.py[:n],self.pwp[:n],
            center=self._cx,outlet=2*self._neck_y-self._taper['y_bot'],
            half=self._taper['t_in'],pitch=pitch)
        cohort=hasattr(self,'pbt') and hasattr(self,'pbm')
        if cohort:
            drawn_x=cohort_stream_positions(drawn_x,self.py[:n],self.pbt[:n],self.pbm[:n],
                center=self._cx,outlet=2*self._neck_y-self._taper['y_bot'],
                half=self._taper['t_in'],pitch=pitch,radius=self._R_inner)
            drawn_x=advected_edge_positions(drawn_x,self.py[:n],self.pbt[:n],
                center=self._cx,outlet=2*self._neck_y-self._taper['y_bot'],
                half=self._taper['t_in'],surface=self.get_mound_top_y())
        from grain_style import drained_stream_y
        drawn_y=drained_stream_y(drawn_x,self.py[:n],center=self._cx,
            outlet=2*self._neck_y-self._taper['y_bot'],half=self._taper['t_in'],pitch=pitch,
            supplied=getattr(self,'_sand_released',0.0))
        if cohort and n:
            drawn_y=rounded_front_positions(drawn_x,drawn_y,center=self._cx,
                outlet=2*self._neck_y-self._taper['y_bot'],half=self._taper['t_in'],
                surface=self.get_mound_top_y(),pitch=pitch)
        vertices=particle_vertices(drawn_x,drawn_y,self.pwp[:n],self.psz[:n],pitch,aspect,
                                   aperture_half=self._taper['t_in'],fringe=True)[order]
        carrier = self._short_material_mode == 'carrier'
        if carrier:
            births=birth_material_payload(self.pbt[:n],self.pbm[:n],
                self._cx+np.asarray(self.pxo[:n]),context["sand_jump_y"])
            pin=context["sand_mix_pin"]
            if pin >= 0.0:births[:,4]=pin
            births=births[order]
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
                if carrier:data[:count,:,6:]=births[i*chunk:i*chunk+count,None,:]
            if count<previous:data[count:previous,:,:2]=-10000
            mesh.texture=self._sand_material.texture if self._sand_material else None
            mesh.vertices=data.ravel()
            self._particle_counts[i]=count
        context.update_flow(self.elapsed,self._particle_motion_scale,self._sand_material,
            (self.sand_base,self.sand_dark,self.sand_light))
        outlet=2*self._neck_y-self._taper["y_bot"]
        context["short_geometry"]=(self._cx,outlet,self._taper["t_in"],pitch)
        context["short_coarse"]=module.SAND_MATERIAL_COARSE
        context["short_strength"]=module.SAND_MATERIAL_GRAIN
        context["short_texture_size"]=tuple(map(float,getattr(self._sand_material.texture,"size",(512,512))))
        context["short_join_overlap"]=module.OUTLET_OVERLAP
        context["short_aspect"]=aspect
        context["short_glass"]=neck_clip_geometry(self._taper['in_pts'],2*self._neck_y,self._lower_y_c)
        context["short_join_enabled"]=float(carrier and bool(self._sand_flow_contexts)
                                            and not module.NECK_FREE_GEOM)
        self._short_flow_state=dict(active=bool(n),particles=n,pitch=pitch,
            rendered_particles=sum(self._particle_counts),birth_identity="pwp+ptl",
            material_mode=self._short_material_mode,material_error=self._short_material_error,
            painter_order="fixed_birth_depth",position_style="coherent_core_v11" if cohort else "coherent_core_plain_v11",
            tail_style="exposed_grain_cap_v2")

    widget_class._build_dynamic_canvas=build
    widget_class._draw_stream=draw
