# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

pc 版球形沙漏(`../hourglass_v2.py`，tkinter + PIL 真圆)的 **Android (Kivy) 移植版**，用 GitHub Actions 云端构建 APK。

- 本工程的 `main.py` 从 `pc/hourglass_v2.py` **从零重写为 Kivy**，**绝不复用** `../../android/main.py`(旧版有严重 bug，已弃用)。
- **唯一真值 / 视觉基准是 pc 版球形沙漏**：改几何/物理/视觉时对照 `../hourglass_v2.py` 和 `../readme.md`，不要参考 android 旧实现。
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
- **`_SoundProxy`**(154-302)：Android `AudioTrack` MODE_STATIC 硬件循环 / Windows `winsound` 驱动层循环 / 桌面 `SoundLoader` fallback
- **`_SandBgPopup`**(305-342)：浅色弹窗，双层兜底覆盖 Kivy 默认深灰
- **`CenterTextInput`**(132-149)：Kivy `TextInput` 无 `text_align`，用 `CoreLabel` 测文本宽度动态算 `padding` 实现居中

### 核心设计：假物理 + 完整球 + 球体积微积分
- 唯一真值是 `elapsed/duration`，所有可见几何从它派生(本质进度条，**不要引入真物理模拟**)。
- 两个**完整球**(非锥形、非球冠) + 颈部圆柱管；`R = (ball_h² + neck_w²)/(2·ball_h)` 保证球顶 w=0、截口处 w=neck_w 与管无缝。
- 球体积 `v(t)=3t²-2t³`，`_raw_height_ratio` 用数值积分查找表(101 档)反查"体积→高度"。球对称 `v(t)+v(1-t)=1` ⟹ 上沙`(1-raw)` + 下沙`(raw)` = 1 **严格守恒**(改这块前先确认守恒不被破坏)。
- 上、下沙都用延迟 `_effective_fallen`(物理计算粒子飞到底的时间，`_fall_delay`，短周期按 `duration*0.45` 缩放)；下沙前期靠**沙面宽度变化**展示进度，高度只给极小保底(`MOUND_FLOOR_*`)，**不拔高**。
- `neck_w` 用 **log 插值**：短周期→宽颈，长周期→窄颈，范围受屏幕比例约束。

### 渲染：玻璃和沙都用 Kivy 真圆(关键，别破坏)
- 玻璃壳：`Ellipse` 画球(**同心椭圆相减**得均匀描边) + 颈部矩形管贯穿两球内端消除截口缺角。缓存于 `canvas.before`，仅 `neck_w` 变时重建。
- 沙体弓形：`StencilPush/StencilUse/StencilUnUse/StencilPop` 把内壁球 `Ellipse` 裁出"y ≤ 沙面"的真圆弓形。
- **沙边和玻璃内壁都是 `Ellipse` 圆 → 同一种真圆技术、严格贴合**。这是复刻 pc"玻璃和沙必须同一种技术，否则边缘失配(月牙/缝)"的核心。**绝不用 `Mesh`/多边形拼弓形**(那是 android 旧版渲染 bug 的根源)。
- 粒子用 `Line`(主流) + `Rectangle`(splash/flares/dust)，按颜色排序减少 draw call。颗粒流用递增 wobble 制造散乱断续感，触底 30px 内喇叭口微扩。

### 渲染分层(`redraw()` 中的 draw 顺序)
1. 上沙弓形(Stencil 裁切)
2. 下沙堆弓形
3. 颈部沙柱(Rectangle)
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
1. 手动解析 WAV RIFF 头（遍历 chunk 找 `data`，兼容非标准 44 字节头）
2. 提取 PCM 裸数据（16bit only），用 `jarray('b')(pcm)` 显式转 Java `byte[]`（pyjnius 不能直接传 Python bytes）
3. `AudioTrack.Builder` 设 `MODE_STATIC` → 一次性写入全部 PCM 到硬件缓冲区
4. `setLoopPoints(0, total_frames, -1)` → 音频 DSP 播到末尾时硬件自动回绕读指针
5. 循环完全在音频芯片内部完成，**不受主线程卡顿、GC 暂停、CPU 调度影响**

