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
// ---- 出口以下那股沙的**密度场**四参数(2026-10-10) ---------------------------
// 用户判词: 「沙柱没有 1 周前的版本好看。当前偏实心」+「可以让随机幅度更大一些」。
// 标定过的判据(`tools/_probe_column_alpha.py`, 自带正对照、偏差 0.000):
//   v1.2 / 1.51(用户说好看) 柱内 alpha 中位 **0.945** / 10 分位 **0.855**
//   2.26(偏实心)            柱内 alpha 中位 **1.000** / 10 分位 **0.953**
// ⇒ 目标是**复刻 0.945 / 0.855 那组统计**, 不是回退到旧版的可见性。
uniform float sand_core;   // 实心核占**半宽**的比例(0=整条都参与打散)
uniform float sand_edge;   // 打散强度总开关(0=完全不打散)
uniform float sand_bite;   // 打散**深度**的下限(旧值 0.25; 越小 ⇒ 随机幅度越大)
uniform float sand_alpha;  // 覆盖率上限(<1 才让粒子层透得出来)
// 🔴 **2026-10-10 (B-①): 出口以下那股沙的漂移速度要与它自己的落速同量级。**
//    上面那条 `velocity` 在**口顶以下**被 `from_mouth.y = max(0, y − w)` 钳成 0
//    ⇒ `sink ≈ 0.98` **恒定** ⇒ 柱子的颗粒纹理**恒漂 50px/s**, 与深度无关。
//    密采样互相关实测(`tools/_probe_grain_motion.py`, dt=0.04s, z 8.5~9.4):
//        上球 上/中/下段   25 / 25 / 50 px/s   (本式预测 26 / 34 / 43)  ← 用户说"美丽和谐"
//        柱   上段         50 px/s            (本式预测 50)             ✓ 一致
//        柱   中/下段      **362 / 500 px/s** (自由落体参照 504 / 645)  ← 粒子层
//    ⇒ 柱子里"板子的颗粒"只漂 50、而穿过它的沙是 300~600 ⇒ 差 7~10 倍,
//      读起来是"一根静止的管子 + 有东西在里面穿过去", 而不是"一股正在落的沙"。
//    修法: 出口以下的漂移速度改成跟着深度走 `K·√(2·g·depth)`, **取两者较大**。
//    `K = 0`(默认) ⇒ 逐字等于旧行为; 调 K 走环境变量 / 标记文件 `freedrift`。
uniform float sand_free_drift;
// 🔴 **2026-10-10 (A3): 板上打**二值**的洞。** 阈值: `<0 = 关`(逐字旧行为), 越大洞越多。
//
//    为什么是"洞"而不是"降 alpha": 实测(`sand_alpha` 消融)拆掉覆盖率上限只把
//    "逐位不动"从 87.0% 抬到 91.2%、"近实心"从 8.5% 降到 4.3% —— **更实、不是更透**。
//    而 v1.2(用户说好看)的柱子中间带是 **0%**、内部孔洞 **5.1%**、98%→36% 的行有洞。
//    ⇒ v1.2 的"颗粒感"来自**颗粒之间漏背景**, 不是来自半透明:
//      每条 2px 竖线读成"一颗沙"; 现在多了一块板把缝填上 ⇒ 那些竖线**连成纤维**
//      (自相关 竖/横 由 1.00 爬到 2.50)。
uniform float sand_hole_th;
// 洞的**渐入长度**(像素, 按球径比例给): <=0 ⇒ 不渐入(出口处会有横向分界线)。
uniform float sand_hole_ramp;
// 🔴 **2026-10-10 (孔隙率随深度升高)**: 洞的**密度**沿深度继续增长。
//    用户: 「沙柱中沙子的空隙率是不变的, 应该不符合现实, 应该随着重力的影响,
//    孔隙率越来越高, 这样的话, 衔接也更自然」。
//    为什么需要它: `sand_hole_ramp` 只把洞**渐入**到出厂值, 之后就**平**了;
//    而粒子的墨量在那个带里是渐入的 ⇒ 两者相减, 净孔隙率变成"**上松下密**"(方向反了,
//    实测: 设备 2.29 的过渡带洞里上多下少)。把阈值随深度继续抬高 ⇒ 洞越来越多 ⇒
//    与粒子层同向叠加, 整条柱子才是"越往下越松"。
//    `= 0` ⇒ 逐字等于只做渐入的那一版。
uniform float sand_hole_grow;
// 🔴 **2026-10-10: 把平流速度的 y 分量乘一个系数**(默认 1.0 = 旧行为)。
//    嫌疑: `grain()` 是**两相位交叉淡入** —— 两次采样沿 `velocity` 相隔 `v·(b−a)`，
//    而 `velocity.y ≈ −50/diameter` ⇒ 设备口径(diameter≈808)上两次采样**纵向差 ~16px**
//    ⇒ 两个纵向错开的噪声场相叠 ⇒ **颗粒被纵向抹开** ⇒ 直筒段"竖着拉长"。
//    设备实测(8 帧跨帧取中位, 柱内 60% 窗口): 收口段 竖/横 **0.95** → 直筒段 **1.62**。
//    `= 0` ⇒ 完全不平流 y ⇒ 若各向异性掉回 ~0.95，则"纵向平流拖影"这条成立。
uniform float sand_flow_vy;
// 🔴 **2026-10-10: 出口以下"打散"的渐入长度**(球径的比例, 与 `sand_hole_ramp` 同一约定)。
//    **病根**: 上面那个 `if (sand_position.y < sand_free.x)` 是**硬分支** —— 出口以上
//    `coverage ≡ 1`, 出口那一行**瞬间**变成"只有内 `sand_core`(0.35) 是实心 + 外圈打散"。
//    设备实测(1080, 50s 档, 逐行最长连续沙色段): 出口以上 **100%**, 出口以下**一步掉到 28%**。
//    算得上: `sand_core` 0.35 的半宽占柱宽 34.6% —— 与实测 28.3% 对得上。
//    **这就是用户说的那条横向分界线。**
//    `= 0` ⇒ 逐字旧行为(可直接当负对照); `> 0` ⇒ 打散按深度从 0 渐入到全功率。
uniform float sand_free_ramp;
// 🔴 **2026-10-10: 颈部不许比球底更暗**(0 = 旧行为)。
//    `lighting()` 里有一项 `sand_lighting.y * (uv.y - 0.5)`, 而**颈部的 `uv.y` 是负的**
//    (`uv.y = (y − 上球内底)/球径`, 管子在上球内底以下好几个球径的位置) ⇒ 整条管子被推到
//    纵向梯度的暗端。后果有两层:
//      ① 均值: 走着色器时颈管比上球暗 **7.6 级**(平色渲染时只差 1.7 级 ⇒ 那 6 级是着色器加的);
//      ② 反差: `tone = clamp(lighting + detail, −1, 1)` 被压成**恒负** ⇒
//         `flowing = mix(sand_base, sand_dark, |tone|)` **恒在暗支**、且 |tone| 摆幅很大
//         ⇒ 画面上是**高反差的大斑块**, 而上球 tone 在 0 附近换号 ⇒ 细而淡的颗粒。
//    用户看到的"颜色分层非常明显"就是它(与上球的细颗粒在玻璃肩那一行硬切)。
//    `= 1` ⇒ 颈部取 `max(uv.y, 0)`, 即与球底同色阶(球体内 `uv.y ≥ 0`, 所以只影响颈部)。
uniform float sand_neck_light;
// 调试: >0 ⇒ 直接把 `uv` 画成颜色(R=uv.x, G=uv.y, 都 clamp 到 0..1) —— 用来**读出**
// 着色器在颈部/球体各自采到的 uv, 而不是靠推。`necklight` 旁边给个 `uvdebug` 标记文件。
uniform float sand_uv_debug;
// 🔴 **2026-10-10: `tone` 那行的 `lighting` 必须和 `grain()` 里减掉的那一个用同一个坐标。**
//    `grain(u)` 返回 `delta/span − lighting(fract(u))`; 而 `tone = lighting(uv) + detail`。
//    球体里 `uv.y ∈ [0,1]` ⇒ `fract` 不变, 两者抵消 ✓;
//    **颈部 `uv.y` 是负的**(管子在上球内底以下) ⇒ `fract` 把它折到 0.88~1.0,
//    于是 `lighting(uv)` 与 `lighting(fract(uv))` 差了一大截, **抵消不掉** ⇒ 颈部多出一个
//    系统偏置。实测(设备/桌面同代码, 行 424..464 只取"确定由颈部四边形画"的行):
//      颈部 |tone| **0.227** vs 上球 **0.112** —— **2 倍**, 画面上就是"斑块"而不是细颗粒。
//    `= 1` ⇒ `tone` 改用 `lighting(vec2(uv.x, fract(uv.y)))`(球体上逐位不变)。
uniform float sand_wrap_light;
// 调试: 每个 `SandFlowContext` 一个标记值, 画成颜色 ⇒ **直接读出"这个像素是哪个 context 画的"**。
uniform float sand_ctx_tag;
// 调试: `jump` 的 y 分量系数(默认 1.0)。两相位沿 v 的固有错开, 与 `sand_flow_vy` 合起来
// 才能把"纵向平流抹开"这条**完整**关掉(只关 `flowvy` 不够 —— `jump.y` 仍分开 32 纹素)。
uniform float sand_jump_y;

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
    // 🔴 **2026-10-09 修: 这两行原来是 `SAND_NECK_UV_ANCHOR` / `SAND_SEAM_BAND`, 而这两个
    //    名字**全仓库没有任何 `#define`** —— 它们是**大写伪装成宏的未声明标识符**;
    //    真正的 uniform 叫小写 `sand_neck_anchor` / `sand_seam_band`(见 `GRAIN_FRAGMENT_HEADER`
    //    与 `SandFlowContext.__init__` 的 `self["sand_neck_anchor"]`)。
    //    再加上 `float diameter` **声明在第一次使用之后** ⇒ **整个 fragment shader 编译失败**:
    //        ERROR: 0:58/0:59/0:63: 'diameter' : undeclared identifier
    //        ERROR: 0:60: 'SAND_NECK_UV_ANCHOR' : undeclared identifier
    //        ERROR: 0:63: 'SAND_SEAM_BAND' : undeclared identifier
    //    ⇒ `SandFlowContext.__init__` 抛 RuntimeError ⇒ `main.py:6338` 捕获 ⇒
    //      `_sand_flow_contexts = ()` ⇒ **上球/颈部的 GPU 平流整条不执行**, 退回静态材质。
    //    后果(用户 2026-10-09 报 + 录像实测): **上球沙子一个像素都不动** ——
    //      v2.13(15:24 提交) 上球沙体隔 8 帧变化 **23.6% / 23.5% / 22.2%**;
    //      v2.19 同期 **0.00%, max 通道差 0**。设备 logcat 同步复现同一组报错。
    //    引入于 **2.17 `0e478c0`**(颈部交界做过渡); 2.18 只把带宽设 0 而**没有修 shader 源码**,
    //    所以"等于旧行为"没有兑现 —— 旧行为是**有平流**的。
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
    // 🔴 **2026-10-09 实测结论: 这一行对画面**没有任何作用** —— 别再往这里下功夫。**
    //    机制: 本函数最后一句是 `mix(original.rgb, flowing, sand_mix)`, 而
    //    `sand_mix = smoothstep(min(1, elapsed/0.3))` ⇒ **elapsed > 0.3s 后恒为 1.0**
    //    ⇒ `original.rgb` 被 `flowing` **完全替换**, 只剩 `original.a`(两侧都是 1)。
    //    判据(可复现): 给这一行加单变量开关, 同一口径同一时刻两臂渲染
    //    **逐字节完全相同**(period-15-time-2.00 / 7.50 / 14.98 三帧全同)。
    //    ⇒ 2.16→2.17→2.18 那一串(含 2.18「未通过量化判据前不许上线」)改的都是这一行,
    //      **它们全部没有上过画面**; 2.16 把它诊断成"根因是各图元自己的 `tex_coord0`"
    //      本身也是错的。
    //    交界处另做了**双向标定**的滑窗判据(见 `tools/_probe_junction_img.py`):
    //      跨界 |dmu| = 1.26 (t=7.50) / 1.56 (t=2.00), 而同图别处 32 个滑窗中位 1.18/0.81
    //      —— **交界不是异常点**; 正对照(给管顶以下 +12 灰阶)能让它升到 10.74, 尺子有分辨力。
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
    // 🔴 2026-10-10 (B-①): 出口以下改按**深度**给漂移速度(见 `sand_free_drift` 的注释)。
    //    必须在下面采 `grain()` **之前**改 —— 那两行就是拿 `velocity` 去偏移采样坐标的。
    if (sand_free.x > 0.0 && sand_position.y < sand_free.x) {
        float depth_px = sand_free.x - sand_position.y;
        float fall = sand_free_drift * sqrt(2.0 * 450.0 * depth_px);
        velocity.y = -max(4.0 + 46.0 * sink, fall) / diameter;
    }
    if (sand_flow_vy != 1.0) {
        velocity.y *= sand_flow_vy;
    }
    float jitter = 0.2 * sin(from_mouth.x * 3.1 + from_mouth.y * 5.2);
    float t = sand_clock + jitter;
    float a = fract(t);
    float b = fract(t + 0.5);
    float delta = luma(texture2D(texture0, fract(uv)).rgb) - luma(sand_base);
    float wa = 1.0 - abs(1.0 - 2.0 * a);
    float wb = 1.0 - wa;
    vec2 jump = vec2(0.125, 0.0625 * sand_jump_y);
    float ga = grain(uv - velocity * a + floor(t) * jump);
    float gb = grain(uv - velocity * b + floor(t + 0.5) * jump + vec2(0.5));
    float detail = (ga * wa + gb * wb) * inversesqrt(wa * wa + wb * wb);
    vec2 luv = (sand_wrap_light > 0.0) ? vec2(uv.x, fract(uv.y)) : uv;
    float tone = clamp(lighting(luv) + detail, -1.0, 1.0);
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
        // 🔴 **2026-10-10 修 D1: 掩码位置错了。**
        //    `u` 在 左缘=0 / 中轴=0.5 / 右缘=1 ⇒ 原来的 `abs(u)*2` 是
        //    **左缘 0、中轴 1、右缘 1** ⇒ 与"两条边毛掉、内 62% 实心"**正好相反**
        //    (左缘完全不打散, 中轴与右半边全功率打散)。正确是 `abs(u - 0.5) * 2`。
        //    ⚠️ 这也解释了 2.25 那次"拓打边带却没效果": 掩码根本没落在边上。
        float r01 = abs(u - 0.5) * 2.0;          // 0=中轴, 1=两条边
        float rim = smoothstep(sand_core, 1.0, r01);
        // 🔴 **2026-10-10: 打散要**渐入**, 不许在出口那一行瞬间全开。**
        //    没有这一段时, `rim` 在出口以下**立刻**满功率 ⇒ 柱宽从 100% 实心一步掉到
        //    只剩内 `sand_core`(35%) ⇒ 画面上一条横贯的分界线(就是用户报的那条)。
        //    `sand_free_ramp` 是球径的比例; `= 0` ⇒ `fr ≡ 1` ⇒ 逐字旧行为。
        float fr = 1.0;
        if (sand_free_ramp > 0.0) {
            fr = clamp((sand_free.x - sand_position.y) / (sand_free_ramp * diameter),
                       0.0, 1.0);
        }
        // 🔴 **2026-10-10 用户备注: 「可以让随机幅度更大一些, 这个是可以的。
        //    否则就是一个矩形/梯形了」** ⇒ 打散深度从 `0.25+0.75n` 放宽到
        //    `sand_bite_min + (1-它)*n`, 且整条再乘 `sand_edge`。
        float nz = clamp(u < 0.5 ? n : n2, 0.0, 1.0);
        coverage *= 1.0 - rim * (sand_bite + (1.0 - sand_bite) * nz)
                          * sand_edge * sand_free.y * fr;
        // 🔴 核心不再 100% 不透明 —— 否则同色全不透明的它会把**粒子层完全吃掉**
        //    (速度拖尾/高光档全都看不见) ⇒ "颗粒在动"这个信号被自己盖住。
        coverage = min(coverage, sand_alpha);
        // 🔴 **2026-10-10 (A3): 二值洞 —— 必须在上面那两行之后**, 因为它要把结果
        //    压成 {0, 1}(保留区**全不透**), 而不是叠一层灰。
        if (sand_hole_th >= 0.0) {
            // ⚠️ 洞场**必须含 y、而且被平流**。上面那两条 `n`/`n2` 的采样坐标只有
            //    `u` 与**整帧同值**的 `sand_clock` ⇒ 同帧沿 y 恒定 ⇒ 阈后是**通长竖条**,
            //    而且不跟沙走 —— 这是它没法直接拿来打洞的原因。
            //    这里改成"按球径归一的二维坐标 + 与 `flowing` **同一套**平流偏移",
            //    于是洞跟着沙走、尺度约等于材质颗粒(~4px)。
            vec2 huv = vec2(0.5 + (sand_position.x - sand_geometry.x) / diameter,
                            (sand_free.x - sand_position.y) / diameter);
            float hn = grain(huv - velocity * a + floor(t) * jump);
            // 🔴 **2026-10-10 补: 出口处必须渐入。**
            //    `sand_free.x`(= 出口)是这套掩码的**硬边界**: 出口以上 coverage 恒 1、
            //    以下才打洞 ⇒ `step` 正好在出口那一行造出一条**横向分界线**。
            //    实测(桌面 1904×2890, 15s, t=7.64, 逐行"带内露出背景"):
            //        行 1400..1490 = **0.0%**(一条不差) → 行 1500 = 1.0% → 1510 = 4.3%
            //        → 1530 = 6.6% → 1550 = 7.4%   ← 阶跃落在 1499(出口)
            //    (同一帧的 2.26 全程 0.0%, 因为它没有洞 —— 这条线是加洞引入的。)
            //    修法: **按"片"二值地放开** —— 拿一个大尺度噪声当**闸门**,
            //    深度决定"这一片允不允许挖"。每个像素仍然是**二值**(不产生灰雾),
            //    只有洞的**面密度**随深度渐变。
            //    ⚠️ `sand_hole_ramp` 是**球径的比例**(与 `sand_seam_band` 同一约定) ——
            //    写死像素会在平板/手机上差好几倍。`= 0` ⇒ 逐字旧行为(可直接当负对照)。
            //
            //    ⚠️⚠️ **极性**: `solid = 1` 是"**保留沙**"。所以闸门关的时候要把它
            //    按回 1(`mix(1.0, solid, allow)`), **不是** `max(solid, allow)` ——
            //    后者实测正好写反: 近出口没抑制住洞、深处反而把洞**全关**了
            //    (深度 100px 以下露出背景 0.0%)。
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
            // 🔴 **2026-10-10: 洞只挖在"已经打散的那一圈"里, 而且按深度渐入。**
            //    原来是 `coverage = solid;` —— 把**整条柱连芯一起**压成二值洞,
            //    实测出口以下只剩 **28% 不透明**(PC 400×875 / 10s / t=7.40,
            //    `joined + pooloff`: 出口以上 **100%**, 出口以下
            //    100→92→**28/28/43/36/21/35%**), 画面上是一片**硬边碎斑**,
            //    与出口以上那条实心颗粒柱硬切成两种质地 —— 用户报的"颜色分层"。
            //    (2.32 当年就是为了绕开它, 把出口以下搬出着色器换成普通纹理;
            //     代价是词汇变了。这里改成正面修。)
            //    改成 `*= mix(1, solid, rim*fr)` 之后:
            //      · 芯部(`rim = 0`) ⇒ 系数 1 ⇒ 保持实心, 与**管内沙柱连续**;
            //      · 边缘(`rim = 1`) ⇒ 该挖洞的照挖, 该打散的照打散;
            //      · 刚出口(`fr → 0`) ⇒ 洞与打散一起渐入 ⇒ 出口那一行不再有阶跃。
            //    ⇒ **一条柱、一套着色器**, 只在边缘与深度上有区别。
            coverage *= mix(1.0, solid, clamp(rim * fr, 0.0, 1.0));
        }
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
        } else {                              // 这个像素属于哪个 context
            gl_FragColor = vec4(sand_ctx_tag / 4.0, 0.0, 0.0, 1.0);
        }
        return;
    }
    gl_FragColor = frag_color * vec4(mix(original.rgb, flowing, sand_mix),
                                     original.a * coverage);
}
"""


def _flag_float(name, fname):
    """`HG_<name>` 环境变量优先(桌面), 其次与 main.py 同目录的 `<fname>` 标记文件。

    🔴 安卓 app **读不到宿主 shell 的环境变量** ⇒ 设备上做单变量对照只能写标记文件
    (app 私有目录, 与 `flowrate` / `maxfps` / `blit.off` 同一套):
        adb shell "echo 0.30 > /data/data/org.shalou.hourglass/files/app/seamband"
    两个都没有 ⇒ None ⇒ 走出货默认值。
    """
    env = os.environ.get(name)
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), fname), "r") as fh:
            text = fh.read().strip()
        return float(text) if text else None
    except Exception:
        return None


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
        self["sand_clock"] = 0.0
        self["sand_mix"] = 0.0
        self["sand_tail"] = (0.0, 0.0, 1.0)
        self["sand_free"] = (0.0, 0.0, 1.0, 0.0)
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
        for _key, _env, _fname, _dflt in (
                ("sand_core", "HG_SAND_CORE", "sandcore", 0.35),
                ("sand_edge", "HG_SAND_EDGE", "sandedgemul", 1.0),
                ("sand_bite", "HG_SAND_BITE", "sandbite", 0.15),
                ("sand_alpha", "HG_SAND_ALPHA", "sandalpha", 0.93),
                # 出口以下的漂移速度系数: 0 = 关(= 逐字等于旧行为), 1.0 = 完全跟自由落体
                ("sand_free_drift", "HG_FREE_DRIFT", "freedrift", 0.0),
                # 二值洞的阈值: <0 = 关(逐字旧行为); **越大洞越多**(step(th,hn))。
                # 🔴 2026-10-10 定为出货默认 0.10（配合 `main.TRAIL_SCALE = 0.8`）。
                #    标定见 `main.py` 的 `_trail_scale_probe`；梯子图 `_vid/ladder2.png`。
                ("sand_hole_th", "HG_HOLE_TH", "holeth", 0.10),
                # 渐入长度 = 这个系数 × 球径。0 = 关(负对照: 出口会出现分界线)
                ("sand_hole_ramp", "HG_HOLE_RAMP", "holeramp", 0.08),
                # 孔隙率随深度增长的系数: 0 = 关(只剩渐入)
                ("sand_hole_grow", "HG_HOLE_GROW", "holegrow", 0.0),
                # 平流速度 y 分量的系数: 1.0 = 旧行为。0 = 完全不平流 y(诊断用)
                ("sand_flow_vy", "HG_FLOW_VY", "flowvy", 1.0),
                # 出口以下"打散"的渐入长度(球径比例): 0 = 旧行为(有分界线)。
                # 由 `main.NECK_FREE_RAMP_TUBES`(单位=颈管高的倍数)换算后喂进来。
                ("sand_free_ramp", "HG_FREE_RAMP", "freeramp", 0.0),
                # 颈部不许比球底更暗: 0 = 旧行为(颈管被压暗成高反差斑块)
                ("sand_neck_light", "HG_NECK_LIGHT", "necklight", 0.0),
                ("sand_uv_debug", "HG_UV_DEBUG", "uvdebug", 0.0),
                ("sand_wrap_light", "HG_WRAP_LIGHT", "wraplight", 0.0),
                ("sand_jump_y", "HG_JUMP_Y", "jumpy", 1.0)):
            _v = _flag_float(_env, _fname)
            self[_key] = float(_dflt if _v is None else _v)
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
