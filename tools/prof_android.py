# -*- coding: utf-8 -*-
"""设备端**函数级**每帧耗时剖面 —— 只在存在标记文件时生效(2026-10-07)。

## 开关

**开灯要重启**: 标记文件必须在 **app 启动时**就存在(没有标记则一个方法都不包, 零开销)。
**关灯不用**: 删掉标记, 最多 `DUMP_INTERVAL` 秒后自动停(不再累加、不再打印)。

## 为什么要另做一套

上一轮是在 `main.py` 里**临时插桩**、量完再手工撤掉。那个流程**撤错过一次**: 还原时
`cp` 回来的快照里留着插桩的残余(`_globals`/`_SP0`), 闸门直接 `NameError`。
而且每换一个要量的函数就得再改一次 main.py、再推一次、再撤一次 —— 极易出错。

改成**常驻挂点 + 标记文件开关**:

    adb shell touch /data/data/org.shalou.hourglass/files/app/prof.on   # 开
    adb shell rm    /data/data/org.shalou.hourglass/files/app/prof.on   # 关(不必重启)

`logcat | grep HGPROF` 就能拿到每帧均值。改要量的函数只需改本文件顶部的 `METHODS`。

## 与 `frame_benchmark.py` 的四栏探针的关系

四栏(物理/图元/Canvas/前次Swap)是**粗分**, 只能告诉你"哪一大类贵"; 本模块把它拆到
**函数**。两者**不要同时开**: 四栏探针自己也包了 `update_particles` 与 `redraw`,
叠起来两边都会被对方算进去(而且 cProfile 那种"报 −49%、实测 −9%"的夸大也要防)。
⇒ **量四栏时把标记文件删掉。**

## ★ 设备/桌面 ≈ **2.1×**(2026-10-07 用真 `_MoundProfile` 标定, 四项独立吻合)

**桌面测量可以当设备数的预测用** —— 乘 2.1。标定方式是在**本机**跑同一批函数、再与设备
实测逐项对:

| 函数 | 桌面 ms/帧 | ×2.1 | 设备实测 |
|---|---|---|---|
| `_draw_mound_shape` | 0.196 | 0.41 | **0.43** ✓ |
| `_draw_surface_markers` | 0.113 | 0.24 | **0.28** ✓ |
| `_draw_upper_shape` | 0.077 | 0.16 | **0.17** ✓ |
| `_upper_level_for`(3 次合计) | 0.104 | 0.22 | **0.24** ✓ |

⚠️ 这是 **MuMu** 上的比值, 不是 ARM 真机的 —— 换设备要重标。也别拿它去外推
`glTexSubImage2D` 这类**纯 GL 调用**的代价(那部分桌面与设备差得更多)。

## ⚠️ 三条口径声明(读数据前先看)

1. **包在外面的函数会把里面的算进去** —— `redraw` 的数字含 `_draw_stream` /
   `_draw_neck_grains` / ... 它们的和。**要看的是叶子, 不是根。**
2. **每包一层多两次 `perf_counter`** —— 单次约 0.1~0.2µs。所以**不要**去包每帧调用几十次的
   小函数(如 `_upper_area` / `_mound_contact_h`), 那会把量具变成被测对象。
3. **采样是"每次调用"而非"每帧"** —— 名义上是"每帧均值", 但若被包的方法一帧调用多次,
   它除的仍是帧数 ⇒ 那个数已经是"每帧总计", 直接可用。
"""

import os
import sys
import time

from kivy.clock import Clock
from kivy.logger import Logger

# 要量的函数。**只列每帧调一次的**(见口径声明 2)。
# ⚠️ 名单里的名字若在类上不存在(比如渲染器改名), install() 会跳过并打印一行, 不报错。
METHODS = (
    "tick",
    "redraw",
    "update_particles",
    "_spawn_bg_splashes",
    "_draw_stream",
    "_group_stream_particles",
    "_draw_neck_grains",
    "_draw_mound_shape",
    "_draw_upper_shape",
    "_draw_surface_markers",
    "_sync_mound_frame",
    "_upper_level_for",
    "_neck_sand_side",
    "_neck_solid_rect",          # 不存在也没关系, 用来演示"跳过"
)

# **模块里的函数/方法**也要能量 —— 上面 `METHODS` 只包 widget 类的方法, 而最大的两块
# (`flow_numpy.step` 主粒子物理、`TextureFlowBatch.update` 沙流打包) 都在别处。
# 形如 ("模块名", "函数名") 或 ("模块名", "类名.方法名")。
EXTRA_TARGETS = (
    ("flow_numpy", "step"),
    ("flow_texture_experiment", "TextureFlowBatch.update"),
    ("flow_splash_experiment", "SplashBatch.update"),
    ("flow_splash_experiment", "SplashBatch.update_arrays"),
)

DUMP_INTERVAL = 4.0      # 每 4 秒打一行; 一轮 15 秒的周期约 3~4 行
TAG = "HGPROF"

_ACC = {}
_FRAMES = 0
_MARKER = None
_ENABLED = False