`sand_loop.wav` 是 15s 无缝 PCM 16bit mono 22050Hz（646KB），长度足够让任何潜在的系统音频管理问题不影响体验。

### 配色系统(独立暖金/沙色系，不随沙色变化)

弹窗和主界面底部按钮使用固定配色(`POPUP_*` 常量，60-67 行)，不受沙漏沙色切换影响：

| 常量 | 色值 | 用途 |
|---|---|---|
| `POPUP_BG` | `#faf5eb` | 弹窗底色(暖白) |
| `POPUP_GOLD_SEL` | `#caa450` | 选中/确定按钮(暖金) |
| `POPUP_UNSEL_BASE` | `#a89078` | 未选基础周期按钮(暖棕) |
| `POPUP_UNSEL_MULT` | `#b8a088` | 未选倍数按钮(浅棕) |
| `POPUP_CANCEL_BG` | `#d8d2ca` | 取消按钮(暖灰，比未选亮) |
| `POPUP_TEXT` | `#332418` | 按钮/标签文字(深咖啡) |

主界面底部按钮：
- 周期按钮：`#c4ae8e` 暖米色实色
- 音效开：`#caa450` 92%(金色)
- 音效关：`#b7afa4` 暖灰实色
- 开始(停止态)：绿色 `#5b9e3e` + 白字
- 暂停(运行态)：橙色 `#d98e3e` + 白字

`_SandBgPopup` 双层兜底覆盖 Kivy 默认深灰：
1. Popup 本体 `canvas.before` → 奶油底(填充 _container 外间隙)
2. `open()` 后 `_apply_light_theme()` → 清空 `_container.canvas.before` 并画奶油底
3. 标题栏仍为 Kivy 默认深灰，标题文字白色

### 粒子系统
- 主流粒子：从上球截口生成，`rate=600*speed_factor`，重力 `g=-450`，wobble 制造散乱感
- 触底事件：EMA 更新 `mound_peak_offset` + 25% 概率 spawn flare + 50% 概率 spawn splash
- splash 反弹粒子：向上反弹 vy=55~110，实心方块渲染，受 `_sand_half_w` 横向约束
- 完成尘埃：漏完时 spawn 25 颗，1s 寿命，向上喷射
- 横向 clamp：管内壁限幅 → 进下球后平滑过渡到球内壁

## Android 装机坑(桌面预览看不到，只在 APK 暴露)
- **中文乱码**：`LabelBase.register(name="Roboto", fn=fonts/NotoSansSC-Medium.otf)` 全局覆盖默认字体；`buildozer.spec` 的 `source.include_patterns` **必须含 `fonts/*.otf`** 否则字体不进 APK。
- **音效卡顿**：Android `MediaPlayer` 循环短音频有 50-100ms gap → 改用 `AudioTrack MODE_STATIC` 硬件循环(声卡指针回绕，绝对 0 缝隙)。
- **配置路径**：Android 用 `App.user_data_dir`，桌面用 `~/.hourglass_config.json`(与 pc 版共享同一文件)。
- **生命周期**：`on_pause` 必须返回 `True` 保持 GL 上下文。
- **周期弹窗闪退**：lambda 闭包延迟绑定在 Android Kivy 2.3.0 上时序敏感 → `mult_btns` / `preview_label` 必须在循环外预创建。

## 资源文件
`icon.png`(1024×1024) / `presplash.png`(1080×1920, `#fdf6e3` 底) / `sand_loop.wav`(15s 无缝 PCM 16bit mono 22050Hz) / `fonts/NotoSansSC-Medium.otf`(~8MB，Apache 2.0 可公开分发)。修改 `buildozer.spec` 的 `source.include_patterns` 时别漏字体和 wav。
