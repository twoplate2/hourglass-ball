# 沙漏 App · 完整资料包（source.md）

> 由 `tools/_pack_source_md.py` 自动生成：把**运行期代码**逐字打进这一个文件，
> 供没有代码仓库的读者查阅。
> **不含任何 .md 文档** —— 求助信是单独一份，内部复盘是我们的东西（见 `discover_docs`）。
>
> **不含**：二进制资源（`*.wav` / `*.png` / `*.otf`）、测量产物
> （`benchmark_logs/`）、一次性生成脚本（文件名以 `_` 开头）。
> **界面不需要截图**——UI 完全由 `main.py::HourglassApp.build()` 定义，可自行还原。
>
> 一帧的调用链：`main.py::tick()` → `update_particles()`（物理）→
> `redraw()` → `_group_stream_particles()`（打包）→
> `tools/flow_texture_experiment.py::TextureFlowBatch.update()`（上传）。

## 目录

| # | 文件 | 行 | 字节 |
|---|---|---|---|
| 1 | `main.py` | 4029 | 190,699 |
| 2 | `app_version.py` | 3 | 89 |
| 3 | `frame_benchmark.py` | 667 | 30,303 |
| 4 | `tools/flow_numpy.py` | 125 | 5,292 |
| 5 | `tools/flow_texture_experiment.py` | 251 | 11,492 |
| 6 | `tools/flow_batch_experiment.py` | 151 | 6,523 |

**共 6 个文件 / 5226 行 / 244,398 字节**

---

# 代码

## `main.py`

*4029 行 / 190,699 字节 —— 逐字原文, 未删改*

