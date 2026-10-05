# 沙漏 Android 版交接文档

> 更新：2026-10-02。工程：`E:\AI_Tools\other\shalou_claude\pc\apk`。
> 仓库：`https://github.com/twoplate2/hourglass-ball`，分支 `main`。
> 本文用于接手，不是继续执行优化的授权。用户已明确说“别干活了”，优化目标处于暂停状态；本次只更新交接文档，不测试、不提交、不 push。

## 一分钟接手

1. **当前已推送版本为 `0.1.2`，提交 `e251df6`。低帧问题尚未彻底解决。**
2. **0.1.2 相比 0.1.1 没有改变正式沙流、物理或正式渲染。** 它增加开发实验与截图工具，并更新版本标签。不要把它称为性能修复版。
3. 正式运行仍是 Kivy/OpenGL 的 `Ellipse + Stencil + Line` 图元池。已使用 GPU 绘图，但物理、坐标更新和大量独立图元提交仍在 Python/主线程一侧。
4. 新端点纹理合批已有像素一致性和成本改善证据，但正常配置的 **1% low 尚未稳定显著改善，因此没有默认启用**。
5. 用户最在乎 1% low、最慢帧和沙子肉眼细节；不要减少密度、降低抗锯齿、隐藏低帧或只优化平均 FPS。
6. **禁止连接真机、MuMu、ADB。** 用户远程使用电脑，澄清“甄姬”指 PC；后续只在 PC 验证。
7. 每次 push 补丁版本加 1，提交标题带新版本号并使用中文，提交说明也用中文。下次 push 应为 `0.1.3`，不是再次推 `0.1.2`。
8. 原有未跟踪文件 `shengyin_lianxu.md` 是用户文件，不要修改、删除、暂存。

先读本目录 `AGENTS.md`、`README.md`、`CLAUDE.md`。不要从旧 `Clac/android` 或其他 Android 目录继续改；本目录才是当前工程。

## 当前问题与证据

### 用户真机反馈

用户报告设备为 K90、骁龙 8 Elite。下列是用户提供的数据，不是代理连接设备测得：

| 组别 | 周期 | 平均 FPS | 1% low FPS |
| --- | --- | --- | --- |
| 较早文字结果 | 1 秒 | 98.8 | 58.7 |
| 较早文字结果 | 5 秒 | 63.3 | 31.9 |
| 较早文字结果 | 15 秒 | 63.9 | 21.3 |
| 后续截图 | 5 秒，362 帧 | 73.8 | 42.7 |
| 后续截图 | 15 秒，1085 帧 | 72.8 | 41.0 |

后续截图最慢 5 帧：

- 5 秒：`42.5 / 42.8 / 42.8 / 42.8 / 42.9` FPS。
- 15 秒：`38.2 / 40.1 / 40.3 / 40.9 / 41.1` FPS。

曲线有持续低谷，不只是单个尖峰，可能与大量粒子在途时的工作量有关。但没有充分分段证据确定真机是 CPU、驱动提交、GPU 等待还是系统负载；**不要凭旗舰芯片、FPS 摘要就认定原因，更不要归咎于 GC 或热降频**。截图没显示版本号时，不能把“刚 push 的版本”当作已核实代码指纹。

### 视觉反馈

用户多次指出：球形沙漏连接处，上方实心沙柱与下方颗粒流之间有横向材质断层；沙子仍不够细腻。

0.1.1 已改善出口裁切、纹理覆盖与短距离渐变，密集截图没有再发现之前的亮色横缝。但这不是用户真机最终确认，不要宣称所有视觉问题都已彻底解决。后续应继续查看起步、多周期、中段、末段的实际画面，不只看一张好看的图。

## 版本与已完成内容

| 提交 | 内容 |
| --- | --- |
| `f837cea` | 复制报告 v2：设备环境、代码指纹、物理/图元/Canvas/前次 Swap/GC、慢帧明细 |
| `48cdbd0` | 颈部宽 Line 使用不透明 RGB 预混合，避免额外 Stencil；隐藏遮罩归零尺寸 |
| `0b2dec9` | PC 分块池、GPU 顶点平移实验，未默认启用 |
| `75569fa`，0.1.1 | 版本标识、中文版本提交检查、沙流过渡改进、物理循环常量提取、回归工具 |
| `e251df6`，0.1.2 | 端点纹理合批实验、密集截图和固定分辨率对照工具；正式渲染未切换 |

