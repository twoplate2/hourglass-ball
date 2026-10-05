# -*- coding: utf-8 -*-
"""二分定位 **1.107 那三行调参**里到底是哪一个把闸门打红的。

背景(1.108 回退时记的):
    `neck texture stays inside the straight conduit` —— 1.106 PASS / 1.107 FAIL×2 /
    1.108(回退) PASS。**机制未查明**, 只知道是这三行之一:
        UPPER_ROUGH_PERIOD  8.0 → 48.0
        UPPER_ROUGH_FRAMES  64  → 128
        UPPER_ROUGH_HARMONICS (1,2,3) → (5,8,13)

为什么不用跑四遍闸门: 那条断言只用到 **一个场景**(2 颗注入粒子 + 一次 redraw),
这里把同一场景原样复刻出来, 直接打印 `outlet / y_bot / side / points`,
拿到的是**数**而不是 PASS/FAIL —— 归因靠数, 不靠二分出来的一个红叉。

⚠️ `_build_rough_frames` 的 `frames`/`harmonics` 是**默认参数**(def 时就绑定),
   所以必须改 `__defaults__`, 改模块常量没用。

跑法: python tools/_bisect_rough.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (标签, PERIOD, FRAMES, HARMONICS)
CONFIGS = [
    ("1.106 基线      ", 8.0, 64, (1, 2, 3)),
    ("只改 PERIOD→48  ", 48.0, 64, (1, 2, 3)),
    ("只改 FRAMES→128 ", 8.0, 128, (1, 2, 3)),
    ("只改 HARM(5,8,13)", 8.0, 64, (5, 8, 13)),
    ("1.107 全改      ", 48.0, 128, (5, 8, 13)),
]
# 第二步: 这条断言是不是**尺度的函数**? 扫一遍窗口尺寸看余量 `y_bot - points[1]`。
# 闸门默认窗口 = 400×800; 设备上则是手机分辨率 ⇒ 若余量随尺度翻正负,
# 它守的就**不是**"纹理在不在管内"这个不变量, 而是某个尺度的巧合。
SIZES = [(400, 800), (360, 640), (412, 915), (480, 800), (800, 480),
         (1096, 2214), (320, 560), (760, 1460)]
PIX = (1096, 2214)


def main():
    with tempfile.TemporaryDirectory(prefix="bisect-rough-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 15}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                # ⚠️ **不要改窗口尺寸** —— 第一版照着别的探针把窗口设成手机像素(1096×2214),
                #    结果**连基线都 FAIL**。闸门跑的是默认窗口 + `apply_orientation`+布局链,
                #    而这条断言是尺度的函数(top_y 相对 y_bot 的位置), 尺度一换就翻面。
                #    复刻场景必须连**尺度与布局**一起复刻, 只复刻那几行代码是不够的。
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                self.hourglass._rebuild_height_table()
                print("尺度: Window=%s widget=%s" % (tuple(Window.size),
                                                   tuple(self.hourglass.size)))
                w = self.hourglass
                rows = []
                for label, period, frames, harmonics in CONFIGS:
                    m.UPPER_ROUGH_PERIOD = period
                    # frames/harmonics 是**默认参数**, 必须在 __defaults__ 上改
                    m._build_rough_frames.__defaults__ = (frames, harmonics)
                    w.size = w.size              # 触发几何重算
                    w._rebuild_height_table()
                    w._upper_rough_cache_t = None
                    w._mound_frame_k = None
                    # 自证: 这次重建**真的**用上了新参数吗?
                    # 若下面这列在配置间不变, 就是没重建 —— 那"没差别"的结论无效。
                    fr = getattr(w, "_upper_rough_frames", None)
                    rows.append((label, self.probe(w, period, frames, harmonics), {
                        "nframes": len(fr) if fr else 0,
                        "node0": fr[0][0] if fr else None,
                        "node20": fr[0][20] if fr else None,
                    }))
                # ---- 第二步: 这条断言在别的尺度上还成立吗? ----
                m.UPPER_ROUGH_PERIOD = 8.0
                m._build_rough_frames.__defaults__ = (64, (1, 2, 3))
                sizes = []
                base = w.size
                for sw, sh in SIZES:
                    w.size = (sw, sh)
                    w._rebuild_height_table()
                    r = self.probe(w, 8.0, 64, (1, 2, 3))
                    p1 = r["pool1"]
                    sizes.append((sw, sh, r["outlet"], r["y_bot"], r["side"][0][1],
                                  p1[1] if p1 else None, r["count"],
                                  r["new_bad"], r["neg_bad"]))
                w.size = base
                w._rebuild_height_table()
                report(rows)
                report_sizes(sizes)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

            @staticmethod
            def probe(w, period, frames, harmonics):
                w.set_duration(15)
                w.elapsed = 0.6
                w.running = True
                outlet = 2 * w._neck_y - w._taper["y_bot"]
                length = w._taper["y_bot"] - outlet
                w.particles = [{
                    "x": w._cx, "x_offset": 0, "y": outlet - length,
                    "vy": -50, "size": 2, "is_light": True, "trail_time": 0.02,
                }, {
                    "x": w._cx, "x_offset": 0, "y": outlet - length * 0.5,
                    "vy": -50, "size": 2, "is_light": True, "trail_time": 0.02,
                }]
                w.redraw()
                side = w._neck_sand_side()
                pts = []
                for i in range(min(3, len(w._neck_grain_pool))):
                    _c, ln = w._neck_grain_pool[i]
                    pts.append(tuple(ln.points) if ln.points else ())
                # ---- 新判据 + 它的负对照(标定: 已知对 / 已知错 两端都要验) ----
                lo_y, hi_y = side[-1][1], side[0][1]

                def _outside(pool, count):
                    bad = []
                    for _c, _ln in pool[:count]:
                        if not _ln.points:
                            continue
                        _half = _ln.width * 0.5
                        if (_ln.points[1] < lo_y - _half - 1e-6
                                or _ln.points[3] > hi_y + _half + 1e-6):
                            bad.append((round(_ln.points[1], 2), round(_ln.points[3], 2)))
                    return bad

                new_bad = _outside(w._neck_grain_pool, w._neck_grain_count)
                _c0, _ln0 = w._neck_grain_pool[0]
                _saved = tuple(_ln0.points)
                _ln0.points = [_saved[0], hi_y + 5.0, _saved[2], hi_y + 7.0]
                neg_bad = _outside(w._neck_grain_pool, w._neck_grain_count)
                _ln0.points = _saved
                return {
                    "new_bad": new_bad, "neg_bad": neg_bad,
                    "period": period, "frames": frames, "harmonics": harmonics,
                    "outlet": outlet, "y_bot": w._taper["y_bot"],
                    "count": w._neck_grain_count,
                    "side": (side[0] if side else None, side[-1] if side else None),
                    "n_side": len(side) if side else 0,
                    "pts": pts,
                    "pool1": tuple(w._neck_grain_pool[1][1].points)
                    if len(w._neck_grain_pool) > 1
                    and w._neck_grain_pool[1][1].points else (),
                }

        P().run()
    return 0


def report_sizes(sizes):
    print()
    print("=== 第二步: 同一份代码, 换尺度 —— 旧断言 vs 新断言 ===")
    print("旧: `outlet < points[1] < y_bot`   (余量 = y_bot - points[1])")
    print("新: 每颗已画颗粒都落在**沙柱** [side[-1].y, side[0].y] 内(含笔画半宽)")
    print("负对照: 把第 0 颗挪到沙柱上方 5px —— 新判据**必须翻红**, 否则它不判别\n")
    head = ("%-14s %9s %9s %9s %8s %8s  %-8s %-10s %s"
            % ("widget 尺寸", "outlet", "y_bot", "points[1]", "余量", "颗数",
               "旧判据", "新判据", "负对照"))
    print(head)
    print("-" * len(head))
    old_flips = new_flips = neg_miss = 0
    for sw, sh, outlet, y_bot, s0y, p1, count, new_bad, neg_bad in sizes:
        if p1 is None:
            print("%-14s  (没有画出颗粒, count=%d)" % ("%dx%d" % (sw, sh), count))
            continue
        old_ok = outlet < p1 < y_bot
        new_ok = not new_bad
        neg_ok = bool(neg_bad)          # 负对照**必须**报出越界
        if not old_ok:
            old_flips += 1
        if not new_ok:
            new_flips += 1
        if not neg_ok:
            neg_miss += 1
        print("%-14s %9.2f %9.2f %9.2f %8.2f %8d  %-8s %-10s %s"
              % ("%dx%d" % (sw, sh), outlet, y_bot, p1, y_bot - p1, count,
                 "PASS" if old_ok else "**FAIL**",
                 "PASS" if new_ok else "**FAIL**",
                 "抓到" if neg_ok else "**漏了**"))
    print()
    print("旧判据: %d/%d 个尺度 FAIL   ⇒ 它是尺度的函数, 不是不变量" % (old_flips, len(sizes)))
    print("新判据: %d/%d 个尺度 FAIL   ⇒ 应当为 0" % (new_flips, len(sizes)))
    print("负对照: %d/%d 个尺度漏检     ⇒ 应当为 0" % (neg_miss, len(sizes)))


def report(rows):
    print("\n=== 二分: 1.107 三行调参 vs `neck texture stays inside the straight conduit` ===")
    print("断言原文: outlet < _neck_grain_pool[1].points[1] < y_bot\n")
    print("--- 先自证: 每次重建是不是**真的**换了参数? ---")
    for label, _r, meta in rows:
        print("  %-18s frames=%d  fr[0][0]=%s  fr[0][20]=%s"
              % (label, meta["nframes"],
                 ("%.4f" % meta["node0"]) if meta["node0"] is not None else "-",
                 ("%.4f" % meta["node20"]) if meta["node20"] is not None else "-"))
    print()
    head = ("%-18s %8s %8s %8s %7s  %-28s %s"
            % ("配置", "outlet", "y_bot", "side[0]y", "count", "pool[1].points", "判定"))
    print(head)
    print("-" * len(head))
    for label, r, _m in rows:
        p1 = r["pool1"]
        ok = bool(p1) and r["outlet"] < p1[1] < r["y_bot"]
        print("%-18s %8.2f %8.2f %8s %7d  %-28s %s"
              % (label, r["outlet"], r["y_bot"],
                 ("%.2f" % r["side"][0][1]) if r["side"] else "-",
                 r["count"], str(tuple(round(v, 2) for v in p1)), "PASS" if ok else "**FAIL**"))
    print()
    print("=== 各配置的颈部沙柱端点(看 top_y/span 有没有被 rough 改动) ===")
    for label, r, _m in rows:
        s0, s1 = r["side"]
        span = (s0[1] - s1[1]) if (s0 and s1) else 0.0
        print("  %-18s side[0]=(%.2f, %.2f)  side[-1]=(%.2f, %.2f)  span=%.2f  n=%d"
              % (label, s0[0], s0[1], s1[0], s1[1], span, r["n_side"]))
    print()
    print("NOTE: 判定列只复刻了断言里那一条。")
    print("      若 frames 列在配置间变了、而 side 端点**完全没变**, 说明")
    print("      rough 根本没进入颈部几何 —— 那 1.107 的红叉就不在这条链上。")


if __name__ == "__main__":
    sys.exit(main())