```python
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
SAND_MATERIAL_GRAIN = float(os.environ.get("HG_SAND_GRAIN", "0.35"))   # 颗粒强度
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

# ---- 沙面塑形: **已回退**(2026-10-04 用户裁决) ------------------------------------
# 试过"静态粗糙度 + 排水漏斗"(1.60/1.61), 依据是对抗评审的"位移是相对量、沙面下落时
# 每个凹凸跟着走 ⇒ 是可被追踪的特征"。**实测正相反**: 形状固定不变、只是整体平移,
# 读成"一张图在平移"; 用户原话"完全没有变化, 每次都是这样, 还不如平面"。
# ⚠️ 评审看的是**单帧裁图**, 而这个问题只有**跨时间**才看得出来 —— 同模型 panel 的共享
#    盲区, 也是"知觉判断只能由用户裁"的又一个实例。
# ⚠️ 下球的靠壁裙边(见下)保留: 它只在靠壁抬起、且不加粗糙度。
# 下球沙堆的形状: **锥面(堆), 不是平顶的"水位线"** —— 2026-10-04 用户实拍
# 「下面的这个沙子的形状完全不符合物理学吧」「简化版本的也不符合」。
#
# 旧做法: 沙体矩形被球 stencil 裁成一个**平顶弓形**, 只在两端抬 4.4px 裙边 ⇒ 读成
# 液面(还带两端翘起的"浅碟"感)。
# 新做法: 一个**有限的休止角锥面** —— 中央一小段平台(宽度=落沙的横向散布), 向外按
# `MOUND_REPOSE_SLOPE` 下坡, 一直落到球壁。用量: 平台半宽 + 斜率, 使**平均高度仍等于
# 体积反查出来的 h**(`_mound_height_px`), 也就是不改守恒、只改形状。
#
# ⚠️ **平台不是为了好看, 是物理约束**: 粒子的碰撞面是一个**标量** `get_mound_top_y()`
#    (见 update_particles / flow_numpy 的 `mound_top`)。只要平台比沙流的横向散布宽,
#    沙流就永远落在平台上 ⇒ **可见沙面 == 碰撞面** 这条不变式不用动物理就仍然成立。
#    平台之外只有 splash/尘埃会落, 它们各自按 `_mound_top_at(x)` 判定(几十个, 纯 Python)。
#    实测落点横向散布(评审 1 号): 中位 3px / p95 7px / 最大 9px ⇒ 平台取 20px 有余量。
SURFACE_SEGS = 16           # 锥面轮廓的采样段数(每段一个 Quad)
MOUND_REPOSE_SLOPE = 0.60   # 休止角 tanθ ≈ 0.60 (≈31°)
MOUND_PLATEAU_PX = 20.0     # 平台半宽(逻辑像素); 小堆时按可见弦宽自动收窄
MOUND_CREST_MARGIN = 2.0    # 沙体矩形比峰顶再高一点的余量(给 carve 留上沿)
# 沙面离球顶多近时把塑形收掉(占内径的比例)。满沙时沙面就在球顶那个 w=0 的尖点上,
# 那里没有"表面"可言; 而静态粗糙度取负值时轮廓会落到球顶**以下**, carve 于是
# 抠出一个可见的小缺口 —— 用户 2026-10-04 实拍发现("初始状态上面有个空隙")。
SURFACE_FULL_MARGIN = 0.14

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


def _sand_material_rgba(size, base, dark, light, seed=721, grain=0.35, shade=1.0):
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
    qy = (axis * 2.0 - 1.0)[:, None]
    # 「靠壁压暗」只看**水平**距离: 玻璃壁在左右两侧, 而竖直方向的上下两端
    # 分别是沙面(上)与**颈口**(下), 都不是壁。
    # ⚠️ 原来用径向 r²=qx²+qy², 会把球底那个极点也当成"靠壁"压暗 ——
    # 而颈部采样不到那一段, 于是颈部比球体亮一个档, 被读成两种材料
    # (2026-10-04 用户报"上面的部分和颈部的沙子构成完全不同")。
    w = np.clip((qx * qx - 0.64) / 0.36, 0.0, 1.0)
    edge = w * w * (3.0 - 2.0 * w)
    broad = 0.10 * (axis[:, None] - 0.5) - 0.07 * qx - 0.16 * edge
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
    key = (size, tuple(base), tuple(dark), tuple(light), round(grain, 4), round(shade, 4))
    material = _SAND_MATERIAL_CACHE.get(key)
    if material is None:
        try:
            rgba = _sand_material_rgba(size, base, dark, light, grain=grain, shade=shade)
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
        rgba = _sand_material_rgba(size, base, dark, light, grain=grain)
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
FLASH_DURATION = 0.35

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
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        self._mound_shape_cache = None      # (elapsed, (apex,w0,slope)) —— 每帧只解一次
        self.flash_end = 0.0
        self._completion_triggered = False
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
        """
        tp = self._taper
        pts = tp['in_pts']
        y_top, y_end = pts[0][1], 2 * self._neck_y - tp['y_bot']
        fill_t = self._neck_fill_time
        f = min(1.0, max(0.0, self.elapsed / fill_t))
        if f <= 0:
            return []
        fill_y = y_top - (y_top - y_end) * f
        side = [(x, y) for x, y in pts if y > fill_y]
        w = tp['t_in']
        if fill_y >= tp['y_bot']:          # 截断点还在曲线段 → 插值取半宽
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if y1 <= fill_y <= y0:
                    w = x0 + (x1 - x0) * (y0 - fill_y) / max(1e-6, y0 - y1)
                    break
        side.append((w, fill_y))
        return side

    def _mound_shape(self):
        """下球沙堆的形状参数 (峰顶高度, 平台半宽, 斜率) —— 高度单位都是"离球内底"。

        轮廓族(平台 + 休止角锥面, 并被球顶截平):
            S(dx) = min( apex - slope·max(0,|dx|-w0), 球内顶(dx) )
        `apex` 由**面积守恒**解出: 让 {球内 ∧ S 以下} 的面积(二维代理的"体积")等于
        体积反查出来的平顶弓形面积 ⇒ 中期自然是锥面, 而 h 接近满球时 apex 被逼到球顶、
        轮廓收敛成"满球"(这正是真沙漏末端的读感: 底球被塞满, 不是留个锥顶和两侧空隙)。

        ⚠️ 用二分(面积对 apex 单调)而不是闭式解 —— 被球顶截平之后没有简单的解析式。
        代价: 采样 n 列 × 迭代 12 次 ≈ 0.1ms, 且**每帧只算一次**(结果按 elapsed 缓存,
        `get_mound_top_y`/`_draw_mound_shape`/splash 判定都复用它)。
        """
        elapsed = self.elapsed
        cached = self._mound_shape_cache
        if cached is not None and cached[0] == elapsed:
            return cached[1]
        h = self._mound_height_px()
        Ri = self._R_inner
        full = 2.0 * Ri
        slope = MOUND_REPOSE_SLOPE
        if h <= 0.0:
            result = (0.0, 0.0, slope)
            self._mound_shape_cache = (elapsed, result)
            return result
        hc = h if h < full else full
        chord = math.sqrt(max(1.0, Ri * Ri - (Ri - hc) ** 2))
        w0 = min(MOUND_PLATEAU_PX, chord * 0.5)
        # 目标面积 = 平顶弓形(Ri 内、高度 hc)的面积
        c = math.sqrt(max(0.0, 2.0 * Ri * hc - hc * hc))
        target = (Ri * Ri * math.acos(max(-1.0, min(1.0, 1.0 - hc / Ri)))
                  - (Ri - hc) * c)
        n = 25
        step = full / (n - 1)
        dxs = [-Ri + step * i for i in range(n)]
        halfs = [math.sqrt(max(0.0, Ri * Ri - dx * dx)) for dx in dxs]
        w0_local = w0
        slope_local = slope
        def area(apex):
            total = 0.0
            for k in range(n):
                dx = dxs[k]
                adx = dx if dx >= 0.0 else -dx
                s = apex - slope_local * (adx - w0_local) if adx > w0_local else apex
                ceil_h = Ri + halfs[k]
                if s > ceil_h:
                    s = ceil_h
                hgt = s - (Ri - halfs[k])
                if hgt > 0.0:
                    total += hgt
            return total * step
        lo, hi = 0.0, full
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            if area(mid) < target:
                lo = mid
            else:
                hi = mid
        result = (0.5 * (lo + hi), w0, slope)
        self._mound_shape_cache = (elapsed, result)
        return result

    def _mound_top_at(self, x):
        """给定横坐标处的**可见沙面高度**(绝对 y) —— splash / 尘埃用。

        ⚠️ 粒子主流**不用**这个: 它们落在中央平台上, 平台高度 == `get_mound_top_y()`
        这个标量, 所以热循环(update_particles / flow_numpy)一个字都不用改。
        """
        apex, w0, slope = self._mound_shape()
        if apex <= 0.0:
            return self._lower_sand_bot
        dx = abs(x - self._cx)
        return self._lower_sand_bot + apex - slope * max(0.0, dx - w0)

    def get_mound_top_y(self):
        """碰撞面与实际绘制的**平台**一致(见 `_mound_shape`), 不使用未绘制的堆尖。

        平台之外是锥面(斜向球壁), 那里只有 splash/尘埃, 走 `_mound_top_at`。
        """
        apex, _w0, _slope = self._mound_shape()
        return self._lower_sand_bot + apex

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
        self._mound_shape_cache = None      # (elapsed, (apex,w0,slope)) —— 每帧只解一次
        self.flash_end = 0.0
        self._completion_triggered = False
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
                           'sand_grain': SAND_MATERIAL_GRAIN}, f, ensure_ascii=False)
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
            if self.elapsed >= self.duration:
                self.elapsed = self.duration
                self.running = False
                self.flash_end = now + FLASH_DURATION
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
            if rand() < 0.25:
                append_flare({"x": x, "y": mound_top, "end": now + 0.08})
            if rand() < 0.50:
                v = -vy
                bounce = min(110 * motion_scale,
                             (v if v > 0 else 0) * rand_uniform(0.14, 0.28))
                angle = rand_uniform(-0.85, 0.85)
                step_left = step_dt - float(hit_dt[k])
                append_splash({
                    "x": x, "y": mound_top + 0.5,
                    "vx": sin(angle) * bounce,
                    "vy": cos(angle) * bounce,
                    "size": rand_choice([1, 1, 2]),
                    "_step_dt": step_left if step_left > 0 else 0,
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
                wobble_phase = p["wobble_phase"]
                wobble_amp = p["wobble_amp"]
                size = p["size"]
                y += vy * step_dt + 0.5 * g * step_dt * step_dt
                vy += g * step_dt
                hit = y <= mound_top
                hit_dt = 0.0
                if hit:
                    d = old_y - mound_top
                    distance = d if d > 0 else 0
                    v = -old_vy
                    speed = v if v > 0 else 0
                    denom = speed + sqrt(speed * speed + 2 * g_abs * distance)
                    hit_dt = 2 * distance / (denom if denom > 1e-6 else 1e-6)
                    hit_dt = hit_dt if hit_dt < step_dt else step_dt
                    y = mound_top
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
                    if rand() < 0.25:
                        append_flare({"x": x, "y": mound_top, "end": now + 0.08})
                    if rand() < 0.50:
                        v = -vy
                        bounce = min(110 * motion_scale,
                                     (v if v > 0 else 0) * rand_uniform(0.14, 0.28))
                        angle = rand_uniform(-0.85, 0.85)
                        step_left = step_dt - hit_dt
                        append_splash({
                            "x": x, "y": mound_top + 0.5,
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

        # 锥面参数本帧只算一次(平台之外只有 splash/尘埃会落, 它们按 _mound_top_at 判定;
        # 主流落在平台上 ⇒ 用标量 mound_top 就够了, 热循环一个字不改)。
        _apex, _w0, _slope = self._mound_shape()
        _mound_bot = self._lower_sand_bot
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
            _mt = _mound_bot + _apex - _slope * max(0.0, (sx if sx > 0 else -sx) - _w0)
            if vy < 0 and y <= _mt:
                continue
            if y < lower_bot or y > lower_top - 5:
                continue
            append_splash_keep(s)
        self.splashes = new_splashes

        self.flares = [f for f in self.flares if f["end"] > now]

        new_dusts = []
        for d in self.dusts:
            d["y"] += d["vy"] * dt - 225 * dt * dt
            d["vy"] -= 450 * dt
            d["x"] += d["vx"] * dt
            _dd = d["x"] - cx
            _mt = _mound_bot + _apex - _slope * max(0.0, (_dd if _dd > 0 else -_dd) - _w0)
            if now > d["end"] or d["y"] < _mt - 1:
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

    def _draw_mound_shape(self, h_mound):
        """下球沙堆的**锥面**轮廓 —— 用与上球同一套 carve/band 机制。

        轮廓(高度按"离球内底"算, 再换算成绝对 y):
            S(dx) = apex                       , |dx| <= w0   (平台: 沙流落点区)
            S(dx) = apex - slope·(|dx| - w0)   , 其它          (休止角下坡, 落到球壁)
        参数见 `_mound_shape`。carve 把轮廓以上的矩形抠掉(填玻璃色), band 是面下那条窄亮带。

        ⚠️ carve 的上沿 = **沙体矩形的顶**(见 redraw 的 `MOUND_CREST_MARGIN`), 不是轮廓峰顶 ——
           抠不到矩形顶就会在轮廓之上留一条浮空的平沙(1.67 踩过同一个坑)。
        ⚠️ 轮廓可以低于球内底(锥面延伸到球外), 四边形的上沿仍高于下沿 ⇒ 形状合法。
        """
        carve, band = self._mound_carve, self._mound_band
        if h_mound <= 0:
            for q in carve + band:
                q.points = [0] * 8
            return
        cx, Ri = self._cx, self._R_inner
        bottom = self._lower_sand_bot
        apex, w0, slope = self._mound_shape()
        top = bottom + apex + MOUND_CREST_MARGIN      # == redraw 里矩形的顶
        width = min(SAND_SURFACE_BAND, h_mound)
        self._mound_band_color.a = SAND_SURFACE_ALPHA * min(
            1.0, max(0.0, h_mound / SAND_SURFACE_FADE))
        prof = []
        for i in range(SURFACE_SEGS + 1):
            x = cx - Ri + 2.0 * Ri * (i / SURFACE_SEGS)
            dx = abs(x - cx)
            prof.append((x, bottom + apex - slope * max(0.0, dx - w0)))
        for i in range(SURFACE_SEGS):
            x0, y0 = prof[i]
            x1, y1 = prof[i + 1]
            carve[i].points = [x0, y0, x1, y1, x1, top, x0, top]
            band[i].points = [x0, y0, x1, y1, x1, y1 - width, x0, y0 - width]

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
                if yc == self._lower_y_c:
                    # 下球: 只做"靠壁裙边"(中间 60% 严格钉在碰撞线上, 见常量区)
                    Color(*hex_rgb(GLASS_FILL), 1)
                    self._mound_carve = [Quad(points=[0] * 8)
                                         for _ in range(SURFACE_SEGS)]
                    self._mound_band_color = Color(*(tuple(self.sand_light) + (0.0,)))
                    self._mound_band = [Quad(points=[0] * 8)
                                        for _ in range(SURFACE_SEGS)]
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
            self._flash_color = Color(1, 1, 1, 0)
            self._flash_rect = Rectangle(pos=self.pos, size=(0, 0))
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
        up_draw = upper_height
        # ⚠️ 下球矩形**画到锥面峰顶之上**(+MOUND_CREST_MARGIN): carve 只能"减"不能"加",
        #    矩形不够高就抠不出峰顶那一块(第一版裙边就是这么失效的)。
        #    峰顶高度 = `_mound_shape()` 的 apex, 与 `_draw_mound_shape` 里 carve 的上沿同一个值。
        apex_px, _w0, _slope = self._mound_shape()
        mound_draw = (apex_px + MOUND_CREST_MARGIN) if h_mound > 0 else 0.0
        for (_color, rect), height in zip(self._sand_chords, (up_draw, mound_draw)):
            rect.size = (diameter, height)
            if full_uv is not None:
                rect.tex_coords = crop_tex_coords(full_uv, height / diameter)
        # 沙面窄过渡(评审 meishu2.md §4.3): 紧贴沙面**内部**的一条窄亮带。
        # 下沙用 get_mound_top_y() —— 与**粒子碰撞面**同一个值, 保证"落点与可见表面一致"。
        # ⚠️ 沙体薄时按可见厚度按比例减弱, 否则会剩一条悬空的独立亮线。
        # ⚠️ **上球那条已改由 `_draw_surface_shape` 沿起伏轮廓画**(直边矩形跟不上起伏,
        #    会留下悬空亮台/缺口 —— 评审 1 号指出), 所以这里只留 `_sand_bands[1]`。
        self._sand_bands[1][1].size = (0, 0)      # 下球那条改由 _draw_mound_shape 画
        for (band_color, band_rect), top_y, height in (
                (self._sand_bands[0], self._upper_sand_bot + upper_height, upper_height),):
            band = min(SAND_SURFACE_BAND, height)
            band_rect.pos = (self._cx - self._R_inner, top_y - band)
            band_rect.size = (diameter, band)
            band_color.a = SAND_SURFACE_ALPHA * min(1.0, max(0.0, height / SAND_SURFACE_FADE))
        self._draw_mound_shape(h_mound)
        side = self._neck_sand_side() if upper_height > 0 else []
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
        self._flash_color.a = 0.25 if now < self.flash_end else 0
        self._pause_rect.size = self.size if self._pause_color.a else (0, 0)
        self._flash_rect.size = self.size if self._flash_color.a else (0, 0)

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
        """长按**版本号**进的隐藏菜单: 沙子浓度 / 玻璃反光两个滑块 + 性能测试。

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

        # 尺寸照**基准弹窗**那套(它已经跑过真机): 宽度 0.94, 高度取"内容需要"与
        # "窗口 85%"的较小值 —— 不再写死一个数, 否则字体缩放一变就被裁掉。
        # (2026-10-04 用户反馈"太拥挤、很多地方显示不全": 原来写的 0.8 宽 + dp(360) 高。)
        popup_height = min(dp(440), max(Window.width, Window.height) * 0.85)
        popup = _SandBgPopup(title=f"v{APP_VERSION}", content=content,
                             size_hint=(0.94, None), height=popup_height,
                             auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(17)
        self._dev_popup = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_dev_popup", None))
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
        self._completion_popup = popup
        popup.open()

    def _close_completion(self, popup):
        self._completion_popup = None
        popup.dismiss()

    def on_pause(self):
        if self._benchmark_active():
            self._benchmark_runner.cancel()
        self.hourglass._stop_completion_sound()
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
```

