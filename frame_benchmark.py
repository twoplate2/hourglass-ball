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
TAIL_MIN_FRAMES = 5     # "1% low" 至少平均这么多帧 —— 见 frame_statistics 里的说明
# 🔴 **两轮之间的静置**(2026-10-06 用户: 「性能测试的时候, **等沙子彻底流动完成后的 1 秒
#    之后**再进行下一次测试」)。
#    原来这一轮 `elapsed >= duration` 就**立刻** `schedule_once(_prepare_case, 0)` ⇒
#    下一轮是在"上一轮的沙还在空中/颈部还在排空/飞溅还没停"的状态下起跑的。
#    现在: 每帧检查"彻底静止"(在途粒子 0 + 颈部沙柱空 + 飞溅/尘埃/闪光都空),
#    静止后再等 `SETTLE_AFTER_QUIET` 秒才进下一轮。
SETTLE_AFTER_QUIET = 1.0
# 兜底: 万一有东西永远静止不下来, 不能把整轮 benchmark 挂死。
SETTLE_TIMEOUT = 15.0


def tukey_upper_adjacent(values):
    """Tukey 上栅栏(`Q3 + 1.5*IQR`)以内的**最大值**(EDA 里的"相邻值")。

    离群点判定用箱线图的标准栅栏; 而 EDA 的作图规矩是: 非箱线图的坐标轴范围取
    **相邻值**、不取极值 —— 否则单个尖峰就把整张图压成一条草。
    (Grafana 的"Y 轴按百分位自动缩放"就是为同一件事做的。)
    """
    data = sorted(values)
    if not data:
        return 0.0

    def quantile(p):
        return data[min(len(data) - 1, int(round(p * (len(data) - 1))))]

    fence = quantile(0.75) + 1.5 * (quantile(0.75) - quantile(0.25))
    inside = [value for value in data if value <= fence]
    return max(inside) if inside else data[-1]


