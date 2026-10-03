# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠️ 铁律：PC v4 是唯一真理

**不要用"直觉"修改粒子物理、渲染方式或任何核心参数。** PC `hourglass_v4.py` 的公式和参数是经过反复验证的，Android 移植时只做坐标翻转适配。历史上所有"我觉得这样更好"的改动（扩张代替收缩、Ellipse 代替 Line、motion streak 等）全部以回退告终。**改之前先对照 v4，v4 怎么做你就怎么做。**

> **动手前先读 `README.md` 的「经验教训」章节** —— 排查手法(像素级 ASCII 网格诊断、按状态
> 分组比对定位伪影、tkinter/Kivy 各自的可靠抓图方式)、判断陷阱(目测比例不可信、"整幅变淡"
> 是遮罩不是 bug、`系数 × 参数` 要在两个极端都验证)和流程红线(改 `pc/` 先备份、跑测试脚本
> 会静默改写 `~/.hourglass_config.json`)。那一节是真踩出来的,能省掉重复的弯路。

## 项目定位

pc 版球形沙漏(`../hourglass_v4.py`，tkinter + PIL 真圆)的 **Android (Kivy) 移植版**，用 GitHub Actions 云端构建 APK。

- 本工程的 `main.py` 从 `pc/hourglass_v4.py` **从零重写为 Kivy**，**绝不复用** `../../android/main.py`(旧版有严重 bug，已弃用)。
- **唯一真值 / 视觉基准是 pc v4**：改几何/物理/视觉时对照 `../hourglass_v4.py`，不要参考 android 旧实现。
- **PC v4 是经过验证的成熟方案**：粒子物理、管壁渲染、参数配置均已打磨好。移植时原样照搬，只做坐标翻转适配，不要用"直觉"替代 v4 的公式。
- 独立 git 仓库 → https://github.com/twoplate2/hourglass-ball

## 运行 / 构建

桌面预览(验证逻辑和渲染，Buildozer 不支持 Windows 原生，本地只能预览不能打包)：
```
pip install kivy
python main.py
```
非 Android 默认窗口 400×800；**点沙漏两个球 = 开始/暂停**(无"下落"按钮)。

云端构建 APK：push 到 `main` → 自动触发 `.github/workflows/build-apk.yml`，首次约 15-20 分钟(后续命中 `~/.buildozer` 缓存 5-8 分钟)。产物：仓库 Actions → 最近成功 run → 底部 Artifacts → `hourglass-apk.zip`。也可在 Actions 页手动 `workflow_dispatch`。

没有 lint / 单元测试；验证靠桌面 `python main.py` 跑三态(满/中段/漏完) + 装机实测。

## ⚠️ 最关键的构建约束(不遵守必失败)

`buildozer.spec` 里 **`p4a.branch = v2024.01.21`** —— 锁死 python-for-android 到 2024 tag。不锁的话 2026 年新版 p4a 默认下 Python 3.14 alpha，与 Kivy 2.3 的 C API 不兼容，编译 `kivy/graphics/compiler.c` 必报 `_PyLong_AsByteArray` 参数数量错。tag 名格式必须严格 `v2024.01.21`(v + 年.月补零.日补零，写错 git clone 失败)。

配套锁(在 workflow 里)：`cython<3.0` + `buildozer==1.5.0`，host Python 3.10，Java 17，`ubuntu-22.04`(不要 24.04)。改 `buildozer.spec` 后若旧缓存脏，把 workflow `cache key` 的 `v1` 改 `v2` 强制破缓存。完整踩坑史见 `../../android/BUILD_APK.md`。

## main.py 架构(单文件，~1230 行)