0.1.1 APK 构建已确认成功：
`https://github.com/twoplate2/hourglass-ball/actions/runs/36962630735`。
0.1.2 已确认 push 成功，本次交接未重新查询其云端构建状态，不把未知写成成功或失败。

### 正式版已有的行为

- Benchmark：底部音效与开始按钮之间的空白区域长按 3 秒；周期为 **1、5、15 秒**，没有 30 秒。
- 结果有平均帧、1% low、最慢 5 帧、曲线、复制结果；逐帧日志写入 `benchmark_logs`。
- 结束播放柔和提示音及预录微软晓晓 `zh-CN-XiaoxiaoNeural` 的“沙漏计时完成”，一次播报，不弹确认框；Benchmark 禁用完成语音。
- 启动不展示自定义加载图片。使用 `ui/startup_blank.png`，首个可用画面后移除应用原生加载遮罩；Android 系统自己的启动动画不等于应用加载图。
- 玻璃和沙体保持真圆与原轮廓；亮色颗粒后置，触底面与可见沙面一致，上下高度互补。
- 粒子生成速率未降低，GC 保持启用；固定画布和图元池复用。

### 0.1.1 正式沙流细节

- 真实拖尾不再整齐截在出口平面，可延伸到直管内。
- 普通颗粒借用已有 `wobble_phase` 产生稳定、轻微色差，不新增随机调用；颜色分组共享，预留容量考虑色差后的分布。
- 颈部投影复用已有物理颗粒，固定最多 **128** 个图元，优先覆盖出口；这是视觉副本，不是新增物理粒子。
- 柱体末段使用一次创建的 **1x64 RGBA** 纹理，由 GPU 平滑采样；透明度范围最终收敛为约 `0.7 -> 1.0`，起步强度通过两个 Rectangle 合成，不逐帧上传纹理。
- 纹理有 GL 重载观察者；重置后过渡矩形、粒子副本隐藏。
- 起步投影覆盖随主沙流建立，用初速度族判断避免少量快速颗粒把纹理提前拉空。
- 物理循环只提取帧内常量、缓存重复访问，不改重力、生成速率或轨迹公式。

## GPU 实验与测量结论

所有实验都在 `tools/`，通过测试参数启用，**不是手机正式版默认渲染**。

| 实验 | 入口 | 当前结论 |
| --- | --- | --- |
| 原 CPU Mesh 合批 | `--batch-flow` / `--compare-flow` | 少了绘制提交，但 Python 顶点更新较重，low 未稳定改善 |
| 空 Line 分块挂载 | `--chunk-flow` / `--compare-chunks` | 保留预留池，只挂载活动块；未发现稳定的 15 秒 low 收益 |
| GPU 顶点平移 | `--gpu-flow` / `--compare-gpu` | 保留圆头、使用预留缓冲区；Canvas 下降但图元更新增加，未默认启用 |
| 端点纹理 | `--texture-flow` / `--compare-texture` | 固定圆头顶点，每帧只上传端点；有成本改善，但正常 low 收益不稳定 |

端点纹理实现：`tools/flow_texture_experiment.py`。
RGBA8 纹理的前三行保存 x、底端 y、顶端 y 的 float32 字节，顶点 shader 复原 IEEE-754；不要求浮点纹理扩展。共享 RenderContext，保留色组和亮色后置顺序。需要顶点纹理采样支持；**实验不具备正式版完整回退集成，不要直接复制开启便声称兼容所有安卓设备**。

### 有效证据

- 物理热循环：9 个工况、共 **2526** 个逐状态样本，前后粒子/飞溅/闪光状态完全一致。更新均值约下降 4%-12%，多组 p99 下降；它不是整帧 FPS，个别最大耗时没有保证下降。
- 端点纹理：**52 张 1080x2400 密集截图**，50 张逐像素一致，另外两张共 7 个边缘像素不同，未见肉眼细节损失。
- 普通正常配置四轮：15 秒原 Line low `53.92 / 54.02`，端点纹理 `53.94 / 54.82` FPS；约 60 FPS 平均。Canvas 工作量可下降，但 low 差异不足以证明稳定提升。
- 固定高分辨率正常配置四轮，实际窗口 `1080x2399`：15 秒原 Line low `55.98 / 56.17`，端点纹理 `56.02 / 49.38` FPS。最后一轮有约 21ms 的提交间隔，**没有 >25ms 帧**，未见异常 GC 长停顿；正常 low 仍不稳定。

