# -*- coding: utf-8 -*-
"""marker 批处理 **vs** 逐 `Line`: 逐像素对照(2026-10-07)。

## 为什么必须做这个

`tools/marker_batch_experiment.py` 会把 20 根斜短线的
`Color + BindTexture + Line`(共 61 条指令)换成一批 `Mesh`。
**它会改像素**: 着色器里的旋转在 GPU 上算, 而 Kivy 在 CPU 上用 `math.cos/sin` 造网格
⇒ 端点/圆头帽边缘差 ULP 级, 落在像素边界上就翻一个像素。
项目红线要求**视觉改动对着并排图由用户判** ⇒ 先把差量量出来。

## 做法(同一次运行里截两张, 别的什么都没动)

1. 跑到 15s 档中期 → 截图 **A**(原 `Line` 路径)
2. `marker_batch_experiment.install()` + 重建画布 + 再跑几帧 → 截图 **B**(批处理路径)
3. 逐像素比 A/B, 报**差异像素数**与**最大通道差**

Kivy 的 `Window.screenshot(name=...)` 会存成 `<name>0001.png`(带序号), 别按
`<name>.png` 去找。两张图之间沙面会走 6 帧 ⇒ 沙子本身也在动 —— 所以本探针
**只报差量, 不宣称"0 差异"**; 要判"是不是只有标记在变", 看 `_side_by_side.py` 的并排图。
"""
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
OUT = ROOT / "benchmark_logs"
FRAMES_BEFORE = int(15 * 60 * 0.5)
FRAMES_AFTER = 6


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"
    os.environ.setdefault("HG_SPLASH_RENDERER", "batch")
    os.environ.setdefault("HG_NECK_RENDERER", "batch")
    sys.argv = sys.argv[:1] + ["_mkeq"]
    import main as m
    import marker_batch_experiment as mb
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
    m.HourglassWidget.save_config = lambda *_: None

    for f in glob.glob(str(ROOT / "_mkeq_*_.png")):
        os.remove(f)
    box = {}

    def count_instr(node):
        """递归数画布指令条数 —— **逐条确定**, 是"把 N 条并成 1 条"那类改动最硬的证据。"""
        n = 0
        for ch in getattr(node, "children", ()):
            n += 1 + count_instr(ch)
        return n

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            hg.set_duration(15.0)
            hg._rebuild_height_table()
            hg.reset()
            hg.toggle()
            for _ in range(FRAMES_BEFORE):
                hg.elapsed += 1 / 60.0
                hg.tick(1 / 60.0)
            box["n_before"] = getattr(hg, "_surface_marker_n", None)
            # 🔴 **先冻住动画再截** —— 否则两张图相隔几帧, 沙面/沙流自己在动,
            #    差量里绝大部分是沙子, 根本分不出"标记改了什么"。
            #    冻结后 `tick` 不再推进 `elapsed`, 标记也按设计**停在原地**(不是隐藏)。
            hg.running = False
            hg.tick(1 / 60.0)
            Window.screenshot(name="_mkeq_A_.png")   # 裸名 + .png: Kivy 会按 "." 拆扩展名再 join(getcwd())
            Clock.schedule_once(self.swap, 0.5)

        def swap(self, _dt):
            # 🔴 **第三臂**: 只重建画布、**不装批处理**。
            #    `install()` 必须靠重建才生效 ⇒ B 臂里本来就混着"重建"这个变量。
            #    先证明"重建本身是像素中性的"(A == A2), B - A2 才能归给 marker 批处理。
            hg = self.hourglass
            hg._build_dynamic_canvas()
            for _ in range(FRAMES_AFTER):
                hg.tick(1 / 60.0)          # running=False ⇒ elapsed 不动
            box["n_a2"] = getattr(hg, "_surface_marker_n", None)
            box["instr_a2"] = count_instr(hg.canvas)
            Window.screenshot(name="_mkeq_A2_.png")
            Clock.schedule_once(self.install_batch, 0.6)

        def install_batch(self, _dt):
            hg = self.hourglass
            box["installed"] = mb.install(type(hg))
            hg._build_dynamic_canvas()
            for _ in range(FRAMES_AFTER):
                hg.tick(1 / 60.0)
            box["n_after"] = getattr(hg, "_surface_marker_n", None)
            box["instr_b"] = count_instr(hg.canvas)
            Window.screenshot(name="_mkeq_B_.png")
            Clock.schedule_once(self.done, 0.6)

        def done(self, _dt):
            self.stop()

    P().run()

    from PIL import Image

    def pick(tag):
        f = sorted(glob.glob(str(ROOT / ("_mkeq_%s_*.png" % tag))))
        return Image.open(f[-1]).convert("RGB") if f else None

    ia, ia2, ib = pick("A"), pick("A2"), pick("B")
    print("")
    if ia is None or ia2 is None or ib is None:
        print("!! 截图没产出: A=%s A2=%s B=%s" % (ia is not None, ia2 is not None, ib is not None))
        return 1
    if not (ia.size == ia2.size == ib.size):
        print("!! 尺寸不同: %s %s %s" % (ia.size, ia2.size, ib.size))
        return 1
    w, h = ia.size
    total = w * h

    def cmp_(x, y):
        px, py = x.load(), y.load()
        diff = strong = maxd = 0
        for yy in range(h):
            for xx in range(w):
                ca, cb = px[xx, yy], py[xx, yy]
                d = max(abs(ca[0] - cb[0]), abs(ca[1] - cb[1]), abs(ca[2] - cb[2]))
                if d:
                    diff += 1
                if d > maxd:
                    maxd = d
                if d > 2:
                    strong += 1
        return diff, strong, maxd

    d_rebuild = cmp_(ia, ia2)        # A vs A2: **只重建** 的影响(应该接近 0 才算控制干净)
    d_batch = cmp_(ia2, ib)          # A2 vs B: **只装批处理** 的影响(这才是要看的)
    diff, strong, maxd = d_batch
    print("  === marker 批处理等效性(三臂) ===")
    print("  install() 返回        : %s" % box.get("installed"))
    print("  标记数 A/A2/B         : %s / %s / %s"
          % (box.get("n_before"), box.get("n_a2"), box.get("n_after")))
    print("  [控制] A vs A2 (只重建画布, 不装批处理)")
    print("        差 %d 像素 (%.4f%%), 差>2级 %d, 最大通道差 %d"
          % (d_rebuild[0], 100.0 * d_rebuild[0] / total, d_rebuild[1], d_rebuild[2]))
    if d_rebuild[0] == 0:
        print("        -> 重建是像素中性的, 控制干净")
    else:
        print("        -> !! 重建本身就不中性 => **B 臂里混着它**, 下面的差不能全归给批处理")
    ia_, ib_ = box.get("instr_a2"), box.get("instr_b")
    print("  [收益] 画布指令 A2 -> B : %s -> %s (省 %s 条)" % (ia_, ib_, (ia_ - ib_) if (ia_ and ib_) else "?"))
    print("  [结论] A2 vs B (只装批处理)")
    print("  像素总数              : %d" % total)
    print("  有差异的像素          : %d (%.4f%%)" % (diff, 100.0 * diff / total))
    print("  差 >2 级的像素        : %d (%.4f%%)" % (strong, 100.0 * strong / total))
    print("  最大通道差            : %d" % maxd)
    print("")
    print("  两张图相隔 %d 帧 **沙面本身也在动**, 所以上面的差量里含沙子的正常演算。"
          % FRAMES_AFTER)
    print("     要判「是不是只有标记在变」, 看并排图(本探针不宣称 0 差异)")
    return 0


sys.exit(run())
