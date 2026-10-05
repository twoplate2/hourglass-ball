# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🔴 铁律 0：先看 `QA_RULES.md`（2026-10-05 用户质问后立）

**写完 / 改完 / 修完，声明"做完了"时必须同时给出证据等级：**

- **E1 = 有对照的实测**（关掉功能的对照臂 / 已知参考值复现 / 正负对照 / 带标定门槛的量测）
- **E2 = 有实测但无对照**
- **E3 = 只有断言 / 只看了代码**
- **E4 = 事后被推翻**

**没到 E1，不许说"验证通过 / 修好了 / 没问题"。**
报 E2/E3 可以；**不许的是把 E2/E3 说成"验证通过"。**

三条硬要求：① **判据必须先标定**（先拿"已知对"和"已知错"各跑一遍，分不开的门槛等于没有门槛）；
② **"非零"不等于"看得见"**；③ **验"实际画出来的"，不是"我以为的那张图"**。

完整的翻车清单与规律见 **`QA_RULES.md`**（必读，不长）。

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
- 球体积 `v(t)=3t²-2t³`，`_raw_height_ratio` 用数值积分查找表(101 档)反查"体积→高度"。
  ⚠️ **"上沙(1-raw)+下沙(raw)=1 严格守恒" 已于 2026-10-03 作废**(见下方「上沙按恒定流速」)。
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
- 🔴 **容器/遮罩类图元的上沿，必须覆盖"绘制线用到的每一项"**（2026-10-06, 1.129）。
  上球沙体那张 `Rectangle` 只是**容器**（轮廓由 carve 抠出来），它的顶 `up_draw` 原先写的是
  `upper_height + lift` —— **漏了 `rough` 的正峰值**。于是凡 `rough(i) > drop(dx)` 的节点，
  沙面线**连同它那条 3px 亮带**一起露到矩形外面，亮带合成到**玻璃**上 ⇒ 沙面上方浮出一排
  淡色小帽（r27-1号 在设备档6 抓到；颜色实测 (232,211,173) == `sand_light` 与背景的 0.55 混合）。
  `lift` 那一半 **2026-10-05 已经修过一次**，`rough` 是当时漏掉的**第二处**。
  **一般形式：我抬高了 A，那么所有以 A 为下界的 B 都要跟着抬 —— 抬之前先数一遍有几项。**
  镜像方向同样要查：矩形抬高后**不得捅穿 carve 的上沿**（`2Ri + MOUND_CREST_MARGIN`）。
  两条都已进闸门（`upper sand rect covers the drawn surface` /
  `upper sand rect stays under the carve limit`），**负对照都跑过**（分别 19/54 点翻红、每周期翻红）。
  量具：`tools/_probe_rect_vs_surface.py`、`tools/_probe_rect_vs_limit.py`
  （后者在 320×560 / 760×1460 / 1440×2760 三个尺寸上验过 —— `crest ∝ 2Ri` 而 `MOUND_CREST_MARGIN`
  是死像素，**最贴近上沿的状态恰是 crest=0 的满球态**，故余量恒为 2.0px、与尺寸无关）。
- **沙边和玻璃内壁都是 `Ellipse` 圆 → 同一种真圆技术、严格贴合**。这是复刻 pc"玻璃和沙必须同一种技术，否则边缘失配(月牙/缝)"的核心。**绝不用 `Mesh`/多边形拼弓形**(那是 android 旧版渲染 bug 的根源)。
- 粒子用 `Line`(主流) + `Rectangle`(splash/flares/dust)，按颜色排序减少 draw call。
- **流量守恒**(移植自 PC v4)：粒子加速下落时按 A·v=常数横向收缩 `shrink = max(0.70, (60/v_at_y)^0.5)`；颈部 6px 入口区不缩；40px 平滑过渡区从 1.0 渐变到目标值；触底 30px 喇叭口微扩。wobble 随 shrink 同比例衰减(`wobble × (1-shrink×0.4)`)。
  ⚠️ **下限是 0.70，不是 pc v4 的 0.50**（2026-10-03 由外部评审查出）。两处代码一致地写 0.70
  (`main.py:1795` / `tools/flow_numpy.py:83`)，是**本移植有意/无意地偏离了"参数不变"**；
  文档此前一直照抄 pc 的 0.50，属实的写法是 0.70。**要改这个数得两边一起决定**。

### ⚠️ 出口以下的射流包络 —— **已回滚(1.39), 不要再试**(2026-10-03 事故报告)

1.23/1.24 曾给出口以下的横向 clamp 加过一套"物理包络"(`JET_VENA=0.68` 收腰 +
`JET_EDGE=0.30` 沿深度相干摆动, 波长 ~15px), **1.39 已整块删除**。它把出口以下的沙柱做成了
**一串肠节** —— 用户原话"改成什么鬼样子了"。

**它为什么能出货(这段比结论值钱)**: commit `14c9dcf` 写着"本轮 panel 取证 → 定案", 但
**两份 panel 简报对 `vena|收腰` 的 grep 均为 0** —— 它是主持人在 panel 闭幕后自己引入的
(transcript:2761 "我自己……面板双方都没说的判断"), 幅度也是一个人看自己的裁图定的,
**从未进过任何一轮对抗审查**。这是 skill 点名的 **facilitator capture**。

- **验收判据("去趋势残差 0.29→0.99px")对"加抖动"单调递增** —— 加多少涨多少, 永远说不出
  "太多了"。**缺陷和指标是同一个东西**: 他们量出了串珠, 因为数字变大就宣布是修复。
  同节还写着"必须先让它比自然展宽更窄……摆动才有地方显形" —— **"我在优化可见性, 不是
  正确性"** 当时就该触发警报。
- **全程没有一步把改前/改后并排给人看。** 机器一直有(`inspect_flow.py --source` 渲染另一棵树
  + `--dense-neck` 出裁图), 缺的是配对 —— 全仓库改前无任何 `montage/side_by_side/hstack`。
  用户是**出货之后**才被叫去评估的。