## `app_version.py`

*3 行 / 89 字节 —— 逐字原文, 未删改*

```python
"""Single version source for APK metadata and benchmark reports."""

APP_VERSION = "1.69"
```

## `frame_benchmark.py`

*667 行 / 30,303 字节 —— 逐字原文, 未删改*

```python
"""On-device frame pacing benchmark; no persistent settings are modified."""

import copy
from datetime import datetime
import gc
import hashlib
import math
import os
import sys
import time

from app_version import APP_VERSION

from kivy.clock import Clock
from kivy.config import Config
from kivy.core.window import Window
from kivy.core.text import Label as CoreLabel
from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.widget import Widget
from kivy.utils import platform as runtime_platform


PERIODS = (1, 5, 15)
REPORT_REVISION = 2
TAIL_MIN_FRAMES = 5     # "1% low" 至少平均这么多帧 —— 见 frame_statistics 里的说明


def tukey_upper_adjacent(values):
    """Tukey 上栅栏(`Q3 + 1.5*IQR`)以内的**最大值**(EDA 里的"相邻值")。

    离群点判定用箱线图的标准栅栏; 而 EDA 的作图规矩是: 非箱线图的坐标轴范围取
    **相邻值**、不取极值 —— 否则单个尖峰就把整张图压成一条草。
    (Grafana 的"Y 轴按百分位自动缩放"就是为同一件事做的。)
    """
    data = sorted(values)
    if not data:
        return 0.0

    def quantile(p):
        return data[min(len(data) - 1, int(round(p * (len(data) - 1))))]

    fence = quantile(0.75) + 1.5 * (quantile(0.75) - quantile(0.25))
    inside = [value for value in data if value <= fence]
    return max(inside) if inside else data[-1]


def nice_axis_ceiling(value, ticks=4):
    """Heckbert 的 nice numbers(Graphics Gems): 返回 (上界, 步长)。

    步长 = `value/ticks` 的 1/2/5/10 × 10^k 邻值(分数按 1.5 / 3 / 7 三档吸附),
    上界再抬到步长的整数倍。刻度因此总是好读的数, 条数在 3~8 之间浮动。
    """
    if value <= 0:
        return 1.0, 1.0
    raw = value / ticks
    exponent = math.floor(math.log10(raw))
    norm = raw / 10.0 ** exponent
    step = (1 if norm < 1.5 else 2 if norm < 3 else 5 if norm < 7 else 10) * 10.0 ** exponent
    return math.ceil(value / step) * step, step


def benchmark_environment(widget):
    environment = {
        "report_revision": REPORT_REVISION,
        "app_version": APP_VERSION,
        "platform": runtime_platform,
        "window_pixels": tuple(Window.size),
        "maxfps": Config.get("graphics", "maxfps"),
        "clock_resolution_s": round(Clock.get_resolution(), 6),
        "vsync": Config.get("graphics", "vsync"),
        "python": sys.version.split()[0],
    }
    source = sys.modules.get(type(widget).__module__)
    path = getattr(source, "_benchmark_source_path", getattr(source, "__file__", None))
    if path:
        try:
            with open(path, "rb") as stream:
                environment["code_hash"] = hashlib.sha256(stream.read()).hexdigest()[:12]
        except OSError:
            pass
    if runtime_platform == "android":
        try:
            from jnius import autoclass, cast
            build = autoclass("android.os.Build")
            version = autoclass("android.os.Build$VERSION")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            display = activity.getWindowManager().getDefaultDisplay()
            power = cast("android.os.PowerManager", activity.getSystemService("power"))
            environment.update(
                model=str(build.MODEL), manufacturer=str(build.MANUFACTURER),
                android_sdk=int(version.SDK_INT),
                refresh_hz=round(float(display.getRefreshRate()), 1),
                power_save=bool(power.isPowerSaveMode()))
            if version.SDK_INT >= 29:
                environment["thermal_status"] = int(power.getCurrentThermalStatus())
        except Exception as exc:
            environment["device_info_error"] = type(exc).__name__
    return environment


def format_frame_diagnostics(result):
    trace = result.get("frame_trace", [])
    details = result.get("slowest_frame_details", [])
    means = result.get("stage_mean_ms", {})
    if not trace and not details and not means:
        return ""
    lines = ["诊断耗时(ms): " + " / ".join(
        f"{label} {means.get(key, 0):.2f}" for label, key in (
            ("物理", "physics_ms"), ("图元", "update_draw_ms"),
            ("Canvas", "canvas_ms"), ("前次Swap", "previous_swap_ms")))]
    if trace:
        peak = max(frame.get("particles", 0) for frame in trace)
        average = sum(frame.get("particles", 0) for frame in trace) / len(trace)
        peak_splash = max(frame.get("splashes", 0) for frame in trace)
        gc_peak = max(frame.get("gc_ms", 0) for frame in trace)
        gc_full = sum(frame.get("gc_generation", -1) == 2 for frame in trace)
        long_frames = sum(frame["frame_ms"] > 25 for frame in trace)
        lines.extend((
            f"存活粒子 平均 {average:.0f} / 峰值 {peak}；飞溅峰值 {peak_splash}",
            f">25ms {long_frames} 帧；GC最大 {gc_peak:.2f}ms / 全量GC涉及 {gc_full} 帧"))
    digest = format_benchmark_digest(result)
    if digest:
        lines.append(digest)
    if details:
        lines.append("最慢帧明细(ms):")
        for frame in details:
            lines.append(
                f"t={frame.get('elapsed_s', 0):.2f}s 总={frame['frame_ms']:.2f} "
                f"粒子={frame.get('particles', 0)} 飞溅={frame.get('splashes', 0)} "
                f"物理={frame.get('physics_ms', 0):.2f} 图元={frame.get('update_draw_ms', 0):.2f} "
                f"Canvas={frame.get('canvas_ms', 0):.2f} 前次Swap={frame.get('previous_swap_ms', 0):.2f} "
                f"GC={frame.get('gc_ms', 0):.2f}/代{frame.get('gc_generation', -1)}")
    return "\n".join(lines)


def format_benchmark_result(period, result=None):
    if result is None:
        return f"{period} 秒\n待测试"
    avg, low = result["average_fps"], result["one_percent_low_fps"]
    avg_text = f"{avg:.1f}" if avg is not None else "--"
    low_text = f"{low:.1f}" if low is not None else "--"
    slow = " / ".join(f"{fps:.1f}" for fps in result["slowest_five_fps"]) or "--"
    tail = result.get("one_percent_low_frames") or 0
    tail_text = f"(最差 {tail} 帧)" if tail else ""
    return (f"{period} 秒  ·  {result['frames']} 帧\n"
            f"平均 {avg_text} FPS    1% low {low_text} FPS{tail_text}\n"
            f"最慢 5 帧 FPS:\n{slow}")


def format_benchmark_report(results, cancelled=False):
    by_period = {r["period"]: r for r in results}
    blocks = [f"Benchmark v{APP_VERSION} / 诊断报告 v{REPORT_REVISION}"]
    if results and results[0].get("environment"):
        environment = results[0]["environment"]
        blocks.append("环境: " + " / ".join(
            f"{key}={value}" for key, value in environment.items()))
    for period in PERIODS:
        result = by_period.get(period)
        block = format_benchmark_result(period, result)
        if result:
            diagnostic = format_frame_diagnostics(result)
            if diagnostic:
                block += "\n" + diagnostic
            end_state = result.get("environment_end", {})
            state_keys = ("refresh_hz", "thermal_status", "power_save")
            if any(key in end_state for key in state_keys):
                block += "\n结束状态: " + " ".join(
                    f"{key}={end_state.get(key, '--')}" for key in state_keys)
        blocks.append(block)
    if results:
        blocks.append("采样为应用侧提交间隔；Canvas含驱动等待，前次Swap属于上一帧，GC可能包含在各阶段内。")
    if cancelled:
        blocks.append("测试已取消")
    return "\n\n".join(blocks)


STAGE_KEYS = ("physics_ms", "update_draw_ms", "canvas_ms", "previous_swap_ms")


def benchmark_digest(result, bin_seconds=0.5):
    """把逐帧 trace 压成便于定位瓶颈的统计量(分位/超阈值/残差/相关性/分桶)。"""
    trace = result.get("frame_trace") or []
    if not trace:
        return {}
    frame_ms = sorted(frame["frame_ms"] for frame in trace)
    count = len(frame_ms)

    def percentile(q):
        return frame_ms[min(count - 1, max(0, int(round(q * (count - 1)))))]

    # 帧时间减去四个被探针包住的阶段与 GC 后剩下的部分:
    # 若某一帧这一项突然变大,说明卡顿不在渲染/物理里(例如系统调度、锁、纹理上传)。
    residual = [frame["frame_ms"]
                - sum(frame.get(key, 0) for key in STAGE_KEYS)
                - frame.get("gc_ms", 0) for frame in trace]
    # 相关性必须按"同一帧"配对, 所以这里用未排序的帧时间(上面的 frame_ms 已排序)。
    particles = [frame.get("particles", 0) for frame in trace]
    per_frame_ms = [frame["frame_ms"] for frame in trace]
    mean_p = sum(particles) / count
    mean_f = sum(per_frame_ms) / count
    cov = sum((p - mean_p) * (f - mean_f)
              for p, f in zip(particles, per_frame_ms))
    var_p = sum((p - mean_p) ** 2 for p in particles)
    var_f = sum((f - mean_f) ** 2 for f in per_frame_ms)
    correlation = cov / math.sqrt(var_p * var_f) if var_p > 0 and var_f > 0 else 0.0
    buckets = {}
    for frame in trace:
        buckets.setdefault(int(frame["elapsed_s"] / bin_seconds), []).append(frame)
    rows = []
    for index in sorted(buckets):
        group = buckets[index]
        size = len(group)
        rows.append({
            "t0": round(index * bin_seconds, 2),
            "frames": size,
            "fps": round(1000.0 * size / sum(f["frame_ms"] for f in group), 1),
            "frame_ms": round(sum(f["frame_ms"] for f in group) / size, 2),
            "particles": round(sum(f.get("particles", 0) for f in group) / size),
            "splashes": round(sum(f.get("splashes", 0) for f in group) / size),
            **{key: round(sum(f.get(key, 0) for f in group) / size, 2) for key in STAGE_KEYS},
        })
    return {
        "frames": count,
        "p50_ms": round(percentile(0.50), 2),
        "p90_ms": round(percentile(0.90), 2),
        "p99_ms": round(percentile(0.99), 2),
        "max_ms": round(frame_ms[-1], 2),
        "over_60hz": sum(1 for ms in frame_ms if ms > 1000 / 60),
        "over_120hz": sum(1 for ms in frame_ms if ms > 1000 / 120),
        "over_25ms": sum(1 for ms in frame_ms if ms > 25),
        "over_50ms": sum(1 for ms in frame_ms if ms > 50),
        "residual_mean_ms": round(sum(residual) / count, 2),
        "residual_max_ms": round(max(residual), 2),
        "particle_peak": max(particles),
        "particle_mean": round(mean_p),
        "particle_frame_corr": round(correlation, 3),
        "bins": rows,
    }


def format_benchmark_digest(result, bins=False):
    digest = benchmark_digest(result)
    if not digest:
        return ""
    lines = [
        f"帧时间分位(ms): p50={digest['p50_ms']} p90={digest['p90_ms']} "
        f"p99={digest['p99_ms']} max={digest['max_ms']}",
        f"超阈值帧数: >8.33ms {digest['over_120hz']} / >16.7ms {digest['over_60hz']} "
        f"/ >25ms {digest['over_25ms']} / >50ms {digest['over_50ms']}",
        f"阶段残差(帧时间-物理-图元-Canvas-Swap-GC): 均值 {digest['residual_mean_ms']}ms "
        f"/ 最大 {digest['residual_max_ms']}ms",
        f"粒子峰值 {digest['particle_peak']} / 均值 {digest['particle_mean']};"
        f" 粒子数↔帧时间 相关 r={digest['particle_frame_corr']:+.3f}",
    ]
    if bins:
        lines.append("分桶表 t0s,frames,avgFPS,frame_ms,particles,splashes,"
                     + ",".join(STAGE_KEYS))
        for row in digest["bins"]:
            lines.append(",".join(str(row[key]) for key in (
                "t0", "frames", "fps", "frame_ms", "particles", "splashes",
                *STAGE_KEYS)))
    return "\n".join(lines)


def benchmark_log_text(results, cancelled=False):
    """整份日志的文本(逐帧 trace + 分位/超阈值/残差/分桶表 + 环境行)。
    单独拎出来是为了让同一次结果能写到两处(应用私有目录 + 公共 Download)而**同名同内容**。"""
    lines = [format_benchmark_report(results, cancelled),
             f"\nPlatform: {sys.platform}; Window: {tuple(Window.size)}",
             f"Timer resolution: {time.get_clock_info('perf_counter').resolution}s",
             f"VSync configuration: {Config.get('graphics', 'vsync')}",
             f"MaxFPS configuration: {Config.get('graphics', 'maxfps')}",
             "Timing units: milliseconds; samples: Window.on_flip intervals.",
             "Host load note: Windows video compression may be running; cross-run FPS is not a controlled comparison."]
    for result in results:
        lines.append(f"\nPeriod: {result['period']}s")
        lines.append(f"Flow renderer: {result.get('flow_renderer', 'line_pool')}")
        means = result.get("stage_mean_ms", {})
        lines.append("Stage means: " + ", ".join(
            f"{name}={value:.3f}" for name, value in means.items()))
        digest = format_benchmark_digest(result, bins=True)
        if digest:
            lines.append(digest)
        lines.append("Slowest frames:")
        for frame in result.get("slowest_frame_details", []):
            lines.append(", ".join(f"{key}={value:.3f}" for key, value in frame.items()))
        lines.append("Frame trace: time_s,frame_ms,FPS,physics_ms,update_draw_ms,canvas_ms,previous_swap_ms,particles,splashes,gc_ms,gc_generation,mound_px,gap_between_frames_ms,gap_tick_tail_ms,gap_draw_to_flip_ms,index_assigns,chunk_clears,flow_chunks,vertex_rebuild_kib")
        for frame in result.get("frame_trace", []):
            lines.append(",".join(f"{value:.3f}" for value in (
                frame["elapsed_s"], frame["frame_ms"], 1000 / frame["frame_ms"],
                frame.get("physics_ms", 0), frame.get("update_draw_ms", 0),
                frame.get("canvas_ms", 0), frame.get("previous_swap_ms", 0),
                frame["particles"], frame["splashes"], frame.get("gc_ms", 0),
                frame.get("gc_generation", -1), frame.get("mound_px", 0),
                frame.get("gap_between_frames_ms", 0), frame.get("gap_tick_tail_ms", 0),
                frame.get("gap_draw_to_flip_ms", 0), frame.get("index_assigns", 0),
                frame.get("chunk_clears", 0), frame.get("flow_chunks", 0),
                frame.get("vertex_rebuild_kib", 0))))
    return "\n".join(lines) + "\n"


def save_benchmark_log(directory, results, cancelled=False):
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(directory, f"benchmark_{stamp}.txt")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(benchmark_log_text(results, cancelled))
    return path


class BenchmarkFrameChart(Widget):
    def __init__(self, result, **kwargs):
        super().__init__(**kwargs)
        self.result = result
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _label(self, text, x, y, right=False):
        label = CoreLabel(text=text, font_size=sp(11), color=(0.20, 0.14, 0.08, 1))
        label.refresh()
        texture = label.texture
        Rectangle(texture=texture, pos=(x - texture.width if right else x, y),
                  size=texture.size)

    def _draw(self, *_):
        self.canvas.clear()
        left, bottom = self.x + dp(32), self.y + dp(20)
        width, height = max(1, self.width - dp(44)), max(1, self.height - dp(34))
        period = self.result["period"]
        trace = self.result.get("frame_trace", [])
        # ⚠️ Y 轴原先是写死的 0/30/60/90, 而且把 FPS **夹在 90**(`min(90, 1000/frame_ms)`)
        # ⇒ 120fps+ 的机器上整条绿线平贴顶部, 零信息(2026-10-03 发现)。
        # ⚠️ 改用 `max` 也不行: **量到的最高帧远高于常态**(实测平均 122fps 而尖峰 260)
        # ⇒ 上限被拉到 320, 曲线挤在下方 1/3, 上半张图全空。
        # 现在按标准做法两步: ① 上界取 **Tukey 上栅栏内的最大值**(相邻值, 剔掉尖峰);
        # ② 再用 **Heckbert nice numbers** 把上界与步长吸附到好读的数。
        # 代价: 极少数尖峰帧会被夹在顶线上(曲线本来就带 `min(fps_top, …)`)。
        rates = [1000.0 / frame["frame_ms"] for frame in trace
                 if (frame.get("frame_ms") or 0) > 0]
        fps_top, fps_step = (nice_axis_ceiling(tukey_upper_adjacent(rates))
                             if rates else (60.0, 15.0))
        fps_ticks = max(1, int(round(fps_top / fps_step)))
        with self.canvas:
            for i in range(fps_ticks + 1):
                y = bottom + height * i / fps_ticks
                Color(0.75, 0.74, 0.70, 1)
                Line(points=[left, y, left + width, y], width=1)
                Color(1, 1, 1, 1)
                self._label(f"{fps_step * i:g}", left - dp(5), y - dp(6), right=True)
            self._label("FPS", left, self.top - dp(13))
            self._label("0 s", left, self.y)
            self._label(f"{period} s", left + width, self.y, right=True)
            points = []
            for frame in trace:
                points.extend((
                    left + width * min(1, frame["elapsed_s"] / period),
                    bottom + height * min(fps_top, 1000 / frame["frame_ms"]) / fps_top))
            if len(points) >= 4:
                Color(0.18, 0.45, 0.36, 1)
                Line(points=points, width=1)
            # (2026-10-03 用户要求) 这里原本还有第二条橙色曲线 = 在途粒子数, 连同"粒子峰值 N"
            # 的标签一起删掉 —— 图上只留 FPS 一条。
            # ⚠️ **逐帧 trace 日志里仍然记 `particles` 那一列**: A/B 分析、以及
            # "慢帧是不是跟着负载走"的诊断都还要用它, 只是不再画到图上。
            Color(1, 1, 1, 1)
            self._label("绿=FPS", left + width, self.top - dp(13), right=True)


def frame_statistics(intervals):
    samples = [dt for dt in intervals if math.isfinite(dt) and dt > 0]
    if not samples:
        return {"frames": 0, "average_fps": None, "one_percent_low_fps": None,
                "slowest_five_fps": [], "slowest_five_ms": []}
    slowest = sorted(samples, reverse=True)
    # ⚠️ 尾部帧数要设**下限**: 1 秒档只有 ~110 帧, ceil(1%) = 2 帧 ⇒ "1% low" 退化成
    # "最慢那一帧"(实测 95.6 而最慢帧 93.6 —— 几乎是同一个数), 还跟上面那行
    # 「最慢 5 帧」重复显示同一信息, 并且会随测试时长漂移(同一段开头测 1s / 5s 给出的值不同)。
    # 下限取 5 帧, 并把**实际用了几帧**一并报出去(界面和日志都能看见)。
    low_count = min(len(slowest), max(TAIL_MIN_FRAMES, math.ceil(len(samples) * 0.01)))
    return {
        "frames": len(samples),
        "average_fps": len(samples) / sum(samples),
        "one_percent_low_fps": low_count / sum(slowest[:low_count]),
        "one_percent_low_frames": low_count,
        "slowest_five_fps": [1 / dt for dt in slowest[:5]],
        "slowest_five_ms": [dt * 1000 for dt in slowest[:5]],
    }


class BenchmarkHoldArea(Widget):
    def __init__(self, activate, **kwargs):
        super().__init__(**kwargs)
        self._activate = activate
        self._touch = None
        self._hold_event = None

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or self._touch is not None:
            return super().on_touch_down(touch)
        self._touch = touch
        self._origin = touch.pos
        touch.grab(self)
        self._hold_event = Clock.schedule_once(self._held, 3)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            dx, dy = touch.x - self._origin[0], touch.y - self._origin[1]
            if not self.collide_point(*touch.pos) or dx * dx + dy * dy > dp(12) ** 2:
                self._cancel_hold()
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            self._cancel_hold()
            touch.ungrab(self)
            self._touch = None
            return True
        return super().on_touch_up(touch)

    def _cancel_hold(self):
        if self._hold_event is not None:
            self._hold_event.cancel()
            self._hold_event = None

    def _held(self, _dt):
        self._hold_event = None
        # LandLayer 在事件返回后会把 touch.pos 还原为屏幕坐标。
        if self._touch is not None:
            self._activate()


class BenchmarkRunner:
    _STATE_FIELDS = (
        "duration", "elapsed", "running", "particle_acc", "particles", "splashes",
        "flares", "dusts", "mound_peak_offset", "flash_end", "_completion_triggered",
        "completion_enabled",
    )

    def __init__(self, widget, on_case, on_finish, periods=PERIODS):
        self.widget = widget
        self.on_case = on_case
        self.on_finish = on_finish
        self.periods = periods
        self.results = []
        self.active = False
        self._sampling = False
        self._event = None
        self._stages = {}
        self._stamps = {}
        self._gc_ms = 0
        self._gc_generation = -1
        self._gc_start = time.perf_counter()
        self.capture_slow_frames = False

    def start(self):
        if self.active:
            return
        self._saved = {name: copy.deepcopy(getattr(self.widget, name))
                       for name in self._STATE_FIELDS}
        self._paused_at = time.perf_counter()
        self._environment = benchmark_environment(self.widget)
        self.widget.running = False
        self.widget.completion_enabled = False
        self.widget._stop_sound()
        self.active = True
        self._index = 0
        self._install_probes()
        Window.bind(on_flip=self._on_flip)
        self._event = Clock.schedule_once(self._prepare_case, 0)

    def _prepare_case(self, _dt):
        self._event = None
        if not self.active:
            return
        if self._index == len(self.periods):
            self._finish(False)
            return
        if not self.widget.set_duration(self.periods[self._index]):
            self.widget.reset()
        self.widget.redraw()
        self.on_case(self.periods[self._index], self._index + 1)
        # 布局、上轮完成闪光和弹窗退场不计入该轮; 起步动画完整计入。
        self._event = Clock.schedule_once(self._begin_case, 0.5)

    def _begin_case(self, _dt):
        self._event = None
        if not self.active:
            return
        self._intervals = []
        self._frame_details = []
        self._visual_frames = []
        self.widget.toggle()
        self._case_start = time.perf_counter()
        self._last_flip = None
        self._prev_swap_exit = None
        self._gc_ms = 0
        self._gc_generation = -1
        self._sampling = True

    def _on_flip(self, *_):
        if not self._sampling:
            return
        now = time.perf_counter()
        if self._last_flip is None:
            if self.widget.running:
                self._last_flip = now
                self._gc_ms = 0
                self._gc_generation = -1
                return
            self._last_flip = self._case_start
        interval = now - self._last_flip
        self._intervals.append(interval)
        detail = {
            "frame_ms": interval * 1000,
            "elapsed_s": self.widget.elapsed,
            **self._stages,
            **self._probe_gaps(),
            # 粒子的真值是并行数组, pn 就是存活数 —— 不要读 `widget.particles`
            # (那是按需构建的 dict 列表视图, 每帧读会把兼容层开销算进基准)。
            "particles": self.widget.pn,
            "splashes": len(self.widget.splashes),
            "mound_px": self.widget._mound_height_px(),
            "neck_filling": int(self.widget.elapsed < self.widget._neck_fill_time),
            "gc_ms": self._gc_ms,
            "gc_generation": self._gc_generation,
            **self._flow_rebuild_stats(),
        }
        self._gc_ms = 0
        self._gc_generation = -1
        self._frame_details.append(detail)
        if self.capture_slow_frames and (len(self._visual_frames) < 3 or
                detail["frame_ms"] > self._visual_frames[-1]["frame_ms"]):
            self._visual_frames.append({
                "frame_ms": detail["frame_ms"], "at": time.perf_counter(),
                "state": {name: copy.deepcopy(getattr(self.widget, name))
                          for name in self._STATE_FIELDS},
            })
            self._visual_frames.sort(key=lambda frame: frame["frame_ms"], reverse=True)
            del self._visual_frames[3:]
        self._last_flip = now
        if not self.widget.running and self.widget.elapsed >= self.widget.duration:
            self._sampling = False
            self.results.append({
                "period": self.periods[self._index],
                "environment": self._environment,
                "environment_end": benchmark_environment(self.widget),
                "flow_renderer": getattr(self.widget, "flow_renderer", "line_pool"),
                **frame_statistics(self._intervals),
                "stage_mean_ms": {
                    key: sum(frame.get(key, 0) for frame in self._frame_details)
                         / len(self._frame_details)
                    for key in self._stages},
                "slowest_frame_details": sorted(
                    self._frame_details, key=lambda frame: frame["frame_ms"],
                    reverse=True)[:5],
                "frame_trace": self._frame_details,
                "visual_frames": self._visual_frames,
            })
            self._index += 1
            self._event = Clock.schedule_once(self._prepare_case, 0)

    def cancel(self):
        if self.active:
            self._finish(True)

    def _probe_gaps(self):
        """把 flip->flip 的帧时间拆成: 帧间等待 / tick 内探针外 / 绘制到交换。"""
        st = self._stamps
        if not all(k in st for k in ("physics_ms", "update_draw_ms",
                                     "canvas_ms", "previous_swap_ms")):
            return {}
        p0, _ = st["physics_ms"]
        _, r1 = st["update_draw_ms"]
        c0, c1 = st["canvas_ms"]
        s0, s1 = st["previous_swap_ms"]
        out = {"gap_tick_tail_ms": (c0 - r1) * 1000,
               "gap_draw_to_flip_ms": (s0 - c1) * 1000}
        prev = getattr(self, "_prev_swap_exit", None)
        if prev is not None:
            out["gap_between_frames_ms"] = (p0 - prev) * 1000
        self._prev_swap_exit = s1
        return out

    def _install_probes(self):
        self._probe_methods = []
        gc.callbacks.append(self._gc_probe)
        for target, name, stage in (
                (self.widget, "update_particles", "physics_ms"),
                (self.widget, "redraw", "update_draw_ms"),
                (Window, "on_draw", "canvas_ms"),
                (Window, "flip", "previous_swap_ms")):
            original = getattr(target, name)

            def measured(*args, _original=original, _stage=stage, **kwargs):
                before = time.perf_counter()
                try:
                    return _original(*args, **kwargs)
                finally:
                    after = time.perf_counter()
                    self._stages[_stage] = (after - before) * 1000
                    # 额外记时间戳: 把 flip->flip 里四探针之外的部分拆开
                    self._stamps[_stage] = (before, after)

            self._probe_methods.append((target, name, original))
            setattr(target, name, measured)

    def _remove_probes(self):
        gc.callbacks.remove(self._gc_probe)
        for target, name, original in self._probe_methods:
            setattr(target, name, original)
        self._probe_methods = []

    def _gc_probe(self, phase, info):
        if phase == "start":
            self._gc_start = time.perf_counter()
        else:
            self._gc_ms += (time.perf_counter() - self._gc_start) * 1000
            self._gc_generation = max(self._gc_generation, info["generation"])

    def _flow_rebuild_stats(self):
        """把沙流渲染器的**索引重建触发计数**读进这一帧, 读完清零。

        为什么要它: `mesh.indices = ...` 的 setter → 打上 GI_NEEDS_UPDATE →
        下一帧 `VertexInstruction.apply()` 就 `build()` → `Mesh.build()` 拿**整个
        512 槽顶点数组**去 `VertexBatch.set_data()`, `clear_data + add_vertex_data`
        之后 `flags |= V_NEEDUPLOAD`。**改一次索引整块顶点重走一遍并标脏上传。**
        (源码 Kivy 2.3.0: vertex_instructions.pyx:485/460, instructions.pyx:429, vbo.pyx:170)

        ⚠️ 这是**触发条件**的计数, 不是实测 GL 上传流量: 真上传多少由驱动决定。
        渲染器没装载(桌面默认走 line 池)时返回空, 不硬凑 0。
        """
        module = sys.modules.get("flow_texture_experiment")
        if module is None:
            return {}
        stats = dict(module.STATS)
        module.stats_reset()
        return {"index_assigns": stats.get("index_assigns", 0),
                "chunk_clears": stats.get("chunk_clears", 0),
                "flow_chunks": stats.get("chunks", 0),
                "vertex_rebuild_kib": round(stats.get("vertex_bytes", 0) / 1024.0, 1)}

    def _finish(self, cancelled):
        self._sampling = False
        self.active = False
        Window.unbind(on_flip=self._on_flip)
        self._remove_probes()
        if self._event is not None:
            self._event.cancel()
            self._event = None
        self.widget._stop_sound()
        for name, value in self._saved.items():
            setattr(self.widget, name, value)
        pause = time.perf_counter() - self._paused_at
        for effect in self.widget.flares + self.widget.dusts:
            effect["end"] += pause
        if self.widget.flash_end:
            self.widget.flash_end += pause
        self.widget.last_frame = time.perf_counter()
        self.widget.last_tick = self.widget.last_frame if self.widget.running else None
        self.widget._rebuild_height_table()
        self.widget.redraw()
        if self.widget.running:
            self.widget._play_sound()
        self.on_finish(self.results, cancelled)
```

