# 沙漏 App 性能问题简报（low1）

> ## 🔒 本文档已冻结 —— 对应代码 `v1.178` / `code_hash = 0c00a6ad00d2`
>
> **从冻结时刻起不再改这份文档对应的代码**，避免"刻舟求剑"（你在外面问的同时我把基线挪了，
> 拿到的建议就对不上了）。**任何优化都等外部意见回来之后再做。**
>
> 目的：把这个 App 的**性能现状、测量方法、已做的努力、设备侧的实测分解**摆清楚，
> 请外部更强的方法论/经验来判断「还能从哪儿挖」。
> 所有数字都是**实测**，来源与工具都写在原处；推测的地方我都标了「推测」。
>
> ⚠️ **测量环境警告（务必先读）**：本文所有"应用侧"数字来自 **MuMu 模拟器**（x86, 165Hz 面板），
> 真机（骁龙 8 Elite Gen 5 / 120Hz）我们只能确认"**跑满 60fps 上不去 120**"。
> **模拟器的 `前次Swap` 一栏占 13~18ms 且单轮跳动 ±30%** ⇒ 在模拟器上**测不出帧率改进**，
> 只能看 `物理 / 图元 / Canvas` 三栏。**绝对 ms 也不要在模拟器与真机之间互推。**

---

## 0. 一句话现状

一个 Kivy(Python) 写的 2D 沙漏动画。**最好的真机（骁龙 8 Elite Gen 5 / 120Hz）只能跑满 60fps**，
而 1.39 版在同一台机器上是 **120fps**（见 §5 的对照）。目标：**跑满 120fps**（帧预算 8.33ms）。
当前设备（模拟器）上应用侧耗时 **≈19.8ms/帧**（物理 5.3 + redraw 8.5 + GL 6.0）。

---

## 1. 运行环境与技术栈

| | |
|---|---|
| 框架 | **Kivy 2.3.0**（Python），Android 用 python-for-android 打包（GLES2，**无 AA / 无 MSAA**） |
| 语言 | Python **3.11.5**（APK 内） |
| 渲染 | Kivy 图形指令：`Ellipse` / `Rectangle` / `Quad` / `Line` / `Mesh` / `StencilPush-Pop` |
| 目标机 | Android 手机（骁龙 8 Elite Gen 5 / 120Hz 面板）/ 测试用 MuMu 模拟器（x86，165Hz 面板） |
| 屏幕 | 真机 1080×2400 级；模拟器 1080×2328 |

### 关键架构约束（决定了不能"重写成游戏引擎"）

这是一个**"假物理"玩具**，不是仿真器：

- **唯一真值是 `elapsed / duration`**（进度条）。所有可见几何都从它派生。
- **粒子是纯装饰**，不反向影响计时。
- 现有渲染管线已经完全绑定在 Kivy 的指令系统上（`InstructionGroup` + `Mesh` + `Stencil`）。
- 项目有一条铁律：**不许为了"更物理"或"更优雅"去改已验证的视觉**，
  历史上所有"我觉得这样更好"的重写（换成真物理、换成别的图元）**全部回退**。

---

## 2. Benchmark 的核心测量算法（用户点名要先讲这段）

代码：`frame_benchmark.py`（约 700 行）。分三档周期：**1s / 5s / 15s**，依次跑。

### 2.1 采样方式：**不数帧，量"交换间隔"**

```python
# frame_benchmark.py:638  _install_probes()
for target, name, stage in (
        (self.widget, "update_particles", "physics_ms"),
        (self.widget, "redraw",          "update_draw_ms"),
        (Window,      "on_draw",         "canvas_ms"),
        (Window,      "flip",            "previous_swap_ms")):
    original = getattr(target, name)
    def measured(*args, _original=original, _stage=stage, **kwargs):
        before = time.perf_counter()
        try:
            return _original(*args, **kwargs)
        finally:
            self._stages[_stage] = (time.perf_counter() - before) * 1000
            self._stamps[_stage] = (before, after)
    setattr(target, name, measured)

Window.bind(on_flip=self._on_flip)          # 每帧一次
```

- **帧时间 = 相邻两次 `Window.on_flip` 的间隔**（不是数帧除以秒数）。
- 四个探针把一帧切成四段：**物理 / 图元(redraw) / Canvas(GL) / 前次Swap**。
- 另有"**阶段残差**" = 帧时间 − 四段 − GC，用来发现"探针之外"的开销（实测均值 0.1~0.3ms）。

