# 求助：把这个 Android/Kivy 沙漏的帧率（尤其是 1% low）提上去

> 写给一位外部专家（人或 AI）。这不是营销文档，是一份**带原始数据的求助简报**。
> 我尽量把"实测到的"和"我推测的"分开标注 —— 请优先质疑我的**测法**，其次才是结论。
> 项目是中文注释，所以本文用中文；代码/API 名保持原样。

---

## 0. 一句话请求

这个应用在模拟器上稳态能跑 **~116 fps（15 秒档平均）**，但：

- **1% low 只有 69.8 fps（≈14.3ms，是中位帧 8.12ms 的 1.7 倍）**，而我已经实测证明**这部分不是算力**（见 §6a）；
- **最新一代旗舰 SoC 也救不了它**：用户手机是**骁龙 8 Elite Gen 5**，报告帧率仍然偏低（见 §2.2.1）；
- **旧版（v1.2）在同一台机器上只有 87.7 fps 平均 / 55.7 的 1% low**（见 §7.1 的对照行）——
  这是同一个测具、同一窗口、代码身份经 sha256 校验过的数据，可以当"这应用曾经有多慢"的锚点；
- 三个"看起来还能再赚"的优化（拆向量化门槛 / GPU 顶点平移 / 朴素 Mesh 合批）都要么把尾巴打坏，
  要么收益落在噪声带内；**端点纹理批处理是有效的**（Canvas 8.46→3.06ms、15s 平均 47.9→65.4），
  但它对 **1% low** 没有稳定改善 —— 这正是 §6a 想请教的地方。

**我最需要的是：告诉我还有没有"应用侧"的杠杆；如果没有，请直接说"没有"。**
（"没有"也是有价值的答案 —— 本项目历史上最大的坑就是在一个不可优化的指标上反复"优化"。）

---

## 1. 系统是什么

| 项 | 值 |
|---|---|
| 形态 | Android 应用（Kivy），另有 PC 版 tkinter 作视觉基准 |
| 框架 | **Kivy 2.3.0**（锁死）/ Python **3.11.5** / python-for-android **v2024.01.21** |
| 图形 | OpenGL ES（桌面预览走 GL 3.2；设备 logcat 报 `OpenGL ES 3.2 v334 R` / `Adreno (TM) 640`） |
| 代码规模 | `main.py` 3155 行（单文件应用本体）+ `frame_benchmark.py` 590 行 + `tools/flow_*.py` ~700 行 |

### 1.1 架构（决定帧率的地方）

- **"假物理"**：唯一真值是 `elapsed/duration`，所有可见几何由它派生 —— 本质是进度条，
  粒子系统是**纯视觉点缀**，不反向影响时间推进。
- **两个完整球 + 颈部圆柱管**，球↔管之间用二次贝塞尔过渡。
- 每帧做两件事：
  1. `update_particles()` —— 粒子物理（重力积分、生成、横向流量守恒收缩、触底事件）
  2. `redraw()` —— 把粒子打包成图元提交给 GPU（**Android 走端点纹理批处理**：
     每颗粒的 `x/bottom/top` 写进一张顶点纹理，shader 里还原成一条带圆头帽的线）
- 玻璃壳 / 真圆沙体用 Kivy `Ellipse` + `Stencil` 画，**只有颈宽或尺寸变化时才重建**，不占每帧预算。

### 1.2 工作量（实测）

| 档 | 在场粒子峰值 | 在场粒子均值 | 采样帧数 |
|---|---|---|---|
| 1s | 451 | 285 | 99 |
| 5s | 2899 | 1774 | 549 |
| 15s | 2903 | 1914 | 1737 |

（三个数都取 **v1.39 五轮的中位**：峰值逐轮 451/452/452/450/451 等，均值逐轮见下。）

⚠️ **`几何随窗口尺寸缩放`**：球半径 `R` 由 widget `size` 派生 ⇒ 窗口越大，球越大，
在途粒子越多 ⇒ **成本 ∝ 屏幕面积**。（这是设计，不是 bug —— 但它意味着
"换台更大/更强的机器"不会自动变快，见 §2.2.1。）

### 1.3 粒子系统长什么样（**被优化对象的"形态规格"**）

一帧里最多同时有 4 类粒子 + 1 类"伪粒子"：

| 类 | 数量级 | 怎么画 | 寿命 |
|---|---|---|---|
| **主流粒子** | 峰值 2900 / 均值 ~1800（15s 档） | 一条**竖直线段**，长度 `max(2.0, \|vy\| × trail_time / motion_scale)`，`trail_time ∈ [0.018, 0.032]s`；线宽 **2（85%）或 1（15%）** | 从颈口生成 → 落到沙堆顶 |
| **splash 反弹** | 峰值 ~170 | 实心小方块 | 向上弹（`vy = −110~−55`）再落回 |
| **触底 flare** | 每帧几十个 | 4×4 半透明方块 | **0.08s** |
| **完成尘埃** | 25 颗 | 小方块 | 1s（仅"漏完"那一刻） |
| *颈部投影（**不是物理粒子**）* | 固定 **128 个图元池** | `_draw_neck_grains()` 把已流出的粒子**投影回**整条颈部轮廓，画成 1–2px 的颗粒纹理 | 每帧重画 |

**每个主流粒子的字段**（存在并行数组里，一行一个）：
`x_offset`（相对中轴的横向偏移）、`y`、`vy`、`wobble_phase`、`wobble_amp`、`size`、`trail_time`、`is_light`