不要把 Canvas 从十多 ms 降到几 ms 直接理解为 GPU 压力下降同等比例：有些轮次的 VSync/驱动等待位于 Canvas，有些移到 Swap。应结合总帧间隔和前次 Swap 看。

### 原始记录位置

这些文件在本机 `benchmark_logs/`，该目录被 Git 忽略，不会随普通 clone 带走：

- 正常对照：`benchmark_20261002_122352_053876`、`122415_703278`、`122439_323089`、`122502_970494`，各有 txt/json。
- 高分辨率：`benchmark_20261002_134639_899580`、`134703_594518`、`134727_200876`、`134750_883820`，各有 txt/json。
- 物理有效记录：`particle_hot_loop_011_hires.json`。
- **无效耗时记录**：`particle_hot_loop_011.json` 使用了 Windows 粗粒度 `process_time_ns()`，出现大量 0 和 15.625ms；不得拿它的耗时做结论。
- 截图：`flow_visual_bridge_011_before/`、`bridge_011_release/`；`flow_visual_endpoint_baseline_dense/`、`endpoint_texture_dense/`。
- 早期 `bridge_011_final/`、`bridge_011_verified/` 包含未收敛的亮色横缝，是失败样本，不是最终视觉基准。
- 当前视觉基准用 `flow_visual_bridge_011_release/` 或 `endpoint_baseline_dense/`，不要挑旧失败截图证明已修复。

## 关键经验教训

1. **先确认走的后端。** “使用 GPU 绘图”不等于“使用 GPU 合批”；0.1.2 的实验文件进仓库不等于正式 App 调用了它。
2. **先解决持续高负荷，再看单个尖峰。** 用户曲线与在途粒子峰值相关，但 FPS 摘要不能独立定因。
3. **不要减少沙粒或换粗糙几何换帧率。** 玻璃/沙体必须继续用真圆；不要全局关闭 GC，不动生产 VSync/Clock 来做漂亮成绩。
4. **`Line.width > 1` 且 Color alpha < 1 会触发 Kivy 内部额外 Stencil 通道。** 不透明 RGB 预混合可保持外观并避开它；不要退回逐粒半透明宽线。
5. **起步最容易出错。** 只放出口高光不能消除材质差；只让拖尾跨出口也不够。过强透明渐变会制造白横缝，孤立快粒子不应决定投影范围。
6. **不要把猜想当真因。** 曾怀疑渐变纹理污染后续沙粒 alpha；像素探针显示普通 Line 本来就是不透明，假设被否定。额外白纹理/BindTexture 已撤掉。
7. **像素回归必须对齐几何。** 旧参考默认周期 50 秒，测试对象为 60 秒，改 duration 后没重建会在高 DPI 造成几百个边缘差异。现已让两个对象同为 60 秒并重建，横竖屏回归通过。
8. **几何回归不等于材质回归。** 与旧参考比较时，测试临时把过渡底色设为不透明以检查壳/真圆沙体；新材质另靠真实多时刻截图验证。
9. **Windows 时钟要分清。** 动画/测量用 `perf_counter()` / `perf_counter_ns()`；不要用粗粒度 `process_time_ns()` 评估每帧几 ms 工作。
10. **采样有边界。** 当前统计是 `Window.on_flip` 提交间隔，不是 Android 合成器的实际呈现帧。前次 Swap 属于上一帧，阶段耗时不能机械相加定因。
11. **截图不能混进评分。** `--capture` 在采样中 deepcopy 状态，虽结束后才抓屏，复制仍会影响间隔；只用于定位画面，另跑无 capture 的成绩。
12. **取消不是通过。** Benchmark 未完成指定轮数会返回失败；曾旧工具被取消后还输出 All checks passed，已修正。
13. **记录真实窗口尺寸和代码指纹。** Windows DPI/窗口变化会造成大停顿；尺寸改变的轮次不是受控对照。`--source` 参考代码如今保存实际 source path 供指纹读取，不混用当前 main.py 的 hash。
14. **图像读回用 RGBA。** RGB 行对齐在分数 DPI/某些窗口宽度会破坏截图；当前 Inspector 用 RGBA，再翻转图像，不看旧损坏图片。
15. **后台视频压缩、远程桌面会干扰 PC。** 不擅自结束用户后台任务，不把系统波动都归为沙子或 GC；配对交替测试并保存全部原始结果。
16. **不要无限堆实验与进度版本。** 已有多个方案只降低 Canvas 均值、没有稳定提升 low。恢复后应收敛到有分段证据的主因，少量有效 A/B，避免用“又 push 了”代替真正修复。