> 🔴 **2026-10-07 更正（外部评审 youhua1.md §2.2 指出，我查了本机 Kivy 源码，评审是对的）**：
> Kivy 的 `EventLoopBase.idle()` 里顺序是 **`window.dispatch('on_draw')` 然后
> `window.dispatch('on_flip')`**（`kivy/base.py:400-401`），而默认的 `Window.on_flip`
> 处理里才调 `self.flip()`（交换缓冲）。所以**同一轮内是 on_draw → flip**，
> 上一句"flip 在 on_draw 之前"是**错的**（它是本项目旧文档的错，我照抄了）。
> 探针名 `previous_swap_ms` 也随之**名不副实**——它量的是**本轮**的交换。
> ⚠️ 但**四个探针的数字仍然可用**：benchmark 的帧边界是绑在 `on_flip` 上的回调，
> 从回调 N 到 N+1 覆盖的正是"physics → redraw → on_draw → flip"一整个将来帧，
> 所以四项相加与帧时间是同向的（残差实测只有 0.1~0.3ms）。
> 错的是**命名与叙述**，不是那四个数。

### 2.2 统计量

```python
# frame_statistics(...) 的要点
平均 FPS      = 帧数 / 总时长
最慢 5 帧 FPS  = 单独列出（明细带当时的粒子数/各段耗时）
1% low        = 最慢 TAIL_MIN_FRAMES(=5) 帧的平均 FPS
                ⚠️ 原实现是 max(1, ceil(n*0.01))，1s 档只有 ~110 帧 ⇒ 只平均 2 帧，
                   退化成"最慢那一帧"。2026-10-03 改成至少 5 帧。
p50/p90/p99   = 帧时间分位(ms)
超阈值帧数     = >8.33ms / >16.7ms / >25ms / >50ms 各多少帧
```

### 2.3 环境指纹（每次记录）

`app_version / platform / window_pixels / maxfps / clock_resolution / python 版本 /
code_hash(=main.py 的 sha256 前 12 位) / 机型 / android_sdk / refresh_hz /
power_save / thermal_status`。

**`code_hash` 是判"这份日志到底跑的是哪一版"的唯一依据**——
我们就靠它发现过两次"拿 A 版的数字讨论 B 版"。

### 2.4 已知的**不可比性**（踩过很多次，写在文档里）

1. **1s 档天然轻**：热身瞬态占它 40% 的样本，三档**不可互相外推**。
2. **`1% low` 在 1.48 之前/之后不可比**（尾部帧数下限改过）。
3. **`refresh_hz` 不同 ⇒ 不可比**：`maxfps=0` 时帧率会顶到面板刷新率。
4. **模拟器与真机不可比**；**模拟器的两个实例之间也不可比**（我们踩过：480Hz 实例 vs 165Hz 实例，
   同一份代码 `前次Swap` 差 11 倍）。

### 2.5 一份典型的设备日志（本次问题现场，15s 档）

```
15 秒  ·  479 帧
平均 31.9 FPS    1% low 15.8 FPS(最差 5 帧)
诊断耗时(ms): 物理 6.74 / 图元 9.26 / Canvas 7.19 / 前次Swap 7.55
存活粒子 平均 1559 / 峰值 2749；飞溅峰值 2657
帧时间分位(ms): p50 30.75 p90 41.3 p99 71.04 max 98.38
阶段残差(帧时间-物理-图元-Canvas-Swap-GC): 均值 0.34ms / 最大 1.31ms
粒子峰值 2749 / 均值 1559; 粒子数↔帧时间 相关 r=+0.008
```

`r≈0` 是个反复出现的怪现象：**粒子多的时候帧时间并不更高**（见 §5.3）。

---

## 3. 设备侧分段实测（本次新做的，最有价值的一块）

工具：临时在 `main.py` 尾追一个包装器（包住下面这些方法各自计时），跑 benchmark，从 logcat 读。
⚠️ 只做**同一实例的前后对比**，不做跨环境外推。

### 3.1 峰值帧（粒子 1671 / 飞溅 1821）

