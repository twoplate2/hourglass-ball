"""GPU grain advection for the reservoir, neck and lower contact surface.

Two-phase flow follows Catlike Coding's Texture Distortion / Valve flow-map approach.
Only grain detail is advected; the palette and broad lighting stay in world space.
"""

import os
import math

from kivy.graphics import Color, Mesh, RenderContext
from grain_style import receiving_surface_profile
from render_options import float_option


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
// 🔴 **不回绕**的时间(秒) —— 材料坐标的平流用它(见 `sand_free_vref`)。
uniform float sand_time;
uniform float sand_mix;
uniform float sand_mix_pin;
uniform vec3 sand_tail; // mean top, grain-front amplitude, tube half-width
uniform vec4 sand_free; // outlet y, 边缘松散度, tube half-width, 前沿 y (<=0 关闭)
uniform float sand_seam_band;   // 交界过渡半带宽(**球直径的比例**); <=0 ⇒ 阶跃(旧行为)
uniform float sand_neck_anchor; // 颈部那套 UV 的 v 锚点(= main.py 的 NECK_UV_ANCHOR)
// 自由段密度场的尺度、对比度与漂移。
uniform float sand_core;   // 实心核占**半宽**的比例(0=整条都参与打散)
uniform float sand_edge;   // 打散强度总开关(0=完全不打散)
uniform float sand_bite;   // 打散**深度**的下限(旧值 0.25; 越小 ⇒ 随机幅度越大)
uniform float sand_alpha;  // 覆盖率上限(<1 才让粒子层透得出来)
// 自由段的纹理漂移速度使用独立倍率。
uniform float sand_free_drift;
// 二值孔隙的阈值小于零时关闭。
uniform float sand_hole_th;
// 洞的**渐入长度**(像素, 按球径比例给): <=0 ⇒ 不渐入(出口处会有横向分界线)。
uniform float sand_hole_ramp;
// 孔隙密度从出口向下渐增。
uniform float sand_hole_grow;
// 孔隙带从边缘向柱心展开。
uniform float sand_hole_wide;
// 自由段刚性下滚的速度，单位为像素/秒。
uniform float sand_free_vscroll;
// 速度场由出口速度、加速度和速度上限决定。
uniform float sand_free_vref;
uniform float sand_free_vcap;
// 🔴 **2026-10-11: 缩时倍率(`main._particle_motion_scale`)** —— 短周期时粒子被整体加速
//    (`450·ms²` / `60·ms`, main.py:3961/4097), 材质必须**同倍率**跟上, 否则 1s/2s 档
//    粒子比材质快 2~7 倍(工作流审计 2026-10-11 查出: 着色器里 60/450 是写死的)。
uniform float sand_motion_scale;
// 聚团与团间孔隙共享同一移动坐标。
uniform float sand_clump_gap;     // 缝里的覆盖率(1 = 关掉这个机制; 越小缝越透)
uniform float sand_clump_scale;   // 纵向频率(大 = 团更短更密)
uniform float sand_clump_xscale;  // 横向频率(小 = 跨越整条柱的横带; 大 = 圆团)
uniform float sand_clump_warp;    // 域的扭曲量(0 = 等间距横带; 1 = 参考值)
uniform float sand_clump_lo;      // 缝的阈值带(低端): 越大 ⇒ 缝**越少**(大部分连续)
uniform float sand_clump_hi;      // 缝的阈值带(高端): 越窄 ⇒ 缝的边越硬(不糊成灰)
uniform float sand_clump_edge;    // 缝里把轮廓收窄到原来的多少(1 = 不收窄)
uniform float sand_clump_start;   // 从哪个深度(球径比例)开始长
uniform float sand_clump_gain;    // 深度渐入的增益(快饱和 ⇒ 只做"出口附近渐入")
// 控制高速纹理的视觉混叠。
const float V0_FALL = 60.0;
const float G_FALL = 450.0;
// 孔隙梯度的起始深度以球径为单位。
const float HOLE_WIDE_START = 0.35;
// 纵向平流速度倍率。
uniform float sand_flow_vy;
// 出口松散区的渐入长度以球径为单位。
uniform float sand_free_ramp;
uniform float sand_edge_lo;
uniform float sand_edge_hi;
// 采样材质噪声塑造可见轮廓。
uniform float sand_neck_light;
// 调试: >0 ⇒ 直接把 `uv` 画成颜色(R=uv.x, G=uv.y, 都 clamp 到 0..1) —— 用来**读出**
// 着色器在颈部/球体各自采到的 uv, 而不是靠推。`necklight` 旁边给个 `uvdebug` 标记文件。
uniform float sand_uv_debug;
// 着色和颗粒采样使用同一坐标扣除光照。
uniform float sand_wrap_light;
// 调试: 每个 `SandFlowContext` 一个标记值, 画成颜色 ⇒ **直接读出"这个像素是哪个 context 画的"**。
uniform float sand_ctx_tag;
// 调试: `jump` 的 y 分量系数(默认 1.0)。两相位沿 v 的固有错开, 与 `sand_flow_vy` 合起来
// 才能把"纵向平流抹开"这条**完整**关掉(只关 `flowvy` 不够 —— `jump.y` 仍分开 32 纹素)。
uniform float sand_jump_y;
// 自由段纹理尺度越小，颗粒越粗。
uniform float sand_free_coarse;
// Free-region grain contrast multiplier (1 = unchanged, <1 = quieter base).
// Goal: the two layers must read as ONE speed. The material texture cannot convey the
// fall speed (measured: correlation peak stays at dy~0 at every K), so let the PARTICLES
// carry the motion and keep the material as a quiet base. See sand_free_coarse.
uniform float sand_free_grain;
// 🔴 **2026-10-10: 平流用连续时间**(见下面 `adv` 那段)。0 = 旧行为(每周期回跳, 净位移 0)。
uniform float sand_adv_cont;
// 🔴 **2026-10-12(2.88): 上球/颈部改"刚性连续下滚"**(去掉 1 Hz 的台阶与两相位沸腾)。
//    1 = 新行为; 0 = **逐字回到旧行为**(负对照 / 一键回退)。见下面那段注释。
uniform float sand_up_scroll;
// 🔴 **2026-10-12(2.90): 上球材质的纵向采样缩放(= 球径/沙体高)** —— 1.244 的 tex_coords 行为:
//    图案随沙体伸缩(沙面下降时图案跟着往下流, ~25px/s)。1.0 = 不缩放(颈/沙堆用)。
uniform float sand_up_scale;
// Only the neck context enables its existing short overlap with real grains.
uniform vec2 sand_outlet_join; // outlet y, overlap length in canvas pixels
uniform vec4 sand_throat;      // outlet y, half-width, grain pitch and straight inlet y
uniform vec2 sand_funnel;      // glass half-width at the upper join and quadratic control y
uniform vec3 sand_release;     // accelerating length in the neck, exit speed, transit time
uniform float sand_neck_fill; // coverage of the final sub-grain layer
uniform float sand_neck_feed; // current particle discharge relative to steady flow

