# 交接文档（jiaojie.md）—— 沙漏 App · pc/apk（Android Kivy 版）

> 用途：给接手的 AI / 开发者快速同步上下文。**重点是 §2 当前未解决的 Android 音频循环卡顿问题。**
> 最近更新：2026-08-28。仓库：https://github.com/twoplate2/hourglass-ball

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

有 adb 时可选：`adb logcat | grep -E "backend=|AudioTrack init failed|wav_rate="`，与屏显应一致。

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
# 真机(可选,屏显已能确诊): adb logcat | grep -E "backend=|AudioTrack init failed|wav_rate="
```
- 图标/物理验证见 `ICON.md`、`../readme.md`、`../CLAUDE.md`。

## 5. 交接给下一位的 first action

§2 的音频问题已修两层，等装机确认。**装机后第一件事：打开音效弹窗看底部**——
没有红字即硬件循环生效；出现红字则错误文本直接指向下一个失败点。

排查任何"改了没效果"的问题时，先照 §2 的教训确认那段代码真的执行了（README 经验教训
「静默 fallback 会让后续所有修复打空」），再怀疑参数。
