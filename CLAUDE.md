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
