"""On-device frame pacing benchmark; no persistent settings are modified."""

import copy
from datetime import datetime
import gc
import hashlib
import math
import os
import sys
import time

from app_version import APP_VERSION

from kivy.clock import Clock
from kivy.config import Config
from kivy.core.window import Window
from kivy.core.text import Label as CoreLabel
from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.widget import Widget
from kivy.utils import platform as runtime_platform


PERIODS = (1, 5, 15)
REPORT_REVISION = 2


def benchmark_environment(widget):
    environment = {
        "report_revision": REPORT_REVISION,
        "app_version": APP_VERSION,
        "platform": runtime_platform,
        "window_pixels": tuple(Window.size),
        "maxfps": Config.get("graphics", "maxfps"),
        "clock_resolution_s": round(Clock.get_resolution(), 6),
        "vsync": Config.get("graphics", "vsync"),
        "python": sys.version.split()[0],
    }
    source = sys.modules.get(type(widget).__module__)
    path = getattr(source, "_benchmark_source_path", getattr(source, "__file__", None))
    if path:
        try:
            with open(path, "rb") as stream:
                environment["code_hash"] = hashlib.sha256(stream.read()).hexdigest()[:12]
        except OSError:
            pass
    if runtime_platform == "android":
        try:
            from jnius import autoclass, cast
            build = autoclass("android.os.Build")
            version = autoclass("android.os.Build$VERSION")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            display = activity.getWindowManager().getDefaultDisplay()
            power = cast("android.os.PowerManager", activity.getSystemService("power"))
            environment.update(
                model=str(build.MODEL), manufacturer=str(build.MANUFACTURER),
                android_sdk=int(version.SDK_INT),
                refresh_hz=round(float(display.getRefreshRate()), 1),
                power_save=bool(power.isPowerSaveMode()))
            if version.SDK_INT >= 29:
                environment["thermal_status"] = int(power.getCurrentThermalStatus())
        except Exception as exc:
            environment["device_info_error"] = type(exc).__name__
    return environment


def format_frame_diagnostics(result):
    trace = result.get("frame_trace", [])
    details = result.get("slowest_frame_details", [])
    means = result.get("stage_mean_ms", {})
    if not trace and not details and not means:
        return ""
    lines = ["诊断耗时(ms): " + " / ".join(
        f"{label} {means.get(key, 0):.2f}" for label, key in (
            ("物理", "physics_ms"), ("图元", "update_draw_ms"),
            ("Canvas", "canvas_ms"), ("前次Swap", "previous_swap_ms")))]
    if trace:
        peak = max(frame.get("particles", 0) for frame in trace)
        average = sum(frame.get("particles", 0) for frame in trace) / len(trace)
        peak_splash = max(frame.get("splashes", 0) for frame in trace)
        gc_peak = max(frame.get("gc_ms", 0) for frame in trace)
        gc_full = sum(frame.get("gc_generation", -1) == 2 for frame in trace)
        long_frames = sum(frame["frame_ms"] > 25 for frame in trace)
        lines.extend((
            f"存活粒子 平均 {average:.0f} / 峰值 {peak}；飞溅峰值 {peak_splash}",
            f">25ms {long_frames} 帧；GC最大 {gc_peak:.2f}ms / 全量GC涉及 {gc_full} 帧"))
    digest = format_benchmark_digest(result)
    if digest:
        lines.append(digest)
    if details:
        lines.append("最慢帧明细(ms):")
        for frame in details:
            lines.append(
                f"t={frame.get('elapsed_s', 0):.2f}s 总={frame['frame_ms']:.2f} "
                f"粒子={frame.get('particles', 0)} 飞溅={frame.get('splashes', 0)} "
                f"物理={frame.get('physics_ms', 0):.2f} 图元={frame.get('update_draw_ms', 0):.2f} "
                f"Canvas={frame.get('canvas_ms', 0):.2f} 前次Swap={frame.get('previous_swap_ms', 0):.2f} "
                f"GC={frame.get('gc_ms', 0):.2f}/代{frame.get('gc_generation', -1)}")
    return "\n".join(lines)