**生成**（按速率累积，每帧补充）：
- `rate = 600 × speed_factor`，其中 `speed_factor = clamp(60/duration, 0.5, 2.5)`
- 剩余时间 < 0.08s 时再乘 `√(remaining/0.08)`（收尾不突然断流）
- 出生横向 `uniform(−x_clip, x_clip)`；初速 `−(90~120)`（5% 概率）或 `−(35~60)`，再乘 `motion_scale`
- 加速：`g = −450 × motion_scale²`（Kivy y 向上，"向下"是 y 减小）

**横向流量守恒**（"看起来像真沙"的核心，**别动**）：
自由落体段按 `A·v = 常数` 收缩，目标 `shrink = max(0.70, (60/v)^0.5)`；
管内 `shrink = 1.0`；出管后 40px 内从 1.0 线性过渡到目标；触底前 30px 再乘
`1 + (1 − d/30) × 0.4`（喇叭口散开）。
横向位置 `x = cx + x_offset × shrink + sin(fallen_dist × 0.07 + wobble_phase) × wobble_amp × (1 − shrink × 0.4)`。

**触底事件链**（顺序固定）：① 沙堆顶偏移用 EMA 跟随落点 ② 25% 概率加一个 flare
③ 50% 概率加一个 splash。⚠️ **随机数的调用顺序不能改**——改了整条粒子流就变了
（§11.2 的 docstring 里逐条写着顺序，等价性靠 `tools/test_physics_equiv.py` 逐位比对守）。

**颜色**：`_color_table` 是 11 档 `sand_base → sand_light` 的插值表，按粒子的 `y` 取
（颈部偏 base、底部偏 light，模拟撞击区翻起的反光）；`is_light=True` 的那 10% 直接取 `sand_light`。

**Android 上实际怎么画**：**不用** Kivy 的 `Line`（它在 `width>1` 时每条线都自建三角网格），
而走 §11.4 的端点纹理——每颗粒把 `x / bottom / top` 三个 float 写进一张顶点纹理，
shader 里还原成一条**带 10 段圆头帽**的线。

> ⚠️ **形态硬约束**（§3 重申）：线段形状（收缩 + 圆头帽）、粒子数量、抗锯齿**都不许改**。
> 唯一允许动的是"怎么把同样的形态更便宜地提交给 GPU"。

---

## 2. 运行环境

### 2.1 模拟器（日常测量用）

```
MuMu 模拟器，x86_64（伪装成 HUAWEI NCO-AL00 / sdk 35）
窗口 1080×2358（分辨率 override 1080×2400），density 280
显示报 refresh_hz = 480.0  ← 注意这个数字，见 §8.1
maxfps=60（Kivy config），vsync=（空，未设）
Python 3.11.5，Kivy 2.3.0
```

### 2.2 设备

| 设备 | 屏 | 15s 平均 FPS |
|---|---|---|
| 用户手机（K90，**骁龙 8 Elite Gen 5**） | — | **用户报告：仍然偏低**（无具体数值） |
| MuMu 模拟器（x86_64，跑在桌面 CPU 上） | 1080×2358 | **115.8** ← §5 的全部数据来自这里 |

> ⚠️ 这两行**不是同一个受控对比**：分辨率不同、窗口不同 ⇒ 按 §1.2 的缩放关系，
> 粒子数不同。请只当"数量级"看，不要当配对数据。
> （历史上还有两台真机的数字，但**属于很老的版本、且非受控对比，已不作为证据**。）

### 🔴 2.2.1 我要特别请你注意的一条：**最新一代旗舰 SoC 也救不了它**

- **用户手机是骁龙 8 Elite Gen 5**（当下的顶级移动 SoC），**用户报告帧率仍然偏低**。
- **而跑在桌面 CPU 上的 x86_64 模拟器（15s 平均 115.8）反而更快。**

这条组合传递的信息很硬：**瓶颈大概率不在 GPU 吞吐，也不在"算力"这个意义上** ——
否则最新旗舰不该输给一台模拟器。

更像的解释是**单线程 Python 解释器吞吐**（每帧上千颗粒的逐颗 Python 字节码 + 每颗粒一次的
提交/打包调用）：桌面 x86 大核的单线程 IPC 明显高于 ARM 大核，于是模拟器反超。
这也与 §6a 的"应用侧工作量就是全部成本"、§8.3 的"~2.3 µs/颗粒/帧"指向同一个方向。

**但我没能把这变成受控实验**（真机的分辨率/粒子数我没记录，也没法在同一台机器上换 SoC）。
所以这是**我最想请你确认或推翻的一条**：
如果"瓶颈是单线程 Python"，那么任何 GPU 侧优化的上限都很低，正确的方向应该是
**每帧的 Python 调用次数**，而不是"算得更快"。请见 §9 第 1、4 题。

> ⚠️ **不要**因此建议"减粒子"或"降抗锯齿" —— 那是明令禁止的（§3）。

---

## 3. 硬约束（不能碰的东西）

这些是用户明确要求 / 历史踩坑换来的红线，请**在给建议时先假设它们不可协商**：

1. **不许减少粒子数量**，不许降低抗锯齿，不许隐藏低帧、不许只优化平均 FPS。
2. **玻璃与沙体必须用 Kivy 真圆**（`Ellipse` + `Stencil` 裁切）。
   **绝不许**用 `Mesh`/多边形拼弓形 —— 那是旧版渲染 bug 的根源（边缘失配、月牙缝）。