- 回滚实测: 与 1.22(`19ff33f`)逐像素 **104 帧 / 0 帧有差异 / 最大通道差 0**; 与 1.24 版
  **88/104 帧有差异, 最大通道差 152**。并排图 `benchmark_logs/_AB_neck_15_750.png`。
- **不要再试"填满喇叭口"整族**(贴壁沙霜/沙雾/颗粒域扩到镜像颈):`_draw_neck_grains` 画的是
  1–2px `Line`,盖不满 86×22px 的面。
- **不要再试"缩窄喇叭口"**:`w_out = min(R*0.45, max(t_out+2, TAPER_K*nw, shoulder*1.06))`,
  实测恒由 `shoulder*1.06` 主导(47.95 vs R*0.45=74.32、TAPER_K*nw=37.40),而 shoulder 只由
  球半径 R 与描边宽 ow 决定 ⇒ **喇叭口宽度被钉死**,缩小会让"扁平肩台+硬折角"重现。
- 残余不可约项:3600s 档(管 7.4px)在 GLES2 无 AA/无 MSAA 下只能是一根诚实的细圆柱。
- **那道"矩形楔形"仍未解决** —— 回滚只是回到用户没骂过的基线。下一轮唯一正确开局:
  先出"改前/改后"并排图交回用户, **判据只有一句 —— 用户说哪个更自然就照哪个**。

### 🔴 三条流程红线(事故报告 §八, 违反过就该停下)

1. **知觉问题不能由像素统计裁决。** 用户说"不自然"是**知觉判断**。
   `NECK_REDESIGN.md:8-10` 把准入证据限定成"代码事实 + 像素数", 等于默认知觉问题可由
   非知觉证据裁决 —— 这个等号**从未被检验**, 之后所有"验证"都只是它的下游。
   **改任何视觉之前, 先把改前/改后的并排图交回用户; 统计量只允许事后解释"为什么",
   不许事后当"好不好"的判据。用户是审美的唯一权威。**
2. **同模型 panel 只能裁"代码事实", 不能裁"口味"。** 代码事实有复现步
   (refute-by-reproduction) ⇒ 真去相关; 口味没有复现步, panel 没有眼 ⇒ 同模型的收敛是
   纯共享盲区。**口味决策交回用户, panel 最多列选项。**
3. **噪声带上的 n=1 比较是非判别性证据。** "同一份代码连跑两次, 5s 平均 61.5→54.5(±12%)"
   是项目自记的。**不要用单轮对比去否定用户的多轮观感 —— 用户的 n 比你大。**
   要比就**交替多轮**(n≥3), 比 **1% low / p90**, 不比平均。

**要真的做版本对比, 用现成的工具, 别手搓**:
`tools/ab_device_benchmark.sh <revA> <revB> [轮数]` + `python tools/analyze_ab.py benchmark_logs/ab`。
一轮约 55 秒(不是几分钟)。它已经内建了六道防线: 每臂推自己那套文件(含 `tools/flow_*.py`)、
**flow_numpy 的有无在宿主机判定**、`MSYS_NO_PATHCONV` + Windows 形式源路径、推送后**逐文件 sha256**、
拉回来的日志过**闸门**(`code_hash` 必须等于本臂 `main.py` 的 sha256 前 12 位 / 渲染器必须是 texture /
三档齐全), 以及超时重试。**主判据是按轮配对差**(A 的第 i 轮 − B 的第 i 轮), 不是"中位差 > 组内极差"
—— 后者在同码组内极差能横跨 0.4~30 FPS 时不可校准。开跑前先 `ps -ef | grep ab_loop` 确认没有
别的进程在驱动同一台设备(踩过: 上一轮遗留的三个后台循环把测量全打乱了)。

### 🔴 索引重建: 每帧 1.5 MiB 顶点被重新提交(2026-10-03 定位并修掉)

**问题**(外部评审指出, 我们核到源码级): `mesh.indices = ...` 的 setter 里 `flag_data_update()`
→ `VertexInstruction.apply()`(每帧都跑) 看到 `GI_NEEDS_UPDATE` 就 `build()`
→ `Mesh.build()` 拿**整个 512 槽顶点数组**去 `VertexBatch.set_data()`
→ `clear_data()` + `add_vertex_data(全部顶点)` + `flags |= V_NEEDUPLOAD`。
**改一次索引 = 整块顶点重走一遍并标脏上传**, 不是"只改个数字"。
(源码 Kivy 2.3.0: `vertex_instructions.pyx:485/460`, `instructions.pyx:429`, `vbo.pyx:170`)

**实测**(桌面 `HG_FLOW_RENDERER=texture` + `inspect_flow.py --steady-period 1 --steady-frames 60`,
即**连续帧**; 「每帧触发几次」与设备无关, 只有代价与设备有关):

| | 索引赋值/帧 | 被重走的顶点表/帧 |
|---|---|---|
| 改前 | **15.98 次** | **1568.5 KiB** |
| 改后 | **2.12 次** | **165.9 KiB** |

⚠️ **它不是"跳变伪影"**: 粒子总数 441→441→443→444→442 几乎不动, 赋值仍是每帧 15~24 次 ——
因为**桶计数每帧抖动 ±1**(粒子随时生灭, 下落途中还在不同颜色桶之间迁移),
`previous != count` 于是每帧都成立。

**改法**(`tools/flow_texture_experiment.py`): 每块的索引**只增不减**; 缩的时候**不动 indices**,
改把用不到的槽位在端点纹理里写成 `x = -1e5`(`PAD_ENDPOINT`)—— shader 里
`position = vec2(x, mix(bottom, top, vTexCoord)) + vPosition`, x 推出去整条线被裁掉,
**出不了任何像素**。暖机后赋值塌到 1~3 次/帧, 代价只有 134 个槽位/帧的裁剪。