def format_benchmark_result(period, result=None):
    if result is None:
        return f"{period} 秒\n待测试"
    avg, low = result["average_fps"], result["one_percent_low_fps"]
    avg_text = f"{avg:.1f}" if avg is not None else "--"
    low_text = f"{low:.1f}" if low is not None else "--"
    slow = " / ".join(f"{fps:.1f}" for fps in result["slowest_five_fps"]) or "--"
    return (f"{period} 秒  ·  {result['frames']} 帧\n"
            f"平均 {avg_text} FPS    1% low {low_text} FPS\n"
            f"最慢 5 帧 FPS:\n{slow}")


def format_benchmark_report(results, cancelled=False):
    by_period = {r["period"]: r for r in results}
    blocks = [f"Benchmark v{APP_VERSION} / 诊断报告 v{REPORT_REVISION}"]
    if results and results[0].get("environment"):
        environment = results[0]["environment"]
        blocks.append("环境: " + " / ".join(
            f"{key}={value}" for key, value in environment.items()))
    for period in PERIODS:
        result = by_period.get(period)
        block = format_benchmark_result(period, result)
        if result:
            diagnostic = format_frame_diagnostics(result)
            if diagnostic:
                block += "\n" + diagnostic
            end_state = result.get("environment_end", {})
            state_keys = ("refresh_hz", "thermal_status", "power_save")
            if any(key in end_state for key in state_keys):
                block += "\n结束状态: " + " ".join(
                    f"{key}={end_state.get(key, '--')}" for key in state_keys)
        blocks.append(block)
    if results:
        blocks.append("采样为应用侧提交间隔；Canvas含驱动等待，前次Swap属于上一帧，GC可能包含在各阶段内。")
    if cancelled:
        blocks.append("测试已取消")
    return "\n\n".join(blocks)


STAGE_KEYS = ("physics_ms", "update_draw_ms", "canvas_ms", "previous_swap_ms")


def benchmark_digest(result, bin_seconds=0.5):
    """把逐帧 trace 压成便于定位瓶颈的统计量(分位/超阈值/残差/相关性/分桶)。"""
    trace = result.get("frame_trace") or []
    if not trace:
        return {}
    frame_ms = sorted(frame["frame_ms"] for frame in trace)
    count = len(frame_ms)

    def percentile(q):
        return frame_ms[min(count - 1, max(0, int(round(q * (count - 1)))))]

    # 帧时间减去四个被探针包住的阶段与 GC 后剩下的部分:
    # 若某一帧这一项突然变大,说明卡顿不在渲染/物理里(例如系统调度、锁、纹理上传)。
    residual = [frame["frame_ms"]
                - sum(frame.get(key, 0) for key in STAGE_KEYS)
                - frame.get("gc_ms", 0) for frame in trace]
    # 相关性必须按"同一帧"配对, 所以这里用未排序的帧时间(上面的 frame_ms 已排序)。
    particles = [frame.get("particles", 0) for frame in trace]
    per_frame_ms = [frame["frame_ms"] for frame in trace]
    mean_p = sum(particles) / count
    mean_f = sum(per_frame_ms) / count
    cov = sum((p - mean_p) * (f - mean_f)
              for p, f in zip(particles, per_frame_ms))
    var_p = sum((p - mean_p) ** 2 for p in particles)
    var_f = sum((f - mean_f) ** 2 for f in per_frame_ms)
    correlation = cov / math.sqrt(var_p * var_f) if var_p > 0 and var_f > 0 else 0.0
    buckets = {}
    for frame in trace:
        buckets.setdefault(int(frame["elapsed_s"] / bin_seconds), []).append(frame)
    rows = []
    for index in sorted(buckets):
        group = buckets[index]
        size = len(group)
        rows.append({
            "t0": round(index * bin_seconds, 2),
            "frames": size,
            "fps": round(1000.0 * size / sum(f["frame_ms"] for f in group), 1),
            "frame_ms": round(sum(f["frame_ms"] for f in group) / size, 2),
            "particles": round(sum(f.get("particles", 0) for f in group) / size),
            "splashes": round(sum(f.get("splashes", 0) for f in group) / size),
            **{key: round(sum(f.get(key, 0) for f in group) / size, 2) for key in STAGE_KEYS},
        })
    return {
        "frames": count,
        "p50_ms": round(percentile(0.50), 2),
        "p90_ms": round(percentile(0.90), 2),
        "p99_ms": round(percentile(0.99), 2),
        "max_ms": round(frame_ms[-1], 2),
        "over_60hz": sum(1 for ms in frame_ms if ms > 1000 / 60),
        "over_120hz": sum(1 for ms in frame_ms if ms > 1000 / 120),
        "over_25ms": sum(1 for ms in frame_ms if ms > 25),
        "over_50ms": sum(1 for ms in frame_ms if ms > 50),
        "residual_mean_ms": round(sum(residual) / count, 2),
        "residual_max_ms": round(max(residual), 2),
        "particle_peak": max(particles),
        "particle_mean": round(mean_p),
        "particle_frame_corr": round(correlation, 3),
        "bins": rows,
    }