## `tools/flow_numpy.py`

*125 行 / 5,292 字节 —— 逐字原文, 未删改*

```python
"""沙流粒子的向量化物理内核(1.3 / numpy 路线)。

为什么需要它: MuMu 实测 2525 颗粒时 `update_particles` 占 5.79ms(整帧 35%),
每颗粒 ~2.3µs 全是 CPython 字节码 —— **瓶颈不在 GPU**(粒子全关掉帧仍有 8.61ms)。
把粒子全砍光帧也只有 9.7ms, 所以 +200% 必须连物理一起打掉。

⚠️ 逐位等价的三条硬规则(违反任何一条, 画面就会变):

1. **结合律必须照抄**。Python 的 `y += vy*dt + 0.5*g*dt*dt` 等价于
   `y + ((vy*dt) + (((0.5*g)*dt)*dt))`; numpy 里若写成 `py + pvy*pdt + 0.5*g*pdt*pdt`
   会按 `(py + (pvy*pdt)) + (...)` 求值 —— **差一个 ULP**。凡有多项相加, 一律显式加括号。

2. **只算 `[0:n]` 切片**。数组尾部是上一轮的残留值(可能是任意数), 整数组参与运算会
   把垃圾算进去(还可能触发 warning)。

3. **随机数不在这里抽**。命中事件的下标升序返回给调用方, 由调用方按原顺序回放
   `rand() < 0.25` → `rand() < 0.50` → `uniform ×2` → `choice`。

已实测(本机 numpy 2.2.2, 20 万随机样本, 与 CPython 逐位对比):
`x**0.5` / `sqrt` / `sin` / 乘加 **全部 0 ULP 差**。
"""

import numpy as np


def step(px, py, pvy, pxo, pwp, pwa, psz, pdt, n, c):
    """把 [0:n) 的粒子推进一帧。原地改 py/pvy/px。

    参数
    ----
    px, py, pvy : 每帧变化的字段(会被原地写回)
    pxo, pwp, pwa, psz : spawn 后不变的字段(只读)
    pdt : 本帧步长(只有本帧新生的粒子有偏值, 其余为 dt)
    n   : 存活粒子数
    c   : 常量字典, 见下面 keys()

    返回
    ----
    (hit_idx, peak_offset)
    hit_idx     : 命中(触底)粒子的下标, **升序**, 调用方据此回放随机数与删除
    peak_offset : 更新后的沙堆中心 EMA 偏移
    """
    sl = slice(0, n)
    y = py[sl]
    vy = pvy[sl]
    dt = pdt[sl]

    g = c["g"]
    g_abs = c["g_abs"]
    mound_top = c["mound_top"]

    old_y = y.copy()
    old_vy = vy.copy()

    # y += vy*dt + 0.5*g*dt*dt —— 括号位置与原式一一对应(见模块头 规则 1)
    y = y + (vy * dt + 0.5 * g * dt * dt)
    vy = vy + g * dt

    hit = y <= mound_top
    if hit.any():
        d = old_y - mound_top
        distance = np.maximum(d, 0.0)          # d if d > 0 else 0
        v = -old_vy
        speed = np.maximum(v, 0.0)             # v if v > 0 else 0
        denom = speed + np.sqrt(speed * speed + 2 * g_abs * distance)
        denom = np.where(denom > 1e-6, denom, 1e-6)
        hit_dt = 2 * distance / denom
        hit_dt = np.minimum(hit_dt, dt)        # hit_dt if hit_dt < step_dt else step_dt
        y = np.where(hit, mound_top, y)
        vy = np.where(hit, old_vy + g * hit_dt, vy)

    fd = c["gen_y"] - y
    fallen_dist = np.maximum(fd, 0.0)          # fd if fd > 0 else 0

    # 管内填满(shrink=1), 出管后按流量守恒 A·v=常数收缩, 近底 30px 喇叭口微扩
    # ⚠️ 标量版用 `if y > lower_cut: shrink = 1.0` 跳过这一整段; 向量化会对全部粒子求值,
    # 而 lower_cut 以上的粒子 bottom_tube < 0 → 底数可能为负 → power 出 NaN + RuntimeWarning。
    # 底数夹到 1.0 只影响本来就被 np.where 丢弃的那批(y > lower_cut), 在用到的分支上
    # 底数恒 ≥ source_speed_squared(3600), 夹取不改变任何数值。
    below_tube = c["lower_cut"] - y
    v_at_y = np.power(np.maximum(c["source_speed_squared"] + 2 * g_abs * below_tube, 1.0), 0.5)
    target = np.power(c["source_speed"] / v_at_y, 0.5)
    target = np.where(target <= 0.70, 0.70, target)
    shrink_body = np.where(below_tube < 40.0,
                           1.0 + (target - 1.0) * (below_tube / 40.0),
                           target)
    dist_to_floor = y - mound_top
    shrink_body = np.where((dist_to_floor > 0.0) & (dist_to_floor < 30.0),
                           shrink_body * (1 + (1 - dist_to_floor / 30.0) * 0.4),
                           shrink_body)
    shrink = np.where(y > c["lower_cut"], 1.0, shrink_body)

    cx = c["cx"]
    x = (cx + pxo[sl] * shrink
         + np.sin(fallen_dist * 0.07 + pwp[sl]) * pwa[sl] * (1 - shrink * 0.4))

    # 横向 clamp: 管内壁 / 进下球随球内壁过渡
    dy = y - c["lower_center"]
    r = c["Ri2"] - dy * dy
    raw_ball = np.where(r > 0.0, np.sqrt(np.maximum(r, 0.0)), 0.0)
    t = np.minimum((c["lower_top"] - y) / 30.0, 1.0)
    lim_ball = c["tube_lim"] + (raw_ball - c["tube_lim"]) * t
    lim = np.where(y >= c["lower_top"], c["tube_lim"], lim_ball)
    lim = lim - np.where(psz[sl] > 1, psz[sl], 0.5)     # half_stroke
    lim = np.maximum(lim, 0.0)
    x = cx + np.clip(x - cx, -lim, lim)

    px[sl] = x
    py[sl] = y
    pvy[sl] = vy

    if not hit.any():
        return _EMPTY, _EMPTY, c["peak_offset"]

    # 命中粒子的沙堆中心 EMA —— 顺序必须按下标升序, 与原标量循环一致
    peak_offset = c["peak_offset"]
    if mound_top > c["lower_bot"] + 1:
        for xv in x[hit]:
            peak_offset = peak_offset * 0.97 + (xv - cx) * 0.03
    # hit_dt 必须原样带出去: 调用方要用 `step_dt - hit_dt` 作为 splash 的 _step_dt,
    # 从 post-hit 的 vy 反推会差 ULP。
    return np.nonzero(hit)[0], hit_dt[hit], peak_offset


_EMPTY = np.empty(0, dtype=np.intp)
```

