"""Deterministic flow screenshots and measurements; never used for FPS scoring."""

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "main.py")
    parser.add_argument("--label", default="current")
    parser.add_argument("--pixels", default="400,800")
    parser.add_argument("--chunk-flow", action="store_true")
    parser.add_argument("--gpu-flow", action="store_true")
    parser.add_argument("--texture-flow", action="store_true")
    parser.add_argument("--dense-neck", action="store_true")
    parser.add_argument("--landscape", action="store_true")
    # 密度三臂实验用: 固定周期、密采样稳态、可换沙色、可覆写粒子率
    parser.add_argument("--sand", default=None, help="沙色预设名(金沙/红沙/...)")
    parser.add_argument("--speed-factor", type=float, default=None,
                        help="覆写 speed_factor(仅测量脚手架, 不改几何)")
    parser.add_argument("--steady-period", type=float, default=None)
    parser.add_argument("--steady-frames", type=int, default=30)
    # 🔴 2026-10-09: **末段档** —— steady 模式**按设计切掉末 8%**(见下面 `_lo,_hi` 那行),
    #    于是"缝 / 末期"这一类缺陷在闸门里**没有任何一帧**能看见
    #    (1.237 那次与"末段那条缝"是同一个洞咬了两次)。
    #    `--only-tail` **复用默认目标集里已有的末段点**(0.98/1、4.98/5、14.98/15、59.98/60),
    #    不新造时间窗参数。
    parser.add_argument("--only-tail", action="store_true")
    # 🔴 2026-10-09: **开局档** —— steady 模式切掉前 0.5s(`_lo=0.5`), 而
    #    "沙柱在沙还没落到之前就画出来"这个缺陷**恰恰只出现在开局**(50s 档前 2 秒,
    #    用户当场指出)。这一档把 steady 周期的时间窗换成 **[0.25s, 2.5s]**。
    parser.add_argument("--early-window", type=int, default=0,
                        help="开局档: 在 steady 周期上取 N 帧, 时间窗 [0.25, 2.5]s")
    # 🔴 2026-10-10: **双背景差分(chroma-key)** —— 把 `BG_COLOR` 与 `GLASS_FILL`
    #    一起覆写成同一个颜色。两次渲染只差这一个变量, 于是逐像素
    #        P₁ − P₂ = (1−α_粒子)(1−α_柱)·(B₁ − B₂)
    #    ⇒ `α_有效 = 1 − (P₁−P₂)/(B₁−B₂)` **与沙色、与材质纹理完全无关**。
    #    这是**唯一**能把"亮色不透明"与"真半透明"分开的尺子:
    #    旧尺(把像素投影到 背景→纯沙色 连线)把 `sand_light` 判成 α=0.854,
    #    于是"把柱子调亮"就能让它"达标" —— 实测中位 1.000→0.933(alpha 一个像素没动),
    #    判别性负对照见 `tools/_probe_oldruler_control.py`。
    #    ⚠️ 用**改写模块常量**而不是环境变量: 这样**任何历史版本的树**都能跑
    #    (`_wt_12` / `_wt_old` 里没有环境变量钩子), 四个状态才能在同一口径下比。
    parser.add_argument("--key-bg", default=None,
                        help='双背景差分: 把 BG_COLOR 与 GLASS_FILL 都设成这个颜色(如 "#303030")')
    # 🔴 2026-10-10: **密采样窗口**。`--steady-period` 把时间铺满 [0.5, 0.92·P]
    #    ⇒ 12 帧就隔着 1.2s, **做不出 0.04s 的密采样**。而 shader 的颗粒图案
    #    每 `1/speed_scale` 秒被 `floor(t)*jump` **整体重排一次**(横向跳 0.125·直径),
    #    跨过重排点之后帧间根本没有相干 —— 实测跨 1.21s 的互相关峰值 z 只有 2.2~3.0,
    #    在 605 个候选里**不显著**。要量"颗粒流得多快"只能在一个重排周期之内密采样。
    parser.add_argument("--window", default=None,
                        help="密采样: t0,dt,n —— 在 steady 周期上取 n 帧、相邻间隔 dt 秒")
    args = parser.parse_args()
    output = ROOT / "benchmark_logs" / ("flow_visual_" + args.label)
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flow-visual-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from kivy.graphics.transformation import Matrix
        from PIL import Image
        import main  # Register the bundled font before loading a baseline copy.

        spec = importlib.util.spec_from_file_location("flow_visual_source", args.source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if args.key_bg:
            # 两处都是**方法内的全局查表**(`_build_glass_shell` 的 5597 / `_build_dynamic_canvas`
            # 的 6463 与 6471 / `HourglassApp.build` 的 7669 与 5641) ⇒ 在模块加载之后、
            # 建画布之前改写, 逐字生效。默认分支一个字都不碰。
            module.BG_COLOR = args.key_bg
            module.GLASS_FILL = args.key_bg
            print("双背景差分: BG_COLOR = GLASS_FILL = %s" % args.key_bg)
        if args.speed_factor is not None:
            module.HourglassWidget.speed_factor = property(
                lambda self, _v=args.speed_factor: _v)
        if args.chunk_flow:
            chunk_spec = importlib.util.spec_from_file_location(
                "flow_chunk_experiment", ROOT / "tools" / "flow_chunk_experiment.py")
            chunk_module = importlib.util.module_from_spec(chunk_spec)
            chunk_spec.loader.exec_module(chunk_module)
            chunk_module.install(module.HourglassWidget)
        if args.gpu_flow:
            gpu_spec = importlib.util.spec_from_file_location(
                "flow_gpu_experiment", ROOT / "tools" / "flow_gpu_experiment.py")
            gpu_module = importlib.util.module_from_spec(gpu_spec)
            gpu_spec.loader.exec_module(gpu_module)
            gpu_module.install(module.HourglassWidget)
        if args.texture_flow:
            texture_spec = importlib.util.spec_from_file_location(
                "flow_texture_experiment", ROOT / "tools" / "flow_texture_experiment.py")
            texture_module = importlib.util.module_from_spec(texture_spec)
            texture_spec.loader.exec_module(texture_module)
            texture_module.install(module.HourglassWidget)
        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        # 完成弹窗 auto_dismiss=False: 用例跑过第 1 秒档就会弹出并**永不关闭**,
        # 把后面所有裁图盖住。取证工具必须屏蔽它, 与上面的音效桩同理。
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])
        cases = [
            (1, (0, 0.1, 0.2, 0.4, 0.7, 0.98, 1)),
            (5, (0.2, 0.6, 1, 1.5, 2.5, 4.98, 5)),
            (15, (0.2, 0.6, 1.3, 2, 7.5, 14.98, 15)),
            (60, (30, 59.98, 60)),
        ]
        if args.dense_neck:
            extra = {
                1: (0.125, 0.15, 0.175, 0.225, 0.25, 0.3, 0.35),
                5: (0.25, 0.275, 0.3, 0.325, 0.35, 0.4, 0.45, 0.5, 0.7, 0.8),
                15: (0.25, 0.275, 0.3, 0.325, 0.35, 0.4, 0.45, 0.5, 0.55, 0.7, 0.8),
            }
            cases = [(period, tuple(sorted(set(times) | set(extra.get(period, ())))))
                     for period, times in cases]
        targets = [(period, elapsed) for period, times in cases for elapsed in times]
        if args.steady_period:
            _p, _n = float(args.steady_period), max(2, args.steady_frames)
            _lo, _hi = 0.5, _p * 0.92          # 切掉前 0.5s 注满与末 8% 收尾
            targets = [(_p, _lo + (_hi - _lo) * i / (_n - 1)) for i in range(_n)]
        if args.early_window:
            _p, _n = float(args.steady_period or 50.0), max(2, args.early_window)
            targets = [(_p, 0.25 + 2.25 * i / (_n - 1)) for i in range(_n)]
        if args.window:
            _p = float(args.steady_period or 15.0)
            _t0, _dt, _n = (float(v) for v in args.window.split(","))
            targets = [(_p, _t0 + _dt * i) for i in range(int(_n))]
        if args.only_tail:
            # 只留末段(≥0.9 周期)。缝的窗口从 **94~95% 相位**开始(5s 与 50s 实测一致),
            # 所以 0.9 这个门槛一定落在它前面。
            targets = [(p, e) for p, e in targets if e >= 0.9 * p]
        records = []

        class VisualApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                width, height = map(int, args.pixels.split(","))
                self._want = (width, height)
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(width / ratio), round(height / ratio))
                # 🔴 **2026-10-10: 窗口尺寸不是"设了就到位"的。**
                #    原写法只等固定的 0.3s 就开跑, 而 SDL 把窗口改到目标尺寸、Kivy 把它
                #    派发进布局, 是要**好几帧**的。机器一忙就赶不上 ⇒ 几何在**跑到一半**
                #    才变。实测签名: 同一条命令的两次渲染, `2R_inner` 一次 1238.7、
                #    一次 395.5; 而后者在第 1 帧(记录里 t=0.20)还是 395.5、到 t=7.50
                #    已经变成 1880 ⇒ **同一个 run 内部几何自己变了**。
                #    后果: 双背景差分拿到的两张图**画的不是同一个沙漏**, 差分全是垃圾。
                #    ⇒ 改成**等它连续 3 次采样不动**再开跑。
                self._prev = None
                self._stable = 0
                Clock.schedule_interval(self._settle, 0.1)

            def _settle(self, _dt):
                w = getattr(self, "hourglass", None)
                if w is None:
                    return
                key = (round(Window.width), round(Window.height),
                       round(getattr(w, "_R_inner", 0.0), 3))
                if key == self._prev:
                    self._stable += 1
                else:
                    self._stable, self._prev = 0, key
                if self._stable >= 3 and round(Window.width) == self._want[0]:
                    Clock.unschedule(self._settle)
                    print("几何稳定: Window=%dx%d  2R_inner=%.1f"
                          % (round(Window.width), round(Window.height),
                             2 * getattr(w, "_R_inner", 0.0)))
                    # 🔴 **2026-10-11: 材质没起来就直接失败, 不许静默出图。**
                    #    着色器**编译失败**时 `main.py` 只印一行
                    #    `GPU sand flow unavailable, keeping static material: ...` 就退回
                    #    **静态贴图**(一整块平色板) —— 而那张图看着**还是一条沙柱**,
                    #    于是"这一臂"被当成"改动没效果/还是一个固体"。
                    #    当天就这么把一张"A/B"交到用户手上, 他判的词是「右侧还是一个固体啊」
                    #    —— **右边那条臂根本没跑**(报错是 `undefined variable "cg"`)。
                    #    ⇒ 凡是"要靠材质才算数"的实验, 起跑前必须自己确认它真的活着。
                    if os.environ.get("HG_SAND_FLOW", "1") != "0" \
                            and w._current_material() \
                            and not getattr(w, "_sand_flow_contexts", ()):
                        raise SystemExit(
                            "!! GPU 材质没起来(多半是着色器编译失败) —— 这一臂跑的是"
                            "**退回静态贴图**的兜底路径, 不能拿来当 A/B 的臂。\n"
                            "   先看上面 'shader failed to compile / error C####' 那几行。")
                    Clock.schedule_once(self.begin, 0.2)
                    # 🔴 **2026-10-11: 把"团的屏幕尺寸"读出来, 别靠推。**
                    #    聚团的纵向坐标是 `mat_y·(球径/柱半宽)·scale` ⇒ 屏幕周期
                    #       = 柱半宽·v(深度)/(scale·vref)
                    #    —— 三处都是**运行期**的量(柱半宽随周期变、v 随深度变),
                    #    纸上算过一次差了 3 倍(把 `t_in` 当成 24px, 实际远大于此)。
                    _print_clump(w) if False else None
                    _ctxs = getattr(w, "_sand_flow_contexts", ())
                    if _ctxs:
                        # ⚠️ 读 `_taper["t_in"]`, **不要**读 `ctxs[1]["sand_free"][2]` ——
                        #    后者在没开跑时还是初值 `(0,0,1,0)` 里的 1.0, 会把标定算歪 5 倍
                        #    (2026-10-11 就这么纸上算错过一次)。
                        _z = float(w._taper["t_in"])
                        _diam = 2 * getattr(w, "_R_inner", 0.0)
                        # ⚠️ **不在这里算"团长"**: 纸上推的公式与实测对不上(差 1.8~4 倍,
                        #    且不成比例) ⇒ 只报旋钮值, 尺度一律**看渲染图**标定。
                        _c = _ctxs[1]
                        try:
                            _knobs = (float(_c["sand_clump_gap"]),
                                      float(_c["sand_clump_scale"]),
                                      float(_c["sand_clump_xscale"]),
                                      float(_c["sand_clump_warp"]),
                                      float(_c["sand_clump_edge"]))
                        except KeyError:
                            # ⚠️ 旧树(2.64 及以前)没有这些 uniform —— 这里必须**静默跳过**,
                            #    否则探针会在 `_settle` 里抛 KeyError ⇒ **旧树一张图都渲染不出来**
                            #    (2026-10-11 踩过: A/B 想对 2.64, 结果目录是空的)。
                            _knobs = None
                        if _knobs is not None:
                            # 🔴 2026-10-11: **把生效的九元组与 motion_scale 打进日志** ——
                            #    没有这一行时,"这一臂到底跑的是哪档"只能靠注入方式反推,
                            #    而本项目出过多次"臂空转被读成无差别"的事故(1.213)。
                            #    `ms` 是材料坐标的时间压缩因子(短周期 >1), 速度比判据要用它。
                            print("clump: 柱半宽=%.1f 球径=%.1f gap=%.3f scale=%.2f "
                                  "xscale=%.2f warp=%.2f edge=%.2f lo=%.2f hi=%.2f "
                                  "start=%.2f gain=%.2f | ms=%.3f"
                                  % (_z, _diam, *_knobs,
                                     float(_c["sand_clump_lo"]), float(_c["sand_clump_hi"]),
                                     float(_c["sand_clump_start"]), float(_c["sand_clump_gain"]),
                                     float(_c["sand_motion_scale"])))

            def begin(self, _dt):
                if args.sand:
                    for _nm, _b, _d, _l in module.SAND_PRESETS:
                        if _nm == args.sand:
                            self.hourglass.set_sand_color(_b, _d, _l)
                self.first_hit = None   # 周期与 load_config 桩相同(60)时下面不会初始化
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                Clock.unschedule(self.hourglass.tick)
                self.real_draw = self.hourglass.redraw
                self.next_frame(0)

            def next_frame(self, _dt):
                if not targets:
                    (output / "measurements.json").write_text(
                        json.dumps(records, indent=2), encoding="utf-8")
                    self.stop()
                    return
                period, target = targets.pop(0)
                widget = self.hourglass
                # 注意: load_config 桩返回 duration=60, 与 --steady-period 60 相同 ⇒
                # 只判 duration 会跳过启动, running 一直 False, 粒子一个都不生成。
                if widget.duration != period or not widget.running:
                    if widget.duration != period:
                        widget.set_duration(period)
                    # 🔴 **必须在这里再打一次**: 上面 `_settle` 那一次跑在**设周期之前**,
                    #    读到的 `sand_motion_scale` 还是初值(1.0) —— 而 ms 是短周期才 >1 的
                    #    (`ms = max(1, natural / (_fall_delay − _neck_fill))`)。
                    #    2026-10-11 用旧读数算过一次标定, 结果 ms 恒 1.000(错)。
                    print("clump@%.1fs: ms(真值)=%.3f" % (period, widget._particle_motion_scale))
                    widget.completion_enabled = False
                    # ★ **预热必须夹在这里** —— 与真实使用同序(改完周期 → 闲帧 → 按下开始),
                    #   而且 `toggle()` 之后 `running=True`, `_warm_batches_step` 会直接返回。
                    #   见 `main.py:_warm_batches_step`(它把"第一次有内容才建"的批处理块
                    #   挪到动画之外, 免得起跑那一帧建 7 块 —— 用户设备实测 43.88ms)。
                    for _ in range(400):
                        _before = len(getattr(widget, "_warm_queue", ()))
                        widget._warm_batches_step()
                        if len(getattr(widget, "_warm_queue", ())) >= _before:
                            break
                    if not widget.running:
                        widget.toggle()
                    random.seed(23)
                    self.first_hit = None
                # Drawing is observational; advancing only physics keeps snapshots inexpensive.
                widget.redraw = lambda: None
                while widget.elapsed + 1e-8 < target and widget.running:
                    step = min(1 / 120, target - widget.elapsed)
                    now[0] += step
                    widget.tick(step)
                    if widget.flares and self.first_hit is None:
                        self.first_hit = widget.elapsed
                widget.redraw = self.real_draw
                # 量这一帧的"索引重建"触发情况 —— 只统计触发条件, 不是实测 GL 流量。
                import sys as _sys
                _flow = _sys.modules.get("flow_texture_experiment")
                if _flow is not None:
                    _flow.stats_reset()
                widget.redraw()
                flow_stats = dict(_flow.STATS) if _flow is not None else {}
                self.duration_btn.text = module._fmt_duration(period)
                self.on_run_state_changed()
                records.append({
                    "period": period, "elapsed": widget.elapsed,
                    "mound_px": widget._mound_height_px(),
                    "full_height_px": 2 * widget._R_inner,
                    "contact_gap_px": widget.get_mound_top_y() -
                                      (widget._lower_sand_bot + widget._mound_height_px()),
                    "first_hit_s": self.first_hit,
                    "particles": len(widget.particles), "splashes": len(widget.splashes),
                    "flow_stats": flow_stats,
                    "neck_grains": widget._neck_grain_count,
                    "neck_outlet_y": 2 * widget._neck_y - widget._taper["y_bot"],
                    "neck_inlet_y": widget._taper["y_bot"],
                    "max_trail_px": max(
                        (widget._particle_trail(p) if hasattr(widget, "_particle_trail")
                         else abs(p["vy"]) * 0.08 for p in widget.particles), default=0),
                })
                Clock.schedule_once(
                    lambda _dt, p=period, t=target: self.capture(p, t), 0.05)
                Clock.schedule_once(self.next_frame, 0.12)

            def capture(self, period, elapsed):
                width, height = map(int, Window.size)
                # RGBA rows avoid RGB pack-alignment corruption on fractional-DPI windows.
                pixels = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                image = Image.frombytes("RGBA", (width, height), pixels)
                image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                requested = tuple(map(int, args.pixels.split(",")))
                if image.size != requested:
                    image = image.resize(requested, Image.Resampling.LANCZOS)
                image.save(output / f"period-{period}-time-{elapsed:.2f}.png")
                x_scale, y_scale = requested[0] / width, requested[1] / height
                widget = self.hourglass
                center_x, center_y = widget._cx, widget._neck_y
                if self.root.angle:
                    origin_x, origin_y = self.root._rot.origin[:2]
                    rotation = Matrix().rotate(math.radians(self.root.angle), 0, 0, 1)
                    dx, dy, _ = rotation.transform_point(
                        center_x - origin_x, center_y - origin_y, 0)
                    center_x, center_y = origin_x + dx, origin_y + dy
                    logical = self.root._to_eq(center_x, center_y)
                    if not all(math.isclose(a, b, abs_tol=1e-4)
                               for a, b in zip(logical, (widget._cx, widget._neck_y))):
                        raise AssertionError("Neck crop rotation disagrees with the application")
                # 🔴 2026-10-09: 裁图窗口原来是**写死的 ±52 × ±62 像素**，与口径无关 ⇒
                #    在平板口径(1904×2890, t_in≈27.6)下它只框住**柱子内部的一小块**，
                #    拼出来的"三臂并排图"看着几乎一样 —— 图是废的，而当时没先验量具。
                #    改成**按几何派生**：半宽 ∝ 孔径，下边界从**出口**再往下让 150px
                #    (收缩段只有 40px 是写死的，必须保证它在框里)。
                #    ⚠️ 桌面口径下上边界与半宽与旧版**一致**(52 > 2.6·t_in=15.4)，
                #       只有下边界变深(326.8 → 229.4)——这是有意的，旧的下边界**根本没框到收缩段**。
                _tp = widget._taper
                _outlet = 2.0 * widget._neck_y - _tp["y_bot"]
                _half_w = max(52.0, 2.6 * _tp["t_in"])
                _top = center_y + 62.0
                _bot = min(center_y - 62.0,
                           _outlet - max(150.0, 3.0 * _tp["t_in"]))
                crop = image.crop((
                    max(0, round((center_x - _half_w) * x_scale)),
                    max(0, round((height - _top) * y_scale)),
                    min(requested[0], round((center_x + _half_w) * x_scale)),
                    min(requested[1], round((height - _bot) * y_scale))))
                crop.resize((416, 496), Image.Resampling.NEAREST).save(
                    output / f"neck-{period}-time-{elapsed:.2f}.png")

        VisualApp().run()
        print("Visual measurements:", output)


if __name__ == "__main__":
    main()