| 段 | ms/帧 | 备注 |
|---|---|---|
| **`<redraw 总计>`** | **8.73** | |
| ├ `_draw_neck_grains` | 1.65 | **把已流出的粒子投影回整条颈部轮廓**，固定 128 图元池 |
| ├ `_draw_stream` | 1.62 | 沙流粒子 → texture 批处理（每颗粒 3 个 float 写进端点纹理） |
| ├ `_draw_surface_markers` | 1.08 | 上球 8 颗 + 下球 12 颗"表层滑动标记" |
| ├ `_draw_upper_shape` | 0.80 | 上球沙面 carve + 3px 亮带（**固定 64 列**） |
| ├ `_draw_mound_shape` | 0.71 | 下球沙堆轮廓（**固定 64 列**） |
| ├ `_upper_rough_now` | 0.59 | 上球沙面粗糙数组（128 帧预烘，按时间插值） |
| ├ `_group_stream_particles` | 0.55 | 把粒子按下标分桶（渲染前排序） |
| └ **未列入** | **~1.7** | `_sync_rects`（飞溅方块）+ 颈部 Quad 带 + flares + 尘埃 |
| **`<物理 总计>`** | **6.19** | |
| ├ 所有命名方法合计 | ~0.6 | apex 求解 / `_spawn_bg_splashes` / `_sync_mound_frame` … |
| └ **内联主循环 + 飞溅循环** | **~5.6** | **没有可挂钩的方法**，全在 `update_particles` 函数体里 |

### 3.2 飞溅层的**设备侧**定价（临时把飞溅数封顶 400，跑同一轮 benchmark）

| 15s 档 | 飞溅 ~1935（正常） | 飞溅 422（封顶） | 差 |
|---|---|---|---|
| 物理 | 4.74 | 3.39 | **−1.35** |
| 图元 | 7.25 | 6.21 | **−1.04** |
| Canvas | 5.09 | 3.47 | **−1.62** |
| **应用侧合计** | **17.08** | **13.07** | **−4.01ms（占 23%）** |

⇒ **飞溅层在设备上值 4.0ms/帧**，是当前最大的**单点**。
其中 **−2.66ms 在图元+Canvas**（= 1800~2600 个独立 `Rectangle` 的指令/提交成本），
**−1.35ms 在物理**（逐颗积分本身）。

**两个最重要的读数：**

1. **redraw 没有任何单一占了 50% 的段** —— 是**七块 0.5~1.6ms 堆出来的**。
2. **物理的 90% 在两个内联 `for` 循环里**，不是任何具名函数。

---

## 4. 关键代码（**摘要**；完整原文见 §8**自包含**）

### 4.1 沙流渲染器（曾经的最大优化，已落地）

`Line` 在 Kivy 里 **`width>1` 时不走 `glLineWidth`**，而是**每条线自建一个带 10 段圆头帽的三角网格**。
峰值 2500 条 ⇒ 数千次网格提交。**桌面永远测不出来（GL 余量大），只有 GLES 暴露**。

现在的方案（`tools/flow_texture_experiment.py`）：把每颗粒的 `x / bottom / top` 写进一张**顶点纹理**，
每颗粒 1 次 `struct.pack_into`，整块用一次 `Mesh` 提交。

```python
# 端点纹理布局: 一个颗粒的 x/bottom/top **连续放 3 个纹素**
# capacity 固定 == CHUNK(512) ⇒ u 步长 1/(512*3) 对所有分块一样, shader 只要一个 uniform
texture = Texture.create(size=(capacity * 3, 1))
```

实测（同机同配置）：

| 渲染器 | 15s 平均 | Canvas | 图元 |
|---|---|---|---|
| `line_pool`（原样） | 47.9 | 8.46ms | 4.94ms |
| `mesh_batch`（朴素） | 42.6 | 3.24ms | **13.21ms（净亏）** |
| **`mesh_endpoint_texture`** | **65.4** | **3.06ms** | 5.28ms |

### 4.2 飞溅的提交路径（**现在是每颗一个 `Rectangle`**）

```python
# main.py  _sync_rects(group, pool, particles, fixed_size=None)
for i, particle in enumerate(particles):
    sz = particle["size"] if fixed_size is None else fixed_size
    if isinstance(sz, (tuple, list)): w, h = float(sz[0]), float(sz[1])
    else:                             w = h = float(sz)
    pos = (particle["x"] - w / 2, particle["y"] - h / 2)
    if i == len(pool):
        rect = Rectangle(pos=pos, size=size); group.add(rect); pool.append(rect)
    else:
        rect = pool[i]
        if rect.pos != pos:  rect.pos = pos       # 2026-10-06 加的"值没变就不写"
        if rect.size != size: rect.size = size
```

- 飞溅全部**同一个颜色**（`sand_light`），都是 1~3px 的轴对齐方块。
- **峰值 ~1800~2600 颗同时在世** ⇒ 同数量的 `Rectangle` 指令。
- 这是**唯一还没批处理**的粒子层。