3. **不许全局禁用 GC**。
4. **不许改生产环境的 VSync/Clock 设置来美化数字**（可以改，但要证明它同时也是真实收益）。
5. **`frame_benchmark.py` 不许"优化"** —— 见 §6a 的教训：优化测量装置会让数字变好，
   而真实体验不变。
6. 视觉与物理公式来自已验证的 PC 版，**不许用"直觉"替代公式**（历史：扩张代替收缩、
   Ellipse 代替 Line、motion streak —— 全部以回退告终）。

---

## 4. 测量口径（**请先审这一节**）

如果你认为这套测法本身有问题，那比任何优化建议都值钱。

### 4.1 采样点

- **`Window.on_flip` 回调的间隔**（Kivy），单位 ms。
- ⚠️ **这不是 Android 合成器实际呈现的帧** —— 它是"应用提交的节奏"。
- ⚠️ **Kivy 的调用顺序（已核实源码，本机 2.3.1）**：`EventLoop.idle()` 里是
  **先 `window.dispatch('on_draw')`、再 `window.dispatch('on_flip')`**
  （`kivy/base.py`，`idle()` 的末尾三行）；真正的 buffer swap 在 `on_flip` 的默认 handler 里。
  所以"相机"上的 `previous_swap_ms` 指的是**上一帧的 swap**，而 `gap_draw_to_flip_ms`
  会是**负值**（因为它量的是本帧 flip→下一帧 draw，跨了帧边界）。

### 4.2 统计量

- `1% low` = 最慢 1% 帧的调和平均（即 `k / Σ(最慢 k 个间隔)`）。
- `p50/p90/p99` = `frame_ms` 的分位。
- 日志里**每帧 15 列**：`time_s, frame_ms, FPS, physics_ms, update_draw_ms, canvas_ms,
  previous_swap_ms, particles, splashes, gc_ms, gc_generation, mound_px,
  gap_between_frames_ms, gap_tick_tail_ms, gap_draw_to_flip_ms` —— 可自行重算。
  ⚠️ 这 15 列是 **v1.39** 的；更早版本（如 v1.2）只有前 12 列，没有那三个 `gap_*`。

### 4.3 噪声底（这是本项目最贵的一课）

| 观测 | 数值 |
|---|---|
| 同一份代码连跑两次，5s 平均 FPS | **61.5 → 54.5（±12%）** |
| `M = (平均+1%low)/2` 的跑间极差 | **±5~9（三档均值 ±9.4）** |
| 受控 3v3（基线×3 vs 改动×3） | 中位差 **+0.4**，而组内极差 **7.8 / 9.4** ⇒ **不可分辨** |

⇒ **任何小于 ~8 点的 M 改动在本机不可分辨；单轮 A/B 是无效证据。**
⇒ 我现在只用**交替多轮 + 按轮配对差**（A 的第 i 轮 − B 的第 i 轮），
  判据是**同号对数 / 符号检验**，不是"中位差 > 组内极差"（那条在同码组内极差能横跨
  0.4~30 FPS 时不可校准）。

### 4.4 我已知的测量局限

- 模拟器 = **x86_64 二进制翻译**，且宿主是 Windows（会跑视频压缩等后台负载）。
  **这里测出的"外部停顿"未必等于真机的外部停顿。**
- 这是**单机单配置**，没有多设备交叉验证。
- 我没有 Android 侧的 `FrameMetrics` / `gfxinfo` / systrace 数据 —— 只有应用自己打的点。

---

## 5. 实测帧预算（v1.39，5 轮中位，MuMu，15s 档）

### 5.1 阶段均值

| 阶段 | 中位 (ms) | 各轮 | 说明 |
|---|---|---|---|
| `physics_ms` | **1.459** | 1.46 / 1.42 / 1.37 / 1.47 / 1.75 | 粒子物理（numpy 向量化路径） |
| `update_draw_ms` | **2.658** | 2.66 / 2.60 / 2.51 / 2.66 / 2.97 | 每帧打包图元（端点纹理） |
| `canvas_ms` | **2.994** | 2.99 / 2.95 / 2.92 / 3.06 / 3.23 | Kivy Canvas 指令提交 + swap |
| `previous_swap_ms` | **0.492** | 0.49 / 0.48 / 0.47 / 0.51 / 0.58 | 上一帧的 swap |
| **四栏和** | **7.60** | | |
| `frame_ms`（由中位平均帧率反推） | **8.64** | | 115.8 fps |
| **探针外残差** | **~1.0** | | |

### 5.2 分位与超阈值

```
（下表全部为 **v1.39 五轮中位**；括号里是第 1 轮的对照，可见单轮差多少）

p50 = 8.14ms (r1 8.12)   p90 = 12.34ms (r1 12.06)   p99 = 13.63ms (r1 13.70)   max = 16.57ms (r1 16.15)
超阈值: >8.33ms 747 帧 / >16.7ms 0 帧 / >25ms 0 帧 / >50ms 0 帧（共 1750 帧）
1% low = 69.8 fps = 14.33 ms
```

### 5.3 三档对照

