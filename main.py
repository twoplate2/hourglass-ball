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
import time
import json

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase, Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics import (Color, Rectangle, Line, Ellipse,
                           StencilPush, StencilUse, StencilUnUse, StencilPop)
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform


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
POPUP_TEXT = (0.20, 0.14, 0.08, 1)                # 深咖啡文字
POPUP_TEXT_WHITE = (1, 1, 1, 1)

FALL_DELAY = 1.0          # 沙子飞到底的延迟(秒),下沙堆出现与粒子到底同步
MOUND_APPEAR = 0.5        # 下沙堆出现后平滑渐显时长
MOUND_FLOOR_MIN = 2.5     # 前期极小可见保底(dp),仅防薄层消失,不拔高
MOUND_FLOOR_MAX = 3.5
MOUND_FLOOR_EFF = 0.02

DUST_COUNT = 25
DUST_LIFETIME = 1.0
FLASH_DURATION = 0.35

WAV_FILENAME = "sand_loop.wav"


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
    if sec < 60:
        return f"{sec:.0f} 秒"
    elif sec < 3600:
        return f"{sec / 60:.0f} 分钟"
    else:
        return f"{sec / 3600:.0f} 小时"


BASE_PERIODS = [
    ("10 秒", 10),
    ("1 分钟", 60),
    ("10 分钟", 600),
    ("1 小时", 3600),
]


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

    def __init__(self, wav_path):
        self._is_android = (platform == "android")
        self._is_windows = (platform == "win")
        self._audio_track = None
        self._kivy_sound = None
        self._winsound = None
        self._wav_path = wav_path
        self._active = False
        self._needs_reload = False

        if self._is_android:
            try:
                self._init_audio_track(wav_path)
                if self._audio_track is not None:
                    return
            except Exception as e:
                print(f"AudioTrack init failed: {e}")
                self._audio_track = None
            # fallthrough → Kivy SoundLoader 兜底
        if self._is_windows:
            try:
                import winsound
                self._winsound = winsound
                return
            except Exception:
                self._winsound = None
        # 桌面 fallback / Android AudioTrack 失败兜底
        try:
            from kivy.core.audio import SoundLoader
            self._kivy_sound = SoundLoader.load(wav_path)
            if self._kivy_sound is not None:
                self._kivy_sound.loop = True
        except Exception:
            self._kivy_sound = None

    def _init_audio_track(self, wav_path):
        from jnius import autoclass, jarray

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
                     .setTransferMode(0)  # MODE_STATIC = 0
                     .build())
        except Exception as e:
            print(f"Builder init failed: {e}, trying legacy constructor")

        # 方法2: 传统构造函数(API 3+, 某些设备 Builder 不可用时兜底)
        if track is None:
            try:
                AudioTrack = autoclass('android.media.AudioTrack')
                AudioManager = autoclass('android.media.AudioManager')
                track = AudioTrack(
                    AudioManager.STREAM_MUSIC,
                    sample_rate,
                    channel_out,
                    AudioFormat.ENCODING_PCM_16BIT,
                    len(pcm),
                    0)  # MODE_STATIC = 0
            except Exception as e:
                raise ValueError(f"All AudioTrack constructors failed: {e}")

        # 校验初始化状态
        AudioTrack = autoclass('android.media.AudioTrack')
        state = track.getState()
        if state != AudioTrack.STATE_INITIALIZED:
            raise ValueError(
                f"AudioTrack not initialized: state={state}, "
                f"expected={AudioTrack.STATE_INITIALIZED}")

        # 写入 PCM 数据
        java_byte_array = jarray('b')(pcm)
        written = track.write(java_byte_array, 0, len(pcm))
        if written != len(pcm):
            raise ValueError(
                f"AudioTrack.write incomplete: {written}/{len(pcm)} bytes")

        # 硬件循环点
        frame_size = channels * (bits // 8)
        total_frames = len(pcm) // frame_size
        result = track.setLoopPoints(0, total_frames, -1)
        if result != 0:  # SUCCESS = 0
            raise ValueError(f"setLoopPoints failed: {result}")

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
                    except Exception:
                        pass
                    self._needs_reload = False
                self._audio_track.setPlaybackHeadPosition(0)
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
                self._winsound.PlaySound(self._wav_path,
                    self._winsound.SND_LOOP | self._winsound.SND_ASYNC | self._winsound.SND_FILENAME)
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
        super().open(*args, **kwargs)
        Clock.schedule_once(self._apply_light_theme, 0)

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


# ---------- 沙漏画布 ----------

class HourglassWidget(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.duration = 50.0
        self.elapsed = 0.0
        self.running = False
        self.last_tick = None
        self.last_frame = time.time()

        self.sand_base = hex_rgb(SAND_PRESETS[0][1])
        self.sand_dark = hex_rgb(SAND_PRESETS[0][2])
        self.sand_light = hex_rgb(SAND_PRESETS[0][3])
        self._color_table = []
        self._rebuild_color_table()

        self._geom_ready = False
        self._vol_to_height = []

        self.particles = []
        self.particle_acc = 0.0
        self.splashes = []
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        self.flash_end = 0.0
        self._completion_triggered = False

        self.sound_on = True
        try:
            self._sound = _SoundProxy(resource_path(WAV_FILENAME))
        except Exception:
            self._sound = None

        self.bind(size=self._on_size, pos=self._on_size)
        Clock.schedule_once(self._on_size, 0)
        Clock.schedule_interval(self.tick, 1 / 60.0)

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
        tube_h = h * 0.03
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
        self._geom_ready = True
        self._build_glass_shell()

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
    def _fall_delay(self):
        if not self._geom_ready:
            return FALL_DELAY
        dist = self._upper_ball_cut - self._lower_sand_bot
        if dist <= 0:
            return 0.5
        v0 = 50.0
        g = 450.0
        t = (-v0 + math.sqrt(v0 ** 2 + 2 * g * dist)) / g + 0.05
        return max(0.30, min(t, self.duration * 0.45))

    def _effective_fallen(self):
        if self.duration <= 0:
            return 0.0
        if not self.running and self.elapsed >= self.duration:
            return 1.0
        return max(0.0, (self.elapsed - self._fall_delay) / self.duration)

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

    def get_mound_top_y(self):
        """下沙堆顶 y(含中央堆尖; Kivy y 向上,堆从底往上)。"""
        h = self._mound_height_px()
        if h <= 0:
            return self._lower_sand_bot
        mound_base_y = self._lower_sand_bot + h
        eff = self._effective_fallen()
        peak = max(0.0, eff - 0.10) * 10
        peak = min(peak, max(0.0, self._lower_sand_top - mound_base_y - 2))
        return mound_base_y + peak

    def _sand_half_w(self, y, yc):
        Ri = self._R_inner
        return math.sqrt(max(0.0, Ri * Ri - (y - yc) ** 2))

    # ---------- 控制 ----------

    def toggle(self):
        if self.running:
            self.running = False
            self._stop_sound()
        else:
            if self.elapsed >= self.duration:
                self.elapsed = 0
                self._reset_run_state()
            self.running = True
            self.last_tick = time.time()
            if self.sound_on:
                self._play_sound()

    def reset(self):
        self.elapsed = 0
        self.running = False
        self.last_tick = None
        self._reset_run_state()
        self._stop_sound()

    def _reset_run_state(self):
        self.particles = []
        self.particle_acc = 0.0
        self.splashes = []
        self.flares = []
        self.dusts = []
        self.mound_peak_offset = 0.0
        self.flash_end = 0.0
        self._completion_triggered = False

    def set_duration(self, d):
        try:
            d = float(d)
        except (TypeError, ValueError):
            return False
        if d <= 0:
            return False
        d = min(d, 360000)
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

    def toggle_sound(self):
        self.sound_on = not self.sound_on
        if self.sound_on and self.running:
            self._play_sound()
        elif not self.sound_on:
            self._stop_sound()
        return self.sound_on

    def _play_sound(self):
        if self._sound is not None:
            self._sound.play()

    def _stop_sound(self):
        if self._sound is not None:
            self._sound.stop()

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
                           'sound_on': self.sound_on}, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------- tick / 物理 ----------

    def tick(self, _dt_kivy):
        if not self._geom_ready:
            return
        now = time.time()
        dt = min(0.05, now - self.last_frame)   # 物理限幅,防卡顿后飞跳
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
                app = App.get_running_app()
                if app is not None:
                    app.on_run_state_changed()
        self.update_particles(dt)
        self.redraw()
        app = App.get_running_app()
        if app is not None:
            app.update_time(max(0.0, self.duration - self.elapsed), self.duration)

    def _spawn_dust(self):
        mound_top = self.get_mound_top_y()
        cx = self._cx
        w = self._sand_half_w(mound_top, self._lower_y_c)
        now = time.time()
        for _ in range(DUST_COUNT):
            self.dusts.append({
                "x": cx + random.uniform(-w * 0.7, w * 0.7),
                "y": mound_top + random.uniform(0, 5),
                "vx": random.uniform(-25, 25),
                "vy": random.uniform(20, 60),   # 向上喷(Kivy y 向上为正)
                "end": now + DUST_LIFETIME,
            })

    def update_particles(self, dt):
        if not self._geom_ready:
            return
        cx = self._cx
        mound_top = self.get_mound_top_y()
        remaining = self.get_remaining()
        now = time.time()
        neck_w = self.neck_w
        ow = self._ow
        gen_y = self._upper_ball_cut   # 粒子从上球截口起落

        if self.running and remaining > 0:
            rate = 600 * self.speed_factor
            if remaining < 0.08:
                rate *= max(0.1, (remaining / 0.08) ** 0.5)
            self.particle_acc += dt * rate
            x_clip = max(1.0, neck_w - ow)
            while self.particle_acc >= 1:
                self.particle_acc -= 1
                x_off = random.uniform(-x_clip, x_clip)
                vy0 = -(random.uniform(90, 120) if random.random() < 0.05
                        else random.uniform(35, 60))   # 向下为负
                self.particles.append({
                    "x": cx + x_off, "x_offset": x_off,
                    "y": gen_y + random.uniform(-2, 1),
                    "vy": vy0,
                    "wobble_phase": random.uniform(0, math.tau),
                    "wobble_amp": random.uniform(0.4, 1.0),
                    "is_light": random.random() < 0.10,
                    "size": (2 if random.random() < 0.85 else 1) if x_clip >= 3.0 else 1,
                })

        g = -450.0   # 重力向下(Kivy y 向上 → 负)
        new_list = []
        for p in self.particles:
            p["vy"] += g * dt
            p["y"] += p["vy"] * dt
            fallen_dist = max(0.0, gen_y - p["y"])
            # 假物理直觉: 沙出管口后自然散开,略微扩张 + 增加散乱感
            if p["y"] > self._lower_ball_cut:
                # 管内: 管壁约束
                spread = 1.0
                wobble_extra = 0.0
            else:
                below_tube = self._lower_ball_cut - p["y"]
                # 出管后逐渐扩张(最多扩到 1.45x),沙子越落越散
                spread = 1.0 + min(0.45, below_tube * 0.0025)
                # 出管后 wobble 递增,制造自然散乱沙流
                wobble_extra = min(3.5, below_tube * 0.018)
                # 触底喇叭口
                dist_to_floor = p["y"] - mound_top
                if 0 < dist_to_floor < 30:
                    spread += (1 - dist_to_floor / 30) * 0.5
            wobble = math.sin(fallen_dist * 0.07 + p["wobble_phase"]) * (p["wobble_amp"] + wobble_extra)
            p["x"] = cx + p["x_offset"] * spread + wobble

            # 横向 clamp: 管内壁 / 进下球随球内壁平滑过渡
            tube_lim = max(1.0, neck_w - ow)
            if p["y"] > self._lower_ball_cut:
                lim = tube_lim
            else:
                raw_ball = self._sand_half_w(p["y"], self._lower_y_c)
                below = self._lower_ball_cut - p["y"]
                t = min(1.0, below / 30.0)
                lim = tube_lim + (max(tube_lim, raw_ball) - tube_lim) * t
            lim = max(0.2, lim - p["size"] / 2.0)
            off = p["x"] - cx
            p["x"] = cx + max(-lim, min(lim, off))

            if p["y"] <= mound_top + 1:   # 触底
                if mound_top > self._lower_sand_bot + 1:
                    self.mound_peak_offset = self.mound_peak_offset * 0.97 + (p["x"] - cx) * 0.03
                if random.random() < 0.25:
                    self.flares.append({"x": p["x"], "y": mound_top, "end": now + 0.08})
                if random.random() < 0.50:
                    self.splashes.append({
                        "x": p["x"], "y": mound_top,
                        "vx": random.uniform(-35, 35),
                        "vy": random.uniform(55, 110),   # 反弹向上
                        "size": random.choice([1, 1, 2]),
                    })
                continue
            new_list.append(p)
        self.particles = new_list

        new_splashes = []
        for s in self.splashes:
            s["vy"] += g * dt
            s["y"] += s["vy"] * dt
            s["x"] += s["vx"] * dt
            half = self._sand_half_w(s["y"], self._lower_y_c)
            if abs(s["x"] - cx) > half - 1:
                continue
            if s["vy"] < 0 and s["y"] <= mound_top + 1:
                continue
            if s["y"] < self._lower_sand_bot or s["y"] > self._lower_sand_top - 5:
                continue
            new_splashes.append(s)
        self.splashes = new_splashes

        self.flares = [f for f in self.flares if f["end"] > now]

        new_dusts = []
        for d in self.dusts:
            d["vy"] += g * dt
            d["y"] += d["vy"] * dt
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
            tube_top = uyc - R + ow
            tube_bot = lyc + R - ow
            Color(*glass_fill)
            Rectangle(pos=(cx - nw, tube_bot), size=(2 * nw, tube_top - tube_bot))
            Color(*glass_out)
            Rectangle(pos=(cx - nw, tube_bot), size=(ow, tube_top - tube_bot))
            Rectangle(pos=(cx + nw - ow, tube_bot), size=(ow, tube_top - tube_bot))

    # ---------- 渲染 ----------

    def redraw(self):
        self.canvas.clear()
        if not self._geom_ready:
            return
        cx, Ri = self._cx, self._R_inner
        uyc, lyc = self._upper_y_c, self._lower_y_c
        remaining = self.get_remaining()
        now = time.time()
        nw = self.neck_w
        ow = self._ow

        with self.canvas:
            # --- 1. 上沙弓形 ---
            if remaining > 0.001:
                upper_eff = max(0.0, min(1.0, self.elapsed / self.duration)) if self.duration > 0 else 0
                cut_y = self._upper_sand_bot + (self._upper_sand_top - self._upper_sand_bot) * (1 - self._raw_height_ratio(upper_eff))
                self._draw_sand_chord(uyc, cut_y)

            # --- 2. 下沙堆弓形 ---
            h_mound = self._mound_height_px()
            if h_mound > 0.5:
                self._draw_sand_chord(lyc, self._lower_sand_bot + h_mound)

            # --- 3. 颈部沙柱 ---
            if self.elapsed > 0 and remaining > 0.001:
                tsw = max(1.0, nw - ow)
                Color(*self.sand_base)
                Rectangle(pos=(cx - tsw, self._lower_sand_top),
                          size=(2 * tsw, self._upper_sand_bot - self._lower_sand_top))

            # --- 4. 沙流粒子(按颜色排序,减少 draw call) ---
            n_colors = len(self._color_table)
            neck_span = max(1.0, self._neck_y - self._glass_bot)
            div = neck_span / n_colors
            for p in self.particles:
                if p["is_light"]:
                    p["_sk"] = (-1, p["size"])
                else:
                    idx = int((self._neck_y - p["y"]) / div)
                    p["_sk"] = (max(0, min(n_colors - 1, idx)), p["size"])
            self.particles.sort(key=lambda p: p["_sk"])
            for p in self.particles:
                if p["is_light"]:
                    Color(*self.sand_light)
                else:
                    Color(*self._color_table[p["_sk"][0]])
                trail = max(2.0, abs(p["vy"]) * 0.08)
                top_y_p = min(self._upper_ball_cut, p["y"] + trail)
                if top_y_p <= p["y"]:
                    continue
                Line(points=[p["x"], p["y"], p["x"], top_y_p], width=p["size"])

            # --- 6. splash 反弹粒子 ---
            Color(*self.sand_light)
            for s in self.splashes:
                sz = s["size"]
                Rectangle(pos=(s["x"] - sz / 2, s["y"] - sz / 2), size=(sz, sz))

            # --- 7. 触底闪光 ---
            for f in self.flares:
                life = max(0.0, f["end"] - now) / 0.08
                Color(self.sand_light[0], self.sand_light[1], self.sand_light[2], 0.45 * life)
                sz = 4 + life * 2
                Rectangle(pos=(f["x"] - sz / 2, f["y"] - sz / 2), size=(sz, sz))

            # --- 8. 完成尘埃 ---
            Color(*self.sand_light)
            for d in self.dusts:
                Rectangle(pos=(d["x"], d["y"]), size=(dp(1.2), dp(1.2)))

            # --- 9. 颈部高光(漏完可见) ---
            if remaining <= 0.001:
                Color(0.8, 0.8, 0.8, 1)
                Line(points=[cx - nw + 1, self._neck_y - 7, cx - nw + 1, self._neck_y + 7], width=1)
                Line(points=[cx + nw - 1, self._neck_y - 7, cx + nw - 1, self._neck_y + 7], width=1)

            # --- 10. 暂停遮罩 ---
            if not self.running and 0 < self.elapsed < self.duration:
                Color(*hex_rgb(BG_COLOR), 0.55)
                Rectangle(pos=self.pos, size=self.size)

            # --- 11. 完成闪烁 ---
            if now < self.flash_end:
                Color(1, 1, 1, 0.25)
                Rectangle(pos=self.pos, size=self.size)

    def _draw_sand_chord(self, yc, cut_y):
        """Stencil 裁出真圆弓形"""
        Ri = self._R_inner
        cx = self._cx
        bottom = yc - Ri
        if cut_y <= bottom:
            return
        if cut_y >= yc + Ri:
            Color(*self.sand_base)
            Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
            return
        StencilPush()
        Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
        StencilUse()
        Color(*self.sand_base)
        Rectangle(pos=(cx - Ri, bottom), size=(2 * Ri, cut_y - bottom))
        StencilUnUse()
        Ellipse(pos=(cx - Ri, bottom), size=(2 * Ri, 2 * Ri))
        StencilPop()


# ---------- App / UI(v2 布局: 色块在上, 控件在下) ----------

class HourglassApp(App):
    title = "跳跳的沙漏"

    def build(self):
        if platform != "android":
            try:
                Window.size = (400, 800)
            except Exception:
                pass
        Window.clearcolor = (*hex_rgb(BG_COLOR), 1)

        self.hourglass = HourglassWidget(size_hint=(1, 1))
        cfg = self.hourglass.load_config()
        if isinstance(cfg.get('duration'), (int, float)) and cfg['duration'] > 0:
            self.hourglass.duration = cfg['duration']
            self.hourglass._rebuild_height_table()
        if 'sound_on' in cfg:
            self.hourglass.sound_on = bool(cfg['sound_on'])
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
        self.sound_btn = Button(text="音效:开" if self.hourglass.sound_on else "音效:关",
                                size_hint=(None, 1), width=dp(74), font_size=sp(15),
                                background_normal="",
                                background_color=(*POPUP_GOLD_SEL[:3], 0.92) if self.hourglass.sound_on
                                                 else (0.718, 0.686, 0.643, 1),
                                color=POPUP_TEXT)
        self.sound_btn.bind(on_press=self.on_toggle_sound)
        bottom.add_widget(self.sound_btn)
        bottom.add_widget(Widget())   # spacer
        self.start_btn = Button(text="开始", size_hint=(None, 1), width=dp(74),
                                font_size=sp(16), bold=True,
                                background_normal="",
                                background_color=(0.353, 0.620, 0.243, 1), color=(1, 1, 1, 1))
        self.start_btn.bind(on_press=self.on_toggle)
        bottom.add_widget(self.start_btn)
        reset_btn = Button(text="重置", size_hint=(None, 1), width=dp(74), font_size=sp(16))
        reset_btn.bind(on_press=self.on_reset)
        bottom.add_widget(reset_btn)
        root.add_widget(bottom)

        self._mark_selected(color_name)
        return root

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
            mult = max(1, min(100, round(sec / base_val)))
            total = base_val * mult
            diff = abs(total - sec)
            if diff < best_diff:
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

    def _pick_duration(self, sec, popup):
        popup.dismiss()
        if self.hourglass.set_duration(sec):
            self.hourglass.save_config(self._selected_color_name())
        self.duration_btn.text = _fmt_duration(self.hourglass.duration)
        self.on_run_state_changed()

    def on_toggle(self, *_):
        self.hourglass.toggle()
        self.on_run_state_changed()

    def on_reset(self, *_):
        self.hourglass.reset()
        self.on_run_state_changed()

    def on_run_state_changed(self):
        if self.hourglass.running:
            self.start_btn.text = "暂停"
            self.start_btn.background_color = (0.851, 0.557, 0.243, 1)
        else:
            self.start_btn.text = "开始"
            self.start_btn.background_color = (0.353, 0.620, 0.243, 1)

    def on_toggle_sound(self, *_):
        on = self.hourglass.toggle_sound()
        self.sound_btn.text = "音效:开" if on else "音效:关"
        self.sound_btn.background_color = (*POPUP_GOLD_SEL[:3], 0.92) if on else (0.718, 0.686, 0.643, 1)
        self.sound_btn.color = POPUP_TEXT
        self.hourglass.save_config(self._selected_color_name())

    def on_color(self, base, dark, light, name):
        self.hourglass.set_sand_color(base, dark, light)
        self._mark_selected(name)
        self.hourglass.save_config(name)

    def _mark_selected(self, name):
        for n, btn in self.color_btns:
            btn.text = ("● " + n) if n == name else n

    def update_time(self, remaining_sec, duration):
        self.time_label.text = f"{remaining_sec:.0f}/{duration:.0f}秒"

    def on_pause(self):
        return True

    def on_resume(self):
        return True


if __name__ == "__main__":
    HourglassApp().run()
