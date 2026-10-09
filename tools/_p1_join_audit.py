# -*- coding: utf-8 -*-
"""**1号 审查用**: 颈部沙柱"下沿跟沙走"(NECK_JOIN)的机械审计 —— 只量, 不判好坏。

查四件事:
  A. `max(get_mound_top_y(), _lower_sand_top)` 里**哪一项真正胜出**(逐帧计数)。
  B. 延伸段长度 `outlet - _lower_sand_top` 随周期/几何怎么变(它是常数还是跟沙走)。
  C. `side` 的良构性: 单调降 / 长度 vs `_neck_quads` 容量 / `connected` / 暂停归零转屏。
  D. 恒宽柱两条边与穹顶的**悬空量**(`contact(0) - contact(±t_in)`), 以及柱子下沿到堆面的缝。

跑法:
    python tools/_p1_join_audit.py            # 平板口径 1904x2890
    python tools/_p1_join_audit.py desk       # 桌面口径 400x800
    HG_SAND_MATERIAL=flat HG_FLOW_RENDERER=line python tools/_p1_join_audit.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
WHICH = sys.argv[1] if len(sys.argv) > 1 else "tab"
if WHICH == "desk":
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.0")
    WIN = (400, 800)
else:
    os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
    WIN = (1904, 2890)
os.environ["HG_FLOW_RENDERER"] = os.environ.get("HG_FLOW_RENDERER", "texture")

BAD = []
NOTE = []
BOTTOMS = []


def note(msg):
    NOTE.append(msg)
    print("  !! " + msg)


with tempfile.TemporaryDirectory(prefix="p1-join-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    print("=" * 100)
    print("NECK_JOIN=%s  SAND_MATERIAL=%s  FLOW_RENDERER=%s  窗口=%s  density=%s"
          % (m.NECK_JOIN, os.environ.get("HG_SAND_MATERIAL", "(default)"),
             os.environ.get("HG_FLOW_RENDERER"), WIN, os.environ.get("KIVY_METRICS_DENSITY")))

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = WIN
            Clock.schedule_once(self.go, 0.6)

        # ---------- 工具 ----------
        def geom(self, w):
            tp = w._taper
            outlet = 2 * w._neck_y - tp["y_bot"]
            return {"inlet": tp["y_bot"], "outlet": outlet, "t_in": tp["t_in"],
                    "nw": tp["t_out"], "lp": w._lower_sand_top,
                    "lb": w._lower_sand_bot, "ext": outlet - w._lower_sand_top}

        def census(self, tag, w, g):
            side = w._neck_sand_side()
            gt = w.get_mound_top_y()
            # A: max() 里谁赢
            win_mound = gt > g["lp"] + 1e-9
            if win_mound:
                BAD.append("%s: get_mound_top_y(%.2f) > _lower_sand_top(%.2f) —— max() 取了沙堆项"
                           % (tag, gt, g["lp"]))
            # C: 单调 / 容量 / connected
            mono = all(side[i][1] >= side[i + 1][1] - 1e-9 for i in range(len(side) - 1))
            if not mono:
                BAD.append("%s: side 的 y 不单调" % tag)
            cap = len(w._neck_quads)
            if side and len(side) - 1 > cap:
                BAD.append("%s: len(side)-1=%d > 容量 %d(尾段会被静默丢掉)"
                           % (tag, len(side) - 1, cap))
            connected = bool(side and side[-1][1] <= g["outlet"] + 1e-6)
            # D: 恒宽柱两条边与穹顶悬空
            prof = w._mound_profile
            apex = w._mound_apex()
            hang_center = hang_edge = float("nan")
            c0 = ct = float("nan")
            if side and prof is not None:
                c0 = prof.contact(0.0, apex)
                ct = prof.contact(g["t_in"], apex)
                hang_center = side[-1][1] - (g["lb"] + c0)      # 柱底 vs 中轴堆面
                hang_edge = side[-1][1] - (g["lb"] + ct)        # 柱底 vs ±t_in 处堆面
            rect = w._neck_solid_rect.size
            if side:
                BOTTOMS.append(side[-1][1])
            return {"t": w.elapsed, "gt": gt, "lp": g["lp"], "join": max(gt, g["lp"]),
                    "bottom": side[-1][1] if side else float("nan"), "n": len(side),
                    "cap": cap, "conn": connected, "mono": mono,
                    "apex": apex, "hang_c": hang_center, "hang_e": hang_edge,
                    "surf0": g["lb"] + c0, "surft": g["lb"] + ct,
                    "solid": (round(rect[0], 1), round(rect[1], 1)),
                    "sa": round(w._neck_solid_color.a, 3),
                    "fa": round(w._neck_fade_color.a, 3)}

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass

            # ---------- A/B: 纯几何表(不用跑时间) ----------
            print("\n-- A/B 纯几何: 延伸段长度 = outlet - _lower_sand_top --")
            print("  %9s %7s %7s %10s %10s %9s %9s %9s"
                  % ("周期(s)", "t_out", "t_in", "inlet", "outlet", "球内顶", "延伸长", "ext/t_in"))
            for P in (1.0, 5.0, 15.0, 50.0, 600.0, 3600.0, 360000.0):
                w.set_duration(P); w.reset(); w._rebuild_height_table()
                g = self.geom(w)
                print("  %9.0f %7.2f %7.2f %10.2f %10.2f %9.2f %9.2f %9.2f"
                      % (P, g["nw"], g["t_in"], g["inlet"], g["outlet"], g["lp"], g["ext"],
                         g["ext"] / max(1e-9, g["t_in"])))

            # ---------- C/D: 时间扫描(短周期真实步进) ----------
            print("\n-- C/D 时间扫描(真实 60fps 步进) --")
            print("  %6s %7s %8s %8s %8s %8s %8s %4s %4s %5s %9s %9s"
                  % ("周期", "t", "沙堆顶", "柱底", "堆面中轴", "堆面±t_in", "堆-柱缝",
                     "n", "cap", "conn", "柱悬/中轴", "柱悬/边"))
            for P in (1.0, 5.0, 15.0, 50.0):
                w.set_duration(P); w.reset(); w._rebuild_height_table()
                for _ in range(400):
                    _b = len(getattr(w, "_warm_queue", ()))
                    w._warm_batches_step()
                    if len(getattr(w, "_warm_queue", ())) >= _b:
                        break
                w.toggle()
                g = self.geom(w)
                k = 0
                seen_gt_wins = 0
                while k < 96:
                    k += 1
                    t = P * k / 96.0
                    guard = 0
                    while w.elapsed < t - 1e-9 and w.running and guard < 100000:
                        guard += 1
                        now[0] += 1.0 / 60.0
                        w.tick(1.0 / 60.0)
                    if not w.running:
                        break
                    r = self.census("P=%.0f t=%.2f" % (P, t), w, g)
                    if r["gt"] > g["lp"] + 1e-9:
                        seen_gt_wins += 1
                    if k in (1, 8, 24, 48, 72, 84, 90, 93, 95, 96):
                        print("  %6.0f %7.2f %8.2f %8.2f %8.2f %8.2f %8.2f %4d %4d %5s %9.2f %9.2f"
                              % (P, r["t"], r["gt"], r["bottom"], r["surf0"], r["surft"],
                                 g["outlet"] - r["bottom"], r["n"], r["cap"], r["conn"],
                                 r["hang_c"], r["hang_e"]))
                print("     P=%.0f: 全程 get_mound_top_y 胜出 max() 的帧数 = %d / %d; "
                      "柱底取值范围 = [%.2f, %.2f] (球内顶 %.2f)"
                      % (P, seen_gt_wins, k, BOTTOMS[0] if BOTTOMS else float("nan"),
                         BOTTOMS[1] if BOTTOMS else float("nan"), g["lp"]))
                BOTTOMS.clear()

            # ---------- 暂停 / 转屏 / 归零 / 完成 ----------
            print("\n-- 状态切换 --")
            P = 50.0
            w.set_duration(P); w.reset(); w._rebuild_height_table()
            w.toggle()
            while w.elapsed < P * 0.9 and w.running:
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            g = self.geom(w)
            r1 = self.census("pause-1", w, g)
            w.running = False
            w.redraw(); r2 = self.census("pause-2", w, g)
            print("  暂停: 下沿 %s vs %s (应相同); 尺寸 %s / %s"
                  % (r1["bottom"], r2["bottom"], r1["solid"], r2["solid"]))
            if abs(r1["bottom"] - r2["bottom"]) > 1e-9:
                BAD.append("暂停改变了 side 下沿")
            # 转屏: 直接改 widget 尺寸 + 重建几何(不 reset)
            w.size = (w.width * 0.55, w.height * 1.4)
            w._rebuild_height_table()
            g2 = self.geom(w)
            r3 = self.census("rotate", w, g2)
            print("  转屏: 新几何 t_in=%.2f outlet=%.2f 球内顶=%.2f 延伸=%.2f -> 下沿=%.2f n=%d cap=%d"
                  % (g2["t_in"], g2["outlet"], g2["lp"], g2["ext"], r3["bottom"], r3["n"], r3["cap"]))
            # 归零
            w.reset()
            s = w._neck_sand_side()
            print("  reset(): side = %r (应为 [])" % (s,))
            if s:
                BAD.append("reset 后 side 非空")
            # 完成态
            w._rebuild_height_table()
            w.toggle()
            while w.elapsed < P - 1e-6 and w.running:
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            g = self.geom(w)
            r = self.census("done", w, g)
            print("  完成: elapsed=%.2f side n=%d 下沿=%s 实心矩形=%s alpha=%s"
                  % (w.elapsed, r["n"], r["bottom"], r["solid"], r["sa"]))

            print("\n== BAD ==")
            print("  (空)" if not BAD else "\n".join("  " + b for b in BAD))
            self.stop()

    Probe().run()