float luma(vec3 c) {
    return dot(c, vec3(0.299, 0.587, 0.114));
}
float lighting(vec2 uv) {
    float x = uv.x * 2.0 - 1.0;
    float edge = clamp((x * x - 0.64) / 0.36, 0.0, 1.0);
    edge = edge * edge * (3.0 - 2.0 * edge);
    // 颈部(uv.y < 0)默认会被推到梯度暗端 —— 见 `sand_neck_light` 的注释。
    float vy = (sand_neck_light > 0.0) ? max(uv.y, 0.0) : uv.y;
    return sand_lighting.x * (sand_lighting.y * (vy - 0.5)
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
vec3 grain_tone_color(float detail, float illumination) {
    float tone = clamp(illumination + detail, -1.0, 1.0);
    return mix(sand_base, tone >= 0.0 ? sand_light : sand_dark, abs(tone));
}
float throat_texel_scale() {
    // Integer texture repeats keep the bounded sixteen-second clock seamless.
    const float travel=47.5*16.0;
    return max(1.0,floor(travel/max(2.0,2.0*sand_geometry.y)+.5))/travel;
}
vec2 sand_advection(float clock, vec2 scroll) {
    if (sand_adv_cont > 0.0 && sand_up_scroll >= 0.0)
        return sand_up_scroll > 0.0 ? scroll : vec2(0.0);
    return vec2(0.0,clock*47.5*throat_texel_scale());
}
vec2 sand_coordinates(vec2 position, out vec2 stretch) {
    float depth=sand_throat.x-position.y;
    float below=max(0.0,depth);
    float rise=max(0.0,position.y-sand_throat.w);
    float above=rise/max(1.0,2.0*sand_throat.y);
    // A point keeps its streamline and its time of passage through the lip.
    // The supply converges and accelerates from the bowl into the neck.
    float speed;
    float height;
    if (depth>=0.0) {
        speed=sqrt(sand_release.y*sand_release.y+900.0*below);
        height=-47.5*2.0*below/(sand_release.y+speed);
    } else {
        float travelled=max(0.0,sand_release.x+depth);
        float k=sqrt(450.0/max(.001,sand_release.x));
        float u=k*travelled/47.5;
        float v=sqrt(1.0+u*u);
        speed=47.5*v;
        height=max(0.0,-depth-sand_release.x)+47.5*(sand_release.z-log(u+v)/k);
        height+=rise*.82*above/(1.0+above);
    }
    float width=pow(1.0+below/max(1.0,4.5*sand_throat.y),-.25);
    if (rise>0.0) {
        float wall;
        if (position.y<sand_geometry.w) {
            // Invert the same quadratic used to draw the funnel wall.
            float b=2.0*(sand_funnel.y-sand_throat.w);
            float a=sand_geometry.w-sand_throat.w-b;
            float u=clamp(2.0*rise/max(.001,b+sqrt(max(0.0,b*b+4.0*a*rise))),0.0,1.0);
            wall=mix(sand_throat.y,sand_funnel.x,u*u);
        } else {
            float h=clamp(position.y-sand_geometry.z,0.0,sand_geometry.y);
            wall=sqrt(h*(2.0*sand_geometry.y-h));
        }
        // The moving central supply follows the funnel. Near the widest part
        // of the bowl it approaches a broad column of slowly settling sand.
        float extra=max(0.0,wall/sand_throat.y-1.0);
        width=1.0+extra/(1.0+.48*extra);
    }
    float derivative=1.0+.82*(1.0-1.0/((1.0+above)*(1.0+above)));
    stretch=vec2(width,(speed/47.5)/derivative);
    float scale=throat_texel_scale();
    vec2 scroll=vec2(.125,.0625*sand_jump_y)*sand_time;
    return vec2(.5,.31)+sand_advection(sand_clock,scroll)+scale*
          vec2((position.x-sand_geometry.x)/width,sand_throat.x-sand_geometry.z+height);
}
vec3 sand_site_seed(vec2 id) {
    vec3 p=fract(id.xyx*vec3(.1031,.1030,.0973));
    p+=dot(p,p.yxz+33.33);
    return fract((p.xxy+p.yzz)*p.zyx);
}
void sand_site(vec2 id,vec2 local,vec2 stretch,float layer,float layer_cost,float cells,
               inout vec4 front,inout vec4 next_site) {
    vec3 seed=sand_site_seed(mod(id,cells)+vec2(23.71,9.13)*layer);
    vec2 delta=(local-(.17+.66*seed.xy))*stretch;
    vec2 facet=vec2(delta.x+.21*(seed.z-.5)*delta.y,delta.y);
    float d2=dot(facet,facet);
    float radius=.36+.12*seed.z;
    // A front grain conceals deeper grains until acceleration opens a gap.
    // The distance is in physical grain units, so its footprint stays compact.
    float score=max(0.0,d2-radius*radius)+.16*d2+layer_cost*layer;
    vec2 pigment=(mod(id,cells)+.5+.43*facet)/cells
                 +vec2(.37,.19)*layer+seed.z*vec2(.21,.13);
    vec4 site=vec4(score,pigment,fract(seed.x+seed.y+seed.z));
    if (score<front.x) { next_site=front;front=site; }
    else if (score<next_site.x) next_site=site;
}
float flowing_sand_detail(vec2 coord,vec2 stretch,float pitch,out vec2 site_state) {
    float cells=max(8.0,floor(.5+1.0/(1.06*pitch*throat_texel_scale())));
    vec2 q=fract(coord)*cells;
    vec4 front=vec4(1.e4,0.0,0.0,0.0),next_site=front;
    float layer_cost=mix(.038,.018,smoothstep(3.0,12.0,stretch.y));
    for (int k=0;k<10;++k) {
        float layer=float(k);
        if (next_site.x<layer_cost*layer) break;
        vec2 base=q+fract(layer*vec2(.7548777,.5698403));
        vec2 p=base;
        p.y+=fract(mod(floor(p.x),cells)*.618034+layer*.381966);
        vec2 id=floor(p),local=fract(p);
        sand_site(id,local,stretch,layer,layer_cost,cells,front,next_site);
        if (stretch.y<.90 && abs(local.y-.5)>.24) {
            vec2 neighbour=vec2(0.0,sign(local.y-.5));
            sand_site(id+neighbour,local-neighbour,stretch,layer,layer_cost,cells,front,next_site);
        }
        if (stretch.y>1.6 || abs(local.x-.5)>.24) {
            float col=id.x+(local.x>=.5 ? 1.0 : -1.0);
            float row=base.y+fract(mod(col,cells)*.618034+layer*.381966);
            sand_site(vec2(col,floor(row)),vec2(base.x-col,fract(row)),
                      stretch,layer,layer_cost,cells,front,next_site);
        }
    }
    float edge=.5*(1.0-smoothstep(0.0,.016+.12/max(.8,pitch),next_site.x-front.x));
    vec2 tones=vec2(grain(front.yz),grain(next_site.yz));
    float visible=mix(tones.x,tones.y,edge);
    float contact=1.0-smoothstep(0.0,.07,next_site.x-front.x);
    float relief=(.38-contact)*min(.22,abs(tones.x-tones.y));
    site_state=vec2(smoothstep(.045,.15,front.x),front.w);
    // Related tones across a few neighbouring grains keep the converging
    // supply legible when the whole hourglass is displayed at tablet scale.
    float groups=max(3.0,floor(.34*cells));
    vec2 group=fract(coord)*groups;
    vec2 id=floor(group), f=smoothstep(vec2(0.0),vec2(1.0),fract(group));
    vec2 x0=mod(id,groups), x1=mod(id+1.0,groups);
    float packet_tone=mix(mix(sand_site_seed(x0).z,sand_site_seed(vec2(x1.x,x0.y)).z,f.x),
                          mix(sand_site_seed(vec2(x0.x,x1.y)).z,sand_site_seed(x1).z,f.x),f.y)-.5;
    // Neighbouring grains have related colours. As they separate, the
    // compact exposed faces carry more of the detail than that shared patch.
    float loosen=smoothstep(1.35,5.0,stretch.y);
    float related=packet_tone*min(.42,abs(tones.x)+abs(tones.y))*mix(.95,.35,loosen);
    return mix(.88,1.16,loosen)*visible+mix(.46,.22,loosen)*grain(coord)+relief+related;
}
float throat_rim_coverage(vec2 position,float width_scale,float gap) {
    float half_width=sand_throat.y, pitch=sand_throat.z;
    float depth=sand_throat.x-position.y;
    float release=smoothstep(-sand_release.x,.35*half_width,depth)
                 *(1.0-smoothstep(.12*half_width,1.45*half_width,depth));
    float wall_distance=abs(abs(position.x-sand_geometry.x)-half_width*width_scale);
    float rim=1.0-smoothstep(0.0,1.5*pitch,wall_distance);
    float resolved=smoothstep(2.4,6.0,half_width/pitch);
    // Inside the glass, deeper grains show through gaps in the front layer.
    // The same moving sites gradually expose the background past the lip.
    float exposed=smoothstep(-.35*pitch,.42*half_width,depth);
    return 1.0-mix(.10,1.0,exposed)*smoothstep(.43,.86,1.35*release*rim*gap*resolved);
}
float draining_grain_coverage(vec2 position,vec2 site,float feed) {
    if (feed>=1.0) return 1.0;
    float distance=abs(position.y-sand_throat.x);
    float span=max(4.0*sand_throat.z,sand_release.x);
    float inside=smoothstep(0.0,span,distance);
    // Several grains overlap along the line of sight. First the gaps between
    // their sites open, then individual centres remain as the supply ends.
    float vacant=1.0-feed;
    float projected=1.0-vacant*vacant;
    float density=mix(projected,1.0,inside);
    float rank=mix(.015,.985,site.y);
    return smoothstep(rank-.012,rank+.012,density-.30*(1.0-density)*site.x);
}
vec3 flowing_sand_colour(float detail, vec2 position) {
    vec2 uv=vec2(.5+(position.x-sand_geometry.x)/(2.0*sand_geometry.y),
                 (position.y-sand_geometry.z)/(2.0*sand_geometry.y));
    return grain_tone_color(1.48*detail,lighting(uv));
}
// 程序化平滑噪声(聚团场) —— 见 `sand_clump_gap` 那段。
// ⚠️ 哈希**不用 `sin`**(Dave Hoskins 那个无 sin 版本): 移动 GPU 上 `sin` 常是 mediump,
//    `sin(dot(p,...))*43758.0` 会出条纹/块状伪影。这个只用 `fract`/`dot`。
float clump_hash(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float clump_noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = clump_hash(i);
    float b = clump_hash(i + vec2(1.0, 0.0));
    float c = clump_hash(i + vec2(0.0, 1.0));
    float d = clump_hash(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
float clump_field(vec2 p, float warp) {
    // 扰动纵向坐标，打破等距横带。
    float w = clump_noise(p * vec2(0.35, 0.22) + vec2(21.7, 5.3)) - 0.5;
    vec2 q = vec2(p.x + 0.60 * warp * w, p.y + 2.20 * warp * w);
    // ⚠️ 两个八度的**权重**(0.80/0.20): 第二档只用来把边界打毛。
    //    原来 0.62/0.38 把场**压平**了(和的分布比单档更窄) ⇒ 阈值切出来是
    //    "一大片一大片的淡", 不是"一个个团"(实测: 整条柱子只剩 1~2 个特征)。
    return 0.80 * clump_noise(q)
         + 0.20 * clump_noise(q * 2.13 + vec2(7.7, 3.1));
}
"""

FRAGMENT_SHADER = GRAIN_FRAGMENT_HEADER + """
uniform vec4 sand_channel;   // throat / join radii, straight inlet / Bezier control heights (px)
uniform vec4 sand_reservoir; // surface level, dip, half-width (in diameters), equatorial Q/A relative to lip
uniform vec4 sand_transport; // bounded phase, lip travel per cycle, free-surface speed / lip, volume travel

// Return the projected bulk velocity in units of the lip speed. The section follows
// the same spherical wall and quadratic shoulder as the sand mask.
vec2 reservoir_velocity(vec2 position) {
    float radius = sand_geometry.y;
    float y = position.y;
    float wall = sand_channel.x;
    float slope = 0.0;
    if (y >= sand_geometry.w) {
        float h = clamp((y - sand_geometry.z) / radius, 0.0, 2.0);
        wall = radius * sqrt(max(.00001, h * (2.0 - h)));
        slope = max(0.0, (1.0 - h) * radius / wall);
    } else if (y > sand_channel.z) {
        float b = 2.0 * (sand_channel.w - sand_channel.z);
        float a = sand_geometry.w - sand_channel.z - b;
        float rise = y - sand_channel.z;
        float root = sqrt(max(0.0, b*b + 4.0*a*rise));
        float u = clamp(2.0 * rise / max(.001, b + root), 0.0, 1.0);
        float spread = sand_channel.y - sand_channel.x;
        wall += spread * u*u;
        slope = 2.0 * spread * u / max(.001, root);
    }
    float lane = clamp((position.x - sand_geometry.x) / max(1.0,wall), -1.0, 1.0);
    float neck = sand_channel.x / radius;
    float r = wall / radius;
    float area = clamp((r*r - neck*neck) / max(.001, 1.0 - neck*neck), 0.0, 1.0);
    // Bound the apparent Q/A as the neck narrows; the preset controls the mass flow,
    // the visible lip stays continuous with the first free grains even on a 1 s timer.
    float rate = sand_reservoir.w;
    float wide = max(.00001, rate / sqrt(1.0 + rate*rate));
    float mean = wide / (wide + (1.0 - wide) * area);
    // An hours-long timer can supply almost no visible bulk motion. Bring the
    // moving grains into the tube over the lower shoulder instead of its last pixel.
    float approach = 1.0 - smoothstep(0.0, .45, (y - sand_channel.z) / radius);
    mean = mix(mean, 1.0, approach*approach);
    float slip = mix(1.0 - .09*lane*lane, 1.0 + .22*(1.0-2.0*lane*lane),
                     smoothstep(0.0, .32, area));
    float vertical = mean * slip;
    return -vertical * vec2(lane * slope / (1.0 + .65*slope), 1.0);
}

void main(void) {
    // 上球、管内与自由段的材质在出口邻域连续混合。
    float diameter = max(2.0, sand_geometry.y * 2.0);
    vec2 uv_ball = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                        (sand_position.y - sand_geometry.z) / diameter);
    vec2 uv_neck = vec2(uv_ball.x, sand_neck_anchor - uv_ball.y);
    // ⚠️ 带宽是**球直径的比例**不是像素 —— 桌面 400×800(直径≈196) 与平板 1904×2890
    //    (直径≈1132) 差 5.8 倍, 写死像素会在平板上缩得看不见。
    float seam_b = sand_seam_band * diameter;
    float seam_w = (seam_b > 0.0)
        ? smoothstep(-seam_b, seam_b, sand_position.y - sand_geometry.z)
        : step(0.0, sand_position.y - sand_geometry.z);
    vec4 original = mix(texture2D(texture0, fract(uv_neck)),
                        texture2D(texture0, uv_ball), seam_w);
    vec2 uv = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                   (sand_position.y - sand_geometry.z) / diameter);
    vec2 from_mouth = vec2((sand_position.x - sand_geometry.x) / diameter,
                          max(0.0, sand_position.y - sand_geometry.w) / diameter);
    float sink = 1.0 / (1.0 + 20.0 * from_mouth.x * from_mouth.x
                           + 8.0 * from_mouth.y * from_mouth.y);
    vec2 velocity = vec2(-(sand_position.x - sand_geometry.x) * 0.09 * sink,
                         -(4.0 + 46.0 * sink)) / diameter;
    float skin = 0.0;
    float quiet = 0.0;
    float surface = 1.0;
    bool reservoir_flow = sand_channel.x > 0.0 && sand_up_scroll < 0.0
                       && (sand_free.x <= 0.0 || sand_position.y >= sand_free.x);
    if (reservoir_flow) {
        vec2 bulk = reservoir_velocity(sand_position);
        float lane = (uv.x - .5) / max(.0001, sand_reservoir.z);
        surface = sand_reservoir.x - sand_reservoir.y * max(0.0, 1.0 - lane*lane);
        float below = max(0.0, surface - uv.y);
        float in_bowl = smoothstep(0.0, .18, (sand_position.y - sand_channel.z) / diameter);
        float settling = in_bowl * (1.0 - smoothstep(.035, .46, below));
        velocity = mix(bulk, vec2(0.0, -min(5.0, sand_transport.z)), settling) * sand_transport.y;
        // Match velocities across the blend as well as the material itself.
        // Rapidly descending surfaces carry a deeper layer with them. Keep
        // its texture attached to the actual level, then blend into the funnel.
        // Distance discharged per trace period, in cup diameters, independent of lip speed.
        float fast_surface = smoothstep(.2514, .6285, sand_reservoir.w * sand_transport.y);
        float skin_start = mix(.025, .12, fast_surface);
        float skin_end = mix(.11, .25, fast_surface);
        skin = smoothstep(.70,1.0,in_bowl) * (1.0 - smoothstep(skin_start, skin_end, below));
        quiet = 1.0 - smoothstep(.02, .12, -velocity.y / sand_transport.y);
    }
    // 🔴 2026-10-10 (B-①): 出口以下改按**深度**给漂移速度(见 `sand_free_drift` 的注释)。
    //    必须在下面采 `grain()` **之前**改 —— 那两行就是拿 `velocity` 去偏移采样坐标的。
    float clump = 0.5;        // 聚团场 0..1(只有出口以下才有; 0.5 = 中性)
    float clump_g = 0.0;      // 聚团的"深度渐入"权重(0 = 出口处, 不生效)
    float clump_slow = 0.5;   // 更慢的一层: "这一段的缝有多深"(0.5 = 中性)
    vec2 cl_dbg = vec2(0.0);  // 只给 uvdebug 用: 把聚团坐标本身读出来
    // 供下方的颜色、边缘与孔隙共同取样。
    float mat_y = uv.y;       // 出口以下会被换成材料坐标
    float free_here = 0.0;
    if (sand_free.x > 0.0 && sand_position.y < sand_free.x) {
        float depth_px = sand_free.x - sand_position.y;
        // 自由段滚动按下落方向积累位移。
        if (sand_free_vscroll > 0.0) {
            velocity.y = -sand_free_vscroll / diameter;
        } else {
        float ms = max(0.25, sand_motion_scale);
        float v0p = V0_FALL * ms;              // 与粒子同倍率(main.py: `60.0*ms`)
        float gp = G_FALL * ms * ms;           // (main.py: `450.0*ms*ms`)
        float vcap_p = sand_free_vcap * ms;
        float dcap = max(0.0, (vcap_p * vcap_p - v0p * v0p) / (2.0 * gp));
        float dp = min(depth_px, dcap);
        float tau_fall = (sqrt(v0p * v0p + 2.0 * gp * dp) - v0p) / gp
                       + max(0.0, depth_px - dcap) / max(1.0, vcap_p);
        mat_y = (sand_free.x - sand_geometry.z) / diameter
              + sand_free_vref * (tau_fall - sand_time) / diameter;
        free_here = 1.0;
        velocity.y = 0.0;      // y 已由材料坐标接管, 不许再叠一次平流
        // 聚团: 低频、沿材料坐标(⇒ 跟沙走 + 被流动拉长)、按深度渐强。
        // (声明在**外层**, 见上面那两行 —— 这里是赋值, 不是定义。)
        clump_g = clamp(((depth_px / diameter) - sand_clump_start) * sand_clump_gain,
                        0.0, 1.0);
        // 横坐标按柱半宽归一，纵坐标使用随沙流移动的相位。
        float ms_c = max(0.05, sand_motion_scale);
        float cl_w = max(1.0, sand_free.z);
        vec2 cl_p = vec2((sand_position.x - sand_geometry.x) / cl_w * sand_clump_xscale,
                         mat_y * (diameter / cl_w) * sand_clump_scale * ms_c);
        cl_dbg = cl_p;
        clump = clamp(clump_field(cl_p, sand_clump_warp), 0.0, 1.0);
        // 低频噪声调制聚团尺度。
        clump_slow = clamp(clump_noise(cl_p * vec2(0.45, 0.30) + vec2(41.3, 17.9)),
                           0.0, 1.0);
        }
    }
    if (sand_flow_vy != 1.0) {
        velocity.y *= sand_flow_vy;
    }
    float jitter = 0.2 * sin(from_mouth.x * 3.1 + from_mouth.y * 5.2);
    // 上球的滚动模式由 sand_up_scroll 选择。
    float free_s = (sand_free.x > 0.0 && sand_position.y < sand_free.x) ? 1.0 : 0.0;
    float up_static = (sand_adv_cont > 0.0 && sand_up_scroll == 0.0
                       && free_s < 0.5) ? 1.0 : 0.0;
    float t = (up_static > 0.5 ? 0.0 : sand_clock) + jitter;
    if (reservoir_flow) t = sand_transport.x + jitter;
    float a = fract(t);
    float b = fract(t + 0.5);
    // 平流的时间项保持连续。
    float adv = (sand_adv_cont > 0.0
                 && sand_free.x > 0.0 && sand_position.y < sand_free.x) ? sand_clock : a;
    // 连续相位使用全局时钟，避免空间抖动形成接缝。
    float sec = (sand_adv_cont > 0.0
                 && sand_free.x > 0.0 && sand_position.y < sand_free.x) ? 0.0 : 1.0;
    float delta = luma(texture2D(texture0, fract(uv)).rgb) - luma(sand_base);
    float wa = 1.0 - abs(1.0 - 2.0 * a);
    float wb = 1.0 - wa;
    vec2 jump = vec2(0.125, 0.0625 * sand_jump_y);
    float up_scroll = (sand_adv_cont > 0.0 && sand_up_scroll > 0.0
                       && free_s < 0.5) ? 1.0 : 0.0;
    // 自由段(sec=0)把整个采样参数乘 `sand_free_coarse` —— **位置和平流一起乘**,
    // 所以屏幕上的速度不变、只把纹理放大(颗粒变粗)。其余区域系数恒 1 ⇒ 逐字旧行为。
    float ck = (sec < 0.5) ? sand_free_coarse : 1.0;
    vec2 samp_a = uv - velocity * adv;
    // 两个采样相位相隔半个周期。
    float b_off = (sec < 0.5) ? adv : b;
    vec2 samp_b = uv - velocity * b_off;
    // Centred, bounded traces keep grain sites from stretching at strong gradients.
    if (reservoir_flow) {
        samp_a += .5 * velocity;
        samp_b += .5 * velocity;
    }
    if (free_here > 0.5) {         // 自由段: y 交给材料坐标(上面算好的 `mat_y`)
        samp_a.y = mat_y;
        samp_b.y = mat_y;          // 自由段 `sec==0` ⇒ 两相位本来就同式
    } else if (up_scroll > 0.5) {  // 上球/颈部: 刚性连续下滚(A/B 用, 2.88 那条)
        // ⚠️ 这一支与上面的"冻钟"互斥(`sand_up_scroll` 一个值只可能命中一个)
        samp_a = uv + jump * sand_time;
        samp_b = samp_a;
    }
    // 🔴 **2026-10-12(2.90): 上球按沙体伸缩**(1.244 的 tex_coords 行为) —— 见 `sand_up_scale` 的声明。
    //    只缩放**纵向**采样坐标(沙体矮 ⇒ 采样被压缩 ⇒ 图案朝颈口方向流), 不引入任何钟。
    if (up_static > 0.5 && sand_up_scale > 1.0) {
        samp_a.y *= sand_up_scale;
        samp_b.y *= sand_up_scale;
    }
    float ga=0.0,gb=0.0;
    bool common_start = false;
    if ((sand_throat.y<=0.0 && skin<1.0 && quiet<1.0) || sand_uv_debug>.5) {
        vec2 site_a = samp_a * ck + floor(t) * jump * sec * (1.0 - up_scroll);
        vec2 site_b = samp_b * ck + floor(t + 0.5) * jump * sec + vec2(0.5);
        // Phases already alive when Start was pressed share the original still
        // grain sites. Each adopts its normal trace when its weight next reaches zero.
        if (reservoir_flow && sand_time < 2.0) {
            bool first_a = floor(t) == floor(jitter);
            bool first_b = floor(t + .5) == floor(jitter + .5);
            vec2 start_site = uv - velocity * sand_transport.x;
            if (first_a) site_a = start_site;
            if (first_b) site_b = start_site;
            common_start = first_a && first_b;
        }
        ga=grain(site_a);
        gb=grain(site_b);
    }
    float detail = (sec < 0.5)
        ? ga * sand_free_grain
        : ((up_scroll > 0.5) ? ga
           : (ga * wa + gb * wb) * inversesqrt(wa * wa + wb * wb));
    if (common_start) detail = ga;
    if (quiet > 0.0 && skin < 1.0) {
        float slow_detail = grain(vec2(uv.x, uv.y + sand_transport.w));
        detail = mix(detail, slow_detail, quiet) * inversesqrt(quiet*quiet + (1.0-quiet)*(1.0-quiet));
    }
    if (skin > 0.0) {
        float surface_detail = grain(vec2(uv.x, uv.y + (1.0 - surface)));
        detail = mix(detail, surface_detail, skin) * inversesqrt(skin*skin + (1.0-skin)*(1.0-skin));
    }
    // ⚠️ 聚团**不能加在 `detail` 上**: `tone = clamp(lighting + detail, -1, 1)` 早就饱和,
    //    加进去会被**裁掉**(实测 amp=0.18 只改 2 级、amp=2.0 也只 20 级且看不出形状)。
    //    ⇒ 它按物理该是**密度**变化: 改成去调制 `coverage`(见下面自由段那一处)。
    vec2 luv = (sand_wrap_light > 0.0) ? vec2(uv.x, fract(uv.y)) : uv;
    float tone = clamp(lighting(luv) + detail, -1.0, 1.0);
    vec3 target = tone >= 0.0 ? sand_light : sand_dark;
    vec3 flowing = grain_tone_color(detail, lighting(luv));
    float coverage = 1.0;
    if (sand_tail.y > 0.0) {
        float u = 0.5 + (sand_position.x - sand_geometry.x) / (2.0 * sand_tail.z);
        // The centre drains first. The shallow bowl and the mirrored grain
        // noise both average to zero across the width of the free surface.
        float left = grain(vec2(u, sand_clock * 0.25 + 0.37));
        float right = grain(vec2(1.0 - u, sand_clock * 0.25 + 0.37));
        float radial=clamp(2.0*u-1.0,-1.0,1.0);
        float top = sand_tail.x + sand_tail.y * clamp(.90*(radial*radial-1.0/3.0)
                                                     +.65*(left-right),-1.0,1.0);
        coverage = 1.0 - smoothstep(top - 0.5, top + 0.5, sand_position.y);
    }
    float tail_coverage = coverage;
    if (sand_free.x > 0.0 && sand_position.y < sand_free.x) {
        float u = 0.5 + (sand_position.x - sand_geometry.x) / (2.0 * sand_free.z);
        // 边缘噪声沿纵向平流。
        vec2 euv = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                        (sand_free.x - sand_position.y) / diameter);
        vec2 evel = vec2(velocity.x, -velocity.y);
        vec2 ejmp = vec2(jump.x, -jump.y);
        // ⚠️ 自由段 `sec=0` ⇒ 两条边都走 `adv`(**同速**)。原来 n2 用的是 `b = fract(t+0.5)`
        //    —— 那是**另一条时间轴**, 净位移恒为 0 ⇒ **右边不动、左边在动**, 两条边会错开。
        float adv2 = (sec < 0.5) ? adv : b;
        float n = grain(euv - evel * adv + floor(t) * ejmp * sec);
        float n2 = grain(vec2(1.0 - euv.x, euv.y) - evel * adv2
                         + floor(t + 0.5) * ejmp * sec + vec2(0.37, 0.11));
        // 边缘坐标向下为正，与屏幕纵轴相反。
        float r01 = abs(u - 0.5) * 2.0;          // 0=中轴, 1=两条边
        float rim = smoothstep(sand_core, 1.0, r01);
        // 出口处分散程度由零渐入。
        float fr = 1.0;
        if (sand_free_ramp > 0.0) {
            fr = clamp((sand_free.x - sand_position.y) / (sand_free_ramp * diameter),
                       0.0, 1.0);
        }
        // 🔴 **2026-10-10 用户备注: 「可以让随机幅度更大一些, 这个是可以的。
        //    否则就是一个矩形/梯形了」** ⇒ 打散深度从 `0.25+0.75n` 放宽到
        //    `sand_bite_min + (1-它)*n`, 且整条再乘 `sand_edge`。
        float nz = clamp(u < 0.5 ? n : n2, 0.0, 1.0);
        // 轮廓和覆盖率共用团间孔隙的权重。
        float clump_open = (1.0 - smoothstep(sand_clump_lo, sand_clump_hi, clump)) * clump_g
                         * clamp((clump_slow - 0.30) * 2.2, 0.0, 1.0);
        clump_open = clamp(clump_open, 0.0, 1.0);
        float clump_edge_open = clump_open * clamp(1.0 - sand_clump_gap, 0.0, 1.0);
        // 🔴 可见边缘: 由这一行自己的噪声决定"沙到多宽为止"(见上面 `sand_edge_lo/hi`)
        if (sand_edge_hi > 0.0) {
            float cut = mix(sand_edge_lo, sand_edge_hi, 0.5 + 0.5 * nz);
            // 🔴 **缝里同时把轮廓收窄** —— 只降透明度的话, 柱子**外轮廓还是一条直边**,
            //    画面上读成"实心柱上开了几个洞", 不是"一团一团的沙"。
            //    收窄之后缝是**真的断开**(背景从两边一起进来)。
            cut *= mix(1.0, sand_clump_edge, clump_edge_open);
            coverage *= 1.0 - smoothstep(cut - 0.07, cut + 0.07, r01);
        }
        coverage *= 1.0 - rim * (sand_bite + (1.0 - sand_bite) * nz)
                          * sand_edge * sand_free.y * fr;
        // 🔴 核心不再 100% 不透明 —— 否则同色全不透明的它会把**粒子层完全吃掉**
        //    (速度拖尾/高光档全都看不见) ⇒ "颗粒在动"这个信号被自己盖住。
        coverage = min(coverage, sand_alpha);
        // 沙团间隙透出背景。
        coverage *= mix(1.0, sand_clump_gap, clump_open);
        // 🔴 **2026-10-10 (A3): 二值洞 —— 必须在上面那两行之后**, 因为它要把结果
        //    压成 {0, 1}(保留区**全不透**), 而不是叠一层灰。
        if (sand_hole_th >= 0.0) {
            // 孔隙场随颗粒向下移动。
            vec2 huv = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                            (sand_free.x - sand_position.y) / diameter);
            // ⚠️ `huv.y` 同样向下为正 ⇒ 用翻过号的 `evel`/`ejmp`(见上面 `euv` 那段实测)。
            float hn = grain(huv - evel * adv + floor(t) * ejmp * sec);
            // 孔隙随深度渐入，保持出口处连续。
            float depth01 = (sand_free.x - sand_position.y) / diameter;
            // 孔隙率随深度升高: 阈值越大洞越多(`step(th,hn)` 是"hn >= th 才保留沙")。
            // 用 `sqrt` 而不是线性 —— 与"落速 ∝ √深度"同族, 也让近口那段不要涨太快。
            float th = sand_hole_th
                     + sand_hole_grow * sqrt(max(0.0, depth01 - sand_hole_ramp));
            float solid = step(th, hn);                  // 1 = 保留沙, 0 = 挖掉
            float ramp_px = sand_hole_ramp * diameter;
            if (ramp_px > 0.0) {
                // 阈值线性渐入那一版实测**没用**: `hn` 的分布在 0 附近有尖峰
                // (阈值 −0.05 → 0% 洞, 0.00 → 20% 洞), 阈值一降洞就整片冒出来,
                // 渐入被压进 ~30px、看上去仍是一条线。
                float ramp = clamp((sand_free.x - sand_position.y) / ramp_px, 0.0, 1.0);
                float gate = clamp(grain(huv * 0.37 + vec2(19.3, 7.1)) * 0.5 + 0.5,
                                   0.0, 1.0);
                float allow = step(1.0 - ramp, gate);    // 1 = 这一片允许挖
                solid = mix(1.0, solid, allow);
            }
            // 孔隙在已散开的边缘区域逐渐出现。
            float wide = clamp(sand_hole_wide
                               * sqrt(max(0.0, depth01 - HOLE_WIDE_START)), 0.0, 1.0);
            coverage *= mix(1.0, solid, clamp(max(rim * fr, wide * fr), 0.0, 1.0));
        }
    }
    bool outlet_join = sand_outlet_join.x > 0.0 && sand_outlet_join.y > 0.0
                      && sand_position.y < sand_outlet_join.x;
    if (outlet_join) {
        // Retire just the existing overlap, with the upstream colour intact.
        // The particle alpha stays unchanged: two complementary fades would
        // expose a transparent trough under normal source-over compositing.
        coverage = tail_coverage * (1.0-smoothstep(0.0,sand_outlet_join.y,
                                      sand_outlet_join.x-sand_position.y));
    }
    if (sand_free.w > 0.0 && sand_position.y < sand_free.w) {
        // 前沿(在途沙的最低点): 不许切成一刀平。1~5px 的零均值锯齿。
        float l2 = grain(vec2(sand_position.x * 0.35, sand_clock * 0.22 + 0.11));
        coverage *= smoothstep(0.0, 1.0,
                               clamp((sand_free.w - sand_position.y) / (1.0 + 4.0 * l2),
                                     0.0, 1.0));
    }
    // 调试通道(`uvdebug` 标记文件): 1=uv  2=tone(正为红/负为绿, 幅度=|tone|)
    //                           3=coverage  4=detail(颗粒本身)
    if (sand_uv_debug > 0.5) {
        float m = sand_uv_debug;
        if (m < 1.5) {
            gl_FragColor = vec4(clamp(uv, 0.0, 1.0), 0.0, 1.0);
        } else if (m < 2.5) {
            gl_FragColor = vec4(max(tone, 0.0), max(-tone, 0.0), 0.0, 1.0);
        } else if (m < 3.5) {
            gl_FragColor = vec4(coverage, coverage, coverage, 1.0);
        } else if (m < 4.5) {
            gl_FragColor = vec4(max(detail, 0.0), max(-detail, 0.0), 0.0, 1.0);
        } else if (m < 4.75) {                // R=聚团场 G=慢层(缝深) B=深度渐入
            gl_FragColor = vec4(clump, clump_slow, clump_g, 1.0);
        } else if (m < 4.9) {                 // 聚团坐标 ÷64(绿, 每 64 单位的色循环) + mat_y ÷8(红)
            gl_FragColor = vec4(clamp((cl_dbg.y + 512.0) / 1024.0, 0.0, 1.0),
                                fract(cl_dbg.y / 64.0), clamp(mat_y / 8.0 + 0.5, 0.0, 1.0), 1.0);
        } else if (m < 5.5) {                 // ga: crossfade 之前那一次 grain 采样
            gl_FragColor = vec4(max(ga, 0.0), max(-ga, 0.0), 0.0, 1.0);
        } else if (m < 6.5) {                 // delta: 纹理亮度 − sand_base 亮度(未归一)
            gl_FragColor = vec4(max(delta, 0.0) * 4.0, max(-delta, 0.0) * 4.0, 0.0, 1.0);
        } else if (m < 7.5) {                 // wa: crossfade 权重
            gl_FragColor = vec4(wa, wb, 0.0, 1.0);
        } else if (m < 8.5) {                 // sand_position 本体(x/800, y/800)
            gl_FragColor = vec4(sand_position.x / 800.0, sand_position.y / 800.0,
                                0.0, 1.0);
        } else if (m < 8.5) {                 // 同一批顶点里的 vTexCoords0
            gl_FragColor = vec4(tex_coord0.x, tex_coord0.y, 0.0, 1.0);
        } else if (m < 10.5) {
            // 🔴 **2026-10-10: 把 `-velocity.y` 直接画成红** —— 用来回答
            //    "自由段那条 `sand_free_drift` 到底有没有接到采样上"。
            //    0=黑(几乎不动), 越红=平流越快。两臂(`HG_FREE_DRIFT` 0 vs 8)一比就知道。
            gl_FragColor = vec4(clamp(-velocity.y * 0.4, 0.0, 1.0), 0.0, 0.0, 1.0);
        } else if (m < 12.5) {
            // 🔴 **着色器自己看到的 `sand_mix`**(灰阶) —— 判"Python 侧写了但没到 GPU"
            //    只能问着色器: `RenderContext.__getitem__` 读的是**Python 侧缓存**,
            //    它显示 1.0 完全不代表 GPU 拿到了(2026-10-12 就是这么栽的)。
            gl_FragColor = vec4(sand_mix, sand_mix, sand_mix, 1.0);
        } else {                              // 这个像素属于哪个 context
            gl_FragColor = vec4(sand_ctx_tag / 4.0, 0.0, 0.0, 1.0);
        }
        return;
    }
    vec3 final_rgb;
    if (sand_throat.y > 0.0) {
        vec2 stretch;
        vec2 coord=sand_coordinates(sand_position,stretch);
        vec2 site;
        float pigment=flowing_sand_detail(coord,stretch,sand_throat.z,site);
        float material_mix=sand_mix_pin>=0.0 ? sand_mix : 1.0;
        coverage*=mix(1.0,throat_rim_coverage(sand_position,stretch.x,site.x),material_mix);
        final_rgb=mix(original.rgb,flowing_sand_colour(pigment,sand_position),material_mix);
        coverage*=draining_grain_coverage(sand_position,site,sand_neck_feed);
        if (sand_neck_fill < 1.0) {
            float last_grain=.50+.38*clamp(2.5*grain(coord),-1.0,1.0);
            coverage*=smoothstep(last_grain-.12,last_grain+.12,sand_neck_fill);
        }
    } else {
        final_rgb=mix(original.rgb,flowing,sand_mix);
    }
    gl_FragColor = frag_color * vec4(final_rgb,
                                     original.a * coverage);
}
"""


def _flag_float(name, fname):
    return float_option(os.path.dirname(os.path.abspath(__file__)), name, fname)


# ---------------------------------------------------------------- 上球材质: 走哪一支
# `sand_up_scroll`: **−1 = 钟在跑(出货默认, = 1.244 的路)** · 0 = 冻钟(A/B) · >0 = 刚性下滚(A/B)。
# `main.py` 要据此决定"要不要算 `sand_up_scale` 那一项", 所以这一对必须**与旋钮表同源**。
UP_SCROLL_DEFAULT = -1.0


def up_scroll_mode():
    """`sand_up_scroll` 的**生效值**(env/标记 > 档位 > 默认) —— 给 `main.py` 用。

    ⚠️ 优先级链必须与 `SandFlowContext.__init__` 里那一轮赋值**逐字一致**, 否则会出现
    "Python 侧以为走了 A 支、着色器拿到的是 B 支" —— 本项目在 `sand_mix` 上栽过一模一样的坑
    (2026-10-12: 整整 11 个版本那层 GPU 颗粒没接上, 而所有静态闸门都是绿的)。
    """
    v = _flag_float("HG_UP_SCROLL", "upscroll")
    if v is None:
        v = _level_override("sand_up_scroll")
    return float(UP_SCROLL_DEFAULT if v is None else v)


# ---------------------------------------------------------------- 设置项: 沙流断续
# 🔴 **2026-10-11 加(用户要求「把这个搞个参数, 写入设定」)** —— 档位表**只覆盖少数几个旋钮**,
#    其余全部走内置默认。三档的数值**是量出来的, 不是外推的**(量具 `tools/_probe_gap_structure.py`,
#    判据 = 缝的面积占比 / 独立洞数 / 单行最大开口 / 跨宽断开行数, 自带正负对照):
#
#      档位        缝面积占比(50s: t=8.8 / t=21.2)   独立洞数   跨宽断开行
#      实柱        0% / 0%                          0          0        (= 2.64 逐像素一致)
#      自然(出厂)  2.4% / 5.0%                       8 / 10     0 / 8    (= 2.69 的九元组)
#      断续明显    7.1% / 10.8%                     10 / 12     0 / 23
#
#    ⚠️ 被用户否掉的 2.68 那套(`lo/hi = 0.42/0.54` + 强度地板 0.35)实测 **24~32%** —— 那是
#      "整条柱均匀地花", 三档里**没有**它, 也不许把它当成"更断"的档补进来。
#    ⚠️ 档位存**标签**(不是序号), 以后重调数值不会让旧配置串到别的档 —— 不需要 rev 机制。
CLUMP_LEVELS = (
    ("实柱", {"sand_clump_gap": 1.0}),
    ("自然", {}),                       # 空 = 出厂九元组(2.69)
    ("断续明显", {"sand_clump_gap": 0.12, "sand_clump_lo": 0.36, "sand_clump_hi": 0.46}),
)
CLUMP_LEVEL_DEFAULT = "自然"
_clump_level = CLUMP_LEVEL_DEFAULT
_level_table = {}

# 🔴 **聚团这 9 个旋钮的默认值提到模块级** —— 单一真值源: 旋钮表与"档位"共用它。
#    起因(2026-10-11, 设置项自检抓到的真 bug): 切档时若只写"该档覆盖的那几项",
#    从「断续明显」切回「自然」(空覆盖表)就**什么都不写** ⇒ uniform 残留上一档的值
#    (`gap` 还停在 1.0、`lo` 还停在 0.36)。修法 = 切档写**生效的整组值**,
#    所以默认值必须能在不构造 context 的情况下取到。
_CLUMP_KNOBS = (
    ("sand_clump_gap", "HG_CLUMP_GAP", "clumpgap", 0.15),
    ("sand_clump_scale", "HG_CLUMP_SCALE", "clumpscale", 2.5),
    ("sand_clump_xscale", "HG_CLUMP_XSCALE", "clumpxscale", 2.6),
    ("sand_clump_warp", "HG_CLUMP_WARP", "clumpwarp", 0.0),
    ("sand_clump_edge", "HG_CLUMP_EDGE", "clumpedgeshrink", 0.85),
    ("sand_clump_lo", "HG_CLUMP_LO", "clumplo", 0.25),
    ("sand_clump_hi", "HG_CLUMP_HI", "clumphi", 0.35),
    ("sand_clump_start", "HG_CLUMP_START", "clumpstart", 0.12),
    ("sand_clump_gain", "HG_CLUMP_GAIN", "clumpgain", 8.0),
)


def _level_override(key):
    """档位表给这个 uniform 的值(没有 ⇒ None ⇒ 走内置默认)。"""
    return _level_table.get(key)


def effective_clump_knobs(label=None):
    """**切档时要写进 shader 的整组值**: 档位覆盖 > 内置默认。

    ⚠️ 不许只写"该档覆盖的那几项" —— 见 `_CLUMP_KNOBS` 那段(bug 记录)。
    ⚠️ 也不许在这里读环境变量/标记文件: 那是**测量臂**的通路, 与用户档位是两条线
       (`_flag_float` 的优先级: env/标记 > 档位 > 默认; 这里只负责后两段)。
    """
    table = dict(CLUMP_LEVELS)
    if label is None:
        label = _clump_level
    ov = dict(table.get(label, table[CLUMP_LEVEL_DEFAULT]))
    return {name: float(ov.get(name, dflt)) for name, _env, _fn, dflt in _CLUMP_KNOBS}


def live_clump_knobs(label=None):
    """切档时**真正写进 live uniform** 的那些 `(name, value)`。

    🔴 **2026-10-11: 有环境变量/标记文件覆盖的旋钮必须跳过。**
    独立验证查出的**运行期洞**: 构造期的优先级是对的(`_flag_float` 压过档位), 但开发者菜单
    点档时直接把这组值写进 live uniform ⇒ **在设备上点一下档位就盖掉了正在跑的标记臂**
    (直到下次重建画布)。⇒ 这里逐旋钮看一遍 `_flag_float`, 有覆盖就不碰它。
    """
    eff = effective_clump_knobs(label)
    out = []
    for name, env, fname, _dflt in _CLUMP_KNOBS:
        if _flag_float(env, fname) is not None:
            continue
        out.append((name, eff[name]))
    return tuple(out)


def set_clump_level(label):
    """设置档位。返回 **真正生效的覆盖表**(未知标签 ⇒ 回落出厂档)。"""
    global _clump_level, _level_table
    table = dict(CLUMP_LEVELS)
    if label not in table:
        label = CLUMP_LEVEL_DEFAULT
    _clump_level = label
    _level_table = dict(table[label])
    return _level_table


def current_clump_level():
    return _clump_level


def clump_level_labels():
    return tuple(lb for lb, _ in CLUMP_LEVELS)


class SandFlowContext(RenderContext):
    def __init__(self, geometry, vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER,
                 tag=0.0):
        super().__init__(use_parent_projection=True, use_parent_modelview=True)
        self["sand_ctx_tag"] = float(tag)
        self.shader.vs = vertex_shader
        self.shader.fs = fragment_shader
        if not self.shader.success:
            raise RuntimeError("sand flow shader failed to compile")
        self["sand_geometry"] = tuple(map(float, geometry))
        self["sand_channel"] = (0.0, 0.0, 0.0, 0.0)
        self["sand_reservoir"] = (1.0, 0.0, 0.0, 0.0)
        self["sand_transport"] = (0.0, 1.0, 0.0, 0.0)
        self["sand_clock"] = 0.0
        self["sand_mix"] = 0.0
        self["sand_tail"] = (0.0, 0.0, 1.0)
        self["sand_free"] = (0.0, 0.0, 1.0, 0.0)
        self["sand_outlet_join"] = (0.0, 0.0)
        self["sand_throat"] = (0.0, 0.0, 1.0, 0.0)
        self["sand_funnel"] = (0.0, 0.0)
        self["sand_release"] = (0.0, 47.5, 0.0)
        self["sand_neck_fill"] = 1.0
        self["sand_neck_feed"] = 1.0
        _sb = _flag_float("HG_SEAM_BAND", "seamband")
        self["sand_seam_band"] = 0.0 if _sb is None else float(_sb)
        _sa = _flag_float("HG_SEAM_ANCHOR", "seamanchor")
        self["sand_neck_anchor"] = 0.021 if _sa is None else float(_sa)
        # ---- 出口以下那股沙的**密度场**(2026-10-10) ----------------------------
        # 设备上单变量对照走**标记文件**(环境变量到不了安卓), 与 `shrinkmin`/`twoimpl`
        # /`seamband` 同一套:
        #     adb shell "echo 0.25 > <app>/sandcore"     # 实心核占半宽的比例
        #     adb shell "echo 0.10 > <app>/sandbite"     # 打散深度下限(越小→随机越大)
        #     adb shell "echo 0.93 > <app>/sandalpha"    # 覆盖率上限
        #     删掉文件 = 回出厂默认
        for _key, _env, _fname, _dflt in (                # 🔴 **2026-10-10 三次裁定: 0.35 → 0.85 → **0.6**。**
                #    0.85 是压"白毛"时定的, 但它把打散压到**外侧只有 7.5% 半宽**
                #    (20px 的柱子上不到 1 像素) ⇒ 差**亚像素** ⇒ 边场等于没做。
                #    用户随后判词:「沙柱就只有一个问题了, **边缘是个竖线 太规整了**,
                #    和现实中的差异太大」。0.6 让外侧 40% 半宽参与 ⇒ 边缘可见地起伏。
                #    白毛的真因是**没有渐入**(`sand_free_ramp`), 那个已经修好了。
                ("sand_core", "HG_SAND_CORE", "sandcore", 0.6),
                ("sand_edge", "HG_SAND_EDGE", "sandedgemul", 1.0),
                ("sand_bite", "HG_SAND_BITE", "sandbite", 0.15),
                ("sand_alpha", "HG_SAND_ALPHA", "sandalpha", 0.93),
                # 出口以下的漂移速度系数: 0 = 关(= 逐字等于旧行为), 1.0 = 完全跟自由落体
                # ⚠️ **2026-10-11: 试过 0.0 → 1.0, 又退回 0.0 —— 它是错的解药。**
                #    用户的诉求(「沙柱下落的时候速度应该几乎一样…有的沙子速度非常慢」)
                #    指向这一条, 但**实测否决**: K 扫描(清粒子, dt=0.004, 行1290):
                #        K=0.05 期望 +26 → 实测 **0**
                #        K=0.4  期望+206 → 实测 **−250**(z 82)
                #        K=1.0  期望+514 → 实测 **−250**(z 40)
                #    `−250px/s` 正是探针在 dt=0.004 下的**最小量子(1px/帧)**,
                #    且**与 K 无关** ⇒ 这是**周期性纹理的车轮效应**: 材质纹理特征尺度只有
                #    ~3px, 8px/帧时任何速度都读不出来。**⇒ 把底纹加速不是解药,
                #    它只会变成频闪/倒转。** 快的运动只能靠**模糊**(粒子拖尾)表达。
                ("sand_free_drift", "HG_FREE_DRIFT", "freedrift", 0.0),
                ("sand_free_vscroll", "HG_FREE_VSCROLL", "freevscroll", 0.0),
                ("sand_free_vref", "HG_FREE_VREF", "freevref", 400.0),
                # 🔴 **2026-10-11 对抗性审查裁决(跨模型 2 专家 × 3 轮, 主张 5 判为"已证伪"):**
                #    封顶 600 让**材质与粒子在 d>396px 之后不同速** —— 模拟器差 22%、**平板差 41%**,
                #    正是用户最早抱怨的"两个速度差异大的层"; 而我当时的验证点(d=19/65/105)**全在
                #    封顶线以内** ⇒ 结构性看不见。⇒ 抬到 **1200**(平板柱底实测 1023px/s ⇒ 永不生效),
                #    保留成"高于物理速度的保险丝"而不是"限速器"。
                ("sand_free_vcap", "HG_FREE_VCAP", "freevcap", 1200.0),
                # 🔴 **聚团那 9 个旋钮在模块级 `_CLUMP_KNOBS`**(单一真值源: 旋钮表与
                #    "设置项档位"共用它 —— 档位切回默认档时要写回整组值)。
                *_CLUMP_KNOBS,
                # ⚠️ (上面 `gain` 的口径) 增益要**快饱和**(8.0 ⇒ 出口以下 ~290px 就到满值):
                #    原来 2.0 让它**随深度一直涨**, 正好把 2.62 的"越往下越透"**对冲掉**
                #    (实测梯度被压平) ⇒ 只留"出口附近渐入"。
                # 二值洞的阈值: <0 = 关(逐字旧行为); **越大洞越多**(step(th,hn))。
                # 🔴 2026-10-10 定为出货默认 0.10（配合 `main.TRAIL_SCALE = 0.8`）。
                ("sand_hole_th", "HG_HOLE_TH", "holeth", 0.10),
                # 渐入长度 = 这个系数 × 球径。0 = 关(负对照: 出口会出现分界线)
                ("sand_hole_ramp", "HG_HOLE_RAMP", "holeramp", 0.08),
                # 孔隙率随深度增长的系数: 0 = 关(只剩渐入)
                # 🔴 **2026-10-11: 出货默认 0.0 → 0.15。** 用户目标(原话):
                #    「沙子下落…**越往下空隙越多**」+ 更早的
                #    「沙柱中沙子的空隙率是不变的, 应该不符合现实, 应该随着重力的影响,
                #      孔隙率越来越高, 这样的话, 衔接也更自然」。
                #    机制: 阈值 `th = sand_hole_th + grow·√(depth01 − ramp)`,
                #    **阈值越大洞越多**(`step(th, hn)` 是"hn ≥ th 才保留沙")。
                #    取 0.15 ⇒ 深处(depth≈400px)阈值 0.10 → 0.197 ≈ **洞翻倍**;
                #    近口那一段用 `sqrt` 压住、不猛涨。`0` = 旧行为(孔隙率恒定)。
                ("sand_hole_grow", "HG_HOLE_GROW", "holegrow", 1.0),
                # 🔴 2026-10-11: 孔隙率随深度升高的**宽度**分量(见 `sand_hole_wide`)。
                #    `holegrow` 只管"洞多密", 这一只管"allowed 到多宽"。
                ("sand_hole_wide", "HG_HOLE_WIDE", "holewide", 0.5),
                # 平流速度 y 分量的系数: 1.0 = 旧行为。0 = 完全不平流 y(诊断用)
                ("sand_flow_vy", "HG_FLOW_VY", "flowvy", 1.0),
                # 🔴 **2026-10-12(2.91): 出货默认回到 −1 = "钟在跑" —— 那才是 1.244 的路。**
                #    2.89 用 "0 = 冻钟" 躲开用户骂的"扰动和刷新", 顺带把运动一起冻没了
                #    (用户: 「上面的沙子完全不变化了」/「移动幅度比较小」)。
                #    **真根因不是钟, 是相位 b 的位移式子写错了**(见 `samp_b` 那一段):
                #    `adv + 0.5` 与 `fract(t+0.5)` 在 `a≥0.5` 的半个周期里差**一个 `velocity`**
                #    (平板 ≈50px), 而相位 b 的权重恰在 `a→1` 时最大 ⇒ **每秒一次跳变**。
                #    实测(平板口径 50s, 上球沙体): 旧路径 |Δ帧| max/中位 = **1.79**(t=20.85);
                #    1.244 = **1.03**(无尖峰); 修好 `samp_b` 后两臂**逐像素 0 差异**。
                #    0 = 冻钟(2.89 那条, 用户已否) · >0 = 刚性连续下滚(2.88, 太快且带横向)。
                #    两者都只作 A/B; 出货走 −1。
                ("sand_up_scroll", "HG_UP_SCROLL", "upscroll", UP_SCROLL_DEFAULT),
                # 出口以下"打散"的渐入长度(球径比例): 0 = 旧行为(有分界线)。
                # 由 `main.NECK_FREE_RAMP_TUBES`(单位=颈管高的倍数)换算后喂进来。
                ("sand_free_ramp", "HG_FREE_RAMP", "freeramp", 0.0),
                # 颈部不许比球底更暗: 0 = 旧行为(颈管被压暗成高反差斑块)
                # 🔴 **2026-10-10 出货默认 0.0 → 1.0**。用户 2.37 截图(蓝沙):
                #    「这个叫修了?」—— 整条柱子**比上球沙体与沙堆都暗**。
                #    实测(PC 400×875 / 蓝沙 / t=7.40, 管内均值 vs 出口以下均值):
                #        默认 0  → 差 **+2.4**       两个都开 → 差 **+0.2**
                #    注释里原本就写着「用户看到的『颜色分层非常明显』就是它」, 只是没落到默认值。
                ("sand_neck_light", "HG_NECK_LIGHT", "necklight", 1.0),
                ("sand_uv_debug", "HG_UV_DEBUG", "uvdebug", 0.0),
                # 🔴 **诊断旋钮: 把 `sand_mix` 钉成常数**(默认 -1 = 不钉)。
                #    存在的理由: `sand_mix` 在**渲染脚手架**(`tools/inspect_flow.py`)里
                #    的值取决于"这次 `set_duration` 有没有真的改到 duration" ——
                #    改了 ⇒ 那一帧 `blend` 正好是 1.0 ⇒ 臂 A; 没改 ⇒ 沿用启动时那个 0 ⇒ 臂 B。
                #    ⇒ **同一份代码能渲染出两个样子**, 拿它做 A/B 会得出"改了没差别"的假结论
                #    (2026-10-12 真的栽了一次: 修好/没修 两张图 **md5 相同**)。
                #    要断言"这一版画的是哪一支", 只能**钉住**它。
                ("sand_mix_pin", "HG_SAND_MIX_PIN", "sandmixpin", -1.0),
                # 🔴 **2026-10-10 出货默认 0.0 → 1.0**: `tone` 的 `lighting` 必须与
                #    `grain()` 里减掉的那一个用**同一个坐标**, 否则颈部多出一个系统偏置
                #    (实测颈部 |tone| 0.227 vs 上球 0.112 —— 2 倍, 画面上就是"斑块")。
                ("sand_wrap_light", "HG_WRAP_LIGHT", "wraplight", 1.0),
                ("sand_jump_y", "HG_JUMP_Y", "jumpy", 1.0),
                # 自由段纹理尺度(1 = 不变, <1 = 更粗)。见 `sand_free_coarse` 的注释。
                ("sand_free_coarse", "HG_FREE_COARSE", "freecoarse", 0.5),
                ("sand_free_grain", "HG_FREE_GRAIN", "freegrain", 1.0),
                ("sand_adv_cont", "HG_ADV_CONT", "advcont", 1.0),
                ("sand_edge_lo", "HG_EDGE_LO", "edgelo", 0.78),
                ("sand_edge_hi", "HG_EDGE_HI", "edgehi", 1.12)):
            _v = _flag_float(_env, _fname)
            # 优先级: 环境变量/标记文件(_v) > **设置项档位** > 内置默认。
            # 标记文件压过档位是**故意的** —— 设备上的单变量对照臂不许被用户设置搅乱。
            if _v is None:
                _v = _level_override(_key)
            self[_key] = float(_dflt if _v is None else _v)
        self._material_key = None
        self._clock_key = None
        self._time_key = None
        self._tail_key = None
        self._free_key = None
        self._mat_key = None
        self._mix_key = None

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
            # 🔴 **2026-10-11 修: 材料坐标的钟必须是**真实时间**, 不许再乘 `speed_scale`。**
            #    病(用户最早那类「**2 个速度差异大的层**」; 对抗面板查出 + 本机实测确证):
            #      图案速度 = `Ṫ / τ'(d) = Ṫ · v_scaled(d)`, 而 `v_scaled(d) = √(v0p²+2·gp·d)`
            #      `= ms·√(60²+900d)` —— 它**已经**是粒子在屏幕上的真实速度
            #      (粒子的 `v0 = 60·ms`、`g = 450·ms²`)。
            #        · `T = elapsed·ms` ⇒ 图案 = **ms²·√(…)** —— 比粒子快 **ms 倍**;
            #        · `T = elapsed`    ⇒ 图案 = **ms·√(…)** ✓ 与粒子逐点同速。
            #    ms=1(长周期)时两者**恰好相等** ⇒ 15s/50s/600s 档量到的 "560 vs 560" 是真的;
            #    缺陷只在 **ms>1 的档**(平板口径: 3s 档 ms≈2.09, 1s 档 ≈7.2)。
            #    **静态帧金标准结构性看不见它** —— 判据是 `tools/_probe_speed_ratio.py`
            #    (材料图案速度 ÷ 粒子速度律)。实测(3s 档 ms=2.086, 清粒子 + 关细颗粒):
            #      修前 **2100 px/s**(比值 **2.54**) ⇒ 修后回到 ≈1.2(带内偏置, 与 15s 档同)。
        if elapsed != self._time_key:
            self._time_key = elapsed
            self["sand_time"] = float(elapsed)
        if speed_scale != self._mat_key:
            self._mat_key = speed_scale
            self["sand_motion_scale"] = float(speed_scale)
        # 🔴🔴 **2026-10-12 修: `sand_mix` 必须跟着 `blend` 走。**
        #    它在 2.64(`210ab59`, 材料坐标那一版)被**顺手挪进了上面那个 `speed_scale`
        #    分支** —— 而 `speed_scale`(`_particle_motion_scale`)在一轮里是**常数**
        #    ⇒ 那支**只在第一帧跑一次**, 而第一帧 `elapsed ≈ 0` ⇒ `blend ≈ 0`
        #    ⇒ **`sand_mix` 被永久钉在 0**(实测: elapsed 走到 1.69s 仍是 **0.001**)。
        #    后果不是"轻微掉色", 是**整层 GPU 颗粒/光照一次都没接上**:
        #    `mix(original.rgb, flowing, sand_mix)` ⇒ 画面上就是**原贴图**(静态),
        #    用户判词: 「**有部分沙子一动不动…大概是从颈部出来的一小段**」+
        #    「**最上层的沙子之前有一个 Gpu 的流动, 也被你给删了**」。
        #    ⚠️ 它**躲过了 2.64~2.74 全部 11 个版本的所有闸门**:
        #      · 金标准是**静态帧**──`sand_mix` 只改颜色不改几何, 且逐版本重标定过;
        #      · 那些"材质图案跑 220~500px/s"的实测量的是 **coverage 那一套噪声场**
        #        (`hn`/`n`/`n2`, 它们走 `adv` = `sand_clock`, **不受 `sand_mix` 管**)
        #        ⇒ **动的不是颗粒, 是洞和边** —— 颗粒层从头到尾是死的, 而量具看不见。
        #    ⇒ 判据只能直接读那个 uniform: `tools/_probe_sandmix.py`(期望 ≥0.99)。
        if blend != self._mix_key:
            self._mix_key = blend
            self["sand_mix"] = float(blend)
        # 诊断档: 钉住 ⇒ 用于做**确定的** A/B(见 `sand_mix_pin` 的注释)。
        _pin = self["sand_mix_pin"]
        if _pin >= 0.0:
            self["sand_mix"] = float(_pin)
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
varying vec2 surface_displacement;
varying float surface_coverage;
void main(void) {
    sand_position = vPosition;
    surface_displacement = vFlow;
    surface_coverage = vCoverage;
    tex_coord0 = vTexCoords0;
    frag_color = color * vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(vPosition, 0.0, 1.0);
}
"""