## 后续未完成与接手顺序

当前停止在“端点纹理实验已验证大部分视觉与成本，正常 low 提升未成立”的阶段。**正式版还没有换后端，尚未彻底修复真机低帧。**

用户明确恢复工作后再做：

1. 查看上述慢帧明细、粒子峰值、Canvas/前次 Swap/图元更新；不重新跑所有旧实验。
2. 补端点纹理的横屏负坐标、旋转和多分辨率截图。工具已有 `--landscape` 和矩阵裁切，**此分支本阶段尚未跑完横屏动态截图，不得写成已验证**。
3. 在正常配置下验证 high-load low 的稳定收益；非 VSync、`--uncapped` 只用于成本定位，不作为正式帧率改善宣称。最近一次无 VSync 端点高分辨率对照尚未执行。
4. 若确有收益，再提炼生产模块、检查顶点纹理支持与 shader 失败回退、资源重载/退出/重置、启动分配和容量增长。不让正式 main 导入一堆开发实验工具。
5. 再审查用户要求的细腻颗粒及颈部衔接。需要密集起步、中段、末段截图，也需要实际正常运行观察，不能只依赖绿色回归。
6. 下一次 push 必须 `0.1.3`，中文版本标题。不要声称 PC 通过即等同于骁龙 8 Elite 真机保证；当前禁止设备连接。

若仍没有稳定收益，不启用实验，不编造“彻底修复”或“保证最低帧率”。记录剩余风险即可。

## 工程入口与规则

- `main.py`：App、沙漏几何、物理更新、正式图元池、颈部过渡、完成语音。
- `frame_benchmark.py`：隐藏入口、采样/统计、报告/曲线、日志、环境和分段探针。
- `app_version.py`：唯一 `APP_VERSION`；当前 `0.1.2`。
- `buildozer.spec`：使用 `version.regex` + `version.filename` 读取 app_version；不要同时恢复独立 `version = ...`。
- `.githooks/pre-push`、`tools/check_release_version.py`：相对远端版本加一个 patch，标题以版本开头且含中文。本地已设置 `core.hooksPath=.githooks`；CI 同样检查，checkout 为 fetch-depth 0。
- `tools/test_release_version.py`：共享 APK 版本来源、递增和中文标题单元验证。
- `tools/verify_hourglass.py`：隔离配置/音效/剪贴板，快速回归、各实验、配对测试，支持 `--pixels`。
- `tools/inspect_flow.py`：模拟时钟截图，不评分 FPS；密集 52 时刻与旋转裁切。
- `tools/verify_particle_hot_loop.py`：从源码 AST 提取生产更新函数，在无窗口夹具上比较逐状态和更新耗时，不含渲染。
- 构建锁：Kivy **2.3.0**，p4a **v2024.01.21**，Buildozer **1.5.0**，Cython **<3.0**，CI Python **3.10**、Ubuntu **22.04**。不要顺手升级。
- 本机曾使用 Python 3.11.4、Kivy 2.3.1、NVIDIA RTX 4070；不是安卓设备，不能替代其驱动/调度。

下面只是恢复授权后可用的命令，不应在暂停时自动运行：

```powershell
cd E:\AI_Tools\other\shalou_claude\pc\apk
git -c safe.directory=E:/AI_Tools/other/shalou_claude/pc/apk status --short
python tools/test_release_version.py
python tools/verify_hourglass.py --quick
python tools/verify_hourglass.py --quick --landscape
python tools/verify_hourglass.py --quick --texture-flow
python tools/verify_hourglass.py --benchmark-only --compare-texture --rounds 4 --pixels 1080,2400
python tools/inspect_flow.py --label line_dense --pixels 1080,2400 --dense-neck
python tools/inspect_flow.py --label texture_dense --texture-flow --pixels 1080,2400 --dense-neck
python tools/inspect_flow.py --label texture_land --texture-flow --landscape --pixels 2400,1080 --dense-neck
```

`benchmark_logs` 中的源码备份只在本机存在，fresh clone 不可直接假定 `--source` 或热循环 `--before` 文件存在。
不要使用 `git add .`，只暂存任务文件。用户原有 `shengyin_lianxu.md` 保持未跟踪。

## 历史音频经验

音频循环路径此前已修，不是当前要重新扩展的方向。核心教训：