**验收**: **43 帧逐像素 0 差异**(最大通道差 0; 密集 1 秒档, 前后两版同 seed 同时刻)。

⚠️ **已知代价(记录在案)**: 某个桶一旦冲高过, 之后一直按历史最高槽位绘制, 浪费上界 =
`历史最高 − 当前`。实测只有 134 槽/帧、可忽略; 但若某桶长期塌得很低, 浪费会变大 ——
那时的解法是给高水位加衰减, 或上原生静态 VBO+IBO 的 draw-count 组件。
⚠️ `part[4]` 的语义已从"当前粒子数"变成"**已提交的索引数**", 只被 `update()` 自己读, 无外部读者。

### 落沙密度 vs 瓶口: **`rate` 的周期依赖是承重的, 不要拉平**(2026-10-03 三臂实测)

用户问过"瓶口随周期变, 沙子的流动表现要不要相同"。**答: 密度该相同, 而现状的 `rate` 随周期降
(1500→300)正是在抵消孔径收窄(22→8px), 使面密度大致持平。** 若把 rate 拉平到 1500,
3600s 档面密度会变成 1s 档的 2.75 倍 —— **那才是不一致**。

- 三臂实验(固定 60s、只在测具里覆写 `speed_factor` ⇒ rate 300/600/1500): 固定窗填充率
  **20.04 / 22.17 / 23.62%**, 帧间 σ 2.3–2.5% ⇒ 5× 率差只推动 **3.58%, 刚过判据**(弱可见)。
- 工具: `tools/inspect_flow.py --steady-period/--steady-frames/--speed-factor/--sand`,
  分析 `tools/neck_density.py`(**固定几何窗 + 最近邻分类 + p5/p95**; 不要再用"自身跨度内的同色比",
  那是自指的, 恒 100%)。
- **不要再试**: ①让 `rate` 绑回孔径(方向同 Beverloo) —— 本项目**已经把那条约束花在
  "孔径↔周期"上**(旧版 `hourglass.py:184` `(60/duration)**0.4` 就是 D∝t^-0.4), 再用一次是**过定**;
  ②把"粒子率阶梯"当缺陷 —— `clamp(60/dur,0.5,2.5)` 在 24s/120s 处**无跳变**, 只是斜率折点。
- 已知真偏差(记录不改): `[120s,∞)` 孔径还在 11→4px 地收而 rate 已触底 300 ⇒ 超长周期略偏密;
  修它要**再减粒子**, 撞「不许减少粒子数量」红线, 且那段无人盯颈部。

### 渲染分层(`redraw()` 中的真实调用顺序)
⚠️ **2026-10-06 重写**：旧表列了 10 条，其中**第 10 条「完成闪烁」早已删除**
（`main.py:980` 自己写着「删掉的东西: 常量 `FLASH_DURATION` / `flash_end` / `_flash_color` /
`_flash_rect`」，闸门里也注了 4 处），而**实际存在的两层却一个都没列**。
照旧表写简报会把手下的专家指到不存在的东西上（**当天就发生过一次**）。
下表按 `redraw()` 的**调用行号**排（`:3896` 起），改绘制顺序时请一并改这里。

1. `_draw_surface_markers()` —— §5 表层滑动标记（上球 8 颗 + 下球 12 颗）
2. `_draw_upper_shape()` —— 上球沙面 carve + 3px 亮带
3. `_draw_mound_shape()` —— 下球沙堆锥面轮廓
4. 颈部沙柱（沿 `_neck_sand_side()` 的 Quad 带；上喇叭口+直筒，下喇叭口敞开给粒子）
5. `_draw_stream()` —— 沙流粒子(按 `_color_table` 排序后 `Line` 渲染)
6. `_draw_neck_grains(side)` —— 颈部颗粒层（128 图元池，把已流出的粒子投影回整条颈部轮廓）
7. splash 反弹粒子(`_sync_rects(_splash_group, …)`，`Rectangle`，`sand_light` 色)
8. 触底闪光 flares(0.08s 寿命，4+ 半透明方块)
9. 完成尘埃 dust(`_sync_rects(_dust_group, …, dp(1.2))`，25 颗，1s 寿命，向上喷射)
10. 颈部孔高光 —— `self._bore_color.a = 1 if remaining <= 0.001 else 0`
    （⚠️ 代码里叫 **`_bore_`**（bore = 玻璃孔），**不叫** `neck_gloss`/`neck_highlight`；
    按中文名或 `neck_gloss` 去 grep 会误判成"这个功能不存在"—— 当天踩过一次）
11. 暂停遮罩 —— `self._pause_color.a = 0.55 if not self.running and 0 < self.elapsed < self.duration else 0`

（完成闪烁 **2026-10-04 已删除**，见上。）

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

### ⚠️ 本机测不出 \`(avg+1%low)/2\` 的小改动: M 跑间极差 ±5~9(2026-10-03)

目标曾改为最大化 **M = (平均帧率 + 1% low帧率)/2**。三轮同机同配置 benchmark 结果:

| 轮次 | 1s M | 5s M | 15s M | 三档均值 |
|---|---|---|---|---|
| 基线 1.28 | 85.7 | 85.6 | 79.6 | 83.6 |
| 改动 #1 | 84.8 | 82.6 | 84.8 | 84.0 |
| 重复 r2 | **67.5** | 78.8 | 81.5 | **76.0** |
| 重复 r3 | 84.3 | 83.1 | 88.7 | 85.4 |
| **中位** | 84.5 | 82.8 | 83.1 | **83.8** |
| **极差** | **18.2** | 6.7 | 9.1 | **9.4** |

