# 跳跳的沙漏 — Android (Kivy) 打包工程

把 **pc 版球形沙漏**(`../hourglass_v4.py`，tkinter + PIL 真圆 + 完整球 + 球体积微积分)
重写为 Kivy，并用 **GitHub Actions 云端构建** 成 Android APK。全程不需要本地装
Android SDK / NDK / Buildozer，Windows 也能用。

> 本目录 `main.py` 是从 `pc/hourglass_v4.py` **从零重写** 的 Kivy 版(公式/参数以 v4 为基准)，不复用 `../../android/main.py`。

## 桌面预览

```powershell
pip install kivy
cd E:\AI_Tools\other\shalou_claude\pc\apk
python main.py
```

非 Android 平台默认窗口 400×800(模拟手机竖屏)。点沙漏两个球 = 开始/暂停。

启动不再展示自定义加载图：使用与主界面同色的单像素占位，首个可用画面后立即移除原生遮罩。
初始化耗时仍然存在；Android 系统自身的启动动画由系统控制。

## 计时完成语音

结束时播放一小段柔和提示音，再由微软晓晓 `zh-CN-XiaoxiaoNeural` 说一次「沙漏计时完成」，
不弹确认框。整段声音已预录到 `sounds/completion.wav`，无需联网或手机系统 TTS。
背景音效选「无声音」不影响完成播报；重置或开始新一轮会停止上一轮的播报。
Benchmark 自动禁用完成语音并在结束后恢复，避免连续短周期打断播报或影响测量。
开发机重新生成：`python tools/generate_completion_voice.py`，只需开发环境的 `edge-tts` / `miniaudio`。

## 帧率与 Benchmark

当前版本由 `app_version.py` 的 `APP_VERSION` 统一提供：APK 元数据、Benchmark 标题与复制报告一致。
每次 push 将补丁版本加 1；提交标题以新版本号开头，标题和说明使用中文，例如 `0.1.1 修复沙流衔接`。
本仓库通过 `git config core.hooksPath .githooks` 启用 push 前检查，GitHub Actions 也校验版本与标题。

长按底部音效按钮与开始按钮之间的空白区域 3 秒，打开 Benchmark。
点击「开始测试」，依次完整运行 1、5、15 秒周期；测试过程中「开始」按钮变为「取消」。
结束后弹窗显示各周期的平均 FPS、1% low 和最慢 5 帧各自的 FPS。
结果页同时显示帧率曲线，点击「复制结果」复制完整统计；每轮结果和逐帧耗时自动写入 `benchmark_logs/*.txt`。
复制报告 v2 还包含物理/图元/Canvas/Swap 耗时、并发粒子峰值、GC 和最慢帧明细，
以及代码指纹、屏幕尺寸；Android 尽力读取机型、刷新率、省电与热状态，读取失败会标记未知。
这些环境信息仅在测试开始和周期结束读取，不增加每帧系统查询，不改变 FPS 计算方式。
Windows 日志在本工程目录下，Android 日志在应用私有数据目录下。曲线纵轴为 0–90 FPS，
超过 90 的瞬时值只在曲线中顶格显示，统计与原始日志不裁剪。
测试不会保存配置，结束、取消或切到后台都会恢复原周期、进度、动画和音效状态。

- 平均 FPS = 帧数 / 帧间隔总和。
- 1% low = 最慢 `ceil(帧数 × 1%)` 帧的平均耗时的倒数；1 秒样本较短，通常仅取 1 帧。
- 最慢 5 帧逐项按 `1 / 帧耗时` 列出，由慢到快排列。
- 从 Kivy `Window.on_flip` 采样，包含起步、物理更新和渲染提交的间隔；不统计弹窗退场和周期准备时间。
  这是应用侧提交帧间隔，不等同于 Android 合成器的实际显示时间；请在目标手机上实测。

2026-10-01 的优化保留 `Ellipse/Stencil/Line` 画法、沙粒密度、线宽、拖尾和特效：
固定绘制指令只在几何改变时重建，粒子/飞溅/闪光复用图元，同色同线宽共享颜色指令。
起步先注满沙柱再从出口发射，出生时间分散到帧内，恒加速度积分不再依赖帧长；暂停不再推进粒子。
Windows 使用 `perf_counter()` 高精度时钟，避免 Python 3.11 `GetTickCount64` 的 15.625ms 量化；
动画每个 Kivy 帧更新一次，绘制分组复用粒子引用，减少临时坐标/特效列表分配，GC 保持启用。
按飞行时间预留流动图元，重置/首次开始时清理旧场景的循环引用，把大分配和全量回收放在流动之前。

