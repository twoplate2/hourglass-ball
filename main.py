"""
跳跳的沙漏 — Android (Kivy) 版
====================================
从 pc/hourglass_v2.py(tkinter + PIL 真圆 + 完整球 + 球体积微积分 + v2 布局)
**从零重写**为 Kivy。不复用 android 旧 main.py。

核心移植决策:
- 渲染: 玻璃壳和沙体都用 Kivy 真圆 —— Ellipse 画球, Stencil 裁出弓形沙面。
  复刻 pc"玻璃和沙必须同一种真圆技术,否则边缘失配(月牙/缝)"的核心原则。
  **不用 Mesh / 多边形拼弓形**(那是 pc readme 明令禁止、也是边缘失配的根源)。
- 坐标: Kivy y 向上(原点左下), pc 是 y 向下 —— 所有几何上下翻转。
- 几何: 自适应 widget 尺寸; 在 pc 的 380x730 比例下复现 R≈168。
- 守恒: 上沙(1-raw) + 下沙(raw) = 1, 由完整球对称 v(t)+v(1-t)=1 严格成立。
"""
import math
import os
import random
import struct
from array import array
from bisect import bisect_right     # 下球沙堆面积表求逆(_MoundArea)
import sys
import time
import wave
import json


# 沙流粒子的并行数组字段(见 NUMPY_PLAN.md)。numpy 缺失时整条向量化路径关闭,
# 自动退回 update_particles 里的原标量循环 —— 不给沙漏制造风险。
_P_FIELDS = ("px", "py", "pvy", "pxo", "pwp", "pwa", "psz", "ptl", "pli", "pdt")
# 向量化的固定开销(每个桶一次 np.array/argsort/bincount, 每次 numpy 调用 ~5-20µs)
# 在粒子少时会盖过 O(pn) 循环省下的时间。设备实测: 1 秒档(约 278 颗)打包+分组
# 反而慢 0.92ms, 5 秒档(约 1695 颗)才转正。阈值取两者之间, 低于它走原标量路径。
_NUMPY_MIN = 800


class _FlowView:
    """本帧粒子数组的 Python list 快照, 渲染层按**下标**读它。

    为什么要它: `px[i]` 每读一次都要新建一个 np.float64 标量对象, 逐颗粒读比读 dict
    还慢; `arr[:pn].tolist()` 一次 C 循环就把字段摊成原生 float(2500 颗 × 7 个字段
    合计约 0.08ms), 循环里读到的就是普通 float。字段对应见 `_p_refresh_view`。
    """

    __slots__ = ("n", "x", "y", "vy", "tl", "sz", "light", "wp",
                 "nx", "ny", "nvy", "ntl", "use_np")

    def __init__(self):
        self.n = 0
        self.x = self.y = self.vy = self.tl = self.sz = self.light = self.wp = []
        # numpy 零拷贝切片, 只给向量化打包用(见 tools/flow_texture_experiment.py)。
        self.nx = self.ny = self.nvy = self.ntl = None
        self.use_np = False


_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
try:
    import numpy as _np
    import flow_numpy as _flow_numpy
except Exception as _exc:                    # 别静默: 退回标量循环时要能从 logcat 看出来
    _np = None
    _flow_numpy = None
    print("numpy flow core unavailable, using scalar loop: %r" % (_exc,))

import gc

from app_version import APP_VERSION

# ---- 生产: 关掉 Kivy Clock 的软件节拍(仅 Android) ----
# kivy/clock.py `ClockBaseBehavior.idle()` + `_check_ready`(实测 resolution = 1/(3*fps)):
#   done = (sleeptime - 4/5*min_sleep <= min_sleep),  sleeptime = 1/fps - 本帧已耗时
#   => 本帧耗时 W >= 0.4/fps 才不睡; W < 0.4/fps 时帧间隔被顶到 (11/15)/fps, 与 W 无关。
#      maxfps=60  -> 悬崖 6.667ms, 台阶 12.222ms(逐帧直方图实测 12.1~12.6)
#      maxfps=120 -> 悬崖 3.333ms, 台阶  6.111ms(实测 6.0~6.3) —— 只是把台阶挪近, 没取消
#   台阶是"算得越快被顶得越死"; 且 6.111ms 在 165Hz 面板(vblank 6.06ms)上会掉一整帧。
# maxfps=0 => idle() 整段跳过, 完全不休眠, 节拍交给 vsync。安卓的 buffer swap 必然等
#   SurfaceFlinger 的 vblank, 所以 0 不会空转; 120Hz 机上 120 与 0 等价, 165Hz 机上 0 更好。
# 只在 Android 生效: 桌面无 vblank 兜底, 设 0 会纯烧 CPU, 也会改掉测量工具的节拍。
from kivy.config import Config          # ⚠️ 必须在下面那个 if **之前**！
# (2026-10-03 事故: 改这段注释时把这行 import 删了 ⇒ 1.40~1.46 七个版本在 Android 上
#  启动即死 NameError: name 'Config' is not defined。桌面测不出来 —— 这行只在
#  Android 分支执行, 而 ast.parse 只查语法不查名字。见 README 经验教训。)

if "P4A_BOOTSTRAP" in os.environ or "ANDROID_ARGUMENT" in os.environ:
    Config.set('graphics', 'maxfps', '0')

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase, Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics.texture import Texture           # 沙体材质要用(见 _SandMaterial)
from kivy.graphics import (Color, Rectangle, Line, Ellipse, Quad,
                           StencilPush, StencilUse, StencilUnUse, StencilPop,
                           PushMatrix, PopMatrix, Rotate, InstructionGroup)
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform
from frame_benchmark import (BenchmarkHoldArea, BenchmarkRunner, PERIODS,
                             BenchmarkFrameChart,
                             format_benchmark_result, format_benchmark_report,
                             benchmark_log_text, save_benchmark_log)


# ---- 中文字体: 用 name="Roboto" 覆盖 Kivy 默认字体,全局生效 ----
_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fonts", "NotoSansSC-Medium.otf")
try:
    if os.path.exists(_FONT_PATH):
        LabelBase.register(name="Roboto", fn_regular=_FONT_PATH,
                           fn_bold=_FONT_PATH)
except Exception:
    pass


SAND_PRESETS = [
    ("金沙", "#d9a360", "#b88040", "#e6b870"),
    ("红沙", "#c4523e", "#8e3220", "#d97560"),
    ("蓝沙", "#4a8ec4", "#2e5e87", "#6dabd4"),
    ("绿沙", "#7ba83e", "#4d7820", "#97c45e"),
    ("紫沙", "#8e6db0", "#5d4280", "#a98ac4"),
    ("黑沙", "#4a4540", "#2a2520", "#6a6560"),
]
BG_COLOR = "#fdf6e3"
GLASS_FILL = "#eaf3f8"
GLASS_OUTLINE = "#5f6b70"

# ---- 沙体材质(路线 A: 预生成的 RGBA 彩色纹理) ----------------------------------
# 依据: meishu.md §7 + 外部评审 meishu2.md §3。三条被评审纠正过的前提, 记在这里免得重犯:
# ① **不能**用"灰度 × 沙色": 灰度相乘只能**压暗**, 做不出比 sand_base 更亮的颗粒,
#    黑沙尤其需要独立亮色端点 ⇒ 三端点插值后直接烘成 albedo, 前面用**白色** Color
#    (否则彩色纹理会被**再染一次**而明显发暗)。
# ② **不能**用 32×32: 球内径 800px 时一个纹素盖 25px, 存得下柔和渐变、存不下细颗粒。
# ③ 生成是 O(n²) 的纯 Python/numpy 计算, **绝不能在 redraw 里调**; 按配色缓存。
SAND_MATERIAL = os.environ.get("HG_SAND_MATERIAL", "grain")   # grain | flat(退回旧平色)
SAND_MATERIAL_SIZE = 512        # 512² ⇒ 球内径 800px 时约 1.56 px/纹素
SAND_MATERIAL_GRAIN = float(os.environ.get("HG_SAND_GRAIN", "0.35"))   # 颗粒强度(浓度)

# ---- 「沙体颗粒」六档 (A-F) ----------------------------------------------
# **2026-10-05 用户在对照图里逐档比过、选定 D 为默认**(隐藏菜单可改)。
#   (标签, 竖向明暗强度, 颗粒粗度倍数)
#   对照图: `_shot/sandmat/sandmat_full.png` / `sandmat_upper_1to1.png`
#   ⚠️ 与「沙子浓度」滑杆是**两个不同的旋钮**: 滑杆调 `SAND_MATERIAL_GRAIN`(强弱),
#      这里调的是**明暗落差**(grad)与**颗粒粗细**(coarse)。用户 2026-10-05 的原话:
#      "第一个没有任何意义吧, 因为我可以在设置中设置啊" —— 那句是把两者当成同一个了,
#      实测滑杆到不了这里的任何一档 ⇒ 故单独做成档位。
#   ⚠️ 标签用**数字 1-6**(用户 2026-10-05: "你的选择应该是用数字, 而不是 abcd")。
#   ⚠️ **2026-10-05 重定**: 原表 1/2/3 只差"明暗落差(grad)" —— r17-1号 在设备上逐档整幅差分,
#      查出「1 vs 2」「2 vs 3」在沙体区域 **0 个像素 > 15 级**, 「4 vs 6」整幅只有 1 个像素 > 8 级
#      ⇒ **六档实际只有三种画面, 三个按钮是摆设, 标签还在骗人**。
#      主持人事后用 `tools/verify_grain_levels.py` 复现并获得决定性结论:
#      **grad 这条维度根本走不通** —— 加到 1.0/2.0/3.0(已饱和) 可见占比也只有 0.06%/0.84%/0.00%,
#      因为沙色调色板 base→light 只有十几级, 再大的渐变也翻不出可见差。
#      ⇒ 改成**只有"颗粒粗细"一条维度**的梯子(1 最细 → 6 最粗), 每个相邻对都实测可见。
SAND_GRAIN_LEVELS = (
    ("1", 0.10, 1), ("2", 0.10, 2), ("3", 0.10, 3),
    ("4", 0.10, 4), ("5", 0.10, 5), ("6", 0.10, 6),
)
# ⚠️ **「沙体颗粒」表的版本号** —— 这张表的"含义"改过一次(2026-10-05 grad→只看粗细),
#    同号的档已经不是同一个东西 ⇒ 对不上就回落默认, 免得用户存的"3"被静默换成另一种颗粒。
# ⚠️⚠️ **只锁这一张表!** 第一版把它做成了"两把锁共用一把"(`levels_rev` 同时管 grain 与 rough),
#    而 **rough 表的含义从来没变过** ⇒ **用户明确选过的 rough=4 被一起作废、静默降到 3(降 33%)**。
#    这是 r18-1号 2026-10-05 在设备上查出来的 —— **静默改用户设置**, 比不改还糟。
SAND_GRAIN_REV = 2
SAND_GRAIN_LEVEL_DEFAULT = "1"      # 1 = 最细(≈1.7px 颗粒) —— **用户最后选的是"细颗粒"那一档**
#   ⚠️ 档号变了: 用户 2026-10-05 说的 "C(=3)" 在原表里是"细颗粒", 新表里**细颗粒 = 1**。
#      观感保持不变, 只是号码从 3 挪到 1 —— 已在给用户的汇报里写明。

# ---- 「沙面起伏」六档 (A-F) ----------------------------------------------
# 值 = 起伏幅度 ÷ 直径。**同一张对照图里用户选定 D**(设备 ≈5.35px; 旧值是 A = 1.25px)。
#   ⚠️ 选它的实测理由: 旧值铺在 890px 宽的沙面上只占 0.14% —— 逐列量出来沙面中心
#      只比两侧低 10px/640px, 四位评审独立说"像水位/像液面"。
#   ⚠️ 三个独立读图的人里有两个把再上一档(E=7.13px)排到最后("像被挖过或堆过")。
#   ⚠️ 标签同样用**数字 1-6**, 且**单调**: 1 最平 → 6 最毛。
SURFACE_ROUGH_LEVELS = (
    ("1", 0.0014), ("2", 0.0025), ("3", 0.0040),
    ("4", 0.0060), ("5", 0.0080), ("6", 0.0120),
)
# ⚠️ **默认 = 4**(设备 ≈5.35px) —— 这是**用户 2026-10-05 亲口选的那一档**。
#    1.100 里被主持人在"改颗粒表"的同一次编辑中**顺手改成了 3**, 提交说明里没提,
#    表头上方注释还写着"≈5.35px" —— 代码与注释互相矛盾, 而且静默改了用户的值。
#    r18-1号 在设备上逐条比对 git 才查出来。**已改回 4。**
SURFACE_ROUGH_LEVEL_DEFAULT = "4"


def _grain_level(label):
    """标签 → (明暗强度, 颗粒粗度)。认不得就回出厂默认。"""
    for lb, grad, coarse in SAND_GRAIN_LEVELS:
        if lb == label:
            return grad, coarse
    for lb, grad, coarse in SAND_GRAIN_LEVELS:
        if lb == SAND_GRAIN_LEVEL_DEFAULT:
            return grad, coarse
    return SAND_GRAIN_LEVELS[0][1], SAND_GRAIN_LEVELS[0][2]


def _rough_level(label):
    """标签 → 起伏幅度系数。认不得就回出厂默认。"""
    for lb, frac in SURFACE_ROUGH_LEVELS:
        if lb == label:
            return frac
    return dict(SURFACE_ROUGH_LEVELS)[SURFACE_ROUGH_LEVEL_DEFAULT]


def apply_rough_level(label):
    """按标签写 `UPPER_ROUGH_FRAC`(开机时用; 运行中换档走 `HourglassWidget.set_rough_level`)。"""
    global UPPER_ROUGH_FRAC
    UPPER_ROUGH_FRAC = _rough_level(label)
    return UPPER_ROUGH_FRAC


def current_grain_level():
    """当前生效的「沙体颗粒」档标签(给隐藏菜单打高亮)。"""
    for lb, grad, coarse in SAND_GRAIN_LEVELS:
        if abs(grad - SAND_MATERIAL_GRAD) < 1e-9 and coarse == SAND_MATERIAL_COARSE:
            return lb
    return SAND_GRAIN_LEVEL_DEFAULT


def current_rough_level():
    """当前生效的「沙面起伏」档标签。"""
    for lb, frac in SURFACE_ROUGH_LEVELS:
        if abs(frac - UPPER_ROUGH_FRAC) < 1e-12:
            return lb
    return SURFACE_ROUGH_LEVEL_DEFAULT


SAND_MATERIAL_GRAD, SAND_MATERIAL_COARSE = _grain_level(
    os.environ.get("HG_SAND_LEVEL", SAND_GRAIN_LEVEL_DEFAULT))
# 环境变量仍可单独覆盖(取图与 A/B 用), 但要**逐项**给, 别让本地配置悄悄盖掉测量档位
def apply_grain_level(label):
    """按标签写 `SAND_MATERIAL_GRAD/COARSE`(开机时用)。"""
    global SAND_MATERIAL_GRAD, SAND_MATERIAL_COARSE
    SAND_MATERIAL_GRAD, SAND_MATERIAL_COARSE = _grain_level(label)
    return SAND_MATERIAL_GRAD, SAND_MATERIAL_COARSE


SAND_MATERIAL_GRAD = float(os.environ.get("HG_SAND_GRAD", str(SAND_MATERIAL_GRAD)))
SAND_MATERIAL_COARSE = int(round(float(os.environ.get(
    "HG_SAND_COARSE", str(SAND_MATERIAL_COARSE)))))
SAND_MATERIAL_SHADE = 1.0       # 宏观明暗强度
# 隐藏菜单(长按版本号)里的档位: (显示名, 模式, 颗粒强度)。默认 = 第二/三项之间那档。
# 颈部沙柱采样材质时的 v 锚点。两个值都被实测钉过，别随手改：
# ⚠️ **不能取 0.5** —— 那里明暗项恰好为 0(= 基准色), 颈部会比球底**亮一个档**, 仍然读成两种材料。
# ⚠️ **也不能取 0.0**（2026-10-04 对抗评审实测查出）: 沙柱顶比球底极点还高 5.3px, 取 0 会让
#    柱顶那 5 行的 v **全是负数**, 而材质 `wrap=clamp_to_edge` ⇒ 它们全采到纹理**第 0 行**,
#    一行 texel 被横向拉成 ~87px 宽的竖条纹带（就是"漏斗顶那道梳齿"）。
#    0.021 让柱顶 v≥0（0.0193 是最小值, 留一点余量）。
NECK_UV_ANCHOR = 0.021
# 沙子浓度滑块(2026-10-04 用户要求: 原来是「平色/淡/标准/浓」四档离散, 改成连续)。
# 0.0 = 只有宏观明暗、没有颗粒; 「完全平色」仍由 `HG_SAND_MATERIAL=flat` 保留(工具/A-B 在用)。
SAND_GRAIN_MAX = 0.70
SAND_GRAIN_DEFAULT = 0.35
# 拖动滑块时的预览分辨率。实测生成耗时: 128²=4.2ms / 256²=3.9ms / 512²=17.6ms
# —— 512² 每帧重建会卡在拖动上, 所以拖动只烘 128², 松手(或 0.35s 无操作)才烘正式的。
SAND_PREVIEW_SIZE = 128

# 沙面窄过渡(外部评审 meishu2.md §4.3): 紧贴沙面**内部**一条很窄的亮过渡。
# 他的规格: 厚度 1–3 逻辑像素 / 只向亮色端点轻推 / **不做整条白线、不加黑描边** /
#           近空时随可见厚度减弱(避免剩一条独立亮线) / 落点与碰撞高度保持一致。
# 代价: 每个沙体**新增一次局部绘制**(面积≈可见弦长×带宽), 不是零成本 —— 要单独计费。
SAND_SURFACE_BAND = 3.0     # 带宽(逻辑像素)
SAND_SURFACE_ALPHA = 0.55   # 亮度上限(轻推, 不是白线; 0.32 时被颗粒噪声淹没)
SAND_SURFACE_FADE = 14.0    # 沙体薄于这个厚度就按比例减弱
# ⚠️ **满球时没有自由表面** —— 亮带(横跨直径的一条 3px 矩形)会被球面 stencil 裁成一个
#    贴着内壁顶点的**透镜形亮弧**(实测 57px 宽 × 3px 高), 眼睛读成"沙和玻璃顶之间有缺口"
#    (2026-10-04 用户实拍: 未开始、10/10 时上球顶部那条偏亮的绿)。外部专家
#    xingzhuang2.md §4.1 同一判据:「满球时没有这条自由表面」。离球顶这么窄以内按比例收掉。
#    沙面一离开球顶就恢复: 高度跌 6px 只要 t≈0.0014(1s 档 1.4ms), 观感上是瞬间的。
SAND_BAND_APEX_FADE = 6.0

# ---- 沙面塑形: **已回退**(2026-10-04 用户裁决) ------------------------------------
# 试过"静态粗糙度 + 排水漏斗"(1.60/1.61), 依据是对抗评审的"位移是相对量、沙面下落时
# 每个凹凸跟着走 ⇒ 是可被追踪的特征"。**实测正相反**: 形状固定不变、只是整体平移,
# 读成"一张图在平移"; 用户原话"完全没有变化, 每次都是这样, 还不如平面"。
# ⚠️ 评审看的是**单帧裁图**, 而这个问题只有**跨时间**才看得出来 —— 同模型 panel 的共享
#    盲区, 也是"知觉判断只能由用户裁"的又一个实例。
# ⚠️ 下球的靠壁裙边(见下)保留: 它只在靠壁抬起、且不加粗糙度。
# 下球沙堆的形状: **休止角锥面(堆)**, 不是平顶的"水位线" —— 2026-10-04 用户实拍
# 「下面的这个沙子的形状完全不符合物理学吧」「简化版本的也不符合」。
#
# 球内局部坐标(x=0 中轴, y=0 球内底, R=内半径, D=2R):
#     B(x) = R - sqrt(R²-x²)                  圆内底
#     U(x) = R + sqrt(R²-x²)                  圆内顶
#     P(x) = a - m·max(0, |x| - b)            未裁剪堆面(**a 是虚拟锥顶高度**)
#     H(x) = clamp(P(x), B(x), U(x))          该列**接触高度**(粒子/尘埃判定用)
#
# ⚠️ 下面这六个坑是第一版实现踩的, 由外部专家 2026-10-04 逐条定位(`xingzhuang2.md` §2):
#   ① a 是**虚拟**高度, 可以高于球顶(可见部分由球裁剪) ⇒ 二分上界不能写 2R;
#   ② 平台半宽 b **不许**跟着"旧平顶弓形在该高度的弦宽"收缩 —— 那会在末期缩到 0.5px,
#      沙堆反而变尖、结束时会"倒退缩水"; b 由**落束覆盖范围**定, 随几何一起重建;
#   ③ 目标面积与候选面积必须**同一套离散口径**(解析圆弓 vs 25 列采样差 ~0.9%,
#      最后一小条永远填不上) ⇒ 都用同一张面积表算;
#   ④ 绘制折线必须**经过 ±b 转折点**, 且与碰撞/装饰共用同一份定义(不许三处各写一遍);
#   ⑤ 放开 a>D 之后 UV 不能按高度截取, 否则颗粒被纵向拉伸 ⇒ 下球改成固定 D×D 全 UV + carve;
#   ⑥ 形状缓存不能只认 elapsed(暂停时改尺寸/换周期 → 几何变了而时间没变)。
#
# ⚠️ **平台是碰撞接口的约束, 不是审美**: 粒子主流用的碰撞面是**一个标量**
#    `get_mound_top_y()`(见 update_particles / flow_numpy 的 `mound_top`)。只要落点满足
#       |x| ≤ b  且  B(x) ≤ a ≤ U(x)
#    标量就与可见面严格重合。**第三条在接近满球时必然失效**(圆顶各点高度不同) ⇒
#    专家 §5.2 摆了两条路: A=保平台钉住落束(末期满球时是近似), B=主流也按 H(x) 取接触高度。
#    **2026-10-04 起改走 B**(用户裁决平台不合理 + 专家 dingbu.md §7):
#      只画尖顶而碰撞仍用 `y <= mound_top`, 会出现"颗粒在斜坡上方消失 / 钻进沙堆还可见"。
#      A 是"用外观迁就旧接口", 现在接口跟着外观走。B 是碰撞接口变更 ⇒ 标量路径、
#      numpy 路径、命中回放三处必须同公式(见 tools/test_physics_equiv.py)。
MOUND_REPOSE_SLOPE = 0.60   # 休止角 tanθ ≈ 0.60 (≈31°); 专家: 只是当前风格的初值, 不是定律
# ---- 沙面形状: 取消平台, 改"微不对称尖堆 + 受限微粗糙"(外部专家 dingbu.md §3) ----------
# 用户裁决:「下面的沙子顶部是一个平台 这个绝对不合理, 哪怕是一个不太规则的随机锥形都不比
# 这个合理」。原先的平台半宽 = 1.8 × 管内壁半宽(≈120px @手机), 是为了让**标量碰撞面**
# `get_mound_top_y()` 与可见面严格重合才引进的折中 —— 现在形状优先, 碰撞改成按 x 查 H(x)。
MOUND_SLOPE_L = 0.58        # 左坡斜率(专家 §3.1 建议 0.58/0.62 —— 故意不对称, 破镜像)
MOUND_SLOPE_R = 0.62
MOUND_SHAPE_NODES = 65      # 表面轮廓控制点数(奇数 ⇒ 第 32 点正好在中心轴上, 画得出尖顶)
# ⚠️ **两个旋钮必须一起抬**(2026-10-05 dingbu2.md §4 取证): 幅度 = min(FRAC·2R, 限坡)。
#    限坡 = `SMOOTH × min(斜率) × Δx`, 而 Δx = R/32 ⇒ 限坡 ∝ R, 与 FRAC·2R 同量纲。
#    令两者相等得 `SMOOTH_crit = 103.2 × FRAC`:
#      FRAC=0.003 ⇒ crit=0.310; 而旧的 SMOOTH=0.25 < crit ⇒ **限坡恒生效**,
#      幅度被钉成 `R × 0.01938 × SMOOTH`, **与 FRAC 完全无关**。
#    实测(取证 AI 在 R=445.5 上跑): FRAC = 0.0025/0.003/0.004/0.006/0.01/0.05
#      ⇒ 幅度**全部** 2.1588px, 数组逐位相同, 渲染**逐像素 0 差异**。
#    ⇒ **FRAC 是个死旋钮**, 过去任何"把粗糙度调大"的尝试都静默无效。
#    现在: FRAC 提到 0.004(设备 0.004×891 = 3.56px), SMOOTH 提到 0.45 > crit=0.413
#    ⇒ 限坡**退出约束**, 幅度直接由 FRAC 决定 = 可预测、不依赖 seed 的 peak/dif 比。
#    幅度档位(设备 px, 总偏移 / 单点局部鼓包): 旧 2.16/0.98(A 档, 1:1 看成一条直线)
#      → 新 3.56/1.62(介于评审的 B 与 C 之间); 评审的 C(4.32/1.97) 已"读起来是波浪",
#      D(6.48/2.95) 是"明显锯齿山" —— 他自己划的红线, 不越。
MOUND_ROUGH_FRAC = 0.004    # 粗糙幅度 = 0.004 × 直径
MOUND_ROUGH_SEED = 20261004 # **固定** seed: 整轮不重抽(逐帧重抽 = 1.60/1.61 的"原地闪现"教训)
MOUND_ROUGH_SMOOTH = 0.45   # 相邻差上限系数; 必须 > 103.2×FRAC 否则 FRAC 失效(见上)
# 背景飞溅(用户 2026-10-05: "下面的沙子…飞溅效果现在还是没有…只有沙柱和沙堆尖头那一块")。
# 实测: 在途飞溅 85~119 颗, 但 |dx| 中位只有 ±6px 而沙堆半宽 137px ⇒ **96% 挤在落点**。
# 不是 splash 不工作, 是**它只在一个点上工作** —— 沙流落在一个点, 斜坡上没有生成源。
# 这一层按"离落点越远越稀"补, 是**纯装饰**(不反向影响 elapsed)。
# ⚠️ 会改变随机数调用序列 ⇒ 旧的"同 seed 逐像素对照"基线作废(有意的视觉改动)。
SPLASH_BG_RATE = float(os.environ.get("HG_SPLASH_BG", "520"))   # 颗/秒(满速率)
# ⚠️ 第一版用 `|u|^1.6 × (0.42·R)` —— **上限被钉在 42% 半宽处**, 实测 >50%R 恒 0%。
#    改成"**铺满整个半宽, 密度往外衰减**": mag = 0.96·R·u^POW, POW 越大小越往中心堆。
#    POW=2.4 时: 中位落在 ~0.18R, p90 落在 ~0.75R —— 正是"由强到弱"。
SPLASH_BG_POW = 0.9         # 横向密度衰减指数(用户 2026-10-05: "中间再减少一些, 两边再多一些")
MOUND_CREST_MARGIN = 2.0    # 沙体矩形比球内顶再高一点的余量(carve 上沿)
# ---- 上球漏斗: 取消"0度水平面"(外部专家 dingbu.md §4, 2026-10-05 用户点名) ----------
# 用户投诉:「顶部的沙子还是一个绝对的平面」; r3-2号 实测: 七列采样 y 全等、跨 920px 零偏差,
# 而**同一帧里**下球沙堆是 30.1°/31.7° 的标准休止角 ⇒ 同一种沙两个角度, 实现内部不自洽。
# 真沙漏的上球沙面必然以休止角朝颈口下凹成漏斗(靠壁一圈高于中心)。
# ⚠️ **宽深比按 dingbu2.md §3 重设**(2026-10-05)。原参数(0.03D 深 / 0.10~0.14D 半宽)被
#    外部评审判为"两段长平肩之间压出一个集中凹口", 不是用户要的"斜率很小的宽浅坑";
#    他**主动认领这是自己上一稿把造型范围设窄了**。实测原参数坡度 18.3°~23.8°,
#    新参数 2.5°~2.7°(与他的 0.045 rad ≈ 2.6° 自洽)。
UPPER_FUNNEL_DEPTH = 0.018  # 中段最大下陷 = 0.018 × 直径
#   ⚠️ 0.010 是 dingbu2.md §3 给的"美术起点"(他明说"不是物理定律、待视觉确认")。
#   用户实测后说"坑还是太小" ⇒ 加深到 0.018。**宽度已顶到护栏**(见下), 只能靠深度。
# ⚠️ 用户 2026-10-05 实测后裁定: "**深度也还行, 主要是宽度太窄**"、
#   "中间深两边浅, 而不是又小又深"、"最极端应该达到最大宽度的 90~100%"。
#   0.50C = **100% 半弦宽**(坑的边缘正好落在球壁上)。
#   ⚠️ 为什么 0.35C 看着还是窄: 曲线 [1-(x/b)²]² 掉得极快 —— |x|=b/2 处只剩 56%,
#   |x|=0.7b 处只剩 26% ⇒ **"看得见的坑"只有 b 的一半**。这是评审 §2.1 说的
#   "造型范围偏窄"在观感上的真实后果, 不能只照数字填。
UPPER_FUNNEL_WIDTH = 0.50   # 下陷**半宽** = 0.50 × 可见全宽 C = 1.00 × 半弦宽
UPPER_FUNNEL_MAXH = 0.25    # 下陷深度上限 = 0.25 × 当前沙层厚度
UPPER_FUNNEL_MAXB = 1.00    # 下陷半宽上限(0.80 -> 1.00: 用户要的 100% 宽度需要它)
# ---- §3 公式里的上球微粗糙 r_upper(x)(dingbu2.md §4.2 / §3) ----
# ⚠️ **这一项 1.83~1.88 一直是漏做的**: 评审的公式写的是 `P_upper = a - d·F(x) + r_upper(x)`,
#    并单独给了上球的幅度上限 `A_upper = min(0.0025×D, 1.25/g)`、还要求"独立 seed"。
#    但代码里上球沙面**从来没有粗糙项**(审计 2026-10-05 查出)。
#    幅度按他给的上限折算成占直径比 = 0.0025(他那条 1.25/g 在 g=1 时是 1.25px,
#    设备 D=891 时 0.0025D=2.23px 更大, 取小的那个 ⇒ 用 0.0014 更保守)。
# 上球沙面**图案演化周期**(秒) —— 2026-10-05 用户: "所谓沙面起伏, 目前是沙面粘合剂
# (完全没有起伏, 是静止不动的)"。实测确认: 图案按固定节点号取值 ⇒ 钉死在固定 x 上,
# 相邻帧相关系数 r=0.98(完全没动)。**但绝不能逐帧重抽** —— 那是 1.60/1.61 被用户
# 判死("颗粒原地闪现 + 整条轮廓颤动")的路。这里做的是**平滑演化**: 预烘 64 帧、
# 相邻帧之间在时间上做过环形平滑, 每帧只做 65 次线性插值。
# ⚠️ r18-1号 2026-10-05 查出: 原来是"**8 秒一轮回**"（`ph=(t/8)%1`）—— 53 秒档会看 6 圈同样的花纹。
#    "循环"和"演化"是两回事。改法: **周期拉长到 48 秒, 同时把谐波次数按比例提高**
#    ⇒ 肉眼看到的运动快慢**不变**（最快的分量周期仍是 ~3.7 秒）, 但一轮回变成 48 秒
#    （53 秒档只看 1.1 圈）。帧数跟着提到 128: 最高谐波 13 ⇒ 每周期至少 8 帧采样。
# ⚠️ **1.107 把这三行调成 (48.0 / 128 / (5,8,13)) 之后, 闸门出现一条稳定回归**:
#    `neck texture stays inside the straight conduit` FAIL(连跑两次),
#    而 1.106 的同一支闸门 PASS ⇒ **是这次调参引入的**。机制未查明。
#    ⇒ **先回退到 1.106 的取值**(不带已知回归跑), 等有上下文时再做二分定位。
#    要复现: 把 period 改 48.0 / FRAMES 改 128 / HARMONICS 改 (5,8,13), 跑 verify_hourglass.py。
UPPER_ROUGH_PERIOD = float(os.environ.get("HG_ROUGH_PERIOD", "8.0"))
UPPER_ROUGH_FRAMES = 64
UPPER_ROUGH_HARMONICS = (1, 2, 3)    # 谐波次数(整数 ⇒ 整轮严格闭合, 插值不会跳)

# 上球沙面**图案演化周期**(秒) —— 2026-10-05 用户: "所谓沙面起伏, 目前是沙面粘合剂
# (完全没有起伏, 是静止不动的)"。实测确认: 图案按固定节点号取值 ⇒ 钉死在固定 x 上,
# 相邻帧相关系数 r=0.98(完全没动)。**但绝不能逐帧重抽** —— 那是 1.60/1.61 被用户
# 判死("颗粒原地闪现 + 整条轮廓颤动")的路。这里做的是**平滑演化**: 预烘 64 帧、
# 相邻帧之间在时间上做过环形平滑, 每帧只做 65 次线性插值。
# ⚠️ r18-1号 2026-10-05 查出: 原来是"**8 秒一轮回**"（`ph=(t/8)%1`）—— 53 秒档会看 6 圈同样的花纹。
#    "循环"和"演化"是两回事。改法: **周期拉长到 48 秒, 同时把谐波次数按比例提高**
#    ⇒ 肉眼看到的运动快慢**不变**（最快的分量周期仍是 ~3.7 秒）, 但一轮回变成 48 秒
#    （53 秒档只看 1.1 圈）。帧数跟着提到 128: 最高谐波 13 ⇒ 每周期至少 8 帧采样。
# ⚠️ **1.107 把这三行调成 (48.0 / 128 / (5,8,13)) 之后, 闸门出现一条稳定回归**:
#    `neck texture stays inside the straight conduit` FAIL(连跑两次),
#    而 1.106 的同一支闸门 PASS ⇒ **是这次调参引入的**。机制未查明。
#    ⇒ **先回退到 1.106 的取值**(不带已知回归跑), 等有上下文时再做二分定位。
#    要复现: 把 period 改 48.0 / FRAMES 改 128 / HARMONICS 改 (5,8,13), 跑 verify_hourglass.py。
UPPER_ROUGH_PERIOD = float(os.environ.get("HG_ROUGH_PERIOD", "8.0"))
UPPER_ROUGH_FRAMES = 64
UPPER_ROUGH_HARMONICS = (1, 2, 3)    # 谐波次数(整数 ⇒ 整轮严格闭合, 插值不会跳)

UPPER_ROUGH_FRAC = _rough_level(
    os.environ.get("HG_SURFACE_LEVEL", SURFACE_ROUGH_LEVEL_DEFAULT))
