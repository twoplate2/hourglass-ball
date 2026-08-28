# 横屏反旋转适配机制完整说明

> 本文完整记录"跳跳的弹珠机 Android 版"怎么解决联想 Y700 这类大屏平板**横着拿时显示竖屏画面**的问题。
> 机制 2026-08 定稿，经多个真机版本迭代成型。从"病根"到"四层协同"到"每行代码"到"整条演进史"全部覆盖。
>
> 想快读：§1 结论 + §2 架构总览 + §10 演进时间线。
> 想改代码：§3~§9（每节给到代码位置 + 为什么），修对应文件走 §12 索引。
> 相关文档：`README.md`「横屏适配」章节（正式口径）、`BUILD_APK.md` 3.21/3.22/3.23（踩坑实录）。

---

## 1. 一句话结论

**不管设备横着拿还是竖着拿，画面永远保持"竖着拿手机"的构图** —— 不是把界面横过来，
而是把整个"竖屏画面"旋转 90° 铺满横屏。玩家横拿时**扭头看**，或者把平板转回竖屏玩。

这套做法叫**反旋转层**（代码里 `LandLayer`），是本 app 横屏适配的最终方案。
展开讲是四条并行的子机制，缺一不可（§2）。

---

## 2. 整体架构：四条子机制怎么协同

打个比方：**方向策略**解决"系统允不允许横竖"，**反旋转层**解决"给了全屏横窗后画面怎么保持竖构图"，
**沉浸式**解决"画面满屏无系统栏"，**生命周期**在前后台切换时把这套状态重新对齐。

### 2.1 横屏四件套（缺一不可）

| 层 | 做什么 | 解决什么 | 代码位置 |
|----|--------|---------|---------|
| manifest 声明 | `screenOrientation=fullSensor` + `resizeableActivity=true` | 让系统在**布局/启动时**决定给全屏窗口（而非半屏盒） | `p4a/hook.py` 注入 |
| 版本标签 | `targetSdk 33` | targetSdk 30 的兼容模式在大屏=半屏盒 | `buildozer.spec` `android.api` |
| **运行时方向守卫（关键解药）** | 启动/回前台/转屏瞬间/每 0.7s，按设备分流重申方向请求 | 顶掉 SDL 引擎启动时的竖屏自报 | `_apply_orientation / _orient_guard / _device_is_wide` |
| 横屏反旋转层 | 横窗时按"等效竖屏窗口"布局再整体旋转 ±90° 铺满；触摸逆变换；弹窗挂层 | 让横拿的画面也和竖拿一模一样 | `LandLayer / RotPopup` |

### 2.2 到底谁说了算（优先级）

排序从决定力大到小：

1. **Manifest 层声明**（最大权）：`screenOrientation=fullSensor` + `resizeableActivity=true` + `targetSdk 33`。
   这是操作系统在布局/启动时真正用来决定**窗口形态**（全屏 vs 半屏 letterbox 盒）和初始方向的依据。
   **大屏能不能全屏，完全取决于这三个声明，不在运行时。**本 app 的策略就是把这层占死。
2. **运行时 `setRequestedOrientation()`**（次大权，本 app 刻意用它）：若被调用会覆盖 manifest 的
   `screenOrientation`。本 app 恰恰**用它来持续重申**，把被 SDL 覆盖掉的方向抢回来（§3.3）。
3. **SDL 自报**（被动/下游，无争权）：SDL 只是读取系统实际给出的方向上报给 app，是观察者。
   本 app 的旋转不是"物理转"而是"app 内反旋转构图"，所以 SDL 报什么方向都不会与 manifest 冲突。

<sub>注：最初这份优先级曾被误判为"运行时不该动方向、只靠 manifest"——而真机证明 ZUI 只认运行时请求，
manifest 声明被 SDL 自报顶掉。所以最终是靠"manifest 占死 + 运行时重申"双管齐下。</sub>

### 2.3 按屏幕比例分流（2026-08-19 加）

方向策略按**物理屏比例**分流。开机量一次短边÷长边（`Display.getRealSize` 的物理分辨率，
**含系统栏、不随旋转变**，不受当前窗口尺寸/状态栏影响）：

| 屏比例 | 归类 | 方向策略 | 为什么 |
|--------|------|---------|-------|
| **16:9 及更宽的屏**（`短/长 ≥ 9:16≈0.5625`，平板类） | 宽屏 | `FULL_SENSOR`(10) 四方向，横拿时反旋转层铺满 | 大屏允许旋转 |
| **比 16:9 瘦长的手机**（18:9=0.5、20.5:9≈0.439） | 窄屏 | `SENSOR_PORTRAIT`(7) 锁竖屏（正竖+倒竖180°） | 瘦长机横拿会撞出横向状态栏压坏画面；小屏看旋转竖构图不适合阅读 |

> manifest 对两类设备**统一声明 fullSensor**（manifest 没法按比例分流），运行时请求优先级更高，
> 由方向守卫按 `_device_is_wide()` 分流覆盖。瘦长手机因此不会进横屏（但启动最初窗口变化前可能有一次抖动，见 §10.5）。

---

## 3. 方向策略（系统层面：让不让你横竖）

这套东西的目的就一句话：**别让系统把 app 判成"竖屏 app"而塞半屏盒**。
ZUI/12L+ 只认"这是不是竖屏 app"，竖屏 app 横拿必被塞盒；反着来——让系统觉得"这是全方向的现代 app"，
横拿就给全屏横窗，app 内部再自理。

### 3.1 `_device_is_wide()`：本机是宽屏还是瘦长机（`main.py:2490-2513`）

```python
_DEVICE_WIDE_MIN = 9.0 / 16.0    # 0.5625，短边/长边 ≥ 9:16 判宽屏
_device_wide_cache = None        # 开机量一次，之后不再变

def _device_is_wide():
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
```

要点：
- **必须用 `getRealSize()`（物理分辨率）**，不能用 `getSize()`/窗口尺寸——后者随当前旋转/状态栏变，
  会误判宽窄。这是物理屏固有属性，开机量一次缓存即可。
- **异常回退宽屏**（`aspect=1.0 → True`）：桌面/读不到时按宽屏处理，保持原有行为
  （桌面 `--landscape` 模拟不受影响）。
- 阈值 0.5625：18:9=0.5、20.5:9≈0.439 都小于它 → 窄屏；16:9=0.5625 恰好等于 → 宽屏。