| 档 | 平均 | 1% low | p50 | p90 | 四栏和 |
|---|---|---|---|---|---|
| 1s | 98.2 | 69.4 | 12.15* | 12.86 | 5.34 |
| 5s | 109.6 | 71.8 | 8.34 | 12.53 | 7.14 |
| **15s** | **115.8** | **69.8** | **8.14** | **12.34** | **7.60** |

\* 1s 的 p50 被热身污染，见 §6b。

---

## 6. 三个反直觉、但已实测确认的事实

### 6a. 🔴 慢帧不是算力 —— 最慢 1% 帧的"应用工作量"与中位帧**一模一样**

15s 档 1750 帧，取最慢 17 帧（1%）与全体比较（逐帧 trace 重算）：

| 指标 | 全体中位 | **最慢 1% 中位** | 倍数 |
|---|---|---|---|
| `frame_ms` | 8.123 | **13.918** | **1.71×** |
| **`particles`** | 2002 | **1715** | **0.86×** ← 更少！ |
| `splashes` | 174 | 171 | 0.98× |
| **`physics_ms`** | 1.411 | **1.420** | **1.01×** |
| **`update_draw_ms`** | 2.666 | **2.626** | **0.98×** |
| `canvas_ms` | 3.019 | 3.176 | 1.05× |
| `previous_swap_ms` | 0.480 | 0.444 | 0.93× |
| **四栏和** | **7.576** | **7.666** | **1.01×** |
| **"探针外"残差**（逐帧 `frame_ms`−四栏−gc，取中位） | **0.29** | **6.36** | **21.9×** |
| `gap_between_frames_ms` | 8.332 | 12.492 | 1.50× |

**读法：慢帧的应用侧工作量完全没变（甚至粒子更少），全部 +5.8ms 的超额都落在"探针外"
—— 即帧与帧之间的等待。**

> **在这批样本里，慢帧的额外时间不来自这四栏。**
> 推论（**有限定，别当全称判断**）：降低四栏的工作量**不保证**改善 1% low ——
> 它只在"慢帧仍由外部停顿主导"时成立。

限定条件，请一并看：

- 这是 **1 轮 / 17 个最慢帧** 的样本，**跨轮未复核**（本轮共 5 轮，我只逐帧展开了一轮）；
- 若慢帧恰好发生在**工作量峰值相位**（在途粒子最多时），本条不适用 —— 本文**没有排除**该情形；
- **反例确实存在**：曾把物理快 0.85ms（1.75→0.90），**反而让 1s 的指标崩了**
  （M 85.7→64.9、p99 13.53→20.05ms）—— 说明改动算法**确实能移动尾巴**，只是方向可能是坏的。

（残差口径：正文用"逐帧 `frame_ms` 减四栏再减 gc，取中位"。若改用"两列中位相减"，
数字是 0.55 → 6.25（11.4×）—— 两种口径同向，但**别混用**。）

**所以：如果你的建议是"把 physics 从 1.46 降到 1.0"，请先告诉我它凭什么能改善 1% low。**
我要的是**改变调度/呈现**的东西，或者证明我的"探针外"归因是错的。

### 6b. 1s 档的"慢"是开局热身 —— ⚠️ 成因是 Kivy Clock 的休眠，不是 vsync（2026-10-03 更正）

> ⚠️ **本节标题原文写的是"（vsync 阻塞）"，已证伪。** 那笔 8~11ms 残差的真身是
> `kivy/clock.py` `_check_ready` 的 `usleep`：`maxfps=60` 下**任何耗时 < 6.667ms 的帧
> 都被睡到恒定的 12.222ms**。两臂 A/B 里只改 `maxfps` 一个变量，残差即从
> **8.94ms（三档同值 8.94/8.94/8.94）→ 0.49ms（三档同值）**；且设备报 480Hz、
> vblank 仅 **2.08ms**，**物理上阻塞不出 8.94ms**。详见 `q1.md` §0.1 / §0.3。
> **下面的测量全部有效，只有归因变了。**

1s 用例只跑 1 秒，而**每轮开跑后约 0.5 秒（约 41 帧）都有 8–11ms 的额外残差**，
之后一律塌到 0.27ms。1s 只有 ~100 帧 ⇒ 热身段占了它 40% 的样本，中位被拉到 12.15ms。

- 这段热身的**四栏和只有 1.7–4.5ms 而帧时间 12.4ms** ⇒ 是在**等**，不是在**算**。
  （等的是 clock 的 `usleep`：开局粒子少 ⇒ 耗时 ≈3.3ms ⇒ 被睡到 12.222 ⇒ 残差 8.94；
  0.5s 后粒子涨起来、耗时过 6.667ms ⇒ 不睡了 ⇒ 残差塌到 0.28。一个机制，两个症状。）
- ⚠️ **热身不是 1% low 的主因**：最慢帧落在 t≤0.5s 的比例，我用本轮日志**逐轮复算**为
  **15s: 0–6%（5 轮：5%/5%/0%/0%/0%）**、**5s: 0–40%（0/20/20/40/0%）**。
  ⇒ 慢帧撒在全程，不集中在开局。**但样本极小**（5s 每轮只有 5 帧、15s 只有 16–17 帧），
  这个比例本身统计意义很弱，只能当方向。
- 项目早期记录"去掉热身后 1% low 反而更差（14.09→18.27 / 13.82→17.84）"——
  那组数的原始日志已不在仓库里，**本次无法复现**，仅作参考。

### 6c. 帧间隔疑似被显示刷新率量化