**1s 的代码路径四轮完全相同**(n=452 < \`_NUMPY_MIN=800\` 走标量、逐像素已验 0 差异),
它的 M 却在 **67.5–85.7** 之间跳 ⇒ **M 的跑间极差 ±5~9(三档均值 ±9.4),比 avg 的 ±12% 还狠**。
⇒ **单对比较得出的"M +5.2"是假的**(四轮下 15s 极差 9.1 > 5.2)。
**任何小于 ~8 点的 M 改动在本机都不可分辨。**

**决定性的一次: 3 基线 vs 3 改动(受控对比)** —— 两边都跑, 而不是只重复一边:

| 基线 1.28/1.29 | 三档均值 | 改动 1.30 | 三档均值 |
|---|---|---|---|
| B1 | 83.6 | M1 | 84.0 |
| B2 | **89.4** | M2 | **76.0** |
| B3 | 81.5 | M3 | 85.4 |
| **中位 83.6 / 极差 7.8** | | **中位 84.0 / 极差 9.4** | |

中位差 **+0.4**, 而组内极差 **7.8 / 9.4** ⇒ 差值是极差的 **1/20**。
**关键是两边都出现了"坏轮"(基线 B2=89.4、改动 M2=76.0)** ⇒ 噪声是**内在的**,
不是某次改动的伪影 —— 只重复一边得到的"改进"必然是假的。

⇒ **本机验证"提高 M"的正确做法**: 必须**两边各跑 ≥3 轮**, 看中位差是否 > 组内极差。
只跑改动侧 / 只跑一轮 / 只做单对比较, 一律不出结论。成本 ≈ 28 分钟/次对比。

### 1s 档"最慢"是**热身瞬态**, 不是算力(2026-10-03)

> ⚠️ **本节末尾的归因已证伪**: 那段热身的成因不是 vsync, 是 Kivy `Clock.idle()` 的 `usleep`。
> 本节的**全部测量依旧有效**, 只有成因那一句错了 —— 修正见下方「🔴 归因修正」。

1s 的帧时间分布是**双峰**, 5s/15s 都不是:

| 档 | 5% | **25%** | **50%** | 75% | 95% |
|---|---|---|---|---|---|
| **1s** | 6.3 | **7.1** | **12.1** | 12.5 | 13.2 |
| 5s | 6.8 | 7.6 | 8.6 | 10.3 | 12.7 |
| 15s | 6.8 | 7.6 | 8.3 | 9.2 | 12.7 |

按时间切开 1s 的 trace:

| | 帧中位 | 粒子中位 |
|---|---|---|
| 前半 0~0.5s | **12.34ms** | 214 |
| 后半 0.5~1.0s | **7.46ms** | 383 |

⇒ **1s 用例只跑 1 秒, 热身段(约 0.4–0.5s)占了它 40% 的样本**, 于是中位被拉到 12.1ms、
残差(中位 6.36ms)也集中在那里。**15s 有 1705 帧, 同一段热身只占 2.7%, 所以看不出来。**
后半段**粒子更多反而更快**(383 > 214, 7.46 < 12.34) —— 又是那个负相关。

**把两段拆到栏级, 结论是决定性的**:

| | frame | 物理 | 图元 | Canvas | swap | **残差** |
|---|---|---|---|---|---|---|
| 前半 0~0.5s | 12.34 | 1.22 | 1.06 | 1.71 | 0.48 | **7.77** |
| 后半 0.5~1.0s | 7.46 | 2.26 | 1.35 | 2.21 | 0.45 | **0.30** |
| 前/后 | 1.65× | **0.54×** | **0.79×** | **0.78×** | 1.08× | **25.9×** |

**前半段每一栏都比后半低, 而帧时间高 1.65× ⇒ 差额全在残差里(25.9 倍)。**
⇒ **1s 的"慢"100% 是探针外的热身**(⚠️ 括号里那三种猜测 —— 帧派发 / swap 调度 / 管线预热
—— **全错**, 真身是 `Clock.idle()` 的 `usleep`, 见「🔴 归因修正」),
**不是任何一栏的算力** —— 这彻底解释了拆门那轮(物理降 0.85ms 帧反而更差)。
**热身是"每轮都有", 不是"应用首次"** —— 三档各按 t≤0.5s 切:

| 档 | 前 0.5s 残差中位 | 之后 |
|---|---|---|
| 1s(第1个跑) | 8.36 | 0.27 |
| 5s(第2个) | 10.41 | 0.27 |
| 15s(第3个) | 10.78 | 0.27 |

**每轮开跑后约 41 帧(~0.5s)都有 8–11ms 额外残差, 之后一律塌到 0.27ms。**

**⚠️ 但热身只影响 1s 的中位, 不影响 1% low**(已验, 勿重蹈):

| 档 | 最慢 1% 帧里 t≤0.5s 的 | 去掉热身后的 1% low |
|---|---|---|
| 5s | **0/5 = 0%** | 14.09 → **18.27ms(更差)** |
| 15s | **1/17 = 6%** | 13.82 → **17.84ms(更差)** |

⇒ **1% low 的帧撒在全程**(5s: t=1.0–4.0; 15s: t=0.31–14.62), 且去掉热身后分位取到更深、
**反而更差** ⇒ **热身与 1% low 是两条独立的线**, "慢帧=外部停顿"那条结论不受影响。

**已查明(给 benchmark 加了探针时间戳后实测)** —— 那 8–11ms 是 **"在等"**:
(⚠️ 原句在此断言「vsync 阻塞, 不是算力」—— **已证伪**, 见「🔴 归因修正」)

| 档 | 段 | frame | 四栏和 | tick尾 | 残差 |
|---|---|---|---|---|---|
| 1s | 前0.5s | 12.40 | **4.50** | **0.08** | 7.90 |
| 1s | 之后 | 12.14 | 6.25 | 0.08 | 5.89 |
| 5s | 前0.5s | 12.46 | **1.71** | **0.10** | 10.75 |
| 5s | 之后 | 8.38 | **7.61** | 0.08 | 0.77 |
| 15s | 前0.5s | 12.42 | **2.46** | 0.08 | 9.96 |
| 15s | 之后 | 8.34 | **7.77** | 0.08 | 0.57 |

- **`tick` 尾部(redraw 结束→on_draw 开始)= 0.08–0.10ms, 三档两段全同** ⇒ tick 里探针之外的
  代码可忽略 ⇒ **那扇门关上了: 没有可优化的 tick 代码。**
- **热身段残差与"活少"严格对应**: 热身四栏和只有 1.7–4.5ms 而帧 12.4ms; 之后四栏和 7.6–7.8ms
  而帧 8.3ms ⇒ **那 8–11ms 是"在等"而不是"在算"** —— 开局太闲, 时间花在
  **`Clock.idle()` 的 `usleep`** 上(原文写 vsync 阻塞)。
  **这是"太快"不是"太慢"** ✓ —— 但 ⚠️ **"没有代码杠杆"是错的**: 杠杆就是 `maxfps`,
  见「🔴 归因修正」。

⚠️ **Kivy 的调用顺序**: `Window.flip` 在 `Window.on_draw` **之前**(探针名 \`previous_swap_ms\` 即此意)。
按"先 on_draw 后 flip"去算 gap 会得到**负值**(实测 −12.34 ≈ −frame_ms)。

⇒ **结论: M 的两个半轴都没有本机可验证的代码杠杆** —— 1% low = 全程散布的外部停顿;
1s 的 avg = 开局的软件节拍(原文写 vsync 阻塞 —— ⚠️ **错**: 那是 `Clock` 的 `usleep`,
而且**有杠杆**, `maxfps=120` 一次拿掉 +75% 平均 / p90 −5.8ms, 见「🔴 归因修正」);
15s 的 avg = 响应低于 3v3 噪声带(±7.8/9.4)。

### 🔴 归因修正: 那 8–11ms 是 Kivy `Clock` 的 `usleep`, 不是 vsync(2026-10-03)

**证据 1 —— 两臂 A/B, 只动 `maxfps` 这一个变量**(MuMu, n=3 逐轮配对, 全部过 sha256 + code_hash 闸门)。
"残差" = 帧时间 − (物理 + 更新 + Canvas + swap) 四项探针之和:

| 臂 | 前 0.5s 残差中位(1s / 5s / 15s) | 之后(1s / 5s / 15s) |
|---|---|---|
| A `maxfps=60` | **8.94 / 8.94 / 8.94 ms** | 6.11 / 0.29 / 0.28 |
| B `maxfps=120` | **0.49 / 0.49 / 0.49 ms** | 0.24 / 0.26 / 0.26 |

两臂之间**只有 `maxfps` 变了**, 8.94ms 就整条消失 —— 而它在 A 臂**三档同值(8.94/8.94/8.94)**,
确定性节拍器才有这种指纹; 调度抖动 / vsync / 预热都不可能三档一模一样。

**证据 2 —— 480Hz 直接证伪 vsync**: 模拟器报 **480Hz**, vblank 只有 **2.08ms**,
**物理上阻塞不出 8.94ms**。这条数据一直都在, 早该拿它否掉那个归因。

**机制**(`kivy/clock.py:723` `_check_ready`)。⚠️ **`resolution` 不是常量**: 实测
`get_resolution() = 1/(3*fps)` —— maxfps=60 时是 1/180=5.556ms, 120 时是 1/360=2.778ms:

```
done = (sleeptime - 4/5*min_sleep <= min_sleep),   sleeptime = 1/fps - 本帧已耗时
⇒ 不睡的判据化简到底就是   W >= 0.4/fps
⇒ 不睡不成时的帧间隔 = (11/15)/fps = 0.7333/fps
```

| maxfps | 悬崖(耗时 ≥ 才不睡) | 台阶(帧间隔) | 逐帧直方图实测台阶位置 |
|---|---|---|---|
| 60 | 6.667ms | **12.222ms** | 12.1–12.6(A 臂) |
| **120** | 3.333ms | **6.111ms** | 6.0–6.3(B 臂) |
| 165 | 2.424ms | 4.444ms | — |
| **0(不限)** | — | **`idle()` 整段跳过, 完全不睡** | — |

**这是一道悬崖, 不是斜坡: 算得越快, 被顶得越死。**
⇒ 开局粒子少 ⇒ W≈3.3ms ⇒ 被睡到 12.222 ⇒ 残差 8.94 ✓ 与证据 1 吻合;
0.5s 后粒子涨起来、W>6.667ms ⇒ 不睡了 ⇒ 残差塌到 0.28 ✓。**一个机制, 两个症状。**

⚠️ **`maxfps=120` 只是把台阶挪近, 没有取消它** —— B 臂 1s 的最高分箱恰好聚在
6.1–6.3ms(49/39/25/21 帧), 那就是 6.111ms 台阶。**只有 `maxfps=0` 才是真的不睡**
(`idle()` 里 `if fps > 0:` 整段跳过)。**别写成"≥100 都一样", 那是错的**(本项目自己写错过一次):
- 120Hz 面板(vblank 8.333ms): 6.111 落在 vblank 之内 ⇒ 仍被量化到 120fps, **所以真机看不出差别**;
- **165Hz 面板(vblank 6.06ms): 6.111 > 6.06 ⇒ 掉一整帧 ⇒ 82.5fps** —— 这才是必须上 0 的理由。

**逐帧直方图佐证**(同一批日志, 不用新跑):

| | 中位帧 | 落在 12.2ms 台阶上的帧 |
|---|---|---|
| **1s** A(60) → B(120) | 12.35 → **5.95ms** | **50.7% → 0.0%** |
| 5s | 8.88 → 8.40ms | 6.3% → 0.7% |
| 15s | 8.10 → 7.93ms | 4.6% → 0.4% |

**15s 为什么几乎不动**: 它的工作量(≈7.6ms)**本就在悬崖之上**, 中位帧从来没被睡过 ——
台阶只占它 4.6% 的帧, 所以中位不动, 只有 p90(12.36→9.62ms)和均值(114.0→126.6)动。

**A/B 结果**(n=3 逐轮配对, 每个指标 3/3 同号):

| 档 | 平均 | p90 | 1% low |
|---|---|---|---|
| 1s | 96.7 → **173.4** | 12.93 → **7.12ms** | 64.2 → **111.0** |
| 5s | 108.4 → **141.0** | 12.50 → **9.13ms** | 70.7 → 80.4(在噪声内) |
| 15s | 114.0 → **126.6** | 12.36 → **9.62ms** | 72.7 → 79.4 |

**这条同时是本项目"短周期优化不动"的总解释**: `maxfps=60` 下 1s 档一半的帧被钉在 12.222ms
—— **把物理砍到 0 也还是 12.222**。既往所有短周期优化都打在这堵墙上。

**遗留未解(别当已解决)**: 更早那次诊断里 `Config.set('graphics','vsync','0')` 让空闲态的
`on_flip返回 → 下帧tick` 间隙从 11.98ms 塌到 0.74ms。按新解释那个间隙**就是** clock 的 `usleep`,
关 vsync 不该动它 —— 这两条**还没对上**, 重测之前不要引用那次对比。

**生产已采纳**: 1.40 起 `main.py` 顶部、`import kivy.app` **之前**, **仅 Android**
(按 `P4A_BOOTSTRAP` / `ANDROID_ARGUMENT` 环境变量判定)设 `Config.set('graphics','maxfps','0')`
—— 彻底不休眠, 节拍交给 vsync(安卓的 buffer swap 必然等 SurfaceFlinger 的 vblank, 不会空转)。
**桌面不设**: 没有 vblank 兜底, 设 0 会纯烧 CPU, 也会改掉测量工具的节拍。
(1.39 那版设的是 `120`, 已作废 —— 见上表。)

⚠️ **`maxfps=0` 只是"我们自己不设上限", 它不等于"用屏幕最高刷新率"**(2026-10-03 补)。
安卓**不会**自动把面板跑到最高档 —— 不向系统要 `preferredRefreshRate`, 它就按自己的默认档
(常见 60/120)走, 哪怕面板是 165/185。所以要两件事一起做:
`HourglassApp._apply_max_refresh_rate()` 读 `Display.getSupportedModes()` 取最高档写进
`WindowManager.LayoutParams`(API 23+), `on_start` 调一次 + 1.5s 补一次(SDL 挂稳窗口后会再刷属性,
幂等), **`on_resume` 也要再要一次**(同 `_apply_orientation` 的道理)。
⇒ 实际帧率 = `min(系统给的档位, 1/单帧耗时)` —— **两个因子都要满足**, 缺一个都上不去。
日志里的 `refresh_hz`(基准开始时读)是唯一的判据; 日志里的四栏和就是"1/单帧耗时"那一半。
⚠️ 代价: 轻帧不再睡 ⇒ 整段计时按屏幕刷新率渲染, 比原来费电; 收益只在 **>60Hz 的面板**上看得见
—— 60Hz 屏上两种设置看到的都是 60fps, 那时它是纯亏。

**真机实测(2026-10-03, 1.39/maxfps=120, 120Hz 面板, 骁龙 8 Elite Gen 5)**: 5s 平均 **120.5** /
1% low 88.3; 15s 平均 **120.3** / 1% low 90.5; 粒子峰值 **2829**
⇒ **顶到面板刷新率, app 不再是瓶颈**。⚠️ **165Hz 那台还没测** —— 按上表, 120 那版在它上面
会因 6.111ms 台阶掉帧, 这正是改成 0 的原因。

### 15s 的慢帧不是算力: 慢帧粒子数**更少**、各栏等比上涨(2026-10-03)

逐帧 trace(\`frame_benchmark.py:251-257\` 每帧都写 \`frame_ms,physics_ms,update_draw_ms,
canvas_ms,previous_swap_ms,particles,splashes,gc_ms,gc_generation,mound_px\`)—— **不用新跑**:

| 15s 最慢 23 帧(1.3%) | 中位 | 倍数 |
|---|---|---|
| frame_ms | 16.93 | 8.30 | 2.04× |
| **粒子数** | 1822 | 1997 | **0.91×** ← 不增反减 |
| 残差 | 0.74ms | 0.27ms | 仅 +0.47ms |
| 图元 / Canvas / 物理 | 4.76 / 4.55 / 2.07 | 2.65 / 2.99 / — | 1.80× / 1.52× / 1.30× |
| GC | 最大 0.267ms | | 可忽略 |

**"种群峰"假设(H1)被证伪**: 慢帧粒子数是中位的 0.91 倍。慢帧是**每一栏等比变慢而活没多**
⇒ 签名是**外部停顿**(分配 / 模拟器宿主调度 / driver), **不是任何一栏算法的代价**。
⇒ **优化任何一栏的算法, 对 1% low 的收益≈0** —— 这也解释了账本里"飞溅池消融纹丝不动"。
⇒ **要提高 M 只能打 avg 半轴**(降各栏中位数), 而 1s 的 avg 被 `Clock` 的休眠吸收(残差 4.51ms 且
与工作量负相关 r=−0.597; 原文写 vsync 余量 —— ⚠️ **错**: 这条负相关恰是休眠的指纹,
活越少睡得越多, 而 `maxfps=120` 能整条拿掉它, 见「🔴 归因修正」)。

### ⚠️ 函数级微基准会骗人: 它看不见"每帧新建临时对象"的代价(2026-10-03)

`_NUMPY_MIN = 800` 一个常数**同时**开着物理 numpy 路径(1667)与渲染器向量化(1439/2144/2262)。
1s 档峰值 452 < 800 ⇒ **1s 全程走标量**, 且连"颈部颗粒第一趟向量化"那次收益也一次没吃到。
看上去该拆门。**函数级微基准也支持拆**: 预分配数组上量 `flow_numpy.step`, n=452 只要
**67.8µs**, 而设备 trace 回归出的标量路径是 **2.59µs/颗** ⇒ 452 颗要 1170µs,
**便宜 17 倍**; n=50 时向量化也只要 44.7µs(仍赢)。

**设备实测(1.28 拆门 A/B)**: 物理栏**确实**从 1.75ms 降到 **0.90ms**(−0.85ms, 与预测的 1.1ms 吻合)……
**但指标崩了**:

| 档 | (avg+1%low)/2 | p99 帧时间 | 阶段残差 |
|---|---|---|---|
| 1s | 85.7 → **64.9** (−20.8) | 13.53 → **20.05ms** | 4.51 → **6.64ms** |
| 5s | 85.6 → **77.3** (−8.2) | 14.30 → **17.98ms** | 1.54 → 1.49 |
| 15s | 79.6 → 80.3 (+0.8, 噪声内) | 17.24 → 14.99ms | — |

**根因**: 微基准在**预先分配好的数组**上量函数 ⇒ 只看见省下的, **看不见"真实循环里每帧新建
numpy 临时数组"那笔钱** —— 而它落在探针外, 并把尾巴打坏(残差涨得比省下的还多)。
`_NUMPY_MIN=800` **本来就不是按每颗成本定的**, 是按这段分配/尾巴成本定的
(注释里"1 秒档约 278 颗时打包+分组反而慢 0.92ms"是同一件事的另一面)。

⇒ **已回滚**。**教训**: 判"低 n 该不该向量化"**不能用函数级微基准** —— 要么量**整帧**,
要么量**分配行为**(`tracemalloc` / GC 计数)。函数级只适合回答"同一路径内哪段更贵"。

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
  ⚠️ **`1% low` 的尾部帧数有下限 `TAIL_MIN_FRAMES = 5`**(2026-10-03 改): 原来是
  `max(1, ceil(n*0.01))`, 而 1 秒档只有 ~110 帧 ⇒ 只平均 **2 帧** ⇒ 这个数**退化成"最慢那一帧"**
  (用户实测: 1% low 95.6 而最慢帧 93.6, 几乎是同一个数), 还跟上面那行「最慢 5 帧」重复显示。
  现在至少平均 5 帧, 并把**实际用了几帧**打在界面上(`1% low 100.5 FPS(最差 5 帧)`)。
  ⇒ **1.48 之前和之后的 `1% low` 不可直接比大小**; 跨版本比就比 p90/p99 或重跑。
  ⚠️ **FPS 图 Y 轴也修了**(1.49): 老代码刻度写死 0/30/60/90 且把曲线 **`min(90, 1000/frame_ms)`**
  **夹在 90** ⇒ 120fps+ 的机器上整条绿线**平贴在顶部, 零信息**。现在按本帧数据走
  **`frame_benchmark.nice_axis_ceiling()`**(Heckbert 的 nice numbers: 步长取 `value/ticks`
  的 1/2/5/10 × 10^k 邻值, 上界抬到步长整数倍 ⇒ 刻度总是好读的数, 条数 3~8 浮动), 4 条刻度自动标数。
  ⚠️ **2026-10-06 更正**: 这里原写成 `FPS_AXIS_LADDER`(全是 4 的倍数) —— **那个常量不存在**
  (全仓库只在文档里出现过), 是"按 4 的倍数查表"那版的旧名。按它 grep 会一无所获。
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
- `SOUND_EFFECTS` 表（名字与 pc v4 **逐字一致**，共享配置文件）4 种：沙沙声（`sand_loop.wav`，合成保留）/ 水流声 / 风声 / 钟表声（`sounds/{water,wind,clock}.wav`，48000Hz 16bit mono 无缝循环；water/wind 14s，clock 8.112s）。后 3 个为**实录**，源 MP3 仅本机不入库（`.gitignore` 已含 `mp3/`）。
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
  暖白底 + 金色大字 + 暗红「确定」。Benchmark 期间不弹。
- 文案(用户 2026-10-03 定):标题「计时完成」、副标题**「沙漏计时已完成」**、
  大字**「用时：X小时Y分Z秒」**——`_fmt_duration_cn` 零分量省略**且数字与单位之间不留空格**
  (只有 5 秒就是「用时：5秒」,没有小时就不出现小时)。按钮「确定」。
- ⚠️ 大字高度绑了 `texture_size`,弹窗高度 `dp(330)`:加了「用时：」前缀后,长周期
  (如「用时：100小时59分59秒」)在窄屏上会折行,写死 `dp(52)` 会被裁掉。
- ⚠️ **两条门槛(用户 2026-10-03 定)**:① 弹窗**延后 `COMPLETION_POPUP_DELAY = 1.0s`**
  —— 不在流尽那一刻弹,让闪光/尘埃先演完;② **周期 < `COMPLETION_POPUP_MIN = 20s` 根本不弹**,
  但**完成音照旧**(门槛只管弹窗,`_play_completion_sound` 不受影响)。
- 判定在 `HourglassWidget._schedule_completion_popup()` + `App.completion_popup_allowed()`。
  延迟期间可能被 reset / 重开作废 ⇒ 到点时用 **`_completion_token`** 判定
  (`_reset_run_state` 每次 +1),**不能只看 `running`** —— 重置之后 running 也是 False。
- 烟测 `tools/verify_completion_popup.py` 两段都验:①用**生产常数**跑 2 秒档,断言到点**不弹**;
  ②把门槛/延迟覆写成 0 让它弹出来截图。⚠️ Kivy 的 `Window.screenshot` 会把文件存成
  `completion_popup0001.png`(带序号),别按 `completion_popup.png` 去找。
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
- **流量守恒**(原样移植 v4)：`shrink = max(0.70, (60/v_at_y)^0.5)`，6px 入口不缩，40px 平滑过渡，触底 30px 喇叭口（⚠️ 下限与 pc v4 的 0.50 不同，见上文）
- wobble 随 shrink 衰减：`wobble × (1 - shrink × 0.4)`
- 渲染：`Line` + 速度拖尾 `trail = max(2.0, abs(vy)*0.08)`，`width=p["size"]`(85%为2,15%为1)
- **不要改成扩张(spread)/Ellipse/Rectangle**：v4 的收缩+Line 方案已经过验证，改形状或改物理都只会让效果变差
- 触底事件：EMA 更新 `mound_peak_offset` + 25% 概率 spawn flare + 50% 概率 spawn splash
- splash 反弹粒子：向上反弹 vy=-110~-55，实心方块渲染，受 `_sand_half_w` 横向约束
- 完成尘埃：漏完时 spawn 25 颗，1s 寿命，向上喷射

### 新沙流衔接与颗粒表现

当前 Android 版的视觉基准在本工程验证，不再要求沙流像素与旧 PC 完全一致；玻璃与真圆沙体仍须保持原轮廓。
- `_effective_fallen()` 按 `duration - fall_delay` 归一化，**只给下沙堆用**；上沙走自己的时钟，见下节。

### 上沙按恒定流速，不跟下沙堆挂钩（2026-10-03 修 bug2）

**旧实现**：`redraw()` 里 `upper_height = 满 − 下沙堆高度`（一行）。后果 —— 下沙堆在
`elapsed < _fall_delay` 时恒返回 0 ⇒ **上球一动不动**：

| 周期 | 冻结时长（`_fall_delay`） | 占整个计时 |
|---|---|---|
| **1s** | 0.45s | **45%** |
| **5s** | 1.35s | **27%** |
| 15s | 1.35s | 9% |
| 60s | 1.35s | 2.3% |

实测（5s 档，`tools/inspect_flow.py --steady-period 5`）：上球沙面 y 在 0.50s→1.01s
**位移 0 像素**；逐像素比上半球只有 **159 px** 在变（就是下落的粒子串），沙面零变化。
**周期越短越荒谬** —— 1 秒档将近一半时间是静止的。

**新实现**：`_upper_sand_height_px()` = `_raw_height_ratio(1 − elapsed/duration) × 2·R_inner`
—— **唯一假设是体积流速恒定**。下沙堆仍走 `_effective_fallen`（等粒子真的飞到底），
两者之差 = **还在空中的沙** = `_fall_delay / duration`，**这是真实存在的在途沙，不是误差，不许再去消**。
改后同一区间沙面下沉 25px；两条曲线在末帧重新合拢（4.60s：改前 340px vs 改后 348px）⇒ **无末帧跳变**。

⚠️ **代价**：短周期会看到"上球先空、下球后堆" —— 5s 档头 1.35 秒下球是空的，差额 27%。
要缩小它只有一条路：**压缩粒子飞行时间**（`_particle_motion_scale` 已有机制，但会把沙流变快）。
**那是观感决定，归用户**，不许拿统计量裁决。
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
`icon.png`(1024×1024) / `presplash.png`(1080×1920, `#fdf6e3` 底) / `sand_loop.wav`(15s 无缝 PCM 16bit mono **48000Hz**, ~1.4MB) / `sounds/{water,wind}.wav`(14s 无缝循环)、`sounds/clock.wav`(8.112s = 17 个滴答对，48000Hz 16bit mono，`tools/make_clock_loop.py` 从 `mp3/zhongbiao.mp3` **按拍**加工) / `fonts/NotoSansSC-Medium.otf`(~8MB，Apache 2.0 可公开分发) / `ui/slider_track.png`(8×8 纯色 #8a7a68，周期弹窗滑杆未滑段贴图)。修改 `buildozer.spec` 的 `source.include_patterns` 时别漏字体、wav、`sounds/*.wav` 和 `ui/*.png`。

## 🔴 铁律：push 完必须挂 CI 监视，不许等人去看（2026-10-06 用户定）

用户原话：

> 「你刚 push 之后再等 5 分钟，5 分钟之后你再去看看上一个 push 有没有成功、有没有失败。
>  如果还没成功，再等 5 分钟，发现有错误你就去通告主线程。**不要让我来搞。**」

**为什么立的**：1.111~1.114 **四次构建全失败**，是用户自己起床后发现的。

**关键认知：本地闸门绿 ≠ APK 能构建出来。** `verify_hourglass.py` 验的是**桌面逻辑**，
构建是另一条独立的路（buildozer + p4a + gradle）。「闸门全绿」不能当作"推送没问题"的依据。

**做法**（两步，**顺序不能反**）：

```
BASE=$(bash tools/_watch_build.sh --baseline)   # ① **推送前**记下当前最新 run 号
git push origin main
# ② 推送后立刻用 Monitor 挂上（它出结论会自动回到主线程，失败就报、成功也报一声，不用人问）
bash tools/_watch_build.sh $BASE                # 5 分钟一轮，出结论时打一行并退出
```

⚠️ **必须给基线**。脚本只认 **`基线+1` 那一个 run 号** —— 那才是"我这一次"。
不给基线（`TARGET=1`）匹配不到任何东西。

⚠️ **这条脚本上已经栽过两次，两次都是"看着有结论、其实答错了题"**（崩了反而好发现）：
1. 抓"列表里最新一条**已完成**的" ⇒ 刚推完就抓到**上一次** push 的结果；
2. 抓"**比基线大的**最新一条" ⇒ 5 分钟窗口里若跑完了两次，会**跳过中间那条**（可能是红的）。
⇒ 判"是不是我这次的"要用**确定的号**，不能用"最新"。

脚本走 **actions 列表页的 HTML**（`aria-label` 里就写着结论），**不要走 `api.github.com`**
—— 未认证 60 次/小时，实测这个 IP 直接 403，脚本会**静默空转**。
