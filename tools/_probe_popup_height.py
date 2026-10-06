# -*- coding: utf-8 -*-
"""F2: 弹窗在**反旋转横屏**下有没有被裁 / 够不够得着。

⚠️ 本探针 v1 有个致命 bug, 记录在此以免重犯(2026-10-06):
   `HourglassApp.build()` 里写死 `if "--landscape" in sys.argv: Window.size = (1000, 600)`
   —— 探针在 `run()` **之前**设的 `Window.size = win` **会被 build() 覆盖**。
   于是 case 表里的 `land_1080x1080` 实际跑在 **1000x600** 上, 而且**没人发现**:
   探针从没断言"拿到的尺寸 == 要的尺寸"。
   ⇒ 现在每次跑都打印 requested / actual 并在不等时 **大声报 MISMATCH**。

跑法(一进程一例, Kivy 单例):
  python tools/_probe_popup_height.py <case>
  case ∈ portrait | land | square | dev2400x1080 | dev1920x1080
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPEN = os.environ.get("HG_OPEN", "duration")   # duration | dev
OUT = ROOT / "_shot" / "popupfit"


def run_case(tag, win, landscape, dens):
    tag = "%s__%s" % (tag, OPEN)
    sys.path.insert(0, str(ROOT))
    os.environ["KIVY_METRICS_DENSITY"] = str(dens)
    from kivy.clock import Clock
    from kivy.core.window import Window
    if landscape and "--landscape" not in sys.argv:
        sys.argv.append("--landscape")
    import main as m
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import numpy as np
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    # ⚠️ 这个桩是给别的用例"防完成弹窗遮住裁图"用的; 测完成弹窗自己时必须去掉,
    #    否则**把被测对象关掉了**(2026-10-06 踩过: 量不到完成弹窗, 查了半天)
    if OPEN != "completion":
        m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None
    now = [1000.0]
    m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

    res = {"want": win}

    def shot():
        ww, hh = map(int, Window.size)
        px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
        return np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
            Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))

    class P(m.HourglassApp):
        def on_start(self):
            # ⚠️ 必须在这里补设 —— build() 会把 --landscape 的窗口写死成 1000x600
            try:
                Window.size = win
            except Exception:
                pass
            Clock.schedule_once(self.begin, 1.2)

        def begin(self, _dt):
            res["got"] = tuple(int(v) for v in Window.size)
            res["angle"] = self.root.angle
            self.root.apply_orientation()
            self.root.do_layout()
            if self.root._anchor is not None:
                self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            if os.environ.get("HG_RUNNING") == "1":
                self.hourglass.running = True
            if OPEN == "dev":
                self._open_dev_menu()
            elif OPEN == "sound":
                self.on_sound_picker()
            elif OPEN == "completion":
                self.on_completed(95)
            else:
                self.on_duration_picker(None)
            # 完成弹窗有 COMPLETION_POPUP_DELAY=1.0s 的延迟弹出, 抓图要等过它
            Clock.schedule_once(self.take, 2.2 if OPEN == 'completion' else 0.8)

        def take(self, _dt):
            img = shot()
            OUT.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img).save(OUT / ("%s.png" % tag))
            hh, ww, _ = img.shape
            a = img.astype(int)
            # 弹窗底色 POPUP_BG #faf5eb —— 直接量**弹窗自己**的包围盒最直白
            bg = (np.abs(a - np.array([250, 245, 235])).max(axis=2) < 8)
            ys, xs = np.nonzero(bg)
            res["n_bg"] = int(len(ys))
            res["bg"] = (int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())) if len(ys) else None
            # 深灰标题栏 = 弹窗默认背景色 (Kivy Popup 默认 ~ (0.2,0.2,0.2))
            dark = (a.max(axis=2) < 90)
            ys2, xs2 = np.nonzero(dark)
            res["n_dark"] = int(len(ys2))
            res["dark"] = (int(ys2.min()), int(ys2.max()), int(xs2.min()), int(xs2.max())) if len(ys2) else None
            # 确定 = 暖金 POPUP_GOLD_SEL #caa450
            gold = (np.abs(a - np.array([202, 164, 80])).max(axis=2) < 26)
            ys3, xs3 = np.nonzero(gold)
            res["n_gold"] = int(len(ys3))
            res["gold"] = (int(ys3.min()), int(ys3.max()), int(xs3.min()), int(xs3.max())) if len(ys3) else None
            res["win"] = (ww, hh)
            # ★ 最直白的判据: 弹窗与等效盒**同轴心**(都以窗口中心为中心),
            #   所以"装得下" ⟺ 分量都 ≤。不需要任何颜色/像素推断。
            anc = getattr(self.root, "_anchor", None)
            pop = None
            def _find(w, name):
                if type(w).__name__ == name:
                    return w
                for c in w.children:
                    r = _find(c, name)
                    if r is not None:
                        return r
                return None
            pop = _find(Window, "_SandBgPopup") or _find(Window, "Popup")
            if anc is None:
                anc = _find(Window, "AnchorLayout")
            res["rootcls"] = type(self.root).__name__
            if anc is not None and pop is not None:
                res["box"] = tuple(int(v) for v in anc.size)
                res["pop"] = tuple(int(v) for v in pop.size)
                from kivy.metrics import dp as _dp
                res["dens"] = float(_dp(1))
                res["ovf"] = (max(0, res["pop"][0] - res["box"][0]),
                              max(0, res["pop"][1] - res["box"][1]))
            # widget 树几何转储: 直接问 Kivy "每个盒子多大", 比按颜色猜硬得多
            lines = []
            def walk(w, d=0):
                if d > 6:
                    return
                if hasattr(w, "size"):
                    mh = getattr(w, "minimum_height", None)
                    lines.append("%s%-16s size=%-18s pos=%-18s sh=%s%s" % (
                        "  " * d, type(w).__name__,
                        "%dx%d" % (int(w.size[0]), int(w.size[1])),
                        "%d,%d" % (int(w.pos[0]), int(w.pos[1])),
                        tuple(w.size_hint) if getattr(w, "size_hint", None) else None,
                        ("  min_h=%.0f" % mh) if mh is not None else ""))
                for c in w.children:
                    walk(c, d + 1)
            for c in Window.children:
                walk(c)
            res["tree"] = lines
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return res


def fmt(e, W, H):
    if e is None:
        return "**找不到**"
    y0, y1, x0, x1 = e
    ok = (y0 > 0 and y1 < H - 1 and x0 > 0 and x1 < W - 1)
    return "y %4d~%4d  x %4d~%4d   %s" % (y0, y1, x0, x1, "在屏内" if ok else "**贴边/出屏**")


CASES = {
    "portrait":      ("portrait_1080x2400", (1080, 2400), False, 1.75),
    "land":          ("land_1000x600",     (1000, 600),  True,  2.0),
    "square":        ("land_1080x1080",    (1080, 1080), True,  2.0),
    "dev2400x1080":  ("land_2400x1080",    (2400, 1080), True,  1.75),  # 设备A 横屏(dens280)
    "dev1920x1080":  ("land_1920x1080",    (1920, 1080), True,  3.0),   # 设备B 横屏(dens480)
    # r29-2号 的三点标定梯子(1080x1080 方窗, 他说 @400 完整 / @440 标题被切 / @480 整条不见):
    "sq480p":        ("portrait_1080x1080_480", (1080, 1080), False, 3.0),
    "sq400":         ("sq_1080x1080_400",  (1080, 1080), True,  400 / 160.0),
    "sq440":         ("sq_1080x1080_440",  (1080, 1080), True,  440 / 160.0),
    "sq480":         ("sq_1080x1080_480",  (1080, 1080), True,  480 / 160.0),
    # 他给的正反对照臂
    "sq1200x440":    ("sq_1080x1200_440",  (1080, 1200), True,  440 / 160.0),  # 已知对
    "sq1150x440":    ("sq_1080x1150_440",  (1080, 1150), True,  440 / 160.0),  # 已知错
}

key = sys.argv[1] if len(sys.argv) > 1 else "portrait"
tag, win, land, dens = CASES[key]
r = run_case(tag, win, land, dens)
W, H = r.get("win", (0, 0))
print("")
print("  === %s  [%s] ===" % (tag, OPEN))
print("  请求窗口 %s   实得 %s   %s"
      % (r["want"], r.get("got"),
         "OK" if tuple(r["want"]) == tuple(r.get("got") or ()) else "**MISMATCH —— 上表结论无效**"))
print("  渲染尺寸 %dx%d   旋转角 %s" % (W, H, r.get("angle")))
print("  弹窗底(暖白) %-46s  像素 %d" % (fmt(r.get("bg"), W, H), r.get("n_bg", 0)))
print("  深灰(标题栏) %-46s  像素 %d" % (fmt(r.get("dark"), W, H), r.get("n_dark", 0)))
print("  金(确定/音效) %-46s  像素 %d" % (fmt(r.get("gold"), W, H), r.get("n_gold", 0)))
print("")
if "box" in r:
    bw, bh = r["box"]
    pw, ph = r["pop"]
    ox, oy = r.get("ovf", (0, 0))
    dens = r.get("dens", 0) or 1
    print("  * 等效盒 %dx%d px (= %.0f dp 高)   弹窗 %dx%d px (= %.0f dp 高)"
          % (bw, bh, bh / dens, pw, ph, ph / dens))
    if ox == 0 and oy == 0:
        print("     -> **装得下** (余量 %d x %d px)" % (bw - pw, bh - ph))
    else:
        print("     -> **溢出** 横 %d px / 纵 %d px  => %s 出屏"
              % (ox, oy, "两轴都" if ox and oy else ("横轴" if ox else "纵轴")))
else:
    print("  * 量不到 (没抓到弹窗或盒)")
print("")
print("  --- widget 树 ---")
for ln in r.get("tree", []):
    # 只留与弹窗有关的子树, 免得整棵树刷屏
    print("  " + ln)