`gap_between_frames_ms` 的**中位 = 8.332 ms**，而设备报 `refresh_hz = 480.0`
⇒ 8.332 ≈ **4 × (1/480) = 8.333 ms**（精确吻合）。

而应用四栏和是 7.60ms —— 也就是**刚好塞进 4 个显示周期**。
模型：`帧间隔 = ceil(应用工作量 / 2.083ms) × 2.083ms`

- 15s：7.60 → 4 周期 → 8.33ms → 120fps；实测 115.8 ✓（吻合）
- 5s：7.14 → 4 周期 → 8.33ms；实测 109.6（9.12ms）✗
- 1s：5.70 → 3 周期 → 6.25ms；实测 98.2（10.18ms）✗

**只有 15s 吻合**（5s/1s 有热身污染）。所以这只是一个**假说**，见 §8.1。

**2026-10-03 补充**：5s/1s 不吻合现在有了候选解释 —— 它们是被 **clock 的 12.222ms 台阶**
盖住了（见 §6b 更正），而不是被刷新率量化。台阶移除后（`maxfps=120`）1s 的中位帧
从 **12.35ms 掉到 5.95ms**。**但 5.95ms 仍低于 `ceil(4.65/2.083) × 2.083 = 6.25ms`**，
所以 480Hz 量化模型**既没被证实也没被证伪** —— 继续当假说。

---

## 7. 消融账本：试过什么，结果如何

### 7.1 成功的

| 改动 | 效果（设备实测） |
|---|---|
| **粒子物理改 numpy 向量化**（1.14，commit `fba7b4a`） | **该 commit 自记**：15s 物理 **3.37→1.37ms（−59%）**、15s 平均 **90.1→106.4（+18.1%）** |
| *（对照，非单次改动）* v1.2 → 1.39 **全量** | 15s 平均 **87.7 → 115.8**；**按轮配对中位差 −27.7**（5 轮，5/5 同号）；物理栏中位 **3.55 → 1.46** |
| 渲染端端点打包向量化（1.15/1.16） | 打包从逐颗粒 `struct` 循环换成 numpy 批量 |
| 粒子存储改并行数组（去掉每帧 dict 兼容层） | 粒子管线 **1.54×** |
| 低粒子数回退（1.17） | 修掉 1s 档的退步（峰值 450 < 阈值 800，走标量） |
| 端点纹理批处理（renderer=`texture`） | Canvas **8.46→3.06ms**，15s 平均 **47.9→65.4** |
| 颈部颗粒第一趟向量化（1.22） | 占"图元"栏 **26%** |
| 去掉每帧 `pv.tl` 的无效 `tolist()`（1.38） | 那份每帧 ~2000 个 float 在安卓上从没被读过 |

### 7.2 失败的（**请不要重复建议这些**）

| 尝试 | 结果 | 原因（我的归因） |
|---|---|---|
| **拆 `_NUMPY_MIN` 门**（让低粒子数也走 numpy） | **指标崩了**：1s M 85.7→64.9、p99 13.53→20.05ms、残差 4.51→6.64ms | 函数级微基准在**预分配数组**上量，看不见真实循环里**每帧新建 numpy 临时数组**的代价；那笔钱落在探针外，把尾巴打坏 |
| **渲染器重构（1.18，每帧 blit 22→1）** | 整帧反而**慢 46%**，已回滚（1.20） | 把开销从 CPU 搬到了 GPU |
| **朴素 Mesh 合批（renderer=`batch`）** | 图元 **13.21ms**（净亏），15s 42.6 vs line 池 47.9 | Python 顶点更新太重 |
| **GPU 顶点平移（renderer=`gpu`）** | Canvas 降但"图元更新"涨 | 每颗粒 3 次切片赋值 |
| **飞溅粒子池消融** | 1% low **纹丝不动** | 与 §6a 一致 |
| 颈部"填满喇叭口" / "缩窄喇叭口" | 视觉无效 / 会让扁平肩台重现 | 喇叭口半宽被 `shoulder*1.06` 钉死 |
| 颈部射流包络（1.23/1.24） | 视觉被判为"一串静止的波纹管"，**用户原话**是"把沙漏的颈部沙子形状改成什么鬼样子了"；已回滚（1.39） | 知觉问题不能用指标裁决；且该改动**从未进过任何一轮对抗审查** |

### 7.3 结构性的教训（我觉得对写建议的人有用）

1. **函数级微基准会骗人** —— 它看不见"每帧新建临时对象"的代价。要么量整帧，要么量分配行为。
2. **Kivy 的 `Line` 在 `width>1` 时不用 `glLineWidth`**，而是**每条线**自建带 10 段圆头帽的
   三角网格 ⇒ 2500 条 `width=2` 的线 = 数千次网格提交 + 每颗粒的 Python 提交成本。
   桌面 GL 余量大（帧率对粒子数几乎不变），**只有 GLES 暴露**。
3. **写 `context.shader["名字"] = 值` 会在画布构建时崩** —— 要写 `context["名字"] = 值`。
   ⚠️ **更正一处误诊**：本项目曾把原因写成"`Shader.__setitem__` 是 Kivy 2.3.1 才有的"，
   **实测 2.3.1 上同样没有**（`hasattr(Shader, '__setitem__')` 为 `False`）。
   本地测试全绿的**真因**是这个渲染器**只在 Android 装载**（`if platform == "android"`），
   PC 上根本没走到那段代码 —— 所以"本地全绿"证明不了任何东西。