## `tools/flow_texture_experiment.py`

*251 行 / 11,492 字节 —— 逐字原文, 未删改*

```python
"""Opt-in endpoint texture batching; cap geometry and palette order are retained."""

from array import array
import math
from struct import Struct

try:
    import numpy as np
except ImportError:                          # 兜底: 退回逐颗粒 pack_into
    np = None

from kivy.graphics import BindTexture, Mesh, RenderContext
from kivy.graphics.opengl import glGetIntegerv, GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
from kivy.graphics.texture import Texture

import flow_batch_experiment


FLOAT32 = Struct("<f")
# 三个端点(x, bottom, top)连续放在同一个颗粒的 12 字节里 -> 一次 pack_into 写完。
# 每颗粒占 3 个纹素, u = (3i + k + 0.5) / (3 * CHUNK), 步长对所有分块都相同
# (capacity 恒为 CHUNK), 所以 shader 里只要一个 uniform。
FLOAT3 = Struct("<3f")
TEXELS_PER_PARTICLE = 3
TEXEL_STEP_UNIFORM = "texel_step"
CHUNK = flow_batch_experiment.FlowBatch.CHUNK

# ---- 诊断计数(每帧由 update() 清零) -------------------------------------------
# 为什么需要: `mesh.indices = ...` 的 setter 会 `flag_data_update()` →
# `VertexInstruction.apply()`(每帧) 看到 GI_NEEDS_UPDATE 就 `build()` →
# `Mesh.build()` 拿**整个 512 槽顶点数组**去 `VertexBatch.set_data()` →
# `clear_data()` + `add_vertex_data(全部顶点)` + `flags |= V_NEEDUPLOAD`。
# **改一次索引 = 整块顶点重新走一遍并标脏上传**, 不是"只改个数字"。
# (源码: vertex_instructions.pyx:485/460, instructions.pyx:429, vbo.pyx:170, Kivy 2.3.0)
# 这里只统计**触发条件**(赋值次数 × 顶点表字节), 不等于实测 GL 上传流量。
STATS = {"index_assigns": 0, "vertex_bytes": 0, "chunk_clears": 0, "chunks": 0,
         "buckets": 0, "neutralized": 0}

# 中性化用的端点: x 推到画面外, 该槽位的线整条被裁掉(出不了像素)。
# shader: position = vec2(x, mix(bottom, top, vTexCoords0.y)) + vPosition
PAD_ENDPOINT = FLOAT3.pack(-1e5, 0.0, 0.0)


def stats_reset():
    for key in STATS:
        STATS[key] = 0
TEXEL_STEP = 1.0 / (CHUNK * TEXELS_PER_PARTICLE)


VERTEX_SHADER = """
$HEADER$
uniform sampler2D endpoints;
uniform float texel_step;
float read_float(float u) {
    vec4 b = floor(texture2D(endpoints, vec2(u, 0.5)) * 255.0 + 0.5);
    float exponent = mod(b.a, 128.0) * 2.0 + floor(b.b / 128.0);
    if (exponent == 0.0) {
        return 0.0;
    }
    float fraction = b.r + b.g * 256.0 + mod(b.b, 128.0) * 65536.0;
    float sign_value = b.a >= 128.0 ? -1.0 : 1.0;
    return sign_value * (1.0 + fraction / 8388608.0) * exp2(exponent - 127.0);
}
void main(void) {
    float x = read_float(vTexCoords0.x);
    float bottom = read_float(vTexCoords0.x + texel_step);
    float top = read_float(vTexCoords0.x + texel_step * 2.0);
    vec2 position = vec2(x, mix(bottom, top, vTexCoords0.y)) + vPosition;
    frag_color = color * vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
void main(void) {
    gl_FragColor = frag_color;
}
"""


class TextureFlowBatch(flow_batch_experiment.FlowBatch):
    def __init__(self, group, width, reserve=0):
        super().__init__(group, width)
        for chunk in range(math.ceil(reserve / self.CHUNK)):
            self._ensure_part(chunk, min(self.CHUNK, reserve - chunk * self.CHUNK))

    def _ensure_part(self, chunk, count):
        if chunk == len(self.parts):
            binding = BindTexture(index=1)
            mesh = Mesh(mode=self.mode)
            self.group.add(binding)
            self.group.add(mesh)
            self.parts.append([mesh, array("f"), array("H"), 0, 0, None, None, binding])
        part = self.parts[chunk]
        if part[3] < count:
            # 固定 capacity = CHUNK: u 步长与分块无关, shader 只需一个 uniform。
            capacity = self.CHUNK
            data = bytearray(capacity * TEXELS_PER_PARTICLE * 4)
            texture = Texture.create(
                size=(capacity * TEXELS_PER_PARTICLE, 1), colorfmt="rgba")
            texture.mag_filter = texture.min_filter = "nearest"

            def reload_data(target):
                target.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")

            reload_data(texture)
            texture.add_reload_observer(reload_data)
            span = capacity * TEXELS_PER_PARTICLE
            vertices = array("f", (
                value for i in range(capacity) for dx, dy, end in self.template
                for value in (dx, dy, (i * TEXELS_PER_PARTICLE + 0.5) / span, end)))
            indices = array("H", (
                index + i * len(self.template)
                for i in range(capacity) for index in self.indices))
            part[1:4] = vertices, indices, capacity
            part[5:7] = texture, data
            part[7].texture = texture
            part[0].vertices = vertices
        return part

    def update(self, view, indices, top_limit, motion_scale=1):
        """`view` 是 widget 的 `_pv`(本帧 list 快照), `indices` 是本桶的粒子下标。

        按下标读原生 float, 不再逐颗粒取 numpy 标量。
        """
        STATS["buckets"] += 1
        ys = view.y
        vys = view.vy
        trails = view.tl
        xs = view.x
        total = len(indices)
        chunks = -(-total // self.CHUNK)
        # 向量化: 本桶所有颗粒的 (x, 底端, 顶端) 一次算完, 再 astype('<f4') 出字节。
        # 逐位等价已实测: astype('<f4') 与 struct.pack('<f') 对 30 万样本(含 0/-0/inf/
        # denormal/float32 极值)完全相同, 整段公式的字节输出也完全相同
        # —— 见 tools/test_pack_equiv.py。
        # 阈值由 _FlowView.use_np 统一决定(见 main.py:_NUMPY_MIN): 粒子少时
        # numpy 的逐桶固定开销盖过收益。
        use_np = np is not None and total > 0 and view.use_np
        if use_np:
            nidx = np.array(indices, dtype=np.intp)
        else:
            pack = FLOAT3.pack_into
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = total - start
            if count > self.CHUNK:
                count = self.CHUNK
            part = self._ensure_part(chunk, count)
            mesh, _vertices, _indices, _capacity, previous, texture, data, _binding = part
            if use_np:
                idx = nidx[start:start + count]
                bottom = view.ny[idx]
                # 算式与逐字相同: (-vy) / (vy*tl)/ms / 下限 2 / 上限 top_limit
                vy = np.abs(view.nvy[idx])
                trail = vy * view.ntl[idx] / motion_scale
                np.maximum(trail, 2.0, out=trail)
                top = bottom + trail
                np.minimum(top, top_limit, out=top)
                blk = np.empty((count, 3), dtype=np.float64)
                blk[:, 0] = view.nx[idx]
                blk[:, 1] = bottom
                blk[:, 2] = top
                # 尾部(count*12 之后)保持上一帧的陈旧字节, 与逐颗粒写法一致:
                # 那部分不渲染(mesh.indices 已按 count 截断)。
                data[:count * 12] = blk.astype("<f4").tobytes()
            else:
                # 每颗粒只做 1 次 pack_into(x, bottom, top 连续); 数值与逐字相同。
                offset = 0
                for k in range(start, start + count):
                    i = indices[k]
                    bottom = ys[i]
                    vy = vys[i]
                    if vy < 0:
                        vy = -vy
                    trail = vy * trails[i] / motion_scale
                    if trail < 2:
                        trail = 2
                    top = bottom + trail
                    if top > top_limit:
                        top = top_limit
                    pack(data, offset, xs[i], bottom, top)
                    offset += 12
            # ⚠️ 顺序要紧: 先把纹理内容(含下面的"中性化")写完, 再上传。
            if count > previous:
                # **只在这里赋值** —— 每块的索引只增不减, 暖机之后基本不再发生。
                # 旧写法是 `previous != count` 就赋值 ⇒ 粒子数一变, 整块 160 KiB 顶点
                # 就走一遍 clear_data+add_vertex_data 并把整个 VBO 标脏。
                # 实测(5s 档, 1529 颗): 24 块全变 ⇒ 24 次赋值 / 2112 KiB / 帧。
                mesh.indices = _indices[:count * len(self.indices)]
                part[4] = count
                STATS["index_assigns"] += 1
                STATS["vertex_bytes"] += len(_vertices) * 4
            elif count < previous:
                # 缩了: **不动 indices**(动了又触发整块重建), 改把用不到的槽位在端点
                # 纹理里推到画面外 —— shader 里 x 直接决定横向位置, -1e5 时整条线被裁掉。
                # 代价是每帧多处理"历史最大 − 当前"那几个顶点, 换来不重走顶点表。
                data[count * 12:previous * 12] = PAD_ENDPOINT * (previous - count)
                STATS["neutralized"] += previous - count
            texture.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
            STATS["chunks"] += 1
        for part in self.parts[chunks:]:
            if part[4]:
                # 整块不用了: 这里仍清空索引 —— icount==0 时 build() 直接 clear_data(),
                # **不会**重走顶点表, 而且实测只有 0~4 次/帧。
                part[0].indices = array("H")
                part[4] = 0
                STATS["chunk_clears"] += 1


def install(widget_class):
    build = widget_class._build_dynamic_canvas

    def build_texture_batches(self):
        build(self)
        units = glGetIntegerv(GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS)[0]
        if units < 1:
            raise RuntimeError("Vertex texture sampling is unavailable")
        context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        context.shader.vs = VERTEX_SHADER
        context.shader.fs = FRAGMENT_SHADER
        if not context.shader.success:
            raise RuntimeError("Endpoint texture shader failed to compile")
        # 用 RenderContext 的 __setitem__ 设 uniform(shader[...] 在 Kivy 2.3.0 上不支持
        # 下标赋值, 2.3.1 才加 —— 设备上是 2.3.0, 写 context.shader[...] 会 TypeError 崩)。
        context[TEXEL_STEP_UNIFORM] = TEXEL_STEP
        context["endpoints"] = 1
        first_group = next(iter(self._stream_pools.values()))[0]
        position = self.canvas.children.index(first_group)
        self._flow_batches = {}
        for key, (group, color, pool) in self._stream_pools.items():
            reserve = len(pool)
            self.canvas.remove(group)
            group.clear()
            group.add(color)
            pool.clear()
            context.add(group)
            self._flow_batches[key] = TextureFlowBatch(group, key[1], reserve)
        self.canvas.insert(position, context)
        self._flow_texture_context = context

    def draw_texture_batches(self):
        for key, bucket in self._group_stream_particles().items():
            view, indices = flow_batch_experiment.flow_bucket(self, bucket)
            self._flow_batches[key].update(
                view, indices, self._taper["y_bot"], self._particle_motion_scale)

    widget_class._build_dynamic_canvas = build_texture_batches
    widget_class._draw_stream = draw_texture_batches
    widget_class.flow_renderer = "mesh_endpoint_texture"
```