def _wrap(cls, name, count_frame=False):
    """把 `cls.name` 包成计时版。返回 True 表示挂上了。"""
    orig = getattr(cls, name, None)
    if orig is None or not callable(orig):
        return False
    # ⚠️ 静态方法**绝不能**包成实例方法 —— 会把第一个实参当 `self` 传进去
    #    (踩过: `_sync_rects` 是 `@staticmethod`, 包了之后每帧 TypeError, 应用启动即崩)。
    import inspect
    if isinstance(inspect.getattr_static(cls, name), staticmethod):
        return False

    _count = count_frame

    def wrapper(self, *args, _orig=orig, _name=name, **kwargs):
        t0 = time.perf_counter()
        if _count and _ENABLED:
            # ⚠️ 打点基准必须在**帧首**置位。写在 finally(帧尾)会把"帧与帧之间的等待"
            #    算进第一个区间 —— 实测 `@phys_main` 印出 10~12ms 而整帧 `tick` 只有 8ms,
            #    那个自相矛盾就是这个错。
            _LAST[0] = t0
        try:
            return _orig(self, *args, **kwargs)
        finally:
            if _ENABLED:
                global _FRAMES
                _ACC[_name] = _ACC.get(_name, 0.0) + (time.perf_counter() - t0)
                if _count:                     # 帧数直接数 `tick`, 不再挂一个 0 间隔回调
                    _FRAMES += 1

    wrapper.__name__ = name            # Kivy 的 weakmethod 按 __name__ 回查属性
    wrapper.__doc__ = orig.__doc__
    setattr(cls, name, wrapper)
    return True


_LAST = [0.0]


def _mark(name):
    """区间打点: 把"距上一次打点"的时间记到 `name` 上。

    ⚠️ 与 `_wrap` 的时间**不重叠也不重复计数**: `_wrap` 记的是被包函数自己的耗时段,
    打点记的是两个打点之间的整段(含其间调用的被包函数)。⇒ **读的时候不要把它们相加**。
    """
    if not _ENABLED:
        return
    t = time.perf_counter()
    _ACC["@" + name] = _ACC.get("@" + name, 0.0) + (t - _LAST[0])
    _LAST[0] = t


def _dump(_dt=None):
    global _FRAMES, _ENABLED
    # 标记文件可以随时增删, 不用重启 app(`touch` / `rm` 即可)
    exists = bool(_MARKER) and os.path.exists(_MARKER)
    if not exists:
        if _ENABLED:
            _ENABLED = False
            Logger.info("%s disabled", TAG)
        return
    _ENABLED = True
    if _FRAMES < 1:
        return
    n = _FRAMES
    items = sorted(_ACC.items(), key=lambda kv: -kv[1])
    # ⚠️ 单位是**毫秒**且保留 3 位 —— 用秒 + `%.3f` 会把 0.0008 与 0.0014 都印成 "0.001",
    #    量出来一片"1ms 或 0ms", 什么都看不出(第一版就栽在这)。
    Logger.info("%s frames=%d ms/frame> %s" % (
        TAG, n, " ".join("%s=%.3f" % (k, v * 1000.0 / n) for k, v in items)))
    _ACC.clear()
    _FRAMES = 0


def _wrap_module_target(mod_name, dotted, label):
    """包一个模块里的函数/方法。返回 True 表示挂上了。"""
    mod = sys.modules.get(mod_name)
    if mod is None:
        return False
    owner, _, attr = dotted.rpartition(".")
    target = mod
    for part in (owner.split(".") if owner else []):
        target = getattr(target, part, None)
        if target is None:
            return False
    orig = getattr(target, attr, None)
    if orig is None or not callable(orig):
        return False

    def wrapper(*args, _orig=orig, _name=label, **kwargs):
        t0 = time.perf_counter()
        try:
            return _orig(*args, **kwargs)
        finally:
            if _ENABLED:
                _ACC[_name] = _ACC.get(_name, 0.0) + (time.perf_counter() - t0)

    wrapper.__name__ = attr
    setattr(target, attr, wrapper)
    return True


def install(widget_class, marker_path):
    """挂到 `widget_class` 上。**必须在这之后再装渲染器替换的方法都已就位时调用。**"""
    global _MARKER, _ENABLED
    _MARKER = marker_path
    if not os.path.exists(marker_path):
        # 🔴 **没有标记就一个字都不改** —— 否则每个被包的函数每帧白付两次 `perf_counter`
        #    (13 个方法 × 2 × ~0.1µs), 而**出货路径上本来就不该有探针的开销**。
        #    代价: 这一轮改成"开灯需要重启"(关灯仍是删文件即生效, 见 `_dump`)。
        return [], []
    _ENABLED = True
    hooked, skipped = [], []
    for name in METHODS:
        (hooked if _wrap(widget_class, name, count_frame=(name == "tick"))
         else skipped).append(name)
    for mod_name, dotted in EXTRA_TARGETS:
        # ⚠️ 标签要能区分模块 —— `flow_texture_experiment` 与 `flow_splash_experiment`
        #    取 `split("_")[-1]` **都会得到 "experiment"**, 两行数据会撞成一个名字,
        #    在日志里根本分不出哪个是哪个(踩过)。
        _m = mod_name
        for _p in ("flow_", "_experiment"):
            _m = _m.replace(_p, "")
        lab = _m + "." + dotted.split(".")[-1]
        (hooked if _wrap_module_target(mod_name, dotted, lab) else skipped).append(lab)
    # 把区间打点钩子挂进 main(默认是 None ⇒ 不打点时零开销)
    mod = sys.modules.get(widget_class.__module__)
    if mod is not None and hasattr(mod, "_PROF_MARK"):
        mod._PROF_MARK = _mark
    Clock.schedule_interval(_dump, DUMP_INTERVAL)
    Logger.info("%s installed hooked=%s skipped=%s marker=%s"
                % (TAG, ",".join(hooked), ",".join(skipped) or "-", marker_path))
    return hooked, skipped
