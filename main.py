"""
跳跳的沙漏 — Android (Kivy) 版
====================================
从 pc/hourglass_v2.py(tkinter + PIL 真圆 + 完整球 + 球体积微积分 + v2 布局)
**从零重写**为 Kivy。不复用 android 旧 main.py。

核心移植决策:
- 渲染: 玻璃壳和沙体都用 Kivy 真圆 —— Ellipse 画球, Stencil 裁出弓形沙面。
  复刻 pc"玻璃和沙必须同一种真圆技术,否则边缘失配(月牙/缝)"的核心原则。
  **不用 Mesh / 多边形拼弓形**(那是 pc readme 明令禁止、也是边缘失配的根源)。
- 坐标: Kivy y 向上(原点左下), pc 是 y 向下 —— 所有几何上下翻转。
- 几何: 自适应 widget 尺寸; 在 pc 的 380x730 比例下复现 R≈168。
- 守恒: 上沙(1-raw) + 下沙(raw) = 1, 由完整球对称 v(t)+v(1-t)=1 严格成立。
"""
import math
import os
import random
import struct
from array import array
import sys
import time
import wave
import json


# 沙流粒子的并行数组字段(见 NUMPY_PLAN.md)。numpy 缺失时整条向量化路径关闭,
# 自动退回 update_particles 里的原标量循环 —— 不给沙漏制造风险。
_P_FIELDS = ("px", "py", "pvy", "pxo", "pwp", "pwa", "psz", "ptl", "pli", "pdt")
# 向量化的固定开销(每个桶一次 np.array/argsort/bincount, 每次 numpy 调用 ~5-20µs)
# 在粒子少时会盖过 O(pn) 循环省下的时间。设备实测: 1 秒档(约 278 颗)打包+分组
# 反而慢 0.92ms, 5 秒档(约 1695 颗)才转正。阈值取两者之间, 低于它走原标量路径。
_NUMPY_MIN = 800


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


_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
try:
    import numpy as _np
    import flow_numpy as _flow_numpy
except Exception as _exc:                    # 别静默: 退回标量循环时要能从 logcat 看出来
    _np = None
    _flow_numpy = None
    print("numpy flow core unavailable, using scalar loop: %r" % (_exc,))

import gc

from app_version import APP_VERSION

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase, Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics import (Color, Rectangle, Line, Ellipse, Quad,
                           StencilPush, StencilUse, StencilUnUse, StencilPop,
                           PushMatrix, PopMatrix, Rotate, InstructionGroup)
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform
from frame_benchmark import (BenchmarkHoldArea, BenchmarkRunner, PERIODS,
                             BenchmarkFrameChart,
                             format_benchmark_result, format_benchmark_report,
                             save_benchmark_log)


# ---- 中文字体: 用 name="Roboto" 覆盖 Kivy 默认字体,全局生效 ----
_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fonts", "NotoSansSC-Medium.otf")
try:
    if os.path.exists(_FONT_PATH):
        LabelBase.register(name="Roboto", fn_regular=_FONT_PATH,
                           fn_bold=_FONT_PATH)
except Exception:
    pass


SAND_PRESETS = [
    ("金沙", "#d9a360", "#b88040", "#e6b870"),
    ("红沙", "#c4523e", "#8e3220", "#d97560"),
    ("蓝沙", "#4a8ec4", "#2e5e87", "#6dabd4"),
    ("绿沙", "#7ba83e", "#4d7820", "#97c45e"),
    ("紫沙", "#8e6db0", "#5d4280", "#a98ac4"),
    ("黑沙", "#4a4540", "#2a2520", "#6a6560"),
]
BG_COLOR = "#fdf6e3"
GLASS_FILL = "#eaf3f8"
GLASS_OUTLINE = "#5f6b70"

# ---- 周期弹窗独立配色(暖金/沙色系) ----
POPUP_BG = "#faf5eb"                              # 弹窗底色,暖白
POPUP_GOLD_SEL = (0.792, 0.643, 0.314, 1)         # 选中:暖金 #caa450
POPUP_UNSEL_BASE = (0.659, 0.565, 0.471, 1)       # 未选(基础):暖棕 #a89078
POPUP_UNSEL_MULT = (0.722, 0.627, 0.533, 1)       # 未选(倍数):浅棕 #b8a088
POPUP_CANCEL_BG = (0.847, 0.824, 0.792, 1)        # 取消:暖灰(比未选亮,表示次要操作)
POPUP_CONFIRM = (0.62, 0.23, 0.16, 1)              # 「确定」:暗红(与选中金色区分,强调确认操作)
POPUP_TEXT = (0.20, 0.14, 0.08, 1)                # 深咖啡文字
POPUP_TEXT_SUB = (0.45, 0.38, 0.30, 1)            # 次级说明文字(浅咖啡)
POPUP_TEXT_WHITE = (1, 1, 1, 1)

# 球↔管的曲线收窄过渡(纯渲染,不参与体积/守恒计算;移植自 pc v4)
TAPER_K = 2.2        # 过渡段上端半宽 = K × neck_w, 该点落在球壁上
TAPER_FILL = 0.62    # 过渡段吃掉"球截口→颈中心"竖直空间的比例, 其余留作直筒
TAPER_SEGS = 10      # 过渡曲线采样段数
NECK_FILL = 0.25     # 颈部沙柱注满耗时(秒), 避免起跑瞬间"啪"地从空变满


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


FALL_DELAY = 1.0          # 沙子飞到底的延迟(秒),下沙堆出现与粒子到底同步
MOUND_APPEAR = 0.5        # 下沙堆出现后平滑渐显时长
MOUND_FLOOR_MIN = 2.5     # 前期极小可见保底(dp),仅防薄层消失,不拔高
MOUND_FLOOR_MAX = 3.5
MOUND_FLOOR_EFF = 0.02

# (1.39 回滚) 这里曾有 JET_VENA / JET_DIFFUSE / JET_SPREAD / JET_EDGE / JET_WAVE_K ——
# 出口以下的"vena contracta 收腰 + 沿深度相干摆动"。它把颈部射流做成了一根**静止的
# 波纹管**: 摆动只随 y 变化(空间函数, 不是时间函数), 同一根 zigzag 每帧钉在同一批行上,
# 读起来像被模具挤出来的绳结, 不像会流动的沙。而且它从未进过任何一轮对抗审查, 验收用的
# "去趋势残差"对"加抖动"单调递增 —— 缺陷和指标是同一个东西(事故报告: NECK_REDESIGN §八)。
# 出口以下已恢复恒宽 tube_lim。

DUST_COUNT = 25
DUST_LIFETIME = 1.0
FLASH_DURATION = 0.35

WAV_FILENAME = "sand_loop.wav"

# ---- 音效库(名字两版逐字一致,配置持久化按名字存) ----
SOUND_EFFECTS = [
    ("沙沙声", "sand_loop.wav"),
    ("水流声", "sounds/water.wav"),
    ("风声", "sounds/wind.wav"),
    ("钟表声", "sounds/clock.wav"),
]
SILENT_NAME = "无声音"
# 弹窗选项/配置值域 = 4 个实音效 + 无声音(None 路径仅表示静音,不建 proxy)
SOUND_OPTIONS = SOUND_EFFECTS + [(SILENT_NAME, None)]


def hex_rgb(h):
    return (int(h[1:3], 16) / 255.0,
            int(h[3:5], 16) / 255.0,
            int(h[5:7], 16) / 255.0)


def lerp_rgb(c1, c2, t):
    return (c1[0] + (c2[0] - c1[0]) * t,
            c1[1] + (c2[1] - c1[1]) * t,
            c1[2] + (c2[2] - c1[2]) * t)


def fg_for(hex_color):
    r, g, b = hex_rgb(hex_color)
    return (0, 0, 0, 1) if (r * 0.299 + g * 0.587 + b * 0.114) > 0.59 else (1, 1, 1, 1)


def resource_path(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def config_path():
    if platform == "android":
        try:
            return os.path.join(App.get_running_app().user_data_dir,
                                ".hourglass_config.json")
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), ".hourglass_config.json")


def _fmt_duration(sec):
    """周期显示:整分钟/整小时不加小数,非整数加一位(100 秒 → 1.7 分钟)。"""
    if sec < 60:
        return f"{sec:.0f} 秒"
    if sec < 3600:
        m = sec / 60
        if abs(m - round(m)) < 1e-9:
            return f"{m:.0f} 分钟"
        return f"{m:.1f} 分钟"
    h = sec / 3600
    if abs(h - round(h)) < 1e-9:
        return f"{h:.0f} 小时"
    return f"{h:.1f} 小时"


MULT_SLIDER_MAX = 600         # 对数滑杆上限(倍)
MAX_DURATION = 360000         # 时长上限(秒)= 10分钟×600 = 100 小时


def _mult_from_slider(t):
    """滑杆位置 t∈[0,1] → 倍率:单段对数 MULT_SLIDER_MAX^t,向上取整为整数。
    t=0→1,t=0.5→√上限≈25,t=1→上限,无右侧死区。"""
    m = int(math.ceil((MULT_SLIDER_MAX ** max(0.0, min(1.0, t))) - 1e-9))
    return max(1, min(MULT_SLIDER_MAX, m))