### 3.2 `_land_angle()`：读系统旋转角决定画几度（`main.py:2516-2528`）

```python
def _land_angle():
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        wm = act.getWindowManager()
        disp = wm.getDefaultDisplay()
        return -90 if disp.getRotation() == 3 else 90
    except Exception:
        return 90
```

- `getRotation()`：`1`(ROTATION_90) → 系统转了 +90°，层就转 **+90°** 抵消；`3`(ROTATION_270) → **−90°**。
- 其余/读不到（桌面 `--landscape` 模拟）→ 固定 `+90`。
- **只在横屏被调**（`apply_orientation: angle=_land_angle() if land else 0`），此时 rot 必为 1 或 3。
- 真机若发现扭头方向不对/画面倒置，**交换 90/−90 两映射即可**（一行）。

### 3.3 `_apply_orientation()`：以毒攻毒，把话语权抢回来（`main.py:4382-4393`）

```python
def _apply_orientation(self):
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        act.setRequestedOrientation(10 if _device_is_wide() else 7)
    except Exception:
        pass
```

为什么叫"以毒攻毒"：
- SDL 启动/`onResume` 会按自己的 hint 调 `setRequestedOrientation`，可能把 manifest 的 `fullSensor`
  **运行时覆盖成竖屏** → ZUI 判定"竖屏 app"塞半屏盒。
- 既然 ZUI 只认运行时请求，那就**用同一个 API 主动重申抢回来**：宽屏抢 `FULL_SENSOR(10)`（四方向），
  瘦长机抢 `SENSOR_PORTRAIT(7)`（正竖+倒竖180°）。
- Android 原生常量：`SCREEN_ORIENTATION_FULL_SENSOR=10`、`SCREEN_ORIENTATION_SENSOR_PORTRAIT=7`。
- 常驻定时 + 触发时机见 §3.4 和 §8。

### 3.4 `_orient_guard()`：常驻方向守卫（`main.py:4395-4411`）

```python
def _orient_guard(self, dt):
    if platform != "android":
        return
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        if _device_is_wide():
            rot = act.getWindowManager().getDefaultDisplay().getRotation()
            if rot in (1, 3):                    # 横置才重申；竖置当前本就竖构图，幂等
                act.setRequestedOrientation(10)
        else:
            act.setRequestedOrientation(7)        # 瘦长机持续锁竖屏，任何横屏自报都被顶掉
    except Exception:
        pass
```

区别：宽屏**只在横置（rot 1/3）时重申** fullSensor（竖置时不动，幂等）；瘦长机**无条件持续锁 7**。

---

## 4. 反旋转层 `LandLayer`（渲染层：让画面保持竖构图）

### 4.1 思路：等效竖屏窗口

核心抽象：把整棵 UI 树装进一个**"等效竖屏窗口"**里。物理窗口可横可大，但布局计算统一折算成
`(短边, 长边)`——竖拿构图。横屏时把这个"等效盒"（短边×长边）以**屏幕中心**整体旋转 ±90° 铺满横屏，
于是屏幕上看到的画面和竖拿时一模一样。竖屏时 `angle=0`、层=窗口，**行为与没有这层完全一致（零回归）**。

层关系（`main.py:4341-4371` 组装）：

```
LandLayer(旋转层, FloatLayout)
 └─ anchor(AnchorLayout, 居中容器, size 由 apply_orientation 控制)
     └─ RootWidget(实际 UI, size_hint_x=None)
```

### 4.2 `__init__`（`main.py:2544-2552`）——画布变换的挂钩

```python
def __init__(self, **kw):
    super().__init__(**kw)
    self.angle = 0            # 渲染旋转角，0=竖屏无旋转
    self._anchor = None       # "等效竖屏窗口"容器，由 App.build 塞入
    with self.canvas.before:
        PushMatrix()
        self._rot = Rotate(angle=0, axis=(0, 0, 1), origin=(0, 0))
    with self.canvas.after:
        PopMatrix()
```

- `PushMatrix` 在画布前保存变换栈，让旋转**只作用于本层之后的绘制，不泄漏**；`PopMatrix` 在 `canvas.after` 还原。
- `Rotate(axis=(0,0,1))` = 绕屏幕法向（垂直于画面）旋转，`origin` 后由 `apply_orientation` 置为屏幕中心。
- 成员变量：`angle`（±90 或 0，决定正/逆变换分支）、`_anchor`（等效盒容器）、`_rot`（Rotate 指令）。

### 4.3 `apply_orientation()`：窗口尺寸变化时重算（`main.py:2554-2570`）

```python
def apply_orientation(self):
    w, h = Window.width, Window.height
    land = (w > h and (platform == "android" or "--landscape" in sys.argv)
            and _device_is_wide())
    self.pos = (0, 0)
    self.size = (w, h)                          # 层永远铺满整窗
    self.angle = _land_angle() if land else 0
    self._rot.angle = self.angle
    self._rot.origin = (w / 2.0, h / 2.0)       # 绕屏幕中心旋转
    if self._anchor is not None:
        self._anchor.size = (h, w) if land else (w, h)
        self._anchor.center = self.center
    return land
```

要点：
- **`land` 三闸**，双保险：`w>h`（窗口横向）+（Android 或桌面 `--landscape` 模拟）+ `_device_is_wide()`。
  瘦长机已被 `_apply_orientation` 锁竖屏（窗口不会横），这里再加一道；桌面普通宽窗维持竖构图居中不动。
- **层 `size=(w,h)` 永远等于物理窗口**（它才是渲染载体）。
- **等效竖屏窗口（短边×长边）**：横屏 `anchor.size=(h,w)`，即把 AnchorLayout 设成"竖看"尺寸——
  横窗 1740×1000 → `(h,w)=(1000,1740)`，窄×高。`_anchor.center = self.center` 居中。
  竖屏 `anchor.size=(w,h)`（=窗口）。
- **为什么绕屏幕中心旋转**：因为 anchor 是普通 AnchorLayout（非 RelativeLayout），其子控件根 RootWidget 的 `pos`
  用的是 **window 绝对坐标**（不是 anchor 局部坐标），所以旋转/逆变换都绕窗口中心做就正好（§4.7）。

### 4.4 `_to_eq` / `_to_win`：正/逆变换的数学（`main.py:2572-2600`）

- `_to_eq(x,y)`：物理窗口坐标 → 等效竖屏坐标（**逆变换**，触摸分发用）。
- `_to_win(x,y)`：等效竖屏坐标 → 物理窗口坐标（**正变换**，`to_parent` 用）。两者互逆（`_to_win(_to_eq(p))==p`）。