4. **Windows 的 `monotonic()` 精度只有 15.625ms** —— 一律用 `perf_counter()`。

---

## 8. 我怀疑但没能证明的方向

### 8.1 显示刷新率量化（§6c）
若 480Hz 量化成立，那么在 15s 档要把 120fps 抬到 160fps，必须把应用工作量压到
**< 6.25ms**（现在 7.60ms，差 1.35ms）。而 1% low 的 14.3ms ≈ 7 个周期 —— 如果慢帧也是
量化结果，那"让工作量稳定"比"让工作量变小"更重要。

**请告诉我怎么验证/推翻这个模型。**

### 8.2 Kivy Clock 的分辨率
环境里 `clock_resolution_s = 0.005556`（= 180Hz）。应用用的是
`Clock.schedule_interval(tick, 0)`。**如果 tick 只能落在 5.556ms 的网格上，
那它本身就是一种量化源**（0.005556 × 1.5 = 8.334ms，与 §6c 的 8.332ms 也很接近）。
调整 `kivy.clock_resolution` 有没有意义？会不会只是把忙等变成空转？

### 8.3 每颗粒成本
粗算：15s 档在场粒子均值 **1914**（五轮中位），`physics + update_draw = 4.12ms`
⇒ **~2.15 µs / 颗粒 / 帧**
（纯 Python + numpy 打包 + 一次纹理上传）。
**这个数量级合理吗？有没有已知的 Kivy/GLES 手法能把"每颗粒提交"摊掉？**
（我试过端点纹理批处理，Canvas 降了 5ms 但 1% low 没动 —— 见 §6a。）

### 8.4 成本随屏幕面积缩放
几何随 widget `size` 缩放 ⇒ 屏越大 = 球越大 = 在途粒子越多 = 单帧成本越高。
**除了"减粒子"（被禁），有没有正当做法让成本不随屏幕面积涨？**
例如粒子的空间密度按 DPI 而非像素定义？

---

## 9. 请你回答的问题（按重要性排序）

1. 🔴 **§2.2.1：最新一代旗舰 SoC 也救不了它 —— 瓶颈是不是"单线程 Python"？**
   用户手机是**骁龙 8 Elite Gen 5**（当下顶级移动 SoC），报告帧率仍然偏低；
   而桌面 x86 模拟器跑到 **115.8**。如果这个推断成立，**GPU 侧优化的上限就很低**，
   正确的方向是压缩**每帧的 Python 调用次数**，而不是"算得更快"。
   请确认或推翻，并告诉我该怎么用最少的实验证实它。
2. **§6a 的归因对不对？** 慢帧的应用侧工作量与中位帧相同（四栏和 1.01×、粒子更少）、
   超额 5.8ms 全在"探针外" —— 在这个前提下，**还有没有应用侧能改善 1% low 的杠杆**？
   如果没有，请明说。
3. **§8.1 的 480Hz 量化模型**：怎么用最少的实验证实或推翻？如果成立，优化目标应该从
   "降低平均工作量"改成什么（"降低方差"还是"跨过 6.25ms 这道坎"）？
4. **§8.3：每颗粒 ~2.3µs 的 Python 成本**，在 Kivy/GLES 上还有多少空间？
   有没有我没想到的批处理/提交手法能把"每颗粒一次调用"摊掉？
   （约束：不许减粒子、不许换掉真圆玻璃/沙体、不许全局关 GC。）
5. **§8.4 成本随屏幕面积缩放**：给定"几何随面积缩放"这个设计，有没有**不解耦视觉与成本**的解法？
   （例如粒子的空间密度按 DPI 而非像素定义 —— 这算不算违反"不许减粒子"？）
6. **§8.2**：`Clock.schedule_interval(tick, 0)` + 5.556ms clock resolution 是不是一个真实
   的量化源？调它有没有正当收益，还是只是把忙等变成空转？
7. **我的测法（§4）有什么致命缺陷？** 特别是：只看 `Window.on_flip` 够不够？
   我该用什么工具拿到"合成器实际呈现帧"（FrameMetrics / gfxinfo / atrace）？
8. **有没有"我知道但我没问"的东西？** —— 如果你读完觉得我在错误的层面上优化，
   请直接指出来。

---

## 10. 数据在哪 / 怎么复现

```
仓库根：E:\AI_Tools\other\shalou_claude\pc\apk
  main.py                  应用本体（3155 行）
  frame_benchmark.py       基准装置（590 行，不参与正式运行）
  tools/flow_numpy.py      粒子物理的 numpy 向量化内核
  tools/flow_texture_experiment.py   端点纹理渲染器（Android 默认）
  tools/ab_device_benchmark.sh       交替多轮 A/B（含 sha256 身份校验）
  tools/analyze_ab.py                按轮配对差分析
  tools/inspect_flow.py              固定 seed(23) 的确定性渲染取图

本轮原始日志（每份含 1750 帧 × 15 字段的逐帧 trace）：
  benchmark_logs/ab/*.txt
  benchmark_logs/_ab_final.txt           v1.2 vs v1.21（5 对）
  benchmark_logs/_ab_v12_vs_139.txt      v1.2 vs 1.39（5 对）
```

**复现一次完整 A/B**（在一台已装该应用、且已 `adb root` 的 Android 设备/模拟器上）：