- **`HourglassWidget`**(347-936)：几何 + 物理 + 渲染(沙漏本体，含 `tick` 帧循环、`update_particles`、`redraw`)
- **`HourglassApp`**(941-1228)：v2 布局 UI(顶部 6 色块 / 倒计时 Label / 画布 / 底部 周期+音效+开始+重置)
- **`_SoundProxy`**(~162-360)：Android `AudioTrack` MODE_STATIC 硬件循环 / Windows `winsound` 驱动层循环 / 桌面 `SoundLoader` fallback；另含 `close()` 释放后端资源（切换音效用）
- **`_SandBgPopup`**(305-342)：浅色弹窗，双层兜底覆盖 Kivy 默认深灰
- **`CenterTextInput`**(132-149)：Kivy `TextInput` 无 `text_align`，用 `CoreLabel` 测文本宽度动态算 `padding` 实现居中
- **对数倍率滑杆**(周期弹窗内，`kivy Slider` 0..1：`_mult_from_slider(t)=⌈600^t⌉` 向上取整 1–600 倍，与倍数按钮**不同步**；已滑段金色 `value_track_color`，未滑段用暖灰棕贴图 `ui/slider_track.png`（#8a7a68，默认浅灰贴图太接近弹窗底色；贴图需随 `source.include_patterns` 进 APK）；`MAX_DURATION=360000`；倒计时 `_fmt_countdown_pair` H:MM:SS 自适应)

### 核心设计：假物理 + 完整球 + 球体积微积分
- 唯一真值是 `elapsed/duration`，所有可见几何从它派生(本质进度条，**不要引入真物理模拟**)。
- 两个**完整球**(非锥形、非球冠) + 颈部圆柱管；`R = (ball_h² + neck_w²)/(2·ball_h)` 保证球顶 w=0、截口处 w=neck_w 与管无缝。
- 球↔管之间是**二次贝塞尔曲线过渡**(见下)，不是直接怼上去的矩形管。
- 球体积 `v(t)=3t²-2t³`，`_raw_height_ratio` 用数值积分查找表(101 档)反查"体积→高度"。球对称 `v(t)+v(1-t)=1` ⟹ 上沙`(1-raw)` + 下沙`(raw)` = 1 **严格守恒**(改这块前先确认守恒不被破坏)。
- 上、下沙都用延迟 `_effective_fallen`(物理计算粒子飞到底的时间，`_fall_delay`，短周期按 `duration*0.45` 缩放)；下沙前期靠**沙面宽度变化**展示进度，高度只给极小保底(`MOUND_FLOOR_*`)，**不拔高**。
- `neck_w` 用 **log 插值**：短周期→宽颈，长周期→窄颈，范围受屏幕比例约束。

### 渲染：玻璃和沙都用 Kivy 真圆(关键，别破坏)
- 玻璃壳：`Ellipse` 画球(**同心椭圆相减**得均匀描边) + 颈部曲线过渡。缓存于 `canvas.before`，仅 `neck_w` 变时重建。
- **球↔管曲线过渡**(移植自 pc v4，勿退回矩形管)：球拿极点接管子、球面在极点近乎水平 → 环壁横向摊开
  `√(R²−Ri²)≈√(2R·ow)`(**与 neck_w 无关**)，撞上竖直管壁形成"扁平肩台 + ~84° 硬折角"(垫块感)。
  解法：`_bezier2()` 造一条二次贝塞尔，控制点 `P1 = 球切线 × 管壁线` 的交点 → 两端 C1 相切。
  - `self._taper = {out_pts, in_pts, t_out, t_in, y_bot}` 在 `_rebuild_height_table` 里算好
  - **起点半宽必须 ≥ 肩台半宽**，否则细颈(长周期)时肩台原样残留：
    `w_out = min(R·0.45, max(t_out+2, TAPER_K·nw, shoulder·1.06))` → 恒为 ~44px，喇叭口固定、孔径随周期变
  - `tube_h = h*0.055`(原 3%)给过渡腾空间，R 随约束自动缩 ~2.7%；`y_bot` 有护栏不得越过 `neck_y`
  - Kivy 没有多边形图元 → 沿曲线**逐段 `Quad`**：擦极冠(BG 色)→ 直筒 → 外轮廓(壁)+ 内轮廓(腔)
  - **颈部沙柱直接复用 `in_pts`** 画成同形 Quad 带 → 与内腔天生贴合；颈部高光要用 `t_in` 不是 `neck_w`
  - **沙柱只到直筒下端，下喇叭口敞开不填沙**(`_neck_sand_side`)：填了会变成"绿喇叭悬在空球上"、
    沙流与颈部断开；敞开后沙从孔口流出，与粒子自然接上
  - **沙柱在 `NECK_FILL=0.25s` 内从上往下注满**，不是 `elapsed>0` 一帧切换 —— 喇叭口面积大，
    瞬间从空变满非常刺眼；短周期按 `duration*0.15` 缩放
- 沙体弓形：`StencilPush/StencilUse/StencilUnUse/StencilPop` 把内壁球 `Ellipse` 裁出"y ≤ 沙面"的真圆弓形。
- **沙边和玻璃内壁都是 `Ellipse` 圆 → 同一种真圆技术、严格贴合**。这是复刻 pc"玻璃和沙必须同一种技术，否则边缘失配(月牙/缝)"的核心。**绝不用 `Mesh`/多边形拼弓形**(那是 android 旧版渲染 bug 的根源)。
- 粒子用 `Line`(主流) + `Rectangle`(splash/flares/dust)，按颜色排序减少 draw call。
- **流量守恒**(移植自 PC v4)：粒子加速下落时按 A·v=常数横向收缩 `shrink = max(0.50, (60/v_at_y)^0.5)`；颈部 6px 入口区不缩；40px 平滑过渡区从 1.0 渐变到目标值；触底 30px 喇叭口微扩。wobble 随 shrink 同比例衰减(`wobble × (1-shrink×0.4)`)。

### 出口以下的射流包络(2026-10-03,详见 `NECK_REDESIGN.md`)

用户反馈"颈部有明显的矩形区域"。逐行游程量出来的真凶:**出口以下 21px 带里玻璃从 20px
张到 83px, 而沙流恒 20px**(单侧最多露 31px 的 `GLASS_FILL` 楔形)。修法是给出口以下的
横向 clamp 一个**物理包络**(`JET_VENA/DIFFUSE/SPREAD/EDGE`, `main.py` 顶部常量):

```
u   = min((outlet - y) / JET_SPREAD, 1)
env = tube_lim * (JET_VENA + (JET_DIFFUSE - JET_VENA) * u)
env += sin((outlet - y) * JET_WAVE_K) * tube_lim * JET_EDGE   # 沿深度相干, 波长 ~15px
lim = min(env, 球壁斜坡)      # 仅 y < outlet
```

⚠️ **摆动只保留一次 `np.sin`**。早先用两项合成(`sin(d*0.5)*0.72 + sin(d*0.19+1.7)*0.28`),
在真实规模(n=2900)上直接量: **36.2µs = `flow_numpy.step()` 的 6.4%**, 折到设备物理预算
(1.5–1.9ms/帧)约 **4–5%, 超过项目 3% 阈值**。砍成一次 `np.sin` 后 **16.5µs / 2.9%,
折合 0.9–1.1%** ✅, 而边缘粗糙度不降反升(0.99→1.09 逻辑px)。

- **改在物理层**(`update_particles`)是刻意的:纹理渲染器整体替换 `_draw_stream` 且读
  `pv.nx`(物理数组零拷贝视图),在渲染层改 x 会「桌面变真机不变」。
- ⚠️ **clamp 有两份实现**:`main.py` 的标量循环 **和** `tools/flow_numpy.py` 的向量化版,
  `tools/test_physics_equiv.py` 要求逐位等价 —— **改一处必须改两处**,常量还要同步进
  `consts` 字典(漏一个就 `KeyError: 'jet_edge'`)。测试文件里也有一份标量参考实现,共**三处**。
- **逐颗粒抖包络是无效的**:每图像行压着约 6 颗粒,边缘取最大值,随机被抹平
  (实测残差仅 0.29px)。**只随 y 变化**才有效(0.99px)。且前提是 `env` 要**比粒子的自然
  展宽更窄**,否则 clamp 根本不 binding —— 出口 18px 之外射流宽度由粒子自身决定。
- **不要再试"填满喇叭口"整族**(贴壁沙霜/沙雾/颗粒域扩到镜像颈):`_draw_neck_grains` 画的是
  1–2px `Line`,盖不满 86×22px 的面;而且真实沙漏出口以下本就该是空的。
- **不要再试"缩窄喇叭口"**:`w_out = min(R*0.45, max(t_out+2, TAPER_K*nw, shoulder*1.06))`,
  实测恒由 `shoulder*1.06` 主导(47.95 vs R*0.45=74.32、TAPER_K*nw=37.40),而 shoulder 只由
  球半径 R 与描边宽 ow 决定 ⇒ **喇叭口宽度被钉死**,缩小会让"扁平肩台+硬折角"重现。
- 残余不可约项:3600s 档(管 7.4px)在 GLES2 无 AA/无 MSAA 下只能是一根诚实的细圆柱。

### 渲染分层(`redraw()` 中的 draw 顺序)
1. 上沙弓形(Stencil 裁切)
2. 下沙堆弓形
3. 颈部沙柱(沿 `_neck_sand_side()` 的 Quad 带；上喇叭口+直筒，下喇叭口敞开给粒子)
4. 沙流粒子(按 `_color_table` 排序后 `Line` 渲染)
5. splash 反弹粒子(`Rectangle`，`sand_light` 色)
6. 触底闪光(0.08s 寿命，4+ 半透明方块)
7. 完成尘埃(25 颗，1s 寿命，向上喷射)
8. 颈部高光(仅漏完时可见)
9. 暂停遮罩(`BG_COLOR` 55% 透明度)
10. 完成闪烁(350ms 白色 25% 全屏)

### 沙流渲染器：安卓走批处理(2026-10-02)

`Line` 在 **`width>1` 时不用 `glLineWidth`**，而是**每条线**自建一个带 10 段圆头帽的三角网格。
峰值约 2500 条 `width=2` 的粒子线 = 数千次网格提交 + 每颗粒的 Python 提交成本。
桌面 GL 余量大(实测帧率对粒子数几乎不变：63.2→63.3fps)，所以**在 PC 上永远调不出这个问题**；
只有 GLES 暴露：实测帧耗时 ≈ `2.5ms + 10.05µs × 在途粒子数`，2829 颗 → 30.9ms/帧(32fps)，
表现为"一整段周期帧率都上不去"。

`main.py` 末尾的 `FLOW_RENDERER` 决定用哪套：`line`(原样) / `batch` /
`gpu`(每颗粒 3 次切片赋值) / `texture`(端点搬进顶点纹理，每颗粒 3 个 float)。安卓默认
`texture`；装载失败或设备无顶点纹理采样时**自动退回 `line`**，不给出沙制造风险。
每次基准日志都记录 `Flow renderer:`，便于回溯某次数据用的哪套。实测对照(同机同配置)：

| 渲染器 | 15s 平均 | Canvas | 图元 |
|---|---|---|---|
| `line_pool` | 47.9 | 8.46ms | 4.94ms |
| `mesh_batch` (朴素) | 42.6 | 3.24ms | **13.21ms**(净亏) |
| `mesh_endpoint_texture` | **65.4** | **3.06ms** | 5.28ms |

**端点纹理的布局(2026-10-02 改)**：一个颗粒的 `x/bottom/top` **连续放 3 个纹素**
(`Texture.create(size=(capacity*3, 1))`)，于是每颗粒只要 **1 次** `struct.pack_into`
(原来是 x/bottom/top 各占一行、每颗粒 3 次)。`capacity` 固定等于 `CHUNK`(512)，所以
u 的步长 `1/(512*3)` 对所有分块都一样 —— shader 里只要一个 `uniform float texel_step`，
不用逐块改。实测 15s 峰载段帧时间 13.77→13.54ms、5s 12.62→12.42ms(平均帧率
89.0→90.2 / 87.3→88.9)，104 张截图逐像素 **0 差异**。
⚠️ 别把 `capacity` 改回按需取幂：u 步长会随分块变化，shader 就没法用一个 uniform 表达。
⚠️ **设 uniform 要用 `context["名字"] = 值`，不能写 `context.shader["名字"] = 值`** ——
   `Shader.__setitem__` 是 Kivy **2.3.1** 才有的，设备上跑的是 **2.3.0**，写了会在
   `_build_dynamic_canvas` 里 `TypeError: object does not support item assignment`，
   表现为 app 一启动就死。PC 上是 2.3.1，**本地像素闸门照样全绿**，只有装机才炸。

⚠️ 打包进 APK 的只有 `tools/*.pyc`，所以按 `sys.path + import` 装载，不能按 `.py` 路径。
⚠️ 纹理方案的报错发生在**画布构建时**(不是 `install()` 时)，外面 try/except 包不住 ——
必须**装载前**先探测 `GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS`，否则不支持的设备直接崩。

### 每帧热循环的等价改写(2026-10-02)

⚠️ **`tools/verify_particle_hot_loop.py` 已失效**(2026-10-03 发现):它的 `Fixture` 还是
**并行数组之前**的形态(塞 `particles=[dict...]`),而 `update_particles` 现在用
`px/py/pvy/...` + `pn`,**拿同一个文件自比都会 `AttributeError: 'Fixture' object has no
attribute 'pn'`**。修它等于重写 Fixture,先别花时间 —— **等价守卫改用
`tools/test_physics_equiv.py`**(标量参考 vs `tools/flow_numpy.py`,逐位比对,当前代码有效)。

Canvas 降下来之后剩下的时间几乎全是纯 Python(物理 + 每帧提交)。这类优化**必须
逐位等价**：算式一字不改、随机数调用顺序一个不挪，否则粒子流一变画面就变了。
手法见 `README.md`「压 Python 热循环」；实测 11.7ms/帧 → 6.7ms/帧(−43%)，
函数调用数 −60%，`max`/`min` 内置调用从 220 万次降到热点榜外。

**验收**：`tools/inspect_flow.py` 的 `random.seed(23)` 下逐像素比对必须 0 差异。

### 帧率优化与隐藏 Benchmark(2026-10-01)

- `_build_dynamic_canvas()` 在几何变化时重建固定指令；`redraw()` 更新保留的 Stencil 沙面、Quad 沙柱及 Line/Rectangle 图元池。不要重新退回逐帧 `canvas.clear()`。
- 同色同线宽共用 Color 指令，渲染分桶不排序物理粒子列表；没有减少粒子数量、改变形状或降低抗锯齿。
- 沙柱注满后从直筒出口生成粒子，出生时刻均匀分布在帧内；恒加速度积分、单调时钟、暂停冻结粒子。重力/生成速率/收缩/摆动参数保留。
- `frame_benchmark.py`：底部音效与开始之间的 spacer 长按 3 秒；依次测试 1/5/15 秒。
  用 Window.on_flip 间隔计算平均 FPS、1% low、最慢 5 帧分别的 FPS；测试时不显示结果弹窗。
- 结果页有帧率曲线和「复制结果」；每轮自动保存 benchmark_logs/*.txt，含逐帧更新/Canvas/Swap/GC 耗时。
- 复制报告 v2 将分段均值、并发粒子峰值、GC、最慢帧明细及设备/代码指纹一起导出，不改 FPS 算法。
  环境只在开始/周期结束查询；PowerManager 从 getSystemService 返回对象需 jnius.cast 后使用。
- Windows/Python 3.11 的 monotonic() 基于 GetTickCount64，精度只有 15.625ms；动画统一用 perf_counter()，
  Clock.schedule_interval(tick, 0) 跟随帧更新。分组复用粒子引用，避免每帧创建上千个临时坐标列表；不要全局禁用 GC。
- `_reserve_stream_lines()` 按最慢初速度的飞行时间预留图元，不限制/减少实际粒子；重置和新周期开始前全量回收旧场景，
  不在暂停恢复时强制回收。空池容量保持可增长，窄颈全 1px 粒子的工况也要预留正确线宽。
- BenchmarkRunner 快照并恢复动画状态，不调用配置保存；取消、后台、退出必须解绑 on_flip 并取消定时事件。
- `tools/verify_hourglass.py` 验证同几何下渲染像素、入口、取消恢复及四轮完整测试；不读取/修改用户配置。
  `tools/profile_frames.py` 的独立测试窗口必须设正式 BG_COLOR，否则颈部背景覆盖会在黑底上露成两条横板。

### 坐标系陷阱(最易出错)
Kivy y 向上(原点左下)，pc 是 y 向下 —— 所有几何**上下翻转**：上球 y 大、下球 y 小；重力 `g = -450`(Kivy y 向上，向下运动是 y 减小)；粒子触底判断是 `p.y ≤ mound_top`。移植 pc 逻辑时逐个翻转，别照抄符号。

### 自适应几何(不写死坐标)
`_rebuild_height_table` 从 widget `size` 派生：`R = min(宽约束, 高约束)`，在 pc 380×730 比例下复现 `R≈168`；由 R 反推 `ball_h` 保证球公式成立。改窗口/布局不破坏居中。`neck_w`/字体/保底高度按屏幕比例或 `dp()`，不用绝对像素。

### 音效系统：硬件循环消除缝隙

`_SoundProxy` 按平台选最优方案：

| 平台 | 方案 | 循环点 | 缝隙 |
|---|---|---|---|
| Android | `AudioTrack` MODE_STATIC | `setLoopPoints(0, frames, -1)` 音频 DSP 硬件回绕 | 0ms |
| Windows | `winsound.PlaySound` | `SND_LOOP` 驱动层循环 | 0ms |
| 其他桌面 | Kivy `SoundLoader` | `loop=True` 应用层循环 | 可忽略 |

Android 方案的核心细节：
1. 手动解析 WAV RIFF 头（遍历 chunk 找 `data`，WAV chunk 2 字节对齐需处理奇数 padding）
2. 提取 PCM 裸数据（16bit only），**直接把 Python `bytes` 传给 `write(byte[],int,int)`** —— pyjnius 的 `calculate_score` 对 `'[B'` 参数遇 `bytes/bytearray` 加 10 分、对 `'[S'` 直接返回 −1（确定性命中 byte[] 重载，不会误选 short[]），`convert_pyarray_to_java` 对 `bytes` 走单次 `SetByteArrayRegion` 整块拷贝。⚠️ **pyjnius 没有 `jarray`**，写 `from jnius import autoclass, jarray` 会 ImportError 且被 `except` 吞掉 → 整条 AudioTrack 路径静默不执行（历史 bug，见下方"音效卡顿"）。MODE_STATIC 的 native `writeToTrack()` 每次 write 都 memcpy 到缓冲**起始处**，必须一次写完，不能分块
3. **用 `$Builder` 优先**：pyjnius 用 `autoclass('AudioTrack$Builder')` 的 `$` 符号访问嵌套类（`.` 点号无法解析）；降级兜底：传统 `AudioTrack(streamType, ...)` 构造函数
4. `getState() == STATE_INITIALIZED` 校验 + `write()` 完整写入校验 + `setLoopPoints()` 返回值校验
5. `MODE_STATIC` → 一次性写入全部 PCM + `setLoopPoints(0, frames, -1)` 硬件回绕
6. **三星兼容**：`stop()` 后设 `_needs_reload=True`，重播时调 `reloadStaticData()`；首次播放跳过
7. `_active` 标志**后置于** `play()` 成功后，防止异常后半永久静音
8. **Android AudioTrack 失败时 fallthrough 到 Kivy SoundLoader 兜底**（至少有声）

**采样率不必和设备原生率对齐**：AudioFlinger 的 SRC 挂在 track 流上，loop 在更上游解析成连续重复流，**不会在循环点重置相位**。曾经"22050→48000 非整数重采样导致循环点跳变"的说法已被推翻（真因见"音效卡顿"）。也**不要**在 Python 里自己重采样 —— 逐样本循环 72 万样本跑在 UI 线程上会冻屏数秒。`_init_audio_track` 只打印 `wav_rate=/native_rate=` 供诊断。

`sand_loop.wav` 是 15s 无缝 PCM 16bit mono **48000Hz**（~1.4MB）。修改采样率需同步调整 `_init_audio_track` 中的参数。

**⚠️ MODE_STATIC 的 `getState()` 在写数据前必然是 `STATE_NO_STATIC_DATA`(2)**，不是 `STATE_INITIALIZED`(1)——官方定义就是"已成功初始化、使用静态数据、但还没收到那份数据"。**拿 `==STATE_INITIALIZED` 当写入前的校验，永远不可能通过**（历史 bug：真机 `state=2` → 抛异常 → 静默回退 MediaPlayer）。正确顺序：写前接受 `{1, 2}` → `write()` → 写后再确认 `==1`。

**后端可见化**：`_SoundProxy` 有 `backend`（`audiotrack`/`winsound`/`soundloader`/`none`）和 `error` 两个字段；`HourglassWidget.sound_problem_desc()` **只在没走到无缝后端时**返回文案，音效弹窗底部据此显示一行红字（正常时高度 0，界面上看不见）。错误串要按宽度换行 + `texture_size` 绑定高度，否则窄屏上会被裁掉最关键的开头。这行字曾 30 秒定位上面那个 `state=2`，而在此之前盲改了两轮都没摸到——**兜底路径必须把失败原因自己说出来**（见 README 经验教训）。

### 音效库（5 选 1：沙沙/水流/风/钟表 + 无声音）

- 「音效:开/关」开关已删除，旧配置键 `sound_on` 忽略。**主界面音效按钮直接显示当前音效名（沙沙声/水流声/风声/钟表声/无声音）**。打开弹窗点选项 → 点击即切换（运行中停旧播新），**弹窗不关、高亮跟随点击项，可连续试听**；底部「确定」按钮是唯一关闭出口。选「无声音」(静音)：按钮显示「无声音」+ 暖灰底 `#b7afa4`，启动静默。
- `SOUND_EFFECTS` 表（名字与 pc v4 **逐字一致**，共享配置文件）4 种：沙沙声（`sand_loop.wav`，合成保留）/ 水流声 / 风声 / 钟表声（`sounds/{water,wind,clock}.wav`，48000Hz 16bit mono 无缝循环；water/wind 14s，clock 8.62s）。后 3 个为**实录**，源 MP3 仅本机不入库（`.gitignore` 已含 `mp3/`）。
  - **噪声类（water/wind）**：首尾最像片段选段（频谱相似度+响度差+接缝低谷惩罚）→ 互相关对齐 + crossfade 焊循环 → 软压缩 + RMS 对齐。
  - **⚠️ 有拍子的（clock）绝不能用同一套流程**：钟表是滴/答强弱交替（周期 0.2535s、强弱比 2.48），循环长度必须是**整数个滴答对**、切点落在滴答前的静音里，否则每绕一圈就抢/拖一拍；且**不能压缩**（压扁了强弱交替就没了）。由 `tools/make_clock_loop.py` 按拍切：包络检出滴答 → 取同奇偶强拍 i→j（跨偶数拍）→ 切 `t[i]-60ms` 到 `t[j]-60ms` → 40ms crossfade 全程待在静音里 → 只做峰值归一化到 0.90。踩坑史见 README 经验教训。
- 切换 `_set_sound(name)`：**①新建 `_SoundProxy`（失败→旧态原样保留）②stop 旧 ③`close()` 旧（AudioTrack `release()`）④挂新 ⑤running 则 play**；同名幂等。**不要给旧实例加 reload 复用**——AudioTrack MODE_STATIC 缓冲长度构造时锁死，换 wav 必须重建 track。`_SoundProxy.close()` 释放后端资源。
- 音效弹窗 `on_sound_picker` 复用 `_SandBgPopup`，遍历 `SOUND_OPTIONS`（= `SOUND_EFFECTS` + `(SILENT_NAME, None)`，现 5 项两行 3+2）+ 底部**「确定」按钮**（唯一出口），当前项金色高亮，高度自适应（复用周期弹窗 `minimum_height` 三行链路）；按钮 label/btns 的 lambda 必须默认参数绑定（闭包延迟绑定坑）。`_on_sound_picked(label, btns)`：点击即 `_set_sound`，**不 dismiss**，只刷新 `btns` 高亮；「确定」→ `_close_sound_picker(popup)`（`_sound_popup=None` + dismiss）。选「无声音」→ `_set_sound` 静音分支：stop+close 旧 proxy、`_sound=None`（不建 proxy，`_play_sound/_stop_sound` 对 None 空操作）。`_update_sound_btn()` 把主按钮文字设为当前音效名（静音暖灰、有声金色）。

### 完成播报(2026-10-01;2026-10-03 改为动态拼接)

**播报语随周期变化**(「X小时Y分Z秒的沙漏计时完成」),无法预录成一条 ⇒ 改为**词块拼接**:

- `sounds/voice/*.wav` 76 个词块 = 0–60 整词(`n0`..`n60`,覆盖分/秒全域与小时 1–60)
  + 数字 `d1`..`d9` + `ten`/`hundred`(小时 61–99 与 100 拆着读) + `hour`/`min`/`sec` + `tail`。
  24kHz 单声道、逐块 RMS 归一化(否则拼出来忽大忽小)。生成:`tools/generate_voice_tokens.py`。
- `_VoiceBank` 启动时读进内存;`sentence_keys(sec)` 出词序(零分量省略,`hour` 与 `sec`
  之间夹「零」),`build_pcm()` 拼成**一段** PCM,`_completion_chime(rate)` 现场合成钟声
  (**带缓存** —— 纯 Python 三重循环 1.3 万帧,不缓存会卡在完成那一帧上;启动时预热)。
- **必须走文件**:拼好的 PCM 写进 `config_path()` 同目录的 `completion_announcement.wav`,
  再交给 `_SoundProxy(path, loop=False)`。因为三个后端里有两条**按路径**播放
  (winsound `SND_FILENAME`、Kivy `SoundLoader`),只传裸 PCM 会漏掉它们。
  一次播放、无接缝、不占第二个通道(winsound 单通道,分段播会被下段掐掉)。
- **词库缺失/采样率不一致/声道不对 → `ok=False`,静默回退**到预录的整句 `sounds/completion.wav`
  (由 `tools/generate_completion_voice.py` 生成,仍是兜底,不要删)。
- ⚠️ **新增 wav 必须进 `buildozer.spec` 的 `source.include_patterns`**,现在是
  `sand_loop.wav,fonts/*.otf,sounds/*.wav,sounds/voice/*.wav,ui/*.png` ——
  `sounds/*.wav` **不匹配** `sounds/voice/` 的嵌套。漏了的表现是**桌面有声、装机无声**。
- 改 `SAND_PRESETS` / `SOUND_OPTIONS` 顺序**不影响**本播报(词块按周期而非沙色/音效索引)。
- 已知质量点(用户 2026-10-03 反馈「数字部分可能不是很自然,但可接受」):词块两端各留
  25ms padding ⇒ 连读略顿,可改交叉淡入;小时 61–99 是两次 TTS 拼的,可补录整词。

### 完成弹窗(2026-10-03)

- `HourglassApp.on_completed(duration)`:`auto_dismiss=False`(**不点不关**,用户明确要求),
  暖白底 + 金色大字时长(`_fmt_duration_cn`)+ 暗红「好」。Benchmark 期间不弹。
- ⚠️ **`tools/*.py` 必须桩掉 `HourglassApp.on_completed`**(和桩音效同理):
  `inspect_flow.py` / `profile_frames.py` 无条件桩;`verify_hourglass.py` 只在
  `--completion-demo` 时保留。**不桩的后果**:用例跑过第 1 秒档 → 弹窗弹出并**永不关闭** →
  盖住其后**所有**裁图,取证工具静默失效(踩过一次,104 张裁图全是弹窗)。

### `_SoundProxy` 生命周期补充

- `_SoundProxy(loop=False)` 用于完成播报;默认 `loop=True` 保留背景音的全部行为。
  Windows 无 SND_LOOP，AudioTrack 的 loopCount=0，SoundLoader.loop=False；重置/新周期必须停止旧播报。
- 动态拼出的那份是 `_completion_spoken`(独立于兜底的 `_completion_sound`),
  `on_stop` / `on_resume` 都要 `close()` 并置 `None`(AudioTrack 句柄会泄漏)。
- `_completion_triggered` 保证每轮只播一次；`completion_enabled` 在 Benchmark 中临时关闭并恢复。
  「无声音」只控制背景循环音，完成提示独立。Android 恢复前台时重建单次播放后端，退出时停止并释放。

### 配色系统(独立暖金/沙色系，不随沙色变化)

弹窗和主界面底部按钮使用固定配色(`POPUP_*` 常量，60-67 行)，不受沙漏沙色切换影响：

| 常量 | 色值 | 用途 |
|---|---|---|
| `POPUP_BG` | `#faf5eb` | 弹窗底色(暖白) |
| `POPUP_GOLD_SEL` | `#caa450` | 选中项按钮(暖金) |
| `POPUP_CONFIRM` | `#9e3b29` | 音效弹窗「确定」按钮(暗红，与选中金色区分) |
| `POPUP_UNSEL_BASE` | `#a89078` | 未选基础周期按钮(暖棕) |
| `POPUP_UNSEL_MULT` | `#b8a088` | 未选倍数按钮(浅棕) |
| `POPUP_CANCEL_BG` | `#d8d2ca` | 取消按钮(暖灰，比未选亮) |
| `POPUP_TEXT` | `#332418` | 按钮/标签文字(深咖啡) |

主界面底部按钮：
- 周期按钮：`#c4ae8e` 暖米色实色
- 音效：`#caa450` 92%(金色)；选「无声音」→ `#b7afa4` 暖灰；按钮文案始终显示当前音效名（五种之一）
- 开始(停止态)：绿色 `#5b9e3e` + 白字
- 暂停(运行态)：橙色 `#d98e3e` + 白字

`_SandBgPopup` 双层兜底覆盖 Kivy 默认深灰：
1. Popup 本体 `canvas.before` → 奶油底(填充 _container 外间隙)
2. `open()` 后 `_apply_light_theme()` → 清空 `_container.canvas.before` 并画奶油底
3. 标题栏仍为 Kivy 默认深灰，标题文字白色

### 粒子系统(移植自 PC v4，坐标翻转适配)
- 主流粒子：从上球截口生成，`rate=600*speed_factor`，重力 `g=-450`
- **流量守恒**(原样移植 v4)：`shrink = max(0.50, (60/v_at_y)^0.5)`，6px 入口不缩，40px 平滑过渡，触底 30px 喇叭口
- wobble 随 shrink 衰减：`wobble × (1 - shrink × 0.4)`
- 渲染：`Line` + 速度拖尾 `trail = max(2.0, abs(vy)*0.08)`，`width=p["size"]`(85%为2,15%为1)
- **不要改成扩张(spread)/Ellipse/Rectangle**：v4 的收缩+Line 方案已经过验证，改形状或改物理都只会让效果变差
- 触底事件：EMA 更新 `mound_peak_offset` + 25% 概率 spawn flare + 50% 概率 spawn splash
- splash 反弹粒子：向上反弹 vy=-110~-55，实心方块渲染，受 `_sand_half_w` 横向约束
- 完成尘埃：漏完时 spawn 25 颗，1s 寿命，向上喷射

### 新沙流衔接与颗粒表现

当前 Android 版的视觉基准在本工程验证，不再要求沙流像素与旧 PC 完全一致；玻璃与真圆沙体仍须保持原轮廓。
- `_effective_fallen()` 按 `duration - fall_delay` 归一化，`redraw()` 使用互补上下高度，防止末帧补满跳变。
- `_particle_motion_scale` 仅在飞行时间放不进短周期时加速，初速度乘倍率、重力乘倍率平方；不改生成速率。
- `get_mound_top_y()` 与实际 Rectangle 沙面一致。帧内计算触底时刻，避免越过沙面后才生成效果。
- 新生颗粒保存 `trail_time=18–32ms`；高光组在主体组后面，仍用 Line，不增加装饰粒子。
- 反弹能量来自入射速度的 14–28%，闪光为细小横向颗粒；完成尘埃保持原 1 秒时间尺度。
- 视觉工具 `tools/inspect_flow.py` 保存相同时刻图片和接触差/末段高度指标；不能用模拟时钟结果宣称帧率。
- `_draw_neck_grains()` 把已流出的粒子投影回**整条**颈部轮廓(喇叭口+直筒)，横向按轮廓半宽展开成扇形；色调只在 `[底色 → sand_light]` 之间且**入口端不归零**(往暗色调会变脏斑)。旧版只铺直筒、入口端可见度从 0 起，于是"可见度前沿"以上是零方差纯平色块、以下才有颗粒 —— 在颈部留下一条**横向分界线**(实测逐行 std 由 0.00 跳到 1.51，且前沿每帧在管深 23%~58% 漂移)。改后管内颗粒密度与改前持平(0.357/0.359、0.181/0.134、0.275/0.250)。
  固定 128 个图元池，不新增物理粒子，重置后必须隐藏。
- 保持直管颗粒 Color.a=1；Kivy 半透明宽 Line 会触发额外 Stencil 通道。隐藏时只清空 points。
  暂停/完成遮罩未激活时零尺寸、激活时恢复完整尺寸和原透明度，不改可见效果。
- `--capture` 用于最慢帧状态回放，状态复制会影响采样；性能对比须另跑无 capture 的测试。
- 0.1.1：真实拖尾可跨出口到直管内；原色表中的轻微色差取自已有相位，不增加随机调用。
  直管末段使用一次创建的 1×64 RGBA 渐变纹理过渡材质，两个 Rectangle 合成起步强度，不逐帧上传纹理。
  GL 重载观察者必须恢复该纹理；隐藏时零尺寸，玻璃和真圆沙体不改。

### 无加载图片启动

`presplash.filename` 指向 `ui/startup_blank.png`（1px、与 BG_COLOR 同色），避免移除配置后退回默认 Python 图。
Android `on_start` 绑定首个可用 `on_flip`，调用锁定 p4a 的 `android.loadingscreen.hide_loading_screen()`，
不等待框架 5 秒兜底。系统自身启动动画不等同于应用加载图片。

## Android 装机坑(桌面预览看不到，只在 APK 暴露)
- **中文乱码**：`LabelBase.register(name="Roboto", fn=fonts/NotoSansSC-Medium.otf)` 全局覆盖默认字体；`buildozer.spec` 的 `source.include_patterns` **必须含 `fonts/*.otf`** 否则字体不进 APK。
- **音效卡顿（2026-08-28）**：真机每到 wav 末尾卡一次，且冷启动前几秒断续（MediaPlayer 解码预热）。**两个 bug 叠罗汉**：①`_init_audio_track` 第一行 `from jnius import autoclass, jarray` —— **pyjnius 没有 `jarray`** → ImportError → `except` 吞掉 → 静默回退 Kivy `SoundLoader`；Android 上 Kivy 用 `audio_android`(`MediaPlayer.setLooping`)，**应用层循环不是 gapless**，卡顿周期跟文件时长走。该行自 AudioTrack 方案第一版就在，**硬件循环一次都没跑起来过**，所以"改采样率"和"设备原生率对齐"两轮修复全打在从未执行的代码上。②修掉①之后才露出第二个：写入前用 `getState()==STATE_INITIALIZED` 做校验，而 MODE_STATIC 此时必然是 `STATE_NO_STATIC_DATA`(2) → 照样回退。**修完一层要预期下一层**，别以为一个 bug 就是全部。历史其它坑：Builder 点号 pyjnius 无法解析需用 `$`；三星需 `reloadStaticData()`（且 reload/setPosition 会清 loop 状态，`play()` 里要重新 `setLoopPoints`）；`_active` 必须后置于 play() 成功后；失败需 fallthrough 到 Kivy SoundLoader。
- **新音效无声（打包漏列）**：新增 wav 必须进 `buildozer.spec` 的 `source.include_patterns`（现为 `sand_loop.wav,fonts/*.otf,sounds/*.wav`）。漏列的表现是桌面预览有声、装机无声——桌面验证查不出来。
- **粒子视觉**：千万不要用"直觉"替代 v4 的公式。v4 的流量守恒收缩 + Line 渲染是经过验证的成熟方案。历史上改成扩张(spread)、Ellipse、Rectangle、motion streak 全部失败。**移植 = 照搬 v4 公式 + 坐标翻转 + 参数不变。**
- **配置路径**：Android 用 `App.user_data_dir`，桌面用 `~/.hourglass_config.json`(与 pc 版共享同一文件)。
- **生命周期**：`on_pause` 必须返回 `True` 保持 GL 上下文。
- **周期弹窗闪退**：lambda 闭包延迟绑定在 Android Kivy 2.3.0 上时序敏感 → `mult_btns` / `preview_label` 必须在循环外预创建。

## UI 字号规范(与弹窗对齐)
弹窗和主界面已统一放大，修改字号时保持一致：

| 层级 | 弹窗 | 主界面 |
|---|---|---|
| 标题 | sp(19) | — |
| 倒计时/预览 | sp(18) | sp(24) |
| 主导按钮(确定/开始/重置/周期) | sp(16) | sp(16) |
| 次要按钮(基础周期/倍数/音效) | sp(15)-sp(16) | sp(15) |
| 滑杆 ×N 值标签 | sp(15) | — |
| 标签文字 | sp(15) | — |
| 按钮行高 | dp(40-54) | dp(58) |

## 资源文件
`icon.png`(1024×1024) / `presplash.png`(1080×1920, `#fdf6e3` 底) / `sand_loop.wav`(15s 无缝 PCM 16bit mono **48000Hz**, ~1.4MB) / `sounds/{water,wind}.wav`(14s 无缝循环)、`sounds/clock.wav`(8.62s = 17 个滴答对，48000Hz 16bit mono，`tools/make_clock_loop.py` 从 `mp3/zhongbiao.mp3` **按拍**加工) / `fonts/NotoSansSC-Medium.otf`(~8MB，Apache 2.0 可公开分发) / `ui/slider_track.png`(8×8 纯色 #8a7a68，周期弹窗滑杆未滑段贴图)。修改 `buildozer.spec` 的 `source.include_patterns` 时别漏字体、wav、`sounds/*.wav` 和 `ui/*.png`。