# 出厂默认 = D 档(设备 ≈5.35px)。六档定义见 `SURFACE_ROUGH_LEVELS`(隐藏菜单可改)。
UPPER_ROUGH_SEED = 20261006 # **独立 seed**(评审: 与下球各用一组, 不要共用)
# ---- §5 表层滑动标记: 让静态轮廓读起来像在流沙(专家 dingbu.md §5, 用户点名的那条) ----
# 专家原话: 「只有凹陷和尖堆, 没有材料沿表面运动, 仍可能像一块正在变形的色纸」。
# ⚠️ **不要画一整条随相位移动的亮线** —— 标记必须**离散、细小**, 集中薄表层;
#    整块沙体纹理保持稳定(1.60/1.61 就是因为"整条轮廓平移"被判死的)。
SURFACE_MARKERS_UP = 8      # 上球 8 颗(左右各 4), 向中心滑
SURFACE_MARKERS_LOW = 12    # 下球 12 颗(左右各 6), 沿坡向外滑
SURFACE_MARKER_LIFE = 0.9   # 单颗寿命(秒); 逐颗错开相位
# ⚠️ 单位陷阱: widget 单位在**真机上就是物理像素**(geometry 从 widget size 派生)。
#    1.6 单位 = 1.6 物理像素 = **0.914 dp**(density 280) —— 桌面预览把它放大了 3.26 倍
#    (相对球径: 桌面 0.585%D vs 设备 0.180%D)。"桌面上看着清楚"不能作数。
# 大小分级(dingbu2.md §5: "不要全部一样大"): 交替 2.2 / 3.4 物理像素,
# ⚠️ 原 1.5/2.4 被两位独立审计量出**每颗只有 2~5 个像素**、同一时刻只有 3~5 颗可见 ⇒ 太小。
# 大的那批读作"颗粒簇"。
SURFACE_MARKER_SIZE = 2.2
SURFACE_MARKER_SIZE_BIG = 3.4
SURFACE_MARKER_ALPHA = 1.00
SURFACE_MARKER_UP_START = 0.30   # 上球起点 = 弦半宽的 30% 处 → 滑向中心
# ⚠️ 内侧偏移(dingbu2.md §5): 必须**离开沙面自带的那条 3px 亮带**。
#    取证实测: 原实现 20/20 颗 `offset = +0.0000 px`, 与亮带重合度 **100%** ——
#    正好压在亮带上, 而评审要的是"自由表面**内侧** 2~4 最终像素的薄层"。
# ⚠️ **必须真的大于亮带宽度**(r11-1号 查出: 原来写 2.6 < SAND_SURFACE_BAND=3.0,
# 标记核心落在沙面下第 2~3 行 ⇒ **仍在亮带内** ⇒ 我按"沙体本色"解出来的 α 落空了,
# 实测背景是亮带(解析值 181.4/87.2 命中到 0.8 级内) ⇒ 黑沙实测对比度 −18%/−21%,
# 超规格 8~15% 的 1.2~1.4 倍)。提到 4.0 才真正落在沙体本色上。
SURFACE_MARKER_INSET = 4.0
# ⚠️ 相位抖动(dingbu2.md §5: 不要"像一串珠子排列在坡沿"):
#    原实现每侧等分相位 ⇒ 同侧间距**严格等距**(上球 8.202px, spread=0.000)。
#    这里给每颗一个**固定**的(不随帧变的)相位偏移, 打破等距但不破坏"固定 seed"纪律。
SURFACE_MARKER_JITTER = 0.17
MOUND_AREA_SAMPLES = 129    # 面积表积分节点数(奇数)
MOUND_CURVE_SAMPLES = 129   # 每帧接触高度曲线 H(x) 的均匀节点数(粒子侧 O(1) 定位)
MOUND_DRAW_EXTRA = 48       # 绘制折线的角度采样数(另加 ±R/0 与**逐帧的壁交点**这些真转折点)
#   ⚠️ 24 时实测最外一段弦高出真圆 ~4px ⇒ 沙堆与壁相接处留一条 2~5px 的沙楔; 48 → 误差 ÷4


class _MoundArea:
    """一维面积表: A(a) = Σ w·clamp(a + offset - bottom, 0, top-bottom) —— 折线求逆。

    移植自外部专家 `xingzhuang2.md` §4.3 的参考实现。每一列的"面积"都是
    "先为零 → 线性增长 → 填满后不变", 把各列的起止高度合并事件后, 总面积正好是一条折线,
    所以查表 + 线性插值就是**精确求逆**, 不必每帧二分。
    """

    __slots__ = ("heights", "areas", "capacity")

    def __init__(self, bottom, top, weights, offsets):
        events = {}
        for lo, hi, w, off in zip(bottom, top, weights, offsets):
            if hi <= lo:
                continue
            start, end = lo - off, hi - off
            events[start] = events.get(start, 0.0) + w
            events[end] = events.get(end, 0.0) - w
        self.heights = sorted(events)
        self.areas = []
        area = slope = 0.0
        prev = self.heights[0]
        for height in self.heights:
            area += max(0.0, slope) * (height - prev)
            self.areas.append(area)
            slope += events[height]
            prev = height
        self.capacity = self.areas[-1]

    def area_at(self, height):
        if height <= self.heights[0]:
            return 0.0
        if height >= self.heights[-1]:
            return self.capacity
        i = bisect_right(self.heights, height) - 1
        f = (height - self.heights[i]) / (self.heights[i + 1] - self.heights[i])
        return self.areas[i] + f * (self.areas[i + 1] - self.areas[i])

    def height_at(self, area):
        if area <= 0.0:
            return self.heights[0]
        if area >= self.capacity:
            return self.heights[-1]
        i = bisect_right(self.areas, area) - 1
        f = (area - self.areas[i]) / (self.areas[i + 1] - self.areas[i])
        return self.heights[i] + f * (self.heights[i + 1] - self.heights[i])


def _smoothstep(lo, hi, v):
    """标准夹紧平滑插值(专家 dingbu.md §4.2 用的那个)。"""
    t = min(1.0, max(0.0, (v - lo) / max(1e-9, hi - lo)))
    return t * t * (3.0 - 2.0 * t)