### 4.3 `update_particles` 的内联主循环（物理 5.6ms 所在）

```python
# 主循环（每颗粒）: 积分 + 流量守恒横向收缩 + 触底判定 + 触底事件
for i in range(...):
    ...
    vy += g * dt
    y  += vy * dt
    v_at_y = sqrt(60**2 + 2*g*fallen)
    shrink = max(0.70, (60/v_at_y)**0.5)
    ...
    if y >= mound_top_y - 1:            # 触底
        # EMA 更新堆尖偏移 / 25% 概率 spawn flare / 50% 概率 spawn splash
```

```python
# 飞溅循环（每颗）: 落坡前走抛物线, 落坡后贴面滑行 + 摩擦
for s in self.splashes:
    y = s["y"] + s["vy"]*dt + 0.5*_g*dt*dt
    vy = s["vy"] + _g*dt
    x = s["x"] + s["vx"]*dt
    dy = y - lower_center
    r = Ri2 - dy**2
    half = sqrt(r) if r > 0.0 else 0.0        # ← 每颗每帧一次 sqrt
    if abs(sx) > half - 1: continue
    if vy < 0:                                 # 只在真的判定时求接触高度
        _surf = _mound_bot + <接触面>          # 2026-10-06 改成**每帧一张 257 点查找表**
        if y <= _surf:
            ...贴面 + 摩擦衰减 + 停住后原地滞留...
```

### 4.4 沙堆接触面（曾经的热点）

```python
def contact(self, dx, apex):
    floor, roof = self.bounds(dx)          # r ± sqrt(r² - x²)
    return min(max(self.raw(dx, apex), floor), roof)   # raw = apex + shape_at(dx)

def shape_at(self, dx):                    # 65 个控制点之间**线性插值**
    z = (dx + r) / (2.0*r) * (n-1)
    i = int(z); f = z - i
    return self.shape[i] + (self.shape[i+1] - self.shape[i]) * f
```

原来每颗下落中的飞溅、每帧都调一次（峰值 **~2200 次/帧**）；
现在**每帧建一张 257 点的表**、读表线性插值 ⇒ 调用数 **2214 → 582 次/帧**。

---

## 5. 已经做过的努力与**实测**结果

### 5.1 2026-10-06 本轮（应用侧 23.19 → **17.08ms，−26%**）

| # | 改动 | 效果 |
|---|---|---|
| ① | 上球粗糙**包络每帧只算一次**（原来按节点各算 ⇒ 1880 次 `_smoothstep`/帧） | 掉出热点榜 |
| ② | 飞溅接触面 → **每帧一张 257 点查找表** | 调用 2214 → 582 次/帧 |
| ③ | 停稳的飞溅**跳过整段积分/判定**；`_sync_rects` 值没变就不写 | — |
| ④ | 飞溅寿命收紧：摩擦 4.0→7.0（滑行 12.5→6.8px、刹停 0.66→0.37s）、REST 0.90→0.50、STILL 0.40→0.28 | 在世数 **−25%** |
| ⑤ | **坡脚二分从"每颗标记各算一次"提到"每帧每侧算一次"** | 图元 8.52 → **7.25** |
| ⑥ | 飞溅的球内约束 `sqrt(Ri²-dy²)` → **平方比较**（逐字等价，连 `r≤0`/`sqrt(r)<1` 两个分支一并覆盖） | 每帧省 ~1900 次 `sqrt` |
| ⑦ | 颈部半宽 `half_w_at` 26 段线性扫描 → **bisect** | 250 次调用/帧 × 25 比较 → ~5 |

**同实例设备复测（15s 档）**：

| | 起点 | 现在 |
|---|---|---|
| 物理 | 7.41 | **4.74** |
| 图元(redraw) | 9.38 | **7.25** |
| Canvas | 5.66 | 5.09 |
| **应用侧合计** | **23.19** | **≈17.0（−27%）** |
| 前次Swap | 8.24 | 13.74（环境，跳动 ±30%） |
| 平均 FPS | 32.1 | 32.0（**被 swap 吃掉**） |

⚠️ **帧率在这台模拟器上测不出改进** —— `前次Swap` 一栏就占 13.7ms 且单轮跳动 ±30%。
要判帧率必须用 §5.5 的 n≥3 交替轮 + 按轮配对差。

### 5.2 更早的大改动（都已落地，不再重复挖）