def format_benchmark_digest(result, bins=False):
    digest = benchmark_digest(result)
    if not digest:
        return ""
    lines = [
        f"帧时间分位(ms): p50={digest['p50_ms']} p90={digest['p90_ms']} "
        f"p99={digest['p99_ms']} max={digest['max_ms']}",
        f"超阈值帧数: >8.33ms {digest['over_120hz']} / >16.7ms {digest['over_60hz']} "
        f"/ >25ms {digest['over_25ms']} / >50ms {digest['over_50ms']}",
        f"阶段残差(帧时间-物理-图元-Canvas-Swap-GC): 均值 {digest['residual_mean_ms']}ms "
        f"/ 最大 {digest['residual_max_ms']}ms",
        f"粒子峰值 {digest['particle_peak']} / 均值 {digest['particle_mean']};"
        f" 粒子数↔帧时间 相关 r={digest['particle_frame_corr']:+.3f}",
    ]
    if bins:
        lines.append("分桶表 t0s,frames,avgFPS,frame_ms,particles,splashes,"
                     + ",".join(STAGE_KEYS))
        for row in digest["bins"]:
            lines.append(",".join(str(row[key]) for key in (
                "t0", "frames", "fps", "frame_ms", "particles", "splashes",
                *STAGE_KEYS)))
    return "\n".join(lines)


def save_benchmark_log(directory, results, cancelled=False):
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(directory, f"benchmark_{stamp}.txt")
    lines = [format_benchmark_report(results, cancelled),
             f"\nPlatform: {sys.platform}; Window: {tuple(Window.size)}",
             f"Timer resolution: {time.get_clock_info('perf_counter').resolution}s",
             f"VSync configuration: {Config.get('graphics', 'vsync')}",
             "Timing units: milliseconds; samples: Window.on_flip intervals.",
             "Host load note: Windows video compression may be running; cross-run FPS is not a controlled comparison."]
    for result in results:
        lines.append(f"\nPeriod: {result['period']}s")
        lines.append(f"Flow renderer: {result.get('flow_renderer', 'line_pool')}")
        means = result.get("stage_mean_ms", {})
        lines.append("Stage means: " + ", ".join(
            f"{name}={value:.3f}" for name, value in means.items()))
        digest = format_benchmark_digest(result, bins=True)
        if digest:
            lines.append(digest)
        lines.append("Slowest frames:")
        for frame in result.get("slowest_frame_details", []):
            lines.append(", ".join(f"{key}={value:.3f}" for key, value in frame.items()))
        lines.append("Frame trace: time_s,frame_ms,FPS,physics_ms,update_draw_ms,canvas_ms,previous_swap_ms,particles,splashes,gc_ms,gc_generation,mound_px,gap_between_frames_ms,gap_tick_tail_ms,gap_draw_to_flip_ms")
        for frame in result.get("frame_trace", []):
            lines.append(",".join(f"{value:.3f}" for value in (
                frame["elapsed_s"], frame["frame_ms"], 1000 / frame["frame_ms"],
                frame.get("physics_ms", 0), frame.get("update_draw_ms", 0),
                frame.get("canvas_ms", 0), frame.get("previous_swap_ms", 0),
                frame["particles"], frame["splashes"], frame.get("gc_ms", 0),
                frame.get("gc_generation", -1), frame.get("mound_px", 0),
                frame.get("gap_between_frames_ms", 0), frame.get("gap_tick_tail_ms", 0),
                frame.get("gap_draw_to_flip_ms", 0))))
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return path


