# -*- coding: utf-8 -*-
"""2号(复现者) · 独立确认「颈部颗粒层在 GPU 材质路径下是死层」这条主张。

主张: `_draw_neck_grains` / `_neck_grain_pool` 在材质路径下**每帧白走、一个像素不画**,
      但它那 320 对 (Color, Line) 一直挂在画布上照常 `apply()`。

四种模式(环境变量 P2_MODE):

  census  数画布指令: 按族 / 按类型 / 颈部池子内部; 并回读
          `_sand_material` / `_sand_flow_contexts` / `_neck_grain_count` / 池内非空线数。
  calls   包住 `w._draw_neck_grains` 数它被调了几次(以及 redraw 几次)。
          —— 判调用点, 不判画面。
  time    冻结动画, **交替** 相位: 挂上 / 摘掉 某一族, 量 `Window.on_draw` 差。
          P2_FAMILY = none | neck | marker | extra
            none   = 对照臂(什么都不摘, 量方法自身噪声)
            neck   = 待确认的目标族
            marker = 正对照(已知活着的族: 表层滑动标记, 20 对 Color+Line)
            extra  = 再挂一份"同形空池"(320 对空 Line), 验量法对 +641 条有线性响应
  pix     冻结后截图 A → 摘掉目标 → 截 B, 报逐像素差。
          P2_CUT = none | neck | neck_slots | pause | markers
            none       = 同状态双截(必须 0 差, 否则量法本身不可信)
            pause      = 全屏暂停遮罩(必须大差, 证明截图通路是活的)
            markers    = 摘掉表层标记组(已知活着, 正对照)
            neck       = 摘掉 `_neck_grain_group`(桌面默认路径的死层)
            neck_slots = 摘掉 `_neck_context` 里那 32 个色调槽(批处理路径的死层)

环境变量:
  P2_BATCH=1   装安卓出货那套渲染器 (HG_FLOW_RENDERER=texture / SPLASH+NECK+FLARE=batch)
  P2_FLAT=1    HG_SAND_MATERIAL=flat (关材质 ⇒ 回退路径)
  P2_PERIOD / P2_FRAC / P2_SPAN / P2_FAMILY / P2_CUT

⚠️ 一律先标定再判: `pix` 的 none/pause 两臂 + `time` 的 none/marker/extra 两臂是标定,
**标定不过的读数不看**。

跑法:
  PYTHONIOENCODING=utf-8 python tools/_p2_deadlayer_probe.py            (各变量见上)
"""
import collections
import os
import sys
import tempfile
import time as _time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODE = os.environ.get("P2_MODE", "census")
BATCH = os.environ.get("P2_BATCH", "0") == "1"
FLAT = os.environ.get("P2_FLAT", "0") == "1"
FAMILY = os.environ.get("P2_FAMILY", "neck")
CUT = os.environ.get("P2_CUT", "neck")
PERIOD = float(os.environ.get("P2_PERIOD", "15"))
FRAC = float(os.environ.get("P2_FRAC", "0.5"))
SPAN = float(os.environ.get("P2_SPAN", "2.0"))