- **`maxfps` 归因**：Android 上 `Config.set('graphics','maxfps','0')` —— 彻底不休眠，节拍交给 vsync。
  此前 `maxfps=60` 把"耗时 < 6.667ms 的帧"钉在 **12.222ms**（Kivy `Clock` 的 `usleep` 台阶），
  1s 档一半的帧中招 ⇒ 平均 96.7 → **173.4**。
- **索引重建**：`mesh.indices = ...` 的 setter 会让**整块 512 槽顶点数组**重走并标脏上传
  （实测 15.98 次/帧 = 1568.5 KiB/帧 ⇒ 2.12 次/帧 = 165.9 KiB/帧）。
  改法：索引**只增不减**，缩的时候把用不到的槽位在端点纹理里写成 `x = -1e5`。
- **`Line` → texture 批处理**（见 §4.1）。
- **`_draw_neck_grains` 的可见度前沿**：原来是"入口端可见度从 0 起"，留下一条横向分界线；改成整条轮廓展开。

### 5.3 反复出现的反直觉现象（**这条请重点看**）

> **粒子数 ↔ 帧时间 相关系数 r ≈ 0（+0.008 / −0.055）**，甚至慢帧的粒子数**更少**。
> 15s 档最慢 23 帧：帧时间 2.04×，而**粒子数 0.91×**（更少），各段**等比上涨**（图元 1.80× / Canvas 1.52× / 物理 1.30×），
> 残差只多 0.47ms ⇒ 签名是**外部停顿**（分配 / 模拟器宿主调度 / driver），**不是任何一栏算法的代价**。

**推论：优化任何一栏的算法，对 `1% low` 的收益≈0** —— 这也解释过"飞溅池消融纹丝不动"。

### 5.4 试过但**被数据否掉**的方向

- **低 n 时开 numpy 向量化**：函数级微基准说便宜 17 倍，设备实测物理栏确实降了 0.85ms，
  **但指标崩了**（1s 档 (avg+1%low)/2 从 85.7 → 64.9，p99 13.53 → 20.05ms）。
  根因：微基准在**预分配数组**上量，看不见"真实循环里每帧新建 numpy 临时数组"的代价。
  **已回滚**。
- **用 cProfile 判收益**：桌面 cProfile 报 `update_particles` **−49%**，设备只兑现 **−9%**。
  原因：cProfile **按函数调用计开销**，而本项目每帧上百万次微调用 ⇒ 放大好几倍。
  ⇒ **判"省了多少"只能用墙钟；cProfile 只能用来找热点在哪。**（今天刚踩，已写进文档）

### 5.5 测量纪律（我们有，且踩过很多次）

- 判据**必须先标定**：改之前先拿"已知对"和"已知错"各跑一遍，分不开的门槛等于没有门槛。
- **"非零"不等于"看得见"**；**验"实际画出来的"，不是"我以为的那张图"**。
- 视觉改动必须先出**改前/改后并排图**交人裁决，统计量只许事后解释"为什么"。
- 性能对比要 **n≥3 交替轮、比按轮配对差**（单轮不可判：同码组内极差能横跨 0.4~30 FPS）。
  工具：`tools/ab_device_benchmark.sh <revA> <revB> [轮数]` + `python tools/analyze_ab.py`（一轮约 55 秒）。

---

## 6. 我认为还能挖的地方（**请重点审这一节**）

按"预期收益 / 风险"排：

### A. 飞溅批处理（**预期最大，风险也最大**）
- 现状：**1800~2600 个独立 `Rectangle`**，同色、轴对齐、1~3px。
- 沙流当年用"端点纹理 + 一次 Mesh"解决了同类问题，收益 47.9 → 65.4 fps。
- 估计能拿回 **4~5ms**（墙钟测：桌面 1333 颗飞溅 = 2.39ms，设备按比例 ~5ms）。
- 难点：飞溅要**多色吗？不要**（全同色）；要**缩放吗？要**（1x1/1x2/2x2 混合 + 随屏幕缩放的粒度）。
  所以不能简单照抄"端点纹理"方案。**有没有更省的、允许每颗尺寸不同的批量图元方案？**

### B. `_draw_neck_grains`（1.65ms，第二大）
- 固定 128 图元池，把"已流出的粒子"投影回整条颈部轮廓，逐颗算位置/宽度/色调。
- 机制上是**纯装饰**（描述见 §4.1 附近）。**这个投影真的需要每帧重算 128 次吗？**
  脖子里的颗粒分布只随 `pn`/进度慢变。

### C. ~~`_draw_surface_markers`~~ —— **已修（§5.1-⑤），不再列为待办**