```python
def _to_eq(self, x, y):        # 屏幕→逻辑(逆)
    if self.angle == 0:
        return (x, y)
    cx, cy = Window.width/2.0, Window.height/2.0
    dx, dy = x - cx, y - cy
    if self.angle == 90:
        return (cx + dy, cy - dx)      # 逆变换 = 顺时针 90
    else:
        return (cx - dy, cy + dx)      # angle == -90: 逆时针 90

def _to_win(self, x, y):       # 逻辑→屏幕(正)
    if self.angle == 0:
        return (x, y)
    cx, cy = Window.width/2.0, Window.height/2.0
    dx, dy = x - cx, y - cy
    if self.angle == 90:
        return (cx - dy, cy + dx)      # 正变换 = 逆时针 90
    else:
        return (cx + dy, cy - dx)      # angle == -90: 顺时针 90
```

数学推导（Kivy 逆时针为正、y 向上；`cx,cy` 中心，`dx=x-cx, dy=y-cy`）：
- 渲染正变换（逻辑→屏幕）：逻辑偏移 `(lx,ly)` 绕中心转 θ。
  - `θ=90°: (lx,ly)→(-ly, lx)` 即 `sdx=-ly, sdy=lx`
  - `θ=-90°: (lx,ly)→(ly, -lx)` 即 `sdx=ly, sdy=-lx`
- 逆变换（`_to_eq`）反解上式：`angle=90` 时逻辑偏移=`(dy,-dx)`（即 `cx+dy, cy-dx`，顺时针转回）；
  `angle=-90` 时逻辑偏移=`(-dy,dx)`（即 `cx-dy, cy+dx`，逆时针转回）。✓

### 4.5 `to_local` / `to_parent`：为何必须覆写（`main.py:2602-2616`）

```python
def to_local(self, x, y, **k):   return self._to_eq(x, y)
def to_parent(self, x, y, **k):  return self._to_win(x, y)
```

**这是"横屏点不到按钮"第三坑的解法**，单独补齐：
- 当子控件 `on_touch_down` 里 `touch.grab(self)`，后续 move/up **不再经过本层 `on_touch_*`**，而是
  EventLoop **直接派发给被抓控件本体**。派发前 `Window` 用 `parent.to_widget` 链换算坐标——该链**逐祖先调 `to_local`**。
- 整棵树只有本层带旋转，这里注入逆旋转，横屏下按钮的 `on_touch_up` 才能拿到正确坐标。
- 不覆写则 `collide_point` 判不中 + `always_release` 默认 False → **`on_release` 被吞，按钮按下有反馈、抬起无动作**。
- Scatter 是同一套做法（覆写 `to_local/to_parent`）。

### 4.6 `_pass_touch` + `on_touch_*`：横屏触摸逆旋转（`main.py:2618-2638`）

```python
def _pass_touch(self, method, touch):
    if self.angle == 0:
        return method(touch)              # 竖屏直接透传，零回归
    touch.push()
    touch.apply_transform_2d(self._to_eq)
    ret = method(touch)
    touch.pop()
    return ret

def on_touch_down(self, touch):  return self._pass_touch(super().on_touch_down, touch)
def on_touch_move(self, touch):  return self._pass_touch(super().on_touch_move, touch)
def on_touch_up(self, touch):    return self._pass_touch(super().on_touch_up, touch)
```

### 4.7 三个坑（`BUILD_APK.md:3.21`，2026-08-25 实修）——渲染对了≠触摸对了

真机症状：横屏画面正常转成竖屏构图，但**所有按钮都点不到，必须回竖屏才能玩**。三层叠加：

1. **`touch.pos = (x, y)` 赋值是无效变换**。`MotionEvent.pos` 只是个普通元组属性，直接赋值**只改 pos、
   不改 `touch.x/y`**。而 `ButtonBehavior.on_touch_down` 判点击用的恰恰是 `collide_point(touch.x, touch.y)`。
   → 必须用 `touch.apply_transform_2d(transform)`（RelativeLayout 同款），它把 `x/y/pos/ox/oy/px/py` 一起变。
2. **变换只做逆旋转，不要减容器 pos**。Kivy 是单一 window 坐标系（relativelayout 模块文档）：普通布局
   （含 AnchorLayout）下整棵树的 `pos` 数值本来就是布局坐标，只有 RelativeLayout/Scatter/ScrollView 才开
   新坐标系。误把 anchor 当相对坐标系容器、逆旋转后再减 `anchor.pos`，会把所有控件点击判定区**平移出屏幕**
   （横屏 1740×1000 实测偏移 `(370,−370)`）。
3. **grab 之后的 move/up 不走你的 `on_touch_*`**（见 §4.5），必须覆写 `to_local`（逆）/`to_parent`（正）。

> 验证方法（桌面即可，不用真机）：`--landscape` 启动 + 注入合成触摸到按钮的**视觉屏幕位置**（渲染旋转的
> 正变换算出），走 `EventLoop.post_dispatch_input('begin'/'end', t)` 完整还原真实手指的两段派发（窗口树分发 +
> grab 直达）。注意 `Window.dispatch('on_touch_down', t)` 只还原第一段，测不出第 3 个坑。

---

## 5. 弹窗 `RotPopup`：横屏挂旋转层（`main.py:2641-2679`）

Kivy 2.3 的 `ModalView.open()` **硬编码挂 `Window`**，横屏时弹窗不会跟着旋转层走。所以照抄其
`open/_real_remove_widget`，**只把宿主从 Window 换成旋转层**：

```python
def open(self, *_args, **kwargs):
    layer = _land_layer()
    if layer is None or layer.angle == 0:
        return super().open(*_args, **kwargs)    # 竖屏回落原生行为(零回归)
    if self._is_open:
        return
    self._window = layer                          # 宿主从 Window 换成旋转层
    self._is_open = True
    self.dispatch('on_pre_open')
    if not self.pos_hint:
        self.pos_hint = {"center_x": 0.5, "center_y": 0.5}
    layer.add_widget(self)                        # 挂到层而非 Window
    layer.bind(on_resize=self._align_center, on_keyboard=self._handle_keyboard)
    self.center = layer.center
    self.fbind('center', self._align_center)
    self.fbind('size', self._align_center)
    ...                                           # 动画用 Popup 自带 _anim_alpha
```

