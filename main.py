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
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform


# ---- 中文字体: 用 name="Roboto" 覆盖 Kivy 默认字体,全局生效 ----
_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fonts", "NotoSansSC-Medium.otf")
try:
    if os.path.exists(_FONT_PATH):
        LabelBase.register(name="Roboto", fn_regular=_FONT_PATH)
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


# ---------- 音效: Android SoundPool 无 gap 循环; 桌面 fallback SoundLoader ----------

class _SoundProxy:
    def __init__(self, wav_path):
        self._is_android = (platform == "android")
        self._sp = None
        self._sound_id = None
        self._stream_id = 0
        self._loaded = False
        self._listener = None   # 存实例属性防 PythonJavaClass 被 GC
        self._kivy_sound = None
        if self._is_android:
            try:
                self._init_soundpool(wav_path)
                return
            except Exception:
                self._sp = None
        try:
            from kivy.core.audio import SoundLoader
            self._kivy_sound = SoundLoader.load(wav_path)
            if self._kivy_sound is not None:
                self._kivy_sound.loop = True
        except Exception:
            self._kivy_sound = None

    def _init_soundpool(self, wav_path):
        from jnius import autoclass, PythonJavaClass, java_method
        SoundPool = autoclass('android.media.SoundPool')
        AudioAttributes = autoclass('android.media.AudioAttributes')
        attrs = (AudioAttributes.Builder()
                 .setUsage(AudioAttributes.USAGE_MEDIA)
                 .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                 .build())
        self._sp = (SoundPool.Builder()
                    .setMaxStreams(1)
                    .setAudioAttributes(attrs)
                    .build())
        self._sound_id = self._sp.load(wav_path, 1)
        outer = self

        class _Listener(PythonJavaClass):
            __javainterfaces__ = ['android/media/SoundPool$OnLoadCompleteListener']
            __javacontext__ = 'app'

            @java_method('(Landroid/media/SoundPool;II)V')
            def onLoadComplete(self, soundpool, sample_id, status):
                if status == 0:
                    outer._loaded = True

        self._listener = _Listener()
        self._sp.setOnLoadCompleteListener(self._listener)

    def play(self):
        if self._sp is not None:
            if self._stream_id or not self._loaded:
                return
            # play(id, lVol, rVol, priority, loop=-1 永久, rate)
            self._stream_id = self._sp.play(self._sound_id, 1.0, 1.0, 1, -1, 1.0)
            return
        if self._kivy_sound is not None:
            try:
                if self._kivy_sound.state != "play":
                    self._kivy_sound.play()
            except Exception:
                pass

    def stop(self):
        if self._sp is not None:
            if self._stream_id:
                try:
                    self._sp.stop(self._stream_id)
                except Exception:
                    pass
                self._stream_id = 0
            return
        if self._kivy_sound is not None:
            try:
                self._kivy_sound.stop()
            except Exception:
                pass


# ---------- 沙漏画布 ----------