### D. 物理的两个内联循环（5.6ms，占比最大但最难动）
- 主循环 + 飞溅循环，全在 `update_particles` 函数体里。
- 每帧 ~3500 个对象（1671 粒子 + 1821 飞溅）的纯 Python 逐颗运算。
- **有没有可能把飞溅循环变成批量数组运算**（numpy）？§5.4 记录过"低 n 向量化反而更慢"的教训
  （每帧新建临时数组的代价），但飞溅是**固定结构**的，也许能预分配成紧凑数组？

### E. redraw 的"七块 0.5~1.6ms"
- 没有任何一块占 50%。**是不是有共同的、可摊销的东西？**
  例如：它们是不是都在**重复解同一个几何量**（沙面高度 / 轮廓曲线 / 材质 UV）？

### G. ⭐ **可推广的模式：「同一个几何量被逐颗重算」**（**已用它清掉两处，请帮我找剩下的**）
- **已修**：① 坡脚二分（§5.1-⑤，每帧 12 颗各算一次 → 每侧一次）；
  ② 飞溅的球内 `sqrt`（§5.1-⑥，平方比较等价替换）；
  ③ 颈部半宽 `half_w_at`（§5.1-⑦，26 段扫描 → bisect）。
- **还没查的同类嫌疑**（我判断不了哪些是真重复）：
  - `_draw_neck_grains`（1.65ms）除了 `half_w_at` 之外，**颈部轮廓/色调是不是每颗都在重解**？
  - 主循环的 `v_at_y = sqrt(60² + 2·g·fallen)`（流量守恒收缩）—— 能不能按 y 分档查表？
    它每颗粒每帧算一次，且 `fallen` 只跟 y 有关。
  - `_draw_upper_shape` / `_draw_mound_shape` 的 64 列里，**列与列之间有没有重复解同一个量**？
- **请判断：这个模式还能在哪几处兑现？有没有系统性的找法（而不是一处一处肉眼找）？**

### F. GL/Canvas 6.0ms
- 这一栏我们基本没动过。GLES2 无 AA/无 MSAA。
- **在 Kivy 里，减少 `VertexInstruction` 数量 vs 减少绘制面积，哪个对 `on_draw` 更有效？**

---

## 7. 我需要的外部判断（具体问题）

> ⚠️ 下面每一条都是**我做不了**的（要么是口味取舍、要么是我判断不了的架构选择），
> 我能自己查的自己已经做掉了（见 §5.1）。

1. **§6-F**：Kivy 的 `on_draw` 成本主要跟什么走——指令条数、顶点数、还是覆盖像素？
   这决定 A 值不值得做（批处理减少的是指令数）。
2. **§6-A**：Kivy 2.3 里，**允许每颗不同尺寸的批量小方块**，除了自建 `Mesh` 还有什么路子？
   （`Point` + 点精灵？`Texture` + shader 里按属性缩放？）
3. **§5.3**：`粒子数 ↔ 帧时间 r≈0`、慢帧粒子更少、各段等比上涨 —— 这像什么？
   我们归因于"外部停顿"，但**如果不是**，会是什么？
4. **§5.4**：numpy 化的判据该怎么定？我们现在的教训是"不能用函数级微基准"，
   但整帧 A/B 的成本很高（28 分钟/次对比）。有没有更便宜的判别手段？
5. 有没有我们**完全没想到**的方向？（例如：整个渲染换 `Canvas` 的 `Fbo` 缓存静态层？
   把 8 个静态装饰层烘成一张纹理？）

---

## 8. 关键代码**全文**（本文件**自包含** —— 你只拿到这一个文件也能读）

> 下面是从仓库里**原文抽出**的代码，不需要访问任何其他文件。
> 行号/路径仅供参考，**判断以这里贴出的代码为准**。

### A. `frame_benchmark.py` —— **四探针切帧**
帧时间 = 相邻两次 `Window.on_flip` 的间隔。四段 = 物理/图元/Canvas/前次Swap。
```python
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

```

### B. `frame_benchmark.py` —— **统计量**(平均 / 1% low / 分位)
⚠️ `1% low` 的尾部帧数有下限 `TAIL_MIN_FRAMES = 5`(1s 档只有 ~110 帧)。
```python
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


```