```bash
# ⚠️ 产物目录里已有 *.txt 时脚本会拒绝启动(防止两代产物混进同一个中位数)。
#    重跑前先归档:
mv benchmark_logs/ab/*.txt benchmark_logs/ab/_archive_$(date +%m%d_%H%M)/

tools/ab_device_benchmark.sh <revA> <revB> 5      # 一轮约 55 秒, 5 轮约 9 分钟
python tools/analyze_ab.py benchmark_logs/ab --arm-a <revA> --arm-b <revB>
```

脚本自带 10 条防线（每臂推自己那套文件、推送后逐文件 sha256、日志里的 `code_hash` 必须等于
本臂 `main.py` 的 sha256 前 12 位、渲染器必须是 `texture`、三档齐全、超时重试、
**检测到别的 A/B 脚本在跑就拒绝启动** 等）。**判据是按轮配对差，不是"中位差 > 组内极差"。**

**桌面预览**（不含 Android 特有的 GLES 行为）：

```bash
pip install kivy
python main.py
```

---

## 附：我为什么会把这份文档写成这样

这个项目踩过一个很贵的坑：**在"噪声 ≥ 效应"的指标上做 n=1 比较**，
以及**让验收判据和缺陷变成同一个东西**（"抖动幅度"越大指标越"好"，于是量出串珠却宣布修复）。

所以这份文档里：

- 每个数字都标了来源（哪一档、哪一轮、中位还是单轮）；
- "试过什么"不只写结论，也写**我当时的归因**，方便你判断我的归因对不对；
- §9 第 1 题的期望答案**可以是"没有杠杆"** —— 那我会停止优化，转而去改测量口径或改设计。

请你**优先攻击我**，而不是顺着我的框架给建议。

---

---

---

## 11. 附录：核心代码（逐字原文）

> **对方手上没有代码仓库**，所以这里把决定每帧成本的全部代码**逐字内嵌**
> （按 AST 精确截取，未做任何删改或简化）。省略的只有 UI 布局、音效、弹窗、
> 配置读写 —— 那些不在每帧路径上。
>
> 文件对应：`main.py` 3155 行 / `frame_benchmark.py` 590 行 /
> `tools/flow_numpy.py` 125 行 / `tools/flow_texture_experiment.py` 213 行。
>
> 一帧的调用链：`tick()` → `update_particles()`（物理）→ `redraw()` →
> `_group_stream_particles()`（打包）→ `flow_texture_experiment.update()`（上传）→
> 下一帧 `tick()`。

### 11.1 帧循环 `tick()` 与完成尘埃

*来源: `main.py` 第 1356-1386, 1388-1400 行(逐字原文, 未删改)*

```python
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
                    app = App.get_running_app()
                    if app is not None:
                        app.on_completed(self.duration)
                app = App.get_running_app()
                if app is not None:
                    app.on_run_state_changed()
        if self.running or self._completion_triggered:
            self.update_particles(dt)
        self.redraw()
        app = App.get_running_app()
        if app is not None:
            app.update_time(max(0.0, self.duration - self.elapsed), self.duration)

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
```

### 11.2 粒子物理 `update_particles()`（四栏里代码最长的一段，1.46ms）

*来源: `main.py` 第 1588-1831 行(逐字原文, 未删改)*

```python
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
            if vy < 0 and y <= mound_top:
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
            if now > d["end"] or d["y"] < mound_top - 1:
                continue
            new_dusts.append(d)
        self.dusts = new_dusts
```

### 11.3 numpy 物理内核 `tools/flow_numpy.py` —— **全文**

*来源: `tools/flow_numpy.py` 第 1-125 行(逐字原文, 未删改)*

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

### 11.4 端点纹理渲染器 `tools/flow_texture_experiment.py` —— **全文**

*来源: `tools/flow_texture_experiment.py` 第 1-213 行(逐字原文, 未删改)*

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
            texture.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
            if previous != count:
                mesh.indices = _indices[:count * len(self.indices)]
                part[4] = count
        for part in self.parts[chunks:]:
            if part[4]:
                part[0].indices = array("H")
                part[4] = 0


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

### 11.5 每帧提交 `redraw()` 及其帮手

*来源: `main.py` 第 2104-2175, 2177-2203, 2033-2102, 1901-1979, 1981-2013, 2016-2031 行(逐字原文, 未删改)*

