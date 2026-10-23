"""GPU grain advection for the reservoir, neck and lower contact surface.

Two-phase flow follows Catlike Coding's Texture Distortion / Valve flow-map approach.
Only grain detail is advected; the palette and broad lighting stay in world space.
"""

import os
import math

from kivy.graphics import Color, Mesh, RenderContext
from grain_style import receiving_surface_profile


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
// 🔴 **2026-10-11: 孔隙率随深度升高的「宽度」分量**(默认 0 = 只放开边缘那一圈)。
//    用户 2026-10-10 的原话: 「沙柱中沙子的空隙率是不变的, 应该不符合现实, 应该随着重力的
//    影响, 孔隙率越来越高, 这样的话, 衔接也更自然」。
//    `sand_hole_grow` 只抬**阈值**(洞更密), 而放开比例是 `rim*fr` —— `rim` 在中轴恒 0
//    ⇒ **芯部按构造永不打洞** ⇒ 那一条只实现了"边缘随深度更毛", 柱内量不到梯度
//    (2026-10-11 设备实测: 默认 0.42/0.56/0.00%, `holegrow=0.8` 也只到 0.29/0.29/0.12%)。
//    本旋钮让"放开比例"在整个柱宽上按 `√深度` 长起来: 出口 0(与上层同密度)、越往下越大。
uniform float sand_hole_wide;
// 🔴 **2026-10-11: 出口以下材质的「刚性下滚速度」(px/s, 0 = 关 = 逐字旧行为)。**
//    用户判词: 「沙子下落的时候有 2 个速度差异大的层, **应该是速度接近**、颗粒不同」。
//    实测病根: 材质 ~60px/s、粒子 ~500px/s(差 8~10 倍); 而用 `sand_free_drift`
//    (`-v(y)·t`, v ∝ √深度)去追 ⇒ **随深度的剪切随 t 无限增长** ⇒ 纹理被拉成竖条雾。
//    ⇒ 刚性滚动(整块图案以**常数**速度下走, 无剪切)才是对的: 颗粒保形, 但整块在动。
//    取值应≈柱中段粒子速度(平板~450)。⚠️ `sand_clock` 是 16s 锯齿 ⇒ 每 16s 有一次
//    图案回跳(≈0.1 个纹理周期); 真觉得跳就得上"不回绕的钟"。
uniform float sand_free_vscroll;
// 🔴 **2026-10-11: 出口以下材质的"物理速度场"参数(见下面的材料坐标)。**
//    用户: 「沙柱的下落速度是不是不合理啊 你来算算和改改」+「**符合期望 也别太快**」。
//    `V0`/`G` **与粒子同值**(main.py:4097 `v0, g = 60.0*ms, 450.0*ms*ms`) ⇒
//        v(d) = √(V0² + 2·G·d)        d = 离孔口的深度(px)
//    `vref` = 参考速度(px/s): 只决定纹理在屏上的**尺度**(v/vref 就是局部拉伸);
//    `vcap` = 速度上限(px/s): 用户说"别太快" ⇒ 超过它按匀速积分。
uniform float sand_free_vref;
uniform float sand_free_vcap;
// 🔴 **2026-10-11: 缩时倍率(`main._particle_motion_scale`)** —— 短周期时粒子被整体加速
//    (`450·ms²` / `60·ms`, main.py:3961/4097), 材质必须**同倍率**跟上, 否则 1s/2s 档
//    粒子比材质快 2~7 倍(工作流审计 2026-10-11 查出: 着色器里 60/450 是写死的)。
uniform float sand_motion_scale;
// 🔴 **2026-10-11: 流动诱导的密度聚团(用户方案, 对抗审查后取代"离散标记层")。**
//    用户原话: 「**不可能只有在沙柱里才有这个东西**(上球、下球都没有)… 所以它应该是沙子在
//    流动的过程中**随机形成的一些聚团**… 形成的一些**不规则**的形状, 形成的有**一大条**什么的」。
//    ⇒ 不是"沙里有一些大颗粒"(那会只在柱子里出现 ✗), 而是**流动自己聚出来的团**:
//       · 低频噪声**沿材料坐标 `mat_y`** ⇒ 跟着沙走(与粒子同速) + 被流动**拉长成条**(dτ/dd=1/v)
//       · 强度**随深度从 0 长起来** ⇒ 出口处 ≡0 ⇒ 与颈部/上球无缝(不是"外面套一层")
//       · 噪声 ⇒ 无周期 ⇒ 不构成车轮效应要的那种周期性图案
// 🔴 **2026-10-11 (第二次, 用户否掉第一版之后): 聚团 = **团与团之间的缝**, 不是"给柱子压花纹"。**
//    用户判词(看第一版的并排图): 「**右侧还是一个固体啊**」。
//    第一版写成 `coverage *= 1 + amp*(n−0.5)*2` —— **均值不变**, 只是在实心板上压出明暗花纹
//    ⇒ 整条柱子**还是一块实心**。方向反了: 要让人看出"一团一团", 必须让噪声低的地方
//    **真的透出背景**。
//    现在的做法(分离式):
//        · `clump` 归一成 0..1(≈0.5 中性, 大 = 团心)
//        · `sep = smoothstep(...)` ⇒ 团心 1 / 缝 0
//        · `coverage *= mix(sand_clump_gap, 1.0, sep)` —— 缝里乘到 `sand_clump_gap`
//    ⚠️ **噪声不能用 `grain()`(那张沙纹理)**: 纹理的特征只有 ~3~6px, 把它采样到"团"这个尺度
//       就等于在原尺度上重采样 ⇒ 出来的是各向异性噪点, 不是团。⇒ 用**程序化平滑噪声**
//       (`clump_noise`, 两个八度)。
//    ⚠️ **坐标尺度必须"无单位"**(不能写死像素): 横向按**柱半宽** `sand_free.z` 归一,
//       纵向 = 材料坐标 × (球径/柱半宽) × `sand_clump_scale` ⇒ 团的屏幕尺寸 ∝ 柱宽×当地流速
//       (沙落得越快团被拉得越长 —— 与材料坐标的自拉伸一致), 且换设备/换周期都不用重调。
uniform float sand_clump_gap;     // 缝里的覆盖率(1 = 关掉这个机制; 越小缝越透)
uniform float sand_clump_scale;   // 纵向频率(大 = 团更短更密)
uniform float sand_clump_xscale;  // 横向频率(小 = 跨越整条柱的横带; 大 = 圆团)
uniform float sand_clump_warp;    // 域的扭曲量(0 = 等间距横带; 1 = 参考值)
uniform float sand_clump_lo;      // 缝的阈值带(低端): 越大 ⇒ 缝**越少**(大部分连续)
uniform float sand_clump_hi;      // 缝的阈值带(高端): 越窄 ⇒ 缝的边越硬(不糊成灰)
uniform float sand_clump_edge;    // 缝里把轮廓收窄到原来的多少(1 = 不收窄)
uniform float sand_clump_start;   // 从哪个深度(球径比例)开始长
uniform float sand_clump_gain;    // 深度渐入的增益(快饱和 ⇒ 只做"出口附近渐入")
// 🔴 **2026-10-11: 车轮效应(wagon-wheel)阈值** —— 用户原话:
//    「就是要**卡在人眼的识别阈值内**, 否则某些时候车轮转得特别快, 你反而感觉转得不快」。
//    眼睛靠**帧间追踪**读速度: 每帧位移 ≥ 图案周期的一半就看成"慢/倒转"。
//    ⇒ 表观速度上限 = k·颗粒间距(px)·帧率, 由 main.py 按**实测帧率**逐帧写入。
//    与用户旋钮 `sand_free_vcap` 取 min(旋钮仍然是可以更低的那个上限)。
// 采样用 `uv.y`(相对球心的位置坐标)时, 出口那一行的值 —— 材料坐标从它接上去,
// 保证**颈部与出口以下的纹理连续**(否则出口会有一道纹理缝)。
const float V0_FALL = 60.0;
const float G_FALL = 450.0;
// 🔴 **2026-10-11: 孔隙率梯度的**起始深度**(球径的比例) —— 见 `sand_hole_wide`。**
//    为什么要有它: 直接按 `√depth` 从出口就长 ⇒ **整条柱一起变淡**(设备实测: 同刻逐点
//    颜色 blue 通道 +10~+30), 与用户另一条要求「与上层沙子没有明显的区别」冲突。
//    用户给这条的**目的**是「**衔接也更自然**」(柱子接到沙堆那一段) ⇒ 梯度只该在
//    **接近落点那一段**起来, 上半段保持致密。0.35 ≈ 球径的 35%(平板 ~280px)。
const float HOLE_WIDE_START = 0.35;
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
uniform float sand_edge_lo;
uniform float sand_edge_hi;
// 🔴 **2026-10-10: 可见边缘由**噪声**决定, 不由几何决定。**
//    用户判词:「整个边缘非常奇怪, 说不规整吧, **他额外带了一层直线**」。
//    实测: 关掉材质(平色四边形)+ 抽掉粒子 ⇒ 柱子是**边缘笔直的矩形**
//    ⇒ 那条直线就是**几何轮廓**(`_neck_sand_side` 的自由段), 而毛边是这里腐蚀出来的,
//    在轮廓**里面** ⇒ 外面永远留一条直边。
//    改法两层(配合 `main.FLOW_FREE_MARGIN` 把几何加宽 1.35 倍):
//      每颗粒所在行取**该侧自己的噪声**(`nz`, 已含 y 与平流) 当"沙到多宽为止",
//      在它附近把 coverage 收到 0 ⇒ 边界随深度起伏、左右两条边各不相关。
//    `sand_edge_hi <= 0` ⇒ 关掉(逐字旧行为, 可作负对照)。
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
// 🔴 **2026-10-11: 自由段那个颜色场的「纹理尺度」(1 = 不变, <1 = 更粗)。**
//    用户判词: 「沙子下落的时候有 2 个速度差异大的层, **应该是速度接近、颗粒不同**」。
//    实测(K 扫描, 见 `sand_free_drift` 那段): 材质纹理的特征尺度只有 ~3px,
//    8px/帧时**任何速度都读不出来**(周期性纹理的车轮效应) ⇒ 把底纹挪快只会频闪。
//    ⇒ 要让「同一个速度」能读出来, 底纹必须**更粗**(特征尺度 ≫ 每帧位移)。
//    做法: 把整个采样参数乘一个系数(**位置与平流一起乘**) ⇒ 屏幕上速度不变、纹理变粗。
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
vec3 neck_flow_rgb_at(vec2 position, float clock, vec2 scroll, float mix_amount) {
    // Explicit sampling state also lets a real grain carry its birth patch.
    // Never resample a falling grain from a slower current-world colour field.
    float diameter = max(2.0, 2.0 * sand_geometry.y);
    vec2 uv = vec2(.5 + (position.x-sand_geometry.x)/diameter,
                   (position.y-sand_geometry.z)/diameter);
    vec2 mouth = vec2((position.x-sand_geometry.x)/diameter,
                     max(0.0,position.y-sand_geometry.w)/diameter);
    float sink = 1.0/(1.0+20.0*mouth.x*mouth.x+8.0*mouth.y*mouth.y);
    vec2 velocity = vec2(-(position.x-sand_geometry.x)*.09*sink,
                         -(4.0+46.0*sink)*sand_flow_vy)/diameter;
    bool frozen = sand_adv_cont > 0.0 && sand_up_scroll == 0.0;
    bool scrolling = sand_adv_cont > 0.0 && sand_up_scroll > 0.0;
    float t = (frozen ? 0.0 : clock)+.2*sin(mouth.x*3.1+mouth.y*5.2);
    float a=fract(t), b=fract(t+.5);
    float wa=1.0-abs(1.0-2.0*a), wb=1.0-wa;
    vec2 jump=vec2(.125,.0625*sand_jump_y);
    vec2 sa=uv-velocity*a, sb=uv-velocity*b;
    if (scrolling) { sa=uv+scroll; sb=sa; }
    float ga=grain(sa+floor(t)*jump*(scrolling ? 0.0 : 1.0));
    float gb=grain(sb+floor(t+.5)*jump+vec2(.5));
    float detail=scrolling ? ga : (ga*wa+gb*wb)*inversesqrt(wa*wa+wb*wb);
    vec2 luv=sand_wrap_light > 0.0 ? vec2(uv.x,fract(uv.y)) : uv;
    vec3 flowing=grain_tone_color(detail,lighting(luv));
    if (mix_amount >= 1.0) return flowing;
    vec2 neck_uv=vec2(uv.x,sand_neck_anchor-uv.y);
    float band=sand_seam_band*diameter;
    float blend=band > 0.0 ? smoothstep(-band,band,position.y-sand_geometry.z)
                           : step(sand_geometry.z,position.y);
    vec3 original=mix(texture2D(texture0,fract(neck_uv)).rgb,
                      texture2D(texture0,uv).rgb,blend);
    return mix(original,flowing,mix_amount);
}
vec3 neck_flow_rgb(vec2 position) {
    return neck_flow_rgb_at(position,sand_clock,
        vec2(.125,.0625*sand_jump_y)*sand_time,sand_mix);
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
    // 🔴 **域扭曲**: 没有它的时候, 噪声沿 y 采出来是**等间距的横带** ——
    //    实测图上就是一节一节的"毛毛虫", 一看就是程序生成的。
    //    拿另一个低频噪声把坐标推歪 ⇒ 团的边界不再平行、大小也不齐。
    //    ⚠️ 幅度要**小**(实测 ±2.2 个团周期会把团糊平: 边界全变成渐变, 反而没有"团"了)。
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
    float clump = 0.5;        // 聚团场 0..1(只有出口以下才有; 0.5 = 中性)
    float clump_g = 0.0;      // 聚团的"深度渐入"权重(0 = 出口处, 不生效)
    float clump_slow = 0.5;   // 更慢的一层: "这一段的缝有多深"(0.5 = 中性)
    vec2 cl_dbg = vec2(0.0);  // 只给 uvdebug 用: 把聚团坐标本身读出来
    // ⚠️⚠️ **这两个必须声明在**外层**。** 2026-10-11 我把 `float cg` 留在了下面的
    //    `else` 块里、却在 coverage 那一段(另一个块)用它 ⇒ `error C1008: undefined
    //    variable "cg"` ⇒ **整个 fragment shader 编译失败** ⇒ `main.py` 静默退回
    //    "static material"(一整块平贴图) ⇒ 并排图里那条臂**根本没跑**,
    //    看着就是"还是一个固体"(用户判词)。**着色器编译失败 = 整层消失, 不报错、只印一行。**
    float mat_y = uv.y;       // 出口以下会被换成材料坐标
    float free_here = 0.0;
    if (sand_free.x > 0.0 && sand_position.y < sand_free.x) {
        float depth_px = sand_free.x - sand_position.y;
        // 🔴 **2026-10-11: 刚性下滚(常数速度) —— 见 `sand_free_vscroll`。**
        //    用户要「2 个速度差异大的层 … 应该是**速度接近**」。旧做法 `-v(y)·t` 里
        //    `v(y) ∝ √深度` ⇒ **图案被随深度剪切, 且剪切量随 t 无限增长**
        //    ⇒ 任何纹理都被拉成竖条雾(实测: 竖高频 3.70→6.52, 边缘发虚)——
        //    那是**实现 bug, 不是物理限制**。刚性滚动无剪切 ⇒ 颗粒保形、整块下走。
        // 🔴 **2026-10-11: 走**材料坐标**(精确积分) —— 见 `sand_free_vref`。**
        //    病: ① 常数下滚(`sand_free_vscroll`)物理上必然错(沙在加速);
        //        ② 旧 `-v(y)·t` 的剪切量随 t 无限增长 ⇒ 纹理糊成竖条雾。
        //    正解: 采样坐标取"材料点自己的标签"
        //        τ(d) = ∫₀^d dξ/v(ξ) = (√(V0²+2Gd) − V0)/G        [秒]
        //        y_sample = y_出口 + vref·(τ(d) − t)/diameter
        //    ⇒ 沿材料路径恒定(图案跟着沙走) + 拉伸只有 v(d)/vref(静态、有界)。
        //    ⚠️ `t` 必须用**不回绕**的 `sand_time`(`sand_clock` 是 16s 锯齿 ⇒ 每 16s 跳一次)。
        // ⚠️ 保留**诊断回退**: `freevscroll > 0` ⇒ 走旧的"刚性常数下滚"(2.63 那一版),
        //    用来做 A/B; 默认 0 ⇒ 走下面的材料坐标(物理)。
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
        // 横向: 按**柱半宽**归一(±1 跨满柱宽) ⇒ 团的宽度与柱宽同量级。
        // 纵向: 材料坐标(纯的, **一个速度补偿项都不许加**) × (球径/柱半宽) × 旋钮。
        // 🔴 **2026-10-11: 这里曾经乘过一个 `vl/vref`(想让团的屏幕尺寸与深度无关) —— 实测
        //    它把**平流打死**: 逐帧互相关量到 OFF 臂柱内位移 23px/0.05s(=460px/s, 粒子),
        //    而 ON 臂 **0px** —— 团**钉在屏幕上不动**, 沙从它里面穿过去, 正是用户骂的
        //    "2 个速度差异大的层"。原因: `cl_y = mat_y·A(d)`, 它的等值线只在 `A` 为常数时
        //    才等于**材料**等值线; 而 `mat_y ∝ −时间` 会一直变大 ⇒ `∂cl_y/∂d` 被 `A'·mat_y`
        //    主导 ⇒ 图案越来越"冻住"(60s 档基本全冻)。
        //    ⇒ **物理上本来就该拉伸**: 沙在加速, 同一团沙在下面占的屏幕长度更大。
        //    ⚠️ **团的屏幕尺寸是"量"出来的, 不是推出来的**: 纸上按
        //      `柱半宽·v(d)/(scale·vref)` 算过一次, 与设备实测对不上(差 1.8~4 倍,
        //      而且不成比例) —— 所以别拿公式去标定, 直接看渲染图。
        //      实测标定(平板 1904×2890, 15s): `scale=2.5` ⇒ 团长约 60~100px, 看着是
        //      "一团一团的沙"; 0.55 ⇒ ~300px(整条柱子只有两三个特征 ⇒ 读成"一块实心
        //      加一块透明"); 4.0 ⇒ 太碎, 像泡沫。**scale 越大团越短。**
        // 🔴 **`× ms`(`sand_motion_scale`) 是必需的, 而且它不是"速度补偿"**(那个已经撤掉):
        //    材料坐标里 `τ = ∫dd/v_scaled`, 而短周期会**整体压缩时间**(v0p=60·ms, gp=450·ms²)
        //    ⇒ 同一个屏幕深度上 `Δmat_y ∝ 1/ms` ⇒ 图案被**拉长 ms 倍**。
        //    实测(1080×1920, 1s 档, ms≈8): 不加 ⇒ 整条柱子糊成一条渐变, **一个团都看不见**;
        //    加 `ms` 之后与 15s 档同一副面孔。
        //    ⚠️ 与"不许乘深度相关因子"不冲突: `ms` 在一轮里是**常数**(只由周期决定),
        //      所以它不破坏平流(等值线仍是材料等值线), 只是把"这次动画被压缩了多少"除回去。
        float ms_c = max(0.05, sand_motion_scale);
        float cl_w = max(1.0, sand_free.z);
        vec2 cl_p = vec2((sand_position.x - sand_geometry.x) / cl_w * sand_clump_xscale,
                         mat_y * (diameter / cl_w) * sand_clump_scale * ms_c);
        cl_dbg = cl_p;
        clump = clamp(clump_field(cl_p, sand_clump_warp), 0.0, 1.0);
        // 🔴 **团的**大小**要参差**(用户原话: 「形成的一些**不规则**的形状, 形成的有**一大条**什么的」)。
        //    只有一个尺度的噪声 ⇒ 每 70px 断一次、断口一样深 ⇒ 读起来像机器压出来的。
        //    再取一个**更慢**的噪声当"这一段的缝有多深": 深的地方整团分开、
        //    浅的地方上下两团**并成一大条**(缝还在, 但只是变淡)。
        //    ⚠️ 频率要选在"**几个团**"这个尺度上: 0.09(≈11 个团) 太慢 —— 实测整条柱子
        //    只拿到一个值 ⇒ 变成"一大块实心 + 一大块透", 反而没有"团"了。
        clump_slow = clamp(clump_noise(cl_p * vec2(0.45, 0.30) + vec2(41.3, 17.9)),
                           0.0, 1.0);
        }
    }
    if (sand_flow_vy != 1.0) {
        velocity.y *= sand_flow_vy;
    }
    float jitter = 0.2 * sin(from_mouth.x * 3.1 + from_mouth.y * 5.2);
    // 🔴 **2026-10-12(2.89): 上球/颈部**冻钟** —— 1.244 的做法就是"时钟从不推进"。**
    //    用户判词:「我要的是把流动做成是 **v1.244 类似的**, 你现在的这个流动**速度太快了**,
    //    **方向可能也不对**。你深入对比看看」。
    //    实测(相位相关 + 与 1.244 同时刻逐像素比, 平板口径 50s):
    //      | 臂 | 图案位移(真峰) | vs 1.244 像素差 |
    //      | 1.244 | 测不到 | 0% |
    //      | 2.87(旧) | 净位移 0(只有每秒一次的沸腾) | **7.5%→0%(相位对齐时重合)** |
    //      | 2.88(我上一版) | **(−155,+77) px/s**(太快/方向错) | 70% |
    //      | 2.89-第一版(绕开公式) | 测不到 | **70%** ← 花纹本身变了(少了 `−velocity*0.5` 等偏移) |
    //    ⇒ 2.75~2.87 那套"GPU 流动"**本来就是净位移 0 的沸腾**(文档: "前进一个周期再原地弹回"),
    //      1.244 的"没有流动"只是**钟没推进**(`update_flow` 只在 material/palette 变化时写它)。
    //    ⇒ **正确做法 = 冻钟**(不是绕开公式): 上球/颈部的采样公式**一字不动**,
    //      只把 `sand_clock` 换成常数 0 ⇒ 与 1.244 **同一张图案**、且完全静止。
    //    旋钮 `sand_up_scroll`: 0 = 冻钟(出货默认) · >0 = 刚性下滚(2.88, A/B) · <0 = 旧行为(2.87)。
    float free_s = (sand_free.x > 0.0 && sand_position.y < sand_free.x) ? 1.0 : 0.0;
    float up_static = (sand_adv_cont > 0.0 && sand_up_scroll == 0.0
                       && free_s < 0.5) ? 1.0 : 0.0;
    float t = (up_static > 0.5 ? 0.0 : sand_clock) + jitter;
    float a = fract(t);
    float b = fract(t + 0.5);
    // 🔴 **2026-10-10: 平流的**时间项**必须连续。**
    //    用户判词:「沙柱里面那个**不规则的大颗粒给人的感觉是向上移动**」。
    //    实测(调试档 `HG_UV_DEBUG=10` 把 `-velocity.y` 画成红): `sand_free_drift` 0/1/8
    //    ⇒ 出口以下红度 162/166/**189** ⇒ **药是接上线的**; 可颗粒就是不动。
    //    根因: 下面每一条采样都写成 `... - velocity * a`(`a = fract(t)`)
    //    ⇒ 图案**前进一个周期再原地弹回**, **净位移恒为 0** ⇒ 速度再大也不动。
    //    ⇒ 全部改用连续时间 `adv`(权重仍走 `fract`, 交叉淡化照旧)。
    //    `sand_adv_cont = 0` ⇒ 逐字回到旧行为(可作负对照)。
    // 🔴 **2026-10-10 收紧: 只作用于**出口以下**。** 用户:「**不要影响上层沙子的材质**」。
    //    第一版是全局的 ⇒ 上球沙体被改成竖条纹(实测: 用"确定是沙"的窗口比
    //    `HG_ADV_CONT` 两臂, 差 **5894 px**; 我先前那个窗口落在**空玻璃**上, 所以量到 0 —— 又踩一次)。
    float adv = (sand_adv_cont > 0.0
                 && sand_free.x > 0.0 && sand_position.y < sand_free.x) ? sand_clock : a;
    // 🔴 **2026-10-11: 连续时间那一支必须用 `sand_clock`, 不能带 `jitter`。**
    //    `jitter` 是**每像素 0.2"秒"**的相位偏移(它是拿来错开交叉淡化的**相位**的,
    //    见 `float t = sand_clock + jitter`), 而下面每一条采样都是 `uv - velocity * adv`
    //    ⇒ 它一进**位移**, 就变成 `velocity * jitter` 的**空间扭曲**:
    //         K=0 (V=50px/s)   → 扭曲 ~6.5%   (可忽略, 实测 r=0.998 干净)
    //         K=1 (V=514px/s)  → 扭曲 **66%** (纹理被抹开, 平移读不出来)
    //    实测(`vel=0` 两条相关曲线, 粗纹理、dt=0.02、清粒子):
    //         K=0: 峰在 **dy=+1** r=**0.998**  (50px/s×0.02 = 1px ✓ 分毫不差)
    //         K=1: 峰在 dy=−3 r=0.925, r(0)=0.839, r(+10)=0.797 ⇒ **曲线变宽、
    //              位移只有应有值的 ~1/3** ⇒ 我先后读成"向上 250px/s"(那是伪影)
    //    这正是 Valve flow-map 的标准做法: **低频噪声只偏移"相位", 不偏移"位移"**。
    //    ⇒ 位移用**不带 jitter** 的 `sand_clock`; 相位(权重 `a`/`b`、`floor(t)`)照旧带 jitter。
    // 🔴 **2026-10-11: 自由段**关掉第二相位**(`sec = 0`)。**
    //    两件事:
    //    ① **它是多余的**: 第二相位的全部作用是"藏住 `fract` 的回跳", 而自由段
    //       现在用**连续时间 `adv = t`**, 根本没有回跳可藏。
    //    ② **它带着一个真 bug**: `n2`(右边那条边)原来喂的是 `b = fract(t+0.5)`,
    //       那是**另一条时间轴**、净位移恒为 0 ⇒ **右边不动、左边在动**, 两条边会错开。
    //       现在 `sec = 0` ⇒ 两条边都走 `adv`(同速)。
    //    ⚠️ **我一度以为它也解释了 K=1 下"材质朝上 250px/s"** —— **那是错的**:
    //      关掉第二相位之后实测**仍然 −250**(见 `sand_free_drift` 那段)。
    //      真因是**周期性纹理的车轮效应**(探针在 dt=0.004 下的量子就是 250px/s)。
    //    `sec = 1` 的区域 ⇒ **逐字旧行为**。
    float sec = (sand_adv_cont > 0.0
                 && sand_free.x > 0.0 && sand_position.y < sand_free.x) ? 0.0 : 1.0;
    float delta = luma(texture2D(texture0, fract(uv)).rgb) - luma(sand_base);
    float wa = 1.0 - abs(1.0 - 2.0 * a);
    float wb = 1.0 - wa;
    vec2 jump = vec2(0.125, 0.0625 * sand_jump_y);
    // 🔴🔴 **2026-10-12(2.91) 更正: "1.244 的钟冻住了" 是错的 —— 它每帧都写 `sand_clock`。**
    //    用户判词:「上层的沙子会不断的**扰动和刷新** …… 你看看 v1.244 是怎么做的, 好像就没有」。
    //    实测(平板口径 50s, 上球沙体 |Δ帧|): 旧路径每隔 **1.00 秒**一次尖峰
    //    (max/中位 **1.79**, 尖峰落在 t=x.85); 1.244 = **1.03**(无尖峰)。
    //    **真根因不是钟, 是相位 b 的位移式子写错了** —— 见下面 `samp_b` 那一段:
    //    `adv + 0.5` 与 1.244 的 `fract(t+0.5)` 在 `a ≥ 0.5` 的半个周期里差**一个 `velocity`**
    //    (平板 ≈50px), 而相位 b 的权重恰在 `a → 1` 时最大 ⇒ 每秒一次图案跳变。
    //    ⚠️ 1.244 的上球材质**一直在动**: `−velocity·fract(sand_clock)`, 实测 **25~50 px/s**、
    //    **越靠颈口越快**(`sink` 里那个 `8·from_mouth.y²` 项)、**无任何台阶** ——
    //    两个 `fract` 回跳与 `floor(t)·jump` 都恰好落在该相位**权重为 0** 的那一帧上, 本就藏得住。
    //    ⇒ 我先后两次误诊都**过头**了: **2.88**(刚性下滚)把它做成真平移(−155,+77)px/s,
    //    快 4.6 倍且多出横向; **2.89**(冻钟)连那条 25~50px/s 的漂移一起冻掉
    //    (用户:「现在上面的沙子**完全不变化**了」/「移动**幅度比较小**」)。
    //    ⇒ **出货默认 = −1(钟在跑 = 1.244 的路)**, 逐像素对齐; 0 / >0 只作 A/B。
    float up_scroll = (sand_adv_cont > 0.0 && sand_up_scroll > 0.0
                       && free_s < 0.5) ? 1.0 : 0.0;
    // 自由段(sec=0)把整个采样参数乘 `sand_free_coarse` —— **位置和平流一起乘**,
    // 所以屏幕上的速度不变、只把纹理放大(颗粒变粗)。其余区域系数恒 1 ⇒ 逐字旧行为。
    float ck = (sec < 0.5) ? sand_free_coarse : 1.0;
    vec2 samp_a = uv - velocity * adv;
    // 🔴 **2026-10-12(2.91): 相位 b 的位移必须用 `fract(t + 0.5)`(= 1.244 原式)。**
    //    原先写的是 `adv + 0.5 * sec`, 而它只在 `a < 0.5` 时等于 `fract(t + 0.5)`
    //    (那时 `fract(t+0.5) = a + 0.5`); 一旦 `a ≥ 0.5`, `fract(t+0.5) = a − 0.5`
    //    ⇒ 两者差**整整一个 `velocity`**(平板口径 ≈50px) —— 而相位 b 的权重恰在
    //    `a → 1` 时最大 ⇒ **每秒一次的图案跳变**(就是用户说的"扰动和刷新")。
    //    实测(平板口径 50s, 上球沙体 |Δ帧|): 旧写法 max/中位 = **1.79**(尖峰在 t=x.85);
    //    1.244 = **1.03**(无尖峰); 改回 `b` 之后两臂同刻**逐像素 0 差异**、尖峰消失。
    //    (自由段 `sec == 0` ⇒ 取 `adv`, 与旧写法逐位相同, 那一侧一字未动。)
    float b_off = (sec < 0.5) ? adv : b;
    vec2 samp_b = uv - velocity * b_off;
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
    float ga = grain(samp_a * ck + floor(t) * jump * sec * (1.0 - up_scroll));
    float gb = grain(samp_b * ck + floor(t + 0.5) * jump * sec + vec2(0.5));
    float detail = (sec < 0.5)
        ? ga * sand_free_grain
        : ((up_scroll > 0.5) ? ga
           : (ga * wa + gb * wb) * inversesqrt(wa * wa + wb * wb));
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
        // Mirrored grain noise is odd across the tube: no net added cap volume.
        float left = grain(vec2(u, sand_clock * 0.25 + 0.37));
        float right = grain(vec2(1.0 - u, sand_clock * 0.25 + 0.37));
        float top = sand_tail.x + sand_tail.y * clamp((left - right) * 0.7, -1.0, 1.0);
        coverage = 1.0 - smoothstep(top - 0.5, top + 0.5, sand_position.y);
    }
    float tail_coverage = coverage;
    if (sand_free.x > 0.0 && sand_position.y < sand_free.x) {
        // 出口以下那股沙**不再被玻璃约束** —— 它现在是"落下来的一把沙"。
        // 用户判词: 「沙柱看起来是个规整的矩形」 —— 两条边死直、平行、通体实心。
        // 两条边**各用各的噪声**(不是镜像: 玻璃没了, 没有"平均宽度不变"这条约束),
        // 于是宽度会随深度呼吸, 边缘毛掉; 内 62% 保持实心。
        float u = 0.5 + (sand_position.x - sand_geometry.x) / (2.0 * sand_free.z);
        // 🔴 **2026-10-10: 边缘场也必须含 y、而且被平流** —— 与下面洞场**同一条教训**。
        //    `n`/`n2` 原来的第二个采样坐标是 `sand_clock * 0.25`(**整帧同值**)
        //    ⇒ 同一帧里沿 y 恒定 ⇒ 两条边在**构造上就是笔直的竖线**,
        //    宽度不会"随深度呼吸"(`sand_clock` 只让整条边**整体**左右呼吸)。
        //    用户判词:「沙柱就只有一个问题了, **边缘是个竖线 太规整了**, 和现实中的差异太大」。
        //    修法与洞场逐字同款(洞场当年就是被这条咬过才改的, 见下面 `hn` 那段的注释):
        //    按球径归一的**二维**坐标 + `flowing` 那一套平流偏移 ⇒ 边随沙走、跟着落。
        // 🔴 **2026-10-10: 这个噪声场的 x 与 y 必须**同一个尺度**。**
        //    原来 `euv = vec2(u, …)` 里 `u` 是按**管宽**归一的(`u` 横跨 0→1 只有
        //    `2·sand_free.z` ≈ 36px), 而 y 是按**球径**归一的(1.0 = diameter ≈ 808px)
        //    ⇒ **两轴差 ~22 倍** ⇒ 那个噪声在画面上是**竖条**(1 纹素宽 ≈ 0.08px ⇒ 亚像素,
        //    纵向 1 纹素 ≈ 1.6px 可见) ⇒ 边框那一圈被腐蚀成**纤维**、边缘被锯齿成
        //    "看起来还是直线"(亚像素级抖动被平均掉了)。
        //    用户判词: 「整个边缘非常奇怪…**额外带了一层直线**」+「柱子内部是竖条纤维」。
        //    `u` 仍然保留给 `r01`(0=中轴 1=边)与左右分支用 —— 只是**不再喂给噪声坐标**。
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
        // 🔴 **2026-10-10: `euv.y` 是"从出口往下量" ⇒ 向下为正, 而 `velocity`/`jump`
        //    是按**球体 uv(y 向上)**定的 ⇒ y 分量必须翻号。**
        //    不翻的后果(**分场实测**, 调试通道 `uvdebug` 3(覆盖率) vs 4(颗粒), 同一状态、
        //    dt=0.02、各 19 帧, 平板口径 1080×1920):
        //        颗粒场 **+50 px/s 向下**(z **22.1**) · 覆盖率场 **−50 px/s 向上**(z **7.1**)
        //    两者**同幅反向** ⇒ 同一层材质里两个纹理对着爬, 而洞/边正是柱子里最显眼的
        //    那个尺度(2.42 自己记过"柱子里可见的纹理主要是洞与边在画")
        //    ⇒ 用户看到的就是「**大颗粒往上走**」。这是它的**根因**, 不是错觉。
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
        // 🔴 **聚团的缝权重**(0 = 缝, 1 = 团心) —— **必须在这里算**, 因为下面
        //    "可见边缘"与"覆盖率"两处都要用它。`clump_g = 0`(出口那一行 / 诊断档) ⇒ 恒 1。
        //    ⚠️ 2026-10-11: 这里原来还提到一个叫 `sand_clump_span` 的量 —— **那个名字
        //    从来就没有过**(uniform 与旋钮表里都查不到, 全仓库只剩这条注释)。是写这一版时
        //    留下的**过期引用**, 会让人以为存在一个可调的"缝深参差"旋钮。已删。
        // 🔴 **"有连续、有断续"** (用户 2026-10-11 第二轮判词):
        //    「后期散的也太离开了 不符合物理规律吧」+「理论上应该是**有连续 有断续**才合理」。
        //    上一版的病: `0.35 + 1.30·slow` 的**地板 0.35** ⇒ 连"并在一起"的地方也带 35% 的缝
        //    ⇒ 整条柱子**均匀地花**了, 读成"被打散"而不是"有团有缝"。
        //    现在: 缝只在**慢噪声高**的地方才开 ⇒ 大部分柱身**完全连续**,
        //    少数地方**真的断开**(而且 `sand_clump_gap` 仍是"透到底"的程度)。
        float clump_open = (1.0 - smoothstep(sand_clump_lo, sand_clump_hi, clump)) * clump_g
                         * clamp((clump_slow - 0.30) * 2.2, 0.0, 1.0);
        clump_open = clamp(clump_open, 0.0, 1.0);
        // ⚠️ **`clumpgap = 1` 必须是真的"全关"**: 轮廓那一处要说它俩是同一件事,
        //    否则关掉之后**边还在被收窄**(2026-10-11 自查出来的: 那样 OFF 臂就不是
        //    "旧行为", 拿它当并排图的左边是错的)。覆盖率那处用 `sand_clump_gap` 本身,
        //    轮廓这处再乘一个 `(1-gap)`, 于是 `gap=1` ⇒ 两处都不动。
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
        // 🔴 **流动诱导的聚团**: 团心保持上球那种密度, **团与团之间透出背景**(缝)。
        //    ⚠️ 方向: 第一版是"给实心柱压花纹"(均值不变) ⇒ 用户判词「右侧还是一个固体啊」。
        //      现在低噪声处**真的乘小 coverage** ⇒ 缝里看得见背景。
        //    ⚠️ `clump_g = 0`(出口那一行 / `freevscroll` 诊断档) ⇒ 系数恒 1 ⇒ 与上球无缝。
        coverage *= mix(1.0, sand_clump_gap, clump_open);
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
            // ⚠️ `huv.y` 同样向下为正 ⇒ 用翻过号的 `evel`/`ejmp`(见上面 `euv` 那段实测)。
            float hn = grain(huv - evel * adv + floor(t) * ejmp * sec);
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
            // 🔴 **2026-10-11: 孔隙率随深度升高的"宽度"分量 —— 见 `sand_hole_wide`。**
            //    `rim * fr` 只放开**边缘那一圈**(芯部 `rim=0` ⇒ 按构造永不打洞)
            //    ⇒ 用户要的「越往下空隙越多」在**柱内**量不到(实测: 默认 0.42/0.56/0.00%,
            //    梯度还是**反的**; `holegrow=0.8` 也只到 0.29/0.29/0.12%)。
            //    ⚠️ **2026-10-11 实测: 二值"分片闸门"这一版做不出渐变。**
            //    `wgate`(由 `grain()` 造的场)是**双峰**的 ⇒ `wallow` 只在
            //    `wide` 越过某个深度阈值时**整片跳开**: 扫 `holewide` 0.5/0.6/0.7 得
            //    `0/0/0/29/0/0%` → `0/0/71/48/100/0%` → `0/48/86/62/100/0%`
            //    ⇒ 要么几乎不动, 要么一路开到 100%(材质整层消失)。**都不是"渐渐变松"。**
            //    ⇒ 改成**分数混合**(平滑、单调、可控): 洞场的像素按 `wide` 比例变淡,
            //    远处看是"越往下越透", 近处仍是颗粒, 且**不会整层消失**。
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
    vec3 final_rgb = outlet_join ? neck_flow_rgb(sand_position)
                                : mix(original.rgb, flowing, sand_mix);
    gl_FragColor = frag_color * vec4(final_rgb,
                                     original.a * coverage);
}
"""


def _flag_float(name, fname):
    """`HG_<name>` 环境变量优先(桌面), 其次**本模块自己所在目录**的 `<fname>` 标记文件。

    🔴 安卓 app **读不到宿主 shell 的环境变量** ⇒ 设备上做单变量对照只能写标记文件
    (app 私有目录, 与 `flowrate` / `maxfps` / `blit.off` 同一套)。
    ⚠️⚠️ **目录是 `<app>/tools/`, 不是 `<app>/`** —— 代码读的是 `dirname(abspath(__file__))`。
    这个文件被推到设备上的位置就是 `<app>/src/app/sand_flow_material.py`, 所以:
        adb shell "echo 0.30 > /data/data/org.shalou.hourglass/files/app/tools/seamband"
    (`main.py` 自己的探针读 `<app>/` —— **两处不一样**, 写错那边 = 该臂**空转**,
     而回读照样读得到, 会被读成"没差别"。本项目为这个目录差栽过不止一次。)
    ⚠️ 旧 docstring 写的是"与 main.py 同目录"并举例 `<app>/seamband` —— **那是错的**
    (2026-10-11 由两名专家各自独立查出), 2026-10-11 已改。
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
        self["sand_clock"] = 0.0
        self["sand_mix"] = 0.0
        self["sand_tail"] = (0.0, 0.0, 1.0)
        self["sand_free"] = (0.0, 0.0, 1.0, 0.0)
        self["sand_outlet_join"] = (0.0, 0.0)
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
                #    标定见 `main.py` 的 `_trail_scale_probe`；梯子图 `_vid/ladder2.png`。
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
            radius=self['sand_geometry'][1]
            roof=self['sand_geometry'][2]+radius+math.sqrt(max(0.0,radius*radius-(x-cx)**2))
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