# Keep grain contrast and lighting identical to the existing flow material.
MOUND_FRAGMENT_SHADER = GRAIN_FRAGMENT_HEADER + """
varying vec2 surface_displacement;
varying float surface_coverage;
uniform float surface_phase;
void main(void) {
    float diameter = max(2.0, sand_geometry.y * 2.0);
    // Match the fixed complete UV of the static mound, regardless of pile height.
    vec2 uv = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                  (sand_position.y - sand_geometry.z) / sand_geometry.w);
    vec4 original = texture2D(texture0, uv);
    float depth_weight = 1.0 - smoothstep(0.0, 1.0, tex_coord0.y);
    vec2 displacement = surface_displacement / vec2(diameter, sand_geometry.w)
                      * clamp(sand_mix, 0.0, 1.0) * surface_coverage * depth_weight;
    float a = surface_phase;
    float b = fract(a + 0.5);
    float wa = 1.0 - abs(1.0 - 2.0 * a);
    vec3 ca = texture2D(texture0, uv - displacement * a).rgb;
    vec3 cb = texture2D(texture0, uv - displacement * b).rgb;
    // At the boundary both samples equal the original: no translucent overlay.
    gl_FragColor = frag_color * vec4(ca * wa + cb * (1.0 - wa), original.a);
}
"""