要点：
- **宿主替换**：`self._window = layer` + `layer.add_widget(self)`。层带旋转渲染，弹窗作为层子项**同样被旋转**，
  横屏内文也保持"竖构图"可读。`self.center = layer.center`（屏幕中心）+ `pos_hint center_x/y=0.5` 居中。
- `layer.bind(on_resize/on_keyboard)`：层无这两个事件，bind **静默无害**；返回键仍走 `RootWidget._on_key_down` 原路径。
- `_real_remove_widget()`（2672-2679）对称清理：从层移除、unbind 两处、复位 `_window=None`。
- **竖屏（`layer.angle==0`）一律回落 `super().open()`**，保零回归。

---

## 6. UI 缩放：横竖屏同一套布局（`RootWidget`）

物理窗口可横可大，但所有布局计算都吃"等效竖屏窗口"——横竖屏一套布局代码只分支一次。

### 6.1 `_veq()`：等效竖屏窗口（`main.py:3127-3132`）

```python
def _veq(self):
    w, h = Window.width, Window.height
    return (w, h) if w <= h else (h, w)    # 短边x长边
```

物理窗口 1740×1000 横屏 → `_veq()=(1000,1740)`（vw=1000, vh=1740）。**尺寸轮询读物理窗口，但布局只吃等效值**——
物理窗口只喂 LandLayer 算旋转/等效盒；若 `_fit_width/_apply_sizes` 误用物理窗口，横竖屏会分叉成两套布局。

### 6.2 `_fit_width()`：算内容宽度（`main.py:3134-3147`）

```python
self._ui_scale   = min(1.0, vh / dp(680))                 # 纵向基准缩放
scaled_fixed     = (dp(H_TOP+H_RTP+H_BETS+H_INFO+H_BOTTOM) * us
                    + dp(10)*5 * us*us + dp(12) * us)     # 缩放后固定开销(222=44+44+44+26+64)
avail_h          = max(100.0, vh - scaled_fixed)          # 留给游戏区净高
want             = avail_h * (CW/CH) + dp(8)              # 520/660 画布比
self.width       = min(vw, want)                          # 窄屏铺满，宽屏列居中
self._font_scale = min(1.0, self.width / dp(360))         # 宽度基准缩放
```

### 6.3 `_apply_sizes()`：把所有固定元素按缩放因子重写（`main.py:4120-4170`）

```python
us = self._ui_scale
uv = us * us                  # 纵向边距平方衰减
fs = self._font_scale * us    # 文本总缩放 = 宽因子 x 纵向因子
self._row_*.height = dp(H_*) * us        # 行高(线性 x us)
self.spacing      = dp(10) * uv          # 行间距(平方 x us²)
row padding 纵向  = dp(4) * uv           # 行内垂直 padding(平方)
self.title_lbl.font_size = sp(18) * fs   # 字号 sp(N) * fs
...按钮宽: mute_btn.width=dp(64)*us, fire_btn.width=dp(110)*us
self.padding = [0,0,0,dp(12)]            # 底部留白不乘 us
```

### 6.4 公式汇总

| 量 | 公式 | 含义 |
|----|------|------|
| `us`（ui_scale） | `min(1.0, 等效高/dp(680))` | 纵向缩放；680=设计基准高度（无需缩放的满高） |
| `uv` | `us*us` | 纵向边距平方衰减，横屏更激进挤空白 |
| `_font_scale` | `min(1.0, 内容宽/dp(360))` | 宽度缩放；360=设计基准宽度 |
| `fs` | `_font_scale*us` | 文本总缩放，字号 `sp(N)*fs` |
| `scaled_fixed` | `dp(222)*us + dp(10)*5*us² + dp(12)*us` | 缩放后五行固定开销（44+44+44+26+64=222） |
| `avail_h` | `max(100, 等效高 - scaled_fixed)` | 留给游戏区净高 |
| `want` | `avail_h*(520/660)+dp(8)` | 画布比换算出的目标内容宽 |
| `width` | `min(等效宽, want)` | 内容实际宽（窄屏铺满/宽屏居中） |

### 6.5 `size_hint_x=None` 的意义

- BoxLayout 里 `size_hint_x` 为数值的控件按比例瓜分剩余空间；设 `None` 表示"**不参与弹性分配，用我的显式 width**"，
  `_apply_sizes` 才能直接改 `.width` 生效。固定宽控件一律 `size_hint_x=None + width=dp(X)`；
  行则是 `size_hint_y=None + height=dp(H_*)`。
- 弹性空白用三种 spacer：`Widget()`（默认 flex 1）、`Widget(size_hint_x=0.95/0.05)`（弹簧）、
  `Widget(size_hint_x=None, width=dp(X))`（固定垫块）。
- `GameArea._redraw`：`s = min(width/CW, height/CH)` 自适配，吃 RootWidget 的 width/height。

---

## 7. 沉浸式全屏：`_enter_immersive` / `_immersive_task`

`buildozer fullscreen=1` 之外的运行时双保险。状态栏/导航栏隐藏由**运行时** `setSystemUiVisibility` 实现。

### 7.1 两个坑（`BUILD_APK.md:3.22/3.23`）

1. **必须投到 UI 线程**（真凶，2026-08-26 dumpsys 实锤）：`setSystemUiVisibility` 从 SDL/Python **线程**直调，
   视图 attach 后触发 `CalledFromWrongThread` 异常，被 `except: pass` 吞掉无声无息 → 竖屏启动期系统栏一直不隐藏、
   **转一次屏才"自愈"**（窗口重排恰好让某次调用绕过检查）。解法见下。
2. **`_enter_immersive(*_)` 必须保留 `*_` 形参**：`schedule_interval` 回调会塞 `dt` 进来，**零参签名**在真机启动
   0.7s 即 `TypeError` 闪退（2026-08-26 logcat 实锤，桌面测不出——桌面不注册该 interval）。
   **通用法则：凡交给 `Clock.schedule_*` 的函数引用，签名必须能收 1 个位置参数。**

### 7.2 `_immersive_task()`：单实例缓存 Runnable（`main.py:4413-4445`）