### C. `main.py` —— **主粒子循环**的每帧核心(流量守恒收缩所在)
每颗粒每帧都会走这里。**峰值 ~1540 颗粒**。
```python
                fd = gen_y - y
                fallen_dist = fd if fd > 0.0 else 0.0
                # 管内: 管壁约束,填满内径 shrink=1.0
                # 出管: 40px 平滑过渡区渐变到流量守恒目标值,避免突兀收缩
                if y > lower_cut:
                    shrink = 1.0
                else:
                    below_tube = lower_cut - y
                    # ★ `** 0.5` → `sqrt`(2026-10-06 性能): 两处操作数都**非负**
                    #   ⇒ 逐字等价, 而 CPython 里 `**0.5` 走 `pow`、明显慢于 `math.sqrt`。
                    #   这是**每颗粒每帧两次**, 峰值 1540 颗粒 ⇒ ~3080 次/帧。
                    v_at_y = sqrt(source_speed_squared + 2 * g_abs * below_tube)
                    target = sqrt(source_speed / v_at_y)
                    if target <= FLOW_SHRINK_MIN:
                        target = FLOW_SHRINK_MIN
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

```

### D. `main.py` —— **飞溅循环**(落坡前抛物线 + 落坡后贴面滑行)
峰值 **~1900~2600 颗**。每颗每帧一次 sqrt(已改平方比较)、一次接触面查询(已改每帧一张表)。
```python
        for s in self.splashes:
            step_dt = s.pop("_step_dt", dt)
            # ★ **已停稳的直接跳过整段积分与接触判定**(它们 x/y/v 都不再变, 只推进计时)。
            #   滞留期占在途寿命约 4 成 ⇒ 这一段省掉的是 4 成的飞溅循环。
            _still = s.get("_still")
            if _still is not None:
                _still += step_dt
                if _still > SPLASH_STILL_LIFE:
                    continue
                s["_still"] = _still
                append_splash_keep(s)
                continue
            _g = g_splash * s.get("gd", 1.0)      # 每颗自己的重力(轨迹才各不相同)
            y = s["y"] + s["vy"] * step_dt + 0.5 * _g * step_dt * step_dt
            vy = s["vy"] + _g * step_dt
            x = s["x"] + s["vx"] * step_dt
            s["y"] = y
            s["vy"] = vy
            s["x"] = x
            # ★ **平方比较代替 `sqrt`**(2026-10-06 性能): 这颗 sqrt 只用来跟 `|sx|`
            #   比大小, 而 `|sx| > sqrt(r) - 1  ⟺  (|sx|+1)² > r = Ri² - dy²`
            #   (两边都非负)。**逐字等价**, 连原来那两个分支都一并覆盖了:
            #   `r <= 0` 时右边为负 ⇒ 恒真(跳过); `sqrt(r) < 1` 时右边 ≤ 1 < (|sx|+1)²
            #   ⇒ 也恒真(跳过) —— 与原式完全一致。省掉每颗每帧一次 sqrt + 一个分支。
            dy = y - lower_center
            sx = x - cx
            _ax = sx + 1.0 if sx > 0.0 else 1.0 - sx
            if _ax * _ax + dy * dy > Ri2:
                continue
            # ⚠️ **只在真的要判定时才求接触高度**(v<0 = 正在下落): 上升期多算一次
            #    `contact()` 实测让 15s 档 physics 多 0.35ms(A/B 3 轮可分辨)。
            #    定义仍然是 `_MoundProfile.contact` 那一份, 没有第二套公式。
            if vy < 0 and _ctab is not None:
                _z = (sx + _ctab_r) * _ctab_inv
                _i = int(_z)
                if _i < 0:
                    _surf = _mound_bot + _ctab[0]
                elif _i >= _ctab_n - 1:
                    _surf = _mound_bot + _ctab[_ctab_n - 1]
                else:
                    _a0 = _ctab[_i]
                    _surf = _mound_bot + _a0 + (_ctab[_i + 1] - _a0) * (_z - _i)
                if y <= _surf:
                    # 🔴 **2026-10-06 用户口径: 「溅到沙子上才开始掉的」+「简化一个模型」**
                    #    旧版这里直接 `continue`(删掉) ⇒ 颗粒**永远贴不上沙面**,
                    #    看到的就是"悬在半空的一圈浮尘"。改成 **贴着坡面滑**:
                    #    贴到面 + 纵向速度归零(重力下一帧又把它按回面上 ⇒ 自然沿面走)
                    #    + 横向按摩擦衰减, 直到停住或超时。
                    y = _surf
                    s["y"] = y
                    s["vy"] = 0.0
                    s["_rest"] = s.get("_rest", 0.0) + step_dt
                    _avx = s["vx"] if s["vx"] >= 0.0 else -s["vx"]
                    if _avx < SPLASH_MIN_VX:
                        # 🔴 **2026-10-06 用户: 「沙子的向下流动应该是流动一会会就不动了
                        #   (因为有阻力), 否则全部向下流动, 但是下面没有堆积, 这个不合理」**
                        #   旧版这一支直接 `continue`(**删除**) ⇒ 颗粒**永远看不到"停住"**,
                        #   只看到它一路顺着坡滑到底、然后被抹掉 ⇒ 读起来就是
                        #   "所有沙都在往下流、可下面什么都没堆"。
                        #   现在: 停住之后**原地留 `SPLASH_STILL_LIFE` 秒**再消失 ——
                        #   任一时刻坡面上都撒着一层已经停稳的颗粒, 覆盖面积反而更大
                        #   (这也正是用户此前要的「表面大部分地方都有沙子在流动」)。
                        s["vx"] = 0.0
                        s["_still"] = s.get("_still", 0.0) + step_dt
                        if s["_still"] > SPLASH_STILL_LIFE:
                            continue
                    else:
                        # ★ **沿坡加速度**(2026-10-06 加; 同日又调成 ~0, 见常量区注释)。
                        #   ⚠️ 坡角用 `contact` 的**中心差分**取(与绘制同一份定义, 不另写一套)。
                        if SPLASH_SLOPE_GAIN > 0.0:
                            _dd = 2.0
                            _slope = (_profile.contact(sx + _dd, _apex)
                                      - _profile.contact(sx - _dd, _apex)) / (2.0 * _dd)
                            _sin_a = abs(_slope) / math.sqrt(1.0 + _slope * _slope)
                            s["vx"] += ((1.0 if sx >= 0.0 else -1.0)
                                        * g_abs * _sin_a * SPLASH_SLOPE_GAIN * step_dt)
                        _k = 1.0 - SPLASH_SLIDE_DAMP * step_dt
                        s["vx"] = s["vx"] * (_k if _k > 0.0 else 0.0)
                        if s["_rest"] > SPLASH_REST_LIFE:
                            continue
            if y < lower_bot or y > lower_top - 5:
                continue
            append_splash_keep(s)
```