def _fmt_duration_cn(sec):
    """完成弹窗里的时长("1 小时 30 秒"),零分量省略。"""
    total = max(0, int(round(sec)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} 小时")
    if minutes:
        parts.append(f"{minutes} 分")
    if secs or not parts:
        parts.append(f"{secs} 秒")
    return " ".join(parts)


def _fmt_countdown_pair(remaining, total):
    """倒计时显示,格式由总时长决定:H:MM:SS / M:SS / 秒。"""
    tot = int(round(total))
    rem = max(0, math.ceil(remaining))
    if tot >= 3600:
        fmt = lambda s: f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    elif tot >= 60:
        fmt = lambda s: f"{s // 60}:{s % 60:02d}"
    else:
        fmt = lambda s: f"{s:.0f}"
    return f"{fmt(rem)} / {fmt(tot)}"


BASE_PERIODS = [
    ("1 秒", 1),
    ("10 秒", 10),
    ("1 分钟", 60),
    ("10 分钟", 600),
]


# ---------- 横屏方向策略(移植自 hengping.md §3; 反旋转层 LandLayer 的配套) ----------

_DEVICE_WIDE_MIN = 9.0 / 16.0    # 0.5625: 短边/长边 ≥ 9:16 判宽屏(平板),小于判瘦长机
_device_wide_cache = None        # 开机/量一次,之后不再变
_LAYER = None                    # 当前反旋转层实例(唯一,供 _land_layer / 弹窗 remount 取用)


def _device_is_wide():
    """物理屏比例分流: 宽屏(平板类)→允许横转+反旋转; 瘦长机→锁竖屏。"""
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


def _land_angle():
    """读系统旋转角决定反旋转画几度: ROTATION_270(3)→-90, 其余→+90。只在横屏被调。"""
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        wm = act.getWindowManager()
        disp = wm.getDefaultDisplay()
        return -90 if disp.getRotation() == 3 else 90
    except Exception:
        return 90


def _land_layer():
    return _LAYER


# ---------- 居中输入框(Kivy TextInput 无 text_align,手动算 padding) ----------

class CenterTextInput(TextInput):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(text=self._refresh_pad, size=self._refresh_pad,
                  font_size=self._refresh_pad)
        Clock.schedule_once(self._refresh_pad, 0)

    def _refresh_pad(self, *_):
        try:
            cl = CoreLabel(text=self.text or "0", font_size=self.font_size,
                           font_name=self.font_name or "Roboto")
            cl.refresh()
            tw, th = cl.content_size
        except Exception:
            tw, th = 0, 0
        ph = max(2, (self.width - tw) / 2)
        pv = max(2, (self.height - th) / 2)
        self.padding = [ph, pv, ph, pv]


# ---------- 音效: Android AudioTrack 硬件循环; Windows winsound; 桌面 fallback ----------

class _SoundProxy:
    """Android: AudioTrack MODE_STATIC($Builder) + getState校验 + reloadStaticData
    三星设备兼容; Windows: winsound SND_LOOP; 其他桌面/Android失败: Kivy SoundLoader。"""

    def __init__(self, wav_path, loop=True):
        self._is_android = (platform == "android")
        self._is_windows = (platform == "win")
        self._audio_track = None
        self._kivy_sound = None
        self._winsound = None
        self._wav_path = wav_path
        self._loop = loop
        self._active = False
        self._needs_reload = False
        self._loop_frames = 0
        self.backend = None
        self.error = ""      # 后端选择失败原因(只进 logcat, 界面不显示)

        if self._is_android:
            try:
                self._init_audio_track(wav_path)
                if self._audio_track is not None:
                    self.backend = "audiotrack"
                    print("backend=audiotrack")
                    return
                self.error = "AudioTrack: no track"
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:70]
                print(f"AudioTrack init failed (fallback): {e}")
                self._audio_track = None
        if self._is_windows:
            try:
                import winsound
                self._winsound = winsound
                self.backend = "winsound"
                print("backend=winsound")
                return
            except Exception:
                self._winsound = None
        try:
            from kivy.core.audio import SoundLoader
            self._kivy_sound = SoundLoader.load(wav_path)
            if self._kivy_sound is not None:
                self._kivy_sound.loop = self._loop
            else:
                self.error = self.error or "SoundLoader.load returned None"
            self.backend = "soundloader"
            print("backend=soundloader")
        except Exception as e:
            self._kivy_sound = None
            self.backend = "none"
            self.error = f"{type(e).__name__}: {e}"[:70]

    def _init_audio_track(self, wav_path):
        # ⚠️ pyjnius **没有** jarray —— 早期版本写成 `from jnius import autoclass, jarray`,
        # 这一行直接 ImportError, 整个 AudioTrack 路径从未执行过一次(静默回退 SoundLoader,
        # 循环点每 15s 卡一次)。byte[] 参数直接传 Python bytes 即可, 见下方 write 处注释。
        from jnius import autoclass

        # 解析 WAV 头 + 提取 PCM 裸数据
        with open(wav_path, 'rb') as f:
            data = f.read()
        if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
            raise ValueError("Not a WAV file")
        sample_rate = int.from_bytes(data[24:28], 'little')
        channels = int.from_bytes(data[22:24], 'little')
        bits = int.from_bytes(data[34:36], 'little')
        if bits != 16:
            raise ValueError(f"Only 16bit WAV supported, got {bits}")
        # 遍历 chunk 找 data(WAV chunk 2 字节对齐:奇数 size 补 1 字节)
        pcm = None
        idx = 12
        while idx + 8 <= len(data):
            chunk_id = data[idx:idx+4]
            chunk_size = int.from_bytes(data[idx+4:idx+8], 'little')
            if chunk_id == b'data':
                pcm = data[idx+8:idx+8+chunk_size]
                break
            idx += 8 + chunk_size + (chunk_size & 1)  # 奇数对齐
        if pcm is None:
            raise ValueError("No data chunk in WAV")

        # 设备原生输出率(仅诊断用)。**不做重采样**: AudioFlinger 的 SRC 挂在 track 流上,
        # loop 在更上游解析成连续重复流, 不会在循环点重置相位; 而纯 Python 逐样本重采样
        # 72 万样本跑在 UI 线程上会冻屏数秒。采样率不一致交给系统 SRC 即可。
        AudioManager = autoclass('android.media.AudioManager')
        AudioTrack = autoclass('android.media.AudioTrack')
        try:
            native_rate = int(AudioTrack.getNativeOutputSampleRate(AudioManager.STREAM_MUSIC))
        except Exception:
            native_rate = sample_rate
        print(f"_init_audio_track wav_rate={sample_rate} native_rate={native_rate}")

        AudioFormat = autoclass('android.media.AudioFormat')
        channel_out = (AudioFormat.CHANNEL_OUT_STEREO if channels == 2
                       else AudioFormat.CHANNEL_OUT_MONO)

        # 方法1: $Builder(兼容 API 23+, pyjnius 需 $ 符号访问嵌套类)
        track = None
        try:
            ATBuilder = autoclass('android.media.AudioTrack$Builder')
            AABuilder = autoclass('android.media.AudioAttributes$Builder')
            AFBuilder = autoclass('android.media.AudioFormat$Builder')
            AudioAttributes = autoclass('android.media.AudioAttributes')

            attrs = (AABuilder()
                     .setUsage(AudioAttributes.USAGE_MEDIA)
                     .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                     .build())
            fmt = (AFBuilder()
                   .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                   .setSampleRate(sample_rate)
                   .setChannelMask(channel_out)
                   .build())
            track = (ATBuilder()
                     .setAudioAttributes(attrs)
                     .setAudioFormat(fmt)
                     .setBufferSizeInBytes(len(pcm))
                     .setTransferMode(AudioTrack.MODE_STATIC)
                     .build())
        except Exception as e:
            print(f"Builder init failed: {e}, trying legacy constructor")

        # 方法2: 传统构造函数(API 3+, 某些设备 Builder 不可用时兜底)
        if track is None:
            try:
                track = AudioTrack(
                    AudioManager.STREAM_MUSIC,
                    sample_rate,
                    channel_out,
                    AudioFormat.ENCODING_PCM_16BIT,
                    len(pcm),
                    AudioTrack.MODE_STATIC)
            except Exception as e:
                raise ValueError(f"All AudioTrack constructors failed: {e}")

        # ⚠️ MODE_STATIC 的 track 在**写入数据前**状态必然是 STATE_NO_STATIC_DATA(2) ——
        # 官方定义:"已成功初始化、使用静态数据、但还没收到那份数据的 AudioTrack"。
        # 写完 PCM 才会转成 STATE_INITIALIZED(1)。所以这里拿 ==INITIALIZED 当前置校验
        # 是**永远不可能通过**的(历史 bug: 真机报 state=2 → 抛异常 → 静默回退 MediaPlayer)。
        # 正确做法:写前接受 {INITIALIZED, NO_STATIC_DATA},写后再确认 == INITIALIZED。
        STATE_NO_STATIC_DATA = getattr(AudioTrack, 'STATE_NO_STATIC_DATA', 2)
        state = track.getState()
        if state not in (AudioTrack.STATE_INITIALIZED, STATE_NO_STATIC_DATA):
            raise ValueError(
                f"AudioTrack ctor state={state}, "
                f"expected {AudioTrack.STATE_INITIALIZED} or {STATE_NO_STATIC_DATA}")

        # 写入 PCM 数据。pyjnius 对 '[B' 参数的 bytes/bytearray 有专门支持:
        #   calculate_score  → '[B' 遇 bytes +10 分, '[S' 遇 bytes 直接 -1
        #                      ⟹ 三参调用确定性命中 write(byte[],int,int), 不会误选 short[]
        #   convert_pyarray_to_java → bytes 走单次 SetByteArrayRegion 整块拷贝(列表才是慢路径)
        # ⚠️ MODE_STATIC 的 native writeToTrack() 每次 write 都 memcpy 到共享缓冲**起始处**,
        #    分块写会让最后一块落到 offset 0 —— 必须一次写完。
        if not isinstance(pcm, (bytes, bytearray)):
            pcm = bytes(pcm)
        written = track.write(pcm, 0, len(pcm))
        if written != len(pcm):
            raise ValueError(
                f"AudioTrack.write incomplete: {written}/{len(pcm)} bytes")

        # 写完数据后才该是 INITIALIZED(见上面的状态说明)
        state = track.getState()
        if state != AudioTrack.STATE_INITIALIZED:
            raise ValueError(
                f"AudioTrack not initialized after write: state={state}, "
                f"expected={AudioTrack.STATE_INITIALIZED}")

        # 硬件循环点
        frame_size = channels * (bits // 8)
        total_frames = len(pcm) // frame_size
        result = track.setLoopPoints(0, total_frames, -1 if self._loop else 0)
        if result != 0:  # SUCCESS = 0
            raise ValueError(f"setLoopPoints failed: {result}")

        self._loop_frames = total_frames
        self._audio_track = track

    def play(self):
        if self._audio_track is not None:
            if self._active:
                return
            try:
                # 重播时才 reloadStaticData(三星兼容),首次播放直接 play
                if self._needs_reload:
                    try:
                        self._audio_track.reloadStaticData()
                        self._needs_reload = False
                    except Exception:
                        self._needs_reload = True   # 失败保留, 下次重试(避免PLAYING时误reload竞态)
                self._audio_track.setPlaybackHeadPosition(0)
                # native 层 reload()/setPosition() 会清掉 loop 状态(setLoop(...,0)),
                # 每次播放前重新武装, 否则"停止再播"只响一遍就没了
                if self._loop_frames:
                    self._audio_track.setLoopPoints(
                        0, self._loop_frames, -1 if self._loop else 0)
                self._audio_track.play()
                self._active = True  # 成功后置,防异常后半永久静音
            except Exception:
                pass
            return
        if self._winsound is not None:
            if self._active:
                return
            self._active = True
            try:
                flags = self._winsound.SND_ASYNC | self._winsound.SND_FILENAME
                if self._loop:
                    flags |= self._winsound.SND_LOOP
                self._winsound.PlaySound(self._wav_path, flags)
            except Exception:
                self._active = False
            return
        if self._kivy_sound is not None:
            try:
                if self._kivy_sound.state != "play":
                    self._kivy_sound.play()
            except Exception:
                pass

    def stop(self):
        if self._audio_track is not None:
            if not self._active:
                return
            self._active = False
            self._needs_reload = True
            try:
                self._audio_track.pause()
                self._audio_track.flush()
                self._audio_track.stop()
            except Exception:
                pass
            return
        if self._winsound is not None:
            if not self._active:
                return
            self._active = False
            try:
                self._winsound.PlaySound(None, self._winsound.SND_PURGE)
            except Exception:
                pass
            return
        if self._kivy_sound is not None:
            try:
                self._kivy_sound.stop()
            except Exception:
                pass

    def close(self):
        """释放底层后端资源。切换音效时在 stop() 后调用,防 AudioTrack 句柄泄漏。"""
        if self._audio_track is not None:
            try:
                self._audio_track.release()
            except Exception:
                pass
            self._audio_track = None
            return
        if self._kivy_sound is not None:
            try:
                self._kivy_sound.stop()
                self._kivy_sound.unload()
            except Exception:
                pass
            self._kivy_sound = None


def _read_wav_pcm(path):
    """读 WAV -> (裸 PCM bytes, 采样率, 声道)。只支持 16bit,失败抛异常。"""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE" or len(data) < 36:
        raise ValueError("Not a WAV file")
    rate = int.from_bytes(data[24:28], "little")
    channels = int.from_bytes(data[22:24], "little")
    if int.from_bytes(data[34:36], "little") != 16:
        raise ValueError("Only 16bit WAV supported")
    idx = 12
    while idx + 8 <= len(data):
        size = int.from_bytes(data[idx + 4:idx + 8], "little")
        if data[idx:idx + 4] == b"data":
            return data[idx + 8:idx + 8 + size], rate, channels
        idx += 8 + size + (size & 1)
    raise ValueError("No data chunk in WAV")


_CHIME_CACHE = {}


def _completion_chime(rate):
    """播报前的短钟声(C 大三和弦琶音, 与 tools/generate_completion_voice.py 同配方)。

    现场合成而不是烘成文件:只需要 0.55s 的正弦叠加,还免掉一个 wav 进 APK。
    合成是纯 Python 三重循环(~1.3 万帧),必须缓存 —— 否则每次流尽都在 UI 线程
    上现算,正好叠在完成闪烁那一帧上。
    """
    cached = _CHIME_CACHE.get(rate)
    if cached is not None:
        return cached
    notes = ((523.25, 0.0), (659.25, 0.10), (783.99, 0.20))
    samples = []
    for i in range(int(rate * 0.55)):
        t = i / rate
        value = 0.0
        for frequency, start in notes:
            age = t - start
            if age >= 0:
                envelope = min(1.0, age / 0.012) * math.exp(-age * 12)
                value += math.sin(math.tau * frequency * age) * envelope * 0.075
        samples.append(round(value * min(1.0, (0.55 - t) / 0.05) * 32767))
    pcm = struct.pack(f"<{len(samples)}h", *samples) + b"\0\0" * round(rate * 0.12)
    _CHIME_CACHE[rate] = pcm
    return pcm


class _VoiceBank:
    """预录词块 -> 完成播报 PCM 拼接。

    完成语随周期变化("两小时三十分零五秒的沙漏计时完成"),无法预录成一条。这里把
    可能出现的每个词烘成一个小 wav,播报时按需拼成**一段** PCM 再交给 AudioTrack ——
    一次播放、无接缝、不占第二个通道(winsound 单通道,分段播会被下段掐掉)。

    词块缺失或采样率不一致 -> ok=False,调用方回退到预录的整句 completion.wav。
    """

    def __init__(self, root):
        self.rate = 0
        self.clips = {}
        self.ok = False
        path = os.path.join(root, "sounds", "voice")
        if not os.path.isdir(path):
            print("Voice tokens missing: sounds/voice/")
            return
        try:
            for name in sorted(os.listdir(path)):
                if not name.endswith(".wav"):
                    continue
                pcm, rate, channels = _read_wav_pcm(os.path.join(path, name))
                if channels != 1:
                    raise ValueError(f"{name}: expected mono, got {channels}")
                if self.rate and rate != self.rate:
                    raise ValueError(f"{name}: rate {rate} != {self.rate}")
                self.rate = rate
                self.clips[name[:-4]] = pcm
        except Exception as exc:
            print(f"Voice token load failed: {exc}")
            self.clips, self.rate = {}, 0
            return
        missing = [k for k in ("n0", "n1", "n60", "d1", "ten", "hundred",
                               "hour", "min", "sec", "tail") if k not in self.clips]
        if missing:
            print(f"Voice tokens incomplete, missing: {', '.join(missing)}")
            self.clips, self.rate = {}, 0
            return
        self.ok = True
        print(f"voice bank: {len(self.clips)} tokens @ {self.rate}Hz")

    def _hour_keys(self, hours):
        """1..100 小时:1-60 用整词,61-99 拆成 六十/一,100 = 一百。"""
        if hours <= 60:
            return [f"n{hours}"]
        if hours == 100:
            return ["d1", "hundred"]
        tens, ones = divmod(hours, 10)
        keys = [f"d{tens}", "ten"]
        if ones:
            keys.append(f"d{ones}")
        return keys

    def sentence_keys(self, seconds):
        """拼出"X小时Y分Z秒的沙漏计时完成"的词块序列(零分量省略,中间夹"零")。"""
        total = max(0, int(round(seconds)))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        keys = []
        if hours:
            keys += self._hour_keys(hours) + ["hour"]
        if minutes:
            keys += [f"n{minutes}", "min"]
        elif hours and secs:
            keys.append("n0")               # "一小时零三十秒"
        if secs:
            keys += [f"n{secs}", "sec"]
        if not keys:
            keys += ["n0"]
        return keys + ["tail"]

    def build_pcm(self, seconds):
        return b"".join(self.clips[k] for k in self.sentence_keys(seconds))


class _SandBgPopup(Popup):
    """浅色背景 Popup,覆盖 Kivy 默认深灰风格(双层兜底)"""
    def __init__(self, bg_hex=POPUP_BG, **kwargs):
        self._bg_hex = bg_hex
        self._bg_rgb = hex_rgb(bg_hex)
        kwargs.setdefault('separator_color', (*POPUP_GOLD_SEL[:3], 0.3))
        kwargs.setdefault('title_color', (1, 1, 1, 1))
        kwargs.setdefault('title_align', 'center')
        super().__init__(**kwargs)
        # 兜底层: Popup 本体 canvas.before(填充容器外间隙)
        with self.canvas.before:
            Color(*self._bg_rgb, 1)
            self._popup_bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._upd_popup_bg, size=self._upd_popup_bg)

    def _upd_popup_bg(self, inst, _value):
        self._popup_bg.pos = inst.pos
        self._popup_bg.size = inst.size

    def open(self, *args, **kwargs):
        layer = _land_layer()
        if layer is None or layer.angle == 0:
            super().open(*args, **kwargs)          # 竖屏回落原生行为(零回归)
            Clock.schedule_once(self._apply_light_theme, 0)
            return
        # 横屏: 不再把弹窗挂 Window(Window 不旋转), 改挂旋转层让其随层旋转成竖构图。
        # 弹窗用 size_hint=(0.88, None), 直接挂层会按"物理长边×0.88"放大溢出, 故按
        # "等效竖屏窗宽=短边"换算显式 size, 再整体旋转 → 视觉与竖屏一致。
        if self._is_open:
            return
        eq_w = min(Window.width, Window.height)    # 等效竖屏窗宽=短边
        frac = None
        if self.size_hint and self.size_hint[0] is not None:
            frac = self.size_hint[0]
        else:
            frac = 0.88
        self.size_hint = (None, None)
        self.size = (frac * eq_w, self.height)
        self._window = layer                        # 宿主从 Window 换成旋转层
        self._is_open = True
        self.dispatch('on_pre_open')
        if not self.pos_hint:
            self.pos_hint = {"center_x": 0.5, "center_y": 0.5}
        layer.add_widget(self)                      # 挂到层而非 Window
        layer.bind(on_resize=self._align_center, on_keyboard=self._handle_keyboard)
        self.center = layer.center
        self.fbind('center', self._align_center)
        self.fbind('size', self._align_center)
        self._anim_alpha = 1.
        self.dispatch('on_open')
        Clock.schedule_once(self._apply_light_theme, 0)

    def _real_remove_widget(self):
        """覆写: dismiss 时从旋转层对称摘除(非横屏时 host=Window, 行为等同原生)。"""
        if not self._is_open:
            return
        self._window.remove_widget(self)
        try:
            self._window.unbind(on_resize=self._align_center,
                                on_keyboard=self._handle_keyboard)
        except Exception:
            pass
        self._is_open = False
        self._window = None

    def _apply_light_theme(self, *_):
        """清空 _container 深色背景,替换为浅色"""
        try:
            container = self.content.parent
            container.canvas.before.clear()
            with container.canvas.before:
                Color(*self._bg_rgb, 1)
                self._ctr_bg = Rectangle(pos=container.pos, size=container.size)
            container.bind(pos=self._upd_ctr_bg, size=self._upd_ctr_bg)
        except Exception:
            pass

    def _upd_ctr_bg(self, inst, _value):
        self._ctr_bg.pos = inst.pos
        self._ctr_bg.size = inst.size