开发验证：`python tools/verify_hourglass.py`，快速回归加 `--quick`，横屏加 `--landscape`。
无配置读写的帧率对比：`python tools/profile_frames.py --seconds 20 --duration 30`。
自动跑三周期并逐轮保存日志：`python tools/verify_hourglass.py --benchmark-only --rounds 2`。
加 `--capture` 会记录每个周期最慢 3 帧的画面状态，结束采样后重建并截图，不在测量期间抓屏。
状态快照本身仍有复制开销，`--capture` 只用于定位画面，不用于比较优化成绩；评分应另跑不带它的测试。
后台视频压缩等负载会影响 Windows 和模拟器测量，不能据此承诺真机最低帧率。

排查依据：[Kivy Clock](https://kivy.org/doc/stable/api-kivy.clock.html)、
[Line.points 性能说明](https://kivy.org/doc/stable/api-kivy.graphics.vertex_instructions.html#kivy.graphics.vertex_instructions.Line.points)、
[Python 高精度计时器](https://docs.python.org/3.11/library/time.html#time.perf_counter)。

### 沙流合批研究

参考 [Kivy 粒子 Mesh 案例](https://stackoverflow.com/questions/55587933/kivy-custom-shaders-touch-events)
与 [Mesh 连续缓冲区文档](https://kivy.org/doc/stable/api-kivy.graphics.vertex_instructions.html#kivy.graphics.vertex_instructions.Mesh)，
增加了仅用于开发测试的同色线段合批实验，保留原圆头、线宽与拖尾，玻璃/沙体仍是 Ellipse/Stencil。
`python tools/verify_hourglass.py --benchmark-only --compare-flow --rounds 4`
会在同一窗口中交替测试两轮图元池、两轮合批，并记录 renderer 名称。
本次 Windows 试验中，合批降低了 Canvas 提交耗时，但增加了 Python 顶点更新耗时，1% low 没有稳定改善。
因此正式运行保持原图元池方案，不默认开启合批；实验入口为 `--batch-flow`。

PC 开发实验另有两项，均不改变正式渲染：

- `--chunk-flow` / `--compare-chunks`：预留 Line 按 64 个分块，仅挂载当前使用的块，沙粒数量与画法不变。
- `--gpu-flow` / `--compare-gpu`：保留原圆头顶点与拖尾，将宽沙粒的顶点平移交给 GLSL；窄沙粒保留线段合批。

交替复测：`python tools/verify_hourglass.py --benchmark-only --compare-gpu --rounds 4 --no-vsync`。
`--no-vsync` 仅用于该测试进程，正式 VSync 不变；不要与 `--capture` 的成绩混比。
视觉对照：`python tools/inspect_flow.py --label gpu --gpu-flow --pixels 400,800`。
2026-10-02 的 PC 四轮测试中，分块池未显示稳定的 15 秒 low 帧收益；
GPU 方案降低了 Canvas 耗时，但增加了 Python 图元更新耗时，尚未证明整体 low 帧改善，因此两者都不默认启用。
结果以本地 `benchmark_logs` 的完整记录为准，不据此推断真机性能。
GPU 实验按原 Line 池的容量估计预留宽粒子顶点缓冲区，避免起步逐级扩容；超过估计仍可增长，不限制沙粒。
`--uncapped` 只用于独立 PC 成本诊断，不改正式帧率上限，不能把该成绩当作正常运行 FPS。
取消或未完成指定轮数的开发 Benchmark 会返回失败，不把部分结果记为通过。

0.1.2 增加独立端点纹理实验：`--texture-flow` / `--compare-texture`。
固定圆头顶点复用，每帧只上传端点；GPU 从 RGBA8 纹理复原 float32 坐标，不减少颗粒或改变颜色顺序。
`python tools/verify_hourglass.py --benchmark-only --compare-texture --rounds 4 --pixels 1080,2400`
可在固定高分辨率下交替比较。当前 PC 测试减少了绘制工作量，但正常配置的 low 帧尚未稳定显著提高，
因此正式渲染仍保持 Line，不把该实验默认为最终修复。
`python tools/inspect_flow.py --label endpoint --texture-flow --pixels 1080,2400 --dense-neck`
增加起步密集截图；本次 52 张图中 50 张逐像素一致，另外两张仅共 7 个边缘像素有差异。
横屏截图可加 `--landscape --pixels 2400,1080`，裁切按应用旋转矩阵定位颈部。

### 沙流视觉与衔接

- 亮色颗粒后置绘制，避免被主体盖住；新生颗粒使用 18–32ms 的不同拖尾，保留同样的生成速率和 Line 画法。
- 碰撞面直接使用可见沙面，不再使用未绘制的堆尖；碰撞时刻在当前帧内求解，飞溅从实际接触点生成。
- 反弹速度随入射速度变化，能量衰减；接触闪光为短水平细粒，不再是较大的方形光块。
- 延迟后的体积进度在剩余计时窗口内归一化，上下可见高度互补，不在结束帧突然强制补满。
- 极短周期对粒子飞行作缩时播放，使首批沙粒在开始堆积前触底；普通周期仍使用原始 450 重力和原始初速度。
- 玻璃、球体积反函数、真圆裁切、沙色和粒子生成速率不变。
- 直管内投影已有颗粒，优先覆盖出口；最多复用 128 个图元，不增加物理粒子。

2026-10-02：直管高光改为与沙柱底色预混合的实色，保留平滑渐入、位置和线宽，
避免 Kivy 对半透明宽 Line 的额外 Stencil 绘制。未激活的暂停/完成全屏遮罩使用零尺寸，
激活时恢复原尺寸和透明度；已隐藏的闪光不再重复改写。沙粒数量、物理和真圆裁切不变。
Windows 的负载、窗口尺寸与驱动等待仍会造成长帧，桌面结果不能代替 K90 真机验证。

0.1.1：真实沙粒拖尾不再按出口水平线截断，可延伸到直管内；普通沙粒借用已有相位产生轻微、稳定的色差，
复用原颜色分组，不新增随机调用或物理粒子。图元预留按色差后的分布估计，避免新增视觉细节触发起步扩容。
直管末段以 1×64 RGBA 纹理由 GPU 平滑采样，柱体在短距离内过渡为颗粒材质，不再只靠出口高光掩盖断层。
纹理一次创建、支持 GL 上下文重载；起步渐变不逐帧生成纹理，重置后图元归零隐藏。
物理循环仅把帧内常量和重复访问提到循环外，不改公式。
无窗口状态对照：`python tools/verify_particle_hot_loop.py --before benchmark_logs/particle_hot_loop_before.py`，
检查逐帧全部粒子/飞溅/闪光状态相同；其耗时只涵盖更新函数，不是 FPS。

视觉检查：`python tools/inspect_flow.py --label current`，输出到 `benchmark_logs/flow_visual_current/`。
该工具使用独立模拟时钟，只检查画面与几何，不用于评分 FPS。

## 云端构建 APK

1. 本目录单独建一个 git 仓库并 push 到 GitHub(见下方"推送")
2. push 到 `main` 自动触发 `.github/workflows/build-apk.yml`，约 15-20 分钟产出 debug APK
   (后续命中 `~/.buildozer` 缓存约 5-8 分钟)；也可在 Actions 页手动 `workflow_dispatch`
3. 进 GitHub → Actions → 最近一次成功 run → 底部 **Artifacts** → 下载 `hourglass-apk.zip`
4. 解压得 `.apk`，传手机安装(需开"未知来源"权限)

### 推送(每个项目单独 repo)

```powershell
cd E:\AI_Tools\other\shalou_claude\pc\apk
git init -b main
git add .
git commit -m "球形沙漏 Kivy 版 Android 工程"
# 在 https://github.com/new 建空仓库(不勾 README/.gitignore/license)
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

## 关键技术点(踩坑后的最稳组合)

```
Kivy 2.3.0 + python-for-android v2024.01.21 + buildozer 1.5.0 + cython<3.0 + host Python 3.10
```

- **`buildozer.spec` 里 `p4a.branch = v2024.01.21`** —— 没有这行必失败(2026 年新版 p4a
  默认下 Python 3.14 alpha，与 Kivy 2.3 的 C API 不兼容)
- **音效**：Android 用 `AudioTrack MODE_STATIC` + `setLoopPoints()` 硬件循环，声卡 DSP 自己回绕指针，绝对 0 缝隙，不受主线程卡顿影响；Windows 用 `winsound.SND_LOOP` 驱动层循环；其他桌面 fallback Kivy `SoundLoader`。主界面音效按钮显示当前音效名（沙沙/水流/风/钟表 + 无声音 5 种），弹窗点击即切换（弹窗不关、高亮跟随，可连续试听），底部「确定」关闭
- **渲染**：玻璃壳和沙体都用 Kivy 真圆 —— `Ellipse` 画球 + `Stencil` 裁出弓形沙面，
  复刻 pc"玻璃和沙必须同一种真圆技术、否则边缘失配"的核心原则，**不用 Mesh/多边形拼弓形**
- **球↔管曲线过渡**：球拿极点接管子会摊出 `√(2R·ow)≈44px` 的扁平肩台 + 近 90° 硬折角(像塞了块矩形垫块)。
  改成"球壁 →(相切)二次贝塞尔 →(相切)短直筒"，两端 C1 连续；过渡起点必须宽过肩台，否则细颈时肩台残留。
  颈管高度 3%→5.5% 腾空间，沙柱复用同一条内轮廓曲线。沙柱只填到直筒下端(下喇叭口敞开，
  沙从孔口流出与粒子接上)，并在 0.25s 内从上往下注满，避免起跑瞬间从空变满
- **中文字体**：`fonts/NotoSansSC-Medium.otf` + `LabelBase.register(name="Roboto", ...)`
  全局覆盖，否则装机后汉字全是豆腐块
- **弹窗配色**：`_SandBgPopup` 双层兜底（Popup 本体 `canvas.before` + 内部 `_container` `canvas.before`），覆盖 Kivy 默认深灰；按钮用暖金/沙色系独立配色（`POPUP_*` 常量），不随沙色变化
- **周期设置**：基础时间(1秒/10秒/1分钟/10分钟) × 倍数按钮(1-100)或对数倍率滑杆(1-600，⌈600^t⌉ 向上取整；与按钮不同步；已滑段金色、未滑段暖灰棕 `ui/slider_track.png`)，最长 100 小时(360000s)；主界面倒计时显示按总时长自适应 H:MM:SS / M:SS / 秒

## 配色系统

弹窗和主界面底部按钮使用独立的暖金/沙色系，不受沙漏沙色切换影响：

| 常量 | 色值 | 用途 |
|---|---|---|
| `POPUP_BG` | `#faf5eb` | 弹窗底色 |
| `POPUP_GOLD_SEL` | `#caa450` | 选中项按钮 |
| `POPUP_CONFIRM` | `#9e3b29` | 音效弹窗「确定」按钮(暗红) |
| `POPUP_UNSEL_BASE` | `#a89078` | 未选基础周期按钮 |
| `POPUP_UNSEL_MULT` | `#b8a088` | 未选倍数按钮 |
| `POPUP_CANCEL_BG` | `#d8d2ca` | 取消按钮(暖灰) |
| `POPUP_TEXT` | `#332418` | 按钮/标签文字(深咖啡) |

主界面底部按钮：周期按钮 `#c4ae8e`(暖米色)，音效按钮 `#caa450` 92%(金色，文案=当前音效名；选「无声音」变暖灰 `#b7afa4`)。

## 音效方案

`_SoundProxy` 按平台选最优方案：

| 平台 | 方案 | 循环方式 | 缝隙 |
|---|---|---|---|
| Android | `AudioTrack` MODE_STATIC | `setLoopPoints(0, frames, -1)` 硬件回绕 | 0ms |
| Windows | `winsound` | `SND_LOOP` 驱动层循环 | 0ms |
| 其他桌面 | Kivy `SoundLoader` | `loop=True` 应用层循环 | 可忽略 |

Android 方案的核心：手动解析 WAV 头提取 PCM 裸数据 → 一次性写入 AudioTrack 硬件缓冲区 → `setLoopPoints` 告诉音频 DSP 自动回绕读指针。整个循环发生在音频芯片内部，**不经过任何软件层**。pyjnius 传 `byte[]` **直接传 Python `bytes` 即可**(单次 `SetByteArrayRegion` 整块拷贝) —— pyjnius **没有** `jarray`,写了会 ImportError 并静默回退到会卡的应用层循环。

**音效素材**：3 个实录音效(水流/风/钟表)。噪声类(水流/风)按"首尾相似片段选段 + 互相关对齐 + crossfade"焊循环;**钟表必须按拍切**(`tools/make_clock_loop.py`,见经验教训「有拍子的音效不能按秒切」)。源 MP3 放 `mp3/`(已进 .gitignore,不入库)。

## 经验教训(给后来的 AI/开发者)

这一节记的是**排查手法和判断错误**;几何/物理的结论在 `CLAUDE.md` 与 `../readme.md`。
起因是一轮"沙漏接口有显示残留"的视觉修复,下面每条都是那一轮真实踩出来的。

### 性能:PC 上调不出来的问题,别用 PC 的数据下结论

`Line(width=2)` 会为**每条线**生成一个带 10 段圆头帽的三角网格(Kivy 不用 `glLineWidth`)。
峰值约 2500 条这样的线 = 数千次网格提交 + 每颗粒的 Python 提交成本。**桌面 GL 提交便宜、
余量大**,实测帧率对粒子数几乎不变(63.2→63.3fps,CPU 0.08→4.02ms),所以在 PC 上
怎么调都看不出问题;换到 GLES 立刻变成"一整段周期帧率都上不去"。

- **判据**:帧耗时与在途粒子数近似线性(实测 `2.5ms + 10.05µs × N`)。出现这条线性关系,
  就是"每颗粒一个固定成本",而不是某个尖峰。
- **排除法**:把粒子全改成 `width=1`(顶点数降到 1/12),Canvas 立刻 6.20→4.37ms、
  >25ms 帧 20→2 —— 一下就钉死是宽线几何。
- **朴素批处理会净亏**:把每个盖帽顶点都在 Python 里写一遍(每颗约 96 次索引写),
  图元耗时 4.81→13.21ms,把 Canvas 省下的全吃回去。**必须让每颗粒的 Python 成本
  降到常数级**(端点搬进顶点纹理,每颗粒只写 3 个 float)。
- **砍核心数能分辨瓶颈**:8 核→4 核只差 6.5%(落在噪声内)→ 瓶颈在**单线程**,
  加核没用。

### 压 Python 热循环:先过像素一致性,再谈收益

把 GPU 那层(宽线网格)解决之后,低帧的大头迟早落到**纯 Python**上(粒子物理 +
每帧提交)。这类改动**必须逐位等价**:算式一字不改、随机数调用顺序一个不挪,
否则粒子流一变、画面就变了。

实测有效的手法(按收益排序):

- **内联 `max`/`min`** —— 这两个内置函数的调用开销远超一次比较。把 `max` 调用从
  132 万次降到 8.8 万次,单这一项就占原本 Python 时间的约 15%。⚠️ 用 `>`/`<` 时
  方向要对齐 `max`/`min` 的返回规则(`max(a, b)` 在 `b > a` 时返回 `b`,否则返回 `a`)。
- **字段缓存进局部变量** —— dict 查找改成读一次、写回一次。
- **别在循环里造临时对象** —— `buckets[index, size]` 每颗粒都新建一个元组;
  改成预摊平的二维表两次下标。
- **别在循环里用生成器** —— `tuple(x for x in ... zip(a, b))` 比三次直接算术慢得多。
- **内联每颗粒调一次的小函数** —— `_sand_half_w`/`lerp_rgb` 把 sqrt/算术铺开,
  顺便提升 `Ri*Ri`、球心这类不变量。

**验收门槛**:`tools/inspect_flow.py` 固定了 `random.seed(23)`,在同一份种子下抓
整幅截图逐像素比对,**必须 0 差异**。过不了这关,就不算"没动画质"。

### 别信"单次跑"和"没校验的推送"

- 同一份代码连跑两次,5s 平均 61.5 → 54.5(**±12%**)。**A/B 必须交替多轮**,
  只看单次会被噪声骗。
- `adb root` 会中途掉,之后 `push` 全部**静默失败** —— 应用其实还在跑旧文件,
  于是"实验结论"全是假的(我们就被这个骗了一轮:以为 GPU 方案装不上、
  纹理方案退化成朴素批处理)。**推送后必须校验 local/remote 字节数**,
  并让应用开机把实际装载的渲染器写进标记文件,与日志里的 `Flow renderer:` 对上。
- 生成的 `.py` 变体**推之前必须本地 `ast.parse`**:一次转义塌成真换行导致
  `SyntaxError`,应用直接起不来(AND 报 `Python for android ended`)。

### 诊断:先量像素,别靠肉眼看放大图

肉眼看 6× 放大图会误判——把连续曲线看成"台阶"、把抗锯齿看成"接缝"、把正常的球面
收敛看成"毛刺"。把截图按颜色分类打成 ASCII 网格,边界在哪、壁厚几像素一目了然:

```python
def cls(px):
    r, g, b = px
    if abs(r-0x5f)<28 and abs(g-0x6b)<28 and abs(b-0x70)<28: return '#'   # 玻璃描边
    if abs(r-0xea)<12 and abs(g-0xf3)<12 and abs(b-0xf8)<12: return '.'   # 玻璃内腔
    if abs(r-0xfd)<12 and abs(g-0xf6)<12 and abs(b-0xe3)<12: return ' '   # 背景
    if g > r and g > b: return 'S'                                        # 沙(绿沙时)
    return '?'                                                            # 混色/边缘
for y in range(y0-20, y0+20):
    print('y=%3d |%s|' % (y, ''.join(cls(im[y, x]) for x in range(cx-40, cx+40))))
```

"球↔管扁平肩台"就是这么定位的:截口那一行的暗带横跨 ±46px,而管半宽只有 17px ——
**说明问题出在球身上,不是管子**。只看图很容易得出"把管子改细"的错误结论(改了也没用)。
拿不准某个像素是什么,直接 `print(tuple(im[y, x]))` 和参考色比对,别猜。

### 诊断:用户的"矩形感"要先变成可测的数,再动手

用户说"颈部有个明显的矩形区域"。目测很容易归错因(我先归成了"顶端那条水平线",
量完才发现不是)。把裁图逐行做**颜色游程编码**,`tools/neck_profile.py` 就是干这个的:

- 逐行统计沙色像素的横向范围 → **宽度恒定 = 矩形**;随 y 单调变化 = 跟着玻璃走。
- 逐行统计玻璃灰线内侧 → 与沙宽的差 = **"喇叭口里那块没沙的楔形"**,可以量化到像素。

结果:出口以上全部贴合,**只有出口以下 21px 带里**玻璃从 20px 张到 83px 而沙流恒 20px
(单侧最多露 31px)。根因是 clamp 的拐点 `_lower_sand_top` 落在带内部。
**没有这一步,任何改动都是瞎改。**

同理,panel 提出的"出口上下有色调台阶"被这一步否掉:上下 1px 中轴像素都是 `#e6b870`,
RGB 距离 **0**,假设不成立 —— 一条改动直接省掉。

### 想让边缘"变毛",逐颗粒加随机是无效的

第一版给每颗粒的横向 clamp 乘一个伪随机抖动(复用已有相位,不新增 `random`)。
实测边缘去趋势残差只有 **0.29 逻辑 px**,几乎没变。原因:**每图像行压着约 6 颗粒,
边缘取的是它们的最大值** —— 逐颗粒的随机性被这个 max 抹平了。

改成**只随 y 变化**的相干摆动(整条射流边缘一起起伏)后,残差 0.29 → **0.99 px**、峰峰 4-5px,
肉眼可见。**"给每个粒子加噪声" ≠ "让边缘变毛"。**

还有个前置条件:这条射流在出口 18px 之外的宽度**根本不是 clamp 决定的**,而是粒子
自身的展宽(`shrink` 收缩 + wobble + 描边宽)。想让 clamp 起作用,必须先让它**比自然
展宽更窄**(本次把包络从 `0.68×孔径` 用 110px 松弛回孔径,于是全程 binding)。

### 视觉改动先问:它落在物理层还是渲染层?

安卓上 `tools/flow_texture_experiment.install()` 会执行
`widget_class._draw_stream = draw_texture_batches` —— **整个方法被替换**,而且它读
`view.nx`(`pv.nx = self.px[:n]`,**物理数组的零拷贝视图**),不是 `pv.x`。

所以:
- 在 `_draw_stream` 的 Python 循环里改 `x` → **桌面变、真机不变**;
- 写 `pv.nx` → 把渲染偏移写回物理状态,下一帧粒子真的跑偏;
- 落在 `update_particles`(物理层)→ 两条渲染路径都看得到 ✅(本次改的 clamp 就在这里)。

**而且 clamp 有两份实现**:`main.py` 的标量循环 **和** `tools/flow_numpy.py` 的向量化版,
`tools/test_physics_equiv.py` 要求逐位等价 —— **改一处必须改两处**(常量也要同步进
`consts` 字典,漏一个就 `KeyError`)。

### 诊断:按状态分组比对同一行像素

伪影只在某个状态出现 → 必然来自只在该状态绘制的图元。改完曲线后颈部出现一条 2px 灰带,
比对 idle / run / pause / done 四态发现**只有 done 有** → 直接锁定"颈部高光"
(`remaining <= 0.001` 才画的两条线,硬编码在 `cx ± (neck_w - 1)`,孔壁收窄后糊在了管壁上)。
这比从头读渲染代码快一个数量级。

### 诊断:别信目测比例,也别信"它看起来像哪个状态"

- 转述来的第三方意见里"接口宽度约为球体直径的 20-30%,偏大"是**错的**:实测管外宽 34px
  vs 球径 ≈260px,约 13%,本来就比建议值更细。**裁剪放大图会彻底破坏比例感,任何比例
  判断都必须回原图量。**
- 用户以为的"下落第 1 帧"其实是**暂停态**——整幅发白是暂停遮罩(v4 `BG_COLOR` +
  `stipple='gray50'`,apk `Color(*BG_COLOR, 0.55)` 全画布矩形)。**看到"整幅变淡"先想
  遮罩层,不是渲染 bug。**先把状态认对,再讨论像素。

### 抓图:不要用 ImageGrab 截 tkinter 窗口

`ImageGrab.grab()` 会偶发 `OSError: screen grab failed`,异常打断清理逻辑会让 mainloop
卡死变僵尸进程(`../readme.md` 早写过)。可靠做法:

- **v4(tkinter + PIL)**:monkeypatch `ImageTk.PhotoImage` 截获渲染好的 PIL 图层,自己
  合成 `背景 → 玻璃 → 沙`,完全不碰屏幕。缺点:抓不到画在 Canvas 上的粒子。
- **apk(Kivy)**:`Window.screenshot(name=...)` 能抓到粒子,返回值是**实际落盘路径**
  (会自动插序号,别硬拼文件名)。窗口刚创建时截会拿到 800×600 的旧帧,**延迟 ≥1s 再截**;
  同理 widget 未完成布局时读到的几何是 100×100 的垃圾值。

### 视觉改动:先在 v4 定形,再移植 apk

v4 有 PIL 4× 超采样 + LANCZOS,轮廓最干净,适合判断"形状对不对";Kivy 只有
`multisamples=2`,几何问题会和渲染噪声混在一起。铁律本来也要求 v4 是唯一基准 ——
**在基准上把参数调到满意,再照搬 + 坐标翻转**,能省掉大量来回。

### 参数陷阱:在一个工况下"碰巧对"

曲线过渡的起点宽度第一版写成 `TAPER_K × neck_w`:宽颈(neck_w=20)时 2.2×20=44,恰好盖住
44px 的肩台,**看起来完全正确**;细颈(neck_w=5)时只有 11px,肩台原样残留。正确的量纲是
肩台自己 `√(R²−Ri²)`,与 neck_w 无关。

**凡是"系数 × 某参数"的表达式,必须在该参数的两个极端都验证。** 本项目 neck_w 跨度
7→17(apk)/4→20(v4),周期跨度 1s→100 小时(`MAX_DURATION=360000`),验证要跑到头。
写个几何冒烟脚本比截图快:断言"直筒段不消失 / 曲线单调 / 壁厚为正 / 起点盖住肩台"。

### 面积变大,会把原本藏得住的"开关式逻辑"暴露出来

颈部沙柱一直是 `elapsed > 0` 一帧切换(空↔满)。原来颈部只是 40×34px 的直管夹在两球之间,
没人注意;喇叭口张到 88px 宽之后,起跑瞬间"啪"地全绿,用户立刻报"没开始/第 1 帧/第 3 秒
区别非常大"。

**改动放大了某个区域时,回头审视这个区域里所有的布尔开关**——它们大概率都得改成渐变。
(现在是 `NECK_FILL = 0.25s` 内从上往下注满,`_neck_sand_side()`。)

同一轮还暴露了第二个:沙柱把**下喇叭口**也填成实心,视觉上变成"宽→窄→又宽→挂一根细绳",
沙流与颈部断开。敞开下喇叭口交给粒子,才像沙从孔口流出来。

### 用户猜测原因时,先验证因果,再决定要不要回退

用户看到新问题后问"是不是不该改圆弧,只去掉残余就行?"。实测跳变是旧逻辑造成的,圆弧只是
把它放大了 —— 回退只会把问题重新藏起来。**用证据讲清因果,然后修真正的原因**;同时把回退
成本讲明白(备份路径),让用户有的选。反过来也一样:别因为自己改过就嘴硬,证据指向哪就是哪。

### 静默 fallback 会让后续所有修复打空

Android 音频"每 15s 循环点卡一次"排查了两轮（改采样率、对齐设备原生率）都毫无效果。真因是
`_init_audio_track` 的**第一行**：

```python
from jnius import autoclass, jarray   # ← pyjnius 根本没有 jarray
```

ImportError 被外层 `except Exception` 吞成一句 print，静默回退 Kivy `SoundLoader`；Android 上
Kivy 走 `audio_android`(`MediaPlayer.setLooping`)，**应用层循环不是 gapless**。也就是说
AudioTrack 硬件循环从第一版起**一次都没执行过**，那两轮修复改的代码全在这行下面，永远跑不到。

三条可复用的教训：

1. **"某后端在用"不能靠假设。** 兜底路径必须把选中的后端和失败原因**暴露到界面上**，否则装机后
   无从判断，只能靠 adb —— 而没有 adb 就会像交接文档那样卡住:"必须先上真机才能分支"。
   这行小字后来直接贴出 `soundloader — ValueError: AudioTrack not initialized: state=2`,
   **30 秒定位了第二个 bug**(见下条),而在此之前盲改了两轮都没摸到。现在它只在异常时出现
   (`sound_problem_desc()`,正常时高度 0),既不碍眼又留着线索。
2. **改完没效果时，先确认那段代码真的执行了**，再怀疑参数。加一句 print 在函数入口，
   比再猜一轮参数便宜得多。
3. **上游 API 别凭印象写。** `jarray` 看起来很像 pyjnius 该有的东西，实际不存在
   （`jnius/__init__.py` 只 star-import `jnius.jnius` + `jnius.reflect`，都没有这个名字；
   唯一含该字串的是内部 `cdef convert_jarray_to_python`）。查 30 秒上游源码即可证伪。
   顺带一提，byte[] 参数**直接传 Python `bytes`** 就行，pyjnius 有单次 `SetByteArrayRegion` 快路径。

### 修完一层,要预期下一层(状态常量用错的经典坑)

`jarray` 修掉之后**问题没好**,因为下面还埋着第二个:

```python
state = track.getState()
if state != AudioTrack.STATE_INITIALIZED:   # ← MODE_STATIC 在写数据前永远不等于它
    raise ValueError(...)
... 后面才 track.write(pcm, ...)
```

`STATE_NO_STATIC_DATA`(2) 的官方定义就是"**已成功初始化、使用静态数据、但还没收到那份数据**
的 AudioTrack"。也就是说 track 造出来了、1.4MB 静态缓冲也被接受了,只是**校验放在了写数据之前**,
这个条件按设计不可能成立 → 照样抛异常 → 照样回退 MediaPlayer。正确顺序是
"写前接受 {1,2} → write() → 写后再确认 ==1"。

两条教训:

- **同一条链路上的 bug 会叠罗汉**。第一个修好后症状原样不变,很容易误判成"第一个没修对"而回退。
  判据是**失败原因变了没有**:这次错误串从 `ImportError: jarray` 变成了 `state=2`,
  说明第一个确实修好了、只是撞上了第二个。
- **校验类代码要连"这个条件在什么时刻才可能成立"一起想**。前置校验写成后置条件,
  是"看起来最稳妥、实际最致命"的一类错误 —— 它把正常路径整条封死,而且伪装成"设备不支持"。

同一轮还发现一个"看起来很合理但是有害"的改动:`_resample_pcm` 纯 Python 逐样本重采样
72 万样本跑在 UI 线程上，命中即冻屏数秒 —— 而且没必要（AudioFlinger 的 SRC 在 track 流上，
loop 在更上游解析成连续重复流，不会在循环点重置相位）。**兜底代码也要算成本**，
不能因为"平时不走"就随便写。

### 有拍子的音效不能"按秒切",要"按拍切"

水流/风是随机噪声,循环只要求接缝电平和频谱连续,切多长都行。钟表**有拍子**,同一套流程
套上去就坏了 —— 用户报"节奏不对,效果很差"。实测三个版本(源:0.2535s 一拍,滴/答强弱交替 2.48 倍):

| | 时长 | 回绕间隔 | 强弱比 | 峰值 | crest | 最大拍/中位拍 |
|---|---|---|---|---|---|---|
| 7s 版 | 6.986s | **−47%(抢半拍)** | 0.95(没了) | 0.95 | 16.6 | 1.8 |
| 6s 版 | 5.986s | +3.7%(其实没问题) | 1.76 | **1.00 削顶** | 16.9 | **5.3(有爆点)** |
| 按拍切 | 8.619s | +3.4%(=源天然抖动) | 2.33 | 0.90 | 25.6 | 2.4 |

三条教训:

1. **循环长度必须是整数个"节拍单元"**,而且要认对单元 —— 钟表是**滴+答一对**才是一个单元,
   跨奇数拍绕回后强弱会翻转。做法:在包络里检出所有拍点,切点取 `t[i]−60ms`(落在拍前静音里),
   长度取 `t[j]−t[i]`;这样回绕间隔**等于源里的天然拍距**,不是算出来的近似值。
2. **crossfade 要藏进静音**,别横跨瞬态。40ms 的 fade 全程待在拍前静音里 → 听不见;
   横跨滴答就会把瞬态糊成两个半拍。
3. **别对瞬态类素材做响度压缩**。为了把 RMS 拉到和沙沙声一样(0.0095→0.059,6 倍增益),
   6s 版压扁了动态:crest 26.8→16.9、强弱比 2.48→1.76、还削了顶并留下 3 个爆点。
   钟表大部分时间是静音,**RMS 根本不是它的响度指标**,按峰值归一化(0.90)就对了。

诊断上还有一条:**阈值取错会伪造出"问题"**。我第一次用 `env.max()*0.35` 检测拍点,
漏掉了弱拍,把 6s 版的回绕误算成 −10.8% 并据此下了结论;降到 0.12 重测才发现是 +3.7%,
真正的毛病是削顶和爆点。**检测类阈值要在两端各试一档,确认结论不随阈值翻转再下判断。**

### 流程红线

1. **改 `pc/` 之前先手动备份** —— `pc/` 不在任何 git 仓库里,改错没法回滚
   (本轮备份在 `pc/backup/hourglass_v4_pre_taper.py`)。
2. **跑任何会调 setter 的自动化脚本前,先备份 `~/.hourglass_config.json`** ——
   v4 的 `set_duration()` / `set_sand_color()` 内部会 `_save_config()`,测试脚本会静默
   改写用户的周期/沙色/音效。`../readme.md` 里早写过这条坑,本轮仍然踩了(把用户的周期
   从 2 秒改成了 10 分钟,事后才发现并还原)。**已知的坑要在动手前先读一遍,不是出事后再查。**
3. **临时脚本和 `_shots/` 别留在工作区** —— `.gitignore` 没覆盖它们,`git add .` 会一起提交。
4. **改渲染不等于可以改物理** —— 本轮所有改动都只碰绘制:`_raw_height_ratio`、守恒、
   粒子的 shrink/wobble/重力一行没动。粒子相关的"我觉得这样更好"在本项目已经回退过 5 次
   (见 `CLAUDE.md` 开头的铁律)。

## 文件结构

```
apk/
├── main.py                       # Kivy 应用入口(从 pc/hourglass_v4.py 重写，~1550 行)
├── buildozer.spec                # 构建配置(锁 p4a v2024.01.21)
├── icon.png                      # 启动器图标 1024×1024
├── presplash.png                 # 启动屏 1080×1920(#fdf6e3 底)
├── sand_loop.wav                 # 15s 无缝循环 PCM 16bit mono 48000Hz
├── sounds/
│   ├── water.wav / wind.wav / clock.wav
│   │                             # 3 个实录音效:无缝循环 48000Hz(water/wind 14s;clock 8.62s 按拍切)
├── ui/
│   └── slider_track.png          # 滑杆未滑段贴图 8×8 纯色 #8a7a68(暖灰棕,9-patch 可拉伸)
├── tools/
│   └── make_clock_loop.py        # 钟表 mp3 → 按拍切的无缝循环 wav(miniaudio + numpy)
├── mp3/                          # 实录音效源文件(仅本机,.gitignore)
├── fonts/
│   └── NotoSansSC-Medium.otf     # 中文字体(~8MB, Apache 2.0 可分发)
├── .github/workflows/build-apk.yml
├── .gitignore
└── README.md
```

完整踩坑指南见 `../../android/BUILD_APK.md`，pc 版设计见 `../readme.md` 与 `../CLAUDE.md`。