- pyjnius 没有 Python 层 `jarray`，直接传 `bytes` 一次写入 PCM。
- MODE_STATIC 写入前 `STATE_NO_STATIC_DATA(2)` 合法；写完才应检查 `STATE_INITIALIZED(1)`。
- reload/seek 后需重新设置 loop points；停止、切音效须释放旧后端。
- 不要在 UI 线程做 72 万样本的 Python 重采样；后端失败不能静默隐藏。
- 有节拍的钟表音按滴答对切循环，不能按秒乱切或用噪声素材的压缩方法。

更完整历史记录保留在下方，但里面的装机/旧 PC 唯一基准描述不覆盖上面的当前规则；**不执行设备连接或装机步骤**。

<details>
<summary>2026-08-28 历史音频排查附录（已解决，非当前任务）</summary>

## 0. 项目拓扑（一分钟版）

| 位置 | 说明 |
|---|---|
| `pc/apk/` | **Android(Kivy)版，本目录。** 独立 git 仓库，push `main` 自动触发 Actions 构建 APK |
| `pc/hourglass_v4.py` | **PC(tkinter+PIL)版，视觉/物理唯一基准**（"铁律"）。不在任何 git 仓库里 |
| `pc/CLAUDE.md` / `pc/readme.md` | 两版架构文档 + 经验教训库 |
| `ICON.md` | 图标制作经验教训（纯色大块抗锯齿、体积守恒、PIL 坑） |

沙漏本质"假物理"：唯一真值 = `elapsed/duration`，所有可见几何由它推导。改物理/视觉前先对照 `../hourglass_v4.py`，不要用直觉替代已验证公式。

## 2. ✅ 已解决：Android 音频每 ~15s 循环点卡顿（2026-08-28）

### 症状
沙沙声（`sand_loop.wav`，15s）在第 15s 卡一次；`sounds/water.wav` 同理。Windows winsound 连续。
用户设备是顶级旗舰，与性能无关。

### 真因：`_init_audio_track` 第一行的 `jarray` 不存在，整条 AudioTrack 路径从未执行

```python
from jnius import autoclass, jarray   # ← pyjnius 根本没有 jarray
```

1. **pyjnius 没有 `jarray`**：`jnius/__init__.py` 只 star-import `jnius.jnius` + `jnius.reflect`，
   两者都无此符号；`jnius.pyx` 及其 `.pxi` 里唯一含该字串的是内部 `cdef convert_jarray_to_python`，
   Python 层取不到 → 这一行直接 `ImportError`。
2. `__init__` 的 `except Exception` 吞掉 → `backend="soundloader"`。
3. Android 上 Kivy provider 顺序 `audio_android` 优先（`kivy/core/audio/__init__.py:208`），实现是
   `MediaPlayer` + `setLooping(True)`（`audio_android.py:99`）—— **应用层循环不是 gapless**，
   每到文件末尾停顿一次，卡顿周期跟 wav 时长走。
4. Windows 走 winsound `SND_LOOP`（驱动层）→ 连续。

`git log -S jarray -- main.py` 显示这行自 AudioTrack 方案第一版（`9b6dee5`）就在 ——
**硬件循环一次都没跑起来过**。这也解释了前两轮修复（`704db63` 采样率 44100→48000、
`cdebf71` 设备原生率对齐）为何毫无效果：**它们改的代码全在这一行下面，永远跑不到**。

### 修复内容

1. **去掉 `jarray`**：`track.write(pcm, 0, len(pcm))` 直接传 Python `bytes`。pyjnius
   `calculate_score` 对 `'[B'` 遇 bytes +10 分、对 `'[S'` 返回 −1（确定性命中 `write(byte[],int,int)`），
   `convert_pyarray_to_java` 对 bytes 走单次 `SetByteArrayRegion` 整块拷贝。
   ⚠️ MODE_STATIC 的 native `writeToTrack()` 每次 write 都 memcpy 到缓冲**起始处**，必须一次写完。
2. **删掉 `_resample_pcm` 及调用点**：纯 Python 逐样本重采样 72 万样本跑在 UI 线程会冻屏数秒；
   且没必要 —— AudioFlinger 的 SRC 挂在 track 流上，loop 在更上游解析成连续重复流，
   不在循环点重置相位（"非整数重采样断相位"的旧假设上一轮已被对抗推翻）。保留 `native_rate` 打印供诊断。