```python
@classmethod
def _immersive_task(cls):
    if cls._immersive_task_inst is None:
        from jnius import PythonJavaClass, java_method
        class ImmersiveTask(PythonJavaClass):
            __javainterfaces__ = ['java/lang/Runnable']
            @java_method('()V')
            def run(self):
                try:
                    from jnius import autoclass
                    act = autoclass("org.kivy.android.PythonActivity").mActivity
                    View = autoclass("android.view.View")
                    act.getWindow().getDecorView().setSystemUiVisibility(
                        View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
                except Exception:
                    pass
        cls._immersive_task_inst = ImmersiveTask()
    return cls._immersive_task_inst
```

- **单实例缓存**（`cls._immersive_task_inst`）：`PythonJavaClass` 代理若每次新建即弃，Java 侧 proxy 可能被 GC
  回收成悬垂引用（native 崩溃）。单实例反复投递保障稳定。
- **每次 `run()` 重新取 mActivity/View**：防 Activity 重建后引用过期。
- `IMMERSIVE_STICKY`：玩家从屏幕边缘滑入可临时呼出系统栏，几秒后自动隐藏。
- `setSystemUiVisibility` 在 API30+ 已弃用但未移除，targetSdk 33 下仍生效。

```python
def _enter_immersive(*_):
    if platform != "android":
        return
    try:
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        act.runOnUiThread(PlinkoApp._immersive_task())
    except Exception:
        pass
```

---

## 8. 生命周期与触发链（什么时候触发什么）

注册（`main.py:4365-4370`，只 android 分支）：
- `Clock.schedule_once(lambda *_: self._apply_orientation(), 1.0)`：启动 1s 后抢一次方向话语权。
- `Clock.schedule_interval(self._orient_guard, 0.7)`：每 0.7s 常驻重申。
- `Clock.schedule_once(lambda *_: self._enter_immersive(), 1.0)`：启动 1s 后进一次沉浸。
- `Clock.schedule_interval(self._enter_immersive, 0.7)`：每 0.7s 重申沉浸。

| 时机 | 触发链 |
|------|--------|
| 启动 build | `LandLayer`(建层+PushMatrix/Rotate) → `anchor.size_hint=(None,None)`+挂层 → `RootWidget(size_hint_x=None)` 挂 anchor → `layer.apply_orientation()` 首算 → `_fit_width()/_apply_sizes()` → return layer。注册 4 计时器 |
| 启动 1.0s | `schedule_once` 两记：`_apply_orientation()`（SDL 启动序列完成后抢话语权）、`_enter_immersive()` |
| 每 0.7s | `_orient_guard(dt)`（读 rotation 分流重申）+ `_enter_immersive(dt)`（重申沉浸，dt 塞进 `*_`） |
| 转屏瞬间/窗口变化 | `_frame(dt)` 检测 `Window.size` 变化 → `layer.apply_orientation()`（重算角度/等效盒）→ `app._apply_orientation()`（**立即重申不等 0.7s，跟手**）→ `_fit_width()/_apply_sizes()` |
| 切后台 on_pause | `rootw.sfx.pause_out()`（SoundPool.autoPause 静音）；`return True`（**必须 True 保 GL 上下文**） |
| 回前台 on_resume | android: `_apply_orientation()`（SDL 回前台重报方向，再抢一次）+ `_enter_immersive()`（系统栏复活，重隐藏）；`rootw.sfx.resume_out()`；return True |
| 退出 on_stop | `rootw.sfx.close()`；return True |

`_frame` 窗口轮询（`main.py:4172-4185`）：
```python
def _frame(self, dt):
    ws = (Window.width, Window.height)
    if ws != self._last_win_size:
        self._last_win_size = ws
        layer = _land_layer()
        if layer is not None:
            layer.apply_orientation()
        force = getattr(app, "_apply_orientation", None)
        if force is not None:
            force()                 # 窗口一变立即重申，不等守卫周期(转屏跟手)
        self._fit_width()
        self._apply_sizes()
```

**话语权争夺排序**：SDL 在启动/onResume/尺寸变化多时机重报方向 → 我方 `schedule_once(1s)` 抢首唱 +
`schedule_interval(0.7s)` 常驻 + `on_resume` 再抢 + `_frame` 变化立即 force()，保证"**下一句总是我方说的**"。

---

## 9. 打包配置：`buildozer.spec` + `p4a/hook.py`

### 9.1 buildozer 1.5.0 的坑：orientation 只认 4 个值

`buildozer.spec` 的 `[app] orientation` 选项被 **1.5.0 校验器锁死**：只接受 `landscape / portrait /
portrait-reverse / landscape-reverse` 四值。任何其它写法（`sensorPortrait`、`fullSensor`、`sensor`、
`sensorLandscape`、`all` 等 Android 原生 `screenOrientation` 值）都会在解析阶段直接抛错——
`"is not a valid value for orientation"`。

**所以想用 `fullSensor`（真正的"四方向随重力"原生值）不能走 `orientation` 键。** 这就是为什么 manifest 层要走别的路径（§9.2）。

### 9.2 三路径桥接：把 fullSensor + resizeable 落进最终 manifest

| 步骤 | 做法 | 作用 |
|------|------|------|
| 1 | `orientation = portrait, portrait-reverse, landscape, landscape-reverse` | 只满足 buildozer 校验器让构建过；本身不锁方向（p4a 拿到多方向后主 activity 的 `screenOrientation` 往往写成 `unspecified`） |
| 2 | `android.manifest.orientation = fullSensor` | buildozer 的 manifest **直写键**，不走 orientation 校验器，直接把 `screenOrientation=fullSensor` 写进模板 |
| 3 | `p4a.hook` 在 `before_apk_build` 正则兜底强改 | 路径 1 给多方向参数时 p4a 可能写出 `unspecified`，hook 再强制改回 fullSensor，并叠加 `resizeableActivity=true`（+targetSdk33 才能解锁大屏全屏） |

关键：buildozer 校验的是 `orientation` 键（四值），而 Android 系统认的是 manifest 里的 `screenOrientation`（任意原生值）。
四值列表是"过校验的壳"，fullSensor 才是"真给系统的值"，二者靠 `android.manifest.orientation` + hook 桥接。

### 9.3 hook 注入细节（`p4a/hook.py`）

- **定位 manifest**：`_find_manifest` 先测 cwd 下 `src/main/AndroidManifest.xml` 与 `AndroidManifest.xml`（before_apk_build 把 cwd
  设为 dist 目录）；找不到再回退 `self.ctx.distribution.dist_dir`；都找不到返回 None。
- **只匹配主 activity**：正则 `<activity[^>]*org\.kivy\.android\.PythonActivity[^>]*?>`，唯一类名锚点保证只命中
  启动器主 activity，跳过其他自定义 activity/service/receiver；找不到则 info 跳过返回 0。
