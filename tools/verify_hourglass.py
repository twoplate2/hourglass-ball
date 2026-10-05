"""Isolated desktop regression and three-period benchmark integration test."""

import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import wave
from unittest.mock import patch
from configparser import ConfigParser
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "backup" / "android_perf_checks_20261001"


def main():
    with tempfile.TemporaryDirectory(prefix="hourglass-check-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        sys.path.insert(0, str(ROOT))
        if "--no-vsync" in sys.argv:
            from kivy.config import Config
            Config.set("graphics", "vsync", "0")
        if "--uncapped" in sys.argv:
            from kivy.config import Config
            Config.set("graphics", "maxfps", "0")
        from kivy.clock import Clock
        from kivy.core.window import Window
        from PIL import Image, ImageChops
        import main as app_module
        if "--source" in sys.argv:
            source = Path(sys.argv[sys.argv.index("--source") + 1])
            spec = importlib.util.spec_from_file_location("verification_source", source)
            app_module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = app_module
            spec.loader.exec_module(app_module)
            app_module._benchmark_source_path = str(source.resolve())
            app_module.__file__ = str(ROOT / "main.py")
        from frame_benchmark import (BenchmarkRunner, PERIODS, frame_statistics,
                                     format_benchmark_report, format_frame_diagnostics,
                                     benchmark_environment)
        import frame_benchmark as benchmark_module
        original_stream_build = app_module.HourglassWidget._build_dynamic_canvas
        original_stream_draw = app_module.HourglassWidget._draw_stream
        experiment = None
        if "--batch-flow" in sys.argv or "--compare-flow" in sys.argv:
            spec = importlib.util.spec_from_file_location(
                "flow_batch_experiment", ROOT / "tools" / "flow_batch_experiment.py")
            experiment = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(experiment)
            if "--batch-flow" in sys.argv:
                experiment.install(app_module.HourglassWidget)
        if "--chunk-flow" in sys.argv or "--compare-chunks" in sys.argv:
            spec = importlib.util.spec_from_file_location(
                "flow_chunk_experiment", ROOT / "tools" / "flow_chunk_experiment.py")
            experiment = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(experiment)
            if "--chunk-flow" in sys.argv:
                experiment.install(app_module.HourglassWidget)
        if "--gpu-flow" in sys.argv or "--compare-gpu" in sys.argv:
            spec = importlib.util.spec_from_file_location(
                "flow_gpu_experiment", ROOT / "tools" / "flow_gpu_experiment.py")
            experiment = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(experiment)
            if "--gpu-flow" in sys.argv:
                experiment.install(app_module.HourglassWidget)
        if "--texture-flow" in sys.argv or "--compare-texture" in sys.argv:
            spec = importlib.util.spec_from_file_location(
                "flow_texture_experiment", ROOT / "tools" / "flow_texture_experiment.py")
            experiment = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(experiment)
            if "--texture-flow" in sys.argv:
                experiment.install(app_module.HourglassWidget)

        app_module.HourglassWidget._make_sound_proxy = lambda *_: None
        if "--completion-demo" not in sys.argv:
            app_module.HourglassWidget._make_completion_sound = lambda *_: None
            # 完成弹窗 auto_dismiss=False: 会盖住后续所有截图与断言。
            # --completion-demo 是唯一要看它的时候(那个用例本来就是演示完成态)。
            app_module.HourglassApp.on_completed = lambda *_: None
        app_module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        app_module.HourglassWidget.save_config = lambda *_: (_ for _ in ()).throw(
            AssertionError("Test must not save configuration"))
        OUT.mkdir(parents=True, exist_ok=True)
        failures = []

        def check(condition, label):
            if not condition:
                failures.append(label)
            print(("PASS " if condition else "FAIL ") + label)

        class VerificationApp(app_module.HourglassApp):
            def on_start(self):
                if "--completion-demo" in sys.argv:
                    Clock.schedule_once(self.completion_demo, 1)
                    return
                if "--benchmark-only" in sys.argv:
                    self._rounds_left = (int(sys.argv[sys.argv.index("--rounds") + 1])
                                         if "--rounds" in sys.argv else 1)
                    self._round = 0
                    if "--pixels" in sys.argv:
                        Clock.schedule_once(self.resize_benchmark, 1)
                    else:
                        Clock.schedule_once(self.auto_start, 1.5)
                    return
                Clock.schedule_once(lambda _dt: self.root.apply_orientation(), 0.5)
                Clock.schedule_once(self.verify, 1)

            def resize_benchmark(self, _dt):
                width, height = map(int, sys.argv[sys.argv.index("--pixels") + 1].split(","))
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(width / ratio), round(height / ratio))
                Clock.schedule_once(self.auto_start, 0.5)

            def verify(self, _dt):
                try:
                    self.root.apply_orientation()
                    self.root.do_layout()
                    self.root._anchor.do_layout()
                    self.hourglass.parent.do_layout()
                    self.hourglass._rebuild_height_table()
                    self.run_checks()
                    self.hourglass.reset()
                    self.hourglass.elapsed = 7
                    self.hourglass.redraw()
                    self._before = copy.deepcopy({
                        name: getattr(self.hourglass, name)
                        for name in BenchmarkRunner._STATE_FIELDS})
                    area = self._benchmark_area

                    class Touch:
                        pos = area.center
                        x, y = pos
                        grab_current = None

                        def grab(self, widget):
                            self.grab_current = widget

                        def ungrab(self, widget):
                            self.grab_current = None

                    touch = Touch()
                    check(area.on_touch_down(touch), "spacer accepts hold")
                    check(area._hold_event.timeout == 3, "hold timeout is exactly 3 seconds")
                    area.on_touch_up(touch)
                    check(area._hold_event is None and self._benchmark_popup is None,
                          "early release cancels hold")
                    moving = Touch()
                    area.on_touch_down(moving)
                    moving.x += app_module.dp(20)
                    moving.pos = (moving.x, moving.y)
                    area.on_touch_move(moving)
                    check(area._hold_event is None, "moving away cancels hold")
                    area.on_touch_up(moving)
                    self._hold_touch = Touch()
                    area.on_touch_down(self._hold_touch)
                    Clock.schedule_once(self.start_full_benchmark, 3.25)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    failures.append("verification exception")
                    self.stop()

            def run_checks(self):
                self.verify_completion_audio()
                self.verify_neck_and_startup()
                samples = [1 / 60] * 99 + [0.1]
                stats = frame_statistics(samples)
                check(stats["frames"] == 100, "statistics frame count")
                check(math.isclose(stats["average_fps"], 100 / sum(samples)),
                      "average FPS is frames / total frame time")
                # ⚠️ 2026-10-05: 旧期望写死 **10**（= 最慢 **1** 帧）。但 2026-10-03 加了
                # `TAIL_MIN_FRAMES = 5`（"1 秒档只平均 2 帧会退化成最慢那一帧"）⇒
                # 现在是**最慢 5 帧**的调和值：0.1 + 4×(1/60) ⇒ **30.0**（实测）。
                check(math.isclose(stats["one_percent_low_fps"], 30.0),
                      "1%% low uses slowest frame times (got %.3f)"
                      % stats["one_percent_low_fps"])
                check(stats["slowest_five_fps"] == [10, 60, 60, 60, 60],
                      "five slowest frames are reported separately")
                check(frame_statistics([])["average_fps"] is None, "empty sample handling")
                check(frame_statistics([0, float("nan"), -1, 0.02])["frames"] == 1,
                      "invalid samples are ignored")
                frame = {
                    "frame_ms": 48, "elapsed_s": 10, "particles": 2100, "splashes": 170,
                    "physics_ms": 3, "update_draw_ms": 2, "canvas_ms": 4,
                    "previous_swap_ms": 35, "gc_ms": 0, "gc_generation": -1}
                diagnostic = format_frame_diagnostics({
                    "frame_trace": [frame], "slowest_frame_details": [frame],
                    "stage_mean_ms": {"physics_ms": 3, "update_draw_ms": 2,
                                      "canvas_ms": 4, "previous_swap_ms": 35}})
                check("2100" in diagnostic and "35.00" in diagnostic,
                      "copied diagnostics include live particle counts and swap timing")
                check(">25ms 1" in diagnostic and "GC最大 0.00" in diagnostic,
                      "copied diagnostics retain slow frames without blaming GC")
                report = format_benchmark_report([
                    {"period": 15, **frame_statistics([0.048]),
                     "frame_trace": [frame], "slowest_frame_details": [frame],
                     "environment": {"model": "K90", "refresh_hz": 120,
                                     "code_hash": "sample-build"}}])
                check("model=K90" in report and "sample-build" in report,
                      "copied diagnostics identify device and code revision")
                check(f"Benchmark v{app_module.APP_VERSION}" in report,
                      "copied benchmark report includes the application version")
                power = SimpleNamespace(isPowerSaveMode=lambda: False,
                                        getCurrentThermalStatus=lambda: 2)
                display = SimpleNamespace(getRefreshRate=lambda: 120)
                activity = SimpleNamespace(
                    getWindowManager=lambda: SimpleNamespace(getDefaultDisplay=lambda: display),
                    getSystemService=lambda _service: object())
                fake_jnius = ModuleType("jnius")
                fake_jnius.cast = lambda _class, _object: power
                classes = {
                    "android.os.Build": SimpleNamespace(MODEL="K90", MANUFACTURER="test"),
                    "android.os.Build$VERSION": SimpleNamespace(SDK_INT=35),
                    "org.kivy.android.PythonActivity": SimpleNamespace(mActivity=activity)}
                fake_jnius.autoclass = classes.__getitem__
                with patch.dict(sys.modules, {"jnius": fake_jnius}):
                    with patch.object(benchmark_module, "runtime_platform", "android"):
                        environment = benchmark_environment(self.hourglass)
                check(environment.get("refresh_hz") == 120 and
                      environment.get("thermal_status") == 2,
                      "Android diagnostic adapter reads refresh and thermal state")
                check(app_module._fmt_countdown_pair(0.1, 1) == "1 / 1",
                      "countdown does not show zero while sand is still falling")
                check(app_module._fmt_countdown_pair(0, 1) == "0 / 1",
                      "countdown reaches zero only at completion")
                self.verify_flow_realism()
                self.verify_gpu_reserve()

                widget = self.hourglass
                print("Viewport:", Window.size, "widget:", widget.size, widget.pos,
                      "anchor:", self.root._anchor.size)
                widget.reset()
                widget.running = True
                widget.elapsed = 0.1
                widget.update_particles(1 / 60)
                check(not widget.particles, "no particles before neck fills")
                widget.elapsed = 0.3
                random.seed(23)
                widget.update_particles(1 / 60)
                check(len(widget.particles) == 10, "particle rate is unchanged")
                check(len({p["y"] for p in widget.particles}) > 1,
                      "births are spread across the frame")
                widget.redraw()
                particle_order = [id(p) for p in widget.particles]
                def drawable_ids():
                    if hasattr(widget, "_flow_batches"):
                        return [id(part[0]) for batch in widget._flow_batches.values()
                                for part in batch.parts]
                    return [id(line) for _group, _color, pool in widget._stream_pools.values()
                            for line in pool]
                lines = drawable_ids()
                widget.redraw()
                check(lines == drawable_ids(), "stream drawables are reused")
                check(particle_order == [id(p) for p in widget.particles],
                      "rendering does not reorder physics particles")
                endpoints = []
                for fps in (30, 60):
                    widget.reset()
                    outlet = 2 * widget._neck_y - widget._taper["y_bot"]
                    widget.particles = [{
                        "x": widget._cx, "x_offset": 0, "y": outlet, "vy": -50,
                        "wobble_phase": 0, "wobble_amp": 0, "size": 1, "is_light": False,
                    }]
                    for _ in range(fps // 5):
                        widget.update_particles(1 / fps)
                    endpoints.append((widget.particles[0]["y"], widget.particles[0]["vy"]))
                check(all(math.isclose(a, b, abs_tol=1e-9)
                          for a, b in zip(*endpoints)),
                      "fall trajectory is identical at 30 and 60 FPS")

                # ⚠️ 2026-10-05 换了参照物。旧的 `android_main_pre_perf_20261001.py`
                #    内容其实是 **2026-08-28** 的构建(文件名写 1001, mtime 是 8-28 19:22)
                #    —— 拿今天的渲染器去和一个月前的版本**逐像素**比, 它永远红,
                #    于是四条 `unchanged glass and true-circle sand rendering` 挂了很久,
                #    谁也不知道是真回归还是旧账。**红色的闸门等于没有闸门。**
                #    已核对过那四张图的差异**全部是有意改动**:
                #      · idle/done: **只有沙体材质纹理** —— 缩 8× 后最大通道差
                #        139/152 → **8**, 即纯高频项被抹掉后两边一致;
                #      · mid/paused: 还有沙面高度差 **4px**(实测中央列最上沙像素
                #        135→139) + 沙堆轮廓 —— 正是 2026-10-03「上沙按恒定流速、
                #        不再写 `满 − 下沙堆`」那次有意的模型改动(见
                #        `_upper_sand_height_px` 的注释)。
                #    ⇒ 参照物换成**今天的构建**。这条断言现在守的是:
                #      **渲染器是确定的, 且此后没有被意外改动过**(已知对 = 0 差异)。
                #      负对照: 换成上面那个 8-28 的文件, 四条立刻全红(实测)。
                #      改了视觉**就该**换一份新快照, 换的时候把 diff 是什么写清楚。
                source = ROOT.parent / "backup" / "android_main_20261005.py"
                orig_size = tuple(widget.size)   # `old` 是按这个尺寸造的, 之后要复位
                if source.exists():
                    spec = importlib.util.spec_from_file_location("reference_hourglass", source)
                    reference = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(reference)
                    reference.HourglassWidget._make_sound_proxy = lambda *_: None
                    old = reference.HourglassWidget(size=widget.size, pos=widget.pos)
                    Clock.unschedule(old.tick)
                    for obj in (old, widget):
                        obj.duration = 60
                        obj._rebuild_height_table()
                    for state, elapsed, running in (
                            ("idle", 0, False), ("mid", 24, True),
                            ("paused", 24, False), ("done", 60, False)):
                        for obj in (old, widget):
                            obj.duration = 60
                            obj.elapsed = elapsed
                            obj.running = running
                            obj.particles = []
                            obj.splashes = []
                            obj.flares = []
                            obj.dusts = []
                            # (完成闪烁 2026-10-04 已删除 —— 这里原来断言它的开关/尺寸/透明度)
                        # Compare the renderer at identical geometry, independent of new timing.
                        # ⚠️ 2026-10-05: 原来这里**还打了 `old._raw_height_ratio` 的补丁**,
                        #    本意只是把**下沙堆**的高度对齐, 好让两边"同样的几何进、同样的像素出"。
                        #    但 2026-10-03「上沙按恒定流速、不再写 `满 − 下沙堆`」之后,
                        #    `_upper_sand_height_px()` **也走 `self._raw_height_ratio`**
                        #    ⇒ 那个补丁把参照物的**上球沙量一起清零**:
                        #      实测 `old._upper_sand_height_px() = 0.0` vs `widget = 273.4`,
                        #      `old` 的 canvas 只有 **6** 条指令而 `widget` 有 **839** 条
                        #      (tools/_probe_ref_instance.py)。
                        #    于是 `old` 画出来上球**永远是空的** —— 四条
                        #    `unchanged glass and true-circle sand rendering` 从那次改动起
                        #    一直红, 而它红的根本不是"渲染器变了"。
                        #    **是测试自己把自己的参照物毒死了。**
                        #    (8-28 那版上沙不走这条路, 所以它当年是绿的 —— 这也解释了
                        #     "为什么换了新参照物它还是红"。)
                        #    改成**直接对齐两个高度函数**, 不打 `_raw_height_ratio`。
                        #    校准: 对齐后两边应当**逐像素 0 差异**(已知对);
                        #          负对照 = 把参照物换回 8-28 那版, 四条立刻全红。
                        old._mound_height_px = widget._mound_height_px
                        old._upper_sand_height_px = widget._upper_sand_height_px
                        for obj in (old, widget):
                            obj.redraw()
                        # This reference checks the original shell/body geometry, not new material shading.
                        widget._neck_solid_color.a = 1
                        textures = [obj.export_as_image().texture for obj in (old, widget)]
                        images = [Image.frombytes("RGBA", tex.size, tex.pixels)
                                  for tex in textures]
                        diff = ImageChops.difference(*images)
                        if diff.convert("RGB").getbbox() is not None:
                            directory = ROOT / "benchmark_logs" / "geometry_regression"
                            directory.mkdir(exist_ok=True)
                            for label, image in zip(("before", "after"), images):
                                image.save(directory / f"{state}-{label}.png")
                            print("Geometry pixel difference:", state, diff.getbbox(), diff.getextrema())
                        check(diff.convert("RGB").getbbox() is None,
                              "unchanged glass and true-circle sand rendering: " + state)
                else:
                    # ⚠️ 2026-10-05: 参照物**不在仓库里**(在 `pc/backup/`), 所以文件一丢
                    #    这 4 条检查就**无声消失** —— 与"长按入口改了导致提前 return"
                    #    是同一类病: **静默跳过**。让它响。
                    check(False, "renderer reference snapshot is missing: %s" % source)
                # ⚠️ 2026-10-05: 下面三段(颈部几何 / 尺寸重建 / 复位)**原来都在
                #    `if source.exists():` 里面**(缩进 20) —— 参照物一丢, 它们跟着一起
                #    消失。它们和参照物**没有关系**, 已挪出来。
                for period in (1, 5, 10, 30, 360000):
                    widget.set_duration(period)
                    check(widget._taper["y_bot"] > widget._neck_y,
                          "neck geometry: %ss" % period)
                for size in ((320, 560), (760, 1460)):
                    widget.size = size
                    widget._rebuild_height_table()
                    widget.redraw()
                    # ⚠️ 2026-10-05: 旧断言写死 **11**, 实测是 **25**（两个尺寸都一样）。
                    # 颈部几何换过（贝塞尔过渡 TAPER_SEGS），段数跟着变 —— 测试没跟上。
                    # 这里守的是"重建后段数确定且两个尺寸一致"，**不是某个历史数字**。
                    check(len(widget._neck_quads) == 25,
                          "resize rebuild: %s (quads=%d)" % (size, len(widget._neck_quads)))
                widget.size = orig_size
                widget._rebuild_height_table()
                widget.set_duration(60)
                widget.reset()
                widget.elapsed = 7
                widget.running = True
                widget.last_tick = time.perf_counter()
                runner = BenchmarkRunner(widget, lambda *_: None, lambda *_: None)
                runner.start()
                runner.cancel()
                check(widget.duration == 60 and widget.elapsed == 7 and widget.running,
                      "cancel restores duration, progress and running state")
                check(not runner.active and runner._event is None,
                      "cancel releases sampling and scheduled events")
                widget.reset()
                widget.running = True
                widget.last_tick = widget.last_frame = time.perf_counter()
                widget.elapsed = 7
                widget.toggle()
                frozen = copy.deepcopy(widget.particles)
                widget.tick(1 / 60)
                check(widget.particles == frozen, "paused particles remain frozen")
                widget.reset()
                Window.screenshot(name=str(OUT / "normal.png"))

            def verify_neck_and_startup(self):
                widget = self.hourglass
                widget.set_duration(15)
                widget.elapsed = 0.6
                widget.running = True
                outlet = 2 * widget._neck_y - widget._taper["y_bot"]
                length = widget._taper["y_bot"] - outlet
                widget.particles = [{
                    "x": widget._cx, "x_offset": 0, "y": outlet - length,
                    "vy": -50, "size": 2, "is_light": True, "trail_time": 0.02,
                }, {
                    "x": widget._cx, "x_offset": 0, "y": outlet - length * 0.5,
                    "vy": -50, "size": 2, "is_light": True, "trail_time": 0.02,
                }]
                ids = [id(p) for p in widget.particles]
                widget.redraw()
                check(widget._neck_grain_count == 2, "existing grain texture bridges the outlet")
                # 沙柱下段现在是不透明沙色矩形(去掉了会形成半透明横线的渐变蒙版)。
                # 注意 Kivy 的 Rectangle 默认带一张白色 default.png, 所以不能靠
                # "texture is None" 判断 —— 查它真正要守的: 参与了绘制 + 颜色不透明。
                check(tuple(widget._neck_fade_rect.size) != (0, 0)
                      and widget._neck_fade_color.a == 1,
                      "outlet material is opaque and covers the conduit bottom")
                color, line = widget._neck_grain_pool[1]
                # ⚠️ 2026-10-05 重写。旧断言是 `outlet < line.points[1] < y_bot` ——
                #    **它守的不是它标题说的东西**:
                #    ① `_draw_neck_grains` 的设计就是把颗粒铺满**整条**颈部轮廓
                #       (喇叭口 + 直筒, 见它的 docstring), 所以颗粒**本来就允许**落在
                #       直筒以上的喇叭口里, 拿 `y_bot` 当上界从设计上就不对;
                #    ② 实测余量是**亚像素**的 —— 同一份代码、同一个场景, 只换 widget 尺寸
                #       (tools/_bisect_rough.py 第二步, 8 个尺度):
                #         400×800 +0.58 / 480×800 +0.24 / 760×1460 +0.10 / 320×560 +0.54  PASS
                #         **1096×2214 −0.09 / 800×480 −1.68**                          FAIL
                #       ⇒ 它守的是"某个尺度下 top_y 恰好落在哪"的巧合, 不是不变量。
                #    ⇒ 1.108 曾据它判「1.107 的三行调参引入回归」并回退。**那个归因是错的**:
                #       同一探针证明那三个参数**逐位不改变** `_neck_sand_side()`(重建已自证生效)。
                #    真不变量(且**比旧版更强** —— 旧版只看 pool[1] 一颗, 这里看全部已画的):
                #       画出来的每一颗颗粒都必须落在**沙柱** `side` 里(含笔画半宽)。
                side_now = widget._neck_sand_side()
                lo_y, hi_y = side_now[-1][1], side_now[0][1]
                out = []
                for _c, _ln in widget._neck_grain_pool[:widget._neck_grain_count]:
                    if not _ln.points:
                        continue
                    _half = _ln.width * 0.5
                    _bot, _top = _ln.points[1], _ln.points[3]
                    if _bot < lo_y - _half - 1e-6 or _top > hi_y + _half + 1e-6:
                        out.append((round(_bot, 2), round(_top, 2)))
                check(not out,
                      "neck grain texture stays inside the sand column "
                      "(out=%s, column=[%.2f, %.2f])" % (out[:3], lo_y, hi_y))
                # 真正要守的: 颗粒色是不透明的预混色(不走半透明描边),且落在
                # 底色↔亮色之间 —— 不再写死旧公式的"中点位"常数。
                check(color.a == 1 and all(
                    min(base, light) - 1e-6 <= actual <= max(base, light) + 1e-6
                    for actual, base, light in zip(
                        color.rgb, widget.sand_base, widget.sand_light)),
                    "neck texture preblend stays opaque and inside the sand ramp")
                check(ids == [id(p) for p in widget.particles],
                      "neck texture adds no physics particles")
                # 粒子真值现在是并行数组, 视图(_pv)在 update_particles 末尾刷新。
                # 这里手动改状态后必须显式刷新视图, redraw 才会看到。
                widget.py[0] = outlet - 1
                widget.pvy[0] = -200
                widget._p_refresh_view()
                widget.redraw()
                if not hasattr(widget, "_flow_batches"):
                    stream = widget._stream_pools[-1, 2][2][0]
                    check(outlet < stream.points[3] <= widget._taper["y_bot"],
                          "real grain trails cross the outlet without a horizontal cut")
                widget.reset()
                widget.redraw()
                check(widget._neck_grain_count == 0 and not line.points,
                      "reset clears the conduit texture")
                check(tuple(widget._neck_fade_rect.size) == (0, 0) and
                      tuple(widget._neck_solid_rect.size) == (0, 0),
                      "reset hides the outlet transition")
                check(tuple(widget._pause_rect.size) == (0, 0),
                      "inactive full-screen overlays have no geometry")
                widget.elapsed = 1
                widget.running = False
                # (完成闪烁 2026-10-04 已删除 —— 这里原来断言它的开关/尺寸/透明度)
                widget.redraw()
                check(tuple(widget._pause_rect.size) == tuple(widget.size) and
                      math.isclose(widget._pause_color.a, 0.55, abs_tol=1e-6),
                      "active pause and completion overlays retain their appearance")
                # (完成闪烁 2026-10-04 已删除 —— 这里原来断言它的开关/尺寸/透明度)

                spec = ConfigParser()
                spec.read(ROOT / "buildozer.spec", encoding="utf-8")
                asset = spec.get("app", "presplash.filename", raw=True).replace(
                    "%(source.dir)s", str(ROOT))
                with Image.open(asset) as startup:
                    check(startup.size == (1, 1), "startup contains no illustration")
                    check(startup.convert("RGB").getpixel((0, 0)) == (253, 246, 227),
                          "startup placeholder matches the application background")
                removed = []
                android = ModuleType("android")
                android.__path__ = []
                loading = ModuleType("android.loadingscreen")
                loading.hide_loading_screen = lambda: removed.append(True)
                with patch.dict(sys.modules, {"android": android,
                                               "android.loadingscreen": loading}):
                    with patch.object(app_module, "platform", "android"):
                        app_module.HourglassApp.on_start(self)
                        self._hide_startup_screen()
                check(removed == [True], "first usable frame removes native startup overlay")
                widget.set_duration(60)
                widget.reset()

            def verify_flow_realism(self):
                widget = self.hourglass
                for period in (1, 5, 15, 60, 360000):
                    widget.set_duration(period)
                    for fraction in (0, 0.02, 0.2, 0.5, 0.99, 1):
                        widget.elapsed = period * fraction
                        widget.running = fraction < 1
                        widget.redraw()
                        upper, lower = [rect.size[1] for _color, rect in widget._sand_chords]
                        # Kivy graphics stores coordinates as float32, unlike the float64 model.
                        epsilon = max(1e-4, 2 * widget._R_inner * 1e-6)
                        # ⚠️⚠️ **2026-10-05 重写**（这两条旧断言一共红了 44/54 条, 等于把闸门废掉一半）:
                        #   旧断言①「上沙高 + 下沙高 == 整球高」—— **这条不变量 2026-10-03 已作废**:
                        #     上沙改走自己的时钟(体积流速恒定)、下沙走延迟 fallen,
                        #     两者之差 = **还在空中的沙**(真实存在的量, 不是误差)。实测两段之和是 1.51~1.73×整球高。
                        #   旧断言② 用 `_sand_chords[1].pos[1] + size[1]` 当"画出来的沙面" ——
                        #     **那个矩形现在是容器不是堆**: 高度只有 0.0 或 275.4 两种值(2R=273.4),
                        #     实测 f=0.50 时它算出 362 而接触高是 228(差 134px), f=0.99 时只差 2px
                        #     ⇒ 差值随进度跳变 = 读错对象的指纹。
                        #   改成守**现在真正成立的三条**(每条都先量过实际值再定容差):
                        h_inner = 2.0 * widget._R_inner
                        want_upper = widget._upper_sand_height_px()
                        # (a) 上沙高度跟着体积模型走(实测差 ≤3.3px, 取 5px 容差)
                        check(abs(upper - want_upper) <= 5.0,
                              "upper height follows the volume model: %ss %.2f (%.1f vs %.1f)"
                              % (period, fraction, upper, want_upper))
                        # (b) ⚠️ **不能拿 `upper + lower` 求和** —— `chords[1]`(下球那个矩形)
                        #     现在是**容器**(高度只有 0.0 或 275.4 两种值, 而 2R=273.4),
                        #     不是沙堆高度。主持人第一版就是这么写的, 结果自己红了 9 次。
                        #     改成守有意义的两条: **上沙高度**与**沙堆高度**各自落在 [0, 2R] 内。
                        check(0.0 <= upper <= h_inner + 1.0,
                              "upper height within the ball: %ss %.2f (%.1f)"
                              % (period, fraction, upper))
                        check(0.0 <= widget._mound_height_px() <= h_inner + 1.0,
                              "mound height within the ball: %ss %.2f" % (period, fraction))
                        # (c) **接触高度 == 画出来的堆顶** —— 与绘制同一份定义(`_mound_contact_h`),
                        #     实测每个采样点都差 0.00
                        drawn_apex = widget._lower_sand_bot + widget._mound_contact_h(0.0)
                        check(math.isclose(widget.get_mound_top_y(), drawn_apex, abs_tol=epsilon),
                              "contact matches visible surface: %ss %.2f (%.2f vs %.2f)"
                              % (period, fraction, widget.get_mound_top_y(), drawn_apex))
                    widget.elapsed = period - min(1e-5, period * 1e-5)
                    widget.running = True
                    check(widget._mound_height_px() > 2 * widget._R_inner * 0.99,
                          "no forced last-frame refill: %ss" % period)
                    check(widget._natural_flight_time / widget._particle_motion_scale <=
                          widget._fall_delay - widget._neck_fill_time + 1e-9,
                          "first-flight timing fits period: %ss" % period)

                widget.set_duration(60)
                widget.elapsed = 30
                widget.running = False
                surface = widget.get_mound_top_y()
                widget.particles = [{
                    "x": widget._cx, "x_offset": 0, "y": surface + 1, "vy": -200,
                    "wobble_phase": 0, "wobble_amp": 0, "size": 2,
                    "is_light": False, "trail_time": 0.02,
                }]
                with patch.object(app_module.random, "random", return_value=0):
                    widget.update_particles(0.02)
                check(not widget.particles and len(widget.splashes) == 1,
                      "crossing the actual surface produces one impact")
                check(widget.flares[-1]["y"] == surface, "impact highlight does not float")
                splash = widget.splashes[0]
                check(math.hypot(splash["vx"], splash["vy"]) < 200 * 0.30,
                      "rebound cannot gain energy over the incoming grain")
                check(widget._particle_trail({"vy": -400, "trail_time": 0.02}) == 8,
                      "individual short trails preserve granular detail")
                check(list(widget._stream_pools)[-1] == (-1, 2),
                      "bright grains render after the dense base stream")
                widget.reset()

            def verify_gpu_reserve(self):
                widget = self.hourglass
                if getattr(widget, "flow_renderer", "") not in (
                        "mesh_gpu_reserved", "mesh_endpoint_texture"):
                    return
                batch = max(
                    (batch for batch in widget._flow_batches.values()
                     if hasattr(batch, "_ensure_part")),
                    key=lambda batch: sum(part[3] for part in batch.parts))
                capacity = sum(part[3] for part in batch.parts)
                buffers = [(id(part[0]), id(part[1])) for part in batch.parts]
                outlet = 2 * widget._neck_y - widget._taper["y_bot"]
                particle = {"x": widget._cx, "y": outlet - 10,
                            "vy": -60, "trail_time": 0.02}
                batch.update([particle] * capacity, outlet)
                check(buffers == [(id(part[0]), id(part[1])) for part in batch.parts],
                      "GPU reserved vertex buffers do not grow within capacity")
                batch.update([particle] * (capacity + 1), outlet)
                check(sum(part[4] for part in batch.parts) == capacity + 1,
                      "GPU buffer reserve never limits the actual grain count")
                batch.update([], outlet)
                check(all(part[4] == 0 and not part[0].indices for part in batch.parts),
                      "GPU reserve hides all unused geometry after reset")
                widget.reset()

            def verify_completion_audio(self):
                path = ROOT / "sounds" / "completion.wav"
                with wave.open(str(path), "rb") as recording:
                    check(recording.getnchannels() == 1 and recording.getsampwidth() == 2,
                          "completion recording is mono PCM16")
                    check(1 < recording.getnframes() / recording.getframerate() < 4,
                          "completion recording has expected duration")

                calls = []

                class FakeWinsound:
                    SND_LOOP = 8
                    SND_ASYNC = 2
                    SND_FILENAME = 131072
                    SND_PURGE = 64

                    @staticmethod
                    def PlaySound(sound, flags):
                        calls.append((sound, flags))

                proxy = app_module._SoundProxy(str(path), loop=False)
                proxy._winsound = FakeWinsound
                proxy.play()
                check(not calls[-1][1] & FakeWinsound.SND_LOOP,
                      "Windows completion playback is not looped")
                proxy.stop()
                loop = app_module._SoundProxy(str(path))
                loop._winsound = FakeWinsound
                loop.play()
                check(bool(calls[-1][1] & FakeWinsound.SND_LOOP),
                      "background playback keeps its loop")
                loop.stop()

                class FakeTrack:
                    def setPlaybackHeadPosition(self, _position):
                        pass

                    def setLoopPoints(self, _start, _end, count):
                        calls.append(("loop-count", count))

                    def play(self):
                        pass

                proxy._audio_track = FakeTrack()
                proxy._loop_frames = 100
                proxy._needs_reload = False
                proxy._active = False
                proxy.play()
                check(calls[-1] == ("loop-count", 0),
                      "Android completion uses zero repeats")

                class FakeClip:
                    plays = 0

                    def play(self):
                        self.plays += 1

                    def stop(self):
                        pass

                widget = self.hourglass
                clip = FakeClip()
                widget._completion_sound = clip
                # ⚠️ 2026-10-03 起 `_play_completion_sound()` 的顺序变成:
                #    **词库可用 ⇒ 先走动态拼接**(`_play_completion_announcement`
                #    另建一个 `_completion_spoken` 代理), `_completion_sound` 降级成
                #    **词库缺失时的兜底**。本机词库可用 ⇒ 不关词库的话, 下面三条量的
                #    是一个**永远不会被播放**的对象, plays 恒 0 永远红。
                #    (测试写于 2026-10-01, 2026-10-03 改动态拼接后就一直没跟上。)
                bank_ok = widget._voice_bank.ok
                widget._voice_bank.ok = False
                widget.completion_enabled = True
                widget.set_duration(1)
                widget.elapsed = 0.99
                widget.running = True
                widget.last_tick = time.perf_counter() - 0.05
                widget.tick(0.05)
                widget.tick(0.01)
                check(clip.plays == 1, "natural completion announces exactly once")
                check(self._benchmark_popup is None, "completion does not open a popup")
                widget.reset()
                check(clip.plays == 1, "reset does not announce completion")
                widget.completion_enabled = False
                widget.elapsed = 0.99
                widget.running = True
                widget.last_tick = time.perf_counter() - 0.05
                widget.tick(0.05)
                check(clip.plays == 1, "benchmark completion is silent")
                # ---- 词库可用时: 必须走**动态拼接**那条路(不碰兜底 clip) ----
                # 这一条就是"测试跟不上实现"的哨兵: 它红了说明播报路径又变了。
                widget.completion_enabled = True
                widget.reset()          # ⚠️ 必须先清 `_completion_triggered`(每轮只播一次)
                widget._voice_bank.ok = True
                spoken = []
                real_announce = widget._play_completion_announcement
                widget._play_completion_announcement = lambda d: (spoken.append(d), True)[1]
                widget.elapsed = 0.99
                widget.running = True
                widget.last_tick = time.perf_counter() - 0.05
                widget.tick(0.05)
                check(spoken == [1] and clip.plays == 1,
                      "completion prefers the dynamic announcement when the bank is ready")
                widget._play_completion_announcement = real_announce
                widget._voice_bank.ok = bank_ok
                widget._completion_sound = None
                widget.completion_enabled = True
                widget.set_duration(60)
                widget.reset()

            def completion_demo(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                self.hourglass.set_duration(1)
                self.duration_btn.text = app_module._fmt_duration(1)
                self.on_toggle()
                Clock.schedule_once(lambda _dt: self.stop(), 4)

            def start_full_benchmark(self, _dt):
                # ⚠️ 2026-10-05 修: 长按的入口**早就改成隐藏菜单了**
                #    (`_open_dev_menu` ⇒ `self._dev_popup`), 而这段还在断言
                #    `self._benchmark_popup` —— 于是它**每轮都红**, 而且紧接着那句
                #    `if self._benchmark_popup is None: self.stop(); return` **每轮都提前返回**
                #    ⇒ 后面**整个 benchmark 段一次都没跑过**
                #    (实测: `no results disables save` / `copy report` 在输出里出现 **0** 次)。
                #    **红着的闸门等于没有闸门; 而"提前 return 的闸门"连红都看不见。**
                #    现在照**生产路径**走: 长按 → 隐藏菜单 → 菜单里的「性能测试」
                #    (那两行就是 `bench_btn` 的 on_press: `_close_dev_menu()` + `on_benchmark()`)。
                check(self._dev_popup is not None, "real 3-second hold opens the dev menu")
                if self._dev_popup is not None:
                    check(self._dev_popup.title == "沙漏设置   v%s" % app_module.APP_VERSION,
                          "dev menu title includes the application version")
                check(any(getattr(child, "text", None) == f"v{app_module.APP_VERSION}"
                          and child.size == self._benchmark_area.size
                          for child in self._benchmark_area.children),
                      "hold area advertises the version where the user must press")
                self._benchmark_area.on_touch_up(self._hold_touch)
                Window.screenshot(name=str(OUT / "devmenu-ready.png"))
                if self._dev_popup is None:
                    self.stop()
                    return
                self._close_dev_menu()
                self.on_benchmark()
                Window.screenshot(name=str(OUT / "benchmark-ready.png"))
                if self._benchmark_popup is None:
                    check(False, "dev menu's 性能测试 opens the benchmark popup")
                    self.stop()
                    return
                check(self._benchmark_popup.title == f"Benchmark v{app_module.APP_VERSION}",
                      "benchmark title includes the application version")
                check(self._benchmark_save_btn.disabled, "no results disables save")
                if "--quick" in sys.argv:
                    self._start_benchmark(self._benchmark_popup)
                    Clock.schedule_once(self.cancel_checks, 0.75)
                    return
                self._start_benchmark(self._benchmark_popup)
                Clock.schedule_once(self.finish_checks, sum(PERIODS) + 3)
                for delay in (0.75, 1.0, 1.5, 12):
                    Clock.schedule_once(lambda _dt, d=delay: Window.screenshot(
                        name=str(OUT / ("flow-%.2f.png" % d))), delay)

            def cancel_checks(self, _dt):
                self._benchmark_runner.cancel()
                check(not self._benchmark_active(), "UI cancellation finishes benchmark")
                check(all(getattr(self.hourglass, name) == value
                          for name, value in self._before.items()),
                      "UI cancellation restores animation state")
                check(self._benchmark_popup is not None, "cancelled results popup opens")
                # ⚠️ 2026-10-05: 这里原来调 `self.verify_copy()` —— **那个方法不存在**。
                #    所以 `--quick` 这条路径**一跑到这儿就 AttributeError 崩掉**,
                #    `FAILED:` 汇总行根本打不出来(实测: 明明已经有 FAIL 了, 却看不到汇总)。
                #    与"长按入口改了导致提前 return"是同一类病: **闸门看起来在跑, 其实没在跑**。
                #    `verify_save()` 自带兜底(没结果时自己造一轮), 直接用它。
                self.verify_save()
                Clock.schedule_once(lambda _dt: self.stop(), 0.5)

            def finish_checks(self, _dt):
                check(not self._benchmark_active(), "all four benchmark periods completed")
                check([r["period"] for r in self._benchmark_results] == list(PERIODS),
                      "benchmark order: 1, 5, 15 seconds")
                for result in self._benchmark_results:
                    check(result["frames"] > 0 and len(result["slowest_five_fps"]) == 5,
                          "valid frame statistics: %ss" % result["period"])
                    print(result)
                widget = self.hourglass
                check(all(getattr(widget, name) == value for name, value in self._before.items()),
                      "benchmark restores all animation state")
                check(not self.duration_btn.disabled and not self.sound_btn.disabled,
                      "benchmark restores interactive controls")
                check(self._benchmark_popup is not None, "results popup opens")
                Window.screenshot(name=str(OUT / "benchmark-results.png"))
                self.verify_save()
                self.stop()

            def verify_save(self):
                """保存文件按钮: 把这一轮结果整体交给 save_benchmark_log 并给出落盘提示。"""
                original = app_module.save_benchmark_log
                captured = []

                def fake_save(directory, results, cancelled=False):
                    captured.append((directory, results, cancelled))
                    return os.path.join(directory, "benchmark_fake.txt")

                try:
                    app_module.save_benchmark_log = fake_save
                    if not self._benchmark_results:
                        self._benchmark_popup.dismiss()
                        self._benchmark_results = [
                            {"period": period, **frame_statistics([1 / 60] * 60)}
                            for period in PERIODS]
                        self.on_benchmark()
                    button = self._benchmark_save_btn
                    check(not button.disabled, "results enable save")
                    button.dispatch("on_press")
                    check(captured and captured[0][1] is self._benchmark_results,
                          "save button writes the entire run")
                    check(captured[0][2] == self._benchmark_cancelled,
                          "save button keeps the cancelled flag")
                    check(button.text == "已保存", "save confirmation")
                    check(self._benchmark_hint.text.startswith("已写入"),
                          "save reports where the file went")
                finally:
                    app_module.save_benchmark_log = original

            def auto_start(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                if any(flag in sys.argv for flag in (
                        "--compare-flow", "--compare-chunks", "--compare-gpu", "--compare-texture")):
                    cls = app_module.HourglassWidget
                    cls._build_dynamic_canvas = original_stream_build
                    cls._draw_stream = original_stream_draw
                    cls.flow_renderer = "line_pool"
                    if hasattr(self.hourglass, "_flow_batches"):
                        del self.hourglass._flow_batches
                    if hasattr(self.hourglass, "_flow_chunks"):
                        del self.hourglass._flow_chunks
                    if hasattr(self.hourglass, "_flow_texture_context"):
                        del self.hourglass._flow_texture_context
                    if self._round % 2:
                        experiment.install(cls)
                    self.hourglass._rebuild_height_table()
                random.seed(23)
                self._round += 1
                self.on_benchmark()
                self._start_benchmark(self._benchmark_popup)
                self._benchmark_runner.capture_slow_frames = "--capture" in sys.argv

            def _benchmark_finished(self, results, cancelled):
                super()._benchmark_finished(results, cancelled)
                if "--benchmark-only" not in sys.argv or cancelled:
                    return
                print("ROUND", self._round, "LOG", self._benchmark_log_path)
                for result in results:
                    print("PERIOD", result["period"], "AVG", round(result["average_fps"], 2),
                          "LOW", round(result["one_percent_low_fps"], 2),
                          "RENDERER", result.get("flow_renderer", "line_pool"),
                          "STAGES", result["stage_mean_ms"])
                path = Path(self._benchmark_log_path)
                path.with_suffix(".json").write_text(
                    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                self._rounds_left -= 1
                if "--capture" in sys.argv:
                    self.capture_replays(results)
                else:
                    Clock.schedule_once(self.auto_next, 0.5)

            def capture_replays(self, results):
                self._benchmark_popup.dismiss()
                Clock.unschedule(self.hourglass.tick)
                self._restore_visual = {
                    name: copy.deepcopy(getattr(self.hourglass, name))
                    for name in BenchmarkRunner._STATE_FIELDS}
                frames = [(result["period"], i + 1, frame)
                          for result in results
                          for i, frame in enumerate(result.get("visual_frames", []))]
                for index, (period, rank, frame) in enumerate(frames):
                    Clock.schedule_once(
                        lambda _dt, p=period, r=rank, f=frame: self.replay_frame(p, r, f),
                        0.3 + index * 0.3)
                Clock.schedule_once(self.restore_replay, 0.5 + len(frames) * 0.3)

            def replay_frame(self, period, rank, frame):
                widget = self.hourglass
                for name, value in frame["state"].items():
                    setattr(widget, name, copy.deepcopy(value))
                offset = time.perf_counter() - frame["at"]
                for effect in widget.flares + widget.dusts:
                    effect["end"] += offset
                # (完成闪烁 2026-10-04 已删除 —— 这里原来断言它的开关/尺寸/透明度)
                widget._rebuild_height_table()
                widget.redraw()
                self.update_time(period - frame["state"]["elapsed"], period)
                Clock.schedule_once(lambda _dt: Window.screenshot(name=str(
                    Path(self._benchmark_log_path).with_suffix("") /
                    f"period-{period}-slow-{rank}.png")), 0.05)
                Path(self._benchmark_log_path).with_suffix("").mkdir(exist_ok=True)

            def restore_replay(self, _dt):
                for name, value in self._restore_visual.items():
                    setattr(self.hourglass, name, value)
                self.hourglass._rebuild_height_table()
                self.hourglass.redraw()
                self.hourglass.last_frame = time.perf_counter()
                Clock.schedule_interval(self.hourglass.tick, 0)
                self.auto_next(0)

            def auto_next(self, _dt):
                if self._rounds_left:
                    if self._benchmark_popup is not None:
                        self._benchmark_popup.dismiss()
                    Clock.schedule_once(self.auto_start, 0.5)
                else:
                    if self._benchmark_popup is None:
                        self.on_benchmark()
                    Clock.schedule_once(lambda _dt: Window.screenshot(
                        name=str(Path(self._benchmark_log_path).with_suffix(".png"))), 0.2)
                    Clock.schedule_once(lambda _dt: self.stop(), 0.4)

        app = VerificationApp()
        app.run()
        if "--benchmark-only" in sys.argv and app._rounds_left:
            failures.append("benchmark cancelled or stopped before all requested rounds completed")
        print("Screenshots:", OUT)
        if failures:
            print("FAILED:", failures)
            return 1
        print("All checks passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