class HourglassWidget(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.duration = 60.0
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
        d = min(d, 100000)
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
            # 颗粒流不缩窄,用递增 wobble 制造散乱断续感(≤40% 管径)
            wobble_amp = p["wobble_amp"]
            if p["y"] <= self._lower_ball_cut:
                below_cut = self._lower_ball_cut - p["y"]
                max_extra = max(1.5, (neck_w - ow) * 0.3)
                wobble_amp += min(max_extra, below_cut * 0.012)
            wobble = math.sin(fallen_dist * 0.07 + p["wobble_phase"]) * wobble_amp
            shrink = 1.0
            dist_to_floor = p["y"] - mound_top
            if 0 < dist_to_floor < 30:
                shrink = 1 + (1 - dist_to_floor / 30) * 0.4   # 触底喇叭口微扩
            p["x"] = cx + p["x_offset"] * shrink + wobble

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

        root = BoxLayout(orientation="vertical", spacing=dp(2),
                         padding=[dp(6), dp(4), dp(6), dp(4)])

        # 顶部色块
        top_colors = BoxLayout(orientation="horizontal", size_hint=(1, None),
                               height=dp(44), spacing=dp(3))
        self.color_btns = []
        for name, base, dark, light in SAND_PRESETS:
            btn = Button(text=name, font_size=sp(13), background_normal="",
                         background_color=(*hex_rgb(base), 1), color=fg_for(base))
            btn.bind(on_press=lambda inst, b=base, d=dark, l=light, n=name:
                     self.on_color(b, d, l, n))
            top_colors.add_widget(btn)
            self.color_btns.append((name, btn))
        root.add_widget(top_colors)

        # 倒计时
        self.time_label = Label(
            text=f"{self.hourglass.duration:.0f}s / {self.hourglass.duration:.0f}s",
            font_size=sp(20), bold=True, size_hint=(1, None), height=dp(34),
            color=(0.2, 0.2, 0.2, 1))
        root.add_widget(self.time_label)

        # 沙漏画布
        root.add_widget(self.hourglass)

        # 底部控件
        bottom = BoxLayout(orientation="horizontal", size_hint=(1, None),
                           height=dp(50), spacing=dp(4))
        bottom.add_widget(Label(text="周期:", size_hint=(None, 1), width=dp(48),
                                color=(0.2, 0.2, 0.2, 1), font_size=sp(14)))
        self.duration_input = CenterTextInput(
            text=str(int(self.hourglass.duration)), multiline=False,
            size_hint=(None, 1), width=dp(72), font_size=sp(18), input_filter="float")
        self.duration_input.bind(on_text_validate=self.on_set_duration)
        bottom.add_widget(self.duration_input)
        self.sound_btn = Button(text="音效:开" if self.hourglass.sound_on else "音效:关",
                                size_hint=(None, 1), width=dp(66), font_size=sp(12))
        self.sound_btn.bind(on_press=self.on_toggle_sound)
        bottom.add_widget(self.sound_btn)
        bottom.add_widget(Widget())   # spacer
        self.start_btn = Button(text="开始", size_hint=(None, 1), width=dp(66),
                                font_size=sp(14), bold=True,
                                background_normal="",
                                background_color=(0.353, 0.620, 0.243, 1), color=(1, 1, 1, 1))
        self.start_btn.bind(on_press=self.on_toggle)
        bottom.add_widget(self.start_btn)
        reset_btn = Button(text="重置", size_hint=(None, 1), width=dp(66), font_size=sp(14))
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

    def on_set_duration(self, *_):
        if self.hourglass.set_duration(self.duration_input.text):
            self.hourglass.save_config(self._selected_color_name())
        self.duration_input.text = str(int(self.hourglass.duration))
        self.on_run_state_changed()

    def on_toggle(self, *_):
        # 幂等地先应用周期再 toggle,与输入框 FocusOut 时序无关
        self.hourglass.set_duration(self.duration_input.text)
        self.duration_input.text = str(int(self.hourglass.duration))
        self.hourglass.toggle()
        self.on_run_state_changed()

    def on_reset(self, *_):
        self.hourglass.set_duration(self.duration_input.text)
        self.duration_input.text = str(int(self.hourglass.duration))
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
        self.hourglass.save_config(self._selected_color_name())

    def on_color(self, base, dark, light, name):
        self.hourglass.set_sand_color(base, dark, light)
        self._mark_selected(name)
        self.hourglass.save_config(name)

    def _mark_selected(self, name):
        for n, btn in self.color_btns:
            btn.text = ("● " + n) if n == name else n

    def update_time(self, remaining_sec, duration):
        self.time_label.text = f"{remaining_sec:.0f}s / {duration:.0f}s"

    def on_pause(self):
        return True

    def on_resume(self):
        return True


if __name__ == "__main__":
    HourglassApp().run()