## `tools/flow_batch_experiment.py`

*151 行 / 6,523 字节 —— 逐字原文, 未删改*

```python
"""Opt-in benchmark experiment; production keeps the original Line pools."""

from array import array
import math

from kivy.graphics import Mesh


class DictFlowView:
    """老 main.py(粒子还是 dict 列表)的只读适配器。

    正式路径上 `_group_stream_particles()` 返**下标**、widget 上有 `_pv` 数组快照,
    走不到这里; 它只为 `tools/inspect_flow.py --source <main 分支的 main.py>` 那套
    对照闸门保留 —— 两侧用同一份渲染器, 只有被对照的 main.py 不同。
    """

    __slots__ = ("n", "x", "y", "vy", "tl", "sz", "light", "wp")

    def __init__(self, particles):
        self.n = len(particles)
        self.x = [p["x"] for p in particles]
        self.y = [p["y"] for p in particles]
        self.vy = [p["vy"] for p in particles]
        self.tl = [p.get("trail_time", 0.08) for p in particles]
        self.sz = [p["size"] for p in particles]
        self.light = [p["is_light"] for p in particles]
        self.wp = [p.get("wobble_phase", 0) for p in particles]


def flow_bucket(widget, bucket):
    """把 `_group_stream_particles()` 的桶翻成 (view, indices)。

    新版: 桶就是下标列表, view 是 widget 的 `_pv` 数组快照。
    老 main.py(`--source` 对照): 桶里是 dict, 现摊一份适配器 + `range`。
    """
    view = getattr(widget, "_pv", None)
    if view is None:
        return DictFlowView(bucket), range(len(bucket))
    return view, bucket


class FlowBatch:
    CHUNK = 512

    def __init__(self, group, width):
        self.group = group
        self.width = width
        self.parts = []
        if width == 1:
            self.template = [(0, 0, 0), (0, 0, 1)]
            self.indices = [0, 1]
            self.mode = "lines"
        else:
            # Kivy Line 的 10 段圆头帽 + 它的半宽约定。**每颗粒 20 顶点 / 54 索引**
            # (原 24 / 66), 覆盖像素不变:
            #   ① 帽的扇形从**矩形角**起扇, 不从平边中点 (0,0) —— 后者是边界上的点、
            #      不是顶点, 白白多一个顶点;
            #   ② 弧的第 0 个采样点与矩形角**逐位相同** —— 原写法里
            #      (center, first, arc0) 是零面积退化三角形, 一并去掉。
            #   凸多边形从任一顶点起扇覆盖的区域完全相同, 所以可见像素一致。
            angle = math.pi / 2
            half_pi = float(array("f", [math.pi / 2])[0])
            a0, a1 = angle - half_pi, angle + half_pi
            right = (math.cos(a0) * width, math.sin(a0) * width)
            left = (math.cos(a1) * width, math.sin(a1) * width)
            self.template = [
                (*right, 0), (*right, 1), (*left, 1), (*left, 0)]
            self.indices = [0, 1, 2, 0, 2, 3]
            for end, direction, first, last in ((0, -1, 0, 3), (1, 1, 1, 2)):
                base = len(self.template)
                # 弧采样点取 i=1..8 (i=0 与 first 重合, i=10 就是 last)
                for i in range(1, 9):
                    a = a0 + direction * (a1 - a0) * i / 10
                    self.template.append(
                        (math.cos(a) * width, math.sin(a) * width, end))
                pts = [first] + [base + k for k in range(8)] + [last]
                for k in range(8):          # 10 个点的扇形 = 8 个三角形
                    self.indices.extend((pts[0], pts[k + 1], pts[k + 2]))
            self.mode = "triangles"

    def update(self, view, indices, top_limit, motion_scale=1):
        """`view` 是粒子快照(`_pv` 或 dict 适配器), `indices` 是本桶的粒子下标。

        按下标读原生 float —— 不再逐颗粒取 numpy 标量(那比读 dict 还慢)。
        """
        stride = len(self.template) * 4
        index_stride = len(self.indices)
        ys = view.y
        vys = view.vy
        trails = view.tl
        total = len(indices)
        chunks = math.ceil(total / self.CHUNK)
        for chunk in range(chunks):
            start = chunk * self.CHUNK
            count = min(self.CHUNK, total - start)
            if chunk == len(self.parts):
                mesh = Mesh(mode=self.mode)
                self.group.add(mesh)
                self.parts.append([mesh, array("f"), array("H"), 0, 0])
            part = self.parts[chunk]
            mesh, vertices, indices_arr, capacity, previous = part
            if capacity < count:
                capacity = min(self.CHUNK, max(32, count, capacity * 2))
                vertices = array("f", [0]) * (capacity * stride)
                indices_arr = array("H", (
                    index + i * len(self.template)
                    for i in range(capacity) for index in self.indices))
                part[1:4] = vertices, indices_arr, capacity
            for i in range(count):
                pi = indices[start + i]
                x, bottom = view.x[pi], ys[pi]
                top = min(top_limit, bottom + max(
                    2, abs(vys[pi]) * trails[pi] / motion_scale))
                base = i * stride
                for vertex, (dx, dy, end) in enumerate(self.template):
                    offset = base + vertex * 4
                    vertices[offset] = x + dx
                    vertices[offset + 1] = (top if end else bottom) + dy
            mesh.vertices = vertices
            if previous != count:
                mesh.indices = indices_arr[:count * index_stride]
                part[4] = count
        for part in self.parts[chunks:]:
            if part[4]:
                part[0].indices = array("H")
                part[4] = 0


def install(widget_class):
    build = widget_class._build_dynamic_canvas

    def build_batches(self):
        build(self)
        self._flow_batches = {}
        self._flow_reserve_counts = {
            key: len(pool) for key, (_group, _color, pool) in self._stream_pools.items()}
        for key, (group, color, pool) in self._stream_pools.items():
            group.clear()
            group.add(color)
            pool.clear()
            self._flow_batches[key] = FlowBatch(group, key[1])

    def draw_batches(self):
        for key, bucket in self._group_stream_particles().items():
            view, indices = flow_bucket(self, bucket)
            self._flow_batches[key].update(
                view, indices, self._taper["y_bot"], self._particle_motion_scale)

    widget_class._build_dynamic_canvas = build_batches
    widget_class._draw_stream = draw_batches
    widget_class.flow_renderer = "mesh_batch"
```