# ---------- 横屏反旋转层(移植自 hengping.md §4; 竖屏 angle=0 时行为等同无此层) ----------

class LandLayer(FloatLayout):
    """横屏时把"等效竖屏窗口"整体旋转 ±90° 铺满横窗; 竖屏 angle=0 零回归。
    结构: LandLayer(旋转层, 铺满整窗) → anchor(AnchorLayout, 等效竖屏盒) → 实际 UI(root)。
    """
    def __init__(self, **kw):
        super().__init__(**kw)
        self.angle = 0            # 渲染旋转角: 0=竖屏无旋转, 其他=±90
        self._anchor = None       # "等效竖屏窗口"容器(AnchorLayout), 由 build 塞入
        with self.canvas.before:
            PushMatrix()
            self._rot = Rotate(angle=0, axis=(0, 0, 1), origin=(0, 0))
        with self.canvas.after:
            PopMatrix()

    def apply_orientation(self):
        """窗口尺寸变化时重算: 层永远铺满整窗, anchor 按"等效竖屏盒"定尺寸并绕屏中心旋转。"""
        w, h = Window.width, Window.height
        land = (w > h and (platform == "android" or "--landscape" in sys.argv)
                and _device_is_wide())
        self.pos = (0, 0)
        self.size = (w, h)                          # 层永远铺满
        self.angle = _land_angle() if land else 0
        self._rot.angle = self.angle
        self._rot.origin = (w / 2.0, h / 2.0)       # 绕屏幕中心旋转
        if self._anchor is not None:
            self._anchor.size = (h, w) if land else (w, h)
            self._anchor.center = self.center
        return land

    def _to_eq(self, x, y):
        """屏幕(物理)坐标 → 等效竖屏(逻辑)坐标; 逆变换, 触摸分发用。"""
        if self.angle == 0:
            return (x, y)
        cx, cy = Window.width / 2.0, Window.height / 2.0
        dx, dy = x - cx, y - cy
        if self.angle == 90:
            return (cx + dy, cy - dx)               # 逆时针旋转 → 顺时针转回
        return (cx - dy, cy + dx)                   # angle == -90

    def _to_win(self, x, y):
        """等效竖屏(逻辑)坐标 → 屏幕(物理)坐标; 正变换, to_parent 用。两者互逆。"""
        if self.angle == 0:
            return (x, y)
        cx, cy = Window.width / 2.0, Window.height / 2.0
        dx, dy = x - cx, y - cy
        if self.angle == 90:
            return (cx - dy, cy + dx)               # 正变换 = 逆时针 90
        return (cx + dy, cy - dx)                   # angle == -90

    # grab 后的 move/up 不经 on_touch_*, 而是 Window 沿祖先链调 to_local 换算 → 必须覆写
    def to_local(self, x, y, **k):
        return self._to_eq(x, y)

    def to_parent(self, x, y, **k):
        return self._to_win(x, y)

    def _pass_touch(self, method, touch):
        if self.angle == 0:
            return method(touch)                    # 竖屏直接透传, 零回归
        touch.push()
        touch.apply_transform_2d(self._to_eq)       # 逆旋转, 把 x/y/pos/ox/oy/px/py 一起变
        ret = method(touch)
        touch.pop()
        return ret

    def on_touch_down(self, touch):
        return self._pass_touch(super().on_touch_down, touch)

    def on_touch_move(self, touch):
        return self._pass_touch(super().on_touch_move, touch)

    def on_touch_up(self, touch):
        return self._pass_touch(super().on_touch_up, touch)


