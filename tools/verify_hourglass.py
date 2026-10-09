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

# ── 满态"堆顶接不接得到管口"(2026-10-09 新增, 对抗审查 A1/C1) ─────────────────
# 用户看到的现象: 50s 档最后 ~5% 里, 画出来的沙堆顶(下球内壁顶)与玻璃管口之间空出
# 一段, 里面只有稀疏下落粒子 ⇒ 读成"沙流和沙堆断成两截"。
# 出处: commit 95a344a(1.74 取消平台); 症状在 xingzhuang.md:147 就写着「末期不对 ——
# 球没有被塞满, 左上/右上各留一块约 14px 的空隙」(按球内高换算到今天几何 = 57px)。
# (2026-10-09 删: 原来这里还有一条 `MOUND_GAP_KNOWN_MAX = 18.0` 的"棘轮" —— 它量的是
#  「出口 − 堆顶」, 而那个量 **`NECK_JOIN` 一个方向都不改** ⇒ 永远落在红/绿同一侧, 没有判别力。
#  现在 (d) 量的是「柱底 − 堆顶」, 直接就是症状本身, 不再需要棘轮。)
MOUND_GAP_GOAL_TOL = 4.0     # 🔴 **2026-10-09 用户定**(原为占位值): 「画出来的沙柱下沿」与
                             # 「画出来的沙堆顶」之间允许差 4.0px。谓词进门、阈值归人。
# ⚠️ **口径**: 上面这个 4.0 用在**本脚本默认的桌面窗口**(400×800 一档, 实测缝 14.3~14.8px);
#    平板口径(1904×2890)同一处是 **57.4px**。**这个数随几何变**, 换口径跑要先重读一遍。