class MoundSurfaceFlowContext(SandFlowContext):
    """One fixed mesh inside the shared mound surface; no additional sand volume."""

    def __init__(self, geometry, max_nodes, texture):
        super().__init__(geometry, MOUND_VERTEX_SHADER, MOUND_FRAGMENT_SHADER)
        self["surface_phase"] = 0.0
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
        self["surface_phase"] = float((clock * 0.8) % 1.0)
        if strength <= 0.0 or not cols:
            if self._node_count:
                self._mesh.indices = []
                self._node_count = 0
            return
        self._mesh.texture = material.texture
        cx = self["sand_geometry"][0]
        reach = 2.0 * diameter
        vertices = self._vertices
        count = 0
        visible = False
        for i, (x, y, free, thick) in enumerate(cols):
            if abs(x - cx) > reach:
                # Keep one zero-displacement neighbour at each edge of the patch.
                adjacent = ((i > 0 and abs(cols[i - 1][0] - cx) <= reach)
                            or (i + 1 < len(cols) and abs(cols[i + 1][0] - cx) <= reach))
                if not adjacent:
                    continue
            before = cols[max(0, i - 1)]
            after = cols[min(len(cols) - 1, i + 1)]
            slope = (after[1] - before[1]) / max(1e-6, after[0] - before[0])
            radius = self['sand_geometry'][1]
            sphere_roof = radius + math.sqrt(max(0.0, radius*radius - (x-cx)**2))
            roof = self['sand_geometry'][2] + sphere_roof
            depth, dx, dy = receiving_surface_profile(
                x - cx, diameter, thick, slope, max(0.0,roof-y))
            coverage = 1.0 if depth > 0.0 else 0.0
            visible = visible or coverage > 0.0
            k = count * 14
            vertices[k:k + 14] = (
                x, y, (x - cx) / diameter, 0.0, dx, dy, coverage,
                x, y - depth, (x - cx) / diameter, 1.0, dx, dy, coverage)
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