- **改写 `screenOrientation=fullSensor`**：若已有 `android:screenOrientation="..."` 且值≠fullSensor 则替换；
  否则在开标签尾部 `>` 前插入（`tag[:tag.rfind('>')] + ' android:screenOrientation="fullSensor">' + ...`）。
- **改写 `resizeableActivity=true`**：同款逻辑（31+ 默认 true，显式写双保险）。
- 仅当 `changed>0` 才回写；`before_apk_build`（gradle 前）是生效点，`after_apk_build` 只是幂等兜底（此时 APK 已产出，无实际作用）。

### 9.4 关键配置原样值

```ini
version = 0.5.4
requirements = python3,kivy==2.3.0,pyjnius
p4a.branch = v2024.01.21
p4a.hook = p4a/hook.py
orientation = portrait, portrait-reverse, landscape, landscape-reverse
android.manifest.orientation = fullSensor
fullscreen = 0
android.permissions = VIBRATE
android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a,armeabi-v7a
```

**为何 `android.api=33`（targetSdk 33）是大屏全屏前提**：Android 12L+（sw≥600dp，如 ZUI）对"兼容模式"走特殊窗口策略——
`targetSdk≤30` 的 app 被塞**固定比例 letterbox 盒**（ZUI 近正方形半屏盒），此时即便声明 fullSensor 四方向，系统也只给
那个半屏框，画面被缩小。`targetSdk≥33` 且声明全方向+resizeable 才给全屏窗口。所以 `android.api` 别降回 30——那等于把全屏让给缩小的兼容盒。

> ⚠️ `fullscreen=0` 是刻意保持的（沉浸由运行时 `_enter_immersive()` 实现），与方向适配两码事。
> 0.5.1(=1)/0.5.2(=0) 当时都闪退，真凶是 `_enter_immersive` 零参签名 bug，不是 fullscreen 本身（红鲱鱼）。

---

## 10. 演进史：从"锁竖屏"到"四方向+反旋转层"

### 10.1 分阶段叙事

**阶段一 锁竖屏的"反向修正期"（08-16）**：一边锁竖屏一边追查"为何真机变横"。
`69e2055` 修最表层 bug（manifest 写死 portrait → sensorPortrait）；`065c053` 引入 p4a.hook 对抗 Y700/ZUI；
`d3a50e2` 加运行时兜底（on_resize 检测横屏拉回）；`08e3c85` 意识到 targetSdk≥31 在 12L+ 大屏强制多窗口、系统无视
`screenOrientation`，于是降 targetSdk 30 + 运行时反复拉回；`c92abe4` 落实为 spec `android.api=30`。
但同一枚 hook 马上被 `fce329f` 反着改——`resizeableActivity=false` 干扰 targetSdk30 兼容模式的 letterbox 窗口尺寸，
横屏时窗口被压成接近正方形（1519×1754）、画面拉伸变形。`d5a5076` 是这个阶段"治标死路"：既然盒子改不了，就接受盒子，
在渲染层给内容列加 0.659 列宽上限，把画面挤成居中竖条，同时删掉定时 `setRequestedOrientation` 空转循环。

**阶段二 放弃锁竖屏、转向四方向（08-16~08-17）**：意识到"锁竖屏"本身是死路——12L+/ZUI 只认"这是不是竖屏 app"，
竖屏 app 横拿必被塞半宽盒，而 app 改不了盒子宽高。`a3f9d8e` 转向"不锁方向，声明 fullSensor 四方向"，横屏时切
[左盘面满高+右控制列]分栏（被弃的第一次布局尝试）。`c36c976` 定案：用户拍板"横拿画面必须与竖拿一模一样、必须满屏"，
于是撤分栏，新增 **LandLayer 反旋转层**；同时把 head 段 `KIVY_ORIENTATION`（仍锁竖屏两方向会运行时覆盖 spec 四方向）
改成四方向（SDL 四方向解锁）。但真机上 LandLayer 根本没触发——窗口还是 letterbox 半屏盒。

**阶段三 方向守卫与按比例分流（08-17~08-19）**：`78559dd` 用真机横拿截图像素取证**锁死真凶**——targetSdk=30 的兼容模式在
12L+ 大屏被塞固定比例 letterbox 盒，**系统只认 targetSdk，与方向声明无关**；于是 `android.api` 升到 33 + resizeableActivity=true，
LandLayer 从此真正触发全屏反旋转。升到 33 后窗口仍偶发被塞盒（SDL 行为），`a24a897` 找到最后变量：SDL 启动/onResume 按自身
hint 调 `setRequestedOrientation` 把 manifest 的 fullSensor 运行时覆盖成竖屏 → ZUI 判定竖屏 app 塞盒；于是加**运行时方向守卫**
（启动 1s/onResume/常驻定时反复 `setRequestedOrientation(FULL_SENSOR)` 顶掉 SDL 竖屏自报）。`746b45a` 提速（转屏瞬间立即重申 +
守卫 0.7s 常驻）。`3717663` 按物理屏比例分流（宽屏保持 fullSensor+反旋转，瘦长机锁竖屏）。

**阶段四 交互与沉浸补齐（08-25~08-26）**：`d4a0346` 修"渲染转对了但按钮全点不到"——横屏触摸三重失效
（touch.pos 赋值不改 x/y、误减 anchor.pos 平移判定区、grab 后 move/up 走 EventLoop 直发按钮走 to_local 链），
用 `apply_transform_2d` + 覆写 `to_local/to_parent` 修满三处。最后的沉浸是全屏化补丁：`699233f` 修"真机打开即闪退"真凶
（`_enter_immersive` 零参签名收不了 schedule_interval 塞的 dt，启动 0.7s 即 TypeError，0.5.1/0.5.2 都死于此，一度误记到
fullscreen=1 头上）；`9873ab0` 修"竖屏启动沉浸不生效、转屏才自愈"（`setSystemUiVisibility` 从 Python 线程直调被 ViewRootImpl
线程检查静默拦截，改 `runOnUiThread` 投递到 UI 线程）。

### 10.2 全 commit 表