class BenchmarkFrameChart(Widget):
    def __init__(self, result, **kwargs):
        super().__init__(**kwargs)
        self.result = result
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _label(self, text, x, y, right=False):
        label = CoreLabel(text=text, font_size=sp(11), color=(0.20, 0.14, 0.08, 1))
        label.refresh()
        texture = label.texture
        Rectangle(texture=texture, pos=(x - texture.width if right else x, y),
                  size=texture.size)

    def _draw(self, *_):
        self.canvas.clear()
        left, bottom = self.x + dp(32), self.y + dp(20)
        width, height = max(1, self.width - dp(44)), max(1, self.height - dp(34))
        period = self.result["period"]
        with self.canvas:
            for fps in (0, 30, 60, 90):
                y = bottom + height * fps / 90
                Color(0.75, 0.74, 0.70, 1)
                Line(points=[left, y, left + width, y], width=1)
                Color(1, 1, 1, 1)
                self._label(str(fps), left - dp(5), y - dp(6), right=True)
            self._label("FPS", left, self.top - dp(13))
            self._label("0 s", left, self.y)
            self._label(f"{period} s", left + width, self.y, right=True)
            points = []
            for frame in self.result.get("frame_trace", []):
                points.extend((
                    left + width * min(1, frame["elapsed_s"] / period),
                    bottom + height * min(90, 1000 / frame["frame_ms"]) / 90))
            if len(points) >= 4:
                Color(0.18, 0.45, 0.36, 1)
                Line(points=points, width=1)
            # 第二条序列:在途粒子数(按本图峰值归一化)—— 一眼看出"低帧是不是跟着负载走"。
            trace = self.result.get("frame_trace", [])
            peak = max((frame.get("particles", 0) for frame in trace), default=0)
            if peak > 0:
                load_points = []
                for frame in trace:
                    load_points.extend((
                        left + width * min(1, frame["elapsed_s"] / period),
                        bottom + height * frame.get("particles", 0) / peak))
                if len(load_points) >= 4:
                    Color(0.85, 0.45, 0.18, 0.55)
                    Line(points=load_points, width=1)
                Color(1, 1, 1, 1)
                self._label(f"粒子峰值 {peak}", left + dp(4), self.top - dp(26))
            Color(1, 1, 1, 1)
            self._label("绿=FPS 橙=粒子数", left + width, self.top - dp(13), right=True)


def frame_statistics(intervals):
    samples = [dt for dt in intervals if math.isfinite(dt) and dt > 0]
    if not samples:
        return {"frames": 0, "average_fps": None, "one_percent_low_fps": None,
                "slowest_five_fps": [], "slowest_five_ms": []}
    slowest = sorted(samples, reverse=True)
    low_count = max(1, math.ceil(len(samples) * 0.01))
    return {
        "frames": len(samples),
        "average_fps": len(samples) / sum(samples),
        "one_percent_low_fps": low_count / sum(slowest[:low_count]),
        "slowest_five_fps": [1 / dt for dt in slowest[:5]],
        "slowest_five_ms": [dt * 1000 for dt in slowest[:5]],
    }


class BenchmarkHoldArea(Widget):
    def __init__(self, activate, **kwargs):
        super().__init__(**kwargs)
        self._activate = activate
        self._touch = None
        self._hold_event = None

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or self._touch is not None:
            return super().on_touch_down(touch)
        self._touch = touch
        self._origin = touch.pos
        touch.grab(self)
        self._hold_event = Clock.schedule_once(self._held, 3)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            dx, dy = touch.x - self._origin[0], touch.y - self._origin[1]
            if not self.collide_point(*touch.pos) or dx * dx + dy * dy > dp(12) ** 2:
                self._cancel_hold()
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            self._cancel_hold()
            touch.ungrab(self)
            self._touch = None
            return True
        return super().on_touch_up(touch)

    def _cancel_hold(self):
        if self._hold_event is not None:
            self._hold_event.cancel()
            self._hold_event = None

    def _held(self, _dt):
        self._hold_event = None
        # LandLayer 在事件返回后会把 touch.pos 还原为屏幕坐标。
        if self._touch is not None:
            self._activate()


