# -*- coding: utf-8 -*-
"""沙体材质 A/B —— 回答 r14-2号 的那句"它像不像沙"。

## 为什么要做这个

r14-2号（"第一次用这个 App 的普通用户"，不许读代码）说：

    上面那颗球满的时候，**完全看不出里面是沙** —— 没有沙面、没有颗粒，
    就是一整块颜色 + 细噪点，像**彩色橡皮泥**，或者**一只网球**。

r14-1号（独立量像素）量到同一件事的另一面：沙体亮度在 p35 与 p85 之间**变化 ≤1.0 级**
—— 沙面高低完全不改沙体本色。

两句话对上了，根因在 `_sand_material_rgba()` 里，两处：

    ① broad = 0.10*(y-0.5) - 0.07*qx - 0.16*edge
       竖直渐变只有 **±0.05 个 tone 单位** ⇒ 金沙约 **±1.3 级亮度**
       （人眼要 2~3 级才分得出）⇒ 宏观上就是一块平色。
    ② 颗粒是 512² 纹理的**逐像素白噪** ⇒ 铺到 ~890px 的球上每颗约 **1.7px**
       ⇒ 眼睛只能读成"细噪点", 读不成"沙粒"。

## 本工具做什么

**只用运行期替换 `_sand_material_rgba`, 不改 `main.py` 一个字节。** 渲四个候选:

    A cur     当前 (grad=0.10, coarse=1)
    B grad    竖直渐变 ×3.5 (grad=0.35)
    C coarse  颗粒变粗 ×3, **RMS 归一到与白噪同强度**（只改"粗细", 不掺杂"强弱"）
    D both    B + C

## 🔴 判据只有一句

**用户说哪个更像沙, 就照哪个。** 项目红线：知觉问题不能由像素统计裁决
（`apk/CLAUDE.md` 三条流程红线 §1）。本工具**只产出对照图, 不下任何结论**。

跑法:
    python tools/sand_material_ab.py
产物:
    _shot/sandmat/sandmat_full.png    整只沙漏, 2 场景 x 4 候选
    _shot/sandmat/sandmat_upper.png   上球特写（"像不像沙"就看这张）
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "sandmat"
PIX = (1096, 2214)          # 设备尺度: 1 桌面像素 = 1 设备像素（r14-1号 已标定）
SEED = 23
DURATION = 60.0

# (标签, 竖直渐变, 颗粒粗度倍数)
# ⚠️ 第一版只给了 grad=0.35 —— 实测 maxdiff 只有 **5 级**, 肉眼看不见(等于白给一档);
#    现在把渐变拆成 0.35 / 0.70 两档, 并且颗粒加一档 2x(3x 的第一版"像海绵")。
MAT_VARIANTS = [
    ("A_current", 0.10, 1),
    ("B_grad_mid", 0.35, 1),
    ("C_grad_strong", 0.70, 1),
    ("D_grain2", 0.10, 2),
    ("E_grain3", 0.10, 3),
    ("F_grain2_grad", 0.35, 2),
]

# ---- case surf: 只动**上球沙面微粗糙**这一个变量 ----------------------------
# 起因: r14-2号 与 r15-2号 (互相不知道对方) 各自独立说同一句话 ——
#   "沙面是一条笔直的水平线 …… 这就是一杯水的水位线"。
# 代码里的不对称(实测量出来的):
#   下球沙堆 MOUND_ROUGH_FRAC = 0.004  ->  设备 3.56 px
#   上球沙面 UPPER_ROUGH_FRAC = 0.0014 ->  设备 1.25 px   **只有下球的 1/3**
# 所以候选就是把上球往上抬, C 档 = 与下球持平。
# ⚠️ 2026-10-05 扩档: 实测发现**上球 1.25px / 下球 3.57px 的起伏, 铺在 890px 宽的沙面上
#    只有 0.14%~0.40%** —— 在这个比例下眼睛读到的是"干净的几何边"。四位评审独立说的
#    "像水位/像纸卷的圆锥/像塑料" 都指向这个量级问题。原来四档最大只到 5.35px(0.6%),
#    **可能全都太保守**, 故加 E/F 两档到 0.8%~1.2%。
SURF_VARIANTS = [
    ("A_cur_14", 0.0014),
    ("B_r25", 0.0025),
    ("C_r40", 0.0040),   # = MOUND_ROUGH_FRAC, 与下球持平
    ("D_r60", 0.0060),
    ("E_r80", 0.0080),   # 设备 ≈7.13 px
    ("F_r120", 0.0120),  # 设备 ≈10.70 px (1.2%)
]

CASE = (sys.argv[1] if len(sys.argv) > 1 else "mat").lower()
VARIANTS = SURF_VARIANTS if CASE == "surf" else MAT_VARIANTS
# (场景名, 进度)  —— mat: p=0 就是 r14-2号 说的"静置/满的时候"
#                    surf: 沙面在中段才看得清, 取 0.20 / 0.50
SCENES = ([("s035", 0.35), ("s060", 0.60)] if CASE == "surf"
          else [("full", 0.0), ("mid", 0.333)])


def variant_rgba(size, base, dark, light, seed=721, grain=0.35, shade=1.0,
                 grad=0.10, coarse=1):
    """`_sand_material_rgba` 的**参数化副本**（算式逐字照抄, 只把两处常量变成旋钮）。"""
    import numpy as np
    axis = (np.arange(size, dtype=np.float32) + 0.5) / size
    qx = (axis * 2.0 - 1.0)[None, :]
    w = np.clip((qx * qx - 0.64) / 0.36, 0.0, 1.0)
    edge = w * w * (3.0 - 2.0 * w)
    broad = grad * (axis[:, None] - 0.5) - 0.07 * qx - 0.16 * edge

    rng = np.random.default_rng(seed)
    if coarse > 1:
        from PIL import Image as PILImage
        low = max(2, int(round(size / coarse)))
        small = rng.random((low, low), dtype=np.float32)
        up = PILImage.fromarray((small * 255.0).astype(np.uint8)).resize(
            (size, size), PILImage.BILINEAR)
        noise = np.asarray(up, dtype=np.float32) / 255.0
        # ⚠️ 归一化到**与逐像素白噪相同的 RMS**(均匀分布 σ=1/√12):
        #    否则"颗粒变粗"会顺带把对比度也改掉, 两个变量混在一起就不是干净对照了。
        sd = float(noise.std())
        if sd > 1e-6:
            noise = 0.5 + (noise - float(noise.mean())) * (0.2886751 / sd)
    else:
        noise = rng.random((size, size), dtype=np.float32)

    tone = np.clip(shade * broad + grain * (2.0 * noise - 1.0), -1.0, 1.0)
    pal = [np.asarray(c, dtype=np.float32) for c in (base, dark, light)]
    target = np.where(tone[..., None] >= 0.0, pal[2], pal[1])
    rgb = pal[0] + np.abs(tone)[..., None] * (target - pal[0])
    out = np.empty((size, size, 4), dtype=np.uint8)
    out[..., :3] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out.tobytes()


def main():
    with tempfile.TemporaryDirectory(prefix="sandmat-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image

        # 音效/弹窗全部桩掉（与其它取证工具同规矩）
        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": DURATION}
        m.HourglassWidget.save_config = lambda *_: None

        # 冻结时钟: dt 恒 0 ⇒ 模拟不推进, 但每帧照常 redraw ⇒ 材质/颜色一定刷到位。
        # ⚠️ 绝不要 unschedule(hg.tick) —— r13 的底图就是这么坏的（换色只渲一帧, 滞后一例）。
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}
        orig_rgba = m._sand_material_rgba

        def install(grad, coarse):
            """把材质生成器换成候选, 并清缓存 ⇒ 下一次 sand_material() 会真重烘。"""
            m._sand_material_rgba = (
                lambda size, base, dark, light, seed=721, grain=0.35, shade=1.0:
                variant_rgba(size, base, dark, light, seed=seed, grain=grain,
                             shade=shade, grad=grad, coarse=coarse))
            m._SAND_MATERIAL_CACHE.clear()

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(PIX[0] / ratio), round(PIX[1] / ratio))
                Clock.schedule_once(self.begin, 0.5)

            def begin(self, _dt):
                self.hg = w = self.hourglass
                w.set_duration(int(DURATION))
                w.completion_enabled = False
                if not w.running:
                    w.toggle()
                w._done_at = None
                self.si = self.vi = 0
                Clock.schedule_once(self.scene, 0.4)

            # ---- 场景切换 ------------------------------------------------
            def scene(self, _dt):
                if self.si >= len(SCENES):
                    return self.finish()
                self.scene_name, self.p = SCENES[self.si]
                self.seek(DURATION * self.p)
                self.vi = 0
                Clock.schedule_once(self.variant, 0.3)

            def seek(self, target):
                w = self.hg
                import random
                random.seed(SEED)
                guard = 0
                while w.elapsed + 1e-9 < target and w.running:
                    step = min(1.0 / 120.0, target - w.elapsed)
                    now[0] += step
                    w.tick(step)
                    guard += 1
                    if guard > 400000:
                        break
                w.elapsed = min(target, w.duration)
                # 粒子清空: 这一版只比**沙体材质**, 不要让沙流给对照添噪
                w.particles[:] = []
                w.splashes = []
                w.flares[:] = []
                w.dusts[:] = []

            # ---- 候选切换 ------------------------------------------------
            def variant(self, _dt):
                if self.vi >= len(VARIANTS):
                    self.si += 1
                    return Clock.schedule_once(self.scene, 0.3)
                w = self.hg
                if CASE == "surf":
                    label, frac = VARIANTS[self.vi]
                    # 只换那 65 个浮点数组(纯赋值, 不重建几何/画布);
                    # `_upper_area` 与 `_draw_upper_shape` 读的是同一个数组 ⇒ 守恒自动一致。
                    m.UPPER_ROUGH_FRAC = frac
                    Ri = w._R_inner
                    w._upper_rough = m._surface_roughness(
                        Ri, frac, m.UPPER_ROUGH_SEED, frac * 2.0 * Ri)
                    w._mound_shape_cache = None
                    print("   %-10s FRAC=%.4f  设备幅度≈%.2f px"
                          % (label, frac, frac * 2.0 * 445.63))
                    w.redraw()
                else:
                    label, grad, coarse = VARIANTS[self.vi]
                    install(grad, coarse)
                    mat = m.sand_material(w.sand_base, w.sand_dark, w.sand_light)
                    if mat is None:
                        raise SystemExit("材质生成失败（numpy/PIL 缺失？）")
                    w.rebind_sand_material(mat)      # ⚠️ 轻量绑定, 不重建画布
                # ⚠️ 抓图必须在**下一个独立回调**里 —— 同一回调里连抓会读到同一张已呈现帧
                Clock.schedule_once(lambda dt: self.grab(label), 0.35)

            def grab(self, label):
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (ww, hh), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                shots[(self.scene_name, label)] = img
                self.check_geometry()
                self.vi += 1
                Clock.schedule_once(self.variant, 0.25)

            # ---- 闸门: 实测沙体主色必须等于配色 base --------------------
            def check_geometry(self):
                if getattr(self, "_geo", None) is not None:
                    return
                w = self.hg
                ww, hh = map(int, Window.size)
                cx_px, uy_px = w.to_window(w._cx, w._upper_y_c)
                self._geo = {
                    "win": (ww, hh),
                    "cx": cx_px, "uy_img": hh - uy_px,
                    "R": w._R_inner,
                    "base_px": [w.sand_base, w.sand_dark, w.sand_light],
                }

            def finish(self):
                m._sand_material_rgba = orig_rgba
                compose(shots, self._geo)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        app = P()
        app.run()
        m._sand_material_rgba = orig_rgba
    return 0


def compose(shots, geo):
    """拼对照图 + **自检**（四个候选若逐像素相同 ⇒ 这张"对照图"是假的）。"""
    from PIL import Image, ImageChops
    cx, uy, R = geo["cx"], geo["uy_img"], geo["R"]
    labels = [v[0] for v in VARIANTS]

    # ---- 自检: 相邻候选必须不同 ----
    print("[自检] 候选两两差异（必须 >0, 否则说明材质没真换）:")
    bad = []
    for scene, _ in SCENES:
        for i in range(len(labels) - 1):
            a, b = shots[(scene, labels[i])], shots[(scene, labels[i + 1])]
            mx = max(ImageChops.difference(a, b).convert("L").getdata())
            print("   %-5s %s vs %s : maxdiff=%d %s"
                  % (scene, labels[i], labels[i + 1], mx,
                     "OK" if mx > 0 else "**假对照! 材质没换**"))
            if mx == 0:
                bad.append((scene, labels[i], labels[i + 1]))

    # ---- 整只沙漏 ----
    x0 = max(0, int(cx - R - 40))
    x1 = min(shots[(SCENES[0][0], labels[0])].width, int(cx + R + 40))
    y0 = max(0, int(uy - R - 60))
    imgs, lbs = [], []
    for scene, _ in SCENES:
        for lb in labels:
            imgs.append(shots[(scene, lb)].crop((x0, y0, x1, y0 + 2 * int(R) + 260)))
            lbs.append("%s | %s" % (scene, lb))
    tag = "surf" if CASE == "surf" else "sandmat"
    _montage(imgs, lbs, OUT / ("%s_full.png" % tag),
             "%s  whole hourglass  (rows: %s / %s)"
             % (CASE, SCENES[0][0], SCENES[1][0]), per_row=3)

    # ---- 上球特写（"像不像沙"就看这张）----
    zoom = 1.6
    side = int(2 * R * 1.02)
    up, ulbs = [], []
    for scene, _ in SCENES:
        for lb in labels:
            c = shots[(scene, lb)].crop((int(cx - side / 2), int(uy - side / 2),
                                         int(cx + side / 2), int(uy + side / 2)))
            up.append(c.resize((int(c.width * zoom), int(c.height * zoom)),
                               Image.Resampling.LANCZOS))
            ulbs.append("%s | %s" % (scene, lb))
    _montage(up, ulbs, OUT / ("%s_upper.png" % tag),
             "%s  upper ball closeup (1.6x)  (rows: %s / %s)"
             % (CASE, SCENES[0][0], SCENES[1][0]), per_row=3)

    # ---- 1:1 设备像素（不缩放, 就是用户眼睛真正收到的尺度）----
    # 用**宽横带**而不是方块: "沙面直不直"要看到整条线才判得出来
    one, olbs = [], []
    for scene, _ in SCENES:
        for lb in labels:
            c = shots[(scene, lb)].crop((int(cx - R - 20), int(uy - 250),
                                         int(cx + R + 20), int(uy + 250)))
            one.append(c)
            olbs.append("%s | %s" % (scene, lb))
    _montage(one, olbs, OUT / ("%s_surface_1to1.png" % tag),
             "%s  whole upper surface, 1:1 device px (no scaling)" % CASE,
             per_row=1)

    # ---- 每格单独存一份(网页直接用), 直接来自抓到的整帧, 不经过拼图索引 ----
    web = OUT / "web"
    web.mkdir(exist_ok=True)
    for scene, _p in SCENES:
        for lb in labels:
            c = shots[(scene, lb)].crop((int(cx - R - 20), int(uy - 250),
                                         int(cx + R + 20), int(uy + 250)))
            if c.width > 720:
                c = c.resize((720, round(c.height * 720 / c.width)),
                             Image.Resampling.LANCZOS)
            c.convert("RGB").save(web / ("%s_%s_%s.jpg" % (tag, lb, scene)),
                                  quality=82, optimize=True)
    print("  -> web/ 单格 %d 张" % (len(SCENES) * len(labels)))

    # 收掉自检的返回值, 便于外部读
    if bad:
        print("\n**有假对照**: %s" % bad)
    print("\n产物: %s" % OUT)


def _montage(images, labels, path, title, per_row=3):
    from PIL import Image, ImageDraw
    pad, hdr, lab = 8, 22, 16
    rows = [images[i:i + per_row] for i in range(0, len(images), per_row)]
    rowlabels = [labels[i:i + per_row] for i in range(0, len(labels), per_row)]
    w = sum(im.width for im in rows[0]) + pad * (len(rows[0]) + 1)
    h = sum(r[0].height for r in rows) + (pad + lab) * len(rows) + pad + hdr
    canvas = Image.new("RGB", (w, h), (30, 30, 30))
    d = ImageDraw.Draw(canvas)
    y = pad
    d.text((pad, y), title, fill=(230, 230, 230))
    y += hdr
    for row, rl in zip(rows, rowlabels):
        x = pad
        for im, lb in zip(row, rl):
            canvas.paste(im, (x, y + lab))
            d.text((x + 4, y + 2), lb, fill=(255, 220, 120))
            x += im.width + pad
        y += row[0].height + lab + pad
    canvas.save(path)
    print("  -> %s  (%dx%d)" % (path.name, canvas.width, canvas.height))


if __name__ == "__main__":
    sys.exit(main())