| commit | 题目 | 病根 | 修复 | 备注 |
|--------|------|------|------|------|
| `69e2055` | 修复竖屏180度 | manifest 写死 portrait，永不 180° | 改 sensorPortrait（正竖↔倒竖，不横屏） | 只解决能否倒竖，没触及 12L+ 塞盒那层 |
| `065c053` | 修复大屏横屏 | 安卓12+大屏强制多窗口忽略方向 + ZUI主动覆盖 | 引入 p4a.hook 注入 resizeableActivity=false + 方向覆盖退出 | 治标；resizeableActivity 后来是双刃剑 |
| `d3a50e2` | 横屏调试+运行时强制竖屏兜底 | 仅靠系统声明不够 | on_resize 检测横屏→setRequestedOrientation(1)+Toast | 运行时兜底第一版；当时未意识到 12L+ 上该调用是 no-op |
| `08e3c85` | 竖屏: targetSdk降到30治本 | targetSdk31 在 12L+ 大屏强制多窗口、系统无视方向 | hook 降 targetSdk30 + 运行时权威检测旋转角反复拉回 | 关键认知：targetSdk≥31 是根 |
| `c92abe4` | 治本: android.api降到30 | targetSdk31 下 setRequestedOrientation 被系统拒绝 | spec android.api 30 | 把降级落实为配置 |
| `fce329f` | hook 只留 sensorPortrait | resizeableActivity=false 干扰 targetSdk30 兼容模式 letterbox 窗口尺寸，横屏压成近正方形(1519×1754) | hook 只强制 screenOrientation=sensorPortrait | 暴露"letterbox 盒宽高 app 改不了"铁律 |
| `d5a5076` | 横屏修复: 内容锁竖屏列宽上限0.659 | ZUI 大屏 letterbox 给近正方形盒子，app 改不了 | _fit_width 加列宽上限把内容收窄成居中竖条；删运行时方向空转循环 | 治标死路（细条），放弃锁竖屏的诱因 |
| `a3f9d8e` | 横屏终版: fullSensor 四方向+分栏 | 锁竖屏会被 12L+/ZUI 关进半屏盒 | manifest 改 fullSensor；横屏切[盘面满高]+[右控制列]分栏 | 转向点；分栏后被反旋转层弃掉 |
| `c36c976` | 横屏定案: 反旋转层 | 上一版真机失效：head 段 KIVY_ORIENTATION 仍锁竖屏两方向，运行时覆盖了 spec 四方向 | KIVY_ORIENTATION 改四方向；撤分栏；新增 LandLayer 反旋转层 | 用户拍板"横拿画面与竖拿一模一样必须满屏"；但真机仍未触发（窗口还塞盒） |
| `78559dd` | targetSdk 30->33 + resizeable | 真机像素取证：fullSensor 四方向没换来全屏窗口，是 targetSdk30 兼容模式塞 letterbox | spec android.api 33；hook 改注入 fullSensor+resizeable=true；CI 缓存 key 强制重建 | **全局最关键解药**；宣告锁竖屏时代结束 |
| `a24a897` | 方向守卫: 持续请求 fullSensor | targetSdk33+manifest fullSensor 下窗口仍 1519×1754 半宽盒；唯一变量=SDL 运行时自报 | _force_fullsensor：启动1s+onResume+常驻顶掉 SDL 竖屏自报 | **方向守卫是关键解药**；ZUI 只认运行时请求 |
| `746b45a` | 删诊断小字+转屏跟随提速 | 方向守卫是解药但需提速 | 转屏瞬间立即重申；守卫 2s→0.7s；删诊断小字 | 把"能转"打磨成"跟手" |
| `3717663` | 方向策略按屏幕比例分流 | 20.5:9 瘦长机横拿会撞横向状态栏，显示坏掉 | _device_is_wide() 分流；宽屏 fullSensor+反旋转，瘦长机 SENSOR_PORTRAIT(7) | 最终策略收尾；manifest 仍统一 fullSensor，靠运行时守卫覆盖 |
| `d4a0346` | 横屏反旋转层触摸三重失效 | 横屏画面转对了但按钮全点不到 | ①apply_transform_2d ②_to_eq 不减 anchor.pos ③覆写 to_local/to_parent | 渲染对了≠触摸对；写入 BUILD_APK 3.21 |
| `699233f` | 真机闪退真凶 _enter_immersive 零参签名 | TypeError: 收不了 schedule_interval 塞的 dt，0.5.1/0.5.2 同死 | _enter_immersive() → (*_)(一行)；version 0.5.3 | 桌面门禁全绿≠真机能跑 |
| `9873ab0` | 竖屏启动沉浸不生效 | setSystemUiVisibility 从 Python 线程直调被线程检查静默拦截，转屏才"自愈" | runOnUiThread 投递 UI 线程；单实例缓存 Runnable | 通用法则：碰 View/Window 一律 runOnUiThread |

### 10.3 死路 / 被放弃的弯路

1. **锁竖屏双向都是死**：targetSdk≥31 在 12L+ 大屏被强制多窗口、系统无视方向；为兼容模式降回 30，又换来
   "固定比例 letterbox 半屏盒"（app 改不了盒子宽高）——**升也死、降也死**。
2. **运行时 `setRequestedOrientation` 当方向守卫**（08e3c85 的权威旋转角检测循环 + d3a50e2 的 on_resize 强制）：
   在 12L+ 上本身是 no-op，只会造成 resize 抖动，被 d5a5076 删除。它只能起"得知真相"的诊断作用。
3. **渲染级治标**（d5a5076 的 0.659 列宽上限）：接受盒子、把内容挤成居中竖条——体验差，被弃。
4. **横屏分栏布局**（a3f9d8e 的左盘面+右控制列）：改了布局结构，与用户"横拿画面要和竖拿一模一样"矛盾，被反旋转层取代。
5. **打包侧 fullscreen=1**（0.5.1）：曾被误判为"真机打开即闪退"真凶而回退；真相是 `_enter_immersive` 零参签名 bug，纯属误诊。
6. **高频诊断循环**（每隔 0.7s/2s 弹 Toast + setRequestedOrientation）：找根因时极有用，方向定案后被精简掉。

### 10.4 关键解药（真正站得住脚的）

1. **sensorPortrait 替代 portrait**（69e2055）：解决"能否 180° 倒竖"，至今保留。
2. **放弃"app 是竖屏"的自我定位，改成"全方向 fullSensor + 全屏窗口"**（a3f9d8e 转向 + 78559dd 的 targetSdk33+resizeable）：
   只有顺着大屏"要全屏"，系统才给全屏窗口，才谈得上 app 内自适应。**这是病根的钥匙。**