```python
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

    def redraw(self):
        if not self._geom_ready:
            return
        remaining = self.get_remaining()
        now = time.perf_counter()
        colors = (self.sand_base, self.sand_light)
        if colors != self._render_colors:
            for color, _rect in self._sand_chords:
                color.rgb = self.sand_base
            self._neck_color.rgb = self.sand_base
            self._neck_solid_color.rgb = self._neck_fade_color.rgb = self.sand_base
            for (index, _size), (_group, color, _pool) in self._stream_pools.items():
                color.rgb = self.sand_light if index < 0 else self._color_table[index]
            self._splash_color.rgb = self._dust_color.rgb = self.sand_light
            self._render_colors = colors

        h_mound = self._mound_height_px()
        upper_height = max(0, 2 * self._R_inner - h_mound)
        self._sand_chords[0][1].size = (2 * self._R_inner, upper_height)
        self._sand_chords[1][1].size = (2 * self._R_inner, h_mound)
        side = self._neck_sand_side() if upper_height > 0 else []
        outlet = 2 * self._neck_y - self._taper["y_bot"]
        inlet = self._taper["y_bot"]
        transition = min(inlet - outlet, max(8, self._taper["t_in"] * 0.7))
        connected = bool(side and side[-1][1] <= outlet + 1e-6)
        fade_top = outlet + transition
        for i, quad in enumerate(self._neck_quads):
            if i < len(side) - 1:
                (x0, y0), (x1, y1) = side[i], side[i + 1]
                if connected and i == len(side) - 2:
                    y1 = fade_top
                quad.points = [self._cx - x0, y0, self._cx + x0, y0,
                               self._cx + x1, y1, self._cx - x1, y1]
            else:
                quad.points = [0] * 8
        if connected:
            pos = (self._cx - self._taper["t_in"], outlet)
            size = (2 * self._taper["t_in"], transition)
            self._neck_solid_rect.pos = self._neck_fade_rect.pos = pos
            self._neck_solid_rect.size = self._neck_fade_rect.size = size
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

    def _build_dynamic_canvas(self):
        """保留真圆/Stencil/Line 画法,只在几何变化时重建固定指令。"""
        self.canvas.clear()
        cx, Ri = self._cx, self._R_inner
        self._sand_chords = []
        with self.canvas:
            for yc in (self._upper_y_c, self._lower_y_c):
                bottom = yc - Ri
                StencilPush()
                Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
                StencilUse()
                color = Color(*self.sand_base)
                rect = Rectangle(pos=(cx - Ri, bottom), size=(2 * Ri, 0))
                StencilUnUse()
                Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
                StencilPop()
                self._sand_chords.append((color, rect))
            self._neck_color = Color(*self.sand_base)
            self._neck_quads = [
                Quad(points=[0] * 8) for _ in range(TAPER_SEGS + 1)]
            self._neck_solid_color = Color(*self.sand_base)
            self._neck_solid_rect = Rectangle(size=(0, 0))
            # 沙柱下段(孔口往上 transition 那段): 直接画不透明的沙色矩形。
            # 原来这里用 1×64 渐变纹理做 alpha 0.7→1.0 的"出口柔化", 但这条矩形
            # 只有几个像素高, **任何 alpha 变化都等于硬边** —— 实测在管内留下一条
            # 半透明横线(关掉颗粒层后单行跳变 dB=10.9;改成不透明后降到 5.3,
            # 剩下的是"沙柱→敞开喇叭口"的自然边界)。
            self._neck_fade_color = Color(*self.sand_base)
            self._neck_fade_rect = Rectangle(size=(0, 0))

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
                color = Color(*(self.sand_light if index < 0 else self._color_table[index]))
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
```

### 11.6 颈部颗粒（固定 128 图元池）

*来源: `main.py` 第 2214-2354, 2209-2212 行(逐字原文, 未删改)*

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
            tone_t = 0.28 + 0.72 * t
            if lights_p[i]:
                tr, tg, tb = light_r, light_g, light_b
            else:
                variation = int(phases_p[i] * tone_scale)
                if variation > 4:
                    variation = 4
                variation -= 2
                mix = tone_t + variation * 0.09
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

    def _hide_neck_grains(self):
        for color, line in self._neck_grain_pool[:self._neck_grain_count]:
            line.points = []
        self._neck_grain_count = 0
```

### 11.7 并行数组与 numpy 视图

*来源: `main.py` 第 1407-1409, 1411-1414, 1416-1428, 1430-1467 行(逐字原文, 未删改)*

```python
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
```

### 11.8 几何与“假物理”（含**几何随 size 缩放**）

*来源: `main.py` 第 135-144, 940-953, 956-957, 959-1049, 1051-1069, 1087-1089, 1092-1095, 1097-1101, 1103-1107, 1109-1118, 1120-1145, 1151-1153 行(逐字原文, 未删改)*

```python
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
        """体积比 → 高度比 raw=v⁻¹(vol)。球对称 ⟹ 上沙(1-raw)+下沙(raw)=1 守恒。"""
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

    def _fall_delay(self):
        return min(self._neck_fill_time + self._natural_flight_time + 0.05,
                   self.duration * 0.45)

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

    def _sand_half_w(self, y, yc):
        Ri = self._R_inner
        return math.sqrt(max(0.0, Ri * Ri - (y - yc) ** 2))
```

### 11.9 基准装置（**请审这一节的测法**）

*来源: `frame_benchmark.py` 第 28-64, 321-334, 381-590 行(逐字原文, 未删改)*

```python
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

def frame_statistics(intervals):
    samples = [dt for dt in intervals if math.isfinite(dt) and dt > 0]
    if not samples:
        return {"frames": 0, "average_fps": None, "one_percent_low_fps": None,
                "slowest_five_fps": [], "slowest_five_ms": []}
    slowest = sorted(samples, reverse=True)
    low_count = max(1, math.ceil(len(samples) * 0.01))
    return {
        "frames": len(samples),
        "average_fps": len(samples) / sum(samples),
        "one_percent_low_fps": low_count / sum(slowest[:low_count]),
        "slowest_five_fps": [1 / dt for dt in slowest[:5]],
        "slowest_five_ms": [dt * 1000 for dt in slowest[:5]],
    }

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

### 11.10 渲染器装载与回退路径

*来源: `main.py` 第 3119-3144 行(逐字原文, 未删改)*

```python
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
```

### 11.11 状态与缓冲区初始化

*来源: `main.py` 第 35-51 行(逐字原文, 未删改)*

```python
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
```