def main():
    home = tempfile.mkdtemp(prefix="p2dl-")
    os.environ["KIVY_HOME"] = home
    os.environ["KIVY_NO_ARGS"] = "1"
    os.environ["KIVY_NO_FILELOG"] = "1"
    os.environ["KIVY_METRICS_DENSITY"] = "1.75"
    _RENDER_ENVS = ("HG_FLOW_RENDERER", "HG_SPLASH_RENDERER",
                    "HG_NECK_RENDERER", "HG_FLARE_RENDERER", "HG_MARKER_RENDERER")
    if BATCH:
        os.environ["HG_FLOW_RENDERER"] = "texture"
        os.environ["HG_SPLASH_RENDERER"] = "batch"
        os.environ["HG_NECK_RENDERER"] = "batch"
        os.environ["HG_FLARE_RENDERER"] = "batch"
    else:
        for k in _RENDER_ENVS:
            os.environ.pop(k, None)
    if FLAT:
        os.environ["HG_SAND_MATERIAL"] = "flat"
    else:
        os.environ.pop("HG_SAND_MATERIAL", None)
    if os.environ.get("P2_NOFLOW") == "1":
        os.environ["HG_SAND_FLOW"] = "0"
    else:
        os.environ.pop("HG_SAND_FLOW", None)

    sys.path.insert(0, str(ROOT))
    import main as m
    if os.environ.get("P2_NOGPU") == "1":
        # 模拟"GPU sand flow unavailable"(着色器编译失败 / 设备不支持):
        # `_build_dynamic_canvas` 里是 `from sand_flow_material import SandFlowContext`,
        # 每次建画布时**读模块属性** ⇒ 把它换成一个必然抛异常的类即可。
        import sand_flow_material as _sfm

        class _BoomCtx(object):
            def __init__(self, *a, **k):
                raise RuntimeError("simulated sand flow shader failure")

        _sfm.SandFlowContext = _BoomCtx
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics import Color, InstructionGroup, Line
    from kivy.graphics.opengl import (glReadPixels, GL_RGBA,
                                      GL_UNSIGNED_BYTE)
    import numpy as np

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None
    now = [1000.0]
    m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

    box = {"res": collections.defaultdict(list), "phase": 0,
           "cpu": collections.defaultdict(list)}

    orig_on_draw = Window.on_draw

    def on_draw_probe(*a, **k):
        t0 = _time.perf_counter()
        c0 = _time.process_time()
        try:
            return orig_on_draw(*a, **k)
        finally:
            if box["phase"]:
                box["res"][box["phase"]].append(
                    (_time.perf_counter() - t0) * 1000.0)
                # ★ **CPU 时间**: 墙钟会被 vsync/呈现吸收(vsync 一挡, 多干的活看不见),
                #   而 apply() 的 Python/Cython 开销是 CPU 活 ⇒ 用 process_time 才量得到。
                box["cpu"][box["phase"]].append(
                    (_time.process_time() - c0) * 1000.0)

    Window.on_draw = on_draw_probe

    def walk(node, ty, stats, fam=None):
        for ch in getattr(node, "children", ()):
            nm = type(ch).__name__
            ty[nm] += 1
            stats["total"] += 1
            if fam is not None:
                fam[nm] += 1
            walk(ch, ty, stats, fam)

    def census(hg):
        ty = collections.Counter()
        fam = collections.defaultdict(collections.Counter)
        stats = {"total": 0}
        label_map = [("_neck_grain_group", "neck_grains"),
                     ("_surface_marker_group", "markers"),
                     ("_flare_group", "flare"),
                     ("_splash_group", "splash"),
                     ("_dust_group", "dust"),
                     ("_mound_carve", "mound_carve"),
                     ("_mound_band", "mound_band"),
                     ("_upper_carve", "upper_carve"),
                     ("_upper_band", "upper_band"),
                     ("_neck_context", "neck_batch_ctx")]
        for i, ch in enumerate(hg.canvas.children):
            top = None
            for attr, tag in label_map:
                if getattr(hg, attr, None) is ch:
                    top = tag
                    break
            if top is None and type(ch).__name__ in ("_QuadBand", "Mesh", "RenderContext"):
                for attr in ("_neck_quads",):
                    if getattr(hg, attr, None) is ch:
                        top = attr
            if top is None:
                top = "%s#%d" % (type(ch).__name__, i)
            fam[top][type(ch).__name__] += 1
            stats["total"] += 1
            sub = {"total": 0}
            walk(ch, ty, sub, fam[top])
            fam[top]["**total**"] += sub["total"]
            stats["total"] += sub["total"]
        return ty, fam, stats

    def report_state(hg, out=None):
        emit = (lambda s: print(s)) if out is None else out.append
        emit("  -- 状态回读 --")
        emit("  _sand_material is not None      : %s"
             % (hg._sand_material is not None))
        emit("  len(_sand_flow_contexts)        : %d"
             % len(getattr(hg, "_sand_flow_contexts", ()) or ()))
        emit("  neck_renderer(类属性)           : %s"
             % getattr(type(hg), "neck_renderer", None))
        emit("  has _neck_context (批处理)      : %s"
             % hasattr(hg, "_neck_context"))
        emit("  _draw_neck_grains is batched    : %s"
             % getattr(type(hg)._draw_neck_grains, "_neck_batched", False))
        pool = getattr(hg, "_neck_grain_pool", [])
        emit("  len(_neck_grain_pool)           : %d" % len(pool))
        emit("  _neck_grain_count               : %s"
             % getattr(hg, "_neck_grain_count", None))
        n_nonempty = 0
        for c, ln in pool:
            if getattr(ln, "points", None):
                n_nonempty += 1
        emit("  池内 points 非空的线            : %d" % n_nonempty)
        if hasattr(hg, "_neck_batches"):
            bs = hg._neck_batches
            emit("  _neck_batches                   : %s"
                 % ("None" if bs is None else "%d 个色调批" % len(bs)))
            if bs:
                nparts = n_idx = 0
                alphas = []
                for color, batch in bs:
                    alphas.append(round(float(color.a), 2))
                    for part in getattr(batch, "parts", ()):
                        nparts += 1
                        if len(part[0].indices or ()) > 0:
                            n_idx += 1
                emit("  批内已建块 / 索引非空的块       : %d / %d"
                     % (nparts, n_idx))
                emit("  32 档 Color alpha 取值集合      : %s"
                     % sorted(set(alphas)))
        return out

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.setup, 1.0)

        def setup(self, _dt):
            Clock.schedule_once(self.prepare, 0.4)

        def prepare(self, _dt):
            w = self.hourglass
            try:
                Clock.unschedule(self._warm_sand_materials)
            except Exception:
                pass
            w.completion_enabled = False
            w.set_duration(PERIOD)
            w.reset()
            w.running = True
            w.last_tick = now[0]
            dt = 1.0 / 120.0
            target = PERIOD * FRAC
            while w.elapsed < target:
                now[0] += dt
                w.tick(dt)
            # 冻结: 物理停(不再推进 elapsed), 但 **`tick` 仍然每帧跑** ——
            # 窗口只在有 Clock 事件时才出帧, 把 tick 摘掉会变成"每相位只画一帧",
            # 而那一帧是在**画布重编译**, 量到的不是稳态 apply 成本(实测 n=1/2s)。
            w.running = False
            w._completion_triggered = False
            self.box = box
            if MODE == "census":
                self.do_census()
                self.stop()
            elif MODE == "neckc":
                self.do_neckc()
                self.stop()
            elif MODE == "tree":
                self.do_tree()
                self.stop()
            elif MODE == "calls":
                self.do_calls()
            elif MODE == "time":
                self.do_time()
            elif MODE == "pix":
                self.do_pix()

        # ---------------- census ----------------
        def do_census(self):
            w = self.hourglass
            out = []
            out.append("  === %s === (周期 %.0fs, 冻结在 %.0f%%)"
                       % ("BATCH(安卓出货配置)" if BATCH else "桌面默认配置",
                          PERIOD, FRAC * 100))
            report_state(w, out)
            ty, fam, stats = census(w)
            out.append("")
            out.append("  -- 画布指令按族(顶层节点) --")
            for k, c in sorted(fam.items(), key=lambda kv: -kv[1]["**total**"]):
                n = c["**total**"]
                if n:
                    det = dict((kk, vv) for kk, vv in c.most_common(8)
                               if kk != "**total**")
                    out.append("  %-22s %-7d %s" % (k, n, det))
            out.append("")
            out.append("  -- 按类型合计 --")
            for k, v in ty.most_common(14):
                out.append("  %-22s %d" % (k, v))
            out.append("")
            out.append("  **画布总计 %d 条**" % stats["total"])
            # canvas.before(玻璃壳)
            nbefore = 0
            tb = collections.Counter()
            for ch in w.canvas.before.children:
                tb[type(ch).__name__] += 1
                nbefore += 1
                s2 = {"total": 0}
                walk(ch, tb, s2)
                nbefore += s2["total"]
            out.append("  (canvas.before 玻璃壳另有 %d 条: %s)"
                       % (nbefore, dict(tb.most_common(6))))
            print("\n".join(out))
            sys.stdout.flush()

        # ---------------- neckc ----------------
        def do_neckc(self):
            """批处理路径下 `_neck_context` 的指令数: **刚建完画布(预热前) vs 预热后**。

            主张里写的是 "neck 族 65 条" —— 65 = 1(RenderContext) + 32×(InstructionGroup
            + Color) 正好是**预热前**的槽结构; 预热会给每个槽补 `BindTexture + BindTexture
            + Mesh`(5 条/槽) ⇒ 161。
            """
            w = self.hourglass
            print("  === neckc: %s ===" % (
                "BATCH(安卓出货配置)" if BATCH else "桌面默认配置"))
            report_state(w)
            w._build_dynamic_canvas()      # 重建一次 ⇒ 拿到"预热前"的干净状态

            def count_ctx(ctx):
                ty = collections.Counter()

                def walk2(node):
                    for ch in getattr(node, "children", ()):
                        ty[type(ch).__name__] += 1
                        walk2(ch)
                walk2(ctx)
                return ty

            ctx = getattr(w, "_neck_context", None)
            if ctx is None:
                print("  没有 _neck_context(batch 没装)")
                sys.stdout.flush()
                return
            print("  预热前: %d 条 %s"
                  % (sum(count_ctx(ctx).values()), dict(count_ctx(ctx))))
            # 手动把预热队列跑干(等价于界面上"按开始之前那 ~0.5s")
            w._warm_queue = w._collect_warm_jobs()
            n_jobs = len(w._warm_queue)
            guard = 0
            while w._warm_queue and guard < 200:
                w._warm_batches_step()
                guard += 1
            print("  预热队列 %d 个任务, 跑了 %d 轮" % (n_jobs, guard))
            print("  预热后: %d 条 %s"
                  % (sum(count_ctx(ctx).values()), dict(count_ctx(ctx))))
            bs = w._neck_batches or []
            nparts = sum(len(b.parts) for _c, b in bs)
            n_idx = sum(1 for _c, b in bs for p in b.parts
                        if len(p[0].indices or ()) > 0)
            print("  批内块数 %d, 其中索引非空 %d" % (nparts, n_idx))
            sys.stdout.flush()

        # ---------------- tree ----------------
        def do_tree(self):
            w = self.hourglass
            print("  === tree: %s ===" % (
                "BATCH(安卓出货配置)" if BATCH else "桌面默认配置"))
            report_state(w)
            for name in ("_neck_grain_group", "_neck_context",
                         "_surface_marker_group"):
                g = getattr(w, name, None)
                if g is None:
                    continue
                print("  -- %s (%s) --" % (name, type(g).__name__))

                def dump(node, depth, limit=6, budget=[40]):
                    ch = list(getattr(node, "children", ()))
                    for i, c in enumerate(ch):
                        if i >= limit and len(ch) > limit + 1:
                            if i == limit:
                                print("  " + "    " * depth + "...(共 %d 个子节点)"
                                      % len(ch))
                            continue
                        if budget[0] <= 0:
                            return
                        budget[0] -= 1
                        extra = ""
                        if type(c).__name__ == "Line":
                            pts = getattr(c, "points", ())
                            extra = "  points=%d  width=%s" % (len(pts),
                                                               getattr(c, "width", None))
                        print("  " + "    " * depth + "- %s%s"
                              % (type(c).__name__, extra))
                        dump(c, depth + 1)
                dump(g, 1)
            sys.stdout.flush()

        # ---------------- calls ----------------
        def do_calls(self):
            w = self.hourglass
            cnt = {"neck": 0, "redraw": 0, "flow_ctx": 0}
            orig_neck = w._draw_neck_grains
            orig_redraw = w.redraw

            def neck_wrap(side):
                cnt["neck"] += 1
                return orig_neck(side)

            def redraw_wrap():
                cnt["redraw"] += 1
                return orig_redraw()

            w._draw_neck_grains = neck_wrap
            w.redraw = redraw_wrap
            for ctx in (w._sand_flow_contexts or ()):
                orig_u = ctx.update_flow

                def uf(*a, _o=orig_u, **k):
                    cnt["flow_ctx"] += 1
                    return _o(*a, **k)
                ctx.update_flow = uf
            w.running = True
            w.last_tick = now[0]
            dt = 1.0 / 60.0
            steps = 60
            for _ in range(steps):
                now[0] += dt
                w.tick(dt)
            print("  === %s === (周期 %.0fs, 冻结前驱到 %.0f%%, 之后手驱 %d 帧)"
                  % ("BATCH(安卓出货配置)" if BATCH else "桌面默认配置",
                     PERIOD, FRAC * 100, steps))
            report_state(w)
            print("  驱动 %d 帧: redraw=%d  _draw_neck_grains=%d  材质路径 update_flow=%d"
                  % (steps, cnt["redraw"], cnt["neck"], cnt["flow_ctx"]))
            print("  ⇒ %s"
                  % ("材质路径生效: _draw_neck_grains **一次都没被调**"
                     if cnt["neck"] == 0 and cnt["flow_ctx"] else
                     "回退路径生效: _draw_neck_grains 每帧被调"))
            sys.stdout.flush()
            self.stop()

        # ---------------- time ----------------
        def do_time(self):
            w = self.hourglass
            target = getattr(w, "_neck_grain_group", None)
            if FAMILY == "splash":
                # **量程标定**: 一个又大又活着的族(4600+ 条指令, ~1500 个可见方块)。
                # 若连它都量不出差 ⇒ 本量具没有量程, 那么"960 条量出 0"说明不了任何事。
                target = getattr(w, "_splash_group", None)
            if FAMILY == "marker":
                target = getattr(w, "_surface_marker_group", None)
            if FAMILY == "dust":
                # 诱饵: 一个只有 ~2 条指令的惰性组。它换了位置也**不该**改变成本
                # ⇒ 拿它当"边界操作自身有没有代价"的标定。
                target = getattr(w, "_dust_group", None)
            if FAMILY == "extra":
                g = InstructionGroup()
                for _ in range(320):
                    c = Color(*w.sand_base)
                    ln = Line(points=[], width=1)
                    g.add(c)
                    g.add(ln)
                w.canvas.add(g)
                target = g
            if FAMILY == "extra3":
                # ★ 斜率法: 一次性挂 6 份"同形空池"(各 320 对空 Line, 与颈部池逐字同构),
                #   相位交替"摘掉 3 份 / 挂回 3 份" —— **两个相位做的结构操作数一样**(3 次),
                #   而相差 3×960 = 2880 条空指令。2880 条若按 ~0.4µs/条 ≈ 1.2ms,
                #   远超本机 ~0.5ms 的块间噪声 ⇒ 这条能真的分辨。
                extra_all = []
                for _ in range(6):
                    g = InstructionGroup()
                    for _ in range(320):
                        c = Color(*w.sand_base)
                        ln = Line(points=[], width=1)
                        g.add(c)
                        g.add(ln)
                    w.canvas.add(g)
                    extra_all.append(g)
                subgroup = extra_all[:3]
                positions = [w.canvas.children.index(g) for g in subgroup]
                self._extra_note = ("相位只在 3/6 份之间切换 ⇒ 每相位 3 次结构操作, "
                                    "差 2880 条空指令")

                def remove():
                    for g in subgroup:
                        w.canvas.remove(g)

                def restore():
                    for g, p in zip(subgroup, positions):
                        w.canvas.insert(p, g)

            if FAMILY == "none":
                target = None
            if FAMILY != "extra3":
                pos = w.canvas.children.index(target) if target is not None else None

                def n_copies():
                    return sum(1 for c in w.canvas.children if c is target)

                def remove():
                    # 🔴 **必须按"有几份"来摘** —— 第一版用 `canvas.remove()` 一次,
                    #    而 `insert()` 没有守卫 ⇒ 会把同一个组插第二遍 ⇒ 相位在
                    #    "2 份 / 1 份"之间切换, 差的是 **一份**, 不是"有/无"。
                    #    (`target in children` 也照样报 True, 因为还剩一份 —— 静默量错。)
                    if target is not None:
                        while n_copies():
                            w.canvas.remove(target)

                def restore():
                    if target is not None:
                        while n_copies():
                            w.canvas.remove(target)
                        w.canvas.insert(pos, target)
                        if os.environ.get("P2_DEBUG"):
                            print("   [dbg] restore -> copies=%d (n=%d)"
                                  % (n_copies(), len(w.canvas.children)))

            phases = []
            order = os.environ.get("P2_ORDER", "abba")
            n_block = int(os.environ.get("P2_BLOCKS", "2"))
            for _ in range(n_block):
                if order == "abba":
                    # on,off,off,on ⇒ 一个块内 on/off 的**时间位置对称** ⇒ 线性漂移抵消
                    phases += [("on", restore), ("off", remove),
                               ("off", remove), ("on", restore)]
                else:
                    phases += [("on", restore), ("off", remove)]
            # 先跑一个完整循环把稳态建立起来, 再正式测
            for _name, act in phases:
                act()
            self.acts = phases
            self._time_target = target
            self.idx = 0
            self.attached_log = []
            Clock.schedule_once(self.nxt, 0.5)

        def nxt(self, _dt):
            if self.idx >= len(self.acts):
                self.finish_time()
                return
            name, act = self.acts[self.idx]
            act()
            # 回读: 这一相位目标到底在不在画布上(负对照 —— 没摘掉的话读数不算数)
            w = self.hourglass
            if FAMILY not in ("none", "extra3"):
                on = sum(1 for c in w.canvas.children if c is self._time_target)
                self.box.setdefault("attach", []).append(on)
            self.attached_log.append(self.idx + 1)
            self.box["phase"] = self.idx + 1
            Clock.schedule_once(self.advance, SPAN)

        def advance(self, _dt):
            self.box["phase"] = 0
            self.idx += 1
            Clock.schedule_once(self.nxt, 0.05)

        def finish_time(self):
            res = box["res"]
            cpu = box["cpu"]
            # 每个相位丢掉头 30 帧 —— 结构改动那一帧在**重编译整块画布**,
            # 它与稳态 apply 成本无关, 混进来会把"摘掉"的量成很贵。
            SKIP = 30

            def stats(v):
                v = sorted(v[SKIP:])
                if not v:
                    return float("nan"), float("nan"), 0
                return (sum(v) / len(v), v[len(v) // 2], len(v))

            print("  === %s === family=%s span=%.1fs ×%d 相位(丢头 %d 帧)"
                  % ("BATCH(安卓出货配置)" if BATCH else "桌面默认配置",
                     FAMILY, SPAN, len(res), SKIP))
            if box.get("attach") is not None:
                print("  各相位开始时的**份数**(1=在画布上, 0=已摘掉): %s"
                      % box["attach"])
            if getattr(self, "_extra_note", None):
                print("  %s" % self._extra_note)
            report_state(self.hourglass)
            ons, offs = [], []
            for i in sorted(res):
                m, med, n = stats(res[i])
                cm, cmed, _ = stats(cpu[i])
                on = (self.acts[i - 1][0] == "on")
                tag = "挂上" if on else "摘掉"
                print("  相位%d %s wall mean=%7.3f  cpu mean=%6.3f ms (n=%d)"
                      % (i, tag, m, cm, n))
                (ons if on else offs).append((m, cm))
            if ons and offs:
                nb = len(self.acts) // 4
                for b in range(nb):
                    idx = [i for i in range(b * 4, b * 4 + 4) if (i + 1) in res]
                    on_v = [(res[i + 1], cpu[i + 1]) for i in idx
                            if self.acts[i][0] == "on"]
                    off_v = [(res[i + 1], cpu[i + 1]) for i in idx
                             if self.acts[i][0] == "off"]
                    if len(on_v) != 2 or len(off_v) != 2:
                        continue
                    lv = [stats(v[0])[0] for v in on_v + off_v]
                    stable = (max(lv) - min(lv)) <= 1.0
                    dw = (sum(stats(v[0])[0] for v in on_v) / len(on_v)
                          - sum(stats(v[0])[0] for v in off_v) / len(off_v))
                    dc = (sum(stats(v[1])[0] for v in on_v) / len(on_v)
                          - sum(stats(v[1])[0] for v in off_v) / len(off_v))
                    print("  块%d 配对差: wall %+.3f ms   CPU %+.3f ms   [%s]"
                          % (b + 1, dw, dc,
                             "四相位同模式, 作数" if stable
                             else "四相位跨模式(%.1f..%.1f), **作废**"
                                  % (min(lv), max(lv))))
            sys.stdout.flush()
            self.stop()
            sys.stdout.flush()
            self.stop()

        # ---------------- pix ----------------
        def do_pix(self):
            w = self.hourglass
            self.shots = {}
            # 🔴 **2026-10-09: 取两张**未切**的图(A0/A), 用来**先证明状态是冻住的**。**
            #    原来只有 A/B 两张、相隔 0.25s, 而零对照是**间歇性脏**的:
            #    同一句 `P2_CUT=none`(必须 0 差)在对抗审查里出过 7~20px/max 8~90,
            #    复跑又全是 0 —— 两位专家谁都没说谎, 是这把尺子不稳。
            #    这里只按 `elapsed` 冻结是不够的: 场景里还有按**真实时间**走的元素
            #    (flares/dust 的寿命走 `time.perf_counter()`), A/B 之间它们会自己变。
            #    ⇒ 结构化的"先标定再判": A0 与 A 不一致就直接宣布本次作废。
            self.steps = ["A0", "A", "B"]
            w.running = True      # 免得上一次 redraw 画上"暂停遮罩"(会盖住整幅)
            w.redraw()
            Clock.schedule_once(self.next_shot, 0.25)

        def grab(self, name):
            width, height = map(int, Window.size)
            px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
            img = np.frombuffer(px, dtype=np.uint8).reshape(height, width, 4)
            img = img[::-1, :, :3].astype(int)
            return img

        def next_shot(self, _dt):
            w = self.hourglass
            if self.steps:
                step = self.steps.pop(0)
                self.shots[step] = self.grab(step)
                if step == "A":
                    self.cut()
                Clock.schedule_once(self.next_shot, 0.25)
                return
            self.finish_pix()

        def cut(self):
            w = self.hourglass
            if CUT == "neck":
                g = getattr(w, "_neck_grain_group", None)
                if g is not None:
                    cnt = 0
                    while any(c is g for c in w.canvas.children):
                        w.canvas.remove(g)
                        cnt += 1
                    print("  摘掉 _neck_grain_group ×%d" % cnt)
                else:
                    print("  !! 没有 _neck_grain_group")
            elif CUT == "markers":
                g = getattr(w, "_surface_marker_group", None)
                if g is not None:
                    cnt = 0
                    while any(c is g for c in w.canvas.children):
                        w.canvas.remove(g)
                        cnt += 1
                    print("  摘掉 _surface_marker_group ×%d" % cnt)
                else:
                    print("  !! 没有 _surface_marker_group")
            elif CUT == "neck_slots":
                ctx = getattr(w, "_neck_context", None)
                if ctx is None:
                    print("  !! 没有 _neck_context")
                else:
                    slots = [c for c in list(getattr(ctx, "children", ()))
                             if type(c).__name__ == "InstructionGroup"]
                    print("  摘掉 %d 个色调槽(从 _neck_context 里)" % len(slots))
                    for s in slots:
                        ctx.remove(s)
            elif CUT == "pause":
                w._pause_rect.size = w.size
                w._pause_color.a = 0.55
            elif CUT == "none":
                pass

        def finish_pix(self):
            w = self.hourglass
            frozen = np.abs(self.shots["A0"] - self.shots["A"]).max()
            a, b = self.shots["A"], self.shots["B"]
            d = np.abs(a - b).max(axis=2)
            changed = int((d > 0).sum())
            print("  === %s === CUT=%s" % (
                "BATCH(安卓出货配置)" if BATCH else "桌面默认配置", CUT))
            report_state(w)
            # 🔴 **2026-10-09: 把"标定不过的读数不看"从文档变成执行。**
            #    背景: 同一句 `P2_MODE=pix P2_CUT=none`(必须 0 差)在对抗审查里出现过
            #    **两个对立的读数** —— 一位报 7~20px/max 8~90(他自己注明是"工作树",
            #    多半是改到一半的树), 两位复跑 + 我自己三种配置复跑**全是 0**。
            #    分歧的根不是量法, 是**读数没带配置、也没人拦**。
            #    ⇒ 现在: ① 每次报数都带上**全部**环境开关(可归因);
            #      ② `P2_CUT=none` 这一臂**自己判红**(非 0 即说明量法此刻不可信,
            #        后面任何读数都不许拿去下结论)。
            print("  配置: P2_CUT=%s P2_BATCH=%d P2_FLAT=%d P2_NOFLOW=%s "
                  "P2_PERIOD=%s P2_FRAC=%s P2_SPAN=%s"
                  % (CUT, int(BATCH), int(FLAT), os.environ.get("P2_NOFLOW", "0"),
                     os.environ.get("P2_PERIOD", "-"), os.environ.get("P2_FRAC", "-"),
                     os.environ.get("P2_SPAN", "-")))
            print("  逐像素: 变化 %d px (%.3f%%), 最大通道差 %d"
                  % (changed, 100.0 * changed / d.size, int(d.max())))
            if CUT == "none":
                ok = changed == 0
                print("  **零对照(none): %s** —— %s"
                      % ("通过" if ok else "!! 不通过",
                         "量法此刻可信" if ok else
                         "同一个状态截两次都不一样 ⇒ 本次全部读数作废, 先查树/配置"))
            elif CUT == "pause":
                # 🔴 **2026-10-09: 这条负对照自己也废了(实测 0 px)。**
                #    它本该"必须大差(证明截图通路是活的)", 但 `do_pix` 先设
                #    `running=True`, 而 `redraw()` 里遮罩 alpha 是
                #    `0.55 if not running and 0 < elapsed < duration else 0`
                #    ⇒ 这一刀**被下一次 redraw 抹掉**, 屏幕上什么都没变。
                #    它此前偶尔报出的 16px 是**动画抖动**, 不是遮罩 ——
                #    典型的**非判别性证据**(换个假设也得到同一个读数)。
                #    ⇒ 不再假装它通过: 不达"大差"就明说它不判别。
                print("  (负对照 pause: %s)"
                      % ("通路是活的(大差 %d px)" % changed if changed > 2000 else
                         "**!! 没产生遮罩(只有 %d px) ⇒ 这条对照不判别, 别拿它当证据**"
                         % changed))
            # 🔴 **状态冻没冻住** —— 这一条比上面那条更根本: A0/A 都不切任何东西,
            #    它们有差就说明场景里有按真实时间走的元素, 那么 A/B 的差里就**混着
            #    它自己的变化**, 不能归给"摘掉的那一层"。
            print("  **状态冻结: %s**(A0 vs A, 相隔 0.25s, 最大通道差 %d)"
                  % ("是" if frozen == 0 else "**否** ⇒ 本次读数不可归因",
                     int(frozen)))
            ys, xs = np.nonzero(d > 0)
            if len(ys):
                print("  变化 bbox: y %d..%d  x %d..%d"
                      % (ys.min(), ys.max(), xs.min(), xs.max()))
            sys.stdout.flush()
            self.stop()

    P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