3. **`play()` 每次重新武装 `setLoopPoints`**：native 层 `reload()`/`setPosition()` 会清 loop 状态，
   不重新武装的话"停止再播只响一遍"。顺带把魔法数 `0` 换成 `AudioTrack.MODE_STATIC`。
4. **后端可见化**：`_SoundProxy` 增加 `error` 字段，`HourglassWidget.sound_problem_desc()`
   **只在没走到无缝后端时**在音效弹窗底部显示一行红字（正常时高度 0，看不见）。

### 第二层（同一条链路上的第二个 bug，靠上面那行小字 30 秒定位）

装机后小字贴出 `soundloader — ValueError: AudioTrack not initialized: state=2`。
`state=2` 是 **`STATE_NO_STATIC_DATA`**：官方定义"已成功初始化、使用静态数据、但还没收到那份数据"。
即 track 造出来了、1.4MB 静态缓冲也被接受了，**但校验放在了 `write()` 之前**，
`getState()==STATE_INITIALIZED(1)` 在 MODE_STATIC 上按设计不可能成立 → 照样回退 MediaPlayer。
修法：写前接受 `{1, 2}` → `write()` → 写后再确认 `==1`。

这同时解释了用户报的另一条：**冷启动前 5s 断续、重置后就正常** —— MediaPlayer 首播要解码 + 建缓冲，
第二次文件已在页缓存里；AudioTrack MODE_STATIC 是整段 PCM 提前驻留，不存在预热。

旁证：`sand_loop.wav` 实测极其平稳（每秒 RMS 波动 1.3%、频谱重心 0.9%，接缝处谱通量只有中位的
1.1 倍、99 分位的 0.98 倍），**素材层面挑不出毛病** —— 循环点还能听出东西，只可能是播放链路。

### 装机验证方式

打开音效弹窗看底部：
- **什么都没有** → 已走 audiotrack（或桌面 winsound），硬件循环生效。听沙沙声连跑 ≥45 秒跨 3 个循环点。
- **出现一行红字** → 还有下一个失败点，错误文本直接指向是 `$Builder` / `getState` / `write` /
  `setLoopPoints` 哪一步，不用再猜。

历史上曾用设备日志辅助定位；当前禁止连接设备，不执行该路径。

### 若 audiotrack 仍卡（本轮未实现的后手）
- 两个 `MediaPlayer` + `setNextMediaPlayer` 互相接力，替代 Kivy 那条会卡的 `setLooping` 路径；
- 或 AudioTrack `MODE_STREAM` + 后台线程喂 PCM（不受静态缓冲大小限制，但 Python 线程喂数据有 underrun 风险）。

## 3. 关键代码位置（main.py）

- `_SoundProxy`（约 216-）：`__init__`（`backend` + `error` 标记）/ `_init_audio_track`（WAV 解析 → AudioTrack MODE_STATIC → 单次 write bytes → setLoopPoints）/ play（每次重新武装循环点）/ stop / close。
- `HourglassWidget.sound_problem_desc`：**仅异常时**返回文案（正常/静音返回空串）；弹窗里那行红字按宽度换行、高度绑 `texture_size`。
- `_make_sound_proxy`（约 780-）：不再静默吞异常，打日志。
- 音频资源：`sand_loop.wav`(15s)、`sounds/{water,wind}.wav`(14s)、`sounds/clock.wav`(8.62s，全 48000Hz mono 16bit)。
  钟表声 2026-08-28 按拍重切（`tools/make_clock_loop.py`）：旧 7s 版回绕抢半拍、旧 6s 版削顶+爆点+压扁强弱交替。
  **有拍子的音效不能套噪声那套流程**，详见 README 经验教训「有拍子的音效不能按秒切」。

## 4. 常用验证

```powershell
cd pc/apk
python -m py_compile main.py            # 语法检查
python main.py                          # 桌面预览(走 winsound;音效弹窗底部应无红字)
# 当前禁止设备连接；历史验证命令不作为恢复优化的授权。
```
- 图标/物理验证见 `ICON.md`、`../readme.md`、`../CLAUDE.md`。

## 5. 历史音频经验总结

§2 记录音频链路两层问题的定位过程。当前不是等待代理装机验证的任务，不连接真机或模拟器。
未来经用户明确授权排查音频时，后端错误提示仍是重要线索。

排查任何"改了没效果"的问题时，先照 §2 的教训确认那段代码真的执行了（README 经验教训
「静默 fallback 会让后续所有修复打空」），再怀疑参数。

</details>