class BenchmarkRunner:
    _STATE_FIELDS = (
        "duration", "elapsed", "running", "particle_acc", "particles", "splashes",
        "flares", "dusts", "mound_peak_offset", "flash_end", "_completion_triggered",
        "completion_enabled",
    )

    def __init__(self, widget, on_case, on_finish, periods=PERIODS):
        self.widget = widget
        self.on_case = on_case
        self.on_finish = on_finish
        self.periods = periods
        self.results = []
        self.active = False
        self._sampling = False
        self._event = None
        self._stages = {}
        self._stamps = {}
        self._gc_ms = 0
        self._gc_generation = -1
        self._gc_start = time.perf_counter()
        self.capture_slow_frames = False

    def start(self):
        if self.active:
            return
        self._saved = {name: copy.deepcopy(getattr(self.widget, name))
                       for name in self._STATE_FIELDS}
        self._paused_at = time.perf_counter()
        self._environment = benchmark_environment(self.widget)
        self.widget.running = False
        self.widget.completion_enabled = False
        self.widget._stop_sound()
        self.active = True
        self._index = 0
        self._install_probes()
        Window.bind(on_flip=self._on_flip)
        self._event = Clock.schedule_once(self._prepare_case, 0)

    def _prepare_case(self, _dt):
        self._event = None
        if not self.active:
            return
        if self._index == len(self.periods):
            self._finish(False)
            return
        if not self.widget.set_duration(self.periods[self._index]):
            self.widget.reset()
        self.widget.redraw()
        self.on_case(self.periods[self._index], self._index + 1)
        # 布局、上轮完成闪光和弹窗退场不计入该轮; 起步动画完整计入。
        self._event = Clock.schedule_once(self._begin_case, 0.5)

    def _begin_case(self, _dt):
        self._event = None
        if not self.active:
            return
        self._intervals = []
        self._frame_details = []
        self._visual_frames = []
        self.widget.toggle()
        self._case_start = time.perf_counter()
        self._last_flip = None
        self._prev_swap_exit = None
        self._gc_ms = 0
        self._gc_generation = -1
        self._sampling = True

    def _on_flip(self, *_):
        if not self._sampling:
            return
        now = time.perf_counter()
        if self._last_flip is None:
            if self.widget.running:
                self._last_flip = now
                self._gc_ms = 0
                self._gc_generation = -1
                return
            self._last_flip = self._case_start
        interval = now - self._last_flip
        self._intervals.append(interval)
        detail = {
            "frame_ms": interval * 1000,
            "elapsed_s": self.widget.elapsed,
            **self._stages,
            **self._probe_gaps(),
            # 粒子的真值是并行数组, pn 就是存活数 —— 不要读 `widget.particles`
            # (那是按需构建的 dict 列表视图, 每帧读会把兼容层开销算进基准)。
            "particles": self.widget.pn,
            "splashes": len(self.widget.splashes),
            "mound_px": self.widget._mound_height_px(),
            "neck_filling": int(self.widget.elapsed < self.widget._neck_fill_time),
            "gc_ms": self._gc_ms,
            "gc_generation": self._gc_generation,
        }
        self._gc_ms = 0
        self._gc_generation = -1
        self._frame_details.append(detail)
        if self.capture_slow_frames and (len(self._visual_frames) < 3 or
                detail["frame_ms"] > self._visual_frames[-1]["frame_ms"]):
            self._visual_frames.append({
                "frame_ms": detail["frame_ms"], "at": time.perf_counter(),
                "state": {name: copy.deepcopy(getattr(self.widget, name))
                          for name in self._STATE_FIELDS},
            })
            self._visual_frames.sort(key=lambda frame: frame["frame_ms"], reverse=True)
            del self._visual_frames[3:]
        self._last_flip = now
        if not self.widget.running and self.widget.elapsed >= self.widget.duration:
            self._sampling = False
            self.results.append({
                "period": self.periods[self._index],
                "environment": self._environment,
                "environment_end": benchmark_environment(self.widget),
                "flow_renderer": getattr(self.widget, "flow_renderer", "line_pool"),
                **frame_statistics(self._intervals),
                "stage_mean_ms": {
                    key: sum(frame.get(key, 0) for frame in self._frame_details)
                         / len(self._frame_details)
                    for key in self._stages},
                "slowest_frame_details": sorted(
                    self._frame_details, key=lambda frame: frame["frame_ms"],
                    reverse=True)[:5],
                "frame_trace": self._frame_details,
                "visual_frames": self._visual_frames,
            })
            self._index += 1
            self._event = Clock.schedule_once(self._prepare_case, 0)

    def cancel(self):
        if self.active:
            self._finish(True)

    def _probe_gaps(self):
        """把 flip->flip 的帧时间拆成: 帧间等待 / tick 内探针外 / 绘制到交换。"""
        st = self._stamps
        if not all(k in st for k in ("physics_ms", "update_draw_ms",
                                     "canvas_ms", "previous_swap_ms")):
            return {}
        p0, _ = st["physics_ms"]
        _, r1 = st["update_draw_ms"]
        c0, c1 = st["canvas_ms"]
        s0, s1 = st["previous_swap_ms"]
        out = {"gap_tick_tail_ms": (c0 - r1) * 1000,
               "gap_draw_to_flip_ms": (s0 - c1) * 1000}
        prev = getattr(self, "_prev_swap_exit", None)
        if prev is not None:
            out["gap_between_frames_ms"] = (p0 - prev) * 1000
        self._prev_swap_exit = s1
        return out

    def _install_probes(self):
        self._probe_methods = []
        gc.callbacks.append(self._gc_probe)
        for target, name, stage in (
                (self.widget, "update_particles", "physics_ms"),
                (self.widget, "redraw", "update_draw_ms"),
                (Window, "on_draw", "canvas_ms"),
                (Window, "flip", "previous_swap_ms")):
            original = getattr(target, name)

            def measured(*args, _original=original, _stage=stage, **kwargs):
                before = time.perf_counter()
                try:
                    return _original(*args, **kwargs)
                finally:
                    after = time.perf_counter()
                    self._stages[_stage] = (after - before) * 1000
                    # 额外记时间戳: 把 flip->flip 里四探针之外的部分拆开
                    self._stamps[_stage] = (before, after)

            self._probe_methods.append((target, name, original))
            setattr(target, name, measured)

    def _remove_probes(self):
        gc.callbacks.remove(self._gc_probe)
        for target, name, original in self._probe_methods:
            setattr(target, name, original)
        self._probe_methods = []

    def _gc_probe(self, phase, info):
        if phase == "start":
            self._gc_start = time.perf_counter()
        else:
            self._gc_ms += (time.perf_counter() - self._gc_start) * 1000
            self._gc_generation = max(self._gc_generation, info["generation"])

    def _finish(self, cancelled):
        self._sampling = False
        self.active = False
        Window.unbind(on_flip=self._on_flip)
        self._remove_probes()
        if self._event is not None:
            self._event.cancel()
            self._event = None
        self.widget._stop_sound()
        for name, value in self._saved.items():
            setattr(self.widget, name, value)
        pause = time.perf_counter() - self._paused_at
        for effect in self.widget.flares + self.widget.dusts:
            effect["end"] += pause
        if self.widget.flash_end:
            self.widget.flash_end += pause
        self.widget.last_frame = time.perf_counter()
        self.widget.last_tick = self.widget.last_frame if self.widget.running else None
        self.widget._rebuild_height_table()
        self.widget.redraw()
        if self.widget.running:
            self.widget._play_sound()
        self.on_finish(self.results, cancelled)
