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
from collections import deque
from bisect import bisect_left as _bisect_left
from bisect import bisect_right, insort as _insort   # 面积表求逆 / 沙堆节点插入
import sys
import time
import wave
import json


# 沙流粒子的并行数组字段(见 NUMPY_PLAN.md)。numpy 缺失时整条向量化路径关闭,
# 自动退回 update_particles 里的原标量循环 —— 不给沙漏制造风险。
_P_FIELDS = ("px", "py", "pvy", "pxo", "pwp", "pwa", "psz", "ptl", "pli", "pdt",
             "pmass")

# 飞溅的并行数组字段 —— 与 `_P_FIELDS` 同一套做法(数组是真值, dict 只在取证时物化)。
# `shas` 是「这颗有没有 `_still`」的 **float 0/1 标志**(不是 bool 数组: 沿用 `pli` 的先例,
# 这样 `_newbuf` 那套"numpy 缺席就退回 list"的兜底不用再写一份)。
# ⚠️ 它是**真语义不是表示**: 读端 `s.get("_still")` 判 `is not None` —— 缺失与 `0.0`
#    走的是**不同分支**(缺失 = 活跃, `0.0` = 已停稳但本帧才停)。
# `stag` 是**给取证探针用的标记位**(默认 0.0), 它跟着压实一起被 gather => 探针可以按
# "从下标 n0 起"打标, 隔若干帧再按标记读回来。**原来那些探针靠 dict 的 `id()`/对象同一性
# 跨帧认人, 换成数组后那条路断了** —— 用这个字段代替。
_S_FIELDS = ("sx", "sy", "svx", "svy", "sgd", "shw", "shh",
             "srest", "sstill", "shas", "sdt", "stag", "saway", "srw", "srh")
_S_MOTION_FIELDS = ("saway", "srw", "srh")
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

    __slots__ = ("n", "nx", "ny", "nvy", "ntl", "nwp", "nsz", "nli", "use_np",
                 "_x", "_y", "_vy", "_tl", "_sz", "_light", "_wp", "tail",
                 "tail_blend")

    def __init__(self):
        self.n = 0
        self.tail = False
        self.tail_blend = 0.0
        # 🔴 **list 快照是惰性的**(2026-10-07): 六个字段每帧 `tolist()` 实测 **0.11ms**
        #    (桌面; 设备更贵), 而**安卓上一条读它的路径都走不到** —— 三个读点
        #    (`_group_stream_particles` / `_draw_stream` / `_draw_neck_grains`)
        #    都只在**标量兜底分支**里读, 而沙流走纹理渲染器、`use_np` 在安卓恒为真。
        #    改成属性: **谁要谁建, 建一次缓存一帧**。逐位不变(还是同一个 `tolist()`)。
        self._x = self._y = self._vy = self._tl = None
        self._sz = self._light = self._wp = None
        # numpy 零拷贝切片, 只给向量化打包用(见 tools/flow_texture_experiment.py)。
        # `nwp/nsz/nli` 另供 `_draw_neck_grains` 的向量化分支(颈部颗粒的相位/尺寸/亮标)。
        self.nx = self.ny = self.nvy = self.ntl = None
        self.nwp = self.nsz = self.nli = None
        self.use_np = False

    def _lazy(self, slot, src):
        v = getattr(self, slot)
        if v is None:
            v = src.tolist() if src is not None else []
            setattr(self, slot, v)
        return v

    @property
    def x(self):
        return self._lazy("_x", self.nx)

    @property
    def y(self):
        return self._lazy("_y", self.ny)

    @property
    def vy(self):
        return self._lazy("_vy", self.nvy)

    @property
    def tl(self):
        return self._lazy("_tl", self.ntl)

    @property
    def sz(self):
        return self._lazy("_sz", self.nsz)

    @property
    def light(self):
        return self._lazy("_light", self.nli)

    @property
    def wp(self):
        return self._lazy("_wp", self.nwp)


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

def _maxfps_path():
    """`maxfps` 标记文件的路径(**与 main.py 同目录**)。

    ⚠️ **不再有 UI 入口** —— 2026-10-07 用户要求「删掉沙漏设置里那个帧率节奏机制, 默认选最高」,
    所以开发者菜单里那一行已经拆掉。这个标记文件现在**只剩开发用途**: 设备上做 A/B 时
    `adb shell "echo 120 > <app>/maxfps"`(环境变量 `HG_MAXFPS` 优先)。
    **出货默认恒为 "0"(= 不设上限, 节拍交给 vsync)。**
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "maxfps")


def _maxfps_knob():
    """Kivy 帧率上限(安卓出厂 = `"0"` = 完全不睡, 节拍交给 vsync)。

    ## 为什么留一个旋钮(2026-10-07)

    `maxfps` 是 Kivy `Clock.idle()` 的**睡眠地板**: `cap=120` ⇒ 地板 `(11/15)/120 = 6.111ms`,
    而 165Hz 一格 vsync 是 **6.06ms** —— 两者**几乎重合** ⇒ **睡眠变成了抖动的缓冲**
    (某帧多干 δ, 就少睡 δ, 周期不变)。
    DanZhu 在**同一台设备**上实测过这个取舍: `cap=120` ⇒ 平均 160.5 / 1%Low **118.9**;
    `cap=165`(等价于我们的 0) ⇒ 平均 165.1 / 1%Low **112.1** ⇒ **平均 −3% 换尾部 +6%**。

    我方单侧证据(1s 档四轮)当年是反的: M 从 96.7 → **173.4**、p90 12.93 → **7.12ms**
    —— 但那是在 **60Hz 模拟器**上、且 `maxfps=60` 的台阶(12.222ms)远高于 vsync 的情形,
    **不能外推到 165Hz 面板**(面板格 6.06ms 与 120 档地板 6.111ms 几乎重合, 是另一种状态)。

    ⚠️ **这是口味取舍(平均 vs 尾部), 归用户** ⇒ 只装旋钮, **默认 "0"**(与今天一字不差)。
    设备侧:`adb shell "echo 120 > <app>/maxfps"`(要重启 app 才生效 —— 它在 import 期读)。
    """
    env = os.environ.get("HG_MAXFPS")
    if env:
        try:
            return str(int(float(env)))
        except ValueError:
            pass
    try:
        _p = _maxfps_path()
        if os.path.exists(_p):
            with open(_p) as fh:
                return str(int(float(fh.read().strip() or 0)))
    except Exception:
        pass
    return "0"


if "P4A_BOOTSTRAP" in os.environ or "ANDROID_ARGUMENT" in os.environ:
    Config.set('graphics', 'maxfps', _maxfps_knob())

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase, Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics.texture import Texture           # 沙体材质要用(见 _SandMaterial)
from kivy.graphics import (BindTexture, Color, Mesh, Rectangle, Line, Ellipse, Quad,
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
BG_COLOR = os.environ.get("HG_BG_COLOR", "#fdf6e3")
# 🔴 **2026-10-10: 这两个改成读环境, 只为 `tools/_probe_column_alpha.py` 的"双背景差分"服务。**
#    背景(为什么): 旧那把 α 尺把像素投影到 (背景→纯沙色) 连线上, 而 `sand_light` 的投影恰 = **0.854**
#    —— 正好等于它当时当作 p10 目标的 0.855 ⇒ **它分不开"亮色不透明"与"真半透明"**
#    ("把柱子调亮"就能让读数达标)。AP 两名专家各自按调色板算出 0.854 独立复现。
#    新尺: 同一场景渲**两次**, 只改**柱背后那一层**(= 玻璃内腔填充), 解
#        α = 1 − (P₁ − P₂)/(B₁ − B₂)      ← 沙色 S 被完整消掉
#    **默认值逐字不变** ⇒ 出货行为零变化, 金标准基线不受影响。
#    ⚠️ `GLASS_FILL` 在 5588 行被**捕获进局部**(玻璃壳烘焙) ⇒ 覆盖必须在**模块加载时**生效
#      ⇒ 只能走环境变量(桌面探针能传), 不能在 redraw 里改。
GLASS_FILL = os.environ.get("HG_GLASS_FILL", "#eaf3f8")
GLASS_OUTLINE = "#5f6b70"

# ---- 沙体材质(路线 A: 预生成的 RGBA 彩色纹理) ----------------------------------
# 依据: meishu.md §7 + 外部评审 meishu2.md §3。三条被评审纠正过的前提, 记在这里免得重犯:
# ① **不能**用"灰度 × 沙色": 灰度相乘只能**压暗**, 做不出比 sand_base 更亮的颗粒,
#    黑沙尤其需要独立亮色端点 ⇒ 三端点插值后直接烘成 albedo, 前面用**白色** Color
#    (否则彩色纹理会被**再染一次**而明显发暗)。
# ② **不能**用 32×32: 球内径 800px 时一个纹素盖 25px, 存得下柔和渐变、存不下细颗粒。
# ③ 生成是 O(n²) 的纯 Python/numpy 计算, **绝不能在 redraw 里调**; 按配色缓存。
def _sand_material_probe():
    """`HG_SAND_MATERIAL` 环境变量优先(桌面), 其次与 main.py 同目录的 `matmode` 标记文件。

    🔴 **存在的理由(2026-10-10)**: 出口那条"分界线"的成因要按层拆开。已在设备上做过
    一次粒子消融(`flowrate=1`, 粒子几乎全灭) —— **那条线还在** ⇒ 不是粒子层。
    剩下的层是"GPU 沙流材质板"与"颈部沙柱四边形", 而关材质的开关以前**只有环境变量**,
    安卓 app 读不到宿主环境变量 ⇒ 这个消融在设备上做不到。补上标记文件通路:
        adb shell "echo flat > /data/data/org.shalou.hourglass/files/app/matmode"
        adb shell rm  /data/data/org.shalou.hourglass/files/app/matmode    # 回默认
    """
    env = os.environ.get("HG_SAND_MATERIAL")
    if env:
        print("sand material mode = %r (from HG_SAND_MATERIAL)" % env)
        return env, True
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matmode")
    try:
        with open(path, "r") as fh:
            text = fh.read().strip()
        print("sand material mode = %r (from %s)" % (text or "grain", path))
        return (text if text else "grain"), True
    except Exception as exc:
        print("sand material mode = 'grain' (no marker: %s)" % exc)
        return "grain", False


# 🔴 **`SAND_MATERIAL_FORCED` 不是装饰**: 启动时那段"从配置恢复材质"的守卫**只挡了环境变量**
#    ⇒ 只写标记文件的话, 它在 `build()` 里被 `apply_sand_style(cfg['sand_mode'])` 覆盖回 grain,
#    而**日志照样会打印出的 'flat'** —— 臂空转、看起来像"没差别"(2026-10-10 实际栽了一次,
#    靠 `_build_dynamic_canvas` 那句 "GPU sand flow active" 仍然出现才发现)。
#    现在: 环境变量**或**标记文件任一存在 ⇒ 一律不读配置。
SAND_MATERIAL, SAND_MATERIAL_FORCED = _sand_material_probe()   # grain | flat(退回旧平色)


def _neck_marker(name):
    """`<app>/<name>` 标记文件非空 ⇒ True。⚠️ 目录是 `dirname(main.py)` = `<app>/`。

    🔴 **只在 import 时调一次**(见下面几个缓存的常量) —— 它每调一次就是一次 `open()`,
       而 `_neck_log_on()` / `_neck_nouv_on()` 都在 `redraw` 的**热路径**上。每帧开一次文件
       是白扔几十微秒。
    """
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               name), "r") as fh:
            return fh.read().strip() not in ("", "0")
    except Exception:
        return False


NECK_LOG_ON = _neck_marker("necklog")


def _neck_log_on():
    """诊断开关: `<app>/necklog` 非空 ⇒ 每秒打一行颈部节点表。**import 时读一次就缓存。**"""
    return NECK_LOG_ON


NECK_NOUV_ON = _neck_marker("necknouv")
NECK_QUADS_OFF = _neck_marker("neckquads")
NECK_MOUND_OFF = _neck_marker("neckmound")
NECK_MOUNDSHAPE_OFF = _neck_marker("neckmshape")
NECK_CARVE_OFF = _neck_marker("neckcarve")
NECK_RECTS_OFF = _neck_marker("neckrects")
NECK_MISC_OFF = _neck_marker("neckmisc")
NECK_DUMP_ON = _neck_marker("neckdump")
NECK_GEOM_ON = _neck_marker("neckgeom")
NECK_FREE_OFF = _neck_marker("neckfree")
NECK_MARKER_OFF = _neck_marker("markeroff")
NECK_ABL5_OFF = _neck_marker("abl5")
NECK_POOL_OFF = _neck_marker("pooloff")
NECK_FREE_ONTOP = _neck_marker("freeontop")
MAT_OVER_ALPHA = 0.62   # 材质铺层的透明度(见 _mat_over_rect)
# 🔴 **2026-10-10: 出货默认关。** 用户判词「你tmd直接把沙柱下落的间隙/沙子给删了」——
#    这一层把粒子流整个盖住, 等于删掉"看得见沙在落"这件事(项目红线)。
#    要试仍可显式打开: `matover` 标记文件。
#    (上面那行"出货默认开"是 2.33 当天的注释, 已被 2.34 的回退取代。另: `nomatover`
#     这个名字**全仓库没有任何代码读它**, 别再照它写脚本。)
NECK_MAT_OVER = _neck_marker("matover")

# 🔴 **2026-10-10: 出口以下的沙柱是否单独画在 context 外面。**
#    默认 **False = 不分开** ⇒ 整条沙柱(上球 → 颈部 → 出口以下 → 在途沙前沿)全部走
#    **同一个 `SandFlowContext` 沙流着色器** —— 用户要的「沙柱 + 颈部 + 上面都用
#    一套渲染, 只是有所区别」。
#
#    原来为什么分开(2.32 / `96fe150`): 量到"出口以下只有 27.4% 实心", 读成
#    "那几段根本没被画出来", 于是搬出 context 换普通纹理四边形(89.4%)。
#    🔴 **2026-10-10 重测推翻了那条结论**: 真清空 `_neck_free_band`(标记 `neckfree`)
#    ⇒ 出口以下那一段从 **57/57 沙色像素掉到 0/57**(PC 400×875 / 10s / t=7.40),
#    它**画满了整条出口以下的柱子**; 记录里"净贡献 1px / 出不了像素"是无效测量。
#    而 27.4% 是**二值洞与渐入都还没加**时的旧数(见 `sand_hole_ramp` 的注释)。
#
#    回退: 标记 `outsplit` ⇒ 恢复 2.34 的行为(出口以下走普通纹理四边形)。
NECK_OUT_SPLIT = _neck_marker("outsplit")


def _neck_nouv_on():
    """**只切颈部四边形 uv 支路**(材质照旧在场) —— 用来把"uv 写错了"与"纹理没绑上"劈开。

    结论(2026-10-10 实测): **关掉它没有任何变化**(出口以下仍是 2~9px 的碎段)。
    ⇒ 颈部沙柱四边形在材质路径下**根本不是用普通纹理画的** —— 它们被建在
    `neck_flow`(一个 `RenderContext`)里面, 走的是**沙流着色器**。
    ⚠️ 但**别把结论写成"是 coverage 把它抹掉的"**: 随后把着色器里的打散
    (`sandedgemul=0`)与二值洞(`holeth=-1`)**两项一起关掉, 出口以下还是 27%**
    ⇒ 那几段**根本没被画出来**。真正的判据是 `neckout=1`(整条移出 context):
    出口以下 **27.4% → 89.4%**(= 材质关掉的对照值)。修法见 `_neck_free_band`。
    留着这个开关只作以后排查用。
    """
    return NECK_NOUV_ON


# 出口以下"打散"的渐入长度, 单位 = **颈管高的倍数**(用户口径: 「不用超过瓶颈高度的 2 倍,
# 或者 1 倍」)。0 = 旧行为(出口那条横向分界线原样在)。
#     adb shell "echo 1.0 > /data/data/org.shalou.hourglass/files/app/freeramptubes"
#     adb shell rm  /data/data/org.shalou.hourglass/files/app/freeramptubes
def _neck_free_ramp_tubes():
    _v = os.environ.get("HG_FREE_RAMP_TUBES")
    if _v:
        try:
            return max(0.0, float(_v))
        except ValueError:
            pass
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "freeramptubes"), "r") as fh:
            return max(0.0, float(fh.read().strip() or 0))
    except Exception:
        # 🔴 **2026-10-10 出货默认 0.0 → 1.0**(= 1× 直筒高; 用户口径「不超过瓶颈高度的 2 倍」)。
        #    设备实测(1080×1920 / MuMu / 同一状态, 柱内高频 std 中位):
        #        2.34(出口以下是普通纹理)      **2.53**   边缘硬、实心, 但词汇与上球不同
        #        2.36(走着色器, ramp=0)         **5.81**   两边糊出白毛、出口处一团糊
        #        2.36 + ramp=1.0 + core=0.85    **4.34**   边缘干净 + 上球那套细颗粒  ← 选定
        #    ⇒ 打散**必须按深度渐入**, 否则出口那一行瞬间满功率 ⇒ 糊。
        return 1.0


NECK_FREE_RAMP_TUBES = _neck_free_ramp_tubes()

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
    ("1", 0.10, 1.0), ("2", 0.10, 1.4), ("3", 0.10, 1.8),
    ("4", 0.10, 2.2), ("5", 0.10, 2.6), ("6", 0.10, 3.0),
)
# 🔴 **2026-10-06 用户实测反馈: 「本来还凑合 … 新版本的很奇怪, 好像需要很低的数值
#    才能达到之前比较高数值的效果」** —— 说的是**准的**, 原因在版本史里:
#      1.99  引入六档: 粗度 = 1,1,1,2,3,2 (最高 3 倍), 另有一维"明暗落差" grad
#      1.100 判定 grad 是**假档**(1/2/3 三档画出来一样) ⇒ 删掉那一维,
#            顺手把粗度铺成 1..6 ⇒ **上限从 3 倍变成 6 倍**
#    ⇒ 同一个"4", 旧版 = 粗 2, 新版 = 粗 4 ⇒ 想要旧的样子只能选很低的数字。
#    **修法: 六档保持六个不同的档(不回到假档), 但把粗度区间压回 1→3**,
#    这样"4"≈ 旧的"4"(2.2 vs 2)、"6"≈ 旧的"5"(3.0 vs 3) —— 手感回到"还凑合"那版。
#    ⚠️ 粗度在 `_coarsen_noise` 里是**除数**(`size / coarse`) ⇒ 小数合法。
# ⚠️ **「沙体颗粒」表的版本号** —— 这张表的"含义"改过一次(2026-10-05 grad→只看粗细),
#    同号的档已经不是同一个东西 ⇒ 对不上就回落默认, 免得用户存的"3"被静默换成另一种颗粒。
# ⚠️⚠️ **只锁这一张表!** 第一版把它做成了"两把锁共用一把"(`levels_rev` 同时管 grain 与 rough),
#    而 **rough 表的含义从来没变过** ⇒ **用户明确选过的 rough=4 被一起作废、静默降到 3(降 33%)**。
#    这是 r18-1号 2026-10-05 在设备上查出来的 —— **静默改用户设置**, 比不改还糟。
SAND_GRAIN_REV = 3
# 🔴 **2026-10-07 用户要求: 出厂默认改成 3**(此前是 1)。
#    「新增改动2: 默认沙子颗粒和起伏 都默认选择档次3」—— 这是**用户明确指定**的, 与下面
#    rough 表那条"静默改用户设置"的教训**不是一回事**(那条是主持人顺手改、没告知)。
#    ⚠️ 表意提醒: 本表 1.100 之后把粗度区间压回了 1→3, 所以 "3" = 粗度 1.8(不是 6 档里的中值 2.2)。
#    ⚠️ **改出厂默认 ≠ 改用户已存的档**: 配置里已有值时**以配置为准**, 老装机不会被动改变。
SAND_GRAIN_LEVEL_DEFAULT = "3"
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
# ⚠️ **默认曾经 = 4**(设备 ≈5.35px) —— 那是**用户 2026-10-05 亲口选的那一档**。
#    1.100 里被主持人在"改颗粒表"的同一次编辑中**顺手改成了 3**, 提交说明里没提,
#    表头上方注释还写着"≈5.35px" —— 代码与注释互相矛盾, 而且静默改了用户的值。
#    r18-1号 在设备上逐条比对 git 才查出来。**当时改回了 4。**
# 🔴 **2026-10-07 用户明确要求: 出厂默认改成 3**(「默认沙子颗粒和起伏 都默认选择档次3」)。
#    ⇒ 规则**不是"不能改", 是"不许静默改"**: 用户开口了就照办, 并在这里写明是谁在何时要的。
#    ⚠️ 改出厂默认 ≠ 改用户已存的档 —— 配置里已有值时**以配置为准**, 老装机不会被动变。
SURFACE_ROUGH_LEVEL_DEFAULT = "3"


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
# ⚠️ 原来是 `int(round(...))` —— 表格里一旦出现小数粗度会被四舍五入掉(2026-10-06 改)
SAND_MATERIAL_COARSE = float(os.environ.get(
    "HG_SAND_COARSE", str(SAND_MATERIAL_COARSE)))
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
SAND_BAND_NECK_FADE = 70.0  # 锥顶离**出口**还有多少 px 时把沙堆亮带淡到 0
MOUND_BAND_NECK_FADE = _neck_marker("moundbandfade")
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
# 🔴 沙堆**接触高度查找表**的采样数(见 `update_particles` 的飞溅段, 2026-10-06 性能)。
#   257 ≈ 4× 控制点数 ⇒ 线性插值的误差远小于 1px; 表本身每帧只建一次。
CONTACT_TABLE_N = 257
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
# ★ 与周期无关的**水位**(用户 2026-10-06: 「和周期没有关系」)—— 这就是唯一那个数。
#   520 = 用户说"有点少了"时的量; 现取 **700(+35%)**。要更多/更少只改这一个数。
# 🔴 **2026-10-07 性能: 700 → 480**(用户 /goal: 「甚至可以牺牲少量表现」)。
#   设备分段实测 `物理` 3.77ms 里的大头是**飞溅循环**(~1700 颗 × 纯 Python dict 读写),
#   而飞溅的**在世数 = 生成率 × 寿命** ⇒ 直接按比例换时间。
#   480/700 = −31% 的背景层(背景层约占生成量的一半) ⇒ 总颗数约 −15%。
#   ⚠️ 这是**观感取舍**: 觉得稀了就把这个数调回去(一行)。
SPLASH_BG_RATE = float(os.environ.get("HG_SPLASH_BG", "600"))
SPLASH_HIT_CHANCE = 0.50
# 同口径仍用同一水位; 超细颈部按有效内宽缩放, 不按上球剩余量减弱。
#   曾按 `∝ 在途主流粒子数` 做过(比例跨 14 倍 → 4.2 倍), 但那必然让长周期变少(50s 只剩 45%)
#   ⇒ **作废**。默认 0 = 走定率 `SPLASH_BG_RATE`; 非 0 可恢复"按比例"(留作对照臂)。
SPLASH_BG_PER_PARTICLE = float(os.environ.get("HG_SPLASH_PER_PARTICLE", "0.0"))


def _splash_max():
    """**飞溅存活上限**。`0` = 不限制。

    ## 2026-10-07 晚: 出厂默认从 0 改成 **1300**(用户裁决)

    用户看了两段自己录的录像之后给的判词: **「5s 那个感觉不错, 15s 那个不喜欢」**
    —— 而 5s 与 15s 的差别**不是档位造成的**, 是**飞溅层的水位还没爬上去**:

    | 档内进度 | 5s 档碰撞处亮色比 | 15s 档 |
    |---|---|---|
    | 40% | **0.079** | **0.300** |
    | 60%(同一沙堆高度) | **0.098** | **0.280** |

    ⇒ 同样几何下差 **~3 倍**。逐帧数出来的机制: **生成 ≈1180/s 恒定, 消亡 ≈1100/s 略少**
    ⇒ 净攒 ~100/s ⇒ 在世数 **5s:1900 → 25s:3560, 一路爬**。用户要的是 5s 那种稀疏度,
    **而且是"每一档都长这样", 不是"慢慢变成 15s 那样"**。

    ⇒ 拿上限把水位**钉在 5s 那档**: 5s 档收尾时 ≈1190 ⇒ `SPLASH_MAX = 1300`。
    长周期爬到 1300 就平了, 短周期本来也够不到 ⇒ **所有档位看起来一致**。
    顺带 **−60% 的飞溅颗粒**(在世数 3560 → 1300), 那是 `物理` 栏里最大的一块 ⇒ 每帧余量变大。

    ⚠️ 早先(同一天)这个旋钮是当**性能消融臂**装的, 四臂实测"压飞溅数"**对 p90 尾部没用**
    (那 5% 是设备整机抖动) ⇒ 当时结论是"不要当优化出货"。**那结论仍然成立** ——
    现在改默认值**不是因为省时间, 是因为观感**(用户明确要稀的那一档)。
    量具: `tools/_splash_max_arms.sh`(标记文件 `splashmax`; 设备上环境变量读不到)。
    """
    env = os.environ.get("HG_SPLASH_MAX")
    if env:
        try:
            return max(0, int(float(env)))
        except ValueError:
            pass
    try:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "splashmax")
        if os.path.exists(_p):
            with open(_p) as fh:
                return max(0, int(float(fh.read().strip() or 0)))
    except Exception:
        pass
    # v2.6: 加量集中在有效方向, 上限仍有限, 绘制使用四个固定批处理块。
    return 2000

# 生效值(启动时定一次; 设备侧改它要重启 app —— 标记文件与 `flowrate` 同一套)。
SPLASH_MAX = _splash_max()

# ================== 飞溅的**弹道模型** —— 参数逐字取自 PC v4 ==================
# 🔴 **依据**: `pc/hourglass_v4.py:1109-1118`(项目自定的"唯一真理")。v4 里飞溅**只有一种**,
#   而且是**从落点向上弹出、走抛物线**:
#       'x': p['x'](命中处),  'y': mound_top_y - 2,
#       'vx': U(-35, 35),     'vy': U(-110, -55)      ← tkinter y 向下 ⇒ **负 = 向上**
#   ⚠️ **v4 里根本没有"背景飞溅层"** —— `_draw_mound_surface` 是死代码, `draw()` 从不调用它。
#     安卓版历史上加了那一层, 同时把命中层的 `vy` 改成了**朝下**(见 `_replay_hits` 里的旧注释)
#     ⇒ 抛物线没了 + 一层沿整个坡撒的颗粒 = 用户 2026-10-06 的判词
#     「**像打农药一样**」「**实际它是一个先喷射再抛物线**」。
#   ⇒ 现在**两层共用同一个模型**(`_eject_splash`), 只有数量不同。
#
# `SPLASH_GAIN` = **唯一的夸张旋钮**。用户 2026-10-06:「有**适度的夸张表演性质**,
#   看起来**凑合合理**即可, 不是做一个**极端的合理**, 否则这个玩具**完全没有尽头**」。
#   同时乘在 vx/vy 上 ⇒ **弧高 ∝ GAIN²、射程 ∝ GAIN²**:
#       1.0 = 逐字 v4(弧高 13px / 射程 17px) | 1.6 = 34 / 44 | 2.4 = 77 / 100
#   ⚠️ "改前/改后要交回用户判"的那一类(观感决定) —— 出并排图用
#      `HG_SPLASH_GAIN=1.0 / 1.6 / 2.4`。
# ⚠️ **要移植的是"比例", 不是 v4 的绝对值** —— 闸门有一条
#   `rebound cannot gain energy over the incoming grain`(飞溅速度必须 < 0.30 × 入射速度)。
#   把 v4 的 `vx ±35 / vy 55~110` 换算到它自己的入射速度(≈400~600)正是 **0.09~0.28**
#   —— 所以比例区间取 `U(0.10, 0.28)`。
#   旧版真正输在两个地方: ① `vy` 是**朝下**的(抛物线没了); ② `min(110*motion_scale, ...)`
#   那个**硬上限**(实测 94% 的颗粒被它钳住 ⇒ 不管砸得多狠, 飞溅永远是同一撮)。
# ⚠️ **2026-10-06 用户裁决上调**: 「范围太窄…你范围提高就好了」「不要**全部靠下滑**,
#   下滑距离小就行」 ⇒ **铺开必须来自发射本身**, 不能靠沿坡长滑。
#   旧区间 0.10~0.28 实测只有 **11%** 的颗粒越过沙堆半宽的中点(用户看到的就是"只有一点点地方")。
#   提到 **0.12~0.42** —— 仍**远低于物理界 1.0**(入射速度), 能量守恒不受影响。
SPLASH_SPEED_LO = float(os.environ.get("HG_SPLASH_SPD_LO", "0.12"))   # × 入射速度
SPLASH_SPEED_HI = float(os.environ.get("HG_SPLASH_SPD_HI", "0.42"))
# `SPLASH_GAIN` = **唯一**的夸张旋钮(乘在比例上)。⚠️ 拉到 ~1.07 以上会越过 0.30 的物理界、
#   闸门会翻红 —— 那是**有意的护栏**, 不要去放宽它。
SPLASH_GAIN = float(os.environ.get("HG_SPLASH_GAIN", "1.0"))
# 环境参数保留旧的“相对竖直线、单位弧度”语义; 默认换算为水平仰角 0.01–60 度。
# 发射时再限制水平仰角不超过 60 度, 包括使用旧环境参数的情况。
SPLASH_ANGLE_MIN = float(os.environ.get("HG_SPLASH_ANG_LO", str(math.radians(30))))
SPLASH_ANGLE_MAX = float(os.environ.get("HG_SPLASH_ANGLE", str(math.radians(89.99))))
SPLASH_HORIZONTAL_ANGLE_LIMIT = math.radians(60)
SPLASH_LIFT_PX = float(os.environ.get("HG_SPLASH_LIFT_PX", "2"))  # 粗沙柱保留 2.9 的起点偏移。
# ★ **每颗飞溅各有各的重力倍率** —— 用户 2026-10-06:「不同沙子的**轨迹是略有不同的**…
#   可以重复, 但是**不要都是一个曲线**」。
#   只靠角度/初速不同, 出来的仍是**一族标准抛物线**, 读起来同构。
#   给每颗一个 `gd ~ U(LO,HI)` 当等效阻力(轻的飘、重的沉) ⇒ 每条曲线形状都不一样。
#   ⚠️ 它**不改变出射速度**, 所以闸门那条"飞溅不能比入射快"不受影响。
SPLASH_GRAV_LO = float(os.environ.get("HG_SPLASH_GRAV_LO", "0.62"))
SPLASH_GRAV_HI = float(os.environ.get("HG_SPLASH_GRAV_HI", "1.55"))
# ★ **飞溅颗粒的尺寸**(用户 2026-10-06:「颗粒度从 1x1 改成 2x2? 或者随机, 有 1x1 又有 1x2
#   还有 2x2 的…现在感觉太密集、太细了」)。
#   ⚠️ **两个独立的毛病, 一起修**:
#     ① **尺寸没跟屏幕缩放** —— 专家实测: 同一份 1~2px 的颗粒, 桌面占球半径 0.92~1.85%,
#        设备只有 **0.22~0.45%**(**桌面是设备的 3.3 倍**) ⇒ 设备上细到几乎看不见。
#        现在按 `_R_inner / 140`(桌面参考半径) 缩放。
#     ② **尺寸单一** —— 全是 1x1(67%)/2x2(33%), 读起来像一层均匀点阵 ⇒ 按用户说的做成混合。
#   `SPLASH_SIZE_MIX` 里每个条目是 (宽, 高) 的**相对倍数**, 乘上缩放后的基准。
# ⚠️ **2026-10-06 用户裁定: 只用一种尺寸 —— 最小的那个(1x1)**。
#   「下面的沙子的尺寸**就不用做成有 1x1、1x2、1x4**, 就做成**一种尺寸**就行了,
#     做成**最小的那个 1x1**。**缩放还是要有的**。」
#   保留成"表"是为了以后想再试混合时只改这一行(取单元素表 = 单一尺寸)。
SPLASH_SIZE_MIX = ((1, 1),)
SPLASH_PX_BASE = float(os.environ.get("HG_SPLASH_PX", "1.0"))   # 基准像素(再乘屏幕缩放)

# 飞溅**射程下限**(= 最细颈那档的射程 / 最粗颈那档的射程)。2026-10-09 由用户从 1/3 定为 1/4。
# ⚠️ 射程 ∝ 速度² ⇒ 实际用的是 `sqrt(下限 + (1−下限)·blend)`, 所以改这里会同时改
#    `_splash_speed_scale` —— **是个观感量, 出货前必须出并排图交用户判**。
SPLASH_RANGE_FLOOR = float(os.environ.get("HG_SPLASH_RANGE_FLOOR", "0.25"))

# 颈部颗粒的**标量兜底路径**开关 —— **只为对照**。`HG_NECK_SCALAR=1` 强制走老的单颗循环:
# 向量化那版必须与它在 `random.seed(23)` 下**逐像素 0 差异**(否则量出来的加速是拿画面对错的)。
NECK_SCALAR = os.environ.get("HG_NECK_SCALAR") == "1"

# **区间打点**钩子 —— 由 `tools/prof_android.py` 在装上时填一个 callable, 平时是 `None`。
# 用来把 `update_particles` 这个整体拆成"主粒子 / 飞溅"两段(它内部没有可单独包的方法)。
# ⚠️ 读它的写法是 `_pm = _PROF_MARK` 一次 + `if _pm:` —— **别直接 `if _PROF_MARK:`**,
#    那会在热路径上多一次全局查表。
# 🔴 **局部名要挑没被占用的** —— 2026-10-07 我在 `_draw_neck_grains` 里照抄了 `_m`,
#    而那个函数**下面**有 `_m = (0.06 + 0.20 * _t + ...) * 0.85`(向量化分支的色调混合数组)
#    ⇒ 打点函数被数组覆盖, 随后 `if _m:` 变成"数组的真值"
#    ⇒ `ValueError: truth value of an array with more than one element is ambiguous`。
#    代价: 应用一跑到那帧就崩。**加打点前先在该函数里 grep 一遍你要用的名字。**
_PROF_MARK = None


class _MarkCollector:
    """把 `_PROF_MARK` 的区间打点收进一个 dict, **每帧由基准清一次**(2026-10-07)。

    为什么要它: 用户平板 165Hz 的 log 只给到四栏(`物理/图元/Canvas/Swap`), 而尾部成本
    全落在 `图元` 这一栏里 —— **不拆开就不知道该改哪块**。打点本来只在 `prof_android`
    开着(`prof.on` 标记文件)时才挂, 而那需要用户在设备上建文件; 基准跑的时候挂上这个
    收集器, **用户照常点"开始测试"就能把逐段耗时带进日志**。

    ⚠️ 语义与 `prof_android._mark` **逐字一致**: 记的是"**距上一次打点**"的那一段,
    所以标签指的是**刚结束的那一段**, 不是"这个名字的函数耗时"。⇒ **读的时候不要相加**
    (相邻段首尾相接, 相加会把 `图元` 算两遍)。
    ⚠️ 默认 `_PROF_MARK is None` ⇒ 出货路径零开销(一次局部读 + 一次判空)。
    """

    __slots__ = ("acc", "_last")

    def __init__(self):
        self.acc = {}
        self._last = time.perf_counter()

    def reset(self):
        self.acc = {}
        self._last = time.perf_counter()

    def __call__(self, name):
        t = time.perf_counter()
        self.acc[name] = self.acc.get(name, 0.0) + (t - self._last) * 1000.0
        self._last = t

    def snapshot(self):
        return dict(self.acc)

# 颈部颗粒的**批处理直通**钩子 —— 由 `tools/flow_splash_experiment.install_neck` 填。
# 填了就跳过"写记录器 -> 再收集成桶"那两步(设备实测合计 ~0.35ms/帧), 直接把已经是
# numpy 的几何量交给批处理。签名: `sink(widget, cnt, xs, bot, top, ji, sz)`。
# ⚠️ `ji` 是 **int64 数组**(分组用 `bincount`), 不是 list。
# ⚠️ 钩子必须负责**清掉它所有的批**, 包括 `cnt == 0`(颈部隐藏)时。
_NECK_SINK = None

# 落点处的横向尺度 —— 只跟**落点**走, 与沙堆有多宽无关(v4 也是只从命中点出)。
# ⚠️ 它**不是**"铺满沙堆"的那个尺度 —— 那是 2026-10-06 被用户判为"打农药"的做法。
SPLASH_BG_SPRAY_FRAC = float(os.environ.get("HG_SPLASH_SPRAY", "0.045"))
SPLASH_BG_EDGE_SIGMA = float(os.environ.get("HG_SPLASH_SIGMA", "1.5"))   # 外沿 = 1.5σ
# splash 重力对 `motion_scale` 的指数 —— **只给对照实验用, 默认 1.0 = 现状逐位不变**。
SPLASH_G_SCALE = float(os.environ.get("HG_SPLASH_G", "1.0"))
# ★ **落到沙面上之后** —— 用户 2026-10-06 选定「**贴坡面滑一段再没**」(不是 v4 的"落回即删")。
#   ⚠️ 这是**有意的偏离**, 不是抄错 v4。贴 y 到面 + `vy` 归零(重力下一帧又把它按回面上
#      ⇒ 自然沿坡走) + 横向按摩擦衰减, **停住后原地留一会儿**才消失。
#
# 🔴 **2026-10-06 第二次调(用户): 「沙子的向下流动应该是流动一会会就不动了(因为有阻力),
#    否则全部向下流动, 但是下面没有堆积, 这个不合理」**。
#    上一版是「沿坡重力 `g·sinα·0.5` + 摩擦只有 1.5/s」⇒ 颗粒**越滑越快**,
#    终端速度 `a/k = 112/1.5 ≈ 75px/s`, 一路滑到 `REST_LIFE` 计时器把它**半路砍掉**。
#    三处一起改:
#      ① `SLOPE_GAIN → 0`  —— 不让重力再给颗粒加速, 摩擦说了算;
#      ② `SLIDE_DAMP 1.5 → 4.0` —— 落地 |vx| 中位 57px/s(专家团实测) ⇒
#         **滑行距离 `(57-4)/4 ≈ 13px`、刹停耗时 0.66s**, 即"流动一会会就停";
#      ③ **"停住"不再等于"立刻删除"** —— 旧版 `|vx| < MIN_VX` 直接 `continue`(删除),
#         于是**永远看不到"停住"那一刻**, 只看到它滑到底然后被抹掉。
#         现在停住后原地留 `SPLASH_STILL_LIFE` 秒 ⇒ 任一时刻坡面上都撒着一层**已停稳**的
#         颗粒, **覆盖面积反而更大**(也正是用户此前要的「表面大部分地方都有沙子在流动」)。
# 🔴 **2026-10-06 性能**: 4.0 → **7.0**。飞溅的**在世数 = 生成率 × 寿命**, 而设备实测
#   `物理` +3.15ms / `Canvas` +3.83ms 全是飞溅数量(431 → 2657)推上去的 ——
#   今天"溅到沙子上才开始掉"那一轮把寿命从"落地即删"拉到 ~1.06s, 代价就在这里。
#   摩擦 7.0 ⇒ 滑行距离 `(57-4)/7 ≈ 7.6px`、刹停 **0.37s**(原 0.66s)
#   ⇒ 一条命从 ~1.06s 收到 ~0.65s(**−39% 在世数**), 而**观感仍是"流动一会会就停"**。
SPLASH_SLIDE_DAMP = float(os.environ.get("HG_SPLASH_SLIDE", "7.0"))   # 坡面摩擦(1/秒)
# ⚠️ 必须 > 刹停耗时(摩擦 7.0 时 0.37s), 否则又变成"滑到一半被抹掉"。
SPLASH_REST_LIFE = float(os.environ.get("HG_SPLASH_REST", "0.50"))     # 滑动阶段最久多久(s)
# ⚠️ **代价(实测, 别当成免费)**: 滞留会抬高飞溅的**稳态在世数** ——
#   桌面 15s 档隔离实测(`tools/_probe_splash_count.py`, 只动这一个变量):
#     滞留 0.40s ⇒ 稳态峰值 **2187** / 均值 1924
#     滞留 0    ⇒ 稳态峰值 **1720** / 均值 1436     (即 **+27%**)
#   要压成本先降这个值, 别去动生成率(那是用户按 σ 定过的数量轴)。
SPLASH_STILL_LIFE = float(os.environ.get("HG_SPLASH_STILL", "0.28"))   # **停住之后**再留多久(s)
# 沿坡加速度的倍率(1.0 = 真实 `g·sinα`)。⚠️ 默认 **0**: 见上面 ①。
SPLASH_SLOPE_GAIN = float(os.environ.get("HG_SPLASH_SLOPE", "0.0"))
SPLASH_MIN_VX = float(os.environ.get("HG_SPLASH_MINVX", "4.0"))       # 小于它就当停住(px/s)


def _autofit_font(btn, base_size, pad=6.0, floor=0.6):
    """按钮标签**跟着按钮实际宽度缩字号** —— 不许文字溢出去压邻居。

    🔴 Kivy 事实(2026-10-06 实测): `Button` 把文字**居中画出来, 既不裁切也不缩字号**。
    ⇒ 只要按钮被压得比文字窄, 字就**画到邻居身上** —— 用户实拍的症状是
    `50秒 / 沙沙声 / v1.154 / 开始 / 重置` 糊成一片、顶部 6 个色块文字连成一条。
    触发条件是**密度 × 窗宽**换算下来控件不够宽(桌面 density≥1.5, 或窄窗),
    **不是代码"必然坏"** —— 同密度下 1.152 更糟: 底栏子控件合计 532/400=133%、
    `重置` 直接跑到 x458..588 **完全出屏**(1.153 的 `_fit_bottom_widths` 修的是那个),
    但它把按钮压小之后, **文字溢出**这一层才露出来。

    ⚠️ **只在装不下时才缩**(装得下时字号一字不动) ⇒ 设备上(密度 3.0、控件宽裕)
    是**空操作**, 不影响已验收的观感。
    """
    state = {"key": None}

    def _fit(*_a):
        w = btn.width
        if w <= 1:
            return
        key = (round(w, 1), btn.text, base_size)
        if state["key"] == key:
            return
        state["key"] = key
        avail = max(8.0, w - pad)
        try:
            from kivy.core.text import Label as _CL
            cl = _CL(text=btn.text, font_size=base_size,
                     font_name=btn.font_name or "Roboto")
            cl.refresh()
            need = cl.content_size[0]
        except Exception:
            return
        if need <= avail or need <= 0:
            btn.font_size = base_size
        else:
            btn.font_size = max(base_size * floor, base_size * avail / need)

    btn.bind(width=_fit, text=_fit)
    _fit()


def _inv_norm(p):
    """标准正态的**逆 CDF**(Acklam 有理逼近, |绝对误差| < 1.15e-9)。

    ⚠️ **为什么不用 `random.gauss` / `normalvariate`**:
       · `random.gauss` 内部**缓存第二个样本** ⇒ 每次调用消耗的均匀数是 1 或 2, 在跳;
       · `random.normalvariate` 是**拒绝采样** ⇒ 消耗量不定。
       本工程大量取证工具靠「同 seed 逐像素对照」(`tools/inspect_flow.py` 等),
       要求**每帧随机数调用次数可复现** ⇒ 这里用「1 个均匀数 → 1 个样本」的定长写法。
    """
    if p <= 0.0:
        p = 1e-12
    elif p >= 1.0:
        p = 1.0 - 1e-12
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    if p < 0.02425:                                   # 左尾
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > 1.0 - 0.02425:                             # 右尾
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5                                       # 中段
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)

# 流量守恒的**收缩下限** —— 粒子加速下落时横向按 A·v=常数收缩, 这是它的地板。
# ⚠️ **本移植与 PC v4 不一致, 而且这一条压在"铁律: PC v4 是唯一真理, 参数不变"上**:
#      pc/hourglass_v4.py:1090     `max(0.5, ...)`
#      本文件(标量路径)             硬钳位 0.70
#      tools/flow_numpy.py:103      硬钳位 0.70      ← 两条路径**互相一致**, 一起偏离 v4
#    文档(《CLAUDE.md》两处)记着这件事, 写着"有意/无意地偏离了参数不变, **要改这个数得两边一起决定**",
#    但**没有记原因** —— 从没人问过用户。
#    ⇒ 做成开关(默认 0.70 = **零行为改动**), 好让"0.70 vs 0.50 看起来差多少"变成可判的。
#    ⚠️ 值通过 `consts` 传给 numpy 路径, 两条路径**不可能**再各写各的。
def _shrink_min_probe():
    """`HG_FLOW_SHRINK_MIN` 环境变量优先(桌面), 其次与 main.py 同目录的 `shrinkmin` 标记文件。

    🔴 **环境变量到不了安卓 app**(项目已记录: `main.py` 里那一整套 `HG_*` 在设备上一律取默认值)
    ⇒ 要在设备上做"收多细"的单变量对照, 只能写标记文件(app 私有目录, 与 `flowrate` /
    `maxfps` / `seamband` 同一套):
        adb shell "echo 0.50 > /data/data/org.shalou.hourglass/files/app/shrinkmin"
        adb shell rm  /data/data/org.shalou.hourglass/files/app/shrinkmin   # 回默认
    两个都没有 ⇒ None ⇒ 走出货默认值。
    """
    env = os.environ.get("HG_FLOW_SHRINK_MIN")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "shrinkmin"), "r") as fh:
            text = fh.read().strip()
        return float(text) if text else None
    except Exception:
        return None


_sm = _shrink_min_probe()


def _twoimpl_probe():
    """柱/云两套实现的**对齐档位**(0/1/2), 桌面走 `HG_TWOIMPL`, 设备走同目录 `twoimpl` 标记文件。

      **0 = 出货行为(与今天逐位一致)** —— 柱: 提前 return + ramp; 零点 = 出口
      **1** —— 柱改成「先钳 target 再 ramp」(= 粒子那条路径的钳位序, 连续, 无早退断点)
      **2** —— 在 1 之上, 再把柱的**零点**从"出口"挪到 `_lower_ball_cut`(= 粒子那套的原点)

    为什么要有它: AP 两名攻击手独立测得 `_free_width_ratio` 的 docstring「update_particles
    里每颗粒子用的就是它」**是假的** —— 两条路径在**零点**(差 shift px)与**钳位顺序**上都不同,
    实测最大差 0.2866·t_in(桌面 2.27px / 平板 8.3px)。要让它们口径一致必须先能**分别**开这两味,
    否则分不清是哪一味造成画面变化。
    默认 0 ⇒ **不动出货**; 视觉改动要并排图交用户判(项目红线)。
    """
    env = os.environ.get("HG_TWOIMPL")
    text = env if env else ""
    if not text:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "twoimpl"), "r") as fh:
                text = fh.read().strip()
        except Exception:
            return _TWOIMPL_DEFAULT
    try:
        n = int(text)
    except (TypeError, ValueError):
        return _TWOIMPL_DEFAULT
    return n if n in (0, 1, 2, 3) else _TWOIMPL_DEFAULT


# 🔴 **2026-10-09: 出厂默认 = 3**(把"柱与粒子同一条式子"这条 docstring 声明兑现)。
#    换掉出货行为要能一句话回退 ⇒ 写 `twoimpl` 文件内容 `0`(或 `HG_TWOIMPL=0`)即可。
#    依据(全部实测, 详见 shazhu_cuxi_plan.md F6 与 A4 闸门):
#      · 收益: 柱/云在同一深度的半宽差 0.205·t_in ⇒ 桌面 2.27 / 模拟器 4 / 平板 12.8px 归零。
#      · 代价: `poke = 柱半宽 − 云外缘` 最坏 +2.603 → +2.935px(深段, 两档都有, 属既有);
#              0–40px 近场由"藏在云内 −1.300"变为"冒出 **+0.254px**"(亚像素, 低于像素栅格)。
#      · ⇒ 用"≤0.33px 的 poke"换掉"4~13px 的柱云错位"。
#    ⚠️ 两套实现之所以能长期漂移, 正因为漂移让柱子留在云里面 —— 所以**任何**让两者一致的
#      修法都会让 poke 变大; 这不是本档的缺陷, 是这个几何的固有代价。
_TWOIMPL_DEFAULT = 3
_TWOIMPL = _twoimpl_probe()
# 🔴 **2026-10-09: 下限 0.70 → 0.20。** 用户报「目前的沙珠是一个规整的直线」。
#    量出来: 设备 1080×1920 柱子从出口往下 431 行, 左/右缘对**直线**的残差只有 2.49/2.73px。
#    **两个成因, 缺一不可**(单独修任一个都无效, 都实测过):
#      ① 节点分布 —— 自由段只有 4 个节点挤在出口下 40px, 之后是一条直线弦(见
#         `NECK_FREE_EXTRA_SEGS`)。单改节点: 当前律在 40px 后恒 0.70 ⇒ 多打的点仍共线。
#      ② **平台的来源就是这个 0.70 下限** —— `target` 在 d≈12.7px 处就跌到 0.70, 之后
#         恒等于它。单改下限三臂(0.70/0.35/0.10)实测直度残差几乎不动(2.49→2.29→2.26),
#         只是那条**直线变陡**(底宽 37→22→20)。
#    ⇒ 两件一起做才出曲线。取证: `shrinkmin=0.10` 臂里 `b_sat = 4(m⁻⁴−1) ≈ 4·10⁴ px`
#      远超落程 ⇒ **提前饱和短路永不触发, 走的就是无钳位的 A·v 律**。
#    ⚠️ **一个常数同时管两条路**(`_free_width_ratio` 的柱 + `update_particles`/`flow_numpy`
#      的粒子), 所以柱与云仍然**同一条式子**、不会重新分叉 —— 这正是 2.19 立下的约束。
#    ⚠️ 0.20 是**安全底**: 本式在整段落程上的渐近值约 **0.32~0.33**(desktop span≈317px →
#       (1/(1+317/4))^0.25 = 0.334; 平板 span≈390 → 0.317), 所以 0.20 **在可见范围内永不咬合**,
#       它只在病态几何下兜底。
# 🔴 **2026-10-09 (2.23): 0.20 → 0.50 —— 用户判「减少的幅度太大了，统一缩小一些」。**
#    2.22 把下限放到 0.20(等于无钳位)后, 底宽 37 → **19px**, 收得过头。
#    0.50 是**折中**: 本式的渐近值约 0.32, 所以 0.50 会在 d = 4·(0.50⁻⁴−1) = **60px** 处咬合
#    ⇒ 前 60px 仍是曲线(节点已按 N1 加密, 曲率画得出来), 60px 之后是 0.50 的斜直线段。
#    底宽预计 37 → **~26px**(= 37 × 0.50/0.70), 正好是 19 与 37 的中间。
#    ⚠️ 这会把下段重新变成直线(N1 修的"曲率"只剩前 60px)。若要**又缓又全程有曲率**,
#      需要引入第二个常数(把整个收缩强度统一乘一个 <1 的系数), 那要同时改
#      `_free_width_ratio` / `update_particles` 标量 / `flow_numpy` 三处 + 饱和点 `b_sat`。
#      本轮**不做**(用户只要"幅度小一点")。
# 🔴 **2026-10-09 (2.24): 收缩摊开的长度(px)。原来是写死的 40px。**
#    **2026-10-09 (2.25) 用户看 2.24 再判: 「应该是**在前 1/4 的距离变窄之后后面不再变窄**
#    (力量被摩擦力等抵消了)」** ⇒ ramp 200 → **110px**
#    (设备上柱子 431 行, 1/4 = 108px; 桌面落程 ~317px 下占 35%—— 要对齐口径得让它跟几何走, 见下)。
#    **2026-10-09 (2.25) 同一条判词还有第二个条件: 「变窄的幅度很小, 可能只有 85%-90%」**
#    ⇒ `FLOW_SHRINK_MIN` 0.50 → **0.87**。
#    两个条件合起来给出的形状正是用户描述的那一条:
#      · `target` 在 d = 4·(0.87⁻⁴−1) ≈ **3px** 处就跌到 0.87 ⇒ 下限不参与"何时收完"的决定;
#      · 于是 **ramp 决定一切**: 1.0 → 0.87 均匀摊在前 110px(= 柱长 1/4), 之后**恒定 0.87**。
#      · 底宽预计 ≈ 46px(管腔 ~48px) —— "几乎和孔一样宽, 只收一点点"。
#    ⚠️ 0.87 落在用户给的 85%~90% 中点; 想微调就改这一个数(柱/粒子共读, 不会分叉)。
#    原判词(2.23): 「应该是**逐渐收窄, 然后几乎不变**, 现在还是**突然变窄**, 不太合理」。
#    根因: 收缩是三段拼的, **全挤在前 60px** —— d=0 起 40px 的线性 ramp 到 0.549,
#    d=60 下限 0.50 咬合, 之后恒定 ⇒ **90% 的收窄发生在最上面 10% 的长度里**。
#    改法: 把 ramp 从 40px 摊到 **200px** ⇒ d=40 只收到 0.910、d=100 到 0.754、
#    d=200 才到 0.50, 之后恒定 —— 正好是"逐渐收窄, 然后几乎不变"。
#    ⚠️ **三处必须同值**(柱 `_free_width_ratio` / 粒子标量 / `flow_numpy`), 否则柱云分叉。
#    ⚠️ 这是**观感尺度常数**, 不是物理量 —— 想更缓/更急就改这一个数。
FLOW_SHRINK_RAMP = 92.0
FLOW_SHRINK_MIN = float(os.environ.get("HG_FLOW_SHRINK_MIN", "0.87")) if _sm is None else _sm
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
# 判死("颗粒原地闪现 + 整条轮廓颤动")的路。这里做的是**平滑演化**: 预烘多帧、
# 相邻帧之间在时间上做过环形平滑, 每帧只做节点数次线性插值。
# ⚠️ r18-1号 2026-10-05 查出: 原来是"**8 秒一轮回**"（`ph=(t/8)%1`）—— 53 秒档会看 6 圈同样的花纹。
#    "循环"和"演化"是两回事。改法: **周期拉长到 48 秒, 同时把谐波次数按比例提高**
#    ⇒ 肉眼看到的运动快慢**不变**（最快的分量周期仍是 ~3.7 秒）, 但一轮回变成 48 秒
#    （53 秒档只看 1.1 圈）。帧数跟着提到 128: 最高谐波 13 ⇒ 每周期至少 8 帧采样。
#    ⚠️ 周期拉长也会把下球 `_sync_mound_frame` 的步进从 7.9Hz 放慢到 2.7Hz
#    （它按整数帧号取, 不插值）。**不做插值**是有意的: `_MoundProfile` 的面积表就是
#    "预烘帧"存在的原因, 逐帧重建会重新制造它 docstring 明令避免的"求解读 A、绘制读 B"裂缝;
#    而 r18-1号 自己判这一步"大概率看不出来"。
#
# ⚠️ **1.108 曾把这三行回退, 理由是"1.107 引入了 `neck texture stays inside the
#    straight conduit` 回归"。那个归因 2026-10-05 被推翻 —— 别照抄它。**
#    `tools/_bisect_rough.py` 实测(重建已自证生效, frames 64→128 可见):
#      五个配置(基线 / 只改 PERIOD / 只改 FRAMES / 只改 HARM / 全改)下
#      `_neck_sand_side()` 的端点**逐位相同** ⇒ 这三个参数**根本不进**那条链。
#    真因是**那条旧断言本身不判别**: 它的余量 `y_bot - points[1]` 是**亚像素**的,
#      同一份代码只换 widget 尺寸: 400×800 +0.58 / 480×800 +0.24 / 760×1460 +0.10  PASS,
#      而 **1096×2214 −0.09 / 800×480 −1.68 FAIL** ⇒ 它守的是"某个尺度下 top_y
#      恰好落在哪"的巧合。断言已重写成真不变量(颗粒落在 `_neck_sand_side()` 里,
#      且检查**全部**已画颗粒而非只看 pool[1]), 负对照 8/8 能抓到越界。
UPPER_ROUGH_PERIOD = float(os.environ.get("HG_ROUGH_PERIOD", "48.0"))
UPPER_ROUGH_FRAMES = 128
UPPER_ROUGH_HARMONICS = (5, 8, 13)   # 谐波次数(整数 ⇒ 整轮严格闭合, 插值不会跳)

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
    """一维加权容量表: Σ w·clamp(a + offset - bottom, 0, top-bottom), 折线求逆。

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
    # 🔴 **带限噪声**(2026-10-06 用户实测: 「起伏调到最大的时候会出现**锐角的、三角形的、
    #   尖尖的**」)。
    #   旧版 = **白噪声 + 一次 3 抽头平滑**, 而 `shape_at` 是**线性插值**
    #   ⇒ 相邻两个控制点之间就是一个**折角**, 幅度一大(档 6)整条沙面就读成一排**锯齿**。
    #   改成 **最低 K 个谐波之和**: 波长 ≥ n/K 个节点 ⇒ 转折天生是**圆滑**的,
    #   幅度再大也只是"起伏更强", 不会变成三角形。
    #   ⚠️ **别再退回白噪声** —— 那条路 2026-10-06 被用户实拍判死。
    K = 5
    sm = [0.0] * n
    for h in range(1, K + 1):
        a = rng.uniform(-1.0, 1.0) / h            # 振幅 ~1/h ⇒ 以低频为主
        ph = rng.uniform(0.0, 2.0 * math.pi)
        for i in range(n):
            sm[i] += a * math.sin(2.0 * math.pi * h * i / n + ph)
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
    # 🔴🔴 **空间也必须带限**(2026-10-06 用户实测: 「起伏放到最大**还是有一个锐角**」)。
    #    旧版 `psis[j] = rng.uniform(0, 2π, size=n)` 是**逐节点独立**的随机相位
    #    ⇒ **时间上平滑, 空间上是白噪声** ⇒ 相邻节点可以差很多, 被 `shape_at` 的
    #    **线性插值**连起来就是**锯齿 / 锐角**。
    #    ⚠️ 1.163 只修了**下球**那条(`_surface_roughness`), **上球这条路没改到** ——
    #       这是本轮的真实教训: **两条独立的生成路, 修了一条不等于修了两条**。
    #    改成 **2 维带限**: 空间频率 `p`(整数 1..P) × 时间频率 `m`(整数, 取 `harmonics`)
    #    的正弦积之和 ⇒ **空间、时间两边都连续**; `m` 取整数 ⇒ **整轮闭合**。
    #    空间基用 `sin(2πp·(i-c)/n)`(`c` = 中心节点) ⇒ **天生在中心为 0**,
    #    与"堆尖保持在入沙轴线上"那条约束**不打架**, 也就**不需要**最后再硬把中心点置 0
    #    —— 那个硬置 0 本身就是一个折角源(邻居有余量、中心被摁到 0)。
    # ⚠️ `P` 只到 4 时空间过于"完美" —— 用户 2026-10-06:「**太光滑了, 就是一个完美的曲线**,
    #    应该有点**不均匀的随机点**什么的」。分成两段:
    #      · **主体**(p=1..4): 振幅 ~`1/p` —— 负责"起伏"的形
    #      · **细节**(p=5..16): 振幅 ~`0.30/p` —— 只占主体的两三成, 波长 ≥4 个节点
    #    ⇒ 既不是完美正弦, **折角也仍是亚像素级**(幅度小 ⇒ 读作纹理, 不读作尖角)。
    #    ⚠️ **不要再往上加更高频/更大振幅** —— 那会退回 1.165 修掉的锯齿。
    P_MAIN, P_DETAIL = 4, 16
    _idx = np.arange(n) - (n - 1) // 2
    comps = []
    for _p in range(1, P_DETAIL + 1):
        _k = 1.0 / _p if _p <= P_MAIN else 0.55 / (_p ** 0.5)
        for _m in hz:
            _A = rng.uniform(0.6, 1.0) * _k
            _psi = rng.uniform(0.0, 2.0 * np.pi)
            comps.append((_A * np.sin(2.0 * np.pi * _p * _idx / n), _m, _psi))
    # 目标 RMS: 与静态版同源(用同一套空间平滑口径算一遍参考值)
    ref = np.asarray(_surface_roughness(radius, amp_frac, seed, amp_frac * 2.0 * radius))
    target_rms = float(np.sqrt((ref ** 2).mean())) or 1.0
    out = []
    for k in range(frames):
        v = np.zeros(n)
        for _spatial, _m, _psi in comps:
            v += _spatial * np.sin(2.0 * np.pi * _m * k / float(frames) + _psi)
        rms = float(np.sqrt((v ** 2).mean()))
        v = v * (target_rms / rms) if rms > 1e-9 else v
        out.append(v.tolist())
    return out


def _mound_base_drop(x, cap_radius=0.0):
    """Localized rounded contact cap, tangent to the original slopes at its edges."""
    slope = MOUND_SLOPE_L if x < 0.0 else MOUND_SLOPE_R
    distance = abs(x)
    if cap_radius <= 0.0 or distance >= cap_radius:
        return slope * distance
    u = distance / cap_radius
    center = (MOUND_SLOPE_L + MOUND_SLOPE_R) * 0.25
    return cap_radius * (center + (2.0 * slope - 3.0 * center) * u * u
                         + (2.0 * center - slope) * u * u * u)


def _mound_shape_array(radius, frac=None, cap_radius=0.0, rough=None):
    """下球轮廓: 原斜坡 + 局部圆钝接触带 + 微粗糙, 无平顶平台。

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
    if rough is None:
        rough = _surface_roughness(radius, frac, MOUND_ROUGH_SEED, limit)
    out = []
    for i in range(n):
        u = (i - half) * dx
        noise = rough[i]
        if cap_radius > 0.0 and abs(u) < cap_radius:
            weight = _smoothstep(0.0, cap_radius, abs(u))
            noise = rough[half] + (noise - rough[half]) * weight
        out.append(-_mound_base_drop(u, cap_radius) + noise)
    return out


class _MoundProfile:
    """下球沙堆的形状解 —— 移植自外部专家 `xingzhuang2.md` §4.3 的参考实现。

    轮廓 = `P(x) = apex + f(x)`, `f` 由**固定的** 65 点控制数组线性插值给出
    (2026-10-04 起按 `dingbu.md` §3 取消平台, 改为微不对称尖堆 + 受限微粗糙)。
    因为 `f` 与沙量无关, 两张径向容量表只在几何变化时重建, 每帧查表求逆。

    单位: 构造时 `radius`/`shape` 是绝对像素, 内部用归一化坐标算面积表,
    返回值 `apex` 是轮廓纵向偏移; 实际接触高度由 `apex + f(x)` 裁剪后给出。
    """

    __slots__ = ("radius", "shape", "xs", "flat", "heap", "slope_l", "slope_r",
                 "_ctab_axes", "_geom_cache")

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
            left = 0.5 * (xs[i - 1] + xs[i]) if i else -radius
            right = 0.5 * (xs[i] + xs[i + 1]) if i + 1 < k else radius
            # Ring-volume weights; pi cancels when using normalized sand fractions.
            weights.append(0.5 * (right * abs(right) - left * abs(left)))
        bottom = [radius - math.sqrt(max(0.0, radius * radius - x * x)) for x in xs]
        top = [2.0 * radius - y for y in bottom]
        self.flat = _MoundArea(bottom, top, weights, [0.0] * k)
        self.heap = _MoundArea(bottom, top, weights, offs)
        # `contact_table_axes` / `geometry_at` 的缓存槽 —— 几何一变这个实例就被换掉,
        # 所以不必另设失效代。
        self._ctab_axes = None
        self._geom_cache = {}

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

    def apex_for_fraction(self, fraction):
        """Direct volume-fraction inversion, without a 2D-area conversion."""
        return self.heap.height_at(max(0.0, min(1.0, fraction)) * self.heap.capacity)

    def raw(self, dx, apex):
        """未裁剪堆面高度(绝对, 离球内底)。"""
        return apex + self.shape_at(dx)

    def bounds(self, dx):
        """该列的球内底/球内顶高度(绝对) —— 理想圆公式, 实际接缝仍交给 Ellipse 裁剪。"""
        r = self.radius
        x = min(max(dx, -r), r)
        half = math.sqrt(max(0.0, r * r - x * x))
        return r - half, r + half

    def _parts(self, dx, apex):
        """一列的 `(球底, 球顶, 未裁剪堆面高度)` —— **三个判定共用的一次求值**。

        ★ 2026-10-07(外部评审 §5.1): 原来 `contact` / `has_sand` / `free_surface`
        **各自**都算一遍 `bounds`(一次 `sqrt`)与 `raw`(一次 65 点插值),
        而绘制时一列里三个都要用 ⇒ 一列 3 次 `bounds` + 2 次 `shape_at`。
        把求值收进这里, 三个判定都走它 —— **算式一个字没改, 只是不再重复求**。
        """
        floor, roof = self.bounds(dx)
        return floor, roof, apex + self.shape_at(dx)

    def contact_np(self, dxs, apex):
        """`contact()` 的**向量版** —— 对一批 `dx` 一次算完, 算式逐字照抄。

        ## 为什么需要它

        `_replay_hits` 对**每一颗命中粒子**按它自己的 x 调一次 `contact`(口径必须与标量
        路径一致, 见那里的注释)。而粒子 x 每帧都不同 ⇒ 那些调用在 `geometry_at` 的缓存里
        **全是 miss**, 每帧几十上百次完整的 `sqrt` + 插值; 还会把缓存冲得定期整体清空
        (8192 上限)。设备分段实测 `_mound_contact_h` **0.183ms/帧**。

        ## 逐位等价

        每个算子次序都照抄 `contact` / `bounds` / `shape_at`:
        - `max(0.0, r*r-x*x)` 那条要写成 `where(d2 > 0, sqrt(max(d2,0)), 0.0)` ——
          直接 `sqrt(maximum(d2,0))` 在 **d2 = NaN** 时给 NaN, 而标量给 0.0(`d2 > 0.0` 为假)。
        - `(dx + r) / (2.0 * r) * (n - 1)` 的分组不许化简。
        - `int(z)` 是**向零截断** ⇒ `np.trunc`, 且只在 `z ∈ [0, n-1)` 那一支被用到
          (越界两支由 `where` 覆盖)。
        - 钳位**先判 `p < floor`**。
        ⚠️ 守卫: `tools/_probe_ctab_equiv.py`(逐位) + `tools/_splash_golden.py`
        (hy 直接进 flare/splash 的坐标 ⇒ 差 1 ULP 就翻红)。
        """
        np = _np
        r = self.radius
        shape = self.shape
        sn = len(shape) - 1
        x = np.clip(dxs, -r, r)
        d2 = r * r - x * x
        half = np.where(d2 > 0.0, np.sqrt(np.maximum(d2, 0.0)), 0.0)
        floor = r - half
        roof = r + half
        z = (dxs + r) / (2.0 * r) * sn
        zi = np.trunc(np.clip(z, -1099511627776.0, 1099511627776.0)).astype(np.int64)
        ic = np.clip(zi, 0, sn - 1)
        shp = np.asarray(shape)
        a0 = shp[ic]
        a1 = shp[ic + 1]
        off = a0 + (a1 - a0) * (z - ic.astype(np.float64))
        off = np.where(z <= 0.0, shape[0], np.where(z >= sn, shape[-1], off))
        p = apex + off
        return np.where(p < floor, floor, np.where(p > roof, roof, p))

    def geometry_at(self, dx):
        """`(球内底, 球内顶, 轮廓偏移)` —— **只跟几何有关**(与 `apex` 无关), 按 `dx` 缓存。

        `H = clamp(apex + off, floor, roof)` ⇒ 一列里那次 `sqrt` 与 65 点插值,
        **每帧算的都是同一个数**。而绘制节点(111 个)、表层标记(~120 次)、尘埃(~25 次)
        反复问同一批 `dx`。实测(真 `_MoundProfile`, 111 点): `column()` **0.070ms → 0.012ms**。

        ⚠️ **按 `dx` 精确相等做键**(float 直接当 key, 无 epsilon) ⇒ 命中时拿到的是
        **同一个 float** ⇒ 逐位等价, 不是"近似相等"。缓存挂在实例上, 几何一变实例
        就被换掉, 天然失效。
        ⚠️ 壁交点(`_mound_wall_cross`)的 `dx` **随 apex 每帧变** ⇒ 缓存会缓慢增长,
        故设上限: 超了整体清空(重建 111 个固定节点只值 0.07ms)。
        ⚠️ 算式与 `bounds` / `shape_at` **逐字相同**(含 `max(0.0, ...)` 在 `-0.0`/NaN 上
        的取值、`(dx+r)/(2r)*(n-1)` 的算子次序)。守卫: `tools/_probe_ctab_equiv.py`。
        """
        cache = self._geom_cache
        hit = cache.get(dx)
        if hit is not None:
            return hit
        r = self.radius
        x = dx if dx > -r else -r
        if x > r:
            x = r
        d2 = r * r - x * x
        half = math.sqrt(d2) if d2 > 0.0 else 0.0
        shape = self.shape
        sn = len(shape) - 1
        z = (dx + r) / (2.0 * r) * sn
        if z <= 0.0:
            off = shape[0]
        elif z >= sn:
            off = shape[-1]
        else:
            i = int(z)
            off = shape[i] + (shape[i + 1] - shape[i]) * (z - i)
        val = (r - half, r + half, off)
        if len(cache) >= 8192:
            cache.clear()
        cache[dx] = val
        return val

    def column(self, dx, apex):
        """绘制用: 一次拿到 `(接触高度, 是否自由表面, 距球底厚度)`。

        三个量与 `contact` / `free_surface` **同定义**, 只是算一次。
        """
        floor, roof, off = self.geometry_at(dx)
        p = apex + off
        y = floor if p < floor else (roof if p > roof else p)
        return y, floor < p < roof, y - floor

    def contact(self, dx, apex):
        """该列的**接触高度**(绝对) —— 粒子/尘埃的判定面, 与绘制同一份定义。"""
        floor, roof, off = self.geometry_at(dx)
        p = apex + off
        return floor if p < floor else (roof if p > roof else p)

    def contact_table_axes(self, n, x_offset, inv):
        """`(offs, floors, roofs)` —— **只跟几何有关**的三条数组, 每个 `_MoundProfile`
        实例只建一次。

        ## 等式

        `contact(dx, apex) = clamp(apex + f(dx), B(dx), U(dx))` —— `f`(轮廓偏移)、
        `B`(球内底)、`U`(球内顶) **三个都与 `apex` 无关**。所以每帧真正变的只有那个
        `apex +`, 其余全是几何常数。
        ⇒ 建表从"每点一次 `sqrt` + 65 点插值 + 三四层调用"降到"一次加法 + 两次比较"。
        实测(真 `_MoundProfile`, 257 点): **0.066ms → 0.012ms(5.6×)**。

        ## 为什么逐位相等

        预计算跑的是**与被替换代码逐字相同的算式**(同一串 `x = dx if dx > -r else -r`
        / `d2 = r2 - x*x` / `z = (dx+r)/two_r*sn` …), 只是**一个几何只跑一次**而非每帧。
        纯函数 + 同一输入 ⇒ 同一 float。`fill_contact_table` 随后只做
        `p = apex + offs[k]` 与**次序不变**的钳位(先判 `p < floor`)。
        ⚠️ 守卫: `tools/_probe_ctab_equiv.py`(逐位) + `tools/_splash_golden.py`
        (表值差 1 ULP 会直接写进贴坡的 `y = _surf` ⇒ 必翻红)。
        ⚠️ **缓存挂在实例上** —— 几何一变 `_mound_profile` 就是新对象, 天然失效。
        """
        key = (n, x_offset, inv)
        cache = self._ctab_axes
        if cache is not None and cache[0] == key:
            return cache[1], cache[2], cache[3]
        shape = self.shape
        r = self.radius
        sn = len(shape) - 1
        two_r = 2.0 * r
        r2 = r * r
        offs = [0.0] * n
        floors = [0.0] * n
        roofs = [0.0] * n
        for k in range(n):
            dx = x_offset + k / inv
            # --- bounds(dx): 逐字照抄 ---
            x = dx if dx > -r else -r
            if x > r:
                x = r
            d2 = r2 - x * x
            half = math.sqrt(d2) if d2 > 0.0 else 0.0
            floors[k] = r - half
            roofs[k] = r + half
            # --- shape_at(dx): 逐字照抄 ---
            z = (dx + r) / two_r * sn
            if z <= 0.0:
                offs[k] = shape[0]
            elif z >= sn:
                offs[k] = shape[-1]
            else:
                i = int(z)
                offs[k] = shape[i] + (shape[i + 1] - shape[i]) * (z - i)
        self._ctab_axes = (key, offs, floors, roofs)
        return offs, floors, roofs

    def fill_contact_table(self, table, x_offset, inv, apex):
        """一次性把整条接触曲线填进 `table` —— **`contact()` 的保序内联**, 逐位等价。

        ## 为什么要单独来一份(而不是循环调 `contact()`)

        设备实测: 每帧重建 257 点, **循环调 `contact()` 要 0.40ms**(峰值帧, 占
        `update_particles` 的 10.6%)。拆开看, 1.56µs/点里绝大部分是**四层 Python 调用**
        (`contact → _parts → bounds` + `shape_at`) —— 而这条曲线一帧只要一条。
        桌面计时对照(257 点, 300 轮): **0.182ms(调) vs 0.082ms(内联)**, 2.2×。

        ## 🔴 它与 `contact()` 的关系: **同一份公式, 两次抄写**

        项目原本的规矩是"定义只有 `contact` 一份, 建表就是调它"。这里是**有意的例外**,
        代价必须用守卫抵掉:
          - `tools/_probe_ctab_equiv.py`: 多组随机 (radius, shape, apex) 下逐位比对
            内联结果与 `contact()` 的结果;
          - `tools/_splash_golden.py`: 表值一旦差 1 ULP, 贴坡时的 `y = _surf`
            会**直接**写进被记录的轨迹 ⇒ 金标准轨迹必然翻红。
        ⚠️ **改 `contact` / `_parts` / `bounds` / `shape_at` 任何一个, 必须同步改这里,
        并重跑上面两个守卫。** 四个内联的算子次序全部照抄, 不许"化简":
        `(dx+r)/(2r)*(n-1)` 不能写成 `(dx+r)*(n-1)/(2r)`, `p < floor` 必须先判。
        """
        offs, floors, roofs = self.contact_table_axes(len(table), x_offset, inv)
        for k in range(len(table)):
            # 每帧真正变的只有 `apex +` 这一项, 其余全是几何常数(见 `contact_table_axes`)
            f = floors[k]
            p = apex + offs[k]
            roof = roofs[k]
            # 钳位次序**必须**先判 floor(与 `contact()` 逐字一致)
            table[k] = f if p < f else (roof if p > roof else p)

    def has_sand(self, dx, apex):
        """该列有没有沙(自由表面/填满都算有; P ≤ B 才是裸露球底)。"""
        floor, _roof, off = self.geometry_at(dx)
        return apex + off > floor

    def free_surface(self, dx, apex):
        """该列是不是**真正的自由表面**(沙与空气之间) —— 亮带只画在这里。"""
        floor, roof, off = self.geometry_at(dx)
        p = apex + off
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
# 🔴 **色调抖动的中点**(`_flow_table` 下标; 5 == `sand_base`)。
#   **不是 5** —— 这一点反直觉, 但是设备实测出来的:
#   沙流是**按下标分桶、按下标顺序提交**的, 而 Kivy 里**后提交的画在上层** ⇒
#   高下标的颗粒**压在**低下标的上面, 于是**可见像素系统性地偏向上半段**。
#   设备实测(50s 档, 沙流带 vs 沙堆带的逐色直方图): 中点定在 5(=base) 时,
#   沙流带里只看得见 `_flow_table[6..9]` 与高光色, **[0..5] 全被盖住** ⇒
#   沙流均值比沙层亮 **7~11 级**(用户在真机上看到的就是这个)。
#   把中点下移到 2 恰好抵消这个可见性偏差(每档 ≈ +2.5R/+3.5G/+3.6B)。
#   ⚠️ 改它之前先重量一次直方图 —— 这个数是**量出来的**, 不是推出来的。
FLOW_TONE_CENTER = 2
# 🔴 **2026-10-10: 开关(也是回退口)**: `0` = 退回"出生相位 → 色调档"的老取色。
#    **出货默认 0** —— 这条路径**试过、量过、没帮上忙**, 留着只作以后排查:
#      · 自证过它真的执行(`HG_GRAIN_TONE=0` vs `=1` ⇒ 9352 px 不同);
#      · 但柱内高频 std 反而从 1.79 掉到 0.97、均值从 170 抬到 181(偏亮偏平)
#        —— 因为"亮度就近取 `_flow_table` 档"会在色表两端截断(色表只有 ±10 R 级,
#        而材质纹理的亮度偏离更大) ⇒ 颗粒挤向最亮那一档。
#    真正解决「精细度不够」的是 **`FLOW_WIDTH_CAP = 1` + `TRAIL_SCALE = 0.5`**
#    (把墨块从 2px×(5~8)px 变成 1px×(2~3)px), 与取色无关。
FLOW_GRAIN_TONE = os.environ.get("HG_GRAIN_TONE", "0") != "0"
# 🔴 **2026-10-10: 色调档的量化步长。**
#    `3` = 旧行为: `idx -= idx % 3` 把可达档磨成 **{0, 3, 6} 三档**
#          (桶数 22→8, 是 2026-10-07 的性能优化)。
#    `1` = 不量化。
#    默认值**跟随取色路径**: 走材质颗粒场时必须不量化(否则细颗粒被磨成 3 档,
#    `HG_GRAIN_GAIN` 也就测不出差别 —— 2026-10-10 实测过这个坑)。
_TQ = os.environ.get("HG_TONE_QUANT")
FLOW_TONE_QUANT = (max(1, int(float(_TQ))) if _TQ
                   else (1 if FLOW_GRAIN_TONE else 3))
# 🔴 **2026-10-10: 沙流颗粒"从材质颗粒场取色"时的放大系数。**
#    1.0 = 原样用材质纹理的亮度偏离; 更大 ⇒ 颗粒对比更强(但会与底下的材质面对不上)。
#    调它的判据是 `tools/_accept_check.py` 的 A1(柱内颗粒量 / 上球 ∈[0.6,1.5])
#    与 A2(连续平填行 ≤2), 外加并排图。
FLOW_GRAIN_GAIN = float(os.environ.get("HG_GRAIN_GAIN", "1.0"))
# 🔴 **2026-10-10: 出口以下那段沙柱的**几何**要加宽, 让它的硬边退到看不见的地方。**
#    病(用户判词): 「整个边缘非常奇怪, 说不规整吧, **他额外带了一层直线**」。
#    实测: 把材质关掉(平色四边形)、把粒子抽掉, 柱子就是一个**边缘笔直的矩形**
#    ⇒ 那条直线是**几何轮廓**; 而"毛"的那层是着色器在轮廓**里面**腐蚀出来的
#    ⇒ 外面永远留一条直边。可见边缘改由着色器里那条**噪声门槛**决定(见
#    `sand_edge_lo/hi`), 几何只负责"够宽, 别露头"。1.0 = 旧行为。
FLOW_FREE_MARGIN = float(os.environ.get("HG_FREE_MARGIN", "1.35"))
# 🔴 **2026-10-10: 末段出生带的两个形状参数**(见 `update_particles` 里那一段注释)。
#    `FLOW_TAIL_NARROW`: 颈管快空时出生带收到原来的几成宽(1.0 = 不收窄 = 旧行为)。
#    `FLOW_TAIL_TAPER` : 同时把横向分布从 uniform 收成 `|u|^p`(1.0 = 不变 = 旧行为)。
#    适应症: 末段那批沙**同时出生、同速下落** ⇒ 铺满整管宽的一块**板**(用户:「就是一个矩形啊」)。
FLOW_TAIL_NARROW = float(os.environ.get("HG_TAIL_NARROW", "0.45"))
FLOW_TAIL_TAPER = float(os.environ.get("HG_TAIL_TAPER", "2.6"))
# 收尾段占**整个计时**的比例(0.04 = 最后 4%)。见 `update_particles` 里那一段。
FLOW_TAIL_SPAN = float(os.environ.get("HG_TAIL_SPAN", "0.04"))
# 🔴 **2026-10-10: 沙流颗粒的线宽上限。0 = 不限制(出货行为: 85% 是 2px)。**
#    为什么要它: 出口以下的**可见面就是粒子层**(覆盖柱宽 89%), 而它的墨是
#    `2px 宽 × 5~8px 高` 的块 ⇒ 读起来是"块"不是"颗粒"(用户判词「精细度都不够」)。
#    ⚠️ 只盖结果, **不挪随机数流** —— 位置/相位/寿命一字不变。
FLOW_WIDTH_CAP = int(float(os.environ.get("HG_FLOW_WIDTH", "1")))
# 🔴 **设备可达通路**: `FLOW_WIDTH_CAP` 原来是**只读环境变量**的, 而环境变量到不了安卓 app
#    ⇒ 设备上根本没法做这个单变量对照(本项目的老坑)。这里补一个与 `main.py` 同目录的
#    标记文件 `flowwidth`(内容 = 线宽上限, `0` = 不限):
#        adb shell "echo 2 > /data/data/org.shalou.hourglass/files/app/flowwidth"
#        adb shell rm  /data/data/org.shalou.hourglass/files/app/flowwidth     # 回出货默认 1
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "flowwidth"), "r") as _fh:
        _fw = _fh.read().strip()
        if _fw:
            FLOW_WIDTH_CAP = int(float(_fw))
except Exception:
    pass
# 调色板**每档多少 R 级**(等距, 见 `_flow_tone`)。整条 R 跨度 = 2×5×STEP。
# 2.0 ⇒ 跨 20 级; 配合中点 2 之后实际用到第 0~6 档 = **R 207~219**,
# 与**沙堆实测的 208~219** 基本重合(这是"不发黑"的判据)。
FLOW_TONE_STEP = 2.0
# 🔴 **2026-10-10: 沙流的"随深度变亮"斜率**(档/整条柱高)。默认 0 = 保持 10-06 的"以 base 居中"。
#    来历: 2026-10-06 用户报「沙子流和沙层颜色差异过大」⇒ 当时把这条斜率**删掉**了
#    (注释原文:「不再有'越往下越亮'的深度斜率 —— 沙堆没有那条斜率, 沙流也不该有」),
#    实测依据是沙流底部比沙堆亮 13R/21G/20B。
#    **但现在(1080 口径 = 真机物理像素)量到的东西相反**:
#        v1.2(用户说好看的旧版)  柱心−沙体 = **+11.3 级**, 且沿柱身四段 171.5→184.7(**+13.2**)
#        2.31(现在)              柱心−沙体 = **+1.6 级**, 沿柱身四段 168.9→173.0(**+4.1**)
#    ⇒ **两次用户判词在这一根轴上相反**。所以做成梯度臂, 不悄悄改默认值。
_flow_slope = os.environ.get("HG_FLOW_SLOPE")
try:
    FLOW_TONE_SLOPE = 0.0 if _flow_slope is None else float(_flow_slope)
except ValueError:
    FLOW_TONE_SLOPE = 0.0

# 🔴 沙流的**基础生成率**(颗/秒) —— **与周期无关**(2026-10-06, 用户报「沙流和沙堆差距过大」)。
#
# 历史: 原来是 `600 * speed_factor` = `600 * clamp(60/duration, 0.5, 2.5)`,
# 即 1500/s(≤24s) 一路降到 300/s(≥120s)。文档给的理由是"抵消孔径随周期收窄,
# 使**面密度**持平" —— 但那条理由成立于 `neck_w` 还是 `10*(60/d)**0.4`、
# 从 22px 收到 8px(**2.75×**)的年代。现在 `neck_w` 是 **log 插值**,
# 只从 17px 收到 13px(**1.31×**) ⇒ 补偿超了约 **3.8 倍**。
#
# 设备实测(1080x2400, 量具 `tools/_probe_stream_ink.py`; 覆盖率 = 自由落体段
# 每行沙色像素 / 该行沙流宽度):

# | 周期 | rate/s | 线密度 颗/px | 沙流每行覆盖率 |
# |---|---|---|---|
# | 50s  | 720 | 2.80 | **100%** |
# | 600s | 300 | 1.16 | **72%**  ← 28% 的像素透出背景 |
#
# 沙堆是由 carve 抠出的**不透明实心**多边形 ⇒ 覆盖率恒 100%。600s 档沙流于是
# 读成"一串断续小珠", 与沙堆"差距巨大"(用户原话)。这也正合用户早就说过的
# 心智模型: 「下落的沙子的速度是固定的, **理论上在大部分飞行时间中沙子的情况
# 都是近似固定的**」。
#
# 取 1500 = 原公式在 ≤24s 档的取值 ⇒ **那些档一点没动**(1s 档还要再乘
# `_particle_motion_scale`, 同样不变), 只有 >24s 的档变密。
# ⚠️ **不要再按孔径去调它** —— 那会把"孔径↔周期"的约束用第二次(过定),
#    且会把已经修掉的"长周期虚线"重新做回来。
#
# ⚠️ 但**它可以为了帧率整体调低** —— 见下面 `_flow_rate_probe()`:
#    在途粒子数 = rate × 飞行时间, 而 `物理` 与 `图元` **两栏都正比于在途粒子数**
#    (实测 15s 档峰值 2755 颗)。这是**唯一能把两栏一起按百分比压下去**的旋钮。
#    代价是沙流线密度(颗/px)同比例下降 —— 那是**观感**, 由用户裁决。
def _flow_rate_probe():
    """`HG_FLOW_RATE` 环境变量优先(桌面), 其次与 main.py 同目录的 `flowrate` 标记文件。

    🔴 安卓 app **读不到宿主 shell 的环境变量** ⇒ 设备上做"改 rate"的单变量对照只能
    写标记文件(app 私有目录, 与 `prof.on` / `blit.off` 同一套):
        adb shell "echo 1000 > /data/data/org.shalou.hourglass/files/app/flowrate"
    两个都没有 ⇒ 返回 None ⇒ 走出货默认值。
    """
    env = os.environ.get("HG_FLOW_RATE")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "flowrate"), "r") as fh:
            text = fh.read().strip()
        return float(text) if text else None
    except Exception:
        return None


FLOW_RATE_PROBE = _flow_rate_probe()
FLOW_BASE_RATE = FLOW_RATE_PROBE if FLOW_RATE_PROBE else 1500.0


def _trail_scale_probe():
    """`HG_TRAIL_SCALE` 环境变量优先(桌面), 其次与 main.py 同目录的 `trailscale` 标记文件。

    🔴 **2026-10-10 (A5): 用来证伪/证实"纤维感来自线太长"这条。**
    可见的粒子墨迹是 `Line`，高度 `trail = max(2, |vy|·trail_time/motion_scale)`
    （`trail_time ∈ [0.018, 0.032]`）⇒ 柱中 vy≈500 时 **9~16px 高、2px 宽**，
    实测柱区纹理的"竖/横自相关比"= **2.00~2.50**，而上球沙体是 **1.00**。
    把这个系数调小，线就变短、词汇由"竖条纹"变"颗粒"。
    **若把它调到 0.2 而自相关比纹丝不动 ⇒ "线太长"这条被直接证伪**
    （那就说明纤维感来自"缝被那块板填了"——见 `sand_hole_th`）。

    安卓读不到环境变量 ⇒ 设备单变量走标记文件:
        adb shell "echo 0.2 > /data/data/org.shalou.hourglass/files/app/trailscale"
    """
    env = os.environ.get("HG_TRAIL_SCALE")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "trailscale"), "r") as fh:
            text = fh.read().strip()
        return float(text) if text else None
    except Exception:
        return None


def _stream_spread_probe():
    """粒子**出生点沿 y 摊开的范围**(设备像素)。`HG_STREAM_SPREAD` 环境变量优先,
    其次与 main.py 同目录的 `streamspread` 标记文件。默认 0 = 逐字旧行为。

    为什么要有它: 所有粒子都在同一条 y(`gen_y`)上出生 ⇒ 粒子层的**上边缘是一条几何直线**,
    而出口以上只有材质面 ⇒ 用户看到一条"死横线"(实测: 柱心高通 std 在出口由 2.1 跳到 3.9,
    再往下 9.5~23)。摊开之后那条边缘变成一条有范围的随机带。
    """
    env = os.environ.get("HG_STREAM_SPREAD")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "streamspread"), "r") as fh:
            text = fh.read().strip()
        return float(text) if text else None
    except Exception:
        return None


_SPREAD = _stream_spread_probe()
# 🔴 **2026-10-10 定为出货默认 2.0 × 直筒高度**(瓶颈 = `_tube_h = h*0.055`)。
#    用户:「过渡层不用太高, 不超过瓶颈高度的 2 倍吧。或者 1 倍」(1× / 2× 都行, 我定)
#    标定(桌面 1904×2890, tube_h=159px; 柱心 ±25px 高通std, 出口以上 1.83 为基准):
#        spread 0   → 刚过出口 3.93  (阶跃 2.15×)
#        spread 140 = 0.88×tube    → 3.23  (1.76×)   ← 1× 基本没用
#        spread 320 = 2.0×tube     → 2.72  (1.49×)   ← **选定**
#        spread 700 = 4.4×tube     → 1.65  (0.90×)   ← 线没了, 但柱子明显变稀
#    ⇒ 取用户给的上限 2×: 1× 实测几乎无效, 再大就开始牺牲密度。
#    ⚠️ 是**比例的**不是像素 —— 写死像素会在平板/手机上差好几倍。
# 🔴 **2026-10-10 补: 上面写的"出货默认"此前**从没落到代码里**(代码是 `0.0 if None`),
#    于是这个已标定的解药一直没生效。现已改成默认 **2.0**。
#    为什么它是"一套渲染"那件事的一半: 出口以下那一段的**可见面是粒子层**
#    (实测覆盖柱宽 **89%**, 材质被压在下面), 而所有粒子都在**同一条 y** 上出生
#    ⇒ 粒子的上边缘是一条几何直线 ⇒ 出口那一行"从材质面硬切成粒子面"。
#    实测(PC 400×875 / 10s / t=7.40, 柱内高频 std 中位):
#        改前 1.01  →  出生点摊开 2.0×直筒高  **1.79**  (材质面自身 = 2.73)
#    回退: 环境变量 `HG_STREAM_SPREAD=0` 或标记文件 `streamspread` 写 `0`。
# 🔴 **2026-10-10 二次裁定: 回到 0.0(关)。**
#    2.35 把它当"出口那条死横线"的解药打开, **但它在开局是错的**: 它把新生粒子放到
#    `gen_y` **以下**最多 `2×tube_h`(设备上 ~200px) —— 那些沙**还没落到那儿**。
#    用户判词:「前60帧还tmd一堆bug」。逐帧实拍(蓝沙, 前 1s):
#        t=0.26 柱子下沿还在上面, 底下已经散着一片粒子  ← 凭空出现
#        t=0.34~0.68 柱子下方炸开一团弥散粒子云, 柱子本身在一条硬横线上截断
#    它当初要解决的"出口硬线"现在由**材质那一层**解决了(芯部实心 + 边缘按深度渐入)
#    ⇒ 这剂药已经没有适应症, 而副作用是"沙在半空出现"。
#    (留 `HG_STREAM_SPREAD` / 标记 `streamspread` 作以后的实验旋钮。)
STREAM_SPREAD_RATIO = 0.0 if _SPREAD is None else max(0.0, _SPREAD)

TRAIL_SCALE = _trail_scale_probe()
# 🔴 **2026-10-10 定为出货默认 0.5**(此前是 0.8)。
#    为什么改: 用户对 2.35 的判词是**「精细度都不够」**。实测(PC 400×875 / 10s / t=7.40,
#    柱内高频 std 中位, 材质面自身 = 2.73; 配合下面 `FLOW_WIDTH_CAP=1`):
#        拖尾 0.8(=2.35) → 1.79      拖尾 0.5 → **2.93**      拖尾 0.25 → 2.85
#    0.5 与 0.25 差在噪声内, 取更保守的 0.5。
#    ⚠️ 旧记录里"拖尾 0.2/0.4 ⇒ 太稀疏"是在**底下那层材质只有 28% 不透明**的年代量的
#      (见 `sand_flow_material.py` 里 `coverage = solid` 那一段); 现在底层是 98~100%
#      实心的颗粒面, 实测 1px 墨 + 拖尾 0.5 时柱内**背景色 0.0%、沙色 99.8%** ⇒ 不会稀。
#    旧标定(供参考): 1.0 → 背景 0.0% / 各向异性 2.34~2.51; 0.8 → 9.4% / 1.88~2.23;
#    「最右边 2 个的沙子更真实，不过太稀疏了」⇒ 要那两格的质感、但更密）。
#    标定（固定 `holeth=0.10`、只改这一个旋钮，柱内露出背景占比 / 柱区竖横自相关比）：
#        1.0 → 0.0% / 2.34~2.51     0.8 → **9.4%** / 1.88~2.23（选定）
#        0.6 → 18.5% / 1.31~1.44    0.2 → 26.5% / 1.16~1.35
#        v1.2(用户说好看) 参照 = 16.7% / 1.54~1.70
#    ⚠️ **密度与各向同性由这一个旋钮反向拉扯** —— 越密越"竖"。0.6 是同时复刻 v1.2 的那一档；
#      0.8 是"比 v1.2 更密"的那一档。改它之前先回去看那张梯子图。
# 🔴 **2026-10-10: 出货默认 0.5 → 0.2。** 用户判词: 「柱子内部仍是**竖条纤维**」。
#    **归因(消融, 平板口径 1904×2890 / t=7.40)**:
#      · 材质单独量(`pooloff`) 柱区 竖/横 = **1.02 / 1.10 / 1.15** —— 与上球同档, **各向同性**;
#      · 把粒子加回来 ⇒ **1.7~2.2**(且 `flowvy=0` / `jumpy=0` 两臂**完全不动**)
#      ⇒ **"纤维"是粒子的速度拖尾画的**(每颗 `trail ≈ 6px` 的竖线), 不是材质。
#    改法: 拖尾缩到 **0.2** ⇒ 除最快那批(16px×0.2=3.2px)外都落到 `max(2.0, …)` 地板
#    ⇒ 每颗读成**一颗沙**, 而不是一道竖线 —— 正是用户点名的 1.2 那版的样子
#    (1.2 的前沿是**一堆离散圆颗粒**)。
#    梯子图 `benchmark_logs/_vid/trail_ladder.png`(0.6 | 0.5 | 0.35 | 0.15, 同一帧同一裁图)。
#    ⚠️ **别再拿"柱区竖/横"这一个数定值** —— 它对窗口极敏感(同一张图 ±40 与 ±45 会给出
#      2.16 与 1.09), 只能当**同一次比对里**的相对量用。
#    ⚠️ 上面那张老梯子表(0.6≈v1.2 / 0.8 选定)是**另一套几何**下标的, 与今天不可直接比;
#      但它与人眼在这张梯子上看到的**方向**一致: 拖尾越短越不"竖"。
# ⛔ **2026-10-11: 0.35 已撤回(2.59)** —— 用户的判词是「**太稀疏了**」, 要的是
#    「颈部和沙柱和上层的沙子**没有明显的区别**」 ⇒ 材质层留下(与上层同一套), 墨量不需要靠粒子撑。
#    (下面这段是当时的推理, 保留作记录)
#    QA 说的对: 2.55 那次这个改动是**空转的** —— 因为当时**材质层还在**(每行 100% 填满),
#    没有"虚线"要防。**现在几何真的撤了**(柱区沙色覆盖 35%→5%), 墨量全靠粒子 ⇒ 才需要它。
TRAIL_SCALE = 0.2 if TRAIL_SCALE is None else max(0.0, TRAIL_SCALE)

# `_apply_max_refresh_rate()` 把 `Display.getSupportedModes()` 的**整个列表**写在这里,
# 由基准日志的 `refresh_modes=` 带出来(桌面/无安卓时为 None)。
# 为什么要它: 用户那台 Y700 五代是 **165Hz 屏**, 而日志里 `refresh_hz` 一直只有 **120**
# —— 不把"系统到底给了哪些档"记下来, 就分不清是**系统没开**(要在显示设置里选极致刷新率)
# 还是**我们没要到**(请求方式不对)。见 `_apply_max_refresh_rate` 的注释。
REFRESH_INFO = None


def _refresh_cap():
    """**请求的刷新率上限**(2026-10-07)。`0` = 不设上限(要屏幕最高档)。

    ## 为什么要有这个旋钮

    165Hz 上我们的**余量极薄**: 165Hz 的预算是 6.06ms, 而平板实测三栏和
    (物理+图元+Canvas) p50 已经 **4.04ms**、p90 **6.55ms** ⇒ **p90 已经超预算**,
    14.5% 的帧的活干不完 ⇒ 掉一格 vsync(→12.1ms), 那就是 1% low 里那 5.4% 的帧。

    **换档位比抠代码的杠杆大得多**: 144Hz 的预算是 **6.94ms(+0.88)**、120Hz 是 **8.33ms(+2.27)**。
    DanZhu 在**同一台设备**上量过同一个取舍(他们的记录): `cap=120` ⇒ 平均 160.5 / 1%Low **118.9**;
    `cap=165` ⇒ 平均 165.1 / 1%Low **112.1** —— **平均 −3% 换 1%Low +6%**。
    机制: 预算变大 ⇒ 掉格的帧变少 ⇒ 尾部抬高。

    ⚠️ **这是口味取舍(平均 vs 尾部), 归用户定** —— 所以这里只装旋钮, **默认 0**(与今天一致)。
    设备侧只能写**标记文件**(环境变量到不了 app):
        adb shell "echo 144 > /data/data/org.shalou.hourglass/files/app/refreshmax"
        adb shell rm  /data/data/org.shalou.hourglass/files/app/refreshmax     # 回到最高档
    日志里 `refresh_modes=` 会带出 `cap=` —— **标记文件没被读到的话那一臂是空转**。
    """
    env = os.environ.get("HG_REFRESH_MAX")
    if env:
        try:
            return max(0.0, float(env))
        except ValueError:
            pass
    try:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "refreshmax")
        if os.path.exists(_p):
            with open(_p) as fh:
                return max(0.0, float(fh.read().strip() or 0))
    except Exception:
        pass
    return 0.0


REFRESH_CAP = _refresh_cap()


def _peak_refresh_cap(activity):
    """系统侧"峰值刷新率"上限(`Settings.System.PEAK_REFRESH_RATE`), 读不到返回 0。

    🔴 为什么必须读它(2026-10-07, 照抄 `DanZhu` 的 `_android_system_fps_cap`):
    很多机器(含联想这台)的**显示设置里有一个"刷新率/峰值刷新率"档**, 系统会用这个值把
    所有窗口的请求**夹住** —— 用户把它设成 120 时, app 怎么申请都拿不到 165。
    **不读它, 就分不清"系统没开"与"我们没要到"**, 而那两件事的处理办法完全相反
    (一个要用户去设置里改, 一个要改我们的代码)。
    """
    try:
        settings = __import__("jnius").autoclass("android.provider.Settings$System")
        cap = float(settings.getFloat(activity.getContentResolver(),
                                      settings.PEAK_REFRESH_RATE, 0.0))
        return cap if cap > 1.0 else 0.0
    except Exception:
        return 0.0


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

    def __init__(self, size, rgba, shade=1.0, grad=0.10):
        self.rgba = rgba
        self.flow_shade = shade
        self.flow_grad = grad
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


class _QuadBand:
    """N 个四边形 = **一个 `Mesh`** —— 逐像素等价, 指令数却从 2N 掉到 2。

    ## 为什么(2026-10-07)

    画布普查 + 消融(`tools/_probe_canvas_cost.py`): 每条指令每帧都要走一次 `apply()`,
    桌面实测 **~1.0~1.4µs/条**; 而且**每条带纹理的顶点指令还跟着一条自己的 `BindTexture`**
    ⇒ 真实开销要按 2 条算。`_mound_carve`/`_mound_band` 各 200+ 条、`_upper_*` 各 65 条,
    四族合计 548 条 `Quad` ≈ 1100 条指令, 占画布总指令数(1669)的三分之二。
    合成 4 个 `Mesh` 之后这一整族消失。

    ## 逐像素等价的依据(`_qa/quadmesh_ab.py`, 同机实测)

    ① 同一组 `points`, 逐 `Quad` 与单 `Mesh`(`vertices=[x,y,u,v]×4n`,
       `indices=[b,b+1,b+2, b,b+2,b+3]`)画出来**逐像素 0 差异** —— 用例里**含一个凹四边形**,
       因为凹角处两种三角剖分本来就会给出不同结果, 不验它等于没验;
    ② **预分配 list 原地改、再重新赋值**照样生效(Kivy 的 list 属性不按同一性短路)。
       这一条是必须的: 否则为了"让属性认得出变化", 每帧都得白复制一份三千多个 float。

    ⚠️ **`flush()` 每帧必须调一次** —— 顶点是在**赋值那一刻**才标脏上传的, 只改 list 不赋值
       等于什么都没发生(静默失效: 画面冻住, 一行报错都没有)。
    """

    __slots__ = ("mesh", "bind", "_v", "_uv", "_n", "_zx")

    def __init__(self, n, texture=None):
        # uv 与默认纹理**向一个空 `Quad` 要**, 不猜 Kivy 的默认值(猜错就是整条带偏色)
        probe = Quad(points=[0] * 8, texture=texture)
        self._n = n
        self._uv = [float(x) for x in probe.tex_coords]
        self._v = [0.0] * (n * 16)
        self._zx = (0.0,) * (n * 4)
        idx = []
        for i in range(n):
            b = i * 4
            idx += [b, b + 1, b + 2, b, b + 2, b + 3]
        self._uv_fill()
        # ⚠️ `BindTexture` 必须**写进指令流**(与逐 `Quad` 时 Kivy 自动加的那条同义);
        #    只给 `Mesh.texture` 赋值**不等于**绑上了纹理。见 `flow_splash_experiment` 里
        #    同一条教训("建了 BindTexture 不等于绑了这张纹理")。
        self.bind = BindTexture(texture=probe.texture)
        self.mesh = Mesh(mode="triangles", vertices=self._v, indices=idx,
                         texture=probe.texture)

    def __len__(self):
        return self._n

    def __getitem__(self, i):
        if i < 0:
            i += self._n
        if not (0 <= i < self._n):
            raise IndexError(i)
        return _QuadView(self, i)

    def __iter__(self):
        for i in range(self._n):
            yield _QuadView(self, i)

    def _uv_fill(self):
        """把 uv 写满一次 —— **顶点布局是 `[x, y, u, v]`**, 所以 uv 落在下标 ≡2,3 (mod 4),
        而 x/y 落在 ≡0,1。两者互不重叠 ⇒ 更新位置时**根本不必碰 uv**, 它在
        `__init__` / `set_texture` 之后就是常量。"""
        v, u, n = self._v, self._uv, self._n
        v[2::16] = [u[0]] * n
        v[3::16] = [u[1]] * n
        v[6::16] = [u[2]] * n
        v[7::16] = [u[3]] * n
        v[10::16] = [u[4]] * n
        v[11::16] = [u[5]] * n
        v[14::16] = [u[6]] * n
        v[15::16] = [u[7]] * n

    def set(self, i, pts):
        """只写 8 个位置 —— 两条**步长 4 的切片赋值**(C 级), 不是 16 个下标写。

        `pts` 的顺序与 `Quad.points` 一致(4 组 x,y)。
        """
        o = i * 16
        v = self._v
        v[o:o + 16:4] = (pts[0], pts[2], pts[4], pts[6])
        v[o + 1:o + 17:4] = (pts[1], pts[3], pts[5], pts[7])

    def set_uv(self, i, pts, uvs):
        """位置 + **逐条 uv**(颈部沙柱要按实际半宽/直径取纹理坐标 ⇒ 每帧都变)。"""
        o = i * 16
        self._v[o:o + 16] = (pts[0], pts[1], uvs[0], uvs[1],
                             pts[2], pts[3], uvs[2], uvs[3],
                             pts[4], pts[5], uvs[4], uvs[5],
                             pts[6], pts[7], uvs[6], uvs[7])

    def zero(self, i):
        """单条退化 —— 等价于 `quad.points = [0] * 8`(uv 留着, 退化三角形出不了像素)。"""
        o = i * 16
        v = self._v
        v[o:o + 16:4] = _ZERO4
        v[o + 1:o + 17:4] = _ZERO4

    def clear(self):
        """整条退化 —— 两条**整表步长 4** 的切片赋值, 一次 C 级循环, 没有 Python 逐条。"""
        self._v[0::4] = self._zx
        self._v[1::4] = self._zx

    def set_texture(self, tex):
        """换纹理 —— **两边都要写**: 只改 `Mesh.texture` 不会动那条 `BindTexture`
        (真正生效的是后者), 只改 `BindTexture` 又会与 `Mesh` 自己的记录不一致。
        uv 也按新纹理的默认值重铺一遍(`Quad` 的 `tex_coords` 就是这么来的)。"""
        self.bind.texture = tex
        self.mesh.texture = tex
        self._uv = [float(x) for x in Quad(points=[0] * 8, texture=tex).tex_coords]
        self._uv_fill()

    def flush(self):
        self.mesh.vertices = self._v


_ZERO4 = (0.0, 0.0, 0.0, 0.0)


class _QuadView:
    """**只读**的"第 i 个四边形"视图 —— 给 `tools/` 下那批按 Quad 列表写的探针用。

    🔴 2026-10-07: `_upper_*` / `_mound_*` / `_neck_quads` 从"Quad 列表"变成了
    `_QuadBand`(一个 `Mesh`), 于是 `_probe_neck_uv` / `_probe_neck_ablate` /
    `_r1_probe3` / `_rv2_geom` 四个取证探针当场 `TypeError: not subscriptable`。
    对抗审查专家的原话: **"改动本身没问题, 但它拆掉了自己的证据链"** —— 性能优化
    把复现"优化前结论"的量具弄坏了, 那条结论就再也回不去。这里补上只读协议接回去。

    ⚠️ **写入一律不许** —— 真值在那块扁平顶点数组里, 逐条写会绕过 `flush()`,
    静默失效(改了但不提交)。要改就走 `set`/`zero`/`set_uv`。
    """

    __slots__ = ("_b", "_i")

    def __init__(self, band, i):
        self._b = band
        self._i = i

    @property
    def points(self):
        v = self._b._v
        o = self._i * 16
        return [v[o], v[o + 1], v[o + 4], v[o + 5],
                v[o + 8], v[o + 9], v[o + 12], v[o + 13]]

    @points.setter
    def points(self, pts):
        """逐条写顶点 —— **走 `_QuadBand.set`**, 只动位置、不动 uv
        (原来 `Quad.points = ...` 也不动 `tex_coords`, 行为一致)。
        ⚠️ 写入**不会** `flush()` —— 与逐 `Quad` 时代一样, 提交由调用方那一帧的
        `flush()` 负责。"""
        self._b.set(self._i, list(pts))

    @property
    def tex_coords(self):
        v = self._b._v
        o = self._i * 16
        return (v[o + 2], v[o + 3], v[o + 6], v[o + 7],
                v[o + 10], v[o + 11], v[o + 14], v[o + 15])

    @property
    def texture(self):
        return self._b.bind.texture

    @texture.setter
    def texture(self, tex):
        # ⚠️ 逐条换纹理对整条带是**全局**效果(一个 Mesh 只有一张纹理) —— 与逐 Quad
        #    时代"每 Quad 各自一张"不同。探针若靠逐条换纹理来做对照, 得改成整条换。
        self._b.set_texture(tex)


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
            material = _SandMaterial(size, rgba, shade=shade, grad=SAND_MATERIAL_GRAD)
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
        return _SandMaterial(size, rgba, grad=SAND_MATERIAL_GRAD)
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

# 批处理块的**预热**(见 `HourglassWidget._warm_batches_step`)。
# 建一块的代价: 沙流那块设备实测 ≈6ms(512 槽 × 12 顶点 × float32 的顶点表 + 纹理 +
# 索引数组), 飞溅/颈部/闪光那几块钱小得多 ⇒ 分开估。预算 6ms/帧 就是"一帧最多建一块
# 沙流块", 免得把 24+37 块挤在一帧里又造出一次冻结。
WARM_COST_FLOW = 6.0
WARM_COST_SMALL = 1.5
WARM_BUDGET_MS = 12.0     # 一帧最多建 2 块沙流(见下: 预热深度按几何算, 12ms 才赶得完)
# 🔴 **每个沙流桶要预热几块, 现在是按几何算出来的, 不是一个常数**(2026-10-07 晚)。
#    历史: 先 1 块 → 用户平板复测发现 3 个桶超 512 ⇒ 改 2 块 → 仍被抓到。
#    1.221 的平板 log 里每档**必然有一帧** `图元` 9.2~12.5ms(稳态 2.0), 同帧
#    `ia=1 / vrb=160 KiB` —— 就是"运行期现建一块沙流"(512 槽 × 20 顶点 × 4 float × 4B
#    = 160 KiB, 设备 ~7.7ms)。落在 t=0.38 / 1.81 / 1.83s。
#    ⚠️ **本机一直复现不出来**: 桶里有多少颗 ∝ 粒子的下落路程 ∝ 窗口高度。
#       把桌面窗口调成平板的 1904×2890 之后**一次就复现**(`tools/_chunkneed*.py`):
#       最热的桶峰值 **1525 颗** ⇒ 要 **3 块**, 而只预热了 2 块。
#       ⇒ 这就是"桌面测不出来"的又一处: **不是慢, 是几何不够大**。
#    ⇒ 判据改成"**在途粒子数上界 × 最热桶占比 / 块容量**":
#       `在途 ≈ rate × 飞行时间 = (FLOW_BASE_RATE × motion_scale) × (natural / motion_scale)`
#       = **FLOW_BASE_RATE × `_natural_flight_time`** —— 与 motion_scale 无关(两项正好抵消),
#       所以 1s 档和 15s 档拿到同一个上界(与实测一致: 平板两档峰值都在 3100 附近)。
#    ⚠️ 桶占比 0.55 是**实测的**: 色调合并后 5/9 的档落进最热的桶(`idx -= idx % 3` 前
#       在 0 处被 clip), 其中 85% 是 size=2 ⇒ 0.556×0.85 ≈ 0.47, 再留 1.2× 余量。
WARM_FLOW_CHUNKS = 2          # **下限**(短周期/小窗口时的值, 与原行为一致)
WARM_FLOW_BUCKET_SHARE = 0.55  # 最热那个桶占在途粒子的比例上界(实测 0.49)
WARM_FLOW_CHUNKS_MAX = 8      # 上限, 防病态几何把预热队列撑爆
# 飞溅峰值(用户平板实测 3984 颗)⇒ 8 块(每块 512)。这几块钱小得多(4 顶点/颗)。
# 🔴 2026-10-09: 颈部沙柱的下沿**跟着沙走**(而不是钉在玻璃的"管口"线上)。
#    **用户看完并排图后定的就是这一版**(并排图 benchmark_logs/_vid/seam/ZOOM49b.png)。
#    关掉它用 `HG_NECK_JOIN=0`(回退到"下沿钉在玻璃线上"的老行为), 便于 A/B 与取证。
NECK_JOIN = os.environ.get("HG_NECK_JOIN", "1") == "1"
# 🔴 2026-10-09: 颈部沙柱往下长到**在途沙的前沿**为止, 不是长到沙堆面为止。
#    **用户当场指出的**: 「你tmd自己能不能用50s这个周期去跑下模拟, 搞个截图, 看看前2秒
#    是否合理?」—— 实测(50s 档, 平板口径)前 2 秒里, 柱子**从出口一路插到球内底**、且
#    **一个粒子都没有**(沙还在半路), 是一根贯穿整个下球的棍子。这正是 1.238 的死因:
#    下沿钉在 `get_mound_top_y()` 上, 而开局那几秒沙堆还在球底 ⇒ 那个标量 ≈ **球内底**。
#    前沿 = `min(self.py[:self.pn])` = **沙真的落到了哪儿** ⇒ 开局只有一小截, 沙落到底
#    自然与沙堆接上。关掉用 `HG_NECK_FRONT=0`(回退成"长到沙堆面", 即 2.13 的行为)。
NECK_FRONT = os.environ.get("HG_NECK_FRONT", "1") == "1"
# 🔴 2026-10-09: 出口以下那段沙柱的**边缘要毛**。用户判词:「沙柱看起来是个**规整的矩形**」
#    —— 两条边死直、平行、通体实心。玻璃在出口就结束了, 以下没有任何东西约束它。
#    关掉用 `HG_NECK_FREE=0`。
# ⛔ **2026-10-11: 默认 1→0 那一刀已撤回(2.57)。它是**空转臂**:
#    自由段那层几何**不走这个开关**(`NECK_FREE_OFF` 只在 `_neck_free_band` 上生效, 而那个带
#    只在 `NECK_OUT_SPLIT` 下才创建) ⇒ 开/关它**逐像素 0 差异**(2.53/2.55 两棵树实测)。
#    **代价是它把关掉的 `free_front` 打散也一起撤了**(F6: 边缘打散 ⇒ 柱子回到"规整矩形")。
#    要"只剩粒子一层"必须去**几何那一侧**动手(`_neck_sand_side` 出口以下的发射), 见 QA 报告。
NECK_FREE = os.environ.get("HG_NECK_FREE", "1") == "1"

# 🔴 **2026-10-11: 出口以下**还发不发射材质几何**(默认 0 = 不发射)。**
#    用户判词: 「**你是看不到外面有一层的, 他是只有一层的**」+「不要做分层了」。
#    ⚠️ **这才是那个开关**: `NECK_FREE` 只管着色器里那个 `free_front`(边缘打散),
#    **几何**是在 `_neck_sand_side` 里**无条件**发射的 ⇒ 2.55 只翻 `NECK_FREE` 是**空转臂**
#    (跨模型测试员两轮独立复现: 开/关 `neckfree` 逐像素 0 差异; 清空颈部四边形后柱子整条消失)。
#    `= 0` ⇒ 多边形在**出口**收尾, 出口以下只剩**粒子**(与 1.2 同构) ⇒ 一并消掉
#      "外面那一层" / 头部那个锥(材质几何画的) / "前沿换堆面"的硬切换(没有几何轮廓可换)。
NECK_FREE_GEOM = os.environ.get("HG_NECK_FREE_GEOM", "1") == "1"
# 🔴 **2026-10-10: 颈部颗粒提亮层在材质路径下是否保留。默认 "1" = 保留(旧行为)。**
#    2.12 把它在材质路径下**整个禁掉**(理由: 那 320 条 Line 一个像素都画不出来)——
#    不画是对的, 但**它的提亮作用也一起没了**。1080 口径逐层消融实测(同一冻结帧 t=7.64):
#        无粒子(只材质板)  柱心−沙体 = **+39.9 级**(板很亮)
#        无板(只粒子)      柱心−沙体 = **+3.7 级**(粒子偏暗)
#        full(两者都有)    柱心−沙体 = **+1.6 级**  ← 粒子把亮板整个盖住
#        v1.2(用户说好看)   柱心−沙体 = **+11.3 级**
#    ⇒ 柱子的可见面是**暗粒子**, 而 v1.2 因为这一层活着而亮 ~10 级;
#      出口那条亮度阶跃**同一个原因**(粒子在出口从无到有, 而它比板暗 ~36 级)。
#    `HG_NECK_GRAINS=0` 可退回 2.12 的行为。
# 出口以下的自由收缩段(vena contracta)。关掉用 `HG_NECK_TAPER=0`。
NECK_TAPER = os.environ.get("HG_NECK_TAPER", "1") == "1"
NECK_TAPER_SEGS = 4          # 收缩段节点数(前 40px 均分)
# 🔴 **2026-10-09: 自由段的**额外**节点数(N1)。**
#    用户报「目前的沙珠是一个规整的直线」—— 量出来是真的: 设备 1080×1920 上柱子
#    从出口往下 431 行, 左/右缘对**直线**回归的残差只有 2.49 / 2.73px ⇒ 两条边就是直线。
#    根因**不是**宽度律, 是**节点分布**: 自由段只有 d=10/20/30/40 四个节点(全挤在出口下 40px),
#    之后直接跳到"前沿"那一点 ⇒ **d>40px 的那 ~350px 就是一条直线弦**, 选什么律都画成直线。
#    实测佐证: 把下限 0.70 → 0.35 → 0.10 三臂, 直度残差几乎不动(2.49→2.29→2.26),
#    只是那条直线变陡(底宽 37→22→20)。⇒ 不加密节点, "该收多细"的讨论都落不到画面上。
#    布点: **前 40px 仍用 4 个(收缩段要密)**, 40px 之后按 `span·(k/(K+1))^1.4` 前密后疏补 K 个。
NECK_FREE_EXTRA_SEGS = 10
# 🔴 **2026-10-10: 沙柱**头部**(前沿那一段)沿宽度打几个节点。**
#    用户判词(附截图): 「这个头部的形状也不对, **固定一个三角形啊**」。
#    病: 前沿原来只用**两个节点**(最外一个点 + 中轴一个点), 而前沿是条抛物线
#    (`_front_at = _front + _bulge·(dx/w)²`) ⇒ 两点之间是**一条直弦** ⇒ 画出来就是个三角。
#    改: 沿半宽打 `NECK_FRONT_SEGS` 段 + 一层**随时间和位置起伏**的抖动
#    (`FLOW_FRONT_WOBBLE`, 相位每次启动不同 ⇒ 每轮头部形状都不一样)。
NECK_FRONT_SEGS = 12
FLOW_FRONT_WOBBLE = float(os.environ.get("HG_FRONT_WOBBLE", "0.45"))
NECK_GRAINS_IN_MATERIAL = os.environ.get("HG_NECK_GRAINS", "0") != "0"
# 前沿的**穹顶**量(以半宽为单位): 边缘比中轴高出这么多。`0` = 平头(2.14 的行为)。
# 🔴 **2026-10-10 三改: 0.80 → 0.30, 而且再按"已经落下去多远"封顶。**
#    用户判词(附设备截图): 「下落到一个地方的时候, **突然伸出尖尖**, 这个时候的宽度给错了」
#    + 「2.40版本的这个尖尖的形状是不对的, **应该是一个不规则的东西**, 你去看看1.2版本是个啥」。
#    **实测 1.2(用户点名)的前沿**: 一堆**离散圆颗粒** + 沙块下缘一条**毛糙的点状边** ——
#    **没有任何几何尖角**。而 `0.80×半宽` 的穹顶在**刚出生**时是什么样, `necklog` 读出来了:
#        t=0.2  outlet=416.2  end_at(w_end)=430.7(在管**里面**)  end_at(0)=416.2
#    ⇒ 边缘比中轴**高 14.5px** ⇒ 那一刻**只有中轴那个尖探出孔口** ⇒ 尖尖 + "宽度给错了"
#      (宽度本该是整条管口, 实际只露出一个点)。
#    `FLOW_FREE_MARGIN=1.35` 又把这个穹顶在外缘放大 `(1.35)²=1.82` 倍 ⇒ 2.43 起更突出。
#    现在: ① 系数降到 0.30; ② 再按**前沿已经落下去的距离**封顶(`NECK_FRONT_DOME_SPAN`)
#      —— 沙刚冒头时前沿还没落下 ⇒ 穹顶 ≈ 0 ⇒ **平着出场**, 落得越深穹顶才越明显。
NECK_FRONT_DOME = float(os.environ.get("HG_NECK_DOME", "0.10"))
NECK_FRONT_DOME_SPAN = float(os.environ.get("HG_NECK_DOME_SPAN", "0.45"))
# 🔴 **2026-10-11: "前沿落到堆面"那一刻的**硬切换**要抹平(用户原话: 「**不可能有这么一个突变**」)。**
#    病(跨模型测试员实测, 两轮独立复现): t=2.06→2.08 之间, 材质带下沿从 **+17px/帧 翻成 −18px/帧**,
#    末端宽度 **66→176→240px** 一帧张开。根因: `_end_at` 里 `max(_my, _fr)` —— 前沿还在空中时
#    下沿是"前沿"(窄), 前沿一碰到堆面就换成"堆面"(宽) ⇒ **一帧换源**。
#    改法: 用**平滑最大值**(log-sum-exp 型)代替 `max` —— 处处连续可导, `k` 是过渡尺度(px)。
#    `k=0` ⇒ 逐字旧行为(可直接当负对照)。
NECK_SOFT_END = float(os.environ.get("HG_SOFT_END", "40.0"))
WARM_SPLASH_CHUNKS = 8


def _warm_enabled():
    """预热开关。设备侧做单变量对照用**标记文件**(安卓读不到宿主环境变量):

        adb shell touch /data/data/org.shalou.hourglass/files/app/warm.off

    ⚠️ **不要用 `HG_NO_WARM=1 bash ...` 去量设备** —— 环境变量到不了 app 端,
    那样量出来的"两臂一样"其实是"两臂都没改"(项目在 `blit.off` 上踩过)。
    """
    if os.environ.get("HG_NO_WARM") == "1":
        return False
    try:
        return not os.path.exists(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "warm.off"))
    except Exception:
        return True


WARM_ENABLED = _warm_enabled()


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
# ⚠️ **2026-10-06 更正: 上面这个"可见保底"实际从来没有可见过。**
#    `h = appear(eff) × max(raw(eff)·H, floor(eff))` —— floor 主导的窗口只有
#    `eff < 0.000586`（elapsed 后 **仅 28ms**），而窗口内 `appear ≤ 0.056`
#    ⇒ **峰值 h ≤ 0.507px**（设备探针每帧实测: 0.000 → 0.238 → **0.505** → 1.223，
#    最后一步已由 raw 接管）。⇒ dp(2.5)=7.5px 这个值**从未以 >0.51px 出现在画面上**。
#    r35-1号 设备每帧读数与解析式逐点吻合到 0.01px。（仅注释, 未改行为 —— 改不改归用户）
MOUND_FLOOR_MAX = 3.5
MOUND_FLOOR_EFF = 0.02

COMPLETION_POPUP_DELAY = 1.0   # 完成提示延后(秒): 让闪光/尘埃先演完再弹(用户 2026-10-03 定)
COMPLETION_POPUP_MIN = 1800.0  # 总时长至少 30 分钟才弹完成提示

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

    def sentence_keys(self, seconds, zero=True):
        """拼出"X小时Y分Z秒的沙漏计时完成"的词块序列(零分量省略)。

        `zero=True`(默认, 完成播报用): 时与秒之间缺分时夹一个"零"("一小时**零**三十秒")。
        `zero=False`(操作提示音用, 用户 2026-10-07 定「0 不说, 尽量简化」): 不夹那声"零"。
        ⚠️ **默认值必须保持 True** —— 完成播报的句子一字不能变。
        """
        total = max(0, int(round(seconds)))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        keys = []
        if hours:
            keys += self._hour_keys(hours) + ["hour"]
        if minutes:
            keys += [f"n{minutes}", "min"]
        elif hours and secs and zero:
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
        # 记住**构造时**的宽度比例: 重挂回竖屏时要拿它还原 `size_hint`。
        # 不能等 open() 时再读 —— 横屏分支把 size_hint 改成了 (None, None),
        # 那时再读就只剩兜底值 0.88。
        # ⚠️ **比例有三个, 不是一个**(r34-1号 设备实测逐个数过):
        #    周期/音效 = **0.88**(`main.py:4847`/`5371`)、
        #    开发者菜单/Benchmark = **0.94**(`5110`/`5180`)、
        #    完成弹窗 = **0.86**(`5495`)。
        #    他造了反向臂验证 `_frac` 是**承重的**: 换回旧的 `size_hint[0] or 0.88`
        #    后, 开发者菜单暖白宽从 **972 掉到 908**(logcat `using 0.88`)。
        self._frac = (self.size_hint[0]
                      if (self.size_hint and self.size_hint[0] is not None) else 0.88)
        self._ov_rot = False                             # 是否处于"自己画遮罩"的横屏态
        self._ov_dim = float(self.overlay_color[3])      # 原α(Kivy 默认 0.70)
        # 🔴 **画家顺序是承重的, 别调换这两块**: 原生顺序是
        #      `canvas.before`(兜底奶油底) → `canvas`(遮罩 → 卡片背景 BorderImage → 内容)
        #    ⇒ 遮罩必须**在兜底奶油底之后**画。第一版把遮罩摆在前面, 结果卡片外沿那圈
        #    3~6px 的奶油色(卡片圆角处 BorderImage 透明、露出兜底色)**不再被压暗**:
        #    实测从 (70,69,67)(压暗后) 变成 (234,230,220)(没压暗), 与原生逐像素对不上。
        with self.canvas.before:
            # 兜底层: Popup 本体 canvas.before(填充容器外间隙)
            Color(*self._bg_rgb, 1)
            self._popup_bg = Rectangle(pos=self.pos, size=self.size)
            # 自己那块遮罩(要紧跟其后)
            self._ov_color = Color(0, 0, 0, 0)
            self._ov_rect = Rectangle(pos=(0, 0), size=(0, 0))
        self.bind(pos=self._upd_popup_bg, size=self._upd_popup_bg)
        self.bind(_anim_alpha=self._upd_overlay)         # 自己那块也要跟着开/关动画淡入淡出

    def _upd_popup_bg(self, inst, _value):
        self._popup_bg.pos = inst.pos
        self._popup_bg.size = inst.size

    # ---------- 遮罩: 横屏下自带那层只盖 55% 屏宽, 换成"以屏心为中心的正方形" ----------
    # 🔴 病灶(`kivy/data/style.kv:509-514` 的 `<ModalView>`):
    #       Color: rgba: root.overlay_color[:3] + [root.overlay_color[-1] * self._anim_alpha]
    #       Rectangle: size: self._window.size if self._window else (0, 0)
    #    —— **只给了 size, 没给 pos** ⇒ pos 恒为 (0,0)。竖屏时宿主是 Window 且不转, 没事;
    #    **横屏时弹窗挂在反旋转层上**(见 `open()`), 整个层绕**屏心**转 ±90° ⇒ 那块
    #    2400×1080 的矩形转完只剩中间 1080 宽的竖条, 两侧被白白漏掉
    #    带宽 = (长边−短边)/2 = 660px。桌面实测(`tools/_probe_overlay_band.py 2400 1080`):
    #    x 0~659 与 x 1740~2399 各 660px 比值 1.000(完全没被压暗), 带里才是 0.302。
    # ✅ 修法: 矩形要在"绕屏心转 ±90°"下**映射到自身** ⇒ 取以屏心为中心、边长 max(W,H)
    #    的**正方形**(横竖屏都严丝合缝)。关掉自带那层, 自己画这一块。
    # ⚠️ 坐标系(已用 `tools/_r37_marker_xy.py` 打标记方块实测): 弹窗 `canvas.before` 的指令
    #    画在**父(层)坐标系**, 不是弹窗局部系 —— 按局部系算会得到一条 L 形亮带。
    #    层恒 `pos=(0,0)`(见 `LandLayer.apply_orientation`) ⇒ 直接用窗口坐标即可。
    def _set_overlay_rotated(self, rot):
        """横屏(挂旋转层)⇒ 关掉自带遮罩 + 打开自己那块; 竖屏(挂 Window)⇒ 原样还回去。

        幂等。两个宿主都调: `open()` 按开那一刻的宿主定, `rehost()` 在转屏搬家后再定一次。
        """
        rot = bool(rot)
        if rot != self._ov_rot:
            self._ov_rot = rot
            if rot:
                # 记下"当前α"再关 —— 开发者菜单(配方外的既有代码)会把它设成 0.10 要预览,
                # 直接吞掉会把沙漏压到 30% 亮度 ⇒ α 必须跟随调用方设的值。
                self._ov_dim = float(self.overlay_color[3])
                self.overlay_color = (0, 0, 0, 0)
            else:
                self.overlay_color = (0, 0, 0, self._ov_dim)
        self._upd_overlay()

    def _upd_overlay(self, *_args):
        """自己那块遮罩的几何与α。宿主 resize 时经 `_align_center` 自动跟手。"""
        rect = getattr(self, "_ov_rect", None)
        if rect is None:
            return
        if not self._ov_rot:
            rect.size = (0, 0)                           # 竖屏: 收起自己那块, 走自带遮罩
            return
        w, h = Window.width, Window.height
        side = max(w, h)
        rect.pos = (w / 2.0 - side / 2.0, h / 2.0 - side / 2.0)
        rect.size = (side, side)
        self._ov_color.rgba = (0, 0, 0, self._ov_dim * self._anim_alpha)

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
            # 原生 open() 把 on_keyboard 绑在 Window 上(不是绑在本 widget), 记下来好解。
            self._kb_window = Window
            if layer is not None:
                # ⚠️ 竖屏开的弹窗**也要登记** —— 否则"竖屏开着弹窗再转横屏"时
                #    没人通知它 (2026-10-06 r32-1号 设备实测的那条 E1 缺陷)。
                layer.track_popup(self)
            Clock.schedule_once(self._apply_light_theme, 0)
            self._set_overlay_rotated(False)     # 竖屏: 走自带遮罩(自己那块收起)
            return
        # 横屏: 不再把弹窗挂 Window(Window 不旋转), 改挂旋转层让其随层旋转成竖构图。
        # 弹窗用 size_hint=(0.88, None), 直接挂层会按"物理长边×0.88"放大溢出, 故按
        # "等效竖屏窗宽=短边"换算显式 size, 再整体旋转 → 视觉与竖屏一致。
        if self._is_open:
            return
        eq_w = min(Window.width, Window.height)    # 等效竖屏窗宽=短边
        frac = self._frac                          # 构造时记下的(dev 菜单 0.94, 其余 0.88)
        self.size_hint = (None, None)
        self.size = (frac * eq_w, self.height)
        self._window = layer                        # 宿主从 Window 换成旋转层
        self._is_open = True
        self.dispatch('on_pre_open')
        if not self.pos_hint:
            self.pos_hint = {"center_x": 0.5, "center_y": 0.5}
        layer.add_widget(self)                      # 挂到层而非 Window
        layer.track_popup(self)                     # 转屏时由 apply_orientation 通知重挂
        layer.bind(on_resize=self._align_center)
        # ⚠️ **`on_keyboard` 必须绑在 `Window` 上, 不能绑在层上**（2026-10-06, r31-1号 设备实测）:
        #    Kivy 只在 `Window` 上派发这个事件; 绑在任意 widget(这里是旋转层)上**永远不会被触发**
        #    ⇒ 横屏下按 BACK 键不关弹窗, 一路冒到 p4a ⇒ **整个 app 退到桌面**
        #    (弹窗还会在前台恢复后继续开着)。竖屏走 `ModalView.open()`, 它自己绑的就是 Window,
        #    所以**竖屏一直是对的** —— 只有横屏这条分支漏了。
        Window.bind(on_keyboard=self._handle_keyboard)
        self._kb_window = Window
        self.center = layer.center
        self.fbind('center', self._align_center)
        self.fbind('size', self._align_center)
        self._anim_alpha = 1.
        self.dispatch('on_open')
        self._set_overlay_rotated(True)          # 横屏: 自带那层只盖 55% 屏宽, 换自己那块正方形
        Clock.schedule_once(self._apply_light_theme, 0)

    def _align_center(self, *_args):
        """ModalView 原版是 `if self._is_open: self.center = self._window.center`。
        加一句 `self._window is not None` —— 转屏重挂(`rehost`)过程中会短暂把 `_window`
        清空, 而那时 `size`/`center` 的绑定还在, 原版会在这一瞬间
        `AttributeError: 'NoneType' object has no attribute 'center'`。
        """
        if self._is_open and self._window is not None:
            self.center = self._window.center
        self._upd_overlay()                      # 宿主 resize ⇒ 自己那块遮罩跟着重算

    def _unmount(self):
        """从当前宿主摘下来 + 解开该宿主上的绑定。**不动 `_is_open`**。

        与 `_real_remove_widget` 的区别: 那个是"关掉"(还要置 `_is_open=False`),
        这个是"搬家"—— 摘完马上会挂到另一个宿主上。
        """
        host = self._window
        if host is not None:
            try:
                host.remove_widget(self)
            except Exception:
                pass
            try:
                host.unbind(on_resize=self._align_center)
            except Exception:
                pass
        # 键盘绑定: 两个分支都绑在 **Window** 上(横屏分支见 open() 里的说明),
        # 所以从 host 那边解不掉, 要单独记住 Window 再解。
        kbw = getattr(self, "_kb_window", None)
        if kbw is not None:
            try:
                kbw.unbind(on_keyboard=self._handle_keyboard)
            except Exception:
                pass
            self._kb_window = None
        self._window = None

    def rehost(self, to_layer):
        """窗口方向变了 → 把**已经开着**的弹窗换到正确的宿主上。

        ⚠️ 为什么必须有: `open()` 是在**开的那一刻**按 `layer.angle` 定宿主的 ——
           竖屏挂 `Window`(Window 不旋转) / 横屏挂旋转层(随层转)。转屏只改 `layer.angle`,
           原先**没有任何地方重挂已开的弹窗** ⇒ 竖屏开着弹窗再转横屏, 弹窗**侧躺 90°**。
           反向(横→竖)只是"侥幸对": 层角回 0, 挂在层上也不转了。
           (2026-10-06 r32-1号 设备实测 E1: 周期/音效两个弹窗各复现一次,
            反向对照臂方向正确。)

        幂等: 已经在正确宿主上就直接返回。宿主由 `LandLayer.apply_orientation()` 调。
        """
        if not self._is_open:
            return
        layer = _land_layer()
        if layer is None:
            return
        on_layer = (self._window is layer)
        if bool(to_layer) == on_layer:
            return
        self._unmount()
        if to_layer:
            # 挂旋转层: 尺寸按"等效竖屏窗宽 = 短边"换算(直接挂会按物理长边放大溢出)
            eq_w = min(Window.width, Window.height)
            self.size_hint = (None, None)
            self.size = (self._frac * eq_w, self.height)
            self._window = layer
            layer.add_widget(self)
            layer.bind(on_resize=self._align_center)
            Window.bind(on_keyboard=self._handle_keyboard)
            self._kb_window = Window
            self.center = layer.center
        else:
            # 回竖屏: 还原成原生宿主(Window)与原生 size_hint, 让 Window 自己排
            Window.add_widget(self)
            self._window = Window
            Window.bind(on_resize=self._align_center,
                        on_keyboard=self._handle_keyboard)
            self._kb_window = Window
            self.size_hint = (self._frac, None)
            self.center = Window.center
        # 遮罩跟着宿主走: 挂层(横屏)⇒自己画那块正方形; 回 Window(竖屏)⇒还原自带遮罩。
        # ⚠️ 只在 `rehost` 里还原, **不在 `_unmount` 里** —— dismiss 走的是
        #    `_real_remove_widget` → `_unmount`, 而 ModalView 关窗前还有 ~0.2s 的
        #    `_anim_alpha` 淡出动画; 那时若把自带遮罩还回去, 横屏下会闪一条 660px 亮带。
        self._set_overlay_rotated(to_layer)

    def _real_remove_widget(self):
        """覆写: dismiss 时从旋转层对称摘除(非横屏时 host=Window, 行为等同原生)。

        ⚠️ 原先那句"覆写"其实是**抄了一遍 ModalView 的实现再打补丁**;
        现在统一走 `_unmount()`, 免得两处各解一半绑定(转屏重挂新增了第二个调用点)。
        """
        if not self._is_open:
            return
        layer = _land_layer()
        if layer is not None:
            layer.untrack_popup(self)
        self._unmount()
        self._is_open = False

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
        # 开着的沙色弹窗(它们要在转屏时换宿主, 见 _SandBgPopup.rehost)
        self._popups = set()
        with self.canvas.before:
            PushMatrix()
            self._rot = Rotate(angle=0, axis=(0, 0, 1), origin=(0, 0))
        with self.canvas.after:
            PopMatrix()

    def track_popup(self, p):
        self._popups.add(p)

    def untrack_popup(self, p):
        self._popups.discard(p)

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
        # ⚠️ **已开着的弹窗必须跟着换宿主**(2026-10-06 r32-1号 设备实测 E1)。
        #    `_SandBgPopup.open()` 是在**开的那一刻**按 `layer.angle` 定宿主的 ——
        #    竖屏挂 `Window`(Window 不旋转), 横屏挂本层(随层转)。而转屏只改 `self.angle`,
        #    原先**没有任何地方重挂已开的弹窗** ⇒ 竖屏开着弹窗再转横屏, 弹窗**侧躺 90°**。
        #    反向(横→竖)只是"侥幸对": 层角回 0, 挂在层上也不转了。
        #    ⚠️ `_LAYER` 那句注释里写的"弹窗 remount"就是这个位置 ——
        #    初版只写了注释、没写代码(见 commit 8d950b3), 这个洞一直留到今天。
        want = (self.angle != 0)
        for _p in list(self._popups):
            try:
                _p.rehost(want)
            except Exception:
                pass
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
        self._sand_released = 0.0
        self._sand_landed = 0.0
        self._sand_pending = 0.0
        self._sand_active = False
        self._sand_timing = None
        self._sand_resized = False
        self._neck_mass_table = ()
        # 飞溅: **并行数组是真值**, `splashes` 只是取证用的 dict 视图(见该 property)
        self._sn = 0
        self._s_alloc(512)
        self._bg_splash_acc = 0.0
        self._mound_flow_clock = 0.0
        self._first_impact_clock = self._last_impact_clock = None
        self._contact_hits = deque(maxlen=256)
        self._effect_random = None
        self._splash_reference_speed = None
        self._splash_density = 1.0
        self._splash_cap = SPLASH_MAX
        self._splash_origin_blend = 1.0
        self._splash_speed_scale = 1.0
        self._splash_stats = dict.fromkeys(
            ("born_air", "born_roll", "pool_full", "settled", "glass", "reentered", "expired"), 0)
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        self._geom_generation = 0            # 几何代: 尺寸/周期/窗口一变就 +1, 形状缓存跟着失效
        self._knots_fixed = None             # `_mound_knots` 的几何固定部分(按几何代缓存)
        self._mound_profile = None           # 下球沙堆形状解(见 _MoundProfile), 几何重建时换新
        self._mound_shape = ()               # 65 点轮廓(绝对值): 绘制节点/接触查表都用它
        # 四条沙面 carve/亮带 —— 由 `_build_dynamic_canvas` 建(见 `_QuadBand`);
        # 在那之前是 None(原来的初值是 `[]`, 换成对象之后必须有哨兵值)。
        self._upper_carve = None             # 上球漏斗 carve(§4)
        self._upper_band = None
        self._mound_carve = None
        self._mound_band = None
        self._upper_band_color = None
        self._surface_marker_pool = []
        self._surface_marker_n = 0
        self._mound_shape_cache = None       # ((几何代, elapsed), apex) —— 每帧只解一次
        self._mound_curve_cache = None       # ((几何代, elapsed), (cx+dx, y)) 接触曲线
        self._contact_table = []             # 飞溅用的接触高度查找表(见 update_particles)
        self._upper_level_key = None        # `_upper_level_for` 的每帧 memo
        self._upper_cols = None             # `_upper_area` 的布局缓存(dx/floor/roof)
        self._neck_tone_tab = None          # 颈部颗粒的色调查表(见 _draw_neck_grains)
        self._neck_tone_last = None         # 每槽上次写过的颜色(值没变就不写)
        # 头部抖动相位: 每次启动不同(不消耗 random 流) ⇒ 每轮头部形状不一样
        self._front_seed = (time.perf_counter() * 7.13) % 6.283
        self._completion_triggered = False
        self._done_at = None                 # 漏完时刻(颈管排空用), 未漏完为 None
        self._completion_token = 0          # 作废"待弹的完成提示"用, 见 _schedule_completion_popup
        self._warm_queue = []               # 待预热的批处理块(见 _warm_batches_step)
        self._warm_src = (None, None)       # 上一批队列对应的批对象本身(重建即换新, 见那里的 ⚠️)
        self._geom_key = None               # 上一次生效的 (w,h,x,y); 见 `_on_size` 的去抖
        self._geom_pending = None           # 已排队但还没跑的几何重建(去抖用)
        self._geom_defers = 0               # 占位尺寸上最多延后几帧(有界, 见 `_do_geom`)
        self._sand_material = None          # 沙体材质纹理(见 sand_material); None = 平色填充
        # 拖动「沙子浓度」滑块时的低分辨率材质槽(见 preview_sand_material)。
        # 非 None 时它**优先于** _sand_material, 松手/换色即清空。
        self._preview_material = None

        self.sound_name = "沙沙声"
        self._sound = self._make_sound_proxy(self.sound_name)
        self.completion_enabled = True
        self._completion_sound = self._make_completion_sound()
        # ★ **语音词库 + 钟声预热挪到第一帧之后**(2026-10-07 启动优化):
        #   它们**只在"计时完成"时才用得到**, 而词库要开 **76 个 wav 文件** ——
        #   桌面上 5ms, 安卓私有目录上按经验要几十~几百 ms, 而这段全压在
        #   "第一帧能看见"这条关键路径上(用户报「启动时间比最早版本长了很多」)。
        #   `None` 期间完成播报**自动走预录整句兜底**(见 `_play_completion_sound`),
        #   所以就算第一帧之前真完成也不会炸。等词库到位后自动切回拼接播报。
        self._voice_bank = None
        self._completion_spoken = None      # 动态拼出的播报(每条周期重建一次)
        self._voice_prompt = None           # 上一次的操作提示音(见 `_voice_say`)
        Clock.schedule_once(self._load_voice_bank, 0.05)

        self.bind(size=self._on_size, pos=self._on_size)
        Clock.schedule_once(self._on_size, 0)
        Clock.schedule_interval(self.tick, 0)

    # ---------- 几何(自适应; Kivy y 向上) ----------

    def _load_voice_bank(self, _dt=None):
        """**第一帧之后**才读语音词库(76 个 wav)+ 预热钟声 —— 见 `__init__` 里那段说明。"""
        if self._voice_bank is not None:
            return
        try:
            self._voice_bank = _VoiceBank(resource_path(""))
        except Exception as exc:                 # `_VoiceBank` 自己不会抛(缺词块时 ok=False),
            print("voice bank load failed (%s)" % (exc,))   # 这里只防意外
            self._voice_bank = None
            return
        if self._voice_bank.ok:
            _completion_chime(self._voice_bank.rate)   # 预热,别让首播卡在完成那一帧

    def _on_size(self, *_):
        """尺寸/位置变化 → 重建几何。**启动时这条路会连发好几次, 每次都是整块画布重建。**

        实测(桌面, 启动 3 秒内的 5 次): `#0 71.5ms(100x100 占位) / #1 42.8ms(与上次**尺寸
        完全相同**, 纯浪费) / #2 41.4ms(只挪了 x/y) / #3 0ms(退化尺寸) / #4 51.4ms(布局
        中途 756x168) / #5 44.6ms(最终 356x368)` —— **合计 ~250ms**, 全压在"第一帧能看见"
        这条关键路径上。布局是会**分几帧稳定**下来的, 而每稳定一步就重付一次全款。

        所以这里做两件事:
        ① **尺寸+位置与上次完全一样 ⇒ 直接跳过**(启动时抓到 1 次纯重复);
        ② **同一帧内的多次请求合并成一次**(排到下一帧执行, 已有待执行的先取消) ——
           布局连发时只付最后一次的钱。代价是几何晚一帧生效, 而 `tick()` 本来就有
           `_geom_ready` 守卫 ⇒ 那一两帧不发图元, 看不见。

        🔴 **只在"Kivy 事件"这条路上去抖** —— 显式调用 `_rebuild_height_table()`
        (改周期 / 改沙面起伏 / 工具探针)**一律立即生效**, 一个字节都不改:
        `set_rough_level` 就是靠"调了就重建"来把新粗糙度烘进面积表的, 在那里加守卫
        会**静默失效**(正是本项目最怕的那种)。
        """
        key = (self.width, self.height, self.x, self.y)
        if self._geom_ready and key == self._geom_key:
            return
        self._geom_key = key
        if self._geom_pending is not None:
            self._geom_pending.cancel()
        self._geom_pending = Clock.schedule_once(self._do_geom, -1)

    def _do_geom(self, *_):
        self._geom_pending = None
        # Kivy 把布局跑出来**之前**, Widget 的尺寸是默认的 100x100 —— 那不是真实布局,
        # 而给这个占位尺寸整块重建画布要 **71.5ms**(实测), 且那时画面上什么都还没有。
        # ⇒ 小到不可能是真布局时**最多再等 3 帧**(有界, 不会永远不建)。
        # ⚠️ 只在"还没建过"时延后(`not self._geom_ready`): 之后任何尺寸变化一律立即重建。
        if (not self._geom_ready and self._geom_defers < 3
                and (self.width < 150.0 or self.height < 150.0)):
            self._geom_defers += 1
            self._geom_pending = Clock.schedule_once(self._do_geom, -1)
            return
        self._rebuild_height_table()

    @property
    def neck_w(self):
        """颈部半宽,log 插值:短周期→宽,长周期→窄,上下限保证沙流可视"""
        return self._neck_width_for_duration(self.duration)

    def _neck_width_for_duration(self, duration):
        lo, hi = self._neck_width_limits()
        dur = max(1.0, duration)
        if dur <= 5:
            return hi
        if dur >= 36000:
            return lo
        lo_d, hi_d = math.log(5), math.log(36000)
        t = (math.log(dur) - lo_d) / (hi_d - lo_d)
        t = max(0.0, min(1.0, t))
        return round(lo + (hi - lo) * (1 - t))

    def _neck_width_limits(self):
        w = self.width
        lo = max(dp(7), round(w * 7.0 / 380.0))     # 最细: 管内壁仍有空间
        hi = round(w * 17.0 / 380.0)                 # 最粗: 不压过球的比例
        return lo, hi

    @property
    def speed_factor(self):
        return max(0.5, min(2.5, 60.0 / max(0.1, self.duration)))

    def _rebuild_height_table(self):
        w, h = self.width, self.height
        if w <= 1 or h <= 1:
            return
        old_flow = None
        if self._geom_ready and self._sand_active:
            old_flow = (self._cx, self._R_inner, self._lower_sand_bot,
                        2 * self._neck_y - self._taper["y_bot"])
        cx = self.x + w / 2.0
        ow = max(2.0, w * (6.0 / 380.0))
        tube_h = h * 0.055   # 给球↔管的曲线过渡留出竖直空间
        side_margin = w * 0.06
        # 上下留白: 原 2%(≈38px@1900) 太宽 —— 用户 2026-10-07 明确要求
        # 「0/50 下面和沙漏下面的空行变矮, 剩下的空间让沙漏更大」。
        # ⚠️ **平板上 R 是高度受限**(宽只用了 62%, 余量 42%) ⇒ 纵向每省 4px, R 就涨 1px。
        #    ⚠️ **桌面预览 400×800 是宽度受限**(w/h=0.5 < 0.5301), 在那边量这条会得出
        #    "省纵向没用"的**相反结论** —— 量它必须用平板口径:
        #    `tools/_probe_vertical_budget.py`(默认就按 1904×2890 建窗口, 且回读断言)。
        # 0.006 → 0.002(≈5px): 纵向留白没有别的用处, 玻璃本来就顶到画布边(见下)
        v_pad = h * 0.002
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
        # 只按同窗口内的实际口径比较: 最粗 150%, 最细按比例且不低于 15%。
        narrowest, widest = self._neck_width_limits()
        widest_inner = max(1.0, widest - ow)
        narrowest_inner = max(1.0, narrowest - ow)
        self._splash_density = max(0.15, min(1.5, 1.5 * (t_in / widest_inner)))
        self._splash_cap = max(1, round(SPLASH_MAX * self._splash_density)) if SPLASH_MAX else 0
        width_fraction = max(0.0, min(1.0, (t_in - narrowest_inner)
                                     / max(1e-9, widest_inner - narrowest_inner)))
        self._splash_origin_blend = width_fraction * width_fraction * (3.0 - 2.0 * width_fraction)
        # 观感目标: 同角度和重力下射程正比于速度平方, 实际落点仍受坡面影响。
        # 🔴 **2026-10-09 用户定: 下限 1/3 → 1/4**(最细颈那档的飞溅射程 = 最粗档的 **25%**,
        #    原先 33%)。射程 ∝ 速度² ⇒ `speed = sqrt(下限 + (1−下限)·blend)`。
        #    只动**射程**这一个量; 数量(`_splash_density`)/上限(`_splash_cap`)/贴底
        #    (`_splash_origin_blend`) 各自独立, 一个字没动。
        range_fraction = (SPLASH_RANGE_FLOOR
                          + (1.0 - SPLASH_RANGE_FLOOR) * self._splash_origin_blend)
        self._splash_speed_scale = math.sqrt(range_fraction)
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
        self._build_neck_mass_table()
        if not self._sand_active:
            self._sand_timing = None
        # 下球沙堆的形状解: **几何一变就重建**(专家 §2.2/§2.6)
        #   轮廓是 65 点固定数组(微不对称尖堆 + 受限微粗糙), **与沙量无关** ⇒
        #   两张面积表只建一次, 每帧只查一次表求逆。粗糙数组用固定 seed, 整轮不重抽。
        self._geom_generation += 1
        try:
            cap_radius = min(Ri * 0.12, self._taper["t_in"] * 1.1)
            shape = _mound_shape_array(Ri, UPPER_ROUGH_FRAC, cap_radius)
            self._mound_profile = _MoundProfile(Ri, shape)
            # 上球微粗糙的 65 点数组(与下球同一套节点口径, 但独立 seed / 独立幅度)
            _uamp = UPPER_ROUGH_FRAC * 2.0 * Ri
            self._upper_rough = _surface_roughness(Ri, UPPER_ROUGH_FRAC, UPPER_ROUGH_SEED, _uamp)
            self._upper_wall_weights = tuple(
                0.35 + 0.65 * (1.0 - (2.0 * i / (MOUND_SHAPE_NODES - 1) - 1.0) ** 2)
                for i in range(MOUND_SHAPE_NODES))
            # 演化帧: 与静态版同幅度、同节点口径, 只是**随时间平滑地换形状**
            self._upper_rough_frames = _build_rough_frames(Ri, UPPER_ROUGH_FRAC, UPPER_ROUGH_SEED)
            self._upper_rough_cache = None
            # ⚠️ **必须把时间戳一起清掉** —— 只清 `_upper_rough_cache` 是 1.124 之前的 bug
            #    (r23-1号 在设备上抓到的): `_upper_rough_now()` 的命中判定是
            #    `if _upper_rough_cache_t == elapsed: return _upper_rough_cache`
            #    ⇒ 时间戳没清时它把刚置空的 **None 原样返回**, 下游 `_upper_rough_at`
            #    见假值就全返 0 ⇒ **上球沙面画成光滑面**。
            #    而 `set_rough_level` 正是走这条路, 且**用户是在"暂停着看预览"时改档的**
            #    (暂停时 elapsed 不动 ⇒ 缓存永远命中 None) ⇒
            #    现象是"在设置里一改沙面起伏, 沙面就变光滑, 六档全是同一个画面" ——
            #    **恰好和真相相反**, 用户很容易读成"N 越大越平"。
            #    (elapsed 一走缓存就失效, 所以只在暂停预览这一种状态下出现, 看起来像"一动又回来了"。)
            self._upper_rough_cache_t = None
            self._upper_env_h = None          # `_upper_rough_at` 的包络 memo(见那里)
            self._upper_env = 0.0
            # 🔴 **`_upper_cols` 也要在这里失效**(2026-10-07 2号专家查出, E1 正负对照):
            #    它是**几何**缓存(`dx`/`floor`/`roof`, 含一个 `sqrt`), 但 `_upper_area` 里的
            #    守卫只有 `cols is None or len(cols) != n+1`, 而 `n = MOUND_SHAPE_NODES-1`
            #    是**常量** ⇒ 建过一次**永不重建**。`_reset_run_state` 里清过它, 但
            #    **转屏 / 分屏 / 拖窗**走的是 `_on_size → _rebuild_height_table` 这条路,
            #    那条路上它没被清 ⇒ 上球沙面的 `level` 一直用旧 `Ri` 的几何算。
            #    实测(resize 720×900, Ri 112.96→130.85): 只清这一个变量, `_upper_level_for`
            #    由 94.3385 → 94.0232, 差 **0.3153px**(缓存里的 floor 项与当前 Ri 应有的值
            #    差 **13.45px**)。而 `level` 正是 `_draw_upper_shape` 画沙面用的那个数。
            self._upper_cols = None
            self._mound_shape = tuple(shape)
            # ---- 下球轮廓的**演化帧**（用户 2026-10-05: "下球斜面也应该有起伏" + "要动"）----
            # ⚠️ 每帧扰动**减掉自己的均值** ⇒ 面积精确不变（用户原话"有高就有低"），
            #    但**只减一个常数平移** —— 空间上的高低起伏仍是**不规则的**，而且逐帧在变，
            #    不会变"均衡"（用户 2026-10-05: "不均衡, 但是又随机变化"）。
            # ⚠️ **预烘 64 个 `_MoundProfile`**（实测 0.25ms/个 ⇒ 共 16ms 一次性）:
            #    物理热循环里只是**换一个指针**, 逐颗粒零成本; 而且面积表与画出来的轮廓
            #    **天生一致** —— 这是"逐帧改轮廓"还能保守恒的关键。
            _frames = []
            for _fr in _build_rough_frames(Ri, UPPER_ROUGH_FRAC, MOUND_ROUGH_SEED):
                _mu = sum(_fr) / len(_fr)
                _shp = _mound_shape_array(
                    Ri, UPPER_ROUGH_FRAC, cap_radius, rough=[v - _mu for v in _fr])
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
        if old_flow is not None:
            old_cx, old_radius, old_bottom, old_outlet = old_flow
            outlet = 2 * self._neck_y - self._taper["y_bot"]
            sx = Ri / old_radius
            sy = (outlet - self._lower_sand_bot) / (old_outlet - old_bottom)
            if abs(sx - 1.0) > 1e-9 or abs(sy - 1.0) > 1e-9:
                self._sand_resized = True
                left = max(1e-6, self.duration - self.elapsed - 1.0 / 120)
                gravity = 450.0 * self._particle_motion_scale ** 2
                for i in range(self.pn):
                    self.px[i] = cx + (self.px[i] - old_cx) * sx
                    self.pxo[i] *= sx
                    self.pwa[i] *= sx
                    self.py[i] = outlet + (self.py[i] - old_outlet) * sy
                    contact = self._mound_top_at(float(self.px[i]))
                    speed = max(0.0, (self.py[i] - contact) / left - 0.5 * gravity * left)
                    self.pvy[i] = min(self.pvy[i] * sy, -speed)
                self._p_refresh_view()
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
        if self._sand_active:
            return max(0.0, min(1.0, self._sand_landed))
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
        if self._sand_active:
            return self._raw_height_ratio(eff) * ball_h_inner
        delay = self._fall_delay
        if self.elapsed < delay:
            return 0.0
        target = max(self._raw_height_ratio(eff) * ball_h_inner, self._mound_floor(eff))
        appear_window = max(0.01, min(MOUND_APPEAR, self.duration - delay))
        appear = min(1.0, (self.elapsed - delay) / appear_window)
        return appear * target

    def _upper_sand_height_px(self):
        """上球、颈部、在途与落地共用一份归一化沙量。"""
        return self._raw_height_ratio(self._upper_sand_fraction()) * 2 * self._R_inner

    def _transfer_timing(self):
        """Reserve flight time; neck storage follows its geometry, not an arbitrary duration."""
        if self._sand_timing is None:
            start = self._neck_fill_time
            scale = self._particle_motion_scale
            gravity = 450.0 * scale * scale
            speed = 35.0 * scale
            outlet = 2 * self._neck_y - self._taper["y_bot"]
            distance = max(0.0, outlet - self._lower_sand_top) + 2.0
            flight = 2.0 * distance / (
                speed + math.sqrt(speed * speed + 2.0 * gravity * distance))
            # 🔴 **2026-10-10 (2.28): 不再提前 `flight` 结束释放。**
            #    用户报: 「较长时间计时时, 最后 x 秒没有沙子流下, 但下面的沙子体积在增加」。
            #    实测(探针 `tools/_probe_two_clocks.py`, 50s 档):
            #      400x800  gap = 0.00s   |   800x1600  gap = **0.25s**(受 0.25s 采样限制)
            #      ⇒ **缺口随口径放大**(`flight ∝ 落程`), 平板上还要更大。
            #    机制: 原来的 `- flight` 是**故意**让释放早结束、好让最后一粒正好在 `duration`
            #    落地; 但那段时间**上球已空、颈部沙柱没了**, 只剩在途颗粒 ⇒
            #    "没有沙子流下, 堆却还在长"。
            #    ⇒ 去掉这一项: **上球恰好在计时结束时才空**, 全程"堆在长的时候一定有沙在落"。
            #    ⚠️ 代价: 最后几粒在 `duration` 之后才落地 ⇒ 终态沙堆差约 0.4%(肉眼不可辨),
            #      换来"整个计时期间流动与堆积同进同止"。
            end = max(start + 1e-6, self.duration - 1.0 / 60.0)
            sphere_volume = 4.0 * self._R_inner ** 3 / 3.0
            reserve = min(0.45, max(1e-9, self._neck_volume / sphere_volume))
            self._sand_timing = (start, end, reserve)
        return self._sand_timing

    def _released_fraction_at(self, elapsed):
        start, end, _reserve = self._transfer_timing()
        return max(0.0, min(1.0, (elapsed - start) / (end - start)))

    def _upper_sand_fraction(self):
        if self.duration <= 0 or not self._geom_ready:
            return 1.0 if self.duration > 0 else 0.0
        start, _end, reserve = self._transfer_timing()
        remaining = 1.0 - self._released_fraction_at(self.elapsed)
        neck = min(remaining, reserve * min(1.0, max(0.0, self.elapsed / start)))
        return max(0.0, remaining - neck)

    def sand_transfer_state(self):
        """取证/快照接口; 热循环不按颗粒构造字典。"""
        upper = self._upper_sand_fraction()
        released = (self._sand_released if self._sand_active
                    else self._released_fraction_at(self.elapsed))
        landed = self._sand_landed if self._sand_active else self._effective_fallen()
        return {"upper": upper, "neck": max(0.0, 1.0 - upper - released),
                "flight": max(0.0, released - landed), "landed": landed}

    def _neck_width_at(self, y):
        pts = self._taper["in_pts"]
        if y >= pts[0][1]:
            return pts[0][0]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if y1 <= y <= y0:
                return x0 + (x1 - x0) * (y0 - y) / max(1e-6, y0 - y1)
        return self._taper["t_in"]

    def _build_neck_mass_table(self):
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        top = min(self._upper_sand_bot, self._taper["in_pts"][0][1])
        table = [(0.0, outlet)]
        volume = 0.0
        last_y, last_x = outlet, self._neck_width_at(outlet)
        for i in range(1, 129):
            y = outlet + (top - outlet) * i / 128.0
            x = self._neck_width_at(y)
            volume += (y - last_y) * (last_x * last_x + last_x * x + x * x) / 3.0
            table.append((volume, y))
            last_y, last_x = y, x
        self._neck_mass_table = tuple((v / max(1e-12, volume), y) for v, y in table)
        self._neck_volume = volume

    def _neck_surface_y(self, fraction):
        table = self._neck_mass_table
        k = min(len(table) - 1, max(1, bisect_right(table, (fraction, float("inf")))))
        v0, y0 = table[k - 1]
        v1, y1 = table[k]
        return y0 + (y1 - y0) * max(0.0, min(1.0, (fraction - v0) / max(1e-12, v1 - v0)))

    def _free_width_ratio(self, depth):
        """自由射流在**出口以下 depth 处**的半宽收缩比 (1.0 = 与孔径同宽)。

        文献给的形状(不用自己造轮子): 从孔口射出的射流先收缩成 vena contracta
        —— 液体收缩到孔面积的 ~0.6(直径比 √0.6 ≈ 0.77); 颗粒流的排出系数实测
        `C_D² = 0.53 ± 0.01`(对比液体的 0.63), 折算**直径比 √0.53 ≈ 0.73**;
        再往下按 `A·v = 常数` 继续收窄。

        **这个式子不是新造的**: `update_particles` 里每颗粒子用的就是它
        (`target = sqrt(source_speed / v_at_y)`, 下限 `FLOW_SHRINK_MIN = 0.70` —— 与
        文献的 0.73 基本重合)。柱子照抄它, 画出来的柱子和粒子云才**同宽**;
        否则柱子从粒子外面鼓出来 —— 用户看到的正是"一块**规整的矩形**"。
        ⚠️ 数字必须与 `update_particles` 保持一致, 改一边就得分叉。
        """
        if not NECK_TAPER or depth <= 0.0:
            return 1.0
        if _TWOIMPL >= 2:
            # 零点挪到粒子那套的原点: 粒子的 `below_tube = _lower_ball_cut - y`,
            # 而本函数的 `depth = 出口 - y` ⇒ 等价的粒子深度 = depth - shift,
            # shift = 出口 - _lower_ball_cut（`_lower_ball_cut` 在出口**下方** ⇒ shift > 0）。
            depth -= (2.0 * self._neck_y - self._taper["y_bot"]) - self._lower_ball_cut
            if depth <= 0.0:
                return 1.0
        ms = self._particle_motion_scale
        v0 = 60.0 * ms
        v_at = math.sqrt(v0 * v0 + 2.0 * 450.0 * ms * ms * depth)
        target = math.sqrt(v0 / v_at)
        if _TWOIMPL >= 1:
            # 与 `update_particles` 逐字同序: **先钳 target, 再乘 ramp** ⇒ 连续, 无早退断点。
            if target <= FLOW_SHRINK_MIN:
                target = FLOW_SHRINK_MIN
            return 1.0 + (target - 1.0) * min(1.0, depth / FLOW_SHRINK_RAMP)
        if target <= FLOW_SHRINK_MIN:
            return FLOW_SHRINK_MIN
        return 1.0 + (target - 1.0) * min(1.0, depth / FLOW_SHRINK_RAMP)

    def _falling_front(self):
        """在途沙的**前沿**(最低点, Kivy y 向上 ⇒ 越小越低)—— 沙柱往下长到哪儿为止。

        为什么需要它(2026-10-09, 用户当场指出): 沙柱的下沿原来是钉在**碰撞面**
        (`get_mound_top_y()`)上的, 而 50s 档前 2 秒沙堆还趴在球底 ⇒ 那个标量 ≈
        **下球内底** ⇒ 柱子**从出口一路插到球底**, 而同一时刻**一个粒子都没有**
        (沙还在半路)。画面上就是一根凭空贯穿下球的棍子(1.238 同一死因,
        用户判词「你不要顾头不顾腚」)。前沿 = 沙**真的**落到了哪儿。

        🔴 **用解析式, 不用 `min(self.py[:self.pn])`**(第一版就是后者, 退了一条)。原因:
        粒子**一触面就消失** ⇒ 逐帧取 min 得到的前沿永远比堆面高**一帧的落程**
        (实测 +14.4~14.8px, 三个周期全中), 于是刚接上的接缝又被这 14px 重新开出来
        (`verify_hourglass.py` 的 `drawn column meets the drawn mound` 当场翻红)。
        解析前沿是**同一条自由落体**、但会一路穿过堆面 ⇒ `max(堆面, 前沿)` 自动交还,
        末段与 2.13 **逐位相同**。实测两者一致: t=1.0 解析 1205.1 / 粒子 1205.7;
        t=2.0 解析 582.7 / 粒子 583.9。
        ⚠️ 初速/重力必须与 `update_particles` 同源(`motion_scale` 那两处), 否则短周期档分叉。
        """
        y_end = 2 * self._neck_y - self._taper["y_bot"]
        if not NECK_FRONT:
            return self._lower_sand_bot
        ms = self._particle_motion_scale
        tau = self.elapsed - self._neck_fill_time       # 第一颗沙离口的时刻
        if tau <= 0.0:
            return y_end
        v0, g = 60.0 * ms, 450.0 * ms * ms
        return max(y_end - (v0 * tau + 0.5 * g * tau * tau), self._lower_sand_bot)

    def _neck_dump_canvas(self):
        """**直接问画布**: 逐条列出指令的包围盒与纹理 —— 判"哪一行是谁画的"。"""
        tag = int(self.elapsed)
        if tag == getattr(self, "_neck_dump_t", -1):
            return
        self._neck_dump_t = tag
        print("NECKDUMP t=%.1f widget pos=(%.1f,%.1f) size=(%.1f,%.1f)"
              % (self.elapsed, self.x, self.y, self.width, self.height))
        seen = [0]
        try:
            _c = list(self.canvas.children)
            _names = ("_splash_group", "_dust_group", "_flare_group",
                      "_surface_marker_group", "_neck_grain_group",
                      "_upper_carve", "_mound_carve", "_upper_band", "_mound_band",
                      "_neck_quads", "_neck_free_band", "_contact_grains",
                      "_upper_flow", "_neck_flow")
            for _i in range(max(0, len(_c) - 22), len(_c)):
                _o = _c[_i]
                _who = [nm for nm in _names if getattr(self, nm, None) is _o]
                _sub = getattr(_o, "children", None)
                print("NECKORD [%2d] %-18s %-22s kids=%s"
                      % (_i, type(_o).__name__, (_who[0] if _who else ""),
                         len(list(_sub)) if _sub else 0))
        except Exception as exc:
            print("NECKORD failed: %s" % exc)
        try:
            _c = list(self.canvas.children)
            for _i in (52, 51, 53):
                if _i < len(_c):
                    _o = _c[_i]
                    _who = [nm for nm in ("_splash_group", "_dust_group", "_flare_group",
                                          "_surface_marker_group", "_neck_grain_group",
                                          "_stream_pools", "_upper_carve", "_mound_carve",
                                          "_upper_band", "_mound_band", "_neck_quads",
                                          "_neck_free_band", "_upper_flow", "_neck_flow")
                            if getattr(self, nm, None) is _o]
                    print("NECKDUMP canvas.children[%d] = %s  %s"
                          % (_i, type(_o).__name__, _who or "?"))
        except Exception as exc:
            print("NECKDUMP who failed: %s" % exc)

        def leaf(node, path):
            _path = path
            if type(node).__name__ == "Mesh" and hasattr(node, "vertices"):
                try:
                    vv = list(node.vertices)
                    xs = vv[0::4]; ys = vv[1::4]; us = vv[2::4]; vs = vv[3::4]
                    keep = [i for i in range(len(xs)) if not (xs[i] == 0.0 and ys[i] == 0.0)]
                    if keep:
                        U = [us[i] for i in keep]; V = [vs[i] for i in keep]
                        _m = node
                        print("NECKFIELDS n_verts=%d n_idx=%s tex=%s in_canvas=%s"
                              % (len(vv), len(list(_m.indices)) if hasattr(_m, "indices") else "?",
                                 getattr(getattr(_m, "texture", None), "size", None),
                                 any(_m is c for c in list(self.canvas.children))))
                        print("NECKUV %-14s pos y=[%.0f..%.0f] x=[%.0f..%.0f]  "
                              "uv.u=[%.4f..%.4f] (Δ%.4f)  uv.v=[%.4f..%.4f] (Δ%.4f)"
                              % (_path[-14:], min(ys[i] for i in keep), max(ys[i] for i in keep),
                                 min(xs[i] for i in keep), max(xs[i] for i in keep),
                                 min(U), max(U), max(U) - min(U),
                                 min(V), max(V), max(V) - min(V)))
                except Exception:
                    pass
            cls = type(node).__name__
            if cls == "Color":
                try:
                    r, g, b, a = node.rgba
                    if 0.25 <= a <= 0.42:
                        print("NECKDUMP  **Color a=%.3f rgb=(%.3f,%.3f,%.3f) path=%s"
                              % (a, r, g, b, _path))
                except Exception:
                    pass
                return
            if cls in ("BindTexture", "StencilPush", "StencilPop",
                       "StencilUse", "StencilUnUse", "PushMatrix", "PopMatrix",
                       "Rotate", "Translate", "Scale", "MatrixInstruction"):
                return
            bb = None
            try:
                if hasattr(node, "vertices"):
                    vv = list(node.vertices)
                    if len(vv) >= 4 and (len(vv) % 4 == 0):
                        xs0 = vv[0::4]; ys0 = vv[1::4]
                    else:
                        xs0 = vv[0::2]; ys0 = vv[1::2]
                    # 🔴 **必须排掉 `zero()` 掉的四边形**(x,y 同时为 0) —— 否则每个
                    #    `_QuadBand` 的包围盒都被拉到原点, 一律显示 y=[0..X], 等于没量。
                    xs = [x for x, y in zip(xs0, ys0) if not (x == 0.0 and y == 0.0)]
                    ys = [y for x, y in zip(xs0, ys0) if not (x == 0.0 and y == 0.0)]
                    if xs and ys:
                        bb = (min(xs), min(ys), max(xs), max(ys))
                elif hasattr(node, "points"):
                    pts = list(node.points)
                    xs = pts[0::2]; ys = pts[1::2]
                    if xs and ys:
                        bb = (min(xs), min(ys), max(xs), max(ys))
                elif hasattr(node, "pos") and hasattr(node, "size"):
                    bb = (node.pos[0], node.pos[1],
                          node.pos[0] + node.size[0], node.pos[1] + node.size[1])
            except Exception:
                bb = None
            if bb is None:
                return
            seen[0] += 1
            tex = getattr(node, "texture", None)
            tn = "None" if tex is None else ("%dx%d" % tuple(tex.size))
            print("NECKDUMP  %-20s y=[%8.1f..%8.1f] x=[%8.1f..%8.1f] tex=%s n=%d"
                  % (cls, bb[1], bb[3], bb[0], bb[2], tn, seen[0]))

        def walk(node, depth, path):
            ch = getattr(node, "children", None)
            if ch:
                for k, c in enumerate(list(ch)):
                    walk(c, depth + 1, path + "/%s[%d]" % (type(c).__name__, k))
                return
            leaf(node, path)

        # 紧凑树: 只看结构, 回答"Rectangle 到底在不在"
        try:
            tops = list(self.canvas.children)
            print("NECKDUMP canvas 顶层 %d 个" % len(tops))
            for k, c in enumerate(tops):
                cn = type(c).__name__
                sub = getattr(c, "children", None)
                if sub:
                    kinds = {}
                    for s in list(sub):
                        kinds[type(s).__name__] = kinds.get(type(s).__name__, 0) + 1
                    print("NECKDUMP   [%2d] %-18s children=%d %s"
                          % (k, cn, len(list(sub)), kinds))
                else:
                    print("NECKDUMP   [%2d] %-18s (leaf)" % (k, cn))
            ids = [id(r) for _c, r in getattr(self, "_sand_chords", [])]
            print("NECKDUMP _sand_chords=%d 个 rect, id=%s" % (len(ids), ids))
        except Exception as exc:
            print("NECKDUMP tree failed: %s" % exc)

        for holder in ("before", "", "after"):
            try:
                c = getattr(self.canvas, holder) if holder else self.canvas
                print("NECKDUMP --- canvas.%s ---" % (holder or "self"))
                walk(c, 0, holder or "canvas")
            except Exception as exc:
                print("NECKDUMP walk %s failed: %s" % (holder, exc))

    def _neck_sand_side(self):
        """颈部只有一个自由表面; 上球耗尽后从上往下排空。"""
        tp = self._taper
        pts = tp['in_pts']
        y_top, y_end = pts[0][1], 2 * self._neck_y - tp['y_bot']
        start, _end, reserve = self._transfer_timing()
        if self.elapsed <= 0:
            return []
        if self.elapsed < start:
            bottom = y_top - (y_top - y_end) * self.elapsed / start
            top = y_top
        else:
            upper = self._upper_sand_fraction()
            if upper > 0.0:
                height = self._upper_sand_height_px()
                d, b = self._upper_funnel_params(1.0 - upper, height)
                center = (self._upper_sand_bot + self._upper_level_for(height)
                          - self._upper_surface_drop(0.0, d, b)
                          + self._upper_rough_at(MOUND_SHAPE_NODES // 2, height))
                top = min(y_top, center)
            else:
                released = (self._sand_released if self._sand_active
                            else self._released_fraction_at(self.elapsed))
                neck = max(0.0, 1.0 - released)
                if neck <= 1e-12:
                    return []
                top = self._neck_surface_y(min(1.0, neck / reserve))
            bottom = y_end
        if top <= bottom:
            return []
        side = [(self._neck_width_at(top), top)]
        side.extend((x, y) for x, y in pts if bottom < y < top)
        side.append((self._neck_width_at(bottom), bottom))
        # 🔴 **2026-10-09: 沙柱的下沿不许钉在玻璃线上。**
        #    用户原话:「管子里的沙的处理有问题, 完全不符合直觉」「不可能是让沙堆涨上去吧?
        #    而是自己落下来」。病根在**形状的来源**: 这根沙柱的轮廓是照抄**玻璃内壁**
        #    (`in_pts`) 描的, 下沿钉死在 `y_end`(= 玻璃直筒的下端) —— 于是同一坨沙
        #    在玻璃线以上是"一整块实心"、以下换成"撒下去的一把沙", 在一条**玻璃线**上
        #    被一刀切平。**沙不认识"管口"这条线。**
        #    改法(无魔数, 且天然有界): 下沿 = `max(沙堆面, 下球内壁的顶)` ——
        #      · 沙堆还没长上来 ⇒ 下沿 = 球内顶(沙柱穿过喇叭口, 到球顶为止)
        #      · 沙堆长进喇叭口 ⇒ 下沿 = 沙堆面, **自动接上**
        #      · 下探量 ≤ 喇叭口高 + 沙堆已探入的那一小段 ⇒ **不可能变成"贯穿下球的杆"**
        #        (这正是 1.238 的死因: 它的锚是**标量** `get_mound_top_y()`, 堆≈0 时
        #         那个标量等于**球内底**, 用户判词「你不要顾头不顾腚」)
        #    ⚠️ 只在"沙柱已经到过玻璃线"之后生效 —— 注满动画期间不许跳(否则前 0.25s
        #      那段落砂动画会被整段跳过)。
        if NECK_JOIN and bottom <= y_end + 1e-6:
            # 🔴 **2026-10-09 更正(D1)**: 这里原来写的是
            #    `_join = max(self.get_mound_top_y(), self._lower_sand_top)`,
            #    注释还说"沙堆长进喇叭口 ⇒ 下沿 = 沙堆面**自动接上**" —— **那是错的**:
            #    **第二项永远不赢, `max` 是死代码。** 构造性证明(逐字赋值, 不是近似):
            #      `contact = clamp(apex+off, floor, roof)`, 而 dx=0 时 `roof = 2·radius`;
            #      `_MoundProfile` 全仓库**只用 `Ri` 构造**(3254/3300), 与 `_lower_sand_top`
            #      里的同一个 `Ri` ⇒ `get_mound_top_y() ≤ _lower_sand_bot + 2Ri = _lower_sand_top` ∎
            #    实测复核: 1/5/15/50s × 96 帧, 第一项胜出 **0** 帧。
            #    ⇒ **真实机制不是"跟沙走", 而是"把切口从【出口】挪到了【球内顶】"** ——
            #      换了条固定的线而已。t=48/49 看着"正好接上", 是因为**两边同时被钉在
            #      同一个球内顶**上, 不是它接上去的。
            #    ⚠️ 将来若有人给 `_MoundProfile` 换一个 ≠ `Ri` 的半径, 这条等式会**静默失效**
            #       (`verify_hourglass.py` 里有断言守着它)。
            _join = self._lower_sand_top
            if _join < bottom - 1e-9:
                # 🔴 **2026-10-09 (D6): 下沿要跟着【堆面】走, 不是一条水平线。**
                #    柱子是**恒宽**的(`_neck_width_at(y<y_bot)` 兜底返回 `t_in`), 而堆面
                #    在中轴最高、向两侧低下去 —— 水平下沿会在两只角上**悬空**
                #    `t_in²/(2Ri)`(平板 1s/5s 档 **2.54px**, 桌面 0.37px)。
                #    改成两个节点: `(t_in, 堆面(t_in))` → `(0, 堆面(0))`,
                #    最后一条四边形因此是个 apex 朝上的三角形, 正好把那块空楔填掉。
                #    ⚠️ 这条四边形**不再单调下降**(y 从 1316.8 回到 1319.3), 是有意的:
                #      绘制循环只要求两个相邻节点, 不要求单调; 但**容量要够**(见 `_neck_quads`)。
                #
                # 🔴 **2026-10-09 (F5): 但下沿也不能一头扎到堆面 —— 要先看"沙落到了哪儿"。**
                #    堆面是**碰撞面**, 不是**在途沙**。50s 档前 2 秒实测: 柱子在
                #    `[出口, 球内底]` 整段实心, 而同一时刻**一个粒子都没有**(沙还在半路)。
                #    ⇒ 多出来的那一大截是**凭空画的沙**。真实的下沿 = `_falling_front()`。
                _front = self._falling_front()
                _w = self._neck_width_at(_join)
                # 🔴 **2026-10-09: 前沿不能是一刀平。** 用户看 2.14 截图问「沙柱头部也是平的
                #    合理吗」—— 量出来**起伏 0.0px**(前沿说了算时, 沙柱最后两个节点的 y
                #    完全相同, `tools/_probe_front_shape.py`)。物理上该是**穹顶**: 孔口处的
                #    速度剖面 `v(x) = v0·sqrt(1-(x/R)²)` 中间快(Janda 等在 2D 料斗孔口的
                #    自相似实测), 所以**中轴领先、两边落后**。
                #    `HG_NECK_DOME` = 边缘比中轴高出的量, 以**半宽**为单位(0 = 平头 = 2.14)。
                # 🔴 **2026-10-11 四改: 穹顶不再按 `_fall` 封顶(它把顺序搞反了), 抖动改成
                #    按柱长**平滑渐入**(有下限, 不再出生即归零)。**
                #    用户判词: 「刚开始的时候**有一个很尖尖的头, 突然又不尖了**, 很奇怪, 很固化」。
                #    设备开场逐帧(10 帧)实测的序列正是: **平 → V 尖 → 突然变钝**。
                #    根因: `_fall = bottom - _falling_front()`, 而**出生时粒子前沿还没生成**
                #    ⇒ 它取到一个很远的 y ⇒ `_fall` 大 ⇒ 穹顶**满值**(V 尖); 真前沿一出来
                #    `_fall` 骤降 ⇒ 穹顶≈0 ⇒ **一瞬间从尖变钝** —— 那个"固化"就是这一跳。
                #    现在: 穹顶用**常数**(`NECK_FRONT_DOME*_w`, 不再跟前沿走),
                #    抖动 = 满幅 × `clamp(_span/30, 0.3, 1)`(**渐入且出生就有 30%**)
                #    ⇒ 形状连续、不跳, 且一出生就带不规则。
                _bulge = NECK_FRONT_DOME * _w

                def _front_at(_dx):
                    return _front + _bulge * (_dx / _w) ** 2 if _w > 1e-6 else _front
                _yc, _Ri = self._lower_y_c, self._R_inner
                _c = self._mound_contact_h
                _bot = self._lower_sand_bot

                def _smax(_a, _b):
                    """平滑 max —— 见 `NECK_SOFT_END`。`k=0` 时逐字等于内建 `max`。"""
                    if NECK_SOFT_END <= 0.0:
                        return _a if _a > _b else _b
                    _d = abs(_a - _b)
                    if _d > 5.0 * NECK_SOFT_END:          # 离得远 ⇒ 退化成 max(省 exp)
                        return _a if _a > _b else _b
                    return (max(_a, _b)
                            + NECK_SOFT_END * math.log1p(math.exp(-_d / NECK_SOFT_END)))

                def _end_at(_dx):
                    _my = _bot + _c(_dx)
                    _wl = _yc + math.sqrt(max(0.0, _Ri * _Ri - _dx * _dx))
                    _fr = _front_at(_dx) if NECK_FRONT else _front
                    if _my > _wl:
                        # D6: 堆面那条腿越了球内壁 ⇒ 夹回来。
                        # **前沿**越了不算越界 —— 出口以下、管径以内是**喇叭口**,
                        # 那是玻璃的一部分(球内壁那条线到球顶就没了)。
                        return _smax(_fr, _wl) if NECK_FRONT else _wl
                    return _smax(_my, _fr) if NECK_FRONT else _my

                # 🔴 **2026-10-11: 出口以下是否发射材质几何** —— 见 `NECK_FREE_GEOM`。
                #    关掉时这一段整块不执行 ⇒ `side` 在多边形**出口**处收尾
                #    ⇒ 出口以下只剩粒子层(用户要的"只有一层")。
                #    ⚠️ 先预置这几个量 —— 下面 `NECKDBG` 那行无条件读它们(关掉时值无意义, 但不许 NameError)。
                _w_end = _w
                _y_endw = _y_end0 = _end_at(0.0)
                _span = bottom - _y_endw
                if NECK_FREE_GEOM:
                                    # 🔴 **自由收缩段**: 出口 → 下沿, 半宽按 `_free_width_ratio` 收窄
                                    #    (vena contracta + A·v=常数, 与粒子同一条式子)。收缩集中在前 40px,
                                    #    所以那几个节点按 40px 均分采, 而不是按整段落程均分。
                                    _w_end = _w * self._free_width_ratio(bottom - _end_at(_w)) * FLOW_FREE_MARGIN
                                    _y_endw, _y_end0 = _end_at(_w_end), _end_at(0.0)
                                    _span = bottom - _y_endw
                                    if _span > 1.0:
                                        for _k in range(1, NECK_TAPER_SEGS + 1):
                                            _d = FLOW_SHRINK_RAMP * _k / float(NECK_TAPER_SEGS)
                                            if _d < _span - 1.0:
                                                side.append((_w * self._free_width_ratio(_d) * FLOW_FREE_MARGIN,
                                                             bottom - _d))
                                        # 🔴 **N1(2026-10-09): 40px 之后补节点。** 不做这一步, `d>40px` 那一段
                                        #    永远是**一条直线弦**(用户报的"规整的直线"), 任何宽度律都画不出来。
                                        #    前密后疏: `d = span·(k/(K+1))^1.4`; 太靠近收缩段(<=40px)或端点(>=span-2)
                                        #    的丢掉 —— 那两个位置已有节点。**这一步本身零视觉变化**(当前律在 40px
                                        #    后恒 0.70, 多打的点仍落在同一条直线上)。
                                        _extra = []
                                        for _k in range(1, NECK_FREE_EXTRA_SEGS + 1):
                                            _d = _span * (_k / float(NECK_FREE_EXTRA_SEGS + 1)) ** 1.4
                                            if FLOW_SHRINK_RAMP + 2.0 < _d < _span - 2.0:
                                                _extra.append(_d)
                                        for _d in _extra:
                                            side.append((_w * self._free_width_ratio(_d) * FLOW_FREE_MARGIN,
                                                         bottom - _d))
                                    # 🔴 头部: 沿宽度打点 + 抖动(见 `NECK_FRONT_SEGS` 的注释)
                                    _ph = getattr(self, "_front_seed", 0.0)
                                    _et = self.elapsed
                                    for _k in range(NECK_FRONT_SEGS + 1):
                                        _f = 1.0 - _k / float(NECK_FRONT_SEGS)
                                        _dx = _w_end * _f
                                        # 🔴 **2026-10-10 三改: 抖动必须是"带限"的, 不能是尖刺。**
                                        #    用户判词: 「刚开场的尖刺…**应该是一个不规则的东西**」。
                                        #    旧版是两条正弦(5.3 / 11.9 rad) 打在**5 段**上 ⇒ 相邻节点相位差
                                        #    高达 2.4 rad ⇒ 相邻节点各自乱摆 ⇒ 画出来是一排**锯齿/尖刺**。
                                        #    现在: 12 段 + 三条**低频**谐波(2.7 / 6.1 / 11.3 rad ⇒ 最高 1.8 个周期,
                                        #    每周期 6.7 个节点) 且振幅递减 0.55/0.30/0.15(和为 1)
                                        #    ⇒ 形状**不规则但连续**, 相邻节点不会跳。
                                        #    ⚠️ 幅度**同时**受柱长封顶 —— 沙刚冒头时柱长只有几像素, 抖十几像素
                                        #    就成了"尖刺王冠"(2.43 用户截图点名过)。
                                        # 幅度 = 满幅 × 按柱长**平滑渐入**(30px 到顶), 下限 0.3 ⇒
                                        # **出生时就有三成不规则**(不是一根光溜溜的尖), 且不跳变。
                                        _amp = FLOW_FRONT_WOBBLE * _w_end * min(1.0, max(0.3, _span / 30.0))
                                        _wob = (_amp * (0.55 * math.sin(_f * 2.7 + _et * 1.3 + _ph)
                                                        + 0.30 * math.sin(_f * 6.1 - _et * 2.1 + _ph * 1.7)
                                                        + 0.15 * math.sin(_f * 11.3 + _et * 3.1 + _ph * 2.3)))
                                        side.append((_dx, _end_at(_dx) - _wob))
                                    # 🔴 **诊断用(2026-10-10)**: 设备实测"出口以下固体只占 ~28%"(`flowrate=1` 一臂),
                                    #    而这段代码看着应当把出口一直填到 `_end_at()`。**别再推理, 把它读出来。**
                                    #    标记文件 `<app>/necklog`(非空) ⇒ 每秒打一行。
                if _neck_log_on():
                    _tag = int(self.elapsed)
                    if _tag != getattr(self, "_neck_log_t", -1):
                        self._neck_log_t = _tag
                        _ys = [p[1] for p in side]
                        print("NECKDBG t=%.1f outlet=%.1f join=%.1f front=%.1f "
                              "end_at(w_end)=%.1f end_at(0)=%.1f w=%.2f w_end=%.2f "
                              "nodes=%d y=[%.1f..%.1f]"
                              % (self.elapsed, bottom, _join, _front,
                                 _y_endw, _y_end0, _w, _w_end, len(side),
                                 min(_ys), max(_ys)))
        # 🔴 **容量断言(2026-10-09)。** `_neck_quads` 装不下时, 绘制循环
                #    `for i in range(len(quads))` **静默丢掉最后几段, 一行报错都没有** ——
                #    项目踩过(`len(side)=27 > 容量 25`, 画面"一点没变"而原因查了很久)。
                #    容量表达式必须与 `_build_dynamic_canvas` 里那一处**逐字一致**。
        _cap = len(self._taper["in_pts"]) + 8 + NECK_FREE_EXTRA_SEGS + NECK_FRONT_SEGS
        if len(side) - 1 > _cap:
            raise AssertionError(
                "颈部沙柱节点 %d 段 > _neck_quads 容量 %d —— 超容量是**静默丢弃**, "
                "请同步改 `_build_dynamic_canvas` 里 `_QuadBand(...)` 的容量"
                % (len(side) - 1, _cap))
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
        if profile is None or h <= 0.0:
            apex = 0.0
        elif self._sand_active:
            apex = profile.apex_for_fraction(self._effective_fallen())
        else:
            apex = profile.apex_for_height(h)
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

    def _mound_edge(self):
        """沙堆**当前**的半宽(px) —— `has_sand` 为真的最远 |dx|, 即"最边缘"在哪。

        飞溅的高斯 σ 以它为基准(用户口径: 「边缘 = N 个标准差」, 见
        `SPLASH_BG_EDGE_SIGMA`)。**边缘随沙堆长大, 所以每帧要重算** ——
        钉死 σ 会让前期(沙堆还小)的飞溅撒到没沙的球底上。
        ⚠️ 每帧只调一次(不是每颗粒), 从外往里 2px 步进扫; σ 是 60px 量级,
        2px 的量化误差可以忽略。用 `has_sand` 而不是另写判据(工程红线)。
        """
        profile = self._mound_profile
        apex = self._mound_apex()
        if profile is None or apex <= 0.0:
            return 0.0
        # 🔴 **二分代替 2px 线性扫**(2026-10-07 性能)。原式从球壁往里 2px 一步扫,
        #   沙堆小的时候要扫 **~223 步**, 每步 `has_sand` 又是一次 `bounds`(sqrt) + `shape_at`
        #   ⇒ 设备实测 `_spawn_bg_splashes` **0.33ms/帧**, 而它每帧只生成约 8 颗飞溅
        #   —— 那 41µs/颗的怪数**全花在这个扫描上**。
        #   判据的单调性: `has_sand(dx) = raw(dx,apex) > floor(dx)`, 其中 `floor` 随 |dx|
        #   **单调快速上升**, 而 `raw` 只是缓慢起伏(粗糙度几 px) ⇒ 在"有没有沙"这个尺度上单调。
        #   二分 12 步 ⇒ 分辨率 2⁻¹²·R ≈ **0.04px**, 比原来的 2px 量化**更准**。
        hi = self._R_inner - 1.0
        if profile.has_sand(hi, apex):     # 满到贴壁: 原式也会立刻返回
            return hi
        lo = 0.0                            # 中心轴线上 `floor=0` ⇒ 必有沙(apex>0 已判)
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            if profile.has_sand(mid, apex):
                lo = mid
            else:
                hi = mid
        return lo

    def _mound_top_at(self, x):
        """绝对 y 版的接触高度 —— splash / 尘埃用(它们会跑到平台之外)。"""
        return self._lower_sand_bot + self._mound_contact_h(x - self._cx)

    def _contact_flow_diameter(self):
        return 2.0 * self._taper["t_in"] * FLOW_SHRINK_MIN

    def _mound_flow_strength(self):
        if self._last_impact_clock is None:
            return 0.0
        onset = _smoothstep(0.0, 0.08, self._mound_flow_clock - self._first_impact_clock)
        tail = 1.0 - _smoothstep(0.0, 0.2, self._mound_flow_clock - self._last_impact_clock)
        return onset * tail

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
            self._sand_active = True
            if self._geom_ready:
                self._transfer_timing()
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
        self._sand_released = self._sand_landed = self._sand_pending = 0.0
        self._sand_active = False
        self._sand_timing = None
        self._sand_resized = False
        # ⚠️ 背景飞溅的**预算累加器**也要清 —— 原来只在 `__init__` 初始化过一次,
        #    重置后残留的零头会让新一局头几帧多喷几颗(总量可控但没道理)。
        self._bg_splash_acc = 0.0
        self._mound_flow_clock = 0.0
        self._first_impact_clock = self._last_impact_clock = None
        self._contact_hits.clear()
        self._effect_random = None
        self._splash_reference_speed = None
        self._splash_stats = dict.fromkeys(self._splash_stats, 0)
        self._sn = 0                  # 飞溅: 只改存活数, 数组不必清(存活数之外无意义)
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        # ⚠️ 只清**每帧缓存**, 不动 `_mound_profile`/`_geom_generation` —— 那两个是**几何**,
        #    由尺寸/周期变化重建; 重置一局不能把沙堆形状解删掉(删了沙堆就画不出来)。
        self._mound_shape_cache = None       # ((几何代, elapsed), apex) —— 每帧只解一次
        self._mound_curve_cache = None       # ((几何代, elapsed), (cx+dx, y)) 接触曲线
        self._contact_table = []             # 飞溅用的接触高度查找表(见 update_particles)
        self._upper_level_key = None        # `_upper_level_for` 的每帧 memo
        self._upper_cols = None             # `_upper_area` 的布局缓存(dx/floor/roof)
        self._neck_tone_tab = None          # 颈部颗粒的色调查表(见 _draw_neck_grains)
        self._neck_tone_last = None         # 每槽上次写过的颜色(值没变就不写)
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

    def _flow_tone(self, k):
        """沙流调色板第 `k` 档(k=5 == `sand_base`)。

        🔴 **按"R 级数"等距, 不是"往 sand_dark/sand_light 走百分之几"**(2026-10-06 修)。
        后者踩过一次: `sand_dark` 离 base 有 **−33 级**, 而 `sand_light` 只有 **+13 级**
        ⇒ 同一个百分比在暗端是**两倍半的落差** ⇒ 只要暗半段露出来就**发黑**。
        设备实测(用户:「颈部的沙流颜色好像**发黑的沙子**」): 出口下方的 P5 到 (200,145,80),
        而**沙堆的 P5 是 (208,154,88)** —— 沙堆整条只跨 R 208~219(**很紧**),
        沙流却两头都超(暗尾 200 / 亮头 221)。
        现在每档固定 `FLOW_TONE_STEP` 级 ⇒ 整条 R 跨度 = 2×5×STEP。
        ⚠️ 每档**下限到 `sand_dark`/`sand_light` 为止**(不越界)。
        """
        d = (k - 5) * FLOW_TONE_STEP
        if d == 0.0:
            return self.sand_base
        end = self.sand_light if d > 0 else self.sand_dark
        span = abs(end[0] - self.sand_base[0]) * 255.0
        return lerp_rgb(self.sand_base, end, min(1.0, abs(d) / max(1e-6, span)))

    def _rebuild_color_table(self):
        self._color_table = [lerp_rgb(self.sand_base, self.sand_light, i / 10.0)
                             for i in range(11)]
        # 🔴 **沙流专用调色板: 以 `sand_base` 居中**(下标 5 == base)。
        #   原因(2026-10-06, 用户:「沙子流和沙层颜色差异过大」): 沙流原来直接用
        #   `_color_table`(**base→light 单向 11 档**), 再叠一条"越往下档位越高"的
        #   深度斜率(`div = (neck_y-glass_bot)/11`) ⇒ 自由落体**底部**的颗粒稳定落在
        #   8~10 档 = 接近 `sand_light`, 而**沙堆停在 base**。
        #   实测(**用户自己的截图**, 588x910, 50s 档):
        #     沙层 (206,163,103) / 沙流底部 **(219,183,123)** —— 亮 13R 21G 20B。
        #   改成居中之后: **均值 == 沙层色**, 抖动只负责给出与沙堆同性质的颗粒感,
        #   不再有系统性的偏亮。暗端取 `sand_dark` ⇒ 与沙堆材质的暗颗粒同源。
        self._flow_table = [self._flow_tone(k) for k in range(11)]
        # 🔴 **颈部颗粒的色调查表**(2026-10-07 性能)。原来每颗要现算:
        #   2 次 clamp + 3 次 lerp(得到 T) + 再 3 次 lerp(0.85 预混) = 6 次 lerp/颗。
        #   而整条式子化简后就是 `base + (light-base) * M`, `M = mix*0.85`(高光颗恒 0.85)。
        #   把 M **量化成 32 档**列表: 最大量化误差 = (light-base)/31/2 ≈ **0.21 级**,
        #   肉眼不可见; 换来每颗只要 1 次查表。
        self._neck_tone_tab = [
            tuple(self.sand_base[i]
                  + (self.sand_light[i] - self.sand_base[i]) * (j / 31.0)
                  for i in range(3))
            for j in range(32)]
        self._neck_tone_last = [None] * len(getattr(self, "_neck_grain_pool", ()))
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
        # 色调档的**亮度**表: 给 `_stream_grain_idx` 做"就近取档"用(单调, 可二分)。
        _lw = (0.299, 0.587, 0.114)
        self._flow_lum = tuple(c[0] * _lw[0] + c[1] * _lw[1] + c[2] * _lw[2]
                               for c in self._flow_table)
        self._material_lum = None          # (id(material), 亮度场) —— 见下
        # ⚠️ **必须在这里算**(而不是在 `_material_luminance` 的缓存未命中分支里):
        #    那条分支按 `id(material)` 命中就早退, 而本函数每次换配色都会把它清成 0
        #    ⇒ 基准一旦是 0, `tgt = 0 + g` 恒大于色表上界 ⇒ **所有颗粒饱和到最亮那一档**
        #    (实测症状: 柱内均值 174→181、高频 std 反而降到 0.97、`HG_GRAIN_GAIN`
        #     换三档画面逐像素相同)。2026-10-10 栽过一次。
        self._mat_lum_base = (self.sand_base[0] * 0.299 + self.sand_base[1] * 0.587
                              + self.sand_base[2] * 0.114) * 255.0

    def _material_luminance(self):
        """材质纹理的**亮度场** `(size, size) float32`, 按 material 身份缓存。

        面积 512² = 262144 个 float32(1 MiB), 只在换材质时算一次。
        """
        m = self._sand_material
        if m is None or _np is None:
            return None
        key = id(m)
        if self._material_lum is not None and self._material_lum[0] == key:
            return self._material_lum[1]
        try:
            a = _np.frombuffer(bytes(m.rgba), dtype=_np.uint8)
            size = int(round((a.size // 4) ** 0.5))
            a = a[:size * size * 4].reshape(size, size, 4).astype(_np.float32)
            lum = (a[:, :, 0] * 0.299 + a[:, :, 1] * 0.587 + a[:, :, 2] * 0.114)
        except Exception:
            return None
        self._material_lum = (key, lum)
        return lum

    def _stream_grain_idx(self, xs, ys, phases):
        """**沙流颗粒的色调改从材质那张颗粒场里取** —— 让沙流与沙体说同一套词汇。

        ## 为什么(2026-10-10, 用户判词「精细度都不够」)

        出口以下的**可见面其实就是粒子层**(实测覆盖柱宽 **89%**, 材质被压在下面),
        而粒子的色调原来只由**出生相位**决定、再量化到 `{0,3,6}` 三档;
        更要命的是它**按档序提交、后提交的压在上面**(`FLOW_TONE_CENTER` 的注释里
        记着这件事: 中点定 5 时"[0..5] 全被盖住") ⇒ 可见色被最高档支配
        ⇒ 叠出来是一层**平的涂抹**, 与上球那张 2~4px 的细颗粒场不是一套词汇。
        (旁证: 把出生点沿 y 摊开 2×直筒高只把 std 从 1.01 抬到 1.79, 材质面自身是 2.73
         —— **形状变了, 词汇没变**。)

        ## 做法

        在**与着色器完全相同的 uv 约定**下采样材质纹理的亮度:
            `u = 0.5 + (x − cx)/diameter`,  `v = (y − 上球内底)/diameter`
        再叠一个**每颗粒自己的相位偏移**(同一张场、不同实现 ⇒ 统计同族, 但不与底下
        那张逐像素相同 ⇒ 粒子仍然看得见"沙在落"); 偏离 base 的幅度再乘
        `FLOW_GRAIN_GAIN` 放大, 最后**就近取 `_flow_table` 的档**。

        返回 `w`(与旧路径同一个语义: `idx = w + (FLOW_TONE_CENTER−4)`), 形状同 `xs`。
        """
        lum = self._material_luminance()
        if lum is None or _np is None:
            return None
        d = max(2.0, 2.0 * self._R_inner)
        u = 0.5 + (xs - self._cx) / d
        v = (ys - self._upper_sand_bot) / d
        # 每颗粒自己的偏移(相位已在 [0,2π)) ⇒ 同一张噪声场、不同实现
        u = u + (phases * 0.15915494309189535)      # ×1/(2π)
        v = v + (phases * 0.07957747154594767)      # ×1/(4π)
        size = lum.shape[0]
        ix = (u * size).astype(_np.int64) % size
        iy = (v * size).astype(_np.int64) % size
        g = lum[iy, ix] - self._mat_lum_base
        g = g * FLOW_GRAIN_GAIN
        # 就近取档: `_flow_lum` 单调 ⇒ 二分
        tgt = self._mat_lum_base + g
        idx = _np.searchsorted(_np.asarray(self._flow_lum), tgt)
        idx = _np.clip(idx, 0, len(self._flow_lum) - 1)
        return idx - (FLOW_TONE_CENTER - 4)

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
        # ⚠️ `_voice_bank` 可能是 **None** —— 词库是**第一帧之后**才加载的(见 __init__ 里那段),
        #    这期间完成播报走预录兜底。少这个判空就是启动后立刻完成 → AttributeError。
        if (self._voice_bank is not None and self._voice_bank.ok
                and self._play_completion_announcement(duration)):
            return
        if self._completion_sound is not None:
            self._completion_sound.stop()
            self._completion_sound.play()

    # ==================== 操作提示音(2026-10-07) ====================
    # 换沙色 / 改周期 / 换提示音 三个操作各补一句语音(文案由用户逐条拍定):
    #   换沙色   → 「金沙」                     (只说颜色名)
    #   改周期   → 「计时时间设定为一分钟」      (**只在点「确定」时**; 弹窗里调基础时间/
    #                                            倍数/拖滑杆一律不播 —— 用户明确要求)
    #   换提示音 → 「提示音设定为沙沙声」        (点选项**真的切换成功**时才播)
    # 播法照抄完成播报那条唯一跑通的路径: **拼 PCM → 写 wav → 独立的 _SoundProxy**
    # (必须走文件: 三个后端里有两条按路径播)。
    #
    # ⚠️ **通道**: Windows winsound 单通道且 `SND_PURGE` 是**全局停播** ⇒ 语音会顶掉背景
    #    循环音, 必须"让路 + 归还"; 安卓 AudioTrack 每个 proxy 独立一条 track ⇒ 天然混播,
    #    **不做让路**(否则用户会白听一个停顿)。
    def _voice_say(self, keys, duck=True):
        """按词块键顺序拼成一句播出去。词库没就绪 / 任何异常 ⇒ **静默跳过**。"""
        bank = self._voice_bank
        if bank is None or not bank.ok or not keys:
            return False
        try:
            clips = bank.clips
            if any(k not in clips for k in keys):
                return False
            pcm = b"".join(clips[k] for k in keys)
        except Exception:
            return False
        cache_dir = os.path.join(os.path.dirname(config_path()), "voice_cache")
        path = os.path.join(cache_dir, "%s.wav" % "_".join(keys))
        try:
            if not os.path.exists(path):
                os.makedirs(cache_dir, exist_ok=True)
                tmp = path + ".tmp"
                with wave.open(tmp, "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(bank.rate)
                    stream.writeframes(pcm)
                os.replace(tmp, path)          # 原子换名: 半截文件永远不会被读到
        except Exception as exc:
            print("voice prompt write failed: %s" % (exc,))
            return False
        # 让路: 只在 winsound 这条单通道后端上做(安卓/Kivy 都能混播, 不必打断背景音)
        held = False
        if (duck and self.running and self._sound is not None
                and getattr(self._sound, "backend", "") == "winsound"):
            self._stop_sound()
            held = True
        old = getattr(self, "_voice_prompt", None)
        if old is not None:
            old.stop()
            old.close()
            self._voice_prompt = None
        try:
            proxy = _SoundProxy(path, loop=False)
        except Exception as exc:
            print("voice prompt audio init failed: %s" % (exc,))
            if held:
                self._play_sound()
            return False
        if proxy.backend == "none":
            if held:
                self._play_sound()
            return False
        self._voice_prompt = proxy
        proxy.play()
        if held:                                # 播完按 PCM 时长归还背景音
            dur = len(pcm) / float(bank.rate or 24000)
            Clock.unschedule(self._voice_release_bg)
            Clock.schedule_once(self._voice_release_bg, dur + 0.05)
        return True

    def _voice_release_bg(self, _dt=None):
        if self.running:
            self._play_sound()

    def _voice_keys_color(self, name):
        """色块名 → `c0..c5`, **序号与 `SAND_PRESETS` 同序**。"""
        for i, preset in enumerate(SAND_PRESETS):
            if preset[0] == name:
                return ["c%d" % i]
        return None

    def _voice_keys_sound(self, name):
        """音效名 → `e0..e4`, **序号与 `SOUND_OPTIONS` 同序**。"""
        for i, (n, _path) in enumerate(SOUND_OPTIONS):
            if n == name:
                if n == SILENT_NAME:
                    # 用户 2026-10-07: 选「无声音」不说"设定为无声音"(绕), 直接说
                    # **「提示音改为无声」**(整句, 独立的 pre_silent 词块)。
                    return ["pre_silent"]
                return ["pre_sound", "e%d" % i]
        return None

    def _voice_keys_duration(self, seconds):
        """时长 → `pre_time` + `sentence_keys` 的词序。

        ⚠️ **必须去掉末尾的 `tail`** —— `sentence_keys` 恒在结尾附一句
        「的沙漏计时完成」(那是给**完成播报**用的) ⇒ 不去掉会念成
        「计时时间设定为一分钟的沙漏计时完成」。
        """
        bank = self._voice_bank
        if bank is None or not bank.ok:
            return None
        keys = list(bank.sentence_keys(seconds, zero=False))
        if keys and keys[-1] == "tail":
            keys.pop()
        return ["pre_time"] + keys

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

    def _live_flow_bucket_keys(self):
        """**可能**有内容的沙流桶 —— 由取色调的算式直接推出来, 不是"看它一直是空的"。

        沙流那一行把色调档磨成 `FLOW_TONE_QUANT` 的倍数(`idx -= idx % FLOW_TONE_QUANT`), 再叠上 `FLOW_TONE_CENTER`
        的偏移与 `clip(0, last)` —— 把 `w` 的全部取值域(0..8, `np.minimum(w, 8)` 封顶)
        代进去, 落在合并后的**只有 {0, 3, 6}**; 再加高光那一档(`key = n_colors`,
        在 `_stream_pools` 里就是 `-1`)。
        ⇒ `_stream_pools` 那 **24** 个桶里, 另外 **16 个永远拿不到颗粒**。

        ⚠️ 两条路径的次序不同(numpy 先 clip 后合并, 标量先合并后 clip)⇒ 这里两种次序
        都算一遍取并集, 免得将来改了一处就漏。
        ⚠️ **只用来决定"预热哪些桶"** —— 画布结构(24 个组)一个字没动, 所以不涉及像素。
        """
        last = len(self._color_table) - 1
        out = set()
        for w in range(9):
            a = w + (FLOW_TONE_CENTER - 4)
            a = 0 if a < 0 else (last if a > last else a)
            a -= a % FLOW_TONE_QUANT
            b = w + (FLOW_TONE_CENTER - 4)
            b -= b % FLOW_TONE_QUANT
            b = 0 if b < 0 else (last if b > last else b)
            out.add(a)
            out.add(b)
        out.add(-1)                      # 高光档
        return out

    def _warm_flow_depth(self, chunk_cap):
        """每个沙流桶要预热几块 —— **由在途粒子数的上界推出来**, 不是常数。

        为什么不是常数(2026-10-07 晚, 用户平板 1.221 的 log):
        每档**必然有一帧** `图元` 9.2~12.5ms(稳态 2.0), 同帧 `ia=1 / vrb=160 KiB`
        —— 运行期现建了一块沙流(160 KiB 顶点表, 设备 ~7.7ms)。
        根因是**块容量固定 512, 而最热的那个桶峰值 1525 颗** ⇒ 要 3 块, 只备了 2 块。
        桶里有多少颗 **∝ 粒子的下落路程 ∝ 窗口高度** ⇒ 平板上要 3 块, 我们桌面
        400×800 的测试窗口永远只要 2 块 ⇒ **本机一直复现不出来**。

        上界: `在途 ≈ rate × 飞行时间`, 而
        `rate = FLOW_BASE_RATE × motion_scale`、`飞行时间 = natural / motion_scale`
        ⇒ **`在途 ≈ FLOW_BASE_RATE × _natural_flight_time`**(与 motion_scale 无关)。
        再乘最热桶的占比(实测 0.49, 取 0.55 留余量)除以块容量。

        ⚠️ `_natural_flight_time` 用**当前**下沙面算距离 ⇒ 沙子落下去之后它会变小,
        是**保守方向**(早期估得更大), 不会造成欠备。
        """
        if chunk_cap <= 0 or not getattr(self, "_geom_ready", False):
            return WARM_FLOW_CHUNKS
        in_flight = FLOW_BASE_RATE * self._natural_flight_time
        need = int(math.ceil(WARM_FLOW_BUCKET_SHARE * in_flight / float(chunk_cap)))
        return max(WARM_FLOW_CHUNKS, min(WARM_FLOW_CHUNKS_MAX, need))

    def _collect_warm_jobs(self):
        """列出"待预热的块"。**顺序 = 重要性**: 沙流第 0 块 → 沙流第 1 块 → 颈部 → 飞溅 → 闪光。

        预热可能被"按下开始"打断(它只在没在跑的时候做), 所以**先把最要紧的那半做完** ——
        沙流第 0 块是"所有桶同时第一次有内容"那一帧要用的, 缺了它那一帧会退回 40ms 级。
        """
        jobs = []
        # 🔴 **只预热"可能有内容"的桶**(2026-10-07 晚): 24 个桶里只有 8 个拿得到颗粒
        #    (见 `_live_flow_bucket_keys` 的推导)⇒ 另外 16 个桶的每一块都是**纯浪费**:
        #    块一旦建好就恒画 `CHUNK` 个四边形(索引一次给满), 每帧白走一遍顶点着色器。
        #    实测(平板几何 1904×2890): 预热 2 块时 24×2=48 块 = 24576 个四边形/帧,
        #    其中真有内容的只有 ~11 块。滤掉死桶之后, **深度翻了倍但总量反而更小**。
        live = self._live_flow_bucket_keys()
        flow = [b for key, b in (getattr(self, "_flow_batches", None) or {}).items()
                if key[0] in live and hasattr(b, "warm")]
        depth = self._warm_flow_depth(flow[0].CHUNK if flow else 0)
        for chunk in range(depth):
            for batch in flow:
                jobs.append((batch, chunk, WARM_COST_FLOW))
        for item in (getattr(self, "_neck_batches", None) or ()):
            batch = item[1]
            if hasattr(batch, "warm"):
                jobs.append((batch, 0, WARM_COST_SMALL))
        splash = getattr(self, "_splash_batch", None)
        if splash is not None and hasattr(splash, "warm"):
            for chunk in range(WARM_SPLASH_CHUNKS):
                jobs.append((splash, chunk, WARM_COST_SMALL))
        flare = getattr(self, "_flare_batches", None)
        if flare is not None and hasattr(flare, "warm"):
            jobs.append((flare, 0, WARM_COST_SMALL))
        jobs.reverse()                       # 用 `pop()` 从头取 ⇒ 上面的顺序名副其实
        return jobs

    def _warm_batches_step(self):
        """把"第一次有内容才建"的批处理块, **挪到动画之外**一帧一块地建好。

        ## 为什么必须有这个(2026-10-07, 由用户设备 Lenovo TB323FU 的 log 定位)

        沙柱注满前**不出粒子** ⇒ 注满那一帧**所有桶同时**第一次拿到内容 ⇒ 那一帧要建 7 块。
        实测那一帧的 `图元` = **43.88ms**(稳态 2.9ms), 三档的落点
        **1s→t=0.156s / 5s→t=0.25s / 15s→t=0.25s** 正好都是 `_neck_fill_time`;
        之后每帧还有 ~8 次"索引重赋"(每次 ~90 KiB 顶点表重建)衰减到 0.7 次/帧。
        ⇒ 用户看到的"**必然有一帧很低**"(1% low 63~75 / 最慢帧 28~35ms)。

        ## 规则

        * **只在没在跑的时候做** —— 设备上建一块 ≈6ms, 混进动画帧就是新的卡顿。
          正常使用时这 0.3 秒落在用户按下"开始"**之前**(改周期/转屏之后立刻开始跑的话,
          预热会暂停, 代价退回今天这样, **不会更差**)。
        * **每帧限预算** —— 一帧内把 24+37 块全建出来就是又一次 200ms 冻结。
        * 队列在**画布重建**时重取(`_flow_batches` 是每次重建新建的 dict, 身份即代次)。
        * 任何一块建失败就**整队放弃** —— 运行期照样按需建块, 只是没有这份优化。
        """
        if not WARM_ENABLED:
            return
        # ⚠️ **握对象引用, 不要只握 `id()`** —— 只存 id 的话上一代 dict 会被回收,
        #    CPython 的 dict freelist 很可能把**同一个地址**再发给新建的那个 ⇒ 世代判不出来。
        #    握住引用 ⇒ 上一代活着 ⇒ 地址不可能被复用 ⇒ `is` 一定准。
        src = (getattr(self, "_flow_batches", None),
               getattr(self, "_splash_batch", None))
        if src[0] is not self._warm_src[0] or src[1] is not self._warm_src[1]:
            self._warm_src = src
            self._warm_queue = self._collect_warm_jobs()
        queue = self._warm_queue
        if not queue:
            return
        budget = WARM_BUDGET_MS
        done = 0
        while queue:
            batch, chunk, cost = queue[-1]
            if done and cost > budget:
                break
            queue.pop()
            try:
                batch.warm(chunk)
            except Exception as exc:               # 装不上就整队放弃, 别每帧炸一次
                print("batch warm failed (%s); 放弃预热, 运行期照旧按需建块" % (exc,))
                del queue[:]
                return
            budget -= cost
            done += 1

    def tick(self, _dt_kivy):
        if not self._geom_ready:
            return
        now = time.perf_counter()
        dt = max(0.0, min(0.05, now - self.last_frame))   # 特效限幅; 沙流与计时共用 flow_dt
        self.last_frame = now
        flow_dt = 0.0
        completed = False
        if self.running:
            if self.last_tick is not None:
                previous = self.elapsed
                self.elapsed = min(self.duration, self.elapsed + max(0.0, now - self.last_tick))
                flow_dt = self.elapsed - previous
            self.last_tick = now
            # ⚠️ 换帧必须在**物理之前** —— 否则同一帧里"粒子撞的轮廓"和"画出来的轮廓"
            #    不是同一个(1.93 那类裂缝的根源)。
            self._sync_mound_frame()
            self.update_particles(dt, flow_dt=flow_dt)
            if self.elapsed >= self.duration:
                self.running = False
                self._done_at = now
                self._stop_sound()
                completed = not self._completion_triggered
                self._completion_triggered = True
        elif self._completion_triggered:
            self.update_particles(dt)
        else:
            self._warm_batches_step()
        self.redraw()
        app = App.get_running_app()
        if app is not None:
            app.update_time(max(0.0, self.duration - self.elapsed), self.duration)
        if completed:
            self._spawn_dust()
            self._play_completion_sound(self.duration)
            self._schedule_completion_popup(app)
            if app is not None:
                app.on_run_state_changed()

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
        # A full bulb has no exposed free surface to eject sand back into the empty neck.
        if mound_top >= self._lower_sand_top - 1e-6:
            return
        cx = self._cx
        w = self._sand_half_w(mound_top, self._lower_y_c)
        now = time.perf_counter()
        for _ in range(DUST_COUNT):
            self.dusts.append({
                "x": cx + random.uniform(-w * 0.7, w * 0.7),
                "y": mound_top + random.uniform(0, 5),
                "vx": random.uniform(-25, 25),
                # 🔴 2026-10-06: 原 20~60 配 g=450 ⇒ 最高只升 `vy²/900` = **0.44~4px**, 而颗粒自己就 3.6px;
                #    落回 `2vy/450` = 0.089~0.267s ⇒ 实测寿命 p50 0.24s、净上升 1.2px ⇒ **读不出"扬起"**。
                #    这是**重力用错对象**(450 是为"颈部→球底 600px"定的), 不是"该多淡"的取舍。
                #    改 120~220 ⇒ 升 16~54px、滞空 0.53~0.98s, 与 DUST_LIFETIME=1.0 自然咬合。
                "vy": random.uniform(120, 220),   # 向上喷(Kivy y 向上为正)
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

    # ---------------- 飞溅存储(并行数组, 与粒子同一套做法) ----------------
    def _s_alloc(self, cap):
        self._s_cap = cap
        for name in _S_FIELDS:
            setattr(self, name, self._newbuf(cap))

    def _s_grow(self, need):
        if need <= self._s_cap:
            return
        cap = self._s_cap or 512
        while cap < need:
            cap *= 2
        old = self._s_cap
        for name in _S_FIELDS:
            src = getattr(self, name)
            dst = self._newbuf(cap)
            dst[:old] = src[:old]
            setattr(self, name, dst)
        self._s_cap = cap

    def _s_append(self, x, y, vx, vy, hw, hh, gd):
        """追加一颗飞溅, 返回它的下标(调用方用它写 `_step_dt`, 与粒子的 `pn` 同法)。

        ⚠️ `hw`/`hh` 是**半宽半高**(不是全宽) —— 渲染每帧都要 `x±hw`, 存半宽就省掉
        每颗一次乘法。全宽用 `hw*2` 还原(浮点的乘 2 是精确的, 所以 dict 视图取整无损)。
        """
        i = self._sn
        if i >= self._s_cap:
            self._s_grow(i + 1)
        self.sx[i] = x
        self.sy[i] = y
        self.svx[i] = vx
        self.svy[i] = vy
        self.shw[i] = hw
        self.shh[i] = hh
        self.sgd[i] = gd
        self.srest[i] = 0.0
        self.sstill[i] = 0.0
        self.shas[i] = 0.0
        self.saway[i] = 0.0
        self.srw[i], self.srh[i] = hw, hh
        self.stag[i] = 0.0         # 取证探针的标记位(默认空)
        self.sdt[i] = 0.0          # 由调用方按需覆盖成"帧内偏步长"
        self._sn = i + 1
        return i

    def _s_tag_from(self, n0, value):
        """给**下标 >= n0** 的那批飞溅打标记(取证探针用; 在 `_spawn_bg_splashes` 之后调)。

        ⚠️ 用这个, **不要**写 `for sp in self.splashes[n0:]: sp["_tag"] = 1` ——
        `splashes` 是 property, **每次访问都重建一批新 dict**, 那个赋值落到临时对象上,
        静默无效且不报错。探针会因此把全部飞溅判成同一类 —— 正是它自己注释里警告过的
        那个历史误判。
        """
        end = self._sn
        if n0 < 0:
            n0 = 0
        if n0 < end:
            self.stag[n0:end] = value

    @property
    def splashes(self):
        """**只给取证工具/测试**的 dict 视图(每次访问重建, 慢)。

        ⚠️ 物理与渲染**都不读它** —— 它们走数组。留着它是为了让 `tools/` 下那十几个
        探针(`_probe_splash_sigma` 的 `_bg` 标记、`_probe_splash_population` 的
        `set(id(s))` 等)继续能跑。**别再往热路径上加它的读者。**
        """
        out = []
        for i in range(self._sn):
            d = {"x": float(self.sx[i]), "y": float(self.sy[i]),
                 "vx": float(self.svx[i]), "vy": float(self.svy[i]),
                 "size": (float(self.shw[i] * 2.0), float(self.shh[i] * 2.0)),
                 "gd": float(self.sgd[i])}
            rest = float(self.srest[i])
            if rest:
                d["_rest"] = rest
            if self.shas[i] != 0.0:
                d["_still"] = float(self.sstill[i])
            tag = float(self.stag[i])
            if tag:
                d["_tag"] = tag
            d["_motion_state"] = tuple(float(getattr(self, name)[i]) for name in _S_MOTION_FIELDS)
            out.append(d)
        return out

    @splashes.setter
    def splashes(self, items):
        """整体替换 —— 给工具用(`w.splashes = []` 清空 / 还原快照)。
        ⚠️ 原地改(`w.splashes[:] = []`)会改到**临时 list** 上, 静默无效 —— 那几处已改。"""
        self._sn = 0
        for d in items:
            sz = d.get("size", (1, 1))
            if not isinstance(sz, (tuple, list)):
                sz = (sz, sz)
            i = self._s_append(d.get("x", 0.0), d.get("y", 0.0),
                               d.get("vx", 0.0), d.get("vy", 0.0),
                               sz[0] * 0.5, sz[1] * 0.5, d.get("gd", 1.0))
            if d.get("_rest"):
                self.srest[i] = float(d["_rest"])
            if d.get("_still") is not None:
                self.shas[i] = 1.0
                self.sstill[i] = float(d["_still"])
            if d.get("_step_dt") is not None:
                self.sdt[i] = float(d["_step_dt"])
            if d.get("_tag"):
                self.stag[i] = float(d["_tag"])
            if "_motion_state" in d:
                for name, value in zip(_S_MOTION_FIELDS, d["_motion_state"]):
                    getattr(self, name)[i] = value

    def _p_refresh_view(self):
        """数组 -> `_pv`(Python list 快照)。每帧调一次, 并让 dict 缓存失效。

        只在物理/存储真正改动数组之后调用; 读端不碰 numpy 标量。
        """
        pv = self._pv
        n = self.pn
        pv.n = n
        pv.use_np = _np is not None and n >= _NUMPY_MIN
        # **作废上一帧的 list 缓存** —— 数组在这一帧可能已被物理改写。
        pv._x = pv._y = pv._vy = pv._tl = None
        pv._sz = pv._light = pv._wp = None
        if _np is None:
            pv.nx = pv.ny = pv.nvy = pv.ntl = None
            pv.nwp = pv.nsz = pv.nli = None
            # 兜底后端本身就是 Python list, 切片即得原生 float。
            pv._x = self.px[:n]
            pv._y = self.py[:n]
            pv._vy = self.pvy[:n]
            pv._tl = self.ptl[:n]
            pv._sz = self.psz[:n]
            pv._light = self.pli[:n]
            pv._wp = self.pwp[:n]
        else:
            # 零拷贝切片(数组视图, 不分配): 供纹理渲染器向量化打包。
            # 逐颗粒循环仍用下面的 list 快照 —— 两种读法各有各的便宜处。
            pv.nx = self.px[:n]
            pv.ny = self.py[:n]
            pv.nvy = self.pvy[:n]
            pv.ntl = self.ptl[:n]
            pv.nwp = self.pwp[:n]
            pv.nsz = self.psz[:n]
            pv.nli = self.pli[:n]
            # x/y/vy/tl/sz/light/wp 这七份 list **不再在这里建** —— 改成惰性属性
            # (见 `_FlowView` 顶部注释)。读端自己 `pv.x` 就会拿到, 行为逐位不变。
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
        pmass = self.pmass
        particles = []
        append = particles.append
        for i in range(pn):
            d = {
                "x": float(px[i]), "y": float(py[i]), "vy": float(pvy[i]),
                "x_offset": float(pxo[i]), "wobble_phase": float(pwp[i]),
                "wobble_amp": float(pwa[i]), "size": int(psz[i]),
                "trail_time": float(ptl[i]), "is_light": bool(pli[i]),
                "_sand_mass": float(pmass[i]),
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
        pmass = self.pmass
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
            pmass[k] = d.get("_sand_mass", 0.0)
        self.pn = n

    def _replay_hits(self, hit_idx, hit_dt, mound_top, motion_scale, now):
        """按下标升序回放命中事件 —— 随机数调用顺序与原标量循环逐字一致。

        ⚠️ 随机数只在 splash 成立时才抽, 顺序:
           `rand()<0.25`(flare) → `rand()<0.50`(splash) →
           `_eject_splash` 内部的 `uniform(vx) → uniform(vy) → choice((1,1,2))`。
        ⚠️ `mound_top` **未被使用**(出生高度用逐颗粒的 `hy`), 保留只为签名稳定。
        """
        px = self.px
        pvy = self.pvy
        pdt = self.pdt
        self._sand_landed += float(_np.sum(self.pmass[hit_idx]))
        rand = random.random
        append_flare = self.flares.append
        # ★ **全部命中的 hy 一次算完**(向量化)。原来每颗现调一次 `_mound_contact_h`,
        #   而它按**该颗粒的 x**(每帧都不同)走 `geometry_at` ⇒ 每帧几十上百次全 miss,
        #   还把那个缓存冲得定期整体清空 —— 设备分段实测这一族 **0.183ms/帧**。
        #   算式与 `_mound_contact_h` 逐字相同(见 `contact_np`), 由金标准轨迹兜底。
        _prof = self._mound_profile
        _apex = self._mound_apex()
        # 🔴 **2026-10-07 回退**: 这里曾用 `contact_np` 整批算 `hy`(向量化, 净 −0.05ms)。
        #    但加完之后逐像素比对显示 **30 帧里有 1 帧不同**(同代码两遍是 0 差异 ⇒ 渲染本身
        #    是确定的, 差别来自这次改动)。隔离守卫(`_probe_ctab_equiv` 的 300×62 点)与
        #    金标准轨迹都过了, 所以差异落在**两者都没覆盖的工况**上 —— 我没能定位到具体是哪
        #    一种输入。**收益 0.05ms 远小于一个我说不清的差异** ⇒ 整批路径不再启用。
        #    `contact_np` 与它的守卫留在代码里备查(要再启用, 先补一个能覆盖真实工况的判据)。
        hy_all = None

        def _hit_hy(kk, xx):
            """该颗粒**当地的接触高度** —— 与 scalar 路径同源(dingbu.md §7.1 第 6 条)。

            ⚠️ 只在真要用时才调(**推迟求值**, 2026-10-07 性能): 下面两个分支是
            `rand()<0.25` / `rand()<0.50`, **两个都不中时(概率 0.375)这颗的 hy 根本没人读**。
            而这一查走 `_mound_contact_h` → `geometry_at`, 每颗 x 都不同 ⇒ 命中率低,
            **设备实测单次 ~4.8µs**, 是整个 `_replay_hits` 里最大的一笔
            (`_replay_hits` 0.255ms/帧, 其中 `_mound_contact_h` 0.119)。**推迟后省掉 37.5%。**
            纯函数 + 不抽随机数 ⇒ **两个 `rand()` 的顺序一字未动**, 画面逐位不变。
            """
            if hy_all is not None:
                return float(hy_all[kk])
            return self._lower_sand_bot + self._mound_contact_h(xx - self._cx)

        for k in range(len(hit_idx)):
            i = int(hit_idx[k])
            x = float(px[i])
            vy = float(pvy[i])
            step_dt = float(pdt[i])
            first_contact = self._record_contact(x, -vy)
            hy = None
            if rand() < 0.25 * self._splash_density:
                hy = _hit_hy(k, x)
                append_flare({"x": x, "y": hy, "end": now + 0.08})
            selected = rand() < SPLASH_HIT_CHANCE * self._splash_density
            if selected or first_contact:
                if hy is None:
                    hy = _hit_hy(k, x)
                # ★ 唯一的飞溅模型(参数取自 PC v4) —— 见 `_eject_splash`。
                #   旧版这里把 v4 的"向上弹"改成了 `vy = -|cos|·bounce·0.2`(**朝下、0.2 倍**)
                #   ⇒ 抛物线没了, 就是用户说的「实际它是一个**先喷射再抛物线**」。
                step_left = step_dt - float(hit_dt[k])
                sp = self._eject_splash(x, hy, -vy, surface_aligned=True)
                if sp >= 0:                      # -1 = 撞上存活上限, 没生成(见 SPLASH_MAX)
                    self.sdt[sp] = step_left if step_left > 0 else 0
                if first_contact:
                    for _ in range(self._first_contact_extra(rand)):
                        sp = self._eject_splash(x, hy, -vy)
                        if sp >= 0:
                            self.sdt[sp] = max(0.0, step_left)

    def _first_contact_extra(self, rand):
        """首次触面**追加几颗** —— 随机取整(2026-10-09, 用户要求)。

        原写法 `round(2 * _splash_density)`, 而 `density ∈ [0.15, 1.5]`
        ⇒ `2×density ∈ [0.3, 3.0]` ⇒ `round()` 之后**只有 {0, 1, 2, 3} 四个取值**。
        实测它们在**整条时长轴上只形成 3 个台阶**(23.3s / 610s / 15933s 各跳一格),
        跨过台阶时**每一次**首次触面都同时多一颗或少一颗 ⇒ 观感上是"档"而不是连续变化。

        **随机取整**: 整数部分照给, 小数部分按概率补一颗 ⇒ 长期平均**精确等于** `2×density`,
        台阶消失。这就是"平均意义上的连续"——单次永远是整数颗, 但观众看到的是整体疏密。

        两条硬约束(踩过就白改):
        ① **`density == 1.5` 时绝不抽随机数**。那时 `want = 3.0`、`frac = 0.0`,
           把它写在 `frac > 0.0 and rand() < frac` 里靠短路跳过 —— 若写成 `rand() < frac`
           会**照样消耗一个随机数**, 整条流平移 ⇒ 用户已经确认过的"粗柱(≤5s)保持 2.9 + 加量
           150%"那一档会跟着变。这是本函数的**唯一**关键点。
        ② 两条物理路径(`_replay_hits` 与标量分支)必须**同一个 RNG**(都用 `random.random`),
           否则 `tools/test_physics_equiv.py` 的标量/numpy 逐位等价会红。
        """
        want = 2.0 * self._splash_density
        n = int(want)
        frac = want - n
        if frac > 0.0 and rand() < frac:
            n += 1
        return n

    def _record_contact(self, x, speed):
        first = self._splash_reference_speed is None
        if first:
            self._splash_reference_speed = max(float(speed), 60.0 * self._particle_motion_scale)
        self._contact_hits.append((self._mound_flow_clock, x, speed))
        return first

    def _splash_random(self):
        if self._effect_random is None:
            self._effect_random = random.Random()
            self._effect_random.setstate(random.getstate())
        return self._effect_random

    def _eject_splash(self, x, y_surface, v_impact, surface_aligned=True):
        """v1.244 的抛射模型; 只稳定力度、朝外出射并限制真实玻璃净空。"""
        rng = self._splash_random()
        gain = rng.uniform(SPLASH_SPEED_LO, SPLASH_SPEED_HI) * SPLASH_GAIN
        side = rng.choice((-1.0, 1.0))
        low = min(SPLASH_ANGLE_MIN, SPLASH_ANGLE_MAX)
        high = max(SPLASH_ANGLE_MIN, SPLASH_ANGLE_MAX)
        low = min(math.pi / 2, max(math.pi / 2 - SPLASH_HORIZONTAL_ANGLE_LIMIT, low))
        high = min(math.pi / 2, max(low, high))
        angle = rng.uniform(low, high)
        px = SPLASH_PX_BASE * max(1.0, self._R_inner / 140.0)
        size = tuple(round(k * px) for k in rng.choice(SPLASH_SIZE_MIX))
        gd = rng.uniform(SPLASH_GRAV_LO, SPLASH_GRAV_HI)
        if self._splash_cap and self._sn >= self._splash_cap:
            self._splash_stats["pool_full"] += 1
            return -1
        profile = self._mound_profile
        if profile is None or v_impact <= 0.0:
            return -1
        height, free, thick = profile.column(x - self._cx, self._mound_apex())
        if not free and thick > 0.0:
            return -1
        y_surface = self._lower_sand_bot + height
        diameter = self._contact_flow_diameter()
        if abs(x - self._cx) >= 0.1 * diameter:
            side = 1.0 if x > self._cx else -1.0
        # 视觉散粒代表持续撞击, 力度以首次实测撞击为基准, 前后最多温和变化 10%。
        reference = self._splash_reference_speed or float(v_impact)
        strength = reference * (0.90 + 0.10 * min(1.0, v_impact / reference))
        speed = strength * gain * self._splash_speed_scale
        vx, vy = side * math.sin(angle) * speed, abs(math.cos(angle)) * speed
        scale = self._particle_motion_scale
        gravity = 450.0 * (scale * scale if SPLASH_G_SCALE >= 1.0
                           else scale ** SPLASH_G_SCALE) * gd
        roof = self._lower_sand_bot + profile.bounds(x - self._cx)[1]
        # 细柱贴底, 粗柱保持旧版可见起点; 过渡只依赖有效内宽, 不依赖剩余沙量。
        half_height = size[1] * 0.5
        wanted_lift = half_height + (SPLASH_LIFT_PX - half_height) * self._splash_origin_blend
        lift = min(max(0.0, wanted_lift), max(0.0, roof - y_surface - half_height))
        clearance = max(0.0, roof - y_surface - lift - size[1] * 0.5)
        vy = min(vy, math.sqrt(2.0 * gravity * clearance))
        i = self._s_append(x, y_surface + lift, vx, vy, size[0] * 0.5, size[1] * 0.5, gd)
        self._splash_stats["born_air"] += 1
        return i

    def _splash_jet_bounds(self):
        if self.pn <= 0:
            return None
        cached = getattr(self, "_splash_jet_cache", None)
        if cached is not None:
            return cached
        radius = self._taper["t_in"] * FLOW_SHRINK_MIN + 2.0
        front = float(min(self.py[:self.pn])) if _np is None else float(_np.min(self.py[:self.pn]))
        low = max(front, self.get_mound_top_y() + max(4.0, radius * 0.35))
        high = 2.0 * self._neck_y - self._taper["y_bot"]
        self._splash_jet_cache = radius, low, high
        return self._splash_jet_cache

    def _splash_jet_rows(self):
        cached = getattr(self, "_splash_rows_cache", None)
        if cached is not None:
            return cached
        if self.pn <= 0:
            return None
        low = float(min(self.py[:self.pn]))
        high = 2.0 * self._neck_y - self._taper["y_bot"]
        inv = 32.0 / max(1e-6, high - low)
        if _np is None:
            left, right = [float("inf")] * 32, [float("-inf")] * 32
            for i in range(self.pn):
                row = min(31, max(0, int((self.py[i] - low) * inv)))
                half = 0.5 * self.psz[i]
                left[row] = min(left[row], self.px[i] - half)
                right[row] = max(right[row], self.px[i] + half)
        else:
            left, right = _np.full(32, _np.inf), _np.full(32, -_np.inf)
            row = _np.clip(((self.py[:self.pn] - low) * inv).astype(_np.intp), 0, 31)
            half = self.psz[:self.pn] * 0.5
            _np.minimum.at(left, row, self.px[:self.pn] - half)
            _np.maximum.at(right, row, self.px[:self.pn] + half)
        self._splash_rows_cache = low, high, inv, left, right
        return self._splash_rows_cache

    def _splash_uniform_half(self):
        """兼容取证接口; 当前绘制直接读取实际半尺寸数组。"""
        return None

    def _update_splashes(self, dt, g_splash, g_abs, ctab, ctab_n, ctab_r, ctab_inv,
                         mound_bot, lower_center, lower_bot, lower_top,
                         cx, Ri2, profile, apex):
        """v1.244 抛物线和表面滑动; 标量路径使用同一接触表和参数。"""
        if _np is None:
            self._update_splashes_scalar(dt, g_splash, g_abs, ctab, ctab_n, ctab_r,
                                         ctab_inv, mound_bot, lower_center, lower_bot,
                                         lower_top, cx, Ri2, profile, apex)
            return
        self._step_contact_effects(ctab, ctab_r, ctab_inv, mound_bot, lower_center,
                                   -g_splash, scalar=False)

    def _update_splashes_scalar(self, dt, g_splash, g_abs, ctab, ctab_n, ctab_r,
                                ctab_inv, mound_bot, lower_center, lower_bot,
                                lower_top, cx, Ri2, profile, apex):
        """无 NumPy 时的同模型兜底。"""
        self._step_contact_effects(ctab, ctab_r, ctab_inv, mound_bot, lower_center,
                                   -g_splash, scalar=True)

    def _step_contact_effects(self, table, radius, inv, bottom, center, gravity, scalar):
        n = self._sn
        if n == 0:
            return
        radius = self._R_inner if radius is None else radius
        inv = (CONTACT_TABLE_N - 1) / (2.0 * radius) if inv is None else inv
        from splash_motion import step
        self._splash_limits = (SPLASH_MIN_VX, SPLASH_SLIDE_DAMP, SPLASH_REST_LIFE,
                               SPLASH_STILL_LIFE, SPLASH_SLOPE_GAIN)
        mask = step(self, table, radius, inv, bottom, center, gravity,
                    max(1.0, self._particle_motion_scale),
                    getattr(self, "_splash_pixel", 1.0), self._splash_jet_rows(), scalar)
        keep = [i for i in range(n) if mask[i]] if scalar or _np is None else mask
        count = len(keep) if isinstance(keep, list) else int(_np.count_nonzero(keep))
        if count != n:
            for name in _S_FIELDS:
                arr = getattr(self, name)
                if _np is None:
                    arr[:count] = [arr[i] for i in keep]
                else:
                    arr[:count] = arr[:n][keep]
            self._sn = count

    def _spawn_bg_splashes(self, dt):
        """复用近期真实撞击位置和速度, 不在整个坡上凭空生成效果。"""
        hits = self._contact_hits
        while hits and self._mound_flow_clock - hits[0][0] > 0.05:
            hits.popleft()
        if not hits:
            self._bg_splash_acc = 0.0
            return
        rate = SPLASH_BG_PER_PARTICLE * self.pn if SPLASH_BG_PER_PARTICLE > 0 else SPLASH_BG_RATE
        rate *= self._splash_density
        self._bg_splash_acc += dt * rate
        count = min(24, int(self._bg_splash_acc))
        self._bg_splash_acc = min(self._bg_splash_acc - count, rate / 60.0)
        rng = self._splash_random()
        samples = tuple(hits)
        for _ in range(count):
            _, x, speed = samples[rng.randrange(len(samples))]
            self._eject_splash(x, self._mound_top_at(x), speed)

    def update_particles(self, dt, flow_dt=None):
        if not self._geom_ready:
            return
        self._splash_jet_cache = None
        self._splash_rows_cache = None
        effect_dt = dt
        self._mound_flow_clock += effect_dt
        landed_before = self._sand_landed
        dt = dt if flow_dt is None else flow_dt
        if self.running:
            self._sand_active = True
        cx = self._cx
        mound_top = self.get_mound_top_y()
        now = time.perf_counter()
        neck_w = self.neck_w
        ow = self._ow
        gen_y = 2 * self._neck_y - self._taper['y_bot']
        motion_scale = self._particle_motion_scale
        self._spawn_from = self.pn          # 没走 spawn 分支时也不能留旧值
        _pm = _PROF_MARK                    # 区间打点(默认 None ⇒ 一次局部读 + 一次判空)
        if _pm:
            _pm("phys_setup")               # 帧首 → 各 property / 常量就绪
        # ⚠️ 本帧步长必须在这里铺满, **不能**在帧尾存"上一帧的 dt"。
        #    闸门用 step = min(1/120, target-elapsed), 到采样点附近会产生偏步长;
        #    存上一帧的 dt 会让下一帧的粒子落得更远、提前触底(实测每周期末 2% 分叉)。
        #    语义对齐原来的 `p.pop("_step_dt", dt)`: 老粒子用**本帧** dt, 帧内新生的
        #    粒子随后在 spawn 里覆盖成自己的偏步长。
        if _np is None:
            self.pdt[:self.pn] = [dt] * self.pn   # list 切片不吃标量广播
            self.sdt[:self._sn] = [effect_dt] * self._sn
        else:
            self.pdt[:self.pn] = dt
            # 🔴 **飞溅的 dt 也在这一行铺满**, 且必须**早于本帧任何 `_eject_splash` 调用** ——
            #    与粒子 `pdt` 同一条道理(见下面那段注释): 帧内新生的颗粒随后把自己的
            #    "偏步长"覆盖上去。铺晚了会把刚写进去的偏步长冲掉, 新飞溅第一帧多落一截。
            #    (`_spawn_bg_splashes` 在飞溅循环**之后**追加 ⇒ 它们本帧不被积分,
            #     下一帧自然落进这次铺的 `dt`。)
            self.sdt[:self._sn] = effect_dt

        start, end, _reserve = self._transfer_timing()
        previous = max(0.0, self.elapsed - dt)
        emit_start = max(start, previous)
        emit_end = min(end, self.elapsed)
        emit_dt = max(0.0, emit_end - emit_start)
        final_emission = previous < end <= self.elapsed
        if self.running and (emit_dt > 0.0 or final_emission):
            # 🔴 **2026-10-06 用户一眼看出来: 1 秒档的沙流是"断续虚线", 5s/15s 是"连续的一条绳"。**
            #    根因: `_particle_motion_scale` 把初速与重力乘了倍率(1s 档 6.44)让粒子飞快穿过,
            #    **但生成率没跟着放大** ⇒ 同样多的粒子被摊在 6.4 倍的长度上
            #    ⇒ 线性密度掉到 1/6.4 ⇒ 看起来就是虚线。
            #    实测算过: 5s = 1500/s ÷ 630px/s = **2.38 粒/px**; 1s = 1500 ÷ 4006 = **0.37 粒/px**。
            #    正解 = 让**线性密度与周期无关**(文档里用户自己定过"落沙密度该相同"):
            #    生成率跟着 `motion_scale` 一起放大。1s 档 1500 → ~9500/s,
            #    而在途粒子数 rate×飞行时间 = ~1400, 与 5s 的 ~2300 **同量级**, 不是性能爆炸。
            rate = FLOW_BASE_RATE * self._particle_motion_scale
            # 沙柱先接通出口; 在帧内均匀发射,避免每一帧生出一整排同龄沙粒。
            self.particle_acc += emit_dt * rate
            self._sand_pending += max(
                0.0, self._released_fraction_at(emit_end)
                - self._released_fraction_at(emit_start))
            spawn_count = int(self.particle_acc)
            if final_emission and self._sand_pending > 0.0:
                spawn_count = max(1, math.ceil(self.particle_acc))
            mass = self._sand_pending / spawn_count if spawn_count else 0.0
            self._spawn_from = self.pn
            # 一次把本帧要生的量预留够, 不在循环里反复扩容。
            self._p_grow(self.pn + spawn_count + 2)
            # 🔴 **2026-10-10: 沙快流完时, 下来的沙要**变细、边要毛**。**
            #    用户判词(末段截图): 「当最后消失的时候, 颈部的沙子太规范不合理,
            #    **就是一个矩形啊** …… 是不是也可以更类似沙子一些?」
            #    实测定位: 那一块**主要是粒子**(`pooloff` 消融把框内 **269/360 px** 清掉,
            #    全图差异也正好 269px), 不是那两条 `Rectangle`(`neckrects` 消融 0px)、
            #    也不是沙柱主带(`neckquads` 消融 0px)。
            #    ⇒ 病根是**出生带**: `x_clip = neck_w - ow`(整管宽) + `uniform`(两边齐平)
            #      ⇒ 最后一批沙同一 y、同一速度落下来就是**一块板**。
            #    两层修:
            #      ① `_band`  : 颈内余量越少 ⇒ 出生带越窄(整管宽 → `FLOW_TAIL_NARROW`)
            #      ② `_taper` : 同时把分布从 uniform 收成 `|u|^p`(两端稀、中轴密)
            #         ⇒ 边界不再是刀切, 而是一股**带毛边的细流**。
            #    ⚠️ **不新增随机数** —— `random.uniform` 照调一次, 只对抽到的值做重映射
            #      ⇒ 抽取顺序与次数与旧版逐字相同(`_taper == 1.0` 时**逐位**等于旧行为)。
            #    🔴 **2026-10-10 二次修: `_neck_rem` 原来用 `reserve` 归一, 而 `reserve` 是
            #      颈管**体积**占比(PC 上 ≈ 0.0019) ⇒ 只在最后 **0.02 秒**起效
            #      —— 实测 t=9.59~9.95 逐帧差 **0 像素**, 等于没做。
            #      改成**按剩余时间比例**(`FLOW_TAIL_SPAN` = 收尾段占整个计时的比例)。
            #    用户补充判词:「主要是**随机一些、不规范一些**可能更好。**每次都不一样更好**」
            #      ⇒ 收尾指数**逐颗不同**(用已经抽好的 `amp` 抖动), 不做成一条固定曲线。
            _released_frac = self._released_fraction_at(min(self.elapsed, end))
            _tail = min(1.0, max(0.0, (1.0 - _released_frac) / max(1e-6, FLOW_TAIL_SPAN)))
            _band = FLOW_TAIL_NARROW + (1.0 - FLOW_TAIL_NARROW) * _tail
            x_clip = max(1.0, (neck_w - ow) * _band)
            _taper = 1.0 + (FLOW_TAIL_TAPER - 1.0) * (1.0 - _tail)
            for spawn_index in range(spawn_count):
                self.particle_acc -= 1
                x_off = random.uniform(-x_clip, x_clip)
                vy0 = -random.uniform(35, 60) * motion_scale
                # 后续字段沿用既有抽取顺序; 窄颈时 size 短路, 不抽额外随机数。
                phase = random.uniform(0, math.tau)
                amp = random.uniform(0.4, 1.0)
                if _taper != 1.0:
                    # 逐颗抖动指数(0.7~1.3 倍) ⇒ 收尾不是一条固定曲线, 每次都不一样
                    _p = _taper * (0.7 + 0.6 * amp)
                    _u = x_off / x_clip
                    x_off = x_clip * math.copysign(abs(_u) ** _p, _u)
                is_light = random.random() < 0.10
                size = (2 if random.random() < 0.85 else 1) if x_clip >= 3.0 else 1
                if FLOW_WIDTH_CAP and size > FLOW_WIDTH_CAP:
                    # 🔴 2026-10-10: **只压线宽, 不动随机数流**(上面那次 `random.random()`
                    #    照抽, 只是结果被盖掉) ⇒ 同 seed 下粒子的位置/相位/寿命一字不变。
                    size = FLOW_WIDTH_CAP
                trail_time = random.uniform(0.018, 0.032)
                i = self.pn
                self.px[i] = cx + x_off
                self.pxo[i] = x_off
                # 🔴 **2026-10-10: 出生点沿 y 随机摊开 —— 消掉出口那条"死横线"。**
                #    用户:「表现的随机一些, 有个范围, 而不是一直在某个横线前后变化」。
                #    实测(桌面 1904×2890, t=7.64, 柱心 ±25px 的高通 std):
                #        出口以上 = 0.7~2.5(只有材质面) → 出口 = 3.9 → 再往下 = 9.5~23(粒子)
                #    阶跃正好落在出口 —— 因为**所有粒子都在同一条 y 上出生**
                #    (`gen_y`), 粒子层的上边缘是一条几何直线。
                #    ⚠️ **不能新抽随机数**: 那会平移整条随机数流、把同 seed 的逐像素对照全废掉。
                #    改用**已经抽好的** `phase` 推偏移(均匀分布于一个周期内) —— 逐位等价的老闸门
                #    在 `STREAM_SPREAD_RATIO = 0` 时照样成立。
                self.py[i] = gen_y - phase / math.tau * (STREAM_SPREAD_RATIO * self._tube_h)
                self.pvy[i] = vy0
                self.pwp[i] = phase
                self.pwa[i] = amp
                self.pli[i] = 1.0 if is_light else 0.0
                self.psz[i] = size
                self.ptl[i] = trail_time
                birth = emit_end - max(0.0, self.particle_acc) / rate
                birth = max(emit_start, min(emit_end, birth))
                self.pdt[i] = max(0.0, self.elapsed - birth)
                if final_emission and spawn_index == spawn_count - 1:
                    self.pvy[i] = -35.0 * motion_scale
                if self._sand_resized:
                    left = max(1e-6, self.duration - birth - 1.0 / 120)
                    distance = max(0.0, gen_y - self._mound_top_at(float(self.px[i])))
                    speed = max(0.0, distance / left - 225.0 * motion_scale ** 2 * left)
                    self.pvy[i] = min(self.pvy[i], -speed)
                self.pmass[i] = mass
                self.pn = i + 1
            if spawn_count:
                self._sand_released += self._sand_pending
                self._sand_pending = 0.0
            if final_emission:
                self.particle_acc = 0.0

        if _pm:
            _pm("phys_spawn")               # `pdt/sdt` 铺满 + 本帧生成循环
        g = -450.0 * motion_scale * motion_scale
        g_abs = abs(g)
        source_speed = 60.0 * motion_scale
        source_speed_squared = source_speed * source_speed
        # 🔴 **`twoimpl=3`: 粒子的收缩零点从"下球截口"改到"直筒出口"**(= 柱子那套的零点)。
        #    物理上**出口才是自由落体的起点** —— 粒子本来就是在出口生成的; `_lower_ball_cut`
        #    比出口低 shift px(桌面 9.1 / 平板 31.6)⇒ 现在粒子**晚收**了 shift px。
        #    ⚠️ 标量版与 `flow_numpy` 版**共用这一处取值**(向量版从 consts 读 `lower_cut`)
        #       ⇒ 改这一行同时作用于两条路, 不会造成新旧分叉。
        lower_cut = ((2.0 * self._neck_y - self._taper["y_bot"]) if _TWOIMPL >= 3
                     else self._lower_ball_cut)
        # ★ **收缩饱和阈值**(2026-10-06, 外部评审 §4.2 提出, 我验算过推导):
        #   `target = sqrt(v0 / sqrt(v0² + 2gb))`, 钳到 `m = FLOW_SHRINK_MIN`。
        #   `target <= m  ⟺  v0 / sqrt(v0²+2gb) <= m²  ⟺  sqrt(v0²+2gb) >= v0/m²
        #                ⟺  v0² + 2gb >= v0²/m⁴  ⟺  b >= v0²(m⁻⁴ - 1) / (2g)`
        #   ⇒ 一旦 `below_tube` 越过 `b_sat`, 后面两次 `sqrt` 全是白算(结果恒为 m)。
        #   本帧的 `g_abs` / `source_speed` 是常量 ⇒ `b_sat` 每帧只算一次。
        #   落程越长饱和占比越高(长周期档几乎全程饱和)。
        #   ⚠️ 阈值附近有浮点舍入差 ⇒ 在 `b_sat` 附近留一小段**回退原式**的缓冲带,
        #      并且必须用 `tools/_probe_shrink_equiv.py` 做逐点数值对照, 不许口头说"等价"。
        _m4 = FLOW_SHRINK_MIN ** -4.0
        b_sat = (source_speed * source_speed * (_m4 - 1.0) / (2.0 * g_abs)
                 if g_abs > 0.0 and 0.0 < FLOW_SHRINK_MIN < 1.0 else -1.0)
        SAT_GUARD = 1.0        # 缓冲带(px): [b_sat - SAT_GUARD, b_sat) 仍走原式
        lower_top = self._lower_sand_top
        lower_center = self._lower_y_c
        sand_half_w = self._sand_half_w
        tube_lim = max(1.0, neck_w - ow)
        new_list = []
        append_particle = new_list.append
        append_flare = self.flares.append
        lower_bot = self._lower_sand_bot
        Ri2 = self._R_inner * self._R_inner      # _sand_half_w 内联用(值与原来一致)
        peak_offset = self.mound_peak_offset
        rand = random.random
        sin = math.sin
        sqrt = math.sqrt
        mound_top_plus_1 = mound_top + 1
        _cx_arr, _cy_arr, _c_x0, _c_scale, _c_n1 = self._mound_contact_curve()
        _use_curve = len(_cx_arr) > 1
        if _pm:
            _pm("phys_curve")               # 接触曲线(129 次 `contact`) + 步长常量
        if _flow_numpy is not None and self.pn >= _NUMPY_MIN:
            # numpy 路线: 纯算术向量化(逐位等价由 tools/test_physics_equiv.py 验收),
            # 随机数仍留在 Python, 命中事件按下标升序回放。
            pn = self.pn
            if pn:
                consts = {
                    "g": g, "g_abs": g_abs, "mound_top": mound_top,
                    "shrink_ramp": FLOW_SHRINK_RAMP,
                    "gen_y": gen_y, "lower_cut": lower_cut,
                    "lower_top": lower_top, "lower_center": lower_center,
                    "tube_lim": tube_lim, "Ri2": Ri2, "lower_bot": lower_bot,
                    "source_speed": source_speed,
                    "shrink_min": FLOW_SHRINK_MIN,
                    # ★ 把**饱和阈值**也交给向量化路径(2026-10-07)。标量路径早就在
                    #   `below_tube >= b_sat + SAT_GUARD` 时短路成 `FLOW_SHRINK_MIN`
                    #   (省两次 sqrt), 而向量化路径**一直在对全部粒子算两次 `np.power`**
                    #   —— 两条路径对同一件事不一致, 而且 15s 稳态实测 **99.9% 的粒子已在
                    #   饱和区** ⇒ 那两次 power 几乎全是白算(`np.power` 比逐元素加乘贵一个量级)。
                    "shrink_sat": b_sat + SAT_GUARD,
                    "source_speed_squared": source_speed_squared,
                    "cx": cx, "peak_offset": peak_offset,
                    "curve": (_cx_arr, _cy_arr, _c_x0, _c_scale, _c_n1),
                }
                hit_idx, hit_dt, peak_offset = _flow_numpy.step(
                    self.px, self.py, self.pvy, self.pxo, self.pwp, self.pwa,
                    self.psz, self.pdt, pn, consts)
                if _pm:
                    _pm("phys_step")
                nhit = len(hit_idx)
                if nhit:
                    self._replay_hits(hit_idx, hit_dt, mound_top,
                                      motion_scale, now)
                    if _pm:
                        _pm("phys_replay")
                    keep = _np.ones(pn, dtype=bool)
                    keep[hit_idx] = False
                    newpn = pn - nhit
                    for _name in _P_FIELDS:
                        _arr = getattr(self, _name)
                        _arr[:newpn] = _arr[:pn][keep]
                    self.pn = newpn
                    if _pm:
                        _pm("phys_compact")
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
                    self._sand_landed += p.get("_sand_mass", 0.0)
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
                    if below_tube >= b_sat + SAT_GUARD:
                        # 已在饱和区: 原式恒等于 FLOW_SHRINK_MIN ⇒ 两次 sqrt 全省。
                        target = FLOW_SHRINK_MIN
                    else:
                        # ★ `** 0.5` → `sqrt`: 两处操作数都**非负** ⇒ 逐字等价,
                        #   而 CPython 里 `**0.5` 走 `pow`、明显慢于 `math.sqrt`。
                        v_at_y = sqrt(source_speed_squared + 2 * g_abs * below_tube)
                        target = sqrt(source_speed / v_at_y)
                        if target <= FLOW_SHRINK_MIN:
                            target = FLOW_SHRINK_MIN
                    # 平滑过渡区长度(px)
                    if below_tube < FLOW_SHRINK_RAMP:
                        shrink = 1.0 + (target - 1.0) * (below_tube / FLOW_SHRINK_RAMP)
                    else:
                        shrink = target
                x = cx + x_offset * shrink + sin(fallen_dist * 0.07 + wobble_phase) \
                    * wobble_amp * (1 - shrink * 0.4)

                # 横向 clamp: 管内壁 / 进下球随球内壁过渡
                if y >= lower_top:
                    lim = tube_lim
                else:
                    dy = y - lower_center
                    r = Ri2 - dy * dy
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
                    first_contact = self._record_contact(x, -vy)
                    if mound_top > lower_bot + 1:
                        peak_offset = peak_offset * 0.97 + (x - cx) * 0.03
                    # ⚠️ **出生点用该颗粒自己的接触高度 hy**(dingbu.md §7.1 第 6 条:
                    #    "闪光、弹跳、滑落起点全部使用该颗粒的接触 y, 不再统一放到一条水平线上")。
                    #    原来放在标量 mound_top 上 —— 审计实测出生点比该颗粒**当地沙面**
                    #    高 p50=7.5px / p90=12.6px / max=15.4px(92% 的命中 >1px)。
                    if rand() < 0.25 * self._splash_density:
                        append_flare({"x": x, "y": hy, "end": now + 0.08})
                    selected = rand() < SPLASH_HIT_CHANCE * self._splash_density
                    if selected or first_contact:
                        # ★ 唯一的飞溅模型(与 numpy 路径同一个函数, 随机数顺序一致)
                        step_left = step_dt - hit_dt
                        sp = self._eject_splash(x, hy, -vy, surface_aligned=True)
                        if sp >= 0:                  # -1 = 撞上存活上限
                            self.sdt[sp] = step_left if step_left > 0 else 0
                        if first_contact:
                            for _ in range(self._first_contact_extra(rand)):
                                sp = self._eject_splash(x, hy, -vy)
                                if sp >= 0:
                                    self.sdt[sp] = max(0.0, step_left)
                    continue
                p["y"] = y
                p["vy"] = vy
                p["x"] = x
                append_particle(p)
            self._p_from_dicts(new_list)
        self.mound_peak_offset = peak_offset
        if abs(self._sand_landed - 1.0) < 1e-12:
            self._sand_landed = 1.0
        if self._sand_landed != getattr(self, "_landed_for_geometry", None):
            self._landed_for_geometry = self._sand_landed
            self._mound_shape_cache = self._mound_curve_cache = None
        # 本帧物理到此结束, 把数组摊成渲染层读的 list 快照(顺带让 dict 缓存失效)。
        self._p_refresh_view()
        self._splash_jet_cache = None  # 落地压实后重新取流前沿, 后续飞溅共用。
        self._splash_rows_cache = None
        if self._sand_landed > landed_before:
            if self._first_impact_clock is None:
                self._first_impact_clock = self._mound_flow_clock
            self._last_impact_clock = self._mound_flow_clock
        dt = effect_dt

        if _pm:
            _pm("phys_main")
        # 平台之外只有 splash/尘埃会落 ⇒ 它们按**接触高度 H(x)** 判定(与绘制同一份定义,
        # 专家 §2.4); 主流落在平台上, 继续用标量 mound_top, 热循环一个字不改(路线 A)。
        _mound_bot = self._lower_sand_bot
        _profile = self._mound_profile
        _apex = self._mound_apex()
        # ⚠️ **splash 的重力是否跟着 `motion_scale²` 走 —— 这是可怀疑项(2026-10-06, 1号专家)**。
        #    粒子需要 `motion_scale` 才能在极短周期里飞完那段路; 但 splash 是**沙堆上的装饰**,
        #    理论上该与周期无关。现状是它直接吃粒子的 `g` ⇒ 1s 档重力被放大 `3²=9` 倍。
        #    1号实测: 在途寿命 1s **0.032s** vs 15s **0.202s**(6.3×), 而"沙堆没成形"那条
        #    归因已被他证伪(判别性实验: 几何与分母都对齐后仍差 ~10×)。
        #    `HG_SPLASH_G=0` ⇒ 用不缩放的 g(对照组); 默认 1.0 = 现状(逐位不变)。
        g_splash = g if SPLASH_G_SCALE >= 1.0 else -450.0 * (motion_scale ** SPLASH_G_SCALE)
        # 🔴 **每帧一张"沙堆接触高度"查找表**(2026-10-06 性能)。
        #   原来**每颗下落中的飞溅、每帧**都现算一次 `contact()` —— 那是
        #   `sqrt` + 65 节点线性插值 + 两次 clamp; 峰值实测 **~2200 次/帧**,
        #   cProfile 里这一族占 **6.3ms/帧**(桌面, 飞溅 1939), 是整帧最大的一块纯 Python。
        #   而 `apex` 一帧只有一个值 ⇒ **整条接触曲线每帧只该算一次**。
        #   读表用**线性插值**(不是最近邻: 最近邻在设备上会有 ~3px 的台阶, 粒子会浮/陷)。
        #   ⚠️ 定义仍然只有 `_MoundProfile.contact` 一份 —— 建表就是调它, 没有第二套公式。
        _ctab = _ctab_r = _ctab_inv = None
        _ctab_n = 0
        if _pm:
            _pm("ctab_before")
        if _profile is not None and _apex > 0.0:
            _ctab_n = CONTACT_TABLE_N
            _ctab_r = _profile.radius
            _ctab_inv = (_ctab_n - 1) / (2.0 * _ctab_r)
            _ctab = self._contact_table
            if len(_ctab) != _ctab_n:
                _ctab = self._contact_table = [0.0] * _ctab_n
            # 保序内联版(`_MoundProfile.fill_contact_table`) —— 与逐点调 `contact()`
            # 逐位等价, 设备实测 0.40ms → 0.18ms。守卫见该方法自己的 docstring。
            _profile.fill_contact_table(_ctab, -_ctab_r, _ctab_inv, _apex)
        if _pm:
            _pm("ctab")
        # 飞溅积分: **并行数组 + numpy 掩码**(原来那段逐颗的 Python 循环见 git 历史)。
        # 语义逐句等价 —— 由 `tools/_splash_golden.py` 逐帧逐字段兜底。
        self._update_splashes(dt, g_splash, g_abs, _ctab, _ctab_n, _ctab_r, _ctab_inv,
                              _mound_bot, lower_center, lower_bot, lower_top,
                              cx, Ri2, _profile, _apex)
        if _pm:
            _pm("splash_loop")
        if _pm:
            _pm("phys_splash")
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

    def _neck_half_width(self, y):
        """高度 `y` 处的**颈部(两球之间那一段)玻璃外轮廓半宽**; `y` 不在这段里返回 None。

        ⚠️ 2026-10-05（r19-2号 设备实测 + `tools/_probe_tap_deadzone.py` 桌面复现）:
           原来 `on_touch_down` 只认"落在上下两个**球**内", 而两球不相交
           ⇒ **两圆之间那一段没有任何一点满足条件** = 死区。
           设备实测: 空闲态点 (540,1250) 按钮不变色、运行中点它也不暂停;
           逐点扫描 y≈1198~1305(约 108px), 整行从左到右全灭。
           桌面复现: 631px 高的 widget 上死带 36px(5.7%), 与设备的 108/2200≈4.9% 同量级。
           **而沙漏正中间正是用户最自然会点的地方**（我自己给评审写坐标表时都写成了 (540,1200)）。
        """
        pts = self._taper.get("out_pts") if self._taper else None
        if not pts:
            return None
        y_top = max(p[1] for p in pts)
        y_knee = self._taper["y_bot"]                  # 上喇叭口的下端 = 直筒上端
        y_low = 2.0 * self._neck_y - y_knee            # 直筒下端(出口)
        # ⚠️ 出口(≈370)比**下球上缘**(≈366)还高几像素, 那一小段也得算进来,
        #    否则颈部死带会剩最后 8px(实测 36→28→8)。取两者更低的那个当下界。
        y_low = min(y_low, self._lower_y_c + self._R)
        # ⚠️ **还要再往下够到下喇叭口的底**(2026-10-06, r28-2号 设备实测 + 本探针):
        #    只够到"下球上缘"是不够的 —— **下喇叭口的下半段整个落在球顶上缘以下**,
        #    那一段于是退回**球圆判定**, 而球在极点附近远窄于画出来的喇叭口。
        #    实测死缝 **22.8px(50s) / 24.7(600s) / 29.5(60000s)**, 纵向 5~6 行,
        #    中心列可点、左右对称(上喇叭口对照恒为 0.0 ⇒ 只有下喇叭口有问题)。
        #    ⚠️ 第一版探针**遇到 `_neck_half_width` 返回 None 就 `continue`**,
        #    恰好把这一段跳过 ⇒ 量出"死缝 0.0", 与设备上"那一点仍点不动"直接矛盾。
        #    **判据自己把被测对象排除了 —— 这是今天第三次同类错**(见 QA_RULES)。
        y_low = min(y_low, 2.0 * self._neck_y - y_top)
        if not (y_low - 1e-6 <= y <= y_top + 1e-6):
            return None
        # ⚠️ **整条颈部轮廓是关于 `neck_y` 对称的**(下喇叭口 = 上喇叭口镜像,
        #    见 `_build_glass_shell` 里的 `mir = 2 * self._neck_y`) ⇒ 命中判定也该对称。
        #    不折的话, 下面那个"等宽直筒"分支会把**整条下喇叭口**也当成直筒
        #    (而它向下逐渐变宽) ⇒ 肩部留下一条**点不动的玻璃**
        #    (r28-2号 2026-10-06 设备实测: 50s 档 ≈6px 高, 60000s 档 ≈33px;
        #     桌面 `tools/_probe_flare_hitgap.py` 量到最宽 8.8→17.0px,
        #     而**上喇叭口对照恒为 0.0** —— 同一把尺子在上半边量不到东西)。
        #    折到上半边后两半走**同一套插值**, 与画出来的玻璃逐行一致。
        y = max(y, 2.0 * self._neck_y - y)
        if y <= y_knee + 1e-6:
            # ⚠️ 直筒段是**等宽**的, 不在 `out_pts` 里 —— 第一版只查了 out_pts,
            #    于是死带只修掉 8px(实测 36→28px), 剩下一整段直筒仍是死的。
            return self._taper["t_out"]
        # out_pts 是 (半宽, y) 的顺序表; 不假定升序还是降序, 逐段找包含 y 的那一段
        for i in range(len(pts) - 1):
            (w0, a), (w1, b) = pts[i], pts[i + 1]
            lo, hi = (a, b) if a <= b else (b, a)
            if lo - 1e-9 <= y <= hi + 1e-9:
                if abs(b - a) < 1e-9:
                    return max(w0, w1)
                t = (y - a) / (b - a)
                return w0 + (w1 - w0) * t
        return pts[-1][0]

    def on_touch_down(self, touch):
        if self._geom_ready and self.collide_point(*touch.pos):
            dx = touch.x - self._cx
            if (dx * dx + (touch.y - self._upper_y_c) ** 2 <= self._R ** 2 or
                    dx * dx + (touch.y - self._lower_y_c) ** 2 <= self._R ** 2):
                app = App.get_running_app()
                if app is not None:
                    app.on_toggle()
                return True
            # 两球之间的**颈部**也要能点(死区修复, 见 `_neck_half_width` 的注释)。
            # 横向仍跟着**玻璃轮廓**走 —— 不是把整幅矩形当热区, 沙漏之外的空白照旧没反应。
            half = self._neck_half_width(touch.y)
            if half is not None and abs(dx) <= half + self._ow:
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

            # ⚠️ 这里原来是"沿曲线逐段画 `Quad`"(Kivy 无多边形图元) —— 一共 **192 条**,
            #    每条还各拖一条 `BindTexture`。换成 `_QuadBand`(一个 `Mesh`)之后整族消失,
            #    顶点一个字节没变(逐像素等价见 `_qa/quadmesh_ab.py`)。这段是**静态**的,
            #    只在几何变化时重建, 所以省下的是**每帧的指令遍历**, 不是构建时间。
            def _put(band, i, pts, sx=None):
                """把一条曲线折线连续写成四边形, 返回下一个空槽位下标。"""
                for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                    if sx is None:
                        band.set(i, [cx - x0, y0, cx + x0, y0,
                                     cx + x1, y1, cx - x1, y1])
                    else:
                        band.set(i, [cx + sx * x0, y0, cx + sx * (R + pad), y0,
                                     cx + sx * (R + pad), y1, cx + sx * x1, y1])
                    i += 1
                return i

            n_out = max(0, len(tp['out_pts']) - 1)
            n_in = max(0, len(tp['in_pts']) - 1)
            # ① 擦掉曲线以外的球极冠(用背景色覆盖): 2 个 flip × 2 个 sgn
            bg = hex_rgb(BG_COLOR)
            Color(*bg)
            cap = _QuadBand(4 * n_out)
            i = 0
            for flip in (False, True):
                op = _pts('out_pts', flip)
                for sgn in (1, -1):
                    i = _put(cap, i, op, sx=sgn)
            cap.flush()
            # ② 直筒段
            Color(*glass_out)
            Rectangle(pos=(cx - t_out, yb_l), size=(2 * t_out, yb_u - yb_l))
            Color(*glass_fill)
            Rectangle(pos=(cx - t_in, yb_l), size=(2 * t_in, yb_u - yb_l))
            # ③ 曲线过渡: 外轮廓(壁) + 内轮廓(腔)
            #    (原来 `_band()` 每调一次都插一条 `Color`, 两个 flip 同色 ⇒ 合成一条色 + 一条带)
            Color(*glass_out)
            wall = _QuadBand(2 * n_out)
            i = 0
            for flip in (False, True):
                i = _put(wall, i, _pts('out_pts', flip))
            wall.flush()
            Color(*glass_fill)
            cavity = _QuadBand(2 * n_in)
            i = 0
            for flip in (False, True):
                i = _put(cavity, i, _pts('in_pts', flip))
            cavity.flush()

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
        self._neck_quads.set_texture(tex)
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
        fixed = self._mound_knots_fixed()
        if apex is None:
            return list(fixed)
        out = None
        for dx in self._mound_wall_cross(apex):
            if not (-Ri < dx < Ri):
                continue
            if out is None:
                out = list(fixed)
            # ⚠️ 原实现走的是 **set** ⇒ 重复的 dx 会被并掉。这里必须**先查重再插入** ——
            #    插进重复值会让 `cols` 里出现零长线段, carve/band 的 Quad 退化。
            # ★ **一次二分代替"线性查重 + insort 再二分"**(2026-10-07 性能): 原来
            #   `dx not in out` 是**逐元素 Python 扫描**(~200 项 × 至多 4 个交点 ≈ 800 次
            #   比较/帧)。`out` 本来就是升序的, 二分一次就能同时回答"在不在"和"插哪儿"。
            i = _bisect_left(out, dx)
            if i >= len(out) or out[i] != dx:
                out.insert(i, dx)
        return out if out is not None else list(fixed)

    def _mound_knots_fixed(self):
        """`apex` **无关**的那部分节点(几何固定) —— 每个几何代只建一次。

        原来每帧都现建一个 ~113 元素的 `set` 再 `sorted()` —— 而其中 65 个控制点、
        46 个 θ 均匀采样点、±R、0 全都只跟几何有关, **只有 ≤2 个壁交点是逐帧变的**。
        按 `_geom_generation` 缓存(`_R_inner`/`_mound_shape` 变时代也变)。
        ⚠️ 顺序必须与原来 `sorted(set(...))` **完全一致** —— 节点顺序决定 carve/band
        的 Quad 顺序, 换了顺序画面就变(值相同也不行)。
        """
        cache = self._knots_fixed
        gen = self._geom_generation
        if cache is not None and cache[0] == gen:
            return cache[1]
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
        val = tuple(sorted(xs))
        self._knots_fixed = (gen, val)
        return val

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

        d = UPPER_FUNNEL_DEPTH · D · 起步/收尾包络 · (0.75 + 0.25·汇流强度)
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
        from_mouth = max(0.0, self._upper_sand_bot + height
                         - self._taper["in_pts"][0][1]) / D
        sink = 1.0 / (1.0 + 8.0 * from_mouth * from_mouth)
        d = UPPER_FUNNEL_DEPTH * D * s1 * s2 * (0.75 + 0.25 * sink)
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

    def _upper_rough_at(self, index, height, arr=None):
        """上球第 index 个节点的粗糙偏移(已乘"随沙量出现/收敛"的包络)。

        包络按评审 §4.3: `e(q) = smoothstep(0,0.015,q) × [1-smoothstep(0.97,1,q)]`,
        q 用"平顶等效高度占球高之比"代理(与目标面积占比单调同向)。
        ⇒ 满球(无自由面)和空球两端自动收敛成平面, 少量沙不会先长出几根尖刺。

        ★ **`arr` 可选传入**(2026-10-06 性能): 本帧的粗糙数组对**所有调用方都是同一份**
        (见 `_upper_rough_now` 的缓存), 而在**循环里**逐点调用时每点都要重走一遍
        `getattr(...) == t` 的缓存判定。峰值实测 `_upper_rough_at` **940 次/帧**,
        其中大头是面积求解 `_upper_area` 的 65 点循环 × 求解器每帧 8~12 次迭代。
        调用方在循环外取一次 `arr` 传入; 绘制与面积求解共用同一份近壁衰减。
        """
        if arr is None:
            arr = self._upper_rough_now()
        if not arr or index >= len(arr):
            return 0.0
        # 🔴 **包络每帧只算一次**(2026-10-06 性能)。`env` 只跟 `height` 走, 而一帧里
        #    `height` 是同一个值 —— 原来却按**每个节点**各算一遍(实测 `_draw_upper_shape`
        #    940 次/帧 ⇒ 1880 次 `_smoothstep`/帧)。cProfile: `_upper_rough_at` + `_smoothstep`
        #    合计 **2.0ms/帧**(桌面), 全是重复劳动。
        if height != self._upper_env_h:
            Ri = self._R_inner
            q = 0.0 if Ri <= 0 else min(1.0, max(0.0, height / (2.0 * Ri)))
            self._upper_env = (_smoothstep(0.0, 0.015, q)
                               * (1.0 - _smoothstep(0.97, 1.0, q)))
            self._upper_env_h = height
        env = self._upper_env
        return arr[index] * env * self._upper_wall_weights[index] if env > 0.0 else 0.0

    def _upper_rough_crest(self, height):
        """`rough` 在整个数组上的**最大正值**(含包络) —— 沙体矩形顶要盖住它。

        用途见 `redraw()` 里 `up_draw` 的说明: 画出来的沙面是 `level − drop + rough`,
        矩形顶**不含 rough** ⇒ 正的 rough 会把沙面线和它的亮带顶到矩形外面。
        取 `max(arr)` 而不是"弦内节点最大": 弦外那些节点本来就不参与绘制, 多算一点
        只会让矩形略高, 而矩形高于沙面的部分由 carve 抠成玻璃 ⇒ **不可见、零风险**,
        换来的是不必在这里重算一遍弦宽(少一处和 `_draw_upper_shape` 走样的机会)。
        """
        arr = self._upper_rough_now()
        if not arr:
            return 0.0
        Ri = self._R_inner
        if Ri <= 0:
            return 0.0
        q = min(1.0, max(0.0, height / (2.0 * Ri)))
        env = _smoothstep(0.0, 0.015, q) * (1.0 - _smoothstep(0.97, 1.0, q))
        if env <= 0.0:
            return 0.0
        return max(0.0, max(arr) * env)

    def _upper_area(self, level, d, b):
        """上球旋转体近似容量及高度导数; 与下球使用相同的径向权重口径。
        """
        Ri = self._R_inner
        n = MOUND_SHAPE_NODES - 1
        w = 2.0 * Ri / n
        area = deriv = 0.0
        rough = self._upper_rough_now()      # ← 循环外取一次(见 `_upper_rough_at` 的 `arr` 说明)
        # `_upper_rough_at` 的包络 —— `level` 在一次调用内不变 ⇒ 每调用只算一次(同式)
        if Ri > 0.0:
            _q = min(1.0, max(0.0, level / (2.0 * Ri)))
            env = _smoothstep(0.0, 0.015, _q) * (1.0 - _smoothstep(0.97, 1.0, _q))
        else:
            env = 0.0
        # 🔴 **整个循环内联**(2026-10-07 性能)。这是一个**牛顿迭代的内核**:
        #   `_upper_solve_level` 每帧调它 ~6 次、每次 65 个节点 ⇒ 每帧 ~390 次节点迭代,
        #   而原来**每个节点要两次函数调用**(`_upper_surface_drop` + `_upper_rough_at`)
        #   ⇒ ~780 次/帧。设备实测 `_upper_level_for` **一次求解就要 0.39ms**, 全在这。
        #   内联 + 把只跟几何有关的量(`dx`/`floor`/`roof`, 含一个 `sqrt`)预先算好。
        cols = self._upper_cols                 # 布局缓存(见 `_rebuild_height_table`)
        if cols is None or len(cols) != n + 1:
            cols = self._upper_cols = [None] * (n + 1)
        for i in range(n + 1):
            c = cols[i]
            if c is None:
                dx = -Ri + w * i
                fl = Ri - math.sqrt(max(0.0, Ri * Ri - dx * dx))
                left, right = max(-Ri, dx - 0.5 * w), min(Ri, dx + 0.5 * w)
                weight = 0.5 * (right * abs(right) - left * abs(left))
                c = cols[i] = (dx, fl, 2.0 * Ri - fl, weight)
            dx, floor, roof, weight = c
            # `_upper_surface_drop` 内联(与那一份定义逐字相同)
            if d > 0.0 and b > 1e-6:
                u = 1.0 - (dx / b) ** 2
                drop = d * u if u > 0.0 else 0.0
            else:
                drop = 0.0
            y = level - drop
            if rough and i < len(rough):
                y += rough[i] * env * self._upper_wall_weights[i]
            if y <= floor:
                continue
            if y >= roof:
                area += (roof - floor) * weight
            else:
                area += (y - floor) * weight
                deriv += weight
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
        # ★ **一帧内只解一次**(2026-10-07 性能)。这是个**牛顿/二分求解**
        #   (`_upper_solve_level` 里反复调 65 点的 `_upper_area`), 而**每帧有三个调用方**:
        #   `redraw`(up_draw 的护栏)、`_draw_upper_shape`(画沙面)、
        #   `_draw_surface_markers`(表层标记定位) —— 三个传进来的 `upper_height`
        #   **逐位相同**(都来自本帧的 `_upper_sand_height_px()`), `elapsed` 一帧内也不变
        #   ⇒ 后两次是**纯白算**。memo 键用 `(elapsed, upper_height, duration)`,
        #   三个量任何一个变了都必须重解(暂停/重置/改周期都会变)。
        _key = (self.elapsed, upper_height, self.duration)
        if getattr(self, "_upper_level_key", None) == _key:
            return self._upper_level_val
        p = 1.0 - self._upper_sand_fraction()
        d, b = self._upper_funnel_params(p, upper_height)
        if d <= 0.0 or upper_height <= 0.0:
            val = upper_height
        else:
            target, _ = self._upper_area(upper_height, 0.0, 0.0)     # 平顶等效面积
            val = self._upper_solve_level(target, d, b, 0.0,
                                          upper_height + d + 1.0)
        self._upper_level_key = _key
        self._upper_level_val = val
        return val

    def _upper_surface_drop(self, dx, d, b):
        """上球沙面在 dx 处的**下陷量**(≥0, px): `d · [max(0,1-(dx/b)²)]`。

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
            # ★ **坡脚每帧只解两次**(2026-10-06 性能): `foot` 只跟 `side`(±1) 与 `apex` 走,
            #   而 12 颗标记左右各 6 颗 —— 原来**每颗各做一次 14 步二分**, 每步调
            #   `prof.raw()` + `prof.bounds()` ⇒ **336 次调用/帧** 全在算同一个数。
            #   设备实测这段占 `_draw_surface_markers` 的绝大部分(该方法 **1.08ms/帧**,
            #   而它只画 8+12=20 根短线, 逐颗成本 ~54µs —— 见 low1.md §6-C)。
            foot_of = {}
            for _side in (-1.0, 1.0):
                _lo, _hi = 1.0, Ri
                for _ in range(14):                   # 找接触面与球底相交处 = 坡脚
                    _mid = 0.5 * (_lo + _hi)
                    if prof.raw(_side * _mid, apex) - prof.bounds(_side * _mid)[0] > 0.0:
                        _lo = _mid
                    else:
                        _hi = _mid
                foot_of[_side] = 0.5 * (_lo + _hi)
            for k in range(SURFACE_MARKERS_LOW):
                side = -1.0 if k < per_side else 1.0
                jit = ((k * 0.6180339887) % 1.0 - 0.5) * SURFACE_MARKER_JITTER
                frac = ((t / life) + (k % per_side) / max(1.0, per_side) + jit) % 1.0
                # 沿接触面从近顶滑向坡脚: 用"接触高度随 |dx| 下降到球底"定终点
                foot = foot_of[side]
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
        if carve is None:                    # 画布还没建(见 `__init__` 的哨兵值)
            return
        if upper_height <= 0.0:
            carve.clear()
            band.clear()
            carve.flush()
            band.flush()
            self._upper_band_color.a = 0.0
            return
        p = 1.0 - self._upper_sand_fraction()
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
        # 🔴 **两端必须补到精确的弦端点**(2026-10-06 用户实测: 「跟容器壁交接的那个地方
        #   经常有**粘滞现象**, 导致有一些**奇怪的形状**」)。
        #   列是**固定网格**采样的(间距 `step = 2Ri/n`), 弦外的直接 `continue` 跳过
        #   ⇒ **最后一列往往差好几像素才到玻璃壁**, carve 与亮带就在那儿**断掉**
        #   ⇒ 看起来就是"沙面粘在壁上、留一个台阶/小翘角"。
        #   实测缺口: 上沙高 234/184/105px 时分别是 **4.3 / 5.7 / 5.6 px**(与 `Ri` 无关, 与 step 同量级)。
        #   补上精确端点后沙面**一路画到壁**; 端点复用相邻节点的粗糙值(它本来就随空间慢变)。
        xs = []
        for i in range(n + 1):
            dx = -Ri + step * i
            if abs(dx) <= half_chord:     # 弦外: 由 stencil 裁, 不出四边形
                xs.append((dx, i))
        if xs:
            if xs[0][0] > -half_chord + 1e-9:
                xs.insert(0, (-half_chord, xs[0][1]))
            if xs[-1][0] < half_chord - 1e-9:
                xs.append((half_chord, xs[-1][1]))
        cols = []
        for dx, i in xs:
            # ⚠️ 必须与 `_upper_area` **同一套算式**(含微粗糙), 否则解出来的面积 != 画出来的面积
            cols.append((cx + dx, level - self._upper_surface_drop(dx, d, b)
                         + self._upper_rough_at(i, upper_height)))
        limit = top - 1e-6
        for i in range(len(carve)):
            if i + 1 < len(cols):
                x0, y0 = cols[i]
                x1, y1 = cols[i + 1]
                carve.set(i, [x0, y0, x1, y1, x1, limit, x0, limit])
                band.set(i, [x0, y0, x1, y1, x1, y1 - band_w, x0, y0 - band_w])
            else:
                carve.zero(i)
                if i < len(band):
                    band.zero(i)
        carve.flush()
        band.flush()

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
        flow = getattr(self, "_mound_surface_flow", None)
        if carve is None:                    # 画布还没建(见 `__init__` 的哨兵值)
            return
        if h_mound <= 0 or profile is None:
            carve.clear()
            band.clear()
            carve.flush()
            band.flush()
            if flow is not None:
                flow.update_surface(self._mound_flow_clock, self._particle_motion_scale,
                                    self._sand_material,
                                    (self.sand_base, self.sand_dark, self.sand_light),
                                    [], self._contact_flow_diameter(), 0.0)
            return
        cx = self._cx
        bottom = self._lower_sand_bot
        top = bottom + 2.0 * self._R_inner + MOUND_CREST_MARGIN   # == redraw 里矩形的顶
        apex = self._mound_apex()
        _mb = SAND_SURFACE_ALPHA * min(1.0, max(0.0, h_mound / SAND_SURFACE_FADE))
        if MOUND_BAND_NECK_FADE:
            # 🔴 **2026-10-10: 锥顶逼近颈部时把亮带淡掉。**
            #    实测(PC 400x875, 10s 档 t=8): 堆涨上来后, 这条 `sand_light` 高光带正好落在
            #    颈部下沿, 在 16px 宽的柱子里读成一块**平的、亮 4~7 级**的纯色条
            #    (x∈[194,209] 行∈[460,472], 命中 314 px, 色 (221,170,101) =
            #     `sand_light` 按 α=0.31 叠加 ⇒ 正是 0.55 × 堆高淡入 0.57)。用户报的"颜色分层"。
            #    上球那条带 (`_upper_band_color`) 有 `(1-apex_fade)`, 但那只在接近满球时起作用,
            #    管不到这里 —— 这里要的是"锥顶接近颈部"这个条件。
            _gap = (2.0 * self._neck_y - self._taper["y_bot"]) - (self._lower_sand_bot + apex)
            _mb *= min(1.0, max(0.0, _gap / SAND_BAND_NECK_FADE))
        self._mound_band_color.a = _mb
        if NECK_GEOM_ON:
            _tag = int(self.elapsed)
            if getattr(self, "_mbg_t", -1) != _tag:
                self._mbg_t = _tag
                _al = self._lower_y_c + self._R_inner
                _ap = self._lower_sand_bot + apex
                print("MBG t=%.1f h_mound=%.1f apex=%.1f low_bot=%.1f low_yc=%.1f Ri=%.1f "
                      "ball_top=%.1f contour_apex=%.1f gap=%.1f band_a=%.3f"
                      % (self.elapsed, h_mound, apex, self._lower_sand_bot,
                         self._lower_y_c, self._R_inner, _al, _ap, _al - _ap,
                         self._mound_band_color.a))
        knots = self._mound_knots(apex)
        cols = []
        # ★ **每列只解一次 `bounds` / `shape_at`**(2026-10-06 性能, 外部评审 §5.1 的实例)。
        #   原来一列里 `bounds` 被算 **3 次**(= 3 个 sqrt)、`shape_at` **2 次** ——
        #   因为 `contact()` 与 `free_surface()` 各自又把它们重算了一遍。
        #   走 `profile.column()` 一次拿齐, **定义仍然只有 `_MoundProfile` 那一份**
        #   (没有在调用方抄第二套算式)。
        column = profile.column
        strength = self._mound_flow_strength() if flow is not None else 0.0
        flow_diameter = self._contact_flow_diameter()
        for dx in knots:
            y, free, thick = column(dx, apex)
            cols.append((cx + dx, bottom + y, free, thick))
        for i in range(len(carve)):
            if i + 1 >= len(cols):
                carve.zero(i)
                if i < len(band):
                    band.zero(i)
                continue
            x0, y0, free0, th0 = cols[i]
            x1, y1, free1, th1 = cols[i + 1]
            carve.set(i, [x0, y0, x1, y1, x1, top, x0, top])
            if i >= len(band):
                continue
            if free0 and free1:
                w = min(SAND_SURFACE_BAND, th0, th1)
                midpoint = abs(0.5 * (x0 + x1) - cx)
                w *= 1.0 - strength * (1.0 - _smoothstep(
                    0.0, 2.5 * flow_diameter, midpoint))
                band.set(i, [x0, y0, x1, y1, x1, y1 - w, x0, y0 - w])
            else:
                band.zero(i)
        if NECK_CARVE_OFF:          # 消融: 判"行468-545那条亮带"是 carve 还是 band
            carve.clear()
        carve.flush()
        band.flush()
        if flow is not None:
            flow.update_surface(self._mound_flow_clock, self._particle_motion_scale,
                                self._sand_material,
                                (self.sand_base, self.sand_dark, self.sand_light),
                                cols, flow_diameter, strength)

    def _build_dynamic_canvas(self):
        """保留真圆/Stencil/Line 画法,只在几何变化时重建固定指令。"""
        self.canvas.clear()
        cx, Ri = self._cx, self._R_inner
        self._sand_chords = []
        material = self._current_material()
        self._sand_material = material
        self._sand_flow_contexts = ()
        self._mound_surface_flow = None
        self._splash_pixel = Window.system_size[0] / max(1.0, Window.width)
        upper_flow = neck_flow = mound_flow = None
        if material and os.environ.get("HG_SAND_FLOW", "1") != "0":
            try:
                from sand_flow_material import SandFlowContext
                geometry = (cx, Ri, self._upper_sand_bot, self._taper["in_pts"][0][1])
                upper_flow = SandFlowContext(geometry, tag=1.0)
                neck_flow = SandFlowContext(geometry, tag=2.0)
                # 下球沙体也走同一条 GPU 材质管线(标记 moundflowall, 默认关)。
                # 实测: 上球那张 Rectangle 在 upper_flow 里(走着色器), 下球那张挂在
                # self.canvas 上(普通贴图) —— 两者在**沙堆锥顶那一行**硬接, 一边 GPU
                # 平流细颗粒、一边整块拉伸的贴图 ⇒ 用户看到的那条"颜色分层"。
                if material and _neck_marker("moundflowall"):
                    mound_flow = SandFlowContext(
                        (cx, Ri, self._lower_sand_bot, self._taper["in_pts"][-1][1]),
                        tag=3.0)
                self._sand_flow_contexts = (upper_flow, neck_flow)
                if not getattr(self, "_sand_flow_logged", False):
                    print("GPU sand flow active: upper reservoir + neck")
                    self._sand_flow_logged = True
            except Exception as exc:
                print("GPU sand flow unavailable, keeping static material: %s" % exc)
        with self.canvas:
            for yc in (self._upper_y_c, self._lower_y_c):
                bottom = yc - Ri
                StencilPush()
                Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
                StencilUse()
                # 材质烘的就是 albedo ⇒ 前面必须是**白色**; 退回平色时才染 sand_base
                if yc == self._upper_y_c and upper_flow is not None:
                    self.canvas.add(upper_flow)
                    with upper_flow:
                        color = Color(1, 1, 1, 1)
                        rect = Rectangle(pos=(cx - Ri, bottom), size=(2 * Ri, 0),
                                         texture=material.texture)
                elif yc == self._lower_y_c and mound_flow is not None:
                    self.canvas.add(mound_flow)
                    with mound_flow:
                        color = Color(1, 1, 1, 1)
                        rect = Rectangle(pos=(cx - Ri, bottom), size=(2 * Ri, 0),
                                         texture=material.texture)
                else:
                    color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
                    rect = Rectangle(pos=(cx - Ri, bottom), size=(2 * Ri, 0),
                                     texture=None if material is None else material.texture)
                # 🔴 **2026-10-09 删**: 这里原来还建一条「沙面窄过渡亮带」
                #    (`_sand_bands`, 两球各一条直边 Rectangle)。它**早就作废了** ——
                #    下球改由 `_draw_mound_shape`、上球改由 `_draw_upper_shape`
                #    沿**真实轮廓逐段**画亮带(见 `redraw` 里那段注释), 直边矩形跟不上起伏,
                #    留着会与曲线带叠成两条。它此后**每帧都被强制 `size=(0,0)/a=0`**,
                #    全仓库没有任何一处把它设回可见 ⇒ **可证的死层**(占用 4 条指令 + 每帧
                #    ~6 次属性写, 一个像素都画不出)。
                #    证据: `_render_golden --check` 两臂各 60/60 **逐图一致**(删它必须 0 像素差)。
                if yc == self._upper_y_c:
                    # 上球: 漏斗 carve(专家 dingbu.md §4) —— 与下球同一套写法, 段数同样按节点数定
                    n_seg_u = max(1, MOUND_SHAPE_NODES)
                    Color(*hex_rgb(GLASS_FILL), 1)
                    self._upper_carve = _QuadBand(n_seg_u)
                    self._upper_band_color = Color(*(tuple(self.sand_light) + (0.0,)))
                    self._upper_band = _QuadBand(n_seg_u)
                if yc == self._lower_y_c:
                    # 下球: carve 按**真转折点**折线抠掉轮廓以上的沙(见 _draw_mound_shape)。
                    # 段数 = 节点数-1(含 ±R/±b/0), 在几何重建时定下来。
                    n_seg = max(1, len(self._mound_knots()) - 1 + 4)   # +4: 逐帧的壁交点
                    Color(*hex_rgb(GLASS_FILL), 1)
                    self._mound_carve = _QuadBand(n_seg)
                    self._mound_band_color = Color(*(tuple(self.sand_light) + (0.0,)))
                    self._mound_band = _QuadBand(n_seg)
                    if (material and os.environ.get("HG_SAND_FLOW", "1") != "0"
                            and os.environ.get("HG_MOUND_FLOW", "0") == "1"):
                        try:
                            from sand_flow_material import MoundSurfaceFlowContext
                            self._mound_surface_flow = MoundSurfaceFlowContext(
                                (cx, Ri, bottom, 2.0 * Ri + MOUND_CREST_MARGIN),
                                n_seg + 1, material.texture)
                            self.canvas.add(self._mound_surface_flow)
                            if not getattr(self, "_mound_flow_logged", False):
                                print("GPU mound contact flow active")
                                self._mound_flow_logged = True
                        except Exception as exc:
                            self._mound_surface_flow = None
                            print("GPU mound flow unavailable, keeping particles: %s" % exc)
                StencilUnUse()
                Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
                StencilPop()
                self._sand_chords.append((color, rect))
        if neck_flow is not None:
            self.canvas.add(neck_flow)
        # 🔴 **2026-10-10: 这几条颈部沙柱图元要不要放进 `neck_flow`(GPU 沙流 context)?**
        #    放进去 ⇒ 它们走的是**沙流着色器**; 放外面 ⇒ 走普通纹理四边形。
        #    `flatnopart` 一臂(材质整个关掉 ⇒ 走外面)出口以下是 **87.5~100%** 的实心柱,
        #    而材质在场(走里面)只有 **27%** —— 且把着色器里的打散/洞**两项都关掉也还是 27%**
        #    ⇒ 怀疑它们压根没被画出来。这个开关就是为了把"context"与"着色器"劈开。
        _neck_out = _neck_marker("neckout")
        with (neck_flow if (neck_flow is not None and not _neck_out) else self.canvas):
            neck_tex = None if material is None else material.texture
            self._neck_color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
            # 颈部沙柱那 25 条逐段四边形 —— 同样是静态的一段折线, 合成一个 `Mesh`
            # (逐帧只更新顶点, 指令数 25×2 → 2)。它有**逐条自定义 uv**(见 `_neck_sand_side`),
            # 所以走 `set(i, pts, uvs)`; 换材质时走 `set_texture()`。
            # 🔴 2026-10-09: 容量**跟着 `in_pts` 走**, 不再写死。
            #    沙柱节点数 = 1(顶) + (in_pts 里 `bottom<y<top` 的点, 最多 `len(in_pts)-1`)
            #              + 1(出口) + 1(下沿跟沙走的延伸) ⇒ 上限 = `len(in_pts) + 2` **个节点**,
            #    而绘制循环 `for i in range(len(quads))` 画的是**段**, 需要 `节点数-1` 格。
            #    ⇒ 取 `len(in_pts) + 3` 留两格余量。
            #    ⚠️ **第一版写死 `TAPER_SEGS + 1`(=25) 就是这次的坑**: 节点 27 > 容量 25
            #      ⇒ 循环走不到最后 1 段(正是延伸段), **不报错、不崩、只是没画出来** ——
            #      现象是"改了但画面上几乎没变"。**容量必须 ≥ 节点数 − 1**。
            #    (复核: 27 节点在容量 25 下丢的是**最后 1 段**, 不是两段 —— 逐项算过。)
            # 🔴 2026-10-09 再改: 自由收缩段又插了 `NECK_TAPER_SEGS` 个节点
            #    (`_neck_sand_side` 的 `_free_width_ratio` 那一段) ⇒ 上限变成
            #    `len(in_pts) + 2 + NECK_TAPER_SEGS`, 这里取 `+8` 留余量。
            #    ⚠️ **改 `_neck_sand_side` 的节点数就要回这里改容量** —— 少一格是静默的
            #      (循环画不到最后一段, 不报错)。
            self._neck_quads = _QuadBand(
                len(self._taper["in_pts"]) + 8 + NECK_FREE_EXTRA_SEGS + NECK_FRONT_SEGS,
                texture=neck_tex)
            self._neck_solid_color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
            self._neck_solid_rect = Rectangle(size=(0, 0), texture=neck_tex)
            # 沙柱下段(孔口往上 transition 那段): 直接画不透明的沙色矩形。
            # 原来这里用 1×64 渐变纹理做 alpha 0.7→1.0 的"出口柔化", 但这条矩形
            # 只有几个像素高, **任何 alpha 变化都等于硬边** —— 实测在管内留下一条
            # 半透明横线(关掉颗粒层后单行跳变 dB=10.9;改成不透明后降到 5.3,
            # 剩下的是"沙柱→敞开喇叭口"的自然边界)。
            self._neck_fade_color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
            self._neck_fade_rect = Rectangle(size=(0, 0), texture=neck_tex)

        # 🔴 **2026-10-10: 出口以下那一段必须**单独画在 context 外面**。**
        #    设备 1080, `flowrate=1` 把粒子抽掉、只看材质 coverage(逐行最高连续沙色段):
        #      材质关(四边形本来就在 context 外面)  出口以下 **89.4%** 实心
        #      材质开(整条四边形在 `neck_flow` 里)   出口以下 **27.4%** ← 用户看到的那条线
        #      材质开 + 整条移出 context             出口以下 **89.4%** ← 恢复
        #    把着色器里的打散(`sandedgemul=0`)与二值洞(`holeth=-1`)**两项都关掉也还是 28%**
        #    ⇒ 不是 coverage 里那两项的事, 是那几段走 `neck_flow` 时**根本没被画出来**。
        #    但整条移出会改到**出口以上**(实测最大通道差 25 / 2653 px) ⇒ 只搬"出口以下"。
        #    出口以上仍走着色器(流动颗粒), 出口以下是普通材质四边形。
        # 🔴 **2026-10-10: 用一条**普通 Rectangle**把材质铺在粒子之上**(标记 `matover`)。
        #    为什么不用已有的 `_neck_free_band`: 实测它几何/uv/索引/层序全对却净贡献 1px
        #    (三次独立确认), 查不动。而 `Rectangle` 这一类(`_sand_chords`)是**已证明可见**的,
        #    所以换一条确定能渲染的路。
        #    它是 `self._stream_pools`(画布最末一族)之后的最后一批指令 ⇒ 天然盖在粒子上。
        self._mat_over_color = None
        self._mat_over_rect = None
        self._neck_free_band = None
        if neck_flow is not None and NECK_FREE_ONTOP:
            # 🔴 **2026-10-10: 把材质带提到粒子之上。**
            #    实测根因(两条独立消融: `HG_FLOW_RATE=1` 与清空 `_stream_pools` 都把
            #    颈部下沿那块 16x13 的纯色贴片从 314px 打到 5px): 那块"分层"就是**下落沙流
            #    自己** —— 2px 的 Line 在出口附近密到并成一块, 颜色统一 ⇒ 读成平的色带。
            #    而 2.32 加的 `_neck_free_band`(材质画的、正确词汇)**画了却被它盖住**
            #    (清它只差 1px)。用户口径就是「把上球那套用到颈部」⇒ 让材质当可见面。
            #    **不减任何粒子**, 只改画布次序。
            pass
        if neck_flow is not None and NECK_OUT_SPLIT:
            with self.canvas:
                self._neck_free_color = (Color(1, 1, 1, 1) if material
                                         else Color(*self.sand_base))
                self._neck_free_band = _QuadBand(
                    NECK_TAPER_SEGS + NECK_FREE_EXTRA_SEGS + NECK_FRONT_SEGS + 1,
                    texture=neck_tex)
            if NECK_FREE_ONTOP:
                # 提到最上层: 先摘下来, 再按序追加到画布末尾。
                # ⚠️ **不能吞异常** —— 第一版就是 try/except pass, 结果 `remove` 全失败、
                #    三个 `add` 变成重复挂载, 画面 0 变化而日志一声不响。
                _n0 = len(list(self.canvas.children))
                _rm = []
                for _nm, _ins in (("color", self._neck_free_color),
                                  ("bind", self._neck_free_band.bind),
                                  ("mesh", self._neck_free_band.mesh)):
                    try:
                        self.canvas.remove(_ins)
                        _rm.append(_nm)
                    except Exception as _e:
                        _rm.append("%s!%s" % (_nm, type(_e).__name__))
                self.canvas.add(self._neck_free_color)
                self.canvas.add(self._neck_free_band.bind)
                self.canvas.add(self._neck_free_band.mesh)
                _n1 = len(list(self.canvas.children))
                _kids = list(self.canvas.children)
                print("FREEONTOP remove=%s  n %d -> %d  mesh_is_last=%s"
                      % (",".join(_rm), _n0, _n1,
                         _kids[-1] is self._neck_free_band.mesh))

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
        # 🔴 **2026-10-09: 材质路径下这一层"连建都不建"**(跨模型两位独立确认)。
        #    `redraw` 的绘制点是硬分支: `if _sand_flow_contexts and _sand_material is not None`
        #    走 `context.update_flow(...)`、**else** 才调 `_draw_neck_grains` ⇒ 材质路径下
        #    这一层**永远不画**(实测: 60 帧 0 次调用, 两条路径都测了)。
        #    但它照样常驻画布每帧 apply: 桌面 `line` 档 **961 条**, 出货 batch 档 **65 条**
        #    (普查实测 327→262, 差 65 = 整个 neck 族);
        #    而且 `_collect_warm_jobs` 还会为它**白建 32 块**顶点表(材质路径下永远不喂)。
        #    ⇒ 不建 ⇒ `install_neck` 的 wrapper 见 `group is None` 直接 `_neck_batches = None`,
        #      32 个颈批与 32 个预热作业**一起消失**(那条分支早就在, 不用改)。
        #    ⚠️ **回退路径必须一字不变**: `HG_SAND_FLOW=0` / `HG_SAND_MATERIAL=flat` /
        #      shader 编译失败时 `_draw_neck_grains` 是**活的**, 这层照旧要建。
        #    ⚠️ 本项目有前科: 1.215/1.216 动"颈部少挂 Color 指令"产出过 13000px 说不清的差异,
        #      两次回退。本改动**必须**过 `_render_golden --check` 逐图 0 差异, 否则回退。
        self._neck_grain_pool = []
        self._neck_grain_count = 0
        if (self._sand_flow_contexts and self._sand_material is not None
                and not NECK_GRAINS_IN_MATERIAL):
            self._neck_grain_group = None          # 不建、不挂 ⇒ 0 条指令、0 次 apply
        else:
            self._neck_grain_group = InstructionGroup()
            self.canvas.add(self._neck_grain_group)
            # Project existing grains upstream; they do not add physics particles.
            for _ in range(320):   # 🔴 128→320: 15s 档候选 281 ⇒ 原池丢 54%(丢的是喇叭口那批)
                color = Color(*self.sand_base)
                line = Line(points=[], width=1)
                self._neck_grain_group.add(color)
                self._neck_grain_group.add(line)
                self._neck_grain_pool.append((color, line))

        with self.canvas:
            self._contact_grain_color = Color(1, 1, 1, 1) if material else Color(*self.sand_base)
            self._contact_grains = _QuadBand(8, texture=None if material is None else material.texture)

        # 新生散粒可被主沙流遮挡; 离开流束后自然显露, 不在接触核心直接剔除。
        self._splash_group = InstructionGroup()
        self._splash_color = Color(*self.sand_light)
        self._splash_group.add(self._splash_color)
        self.canvas.add(self._splash_group)
        self._splash_rects = []

        # 同色同线宽共用一条 Color 指令,且不再排序/改变物理粒子列表。
        self._stream_pools = {}
        # 高光在普通粒子之后绘制,避免被密集的主体完全盖住。
        for index in list(range(len(self._color_table))) + [-1]:
            for size in (1, 2):
                group = InstructionGroup()
                color = Color(*(self._hilite_color if index < 0
                                else self._flow_table[index]))
                group.add(color)
                self.canvas.add(group)
                self._stream_pools[index, size] = (group, color, [])
        self._stream_buckets = {key: [] for key in self._stream_pools}
        # ★ 纹理渲染器在跑时只建**下标数组**桶(见 `_stream_np_only`), 省掉
        #   `tolist()` -> 渲染器再 `np.asarray()` 的往返(峰值 2750 个整数)。
        self._stream_np = {}
        self._stream_np_only = False
        self._stream_counts = {key: 0 for key in self._stream_pools}
        # 🔴 **2026-10-09: 批处理沙流渲染器接管时, 这次预留是纯白做。**
        #    `_reserve_stream_lines` 预建的那批 `Line` 会被
        #    `flow_texture_experiment.build_texture_batches` 当场 `pool.clear()` 丢掉
        #    (它按桶直接写端点纹理)。实测(桌面 15s 档) **20.8ms/次**, 而
        #    改周期 / 转屏 / 分屏 / 启动**每一次**画布重建都要付。
        #    标记由渲染器自己置位(它 `build_texture_batches` 里的 `try/finally`);
        #    它失败时会 `_reserve_stream_lines()` 补回来 ⇒ 回退到 Line 池时图元照旧齐备。
        if not getattr(self, "_stream_batched", False):
            self._reserve_stream_lines()

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
        motion_scale = self._particle_motion_scale
        # ⚠️ 预留也要跟着放大 —— 否则池子按旧速率预留, 1s 档会边跑边扩容
        rate = FLOW_BASE_RATE * motion_scale
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
            # ⚠️ `size` 可以是**数字**(正方形)也可以是 **(w, h) 二元组** —— 飞溅层用它做
            #    1x1 / 1x2 / 2x2 的混合尺寸(用户 2026-10-06:「感觉太密集、太细」)。
            if isinstance(sz, (tuple, list)):
                w, h = float(sz[0]), float(sz[1])
            else:
                w = h = float(sz)
            pos = (particle["x"] - w / 2, particle["y"] - h / 2)
            size = (w, h)
            if i == len(pool):
                rect = Rectangle(pos=pos, size=size)
                group.add(rect)
                pool.append(rect)
            else:
                # ★ **值没变就别写**(2026-10-06 性能): 停稳的飞溅占在世数约四成,
                #   它们的 `pos`/`size` 一帧到一帧**完全一样**。Kivy 的属性赋值要走
                #   描述符 + 事件派发, 每帧白写两千多次不值当。
                #   ⚠️ `Rectangle` **没有** `x`/`y`/`width`/`height`(只有 `pos`/`size`
                #      两个 ReferenceListProperty)—— 踩过, 属性名写错会当场 AttributeError。
                rect = pool[i]
                if rect.pos != pos:
                    rect.pos = pos
                if rect.size != size:
                    rect.size = size
        for rect in pool[len(particles):]:
            if rect.size[0] or rect.size[1]:
                rect.size = (0, 0)

    @staticmethod
    def _sync_rects_arrays(group, pool, count, xs, ys, hws, hhs):
        """`_sync_rects` 的**数组版** —— 与它同语义, 但直接从并行数组读。

        ## 为什么必须有它

        飞溅换成并行数组之后, `splashes` 成了**只给取证工具的 property**(每次访问重建
        一批 dict)。桌面**默认不装**批处理渲染器, 于是 `redraw` 会走
        `_sync_rects(..., self.splashes)` —— 实测在桌面 cProfile 里那一行占
        **0.287s / 1.584s = 18%**, 比任何真实热点都大。

        ⇒ 两个后果: ①桌面跑起来白白慢一大截; ②**所有桌面测量被污染** ——
        连"桌面→设备 ×2.1"那个换算系数在飞溅这一层都失真了。
        **热路径不许碰 `splashes` 这个 property**(它自己的 docstring 就这么写的)。

        ⚠️ 半宽半高已经是**存好的**(`shw`/`shh`), 不用再除 2 —— 与 `update_arrays` 一致。
        ⚠️ "值没变就别写"那条守卫照抄(停稳的飞溅约占四成, 一帧到一帧完全一样)。
        """
        for i in range(count):
            hw = hws[i]
            hh = hhs[i]
            pos = (xs[i] - hw, ys[i] - hh)
            size = (hw * 2.0, hh * 2.0)
            if i == len(pool):
                rect = Rectangle(pos=pos, size=size)
                group.add(rect)
                pool.append(rect)
            else:
                rect = pool[i]
                if rect.pos != pos:
                    rect.pos = pos
                if rect.size != size:
                    rect.size = size
        for rect in pool[count:]:
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
                    self._neck_quads.set_texture(material.texture)
                    self._neck_solid_rect.texture = material.texture
                    self._neck_fade_rect.texture = material.texture
                    self._contact_grains.set_texture(material.texture)
            if self._sand_material is None:
                # 退回平色时颈部才跟着染沙色; 有材质时前面必须保持白色(否则双重着色变暗)
                self._neck_color.rgb = self.sand_base
                self._neck_solid_color.rgb = self._neck_fade_color.rgb = self.sand_base
            # (2026-10-09 删: 这里原来刷 `_sand_bands` 那条作废亮带的颜色 —— 带子已删)
            self._mound_band_color.rgb = self.sand_light
            # ⚠️ **上球那条也要刷**(2026-10-05 r9-1号 查出漏了): 它原来只在
            #    `_build_dynamic_canvas()` 创建时取一次 sand_light, 换沙色后**一直是旧色**,
            #    直到发生几何重建(改周期/改尺寸/跑一次 benchmark)才跟上。
            #    实测: 金沙换绿沙后未重建时沙面首 3 行仍 (221,215,166) 暖色调,
            #    触发重建后 (200,217,160) 才变绿。3~5 设备像素高 × 约 900px 宽, 通道差 ≈20 级。
            self._upper_band_color.rgb = self.sand_light
            for (index, _size), (_group, color, _pool) in self._stream_pools.items():
                color.rgb = (self._hilite_color if index < 0
                             else self._flow_table[index])
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
        # ⚠️ **同一个坑的第二处, 2026-10-06 补**: 画出来的沙面是
        #    `level − drop(dx) + rough(i)`(`_draw_upper_shape`), 而矩形顶只到 `level`
        #    ⇒ 凡 `rough(i) > drop(dx)` 的节点, 沙面线**连同它那条 3px 亮带**一起跑到
        #    矩形外面 —— 那儿没有沙, 亮带于是合成到**玻璃**上, 沙面上方浮出一排淡色小帽。
        #    r27-1号 在设备档6 抓到; 颜色实测 (232,211,173) == `sand_light`(230,184,112)
        #    与背景的 0.55 混合(**逐位**对得上), 正是 `SAND_SURFACE_ALPHA`。
        #    越界量桌面上实测: 档6 **+1.90px** / 档4 +0.52px / 档3 及以下 0
        #    (`tools/_probe_rect_vs_surface.py`)。幅度 `_uamp ∝ 2·Ri` 而
        #    `SAND_SURFACE_BAND = 3.0` 是死像素 ⇒ **设备上比桌面明显**。
        up_draw = upper_height + _up_lift + self._upper_rough_crest(upper_height)
        # 下球矩形**固定画满整个内球**(D + 余量), 不再跟着虚拟峰顶走 —— 专家 §2.5:
        #   ① 放开"虚拟锥顶高于球顶"之后, 再按高度截 UV 会把颗粒**纵向拉伸**;
        #   ② 轮廓以上的沙由 carve 抠掉, 所以矩形只管"铺满", 上沿永远取球内顶 + 余量。
        #   ⇒ 与 `_draw_mound_shape` 里 carve 的上沿是**同一个值**。
        mound_draw = (2.0 * self._R_inner + MOUND_CREST_MARGIN) if h_mound > 0 else 0.0
        if NECK_MOUND_OFF:      # 消融: 判"行468以下那一列"是不是下球沙体矩形画的
            mound_draw = 0.0
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
        # ⚠️ **两条旧的直边亮带都作废了**: 下球改由 `_draw_mound_shape`、上球改由
        #    `_draw_upper_shape` 沿真实轮廓逐段画(直边矩形跟不上起伏, 会留下悬空亮台/
        #    缺口 —— 评审 1 号指出; 留着还会与曲线带叠成两条, 一条平一条弯)。
        # 🔴 **2026-10-09: 那两条作废的 `_sand_bands` 已经彻底删除**(创建/换色/每帧清零
        #    三处一起删) —— 它们此后每帧都被强制 `size=(0,0)/a=0`, 全仓库没有任何一处
        #    把它们设回可见 ⇒ **可证的死层**。删它必须 0 像素差: `_render_golden --check`
        #    两臂各 60/60 逐图一致。
        if not NECK_MISC_OFF:                               # 消融: 行466-523 到底谁画的
            self._draw_surface_markers(upper_height, h_mound)   # §5 表层滑动标记
        if NECK_ABL5_OFF:
            # 🔴 干净消融(五项一起): 上球沙体矩形 / 上球 carve+band / contact grains /
            #    splash+dust+flare 三组 / 表层标记。**只跳过更新是空转, 必须清图形。**
            for _idx, ((_c, _r), _h) in enumerate(zip(self._sand_chords,
                                                     (0.0, 0.0))):
                if _idx == 0:
                    _r.size = (0, 0)
            for _b in (self._upper_carve, self._upper_band):
                if _b is not None:
                    _b.clear(); _b.flush()
            if self._contact_grains is not None:
                self._contact_grains.clear(); self._contact_grains.flush()
            for _g in (self._splash_group, self._dust_group, self._flare_group,
                       self._surface_marker_group):
                if _g is not None:
                    for _ch in list(_g.children):
                        if hasattr(_ch, "points"):
                            _ch.points = []
                        elif hasattr(_ch, "size"):
                            _ch.size = (0, 0)
        if NECK_MARKER_OFF:
            # 🔴 **干净消融**: 必须**真清空 points**。只"跳过更新"是空转 —— 图形指令留在
            #    画布上会继续画上一帧的 points（`neckmshape`/`neckmisc`/`neckfree` 三次
            #    消融都栽在这上面, 报出了假的"已排除"）。
            for _c, _ln in self._surface_marker_pool:
                if _ln.points:
                    _ln.points = []
        self._draw_upper_shape(upper_height)     # §4 上球漏斗(纯减去: 矩形/UV 不动)
        if NECK_MOUNDSHAPE_OFF:
            # 真清空(不是跳过): 跳过会让指令留在画布上继续画上一帧
            for _b in (self._mound_carve, self._mound_band):
                if _b is not None:
                    _b.clear(); _b.flush()
        else:
            self._draw_mound_shape(h_mound)
        # The shared surface, not an independent completion timer, owns the neck.
        side = self._neck_sand_side()
        # 🔴 **消融(2026-10-10)**: 标记文件 `<app>/neckquads` ⇒ 把颈部沙柱四边形整条清空。
        #    目的只有一个 —— **判出"颈部可见的那一列到底是谁画的"**。这轮在这一点上错过两回:
        #    先按"它走着色器"去调 `lighting`/`uv`, 量到"没反应"; 又按"它在 context 里"去解释
        #    2.31/2.32 的逐像素等同。**先把这个问答清楚, 后面所有颈部测量才有对象。**
        if NECK_QUADS_OFF:
            side = []
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        inlet = self._taper["y_bot"]
        tail_front = (0.0, 0.0, 1.0)
        if (side and self._sand_flow_contexts and self._sand_material is not None
                and self._upper_sand_fraction() <= 0.0):
            mean_top = side[0][1]
            grain_px = max(1.0, diameter / SAND_MATERIAL_SIZE * SAND_MATERIAL_COARSE)
            amplitude = max(0.0, min(1.6 * grain_px, 0.45 * (mean_top - outlet),
                                     0.45 * (inlet - mean_top)))
            if amplitude > 0.0:
                # Enlarge only the draw bound; the shader cuts a zero-mean grain front.
                tail_front = (mean_top, amplitude, self._taper["t_in"])
                side = [(side[0][0], mean_top + amplitude)] + side[1:]
        # 🔴 **2026-10-09 (F6): 出口以下那段是"落下来的一把沙", 不是"一块板"。**
        #    用户判词: 「沙柱看起来是个**规整的矩形**」(附 v2.13 截图)。玻璃在出口就没了,
        #    以下没有任何东西约束它 —— 两条边该是毛的。材质着色器里按镜像噪声吃最外一圈
        #    (内 62% 保持实心), 平均宽度不变。关掉用 `HG_NECK_FREE=0`。
        free_front = (0.0, 0.0, 1.0, 0.0)
        free_ramp = 0.0
        if (NECK_FREE and side and self._sand_flow_contexts
                and self._sand_material is not None):
            # 下沿只在**前沿说了算**的时候才毛 —— 沙堆涨上来之后下沿就是堆面,
            # 在接缝上打毛边会把 2.13 刚接上的缝重新"开"出来。
            _mound0 = self._lower_sand_bot + self._mound_contact_h(0.0)
            _front0 = self._falling_front()
            _cut = _front0 if _front0 < _mound0 - 1e-9 else 0.0
            free_front = (outlet, 1.0, self._taper["t_in"], _cut)
            # 🔴 **2026-10-10: 出口那条分界线的解药。**
            #    设备口径实测(`tools/_dev_aniso.sh` + 逐行最长连续沙色段):
            #    出口以上 **100%** 实心, 出口以下**一步**掉到 **28%** —— 因为着色器里
            #    `if (sand_position.y < sand_free.x)` 是**硬分支**, 一进去 `rim` 就满功率,
            #    只剩内 `sand_core`(0.35) 是实心。0.35 的半宽占柱宽 34.6%, 与 28.3% 对得上。
            #    解药 = 把打散**按深度渐入**, 而不是在一条线上瞬间全开。
            #    单位取**颈管高的倍数**(用户口径: 「不用超过瓶颈高度的 2 倍, 或者 1 倍」)。
            #    0 = 旧行为。
            if NECK_FREE_RAMP_TUBES > 0.0 and diameter > 1.0:
                free_ramp = (NECK_FREE_RAMP_TUBES * self._tube_h / diameter)
            else:
                free_ramp = 0.0
        transition = min(inlet - outlet, max(8, self._taper["t_in"] * 0.7))
        connected = bool(side and side[-1][1] <= outlet + 1e-6)
        if connected:
            transition = min(transition, max(0.0, side[0][1] - outlet))
        fade_top = outlet + transition
        neck_uv_scale = (None if (self._sand_material is None or _neck_nouv_on())
                         else 1.0 / diameter)
        quads = self._neck_quads
        # 出口以下那一段走 `_neck_free_band`(在 `neck_flow` **外面**, 见它在
        # `_build_dynamic_canvas` 里的注释); `neck_flow is None` 时主带本来就在外面,
        # 不需要第二条。
        # 🔴 **2026-10-10: 默认不分流** —— 出口以下跟着主带一起走同一个 GPU 着色器
        #    (`NECK_OUT_SPLIT` 见 `_build_dynamic_canvas` 的注释)。
        fband = (getattr(self, "_neck_free_band", None) if NECK_OUT_SPLIT else None)

        fn = len(fband) if fband is not None else 0
        fi = 0

        def _emit(band, idx, x0, y0, x1, y1):
            pts = [self._cx - x0, y0, self._cx + x0, y0,
                   self._cx + x1, y1, self._cx - x1, y1]
            if neck_uv_scale is not None:
                su = neck_uv_scale
                vb = NECK_UV_ANCHOR + (self._upper_sand_bot - y0) * su
                vt = NECK_UV_ANCHOR + (self._upper_sand_bot - y1) * su
                band.set_uv(idx, pts, (0.5 - x0 * su, vb, 0.5 + x0 * su, vb,
                                       0.5 + x1 * su, vt, 0.5 - x1 * su, vt))
            else:
                band.set(idx, pts)

        for i in range(len(quads)):
            if i < len(side) - 1:
                (x0, y0), (x1, y1) = side[i], side[i + 1]
                if connected and not NECK_JOIN:
                    # 🔴 2026-10-09: 这段 clip 的管辖区**只有 `[outlet, fade_top]`**
                    #    (那一段由两片**不透明**矩形画, 且矩形建在四边形**之后**、
                    #    画在它上面 ⇒ 放开这里不会双重曝光, 面板 1号 已实测)。
                    #    开了 `NECK_JOIN` 之后沙柱要往下延伸, 而延伸段正好落在
                    #    `y0 <= fade_top` 那一侧 —— 第一版没放开这里, 画面上**一点没变**。
                    #    (这条 clip 的来历见 `NECK_REDESIGN.md`: 当年"出口硬线"的产物。)
                    if y0 <= fade_top:
                        quads.zero(i)
                        continue
                    if y1 < fade_top:
                        y1 = fade_top
                        x1 = self._neck_width_at(y1)
                if fband is not None and y1 < outlet - 1e-6:
                    # 这条落在出口以下(或跨过出口) ⇒ 归 `fband`。
                    if y0 > outlet + 1e-6:
                        _xc = self._neck_width_at(outlet)     # 跨出口: 主带只画到出口
                        _emit(quads, i, x0, y0, _xc, outlet)
                        _emit(fband, fi, _xc, outlet, x1, y1)
                    else:
                        quads.zero(i)
                        _emit(fband, fi, x0, y0, x1, y1)
                    fi += 1
                    continue
                _emit(quads, i, x0, y0, x1, y1)
            else:
                quads.zero(i)
        if NECK_FREE_OFF and fband is not None:
            # 消融: 只清"自由段带"(真清空, 不是把段挪回主带)
            # ⚠️ `fband` 在不分流(默认)或材质关的路径上是 `None` ⇒ 必须短路,
            #    否则 `fband.clear()` 直接 AttributeError(2026-10-10 查出的潜在崩溃)。
            for _k in range(fn):
                fband.zero(_k)
            fi = fn
            fband.clear(); fband.flush()
        for _k in range(fi, fn):
            fband.zero(_k)
        if fband is not None:
            fband.flush()
        quads.flush()
        if _neck_log_on():
            _tag = int(self.elapsed)
            if _tag != getattr(self, "_neck_log2_t", -1):
                self._neck_log2_t = _tag
                _v = quads._v
                _nz = 0
                _ymin, _ymax = 1e18, -1e18
                for _i in range(len(quads)):
                    _o = _i * 16
                    _pts = _v[_o:_o + 16]
                    if any(_pts):
                        _nz += 1
                        _ys = _pts[1::4]
                        _ymin = min(_ymin, min(_ys))
                        _ymax = max(_ymax, max(_ys))
                print("NECKDBG2 t=%.1f side=%d band=%d written=%d "
                      "drawn_y=[%.1f..%.1f] outlet=%.1f"
                      % (self.elapsed, len(side), len(quads), _nz,
                         _ymin, _ymax, outlet))
                if len(side) >= 2 and getattr(self, "_neck_vdump_t", -1) != _tag:
                    self._neck_vdump_t = _tag
                    for _j in (0, len(side) // 2, len(side) - 2):
                        _o = _j * 16
                        print("NECKV seg%d  quads._v[%d:%d] = %s"
                              % (_j, _o, _o + 16,
                                 " ".join("%.2f" % v for v in quads._v[_o:_o + 16])))
                        if fband is not None:
                            print("NECKV seg%d  fband._v[%d:%d] = %s"
                                  % (_j, _o, _o + 16,
                                     " ".join("%.2f" % v for v in fband._v[_o:_o + 16])))
                if len(side) >= 2:
                    _i = len(side) - 2
                    _o = _i * 16
                    print("NECKDBG3 uvscale=%s seg%d y=(%.1f,%.1f) v=(%.3f,%.3f) u=%.3f"
                          % ("None" if neck_uv_scale is None else "%.5f" % neck_uv_scale,
                             _i, side[_i][1], side[_i + 1][1],
                             quads._v[_o + 3], quads._v[_o + 15],
                             quads._v[_o + 2]))
        if connected:
            pos = (self._cx - self._taper["t_in"], outlet)
            size = (2 * self._taper["t_in"], transition)
            if NECK_RECTS_OFF:      # 消融: 判"管子那一段的平色"是不是这两条矩形画的
                size = (0, 0)
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

        if NECK_GEOM_ON:
            _tag = int(self.elapsed)
            if _tag != getattr(self, "_neck_geom_t", -1):
                self._neck_geom_t = _tag
                print("NECKGEOM t=%.1f widget pos=(%.1f,%.1f) size=(%.1f,%.1f) "
                      "cx=%.1f Ri=%.2f up_bot=%.2f y_bot=%.2f neck_y=%.2f t_in=%.2f "
                      "diameter=? | Window.size=%s"
                      % (self.elapsed, self.x, self.y, self.width, self.height,
                         self._cx, self._R_inner, self._upper_sand_bot,
                         self._taper["y_bot"], self._neck_y, self._taper["t_in"],
                         str(Window.size)))
        if NECK_DUMP_ON:
            self._neck_dump_canvas()
        if not NECK_MISC_OFF:
            self._project_stream_contact()
            self._draw_contact_grains()
        self._draw_stream()
        if (self._mat_over_rect is None and NECK_MAT_OVER
                and self._sand_material is not None):
            # 🔴 **惰性创建**: 必须在**画布建完之后**再加, 否则会被 `_stream_pools`
            #    (在 `_build_dynamic_canvas` 更后面才建)画在上面 —— 上一版就栽在这,
            #    建在 6700 行而池子在 7012 行, 结果整图只差 1px。
            #    `redraw` 首次运行时画布已建完 ⇒ 此时 `with self.canvas:` 是**追加到末尾**。
            with self.canvas:
                # 半透明: 让**下落的粒子透出来** —— 用户要的是上球那种"看得见沙在落",
                # 全不透明会把它盖成一根静止的棒(用户 2026-10-10 截图判「还是不太对」)。
                self._mat_over_color = Color(1, 1, 1, MAT_OVER_ALPHA)
                self._mat_over_rect = Rectangle(
                    pos=(0, 0), size=(0, 0), texture=self._sand_material.texture)
        if self._mat_over_rect is not None:
            # 🔴 **2026-10-10 回归修复(用户开机即见)**: 下沿**绝不能**只取堆面。
            #    堆≈0 时 `_mound_contact_h(0.0)` = 0 ⇒ 下沿 = 球内底 ⇒ 这根矩形从出口
            #    **一路插到球底**, 在空的下球里画出一根蓝柱 —— 正是 1.238 的死因
            #    (用户判词「你不要顾头不顾腚」)。
            #    正解(项目自己写的): 下沿 = `max(堆面, 在途沙前沿)`; 前沿是**解析式**、
            #    开局就在出口附近 ⇒ 柱子自然只有一小截, 沙落到哪儿它长到哪儿。
            #    再加一道闸: **没开始 / 没有在途沙就整个不画**。
            _y0 = (2.0 * self._neck_y - self._taper["y_bot"])          # 出口
            _on = (self.elapsed > 0.0 and self.pn > 0)
            _y1 = max(self._lower_sand_bot + self._mound_contact_h(0.0),
                      self._falling_front()) if _on else _y0
            _h = max(0.0, _y0 - _y1)
            _w = self._taper["t_in"]
            self._mat_over_rect.pos = (self._cx - _w, _y1)
            self._mat_over_rect.size = (2.0 * _w, _h)
            # 🔴 **uv 必须与颈部四边形同密度**(`_emit` 那套), 不能吃 Rectangle 的默认 0..1:
            #    默认 uv 会把整张 512² 材质**横压纵拉**铺在 20x500 的矩形上 ⇒ 一条没有纹理的
            #    竖条("规整的矩形棒", 1.238 事故里用户骂过的)。用户 2026-10-10 截图: 「还是不太对」。
            _d = 2.0 * self._R_inner
            if _d > 1.0:
                _su = 1.0 / _d
                _ub = self._upper_sand_bot
                _u0, _u1 = 0.5 - _w * _su, 0.5 + _w * _su
                _v1 = NECK_UV_ANCHOR + (_ub - _y1) * _su
                _v0 = NECK_UV_ANCHOR + (_ub - _y0) * _su
                self._mat_over_rect.tex_coords = (
                    _u0, _v1, _u1, _v1, _u1, _v0, _u0, _v0)
        if NECK_POOL_OFF:
            # 🔴 **干净消融: 粒子流的 24 个桶**(画布上最后、最上层的一族, kids 81~379)。
            #    清 points 必须在 `_draw_stream()` **之后** —— 它是填 points 的那个,
            #    放前面会被立刻覆盖。清在前面那次 `after51` 崩了, 就是因为它清到了
            #    别人本帧还要按下标读的 `points`。
            for _g, _c, _lines in self._stream_pools.values():
                for _ln in _lines:
                    if _ln.points:
                        _ln.points = []
        if self._sand_flow_contexts and self._sand_material is not None:
            for i, context in enumerate(self._sand_flow_contexts):
                context.update_flow(self.elapsed, self._particle_motion_scale,
                                    self._sand_material, colors,
                                    tail_front if i == 1 else (0.0, 0.0, 1.0),
                                    free_front if i == 1 else (0.0, 0.0, 1.0, 0.0))
                context["sand_free_ramp"] = free_ramp if i == 1 else 0.0
        else:
            self._draw_neck_grains(side)
        if (NECK_GRAINS_IN_MATERIAL and self._neck_grain_group is not None
                and self._sand_flow_contexts and self._sand_material is not None):
            # 材质路径下把提亮层也画上 —— 见 `NECK_GRAINS_IN_MATERIAL` 的注释与消融数据。
            self._draw_neck_grains(side)
        # 飞溅层: 装了批处理渲染器就走批处理, 否则走原来的**逐 `Rectangle`**。
        # (见文件尾 `_install_splash_renderer` 与 `tools/flow_splash_experiment.py`)
        _batch = getattr(self, "_splash_batch", None)
        if _batch is None:
            # 非批处理路径(桌面默认)也走**数组** —— 原先这里传的是 `self.splashes`,
            # 而那是个 property, 每帧重建 1403 个 dict(桌面 cProfile 实测占 **18%**)。
            self._sync_rects_arrays(self._splash_group, self._splash_rects, self._sn,
                                    self.sx, self.sy, self.srw, self.srh)
        else:
            # 并行数组直通(每颗省掉 3 次 dict 查找)。
            _batch.update_arrays(self._sn, self.sx, self.sy, self.srw, self.srh)
        self._draw_flares(now)
        self._sync_rects(self._dust_group, self._dust_rects, self.dusts, dp(1.2))
        self._bore_color.a = 1 if remaining <= 0.001 else 0
        self._pause_color.a = 0.55 if not self.running and 0 < self.elapsed < self.duration else 0
        self._pause_rect.size = self.size if self._pause_color.a else (0, 0)

    def _draw_flares(self, now):
        """触底闪光 —— **原路: 每颗一条 `Color` + 一条 `Rectangle`**。

        ⚠️ 这一段被**单独拎成方法**是有原因的: 装了批处理渲染器时
        (`HG_SPLASH_RENDERER=batch` / 安卓) `tools/flow_splash_experiment.install_flares`
        会把**整个方法**换掉(见那边 `FlareBatch`)。留在 `redraw` 里就没法替换了。
        池子按需增长: `self._flare_rects` 只增不减, 多出来的置零隐藏。
        """
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

    def _group_stream_particles(self):
        """按 (色调, 线宽) 分桶 —— 返回 `{key: [粒子下标, ...]}`, 不再是 dict 列表。

        下标升序、桶内顺序与逐 dict 版逐字相同(渲染顺序不变); 渲染器从 `self._pv`
        按下标取 x / y / vy / trail_time, 于是每帧不必再建 2500 个 dict。
        """
        buckets = self._stream_buckets
        for bucket in buckets.values():
            bucket.clear()
        n_colors = len(self._color_table)
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        # ★ **每颗粒的色调抖动**(2026-10-06 用户:「**下层的沙子落下的层和原有的层差异巨大**」
        #   —— 经确认为**沙流 vs 沙堆**)。实测局部纹理 std: **沙流 0.47 / 沙堆 2.14**
        #   ⇒ 沙流光滑 4.5 倍, 读起来像一条"光带"而不是沙。
        #   原来只随 `W = int(相位×5/τ) ∈ {0..4}` 摆 ±2 档, 交叠后糊平 ⇒ 放宽到 ±4 档。
        #   ⚠️ **numpy 路径与标量路径必须一起改**(下面两处), 否则两条物理路径分叉。
        tone_scale = 9 / math.tau
        # 预先摊平成二维表: 原来每颗粒都要现造一个 (index, size) 元组再查字典,
        # 这里换成两次列表下标。分组结果与原来逐字相同。
        # ⚠️ **`by_key` 只给下面的标量兜底分支用** —— 安卓走 `_stream_np_only`,
        #    那份表建了从来没人读(24 次字典查找 + 12 个 list/帧)。挪到用处再建。
        last = n_colors - 1
        pv = self._pv
        start, end, reserve = self._transfer_timing()
        drain_start = start + (1.0 - reserve) * (end - start)
        progress = max(0.0, min(1.0, (self.elapsed - drain_start)
                               / max(1e-6, min(0.12, self.duration * 0.06))))
        pv.tail_blend = progress * progress * (3.0 - 2.0 * progress)
        pv.tail = pv.tail_blend > 0.0
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
                # ★ **全选时省掉三次花式索引**(2026-10-07 性能)。粒子是在颈口**出生**的,
                #   往下只会更小 ⇒ `y >= outlet` 几乎永不成立, `sel` 实际等于 `arange(n)`
                #   (实测 1036 颗里 0 颗被排除)。而 `pwp/pli/psz[:n][sel]` 三次 gather 各值
                #   ~6.5µs(n=1560, 随机访问)。`sel.size == n` 时 `arr[sel]` **就是** `arr`
                #   —— 逐位相同, 直接换成整片视图。
                if sel.size == n:
                    _pw, _pl, _ps = self.pwp[:n], self.pli[:n], self.psz[:n]
                    _xs_i, _ys_i = self.px[:n], y_all[:n]
                else:
                    _pw, _pl, _ps = self.pwp[:n][sel], self.pli[:n][sel], self.psz[:n][sel]
                    _xs_i, _ys_i = self.px[:n][sel], y_all[:n][sel]
                w = (_pw * tone_scale).astype(np.int64)
                np.minimum(w, 8, out=w)
                # 🔴 **2026-10-10: 色调改从材质那张颗粒场里取** —— 让沙流与沙体说同一套
                #    词汇。旧路径(出生相位 → 三档)叠出来是一层平涂抹, 见
                #    `_stream_grain_idx` 的 docstring。取不到材质 ⇒ 原样退回旧路径。
                _g = (self._stream_grain_idx(_xs_i, _ys_i, _pw)
                      if FLOW_GRAIN_TONE else None)
                if _g is not None:
                    w = _g.astype(np.int64)
                # **以 base 居中**(见 `_rebuild_color_table` 的 `_flow_table` 注释):
                # 不再有"越往下越亮"的深度斜率 —— 沙堆没有那条斜率, 沙流也不该有。
                idx = w + (FLOW_TONE_CENTER - 4)
                if FLOW_TONE_SLOPE:
                    # 深度 0(出口) → 1(球内底), 线性加档。
                    _span = max(1.0, float(getattr(self, "_lower_sand_bot", 0.0)) - outlet)
                    _dep = (outlet - y_all[:n]) / _span
                    if sel.size != n:
                        _dep = _dep[sel]
                    idx = idx + (np.clip(_dep, 0.0, 1.0) * FLOW_TONE_SLOPE).astype(np.int64)
                np.clip(idx, 0, last, out=idx)
                # ★ 色调研磨成 4 档(每 3 档取 1) —— 桶数 22 → 8。见 /tmp/patch_buckets.py
                idx -= idx % FLOW_TONE_QUANT
                key = np.where(_pl != 0.0, n_colors, idx)
                if pv.tail:
                    amp = self.pwa[:n] if sel.size == n else self.pwa[:n][sel]
                    threshold = np.mod(_pw / math.tau + amp * 0.61803398875, 1.0)
                    slot = np.where((_ps == 1.0) | (threshold < pv.tail_blend), 0, 1)
                else:
                    slot = np.where(_ps == 1.0, 0, 1)
                code = key * 2 + slot
                # ★ **先把桶码压到 `uint8` 再排**(2026-10-07 性能)。`np.argsort(kind="stable")`
                #   对整数走**基数排序**, 轮数正比于 dtype 宽度: 实测 n=1560 时
                #   int64 **33.6µs** / int16 4.5µs / **uint8 2.8µs** —— 快 12 倍。
                #   桶码 `key*2+slot` 的取值域是 `[0, 2*(n_colors+1))`, 本项目 n_colors=11
                #   ⇒ 恒 < 24, 一个字节绰绰有余。
                #   ⚠️ **稳定排序的排列由键唯一确定** ⇒ 换 dtype 得到的 `order` 与原来
                #      逐位相同(实测三种 dtype 两两相同)。`bincount` 仍吃原来的 int64
                #      (它反而更快: 1.26 vs 1.81µs)。
                order = np.argsort(code.astype(_np.uint8), kind="stable")
                counts = np.bincount(code, minlength=(n_colors + 1) * 2)
                pos = 0
                if self._stream_np_only:
                    # ★ **只建下标数组桶** —— 纹理渲染器直接用, 不必 `tolist()` 出来再
                    #   让渲染器 `np.asarray()` 转回去(峰值 2750 个整数走两趟)。
                    #   ⚠️ key 的构法与 `_build_dynamic_canvas` 里 `_stream_pools` 一致:
                    #      行 `n_colors` 对应 key `-1`(高光), 其余行就是 key 本身;
                    #      `slot` 0/1 对应尺寸 1/2。
                    npb = self._stream_np
                    npb.clear()
                    for k in range(counts.shape[0]):
                        c = int(counts[k])
                        if not c:
                            continue
                        key_idx = k >> 1
                        npb[(-1 if key_idx == n_colors else key_idx,
                             1 if (k & 1) == 0 else 2)] = order[pos:pos + c]
                        pos += c
                    return npb
                by_key = [[buckets.get((i, s)) for s in (1, 2)]
                          for i in list(range(n_colors)) + [-1]]
                light_row = by_key[n_colors]
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
        # 标量兜底路径要的那份 "桶码 -> 桶" 表(见上面 `pv.use_np` 分支里的同一条)
        by_key = [[buckets.get((i, s)) for s in (1, 2)]
                  for i in list(range(n_colors)) + [-1]]
        light_row = by_key[n_colors]
        # 🔴 **2026-10-10: 标量路径也必须从材质颗粒场取色。**
        #    ⚠️ 上一版只改了 numpy 路径 ⇒ 桌面(393 颗 < `_NUMPY_MIN=800`)走的是**标量**
        #    ⇒ 整整一轮 A/B 是**空转**(三档 `HG_GRAIN_GAIN` 的画面逐像素完全相同)。
        #    **改色调一定要两条路径一起改** —— 判据: 同一个 `HG_GRAIN_GAIN` 换值画面必须变。
        _gx = self.px[:pv.n]
        _gidx = None
        if FLOW_GRAIN_TONE and not pv.use_np and _np is not None and pv.n:
            _ysa = _np.asarray(ys[:pv.n])
            _sel = _np.flatnonzero(~(_ysa >= outlet))
            if _sel.size:
                _g = self._stream_grain_idx(_gx[_sel], _ysa[_sel],
                                            _np.asarray(phases[:pv.n])[_sel])
                if _g is not None:
                    _gidx = _np.zeros(pv.n, dtype=_np.int64)
                    _gidx[_sel] = _g
        for i in range(pv.n):
            y = ys[i]
            if y >= outlet:
                continue
            if lights[i]:
                row = light_row
            else:
                if _gidx is not None:
                    index = int(_gidx[i])
                else:
                    w = int(phases[i] * tone_scale)
                    if w > 8:
                        w = 8
                    index = w + (FLOW_TONE_CENTER - 4)
                index -= index % FLOW_TONE_QUANT      # ← 与 numpy 路径一起改(见上)
                if index < 0:
                    index = 0
                elif index > last:
                    index = last
                row = by_key[index]
            thin = sizes[i] == 1
            if pv.tail and not thin:
                threshold = (phases[i] / math.tau + float(self.pwa[i]) * 0.61803398875) % 1.0
                thin = threshold < pv.tail_blend
            row[0 if thin else 1].append(i)
        return buckets

    def _draw_contact_grains(self):
        """沿真实堆面保留很短的撞击纹理足迹, 补齐柱底横向采样空洞。"""
        band = self._contact_grains
        if (self.pn == 0 or self._mound_profile is None or self._last_impact_clock is None
                or self._mound_flow_clock - self._last_impact_clock > 0.05):
            band.clear()
            band.flush()
            return
        half = min(self._taper["t_in"], self._taper["t_in"] * FLOW_SHRINK_MIN + 1.0)
        height = 3.0 * max(getattr(self, "_splash_pixel", 1.0), self._R_inner / 140.0)
        apex, profile = self._mound_apex(), self._mound_profile
        outlet = 2.0 * self._neck_y - self._taper["y_bot"]
        diameter = 2.0 * self._R_inner
        uv_height = diameter + MOUND_CREST_MARGIN
        self._contact_grain_color.rgb = (1, 1, 1) if self._sand_material else self.sand_base
        cols = []
        for i in range(9):
            dx = half * (i / 4.0 - 1.0)
            y, free, thick = profile.column(dx, apex)
            y += self._lower_sand_bot
            cap = min(outlet, y + height * (1.0 - 0.35 * (dx / half) ** 2))
            cols.append((self._cx + dx, y - 0.15, cap, free and thick > 0.5))
        for i in range(8):
            x0, y0, t0, free0 = cols[i]
            x1, y1, t1, free1 = cols[i + 1]
            if not (free0 and free1 and t0 > y0 and t1 > y1):
                band.zero(i)
                continue
            pts = [x0, y0, x1, y1, x1, t1, x0, t0]
            if self._sand_material:
                u0, u1 = 0.5 + (x0 - self._cx) / diameter, 0.5 + (x1 - self._cx) / diameter
                b = self._lower_sand_bot
                band.set_uv(i, pts, (u0, (y0 - b) / uv_height, u1, (y1 - b) / uv_height,
                                     u1, (t1 - b) / uv_height, u0, (t0 - b) / uv_height))
            else:
                band.set(i, pts)
        band.flush()

    def _project_stream_contact(self):
        """仅补齐已发生碰撞的落点末端采样间隙, 不移动物理粒子或扩大沙柱。"""
        pv = self._pv
        n = self.pn
        if _np is None:
            pv._y = self.py[:n]
        else:
            pv.ny = self.py[:n]
            pv._y = None
        if (n == 0 or self._last_impact_clock is None
                or self._mound_flow_clock - self._last_impact_clock > 0.05):
            return
        band = 3.0 * max(getattr(self, "_splash_pixel", 1.0), self._R_inner / 140.0)
        ceiling = self.get_mound_top_y() + band
        xs, ys, *_ = self._mound_contact_curve()
        overlap = 0.15 * getattr(self, "_splash_pixel", 1.0)
        if _np is None:
            for i in range(n):
                if self.py[i] <= ceiling:
                    surface = self._mound_top_at(self.px[i])
                    if 0.0 < self.py[i] - surface <= band:
                        pv._y[i] = surface - overlap
        else:
            near = _np.flatnonzero(self.py[:n] <= ceiling)
            if near.size:
                surface = _np.interp(self.px[near], xs, ys)
                gap = self.py[near] - surface
                touch = (gap > 0.0) & (gap <= band)
                if touch.any():
                    display_y = self.py[:n].copy()
                    display_y[near[touch]] = surface[touch] - overlap
                    pv.ny = display_y

    def _draw_stream(self):
        # 区间打点: 从上一处打点到这里的整段 = **沙流之前的那些层**
        # (上球沙面/沙堆/表层标记/颈部沙柱)。沙流自己那段的结束点在下一个打点(`neck_enter`)。
        # 见 `_MarkCollector` —— 标签指的是**刚结束的那一段**, 不是"这个名字的函数耗时"。
        _pm = _PROF_MARK
        if _pm:
            _pm("stream_start")
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
                trail = trail * (1.0 - pv.tail_blend) + pv.tail_blend
                if TRAIL_SCALE != 1.0:
                    # A5: 只缩"由速度决定的那一段", `max(2.0, ...)` 的下限与 `tail_blend`
                    # 的偏置保持原样 —— 否则会连"慢粒子也该有的 2px"一起缩掉，
                    # 那就不是单变量了。
                    trail = max(2.0, abs(vys[index]) * trails[index] / motion_scale
                                * TRAIL_SCALE)
                    trail = trail * (1.0 - pv.tail_blend) + pv.tail_blend
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
        """**逐字镜像 `_draw_stream` 里那段拖尾算式**(含 `TRAIL_SCALE`)。

        ⚠️ **2026-10-10 之前它漏了 `TRAIL_SCALE`** ⇒ 两个后果:
          ① `tools/inspect_flow.py` 的 `max_trail_px` 是个**瞎的见证者** ——
             我把 `HG_TRAIL_SCALE` 从 0.8 换成 0.2, 它照样报 8.45, 害我一度以为那一臂空转;
          ② `verify_hourglass.py` 的 "individual short trails preserve granular detail"
             写死 `== 8` ⇒ 从 `TRAIL_SCALE` 进来的那天起就一直是红的(陈旧判据)。
        """
        scale = self._particle_motion_scale if motion_scale is None else motion_scale
        trail = max(2.0, abs(particle["vy"]) * particle["trail_time"] / scale * TRAIL_SCALE)
        return trail * (1.0 - self._pv.tail_blend) + self._pv.tail_blend

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
        # 区间打点(默认 None ⇒ 一次局部读 + 一次判空)。**入口这一笔是必须的**:
        # 打点记的是"距上一次打点"的时长, 所以入口那笔会把**它之前**的代码算进来
        # (读的时候忽略 `neck_enter` 那一格即可) —— 但它把后面每一格的起点钉住了。
        # ⚠️ 变量名**不能叫 `_m`** —— 本函数下面 `_m = (0.06 + 0.20 * _t + ...)` 是
        #    向量化分支里的**色调混合数组**, 会把打点函数覆盖掉, 随后 `if _m:` 就是
        #    "数组的真值" ⇒ `ValueError: truth value of an array ... is ambiguous`
        #    (2026-10-07 实测: `_render_golden --check` 当场 2/60 张就翻红, 抓得很快)。
        _nk_mark = _PROF_MARK
        if _nk_mark:
            _nk_mark("neck_enter")
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        length = max(1e-6, self._taper["y_bot"] - outlet)
        # 🔴 **2026-10-09: 纵向跨度必须钉在【出口】, 不能取 `side[-1][1]`。**
        #    `NECK_JOIN` 让沙柱下沿往下多探一段之后, `side[-1][1]` 比出口更低,
        #    于是 `span = top_y − bottom_y` 变大、映射 `y = top_y − t·span` 把**每一颗**
        #    颗粒都往下多推 `t·(出口 − 下沿)` ⇒ **整条颗粒层的纵向映射被拉长**(不是平移)。
        #    实测(复核实测, 冻结帧, flat 路径): 同帧差 **4349px, 其中出口以上 3627px**
        #    —— 而四条金标准臂**一条都不走 flat**, 所以这个回归当时没人看得见
        #    (现在 `CASES` 里有 `flat` 臂了)。
        #    改前 `side[-1][1]` 恒等于 `outlet` ⇒ 这一改让 flat 路径**逐位回到旧行为**;
        #    `ys/xs` 里那个更低的延伸节点只在 `y < outlet` 时才被 `half_w_at` 取到,
        #    而那时没有颗粒会映射过去。
        top_y, bottom_y = side[0][1], outlet
        span = max(1e-6, top_y - bottom_y)
        scale = self._particle_motion_scale
        twice_gravity = 900 * scale * scale
        source_limit_squared = (75 * scale) ** 2
        # 第一趟只求最深的投影深度, 第二趟再画 —— 原来给每个候选都分配一个
        # (distance, particle) 元组(峰值约 1800 次/帧)。两趟的候选顺序与 depth
        # 都与原实现一致, 所以 128 上限的截断结果也相同。按下标遍历 `_pv` 快照。
        pv = self._pv
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
            # ⚠️ **不要 `.tolist()`** —— 下游的向量化分支紧接着要 `np.array(_cand[:pool_len])`,
            #    来回一趟是白钱(候选峰值 ~1500 个)。留着 numpy 数组, 直接切片用。
            # ⚠️ 空的时候给**空数组**不是空 list —— 下游分支判的是 `_cand is not None`,
            #    空 list 会走进向量化分支再 `.dtype` 炸掉。
            _cand = _np.flatnonzero(_keep) if _keep.any() else _np.empty(0, dtype=_np.intp)
            if _keep.any():
                _mx = float(_d[_keep].max())
                if _mx > depth:
                    depth = _mx
        else:
            # ⚠️ **第一趟的兜底分支也要自己取 list** —— 原来 `ys_p/vys_p` 在函数开头无条件绑定,
            #    改成惰性属性后必须在**每个**要读的分支里取(2026-10-07 漏了这一处,
            #    闸门直接 `UnboundLocalError`)。第二趟的兜底分支另有一份, 别只补一处。
            ys_p = pv.y
            vys_p = pv.vy
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
        if _nk_mark:
            _nk_mark("neck_pass1")      # 第一趟: 扫**全部 pn 颗粒**找候选 + 最深投影
        ys = [y for _x, y in side]
        xs = [x for x, _y in side]

        # ★ **二分代替线性扫描**(2026-10-06 性能): `side` 的 y 是**单调下降**的,
        #   原来每个颗粒都把 26 段扫一遍(实测 `half_w_at` 250 次调用/帧 × 最多 25 次比较)。
        #   翻成升序后 `bisect_left` 一次定位, 语义与原式逐字相同(取夹住 y 的那一段线性插值)。
        ys_asc = ys[::-1]
        xs_asc = xs[::-1]

        def half_w_at(y):
            if y >= ys[0]:
                return xs[0]
            if y <= ys_asc[0]:
                return xs_asc[0]
            k = _bisect_left(ys_asc, y)          # 第一个 >= y 的下标
            y0, y1 = ys_asc[k], ys_asc[k - 1]    # y0 = 上端(大), y1 = 下端(小)
            x0, x1 = xs_asc[k], xs_asc[k - 1]
            if y0 - y1 < 1e-9:
                return x0
            return x0 + (x1 - x0) * (y0 - y) / (y0 - y1)

        t_in = max(1e-6, self._taper["t_in"])
        tone_scale = 5 / math.tau
        tone_tab = self._neck_tone_tab
        if tone_tab is None or len(tone_tab) != 32:
            tone_tab = self._neck_tone_tab = [
                tuple(self.sand_base[k]
                      + (self.sand_light[k] - self.sand_base[k]) * (j / 31.0)
                      for k in range(3)) for j in range(32)]
        pool = self._neck_grain_pool
        pool_len = len(pool)
        tone_last = self._neck_tone_last
        if tone_last is None or len(tone_last) != pool_len:
            tone_last = self._neck_tone_last = [None] * pool_len
        cx = self._cx
        base_r, base_g, base_b = self.sand_base
        light_r, light_g, light_b = self.sand_light
        count = 0
        if _cand is not None and pv.nwp is not None and not NECK_SCALAR:
            # ★ **向量化**(2026-10-07 性能): 池子 320 ⇒ 实测这一段的纯 Python 循环 **1.07ms/帧**。
            #   消融验证(池子 320→1): `_draw_neck_grains` 1.068 → 0.163ms, `redraw` 同步 −1.07;
            #   而属性写入那 ~300 次只值 0.06ms(桌面微基准 128 次 = 0.024ms)
            #   ⇒ **钱全在循环体的算术上** ⇒ 把算式交给 numpy, 写入照旧。
            #   等价性: 逐条照抄标量版(NaN 传播、向零截断、两端越界分支都对齐), 且由
            #   **逐像素比对**兜底(`tools/inspect_flow.py`, random.seed(23) 下必须 0 差异)。
            #   ⚠️ 下面的标量兜底路径**一字未动** —— `_cand is None` 时它是唯一出路。
            # `asarray` 对已经是 intp 的数组是空操作(不拷贝), 不必判断 dtype
            _idx = _np.asarray(_cand[:pool_len], dtype=_np.intp)
            _cnt = len(_idx)
            _t = (outlet - pv.ny[_idx]) / depth   # 0 = 刚出孔口, 1 = 流得最深的一颗
            _np.minimum(_t, 1.0, out=_t)
            _y = top_y - _t * span
            # `half_w_at` 的向量版: `searchsorted(side="left")` 与 `bisect_left` 同义,
            # 越界两支与退化段用 `where` 补回 —— 与标量版逐条对应。
            _ys_a = _np.asarray(ys_asc)
            _xs_a = _np.asarray(xs_asc)
            _k = _np.searchsorted(_ys_a, _y, side="left")
            _np.clip(_k, 1, len(ys_asc) - 1, out=_k)
            _y0 = _ys_a[_k]
            _y1 = _ys_a[_k - 1]
            _x0 = _xs_a[_k]
            _x1 = _xs_a[_k - 1]
            _den = _y0 - _y1
            _hw = _x0 + (_x1 - _x0) * (_y0 - _y) / _np.where(_den < 1e-9, 1.0, _den)
            _hw = _np.where(_den < 1e-9, _x0, _hw)
            _hw = _np.where(_y >= ys[0], xs[0],
                            _np.where(_y <= ys_asc[0], xs_asc[0], _hw))
            _sz = pv.nsz[_idx]
            _limit = _np.maximum(_hw - _np.where(_sz > 1, _sz, 0.5), 0.0)
            # ⚠️ 横向用**原始 half_w**, 只有夹取用 `_limit`(标量版就是两处不同口径)
            _x = cx + _np.clip((pv.nx[_idx] - cx) * (_hw / t_in), -_limit, _limit)
            # 亮端收窄的理由见下面标量分支里那段注释(与沙体材质量级对齐)
            _var = (pv.nwp[_idx] * tone_scale).astype(_np.int64)   # 与 `int()` 同为向零截断
            _np.minimum(_var, 4, out=_var)
            _var -= 2
            _m = (0.06 + 0.20 * _t + _var * 0.04) * 0.85
            _np.clip(_m, 0.0, 1.0, out=_m)
            _ji = (_m * 31.0 + 0.5).astype(_np.int64)
            # ⚠️ `pli` 是 **float** 数组, 直接当索引会 IndexError —— 先转布尔掩码
            _ji[pv.nli[_idx] != 0.0] = 26      # 0.85 * 31 ≈ 26.35 ⇒ 最近的量化档
            if _nk_mark:
                _nk_mark("neck_math")   # 第二趟: 候选的几何/色调算术
            _sink = _NECK_SINK
            if _sink is not None:
                # ★ **直通**: 不写记录器、也不再来一趟 283 颗的桶收集。
                #   几何量本来就是数组, 直接喂批处理。
                #   ⚠️ **不要在这条路上先 `tolist()`** —— 那三个 Python list 只有下面的
                #      else 分支会读, 而安卓走的就是这条路(每帧白建 3×320 个 float,
                #      另外还白算两遍 maximum/minimum)。见 `_probe_neck_equiv`。
                _sink(self, _cnt, _x,
                      _np.maximum(_y - 1.0, bottom_y), _np.minimum(_y + 1.0, top_y),
                      _ji, _sz)
                if _nk_mark:
                    _nk_mark("neck_sink")   # 喂 32 个色调批(含 argsort/bincount/逐批写+上传)
            else:
                _xl = _x.tolist()
                _bl = _np.maximum(_y - 1.0, bottom_y).tolist()
                _tl = _np.minimum(_y + 1.0, top_y).tolist()
                _jl = _ji.tolist()
                _sl = _sz.tolist()
                for _c in range(_cnt):
                    color, line = pool[_c]
                    rgb = tone_tab[_jl[_c]]
                    if tone_last[_c] != rgb:
                        color.rgb = rgb
                        tone_last[_c] = rgb
                    size = _sl[_c]
                    if line.width != size:
                        line.width = size
                    line.points = (_xl[_c], _bl[_c], _xl[_c], _tl[_c])
            count = _cnt
        else:
            # use_np 时只遍历候选(第一趟已算好); 标量兜底路径保持原样。
            # ⚠️ 六个 list 快照**只能在标量分支里取** —— `pv.x/y/...` 现在是**惰性属性**
            #    (见 `_FlowView` 顶部), 写在分支外会无条件把 list 建出来, 惰性就白做了
            #    (而那条路在安卓上根本走不到)。
            ys_p = pv.y
            vys_p = pv.vy
            sizes_p = pv.sz
            phases_p = pv.wp
            lights_p = pv.light
            xs_p = pv.x
            for i in range(pv.n):
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
                    ji = 26                      # 0.85 * 31 ≈ 26.35 ⇒ 最近的量化档
                else:
                    variation = int(phases_p[i] * tone_scale)
                    if variation > 4:
                        variation = 4
                    variation -= 2
                    # 化简: color.rgb = base + (light-base) * (mix * 0.85)
                    # (原来先 lerp 出 T=base+(light-base)*mix, 再 base+(T-base)*0.85 —— 同一个式子)
                    m = (tone_t + variation * 0.04) * 0.85
                    if m < 0.0:
                        m = 0.0
                    elif m > 1.0:
                        m = 1.0
                    ji = int(m * 31.0 + 0.5)
                color, line = pool[count]
                # Opaque preblend avoids Kivy's extra stencil passes for translucent wide lines.
                # 量化后查表 —— 且**同一槽位颜色没变就不写**(Kivy 属性赋值要过描述符 + 事件派发)
                rgb = tone_tab[ji]
                if tone_last[count] != rgb:
                    color.rgb = rgb
                    tone_last[count] = rgb
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
        # 🔴 **2026-10-10: 设备端末段自检钩子**(仅在存在 `tailcheck` 标记文件时生效)。
        #    用户要求「必须在安卓上通过测试才行」, 而墙钟截图追一个 0.2~0.4s 的窗口追不到
        #    (连试 5 次) ⇒ 换成确定性自检: 只推进物理、不渲染, 结果进 logcat 与
        #    `<app>/tailcheck.out`。无标记文件时**零开销、零行为变化**。
        try:
            import _tail_selfcheck
            print('TAILCHECK import ok  flag=%s'
                  % (_tail_selfcheck._app_dir(),))
            if _tail_selfcheck.maybe_run(self.hourglass):
                return
        except ImportError as _exc:
            print('TAILCHECK import FAILED: %r' % (_exc,))
        except Exception as _exc:
            print('TAILCHECK hook failed: %r' % (_exc,))
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

        ## 2026-10-07 重写 —— 照抄 `DanZhu` 的做法(同一台 Y700 TB323FU, 那边实测 165fps)

        用户指出「我这台是 165Hz 屏, 而你这个 app 只有 120」。查 `DanZhu` 的
        `danzhu/platform/device.py:_request_android_high_hz()` —— 他们在**同一台设备**上
        跑出 `平均 165.0 / 1%Low 123~129`, 而他们比我们多做了三件事, **缺一不可**:

        1. **在 UI 线程上申请**(`android.runnable.run_on_ui_thread`)。
           `Window.setAttributes` / `setFrameRate` 是**窗口属性**, 从 Kivy 的 Python 线程
           直接调**不保证生效**(被静默忽略最坏 —— 我们那个 `except Exception` 正好会把
           异常也吞掉, 于是日志上看着"requested 165.0Hz"、实际一直是 120)。
        2. **`Window.setFrameRate(hz)`(API 30+)** —— 这是现代那套**按窗口**要帧率,
           自适应刷新率的机器上比 `LayoutParams` 管用得多。
        3. **先读系统上限 `Settings.System.PEAK_REFRESH_RATE`**, 只申请"屏幕支持 ∩ 系统允许"
           的最高档 —— 顺手把"到底是系统没开还是我们要不到"这件事**记进日志**。

        另外**档位要在当前分辨率下挑**(165 档可能只存在于某个分辨率), 所以先按
        `getPhysicalWidth/Height` 过滤当前分辨率, 取不超过上限的最高档; 一个都不满足时
        宁可降档(照抄他们的选择: 不去绕系统设定)。

        ⚠️ 与 `maxfps=0` 是**两件事**: maxfps 是"我们自己不设上限", 这一步是"让系统别给低档"。
        ⚠️ 档位切换是**异步**的, 紧接着回读可能还是旧值 —— 真正算数的是基准日志里的
           `refresh_hz`(基准开始时才读) 与 `refresh_modes=`(这里写的期望值)。
        """
        global REFRESH_INFO
        try:
            from jnius import autoclass
            from android.runnable import run_on_ui_thread
            sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            window = activity.getWindow()
            display = window.getWindowManager().getDefaultDisplay()
            now = float(display.getRefreshRate())
            modes = list(display.getSupportedModes())
            cur = display.getMode()
            cw, ch = int(cur.getPhysicalWidth()), int(cur.getPhysicalHeight())
            same = [m for m in modes
                    if int(m.getPhysicalWidth()) == cw and int(m.getPhysicalHeight()) == ch]
            cands = same or modes

            def _hz(m):
                return float(m.getRefreshRate())

            screen_cap = max((_hz(m) for m in cands), default=0.0)
            sys_cap = _peak_refresh_cap(activity)          # 读不到返回 0 = 未知
            caps = [c for c in (screen_cap, sys_cap) if c > 0.0]
            target = min(caps) if caps else screen_cap
            # 用户/诊断设的上限(见 `_refresh_cap`): 0 = 不设 ⇒ 与今天一字不差。
            if REFRESH_CAP > 0.0:
                target = min(target, REFRESH_CAP)
            at_or_below = [m for m in cands if _hz(m) <= target + 0.5]
            chosen = (max(at_or_below, key=_hz) if at_or_below else
                      min(cands, key=_hz) if cands else None)
            mode_id = int(chosen.getModeId()) if chosen is not None else 0
            mode_hz = _hz(chosen) if chosen is not None else 0.0
            REFRESH_INFO = ("modes=%s now=%g screen_cap=%g sys_cap=%g cap=%g "
                            "want=%g#%d res=%dx%d"
                            % (";".join("%dx%d@%g#%d" % (m.getPhysicalWidth(),
                                                         m.getPhysicalHeight(),
                                                         _hz(m), m.getModeId())
                                        for m in modes) or "n/a",
                               now, screen_cap, sys_cap, REFRESH_CAP,
                               mode_hz, mode_id, cw, ch))
            print("Refresh modes: " + REFRESH_INFO)

            if mode_hz <= 0.0:
                return
            if mode_hz <= now + 0.5 and sys_cap > 0.0 and sys_cap <= now + 0.5:
                print(f"Refresh rate: already at panel max ({now:.1f}Hz)")
                return

            @run_on_ui_thread
            def _apply(win, hz_, mid, api):
                # ⚠️ 整个申请**必须在 UI 线程**上做(见 docstring 第 1 条)。
                if api >= 30:
                    try:
                        win.setFrameRate(float(hz_))
                    except Exception as exc:
                        print(f"setFrameRate failed: {exc}")
                if mid:
                    try:
                        attrs = win.getAttributes()
                        attrs.preferredDisplayModeId = int(mid)
                        attrs.preferredRefreshRate = float(hz_)
                        win.setAttributes(attrs)
                    except Exception as exc:
                        print(f"setAttributes failed: {exc}")

            _apply(window, mode_hz, mode_id, sdk)
            print("Refresh rate: requested %.1fHz (was %.1fHz, mode #%d)"
                  % (mode_hz, now, mode_id))
        except Exception as exc:
            REFRESH_INFO = "error=%s" % (exc,)
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
                # 🔴 **2026-10-10 用户要求: 「我是 1080p 显示器, 高度最高可以做到 900 像素吗」**
                #    ⇒ 加 `--size 450x900`(或 `--size 400x900`, 运行时任意)。**默认仍是 400x800**,
                #    所以金标准与既有探针的基线**不受影响**。
                #    ⚠️ 窗口是**手机竖屏比例**(宽:高 ≈ 0.5), 想让高到 900 就得宽到 450 ——
                #      只加高不加宽会改比例, 几何会跟着变(`R = min(宽约束, 高约束)`)。
                #      1080p 屏上 450x900 放得下(还留 180px 给标题栏/任务栏)。
                #    ⚠️ 别把窗口设成比屏幕还高 —— 那样布局拿到的可用高度反而变小(项目踩过)。
                #    ⚠️ **2026-10-10 实测踩坑: 必须读 `sys.orig_argv`, 不能读 `sys.argv`** ——
                #      **Kivy 会把不认识的命令行参数从 `sys.argv` 里摘掉**(`--size` 就是),
                #      读 `sys.argv` 会**静默拿不到**(实测: app 正常启动、但那行 print 从没出现)。
                #      `sys.orig_argv` 是进程原始命令行, 框架动不到。
                _argv = getattr(sys, "orig_argv", None) or sys.argv
                _sz = None
                for _i, _a in enumerate(_argv):
                    if _a == "--size" and _i + 1 < len(_argv):
                        _sz = _argv[_i + 1]
                    elif _a.startswith("--size="):
                        _sz = _a.split("=", 1)[1]
                if "--landscape" in _argv:
                    Window.size = (1000, 600)
                elif _sz:
                    _w, _h = (int(v) for v in _sz.lower().split("x"))
                    Window.size = (_w, _h)
                    print("窗口 = %dx%d (命令行 --size)" % (_w, _h))
                else:
                    Window.size = (400, 800)
            except Exception:
                pass
        Window.clearcolor = (*hex_rgb(BG_COLOR), 1)

        self.hourglass = HourglassWidget(size_hint=(1, 1))
        cfg = self.hourglass.load_config()
        if isinstance(cfg.get('duration'), (int, float)) and cfg['duration'] > 0:
            self.hourglass.duration = cfg['duration']
            # ⚠️ **尺寸还没出来就别建** —— `build()` 跑的时候布局还没跑, Widget 还是 Kivy 的
            #    默认 100x100, 为它整块重建画布要 ~70ms(实测), 而画面上什么都还没有。
            #    这里只**记下周期**(`neck_w` 从 duration 派生 ⇒ 尺寸真出来时会自己算对);
            #    真实尺寸一到, `_on_size` 那条路会建。
            if self.hourglass.width >= 150.0 and self.hourglass.height >= 150.0:
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
        if not SAND_MATERIAL_FORCED and os.environ.get("HG_SAND_GRAIN") is None:
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

        root = BoxLayout(orientation="vertical", spacing=dp(2),
                         padding=[dp(8), dp(2), dp(8), dp(2)])

        # 顶部色块
        # ★ 高度(用户 2026-10-06: 「还能继续压缩下高度」): 50 -> 42
        #   口径是"**字要占满按钮**" —— 改前文字墨迹只占按钮高的 24~28%, 框里空。
        #   dp(42) 配 sp(18) ⇒ 墨迹 ~16px = 38%(正常按钮区间 35~45%)。
        top_colors = BoxLayout(orientation="horizontal", size_hint=(1, None),
                               height=dp(46), spacing=dp(4))
        self.color_btns = []
        for name, base, dark, light in SAND_PRESETS:
            btn = Button(text=name, font_size=sp(16), background_normal="",
                         background_color=(*hex_rgb(base), 1), color=fg_for(base))
            btn.bind(on_press=lambda inst, b=base, d=dark, l=light, n=name:
                     self.on_color(b, d, l, n))
            top_colors.add_widget(btn)
            _autofit_font(btn, sp(16))
            self.color_btns.append((name, btn))
        root.add_widget(top_colors)

        # 倒计时
        # 行高 40 → 34 → **按纹理算**(2026-10-07, 用户: 「倒计时的文字是不是可以往上挪一些,
        # 留下更多空间给沙漏」)。平板上 **R 是高度受限**(宽只用 62%), 所以纵向省下来的
        # 每 4px 都换成 R +1px —— 这是现在唯一还够得着的"让球更大"的旋钮。
        #
        # 实测(kv 纹理 208×96, 笔画只占 48px): 文字**在行框里垂直居中**, 行框缩多少,
        # 笔画只上移一半; 而行框小到一定程度**会把字切掉**(14dp 时笔画只剩 37px)。
        # 不裁的条件只与纹理高 T 有关, 与 density/fontscale 无关 —— 推导:
        #   纹理上边 = 行框顶 + (S−T)/2;  笔画在纹理内 [0.323T, 0.823T]
        #   上不出框: (S−T)/2 + 0.323T ≥ 0  ⇒ S ≥ 0.354T
        #   下不出框: (S−T)/2 + 0.823T ≤ S  ⇒ S ≥ 0.646T   ← 这条更紧
        # ⇒ 取 **height = 0.70·T**(留 5% 余量)。这样安卓"字体大小"设置把 T 放大时,
        #   行框自动跟着长, 不会切字; T=96 时得 67px ≈ 24dp, 与写死 dp(24) 几乎一致。
        # ⚠️ 改这一行请跑 `tools/_probe_row_ink.py`(它把**当前**笔画高与"行框开到 40dp 时的
        #    笔画高"对比 —— 矮了就是被切了; 判据自带正负对照, 见其文件头)。
        self.time_label = Label(
            text=f"{self.hourglass.duration:.0f}/{self.hourglass.duration:.0f}秒",
            font_size=sp(24), bold=True, size_hint=(1, None), height=dp(24),
            color=(0.2, 0.2, 0.2, 1))
        self.time_label.bind(
            texture_size=lambda inst, ts: setattr(inst, "height", max(dp(24), ts[1] * 0.70)))
        root.add_widget(self.time_label)

        # 沙漏画布
        root.add_widget(self.hourglass)

        # 底部控件
        # ★ 高度: 58 -> 46(同上口径)。dp(46) 在设备上 ≈7.3mm, 触摸区仍合理。
        bottom = BoxLayout(orientation="horizontal", size_hint=(1, None),
                           height=dp(50), spacing=dp(6))
        self.duration_btn = Button(text=_fmt_duration(self.hourglass.duration),
                                   size_hint=(None, 1), width=dp(82),
                                   font_size=sp(17), bold=True,
                                   background_normal="",
                                   background_color=(0.769, 0.682, 0.557, 1),
                                   color=POPUP_TEXT)
        _autofit_font(self.duration_btn, sp(17), pad=10.0)
        self.duration_btn.bind(on_press=self.on_duration_picker)
        bottom.add_widget(self.duration_btn)
        self.sound_btn = Button(text="沙沙声",
                                size_hint=(None, 1), width=dp(74), font_size=sp(17),
                                background_normal="",
                                background_color=(*POPUP_GOLD_SEL[:3], 0.92),
                                color=POPUP_TEXT)
        _autofit_font(self.sound_btn, sp(17), pad=10.0)
        self.sound_btn.bind(on_press=self.on_sound_picker)
        bottom.add_widget(self.sound_btn)
        self._benchmark_runner = None
        self._benchmark_results = []
        self._benchmark_cancelled = False
        self._benchmark_popup = None
        self._benchmark_area = BenchmarkHoldArea(self._open_dev_menu, size_hint=(None, 1))
        # 长按处印出版本号: 隐藏入口总得让人找得到该按哪儿。
        # BenchmarkHoldArea 是裸 Widget、不做子控件布局, 得手动跟着它铺满。
        # ⚠️ 字号 2026-10-06 由 **sp(11) → sp(22)**(用户: 「把这个版本号的文字大幅增加,
        #    当前太小了」)。这不是美化: 它是**隐藏入口唯一的可见提示**(长按 3 秒进开发者
        #    菜单), sp(11) 在 360dp 机型上小到看不见、还会被挤成三行。**α 保持 0.38 不动**
        #    (调淡/调深是另一个决定, 归用户)。
        hold_label = Label(text=f"v{APP_VERSION}", font_size=sp(15),
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
                                font_size=sp(17), bold=True,
                                background_normal="",
                                background_color=(0.353, 0.620, 0.243, 1), color=(1, 1, 1, 1))
        self.start_btn.bind(on_press=self.on_toggle)
        _autofit_font(self.start_btn, sp(17), pad=6.0)
        bottom.add_widget(self.start_btn)
        reset_btn = Button(text="重置", size_hint=(None, 1), width=dp(74), font_size=sp(20))
        _autofit_font(reset_btn, sp(17), pad=6.0)
        reset_btn.bind(on_press=self.on_reset)
        self._reset_btn = reset_btn
        bottom.add_widget(reset_btn)

        # ---- 底栏宽度分配: 版本/长按区按"版本文字的实测宽"保底, 四个按钮不够时**等比让出** ----
        # 为什么必须有这一段: 四个按钮是**定宽**且底栏没有弹性子控件,
        #   `BoxLayout` 的算法是 `stretch_space = max(0, width - 定宽之和)` —— 空间不够时
        #   **不压缩、直接往右溢出**(Kivy 2.3.1 `boxlayout.py:226` + `:255-277`)。
        #   硬需求 = 82+74*3(按钮) + 6*4(间距) + 8*2(根布局 padding) = **344dp**;
        #   而 360dp 机型只剩 16dp 给版本区(sp(22) 的 "v1.152" 要 ~65dp ⇒ 必然折行/出屏),
        #   320dp 机型连放都放不下 ⇒ 「重置」右缘直接出屏。
        # ✅ 保底 = 版本文字**实测宽**(不是猜的固定值: 换字体/换密度/版本号变长都自适),
        #    不够的部分由四个按钮**同比**让出 ⇒ 宽窗(≥保底+344dp)下按钮**维持原宽, 零改动**。
        #    (用户口径 2026-10-06: 「触摸区差不多合理就行」—— 所以只保证"不出屏 + 版本号一行",
        #     不去做"精确 16dp 触摸目标"那套。)
        self._bottom_btns = (self.duration_btn, self.sound_btn, self.start_btn, reset_btn)
        self._bottom_base_w = (dp(82), dp(74), dp(74), dp(74))

        def _fit_bottom_widths(*_args):
            if bottom.width <= dp(1):
                return                       # 还没布局, 等下一次宽度变化
            # ⚠️ 量"需要多宽"必须**先解掉折行约束**: 标签的 `text_size` 被 `_fit_hold_label`
            #    钉成控件尺寸, 此时 `texture_size` 是**折行后**的宽度, 只会等于控件宽 ——
            #    拿它当 need 会自我满足, 永远算不出"装不下"(第一版就是这么写的, 实测 360dp
            #    上版本区仍只有 74px)。置 (None,None) 才拿到未折行的自然宽度。
            hold_label.text_size = (None, None)
            hold_label.texture_update()
            need = hold_label.texture_size[0] + dp(6)    # 刚够一行 + 一点余量
            gap = dp(6) * 4
            avail = bottom.width - gap
            base = sum(self._bottom_base_w)
            scale = 1.0 if avail >= base + need else max(0.0, (avail - need) / base)
            for btn, w in zip(self._bottom_btns, self._bottom_base_w):
                btn.width = w * scale
            self._benchmark_area.width = max(0.0, avail - base * scale)
            hold_label.text_size = self._benchmark_area.size   # 还给折行约束
            hold_label.texture_update()
            if scale < 1.0:
                bottom.do_layout()           # 改宽度不会自动重排

        bottom.bind(width=_fit_bottom_widths)
        Clock.schedule_once(_fit_bottom_widths, 0)
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

        # 中段控件先攒进列表: **装不装得下要等全建完才知道**, 而两种情况的**控件树结构不同**
        # (见弹窗创建之后那个分支, 注释里写了为什么不能一律套 ScrollView)。
        mid_widgets = []

        def _mid(w):
            mid_widgets.append(w)
            return w

        # 预创建 mult_btns/preview_label,避免 lambda 闭包延迟绑定
        # (Android Kivy 2.3.0 对 late binding 时序敏感,曾导致点周期按钮闪退)
        mult_btns = {}
        preview_label = Label(
            text=f"计时时间：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）",
            size_hint=(1, None), height=dp(34),
            color=POPUP_TEXT, font_size=sp(18))

        # --- 基础时间标题 ---
        base_title = Label(text="基础时间:", size_hint=(1, None), height=dp(22),
                           color=POPUP_TEXT, font_size=sp(15),
                           halign="left", valign="middle")
        base_title.bind(size=lambda inst, val: setattr(inst, 'text_size', (val[0], val[1])))
        _mid(base_title)

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
        _mid(base_grid)

        # --- 倍数按钮 (两行 BoxLayout, 不用 GridLayout 避免 Android 兼容问题) ---
        MULTIPLIERS = [1, 2, 3, 5, 10, 20, 30, 50, 70, 100]
        mult_title = Label(text="倍数:", size_hint=(1, None), height=dp(22),
                           color=POPUP_TEXT, font_size=sp(15),
                           halign="left", valign="middle")
        mult_title.bind(size=lambda inst, val: setattr(inst, 'text_size', (val[0], val[1])))
        _mid(mult_title)
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
            _mid(row)

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
        _mid(slider_row)
        # label 用默认参数固定;滑块值变化不触碰任何按钮高亮(不同步)
        slider.bind(value=lambda inst, v, st=state, pv=preview_label,
                    ml=mult_label: self._on_slider_moved(v, st, pv, ml))

        # --- 预览 (预创建,此处 add 到正确位置) ---
        _mid(preview_label)

        # --- 运行中警告 ---
        if self.hourglass.running:
            warn_label = Label(text="修改计时设置将重置当前进度",
                               size_hint=(1, None), height=dp(26),
                               color=(0.85, 0.45, 0.15, 1), font_size=sp(14))
            _mid(warn_label)

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
        # ⚠️ btn_row **不在这里 add** —— 它挂哪儿取决于"装不装得下"(见弹窗创建后的分支)

        popup = _SandBgPopup(title="计时设置", content=content,
                             size_hint=(0.88, None), height=dp(460),
                             auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(19)
        popup.separator_color = (*POPUP_GOLD_SEL[:3], 0.25)
        popup.title_color = (1, 1, 1, 1)
        # ================= F2: 两种结构, 按"装不装得下"选 =================
        # 触发条件(实测): 窗口长边 < 弹窗自然高(本弹窗 **493dp**) ⇒ 弹窗溢出、居中的话
        # 「取消/确定」被推到屏幕下缘之外, 弹窗干不成它唯一的活(分屏/自由窗口必现)。
        #  ① 装得下 ⇒ **原结构一字不改**(逐像素 0 差异 = 回归闸门)。
        #  ② 装不下 ⇒ 三段式: 中段搬进 ScrollView 可滚动, 「取消/确定」钉在 footer, 高度夹到
        #     max(W,H)*0.85 ⇒ 标题与两个按钮**必定在屏内**(已成闸门: tools/_r37_f2_fit.py)。
        # 🔴 **为什么必须分两种结构, 而不是永远挂 ScrollView** —— Kivy 的 `<Label>` 把文字纹理
        #    摆在 `int(center - texture_size/2)`, 这个 int() 取在**父坐标系**里 ⇒ 把标签挪进
        #    更深一层的父控件(ScrollView 里的 rows)会改变截断相位: 文字整体偏 0.2px, 字形边缘
        #    的抗锯齿像素全变。实测卡片内 17802px 有差、最大通道差 121, 而同时
        #    **文字纹理 md5 完全相同、控件窗口坐标小数点后 4 位完全相同** —— 差的只有那次 int()。
        #    ⇒ 把控件树逐层搬进 ScrollView, 就**不可能**与原版逐像素一致 ⇒ 常见形态必须走①。
        natural = (sum(w.height for w in mid_widgets) + content.spacing * len(mid_widgets)
                   + btn_row.height + content.padding[1] + content.padding[3])
        if natural + dp(85) <= max(Window.width, Window.height):
            for w in mid_widgets:                       # ① 原结构
                content.add_widget(w)
            content.add_widget(btn_row)
            content.size_hint_y = None
            content.pos_hint = {'top': 1}
            content.bind(minimum_height=content.setter('height'))
            content.bind(minimum_height=lambda inst, val: setattr(popup, "height", val + dp(85)))
        else:
            rows = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
            rows.bind(minimum_height=rows.setter("height"))
            for w in mid_widgets:                       # ② 中段可滚动
                rows.add_widget(w)
            scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False)
            scroll.add_widget(rows)
            content.add_widget(scroll)
            content.add_widget(btn_row)                 # footer 留在 content(不随中段滚)
            # ⚠️ content 仍是 **定高 + 顶部对齐**(原实现那两行): 弹窗 chrome 的真实占位比
            #    dp(85) 小 ~24px, 原实现靠它把差额留在卡片**底部**; 改成 size_hint_y=1 会让
            #    footer 连同按钮整体下移 24px(实测确定键文字 bbox 从 rows1480-1507 掉到 1504-1531)。
            content.size_hint_y = None
            content.pos_hint = {'top': 1}

            def _fit_popup_height(*_args):
                # ⚠️ 高度**显式算**: 不能拿 content.minimum_height —— `ScrollView` **不参与**
                #    minimum_height(它的 minimum_height 恒为 0), 拿它定高会让弹窗只剩 footer
                #    那一截。= 中段自然高 + footer + content 自身 padding/spacing + 标题栏余量。
                mid = (rows.minimum_height + btn_row.height
                       + content.padding[1] + content.padding[3] + content.spacing)
                avail = max(Window.width, Window.height) * 0.85 - dp(85)
                content.height = min(mid, avail)
                popup.height = content.height + dp(85)
            rows.bind(minimum_height=_fit_popup_height)
            Clock.schedule_once(_fit_popup_height, 0)

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
        preview_label.text = f"计时时间：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）"

    def _on_mult_picked(self, val, mult_btns, state, preview_label):
        state["mult"] = val
        active_color = POPUP_GOLD_SEL
        inactive_color = POPUP_UNSEL_MULT
        for m, btn in mult_btns.items():
            sel = (m == val)
            btn.background_color = active_color if sel else inactive_color
            btn.color = POPUP_TEXT
        preview_label.text = f"计时时间：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）"

    def _on_slider_moved(self, v, state, preview_label, mult_label):
        """对数滑杆:10^(3t) 向上取整;只更新 state/预览/×N 标签,不动按钮高亮。"""
        m = _mult_from_slider(v)
        if m == state['mult']:
            return
        state['mult'] = m
        mult_label.text = f"×{m}"
        total = state['base'] * m
        preview_label.text = f"计时时间：{_fmt_duration(total)}（{total:.0f}秒）"

    def _pick_duration(self, sec, popup):
        popup.dismiss()
        # 🔴 **操作提示音只挂在这里(= 点「确定」那一下), 且只在周期真的变了时播** ——
        #    用户明确要求: 弹窗里调基础时间/倍数/拖滑杆**一律不要语音**
        #    (滑杆那条还会逐帧触发)。`set_duration` 不变时返回 False ⇒ 天然不播。
        if self.hourglass.set_duration(sec):
            self.hourglass.save_config(self._selected_color_name())
            keys = self.hourglass._voice_keys_duration(self.hourglass.duration)
            if keys:
                self.hourglass._voice_say(keys)
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

        2026-10-04 用户要求: 原来沙子材质是「平色/淡/标准/浓」四档按钮, 改成连续滑块。
        ⚠️ **不要照这句去菜单里找"玻璃反光滑块"** —— 那句 2026-10-04 的用户要求里
        确实提过"并加一个玻璃反光滑块", 但**那个滑块当天之后就被整块删掉了**
        (`main.py:692` 的 `self._dev_sliders` 现在只装了一个滑杆, r23-1号 在设备上
        逐个控件点过、确认菜单里没有它)。旧注释一直留在这儿, 会让人以为它还在。

        ⚠️ 沙子浓度**不能每帧烘正式材质**: 实测 512² 生成 17.6ms + 上传 ⇒ 拖动必卡。
        所以拖动中只烘 `SAND_PREVIEW_SIZE`(128², 4.2ms) 的低分预览, 停手 0.35s 才烘正式版。
        玻璃反光没这个问题(只重建十几条静态 Line), 但**落盘**一样要等停手 —— 否则拖动
        每帧写一次配置文件。
        """
        if self._dev_popup is not None or self._benchmark_active():
            return
        hg = self.hourglass
        content = BoxLayout(orientation="vertical", spacing=dp(6),
                            padding=[dp(16), dp(8), dp(16), dp(12)])
        # 中段控件先攒进列表, 结构与挂法见本函数末尾那个"装不装得下"分支(同周期弹窗)。
        mid_widgets = []

        def _mid(w):
            mid_widgets.append(w)
            return w

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
        _mid(sand_row)
        sand_slider = make_slider(min(100.0, SAND_MATERIAL_GRAIN / SAND_GRAIN_MAX * 100.0))
        _mid(sand_slider)

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

        _mid(Widget(size_hint=(1, None), height=dp(8)))

        # ---- 沙体颗粒 六档 (A-F, 出厂默认 D) ----
        # 换档要重烘一张 512² 材质(约 17ms) ⇒ **只能按键触发, 不能做成连续滑块**。
        def pick_grain(lb):
            if hg.set_grain_level(lb):
                refresh_grain(lb)
                hg.save_config(self._selected_color_name())

        grain_box, refresh_grain, grain_btns = make_levels(
            "沙体颗粒", [lb for lb, _g, _c in SAND_GRAIN_LEVELS],
            current_grain_level(), pick_grain,
            fmt=lambda lb: "颗粒 %.1f 倍粗" % _grain_level(lb)[1])
        _mid(grain_box)

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
        _mid(rough_box)
        self._dev_level_btns = {"grain": grain_btns, "rough": rough_btns}


        _mid(Widget(size_hint=(1, None), height=dp(6)))
        bench = Button(text="性能测试", font_size=sp(16), bold=True, background_normal="",
                       background_color=POPUP_CONFIRM, color=POPUP_TEXT_WHITE,
                       size_hint=(1, None), height=dp(50))
        bench.bind(on_press=lambda *_: (self._close_dev_menu(), self.on_benchmark()))
        _mid(bench)

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
        # ⚠️ close 在这一步**不挂父** —— 挂哪儿取决于装不装得下(见末尾分支)

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
        # ================= F2: 两种结构, 按"装不装得下"选(同周期弹窗) =================
        # 本菜单自然高 **422dp**; 窄盒(长边 < 422dp, 分屏/自由窗口)里「确定」会被推出屏外。
        #  ① 装得下 ⇒ **原结构一字不改**(逐像素 0 差异); ② 装不下 ⇒ 中段滚动 + footer 钉底。
        # 为什么必须分两种结构(不能一律套 ScrollView)见周期弹窗那段长注释。
        natural = (sum(w.height for w in mid_widgets) + content.spacing * len(mid_widgets)
                   + close.height + content.padding[1] + content.padding[3])
        if natural + dp(78) <= max(Window.width, Window.height):
            for w in mid_widgets:                       # ① 原结构
                content.add_widget(w)
            content.add_widget(close)
            # ⚠️ 本菜单**没有** pos_hint(原实现就没有): 内容贴 wrapper 的**底部**摆。
            #    照抄周期弹窗那句 `pos_hint={'top':1}` 会让内容整体上移 17px(实测逐像素对不上)。
            content.size_hint_y = None
            content.bind(minimum_height=content.setter('height'))
            content.bind(minimum_height=lambda inst, val: setattr(popup, "height", val + dp(78)))
        else:
            rows = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
            rows.bind(minimum_height=rows.setter("height"))
            for w in mid_widgets:                       # ② 中段可滚动
                rows.add_widget(w)
            scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False)
            scroll.add_widget(rows)
            content.add_widget(scroll)
            content.add_widget(close)                   # 「确定」钉在 footer
            content.size_hint_y = None
            content.pos_hint = {'top': 1}
            # 高度显式算(ScrollView 不参与 minimum_height) + 夹到 max(W,H)*0.85。
            def _fit_dev_height(*_args):
                mid = (rows.minimum_height + close.height
                       + content.padding[1] + content.padding[3] + content.spacing)
                avail = max(Window.width, Window.height) * 0.85 - dp(78)
                content.height = min(mid, avail)
                popup.height = content.height + dp(78)
            rows.bind(minimum_height=_fit_dev_height)
            Clock.schedule_once(_fit_dev_height, 0)

        popup.pos_hint = {"center_x": 0.5, "y": 0.015}
        # 🔴🔴 **2026-10-06 用户实测: 「让你把沙漏设置做个预览, 结果你把预览窗口直接灰化了,
        #     那预览个毛啊」** —— 这条是**真的, 而且是这个界面唯一的用途**:
        #     它贴底摆就是为了把上半屏的沙漏留出来当预览, 而 `ModalView` 的默认遮罩
        #     (`overlay_color = (0,0,0,0.70)`, 铺满整个宿主) **把上面那块沙压到只剩 30% 亮度**:
        #     实测沙色 (217,164,97) → **(66,50,29)** —— 你要调的正是沙面起伏/颗粒,
        #     而沙被盖在一层 70% 的黑底下 ⇒ **等于没有预览**。
        # ⚠️ 前面两次修都修错了属性: 试的是 `background_color=(0,0,0,0)` 与 `background=""`,
        #    **那两个只影响控件自身背景, 根本不管遮罩** ⇒ 所以一直"关不掉"。
        #    管遮罩的是 **`overlay_color`**, 而全仓库从来没设过它。
        # ✅ 现按**本文件上方那段注释里原本就写明的意图**落地: "留 10% 只为跟卡片分层"。
        #    桌面实测 α 是**线性连续**的: 0→(253,246,227) 与无弹窗完全一致 /
        #    0.10→(228,222,205) / 0.70(原值)→(76,74,69)。取 0.10 = 保留一丝分层感,
        #    沙仍然看得清。⚠️ 只改**这一个弹窗** —— 周期/音效/完成那三个的压暗是**要的**
        #    (它们不是预览界面, 压暗是为了把注意力收到卡片上), 一个都不动。
        popup.overlay_color = (0, 0, 0, 0.10)
        # ⚠️ **原打算**把这个界面的模态遮罩调到几乎透明（当"预览"用, 留 10% 只跟卡片分层）。
        #    🔴 **但这个打算从未落地** —— 全仓库**没有任何地方设 `overlay_color`**
        #    （2026-10-06 实查: `grep -n overlay_color main.py` ⇒ **0 处**;
        #     r34-1号 设备实测遮罩 alpha 就是 **Kivy 默认的 0.70**:
        #     沙色 (217,164,97) → (66,50,29)）。
        #    ⇒ **这一段原先写的"调到几乎透明"是假的, 别按它推理**;
        #    下面那条「已知未解决」才是实情。（同"玻璃反光滑块"那类注释与代码不符。）
        # 🔴 **2026-10-06 更正: "关不掉"是错的 —— 一行就能关。**
        #    之前两次试的是 `background_color=(0,0,0,0)` 与 `background=""` —— **那两个确实不管遮罩**
        #    (`background_color` 只影响控件自身的背景, Kivy 文档明说; 实测顶栏金沙 (54,41,24)
        #     vs 正常 (217,163,96) 那两条就是这个原因)。
        #    管遮罩的是 **`ModalView.overlay_color`(默认 (0,0,0,0.7))**,
        #    而全仓库**从来没有一处设过它**(r34-1号 实查 `grep -n overlay_color main.py` = 0 处)。
        #    桌面实测(`tools/_probe_overlay_color.py`, 取沙漏之外的背景采样点):
        #      无弹窗 (253,246,227) | **α=0 → (253,246,227) 完全恢复** | 0.10 → (228,222,205)
        #      | 0.25 → (190,184,170) | 0.40 → (152,148,136) | 0.55 → (114,111,102)
        #      | 0.70(默认) → (76,74,69)
        #    ⇒ **线性、单调、连续可调。**
        # ⚠️ **但 α 取多少是观感决定 ⇒ 归用户, 尚未定** —— 本函数**先不动**,
        #    等用户挑完 α 再加一行 `popup.overlay_color = (0, 0, 0, α)`。
        #    (用户的待定项里已有一条就是"弹窗后面那层整窗暗底"。)
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
            # 操作提示音: 「提示音设定为沙沙声」。**只在真的切换成功时播** ——
            # 同名连点 `_set_sound` 返回 False ⇒ 天然不重复播。
            # ⚠️ 选「无声音」时**也要念** —— 那是最需要确认的一种(此时没有声音可听)。
            keys = self.hourglass._voice_keys_sound(label)
            if keys:
                self.hourglass._voice_say(keys)
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
        # 操作提示音: 只说颜色名(「金沙」)。词库没就绪时 `_voice_say` 静默跳过。
        keys = self.hourglass._voice_keys_color(name)
        if keys:
            self.hourglass._voice_say(keys)

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


# ---- 飞溅层批处理渲染器(外部评审 youhua1.md §3.1) -----------------------------
# ⚠️ 与沙流那套一样: **默认不装**(桌面 GL 余量大、测不出; 且这是新东西, 要能随时退回)。
#   要验就 `HG_SPLASH_RENDERER=batch`; 装不上或 shader 编译失败**自动回退**原路径,
#   不给出沙制造风险(`available()` 在建画布**之前**探测, 因为 shader 的报错发生在
#   画布构建时、外面 try 包不住)。
SPLASH_RENDERER = os.environ.get("HG_SPLASH_RENDERER", "batch")   # rect | batch

# 颈部颗粒走**批处理**(与飞溅共用着色器与 `SplashBatch`) —— 与飞溅同一个开关理由:
# 桌面 GL 余量大、测不出, 装不上自动回退原来的 320 个 `Line`。
# 实测收益(设备, 池子 320→1 的消融): 这一层值 ~2.3ms/帧(Canvas 1.48 + Python 0.78)。
NECK_RENDERER = os.environ.get("HG_NECK_RENDERER", "batch")       # line | batch
FLARE_RENDERER = os.environ.get("HG_FLARE_RENDERER", "batch")      # rect | batch
# marker 层(20 根斜短线)的批处理 —— **默认 `line`(不启用)**。
# 它与上面三个**不同的地方**: 它会**改像素**(着色器里旋转在 GPU 上算, Kivy 在 CPU 上用
# `math.cos/sin` 造网格 ⇒ 端点/圆头帽边缘差 ULP 级)。实测代价(2026-10-07,
# `tools/_probe_marker_equiv.py` 三臂): **83~97 个像素(0.026%), 全落在 17x14 的框里,
# 最大通道差 6~15 级**; 收益 **画布指令 322 -> 267(省 55 条)** ≈ 0.10ms/帧。
# ⇒ 项目红线: **视觉改动由用户对着并排图(`benchmark_logs/marker_ab.png`)判** ——
#   在用户点头之前**不许把它改成默认 `batch`**。
def _marker_renderer():
    """`HG_MARKER_RENDERER` 优先(桌面), 其次是**与 main.py 同目录的 `marker.on` 标记文件**。

    🔴 安卓 app **读不到宿主 shell 的环境变量** ⇒ 要在**设备上**验这个批处理
    (GLES2 与桌面 GL 对着色器的行为可能不同, 项目为此栽过), 只能靠标记文件:
        adb shell touch /data/data/org.shalou.hourglass/files/app/marker.on
    """
    env = os.environ.get("HG_MARKER_RENDERER")
    if env:
        return env
    try:
        return ("batch" if os.path.exists(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "marker.on")) else "line")
    except Exception:
        return "line"


MARKER_RENDERER = _marker_renderer()     # line | batch (默认 line)


def _install_splash_renderer(widget_class):
    if SPLASH_RENDERER != "batch":
        return
    import importlib
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    mod = importlib.import_module("flow_splash_experiment")
    ok, why = mod.available()
    if not ok:
        print("splash batch unavailable (%s); keeping per-Rectangle" % why)
        return
    mod.install(widget_class)
    # ⚠️ **必须排在 `mod.install` 之后** —— 两者都包 `_build_dynamic_canvas`,
    #    后包的先跑; 颈部那版依赖飞溅那版已经把 RenderContext 建好。
    if NECK_RENDERER == "batch":
        try:
            mod.install_neck(widget_class)
        except Exception as exc:
            print("neck batch unavailable (%s); keeping per-Line" % exc)
    # ⚠️ 闪光那版**必须排在最后** —— 它也包 `_build_dynamic_canvas`, 后包的先跑,
    #    而它要找的 `_flare_group` 是原构建里建的(前面两版都不碰它)。
    _flare_ok = False
    if FLARE_RENDERER == "batch":
        try:
            _flare_ok = mod.install_flares(widget_class)
        except Exception as exc:
            print("flare batch unavailable (%s); keeping per-Rectangle" % exc)
    # ⚠️ **这句只报"接线状态", 不是"生效状态"** —— `install_flares()` 只要把 wrapper 挂上
    #    就返回 True, 而 shader 是**建画布时**才编译的。3号专家 2026-10-07 实测: 强制
    #    失败时本句照样印 `flare=batch`, 同时下面还有一句 `flare batch failed`。
    #    **要判断"批处理真的生效了没有", 看的是建画布之后那对互斥日志**
    #    (`flare batch active (shader compiled)` vs `flare batch failed, ...`)。
    #    这里保留一句"接线"记录, 措辞改成不会读成"已生效"。
    # ⚠️ marker 那版**必须排在最后** —— 它也包 `_build_dynamic_canvas`, 后包的先跑;
    #    它要找的 `_surface_marker_pool` 是**原构建**里建的, 所以它的 wrapper 先调内层
    #    再读池子。**默认关**(见 `MARKER_RENDERER` 那里的注释: 它会改像素, 等用户判)。
    _marker_ok = False
    if MARKER_RENDERER == "batch":
        try:
            import importlib as _il
            _marker_ok = _il.import_module("marker_batch_experiment").install(widget_class)
        except Exception as exc:
            print("marker batch unavailable (%s); keeping per-Line" % exc)
    print("batch renderers wired: splash=%s neck=%s flare=%s marker=%s"
          % (SPLASH_RENDERER, NECK_RENDERER, "batch" if _flare_ok else "rect",
             "batch" if _marker_ok else "line"))


if platform == "android" or os.environ.get("HG_SPLASH_RENDERER"):
    try:
        _install_splash_renderer(HourglassWidget)
    except Exception as exc:
        print(f"splash renderer {SPLASH_RENDERER} unavailable, using per-Rectangle: {exc}")


# ---- 设备端**函数级剖面**挂点(2026-10-07) ------------------------------------
# 只在**存在标记文件**时才真正干活 —— 标记与 app 同目录的 `prof.on`:
#     adb shell touch /data/data/org.shalou.hourglass/files/app/prof.on
#     adb logcat | grep HGPROF
# ⚠️ **位置必须在这后面** —— 沙流/飞溅渲染器会**替换** `_draw_stream` 等属性,
#    先挂就会量到被换掉的那份(量出来是"渲染器没生效"的假象)。
# ⚠️ 量四栏 benchmark 时要把标记删掉(那套探针自己包了 `update_particles`/`redraw`,
#    叠起来互相算进去)。理由与口径见 `tools/prof_android.py` 顶部。
def _install_prof():
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import prof_android
    prof_android.install(HourglassWidget,
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "prof.on"))


try:
    _install_prof()
except Exception as exc:                      # 探针不许影响出货
    print(f"prof hook unavailable: {exc}")


if __name__ == "__main__":
    HourglassApp().run()