def nice_axis_ceiling(value, ticks=4):
    """Heckbert 的 nice numbers(Graphics Gems): 返回 (上界, 步长)。

    步长 = `value/ticks` 的 1/2/5/10 × 10^k 邻值(分数按 1.5 / 3 / 7 三档吸附),
    上界再抬到步长的整数倍。刻度因此总是好读的数, 条数在 3~8 之间浮动。
    """
    if value <= 0:
        return 1.0, 1.0
    raw = value / ticks
    exponent = math.floor(math.log10(raw))
    norm = raw / 10.0 ** exponent
    step = (1 if norm < 1.5 else 2 if norm < 3 else 5 if norm < 7 else 10) * 10.0 ** exponent
    return math.ceil(value / step) * step, step


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
        # 飞溅渲染器与沙流渲染器一样必须记进日志 —— 2026-10-07 踩过:
    # 模块没推到设备上时 main.py **静默回退**, 两条路径量出来一模一样,
    # 而日志里看不出来。
    "splash_renderer": getattr(widget, "splash_renderer", "rect"),
}
    # 纹理上传的量具旋钮也必须记进日志 —— 与上面 `splash_renderer` 同一条教训:
    # **不记的话, "标记文件到底有没有被读到"就只能靠猜**。设备单变量对照全靠它。
    try:
        from flow_batch_experiment import BLIT_REP, BLIT_WIDE
        environment["blit_rep"] = BLIT_REP
        environment["blit_wide"] = int(bool(BLIT_WIDE))
    except Exception as exc:
        environment["blit_probe_error"] = type(exc).__name__
    try:
        from flow_texture_experiment import LAZY_VIEW_OFF
        environment["lazy_view_off"] = int(bool(LAZY_VIEW_OFF))
    except Exception as exc:
        environment["lazy_view_probe_error"] = type(exc).__name__
    source = sys.modules.get(type(widget).__module__)
    # 生成率也要记 —— 它直接决定在途粒子数, 是 A/B 里**唯一那个能同时压 `物理` 与
    # `图元` 两栏**的旋钮。不记的话又回到"标记文件到底有没有被读到"只能靠猜。
    # ⚠️ 这一行**必须在 `source` 之后** —— 放在上面那个 dict 后面会 UnboundLocalError。
    environment["flow_rate"] = getattr(source, "FLOW_BASE_RATE", None)
    # 飞溅存活上限(见 `main.py:_splash_max`) —— 同上, **标记文件必须自证被读到**。
    environment["splash_max"] = getattr(source, "SPLASH_MAX", None)
    # 批处理块预热(见 `main.py:_warm_batches_step`)开没开 —— 同上, **标记文件必须自证被读到**。
    environment["warm"] = int(bool(getattr(source, "WARM_ENABLED", True)))
    # 屏幕给出的**全部**刷新率档位(见 `main.py:_apply_max_refresh_rate`)。
    # 判据: `refresh_hz`(实际拿到的) vs `refresh_modes` 里的最大值 —— 两者不等就说明
    # **面板没跑满**, 而列表里有没有高档决定了"该去改系统设置"还是"该改我们的请求方式"。
    environment["refresh_modes"] = getattr(source, "REFRESH_INFO", None)
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
    tail = result.get("one_percent_low_frames") or 0
    tail_text = f"(最差 {tail} 帧)" if tail else ""
    return (f"{period} 秒  ·  {result['frames']} 帧\n"
            f"平均 {avg_text} FPS    1% low {low_text} FPS{tail_text}\n"
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


def benchmark_log_text(results, cancelled=False):
    """整份日志的文本(逐帧 trace + 分位/超阈值/残差/分桶表 + 环境行)。
    单独拎出来是为了让同一次结果能写到两处(应用私有目录 + 公共 Download)而**同名同内容**。"""
    lines = [format_benchmark_report(results, cancelled),
             f"\nPlatform: {sys.platform}; Window: {tuple(Window.size)}",
             f"Timer resolution: {time.get_clock_info('perf_counter').resolution}s",
             f"VSync configuration: {Config.get('graphics', 'vsync')}",
             f"MaxFPS configuration: {Config.get('graphics', 'maxfps')}",
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
        # 🔴 **最慢几帧的逐段耗时**(2026-10-07): 尾部的钱全在 `图元` 这一栏里, 四栏拆不开,
        #    而用户只能在设备上跑 ⇒ 把分段直接写进日志, 省掉"用户建标记文件"那一步。
        # ⚠️ 语义: 每个名字记的是**距上一次打点**的那一段(标签 = 刚结束的那一段),
        #    **不要把这几项相加** —— 它们首尾相接, 相加就把 `图元` 算了两遍。
        _sm = result.get("slow_marks") or []
        if _sm:
            lines.append("Slowest frame segments (ms, 相邻段首尾相接·不要相加):")
            for ms, seg in _sm:
                lines.append(f"    redraw={ms:.2f}  {seg}")
        lines.append("Frame trace: time_s,frame_ms,FPS,physics_ms,update_draw_ms,canvas_ms,previous_swap_ms,particles,splashes,gc_ms,gc_generation,mound_px,gap_between_frames_ms,gap_tick_tail_ms,gap_draw_to_flip_ms,index_assigns,chunk_clears,flow_chunks,vertex_rebuild_kib")
        for frame in result.get("frame_trace", []):
            lines.append(",".join(f"{value:.3f}" for value in (
                frame["elapsed_s"], frame["frame_ms"], 1000 / frame["frame_ms"],
                frame.get("physics_ms", 0), frame.get("update_draw_ms", 0),
                frame.get("canvas_ms", 0), frame.get("previous_swap_ms", 0),
                frame["particles"], frame["splashes"], frame.get("gc_ms", 0),
                frame.get("gc_generation", -1), frame.get("mound_px", 0),
                frame.get("gap_between_frames_ms", 0), frame.get("gap_tick_tail_ms", 0),
                frame.get("gap_draw_to_flip_ms", 0), frame.get("index_assigns", 0),
                frame.get("chunk_clears", 0), frame.get("flow_chunks", 0),
                frame.get("vertex_rebuild_kib", 0))))
    return "\n".join(lines) + "\n"


def save_benchmark_log(directory, results, cancelled=False):
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(directory, f"benchmark_{stamp}.txt")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(benchmark_log_text(results, cancelled))
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
        trace = self.result.get("frame_trace", [])
        # ⚠️ Y 轴原先是写死的 0/30/60/90, 而且把 FPS **夹在 90**(`min(90, 1000/frame_ms)`)
        # ⇒ 120fps+ 的机器上整条绿线平贴顶部, 零信息(2026-10-03 发现)。
        # ⚠️ 改用 `max` 也不行: **量到的最高帧远高于常态**(实测平均 122fps 而尖峰 260)
        # ⇒ 上限被拉到 320, 曲线挤在下方 1/3, 上半张图全空。
        # 现在按标准做法两步: ① 上界取 **Tukey 上栅栏内的最大值**(相邻值, 剔掉尖峰);
        # ② 再用 **Heckbert nice numbers** 把上界与步长吸附到好读的数。
        # 代价: 极少数尖峰帧会被夹在顶线上(曲线本来就带 `min(fps_top, …)`)。
        rates = [1000.0 / frame["frame_ms"] for frame in trace
                 if (frame.get("frame_ms") or 0) > 0]
        fps_top, fps_step = (nice_axis_ceiling(tukey_upper_adjacent(rates))
                             if rates else (60.0, 15.0))
        fps_ticks = max(1, int(round(fps_top / fps_step)))
        with self.canvas:
            for i in range(fps_ticks + 1):
                y = bottom + height * i / fps_ticks
                Color(0.75, 0.74, 0.70, 1)
                Line(points=[left, y, left + width, y], width=1)
                Color(1, 1, 1, 1)
                self._label(f"{fps_step * i:g}", left - dp(5), y - dp(6), right=True)
            self._label("FPS", left, self.top - dp(13))
            self._label("0 s", left, self.y)
            self._label(f"{period} s", left + width, self.y, right=True)
            points = []
            for frame in trace:
                points.extend((
                    left + width * min(1, frame["elapsed_s"] / period),
                    bottom + height * min(fps_top, 1000 / frame["frame_ms"]) / fps_top))
            if len(points) >= 4:
                Color(0.18, 0.45, 0.36, 1)
                Line(points=points, width=1)
            # (2026-10-03 用户要求) 这里原本还有第二条橙色曲线 = 在途粒子数, 连同"粒子峰值 N"
            # 的标签一起删掉 —— 图上只留 FPS 一条。
            # ⚠️ **逐帧 trace 日志里仍然记 `particles` 那一列**: A/B 分析、以及
            # "慢帧是不是跟着负载走"的诊断都还要用它, 只是不再画到图上。
            Color(1, 1, 1, 1)
            self._label("绿=FPS", left + width, self.top - dp(13), right=True)


def frame_statistics(intervals):
    samples = [dt for dt in intervals if math.isfinite(dt) and dt > 0]
    if not samples:
        return {"frames": 0, "average_fps": None, "one_percent_low_fps": None,
                "slowest_five_fps": [], "slowest_five_ms": []}
    slowest = sorted(samples, reverse=True)
    # ⚠️ 尾部帧数要设**下限**: 1 秒档只有 ~110 帧, ceil(1%) = 2 帧 ⇒ "1% low" 退化成
    # "最慢那一帧"(实测 95.6 而最慢帧 93.6 —— 几乎是同一个数), 还跟上面那行
    # 「最慢 5 帧」重复显示同一信息, 并且会随测试时长漂移(同一段开头测 1s / 5s 给出的值不同)。
    # 下限取 5 帧, 并把**实际用了几帧**一并报出去(界面和日志都能看见)。
    low_count = min(len(slowest), max(TAIL_MIN_FRAMES, math.ceil(len(samples) * 0.01)))
    return {
        "frames": len(samples),
        "average_fps": len(samples) / sum(samples),
        "one_percent_low_fps": low_count / sum(slowest[:low_count]),
        "one_percent_low_frames": low_count,
        "slowest_five_fps": [1 / dt for dt in slowest[:5]],
        "slowest_five_ms": [dt * 1000 for dt in slowest[:5]],
    }


class BenchmarkHoldArea(Widget):
    """长按 3 秒进开发者菜单。**命中区比可见面积大得多**(见 `collide_point`)。

    ## 为什么要把命中区做大(2026-10-07 用户要求)

    用户原话:「把长按版本号出窗口的响应区域**大幅提高**, 至少增加 50% 的长度和宽度」。

    底栏贴着屏幕的**最下沿**(下面只剩根布局 padding dp(6)) —— 那一带正是安卓的
    系统手势区, 手指按在那儿很容易被系统先吃掉, 或者干脆按到屏幕外。而这块地方
    在宽屏上其实**很宽**(`_fit_bottom_widths`: 版本区把余量全吃掉), 所以真正难按的
    是**高度方向**。

    ⇒ 宽 ×1.5(左右各 25%)、高 ×2 且**全部向上长**(不向下, 下面只有 6dp)。
    **只改命中判定, 不动布局尺寸** —— 撑大 `size` 会把四个按钮挤走(`size_hint=(None,1)`
    的定宽子控件在 `BoxLayout` 里不被压缩)。

    ⚠️ 扩大区会盖到相邻按钮(左边两个)与画布底部一条。**两条都不抢**:
    * 落在可用兄弟控件上的点 ⇒ 让给按钮(`_hits_sibling`)。右侧的 `开始/重置` 天然安全
      —— `BoxLayout` 的 `children` 是**倒序**, 它们比本控件先派发; 左边两个在它之后,
      所以必须自己挡, 否则按钮靠里的那几毫米会变成死区。
    * 盖到画布那一条(高 ≈ dp(50))里**有 72.5% 原来是"点沙漏开始/暂停"的热区**(实测
      `tools/_probe_hold_area.py`) ⇒ **只有"按住不放 ≥3 秒"才归我们, 短按一律还给画布**
      (`_forward_tap`)。**扩大长按区不该顺手制造一块死区。**
    """

    HOLD_HIT_W = 1.5      # 命中区宽 = 可见宽 ×1.5(左右各让出 25%)
    HOLD_HIT_UP = 1.0     # 命中区向上多长 = 一个自身高度 ⇒ 总高 = 可见高 ×2

    def __init__(self, activate, **kwargs):
        super().__init__(**kwargs)
        self._activate = activate
        self._touch = None
        self._hold_event = None
        self._fired = False        # 这一下"长按真的触发了"没有(区分"触发"与"被取消")

    def _in_visible(self, pos):
        """点是不是落在**可见矩形**里 —— 判定"这一下要不要还给下面的画布"用。"""
        return Widget.collide_point(self, *pos)

    def _forward_tap(self, touch):
        """把这一下**还给沙漏画布** —— 扩大区长出来的那一条里, **72.5%** 的地方原来
        点一下是"开始/暂停"的热区(实测 `tools/_probe_hold_area.py`)。既然是我们把长按区
        撑大的, 就不该顺手把这块变成死区 ⇒ **只有"按住不放"才归我们, 短按一律透传**。

        ⚠️ 只透传给 `hourglass` 一个目标, 不做通用"找底下那个控件"(通用重放要模拟整个
           `children` 遍历 + 旋转层变换, 风险远大于收益)。那一带上本来就只有画布。
        """
        from kivy.app import App
        app = App.get_running_app()
        hg = getattr(app, "hourglass", None) if app is not None else None
        if hg is None:
            return
        try:
            if hg.on_touch_down(touch):
                hg.on_touch_up(touch)
        except Exception:
            pass

    def _hit_rect(self):
        """(x0, y0, x1, y1) —— 放大后的命中矩形, 单位与 `collide_point` 一致(父坐标)。"""
        grow = self.width * (self.HOLD_HIT_W - 1.0) * 0.5
        return (self.x - grow, self.y,
                self.right + grow, self.top + self.height * self.HOLD_HIT_UP)

    def _hits_sibling(self, x, y):
        """这一点是不是落在某个**可用兄弟控件**上 —— 是的话让给它们, 别把按钮边缘变成死区。"""
        parent = self.parent
        if parent is None:
            return False
        for sib in parent.children:
            if sib is self or getattr(sib, "disabled", False):
                continue
            try:
                if sib.collide_point(x, y):
                    return True
            except Exception:
                continue
        return False

    def collide_point(self, x, y):
        # 原本的矩形先判 —— 视觉上"版本号"那一块永远算命中。
        if super().collide_point(x, y):
            return True
        x0, y0, x1, y1 = self._hit_rect()
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        return not self._hits_sibling(x, y)

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
            fired, pos = self._fired, touch.pos
            self._cancel_hold()
            touch.ungrab(self)
            self._touch = None
            self._fired = False
            # 短按(没到 3 秒)且落点在**可见矩形之外** ⇒ 这一下不是给我们的, 还给画布。
            if not fired and not self._in_visible(pos):
                self._forward_tap(touch)
            return True
        return super().on_touch_up(touch)

    def _cancel_hold(self):
        if self._hold_event is not None:
            self._hold_event.cancel()
            self._hold_event = None

    def _held(self, _dt):
        self._hold_event = None
        self._fired = True
        # LandLayer 在事件返回后会把 touch.pos 还原为屏幕坐标。
        if self._touch is not None:
            self._activate()


class BenchmarkRunner:
    _STATE_FIELDS = (
        "duration", "elapsed", "running", "particle_acc", "particles", "splashes",
        "flares", "dusts", "mound_peak_offset", "_completion_triggered",
        "completion_enabled",
        # ⚠️ **不能漏 `_done_at`**(2026-10-05 r9-1号 查出): 它是"颈管开始排空"的时刻戳,
        #    漏了它 ⇒ 恢复后仍是 20 多秒前的旧值 ⇒ `_neck_sand_side()` 算出 f=0 返回空
        #    ⇒ **颈管沙柱不画了**。实测 2/2 复现(前置状态为"暂停"时肉眼可见:
        #    颈管中轴 x=540,y=1200 从沙色 (195,211,153) 变成玻璃色 (244,245,236))。
        "_done_at",
        "_sand_released", "_sand_landed", "_sand_pending", "_sand_active",
        "_sand_timing",
        "_sand_resized",
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
        self._settle_start = None      # 本轮结束、开始等"彻底静止"的时刻
        self._quiet_since = None       # 第一次观测到"彻底静止"的时刻

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

    def _flow_settled(self):
        """上一轮的沙**彻底流动完成**了吗。

        四个都要空: 在途主流粒子 / 颈部沙柱 / 飞溅颗粒 / 尘埃与闪光。
        ⚠️ **不能只看 `running`** —— 计时归零那一刻它们全都还在。
        """
        w = self.widget
        # ⚠️ 同理: `w.splashes` 是 property, 每帧重建 ~1700 个 dict。这里每帧都调
        #    (静止等待可能持续好几秒) ⇒ 改成真值 `_sn`。
        if w.pn or w._sn or w.dusts or w.flares:
            return False
        return not w._neck_sand_side()

    def _await_settle(self, _dt):
        """每帧轮询, 静止后再多等 `SETTLE_AFTER_QUIET` 秒才进下一轮。"""
        self._event = None
        if not self.active:
            return
        now = time.perf_counter()
        if self._flow_settled():
            if self._quiet_since is None:
                self._quiet_since = now
            if now - self._quiet_since >= SETTLE_AFTER_QUIET:
                self._prepare_case(0)
                return
        else:
            self._quiet_since = None
        if self._settle_start is not None and now - self._settle_start > SETTLE_TIMEOUT:
            self._prepare_case(0)          # 兜底: 不许挂死整轮 benchmark
            return
        self._event = Clock.schedule_once(self._await_settle, 0)

    def _prepare_case(self, _dt):
        self._event = None
        if not self.active:
            return
        self._settle_start = None
        self._quiet_since = None
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
        self._slow_marks = []          # 逐段耗时的"最慢几帧"(见 `_install_probes`)
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
            # 🔴 **必须是 `_sn`, 不能是 `len(self.widget.splashes)`**(2026-10-07 4号专家
            #    实测查出)。`splashes` 是**每次访问都重建一批 dict** 的取证视图, 它自己的
            #    docstring 就写着「**别再往热路径上加它的读者**」—— 而这里每帧读一次。
            #    设备实测代价 = **0.30ms + 2.2µs × 飞溅数**, 15s 档均值 **2.74 ms/帧**
            #    (比 `物理 2.00` 或 `图元 2.48` 单独一项都大), 全部落进四栏**之外**的
            #    阶段残差里, 把"残差"这一栏彻底带偏。
            #    ⚠️ 上一行刚写着"不要读 `widget.particles`(按需构建的 dict 视图)",
            #       下一行就犯了同一个错 —— 这条注释就是为此留的。
            "splashes": self.widget._sn,
            "mound_px": self.widget._mound_height_px(),
            "neck_filling": int(self.widget.elapsed < self.widget._neck_fill_time),
            "gc_ms": self._gc_ms,
            "gc_generation": self._gc_generation,
            **self._flow_rebuild_stats(),
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
                "slow_marks": list(self._slow_marks),
                "visual_frames": self._visual_frames,
            })
            self._index += 1
            # 🔴 不再立刻进下一轮 —— 先等"沙子彻底流动完成"再等 1 秒(见模块顶部常量)。
            self._settle_start = time.perf_counter()
            self._quiet_since = None
            self._event = Clock.schedule_once(self._await_settle, 0)

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
        # 🔴 **逐段耗时收集器**(2026-10-07): 用户平板 165Hz 的尾部成本 100% 落在 `图元`
        #    这一栏里, 而四栏**拆不开它** —— 不拆开就只能猜该改哪块。
        #    挂钩方式与 `prof_android._mark` 逐字一致(见 `main._MarkCollector`),
        #    只是**基准自己开灯**: 用户照常点"开始测试"就能把分段带进日志。
        #    默认 `_PROF_MARK is None` ⇒ 出货路径零开销; 这里开、`_remove_probes` 关。
        self._marks = None
        self._mark_src = sys.modules.get(type(self.widget).__module__)
        _mk = getattr(self._mark_src, "_MarkCollector", None)
        if _mk is not None:
            self._marks = _mk()
            self._mark_src._PROF_MARK = self._marks
        self._slow_marks = []          # [(帧时间, 分段串)] 只留最慢的几帧
        self._phys_ran = False         # 本帧 `update_particles` 跑过没有(见 `measured` 里那段)
        for target, name, stage in (
                (self.widget, "update_particles", "physics_ms"),
                (self.widget, "redraw", "update_draw_ms"),
                (Window, "on_draw", "canvas_ms"),
                (Window, "flip", "previous_swap_ms")):
            original = getattr(target, name)

            def measured(*args, _original=original, _stage=stage, **kwargs):
                before = time.perf_counter()
                if _stage == "physics_ms" and self._marks is not None:
                    self._marks.reset()          # 一帧的起点(物理在前, 重绘在后)
                    self._phys_ran = True
                elif (_stage == "update_draw_ms" and self._marks is not None
                        and not self._phys_ran):
                    # 🔴 **没在跑的那些帧(轮间静止等待)不调 `update_particles`** ⇒ 上一条的
                    #    reset 也就不发生 ⇒ 打点会**跨帧累加**, 于是"最慢几帧"里混进一堆
                    #    假值(实测把 26ms 的读数印成"沙流那一段 14.9ms")。
                    #    这里补一次: 物理没跑过就从这一帧重开。
                    self._marks.reset()
                try:
                    return _original(*args, **kwargs)
                finally:
                    after = time.perf_counter()
                    self._stages[_stage] = (after - before) * 1000
                    # 额外记时间戳: 把 flip->flip 里四探针之外的部分拆开
                    self._stamps[_stage] = (before, after)
                    if _stage == "update_draw_ms" and self._marks is not None:
                        self._phys_ran = False
                        snap = self._marks.snapshot()
                        if snap:
                            _ms = (after - before) * 1000.0
                            self._slow_marks.append((
                                _ms,
                                " ".join("%s=%.2f" % (k, v)
                                         for k, v in sorted(snap.items(),
                                                            key=lambda kv: -kv[1])[:8])))
                            self._slow_marks.sort(key=lambda t: -t[0])
                            del self._slow_marks[5:]

            self._probe_methods.append((target, name, original))
            setattr(target, name, measured)

    def _remove_probes(self):
        gc.callbacks.remove(self._gc_probe)
        for target, name, original in self._probe_methods:
            setattr(target, name, original)
        self._probe_methods = []
        if getattr(self, "_mark_src", None) is not None:      # 关灯: 出货路径零开销
            self._mark_src._PROF_MARK = None
            self._mark_src = None
            self._marks = None

    def _gc_probe(self, phase, info):
        if phase == "start":
            self._gc_start = time.perf_counter()
        else:
            self._gc_ms += (time.perf_counter() - self._gc_start) * 1000
            self._gc_generation = max(self._gc_generation, info["generation"])

    def _flow_rebuild_stats(self):
        """把沙流渲染器的**索引重建触发计数**读进这一帧, 读完清零。

        为什么要它: `mesh.indices = ...` 的 setter → 打上 GI_NEEDS_UPDATE →
        下一帧 `VertexInstruction.apply()` 就 `build()` → `Mesh.build()` 拿**整个
        512 槽顶点数组**去 `VertexBatch.set_data()`, `clear_data + add_vertex_data`
        之后 `flags |= V_NEEDUPLOAD`。**改一次索引整块顶点重走一遍并标脏上传。**
        (源码 Kivy 2.3.0: vertex_instructions.pyx:485/460, instructions.pyx:429, vbo.pyx:170)

        ⚠️ 这是**触发条件**的计数, 不是实测 GL 上传流量: 真上传多少由驱动决定。
        渲染器没装载(桌面默认走 line 池)时返回空, 不硬凑 0。
        """
        module = sys.modules.get("flow_texture_experiment")
        if module is None:
            return {}
        stats = dict(module.STATS)
        module.stats_reset()
        return {"index_assigns": stats.get("index_assigns", 0),
                "chunk_clears": stats.get("chunk_clears", 0),
                "flow_chunks": stats.get("chunks", 0),
                "vertex_rebuild_kib": round(stats.get("vertex_bytes", 0) / 1024.0, 1)}

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
        # `_done_at` 是 perf_counter 时刻戳, 直接还原会变成"很久以前" ⇒ 与 effects 同样平移 pause,
        # 才能保持"距排空已过多久"不变(_neck_sand_side 的 f 只看这个差值)。
        if getattr(self.widget, "_done_at", None) is not None:
            self.widget._done_at += pause
        self.widget.last_frame = time.perf_counter()
        self.widget.last_tick = self.widget.last_frame if self.widget.running else None
        self.widget._rebuild_height_table()
        self.widget.redraw()
        if self.widget.running:
            self.widget._play_sound()
        self.on_finish(self.results, cancelled)