# ---------- 沙漏画布 ----------

class HourglassWidget(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.duration = 50.0
        self.elapsed = 0.0
        self.running = False
        self.last_tick = None
        self.last_frame = time.perf_counter()

        self.sand_base = hex_rgb(SAND_PRESETS[0][1])
        self.sand_dark = hex_rgb(SAND_PRESETS[0][2])
        self.sand_light = hex_rgb(SAND_PRESETS[0][3])
        self._color_table = []
        self._rebuild_color_table()

        self._geom_ready = False
        self._vol_to_height = []

        self.pn = 0
        self._p_alloc(2048)
        # 渲染层的读端(分组/打包/颈部颗粒)按**下标**读这份 list 快照, 见 _p_refresh_view。
        self._pv = _FlowView()
        self._p_dict_cache = None
        self.particles = []
        self.particle_acc = 0.0
        self.splashes = []
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        self.flash_end = 0.0
        self._completion_triggered = False

        self.sound_name = "沙沙声"
        self._sound = self._make_sound_proxy(self.sound_name)
        self.completion_enabled = True
        self._completion_sound = self._make_completion_sound()
        self._voice_bank = _VoiceBank(resource_path(""))
        self._completion_spoken = None      # 动态拼出的播报(每条周期重建一次)
        if self._voice_bank.ok:
            _completion_chime(self._voice_bank.rate)   # 预热,别让首播卡在完成那一帧

        self.bind(size=self._on_size, pos=self._on_size)
        Clock.schedule_once(self._on_size, 0)
        Clock.schedule_interval(self.tick, 0)

    # ---------- 几何(自适应; Kivy y 向上) ----------

    def _on_size(self, *_):
        self._rebuild_height_table()

    @property
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

    @property
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

    def get_remaining(self):
        return max(0.0, 1 - self.elapsed / self.duration) if self.duration > 0 else 0

    @property
    def _neck_fill_time(self):
        return max(1e-6, min(NECK_FILL, self.duration * 0.15))

    @property
    def _natural_flight_time(self):
        if not self._geom_ready:
            return FALL_DELAY
        outlet = 2 * self._neck_y - self._taper['y_bot']
        distance = max(0, outlet - self._lower_sand_bot)
        return (math.sqrt(50 ** 2 + 900 * distance) - 50) / 450

    @property
    def _fall_delay(self):
        return min(self._neck_fill_time + self._natural_flight_time + 0.05,
                   self.duration * 0.45)

    @property
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

    def get_mound_top_y(self):
        """碰撞面与实际绘制的水平沙面一致,不使用未绘制的堆尖。"""
        return self._lower_sand_bot + self._mound_height_px()

    def _sand_half_w(self, y, yc):
        Ri = self._R_inner
        return math.sqrt(max(0.0, Ri * Ri - (y - yc) ** 2))

    # ---------- 控制 ----------

    def toggle(self):
        if self.running:
            self.running = False
            self._stop_sound()
        else:
            self._stop_completion_sound()
            if self.elapsed >= self.duration:
                self.elapsed = 0
                self._reset_run_state()
            elif self.elapsed == 0:
                gc.collect()
            self.running = True
            self.last_tick = self.last_frame = time.perf_counter()
            self._play_sound()

    def reset(self):
        self.elapsed = 0
        self.running = False
        self.last_tick = None
        self._reset_run_state()
        self._stop_sound()

    def _reset_run_state(self):
        self._stop_completion_sound()
        self.pn = 0
        self._p_refresh_view()
        self.particle_acc = 0.0
        self.splashes = []
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        self.flash_end = 0.0
        self._completion_triggered = False
        # 旧场景的循环引用在重置时清理,避免留到流动中触发全量回收。
        gc.collect()

    def set_duration(self, d):
        try:
            d = float(d)
        except (TypeError, ValueError):
            return False
        if d <= 0:
            return False
        d = min(d, MAX_DURATION)
        if d == self.duration:
            return False
        self.duration = d
        self._rebuild_height_table()
        self.reset()
        return True

    def set_sand_color(self, base, dark, light):
        self.sand_base = hex_rgb(base)
        self.sand_dark = hex_rgb(dark)
        self.sand_light = hex_rgb(light)
        self._rebuild_color_table()

    def _rebuild_color_table(self):
        self._color_table = [lerp_rgb(self.sand_base, self.sand_light, i / 10.0)
                             for i in range(11)]

    def _make_sound_proxy(self, name):
        """按音效名新建 _SoundProxy(构造失败返回 None,不抛)。"""
        path = next((p for n, p in SOUND_EFFECTS if n == name),
                    SOUND_EFFECTS[0][1])
        try:
            return _SoundProxy(resource_path(path))
        except Exception as e:
            print(f"sound proxy init failed: {e}")
            return None

    def _make_completion_sound(self):
        path = resource_path("sounds/completion.wav")
        if not os.path.isfile(path):
            print("Completion recording missing: sounds/completion.wav")
            return None
        try:
            return _SoundProxy(path, loop=False)
        except Exception as exc:
            print(f"Completion audio init failed: {exc}")
            return None

    def _stop_completion_sound(self):
        if self._completion_sound is not None:
            self._completion_sound.stop()
        if self._completion_spoken is not None:
            self._completion_spoken.stop()

    def _play_completion_sound(self, duration=0.0):
        """播报"X小时Y分Z秒的沙漏计时完成"。词库缺失时回退预录整句。"""
        if not self.completion_enabled:
            return
        if self._voice_bank.ok and self._play_completion_announcement(duration):
            return
        if self._completion_sound is not None:
            self._completion_sound.stop()
            self._completion_sound.play()

    def _play_completion_announcement(self, duration):
        """把词块拼成一段 PCM 写盘 -> 交给 _SoundProxy 一次播完(无接缝)。

        必须走文件:_SoundProxy 的三个后端里有两条按路径播放(winsound 的
        SND_FILENAME、Kivy SoundLoader),只传裸 PCM 会漏掉它们。
        """
        try:
            bank = self._voice_bank
            pcm = _completion_chime(bank.rate) + bank.build_pcm(duration)
            path = os.path.join(os.path.dirname(config_path()),
                                "completion_announcement.wav")
            with wave.open(path, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(bank.rate)
                stream.writeframes(pcm)
        except Exception as exc:
            print(f"completion announcement failed: {exc}")
            return False
        if self._completion_spoken is not None:
            self._completion_spoken.stop()
            self._completion_spoken.close()
            self._completion_spoken = None
        try:
            proxy = _SoundProxy(path, loop=False)
        except Exception as exc:
            print(f"completion announcement audio init failed: {exc}")
            return False
        if proxy.backend == "none":
            return False
        self._completion_spoken = proxy
        proxy.play()
        return True

    def _set_sound(self, name):
        """切换音效(五步序):①先新建 proxy(失败→旧态原样,绝不静音)②停旧
        ③关旧(release)④挂新 ⑤运行中立即续播。同名幂等。返回是否实际切换。"""
        if name == self.sound_name:
            return False
        if name == SILENT_NAME:
            # 静音:停旧即可,不建 proxy(_play_sound 对 None 是空操作)
            if self._sound is not None:
                self._sound.stop()
                self._sound.close()
            self._sound = None
            self.sound_name = name
            return True
        if not any(n == name for n, _ in SOUND_EFFECTS):
            return False
        new_proxy = self._make_sound_proxy(name)
        if new_proxy is None:
            return False
        if self._sound is not None:
            self._sound.stop()          # winsound SND_PURGE 全局清场,必须先停旧
            self._sound.close()
        self._sound = new_proxy
        self.sound_name = name
        if self.running:
            self._play_sound()
        return True

    def _play_sound(self):
        if self._sound is not None:
            self._sound.play()

    def _stop_sound(self):
        if self._sound is not None:
            self._sound.stop()

    def sound_problem_desc(self):
        """**只有音频没走到无缝后端时**才返回一行提示,正常/静音一律返回空串。
        当初这行小字 30 秒定位了 `state=2`(STATE_NO_STATIC_DATA)那个 bug —— 兜底路径
        必须能把失败原因自己说出来,否则只能靠盲改 + 每轮 15 分钟的云端构建。
        audiotrack=Android 硬件循环; winsound=Windows 驱动层循环; 两者都无缝,不提示。"""
        if self._sound is None:
            return ""
        backend = getattr(self._sound, "backend", None) or "?"
        if backend in ("audiotrack", "winsound"):
            return ""
        err = getattr(self._sound, "error", "")
        return f"音频: {backend}" + (f" — {err}" if err else "")

    # ---------- 配置持久化 ----------

    def load_config(self):
        try:
            with open(config_path(), 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self, color_name):
        try:
            with open(config_path(), 'w', encoding='utf-8') as f:
                json.dump({'duration': self.duration, 'color_name': color_name,
                           'sound_name': self.sound_name}, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------- tick / 物理 ----------

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

    # ------------------------------------------------------------- 粒子存储(numpy)
    # 并行数组是真值; 渲染层的读端一律按**下标**读 `_pv`(本帧的 list 快照),
    # `particles` 只是给外部工具看的 dict 列表视图, 按需构建、不再每帧同步。
    # 迁移计划与消费方清单见 NUMPY_PLAN.md。
    @staticmethod
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

    @property
    def particles(self):
        """数组真相的 dict 列表视图 —— 只给外部工具读(inspect_flow / benchmark 快照)。

        渲染层不再用它, 所以每帧那 ~2500 个 dict 的构建已经删掉。这里按需构建并缓存:
        同一批数组状态下反复读拿到的是同一批对象(部分校验工具依赖 id 稳定),
        物理一推进(`_p_refresh_view`)就失效。
        """
        cached = self._p_dict_cache
        if cached is None:
            cached = self._p_to_dicts()
            self._p_dict_cache = cached
        return cached

    @particles.setter
    def particles(self, value):
        self._p_from_dicts(value)
        self._p_refresh_view()

    def _p_to_dicts(self, new_from=None):
        """数组 -> dict 列表。

        只有外部读者和 **numpy 缺席时的标量兜底路径**用它; 向量化路径每帧不再构建。

        `new_from`: 本帧新生的起始下标。标量兜底路径靠它把 `_step_dt`(帧内偏步长)
        带回去 —— 漏了的话新粒子会拿到整帧 dt, 落得更远、提前触底, 画面就变了。
        """
        pn = self.pn
        px = self.px
        py = self.py
        pvy = self.pvy
        pxo = self.pxo
        pwp = self.pwp
        pwa = self.pwa
        psz = self.psz
        ptl = self.ptl
        pli = self.pli
        pdt = self.pdt
        particles = []
        append = particles.append
        for i in range(pn):
            d = {
                "x": float(px[i]), "y": float(py[i]), "vy": float(pvy[i]),
                "x_offset": float(pxo[i]), "wobble_phase": float(pwp[i]),
                "wobble_amp": float(pwa[i]), "size": int(psz[i]),
                "trail_time": float(ptl[i]), "is_light": bool(pli[i]),
            }
            if new_from is not None and i >= new_from:
                d["_step_dt"] = float(pdt[i])
            append(d)
        return particles

    def _p_from_dicts(self, particles):
        """dict 列表 -> 数组。标量兜底路径与 `particles` 的写入端共用。

        缺字段按渲染层的既有默认值补(wobble 系列 0、trail_time 0.08), 让只写部分字段的
        外部工具仍能把粒子放进来; 兜底路径的 dict 由 `_p_to_dicts` 生成, 字段必然齐。
        """
        n = len(particles)
        self._p_grow(n)
        px = self.px
        py = self.py
        pvy = self.pvy
        pxo = self.pxo
        pwp = self.pwp
        pwa = self.pwa
        psz = self.psz
        ptl = self.ptl
        pli = self.pli
        for k in range(n):
            d = particles[k]
            px[k] = d.get("x", 0.0)
            py[k] = d.get("y", 0.0)
            pvy[k] = d.get("vy", 0.0)
            pxo[k] = d.get("x_offset", 0.0)
            pwp[k] = d.get("wobble_phase", 0.0)
            pwa[k] = d.get("wobble_amp", 0.0)
            psz[k] = 2 if d.get("size", 1) == 2 else 1
            ptl[k] = d.get("trail_time", 0.08)
            pli[k] = 1.0 if d.get("is_light", False) else 0.0
        self.pn = n

    def _replay_hits(self, hit_idx, hit_dt, mound_top, motion_scale, now):
        """按下标升序回放命中事件 —— 随机数调用顺序与原标量循环逐字一致。

        ⚠️ 三个随机数只在 splash 成立时才抽, 顺序:
           rand()<0.25 → rand()<0.50 → uniform(0.14,0.28) → uniform(-0.85,0.85)
           → choice([1,1,2])。
        """
        px = self.px
        pvy = self.pvy
        pdt = self.pdt
        rand = random.random
        rand_uniform = random.uniform
        rand_choice = random.choice
        sin = math.sin
        cos = math.cos
        append_flare = self.flares.append
        append_splash = self.splashes.append
        for k in range(len(hit_idx)):
            i = int(hit_idx[k])
            x = float(px[i])
            vy = float(pvy[i])
            step_dt = float(pdt[i])
            if rand() < 0.25:
                append_flare({"x": x, "y": mound_top, "end": now + 0.08})
            if rand() < 0.50:
                v = -vy
                bounce = min(110 * motion_scale,
                             (v if v > 0 else 0) * rand_uniform(0.14, 0.28))
                angle = rand_uniform(-0.85, 0.85)
                step_left = step_dt - float(hit_dt[k])
                append_splash({
                    "x": x, "y": mound_top + 0.5,
                    "vx": sin(angle) * bounce,
                    "vy": cos(angle) * bounce,
                    "size": rand_choice([1, 1, 2]),
                    "_step_dt": step_left if step_left > 0 else 0,
                })
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

    # ---------- 点击沙漏球 = 开始/暂停 ----------

    def on_touch_down(self, touch):
        if self._geom_ready and self.collide_point(*touch.pos):
            dx = touch.x - self._cx
            if (dx * dx + (touch.y - self._upper_y_c) ** 2 <= self._R ** 2 or
                    dx * dx + (touch.y - self._lower_y_c) ** 2 <= self._R ** 2):
                app = App.get_running_app()
                if app is not None:
                    app.on_toggle()
                return True
        return super().on_touch_down(touch)

    def _build_glass_shell(self):
        """静态玻璃壳缓存到 canvas.before,仅几何变化时重建"""
        self.canvas.before.clear()
        if not self._geom_ready:
            return
        cx, R, Ri, ow = self._cx, self._R, self._R_inner, self._ow
        uyc, lyc = self._upper_y_c, self._lower_y_c
        nw = self.neck_w
        glass_fill = hex_rgb(GLASS_FILL)
        glass_out = hex_rgb(GLASS_OUTLINE)
        with self.canvas.before:
            for yc in (uyc, lyc):
                Color(*glass_out)
                Ellipse(pos=(cx - R, yc - R), size=(2 * R, 2 * R))
                Color(*glass_fill)
                Ellipse(pos=(cx - R + ow, yc - R + ow),
                        size=(2 * (R - ow), 2 * (R - ow)))
            # 颈部: 挖掉球极冠肩台 → 直筒 → 上下曲线过渡(几何见 _rebuild_height_table)
            tp = self._taper
            t_out, t_in = tp['t_out'], tp['t_in']
            mir = 2 * self._neck_y
            yb_u = tp['y_bot']
            yb_l = mir - yb_u
            pad = dp(3)

            def _pts(key, flip):
                return [(x, (mir - y) if flip else y) for x, y in tp[key]]

            def _band(pts, color):
                """沿曲线逐段画横跨左右的四边形(Kivy 无多边形图元)"""
                Color(*color)
                for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                    Quad(points=[cx - x0, y0, cx + x0, y0, cx + x1, y1, cx - x1, y1])

            # ① 擦掉曲线以外的球极冠(用背景色覆盖)
            bg = hex_rgb(BG_COLOR)
            for flip in (False, True):
                op = _pts('out_pts', flip)
                Color(*bg)
                for sgn in (1, -1):
                    for (x0, y0), (x1, y1) in zip(op, op[1:]):
                        Quad(points=[cx + sgn * x0, y0, cx + sgn * (R + pad), y0,
                                     cx + sgn * (R + pad), y1, cx + sgn * x1, y1])
            # ② 直筒段
            Color(*glass_out)
            Rectangle(pos=(cx - t_out, yb_l), size=(2 * t_out, yb_u - yb_l))
            Color(*glass_fill)
            Rectangle(pos=(cx - t_in, yb_l), size=(2 * t_in, yb_u - yb_l))
            # ③ 曲线过渡: 外轮廓(壁) + 内轮廓(腔)
            for flip in (False, True):
                _band(_pts('out_pts', flip), glass_out)
                _band(_pts('in_pts', flip), glass_fill)

    # ---------- 渲染 ----------

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

    @staticmethod
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

    def _particle_trail(self, particle, motion_scale=None):
        scale = self._particle_motion_scale if motion_scale is None else motion_scale
        return max(2.0, abs(particle["vy"]) * particle["trail_time"] / scale)

    def _hide_neck_grains(self):
        for color, line in self._neck_grain_pool[:self._neck_grain_count]:
            line.points = []
        self._neck_grain_count = 0

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


# ---------- App / UI(v2 布局: 色块在上, 控件在下) ----------

class HourglassApp(App):
    title = "跳跳的沙漏"

    def on_start(self):
        if platform == "android":
            Window.bind(on_flip=self._hide_startup_screen)

    def _hide_startup_screen(self, *_):
        if not self.hourglass._geom_ready or self.hourglass.height <= dp(100):
            return
        Window.unbind(on_flip=self._hide_startup_screen)
        try:
            from android.loadingscreen import hide_loading_screen
            hide_loading_screen()
        except Exception as exc:
            print(f"Startup overlay removal failed: {exc}")

    def build(self):
        self._sound_popup = None
        self._sound_diag_label = None
        self._completion_popup = None
        self._last_win_size = None
        if platform != "android":
            try:
                # 桌面模拟: --landscape 用宽窗验证反旋转; 否则维持手机竖屏
                if "--landscape" in sys.argv:
                    Window.size = (1000, 600)
                else:
                    Window.size = (400, 800)
            except Exception:
                pass
        Window.clearcolor = (*hex_rgb(BG_COLOR), 1)

        self.hourglass = HourglassWidget(size_hint=(1, 1))
        cfg = self.hourglass.load_config()
        if isinstance(cfg.get('duration'), (int, float)) and cfg['duration'] > 0:
            self.hourglass.duration = cfg['duration']
            self.hourglass._rebuild_height_table()
        sound_name = cfg.get('sound_name')
        if sound_name in [n for n, _ in SOUND_OPTIONS]:
            self.hourglass._set_sound(sound_name)
        color_name = cfg.get('color_name', '金沙')
        for name, base, dark, light in SAND_PRESETS:
            if name == color_name:
                self.hourglass.set_sand_color(base, dark, light)
                break

        root = BoxLayout(orientation="vertical", spacing=dp(3),
                         padding=[dp(8), dp(6), dp(8), dp(6)])

        # 顶部色块
        top_colors = BoxLayout(orientation="horizontal", size_hint=(1, None),
                               height=dp(50), spacing=dp(4))
        self.color_btns = []
        for name, base, dark, light in SAND_PRESETS:
            btn = Button(text=name, font_size=sp(15), background_normal="",
                         background_color=(*hex_rgb(base), 1), color=(1, 1, 1, 1))
            btn.bind(on_press=lambda inst, b=base, d=dark, l=light, n=name:
                     self.on_color(b, d, l, n))
            top_colors.add_widget(btn)
            self.color_btns.append((name, btn))
        root.add_widget(top_colors)

        # 倒计时
        self.time_label = Label(
            text=f"{self.hourglass.duration:.0f}/{self.hourglass.duration:.0f}秒",
            font_size=sp(24), bold=True, size_hint=(1, None), height=dp(40),
            color=(0.2, 0.2, 0.2, 1))
        root.add_widget(self.time_label)

        # 沙漏画布
        root.add_widget(self.hourglass)

        # 底部控件
        bottom = BoxLayout(orientation="horizontal", size_hint=(1, None),
                           height=dp(58), spacing=dp(6))
        self.duration_btn = Button(text=_fmt_duration(self.hourglass.duration),
                                   size_hint=(None, 1), width=dp(82),
                                   font_size=sp(16), bold=True,
                                   background_normal="",
                                   background_color=(0.769, 0.682, 0.557, 1),
                                   color=POPUP_TEXT)
        self.duration_btn.bind(on_press=self.on_duration_picker)
        bottom.add_widget(self.duration_btn)
        self.sound_btn = Button(text="沙沙声",
                                size_hint=(None, 1), width=dp(74), font_size=sp(15),
                                background_normal="",
                                background_color=(*POPUP_GOLD_SEL[:3], 0.92),
                                color=POPUP_TEXT)
        self.sound_btn.bind(on_press=self.on_sound_picker)
        bottom.add_widget(self.sound_btn)
        self._benchmark_runner = None
        self._benchmark_results = []
        self._benchmark_cancelled = False
        self._benchmark_popup = None
        self._benchmark_area = BenchmarkHoldArea(self.on_benchmark)
        # 长按处印出版本号: 隐藏入口总得让人找得到该按哪儿。
        # BenchmarkHoldArea 是裸 Widget、不做子控件布局, 得手动跟着它铺满。
        hold_label = Label(text=f"v{APP_VERSION}", font_size=sp(11),
                           color=(*POPUP_TEXT[:3], 0.38), halign="center",
                           valign="middle")

        def _fit_hold_label(instance, *_):
            hold_label.pos = instance.pos
            hold_label.size = instance.size
            hold_label.text_size = instance.size

        self._benchmark_area.bind(pos=_fit_hold_label, size=_fit_hold_label)
        _fit_hold_label(self._benchmark_area)
        self._benchmark_area.add_widget(hold_label)
        bottom.add_widget(self._benchmark_area)
        self.start_btn = Button(text="开始", size_hint=(None, 1), width=dp(74),
                                font_size=sp(16), bold=True,
                                background_normal="",
                                background_color=(0.353, 0.620, 0.243, 1), color=(1, 1, 1, 1))
        self.start_btn.bind(on_press=self.on_toggle)
        bottom.add_widget(self.start_btn)
        reset_btn = Button(text="重置", size_hint=(None, 1), width=dp(74), font_size=sp(16))
        reset_btn.bind(on_press=self.on_reset)
        self._reset_btn = reset_btn
        bottom.add_widget(reset_btn)
        root.add_widget(bottom)

        self._mark_selected(color_name)
        self._update_sound_btn()

        # 把整棵 UI 树装进"等效竖屏窗口", 由 LandLayer 横屏时整体旋转 ±90° 铺满
        layer = LandLayer()
        anchor = AnchorLayout(size_hint=(None, None))
        anchor.add_widget(root)
        layer.add_widget(anchor)
        layer._anchor = anchor
        global _LAYER
        _LAYER = layer
        layer.apply_orientation()

        # 运行时方向守卫(只 android): 启动 1s 抢一次 + 常驻 0.7s 重申; 窗口变化由 _frame 立即跟手
        if platform == "android":
            Clock.schedule_once(lambda *_: self._apply_orientation(), 1.0)
            Clock.schedule_interval(self._orient_guard, 0.7)
        Clock.schedule_interval(self._frame, 0.1)
        return layer

    # ---------- 运行时方向守卫(移植自 hengping.md §3.3/§3.4/§8) ----------

    def _apply_orientation(self):
        """以毒攻毒: 用 setRequestedOrientation 重申方向, 顶掉 SDL 启动/回前台的竖屏自报。
        ZUI 只认运行时请求 → 宽屏抢 FULL_SENSOR(10), 瘦长机锁 SENSOR_PORTRAIT(7)。"""
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            act.setRequestedOrientation(10 if _device_is_wide() else 7)
        except Exception:
            pass

    def _orient_guard(self, dt):
        """常驻方向守卫(0.7s): 宽屏仅在横置(rot∈{1,3})时重申 fullSensor; 瘦长机无条件锁竖屏。"""
        if platform != "android":
            return
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            if _device_is_wide():
                rot = act.getWindowManager().getDefaultDisplay().getRotation()
                if rot in (1, 3):                    # 横置才重申; 竖置本就竖构图, 幂等
                    act.setRequestedOrientation(10)
            else:
                act.setRequestedOrientation(7)        # 瘦长机持续锁竖屏
        except Exception:
            pass

    def _frame(self, dt):
        """窗口尺寸变化 → 立即重算反旋转层 + 重申方向(不等守卫周期, 转屏跟手)。"""
        ws = (Window.width, Window.height)
        if ws != getattr(self, "_last_win_size", None):
            self._last_win_size = ws
            layer = _land_layer()
            if layer is not None:
                layer.apply_orientation()
            self._apply_orientation()

    def _selected_color_name(self):
        for n, btn in self.color_btns:
            if btn.text.startswith("● "):
                return n
        return "金沙"

    def _sel_fg_for(self, rgb):
        """选中态按钮文字色:亮色背景用黑字,暗色用白字(保证对比度)"""
        r, g, b = rgb
        return (0, 0, 0, 1) if (r * 0.299 + g * 0.587 + b * 0.114) > 0.59 else (1, 1, 1, 1)

    def _closest_base_and_mult(self, sec):
        best_base, best_mult = 60, 1
        best_diff = float('inf')
        for _, base_val in BASE_PERIODS:
            mult = max(1, min(MULT_SLIDER_MAX, round(sec / base_val)))
            total = base_val * mult
            diff = abs(total - sec)
            # tie-break:同 diff 取更小倍数(更粗基底),避免 50s 显示成"1秒×50倍"
            if diff < best_diff - 1e-9 or (abs(diff - best_diff) <= 1e-9
                                           and mult < best_mult):
                best_diff = diff
                best_base = base_val
                best_mult = mult
        return best_base, best_mult

    def on_duration_picker(self, *_):
        cur = self.hourglass.duration
        init_base, init_mult = self._closest_base_and_mult(cur)
        # mutable closure state
        state = {"base": init_base, "mult": init_mult}

        content = BoxLayout(orientation="vertical", spacing=dp(8),
                            padding=(dp(12), dp(6), dp(12), dp(10)))

        # 预创建 mult_btns/preview_label,避免 lambda 闭包延迟绑定
        # (Android Kivy 2.3.0 对 late binding 时序敏感,曾导致点周期按钮闪退)
        mult_btns = {}
        preview_label = Label(
            text=f"最终周期：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）",
            size_hint=(1, None), height=dp(34),
            color=POPUP_TEXT, font_size=sp(18))

        # --- 基础时间标题 ---
        base_title = Label(text="基础时间:", size_hint=(1, None), height=dp(22),
                           color=POPUP_TEXT, font_size=sp(15),
                           halign="left", valign="middle")
        base_title.bind(size=lambda inst, val: setattr(inst, 'text_size', (val[0], val[1])))
        content.add_widget(base_title)

        # --- 基础周期按钮 ---
        base_grid = BoxLayout(orientation="horizontal", spacing=dp(8),
                              size_hint=(1, None), height=dp(46))
        base_btns = {}
        for label, val in BASE_PERIODS:
            is_sel = (val == init_base)
            btn = Button(text=label, font_size=sp(16),
                         background_normal="",
                         background_color=POPUP_GOLD_SEL if is_sel
                                          else POPUP_UNSEL_BASE,
                         color=POPUP_TEXT)
            btn.bind(on_press=lambda inst, v=val, bb=base_btns, mb=mult_btns,
                              st=state, pl=preview_label:
                     self._on_base_picked(v, bb, st, mb, pl))
            base_btns[val] = btn
            base_grid.add_widget(btn)
        content.add_widget(base_grid)

        # --- 倍数按钮 (两行 BoxLayout, 不用 GridLayout 避免 Android 兼容问题) ---
        MULTIPLIERS = [1, 2, 3, 5, 10, 20, 30, 50, 70, 100]
        mult_title = Label(text="倍数:", size_hint=(1, None), height=dp(22),
                           color=POPUP_TEXT, font_size=sp(15),
                           halign="left", valign="middle")
        mult_title.bind(size=lambda inst, val: setattr(inst, 'text_size', (val[0], val[1])))
        content.add_widget(mult_title)
        for row_vals in [MULTIPLIERS[:5], MULTIPLIERS[5:]]:
            row = BoxLayout(orientation="horizontal", spacing=dp(6),
                            size_hint=(1, None), height=dp(40))
            for m in row_vals:
                is_m = (m == init_mult)
                btn = Button(text=f"{m}倍", font_size=sp(15),
                             background_normal="",
                             background_color=POPUP_GOLD_SEL if is_m
                                              else POPUP_UNSEL_MULT,
                             color=POPUP_TEXT)
                btn.bind(on_press=lambda inst, v=m, mb=mult_btns, st=state,
                                  pl=preview_label:
                         self._on_mult_picked(v, mb, st, pl))
                mult_btns[m] = btn
                row.add_widget(btn)
            content.add_widget(row)

        # --- 对数倍率滑杆(1–1000 倍,与按钮并存但不同步;默认 Kivy 大滑杆样式,
        #     进度条颜色恢复金色值道(与选中态同色的黄色已滑段);
        #     未滑段默认浅灰贴图接近弹窗底色,换暖灰棕贴图 #8a7a68) ---
        slider = Slider(min=0.0, max=1.0,
                        value=math.log(max(1.0, min(float(MULT_SLIDER_MAX),
                                                   float(init_mult)))) / math.log(MULT_SLIDER_MAX),
                        size_hint=(1, None), height=dp(44),
                        value_track=True,
                        value_track_color=(*POPUP_GOLD_SEL[:3], 0.9),
                        background_horizontal=resource_path("ui/slider_track.png"),
                        background_width='8dp',                 # 细线化:未滑段=浅蓝细线,不再 36sp 灰槽
                        value_track_width='8dp')                # 金色线同宽,盖住蓝线左段(已滑=纯金无反边)
        mult_label = Label(text=f"×{init_mult}", size_hint=(None, None),
                           size=(dp(64), dp(36)), font_size=sp(15),
                           color=POPUP_TEXT)
        slider_row = BoxLayout(orientation="horizontal", spacing=dp(8),
                               size_hint=(1, None), height=dp(44))
        slider_row.add_widget(slider)
        slider_row.add_widget(mult_label)
        content.add_widget(slider_row)
        # label 用默认参数固定;滑块值变化不触碰任何按钮高亮(不同步)
        slider.bind(value=lambda inst, v, st=state, pv=preview_label,
                    ml=mult_label: self._on_slider_moved(v, st, pv, ml))

        # --- 预览 (预创建,此处 add 到正确位置) ---
        content.add_widget(preview_label)

        # --- 运行中警告 ---
        if self.hourglass.running:
            warn_label = Label(text="修改周期将重置当前进度",
                               size_hint=(1, None), height=dp(26),
                               color=(0.85, 0.45, 0.15, 1), font_size=sp(14))
            content.add_widget(warn_label)

        # --- 取消 + 确定 按钮行 ---
        btn_row = BoxLayout(orientation="horizontal", spacing=dp(10),
                            size_hint=(1, None), height=dp(54))
        cancel_btn = Button(text="取消", font_size=sp(16),
                            background_normal="",
                            background_color=POPUP_CANCEL_BG,
                            color=POPUP_TEXT)
        btn_row.add_widget(cancel_btn)
        confirm_btn = Button(text="确定", font_size=sp(16), bold=True,
                             background_normal="",
                             background_color=POPUP_GOLD_SEL,
                             color=POPUP_TEXT)
        btn_row.add_widget(confirm_btn)
        content.add_widget(btn_row)

        popup = _SandBgPopup(title="选择周期", content=content,
                             size_hint=(0.88, None), height=dp(460),
                             auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(19)
        popup.separator_color = (*POPUP_GOLD_SEL[:3], 0.25)
        popup.title_color = (1, 1, 1, 1)
        # content 自适应内容高度,不撑满 _container;顶部对齐紧贴 separator
        content.size_hint_y = None
        content.pos_hint = {'top': 1}
        content.bind(minimum_height=content.setter('height'))
        # Popup 高度自适应 content 高度(+ title bar/separator/padding 余量)
        def _adjust_popup_height(inst, val):
            popup.height = val + dp(85)
        content.bind(minimum_height=_adjust_popup_height)

        cancel_btn.bind(on_press=popup.dismiss)
        confirm_btn.bind(on_press=lambda inst, st=state, p=popup:
                         self._pick_duration(st['base'] * st['mult'], p))
        popup.open()

    def _on_base_picked(self, val, base_btns, state, mult_btns, preview_label):
        state["base"] = val
        active_color = POPUP_GOLD_SEL
        inactive_color = POPUP_UNSEL_BASE
        for v, btn in base_btns.items():
            sel = (v == val)
            btn.background_color = active_color if sel else inactive_color
            btn.color = POPUP_TEXT
        preview_label.text = f"最终周期：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）"

    def _on_mult_picked(self, val, mult_btns, state, preview_label):
        state["mult"] = val
        active_color = POPUP_GOLD_SEL
        inactive_color = POPUP_UNSEL_MULT
        for m, btn in mult_btns.items():
            sel = (m == val)
            btn.background_color = active_color if sel else inactive_color
            btn.color = POPUP_TEXT
        preview_label.text = f"最终周期：{_fmt_duration(state['base'] * state['mult'])}（{state['base'] * state['mult']:.0f}秒）"

    def _on_slider_moved(self, v, state, preview_label, mult_label):
        """对数滑杆:10^(3t) 向上取整;只更新 state/预览/×N 标签,不动按钮高亮。"""
        m = _mult_from_slider(v)
        if m == state['mult']:
            return
        state['mult'] = m
        mult_label.text = f"×{m}"
        total = state['base'] * m
        preview_label.text = f"最终周期：{_fmt_duration(total)}（{total:.0f}秒）"

    def _pick_duration(self, sec, popup):
        popup.dismiss()
        if self.hourglass.set_duration(sec):
            self.hourglass.save_config(self._selected_color_name())
        self.duration_btn.text = _fmt_duration(self.hourglass.duration)
        self.on_run_state_changed()

    def on_toggle(self, *_):
        if self._benchmark_active():
            self._benchmark_runner.cancel()
            return
        self.hourglass.toggle()
        self.on_run_state_changed()

    def on_reset(self, *_):
        self.hourglass.reset()
        self.on_run_state_changed()

    def on_run_state_changed(self):
        if self._benchmark_active():
            self.start_btn.text = "取消"
            self.start_btn.background_color = POPUP_CONFIRM
            return
        if self.hourglass.running:
            self.start_btn.text = "暂停"
            self.start_btn.background_color = (0.851, 0.557, 0.243, 1)
        else:
            self.start_btn.text = "开始"
            self.start_btn.background_color = (0.353, 0.620, 0.243, 1)

    def _benchmark_active(self):
        return self._benchmark_runner is not None and self._benchmark_runner.active

    def on_benchmark(self, *_):
        if self._benchmark_active() or self._benchmark_popup is not None:
            return
        content = BoxLayout(orientation="vertical", spacing=dp(8),
                            padding=(dp(8), dp(6), dp(8), dp(6)))
        scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False)
        rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        rows.bind(minimum_height=rows.setter("height"))
        scroll.add_widget(rows)
        content.add_widget(scroll)
        result_by_period = {r["period"]: r for r in self._benchmark_results}
        for period in PERIODS:
            result = result_by_period.get(period)
            text = format_benchmark_result(period, result)
            label = Label(text=text, font_size=sp(14), color=POPUP_TEXT,
                          size_hint=(1, None), halign="left", valign="top")
            label.bind(width=lambda inst, width: setattr(inst, "text_size", (width, None)))
            label.bind(texture_size=lambda inst, size: setattr(inst, "height", size[1] + dp(8)))
            rows.add_widget(label)
            if result and result.get("frame_trace"):
                rows.add_widget(BenchmarkFrameChart(
                    result, size_hint=(1, None), height=dp(112)))
        if self._benchmark_cancelled:
            rows.add_widget(Label(text="测试已取消", color=POPUP_CONFIRM,
                                  font_size=sp(14), size_hint_y=None, height=dp(26)))
        commands = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        run_btn = Button(text="重新测试" if self._benchmark_results else "开始测试",
                         font_size=sp(16), background_normal="",
                         background_color=POPUP_GOLD_SEL, color=POPUP_TEXT)
        close_btn = Button(text="关闭", font_size=sp(16), background_normal="",
                           background_color=POPUP_CANCEL_BG, color=POPUP_TEXT)
        save_btn = Button(text="保存文件", font_size=sp(16), background_normal="",
                          background_color=POPUP_UNSEL_BASE, color=POPUP_TEXT,
                          disabled=not self._benchmark_results)
        save_btn.bind(on_press=self._save_benchmark_results)
        self._benchmark_save_btn = save_btn
        commands.add_widget(run_btn)
        commands.add_widget(save_btn)
        commands.add_widget(close_btn)
        content.add_widget(commands)
        self._benchmark_hint = Label(text="", font_size=sp(11), color=POPUP_TEXT,
                                     size_hint_y=None, height=dp(30), halign="center",
                                     valign="middle")
        self._benchmark_hint.bind(
            size=lambda inst, size: setattr(inst, "text_size", size))
        content.add_widget(self._benchmark_hint)
        popup_height = min(dp(600 if self._benchmark_results else 360),
                           max(Window.width, Window.height) * 0.85)
        popup = _SandBgPopup(title=f"Benchmark v{APP_VERSION}", content=content, size_hint=(0.94, None),
                            height=popup_height,
                            auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(19)
        self._benchmark_popup = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_benchmark_popup", None))
        close_btn.bind(on_press=lambda *_: popup.dismiss())
        run_btn.bind(on_press=lambda *_: self._start_benchmark(popup))
        popup.open()

    def _benchmark_save_dir(self):
        """保存目录:Android 优先外部私有目录(adb / 文件管理器免 root 可取),
        取不到再退回内部 user_data_dir;桌面仍写工程目录下的 benchmark_logs。"""
        if platform == "android":
            try:
                from jnius import autoclass
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                external = activity.getExternalFilesDir(None)
                if external is not None:
                    return os.path.join(str(external.getAbsolutePath()),
                                        "benchmark_logs")
            except Exception as exc:
                print(f"External dir unavailable: {exc}")
            return os.path.join(self.user_data_dir, "benchmark_logs")
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "benchmark_logs")

    def _save_benchmark_results(self, button):
        """把这一轮的全部记录写成文件(逐帧 trace + 分位/超阈值/残差/分桶表)。"""
        if not self._benchmark_results:
            return
        try:
            path = save_benchmark_log(self._benchmark_save_dir(),
                                      self._benchmark_results,
                                      self._benchmark_cancelled)
        except OSError as exc:
            print(f"Benchmark save failed: {exc}")
            button.text = "保存失败"
            return
        self._benchmark_log_path = path
        print(f"Benchmark saved: {path}")
        button.text = "已保存"
        hint = getattr(self, "_benchmark_hint", None)
        if hint is not None:
            hint.text = f"已写入 {path}"

    def _benchmark_controls(self, disabled):
        for control in [self.duration_btn, self.sound_btn, self._reset_btn] + [
                btn for _name, btn in self.color_btns]:
            control.disabled = disabled

    def _start_benchmark(self, popup):
        if self._benchmark_active():
            return
        popup.dismiss()
        self._benchmark_results = []
        self._benchmark_cancelled = False
        self._benchmark_controls(True)
        self._benchmark_runner = BenchmarkRunner(
            self.hourglass, self._benchmark_case, self._benchmark_finished)
        self._benchmark_runner.start()
        self.on_run_state_changed()

    def _benchmark_case(self, period, index):
        self.duration_btn.text = f"{period} 秒"
        self.on_run_state_changed()

    def _benchmark_finished(self, results, cancelled):
        self._benchmark_results = results
        self._benchmark_cancelled = cancelled
        directory = (self.user_data_dir if platform == "android"
                     else os.path.dirname(os.path.abspath(__file__)))
        try:
            self._benchmark_log_path = save_benchmark_log(
                os.path.join(directory, "benchmark_logs"), results, cancelled)
            print(f"Benchmark log: {self._benchmark_log_path}")
        except OSError as exc:
            self._benchmark_log_path = None
            print(f"Benchmark log failed: {exc}")
        self._benchmark_controls(False)
        self.duration_btn.text = _fmt_duration(self.hourglass.duration)
        self.update_time(max(0, self.hourglass.duration - self.hourglass.elapsed),
                         self.hourglass.duration)
        self.on_run_state_changed()
        if not getattr(self, "_benchmark_closing", False):
            self.on_benchmark()

    def on_sound_picker(self, *_):
        """音效选择弹窗:点击即切换(生效但**不关窗**,可连续试听),当前项金色高亮,
        「确定」关闭弹窗(每行 3 个,自动分行)。"""
        cur = self.hourglass.sound_name
        btns = {}   # label → btn,点击后刷新弹窗内高亮

        content = BoxLayout(orientation="vertical", spacing=dp(8),
                            padding=(dp(12), dp(10), dp(12), dp(10)))
        for i in range(0, len(SOUND_EFFECTS), 3):
            row = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint=(1, None), height=dp(54))
            for label, _path in SOUND_OPTIONS[i:i + 3]:
                is_sel = (label == cur)
                btn = Button(text=label, font_size=sp(16),
                             background_normal="",
                             background_color=POPUP_GOLD_SEL if is_sel
                                              else POPUP_UNSEL_BASE,
                             color=POPUP_TEXT)
                # lambda 闭包延迟绑定:label/btns 用默认参数固定(Android Kivy 时序敏感)
                btn.bind(on_press=lambda inst, lb=label, bs=btns:
                         self._on_sound_picked(lb, bs))
                btns[label] = btn
                row.add_widget(btn)
            content.add_widget(row)

        # 后端异常提示:正常(audiotrack/winsound)时文案为空、高度 0,界面上看不见。
        # 错误串可能很长,必须按宽度换行 —— 单行会被裁掉最关键的开头(实测只剩 "state=2")
        diag = Label(text="", font_size=sp(12), color=(0.62, 0.23, 0.16, 0.95),
                     halign="center", valign="top", size_hint=(1, None), height=0)
        diag.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
        # 换行后高度会变,跟着 texture_size 走(宽度绑定晚于首次测量,只算一次会按单行算矮)
        diag.bind(texture_size=lambda inst, ts:
                  setattr(inst, "height", ts[1] if inst.text else 0))
        self._sound_diag_label = diag
        content.add_widget(diag)
        self._refresh_sound_diag()

        # 「确定」= 唯一关闭出口(音效已在点击选项时即时生效,这里只收尾)
        # 暗红底:与选项选中金色区分,避免误看成"选中的音效项"
        confirm_btn = Button(text="确定", font_size=sp(16), bold=True,
                             background_normal="",
                             background_color=POPUP_CONFIRM,
                             color=POPUP_TEXT_WHITE,
                             size_hint=(1, None), height=dp(54))
        content.add_widget(confirm_btn)

        self._sound_popup = _SandBgPopup(title="选择音效", content=content,
                                         size_hint=(0.88, None), height=dp(300),
                                         auto_dismiss=False)
        self._sound_popup.title_align = "center"
        self._sound_popup.title_size = sp(19)
        self._sound_popup.separator_color = (*POPUP_GOLD_SEL[:3], 0.25)
        self._sound_popup.title_color = (1, 1, 1, 1)
        # 内容高度自适应(同周期弹窗的成功链路)
        content.size_hint_y = None
        content.pos_hint = {'top': 1}
        content.bind(minimum_height=content.setter('height'))
        popup = self._sound_popup    # 局部引用:闭包持有,弹窗关闭后布局事件仍安全
        confirm_btn.bind(on_press=lambda inst, p=popup: self._close_sound_picker(p))

        def _adjust_popup_height(inst, val):
            popup.height = val + dp(85)
        content.bind(minimum_height=_adjust_popup_height)
        self._sound_popup.open()

    def _on_sound_picked(self, label, btns):
        """点击选项:立即切换生效,**弹窗不关闭**(可连续试听);高亮跟随点击项。"""
        if self.hourglass._set_sound(label):
            self.hourglass.save_config(self._selected_color_name())
            self._update_sound_btn()
        for lb, btn in btns.items():
            btn.background_color = POPUP_GOLD_SEL if lb == label else POPUP_UNSEL_BASE
            btn.color = POPUP_TEXT
        self._refresh_sound_diag()

    def _refresh_sound_diag(self):
        """没问题→空文案 + 高度 0(不占位);有问题→显示后端与错误串。"""
        lb = getattr(self, "_sound_diag_label", None)
        if lb is None:
            return
        lb.text = self.hourglass.sound_problem_desc()
        if not lb.text:
            lb.height = 0
            return
        lb.texture_update()                      # 立刻拿到换行后的真实高度
        lb.height = max(dp(20), lb.texture_size[1])

    def _close_sound_picker(self, popup):
        """「确定」:关闭音效弹窗(音效已在点击选项时生效,这里只收尾)。"""
        self._sound_popup = None
        self._sound_diag_label = None
        popup.dismiss()

    def _update_sound_btn(self):
        """主按钮显示当前音效名(沙沙声/水流声/风声/钟表声/无声音);
        静音保持暖灰样式,有声金色。"""
        self.sound_btn.text = self.hourglass.sound_name
        if self.hourglass.sound_name == SILENT_NAME:
            self.sound_btn.background_color = (0.718, 0.686, 0.643, 1)
        else:
            self.sound_btn.background_color = (*POPUP_GOLD_SEL[:3], 0.92)
        self.sound_btn.color = POPUP_TEXT

    def on_color(self, base, dark, light, name):
        self.hourglass.set_sand_color(base, dark, light)
        self._mark_selected(name)
        self.hourglass.save_config(name)

    def _mark_selected(self, name):
        for n, btn in self.color_btns:
            btn.text = ("● " + n) if n == name else n

    def update_time(self, remaining_sec, duration):
        self.time_label.text = _fmt_countdown_pair(remaining_sec, duration)

    def on_completed(self, duration):
        """沙漏流尽:弹窗报时长。auto_dismiss=False —— 不点不关(用户明确要求)。"""
        if self._benchmark_active():
            return                       # 基准测试不显示结果弹窗(沿用既有约定)
        if self._completion_popup is not None:
            return
        content = BoxLayout(orientation="vertical", spacing=dp(14),
                            padding=[dp(18), dp(10), dp(18), dp(18)],
                            size_hint=(1, None))
        content.bind(minimum_height=content.setter("height"))

        caption = Label(text="沙漏已流尽", font_size=sp(15),
                        color=POPUP_TEXT_SUB, size_hint=(1, None), height=dp(24))
        content.add_widget(caption)

        big = Label(text=_fmt_duration_cn(duration), font_size=sp(28), bold=True,
                    color=POPUP_GOLD_SEL, size_hint=(1, None), height=dp(52),
                    halign="center", valign="middle")
        big.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
        content.add_widget(big)

        rule = Widget(size_hint=(1, None), height=dp(2))
        with rule.canvas:
            Color(*POPUP_GOLD_SEL[:3], 0.35)
            rule_rect = Rectangle(pos=rule.pos, size=rule.size)
        rule.bind(pos=lambda inst, v: setattr(rule_rect, "pos", v),
                  size=lambda inst, v: setattr(rule_rect, "size", v))
        content.add_widget(rule)

        content.add_widget(Widget(size_hint=(1, None), height=dp(4)))

        close_btn = Button(text="好", font_size=sp(16), bold=True,
                           background_normal="",
                           background_color=POPUP_CONFIRM,
                           color=POPUP_TEXT_WHITE,
                           size_hint=(1, None), height=dp(52))
        content.add_widget(close_btn)

        popup = _SandBgPopup(title="计时完成", content=content,
                             size_hint=(0.86, None), height=dp(300),
                             auto_dismiss=False)
        popup.title_align = "center"
        popup.title_size = sp(19)
        popup.separator_color = (*POPUP_GOLD_SEL[:3], 0.25)
        popup.title_color = (1, 1, 1, 1)
        content.bind(minimum_height=lambda inst, val:
                     setattr(popup, "height", val + dp(85)))
        close_btn.bind(on_press=lambda inst, p=popup: self._close_completion(p))
        self._completion_popup = popup
        popup.open()

    def _close_completion(self, popup):
        self._completion_popup = None
        popup.dismiss()

    def on_pause(self):
        if self._benchmark_active():
            self._benchmark_runner.cancel()
        self.hourglass._stop_completion_sound()
        return True

    def on_stop(self):
        self._benchmark_closing = True
        if self._benchmark_active():
            self._benchmark_runner.cancel()
        self.hourglass._stop_completion_sound()
        if self.hourglass._completion_spoken is not None:
            self.hourglass._completion_spoken.close()
            self.hourglass._completion_spoken = None
        if self.hourglass._completion_sound is not None:
            self.hourglass._completion_sound.close()
        self.hourglass._stop_sound()

    def on_resume(self):
        if platform == "android":
            self.hourglass._stop_completion_sound()
            if self.hourglass._completion_spoken is not None:
                self.hourglass._completion_spoken.close()
                self.hourglass._completion_spoken = None
            if self.hourglass._completion_sound is not None:
                self.hourglass._completion_sound.close()
            self.hourglass._completion_sound = self.hourglass._make_completion_sound()
            # SDL 回前台会重报方向(可能把 fullSensor 覆盖回竖屏), 再抢一次话语权
            self._apply_orientation()
            layer = _land_layer()
            if layer is not None:
                layer.apply_orientation()
        return True


# ---------- 沙流渲染器(仅安卓启用批处理) ----------
#
# Kivy 的 Line 在 width>1 时**不用 glLineWidth**, 而是**每条线**自建一个带 10 段
# 圆头帽的三角网格。峰值约 2500 条 width=2 的粒子线 = 数千次网格提交 + 每颗粒的
# Python 提交成本;桌面 GL 余量大(实测帧率对粒子数几乎不变: 63.2→63.3fps),
# 所以**在 PC 上永远调不出这个问题**, 只有 GLES 暴露 ——
# 实测帧耗时 ≈ 2.5ms + 10.05µs × 在途粒子数, 2829 颗 → 30.9ms/帧(32fps),
# 表现为"一整段周期帧率都上不去"。
# 批处理把同色同线宽的粒子并进一个 Mesh, 端点走顶点纹理;几何(含圆头帽)与
# 逐 Line 版一致, 实测画面逐像素同一。同机同配置: 15s 47.9→65.4fps, Canvas 8.46→3.06ms。
FLOW_RENDERER = "texture"      # line | batch | gpu | texture


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


if platform == "android":
    try:
        _install_flow_renderer(HourglassWidget)
    except Exception as exc:
        print(f"flow renderer {FLOW_RENDERER} unavailable, using line pool: {exc}")


if __name__ == "__main__":
    HourglassApp().run()