### E. `main.py` —— `_sync_rects`(飞溅**每颗一个 `Rectangle`** 的提交路径)
**这是当前最大的单点**: 1800~2600 个独立 `Rectangle`。
```python
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

```

### F. `main.py` —— `_draw_neck_grains`(颈部颗粒层, 1.65ms)
固定 128 图元池, 把已流出的粒子投影回整条颈部轮廓。
```python
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

```

### G. `main.py` —— `_MoundProfile` 的接触面/轮廓(被各层反复调用)
`shape_at` 在 65 个控制点之间**线性插值**; `contact` = clamp(apex+shape, 球底, 球顶)。
```python
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


```

### H. 沙流的**端点纹理批处理**(当年同类问题的解法 —— **飞溅还没做**)
沙流曾经也是逐条 `Line`(Kivy 在 `width>1` 时**每条线自建一个带圆头帽的三角网格**),
靠这个从 47.9 → 65.4 fps。核心: 每颗粒的 `x/bottom/top` **连续放 3 个纹素**,
每颗只做 **1 次** `struct.pack_into`, 整块用一次 `Mesh` 提交。

```python
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
```

---

## 9. 一句话总结给外部评审

> 这是一个**完全用 Python + Kivy 指令**画的 2D 沙漏。真机帧预算 **8.33ms(120fps)**，
> 现在应用侧 **≈17.0ms**（物理 4.5 / 图元 7.1 / Canvas 5.3）。
>
> - **redraw 7.1ms 是七块 0.5~1.6ms 堆出来的，没有大头**；
> - **物理 4.5ms 有 ~3.9ms 在两个内联 `for` 循环里**（挂不到任何方法上）；
> - **Canvas 5.3ms（GL）我们基本没碰过**。
>
> **最大的单点是飞溅层 —— 设备实测 4.0ms/帧**（临时把飞溅数封顶 400 测出来的，见 §3.2），
> 由 **1800~2600 个独立 `Rectangle`** 构成。沙流当年靠"端点纹理 + 一次 Mesh"解决过同类问题。
>
> **请判断：先做哪个、以及有没有我们没想到的招。**
>
> 📌 **本文件自包含**（§8 内联了全部关键代码原文），你不需要访问任何仓库。
> 冻结基线：**`v1.178` / `code_hash = 0c00a6ad00d2`**。