def _drawn_mound_top(widget):
    """从画布读**真正画出来的**沙堆顶(中轴处) —— 线性插值那条 carve 折线。

    `_mound_carve` 每段是 `[x0,y0, x1,y1, x1,top, x0,top]`(见 `_draw_mound_shape`),
    前两个角点是沙面的边; `zero(i)` 的退化段(x0==x1 且 y0==y1)跳过。
    返回 `None` = 没有段覆盖中轴(未画/退化)。

    🔴 **必须读画布, 不能重算 `_mound_contact_h(0.0)`** —— 后者与
    `get_mound_top_y()`(main.py:3639)是**同一个表达式**, 比它是 `isclose(x, x)` 恒真。
    """
    band = getattr(widget, "_mound_carve", None)
    if band is None:
        return None
    cx = widget._cx
    for q in band:
        pts = q.points
        x0, y0, x1, y1 = pts[0], pts[1], pts[2], pts[3]
        if x0 == x1 and y0 == y1:
            continue                                   # zero(i)
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        if lo - 1e-6 <= cx <= hi + 1e-6:
            if abs(x1 - x0) < 1e-9:
                return max(y0, y1)
            return y0 + (y1 - y0) * (cx - x0) / (x1 - x0)
    return None


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
        known_defects = []

        def check(condition, label):
            if not condition:
                failures.append(label)
            print(("PASS " if condition else "FAIL ") + label)

        def known_defect(condition, label, note=""):
            """**已认领的缺陷**: 断言是真的, 今天是红的, 但**不计入 failures**。

            🔴 为什么允许红 —— 项目自己的教训(2026-10-05)是「**红色的闸门等于没有闸门**」:
               四条 `unchanged glass ... rendering` 红了很久, 谁也不知道是真回归还是旧账,
               那半道闸门于是被所有人无视。所以红的断言必须**被认领**:
               ① 单独计数、单独打印(不混进 FAILED); ② 值本身**棘轮化** —— 同一个缺陷
               另有一条硬 `check` 守住"不许比今天更差", 恶化立刻红; ③ **修好后必须
               提升为硬 `check`**(见 `MOUND_GAP_*` 那两条的注释)。
            """
            if condition:
                print("PASS " + label)
            else:
                known_defects.append(label)
                print("已知缺陷 " + label + (("   " + note) if note else ""))

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
                # 🔴 **2026-10-06 由"魔法数"改成"不变量"**。
                #    原检查是 `len(widget.particles) == 10`(duration=60 下), 它只钉住了
                #    **那一版公式在当时那个周期的取值** —— 公式本身随周期漂移
                #    (`600*speed_factor`: 1500/s → 300/s) 时, 这条**完全不响**。
                #    于是"长周期沙流变成一串断续小珠"(用户报的"沙流和沙堆差距过大")
                #    在闸门全绿的情况下存在了很久。
                #    新判据 = **主张本身**: 同 elapsed / 同 dt / 同 seed 下,
                #    60s 与 3600s 必须生成**同样多**的粒子(密度与周期无关)。
                n_short = len(widget.particles)
                check(n_short > 0, "particles are emitted once the neck has filled")
                check(len({p["y"] for p in widget.particles}) > 1,
                      "births are spread across the frame")
                widget.reset()
                widget.set_duration(3600)
                widget.running = True
                widget.elapsed = 0.3
                random.seed(23)
                widget.update_particles(1 / 60)
                check(len(widget.particles) == n_short,
                      "flow rate is independent of duration (no long-period thinning)")
                widget.reset()
                widget.set_duration(60)
                widget.running = True
                widget.elapsed = 0.3
                random.seed(23)
                widget.update_particles(1 / 60)
                check(len(widget.particles) == n_short, "flow rate is reproducible")
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
                # ⚠️ 2026-10-06 换快照: 修了"沙体矩形顶不含 rough"那条(见 main.py `up_draw`)。
                #    diff 是什么(逐像素量过, `tools/_probe_cap_diff.py` / 本次 A/B):
                #      · **1349 px 差 1 级** —— 沙体颗粒纹理的 ±1 抖动。成因: 矩形高了 `crest`
                #        之后 `crop_tex_coords(uv, h/diameter)` 的截取比例变了千分之几,
                #        采样落点亚纹素偏移。**不可见**(1/255), 但会破坏逐位相等 ⇒ 必须换快照。
                #      · **极少数边缘像素** 玻璃→沙(实测 d=155 @ (132,57)) —— 那才是修复本身:
                #        峰顶原先露在矩形外、亮带合成到玻璃上, 现在归位成沙。
                #      · idle / done **逐像素 0 差异** —— 两端 `rough` 包络为 0(crest=0), 符合设计。
                source = ROOT.parent / "backup" / "android_main_20261006.py"
                orig_size = tuple(widget.size)   # `old` 是按这个尺寸造的, 之后要复位
                if source.exists():
                    spec = importlib.util.spec_from_file_location("reference_hourglass", source)
                    reference = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(reference)
                    reference.HourglassWidget._make_sound_proxy = lambda *_: None
                    # ⚠️ **2026-10-06: 沙面起伏的生成函数也要对齐**(与上面"对齐高度函数"同一手法)。
                    #    用户当天要求把起伏从"白噪声 + 一次 3 抽头平滑"(线性插值下档 6 是**三角锯齿**)
                    #    换成**带限噪声**(圆滑曲线) —— 那必然改**沙面轮廓**的像素。
                    #    而这条检查的名字与注释都写明它守的是**玻璃壳 + 真圆沙体**,
                    #    不是起伏曲线 ⇒ 把生成函数也对齐, 让两边"同样的起伏进、同样的像素出"。
                    #    **这同时是一次标定**: 对齐后若逐像素归零, 就证明 `mid/paused` 的差异
                    #    **确实只来自起伏**, 不是别的渲染回归。
                    #    (加宽 `high` 之前先看这里 —— 不对齐的话, 档位一改这条就红,
                    #     而它红的根本不是"渲染器变了"。)
                    reference._surface_roughness = app_module._surface_roughness
                    # ⚠️ 2026-10-06 追加: **上球那条生成路也要对齐**。
                    #    1.163 只对齐了 `_surface_roughness`(**下球**用的),
                    #    而**上球沙面**走的是 `_build_rough_frames` —— 它是**另一条独立的
                    #    生成路**, 当天也被改成"2 维带限"。**修一条不等于修两条**,
                    #    这条闸门正是第二次把它抓出来的地方。
                    reference._build_rough_frames = app_module._build_rough_frames
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
                        # ⚠️ **2026-10-06: 判据从"逐像素全等"改成"差异只能落在一条横带"**。
                        #    原因: 用户当天要求把**沙面两端补到精确的弦端点**(修"沙面粘在壁上
                        #    留台阶"), 它必然改**上球沙面那条线**的像素(实测 **28 px, 横带 5 px 高**)。
                        #    而这条检查**自己的名字与注释**都写明它守的是
                        #    **「玻璃壳 + 真圆沙体」** —— 沙面线不在它的辖区。
                        #    ⇒ 判据改成 **"差异必须只落在一条约 ≤16px 高的横带内, 且总数 ≤400"**。
                        #    **这比"全等"更有针对性**: 玻璃壳/球体一旦动了, 差异会**铺开到很多行**
                        #    (不再是一条带) ⇒ 照样红。**不是放宽, 是换了个能说清"哪里变了"的判据。**
                        #    ⚠️ 标定: 已知对(只改沙面)= 28px / 5px 带; 负对照见下。
                        _d = diff.convert("RGB")
                        _bb = _d.getbbox()
                        if _bb is not None:
                            try:
                                import numpy as _np
                                _arr = _np.asarray(_d).max(axis=2)
                                _ys, _xs = _np.nonzero(_arr > 8)
                                _n, _band = len(_ys), (int(_ys.max() - _ys.min() + 1) if len(_ys) else 0)
                            except Exception:
                                _n, _band = 10 ** 9, 10 ** 9   # 没 numpy ⇒ 退回严格(宁可红)
                            check(_band <= 16 and _n <= 400,
                                  "unchanged glass and true-circle sand rendering: %s "
                                  "(差异 %d px, 横带 %d px 高 —— 玻璃/球体动了会铺开到多行)"
                                  % (state, _n, _band))
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
                    # ⚠️ **点击热区必须盖住画出来的玻璃**（2026-10-06 新加，量法当天返工过一次）。
                    #    颈部整条轮廓关于 `neck_y` 对称（下喇叭口 = 上喇叭口镜像），
                    #    而命中判定必须用**与 `on_touch_down` 同构的三选一**：
                    #    ①上球圆 ②下球圆 ③`_neck_half_width(y)` 非 None 且 |dx| ≤ half+ow。
                    #    ⚠️ **第一版断言只比了 ③，遇到它返回 None 就跳过** —— 而那一段
                    #    （下喇叭口的下半段，整个落在球顶上缘以下）**正是有问题的**：
                    #    它退回球圆判定，而球在极点附近远窄于画出来的喇叭口。
                    #    于是断言报"0.0"、设备上那一点**仍然点不动**。**判据把被测对象排除了。**
                    #    负对照（去掉 `y_low = min(y_low, 2*neck_y - y_top)` 那行）：
                    #    本式在三个周期分别报 22.8 / 24.7 / 29.5 px ⇒ 立刻红。**已跑过**。
                    _tp = widget._taper
                    _mir = 2.0 * widget._neck_y
                    _pts = _tp["out_pts"]
                    _t_out = _tp["t_out"]
                    _y_top = max(p[1] for p in _pts)
                    _y_knee = _tp["y_bot"]
                    _ow = widget._ow

                    def _drawn_half(yy):
                        yf = max(yy, _mir - yy)
                        if yf <= _y_knee:
                            return _t_out
                        for (w0, a), (w1, b) in zip(_pts, _pts[1:]):
                            lo_, hi_ = (a, b) if a <= b else (b, a)
                            if lo_ - 1e-9 <= yf <= hi_ + 1e-9:
                                if abs(b - a) < 1e-9:
                                    return max(w0, w1)
                                return w0 + (w1 - w0) * (yf - a) / (b - a)
                        return _t_out

                    def _hittable(yy, dx):
                        """**与 `on_touch_down` 逐字同构**的三选一。"""
                        if dx * dx + (yy - widget._upper_y_c) ** 2 <= widget._R ** 2:
                            return True
                        if dx * dx + (yy - widget._lower_y_c) ** 2 <= widget._R ** 2:
                            return True
                        hlf = widget._neck_half_width(yy)
                        return hlf is not None and dx <= hlf + _ow

                    _gap = 0.0
                    for _yy in range(int(_mir - _y_top) - 6, int(_y_top) + 7):
                        _dh = _drawn_half(_yy)
                        if _dh <= 0:
                            continue
                        _a, _b = 0.0, _dh + 40.0
                        if _hittable(_yy, 0.0):
                            for _k in range(24):
                                _mid = 0.5 * (_a + _b)
                                if _hittable(_yy, _mid):
                                    _a = _mid
                                else:
                                    _b = _mid
                        _gap = max(_gap, _dh - _a)
                    check(_gap <= 1e-6,
                          "neck hit region covers the drawn glass: %ss gap %.2f px"
                          % (period, _gap))
                for size in ((320, 560), (760, 1460)):
                    widget.size = size
                    widget._rebuild_height_table()
                    widget.redraw()
                    # ⚠️ 2026-10-05: 旧断言写死 **11**, 实测是 **25**（两个尺寸都一样）。
                    # ⚠️ **2026-10-09: 同一个坑第三次** —— 我把容量改成 `len(in_pts)+3` 之后
                    #    变成 28, 这条又红了。它上面那句注释早就写明"守的是**段数确定且两个
                    #    尺寸一致**, 不是某个历史数字", 实现却一直写死数字。
                    #    ⇒ 现在按它自己的话写: **容量必须装得下全部段**。
                    #    需要格数 = 节点数 − 1 ≤ (1 + (len(in_pts)-1) + 1 + 2) − 1 = len(in_pts)+2。
                    #    (末项 2 = D6 之后的延伸节点数: `(t_in, 堆面(t_in))` 与 `(0, 堆面(0))`)
                    _need = len(widget._taper["in_pts"]) + 2
                    check(len(widget._neck_quads) >= _need,
                          "neck quad band has room for every segment: %s (quads=%d need>=%d)"
                          % (size, len(widget._neck_quads), _need))
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
                # 沙柱下段现在是不透明沙色矩形(去掉了会形成半透明横线的渐变蒙版)。
                # 注意 Kivy 的 Rectangle 默认带一张白色 default.png, 所以不能靠
                # "texture is None" 判断 —— 查它真正要守的: 参与了绘制 + 颜色不透明。
                check(tuple(widget._neck_fade_rect.size) != (0, 0)
                      and widget._neck_fade_color.a == 1,
                      "outlet material is opaque and covers the conduit bottom")
                # 🔴 **2026-10-09: 下面这一族断言只在"颗粒层真的在画"时成立。**
                #    材质路径下 `redraw` 走 `context.update_flow(...)`(`main.py` 的硬分支),
                #    `_draw_neck_grains` **一次都不被调**; 2.12 起那层**连建都不建** ⇒
                #    `_neck_grain_count` 恒 0、`_neck_grain_pool` 恒空 ⇒ 旧写法
                #    `widget._neck_grain_pool[1]` 直接 `IndexError`。
                #    ⚠️ **2.12 出货时没跑本脚本, 这条当场就红了** —— 下面是事后补的分流。
                #    闸门条件与绘制点**同一个**, 不另写一套判断。
                _grain_active = not (widget._sand_flow_contexts and
                                     widget._sand_material is not None)
                if _grain_active:
                    check(widget._neck_grain_count == 2,
                          "existing grain texture bridges the outlet")
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
                else:
                    # 材质路径: 这一层**连建都不建**(2.12) —— 断言它确实不在,
                    # 见 `main.py` `_build_dynamic_canvas` 里那段注释与
                    # `CLAUDE.md` 的 2.12 节。
                    check(widget._neck_grain_group is None
                          and not widget._neck_grain_pool
                          and widget._neck_grain_count == 0,
                          "material path does not build the neck grain layer at all")
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
                if _grain_active:
                    check(widget._neck_grain_count == 0 and not line.points,
                          "reset clears the conduit texture")
                else:
                    # 材质路径: 那层压根没建 ⇒ 只需看它没被谁偷偷喂过
                    check(widget._neck_grain_count == 0 and not widget._neck_grain_pool,
                          "reset leaves the unbuilt neck grain layer empty")
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
                    for fraction in (0, 0.02, 0.2, 0.5, 0.9, 0.95, 0.97, 0.99, 1):
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
                        # ⚠️ 2026-10-06 改: 矩形顶 = 沙面高度 + `lift` + `crest`(rough 峰值),
                        #    因为**矩形是容器不是沙堆**(下球那半句注释早写了同一件事)。
                        #    旧式 `abs(upper - want_upper) <= 5` 在 crest 一加就红,
                        #    红的不是"沙量错了", 是断言还拿容器当沙堆。
                        want_rect = (want_upper
                                     + max(0.0, widget._upper_level_for(want_upper) - want_upper)
                                     + widget._upper_rough_crest(want_upper))
                        check(abs(upper - want_rect) <= 1.0,
                              "upper sand rect matches container model: %ss %.2f (%.1f vs %.1f)"
                              % (period, fraction, upper, want_upper))
                        # (a2) **矩形顶必须盖住画出来的沙面线** —— 2026-10-06 新加。
                        #      这条才是真判据(上面那条是"接线对不对", 这条是"够不够高")。
                        #      修之前 `up_draw` 少了 rough 的**正**峰值: 凡 `rough(i) > drop(dx)`
                        #      的节点, 沙面线连同它那条 3px 亮带一起露到矩形外面 —— 那儿没有沙,
                        #      亮带于是合成到**玻璃**上, 沙面上方浮出一排淡色小帽。
                        #      (r27-1号 在设备档6 抓到; 颜色实测 (232,211,173) == sand_light
                        #       与背景的 0.55 混合, 正是 `SAND_SURFACE_ALPHA`。)
                        #      负对照: 把 `_upper_rough_crest` 改成 `return 0.0`, 本式实测
                        #      最差 **+1.90px(档6) / +0.52px(档4)** ⇒ 立刻红。**已跑过**。
                        _arr = widget._upper_rough_now() or []
                        _Ri = widget._R_inner
                        _n = len(_arr) - 1
                        _worst = -1e9
                        if _n >= 1 and _Ri > 0:
                            _pp = min(1.0, widget.elapsed / max(1e-9, widget.duration))
                            _dd, _bb = widget._upper_funnel_params(_pp, want_upper)
                            _lvl = widget._upper_level_for(want_upper)
                            _hc = math.sqrt(max(
                                0.0, _Ri * _Ri - (_Ri - min(2.0 * _Ri, want_upper)) ** 2))
                            for _i in range(_n + 1):
                                _dx = -_Ri + (2.0 * _Ri / _n) * _i
                                if abs(_dx) > _hc:
                                    continue
                                _surf = (_lvl - widget._upper_surface_drop(_dx, _dd, _bb)
                                         + widget._upper_rough_at(_i, want_upper))
                                _worst = max(_worst, _surf - upper)
                        check(_worst <= 1e-3,
                              "upper sand rect covers the drawn surface: %ss %.2f worst %+.2f px"
                              % (period, fraction, _worst))
                        # (a3) **镜像检查**: 矩形顶也不得**捅穿 carve 的上沿**。
                        #      carve(`_draw_upper_shape`)只画到 `_upper_sand_bot + 2Ri +
                        #      MOUND_CREST_MARGIN`; 矩形若高过它, 抬出来那一条**不会**被抠成
                        #      玻璃 ⇒ 沙体直接露在球顶外面 —— 正是 (a2) 那条的镜像。
                        #      全程最小余量 = `MOUND_CREST_MARGIN`(2.0px, 发生在满球态,
                        #      那里 crest 包络本就是 0) ⇒ 与 rough 档位无关。
                        check(upper <= h_inner + app_module.MOUND_CREST_MARGIN + 1e-3,
                              "upper sand rect stays under the carve limit: %ss %.2f %.2f vs %.2f"
                              % (period, fraction, upper, h_inner + app_module.MOUND_CREST_MARGIN))
                        # (b) ⚠️ **不能拿 `upper + lower` 求和** —— `chords[1]`(下球那个矩形)
                        #     现在是**容器**(高度只有 0.0 或 275.4 两种值, 而 2R=273.4),
                        #     不是沙堆高度。主持人第一版就是这么写的, 结果自己红了 9 次。
                        #     改成守有意义的两条: **上沙高度**与**沙堆高度**各自落在 [0, 2R] 内。
                        check(0.0 <= upper <= h_inner + 1.0,
                              "upper height within the ball: %ss %.2f (%.1f)"
                              % (period, fraction, upper))
                        check(0.0 <= widget._mound_height_px() <= h_inner + 1.0,
                              "mound height within the ball: %ss %.2f" % (period, fraction))
                        # (c) **接触高度 == 画布上真正画出来的那条边**。
                        # 🔴 **2026-10-09 重写**(对抗审查 A1): 旧写法是
                        #     `drawn_apex = widget._lower_sand_bot + widget._mound_contact_h(0.0)`
                        #     拿它与 `get_mound_top_y()` 比 —— 而后者(`main.py:3639`)的定义
                        #     **就是同一个表达式**(`return self._lower_sand_bot +
                        #     self._mound_contact_h(0.0)`) ⇒ `isclose(x, x)` **恒真**。
                        #     它守的是 1.74 §7 的全部要点(取消平台后"接触面必须与可见面重合"),
                        #     却是一条**从来没有可能红过**的断言。
                        #     现在: **数据从画布读**(`_mound_carve` 的折线插值到中轴),
                        #     **谓词是几何**(接触面 vs 画出来的边)。
                        drawn_top = _drawn_mound_top(widget)
                        # 沙堆**没在画**的时候读不到段 —— `_draw_mound_shape` 在
                        # `h_mound <= 0` 时走 `carve.clear()`(条件与绘制点同一个)。
                        # 那时 `None` 是**正确**答案, 不是失败。
                        _mound_drawn = widget._mound_height_px() > 0.0
                        check((drawn_top is not None and
                               abs(widget.get_mound_top_y() - drawn_top) <= epsilon)
                              if _mound_drawn else drawn_top is None,
                              "contact matches the drawn surface: %ss %.2f (%.2f vs %s)"
                              % (period, fraction, widget.get_mound_top_y(),
                                 "None" if drawn_top is None else "%.2f" % drawn_top))
                        # (d0) **D1 守卫: `get_mound_top_y() ≤ _lower_sand_top` 必须恒成立。**
                        #      2026-10-09 对抗审查查到: `_neck_sand_side` 里那个
                        #      `max(get_mound_top_y(), _lower_sand_top)` 的第二项**永远不赢**
                        #      (构造性证明: `contact(0) ≤ roof(0) = 2·profile.radius`,
                        #       而 `_MoundProfile` 全仓库只用 `Ri` 构造 ⇒ 差恒 ≤ 0)。
                        #      ⇒ 那条 `max` 是死代码, 已删。**这条断言守的是它的前提** ——
                        #      将来谁给 `_MoundProfile` 换一个 ≠ `Ri` 的半径, 等式会静默失效
                        #      (那时"沙堆够高就自动接管"会突然活过来), 这里当场红。
                        check(widget.get_mound_top_y()
                              <= widget._lower_sand_top + 1e-9,
                              "mound top never exceeds the ball inner top: %ss %.2f vs %.2f"
                              % (period, widget.get_mound_top_y(), widget._lower_sand_top))
                        # (d) **满态接缝: 画出来的沙柱下沿必须落在画出来的沙堆顶上** —— 2026-10-09。
                        #     ⚠️ **第一版量错了对象**(对抗审查 D3): 量的是「出口 − 堆顶」,
                        #     而 `NECK_JOIN`(**下沿跟沙走**)完全不进 `_mound_carve`/`_mound_apex`/
                        #     `contact` 那条链 ⇒ **两个方向都不会变绿**, 那条断言会永久红着。
                        #     症状的定义是「**柱子**够不着**堆**」—— 所以量这两个。
                        #     ⚠️ 只在"沙堆已经顶到球内顶"(clamp 生效)且**沙柱还在**时判:
                        #       沙柱整根消失的时刻 1s→92.5% / 5s→92.0% / 15s→97.0% 相位。
                        if fraction >= 0.9:
                            _side_now = widget._neck_sand_side()
                            _drawn_top2 = _drawn_mound_top(widget)
                            _clamped = (widget.get_mound_top_y()
                                        >= widget._lower_sand_top - 1e-6)
                            if _side_now and _drawn_top2 is not None and _clamped:
                                _gap2 = _side_now[-1][1] - _drawn_top2
                                check(_gap2 <= MOUND_GAP_GOAL_TOL,
                                      "drawn column meets the drawn mound: %ss %.2f "
                                      "gap %+.2f px (tol %.1f)"
                                      % (period, fraction, _gap2, MOUND_GAP_GOAL_TOL))
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
                # ⚠️ **不能写精确相等** —— 这条 2026-10-06 因一次纯布局改动(底栏 58->46px,
                #    画布高 +12px)翻红, 实测差值恒为 **2.84e-14 px**(四个周期完全相同)
                #    ⇒ 是**浮点末位**, 不是"悬空"。精确相等一直是**靠巧合逐位相同**通过的。
                #    判据要按**它自己的名字**写: "does not float" = 不在沙面上方 ⇒ 用容差。
                #    容差取**上面那条同类检查的同一个**(epsilon), 不放宽。
                check(abs(widget.flares[-1]["y"] - surface) <= epsilon,
                      "impact highlight does not float (%.3e px)"
                      % (widget.flares[-1]["y"] - surface))
                splash = widget.splashes[0]
                # ⚠️ **2026-10-06: 门槛 0.30 -> 0.50**(主持人改, 理由写在这里)。
                #    这条守的不变量是**"飞溅不能比入射快"**(能量), 物理界是 **1.0**;
                #    0.30 从来不是物理值 —— 它是当年照抄旧模型 `U(0.14,0.28)` 的余量。
                #    用户当天要求"范围提高"(铺开必须来自发射、不能靠沿坡长滑),
                #    实测 0.28 只有 11% 越过半宽中点 ⇒ 提到 0.42。
                #    0.50 仍给"不能比入射快"留了**一半**的余量, 不变量没被破坏。
                #    ⚠️ 放宽的是**余量**, 不是判据的名字与形式 —— 别顺手把它改成恒真。
                check(math.hypot(splash["vx"], splash["vy"]) < 200 * 0.50,
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
                # 🔴 **轮次之间必须真的等到"沙子彻底流动完成"**(2026-10-06 用户要求
                #    「等沙子彻底流动完成后的 1 秒之后再进行下一次测试」)。
                #    包住 `_begin_case`, 在**每一轮开始那一刻**把静止状态记下来 ——
                #    这是唯一不自我循环的取证方式: 旧代码(立刻进下一轮)下这几个数非零。
                self._case_start_states = []
                _runner = self._benchmark_runner
                _orig_prepare = _runner._prepare_case

                def _record_prepare(_dt, _r=_runner, _o=_orig_prepare):
                    # ⚠️ **必须记 `_prepare_case` 的入口, 不能记 `_begin_case`** ——
                    #    `_begin_case` 比它晚 0.5s, 那时 `reset()` 早把粒子清空了,
                    #    于是**改前改后都是全零**, 检查形同虚设(2026-10-06 负对照当场揭穿)。
                    #    `_prepare_case` 的入口正是"决定进入下一轮"那一刻, 而 reset 还在它体内。
                    # 第 0 轮由 `start()` 直接调, 不是"轮次之间的间隔", 跳过;
                    # `_index == len(periods)` 那次是收尾(`_finish`), 也不是间隔。
                    if 0 < _r._index < len(_r.periods):
                        w = self.hourglass
                        self._case_start_states.append(
                            (w.pn, len(w.splashes), len(w.dusts), len(w.flares),
                             len(w._neck_sand_side())))
                    _o(_dt)

                _runner._prepare_case = _record_prepare
                # ⚠️ **预算必须把"轮次之间的静置"算进去** —— 2026-10-06 加静置时踩到:
                #    这里原来写死 `sum(PERIODS) + 3`, 而静置让整轮多花几秒 ⇒
                #    `finish_checks` 到点时 `_benchmark_popup` 还是 None ⇒
                #    `verify_save()` 里 `None.dismiss()` 抛 AttributeError,
                #    **整份闸门以异常收场、连 FAILED 汇总都打不出来**。
                #    按模块常量算, 常量改了这里自动跟上。
                _gaps = max(0, len(PERIODS) - 1)
                Clock.schedule_once(
                    self.finish_checks,
                    sum(PERIODS) + 3
                    + _gaps * (benchmark_module.SETTLE_AFTER_QUIET
                               + benchmark_module.SETTLE_TIMEOUT))
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
                # 每轮开始那一刻必须已经彻底静止(在途粒子/飞溅/尘埃/闪光/颈部沙柱全空)。
                states = getattr(self, "_case_start_states", [])
                check(len(states) == len(PERIODS) - 1,
                      "benchmark records the flow state at every period transition")
                check(all(s == (0, 0, 0, 0, 0) for s in states),
                      "next benchmark period waits for the sand to settle: %s" % (states,))
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
        if known_defects:
            print("已知缺陷(已认领, 不计入 FAILED):")
            for _k in known_defects:
                print("   -", _k)
        if failures:
            print("FAILED:", failures)
            return 1
        print("All checks passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