3. **反旋转层 LandLayer**（c36c976 渲染 + d4a0346 触摸）：横拿时保持竖构图、整体旋转铺满——用户要的"画面与竖拿一模一样"。
4. **运行时方向守卫**（a24a897 起）：用 `setRequestedOrientation(FULL_SENSOR)` 反复重申，顶掉 SDL 引擎启动时的竖屏自报。
   这是"诊断循环"升华成的正牌解药（别删）。
5. **按物理屏比例分流**（3717663）：16:9 及更宽平板才旋转，瘦长机锁竖屏——解决"窄手机横拿撞状态栏/小屏看旋转构图不适合阅读"。
6. **沉浸的 UI 线程投递**（9873ab0）与 **Clock 回调签名兜底**（699233f）：两个通用工程准则的落地。

### 10.5 边界 / 遗留

1. **manifest 无法按设备比例分流**，只能统一 fullSensor；分离逻辑靠运行时守卫（优先级高于 manifest）。因此瘦长手机在
   启动最初窗口变化前可能有一次竖/横窗口抖动。
2. **反旋转层绕不过系统栏**：横屏时系统状态栏/导航栏自身是横向的，LandLayer 不旋转系统栏；IMMERSIVE_STICKY 下边缘滑入
   呼出的栏在旋转构图下的交互仍属边缘场景（平板全屏沉浸下的滑出行为未彻底验证）。
3. **深度耦合 p4a 与特定 OEM**：方向靠 hook（v2024.01.21）注入 + p4a_env_vars 的 KIVY_ORIENTATION；升级 python-for-android
   或换品牌大屏，机制可能整体失效，需重走像素取证。
4. **反旋转层是全局单点**：所有触摸路径（按钮 grab、Window 级标题长按、弹窗 RotPopup、to_local/to_parent 链）都必须在
   旋转状态做坐标逆变换——未来任何新增控件/手势若绕过层，会再现"横屏点不到"这一坑。BUILD_APK 3.21/3.22/3.23 是防再犯的重要文档。
5. **守卫生效依赖"系统仍响应 setRequestedOrientation"**（这是 12L+ 大屏对 fullSensor app 的行为）；若某 ROM 连全方向请求都无视，
   反旋转无从触发，只能靠渲染层再兜一层。
6. **版本噪音**：整个方向史横跨 0.5.0~0.5.4，每个 APK 靠 version 号辨认（建立了"出包必 bump"规矩），方向相关 bug 常常版本翻车后才回滚。

---

## 11. 验证方式

- **桌面模拟**：`python main.py --landscape`（横窗 1740×1000），验证"画面保持竖拿构图"。桌面读不到物理屏比例/旋转角，
  `_device_is_wide` 回退宽屏、`_land_angle` 固定 +90，LandLayer 只认 `--landscape` 模拟。
- **横向触摸验证**（不用真机）：`--landscape` 启动 + 注入合成触摸到按钮的**视觉屏幕位置**，走
  `EventLoop.post_dispatch_input('begin'/'end', t)` 完整还原两段派发（窗口树分发 + grab 直达）。见 §4.7。
- **真机验收**：① Y700 平板横拿 → 画面转成竖构图铺满、所有按钮可点；② 20.5:9 手机横拿 → 画面保持竖直、不出现横向状态栏。
- **回归**：`python main.py --selftest`（无界面门禁，改完必跑；偶发 3σ 假失败重跑一次）。

---

## 12. 关键文件索引

| 文件 | 内容 |
|------|------|
| `main.py` | 生成物（父项目生成）。`LandLayer`(2536)/`RotPopup`(2641)、`_device_is_wide`(2490)/`_land_angle`(2516)、`_veq`(3127)/`_fit_width`(3134)/`_apply_sizes`(4120)、`_frame`(4172)、`PlinkoApp.build`(4341)、`_apply_orientation`(4382)/`_orient_guard`(4395)、`_immersive_task`(4413)/`_enter_immersive`(4447)、`on_pause/on_resume/on_stop`(4464) |
| `p4a/hook.py` | 构建后 hook：往 manifest 注入 `screenOrientation=fullSensor` + `resizeableActivity=true`（before/after_apk_build） |
| `buildozer.spec` | `android.api=33`、`android.manifest.orientation=fullSensor`、`orientation` 四方向列表、`p4a.hook` 指向、`p4a.branch=v2024.01.21` |
| `README.md` | 「横屏适配」正式章节（方向分流口径） |
| `BUILD_APK.md` | 3.21 触摸三坑、3.22 UI 线程坑、3.23 零参签名坑、方向策略（§3.x） |
| `.github/workflows/build-apk.yml` | 云构建流水线（缓存 key 换代强制重建） |
| 父项目 `../tools/android_part_ui.py` | 上述 Kivy 段的**源文件**（本仓库 `main.py` 由此生成；改界面要改这里，再重跑生成器） |

> ⚠️ `p4a/hook.py` 里 `_inject_manifest` 用正则改 manifest，只匹配 `org.kivy.android.PythonActivity`。若将来 p4a 换类名或
> 主 activity 写成自闭合 `<activity .../>`，正则插入逻辑会破坏结构——改 hook 时须留意。

---

## 13. 通用启示（工程准则）

- **"桌面全绿 ≠ 真机能跑"**：Android 专属问题（线程、签名、生命周期）桌面跑不出来。`_enter_immersive` 签名坑、UI 线程坑
  都是桌面免疫的。
- **`except Exception: pass` 是隐患放大器**：线程错误、ViewRootImpl 检查被吞掉后，现象变成"时好时坏的灵异事件"
  （如"转屏才自愈"）。**吞异常的代码必须保证调用姿势本身不会错**。
- **凡碰 Android View/Window 的调用，一律 `runOnUiThread`**。
- **凡交给 `Clock.schedule_*` 的函数引用，签名必须能收 1 个位置参数**（收 `dt`）。
- **"锁死政策"要顺着走，别对抗**：12L+ 大屏的 letterbox 是对"竖屏 app"的政策，app 改不了盒子宽高；顺着它"要全屏"，
  系统才给全屏，再在 app 内自适应。
- **像素取证强于盲改构建**：这次用 `dumpsys window windows` 逐帧盯窗口 vsysui 标志、真机截图像素取证，比反复改构建快得多。
- **优先级分层的思路**：manifest 决定"初始/布局"，运行时决定"即时"，SDL 只是观察者。想清楚"谁最终说了算"再动手。