def _surface_roughness(radius, amp_frac, seed, limit):
    """受限的**固定**表面微粗糙数组(专家 dingbu.md §3.2)。

    65 个等距控制点, 幅度上限 `amp_frac × 直径`, 相邻差不超过 `limit`。
    ⚠️ **固定 seed, 整轮不重抽** —— 逐帧重抽会变成"颗粒原地闪现 + 整条轮廓颤动",
       这正是 1.60/1.61 被用户判死("完全没有变化…还不如平面")的那条路。
    ⚠️ 幅度**统一缩放**到满足相邻差限制, 不做逐点夹取(逐点夹会造出一片等高平台)。
    ⚠️ 中心点强制为 0: 堆尖保持在入沙轴线上。
    """
    n = MOUND_SHAPE_NODES
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    sm = [(raw[max(0, i - 1)] + 2.0 * raw[i] + raw[min(n - 1, i + 1)]) / 4.0
          for i in range(n)]
    peak = max(abs(v) for v in sm) or 1.0
    vals = [v / peak * (amp_frac * 2.0 * radius) for v in sm]
    dif = max(abs(vals[i + 1] - vals[i]) for i in range(n - 1)) or 1.0
    if dif > limit:
        k = limit / dif
        vals = [v * k for v in vals]
    vals[(n - 1) // 2] = 0.0
    return vals


def _build_rough_frames(radius, amp_frac, seed, frames=UPPER_ROUGH_FRAMES,
                        harmonics=UPPER_ROUGH_HARMONICS):
    """预烘 `frames` 张粗糙数组, **随时间平滑演化**(供上球沙面用)。

    ⚠️ **为什么不用"独立的随机帧 + 时间上环形平滑"**（第一版就是那么写的, 实测被否）:
       64 张独立帧做 ±4 的移动平均, 相邻帧仍只共享 8/9 的样本 ⇒ 实测帧间相关只有 **r≈0.89**,
       按 60fps 算 **~0.17 秒就换一副面孔** —— 那会看起来像微光闪烁, 正是 1.60/1.61
       被用户判死("颗粒原地闪现 + 整条轮廓颤动")的那类毛病。
    改成: **每个节点的时间序列 = 3 个低频正弦之和**(频率 1/2/3 倍周期, 振幅与相位各自随机)
       ⇒ 相邻相位的差是 O(1/frames), 天生连续; 而整轮下来图案确实换过一遍。
    ⚠️ 每帧按 **RMS 归一**(不是按峰值): 峰值归一会让"峰值出现在哪个节点"跳变时整体鼓一下,
       看起来像呼吸; RMS 归一稳定得多。
    """
    import numpy as np
    n = MOUND_SHAPE_NODES
    rng = np.random.default_rng((seed or 721) + 991)
    hz = tuple(harmonics) if hasattr(harmonics, "__len__") else tuple(range(1, harmonics + 1))
    amps = [rng.uniform(0.6, 1.0, size=n) if j == 0 else rng.uniform(0.15, 0.5, size=n)
            for j in range(len(hz))]
    psis = [rng.uniform(0.0, 2.0 * np.pi, size=n) for _ in hz]
    # 目标 RMS: 与静态版同源(用同一套空间平滑口径算一遍参考值)
    ref = np.asarray(_surface_roughness(radius, amp_frac, seed, amp_frac * 2.0 * radius))
    target_rms = float(np.sqrt((ref ** 2).mean())) or 1.0
    out = []
    for k in range(frames):
        ph = k / float(frames)
        v = np.zeros(n)
        for j, m in enumerate(hz):
            v += amps[j] * np.sin(2.0 * np.pi * m * ph + psis[j])
        rms = float(np.sqrt((v ** 2).mean()))
        v = v * (target_rms / rms) if rms > 1e-9 else v
        out.append(v.tolist())
    for a in out:                      # 中心点强制 0: 堆尖保持在入沙轴线上
        a[(n - 1) // 2] = 0.0
    return out


def _mound_shape_array(radius, frac=None):
    """下球轮廓 `f(x)`(绝对值, 相对中心轴): `-m·|x| + r(x)`, 无平台。

    左右斜率故意略不同(0.58 / 0.62) ⇒ 破掉完全镜像, 但**不需要让堆尖来回摆**。
    相邻差限制保证"从中心向两侧主体始终在下降"(粗糙幅度 ≤ 斜率×Δx/4)。
    """
    n = MOUND_SHAPE_NODES
    half = (n - 1) // 2
    dx = radius / half
    frac = MOUND_ROUGH_FRAC if frac is None else frac
    # ⚠️ **限坡必须跟着幅度走**, 否则又踩 1.87 那个"死旋钮":
    #    `_surface_roughness` 在"相邻差 > limit"时**整体缩放**, 而 FRAC 生效的条件是
    #    `SMOOTH > 103.2 × FRAC`。旧写法 limit = 0.45 × 斜率 × dx = 3.63px 是**定值**
    #    ⇒ FRAC 超过约 0.0044 之后**再调大一点用都没有**(幅度被钉成 R×0.01938×SMOOTH)。
    #    现在: 上限放到位所需幅度, 但**不许超过基准坡降的 0.9 倍** ——
    #    否则局部邻点会"翻上去"(从中心向两侧不再是单调下降), 沙堆会长出反坡的小包。
    amp = frac * 2.0 * radius
    limit = min(amp, 0.9 * min(MOUND_SLOPE_L, MOUND_SLOPE_R) * dx)
    rough = _surface_roughness(radius, frac, MOUND_ROUGH_SEED, limit)
    out = []
    for i in range(n):
        u = (i - half) * dx
        m = MOUND_SLOPE_L if u < 0 else MOUND_SLOPE_R
        out.append(-m * abs(u) + rough[i])
    return out


class _MoundProfile:
    """下球沙堆的形状解 —— 移植自外部专家 `xingzhuang2.md` §4.3 的参考实现。

    轮廓 = `P(x) = apex + f(x)`, `f` 由**固定的** 65 点控制数组线性插值给出
    (2026-10-04 起按 `dingbu.md` §3 取消平台, 改为微不对称尖堆 + 受限微粗糙)。
    因为 `f` 与沙量无关, 两张面积表只在**几何变化时**重建一次, 每帧只做一次查表求逆。

    单位: 构造时 `radius`/`shape` 是绝对像素, 内部用归一化坐标算面积表,
    返回值 `apex` 是**中心轴处**的沙面高度(绝对, 离球内底) —— 原点处 f(0)=0, 所以它就是峰高。
    """

    __slots__ = ("radius", "shape", "xs", "flat", "heap", "slope_l", "slope_r")

    def __init__(self, radius, shape, samples=MOUND_AREA_SAMPLES):
        n = len(shape)
        if radius <= 0 or n < 5 or n % 2 == 0:
            raise ValueError("invalid mound shape")
        self.radius = float(radius)
        self.shape = tuple(float(v) for v in shape)
        self.slope_l = abs(shape[0]) / radius
        self.slope_r = abs(shape[-1]) / radius
        # 积分节点 = 控制点 ∪ 相邻中点(专家 §6: 中间值就是两个控制高度的平均)
        m = n - 1
        xs, offs = [], []
        for i in range(n):
            x = -radius + 2.0 * radius * i / m
            xs.append(x); offs.append(self.shape[i])
            if i < m:
                xs.append(x + radius / m)
                offs.append(0.5 * (self.shape[i] + self.shape[i + 1]))
        self.xs = xs
        weights = []
        k = len(xs)
        for i in range(k):
            left = xs[i] - xs[i - 1] if i else 0.0
            right = xs[i + 1] - xs[i] if i + 1 < k else 0.0
            weights.append(0.5 * (left + right))
        bottom = [radius - math.sqrt(max(0.0, radius * radius - x * x)) for x in xs]
        top = [2.0 * radius - y for y in bottom]
        self.flat = _MoundArea(bottom, top, weights, [0.0] * k)
        self.heap = _MoundArea(bottom, top, weights, offs)

    def shape_at(self, dx):
        """轮廓偏移 f(dx) —— 控制点之间线性插值(与面积表、绘制折线同一份定义)。"""
        r = self.radius
        n = len(self.shape)
        z = (dx + r) / (2.0 * r) * (n - 1)
        if z <= 0.0:
            return self.shape[0]
        if z >= n - 1:
            return self.shape[-1]
        i = int(z)
        f = z - i
        return self.shape[i] + (self.shape[i + 1] - self.shape[i]) * f

    def apex_for_height(self, height):
        """体积反查: 平顶等效高度 h → **虚拟**峰高(绝对, 离球内底)。

        ⚠️ **全程绝对单位**。面积表的 bottom/top 是绝对高度(`R - √(R²-x²)`),
        不是归一化的 0..2 —— 1.74 初版这里漏改(拿归一化的 h 去查绝对表、又把结果
        乘 radius), 两次换算互相抵消 ⇒ 峰高无意义, 表现为**下球提前灌满、上球还剩大半**
        (2026-10-04 用户实拍 37/50 抓到: 只漏了 26%, 下球却已经满了)。
        ⚠️ 同一次漏改也让 `tools/_xz_geom_accept.py` 的"面积自洽"检查变成瞎的 ——
        它两边用的是同一个错误口径, 误差自己抵消。已同时修测试并补判别性判据。
        """
        h = min(max(height, 0.0), 2.0 * self.radius)
        fraction = self.flat.area_at(h) / self.flat.capacity
        return self.heap.height_at(fraction * self.heap.capacity)

    def raw(self, dx, apex):
        """未裁剪堆面高度(绝对, 离球内底)。"""
        return apex + self.shape_at(dx)

    def bounds(self, dx):
        """该列的球内底/球内顶高度(绝对) —— 理想圆公式, 实际接缝仍交给 Ellipse 裁剪。"""
        r = self.radius
        x = min(max(dx, -r), r)
        half = math.sqrt(max(0.0, r * r - x * x))
        return r - half, r + half

    def contact(self, dx, apex):
        """该列的**接触高度**(绝对) —— 粒子/尘埃的判定面, 与绘制同一份定义。"""
        floor, roof = self.bounds(dx)
        return min(max(self.raw(dx, apex), floor), roof)

    def has_sand(self, dx, apex):
        """该列有没有沙(自由表面/填满都算有; P ≤ B 才是裸露球底)。"""
        floor, _roof = self.bounds(dx)
        return self.raw(dx, apex) > floor

    def free_surface(self, dx, apex):
        """该列是不是**真正的自由表面**(沙与空气之间) —— 亮带只画在这里。"""
        floor, roof = self.bounds(dx)
        p = self.raw(dx, apex)
        return floor < p < roof


# ---- 玻璃反光: **已删除**(2026-10-04, 用户实拍裁决「有害无益, 全删了」) -------------
# 1.57 按外部美术规格 meishu2.md §5.1/§5.2 加的"左上主反光(两段留断口) + 右下弱反光 +
# 局部内缘压暗"。它骑在玻璃内缘上(一半压在 6px 深色玻璃环里、一半压在沙体边缘), 末端是
# 硬切的等 alpha 弧段 —— 在深色环上读成"贴在边框上的一块浅灰碎片", 放大后尤其像异物
# (用户 9~11 点钟实拍点名)。我试过把它改成两端羽化, 用户裁决不要救、整块删。
# 删掉的东西: GLASS_HL_* 全部常量 / `set_glass_hl` / `_rebuild_glass_highlights` /
# `_glass_hl_group` / 存档键 `glass_hl` / 隐藏菜单里的"玻璃反光"滑块。
# 保留: `FLOW_HILITE_T`(那是沙流新生颗粒的颜色, 与玻璃无关)。
# 新生颗粒("高光组", 刚出孔口那批)的亮度上限 = lerp(sand_base, sand_light, 这个系数)。
# 1.0 = 原样用 sand_light —— 它比出口正上方的颈部沙柱(材质色)亮 15 级, 见
# `_rebuild_color_table` 的注释。0.32 = 与材质自身的亮端上限对齐。
FLOW_HILITE_T = float(os.environ.get("HG_FLOW_HILITE", "0.32"))


def apply_sand_style(mode, grain):
    """设置全局沙体材质。**只改全局, 不碰画布** —— 供"读配置"在建材质之前调用。

    配置存的是 `(mode, grain)` 而**不是档位序号**: 滑块改成连续之后, 旧配置里那些
    档位值(0.00/0.15/0.35/0.70)仍然是合法 grain, 不需要迁移。
    ⚠️ 不再"吸到最近的档位" —— 档位已不存在, 吸了反而会让滑块跳。
    """
    global SAND_MATERIAL, SAND_MATERIAL_GRAIN
    SAND_MATERIAL = mode if mode in ("flat", "grain") else "grain"
    try:
        value = float(grain)
    except (TypeError, ValueError):
        value = SAND_GRAIN_DEFAULT
    SAND_MATERIAL_GRAIN = min(SAND_GRAIN_MAX, max(0.0, value))
# ⚠️ 这个缓存**永不淘汰**, 有两层原因, 别随手加 LRU/上限:
# ① 材质对象被 GC ⇒ `Texture.add_reload_observer` 存的 **WeakMethod** 失效 ⇒
#    图形上下文丢失后纹理再也传不回去(沙体会退回默认纹理, 且不报错)。
#    实测(Kivy 2.3.0 `texture.pyx:677` 是 WeakMethod, `:1123` 遍历调用)。
# ② 缓存命中是换色的**唯一**免卡途径: 冷启动生成一张 512² 约 16ms + 上传 ≈ 单帧 20ms。
_SAND_MATERIAL_CACHE = {}


def _coarsen_noise(size, coarse, seed):
    """把逐像素白噪换成**约 coarse 倍粗**的颗粒场, 幅度保持**同样的 RMS**。

    ⚠️ **为什么必须归一 RMS**: 单纯把噪点放大, 会顺带把对比度也改掉 ——
       那样"颗粒变粗"和"颗粒变强"两个变量就混在一起了, 对照不干净。
       均匀分布 σ=1/√12 = 0.2886751, 放大后按实测 σ 缩回去。
    ⚠️ **取数顺序必须与 A/B 工具逐字节一致**(用户是照着那张图挑的):
       同一个 seed、同样先抽 `(low, low)`、同样 ×255 过一遍 uint8(那是预览管线的一环)。
    """
    import numpy as np
    low = max(2, int(round(size / coarse)))
    small = np.random.default_rng(seed).random((low, low), dtype=np.float32)
    try:
        from PIL import Image as _PILImage
        up = _PILImage.fromarray((small * 255.0).astype(np.uint8)).resize(
            (size, size), _PILImage.BILINEAR)
        out = np.asarray(up, dtype=np.float32) / 255.0
    except Exception:
        # PIL 缺失时的 numpy 双线性(坐标映射与 PIL 的 (i+0.5)*src/dst-0.5 一致, 边缘夹取)
        t = ((np.arange(size, dtype=np.float32) + 0.5) * (low / float(size))) - 0.5
        i0 = np.clip(np.floor(t).astype(np.int32), 0, low - 1)
        i1 = np.clip(i0 + 1, 0, low - 1)
        f = np.clip(t - np.floor(t), 0.0, 1.0).astype(np.float32)
        fx, fy = f[None, :], f[:, None]
        out = (small[np.ix_(i0, i0)] * (1 - fx) * (1 - fy)
               + small[np.ix_(i0, i1)] * fx * (1 - fy)
               + small[np.ix_(i1, i0)] * (1 - fx) * fy
               + small[np.ix_(i1, i1)] * fx * fy)
    sd = float(out.std())
    if sd > 1e-6:
        out = 0.5 + (out - float(out.mean())) * (0.2886751 / sd)
    # ⚠️ **不要在这里 clip 到 [0,1]** —— 归一化会把值推出这个区间(实测 3.5% 的元素),
    #    而对照图那一版**没有 clip**。加了它, 线上就与用户挑的那张图差最多 7 级
    #    (2026-10-05 首次验证就是这么被自己抓出来的)。下游 `tone` 本来就会 clip 到 [-1,1]。
    return out


def _sand_material_rgba(size, base, dark, light, seed=721, grain=0.35, shade=1.0,
                        coarse=1, grad=0.10):
    """生成 size×size 的 RGBA 材质字节。**numpy 缺失时返回 None**(退回原来的平色填充)。

    UV 约定: 第 0 行 = **球底**(沙体底), 最后一行 = 球顶 —— 与"Rectangle 从球底往上长"一致。
    随机数用**独立生成器**: 不碰 `random`, 否则会打乱粒子序列、破坏同 seed 的逐像素对照。
    """
    try:
        import numpy as np
    except Exception:
        return None
    axis = (np.arange(size, dtype=np.float32) + 0.5) / size
    qx = (axis * 2.0 - 1.0)[None, :]
    # 「靠壁压暗」只看**水平**距离: 玻璃壁在左右两侧, 而竖直方向的上下两端
    # 分别是沙面(上)与**颈口**(下), 都不是壁。
    # ⚠️ 原来用径向 r²=qx²+qy², 会把球底那个极点也当成"靠壁"压暗 ——
    # 而颈部采样不到那一段, 于是颈部比球体亮一个档, 被读成两种材质
    # (2026-10-04 用户报"上面的部分和颈部的沙子构成完全不同")。
    w = np.clip((qx * qx - 0.64) / 0.36, 0.0, 1.0)
    edge = w * w * (3.0 - 2.0 * w)
    broad = grad * (axis[:, None] - 0.5) - 0.07 * qx - 0.16 * edge
    if coarse and coarse > 1:
        noise = _coarsen_noise(size, coarse, seed)
    else:
        noise = np.random.default_rng(seed).random((size, size), dtype=np.float32)
    tone = np.clip(shade * broad + grain * (2.0 * noise - 1.0), -1.0, 1.0)
    pal = [np.asarray(c, dtype=np.float32) for c in (base, dark, light)]
    target = np.where(tone[..., None] >= 0.0, pal[2], pal[1])
    rgb = pal[0] + np.abs(tone)[..., None] * (target - pal[0])
    out = np.empty((size, size, 4), dtype=np.uint8)
    out[..., :3] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out.tobytes()


class _SandMaterial:
    """一张预生成的沙体材质 + 它的**完整** UV(每帧截取时以它为基准)。"""

    def __init__(self, size, rgba):
        self.rgba = rgba
        self.texture = Texture.create(size=(size, size), colorfmt="rgba")
        self.texture.wrap = "clamp_to_edge"
        self.texture.min_filter = "linear"
        self.texture.mag_filter = "linear"
        # 上下文丢失后靠 reload observer 重传(留着字节就是为了这一步)
        self.texture.add_reload_observer(self._upload)
        self._upload(self.texture)
        self.tex_coords = tuple(self.texture.tex_coords)

    def _upload(self, texture):
        texture.blit_buffer(self.rgba, colorfmt="rgba", bufferfmt="ubyte")


def sand_material(base, dark, light, size=None, grain=None, shade=None):
    """按配色取/建材质(带缓存)。任何一步失败返回 None ⇒ 上层退回平色填充。"""
    if SAND_MATERIAL == "flat":
        return None
    size = SAND_MATERIAL_SIZE if size is None else size
    grain = SAND_MATERIAL_GRAIN if grain is None else grain
    shade = SAND_MATERIAL_SHADE if shade is None else shade
    key = (size, tuple(base), tuple(dark), tuple(light), round(grain, 4),
           round(shade, 4), SAND_MATERIAL_COARSE, round(SAND_MATERIAL_GRAD, 4))
    material = _SAND_MATERIAL_CACHE.get(key)
    if material is None:
        try:
            rgba = _sand_material_rgba(size, base, dark, light, grain=grain, shade=shade,
                                       coarse=SAND_MATERIAL_COARSE,
                                       grad=SAND_MATERIAL_GRAD)
            if rgba is None:
                return None
            material = _SandMaterial(size, rgba)
        except Exception as exc:
            print(f"sand material unavailable: {exc}")
            return None
        _SAND_MATERIAL_CACHE[key] = material
    return material


def preview_sand_material(base, dark, light, grain, size=None):
    """拖动滑块用的**低分辨率**材质。**故意不进缓存**。

    ⚠️ `_SAND_MATERIAL_CACHE` 是**永不淘汰**的(它被 Texture 的 reload observer 以
    WeakMethod 持有, GC 掉就再也传不回纹理)。拖动一次会产生几十个中间 grain 值 ⇒
    512² 一张 1 MiB, 几十张就是几十 MB 常驻。预览对象由调用方持有、被换掉即 GC ——
    那时它的纹理已经没有任何 rect 引用了, 丢掉 reload 途径无害。
    """
    if SAND_MATERIAL == "flat":
        return None
    size = SAND_PREVIEW_SIZE if size is None else size
    try:
        rgba = _sand_material_rgba(size, base, dark, light, grain=grain,
                                   coarse=SAND_MATERIAL_COARSE,
                                   grad=SAND_MATERIAL_GRAD)
        if rgba is None:
            return None
        return _SandMaterial(size, rgba)
    except Exception as exc:
        print(f"sand preview unavailable: {exc}")
        return None


def crop_tex_coords(full, fraction):
    """把"完整纹理"的四角坐标裁成"只显示底部 fraction 高"的一套。

    ⚠️ 沙体矩形**每帧高度都在变**。若永远给完整的 0..1 纹理, 沙越少纹理就被压得越扁、
    越多就被拉得越长 —— 读起来像橡皮。基准必须始终是**完整纹理**的四角,
    不能在上一次已经裁短过的坐标上继续乘比例。
    """
    u0, v0, u1, v1, u2, v2, u3, v3 = full
    f = 0.0 if fraction < 0.0 else (1.0 if fraction > 1.0 else fraction)
    return (u0, v0,
            u1, v1,
            u1 + (u2 - u1) * f, v1 + (v2 - v1) * f,
            u0 + (u3 - u0) * f, v0 + (v3 - v0) * f)

# ---- 周期弹窗独立配色(暖金/沙色系) ----
POPUP_BG = "#faf5eb"                              # 弹窗底色,暖白
POPUP_GOLD_SEL = (0.792, 0.643, 0.314, 1)         # 选中:暖金 #caa450
POPUP_UNSEL_BASE = (0.659, 0.565, 0.471, 1)       # 未选(基础):暖棕 #a89078
POPUP_UNSEL_MULT = (0.722, 0.627, 0.533, 1)       # 未选(倍数):浅棕 #b8a088
POPUP_CANCEL_BG = (0.847, 0.824, 0.792, 1)        # 取消:暖灰(比未选亮,表示次要操作)
POPUP_CONFIRM = (0.62, 0.23, 0.16, 1)              # 「确定」:暗红(与选中金色区分,强调确认操作)
POPUP_TEXT = (0.20, 0.14, 0.08, 1)                # 深咖啡文字
POPUP_TEXT_SUB = (0.45, 0.38, 0.30, 1)            # 次级说明文字(浅咖啡)
POPUP_TEXT_WHITE = (1, 1, 1, 1)

# 球↔管的曲线收窄过渡(纯渲染,不参与体积/守恒计算;移植自 pc v4)
TAPER_K = 2.2        # 过渡段上端半宽 = K × neck_w, 该点落在球壁上
TAPER_FILL = 0.62    # 过渡段吃掉"球截口→颈中心"竖直空间的比例, 其余留作直筒
TAPER_SEGS = 24      # 过渡曲线采样段数
# ⚠️ 原来 10 段不够: 这条贝塞尔从**近乎水平**(起点切线偏离竖直约 73.5°)转到竖直,
# 10 段意味着每段折 7.4° ⇒ 放大看是一圈可见的多边形棱面(被报成"84° 硬折角")。
# 24 段把每段折角压到 ~3°。只在几何重建时算一次, 不在每帧路径上。
NECK_FILL = 0.25     # 颈部沙柱注满耗时(秒), 避免起跑瞬间"啪"地从空变满


def _bezier2(p0, p1, p2, n):
    """二次贝塞尔采样。P1 取"球切线 × 管壁线"的交点 → 起点与球弧相切、
    终点与管壁竖直相切, 全程无折角(C1 连续)。"""
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return pts


# ⚠️ Kivy `Line.width` 是**半宽**, 不是总宽 —— 2026-10-04 用 tools/_probe_linew*.py 实测:
#      width  0.5 / 1.0 → 1px (≤1 被夹成 GL_LINES)
#             1.05–1.4 → 2px      1.5 → 3px      1.6–2.2 → 4px      4.0 → 8px   8.0 → 16px
#   即 width>1 走 mesh 路径时 实际像素 ≈ 2×width。**本工程以前不知道这件事** ——
#   玻璃反光第一版按"1.9px"写 width=1.9, 实拍成 4px(+圆头 5px), 是"描边有一段变亮"
#   而不是一条独立反光的真因。
#   (顺带: 粒子 `width=p['size']` 里的 2 实际画的是 4px —— 那是既有视觉, **不要动**。)
_LINE_W_FOR_PX = {1: 1.0, 2: 1.2, 3: 1.5, 4: 1.7}


def _line_w(px):
    """想要"实际 N 像素宽" → 该传给 `Line(width=...)` 的值。"""
    px = max(1, int(round(px)))
    return _LINE_W_FOR_PX.get(px, px / 2.0)


def _arc_pts(cx, cy, r, deg0, deg1, segs):
    """圆周弧段采样 → Kivy `Line` 要的平坦坐标表。

    角度按**数学**习惯: 逆时针为正、0° = +x。Kivy 的 y 轴向上, 所以"画面左上"是
    第二象限(90°–180°), 与直觉一致, 不需要为坐标翻转另开一套角度。
    """
    out = []
    span = math.radians(deg1 - deg0)
    a0 = math.radians(deg0)
    for i in range(segs + 1):
        a = a0 + span * i / segs
        out.append(cx + r * math.cos(a))
        out.append(cy + r * math.sin(a))
    return out


FALL_DELAY = 1.0          # 沙子飞到底的延迟(秒),下沙堆出现与粒子到底同步
MOUND_APPEAR = 0.5        # 下沙堆出现后平滑渐显时长
MOUND_FLOOR_MIN = 2.5     # 前期极小可见保底(dp),仅防薄层消失,不拔高
MOUND_FLOOR_MAX = 3.5
MOUND_FLOOR_EFF = 0.02

COMPLETION_POPUP_DELAY = 1.0   # 完成提示延后(秒): 让闪光/尘埃先演完再弹(用户 2026-10-03 定)
COMPLETION_POPUP_MIN = 20.0    # 周期短于这个数就**不弹**完成提示(同上)

# (1.39 回滚) 这里曾有 JET_VENA / JET_DIFFUSE / JET_SPREAD / JET_EDGE / JET_WAVE_K ——
# 出口以下的"vena contracta 收腰 + 沿深度相干摆动"。它把颈部射流做成了一根**静止的
# 波纹管**: 摆动只随 y 变化(空间函数, 不是时间函数), 同一根 zigzag 每帧钉在同一批行上,
# 读起来像被模具挤出来的绳结, 不像会流动的沙。而且它从未进过任何一轮对抗审查, 验收用的
# "去趋势残差"对"加抖动"单调递增 —— 缺陷和指标是同一个东西(事故报告: NECK_REDESIGN §八)。
# 出口以下已恢复恒宽 tube_lim。

DUST_COUNT = 25
DUST_LIFETIME = 1.0
# ---- 完成闪烁: **已删除**(2026-10-04, 用户实拍裁决「这个很不合理, 这里不需要过度」) --------
# 原来是"漏完瞬间全屏盖一层白 25%、350ms"(从 PC v4 的 stipple gray25 继承来的)。
# 实测那一层把下球沙色从 (217,166,103) 冲成 (226,187,140) —— 饱和度掉一大截, 读成
# "沙子上蒙了一层膜", 而且它是**全屏**的, 连玻璃和背景一起冲淡。
# 删除依据(60fps 录屏逐帧扫完 14 秒, 840 帧): 整段只有这一处瞬态(Δ=+66, 340ms),
# 漏完前后稳态逐像素无差异。完成那一刻仍有完成音; 周期 ≥20 秒还有完成弹窗。
# 删掉的东西: 常量 FLASH_DURATION / `flash_end` / `_flash_color` / `_flash_rect`
# 及其每帧的 alpha+size 更新。

WAV_FILENAME = "sand_loop.wav"

# ---- 音效库(名字两版逐字一致,配置持久化按名字存) ----
SOUND_EFFECTS = [
    ("沙沙声", "sand_loop.wav"),
    ("水流声", "sounds/water.wav"),
    ("风声", "sounds/wind.wav"),
    ("钟表声", "sounds/clock.wav"),
]
SILENT_NAME = "无声音"
# 弹窗选项/配置值域 = 4 个实音效 + 无声音(None 路径仅表示静音,不建 proxy)
SOUND_OPTIONS = SOUND_EFFECTS + [(SILENT_NAME, None)]


def hex_rgb(h):
    return (int(h[1:3], 16) / 255.0,
            int(h[3:5], 16) / 255.0,
            int(h[5:7], 16) / 255.0)


def lerp_rgb(c1, c2, t):
    return (c1[0] + (c2[0] - c1[0]) * t,
            c1[1] + (c2[1] - c1[1]) * t,
            c1[2] + (c2[2] - c1[2]) * t)


def fg_for(hex_color):
    """亮底用黑字、暗底用白字。

    ⚠️ 阈值 0.50 而不是 0.59（2026-10-04 对抗评审实测算出的）：0.59 时**绿沙**(2.80:1)
    与金沙都仍是白字，六格里有四格低于 WCAG 3:1；0.50 时最差项是紫沙 4.23:1，六格全部 ≥4.2。
    ⚠️ 这个函数此前**全工程零调用** —— 色块按钮写死白字，是移植时漏接的。
    """
    r, g, b = hex_rgb(hex_color)
    return (0, 0, 0, 1) if (r * 0.299 + g * 0.587 + b * 0.114) > 0.50 else (1, 1, 1, 1)


def resource_path(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def config_path():
    if platform == "android":
        try:
            return os.path.join(App.get_running_app().user_data_dir,
                                ".hourglass_config.json")
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), ".hourglass_config.json")


def _fmt_duration(sec):
    """周期显示:整分钟/整小时不加小数,非整数加一位(100 秒 → 1.7 分钟)。"""
    if sec < 60:
        return f"{sec:.0f} 秒"
    if sec < 3600:
        m = sec / 60
        if abs(m - round(m)) < 1e-9:
            return f"{m:.0f} 分钟"
        return f"{m:.1f} 分钟"
    h = sec / 3600
    if abs(h - round(h)) < 1e-9:
        return f"{h:.0f} 小时"
    return f"{h:.1f} 小时"


MULT_SLIDER_MAX = 600         # 对数滑杆上限(倍)
MAX_DURATION = 360000         # 时长上限(秒)= 10分钟×600 = 100 小时


def _mult_from_slider(t):
    """滑杆位置 t∈[0,1] → 倍率:单段对数 MULT_SLIDER_MAX^t,向上取整为整数。
    t=0→1,t=0.5→√上限≈25,t=1→上限,无右侧死区。"""
    m = int(math.ceil((MULT_SLIDER_MAX ** max(0.0, min(1.0, t))) - 1e-9))
    return max(1, min(MULT_SLIDER_MAX, m))


def _fmt_duration_cn(sec):
    """完成弹窗里的时长("1小时30秒"),零分量省略、数字与单位之间不留空格。"""
    total = max(0, int(round(sec)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if secs or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def _fmt_countdown_pair(remaining, total):
    """倒计时显示,格式由总时长决定:H:MM:SS / M:SS / 秒。"""
    tot = int(round(total))
    rem = max(0, math.ceil(remaining))
    if tot >= 3600:
        fmt = lambda s: f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    elif tot >= 60:
        fmt = lambda s: f"{s // 60}:{s % 60:02d}"
    else:
        fmt = lambda s: f"{s:.0f}"
    return f"{fmt(rem)} / {fmt(tot)}"


BASE_PERIODS = [
    ("1 秒", 1),
    ("10 秒", 10),
    ("1 分钟", 60),
    ("10 分钟", 600),
]


# ---------- 横屏方向策略(移植自 hengping.md §3; 反旋转层 LandLayer 的配套) ----------

_DEVICE_WIDE_MIN = 9.0 / 16.0    # 0.5625: 短边/长边 ≥ 9:16 判宽屏(平板),小于判瘦长机
_device_wide_cache = None        # 开机/量一次,之后不再变
_LAYER = None                    # 当前反旋转层实例(唯一,供 _land_layer / 弹窗 remount 取用)


def _device_is_wide():
    """物理屏比例分流: 宽屏(平板类)→允许横转+反旋转; 瘦长机→锁竖屏。"""
    global _device_wide_cache
    if _device_wide_cache is None:
        aspect = 1.0
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            disp = act.getWindowManager().getDefaultDisplay()
            pt = autoclass("android.graphics.Point")()
            disp.getRealSize(pt)
            s, l = min(pt.x, pt.y), max(pt.x, pt.y)
            aspect = s / float(l)
        except Exception:
            aspect = 1.0
        _device_wide_cache = aspect >= _DEVICE_WIDE_MIN
    return _device_wide_cache


def _land_angle():
    """读系统旋转角决定反旋转画几度: ROTATION_270(3)→-90, 其余→+90。只在横屏被调。"""
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        wm = act.getWindowManager()
        disp = wm.getDefaultDisplay()
        return -90 if disp.getRotation() == 3 else 90
    except Exception:
        return 90


def _land_layer():
    return _LAYER


# ---------- 居中输入框(Kivy TextInput 无 text_align,手动算 padding) ----------

class CenterTextInput(TextInput):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(text=self._refresh_pad, size=self._refresh_pad,
                  font_size=self._refresh_pad)
        Clock.schedule_once(self._refresh_pad, 0)

    def _refresh_pad(self, *_):
        try:
            cl = CoreLabel(text=self.text or "0", font_size=self.font_size,
                           font_name=self.font_name or "Roboto")
            cl.refresh()
            tw, th = cl.content_size
        except Exception:
            tw, th = 0, 0
        ph = max(2, (self.width - tw) / 2)
        pv = max(2, (self.height - th) / 2)
        self.padding = [ph, pv, ph, pv]


# ---------- 音效: Android AudioTrack 硬件循环; Windows winsound; 桌面 fallback ----------

class _SoundProxy:
    """Android: AudioTrack MODE_STATIC($Builder) + getState校验 + reloadStaticData
    三星设备兼容; Windows: winsound SND_LOOP; 其他桌面/Android失败: Kivy SoundLoader。"""

    def __init__(self, wav_path, loop=True):
        self._is_android = (platform == "android")
        self._is_windows = (platform == "win")
        self._audio_track = None
        self._kivy_sound = None
        self._winsound = None
        self._wav_path = wav_path
        self._loop = loop
        self._active = False
        self._needs_reload = False
        self._loop_frames = 0
        self.backend = None
        self.error = ""      # 后端选择失败原因(只进 logcat, 界面不显示)

        if self._is_android:
            try:
                self._init_audio_track(wav_path)
                if self._audio_track is not None:
                    self.backend = "audiotrack"
                    print("backend=audiotrack")
                    return
                self.error = "AudioTrack: no track"
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:70]
                print(f"AudioTrack init failed (fallback): {e}")
                self._audio_track = None
        if self._is_windows:
            try:
                import winsound
                self._winsound = winsound
                self.backend = "winsound"
                print("backend=winsound")
                return
            except Exception:
                self._winsound = None
        try:
            from kivy.core.audio import SoundLoader
            self._kivy_sound = SoundLoader.load(wav_path)
            if self._kivy_sound is not None:
                self._kivy_sound.loop = self._loop
            else:
                self.error = self.error or "SoundLoader.load returned None"
            self.backend = "soundloader"
            print("backend=soundloader")
        except Exception as e:
            self._kivy_sound = None
            self.backend = "none"
            self.error = f"{type(e).__name__}: {e}"[:70]

    def _init_audio_track(self, wav_path):
        # ⚠️ pyjnius **没有** jarray —— 早期版本写成 `from jnius import autoclass, jarray`,
        # 这一行直接 ImportError, 整个 AudioTrack 路径从未执行过一次(静默回退 SoundLoader,
        # 循环点每 15s 卡一次)。byte[] 参数直接传 Python bytes 即可, 见下方 write 处注释。
        from jnius import autoclass

        # 解析 WAV 头 + 提取 PCM 裸数据
        with open(wav_path, 'rb') as f:
            data = f.read()
        if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
            raise ValueError("Not a WAV file")
        sample_rate = int.from_bytes(data[24:28], 'little')
        channels = int.from_bytes(data[22:24], 'little')
        bits = int.from_bytes(data[34:36], 'little')
        if bits != 16:
            raise ValueError(f"Only 16bit WAV supported, got {bits}")
        # 遍历 chunk 找 data(WAV chunk 2 字节对齐:奇数 size 补 1 字节)
        pcm = None
        idx = 12
        while idx + 8 <= len(data):
            chunk_id = data[idx:idx+4]
            chunk_size = int.from_bytes(data[idx+4:idx+8], 'little')
            if chunk_id == b'data':
                pcm = data[idx+8:idx+8+chunk_size]
                break
            idx += 8 + chunk_size + (chunk_size & 1)  # 奇数对齐
        if pcm is None:
            raise ValueError("No data chunk in WAV")

        # 设备原生输出率(仅诊断用)。**不做重采样**: AudioFlinger 的 SRC 挂在 track 流上,
        # loop 在更上游解析成连续重复流, 不会在循环点重置相位; 而纯 Python 逐样本重采样
        # 72 万样本跑在 UI 线程上会冻屏数秒。采样率不一致交给系统 SRC 即可。
        AudioManager = autoclass('android.media.AudioManager')
        AudioTrack = autoclass('android.media.AudioTrack')
        try:
            native_rate = int(AudioTrack.getNativeOutputSampleRate(AudioManager.STREAM_MUSIC))
        except Exception:
            native_rate = sample_rate
        print(f"_init_audio_track wav_rate={sample_rate} native_rate={native_rate}")

        AudioFormat = autoclass('android.media.AudioFormat')
        channel_out = (AudioFormat.CHANNEL_OUT_STEREO if channels == 2
                       else AudioFormat.CHANNEL_OUT_MONO)

        # 方法1: $Builder(兼容 API 23+, pyjnius 需 $ 符号访问嵌套类)
        track = None
        try:
            ATBuilder = autoclass('android.media.AudioTrack$Builder')
            AABuilder = autoclass('android.media.AudioAttributes$Builder')
            AFBuilder = autoclass('android.media.AudioFormat$Builder')
            AudioAttributes = autoclass('android.media.AudioAttributes')

            attrs = (AABuilder()
                     .setUsage(AudioAttributes.USAGE_MEDIA)
                     .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                     .build())
            fmt = (AFBuilder()
                   .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                   .setSampleRate(sample_rate)
                   .setChannelMask(channel_out)
                   .build())
            track = (ATBuilder()
                     .setAudioAttributes(attrs)
                     .setAudioFormat(fmt)
                     .setBufferSizeInBytes(len(pcm))
                     .setTransferMode(AudioTrack.MODE_STATIC)
                     .build())
        except Exception as e:
            print(f"Builder init failed: {e}, trying legacy constructor")

        # 方法2: 传统构造函数(API 3+, 某些设备 Builder 不可用时兜底)
        if track is None:
            try:
                track = AudioTrack(
                    AudioManager.STREAM_MUSIC,
                    sample_rate,
                    channel_out,
                    AudioFormat.ENCODING_PCM_16BIT,
                    len(pcm),
                    AudioTrack.MODE_STATIC)
            except Exception as e:
                raise ValueError(f"All AudioTrack constructors failed: {e}")

        # ⚠️ MODE_STATIC 的 track 在**写入数据前**状态必然是 STATE_NO_STATIC_DATA(2) ——
        # 官方定义:"已成功初始化、使用静态数据、但还没收到那份数据的 AudioTrack"。
        # 写完 PCM 才会转成 STATE_INITIALIZED(1)。所以这里拿 ==INITIALIZED 当前置校验
        # 是**永远不可能通过**的(历史 bug: 真机报 state=2 → 抛异常 → 静默回退 MediaPlayer)。
        # 正确做法:写前接受 {INITIALIZED, NO_STATIC_DATA},写后再确认 == INITIALIZED。
        STATE_NO_STATIC_DATA = getattr(AudioTrack, 'STATE_NO_STATIC_DATA', 2)
        state = track.getState()
        if state not in (AudioTrack.STATE_INITIALIZED, STATE_NO_STATIC_DATA):
            raise ValueError(
                f"AudioTrack ctor state={state}, "
                f"expected {AudioTrack.STATE_INITIALIZED} or {STATE_NO_STATIC_DATA}")

        # 写入 PCM 数据。pyjnius 对 '[B' 参数的 bytes/bytearray 有专门支持:
        #   calculate_score  → '[B' 遇 bytes +10 分, '[S' 遇 bytes 直接 -1
        #                      ⟹ 三参调用确定性命中 write(byte[],int,int), 不会误选 short[]
        #   convert_pyarray_to_java → bytes 走单次 SetByteArrayRegion 整块拷贝(列表才是慢路径)
        # ⚠️ MODE_STATIC 的 native writeToTrack() 每次 write 都 memcpy 到共享缓冲**起始处**,
        #    分块写会让最后一块落到 offset 0 —— 必须一次写完。
        if not isinstance(pcm, (bytes, bytearray)):
            pcm = bytes(pcm)
        written = track.write(pcm, 0, len(pcm))
        if written != len(pcm):
            raise ValueError(
                f"AudioTrack.write incomplete: {written}/{len(pcm)} bytes")

        # 写完数据后才该是 INITIALIZED(见上面的状态说明)
        state = track.getState()
        if state != AudioTrack.STATE_INITIALIZED:
            raise ValueError(
                f"AudioTrack not initialized after write: state={state}, "
                f"expected={AudioTrack.STATE_INITIALIZED}")

        # 硬件循环点
        frame_size = channels * (bits // 8)
        total_frames = len(pcm) // frame_size
        result = track.setLoopPoints(0, total_frames, -1 if self._loop else 0)
        if result != 0:  # SUCCESS = 0
            raise ValueError(f"setLoopPoints failed: {result}")

        self._loop_frames = total_frames
        self._audio_track = track

    def play(self):
        if self._audio_track is not None:
            if self._active:
                return
            try:
                # 重播时才 reloadStaticData(三星兼容),首次播放直接 play
                if self._needs_reload:
                    try:
                        self._audio_track.reloadStaticData()
                        self._needs_reload = False
                    except Exception:
                        self._needs_reload = True   # 失败保留, 下次重试(避免PLAYING时误reload竞态)
                self._audio_track.setPlaybackHeadPosition(0)
                # native 层 reload()/setPosition() 会清掉 loop 状态(setLoop(...,0)),
                # 每次播放前重新武装, 否则"停止再播"只响一遍就没了
                if self._loop_frames:
                    self._audio_track.setLoopPoints(
                        0, self._loop_frames, -1 if self._loop else 0)
                self._audio_track.play()
                self._active = True  # 成功后置,防异常后半永久静音
            except Exception:
                pass
            return
        if self._winsound is not None:
            if self._active:
                return
            self._active = True
            try:
                flags = self._winsound.SND_ASYNC | self._winsound.SND_FILENAME
                if self._loop:
                    flags |= self._winsound.SND_LOOP
                self._winsound.PlaySound(self._wav_path, flags)
            except Exception:
                self._active = False
            return
        if self._kivy_sound is not None:
            try:
                if self._kivy_sound.state != "play":
                    self._kivy_sound.play()
            except Exception:
                pass

    def stop(self):
        if self._audio_track is not None:
            if not self._active:
                return
            self._active = False
            self._needs_reload = True
            try:
                self._audio_track.pause()
                self._audio_track.flush()
                self._audio_track.stop()
            except Exception:
                pass
            return
        if self._winsound is not None:
            if not self._active:
                return
            self._active = False
            try:
                self._winsound.PlaySound(None, self._winsound.SND_PURGE)
            except Exception:
                pass
            return
        if self._kivy_sound is not None:
            try:
                self._kivy_sound.stop()
            except Exception:
                pass

    def close(self):
        """释放底层后端资源。切换音效时在 stop() 后调用,防 AudioTrack 句柄泄漏。"""
        if self._audio_track is not None:
            try:
                self._audio_track.release()
            except Exception:
                pass
            self._audio_track = None
            return
        if self._kivy_sound is not None:
            try:
                self._kivy_sound.stop()
                self._kivy_sound.unload()
            except Exception:
                pass
            self._kivy_sound = None


def _read_wav_pcm(path):
    """读 WAV -> (裸 PCM bytes, 采样率, 声道)。只支持 16bit,失败抛异常。"""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE" or len(data) < 36:
        raise ValueError("Not a WAV file")
    rate = int.from_bytes(data[24:28], "little")
    channels = int.from_bytes(data[22:24], "little")
    if int.from_bytes(data[34:36], "little") != 16:
        raise ValueError("Only 16bit WAV supported")
    idx = 12
    while idx + 8 <= len(data):
        size = int.from_bytes(data[idx + 4:idx + 8], "little")
        if data[idx:idx + 4] == b"data":
            return data[idx + 8:idx + 8 + size], rate, channels
        idx += 8 + size + (size & 1)
    raise ValueError("No data chunk in WAV")


_CHIME_CACHE = {}


def _completion_chime(rate):
    """播报前的短钟声(C 大三和弦琶音, 与 tools/generate_completion_voice.py 同配方)。

    现场合成而不是烘成文件:只需要 0.55s 的正弦叠加,还免掉一个 wav 进 APK。
    合成是纯 Python 三重循环(~1.3 万帧),必须缓存 —— 否则每次流尽都在 UI 线程
    上现算,正好叠在完成闪烁那一帧上。
    """
    cached = _CHIME_CACHE.get(rate)
    if cached is not None:
        return cached
    notes = ((523.25, 0.0), (659.25, 0.10), (783.99, 0.20))
    samples = []
    for i in range(int(rate * 0.55)):
        t = i / rate
        value = 0.0
        for frequency, start in notes:
            age = t - start
            if age >= 0:
                envelope = min(1.0, age / 0.012) * math.exp(-age * 12)
                value += math.sin(math.tau * frequency * age) * envelope * 0.075
        samples.append(round(value * min(1.0, (0.55 - t) / 0.05) * 32767))
    pcm = struct.pack(f"<{len(samples)}h", *samples) + b"\0\0" * round(rate * 0.12)
    _CHIME_CACHE[rate] = pcm
    return pcm


class _VoiceBank:
    """预录词块 -> 完成播报 PCM 拼接。

    完成语随周期变化("两小时三十分零五秒的沙漏计时完成"),无法预录成一条。这里把
    可能出现的每个词烘成一个小 wav,播报时按需拼成**一段** PCM 再交给 AudioTrack ——
    一次播放、无接缝、不占第二个通道(winsound 单通道,分段播会被下段掐掉)。

    词块缺失或采样率不一致 -> ok=False,调用方回退到预录的整句 completion.wav。
    """

    def __init__(self, root):
        self.rate = 0
        self.clips = {}
        self.ok = False
        path = os.path.join(root, "sounds", "voice")
        if not os.path.isdir(path):
            print("Voice tokens missing: sounds/voice/")
            return
        try:
            for name in sorted(os.listdir(path)):
                if not name.endswith(".wav"):
                    continue
                pcm, rate, channels = _read_wav_pcm(os.path.join(path, name))
                if channels != 1:
                    raise ValueError(f"{name}: expected mono, got {channels}")
                if self.rate and rate != self.rate:
                    raise ValueError(f"{name}: rate {rate} != {self.rate}")
                self.rate = rate
                self.clips[name[:-4]] = pcm
        except Exception as exc:
            print(f"Voice token load failed: {exc}")
            self.clips, self.rate = {}, 0
            return
        missing = [k for k in ("n0", "n1", "n60", "d1", "ten", "hundred",
                               "hour", "min", "sec", "tail") if k not in self.clips]
        if missing:
            print(f"Voice tokens incomplete, missing: {', '.join(missing)}")
            self.clips, self.rate = {}, 0
            return
        self.ok = True
        print(f"voice bank: {len(self.clips)} tokens @ {self.rate}Hz")

    def _hour_keys(self, hours):
        """1..100 小时:1-60 用整词,61-99 拆成 六十/一,100 = 一百。"""
        if hours <= 60:
            return [f"n{hours}"]
        if hours == 100:
            return ["d1", "hundred"]
        tens, ones = divmod(hours, 10)
        keys = [f"d{tens}", "ten"]
        if ones:
            keys.append(f"d{ones}")
        return keys

    def sentence_keys(self, seconds):
        """拼出"X小时Y分Z秒的沙漏计时完成"的词块序列(零分量省略,中间夹"零")。"""
        total = max(0, int(round(seconds)))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        keys = []
        if hours:
            keys += self._hour_keys(hours) + ["hour"]
        if minutes:
            keys += [f"n{minutes}", "min"]
        elif hours and secs:
            keys.append("n0")               # "一小时零三十秒"
        if secs:
            keys += [f"n{secs}", "sec"]
        if not keys:
            keys += ["n0"]
        return keys + ["tail"]

    def build_pcm(self, seconds):
        return b"".join(self.clips[k] for k in self.sentence_keys(seconds))


class _SandBgPopup(Popup):
    """浅色背景 Popup,覆盖 Kivy 默认深灰风格(双层兜底)"""
    def __init__(self, bg_hex=POPUP_BG, **kwargs):
        self._bg_hex = bg_hex
        self._bg_rgb = hex_rgb(bg_hex)
        kwargs.setdefault('separator_color', (*POPUP_GOLD_SEL[:3], 0.3))
        kwargs.setdefault('title_color', (1, 1, 1, 1))
        kwargs.setdefault('title_align', 'center')
        super().__init__(**kwargs)
        # 兜底层: Popup 本体 canvas.before(填充容器外间隙)
        with self.canvas.before:
            Color(*self._bg_rgb, 1)
            self._popup_bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._upd_popup_bg, size=self._upd_popup_bg)

    def _upd_popup_bg(self, inst, _value):
        self._popup_bg.pos = inst.pos
        self._popup_bg.size = inst.size

    def _handle_keyboard(self, _window, key, *_args):
        """Android 返回键(27) = 关掉**最上面这个弹窗**, 不是退出 App。

        ⚠️ Kivy `ModalView._handle_keyboard` 的原文是
           `if key == 27 and self.auto_dismiss: self.dismiss(); return True`
           —— 只在 `auto_dismiss=True` 时才**消费** 27。本 App 的弹窗**全是
           `auto_dismiss=False`** ⇒ 27 不被消费, 一路冒到 Window ⇒ p4a 把
           "没有控件要的返回键"当退出 ⇒ **在完成/周期/音效弹窗上按一下返回,
           整个 App 退到桌面**(r14-2号 报, r15-1号 在三个弹窗上逐个复现, 2026-10-05)。

        用户定的"不点不关"说的是**点弹窗外面**, 与返回键不冲突 ——
        返回键是 Android"关掉当前这一层"的约定, 是一次明确的用户动作。
        """
        if key == 27:
            self.dismiss()
            return True
        return False

    def open(self, *args, **kwargs):
        layer = _land_layer()
        if layer is None or layer.angle == 0:
            super().open(*args, **kwargs)          # 竖屏回落原生行为(零回归)
            Clock.schedule_once(self._apply_light_theme, 0)
            return
        # 横屏: 不再把弹窗挂 Window(Window 不旋转), 改挂旋转层让其随层旋转成竖构图。
        # 弹窗用 size_hint=(0.88, None), 直接挂层会按"物理长边×0.88"放大溢出, 故按
        # "等效竖屏窗宽=短边"换算显式 size, 再整体旋转 → 视觉与竖屏一致。
        if self._is_open:
            return
        eq_w = min(Window.width, Window.height)    # 等效竖屏窗宽=短边
        frac = None
        if self.size_hint and self.size_hint[0] is not None:
            frac = self.size_hint[0]
        else:
            frac = 0.88
        self.size_hint = (None, None)
        self.size = (frac * eq_w, self.height)
        self._window = layer                        # 宿主从 Window 换成旋转层
        self._is_open = True
        self.dispatch('on_pre_open')
        if not self.pos_hint:
            self.pos_hint = {"center_x": 0.5, "center_y": 0.5}
        layer.add_widget(self)                      # 挂到层而非 Window
        layer.bind(on_resize=self._align_center, on_keyboard=self._handle_keyboard)
        self.center = layer.center
        self.fbind('center', self._align_center)
        self.fbind('size', self._align_center)
        self._anim_alpha = 1.
        self.dispatch('on_open')
        Clock.schedule_once(self._apply_light_theme, 0)

    def _real_remove_widget(self):
        """覆写: dismiss 时从旋转层对称摘除(非横屏时 host=Window, 行为等同原生)。"""
        if not self._is_open:
            return
        self._window.remove_widget(self)
        try:
            self._window.unbind(on_resize=self._align_center,
                                on_keyboard=self._handle_keyboard)
        except Exception:
            pass
        self._is_open = False
        self._window = None

    def _apply_light_theme(self, *_):
        """清空 _container 深色背景,替换为浅色"""
        try:
            container = self.content.parent
            container.canvas.before.clear()
            with container.canvas.before:
                Color(*self._bg_rgb, 1)
                self._ctr_bg = Rectangle(pos=container.pos, size=container.size)
            container.bind(pos=self._upd_ctr_bg, size=self._upd_ctr_bg)
        except Exception:
            pass

    def _upd_ctr_bg(self, inst, _value):
        self._ctr_bg.pos = inst.pos
        self._ctr_bg.size = inst.size


# ---------- 横屏反旋转层(移植自 hengping.md §4; 竖屏 angle=0 时行为等同无此层) ----------

class LandLayer(FloatLayout):
    """横屏时把"等效竖屏窗口"整体旋转 ±90° 铺满横窗; 竖屏 angle=0 零回归。
    结构: LandLayer(旋转层, 铺满整窗) → anchor(AnchorLayout, 等效竖屏盒) → 实际 UI(root)。
    """
    def __init__(self, **kw):
        super().__init__(**kw)
        self.angle = 0            # 渲染旋转角: 0=竖屏无旋转, 其他=±90
        self._anchor = None       # "等效竖屏窗口"容器(AnchorLayout), 由 build 塞入
        with self.canvas.before:
            PushMatrix()
            self._rot = Rotate(angle=0, axis=(0, 0, 1), origin=(0, 0))
        with self.canvas.after:
            PopMatrix()

    def apply_orientation(self):
        """窗口尺寸变化时重算: 层永远铺满整窗, anchor 按"等效竖屏盒"定尺寸并绕屏中心旋转。"""
        w, h = Window.width, Window.height
        land = (w > h and (platform == "android" or "--landscape" in sys.argv)
                and _device_is_wide())
        self.pos = (0, 0)
        self.size = (w, h)                          # 层永远铺满
        self.angle = _land_angle() if land else 0
        self._rot.angle = self.angle
        self._rot.origin = (w / 2.0, h / 2.0)       # 绕屏幕中心旋转
        if self._anchor is not None:
            self._anchor.size = (h, w) if land else (w, h)
            self._anchor.center = self.center
        return land

    def _to_eq(self, x, y):
        """屏幕(物理)坐标 → 等效竖屏(逻辑)坐标; 逆变换, 触摸分发用。"""
        if self.angle == 0:
            return (x, y)
        cx, cy = Window.width / 2.0, Window.height / 2.0
        dx, dy = x - cx, y - cy
        if self.angle == 90:
            return (cx + dy, cy - dx)               # 逆时针旋转 → 顺时针转回
        return (cx - dy, cy + dx)                   # angle == -90

    def _to_win(self, x, y):
        """等效竖屏(逻辑)坐标 → 屏幕(物理)坐标; 正变换, to_parent 用。两者互逆。"""
        if self.angle == 0:
            return (x, y)
        cx, cy = Window.width / 2.0, Window.height / 2.0
        dx, dy = x - cx, y - cy
        if self.angle == 90:
            return (cx - dy, cy + dx)               # 正变换 = 逆时针 90
        return (cx + dy, cy - dx)                   # angle == -90

    # grab 后的 move/up 不经 on_touch_*, 而是 Window 沿祖先链调 to_local 换算 → 必须覆写
    def to_local(self, x, y, **k):
        return self._to_eq(x, y)

    def to_parent(self, x, y, **k):
        return self._to_win(x, y)

    def _pass_touch(self, method, touch):
        if self.angle == 0:
            return method(touch)                    # 竖屏直接透传, 零回归
        touch.push()
        touch.apply_transform_2d(self._to_eq)       # 逆旋转, 把 x/y/pos/ox/oy/px/py 一起变
        ret = method(touch)
        touch.pop()
        return ret

    def on_touch_down(self, touch):
        return self._pass_touch(super().on_touch_down, touch)

    def on_touch_move(self, touch):
        return self._pass_touch(super().on_touch_move, touch)

    def on_touch_up(self, touch):
        return self._pass_touch(super().on_touch_up, touch)


# ---------- 沙漏画布 ----------

class HourglassWidget(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.duration = 50.0
        self.elapsed = 0.0
        self.running = False
        self.last_tick = None
        self.last_frame = time.perf_counter()

        self.sand_base = hex_rgb(SAND_PRESETS[0][1])
        self.sand_dark = hex_rgb(SAND_PRESETS[0][2])
        self.sand_light = hex_rgb(SAND_PRESETS[0][3])
        self._color_table = []
        self._rebuild_color_table()

        self._geom_ready = False
        self._vol_to_height = []

        self.pn = 0
        self._p_alloc(2048)
        # 渲染层的读端(分组/打包/颈部颗粒)按**下标**读这份 list 快照, 见 _p_refresh_view。
        self._pv = _FlowView()
        self._p_dict_cache = None
        self.particles = []
        self.particle_acc = 0.0
        self.splashes = []
        self._bg_splash_acc = 0.0
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        self._geom_generation = 0            # 几何代: 尺寸/周期/窗口一变就 +1, 形状缓存跟着失效
        self._mound_profile = None           # 下球沙堆形状解(见 _MoundProfile), 几何重建时换新
        self._mound_shape = ()               # 65 点轮廓(绝对值): 绘制节点/接触查表都用它
        self._upper_carve = []               # 上球漏斗 carve(§4)
        self._upper_band = []
        self._upper_band_color = None
        self._surface_marker_pool = []
        self._surface_marker_n = 0
        self._mound_shape_cache = None       # ((几何代, elapsed), apex) —— 每帧只解一次
        self._mound_curve_cache = None       # ((几何代, elapsed), (cx+dx, y)) 接触曲线
        self._completion_triggered = False
        self._done_at = None                 # 漏完时刻(颈管排空用), 未漏完为 None
        self._completion_token = 0          # 作废"待弹的完成提示"用, 见 _schedule_completion_popup
        self._sand_material = None          # 沙体材质纹理(见 sand_material); None = 平色填充
        # 拖动「沙子浓度」滑块时的低分辨率材质槽(见 preview_sand_material)。
        # 非 None 时它**优先于** _sand_material, 松手/换色即清空。
        self._preview_material = None

        self.sound_name = "沙沙声"
        self._sound = self._make_sound_proxy(self.sound_name)
        self.completion_enabled = True
        self._completion_sound = self._make_completion_sound()
        self._voice_bank = _VoiceBank(resource_path(""))
        self._completion_spoken = None      # 动态拼出的播报(每条周期重建一次)
        if self._voice_bank.ok:
            _completion_chime(self._voice_bank.rate)   # 预热,别让首播卡在完成那一帧

        self.bind(size=self._on_size, pos=self._on_size)
        Clock.schedule_once(self._on_size, 0)
        Clock.schedule_interval(self.tick, 0)

    # ---------- 几何(自适应; Kivy y 向上) ----------

    def _on_size(self, *_):
        self._rebuild_height_table()

    @property
    def neck_w(self):
        """颈部半宽,log 插值:短周期→宽,长周期→窄,上下限保证沙流可视"""
        w = self.width
        lo = max(dp(7), round(w * 7.0 / 380.0))     # 最细: 管内壁仍有空间
        hi = round(w * 17.0 / 380.0)                 # 最粗: 不压过球的比例
        dur = max(1.0, self.duration)
        if dur <= 5:
            return hi
        if dur >= 36000:
            return lo
        lo_d, hi_d = math.log(5), math.log(36000)
        t = (math.log(dur) - lo_d) / (hi_d - lo_d)
        t = max(0.0, min(1.0, t))
        return round(lo + (hi - lo) * (1 - t))

    @property
    def speed_factor(self):
        return max(0.5, min(2.5, 60.0 / max(0.1, self.duration)))

    def _rebuild_height_table(self):
        w, h = self.width, self.height
        if w <= 1 or h <= 1:
            return
        cx = self.x + w / 2.0
        ow = max(2.0, w * (6.0 / 380.0))
        tube_h = h * 0.055   # 给球↔管的曲线过渡留出竖直空间
        side_margin = w * 0.06
        v_pad = h * 0.02
        nw = self.neck_w

        # R 同时受"宽不溢出"和"高放得下两球+管"约束,取更紧者;在 380x730 下 ≈168
        R_by_w = w / 2.0 - side_margin
        R_by_h = (h - tube_h - 2 * v_pad) / 4.0
        R = max(dp(10), min(R_by_w, R_by_h))
        # 由 R 反推 ball_h(球顶到截口高),使球顶 w=0 且截口处 w=neck_w 严格成立
        ball_h = R + math.sqrt(max(0.0, R * R - nw * nw))

        neck_y = self.y + h / 2.0
        glass_top = neck_y + ball_h + tube_h / 2.0   # 上球顶(最大 y)
        glass_bot = neck_y - ball_h - tube_h / 2.0   # 下球底(最小 y)

        self._cx = cx
        self._R = R
        self._ow = ow
        self._tube_h = tube_h
        self._ball_h = ball_h
        self._neck_y = neck_y
        self._glass_top = glass_top
        self._glass_bot = glass_bot
        self._upper_y_c = glass_top - R          # 上球心
        self._lower_y_c = glass_bot + R          # 下球心
        self._upper_ball_cut = glass_top - ball_h  # 上球截口(接管,较低 y)
        self._lower_ball_cut = glass_bot + ball_h  # 下球截口(接管,较高 y)

        Ri = R - ow
        self._R_inner = Ri
        self._upper_sand_top = self._upper_y_c + Ri   # 上沙满沙顶(最高)
        self._upper_sand_bot = self._upper_y_c - Ri   # 上沙空沙底(接管)
        self._lower_sand_top = self._lower_y_c + Ri   # 下沙满沙顶(接管)
        self._lower_sand_bot = self._lower_y_c - Ri   # 下沙空堆底(最低)

        # 完整球体积查找表 v(t)=∫w²dy, t=0 球顶 → t=1 截口(外壁 R 算,比例无量纲)
        n = 101
        dy = ball_h / (n - 1)

        def w2(t):
            y = glass_top - t * ball_h
            return max(0.0, R * R - (y - self._upper_y_c) ** 2)

        V_total = 0.0
        prev = w2(0.0)
        for i in range(1, n):
            cur = w2(i / (n - 1))
            V_total += (prev + cur) / 2 * dy
            prev = cur
        table = [(0.0, 0.0)]
        cum = 0.0
        prev = w2(0.0)
        for i in range(1, n):
            cur = w2(i / (n - 1))
            cum += (prev + cur) / 2 * dy
            table.append((cum / V_total if V_total > 0 else 0.0, i / (n - 1)))
            prev = cur
        self._vol_to_height = table

        # ---- 球↔管 曲线收窄过渡(Kivy y 向上, 与 pc v4 上下镜像) ----
        # 球拿极点接管子, 球面在极点附近近乎水平 → 环壁横向摊开 sqrt(R²-Ri²),
        # 与竖直管壁形成近 90° 硬折角+扁平"肩台"(垫块感)。这里把肩台挖掉, 改成
        # 球壁 →(相切) 贝塞尔曲线 →(相切) 短直筒 的连续轮廓。只改渲染, 不动 raw/守恒。
        t_out = nw                                   # 管外壁半宽
        t_in = max(1.0, nw - ow)                     # 管内壁半宽(= 沙柱/粒子通道)
        shoulder = math.sqrt(max(0.0, R * R - Ri * Ri))
        # 过渡起点必须 ≥ 肩台半宽, 否则起点以上仍是那条扁平暗带(细颈时尤其明显)
        w_out = min(R * 0.45, max(t_out + 2.0, TAPER_K * nw, shoulder * 1.06))
        y_out = self._upper_y_c - math.sqrt(max(0.0, R * R - w_out * w_out))
        y_bot = y_out - max(2.0, (y_out - neck_y) * TAPER_FILL)
        w_in = max(t_in + 1.0, w_out - ow)
        y_in = self._upper_y_c - math.sqrt(max(0.0, Ri * Ri - w_in * w_in))
        # 膝点 = 球在过渡起点处的切线与管壁线的交点 → 保证与球弧相切
        y_knee_o = y_out - (w_out - t_out) * w_out / math.sqrt(max(1e-6, R * R - w_out * w_out))
        y_knee_i = y_in - (w_in - t_in) * w_in / math.sqrt(max(1e-6, Ri * Ri - w_in * w_in))
        y_bot = max(min(y_bot, y_knee_o - 2.0, y_knee_i - 2.0), neck_y + 2.0)
        self._taper = {
            'y_bot': y_bot, 't_out': t_out, 't_in': t_in,
            'out_pts': _bezier2((w_out, y_out), (t_out, y_knee_o), (t_out, y_bot), TAPER_SEGS),
            'in_pts': _bezier2((w_in, y_in), (t_in, y_knee_i), (t_in, y_bot), TAPER_SEGS),
        }
        # 下球沙堆的形状解: **几何一变就重建**(专家 §2.2/§2.6)
        #   轮廓是 65 点固定数组(微不对称尖堆 + 受限微粗糙), **与沙量无关** ⇒
        #   两张面积表只建一次, 每帧只查一次表求逆。粗糙数组用固定 seed, 整轮不重抽。
        self._geom_generation += 1
        try:
            shape = _mound_shape_array(Ri, UPPER_ROUGH_FRAC)
            self._mound_profile = _MoundProfile(Ri, shape)
            # 上球微粗糙的 65 点数组(与下球同一套节点口径, 但独立 seed / 独立幅度)
            _uamp = UPPER_ROUGH_FRAC * 2.0 * Ri
            self._upper_rough = _surface_roughness(Ri, UPPER_ROUGH_FRAC, UPPER_ROUGH_SEED, _uamp)
            # 演化帧: 与静态版同幅度、同节点口径, 只是**随时间平滑地换形状**
            self._upper_rough_frames = _build_rough_frames(Ri, UPPER_ROUGH_FRAC, UPPER_ROUGH_SEED)
            self._upper_rough_cache = None
            self._mound_shape = tuple(shape)
            # ---- 下球轮廓的**演化帧**（用户 2026-10-05: "下球斜面也应该有起伏" + "要动"）----
            # ⚠️ 每帧扰动**减掉自己的均值** ⇒ 面积精确不变（用户原话"有高就有低"），
            #    但**只减一个常数平移** —— 空间上的高低起伏仍是**不规则的**，而且逐帧在变，
            #    不会变"均衡"（用户 2026-10-05: "不均衡, 但是又随机变化"）。
            # ⚠️ **预烘 64 个 `_MoundProfile`**（实测 0.25ms/个 ⇒ 共 16ms 一次性）:
            #    物理热循环里只是**换一个指针**, 逐颗粒零成本; 而且面积表与画出来的轮廓
            #    **天生一致** —— 这是"逐帧改轮廓"还能保守恒的关键。
            _nm = MOUND_SHAPE_NODES
            _hm = (_nm - 1) // 2
            _dxm = Ri / _hm
            _basem = [-(MOUND_SLOPE_L if (i - _hm) < 0 else MOUND_SLOPE_R)
                      * abs((i - _hm) * _dxm) for i in range(_nm)]
            _frames = []
            for _fr in _build_rough_frames(Ri, UPPER_ROUGH_FRAC, MOUND_ROUGH_SEED):
                _mu = sum(_fr) / len(_fr)
                _shp = [_basem[i] + (_fr[i] - _mu) for i in range(_nm)]
                _frames.append((tuple(_shp), _MoundProfile(Ri, _shp)))
            self._mound_frames = _frames
            self._mound_frame_k = None
        except ValueError:
            self._mound_profile = None
            self._mound_shape = ()
            self._mound_frames = None
            self._mound_frame_k = None
        self._mound_shape_cache = None
        self._geom_ready = True
        self._build_glass_shell()
        self._build_dynamic_canvas()

    def _raw_height_ratio(self, vol_ratio):
        """体积比 → 高度比 raw=v⁻¹(vol)。

        ⚠️ 球对称曾经给出"上沙(1-raw)+下沙(raw)=1 严格守恒", 那条**已被 2026-10-03 作废**:
        上沙现在只按恒定流速走(`_upper_sand_height_px`), 下沙堆另有一条飞行延迟,
        两者之差是**在途的沙**(`_fall_delay/duration`), 不该再当成误差去消。
        """
        if vol_ratio <= 0:
            return 0.0
        if vol_ratio >= 1:
            return 1.0
        table = self._vol_to_height
        lo, hi = 0, len(table) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if table[mid][0] < vol_ratio:
                lo = mid
            else:
                hi = mid
        v0, x0 = table[lo]
        v1, x1 = table[hi]
        if v1 == v0:
            return x0
        return x0 + (x1 - x0) * (vol_ratio - v0) / (v1 - v0)

    def get_remaining(self):
        return max(0.0, 1 - self.elapsed / self.duration) if self.duration > 0 else 0

    @property
    def _neck_fill_time(self):
        return max(1e-6, min(NECK_FILL, self.duration * 0.15))

    @property
    def _natural_flight_time(self):
        if not self._geom_ready:
            return FALL_DELAY
        outlet = 2 * self._neck_y - self._taper['y_bot']
        distance = max(0, outlet - self._lower_sand_bot)
        return (math.sqrt(50 ** 2 + 900 * distance) - 50) / 450

    @property
    def _fall_delay(self):
        return min(self._neck_fill_time + self._natural_flight_time + 0.05,
                   self.duration * 0.45)

    @property
    def _particle_motion_scale(self):
        """极短周期以缩时播放飞行,首批粒子仍先触底再堆积。"""
        available = max(1e-6, self._fall_delay - self._neck_fill_time)
        return max(1.0, self._natural_flight_time / available)

    def _effective_fallen(self):
        if self.duration <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.elapsed - self._fall_delay) /
                           max(1e-6, self.duration - self._fall_delay)))

    def _mound_floor(self, eff):
        if eff <= 0:
            return dp(MOUND_FLOOR_MIN)
        t = min(1.0, (eff / MOUND_FLOOR_EFF) ** 0.5)
        return dp(MOUND_FLOOR_MIN + (MOUND_FLOOR_MAX - MOUND_FLOOR_MIN) * t)

    def _sync_mound_frame(self):
        """按 `elapsed` 把下球轮廓换成当前那一帧（**只换指针**, 逐颗粒零成本）。

        面积表是**预烘在每个 `_MoundProfile` 里**的 ⇒ 画出来的轮廓与接触高度天然一致,
        不存在"求解读 A、绘制读 B"的裂缝(1.93 那类问题的根源)。
        """
        frames = getattr(self, "_mound_frames", None)
        if not frames:
            return
        nf = len(frames)
        period = UPPER_ROUGH_PERIOD if UPPER_ROUGH_PERIOD > 0 else 1.0
        k = int((self.elapsed / period) % 1.0 * nf) % nf
        if getattr(self, "_mound_frame_k", None) == k:
            return
        self._mound_frame_k = k
        self._mound_shape, self._mound_profile = frames[k]
        self._mound_shape_cache = None

    def _mound_height_px(self):
        eff = self._effective_fallen()
        ball_h_inner = 2 * self._R_inner
        delay = self._fall_delay
        if self.elapsed < delay:
            return 0.0
        target = max(self._raw_height_ratio(eff) * ball_h_inner, self._mound_floor(eff))
        appear_window = max(0.01, min(MOUND_APPEAR, self.duration - delay))
        appear = min(1.0, (self.elapsed - delay) / appear_window)
        return appear * target

    def _upper_sand_height_px(self):
        """上球沙体高度 —— **只由恒定流速决定**, 不跟下沙堆挂钩。

        唯一假设: 体积流速恒定 ⇒ 上球剩余体积比 = 1 − elapsed/duration, 再由 raw 反查高度。
        下沙堆走的是**另一条时钟**(要等粒子真的飞到底, `_fall_delay`), 两者之差 = **还在空中的沙**。

        ⚠️ 旧实现写成 `upper = 满 − 下沙堆`, 于是下沙堆为 0 的那些帧上沙恒为满 ——
        5s 档头 1.35 秒(占整个计时的 27%)上球一动不动
        (逐像素实测: 0.50s→1.01s 上半球只有粒子串那 159 个像素在变, 沙面零变化)。
        ⚠️ 差额 = `_fall_delay / duration`(5s 档 27%, 1s 档 45%), 它是真实存在的在途沙;
        想让它更小, 只能压缩粒子飞行时间(见 `_particle_motion_scale`), 那是另一个决定。
        """
        if self.duration <= 0:
            return 0.0
        t = max(0.0, min(1.0, self.elapsed / self.duration))
        return self._raw_height_ratio(1.0 - t) * 2 * self._R_inner

    def _neck_sand_side(self):
        """颈部沙柱的右半侧轮廓 [(半宽, y), ...] = 上喇叭口曲线 + 直筒(Kivy y 向上)。

        两条刻意的设计(与 pc v4 同源):
        ① **只到直筒下端**, 下喇叭口敞开不填沙 —— 填了会变成"绿喇叭悬在空球上",
           沙流跟颈部反而断开; 敞开后沙从孔口流出, 与粒子自然接上。
        ② 起跑时在 NECK_FILL 秒内**从上往下注满**, 而不是 elapsed>0 一帧切换 ——
           喇叭口面积大, 瞬间从空变满非常刺眼。
        ③ **排空同样要有过程**(2026-10-05 帕累托修复): 原来 `redraw` 用
           `upper_height > 0` 做闸门, 而漏完那一帧 upper_height 恰好归零
           ⇒ 整根沙柱**一帧消失**, 可下落的颗粒还要再飞 0.3s。**进场有动画、退场硬切**。
           两位评审独立量到过(r1-2号: 23ms 内 −4901px; r1-1号/r5-1号: f684→685 一帧掉 4620px)。
           现在漏完后再用同样的 fill_t 把 f 从 1 降到 0 —— 与注满对称, 形状一个字没改。
        """
        tp = self._taper
        pts = tp['in_pts']
        y_top, y_end = pts[0][1], 2 * self._neck_y - tp['y_bot']
        fill_t = self._neck_fill_time
        f = min(1.0, max(0.0, self.elapsed / fill_t))
        if self._done_at is not None:
            d = (time.perf_counter() - self._done_at) / fill_t
            f = min(f, max(0.0, 1.0 - d))
        if f <= 0:
            return []
        # ⚠️ **注满与排空的动边不是同一条**(2026-10-05, r7-1号 实测):
        #    注满: 沙从**上球**经喇叭口注入 ⇒ 顶边钉在喇叭口上端, **下缘往下长**;
        #    排空: 沙从**出口**流走 ⇒ 自由表面只能**下降**, 底边钉在出口, **顶边往下退**。
        #    原实现两者共用 `fill_y = y_top - (y_top-y_end)*f`, 排空时下缘从 y_end 爬回 y_top
        #    ⇒ 画成"沙被从下面吸上去"(1号: top_y 恒 410.28 / bottom_y 373.65→408.82),
        #    与真沙漏相反。两条路径的**端点相同**(f=1 满柱 / f=0 空), 只有中段不同。
        draining = self._done_at is not None
        fill_y = (y_end + (y_top - y_end) * f) if draining else (y_top - (y_top - y_end) * f)
        w = tp['t_in']
        if fill_y >= tp['y_bot']:          # 截断点还在曲线段 → 插值取半宽
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if y1 <= fill_y <= y0:
                    w = x0 + (x1 - x0) * (y0 - fill_y) / max(1e-6, y0 - y1)
                    break
        if draining:
            # 保留曲线段中位于截断点**以下**的部分; 截断点在顶端 ⇒ 补在最前
            # 出口端始终保留一段直筒(底边钉住) ⇒ 消费者看的 `side[-1]` 恒 = 出口 ⇒ connected
            side = [(w, fill_y)]
            side += [(x, y) for x, y in pts if y < fill_y]
            side.append((tp['t_in'], y_end))
        else:
            side = [(x, y) for x, y in pts if y > fill_y]
            side.append((w, fill_y))
        return side

    def _mound_apex(self):
        """本帧的**虚拟**锥顶高度(绝对, 离球内底) —— 每帧只解一次。

        解由 `_MoundProfile.apex_for_height(h)` 给出(面积表求逆, 不是每帧二分):
        h = 体积反查出来的"等面积平顶弓形高度"(时间映射一个字没改)。
        ⚠️ 缓存键 = **(几何代, elapsed)** —— 只认 elapsed 的话, 暂停时改尺寸/换周期
        (几何变了、时间没变)会拿到上一套球半径的结果(专家 §2.6)。
        """
        key = (self._geom_generation, self.elapsed)
        cached = self._mound_shape_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        profile = self._mound_profile
        h = self._mound_height_px()
        apex = 0.0 if (profile is None or h <= 0.0) else profile.apex_for_height(h)
        self._mound_shape_cache = (key, apex)
        return apex

    def _mound_contact_curve(self):
        """本帧的**接触高度曲线** `H(x)`(绝对设备坐标, 均匀 129 节点)。

        专家 dingbu.md §7: 取消平台后不能再只认一个 `y` —— 否则颗粒会在斜坡上方消失、
        或钻进沙堆继续可见。这条曲线与 `_draw_mound_shape` 的折线**同一份 `contact()`**,
        也与 splash/dust 的判定同源。
        均匀网格 ⇒ 粒子侧可以 O(1) 定位, 不必二分。
        缓存键 = (几何代, elapsed): 同一帧里物理与绘制都要用, 只算一次。
        """
        key = (self._geom_generation, self.elapsed)
        cached = self._mound_curve_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        prof = self._mound_profile
        if prof is None:
            out = ([], [], 0.0, 0.0, 0.0)
        else:
            n = MOUND_CURVE_SAMPLES
            r = self._R_inner
            apex = self._mound_apex()
            base = self._lower_sand_bot
            cx = self._cx
            contact = prof.contact
            xs = [0.0] * n
            ys = [0.0] * n
            for i in range(n):
                dx = -r + 2.0 * r * i / (n - 1)
                xs[i] = cx + dx
                ys[i] = base + contact(dx, apex)
            out = (xs, ys, xs[0], (n - 1) / (xs[-1] - xs[0]), n - 1)
        self._mound_curve_cache = (key, out)
        return out

    def _mound_contact_h(self, dx):
        """给定横向偏移处的**接触高度**(离球内底, 绝对) —— 与绘制同一份定义。"""
        profile = self._mound_profile
        apex = self._mound_apex()
        if profile is None or apex <= 0.0:
            return 0.0
        return profile.contact(dx, apex)

    def _mound_top_at(self, x):
        """绝对 y 版的接触高度 —— splash / 尘埃用(它们会跑到平台之外)。"""
        return self._lower_sand_bot + self._mound_contact_h(x - self._cx)

    def get_mound_top_y(self):
        """粒子主流的碰撞面(**一个标量**, 见常量区"路线 A")。

        = 中轴处的接触高度 = clamp(虚拟锥顶, 球内底, 球内顶) —— **必须裁剪**:
        专家 §5.1 指出, 放开 a>D 之后原样返回未裁剪的 a 会制造更明显的悬空碰撞。
        ⚠️ 接近满球时圆顶各点高度不同 ⇒ 标量与可见面**不可能全阶段严格重合**,
        这是路线 A 的已知近似(末期误差另有验收, 见 xingzhuang2.md §5.2)。
        """
        return self._lower_sand_bot + self._mound_contact_h(0.0)

    def _sand_half_w(self, y, yc):
        Ri = self._R_inner
        return math.sqrt(max(0.0, Ri * Ri - (y - yc) ** 2))

    # ---------- 控制 ----------

    def toggle(self):
        if self.running:
            self.running = False
            self._stop_sound()
        else:
            self._stop_completion_sound()
            if self.elapsed >= self.duration:
                self.elapsed = 0
                self._reset_run_state()
            elif self.elapsed == 0:
                gc.collect()
            self.running = True
            self.last_tick = self.last_frame = time.perf_counter()
            self._play_sound()

    def reset(self):
        self.elapsed = 0
        self.running = False
        self.last_tick = None
        self._reset_run_state()
        self._stop_sound()

    def _reset_run_state(self):
        self._stop_completion_sound()
        self.pn = 0
        self._p_refresh_view()
        self.particle_acc = 0.0
        self.splashes = []
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        # ⚠️ 只清**每帧缓存**, 不动 `_mound_profile`/`_geom_generation` —— 那两个是**几何**,
        #    由尺寸/周期变化重建; 重置一局不能把沙堆形状解删掉(删了沙堆就画不出来)。
        self._mound_shape_cache = None       # ((几何代, elapsed), apex) —— 每帧只解一次
        self._mound_curve_cache = None       # ((几何代, elapsed), (cx+dx, y)) 接触曲线
        self._completion_triggered = False
        self._done_at = None                 # 重置后颈管立刻回到"未排空"状态
        self._completion_token += 1          # 作废还没到点的完成提示
        # 旧场景的循环引用在重置时清理,避免留到流动中触发全量回收。
        gc.collect()

    def set_duration(self, d):
        try:
            d = float(d)
        except (TypeError, ValueError):
            return False
        if d <= 0:
            return False
        d = min(d, MAX_DURATION)
        if d == self.duration:
            return False
        self.duration = d
        self._rebuild_height_table()
        self.reset()
        return True

    def set_sand_color(self, base, dark, light):
        self.sand_base = hex_rgb(base)
        self.sand_dark = hex_rgb(dark)
        self.sand_light = hex_rgb(light)
        # 换色 ⇒ 作废滑块留下的低分预览, 否则新配色会顶着旧预览走一帧
        self._preview_material = None
        self._rebuild_color_table()

    def _rebuild_color_table(self):
        self._color_table = [lerp_rgb(self.sand_base, self.sand_light, i / 10.0)
                             for i in range(11)]
        # 「高光组」= 刚出孔口的新生颗粒(trail_time 短)。它原来直接用 sand_light,
        # 而**出口正上方就是颈部沙柱**, 那里是材质色(≈base, tone 中位 -0.04)。
        # 于是出口处出现一道**15 级的亮度阶跃**(实测 215 → 230 @ 屏幕 y=426 = outlet),
        # 读起来像"另一种材料接着", 就是用户 2026-10-04 报的
        # "颈部附近的沙子和上下半球的沙子过渡不太自然"。
        # 材质自身的亮端上限是 tone≈+0.32 ⇒ 高光组收到同量级, 阶跃从 15 级降到 ~6 级。
        # ⚠️ 削弱"新生颗粒反光"是**观感**取舍, 所以留成可调值 + 开关出并排图。
        self._hilite_color = (self.sand_light if FLOW_HILITE_T >= 1.0
                              else lerp_rgb(self.sand_base, self.sand_light,
                                            FLOW_HILITE_T))

    def _make_sound_proxy(self, name):
        """按音效名新建 _SoundProxy(构造失败返回 None,不抛)。"""
        path = next((p for n, p in SOUND_EFFECTS if n == name),
                    SOUND_EFFECTS[0][1])
        try:
            return _SoundProxy(resource_path(path))
        except Exception as e:
            print(f"sound proxy init failed: {e}")
            return None

    def _make_completion_sound(self):
        path = resource_path("sounds/completion.wav")
        if not os.path.isfile(path):
            print("Completion recording missing: sounds/completion.wav")
            return None
        try:
            return _SoundProxy(path, loop=False)
        except Exception as exc:
            print(f"Completion audio init failed: {exc}")
            return None

    def _stop_completion_sound(self):
        if self._completion_sound is not None:
            self._completion_sound.stop()
        if self._completion_spoken is not None:
            self._completion_spoken.stop()

    def _play_completion_sound(self, duration=0.0):
        """播报"X小时Y分Z秒的沙漏计时完成"。词库缺失时回退预录整句。"""
        if not self.completion_enabled:
            return
        if self._voice_bank.ok and self._play_completion_announcement(duration):
            return
        if self._completion_sound is not None:
            self._completion_sound.stop()
            self._completion_sound.play()

    def _play_completion_announcement(self, duration):
        """把词块拼成一段 PCM 写盘 -> 交给 _SoundProxy 一次播完(无接缝)。

        必须走文件:_SoundProxy 的三个后端里有两条按路径播放(winsound 的
        SND_FILENAME、Kivy SoundLoader),只传裸 PCM 会漏掉它们。
        """
        try:
            bank = self._voice_bank
            pcm = _completion_chime(bank.rate) + bank.build_pcm(duration)
            path = os.path.join(os.path.dirname(config_path()),
                                "completion_announcement.wav")
            with wave.open(path, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(bank.rate)
                stream.writeframes(pcm)
        except Exception as exc:
            print(f"completion announcement failed: {exc}")
            return False
        if self._completion_spoken is not None:
            self._completion_spoken.stop()
            self._completion_spoken.close()
            self._completion_spoken = None
        try:
            proxy = _SoundProxy(path, loop=False)
        except Exception as exc:
            print(f"completion announcement audio init failed: {exc}")
            return False
        if proxy.backend == "none":
            return False
        self._completion_spoken = proxy
        proxy.play()
        return True

    def _set_sound(self, name):
        """切换音效(五步序):①先新建 proxy(失败→旧态原样,绝不静音)②停旧
        ③关旧(release)④挂新 ⑤运行中立即续播。同名幂等。返回是否实际切换。"""
        if name == self.sound_name:
            return False
        if name == SILENT_NAME:
            # 静音:停旧即可,不建 proxy(_play_sound 对 None 是空操作)
            if self._sound is not None:
                self._sound.stop()
                self._sound.close()
            self._sound = None
            self.sound_name = name
            return True
        if not any(n == name for n, _ in SOUND_EFFECTS):
            return False
        new_proxy = self._make_sound_proxy(name)
        if new_proxy is None:
            return False
        if self._sound is not None:
            self._sound.stop()          # winsound SND_PURGE 全局清场,必须先停旧
            self._sound.close()
        self._sound = new_proxy
        self.sound_name = name
        if self.running:
            self._play_sound()
        return True

    def _play_sound(self):
        if self._sound is not None:
            self._sound.play()

    def _stop_sound(self):
        if self._sound is not None:
            self._sound.stop()

    def sound_problem_desc(self):
        """**只有音频没走到无缝后端时**才返回一行提示,正常/静音一律返回空串。
        当初这行小字 30 秒定位了 `state=2`(STATE_NO_STATIC_DATA)那个 bug —— 兜底路径
        必须能把失败原因自己说出来,否则只能靠盲改 + 每轮 15 分钟的云端构建。
        audiotrack=Android 硬件循环; winsound=Windows 驱动层循环; 两者都无缝,不提示。"""
        if self._sound is None:
            return ""
        backend = getattr(self._sound, "backend", None) or "?"
        if backend in ("audiotrack", "winsound"):
            return ""
        err = getattr(self._sound, "error", "")
        return f"音频: {backend}" + (f" — {err}" if err else "")

    # ---------- 配置持久化 ----------

    def load_config(self):
        try:
            with open(config_path(), 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self, color_name):
        try:
            with open(config_path(), 'w', encoding='utf-8') as f:
                json.dump({'duration': self.duration, 'color_name': color_name,
                           'sound_name': self.sound_name,
                           # 沙体材质(隐藏菜单的浓度滑块)。存 mode+grain 而不是序号 ——
                           # 以后改名/换量程都不会让旧配置串到别的档。
                           'sand_mode': SAND_MATERIAL,
                           'sand_grain': SAND_MATERIAL_GRAIN,
                           # 隐藏菜单的两个六档(2026-10-05 用户要求做成可调)。
                           # 同样存**标签**: 以后调数值不会让旧配置串到别的档。
                           'grain_level': current_grain_level(),
                           'rough_level': current_rough_level(),
                           'grain_rev': SAND_GRAIN_REV},
                  f, ensure_ascii=False)
        except Exception:
            pass

    # ---------- tick / 物理 ----------

    def tick(self, _dt_kivy):
        if not self._geom_ready:
            return
        now = time.perf_counter()
        dt = max(0.0, min(0.05, now - self.last_frame))   # 物理限幅,防卡顿后飞跳
        self.last_frame = now
        if self.running:
            if self.last_tick is not None:
                self.elapsed += now - self.last_tick   # 计时不限幅,不偏移
            self.last_tick = now
            # ⚠️ 换帧必须在**物理之前** —— 否则同一帧里"粒子撞的轮廓"和"画出来的轮廓"
            #    不是同一个(1.93 那类裂缝的根源)。
            self._sync_mound_frame()
            if self.elapsed >= self.duration:
                self.elapsed = self.duration
                self.running = False
                self._done_at = now          # 颈管沙柱从这个时刻开始排空(见 _neck_sand_side ③)
                self._stop_sound()
                if not self._completion_triggered:
                    self._spawn_dust()
                    self._completion_triggered = True
                    self._play_completion_sound(self.duration)
                    self._schedule_completion_popup(App.get_running_app())
                app = App.get_running_app()
                if app is not None:
                    app.on_run_state_changed()
        if self.running or self._completion_triggered:
            self.update_particles(dt)
        self.redraw()
        app = App.get_running_app()
        if app is not None:
            app.update_time(max(0.0, self.duration - self.elapsed), self.duration)

    def _schedule_completion_popup(self, app):
        """完成提示**延后** COMPLETION_POPUP_DELAY 秒再弹, 让闪光/尘埃先演完;
        周期 < COMPLETION_POPUP_MIN 的**根本不弹**。

        期间可能被 reset / 重开作废, 所以到点时用**令牌**判定, 不只看状态:
        `_reset_run_state` 每次都 +1 ⇒ 令牌对不上就不弹。
        (只看 `running` 不够 —— 重置之后 running 也是 False。)
        """
        if app is None or not app.completion_popup_allowed(self.duration):
            return
        token = self._completion_token

        def fire(_dt):
            if token != self._completion_token:
                return                      # 那一轮已经被重置或重开了
            if not self._completion_triggered or self.running:
                return
            if self.elapsed < self.duration:
                return
            app.on_completed(self.duration)

        Clock.schedule_once(fire, COMPLETION_POPUP_DELAY)

    def _spawn_dust(self):
        mound_top = self.get_mound_top_y()
        cx = self._cx
        w = self._sand_half_w(mound_top, self._lower_y_c)
        now = time.perf_counter()
        for _ in range(DUST_COUNT):
            self.dusts.append({
                "x": cx + random.uniform(-w * 0.7, w * 0.7),
                "y": mound_top + random.uniform(0, 5),
                "vx": random.uniform(-25, 25),
                "vy": random.uniform(20, 60),   # 向上喷(Kivy y 向上为正)
                "end": now + DUST_LIFETIME,
            })

    # ------------------------------------------------------------- 粒子存储(numpy)
    # 并行数组是真值; 渲染层的读端一律按**下标**读 `_pv`(本帧的 list 快照),
    # `particles` 只是给外部工具看的 dict 列表视图, 按需构建、不再每帧同步。
    # 迁移计划与消费方清单见 NUMPY_PLAN.md。
    @staticmethod
    def _newbuf(cap):
        """numpy 缺席时退回 Python list —— 存储层两种后端都能跑, 只有物理分叉。"""
        return _np.zeros(cap, dtype=_np.float64) if _np is not None else [0.0] * cap

    def _p_alloc(self, cap):
        self._p_cap = cap
        for name in _P_FIELDS:
            setattr(self, name, self._newbuf(cap))

    def _p_grow(self, need):
        if need <= self._p_cap:
            return
        cap = self._p_cap or 1024
        while cap < need:
            cap *= 2
        old = self._p_cap
        for name in _P_FIELDS:
            src = getattr(self, name)
            dst = self._newbuf(cap)
            dst[:old] = src[:old]
            setattr(self, name, dst)
        self._p_cap = cap

    def _p_refresh_view(self):
        """数组 -> `_pv`(Python list 快照)。每帧调一次, 并让 dict 缓存失效。

        只在物理/存储真正改动数组之后调用; 读端不碰 numpy 标量。
        """
        pv = self._pv
        n = self.pn
        pv.n = n
        pv.use_np = _np is not None and n >= _NUMPY_MIN
        if _np is None:
            pv.nx = pv.ny = pv.nvy = pv.ntl = None
            # 兜底后端本身就是 Python list, 切片即得原生 float。
            pv.x = self.px[:n]
            pv.y = self.py[:n]
            pv.vy = self.pvy[:n]
            pv.tl = self.ptl[:n]
            pv.sz = self.psz[:n]
            pv.light = self.pli[:n]
            pv.wp = self.pwp[:n]
        else:
            # 零拷贝切片(数组视图, 不分配): 供纹理渲染器向量化打包。
            # 逐颗粒循环仍用下面的 list 快照 —— 两种读法各有各的便宜处。
            pv.nx = self.px[:n]
            pv.ny = self.py[:n]
            pv.nvy = self.pvy[:n]
            pv.ntl = self.ptl[:n]
            pv.x = self.px[:n].tolist()
            pv.y = self.py[:n].tolist()
            pv.vy = self.pvy[:n].tolist()
            # pv.tl 只在纹理渲染器的**非 numpy 分支**被读(flow_texture_experiment.py:156,
            # 在 else 里)。安卓 use_np 恒为真 ⇒ 这份 ~2000 个 float 的 tolist 每帧白建。
            # 给数组视图即可: 该分支走不到, 而真走到的 numpy 分支读的是 ntl。
            pv.tl = self.ptl[:n]
            pv.sz = self.psz[:n].tolist()
            pv.light = self.pli[:n].tolist()
            pv.wp = self.pwp[:n].tolist()
        self._p_dict_cache = None
        return pv

    @property
    def particles(self):
        """数组真相的 dict 列表视图 —— 只给外部工具读(inspect_flow / benchmark 快照)。

        渲染层不再用它, 所以每帧那 ~2500 个 dict 的构建已经删掉。这里按需构建并缓存:
        同一批数组状态下反复读拿到的是同一批对象(部分校验工具依赖 id 稳定),
        物理一推进(`_p_refresh_view`)就失效。
        """
        cached = self._p_dict_cache
        if cached is None:
            cached = self._p_to_dicts()
            self._p_dict_cache = cached
        return cached

    @particles.setter
    def particles(self, value):
        self._p_from_dicts(value)
        self._p_refresh_view()

    def _p_to_dicts(self, new_from=None):
        """数组 -> dict 列表。

        只有外部读者和 **numpy 缺席时的标量兜底路径**用它; 向量化路径每帧不再构建。

        `new_from`: 本帧新生的起始下标。标量兜底路径靠它把 `_step_dt`(帧内偏步长)
        带回去 —— 漏了的话新粒子会拿到整帧 dt, 落得更远、提前触底, 画面就变了。
        """
        pn = self.pn
        px = self.px
        py = self.py
        pvy = self.pvy
        pxo = self.pxo
        pwp = self.pwp
        pwa = self.pwa
        psz = self.psz
        ptl = self.ptl
        pli = self.pli
        pdt = self.pdt
        particles = []
        append = particles.append
        for i in range(pn):
            d = {
                "x": float(px[i]), "y": float(py[i]), "vy": float(pvy[i]),
                "x_offset": float(pxo[i]), "wobble_phase": float(pwp[i]),
                "wobble_amp": float(pwa[i]), "size": int(psz[i]),
                "trail_time": float(ptl[i]), "is_light": bool(pli[i]),
            }
            if new_from is not None and i >= new_from:
                d["_step_dt"] = float(pdt[i])
            append(d)
        return particles

    def _p_from_dicts(self, particles):
        """dict 列表 -> 数组。标量兜底路径与 `particles` 的写入端共用。

        缺字段按渲染层的既有默认值补(wobble 系列 0、trail_time 0.08), 让只写部分字段的
        外部工具仍能把粒子放进来; 兜底路径的 dict 由 `_p_to_dicts` 生成, 字段必然齐。
        """
        n = len(particles)
        self._p_grow(n)
        px = self.px
        py = self.py
        pvy = self.pvy
        pxo = self.pxo
        pwp = self.pwp
        pwa = self.pwa
        psz = self.psz
        ptl = self.ptl
        pli = self.pli
        for k in range(n):
            d = particles[k]
            px[k] = d.get("x", 0.0)
            py[k] = d.get("y", 0.0)
            pvy[k] = d.get("vy", 0.0)
            pxo[k] = d.get("x_offset", 0.0)
            pwp[k] = d.get("wobble_phase", 0.0)
            pwa[k] = d.get("wobble_amp", 0.0)
            psz[k] = 2 if d.get("size", 1) == 2 else 1
            ptl[k] = d.get("trail_time", 0.08)
            pli[k] = 1.0 if d.get("is_light", False) else 0.0
        self.pn = n

    def _replay_hits(self, hit_idx, hit_dt, mound_top, motion_scale, now):
        """按下标升序回放命中事件 —— 随机数调用顺序与原标量循环逐字一致。

        ⚠️ 三个随机数只在 splash 成立时才抽, 顺序:
           rand()<0.25 → rand()<0.50 → uniform(0.14,0.28) → uniform(-0.85,0.85)
           → choice([1,1,2])。
        """
        px = self.px
        pvy = self.pvy
        pdt = self.pdt
        rand = random.random
        rand_uniform = random.uniform
        rand_choice = random.choice
        sin = math.sin
        cos = math.cos
        append_flare = self.flares.append
        append_splash = self.splashes.append
        for k in range(len(hit_idx)):
            i = int(hit_idx[k])
            x = float(px[i])
            vy = float(pvy[i])
            step_dt = float(pdt[i])
            # ⚠️ 与 scalar 路径同源: 出生点用**该颗粒自己的接触高度**(dingbu.md §7.1 第 6 条)。
            #    numpy 路径拿不到 scalar 的 hy, 就地按 x 查一次 —— 两条路径必须同口径。
            hy = self._lower_sand_bot + self._mound_contact_h(x - self._cx)
            if rand() < 0.25:
                append_flare({"x": x, "y": hy, "end": now + 0.08})
            if rand() < 0.50:
                v = -vy
                bounce = min(110 * motion_scale,
                             (v if v > 0 else 0) * rand_uniform(0.14, 0.28))
                angle = rand_uniform(-1.15, 1.15)
                step_left = step_dt - float(hit_dt[k])
                append_splash({
                    "x": x, "y": hy + 0.5,
                    "vx": sin(angle) * bounce,
                    "vy": cos(angle) * bounce,
                    "size": rand_choice([1, 1, 2]),
                    "_step_dt": step_left if step_left > 0 else 0,
                })
    def _spawn_bg_splashes(self, dt):
        """沿沙面**由强到弱**铺开的背景飞溅 —— 补上"斜坡上根本没有生成源"这一块。

        位置抽样: `|u|^FALLOFF` 把样本往中心收(指数越大越集中), 再乘 `SIGMA × R` 定尺度;
        **纵向落在当地沙面上**, 初速比落点那些小(越远的越弱)。
        用 `rand_uniform` 走本工程统一的随机数流(与粒子同源, 便于复现)。
        """
        Ri = self._R_inner
        if Ri <= 0:
            return
        rand_uniform = random.uniform      # 与粒子同一条随机数流(工程惯例, 便于复现)
        self._bg_splash_acc += dt * SPLASH_BG_RATE
        k = int(self._bg_splash_acc)
        if k <= 0:
            return
        if k > 24:                       # 一帧最多补这么多, 防卡顿后一次性炸开
            k = 24
        self._bg_splash_acc -= k
        append = self.splashes.append
        for _ in range(k):
            u = rand_uniform(0.0, 1.0)
            mag = (u ** SPLASH_BG_POW) * Ri * 0.96
            if rand_uniform(0.0, 1.0) < 0.5:
                mag = -mag
            j = mag / Ri if Ri > 0 else 0.0
            if abs(j) > 0.96:            # 不许越出沙堆范围
                continue
            h = self._mound_contact_h(mag)     # 当地沙面(相对下球内底)
            f = abs(j) / 0.96
            append({
                "x": self._cx + mag,
                "y": self._lower_sand_bot + h + rand_uniform(0.0, 2.0),
                # ⚠️ Kivy **y 向上** ⇒ "往上弹"是 **vy > 0**。第一版写成负值,
                #    结果一出生就往下掉进沙里、下一帧被剔除(实测分布一动不动才发现)。
                #    与现有 splash 同源: `vy = cos(angle) * bounce`(bounce > 0)。
                # ⚠️ **高度是这次的关键**: 一跳最高 = v²/(2g)。原来 vy=26~74、g=450
                #    ⇒ 最高只有 **0.75~6px**, 精灵 1~2px —— 在 900px 宽的沙堆上
                #    **读起来是"沙面自带的颗粒", 不是"东西在跳"**(r18-1号 设备实测:
                #    斜坡上的飞溅按**可见像素**只有 7.2% 在半宽以外)。
                #    而且原来还按距离衰减到 38% ⇒ **越靠边跳得越矮, 根本离不了地**。
                #    改: vy 90~180 ⇒ 一跳最高 **9~36px**; 衰减放缓到 30%; 精灵放大。
                "vx": rand_uniform(-1.0, 1.0) * 34.0 * (1.0 - 0.40 * f),
                "vy": rand_uniform(90.0, 180.0) * (1.0 - 0.30 * f),
                "size": 2 if rand_uniform(0.0, 1.0) < 0.7 else 1,
            })

    def update_particles(self, dt):
        if not self._geom_ready:
            return
        cx = self._cx
        mound_top = self.get_mound_top_y()
        remaining = self.get_remaining()
        now = time.perf_counter()
        neck_w = self.neck_w
        ow = self._ow
        gen_y = 2 * self._neck_y - self._taper['y_bot']
        motion_scale = self._particle_motion_scale
        self._spawn_from = self.pn          # 没走 spawn 分支时也不能留旧值
        # ⚠️ 本帧步长必须在这里铺满, **不能**在帧尾存"上一帧的 dt"。
        #    闸门用 step = min(1/120, target-elapsed), 到采样点附近会产生偏步长;
        #    存上一帧的 dt 会让下一帧的粒子落得更远、提前触底(实测每周期末 2% 分叉)。
        #    语义对齐原来的 `p.pop("_step_dt", dt)`: 老粒子用**本帧** dt, 帧内新生的
        #    粒子随后在 spawn 里覆盖成自己的偏步长。
        if _np is None:
            self.pdt[:self.pn] = [dt] * self.pn   # list 切片不吃标量广播
        else:
            self.pdt[:self.pn] = dt

        if self.running and remaining > 0:
            rate = 600 * self.speed_factor
            if remaining < 0.08:
                rate *= max(0.1, (remaining / 0.08) ** 0.5)
            # 沙柱先接通出口; 在帧内均匀发射,避免每一帧生出一整排同龄沙粒。
            emit_dt = min(dt, max(0.0, self.elapsed - self._neck_fill_time))
            self.particle_acc += emit_dt * rate
            x_clip = max(1.0, neck_w - ow)
            self._spawn_from = self.pn
            # 一次把本帧要生的量预留够, 不在循环里反复扩容。
            self._p_grow(self.pn + int(self.particle_acc) + 2)
            while self.particle_acc >= 1:
                self.particle_acc -= 1
                x_off = random.uniform(-x_clip, x_clip)
                vy0 = -(random.uniform(90, 120) if random.random() < 0.05
                        else random.uniform(35, 60)) * motion_scale
                # ⚠️ 抽取顺序必须与原来那个 dict 字面量的求值顺序逐字一致; 尤其
                #    `size` 的条件表达式在 x_clip < 3.0 时短路, **不抽**那个随机数。
                phase = random.uniform(0, math.tau)
                amp = random.uniform(0.4, 1.0)
                is_light = random.random() < 0.10
                size = (2 if random.random() < 0.85 else 1) if x_clip >= 3.0 else 1
                trail_time = random.uniform(0.018, 0.032)
                i = self.pn
                self.px[i] = cx + x_off
                self.pxo[i] = x_off
                self.py[i] = gen_y
                self.pvy[i] = vy0
                self.pwp[i] = phase
                self.pwa[i] = amp
                self.pli[i] = 1.0 if is_light else 0.0
                self.psz[i] = size
                self.ptl[i] = trail_time
                self.pdt[i] = self.particle_acc / rate
                self.pn = i + 1

        g = -450.0 * motion_scale * motion_scale
        g_abs = abs(g)
        source_speed = 60.0 * motion_scale
        source_speed_squared = source_speed ** 2
        lower_cut = self._lower_ball_cut
        lower_top = self._lower_sand_top
        lower_center = self._lower_y_c
        sand_half_w = self._sand_half_w
        tube_lim = max(1.0, neck_w - ow)
        new_list = []
        append_particle = new_list.append
        append_flare = self.flares.append
        append_splash = self.splashes.append
        lower_bot = self._lower_sand_bot
        Ri2 = self._R_inner * self._R_inner      # _sand_half_w 内联用(值与原来一致)
        peak_offset = self.mound_peak_offset
        rand = random.random
        rand_uniform = random.uniform
        rand_choice = random.choice
        sin = math.sin
        sqrt = math.sqrt
        mound_top_plus_1 = mound_top + 1
        _cx_arr, _cy_arr, _c_x0, _c_scale, _c_n1 = self._mound_contact_curve()
        _use_curve = len(_cx_arr) > 1
        if _flow_numpy is not None and self.pn >= _NUMPY_MIN:
            # numpy 路线: 纯算术向量化(逐位等价由 tools/test_physics_equiv.py 验收),
            # 随机数仍留在 Python, 命中事件按下标升序回放。
            pn = self.pn
            if pn:
                consts = {
                    "g": g, "g_abs": g_abs, "mound_top": mound_top,
                    "gen_y": gen_y, "lower_cut": lower_cut,
                    "lower_top": lower_top, "lower_center": lower_center,
                    "tube_lim": tube_lim, "Ri2": Ri2, "lower_bot": lower_bot,
                    "source_speed": source_speed,
                    "source_speed_squared": source_speed_squared,
                    "cx": cx, "peak_offset": peak_offset,
                    "curve": (_cx_arr, _cy_arr, _c_x0, _c_scale, _c_n1),
                }
                hit_idx, hit_dt, peak_offset = _flow_numpy.step(
                    self.px, self.py, self.pvy, self.pxo, self.pwp, self.pwa,
                    self.psz, self.pdt, pn, consts)
                nhit = len(hit_idx)
                if nhit:
                    self._replay_hits(hit_idx, hit_dt, mound_top,
                                      motion_scale, now)
                    keep = _np.ones(pn, dtype=bool)
                    keep[hit_idx] = False
                    newpn = pn - nhit
                    for _name in _P_FIELDS:
                        _arr = getattr(self, _name)
                        _arr[:newpn] = _arr[:pn][keep]
                    self.pn = newpn
        else:
            # numpy 缺席时的兜底: 数组 -> dict -> 原标量循环 -> 写回数组。
            particles = self._p_to_dicts(self._spawn_from)
            for p in particles:
                # 局部变量缓存:原来每颗粒几十次 dict 查找,这里改成读一次写回一次。
                # 所有算式与随机数调用顺序保持逐字不变, 保证粒子流与画面完全一致。
                step_dt = p.pop("_step_dt", dt)
                y = p["y"]
                vy = p["vy"]
                old_y, old_vy = y, vy
                x_offset = p["x_offset"]
                p_x_prev = p["x"]
                wobble_phase = p["wobble_phase"]
                wobble_amp = p["wobble_amp"]
                size = p["size"]
                y += vy * step_dt + 0.5 * g * step_dt * step_dt
                vy += g * step_dt
                # 命中判定: 锥顶已是全堆最高点 ⇒ `y <= mound_top` 是**必要非充分**的免费预筛,
                # 只有落到堆附近的少量颗粒才去查 H(x)(专家 dingbu.md §7)。
                # ⚠️ 用**上一帧的 x**(p["x"]): 粒子一帧横向只动 1~2px ⇒ H 的误差 ≤1.2px(亚像素);
                #    真正的 x 依赖 shrink, 而 shrink 又依赖本判定 ⇒ 不能用本帧的 x(会成环)。
                hit = y <= mound_top
                hy = mound_top
                if hit and _use_curve:
                    z = (p_x_prev - _c_x0) * _c_scale
                    if z <= 0.0:
                        hy = _cy_arr[0]
                    elif z >= _c_n1:
                        hy = _cy_arr[-1]
                    else:
                        _i = int(z)
                        hy = _cy_arr[_i] + (_cy_arr[_i + 1] - _cy_arr[_i]) * (z - _i)
                    hit = y <= hy
                hit_dt = 0.0
                if hit:
                    d = old_y - hy
                    distance = d if d > 0 else 0
                    v = -old_vy
                    speed = v if v > 0 else 0
                    denom = speed + sqrt(speed * speed + 2 * g_abs * distance)
                    hit_dt = 2 * distance / (denom if denom > 1e-6 else 1e-6)
                    hit_dt = hit_dt if hit_dt < step_dt else step_dt
                    y = hy
                    vy = old_vy + g * hit_dt
                fd = gen_y - y
                fallen_dist = fd if fd > 0.0 else 0.0
                # 管内: 管壁约束,填满内径 shrink=1.0
                # 出管: 40px 平滑过渡区渐变到流量守恒目标值,避免突兀收缩
                if y > lower_cut:
                    shrink = 1.0
                else:
                    below_tube = lower_cut - y
                    v_at_y = (source_speed_squared + 2 * g_abs * below_tube) ** 0.5
                    target = (source_speed / v_at_y) ** 0.5
                    if target <= 0.70:
                        target = 0.70
                    # 平滑过渡区长度(px)
                    if below_tube < 40.0:
                        shrink = 1.0 + (target - 1.0) * (below_tube / 40.0)
                    else:
                        shrink = target
                    dist_to_floor = y - mound_top
                    if 0 < dist_to_floor < 30:
                        shrink *= 1 + (1 - dist_to_floor / 30) * 0.4
                x = cx + x_offset * shrink + sin(fallen_dist * 0.07 + wobble_phase) \
                    * wobble_amp * (1 - shrink * 0.4)

                # 横向 clamp: 管内壁 / 进下球随球内壁过渡
                if y >= lower_top:
                    lim = tube_lim
                else:
                    dy = y - lower_center
                    r = Ri2 - dy ** 2
                    raw_ball = sqrt(r) if r > 0.0 else 0.0
                    below = lower_top - y
                    t = below / 30.0
                    if t > 1.0:
                        t = 1.0
                    lim = tube_lim + (raw_ball - tube_lim) * t
                half_stroke = size if size > 1 else 0.5
                lim = lim - half_stroke
                if lim <= 0.0:
                    lim = 0.0
                off = x - cx
                if off > lim:
                    off = lim
                elif off < -lim:
                    off = -lim
                x = cx + off

                if hit:
                    if mound_top > lower_bot + 1:
                        peak_offset = peak_offset * 0.97 + (x - cx) * 0.03
                    # ⚠️ **出生点用该颗粒自己的接触高度 hy**(dingbu.md §7.1 第 6 条:
                    #    "闪光、弹跳、滑落起点全部使用该颗粒的接触 y, 不再统一放到一条水平线上")。
                    #    原来放在标量 mound_top 上 —— 审计实测出生点比该颗粒**当地沙面**
                    #    高 p50=7.5px / p90=12.6px / max=15.4px(92% 的命中 >1px)。
                    if rand() < 0.25:
                        append_flare({"x": x, "y": hy, "end": now + 0.08})
                    if rand() < 0.50:
                        v = -vy
                        bounce = min(110 * motion_scale,
                                     (v if v > 0 else 0) * rand_uniform(0.14, 0.28))
                        angle = rand_uniform(-1.15, 1.15)
                        step_left = step_dt - hit_dt
                        append_splash({
                            "x": x, "y": hy + 0.5,
                            "vx": sin(angle) * bounce,
                            "vy": math.cos(angle) * bounce,
                            "size": rand_choice([1, 1, 2]),
                            "_step_dt": step_left if step_left > 0 else 0,
                        })
                    continue
                p["y"] = y
                p["vy"] = vy
                p["x"] = x
                append_particle(p)
            self._p_from_dicts(new_list)
        self.mound_peak_offset = peak_offset
        # 本帧物理到此结束, 把数组摊成渲染层读的 list 快照(顺带让 dict 缓存失效)。
        self._p_refresh_view()

        # 平台之外只有 splash/尘埃会落 ⇒ 它们按**接触高度 H(x)** 判定(与绘制同一份定义,
        # 专家 §2.4); 主流落在平台上, 继续用标量 mound_top, 热循环一个字不改(路线 A)。
        _mound_bot = self._lower_sand_bot
        _profile = self._mound_profile
        _apex = self._mound_apex()
        new_splashes = []
        append_splash_keep = new_splashes.append
        for s in self.splashes:
            step_dt = s.pop("_step_dt", dt)
            y = s["y"] + s["vy"] * step_dt + 0.5 * g * step_dt * step_dt
            vy = s["vy"] + g * step_dt
            x = s["x"] + s["vx"] * step_dt
            s["y"] = y
            s["vy"] = vy
            s["x"] = x
            dy = y - lower_center
            r = Ri2 - dy ** 2
            half = sqrt(r) if r > 0.0 else 0.0
            sx = x - cx
            if (sx if sx > 0 else -sx) > half - 1:
                continue
            # ⚠️ **只在真的要判定时才求接触高度**(v<0 = 正在下落): 上升期多算一次
            #    `contact()` 实测让 15s 档 physics 多 0.35ms(A/B 3 轮可分辨)。
            #    定义仍然是 `_MoundProfile.contact` 那一份, 没有第二套公式。
            if vy < 0 and _profile is not None and _apex > 0.0                     and y <= _mound_bot + _profile.contact(sx, _apex):
                continue
            if y < lower_bot or y > lower_top - 5:
                continue
            append_splash_keep(s)
        self.splashes = new_splashes
        if self.running:
            self._spawn_bg_splashes(dt)

        self.flares = [f for f in self.flares if f["end"] > now]

        new_dusts = []
        for d in self.dusts:
            d["y"] += d["vy"] * dt - 225 * dt * dt
            d["vy"] -= 450 * dt
            d["x"] += d["vx"] * dt
            _dd = d["x"] - cx
            if now > d["end"]:
                continue
            if _profile is not None and _apex > 0.0                     and d["y"] < _mound_bot + _profile.contact(_dd, _apex) - 1:
                continue
            new_dusts.append(d)
        self.dusts = new_dusts

    # ---------- 点击沙漏球 = 开始/暂停 ----------

    def on_touch_down(self, touch):
        if self._geom_ready and self.collide_point(*touch.pos):
            dx = touch.x - self._cx
            if (dx * dx + (touch.y - self._upper_y_c) ** 2 <= self._R ** 2 or
                    dx * dx + (touch.y - self._lower_y_c) ** 2 <= self._R ** 2):
                app = App.get_running_app()
                if app is not None:
                    app.on_toggle()
                return True
        return super().on_touch_down(touch)

    def _build_glass_shell(self):
        """静态玻璃壳缓存到 canvas.before,仅几何变化时重建"""
        self.canvas.before.clear()
        if not self._geom_ready:
            return
        cx, R, Ri, ow = self._cx, self._R, self._R_inner, self._ow
        uyc, lyc = self._upper_y_c, self._lower_y_c
        nw = self.neck_w
        glass_fill = hex_rgb(GLASS_FILL)
        glass_out = hex_rgb(GLASS_OUTLINE)
        with self.canvas.before:
            for yc in (uyc, lyc):
                Color(*glass_out)
                Ellipse(pos=(cx - R, yc - R), size=(2 * R, 2 * R))
                Color(*glass_fill)
                Ellipse(pos=(cx - R + ow, yc - R + ow),
                        size=(2 * (R - ow), 2 * (R - ow)))
                # 球腔的内缘压暗(对抗评审 A3): 一张径向渐变贴图, 中心全透明、最外
                # ~18% 起坡到 alpha 0.42 的冷深灰。Quad 半宽取 **Ri**(内腔半径) ⇒
                # 贴图的 q=1 正好落在内腔边缘。
                # ⚠️ 放在 canvas.before ⇒ **沙体(画在 canvas 里)会盖住它** ⇒ 这段压暗
                #    天然只出现在空的地方, 不需要 stencil、不需要判断沙面位置。
            # 颈部: 挖掉球极冠肩台 → 直筒 → 上下曲线过渡(几何见 _rebuild_height_table)
            tp = self._taper
            t_out, t_in = tp['t_out'], tp['t_in']
            mir = 2 * self._neck_y
            yb_u = tp['y_bot']
            yb_l = mir - yb_u
            pad = dp(3)

            def _pts(key, flip):
                return [(x, (mir - y) if flip else y) for x, y in tp[key]]

            def _band(pts, color):
                """沿曲线逐段画横跨左右的四边形(Kivy 无多边形图元)"""
                Color(*color)
                for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                    Quad(points=[cx - x0, y0, cx + x0, y0, cx + x1, y1, cx - x1, y1])

            # ① 擦掉曲线以外的球极冠(用背景色覆盖)
            bg = hex_rgb(BG_COLOR)
            for flip in (False, True):
                op = _pts('out_pts', flip)
                Color(*bg)
                for sgn in (1, -1):
                    for (x0, y0), (x1, y1) in zip(op, op[1:]):
                        Quad(points=[cx + sgn * x0, y0, cx + sgn * (R + pad), y0,
                                     cx + sgn * (R + pad), y1, cx + sgn * x1, y1])
            # ② 直筒段
            Color(*glass_out)
            Rectangle(pos=(cx - t_out, yb_l), size=(2 * t_out, yb_u - yb_l))
            Color(*glass_fill)
            Rectangle(pos=(cx - t_in, yb_l), size=(2 * t_in, yb_u - yb_l))
            # ③ 曲线过渡: 外轮廓(壁) + 内轮廓(腔)
            for flip in (False, True):
                _band(_pts('out_pts', flip), glass_out)
                _band(_pts('in_pts', flip), glass_fill)

    # ---------- 渲染 ----------

    def _current_material(self):
        """当前该用的沙体材质: **预览槽优先**(拖动中的低分辨率版), 否则按配色走缓存。"""
        if self._preview_material is not None:
            return self._preview_material
        return sand_material(self.sand_base, self.sand_dark, self.sand_light)

    def rebind_sand_material(self, material):
        """把一份材质绑到**已有的**沙体 rect / 颈部 quad 上。

        ⚠️ **绝不能走 `_build_dynamic_canvas()`** —— 那个会 `canvas.clear()` 并重建
        2500 条粒子线的图元池; 拖动滑块每帧调一次必卡。这里只改 texture 属性
        (这正是 `set_sand_style` 当年重的原因, 拖动场景必须避开)。
        """
        self._sand_material = material
        white = material is not None
        tex = None if material is None else material.texture
        for color, rect in self._sand_chords:
            rect.texture = tex
            color.rgb = (1, 1, 1) if white else self.sand_base
        for quad in self._neck_quads:
            quad.texture = tex
        self._neck_solid_rect.texture = self._neck_fade_rect.texture = tex
        if white:
            # 材质烘的就是 albedo ⇒ 前面必须保持白色, 再染一层沙色会明显发暗
            self._neck_color.rgb = (1, 1, 1)
            self._neck_solid_color.rgb = self._neck_fade_color.rgb = (1, 1, 1)
        else:
            self._neck_color.rgb = self.sand_base
            self._neck_solid_color.rgb = self._neck_fade_color.rgb = self.sand_base
        # 绑定已经是当前配色了 ⇒ 别让 redraw 的换色分支再拿缓存材质覆盖掉预览
        self._render_colors = (self.sand_base, self.sand_dark, self.sand_light)
        self.redraw()

    def set_sand_grain(self, grain, preview=False):
        """沙子浓度(隐藏菜单滑块)。

        `preview=True` 烘 128² 低分材质放在预览槽(`SAND_PREVIEW_SIZE`), 供拖动中调用;
        `preview=False` 烘正式分辨率并写回全局, 供松手/确认时调用。
        返回是否真的换上了(材质生成失败时返回 False, 调用方据此保留旧值)。
        """
        global SAND_MATERIAL, SAND_MATERIAL_GRAIN
        try:
            grain = min(SAND_GRAIN_MAX, max(0.0, float(grain)))
        except (TypeError, ValueError):
            return False
        if preview:
            material = preview_sand_material(self.sand_base, self.sand_dark,
                                             self.sand_light, grain)
            if material is None:
                return False
            self._preview_material = material
            self.rebind_sand_material(material)
            return True
        SAND_MATERIAL = "grain"
        SAND_MATERIAL_GRAIN = grain
        self._preview_material = None
        material = sand_material(self.sand_base, self.sand_dark, self.sand_light,
                                 grain=grain)
        if material is None:
            return False
        self.rebind_sand_material(material)
        return True

    def set_grain_level(self, label):
        """隐藏菜单「沙体颗粒」六档 (A-F)。返回是否真的换上了。

        ⚠️ 与 `set_sand_grain`(浓度滑块) 是**两个旋钮**: 这里改的是明暗落差 + 颗粒粗细,
           那两个值进**缓存键** ⇒ 第一次换到某档要重烘一张 512²(约 17ms + 上传),
           换回来的第二次命中缓存、秒切。**只在按键那一下, 不在每帧。**
        """
        global SAND_MATERIAL_GRAD, SAND_MATERIAL_COARSE
        grad, coarse = _grain_level(label)
        if (abs(grad - SAND_MATERIAL_GRAD) < 1e-9
                and coarse == SAND_MATERIAL_COARSE):
            return False
        SAND_MATERIAL_GRAD, SAND_MATERIAL_COARSE = grad, coarse
        self._preview_material = None
        material = sand_material(self.sand_base, self.sand_dark, self.sand_light)
        if material is None:
            return False
        self.rebind_sand_material(material)     # ⚠️ 轻量绑定, 不重建画布
        return True

    def set_rough_level(self, label):
        """隐藏菜单「沙面起伏」六档 (A-F)。返回是否真的换上了。

        只换 `self._upper_rough` 那 65 个浮点(纯赋值, 不重建几何/画布) ——
        **`_upper_area`(面积求解) 与 `_draw_upper_shape`(绘制) 读的是同一个数组**
        ⇒ 体积守恒自动跟着走, 不需要任何额外同步(两边各算各的就会破守恒, 别改)。
        """
        global UPPER_ROUGH_FRAC
        frac = _rough_level(label)
        if abs(frac - UPPER_ROUGH_FRAC) < 1e-12:
            return False
        UPPER_ROUGH_FRAC = frac
        # ⚠️ **下球轮廓也要跟着重建** —— 用户 2026-10-05「包含下面沙漏中斜面中的沙子的起伏,
        #    你也没有做」: 原先下球用的是另一个常量 `MOUND_ROUGH_FRAC`, 这个设置**压根没接到它**。
        #    `_mound_shape` / `_mound_profile` 的粗糙度是**烘进面积表**的 ⇒ 换档必须重建,
        #    但**只在按键这一下**(不是每帧) —— 每帧重建面积表太贵。
        self._rebuild_height_table()
        self._mound_shape_cache = None
        self.redraw()
        return True

    def set_sand_style(self, mode, grain):
        """（兼容入口）直接设定材质模式+浓度。

        滑块走 `set_sand_grain`; 这个留给 `flat ↔ grain` 切换 —— 那个要改 Color 的
        染色方式, 必须重建画布指令, 不能用 `rebind_sand_material` 糊过去。
        """
        global SAND_MATERIAL, SAND_MATERIAL_GRAIN
        SAND_MATERIAL = mode
        SAND_MATERIAL_GRAIN = float(grain)
        self._preview_material = None
        self._render_colors = None          # 逼 redraw 走一次"换材质"分支
        self._build_dynamic_canvas()
        self.redraw()
        return True

    def _mound_knots(self, apex=None):
        """绘制折线的横向节点(相对中轴的偏移) —— **必须含所有的真转折点**。

        转折点有三类:
          ① 平台的肩 ±b(专家 §2.4: 转角若落在两节点之间, 画出来是跨过转角的斜线,
             而碰撞算的仍是平台);
          ② **锥面与球内底的交点**(自由表面的真实端点, 逐帧求根, 见 `_mound_wall_cross`);
          ③ 最外的 ±R。
        ⚠️ 节点按**角度**均匀取, 不是按 x 均匀(实测踩过): 球壁附近 B(x)=R-√(R²-x²) 的
        切线近乎竖直, 按 x 均匀时最外一段的弦会比真圆**高出约 10px** ⇒ carve 抠不到那一块,
        左右球壁各留一条金边。按 θ 均匀 ⇒ Δx = R·cosθ·Δθ 在壁附近自动变细(最外一格 <1px)。
        """
        Ri = self._R_inner
        xs = {0.0, Ri, -Ri}
        # 轮廓的**控制点本身**就是折线的折点(粗糙起伏在这些点上), 必须全进节点集,
        # 否则 θ 均匀网格(中轴附近 ~29px 一格)会把微粗糙整段抹平。
        if self._mound_shape:
            half = (len(self._mound_shape) - 1) // 2
            step = Ri / half
            for i in range(len(self._mound_shape)):
                v = (i - half) * step
                if -Ri < v < Ri:
                    xs.add(v)
        n = MOUND_DRAW_EXTRA
        for i in range(n + 1):
            xs.add(Ri * math.sin(-math.pi / 2.0 + math.pi * i / n))
        if apex is not None:
            for dx in self._mound_wall_cross(apex):
                if -Ri < dx < Ri:
                    xs.add(dx)
        return sorted(xs)

    def _mound_wall_cross(self, apex):
        """锥面 `P(x)` 与球内底 `B(x)` 的交点(左右各一个, 没有就跳过)。

        这就是**自由表面的端点**: `contact = clamp(P, B, U)` 在这里有一个折角,
        不把它放进绘制节点, carve 的弦就会跨过折角、在壁边留下 ~3px 的沙条
        (实测 t=1.96 时 dx≈±102 处 +3.3px)。
        `P - B` 在 [b, R] 上严格单调递减(raw 斜率 -m, B 单调增) ⇒ 至多一根, 二分即可。
        """
        prof = self._mound_profile
        if prof is None or apex <= 0.0:
            return []
        Ri = self._R_inner
        # 二分下界取轮廓的**峰值点**(中心轴): raw 从中心向壁单调下降 ⇒ 单根
        lo0 = 1e-6          # 轮廓的峰值在中心轴上(raw 从中心向壁严格下降 ⇒ 单根)
        out = []
        for sign in (1.0, -1.0):
            lo, hi = (lo0, Ri) if sign > 0 else (-Ri, -lo0)
            gap_lo = prof.raw(lo, apex) - prof.bounds(lo)[0]
            gap_hi = prof.raw(hi, apex) - prof.bounds(hi)[0]
            if gap_lo <= 0.0:
                continue                      # 连平台下沿都在球底以下 ⇒ 这一侧没有沙
            if gap_hi > 0.0:
                continue                      # 一直到壁都还有沙(近满球) ⇒ 没有交点
            for _ in range(18):
                mid = 0.5 * (lo + hi)
                if prof.raw(mid, apex) - prof.bounds(mid)[0] > 0.0:
                    lo = mid
                else:
                    hi = mid
            out.append(0.5 * (lo + hi))
        return out

    def _upper_funnel_params(self, p, height):
        """上球漏斗的 (下陷深度 d, 半宽 b) —— 专家 dingbu.md §4.2。

        d = 0.03D · smoothstep(0,0.25,p) · [1 - smoothstep(0.85,1,p)]
        b = D · [0.10 + 0.04 · smoothstep(0.10,0.80,p)]
        两个限制: d ≤ 0.25×当前沙层厚, b ≤ 0.8×该高度的半弦宽。
        ⚠️ 两端 sstep 包络 ⇒ **满球和空球都自动收敛成平面**(那是正确的: 满球没有自由表面)。
        """
        D = 2.0 * self._R_inner
        # ⚠️ 半宽基准是 **C = 参考自由面可见全宽 = 2 × 半弦宽**, 不是直径。
        #    dingbu2.md §3 明确: "b 是浅坑半宽; 完整作用宽度是 2b, 不是 b"。
        #    旧式 `b = D × (0.10 + 0.04·sstep)` 量出来只有 0.112~0.149 C ⇒ 太窄。
        half_chord = math.sqrt(max(0.0, self._R_inner ** 2
                                   - (self._R_inner - height) ** 2))
        s1 = _smoothstep(0.0, 0.25, p)
        s2 = 1.0 - _smoothstep(0.85, 1.0, p)
        # d 仍乘 s1/s2 包络(评审: "d 继续乘原有的随沙量出现、收敛的包络"), 满球/空球自动收敛成平面。
        # b 不需要额外的 p 增长项 —— C 本身随沙量变化(半弦宽), 已含"慢慢出来"。
        d = UPPER_FUNNEL_DEPTH * D * s1 * s2
        b = UPPER_FUNNEL_WIDTH * (2.0 * half_chord)
        return min(d, UPPER_FUNNEL_MAXH * height), min(b, UPPER_FUNNEL_MAXB * half_chord)

    def _upper_rough_now(self):
        """**本帧**的上球粗糙数组(随时间平滑演化)。每帧只算一次, 全部调用方共用一份。

        ⚠️ 必须共用: `_upper_area`(面积求解) 与 `_draw_upper_shape`(绘制) 若各算各的,
           算出来的沙量和画出来的就不是一回事 —— 守恒会破(1.90 的老坑, 别再踩)。
        ⚠️ **绝不能逐帧重抽随机数** —— 那是 1.60/1.61 被用户判死
           ("颗粒原地闪现 + 整条轮廓颤动")的路。这里只是**在两张预烘帧之间线性插值**。
        """
        frames = getattr(self, "_upper_rough_frames", None)
        if not frames:
            return getattr(self, "_upper_rough", None)
        t = self.elapsed
        if getattr(self, "_upper_rough_cache_t", None) == t:
            return self._upper_rough_cache
        nf = len(frames)
        period = UPPER_ROUGH_PERIOD if UPPER_ROUGH_PERIOD > 0 else 1.0
        ph = (t / period) % 1.0 * nf
        k = int(ph) % nf
        f = ph - int(ph)
        a, b = frames[k], frames[(k + 1) % nf]
        out = [a[i] + (b[i] - a[i]) * f for i in range(len(a))]
        self._upper_rough_cache = out
        self._upper_rough_cache_t = t
        return out

    def _upper_rough_at(self, index, height):
        """上球第 index 个节点的粗糙偏移(已乘"随沙量出现/收敛"的包络)。

        包络按评审 §4.3: `e(q) = smoothstep(0,0.015,q) × [1-smoothstep(0.97,1,q)]`,
        q 用"平顶等效高度占球高之比"代理(与目标面积占比单调同向)。
        ⇒ 满球(无自由面)和空球两端自动收敛成平面, 少量沙不会先长出几根尖刺。
        """
        arr = self._upper_rough_now()
        if not arr or index >= len(arr):
            return 0.0
        Ri = self._R_inner
        q = 0.0 if Ri <= 0 else min(1.0, max(0.0, height / (2.0 * Ri)))
        env = _smoothstep(0.0, 0.015, q) * (1.0 - _smoothstep(0.97, 1.0, q))
        return arr[index] * env if env > 0.0 else 0.0

    def _upper_area(self, level, d, b):
        """上球沙面在给定 level 下的**面积**(65 点采样)与该 level 处未被夹住的权重和。

        二维视觉代理口径, 与下球 `_MoundArea` 同源(专家 §6)。
        dArea/dLevel = 未被球底/球顶夹住的那些列的权重和 ⇒ 直接给牛顿法当导数用。
        """
        Ri = self._R_inner
        n = MOUND_SHAPE_NODES - 1
        w = 2.0 * Ri / n
        area = deriv = 0.0
        for i in range(n + 1):
            dx = -Ri + w * i
            floor = Ri - math.sqrt(max(0.0, Ri * Ri - dx * dx))
            roof = 2.0 * Ri - floor
            y = level - self._upper_surface_drop(dx, d, b) + self._upper_rough_at(i, level)
            if y <= floor:
                continue
            if y >= roof:
                area += (roof - floor) * w
            else:
                area += (y - floor) * w
                deriv += w
        return area, deriv

    def _upper_solve_level(self, target, d, b, lo, hi):
        """求 level 使**漏斗版面积 == 平顶等效面积**(体积守恒) —— 专家 §6。

        ⚠️ 这一条是用户 2026-10-05 点名要求的:「总的沙子的体积要保持」「不能够说最后漏完之后,
           它说漏完了, 但实际上还有沙子」。没有它, 漏斗扣掉的那块沙会**凭空消失** ——
           不是守恒, 只是"误差小到看不出来"。
        牛顿 + 二分兜底(迭代次数有上限, 但没收敛时靠二分把区间夹死, 不返回没验证过的值)。
        """
        if d <= 0.0:                              # 无漏斗 ⇒ 调用方直接用平顶高度
            return None
        a = min(hi, max(lo, 0.5 * (lo + hi)))
        for _ in range(4):
            area, deriv = self._upper_area(a, d, b)
            if abs(area - target) <= max(1e-6, target * 1e-6):
                return a
            if area < target:
                lo = a
            else:
                hi = a
            nxt = a + (target - area) / deriv if deriv > 0.0 else None
            a = nxt if (nxt is not None and lo < nxt < hi) else 0.5 * (lo + hi)
        for _ in range(16):
            a = 0.5 * (lo + hi)
            area, _d = self._upper_area(a, d, b)
            if abs(area - target) <= max(1e-6, target * 1e-6):
                return a
            if area < target:
                lo = a
            else:
                hi = a
        return 0.5 * (lo + hi)

    def _upper_level_for(self, upper_height):
        """本帧上球沙面的 **level**(绝对, 离球内底) —— 含漏斗的体积补偿。

        无漏斗时直接返回平顶高度(`_upper_sand_height_px` 的结果); 有漏斗时解一次面积方程,
        把沙面抬到"漏斗扣掉多少就补回多少"。⇒ 上球与下球**共用同一个沙量真值**。
        """
        p = 0.0 if self.duration <= 0 else min(1.0, self.elapsed / self.duration)
        d, b = self._upper_funnel_params(p, upper_height)
        if d <= 0.0 or upper_height <= 0.0:
            return upper_height
        target, _ = self._upper_area(upper_height, 0.0, 0.0)     # 平顶等效面积
        return self._upper_solve_level(target, d, b, 0.0,
                                       upper_height + d + 1.0)

    def _upper_surface_drop(self, dx, d, b):
        """上球沙面在 dx 处的**下陷量**(≥0, px): `d · [max(0,1-(dx/b)²)]²`。

        ⚠️ 恒 ≥ 0 ⇒ 沙面只会**低于或等于**平面高度 ⇒ 现有"矩形 + 按高度截 UV"机制不用动,
           只在它上面加一遍 carve 抠掉多出来的那块(与下球 `_draw_mound_shape` 同一套写法)。
        """
        if b <= 1e-6 or d <= 0.0:
            return 0.0
        u = 1.0 - (dx / b) ** 2
        # ⚠️ 指数从 2 降到 1(四次方 → 抛物线), 2026-10-05 由两位独立审计量出:
        #    四次方掉得太快 ⇒ 按"下陷>1px 才算看得见"的口径, **可见坑半宽只有 0.82~0.87×半弦宽**,
        #    p=0.95 时掉到 0.69 —— 用户要的是 90~100%。抛物线下可见半宽 ≈ sqrt(1-1/d)·b ≈ 0.97b。
        #    |x|=b 处值 0 且斜率非 0(不再 C1), 但 b 已到或超过可见半弦, 那一点在沙体外或被球壁裁掉。
        return d * u if u > 0.0 else 0.0

    def _draw_surface_markers(self, upper_height, h_mound):
        """§5 表层滑动标记 —— 让**静态**轮廓读起来像在流沙(专家 dingbu.md §5)。

        纯视觉: 不参与计时与沙量统计, **不减少原有下落颗粒**。
        · 上球 8 颗(左右各 4)**向中心滑**(朝颈口汇入); 下球 12 颗(左右各 6)**沿坡向外滑**
        · 每颗用**固定初相位**求 phase; 时间基准是 `self.elapsed` ⇒ **暂停时冻结、重置归零**
        · `y` 每帧查**真实表面**(上球 `_upper_level_for` + 漏斗下陷; 下球 `profile.contact`)
        · 末端淡出、起点淡入; 只有真自由表面才出现(无沙/被球顶截满/路径进壁 → 隐藏)
        ⚠️ 必须**离散细小**, 不能画成一整条随相位移动的亮线(专家原话)。
        """
        pool = self._surface_marker_pool
        n_used = 0
        # ⚠️ **只在几何缺失时清池, 不要加 `not self.running`** —— 相位取自 `self.elapsed`,
        #    暂停时它本来就不动 ⇒ 标记**天然冻结**; 多这道闸门的效果是"隐藏"而非"冻结",
        #    与 docstring 自相矛盾, 且把用户点名要的"流沙感"在暂停(最高频交互)时整个关掉。
        #    (r7-3号 实测 5/5 次暂停复现; 本地复现: 标记 20→0, 恢复后位置逐点相同 ⇒ 闸门纯多余)
        #    空/满态靠几何自然隐藏: 满球 half_chord=0、漏完 free_surface 全假 —— 不需要它兜。
        if self._mound_profile is None:
            for _c, ln in pool:
                if ln.points:
                    ln.points = []
            self._surface_marker_n = 0
            return
        Ri = self._R_inner
        life = SURFACE_MARKER_LIFE
        t = self.elapsed
        # ⚠️ 两档都必须是**沙面自身那条亮带之外**的色。踩过两次同款坑:
        #    ① 最初用 (sand_light, sand_base): base 就是沙体本色 ⇒ 画上去 delta=0, 10 颗隐形;
        #    ② 改成 (sand_light, sand_dark) 后, **亮档又撞上了沙面自带的 3px 亮带**
        #       (`_mound_band_color` = sand_light @ 0.55) —— 金沙: 亮带 181 级、sand_light 190 级
        #       ⇒ 只差 **+8 级**, 低于材质噪声(±10) ⇒ 1号(上球)/2号(下球) 独立实测都报"看不见"。
        #    现在两档都取暗侧、且彼此可辨: sand_dark(−44 级) / mid(sand_dark, sand_base)(−27 级)。
        # 明暗差按 dingbu2.md §5 的 **8%~15%** 定档。⚠️ **不能写固定配比** ——
        # 六种沙色的 base↔dark 亮度跨度差很大(黑沙 base 仅 70, 金砂 172), 同一个 0.5 配比
        # 在金砂上是 −12%, 在黑沙上是 −29% ⇒ 固定配比必然有一半沙色出界。
        # 正确做法: **按目标亮度百分比反解** —— 在 base→dark 这条线上找 α 使 luma 命中 frac×luma(base)。
        # (实测口径 = 与**沙体本色**之比: 内侧偏移 2.6px 后标记的背景是沙体本色, 不再是亮带。)
        def _tier(frac):
            b, d = self.sand_base, self.sand_dark
            lb = 0.299 * b[0] + 0.587 * b[1] + 0.114 * b[2]
            ld = 0.299 * d[0] + 0.587 * d[1] + 0.114 * d[2]
            alpha = 0.0 if abs(ld - lb) < 1e-6 else (frac * lb - lb) / (ld - lb)
            alpha = max(0.0, min(1.0, alpha))
            return tuple(b[i] + (d[i] - b[i]) * alpha for i in range(3))
        color_base = _tier(0.85)    # −15% (规格上界)
        color_light = _tier(0.90)   # −10% (规格中段)

        def emit(x, y, frac, light, big, slope, weight=1.0):
            """slope = 该点当地沙面的 dy/dx(Kivy y 向上) —— 标记要**顺着坡面**画。

            ⚠️ dingbu2.md §5 明确要求"下球短划线沿当地坡面方向分布, 上球沿向中央汇拢的
            表面分布"。原实现一律画**竖直**短线(审计实测 20/20 颗走向角恒 90°),
            在 30° 的锥面上看着像插着一排钉子, 而不是沙面上的颗粒。
            """
            nonlocal n_used
            if n_used >= len(pool):
                return
            col, ln = pool[n_used]
            fade = min(1.0, 4.0 * frac) * min(1.0, 4.0 * (1.0 - frac))   # 两端淡入淡出
            col.rgba = (*(color_light if light else color_base),
                        SURFACE_MARKER_ALPHA * fade * weight)
            # 往下(沙体内侧)偏移 —— 离开 3px 亮带, 见 SURFACE_MARKER_INSET 的说明
            size = SURFACE_MARKER_SIZE_BIG if big else SURFACE_MARKER_SIZE
            half = size * 0.5
            norm = math.hypot(1.0, slope) or 1.0
            ux, uy = 1.0 / norm, slope / norm          # 单位切向量
            # ⚠️ 内移必须**沿表面法线**, 不能竖直(2026-10-05 r12-1号 查出):
            #    原式 `y - INSET - half*uy` 的偏移是竖直的, 在下坡侧(uy<0)会把
            #    **上坡那一端**抬到 `y - INSET + 2*half*|uy|` —— 30 度坡上浅 ~1.7px,
            #    实测下球右坡 5/6 颗的顶端仍插在 3px 亮带里(深度 2.25~3.12)。
            #    沿法线移 INSET 后, 两端到沙面的垂距都是 INSET(垂直深度 = INSET/cosθ, 只深不浅)。
            nx, ny = uy, -ux                           # 指向沙体内侧的单位法线(Kivy y 向上)
            cx0, cy0 = x + nx * SURFACE_MARKER_INSET, y + ny * SURFACE_MARKER_INSET
            ln.points = [cx0 - ux * half, cy0 - uy * half,
                         cx0 + ux * half, cy0 + uy * half]
            n_used += 1

        # ---- 上球: 从两侧朝中心滑(漏斗内) ----
        if upper_height > 0.0 and SURFACE_MARKERS_UP > 0:
            p = 0.0 if self.duration <= 0 else min(1.0, t / self.duration)
            d, b = self._upper_funnel_params(p, upper_height)
            lvl = self._upper_level_for(upper_height)
            level = self._upper_sand_bot + lvl
            # ⚠️ 半弦宽必须按**实际沙面高度 lvl** 算, 不是平顶等效高度 upper_height ——
            #    满球时 lvl = 2R ⇒ 半弦 = 0 ⇒ 标记路径缩成一点。而判据原先是
            #    `abs(dx) > half_chord`(0 > 0 为假) ⇒ **8 颗不跳过, 全叠在中心轴上**
            #    画成一根 1.6px 竖线(实测: 初始/重置/刚起步三态都是"上=8")。
            #    现在: 窄于 4px 整段不画 —— 那已不是"沿表面滑动", 只是一根柱。
            half_chord = math.sqrt(max(0.0, Ri * Ri - (Ri - min(2.0 * Ri, lvl)) ** 2))
            per_side = SURFACE_MARKERS_UP // 2
            for k in range(SURFACE_MARKERS_UP if half_chord >= 4.0 else 0):
                side = -1.0 if k < per_side else 1.0
                # 固定抖动(不随帧变)打破"一串珠子": 每颗的相位与路径起点都错开一点
                jit = ((k * 0.6180339887) % 1.0 - 0.5) * SURFACE_MARKER_JITTER
                frac = ((t / life) + (k % per_side) / max(1.0, per_side) + jit) % 1.0
                start = SURFACE_MARKER_UP_START + jit * 0.6
                x0 = side * half_chord * start
                x1 = side * half_chord * 0.06            # 终点靠近中心轴
                dx = x0 + (x1 - x0) * frac
                if abs(dx) >= half_chord:
                    continue
                # ⚠️ 必须与 `_draw_upper_shape` **同一个面**: 那边画的是
                #    `level − drop + rough(i)`, 而亮带是沿**那个**面画的。
                #    原来这里少了 `+ rough(i)` ⇒ 标记到**画出来的**沙面深度 = INSET + rough(dx),
                #    rough ∈ [−1.25, +0.95] ⇒ 沙量多时最小深度 **2.99px**, 插进 3px 亮带。
                #    (2026-10-05 r13-1号 在设备上量出 2/118 与 1/116 个端点深度读数 = 2.0;
                #     下球因为用的是 `prof.contact`(含粗糙)所以一直是好的, min=4.0 两轮复现。)
                _ri = int(round((dx + Ri) / (2.0 * Ri / (MOUND_SHAPE_NODES - 1))))
                y = (level - self._upper_surface_drop(dx, d, b)
                     + self._upper_rough_at(_ri, upper_height))
                _h = 1.5     # 数值微分步长(px); 上球沙面是 level 减去下陷量
                _sl = (self._upper_surface_drop(dx - _h, d, b)
                       - self._upper_surface_drop(dx + _h, d, b)) / (2.0 * _h)
                emit(self._cx + dx, y, frac, (k % 2) == 0, (k % 4) < 2, _sl)
        # ---- 下球: 从落点附近沿坡向外滑 ----
        if h_mound > 0.0 and SURFACE_MARKERS_LOW > 0:
            prof = self._mound_profile
            apex = self._mound_apex()
            base = self._lower_sand_bot
            per_side = SURFACE_MARKERS_LOW // 2
            for k in range(SURFACE_MARKERS_LOW):
                side = -1.0 if k < per_side else 1.0
                jit = ((k * 0.6180339887) % 1.0 - 0.5) * SURFACE_MARKER_JITTER
                frac = ((t / life) + (k % per_side) / max(1.0, per_side) + jit) % 1.0
                # 沿接触面从近顶滑向坡脚: 用"接触高度随 |dx| 下降到球底"定终点
                lo, hi = 1.0, Ri
                for _ in range(14):                       # 找接触面与球底相交处 = 坡脚
                    mid = 0.5 * (lo + hi)
                    if prof.raw(side * mid, apex) - prof.bounds(side * mid)[0] > 0.0:
                        lo = mid
                    else:
                        hi = mid
                foot = 0.5 * (lo + hi)
                start = min(foot, max(6.0, 0.12 * Ri))
                # ⚠️ **由强到弱**: 评审 dingbu.md §5 那张表对下球写的是
                #    "先**集中**在堆顶周围的短坡段" —— 不是把 12 颗均匀铺满整条坡。
                #    原实现 `start + (foot-start)*frac` 是线性等距 ⇒ 密度是平的。
                #    现在: 位置用 frac**1.8 映射(越往后越挤向坡脚? 不 —— 见下) + 远端衰减。
                #    用户原话: "至少附近他要有嘛, 有强到弱嘛, 正态分布啥的"。
                _t = frac ** 1.8                    # 0→0 1→1, 前段慢后段快 ⇒ 前半程更密
                dx = side * (start + (foot - start) * _t)
                y = base + prof.contact(dx, apex)
                if not prof.free_surface(dx, apex):
                    continue
                _h = 1.5
                _sl = (prof.contact(dx + _h, apex) - prof.contact(dx - _h, apex)) / (2.0 * _h)
                # 远端(坡脚)只保留 25% 不透明度 ⇒ 落点附近密而亮、越往外越稀越淡
                _w = 1.0 - 0.75 * frac
                emit(self._cx + dx, y, frac, (k % 2) == 1, (k % 4) >= 2, _sl, _w)
        for _c, ln in pool[n_used:]:
            if ln.points:
                ln.points = []
        self._surface_marker_n = n_used

    def _draw_upper_shape(self, upper_height):
        """上球沙面的 carve + 亮带 —— 专家 dingbu.md §4 的绘制部分。

        节点 = 该高度弦上的 65 个等距 x(与下球控制点同一套口径); 只在该弦范围内出四边形,
        弦外由球面 stencil 自己裁(出到弦外会画出反向四边形, 把玻璃色糊到沙上)。
        """
        carve, band = self._upper_carve, self._upper_band
        if upper_height <= 0.0:
            for q in carve + band:
                q.points = [0] * 8
            self._upper_band_color.a = 0.0
            return
        p = 0.0 if self.duration <= 0 else min(1.0, self.elapsed / self.duration)
        d, b = self._upper_funnel_params(p, upper_height)
        cx = self._cx
        Ri = self._R_inner
        # ⚠️ 不是 upper_height 而是**解出来的 level**: 漏斗扣掉的面积要抬回来(体积守恒)
        level = self._upper_sand_bot + self._upper_level_for(upper_height)
        top = self._upper_sand_bot + 2.0 * Ri + MOUND_CREST_MARGIN
        n = MOUND_SHAPE_NODES - 1
        step = 2.0 * Ri / n
        half_chord = math.sqrt(max(0.0, Ri * Ri - (Ri - upper_height) ** 2))
        # 与下球亮带同样的"薄→淡出 / 贴顶→收掉"处理
        fade = min(1.0, max(0.0, upper_height / SAND_SURFACE_FADE))
        apex_fade = min(1.0, max(0.0, (upper_height - (2.0 * Ri - SAND_BAND_APEX_FADE))
                                 / SAND_BAND_APEX_FADE))
        self._upper_band_color.a = SAND_SURFACE_ALPHA * fade * (1.0 - apex_fade)
        band_w = min(SAND_SURFACE_BAND, upper_height)
        cols = []
        for i in range(n + 1):
            dx = -Ri + step * i
            if abs(dx) > half_chord:      # 弦外: 由 stencil 裁, 不出四边形
                continue
            # ⚠️ 必须与 `_upper_area` **同一套算式**(含微粗糙), 否则解出来的面积 != 画出来的面积
            cols.append((cx + dx, level - self._upper_surface_drop(dx, d, b)
                         + self._upper_rough_at(i, upper_height)))
        limit = top - 1e-6
        for i in range(len(carve)):
            if i + 1 < len(cols):
                x0, y0 = cols[i]
                x1, y1 = cols[i + 1]
                carve[i].points = [x0, y0, x1, y1, x1, limit, x0, limit]
                band[i].points = [x0, y0, x1, y1, x1, y1 - band_w, x0, y0 - band_w]
            else:
                carve[i].points = [0] * 8
                if i < len(band):
                    band[i].points = [0] * 8

    def _draw_mound_shape(self, h_mound):
        """下球沙堆的**锥面**轮廓 —— carve 抠掉轮廓以上的沙, band 只画在真正的自由表面上。

        轮廓 H(x) = clamp(P(x), B(x), U(x))(见 `_MoundProfile`), 与碰撞/尘埃**共用同一份
        定义**(不再三处各写一遍, 专家 §2.4)。
        ⚠️ carve 上沿 = 球内顶 + 余量: H 被 U 裁剪过 ⇒ 上沿永远 ≥ H, 不会出上下颠倒的 Quad。
           (也不再需要"矩形画到峰顶之上"那种把 UV 拉长的做法 —— 专家 §2.5)
        ⚠️ band 只在 `B < P < U` 的真自由表面上画; 填满的列和裸露球底都不画,
           否则满球时会在球顶内侧留一条悬空的亮线(专家 §4.1)。
        """
        carve, band = self._mound_carve, self._mound_band
        profile = self._mound_profile
        if h_mound <= 0 or profile is None:
            for q in carve + band:
                q.points = [0] * 8
            return
        cx = self._cx
        bottom = self._lower_sand_bot
        top = bottom + 2.0 * self._R_inner + MOUND_CREST_MARGIN   # == redraw 里矩形的顶
        apex = self._mound_apex()
        self._mound_band_color.a = SAND_SURFACE_ALPHA * min(
            1.0, max(0.0, h_mound / SAND_SURFACE_FADE))
        knots = self._mound_knots(apex)
        cols = []
        for dx in knots:
            floor, _roof = profile.bounds(dx)
            y = profile.contact(dx, apex)
            cols.append((cx + dx, bottom + y, profile.free_surface(dx, apex), y - floor))
        for i in range(len(carve)):
            if i + 1 >= len(cols):
                carve[i].points = [0] * 8
                if i < len(band):
                    band[i].points = [0] * 8
                continue
            x0, y0, free0, th0 = cols[i]
            x1, y1, free1, th1 = cols[i + 1]
            carve[i].points = [x0, y0, x1, y1, x1, top, x0, top]
            if i >= len(band):
                continue
            if free0 and free1:
                w = min(SAND_SURFACE_BAND, th0, th1)
                band[i].points = [x0, y0, x1, y1, x1, y1 - w, x0, y0 - w]
            else:
                band[i].points = [0] * 8

    def _build_dynamic_canvas(self):
        """保留真圆/Stencil/Line 画法,只在几何变化时重建固定指令。"""
        self.canvas.clear()
        cx, Ri = self._cx, self._R_inner
        self._sand_chords = []
        self._sand_bands = []
        material = self._current_material()
        self._sand_material = material
        with self.canvas:
            for yc in (self._upper_y_c, self._lower_y_c):
                bottom = yc - Ri
                StencilPush()
                Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
                StencilUse()
                # 材质烘的就是 albedo ⇒ 前面必须是**白色**; 退回平色时才染 sand_base
                color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
                rect = Rectangle(pos=(cx - Ri, bottom), size=(2 * Ri, 0),
                                 texture=None if material is None else material.texture)
                # 沙面窄过渡: 与沙体**同一个 stencil**, 只在沙面内部多铺一条很窄的亮带
                band_color = Color(*(tuple(self.sand_light) + (0.0,)))
                band_rect = Rectangle(pos=(cx - Ri, bottom), size=(2 * Ri, 0))
                self._sand_bands.append((band_color, band_rect))
                if yc == self._upper_y_c:
                    # 上球: 漏斗 carve(专家 dingbu.md §4) —— 与下球同一套写法, 段数同样按节点数定
                    n_seg_u = max(1, MOUND_SHAPE_NODES)
                    Color(*hex_rgb(GLASS_FILL), 1)
                    self._upper_carve = [Quad(points=[0] * 8) for _ in range(n_seg_u)]
                    self._upper_band_color = Color(*(tuple(self.sand_light) + (0.0,)))
                    self._upper_band = [Quad(points=[0] * 8) for _ in range(n_seg_u)]
                if yc == self._lower_y_c:
                    # 下球: carve 按**真转折点**折线抠掉轮廓以上的沙(见 _draw_mound_shape)。
                    # 段数 = 节点数-1(含 ±R/±b/0), 在几何重建时定下来。
                    n_seg = max(1, len(self._mound_knots()) - 1 + 4)   # +4: 逐帧的壁交点
                    Color(*hex_rgb(GLASS_FILL), 1)
                    self._mound_carve = [Quad(points=[0] * 8) for _ in range(n_seg)]
                    self._mound_band_color = Color(*(tuple(self.sand_light) + (0.0,)))
                    self._mound_band = [Quad(points=[0] * 8) for _ in range(n_seg)]
                StencilUnUse()
                Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
                StencilPop()
                self._sand_chords.append((color, rect))
            neck_tex = None if material is None else material.texture
            self._neck_color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
            self._neck_quads = [
                Quad(points=[0] * 8, texture=neck_tex) for _ in range(TAPER_SEGS + 1)]
            self._neck_solid_color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
            self._neck_solid_rect = Rectangle(size=(0, 0), texture=neck_tex)
            # 沙柱下段(孔口往上 transition 那段): 直接画不透明的沙色矩形。
            # 原来这里用 1×64 渐变纹理做 alpha 0.7→1.0 的"出口柔化", 但这条矩形
            # 只有几个像素高, **任何 alpha 变化都等于硬边** —— 实测在管内留下一条
            # 半透明横线(关掉颗粒层后单行跳变 dB=10.9;改成不透明后降到 5.3,
            # 剩下的是"沙柱→敞开喇叭口"的自然边界)。
            self._neck_fade_color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
            self._neck_fade_rect = Rectangle(size=(0, 0), texture=neck_tex)

        # §5 表层滑动标记(专家 dingbu.md §5): 固定图元池, 不新增物理粒子。
        # ⚠️ **必须建在这里** —— 沙体之后。曾经建在上面那个 `with self.canvas:` 的 stencil 块里,
        #    `canvas.add()` 在那里是**插在当前位**(不是追加到末尾) ⇒ 组落在 index 16,
        #    被其后绘制的沙体整个盖住: 20 颗标记里只有 2 颗露头的能看见(A/B 差分实测)。
        self._surface_marker_n = 0
        self._surface_marker_pool = []
        self._surface_marker_group = InstructionGroup()
        self.canvas.add(self._surface_marker_group)
        for _i in range(SURFACE_MARKERS_UP + SURFACE_MARKERS_LOW):
            _c = Color(*(tuple(self.sand_base) + (0.0,)))
            _l = Line(points=[], width=1)
            self._surface_marker_group.add(_c)
            self._surface_marker_group.add(_l)
            self._surface_marker_pool.append((_c, _l))
        self._neck_grain_group = InstructionGroup()
        self.canvas.add(self._neck_grain_group)
        self._neck_grain_pool = []
        self._neck_grain_count = 0
        # Project existing grains upstream; they do not add physics particles.
        for _ in range(128):
            color = Color(*self.sand_base)
            line = Line(points=[], width=1)
            self._neck_grain_group.add(color)
            self._neck_grain_group.add(line)
            self._neck_grain_pool.append((color, line))

        # 同色同线宽共用一条 Color 指令,且不再排序/改变物理粒子列表。
        self._stream_pools = {}
        # 高光在普通粒子之后绘制,避免被密集的主体完全盖住。
        for index in list(range(len(self._color_table))) + [-1]:
            for size in (1, 2):
                group = InstructionGroup()
                color = Color(*(self._hilite_color if index < 0
                                else self._color_table[index]))
                group.add(color)
                self.canvas.add(group)
                self._stream_pools[index, size] = (group, color, [])
        self._stream_buckets = {key: [] for key in self._stream_pools}
        self._stream_counts = {key: 0 for key in self._stream_pools}
        self._reserve_stream_lines()

        self._splash_group = InstructionGroup()
        self._splash_color = Color(*self.sand_light)
        self._splash_group.add(self._splash_color)
        self.canvas.add(self._splash_group)
        self._splash_rects = []
        self._flare_group = InstructionGroup()
        self.canvas.add(self._flare_group)
        self._flare_rects = []
        self._dust_group = InstructionGroup()
        self._dust_color = Color(*self.sand_light)
        self._dust_group.add(self._dust_color)
        self.canvas.add(self._dust_group)
        self._dust_rects = []
        with self.canvas:
            self._bore_color = Color(0.8, 0.8, 0.8, 0)
            bore = self._taper['t_in']
            for x in (cx - bore + 1, cx + bore - 1):
                Line(points=[x, self._neck_y - 7, x, self._neck_y + 7], width=1)
            self._pause_color = Color(*hex_rgb(BG_COLOR), 0)
            self._pause_rect = Rectangle(pos=self.pos, size=(0, 0))
        self._render_colors = None

    def _reserve_stream_lines(self):
        """按最长飞行时间预留图元,只影响分配时机,实际粒子仍按原速率生成。"""
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        floor = self._lower_sand_bot
        rate = 600 * self.speed_factor
        motion_scale = self._particle_motion_scale
        div = max(1, self._neck_y - self._glass_bot) / len(self._color_table)
        thin_only = max(1, self.neck_w - self._ow) < 3

        def travel(y):
            return ((math.sqrt(35 ** 2 + 900 * max(0, outlet - y)) - 35) /
                    (450 * motion_scale))

        expected_by_color = [0.0] * len(self._color_table)
        for index in range(len(self._color_table)):
            upper = min(outlet, self._neck_y - index * div)
            lower = max(floor, self._neck_y - (index + 1) * div)
            expected = rate * max(0, travel(lower) - travel(upper))
            for variation in range(-2, 3):
                target = max(0, min(len(self._color_table) - 1, index + variation))
                expected_by_color[target] += expected / 5
        for (index, size), (group, _color, pool) in self._stream_pools.items():
            if index < 0:
                expected = rate * travel(floor) * 0.10
            else:
                expected = expected_by_color[index]
            share = (int(size == 1) if thin_only else
                     (0.85 if size == 2 else 0.15))
            count = math.ceil(expected * share + 3 * math.sqrt(expected) + 2)
            for _ in range(count):
                line = Line(points=[], width=size)
                group.add(line)
                pool.append(line)

    @staticmethod
    def _sync_rects(group, pool, particles, fixed_size=None):
        for i, particle in enumerate(particles):
            sz = particle["size"] if fixed_size is None else fixed_size
            offset = sz / 2 if fixed_size is None else 0
            pos = (particle["x"] - offset, particle["y"] - offset)
            size = (sz, sz)
            if i == len(pool):
                rect = Rectangle(pos=pos, size=size)
                group.add(rect)
                pool.append(rect)
            else:
                pool[i].pos = pos
                pool[i].size = size
        for rect in pool[len(particles):]:
            if rect.size[0] or rect.size[1]:
                rect.size = (0, 0)

    def redraw(self):
        if not self._geom_ready:
            return
        remaining = self.get_remaining()
        now = time.perf_counter()
        # ⚠️ 这里必须含 sand_dark: 材质缓存键含三色, 而**只改 dark 的调用方**会拿到陈旧材质 ⇒ 串色。
        # 今天不可达(set_sand_color 三者同改), 但两个独立评审都点了这条。
        colors = (self.sand_base, self.sand_dark, self.sand_light)
        if colors != self._render_colors:
            if self._sand_material is None:
                for color, _rect in self._sand_chords:
                    color.rgb = self.sand_base
            else:
                # 材质烘的就是 albedo ⇒ 前面保持白色, 换色只能**换纹理** ——
                # 再染一层 sand_base 会让画面明显发暗(旧逻辑就是这么写的)。
                # ⚠️ 走 `_current_material()` 而不是直接 sand_material(): 拖动滑块留下的
                # 预览槽优先(换色时 set_sand_color 已把它清掉, 所以这里通常拿到缓存材质)。
                material = self._current_material()
                if material is not None:
                    self._sand_material = material
                    for _color, rect in self._sand_chords:
                        rect.texture = material.texture
                    # 颈部沙柱/出口段用**同一张材质**, 否则沙体有颗粒而颈部是平色, 读成两种材料
                    for quad in self._neck_quads:
                        quad.texture = material.texture
                    self._neck_solid_rect.texture = material.texture
                    self._neck_fade_rect.texture = material.texture
            if self._sand_material is None:
                # 退回平色时颈部才跟着染沙色; 有材质时前面必须保持白色(否则双重着色变暗)
                self._neck_color.rgb = self.sand_base
                self._neck_solid_color.rgb = self._neck_fade_color.rgb = self.sand_base
            # 沙面窄过渡那条带永远用亮端(与材质与否无关)
            for band_color, _rect in self._sand_bands:
                band_color.rgb = self.sand_light
            self._mound_band_color.rgb = self.sand_light
            # ⚠️ **上球那条也要刷**(2026-10-05 r9-1号 查出漏了): 它原来只在
            #    `_build_dynamic_canvas()` 创建时取一次 sand_light, 换沙色后**一直是旧色**,
            #    直到发生几何重建(改周期/改尺寸/跑一次 benchmark)才跟上。
            #    实测: 金沙换绿沙后未重建时沙面首 3 行仍 (221,215,166) 暖色调,
            #    触发重建后 (200,217,160) 才变绿。3~5 设备像素高 × 约 900px 宽, 通道差 ≈20 级。
            self._upper_band_color.rgb = self.sand_light
            for (index, _size), (_group, color, _pool) in self._stream_pools.items():
                color.rgb = (self._hilite_color if index < 0
                             else self._color_table[index])
            self._splash_color.rgb = self._dust_color.rgb = self.sand_light
            self._render_colors = colors

        h_mound = self._mound_height_px()
        upper_height = self._upper_sand_height_px()
        # ⚠️ 高度每帧在变, **UV 不能跟着归一化**: 基准永远是完整纹理的四角,
        # 只按 h/直径 截取 —— 否则沙越少纹理越扁, 读起来像橡皮(见 crop_tex_coords)。
        diameter = 2 * self._R_inner
        full_uv = None if self._sand_material is None else self._sand_material.tex_coords
        # ⚠️ 沙面被面积求解**抬高**了 `lift`(最多 ~1.4px) ⇒ 矩形顶必须跟着抬,
        #    否则抬起来那一条露在矩形外面, 成一条玻璃缝(2026-10-05 自查出的回归)。
        _up_lift = max(0.0, self._upper_level_for(upper_height) - upper_height)
        up_draw = upper_height + _up_lift
        # 下球矩形**固定画满整个内球**(D + 余量), 不再跟着虚拟峰顶走 —— 专家 §2.5:
        #   ① 放开"虚拟锥顶高于球顶"之后, 再按高度截 UV 会把颗粒**纵向拉伸**;
        #   ② 轮廓以上的沙由 carve 抠掉, 所以矩形只管"铺满", 上沿永远取球内顶 + 余量。
        #   ⇒ 与 `_draw_mound_shape` 里 carve 的上沿是**同一个值**。
        mound_draw = (2.0 * self._R_inner + MOUND_CREST_MARGIN) if h_mound > 0 else 0.0
        for index, ((_color, rect), height) in enumerate(
                zip(self._sand_chords, (up_draw, mound_draw))):
            rect.size = (diameter, height)
            if full_uv is not None:
                # 上球: 高度会变 ⇒ 按高度截 UV(颗粒尺度恒定)
                # 下球: 固定 D×D ⇒ 整张 UV(专家 §2.5 的"固定完整 UV")
                rect.tex_coords = (full_uv if index == 1
                                   else crop_tex_coords(full_uv, height / diameter))
        # 沙面窄过渡(评审 meishu2.md §4.3): 紧贴沙面**内部**的一条窄亮带。
        # 下沙用 get_mound_top_y() —— 与**粒子碰撞面**同一个值, 保证"落点与可见表面一致"。
        # ⚠️ 沙体薄时按可见厚度按比例减弱, 否则会剩一条悬空的独立亮线。
        # ⚠️ 下球那条由 `_draw_mound_shape` 沿真实轮廓逐段画(直边矩形跟不上起伏,
        #    会留下悬空亮台/缺口 —— 评审 1 号指出), 这里只画上球那条。
        # ⚠️ **两条旧的直边亮带都作废了**: 下球改由 `_draw_mound_shape`、上球改由
        #    `_draw_upper_shape` 沿真实轮廓逐段画。留着会与曲线带叠成两条
        #    (一条平一条弯) —— 2026-10-05 上球漏斗落地后自查出的回归。
        for _band_color, _band_rect in self._sand_bands:
            _band_rect.size = (0, 0)
            _band_color.a = 0.0
        self._draw_surface_markers(upper_height, h_mound)   # §5 表层滑动标记
        self._draw_upper_shape(upper_height)     # §4 上球漏斗(纯减去: 矩形/UV 不动)
        self._draw_mound_shape(h_mound)
        # ⚠️ 闸门含 `_done_at`: 漏完那一帧 upper_height 已是 0, 但沙柱还要排空 fill_t 秒
        side = (self._neck_sand_side()
                if (upper_height > 0 or self._done_at is not None) else [])
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        inlet = self._taper["y_bot"]
        transition = min(inlet - outlet, max(8, self._taper["t_in"] * 0.7))
        connected = bool(side and side[-1][1] <= outlet + 1e-6)
        fade_top = outlet + transition
        neck_uv_scale = None if self._sand_material is None else 1.0 / diameter
        for i, quad in enumerate(self._neck_quads):
            if i < len(side) - 1:
                (x0, y0), (x1, y1) = side[i], side[i + 1]
                if connected and i == len(side) - 2:
                    y1 = fade_top
                quad.points = [self._cx - x0, y0, self._cx + x0, y0,
                               self._cx + x1, y1, self._cx - x1, y1]
                if neck_uv_scale is not None:
                    # 与沙体**同一张材质、同一颗粒尺度**: u 按**实际半宽/直径**取,
                    # v 从球底那一段起、沿颈部向下递增走进纹理内部。
                    # ⚠️ u 绝不能写 0..1 —— 颈部只有二十来像素宽, 铺满整张纹理会被横向
                    # 压十几倍, 变成一条竖向亮带、两边还取到材质的暗边(2026-10-04 实拍)。
                    su = neck_uv_scale
                    vb = NECK_UV_ANCHOR + (self._upper_sand_bot - y0) * su
                    vt = NECK_UV_ANCHOR + (self._upper_sand_bot - y1) * su
                    quad.tex_coords = (0.5 - x0 * su, vb, 0.5 + x0 * su, vb,
                                       0.5 + x1 * su, vt, 0.5 - x1 * su, vt)
            else:
                quad.points = [0] * 8
        if connected:
            pos = (self._cx - self._taper["t_in"], outlet)
            size = (2 * self._taper["t_in"], transition)
            self._neck_solid_rect.pos = self._neck_fade_rect.pos = pos
            self._neck_solid_rect.size = self._neck_fade_rect.size = size
            if neck_uv_scale is not None:
                su = neck_uv_scale
                half = self._taper["t_in"] * su
                vb = NECK_UV_ANCHOR + (self._upper_sand_bot - outlet) * su
                vt = NECK_UV_ANCHOR + (self._upper_sand_bot - outlet - transition) * su
                uvs = (0.5 - half, vb, 0.5 + half, vb, 0.5 + half, vt, 0.5 - half, vt)
                self._neck_solid_rect.tex_coords = uvs
                self._neck_fade_rect.tex_coords = uvs
            strength = min(1, max(0, (self.elapsed - self._neck_fill_time) / 0.1))
            self._neck_solid_color.a = 1 - strength
        else:
            self._neck_solid_rect.size = self._neck_fade_rect.size = (0, 0)

        self._draw_stream()
        self._draw_neck_grains(side)
        self._sync_rects(self._splash_group, self._splash_rects, self.splashes)
        for i, f in enumerate(self.flares):
            if i == len(self._flare_rects):
                color, rect = Color(), Rectangle()
                self._flare_group.add(color)
                self._flare_group.add(rect)
                self._flare_rects.append((color, rect))
            color, rect = self._flare_rects[i]
            life = max(0.0, f["end"] - now) / 0.08
            color.rgba = (*self.sand_light, 0.45 * life)
            width, height = 2 + life * 2, 0.8 + life * 0.4
            rect.pos = (f["x"] - width / 2, f["y"] - height / 2)
            rect.size = (width, height)
        for color, rect in self._flare_rects[len(self.flares):]:
            if rect.size[0] or rect.size[1]:
                color.a = 0
                rect.size = (0, 0)
        self._sync_rects(self._dust_group, self._dust_rects, self.dusts, dp(1.2))
        self._bore_color.a = 1 if remaining <= 0.001 else 0
        self._pause_color.a = 0.55 if not self.running and 0 < self.elapsed < self.duration else 0
        self._pause_rect.size = self.size if self._pause_color.a else (0, 0)

    def _group_stream_particles(self):
        """按 (色调, 线宽) 分桶 —— 返回 `{key: [粒子下标, ...]}`, 不再是 dict 列表。

        下标升序、桶内顺序与逐 dict 版逐字相同(渲染顺序不变); 渲染器从 `self._pv`
        按下标取 x / y / vy / trail_time, 于是每帧不必再建 2500 个 dict。
        """
        buckets = self._stream_buckets
        for bucket in buckets.values():
            bucket.clear()
        n_colors = len(self._color_table)
        div = max(1.0, self._neck_y - self._glass_bot) / n_colors
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        tone_scale = 5 / math.tau
        # 预先摊平成二维表: 原来每颗粒都要现造一个 (index, size) 元组再查字典,
        # 这里换成两次列表下标。分组结果与原来逐字相同。
        by_key = [[buckets.get((i, s)) for s in (1, 2)]
                  for i in list(range(n_colors)) + [-1]]
        light_row = by_key[n_colors]
        last = n_colors - 1
        pv = self._pv
        if pv.use_np:
            # 向量化: 选(y 未越过 outlet) -> 算色调档 -> 拼成 0..(2*(n_colors+1)-1) 的
            # 桶码 -> 稳定排序按桶分段。桶内下标升序, 与原 append 次序逐字相同。
            np = _np
            n = pv.n
            y_all = self.py[:n]
            # 用 ~(y >= outlet) 而不是 y < outlet: 标量版是 `if y >= outlet: continue`,
            # NaN 时两者都为 False 故**不跳过** —— 取反才逐字对齐(NaN 实际不会出现)。
            sel = np.flatnonzero(~(y_all >= outlet))
            if sel.size:
                yy = y_all[sel]
                w = (self.pwp[:n][sel] * tone_scale).astype(np.int64)
                np.minimum(w, 4, out=w)
                idx = ((self._neck_y - yy) / div).astype(np.int64) + w - 2
                np.clip(idx, 0, last, out=idx)
                key = np.where(self.pli[:n][sel] != 0.0, n_colors, idx)
                slot = np.where(self.psz[:n][sel] == 1.0, 0, 1)
                code = key * 2 + slot
                order = np.argsort(code, kind="stable")
                counts = np.bincount(code, minlength=(n_colors + 1) * 2)
                pos = 0
                for k in range(counts.shape[0]):
                    c = int(counts[k])
                    if not c:
                        continue
                    key_idx = k >> 1
                    row = light_row if key_idx == n_colors else by_key[key_idx]
                    row[k & 1].extend(sel[order[pos:pos + c]].tolist())
                    pos += c
            return buckets
        ys = pv.y
        phases = pv.wp
        sizes = pv.sz
        lights = pv.light
        for i in range(pv.n):
            y = ys[i]
            if y >= outlet:
                continue
            if lights[i]:
                row = light_row
            else:
                w = int(phases[i] * tone_scale)
                if w > 4:
                    w = 4
                index = int((self._neck_y - y) / div) + w - 2
                if index < 0:
                    index = 0
                elif index > last:
                    index = last
                row = by_key[index]
            row[0 if sizes[i] == 1 else 1].append(i)
        return buckets

    def _draw_stream(self):
        buckets = self._group_stream_particles()
        motion_scale = self._particle_motion_scale
        top_limit = self._taper["y_bot"]
        pv = self._pv
        xs = pv.x
        ys = pv.y
        vys = pv.vy
        trails = pv.tl
        for key, indices in buckets.items():
            group, _color, pool = self._stream_pools[key]
            for i, index in enumerate(indices):
                y = ys[index]
                x = xs[index]
                trail = max(2.0, abs(vys[index]) * trails[index] / motion_scale)
                top = min(top_limit, y + trail)
                coords = (x, y, x, top)
                if i == len(pool):
                    line = Line(points=coords, width=key[1])
                    group.add(line)
                    pool.append(line)
                else:
                    pool[i].points = coords
            for line in pool[len(indices):self._stream_counts[key]]:
                if line.points:
                    line.points = []
            self._stream_counts[key] = len(indices)

    def _particle_trail(self, particle, motion_scale=None):
        scale = self._particle_motion_scale if motion_scale is None else motion_scale
        return max(2.0, abs(particle["vy"]) * particle["trail_time"] / scale)

    def _hide_neck_grains(self):
        for color, line in self._neck_grain_pool[:self._neck_grain_count]:
            line.points = []
        self._neck_grain_count = 0

    def _draw_neck_grains(self, side):
        """把已流出的粒子投影回**整条**颈部轮廓(喇叭口 + 直筒),做连续颗粒纹理。

        旧写法只覆盖直筒、且颗粒可见度从入口的 0 起 —— 颗粒在入口一段完全看不见,
        于是"可见度前沿"在颈部留下一条横向分界线(线上是纯平色块、线下才有颗粒),
        就是那条看不出画在哪的横线。这里改为:
        ① 覆盖整条 side(喇叭口→孔口),颗粒横向按轮廓半宽展开成扇形;
        ② 可见度恒定、不再归零;
        ③ 色调只在 [底色 → sand_light] 之间走且入口端不归零 —— 既保留原设计的
           "闪砂"观感(压暗会变成脏斑),又不留纯平区,横向突变随之消失。
        """
        if not side or side[-1][1] > 2 * self._neck_y - self._taper["y_bot"] + 1e-6:
            self._hide_neck_grains()
            return
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        length = max(1e-6, self._taper["y_bot"] - outlet)
        top_y, bottom_y = side[0][1], side[-1][1]
        span = max(1e-6, top_y - bottom_y)
        scale = self._particle_motion_scale
        twice_gravity = 900 * scale * scale
        source_limit_squared = (75 * scale) ** 2
        # 第一趟只求最深的投影深度, 第二趟再画 —— 原来给每个候选都分配一个
        # (distance, particle) 元组(峰值约 1800 次/帧)。两趟的候选顺序与 depth
        # 都与原实现一致, 所以 128 上限的截断结果也相同。按下标遍历 `_pv` 快照。
        pv = self._pv
        ys_p = pv.y
        vys_p = pv.vy
        depth = 1e-6
        if pv.use_np:
            # 向量化这一趟: 它每帧都要扫全部 pn 颗粒(第二趟本来就 break 在 128, 不是 O(pn))。
            # 条件逐字照抄标量版, 且用 ~(A|B) 而不是直接写"保留条件" —— 标量版是
            # `if <cond>: continue`, NaN 时比较为 False 故**不跳过**, 取反才逐字对齐。
            _d = outlet - pv.ny[:pv.n]
            _keep = ~((_d < 0) | (_d > length + 1e-6))
            _keep &= ~(pv.nvy[:pv.n] ** 2 - twice_gravity * _d > source_limit_squared)
            # 第二趟原先是 `for i in range(pv.n)` 的纯 Python 裸扫描, 重复做上面同一组判定。
            # 这里把候选下标取出来给它复用: O(n) -> O(候选数)。
            # flatnonzero 恒升序 ⇒ 与 range(n) 同序 ⇒ count/池下标的分配顺序不变,
            # 且循环体内无 random 调用 ⇒ random.seed(23) 闸门不受影响。
            _cand = _np.flatnonzero(_keep).tolist() if _keep.any() else []
            if _keep.any():
                _mx = float(_d[_keep].max())
                if _mx > depth:
                    depth = _mx
        else:
            for i in range(pv.n):
                distance = outlet - ys_p[i]
                if distance < 0 or distance > length + 1e-6:
                    continue
                # An isolated fast grain must not stretch the startup texture ahead of the main flow.
                if vys_p[i] ** 2 - twice_gravity * distance > source_limit_squared:
                    continue
                if distance > depth:
                    depth = distance
            _cand = None
        ys = [y for _x, y in side]
        xs = [x for x, _y in side]

        def half_w_at(y):
            if y >= ys[0]:
                return xs[0]
            for i in range(len(side) - 1):
                y0, y1 = ys[i], ys[i + 1]
                if y1 <= y <= y0:
                    if y0 - y1 < 1e-9:
                        return xs[i]
                    return xs[i] + (xs[i + 1] - xs[i]) * (y0 - y) / (y0 - y1)
            return xs[-1]

        t_in = max(1e-6, self._taper["t_in"])
        tone_scale = 5 / math.tau
        pool = self._neck_grain_pool
        pool_len = len(pool)
        cx = self._cx
        base_r, base_g, base_b = self.sand_base
        light_r, light_g, light_b = self.sand_light
        sizes_p = pv.sz
        phases_p = pv.wp
        lights_p = pv.light
        xs_p = pv.x
        count = 0
        # use_np 时只遍历候选(第一趟已算好); 标量兜底路径保持原样。
        for i in (_cand if _cand is not None else range(pv.n)):
            distance = outlet - ys_p[i]
            if distance < 0 or distance > length + 1e-6:
                continue
            if vys_p[i] ** 2 - twice_gravity * distance > source_limit_squared:
                continue
            t = distance / depth                     # 0 = 刚出孔口, 1 = 流得最深的一颗
            if t > 1.0:
                t = 1.0
            y = top_y - t * span
            half_w = half_w_at(y)
            size = sizes_p[i]
            half_stroke = size if size > 1 else 0.5
            limit = half_w - half_stroke
            if limit <= 0.0:
                limit = 0.0
            spread = (xs_p[i] - cx) * (half_w / t_in)
            if spread > limit:
                spread = limit
            elif spread < -limit:
                spread = -limit
            x = cx + spread
            # ⚠️ 亮端必须与**沙体材质**的量级对齐(2026-10-04 用户: "上面的部分和颈部的沙子
            # 构成完全不同")。材质在 base ± 0.35 之间, 而这里原来最高走到 base→light 的 0.85,
            # 比球体整整高一个档 ⇒ 颈部读成另一种材料。压暗会变脏斑(项目试过), 所以只收窄亮端。
            tone_t = 0.06 + 0.20 * t
            if lights_p[i]:
                tr, tg, tb = light_r, light_g, light_b
            else:
                variation = int(phases_p[i] * tone_scale)
                if variation > 4:
                    variation = 4
                variation -= 2
                mix = tone_t + variation * 0.04
                if mix < 0.0:
                    mix = 0.0
                elif mix > 1.0:
                    mix = 1.0
                # 等价于 lerp_rgb(sand_base, sand_light, mix)
                tr = base_r + (light_r - base_r) * mix
                tg = base_g + (light_g - base_g) * mix
                tb = base_b + (light_b - base_b) * mix
            color, line = pool[count]
            # Opaque preblend avoids Kivy's extra stencil passes for translucent wide lines.
            color.rgb = (base_r + (tr - base_r) * 0.85,
                         base_g + (tg - base_g) * 0.85,
                         base_b + (tb - base_b) * 0.85)
            if line.width != size:
                line.width = size
            top_pt = y + 1
            if top_pt > top_y:
                top_pt = top_y
            bot_pt = y - 1
            if bot_pt < bottom_y:
                bot_pt = bottom_y
            line.points = (x, bot_pt, x, top_pt)
            count += 1
            if count == pool_len:
                break
        for color, line in self._neck_grain_pool[count:self._neck_grain_count]:
            line.points = []
        self._neck_grain_count = count


# ---------- App / UI(v2 布局: 色块在上, 控件在下) ----------

class HourglassApp(App):
    title = "跳跳的沙漏"

    def on_start(self):
        if platform == "android":
            Window.bind(on_flip=self._hide_startup_screen)
            self._apply_max_refresh_rate()
            # SDL 把窗口挂稳之后可能再刷一次窗口属性, 补一发(幂等)
            Clock.schedule_once(lambda _dt: self._apply_max_refresh_rate(), 1.5)
        # 六种配色的沙体材质**分批**烘好(每张约 16ms), 摊掉"第一次点某个颜色卡一下"。
        # 流沙时让路, 不让它跟帧抢时间; 见 _warm_sand_materials。
        self._sand_warm_queue = list(SAND_PRESETS)
        Clock.schedule_interval(self._warm_sand_materials, 0.05)

    def _warm_sand_materials(self, _dt):
        """每帧最多烘**一种**配色的材质。流沙时跳过(返回 True 继续等), 烘完自动停。"""
        if self.hourglass.running:
            return True
        if not self._sand_warm_queue:
            return False
        _name, base, dark, light = self._sand_warm_queue.pop(0)
        try:
            # ⚠️ SAND_PRESETS 存的是 **'#rrggbb' 字符串**, 不是浮点三元组 ——
            # 直接喂给生成器会 ValueError, 然后被这里的 except 吞掉 ⇒ 预热**静默失效**。
            # (2026-10-04 烟测抓到: 四条 "could not convert string to float" 日志。)
            sand_material(hex_rgb(base), hex_rgb(dark), hex_rgb(light))
        except Exception as exc:
            print(f"sand material warm failed: {exc}")
        return bool(self._sand_warm_queue)

    def _apply_max_refresh_rate(self):
        """向系统**显式要**当前屏幕的最高刷新率。

        安卓**不会**自动把面板跑到最高档 —— 不给 `preferredRefreshRate` 就按系统默认档走
        (常见 60/120, 哪怕面板是 165/185)。这里读 `Display.getSupportedModes()` 取最高档,
        写进窗口的 `WindowManager.LayoutParams`, 并把"要之前/要之后"都打出来便于回溯。

        ⚠️ 与 `maxfps=0` 是**两件事**: maxfps 是"我们自己不设上限", 这一步是"让系统别给低档"。
        ⚠️ SDL 回前台可能重刷窗口属性 ⇒ `on_resume` 也要再要一次(同 `_apply_orientation`)。
        ⚠️ 刚 setAttributes 时档位切换是异步的, 紧接着读回仍可能是旧值 ——
        真正算数的是基准日志里的 `refresh_hz`(它在基准开始时才读)。
        """
        try:
            from jnius import autoclass
            version = autoclass("android.os.Build$VERSION")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            display = activity.getWindowManager().getDefaultDisplay()
            now = float(display.getRefreshRate())
            best = now
            if int(version.SDK_INT) >= 23:
                modes = display.getSupportedModes()
                for i in range(len(modes)):
                    best = max(best, float(modes[i].getRefreshRate()))
            if best <= now + 0.5:
                print(f"Refresh rate: already at panel max ({now:.1f}Hz)")
                return
            attrs = activity.getWindow().getAttributes()
            attrs.preferredRefreshRate = float(best)
            activity.getWindow().setAttributes(attrs)
            after = float(activity.getWindowManager()
                          .getDefaultDisplay().getRefreshRate())
            print(f"Refresh rate: requested {best:.1f}Hz "
                  f"(was {now:.1f}Hz, readback {after:.1f}Hz)")
        except Exception as exc:
            print(f"Refresh rate request failed: {exc}")

    def _hide_startup_screen(self, *_):
        if not self.hourglass._geom_ready or self.hourglass.height <= dp(100):
            return
        Window.unbind(on_flip=self._hide_startup_screen)
        try:
            from android.loadingscreen import hide_loading_screen
            hide_loading_screen()
        except Exception as exc:
            print(f"Startup overlay removal failed: {exc}")

    def build(self):
        self._sound_popup = None
        self._dev_popup = None
        self._sound_diag_label = None
        self._completion_popup = None
        self._last_win_size = None
        if platform != "android":
            try:
                # 桌面模拟: --landscape 用宽窗验证反旋转; 否则维持手机竖屏
                if "--landscape" in sys.argv:
                    Window.size = (1000, 600)
                else:
                    Window.size = (400, 800)
            except Exception:
                pass
        Window.clearcolor = (*hex_rgb(BG_COLOR), 1)

        self.hourglass = HourglassWidget(size_hint=(1, 1))
        cfg = self.hourglass.load_config()
        if isinstance(cfg.get('duration'), (int, float)) and cfg['duration'] > 0:
            self.hourglass.duration = cfg['duration']
            self.hourglass._rebuild_height_table()
        sound_name = cfg.get('sound_name')
        if sound_name in [n for n, _ in SOUND_OPTIONS]:
            self.hourglass._set_sound(sound_name)
        color_name = cfg.get('color_name', '金沙')
        for name, base, dark, light in SAND_PRESETS:
            if name == color_name:
                self.hourglass.set_sand_color(base, dark, light)
                break
        # 沙体材质: **必须在建材质之前**改全局(材质在 _build_dynamic_canvas 里按配色缓存)。
        # 缺项就退回出厂默认(浓度 0.35)。
        # ⚠️ **环境变量优先于配置**: `HG_SAND_*` 是取图与 A/B 的开关, 一旦被本地配置盖掉,
        # 所有测量都会**悄悄用错档位**(A/B 两臂还会变成同一版)。
        if os.environ.get("HG_SAND_MATERIAL") is None and os.environ.get("HG_SAND_GRAIN") is None:
            apply_sand_style(cfg.get('sand_mode', 'grain'),
                             cfg.get('sand_grain', SAND_GRAIN_DEFAULT))
        # 两个六档: 环境变量给了就**不读配置**(取图/AB 的档位绝不能被本地配置盖掉)
        # ⚠️ 档位表改过版 ⇒ 旧配置里的档号**整组作废**(回落默认)。
        #    不加这一条, 用户存的"3"会被静默换成另一种颗粒(2026-10-05 重定表就是这个情形)。
        _grain_rev_ok = cfg.get('grain_rev') == SAND_GRAIN_REV
        if os.environ.get("HG_SAND_LEVEL") is None and os.environ.get("HG_SAND_GRAD") is None                 and os.environ.get("HG_SAND_COARSE") is None:
            apply_grain_level(cfg.get('grain_level', SAND_GRAIN_LEVEL_DEFAULT)
                              if _grain_rev_ok else SAND_GRAIN_LEVEL_DEFAULT)
        # ⚠️ **`rough_level` 一律认, 不受版本号影响** —— 那张表的含义从没变过。
        #    第一版跟着 grain 一起作废, 把用户明确选过的值静默降了 33%(r18-1号 查出)。
        if os.environ.get("HG_SURFACE_LEVEL") is None:
            apply_rough_level(cfg.get('rough_level', SURFACE_ROUGH_LEVEL_DEFAULT))

        root = BoxLayout(orientation="vertical", spacing=dp(3),
                         padding=[dp(8), dp(6), dp(8), dp(6)])

        # 顶部色块
        top_colors = BoxLayout(orientation="horizontal", size_hint=(1, None),
                               height=dp(50), spacing=dp(4))
        self.color_btns = []
        for name, base, dark, light in SAND_PRESETS:
            btn = Button(text=name, font_size=sp(15), background_normal="",
                         background_color=(*hex_rgb(base), 1), color=fg_for(base))
            btn.bind(on_press=lambda inst, b=base, d=dark, l=light, n=name:
                     self.on_color(b, d, l, n))
            top_colors.add_widget(btn)
            self.color_btns.append((name, btn))
        root.add_widget(top_colors)

        # 倒计时
        self.time_label = Label(
            text=f"{self.hourglass.duration:.0f}/{self.hourglass.duration:.0f}秒",
            font_size=sp(24), bold=True, size_hint=(1, None), height=dp(40),
            color=(0.2, 0.2, 0.2, 1))
        root.add_widget(self.time_label)

        # 沙漏画布
        root.add_widget(self.hourglass)

        # 底部控件
        bottom = BoxLayout(orientation="horizontal", size_hint=(1, None),
                           height=dp(58), spacing=dp(6))
        self.duration_btn = Button(text=_fmt_duration(self.hourglass.duration),
                                   size_hint=(None, 1), width=dp(82),
                                   font_size=sp(16), bold=True,
                                   background_normal="",
                                   background_color=(0.769, 0.682, 0.557, 1),
                                   color=POPUP_TEXT)
        self.duration_btn.bind(on_press=self.on_duration_picker)
        bottom.add_widget(self.duration_btn)
        self.sound_btn = Button(text="沙沙声",
                                size_hint=(None, 1), width=dp(74), font_size=sp(15),
                                background_normal="",
                                background_color=(*POPUP_GOLD_SEL[:3], 0.92),
                                color=POPUP_TEXT)
        self.sound_btn.bind(on_press=self.on_sound_picker)
        bottom.add_widget(self.sound_btn)
        self._benchmark_runner = None
        self._benchmark_results = []
        self._benchmark_cancelled = False
        self._benchmark_popup = None
        self._benchmark_area = BenchmarkHoldArea(self._open_dev_menu)
        # 长按处印出版本号: 隐藏入口总得让人找得到该按哪儿。
        # BenchmarkHoldArea 是裸 Widget、不做子控件布局, 得手动跟着它铺满。
        hold_label = Label(text=f"v{APP_VERSION}", font_size=sp(11),
                           color=(*POPUP_TEXT[:3], 0.38), halign="center",
                           valign="middle")

        def _fit_hold_label(instance, *_):
            hold_label.pos = instance.pos
            hold_label.size = instance.size
            hold_label.text_size = instance.size

        self._benchmark_area.bind(pos=_fit_hold_label, size=_fit_hold_label)
        _fit_hold_label(self._benchmark_area)
        self._benchmark_area.add_widget(hold_label)
        bottom.add_widget(self._benchmark_area)
        self.start_btn = Button(text="开始", size_hint=(None, 1), width=dp(74),
                                font_size=sp(16), bold=True,
                                background_normal="",
                                background_color=(0.353, 0.620, 0.243, 1), color=(1, 1, 1, 1))
        self.start_btn.bind(on_press=self.on_toggle)
        bottom.add_widget(self.start_btn)
        reset_btn = Button(text="重置", size_hint=(None, 1), width=dp(74), font_size=sp(16))
        reset_btn.bind(on_press=self.on_reset)
        self._reset_btn = reset_btn
        bottom.add_widget(reset_btn)
        root.add_widget(bottom)

        self._mark_selected(color_name)
        self._update_sound_btn()

        # 把整棵 UI 树装进"等效竖屏窗口", 由 LandLayer 横屏时整体旋转 ±90° 铺满
        layer = LandLayer()
        anchor = AnchorLayout(size_hint=(None, None))
        anchor.add_widget(root)
        layer.add_widget(anchor)
        layer._anchor = anchor
        global _LAYER
        _LAYER = layer
        layer.apply_orientation()

        # 运行时方向守卫(只 android): 启动 1s 抢一次 + 常驻 0.7s 重申; 窗口变化由 _frame 立即跟手
        if platform == "android":
            Clock.schedule_once(lambda *_: self._apply_orientation(), 1.0)
            Clock.schedule_interval(self._orient_guard, 0.7)
        Clock.schedule_interval(self._frame, 0.1)
        return layer

    # ---------- 运行时方向守卫(移植自 hengping.md §3.3/§3.4/§8) ----------

    def _apply_orientation(self):
        """以毒攻毒: 用 setRequestedOrientation 重申方向, 顶掉 SDL 启动/回前台的竖屏自报。
        ZUI 只认运行时请求 → 宽屏抢 FULL_SENSOR(10), 瘦长机锁 SENSOR_PORTRAIT(7)。"""
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            act.setRequestedOrientation(10 if _device_is_wide() else 7)
        except Exception:
            pass

    def _orient_guard(self, dt):
        """常驻方向守卫(0.7s): 宽屏仅在横置(rot∈{1,3})时重申 fullSensor; 瘦长机无条件锁竖屏。"""
        if platform != "android":
            return
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            if _device_is_wide():
                rot = act.getWindowManager().getDefaultDisplay().getRotation()
                if rot in (1, 3):                    # 横置才重申; 竖置本就竖构图, 幂等
                    act.setRequestedOrientation(10)
            else:
                act.setRequestedOrientation(7)        # 瘦长机持续锁竖屏
        except Exception:
            pass

    def _frame(self, dt):
        """窗口尺寸变化 → 立即重算反旋转层 + 重申方向(不等守卫周期, 转屏跟手)。"""
        ws = (Window.width, Window.height)
        if ws != getattr(self, "_last_win_size", None):
            self._last_win_size = ws
            layer = _land_layer()
            if layer is not None:
                layer.apply_orientation()
            self._apply_orientation()

    def _selected_color_name(self):
        for n, btn in self.color_btns:
            if btn.text.startswith("● "):
                return n
        return "金沙"

    def _closest_base_and_mult(self, sec):
        best_base, best_mult = 60, 1
        best_diff = float('inf')
        for _, base_val in BASE_PERIODS:
            mult = max(1, min(MULT_SLIDER_MAX, round(sec / base_val)))
            total = base_val * mult
            diff = abs(total - sec)
            # tie-break:同 diff 取更小倍数(更粗基底),避免 50s 显示成"1秒×50倍"
            if diff < best_diff - 1e-9 or (abs(diff - best_diff) <= 1e-9
                                           and mult < best_mult):
                best_diff = diff
                best_base = base_val
                best_mult = mult
        return best_base, best_mult

    def on_duration_picker(self, *_):
        cur = self.hourglass.duration
        init_base, init_mult = self._closest_base_and_mult(cur)
        # mutable closure state
        state = {"base": init_base, "mult": init_mult}

        content = BoxLayout(orientation="vertical", spacing=dp(8),
                            padding=(dp(12), dp(6), dp(12), dp(10)))

        # 预创建 mult_btns/preview_label,避免 lambda 闭包延迟绑定
        # (Android Kivy 2.3.0 对 late binding 时序敏感,曾导致点周期按钮闪退)
        mult_btns = {}
        preview_label = Label(
            text=f"最终周期：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）",
            size_hint=(1, None), height=dp(34),
            color=POPUP_TEXT, font_size=sp(18))

        # --- 基础时间标题 ---
        base_title = Label(text="基础时间:", size_hint=(1, None), height=dp(22),
                           color=POPUP_TEXT, font_size=sp(15),
                           halign="left", valign="middle")
        base_title.bind(size=lambda inst, val: setattr(inst, 'text_size', (val[0], val[1])))
        content.add_widget(base_title)

        # --- 基础周期按钮 ---
        base_grid = BoxLayout(orientation="horizontal", spacing=dp(8),
                              size_hint=(1, None), height=dp(46))
        base_btns = {}
        for label, val in BASE_PERIODS:
            is_sel = (val == init_base)
            btn = Button(text=label, font_size=sp(16),
                         background_normal="",
                         background_color=POPUP_GOLD_SEL if is_sel
                                          else POPUP_UNSEL_BASE,
                         color=POPUP_TEXT)
            btn.bind(on_press=lambda inst, v=val, bb=base_btns, mb=mult_btns,
                              st=state, pl=preview_label:
                     self._on_base_picked(v, bb, st, mb, pl))
            base_btns[val] = btn
            base_grid.add_widget(btn)
        content.add_widget(base_grid)

        # --- 倍数按钮 (两行 BoxLayout, 不用 GridLayout 避免 Android 兼容问题) ---
        MULTIPLIERS = [1, 2, 3, 5, 10, 20, 30, 50, 70, 100]
        mult_title = Label(text="倍数:", size_hint=(1, None), height=dp(22),
                           color=POPUP_TEXT, font_size=sp(15),
                           halign="left", valign="middle")
        mult_title.bind(size=lambda inst, val: setattr(inst, 'text_size', (val[0], val[1])))
        content.add_widget(mult_title)
        for row_vals in [MULTIPLIERS[:5], MULTIPLIERS[5:]]:
            row = BoxLayout(orientation="horizontal", spacing=dp(6),
                            size_hint=(1, None), height=dp(40))
            for m in row_vals:
                is_m = (m == init_mult)
                btn = Button(text=f"{m}倍", font_size=sp(15),
                             background_normal="",
                             background_color=POPUP_GOLD_SEL if is_m
                                              else POPUP_UNSEL_MULT,
                             color=POPUP_TEXT)
                btn.bind(on_press=lambda inst, v=m, mb=mult_btns, st=state,
                                  pl=preview_label:
                         self._on_mult_picked(v, mb, st, pl))
                mult_btns[m] = btn
                row.add_widget(btn)
            content.add_widget(row)

        # --- 对数倍率滑杆(1–1000 倍,与按钮并存但不同步;默认 Kivy 大滑杆样式,
        #     进度条颜色恢复金色值道(与选中态同色的黄色已滑段);
        #     未滑段默认浅灰贴图接近弹窗底色,换暖灰棕贴图 #8a7a68) ---
        slider = Slider(min=0.0, max=1.0,
                        value=math.log(max(1.0, min(float(MULT_SLIDER_MAX),
                                                   float(init_mult)))) / math.log(MULT_SLIDER_MAX),
                        size_hint=(1, None), height=dp(44),
                        value_track=True,
                        value_track_color=(*POPUP_GOLD_SEL[:3], 0.9),
                        background_horizontal=resource_path("ui/slider_track.png"),
                        cursor_image=resource_path("ui/slider_cursor.png"),
                        background_width='8dp',                 # 细线化:未滑段=浅蓝细线,不再 36sp 灰槽
                        value_track_width='8dp')                # 金色线同宽,盖住蓝线左段(已滑=纯金无反边)
        mult_label = Label(text=f"×{init_mult}", size_hint=(None, None),
                           size=(dp(64), dp(36)), font_size=sp(15),
                           color=POPUP_TEXT)
        slider_row = BoxLayout(orientation="horizontal", spacing=dp(8),
                               size_hint=(1, None), height=dp(44))
        slider_row.add_widget(slider)
        slider_row.add_widget(mult_label)
        content.add_widget(slider_row)
        # label 用默认参数固定;滑块值变化不触碰任何按钮高亮(不同步)
        slider.bind(value=lambda inst, v, st=state, pv=preview_label,
                    ml=mult_label: self._on_slider_moved(v, st, pv, ml))

        # --- 预览 (预创建,此处 add 到正确位置) ---
        content.add_widget(preview_label)

        # --- 运行中警告 ---
        if self.hourglass.running:
            warn_label = Label(text="修改周期将重置当前进度",
                               size_hint=(1, None), height=dp(26),
                               color=(0.85, 0.45, 0.15, 1), font_size=sp(14))
            content.add_widget(warn_label)

        # --- 取消 + 确定 按钮行 ---
        btn_row = BoxLayout(orientation="horizontal", spacing=dp(10),
                            size_hint=(1, None), height=dp(54))
        cancel_btn = Button(text="取消", font_size=sp(16),
                            background_normal="",
                            background_color=POPUP_CANCEL_BG,
                            color=POPUP_TEXT)
        btn_row.add_widget(cancel_btn)
        confirm_btn = Button(text="确定", font_size=sp(16), bold=True,
                             background_normal="",
                             background_color=POPUP_GOLD_SEL,
                             color=POPUP_TEXT)
        btn_row.add_widget(confirm_btn)
        content.add_widget(btn_row)

        popup = _SandBgPopup(title="选择周期", content=content,
                             size_hint=(0.88, None), height=dp(460),
                             auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(19)
        popup.separator_color = (*POPUP_GOLD_SEL[:3], 0.25)
        popup.title_color = (1, 1, 1, 1)
        # content 自适应内容高度,不撑满 _container;顶部对齐紧贴 separator
        content.size_hint_y = None
        content.pos_hint = {'top': 1}
        content.bind(minimum_height=content.setter('height'))
        # Popup 高度自适应 content 高度(+ title bar/separator/padding 余量)
        def _adjust_popup_height(inst, val):
            popup.height = val + dp(85)
        content.bind(minimum_height=_adjust_popup_height)

        cancel_btn.bind(on_press=popup.dismiss)
        confirm_btn.bind(on_press=lambda inst, st=state, p=popup:
                         self._pick_duration(st['base'] * st['mult'], p))
        popup.open()

    def _on_base_picked(self, val, base_btns, state, mult_btns, preview_label):
        state["base"] = val
        active_color = POPUP_GOLD_SEL
        inactive_color = POPUP_UNSEL_BASE
        for v, btn in base_btns.items():
            sel = (v == val)
            btn.background_color = active_color if sel else inactive_color
            btn.color = POPUP_TEXT
        preview_label.text = f"最终周期：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）"

    def _on_mult_picked(self, val, mult_btns, state, preview_label):
        state["mult"] = val
        active_color = POPUP_GOLD_SEL
        inactive_color = POPUP_UNSEL_MULT
        for m, btn in mult_btns.items():
            sel = (m == val)
            btn.background_color = active_color if sel else inactive_color
            btn.color = POPUP_TEXT
        preview_label.text = f"最终周期：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）"

    def _on_slider_moved(self, v, state, preview_label, mult_label):
        """对数滑杆:10^(3t) 向上取整;只更新 state/预览/×N 标签,不动按钮高亮。"""
        m = _mult_from_slider(v)
        if m == state['mult']:
            return
        state['mult'] = m
        mult_label.text = f"×{m}"
        total = state['base'] * m
        preview_label.text = f"最终周期：{_fmt_duration(total)}（{total:.0f}秒）"

    def _pick_duration(self, sec, popup):
        popup.dismiss()
        if self.hourglass.set_duration(sec):
            self.hourglass.save_config(self._selected_color_name())
        self.duration_btn.text = _fmt_duration(self.hourglass.duration)
        self.on_run_state_changed()

    def on_toggle(self, *_):
        if self._benchmark_active():
            self._benchmark_runner.cancel()
            return
        self.hourglass.toggle()
        self.on_run_state_changed()

    def on_reset(self, *_):
        self.hourglass.reset()
        self.on_run_state_changed()

    def on_run_state_changed(self):
        if self._benchmark_active():
            self.start_btn.text = "取消"
            self.start_btn.background_color = POPUP_CONFIRM
            return
        if self.hourglass.running:
            self.start_btn.text = "暂停"
            self.start_btn.background_color = (0.851, 0.557, 0.243, 1)
        else:
            self.start_btn.text = "开始"
            self.start_btn.background_color = (0.353, 0.620, 0.243, 1)

    def _benchmark_active(self):
        return self._benchmark_runner is not None and self._benchmark_runner.active

    def _close_dev_menu(self):
        if self._dev_popup is not None:
            self._dev_popup.dismiss()

    def _open_dev_menu(self, *_):
        """长按**版本号**进的隐藏菜单: 沙子浓度滑块 + **沙体颗粒六档** + **沙面起伏六档** + 性能测试。

        两个六档是 2026-10-05 加的: 用户在对照图里逐档比过, 选定 D/D 为出厂默认,
        并要求"加入设置"以便自己改。它们是**离散档位**不是滑块 —— 换沙体颗粒要重烘
        一张 512² 材质(约 17ms), 连续拖动会卡; 沙面起伏很轻但也没必要连续。

        2026-10-04 用户要求: 原来沙子材质是「平色/淡/标准/浓」四档按钮, 改成连续滑块;
        并加一个玻璃反光滑块。

        ⚠️ 沙子浓度**不能每帧烘正式材质**: 实测 512² 生成 17.6ms + 上传 ⇒ 拖动必卡。
        所以拖动中只烘 `SAND_PREVIEW_SIZE`(128², 4.2ms) 的低分预览, 停手 0.35s 才烘正式版。
        玻璃反光没这个问题(只重建十几条静态 Line), 但**落盘**一样要等停手 —— 否则拖动
        每帧写一次配置文件。
        """
        if self._dev_popup is not None or self._benchmark_active():
            return
        hg = self.hourglass
        content = BoxLayout(orientation="vertical", spacing=dp(6),
                            padding=[dp(16), dp(8), dp(16), dp(12)],
                            size_hint=(1, None))
        content.bind(minimum_height=content.setter("height"))

        def make_row(text, value_text):
            row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                            height=dp(24))
            name = Label(text=text, font_size=sp(15), color=POPUP_TEXT,
                         halign="left", valign="middle")
            name.bind(size=lambda inst, s: setattr(inst, "text_size", s))
            val = Label(text=value_text, font_size=sp(15), color=POPUP_TEXT,
                        size_hint=(None, None), size=(dp(78), dp(24)),
                        halign="right", valign="middle")
            val.bind(size=lambda inst, s: setattr(inst, "text_size", s))
            row.add_widget(name)
            row.add_widget(val)
            return row, val

        def make_slider(value):
            return Slider(min=0.0, max=100.0, value=value,
                          size_hint=(1, None), height=dp(40),
                          value_track=True,
                          value_track_color=(*POPUP_GOLD_SEL[:3], 0.9),
                          background_horizontal=resource_path("ui/slider_track.png"),
                        cursor_image=resource_path("ui/slider_cursor.png"),
                          background_width='8dp', value_track_width='8dp')

        def make_levels(title, labels, current, on_pick, fmt=None):
            """一行标题 + 一行档位按钮(选中金色)。返回 (容器, 刷新高亮, 按钮表)。

            ⚠️ 按钮的 lambda **必须用默认参数绑住 `lb`** —— 闭包延迟绑定会让所有按钮
               都指向最后一档(Android Kivy 2.3.0 上这个坑踩过, 见周期弹窗)。
            """
            box = BoxLayout(orientation="vertical", size_hint=(1, None),
                            height=dp(54), spacing=dp(2))
            head = Label(text=title, font_size=sp(15), color=POPUP_TEXT,
                         halign="left", valign="middle",
                         size_hint=(1, None), height=dp(22))
            head.bind(size=lambda inst, s: setattr(inst, "text_size", s))

            def set_head(lb):
                head.text = title if fmt is None else "%s   %s" % (title, fmt(lb))
            set_head(current)
            box.add_widget(head)
            row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                            height=dp(30), spacing=dp(4))
            btns = {}

            def refresh(sel):
                for lb, b in btns.items():
                    b.background_color = (POPUP_GOLD_SEL if lb == sel
                                          else POPUP_UNSEL_MULT)
                set_head(sel)

            for lb in labels:
                b = Button(text=lb, font_size=sp(14), bold=True, background_normal="",
                           color=POPUP_TEXT, size_hint=(1, 1))
                b.bind(on_press=lambda inst, l=lb: on_pick(l))
                btns[lb] = b
                row.add_widget(b)
            refresh(current)
            box.add_widget(row)
            return box, refresh, btns

        # ---- 沙子浓度 ----
        sand_row, sand_label = make_row(
            "沙子浓度", "%.2f" % SAND_MATERIAL_GRAIN)
        content.add_widget(sand_row)
        sand_slider = make_slider(min(100.0, SAND_MATERIAL_GRAIN / SAND_GRAIN_MAX * 100.0))
        content.add_widget(sand_slider)

        pending = {"grain": None, "token": 0}

        def commit_sand(*_):
            """停手后把预览换成正式分辨率, 并落盘。"""
            Clock.unschedule(commit_sand)
            grain = pending.pop("grain", None)
            if grain is None:
                return
            if hg.set_sand_grain(grain):
                hg.save_config(self._selected_color_name())

        def on_sand(_slider, value):
            grain = value / 100.0 * SAND_GRAIN_MAX
            sand_label.text = "%.2f" % grain
            now = time.perf_counter()
            if now - pending.get("last", 0.0) >= 0.08:      # 预览节流 80ms
                pending["last"] = now
                hg.set_sand_grain(grain, preview=True)
            pending["grain"] = grain
            Clock.unschedule(commit_sand)
            Clock.schedule_once(commit_sand, 0.35)

        sand_slider.bind(value=on_sand)
        self._dev_sliders = (sand_slider,)   # 供测试/自检取用

        content.add_widget(Widget(size_hint=(1, None), height=dp(8)))

        # ---- 沙体颗粒 六档 (A-F, 出厂默认 D) ----
        # 换档要重烘一张 512² 材质(约 17ms) ⇒ **只能按键触发, 不能做成连续滑块**。
        def pick_grain(lb):
            if hg.set_grain_level(lb):
                refresh_grain(lb)
                hg.save_config(self._selected_color_name())

        grain_box, refresh_grain, grain_btns = make_levels(
            "沙体颗粒", [lb for lb, _g, _c in SAND_GRAIN_LEVELS],
            current_grain_level(), pick_grain,
            fmt=lambda lb: "颗粒 %d 倍粗" % _grain_level(lb)[1])
        content.add_widget(grain_box)

        # ---- 沙面起伏 六档 (A-F, 出厂默认 D) ----
        # 这个只是换 65 个浮点 + 一次 redraw, 很轻。
        def pick_rough(lb):
            if hg.set_rough_level(lb):
                refresh_rough(lb)
                hg.save_config(self._selected_color_name())

        rough_box, refresh_rough, rough_btns = make_levels(
            "沙面起伏", [lb for lb, _f in SURFACE_ROUGH_LEVELS],
            current_rough_level(), pick_rough,
            fmt=lambda lb: "约 %.1f 像素" % (_rough_level(lb) * 2 * 445.63))
        content.add_widget(rough_box)
        self._dev_level_btns = {"grain": grain_btns, "rough": rough_btns}

        content.add_widget(Widget(size_hint=(1, None), height=dp(6)))
        bench = Button(text="性能测试", font_size=sp(16), bold=True, background_normal="",
                       background_color=POPUP_CONFIRM, color=POPUP_TEXT_WHITE,
                       size_hint=(1, None), height=dp(50))
        bench.bind(on_press=lambda *_: (self._close_dev_menu(), self.on_benchmark()))
        content.add_widget(bench)

        def close_menu(*_):
            # 拖动中直接点「确定」⇒ 先把没落定的预览烘成正式版, 再关
            if pending.get("grain") is not None:
                commit_sand()
            hg.save_config(self._selected_color_name())
            self._close_dev_menu()

        close = Button(text="确定", font_size=sp(16), background_normal="",
                       background_color=POPUP_CANCEL_BG, color=POPUP_TEXT,
                       size_hint=(1, None), height=dp(46))
        close.bind(on_press=close_menu)
        content.add_widget(close)

        # ⚠️ **高度改成"贴着内容 + 贴底"**（用户 2026-10-05: 「少一个预览功能?」）——
        #    原来高度写死 dp(560), 内容没那么高 ⇒ 标题下面一大块空白;
        #    而且弹窗居中 ⇒ **把沙漏整个盖住, 改完档位当场看不见效果 = 没有预览**。
        #    现在: 高度 = 内容需要的高度 + 标题栏; 靠**贴着底边**摆 ⇒
        #    上半屏的沙漏一直露着, 点一下档位**立刻能看到**, 这就是预览。
        popup_height = dp(360)   # 先给个初值, open 前会被 content 的 minimum_height 覆盖
        # ⚠️ 用户 2026-10-05: 「这个界面是不是没有标题?」—— 原来标题栏只写 `v1.105`,
        #    第一次进来的人看不出这是什么界面。加界面名, 版本号留在后面(长按版本号是入口)。
        popup = _SandBgPopup(title=f"沙漏设置   v{APP_VERSION}", content=content,
                             size_hint=(0.94, None), height=popup_height,
                             auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(17)
        self._dev_popup = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_dev_popup", None))
        # 高度跟着内容长(同周期/音效弹窗那条已跑过真机的链路)
        content.bind(minimum_height=lambda inst, val: setattr(popup, "height", val + dp(78)))
        # **贴底**: 留出上半屏给沙漏当预览
        popup.pos_hint = {"center_x": 0.5, "y": 0.015}
        # ⚠️ **把模态遮罩调到几乎透明** —— 这个界面要当"预览"用, 而 Kivy ModalView 默认那层
        #    暗底会把沙漏的**颜色和质感压失真**(r17-1号 实测过: 55% 遮罩下蓝沙量到
        #    (173,198,211), 真实是 (75,140,192))。留 10% 只为跟卡片分层。
        # ⚠️ **已知未解决（E3，不许当成修好了）**: 弹窗那层**整窗暗底**关不掉。
        #    试过 `background_color=(0,0,0,0)` 与 `background=""` **两次, 都无效**
        #    （实测: 顶栏金沙 (54,41,24) vs 正常 (217,163,96); 背景 (76,74,69) vs 奶油 (253,246,227)）。
        #    而 Kivy 2.3 的文档说 `background_color` 只影响**控件自身**的背景。
        #    ⇒ 沙漏虽然露在弹窗上方（结构上能当预览用）, 但**颜色是被压暗的, 不能用来判色/判质感** ——
        #    这正是 r17-1号 警告过的那个陷阱（55% 遮罩下蓝沙量到 (173,198,211), 真实 (75,140,192)）。
        #    下一步要么找到真正的暗底来源, 要么改成"弹窗里放一个小预览"。
        popup.open()

    def on_benchmark(self, *_):
        if self._benchmark_active() or self._benchmark_popup is not None:
            return
        content = BoxLayout(orientation="vertical", spacing=dp(8),
                            padding=(dp(8), dp(6), dp(8), dp(6)))
        scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False)
        rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        rows.bind(minimum_height=rows.setter("height"))
        scroll.add_widget(rows)
        content.add_widget(scroll)
        result_by_period = {r["period"]: r for r in self._benchmark_results}
        for period in PERIODS:
            result = result_by_period.get(period)
            text = format_benchmark_result(period, result)
            label = Label(text=text, font_size=sp(14), color=POPUP_TEXT,
                          size_hint=(1, None), halign="left", valign="top")
            label.bind(width=lambda inst, width: setattr(inst, "text_size", (width, None)))
            label.bind(texture_size=lambda inst, size: setattr(inst, "height", size[1] + dp(8)))
            rows.add_widget(label)
            if result and result.get("frame_trace"):
                rows.add_widget(BenchmarkFrameChart(
                    result, size_hint=(1, None), height=dp(112)))
        if self._benchmark_cancelled:
            rows.add_widget(Label(text="测试已取消", color=POPUP_CONFIRM,
                                  font_size=sp(14), size_hint_y=None, height=dp(26)))
        commands = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        run_btn = Button(text="重新测试" if self._benchmark_results else "开始测试",
                         font_size=sp(16), background_normal="",
                         background_color=POPUP_GOLD_SEL, color=POPUP_TEXT)
        close_btn = Button(text="关闭", font_size=sp(16), background_normal="",
                           background_color=POPUP_CANCEL_BG, color=POPUP_TEXT)
        save_btn = Button(text="保存文件", font_size=sp(16), background_normal="",
                          background_color=POPUP_UNSEL_BASE, color=POPUP_TEXT,
                          disabled=not self._benchmark_results)
        save_btn.bind(on_press=self._save_benchmark_results)
        self._benchmark_save_btn = save_btn
        commands.add_widget(run_btn)
        commands.add_widget(save_btn)
        commands.add_widget(close_btn)
        content.add_widget(commands)
        self._benchmark_hint = Label(text="", font_size=sp(11), color=POPUP_TEXT,
                                     size_hint_y=None, height=dp(30), halign="center",
                                     valign="middle")
        self._benchmark_hint.bind(
            size=lambda inst, size: setattr(inst, "text_size", size))
        content.add_widget(self._benchmark_hint)
        popup_height = min(dp(600 if self._benchmark_results else 360),
                           max(Window.width, Window.height) * 0.85)
        popup = _SandBgPopup(title=f"Benchmark v{APP_VERSION}", content=content, size_hint=(0.94, None),
                            height=popup_height,
                            auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(19)
        self._benchmark_popup = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_benchmark_popup", None))
        close_btn.bind(on_press=lambda *_: popup.dismiss())
        run_btn.bind(on_press=lambda *_: self._start_benchmark(popup))
        popup.open()

    def _benchmark_save_dir(self):
        """保存目录:Android 优先外部私有目录(adb / 文件管理器免 root 可取),
        取不到再退回内部 user_data_dir;桌面仍写工程目录下的 benchmark_logs。"""
        if platform == "android":
            try:
                from jnius import autoclass
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                external = activity.getExternalFilesDir(None)
                if external is not None:
                    return os.path.join(str(external.getAbsolutePath()),
                                        "benchmark_logs")
            except Exception as exc:
                print(f"External dir unavailable: {exc}")
            return os.path.join(self.user_data_dir, "benchmark_logs")
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "benchmark_logs")

    @staticmethod
    def _publish_via_mediastore(filename, data):
        """路线 B: 走 MediaStore 把文件登记进系统的「下载」集合(API 29+, 免权限)。"""
        from jnius import autoclass
        if autoclass("android.os.Build$VERSION").SDK_INT < 29:
            return None
        MediaColumns = autoclass("android.provider.MediaStore$MediaColumns")
        Downloads = autoclass("android.provider.MediaStore$Downloads")
        ContentValues = autoclass("android.content.ContentValues")
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        values = ContentValues()
        values.put(MediaColumns.DISPLAY_NAME, filename)
        values.put(MediaColumns.MIME_TYPE, "text/plain")
        values.put(MediaColumns.RELATIVE_PATH, "Download")
        resolver = activity.getContentResolver()
        uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
        if uri is None:
            return None
        stream = resolver.openOutputStream(uri)
        if stream is None:
            return None
        try:
            stream.write(data)
            stream.flush()
        finally:
            stream.close()
        return "/storage/emulated/0/Download/" + filename

    def _publish_to_download(self, filename, text):
        """再把日志往公共 Download 目录放一份**同名同内容**的副本(用户/文件管理器直接能取)。
        返回落地路径; 两条路线都失败返回 None —— 私有目录那份已经写好了, 不影响保底。"""
        if platform != "android":
            return None
        data = text.encode("utf-8")
        # 路线 A: 直接写文件。Android 11+ 的 FUSE 允许在 Download 里**新建**文件。
        for directory in ("/storage/emulated/0/Download", "/sdcard/Download"):
            try:
                os.makedirs(directory, exist_ok=True)
                path = os.path.join(directory, filename)
                with open(path, "wb") as stream:
                    stream.write(data)
                return path
            except OSError as exc:
                print(f"Download direct write failed ({directory}): {exc}")
        # 路线 B: MediaStore。jnius 抛的不一定是 OSError, 这里必须宽捕。
        try:
            return self._publish_via_mediastore(filename, data)
        except Exception as exc:
            print(f"MediaStore publish failed: {exc}")
        return None

    def _save_benchmark_results(self, button):
        """把这一轮的全部记录写成文件(逐帧 trace + 分位/超阈值/残差/分桶表),
        并再往公共 Download 目录放一份同名副本 —— 私有目录是保底, Download 是给人取的。"""
        if not self._benchmark_results:
            return
        try:
            path = save_benchmark_log(self._benchmark_save_dir(),
                                      self._benchmark_results,
                                      self._benchmark_cancelled)
        except OSError as exc:
            print(f"Benchmark save failed: {exc}")
            button.text = "保存失败"
            return
        self._benchmark_log_path = path
        print(f"Benchmark saved: {path}")
        button.text = "已保存"
        published = self._publish_to_download(
            os.path.basename(path),
            benchmark_log_text(self._benchmark_results, self._benchmark_cancelled))
        print(f"Benchmark published: {published or '(Download 不可写, 只留私有目录)'}")
        hint = getattr(self, "_benchmark_hint", None)
        if hint is not None:
            hint.text = f"已写入 {published or path}"

    def _benchmark_controls(self, disabled):
        for control in [self.duration_btn, self.sound_btn, self._reset_btn] + [
                btn for _name, btn in self.color_btns]:
            control.disabled = disabled

    def _start_benchmark(self, popup):
        if self._benchmark_active():
            return
        popup.dismiss()
        self._benchmark_results = []
        self._benchmark_cancelled = False
        self._benchmark_controls(True)
        self._benchmark_runner = BenchmarkRunner(
            self.hourglass, self._benchmark_case, self._benchmark_finished)
        self._benchmark_runner.start()
        self.on_run_state_changed()

    def _benchmark_case(self, period, index):
        self.duration_btn.text = f"{period} 秒"
        self.on_run_state_changed()

    def _benchmark_finished(self, results, cancelled):
        self._benchmark_results = results
        self._benchmark_cancelled = cancelled
        directory = (self.user_data_dir if platform == "android"
                     else os.path.dirname(os.path.abspath(__file__)))
        try:
            self._benchmark_log_path = save_benchmark_log(
                os.path.join(directory, "benchmark_logs"), results, cancelled)
            print(f"Benchmark log: {self._benchmark_log_path}")
        except OSError as exc:
            self._benchmark_log_path = None
            print(f"Benchmark log failed: {exc}")
        self._benchmark_controls(False)
        self.duration_btn.text = _fmt_duration(self.hourglass.duration)
        self.update_time(max(0, self.hourglass.duration - self.hourglass.elapsed),
                         self.hourglass.duration)
        self.on_run_state_changed()
        if not getattr(self, "_benchmark_closing", False):
            self.on_benchmark()

    def on_sound_picker(self, *_):
        """音效选择弹窗:点击即切换(生效但**不关窗**,可连续试听),当前项金色高亮,
        「确定」关闭弹窗(每行 3 个,自动分行)。"""
        cur = self.hourglass.sound_name
        btns = {}   # label → btn,点击后刷新弹窗内高亮

        content = BoxLayout(orientation="vertical", spacing=dp(8),
                            padding=(dp(12), dp(10), dp(12), dp(10)))
        for i in range(0, len(SOUND_EFFECTS), 3):
            row = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint=(1, None), height=dp(54))
            for label, _path in SOUND_OPTIONS[i:i + 3]:
                is_sel = (label == cur)
                btn = Button(text=label, font_size=sp(16),
                             background_normal="",
                             background_color=POPUP_GOLD_SEL if is_sel
                                              else POPUP_UNSEL_BASE,
                             color=POPUP_TEXT)
                # lambda 闭包延迟绑定:label/btns 用默认参数固定(Android Kivy 时序敏感)
                btn.bind(on_press=lambda inst, lb=label, bs=btns:
                         self._on_sound_picked(lb, bs))
                btns[label] = btn
                row.add_widget(btn)
            content.add_widget(row)

        # 后端异常提示:正常(audiotrack/winsound)时文案为空、高度 0,界面上看不见。
        # 错误串可能很长,必须按宽度换行 —— 单行会被裁掉最关键的开头(实测只剩 "state=2")
        diag = Label(text="", font_size=sp(12), color=(0.62, 0.23, 0.16, 0.95),
                     halign="center", valign="top", size_hint=(1, None), height=0)
        diag.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
        # 换行后高度会变,跟着 texture_size 走(宽度绑定晚于首次测量,只算一次会按单行算矮)
        diag.bind(texture_size=lambda inst, ts:
                  setattr(inst, "height", ts[1] if inst.text else 0))
        self._sound_diag_label = diag
        content.add_widget(diag)
        self._refresh_sound_diag()

        # 「确定」= 唯一关闭出口(音效已在点击选项时即时生效,这里只收尾)
        # 暗红底:与选项选中金色区分,避免误看成"选中的音效项"
        confirm_btn = Button(text="确定", font_size=sp(16), bold=True,
                             background_normal="",
                             background_color=POPUP_CONFIRM,
                             color=POPUP_TEXT_WHITE,
                             size_hint=(1, None), height=dp(54))
        content.add_widget(confirm_btn)

        self._sound_popup = _SandBgPopup(title="选择音效", content=content,
                                         size_hint=(0.88, None), height=dp(300),
                                         auto_dismiss=False)
        self._sound_popup.title_align = "center"
        self._sound_popup.title_size = sp(19)
        self._sound_popup.separator_color = (*POPUP_GOLD_SEL[:3], 0.25)
        self._sound_popup.title_color = (1, 1, 1, 1)
        # 内容高度自适应(同周期弹窗的成功链路)
        content.size_hint_y = None
        content.pos_hint = {'top': 1}
        content.bind(minimum_height=content.setter('height'))
        popup = self._sound_popup    # 局部引用:闭包持有,弹窗关闭后布局事件仍安全
        # 返回键(见 `_SandBgPopup._handle_keyboard`)会直接 dismiss ⇒ 引用必须挂在
        # on_dismiss 上清, 不能只靠"确定"按钮那条路, 否则 `_sound_popup` 留下来是脏的。
        # ⚠️ **必须返回 None**: Kivy `ModalView.dismiss()` 里有一句
        #    `if self.dispatch('on_dismiss') is True: return` —— 回调返回 True 会把
        #    **整个关闭动作取消掉**(弹窗永远关不上)。元组表达式的 lambda 恰好是踩这个坑
        #    的写法(第一个版本就是, 实测音效弹窗按返回/按确定都关不掉, 2026-10-05)。
        def _forget_sound(*_a):
            self._sound_popup = None
            self._sound_diag_label = None
        popup.bind(on_dismiss=_forget_sound)
        confirm_btn.bind(on_press=lambda inst, p=popup: self._close_sound_picker(p))

        def _adjust_popup_height(inst, val):
            popup.height = val + dp(85)
        content.bind(minimum_height=_adjust_popup_height)
        self._sound_popup.open()

    def _on_sound_picked(self, label, btns):
        """点击选项:立即切换生效,**弹窗不关闭**(可连续试听);高亮跟随点击项。"""
        if self.hourglass._set_sound(label):
            self.hourglass.save_config(self._selected_color_name())
            self._update_sound_btn()
        for lb, btn in btns.items():
            btn.background_color = POPUP_GOLD_SEL if lb == label else POPUP_UNSEL_BASE
            btn.color = POPUP_TEXT
        self._refresh_sound_diag()

    def _refresh_sound_diag(self):
        """没问题→空文案 + 高度 0(不占位);有问题→显示后端与错误串。"""
        lb = getattr(self, "_sound_diag_label", None)
        if lb is None:
            return
        lb.text = self.hourglass.sound_problem_desc()
        if not lb.text:
            lb.height = 0
            return
        lb.texture_update()                      # 立刻拿到换行后的真实高度
        lb.height = max(dp(20), lb.texture_size[1])

    def _close_sound_picker(self, popup):
        """「确定」:关闭音效弹窗(音效已在点击选项时生效,这里只收尾)。"""
        self._sound_popup = None
        self._sound_diag_label = None
        popup.dismiss()

    def _update_sound_btn(self):
        """主按钮显示当前音效名(沙沙声/水流声/风声/钟表声/无声音);
        静音保持暖灰样式,有声金色。"""
        self.sound_btn.text = self.hourglass.sound_name
        if self.hourglass.sound_name == SILENT_NAME:
            self.sound_btn.background_color = (0.718, 0.686, 0.643, 1)
        else:
            self.sound_btn.background_color = (*POPUP_GOLD_SEL[:3], 0.92)
        self.sound_btn.color = POPUP_TEXT

    def on_color(self, base, dark, light, name):
        self.hourglass.set_sand_color(base, dark, light)
        self._mark_selected(name)
        self.hourglass.save_config(name)

    def _mark_selected(self, name):
        for n, btn in self.color_btns:
            btn.text = ("● " + n) if n == name else n

    def update_time(self, remaining_sec, duration):
        self.time_label.text = _fmt_countdown_pair(remaining_sec, duration)

    def completion_popup_allowed(self, duration):
        """完成提示弹不弹: 基准测试期间不弹; 周期 < COMPLETION_POPUP_MIN 也不弹。"""
        return (not self._benchmark_active()) and duration >= COMPLETION_POPUP_MIN

    def on_completed(self, duration):
        """沙漏流尽:弹窗报时长。auto_dismiss=False —— 不点不关(用户明确要求)。"""
        if self._benchmark_active():
            return                       # 基准测试不显示结果弹窗(沿用既有约定)
        if self._completion_popup is not None:
            return
        content = BoxLayout(orientation="vertical", spacing=dp(14),
                            padding=[dp(18), dp(10), dp(18), dp(18)],
                            size_hint=(1, None))
        content.bind(minimum_height=content.setter("height"))

        caption = Label(text="沙漏计时已完成", font_size=sp(15),
                        color=POPUP_TEXT_SUB, size_hint=(1, None), height=dp(24))
        content.add_widget(caption)

        big = Label(text="用时：" + _fmt_duration_cn(duration), font_size=sp(28), bold=True,
                    color=POPUP_GOLD_SEL, size_hint=(1, None), height=dp(52),
                    halign="center", valign="middle")
        big.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
        # 加了"用时："前缀后长周期(如"用时：100小时59分59秒")会折行, 高度得跟着文字长
        big.bind(texture_size=lambda inst, ts: setattr(
            inst, "height", max(dp(52), ts[1])))
        content.add_widget(big)

        rule = Widget(size_hint=(1, None), height=dp(2))
        with rule.canvas:
            Color(*POPUP_GOLD_SEL[:3], 0.35)
            rule_rect = Rectangle(pos=rule.pos, size=rule.size)
        rule.bind(pos=lambda inst, v: setattr(rule_rect, "pos", v),
                  size=lambda inst, v: setattr(rule_rect, "size", v))
        content.add_widget(rule)

        content.add_widget(Widget(size_hint=(1, None), height=dp(4)))

        close_btn = Button(text="确定", font_size=sp(16), bold=True,
                           background_normal="",
                           background_color=POPUP_CONFIRM,
                           color=POPUP_TEXT_WHITE,
                           size_hint=(1, None), height=dp(52))
        content.add_widget(close_btn)

        popup = _SandBgPopup(title="计时完成", content=content,
                             size_hint=(0.86, None), height=dp(330),
                             auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(19)
        popup.separator_color = (*POPUP_GOLD_SEL[:3], 0.25)
        popup.title_color = (1, 1, 1, 1)
        content.bind(minimum_height=lambda inst, val:
                     setattr(popup, "height", val + dp(85)))
        close_btn.bind(on_press=lambda inst, p=popup: self._close_completion(p))
        # 同音效弹窗: 返回键会绕过"确定"直接 dismiss, 引用挂在 on_dismiss 上清
        popup.bind(on_dismiss=lambda *_: setattr(self, "_completion_popup", None))
        self._completion_popup = popup
        popup.open()

    def _close_completion(self, popup):
        self._completion_popup = None
        popup.dismiss()

    def on_pause(self):
        if self._benchmark_active():
            self._benchmark_runner.cancel()
        self.hourglass._stop_completion_sound()
        # ⚠️ **被系统挂到后台时必须停背景循环音**(r6-3号 实测, 2026-10-05):
        #    暂停 / 重置 / 漏完 / on_stop 四条路径都停过这条音, **只有"挂后台"这一条没停**
        #    ⇒ 用户按 HOME 去回微信, 沙沙声会跟着他走。
        #    实测: 3000 秒档离开 334 秒全程在播; 10 秒档在 t=44.8s 仍在播(超计时终点 15 秒);
        #    **熄屏同样不停**。证据是系统级的: `dumpsys media.audio_flinger` 里该轨的
        #    Server FrmCnt 15.14 秒涨 735744 帧 ≈ 48600 帧/秒 = 该 wav 原生采样率
        #    ⇒ **是在播, 不是空挂一个轨道**。对照组干净: 暂停=stopped / 漏完=轨道不存在 /
        #    回前台=立刻 stopped, 说明这是漏了一条路径, 不是设计。
        self.hourglass._stop_sound()
        return True

    def on_stop(self):
        self._benchmark_closing = True
        if self._benchmark_active():
            self._benchmark_runner.cancel()
        self.hourglass._stop_completion_sound()
        if self.hourglass._completion_spoken is not None:
            self.hourglass._completion_spoken.close()
            self.hourglass._completion_spoken = None
        if self.hourglass._completion_sound is not None:
            self.hourglass._completion_sound.close()
        self.hourglass._stop_sound()

    def on_resume(self):
        if platform == "android":
            self.hourglass._stop_completion_sound()
            if self.hourglass._completion_spoken is not None:
                self.hourglass._completion_spoken.close()
                self.hourglass._completion_spoken = None
            if self.hourglass._completion_sound is not None:
                self.hourglass._completion_sound.close()
            self.hourglass._completion_sound = self.hourglass._make_completion_sound()
            # 回前台把背景循环音接回去(与 on_pause 的停成对; 静音时 _sound 为 None, 空操作)
            if self.hourglass.running:
                self.hourglass._play_sound()
            # SDL 回前台会重报方向(可能把 fullSensor 覆盖回竖屏), 再抢一次话语权
            self._apply_orientation()
            # 同理: 回前台可能把窗口的刷新率档位刷回系统默认, 再要一次最高档
            self._apply_max_refresh_rate()
            layer = _land_layer()
            if layer is not None:
                layer.apply_orientation()
        return True


# ---------- 沙流渲染器(仅安卓启用批处理) ----------
#
# Kivy 的 Line 在 width>1 时**不用 glLineWidth**, 而是**每条线**自建一个带 10 段
# 圆头帽的三角网格。峰值约 2500 条 width=2 的粒子线 = 数千次网格提交 + 每颗粒的
# Python 提交成本;桌面 GL 余量大(实测帧率对粒子数几乎不变: 63.2→63.3fps),
# 所以**在 PC 上永远调不出这个问题**, 只有 GLES 暴露 ——
# 实测帧耗时 ≈ 2.5ms + 10.05µs × 在途粒子数, 2829 颗 → 30.9ms/帧(32fps),
# 表现为"一整段周期帧率都上不去"。
# 批处理把同色同线宽的粒子并进一个 Mesh, 端点走顶点纹理;几何(含圆头帽)与
# 逐 Line 版一致, 实测画面逐像素同一。同机同配置: 15s 47.9→65.4fps, Canvas 8.46→3.06ms。
FLOW_RENDERER = os.environ.get("HG_FLOW_RENDERER") or "texture"   # line | batch | gpu | texture
# ⚠️ HG_FLOW_RENDERER 只给**诊断**用: 桌面平时不装这套渲染器(PC 的 GL 余量大, 测不出
# 它的代价), 但"每帧触发多少次索引重建"这个**计数**是设备无关的, 桌面量得准。
# 不设这个变量时行为与以前完全一致。


def _install_flow_renderer(widget_class):
    """装载批处理沙流渲染器; 任何不满足都退回原 Line 池, 不给出沙制造风险。

    注意 ①: 打包进 APK 的只有 tools/*.pyc, 所以按 sys.path + import 装载 ——
            源码缺失时 CPython 会走 sourceless import, 不能按 .py 路径装载。
    注意 ②: 纹理方案的报错发生在**画布构建时**(不是 install() 时), 外面包不住,
            所以这里先自己探测顶点纹理采样能力, 不支持就直接不装。
    """
    if FLOW_RENDERER == "line":
        return
    import importlib
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    target = {"batch": "flow_batch_experiment",
              "gpu": "flow_gpu_experiment",
              "texture": "flow_texture_experiment"}[FLOW_RENDERER]
    if target == "flow_texture_experiment":
        from kivy.graphics.opengl import (
            glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)
        if glGetIntegerv(GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)[0] < 1:
            print("no vertex texture sampling; keeping line pool")
            return
    if target != "flow_batch_experiment":
        importlib.import_module("flow_batch_experiment")
    importlib.import_module(target).install(widget_class)


if platform == "android" or os.environ.get("HG_FLOW_RENDERER"):
    try:
        _install_flow_renderer(HourglassWidget)
    except Exception as exc:
        print(f"flow renderer {FLOW_RENDERER} unavailable, using line pool: {exc}")


if __name__ == "__main__":
    HourglassApp().run()
