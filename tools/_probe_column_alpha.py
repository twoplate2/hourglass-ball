# -*- coding: utf-8 -*-
"""沙柱的**有效不透明度** —— 双背景差分(chroma-key)版。**取代旧的颜色投影尺。**

## 为什么旧的那把是废的(实测, 不是推理)

旧尺: 把像素投影到 (背景色 → 纯沙色) 连线上反推 alpha。它**分不开"亮色不透明"与"真半透明"**:
`tools/_probe_oldruler_control.py` 把 2.26 的柱身整体 **+10 灰阶、alpha 一个像素没动**,
旧尺读数当场从 中位 1.000 / p10 0.953 掉到 **0.933 / 0.870** —— 而它当作目标的
v1.2 是 0.945 / 0.855。**"把柱子调亮"就能让它达标**, 且它的正对照照样报"偏差 0.000"。
⇒ **正对照通过 != 尺子有判别力**: 那个正对照只证明了合成算术, 没证明色/透可分离。

## 新尺

同一场景**渲两次**, 只改柱**背后那一层**的颜色(`BG_COLOR` 与 `GLASS_FILL`)。
像素 `P = 后层们…(α_c·S_c + (1−α_c)·B)` 逐层套下来, 两次相减**沙色 S 被完整消掉**:

    P₁ − P₂ = (1−α_粒子)(1−α_柱)·(B₁ − B₂)
    ⇒ α_有效 = 1 − <P₁−P₂, ΔB> / <ΔB, ΔB>       ← 与沙色、与材质纹理**无关**

`α_有效` 是**穿透到眼睛的总不透明度**(含粒子层), 正是"偏实心"这个词在问的东西。

## 两个必过的对照(合成对, 元件级)

1. **标定**: 合成已知 α(0.90/0.70/0.40) 的带 ⇒ 必须逐个报出(±0.02)。
2. **色盲(判别性)**: 同一条带、**同一个 α**、**沙子换成亮 12 级的另一种色** ⇒ 读数必须**完全相同**。
   **旧尺过不了这一条** —— 本文件把旧尺的算式也实现了一遍, 当场对照打印。
3. **有分辨力(负对照)**: α=0.95 与 α=0.45 的两条带 ⇒ 读数必须分开。

## 真图上的有效性闸门

物理前提是"这一对像素之间**只有后层变了**"。检验办法: 差分向量必须**平行于 ΔB**。
    resid = D − τ·ΔB,   |resid| ≤ 0.06·|ΔB| + 2
不满足的像素**剔除并报出剔除率**。剔除率高 ⇒ 这一帧的读数不作数。

用法:
    python tools/_probe_column_alpha.py <A图> <B图> [标签]
    python tools/_probe_column_alpha.py --selftest
"""
import sys
from pathlib import Path

# 🔴 本项目的老坑: Windows 控制台默认 GBK, 而 `⇒` `₁` 这类字符**不在 GBK 里**
#    ⇒ 打印到一半 `UnicodeEncodeError`, 而报错看上去像"脚本坏了"。
#    探针全部自带这一句, 不依赖调用方设 `PYTHONIOENCODING`。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

# 出厂两层色(与 main.py 的 BG_COLOR / GLASS_FILL 默认值**逐字一致**)
BG_DEFAULT = "#fdf6e3"
GLASS_DEFAULT = "#eaf3f8"


def hex_rgb(s):
    s = s.lstrip("#")
    return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def is_sand(c):
    """**只用来定位**柱子在哪(不参与量化) —— 旧尺的错就在拿它当量化判据。"""
    return c[0] - c[2] > 40 and c[2] < 215


def _runs(px, W, H):
    out = {}
    for y in range(H):
        xs = [x for x in range(W) if is_sand(px[x, y])]
        out[y] = (xs[0], xs[-1], len(xs), xs[-1] - xs[0] + 1) if xs else None
    return out


# ---------------------------------------------------------------- 核心量测

def alpha_from_pair(A, B, dB):
    """给定两张图和 ΔB, 逐像素解 α_有效。返回 (α, 有效掩码, 残差)。"""
    D = A - B
    den = float(dB @ dB)
    tau = (D @ dB) / den
    resid = D - tau[..., None] * dB
    rnorm = np.sqrt((resid * resid).sum(-1))
    valid = rnorm <= 0.06 * np.sqrt(den) + 2.0
    alpha = 1.0 - tau
    return alpha, valid, rnorm


def ball_span(img):
    """从**画出来的那张图**上量沙漏玻璃外形有多宽(最宽那一行的外缘跨度)。

    🔴 **为什么必须有这条**: 两次渲染各自跑一个进程, 而窗口尺寸是**实时**设的
    (`inspect_flow.VisualApp.resize` 用 `Clock.schedule_once` 改 `Window.system_size`)。
    实测踩过: 同一条命令的 `keyB` 那次拿到了 `2R_inner = 395.5`, 而 `keyA` 是 1238.7
    —— 两条臂画的是**大小差 3 倍的沙漏**, 逐像素差分出来的 α 全是垃圾。
    ⇒ 它同时给出"这一对能不能比"的判据, 不靠调用方自觉。
    """
    H, W = img.shape[:2]
    bg = img[2, 2]
    nb = (np.abs(img - bg).max(-1) > 20)
    span = 0
    for y in range(int(H * 0.10), int(H * 0.98), 7):
        xs = np.nonzero(nb[y])[0]
        if xs.size:
            span = max(span, int(xs[-1] - xs[0] + 1))
    return span


def geometry_gate(A, B, tol=3):
    """两条臂画的必须是**同一个几何**。返回 (ok, spanA, spanB)。"""
    sa, sb = ball_span(A), ball_span(B)
    return abs(sa - sb) <= tol, sa, sb


def calibrate_dB(A, B, glass_hex):
    ref = hex_rgb(glass_hex)
    m = (np.abs(A - ref).max(-1) <= 3.0)
    n = int(m.sum())
    if n < 500:
        return None, n, 0.0
    Dm = (A - B)[m]
    dB = np.median(Dm, axis=0)
    spread = float(np.median(np.abs(Dm - dB).max(-1)))
    return dB, n, spread


def band_of(px, W, H, y_lo=0.30):
    """柱带 = 下半幅里连续的"窄"行(旧探针同一套定位, 不参与量化)。"""
    rows = _runs(px, W, H)
    wid = {y: (rows[y][3] if rows[y] else 0) for y in rows}
    narrow = [y for y in range(int(H * y_lo), H) if 4 < wid.get(y, 0) < W * 0.18]
    if len(narrow) < 30:
        return rows, wid, None
    segs, cur = [], [narrow[0]]
    for y in narrow[1:]:
        if y == cur[-1] + 1:
            cur.append(y)
        else:
            segs.append(cur)
            cur = [y]
    segs.append(cur)
    return rows, wid, max(segs, key=len)


def measure(A_path, B_path, tag, glass_hex=GLASS_DEFAULT, save_map=False):
    A = np.asarray(Image.open(A_path).convert("RGB"), dtype=np.float64)
    B = np.asarray(Image.open(B_path).convert("RGB"), dtype=np.float64)
    if A.shape != B.shape:
        print("  !! 尺寸不同 %s vs %s" % (A.shape, B.shape))
        return None
    H, W = A.shape[:2]
    ok_geom, spanA, spanB = geometry_gate(A, B)
    if not ok_geom:
        print("  %-18s !! **几何闸门不过**: 两臂沙漏宽度 %d vs %d —— 画的不是同一个沙漏,"
              " 本次不作数(重渲)" % (tag, spanA, spanB))
        return None
    im = Image.open(A_path).convert("RGB")
    rows, wid, seg = band_of(im.load(), W, H)
    if seg is None:
        print("  %-18s 找不到柱段" % tag)
        return None
    y0, y1 = seg[0], seg[-1]
    cx = int(np.median([(rows[y][0] + rows[y][1]) // 2 for y in seg]))
    band_w = int(np.median([wid[y] for y in seg]))

    dB, n_glass, spread = calibrate_dB(A, B, glass_hex)
    if dB is None:
        print("  %-18s 找不到纯内腔像素(标定不了 ΔB) —— 本次不作数" % tag)
        return None

    alpha, valid, rnorm = alpha_from_pair(A, B, dB)

    # 只取柱带内的像素
    inband = np.zeros((H, W), bool)
    for y in seg:
        r = rows[y]
        if r:
            inband[y, r[0]:r[1] + 1] = True
    sel = inband & valid
    tot = int(inband.sum())
    kept = int(sel.sum())
    a = alpha[sel]
    a_all = alpha[inband]
    # ---- 三分量分解(量化免疫, 不依赖 α 的连续读数) ----
    #  ① **逐位不动**: 两张图那一个像素**完全相同** ⇒ 后层变色对它毫无影响 ⇒ 真·不透明实心。
    #     这是最硬的一档 —— 它不含任何反演误差。
    #  ② **真洞**: α<0.05 ⇒ 后层几乎原样透出来。
    #  ③ **灰雾**: 0.05≤α<0.90 ⇒ 既不是沙也不是背景, 是"雾"。这一档越少越好。
    Dsel = (A - B)[sel]
    frozen = int((np.abs(Dsel) < 0.5).all(axis=1).sum())
    hole = int((a < 0.05).sum())
    fog = int(((a >= 0.05) & (a < 0.90)).sum())
    solid = kept - frozen - hole - fog

    # ---- 后层一致性的旁证: 柱带两旁的**外晕**也应当给出同一个 ΔB ----
    halo = np.zeros((H, W), bool)
    pad = max(2, int(band_w * 0.9))
    for y in seg:
        r = rows[y]
        if not r:
            continue
        halo[y, max(0, r[0] - pad - 40):max(1, r[0] - pad)] = True
        halo[y, min(W, r[1] + pad):min(W, r[1] + pad + 40)] = True
    n_halo = int(halo.sum())
    dB_halo = np.median((A - B)[halo], axis=0) if n_halo else np.zeros(3)
    halo_ang = (float(dB_halo @ dB) /
                max(1e-9, float(np.linalg.norm(dB_halo) * np.linalg.norm(dB))))

    print("  %-18s %dx%d  沙漏宽 %d px(两臂一致 ✓)  柱段 行%d..%d (%d行) 中轴x=%d 中位宽=%d"
          % (tag, W, H, spanA, y0, y1, y1 - y0 + 1, cx, band_w))
    print("     ΔB = %s  (取自 %d 个纯内腔像素, 中位散布 %.1f 级)"
          % (np.round(dB, 1), n_glass, spread))
    print("     外晕 ΔB = %s  与主 ΔB 夹角余弦 %.4f" % (np.round(dB_halo, 1), halo_ang))
    print("     有效性: 柱内 %d px, 平行于 ΔB 的 %d px (%.1f%%)"
          % (tot, kept, 100.0 * kept / max(1, tot)))
    if kept < 0.5 * tot:
        print("     !! 去掉超过一半 —— 这一帧**不作数**(后层不止一种, 差分不成立)")
    print("     **柱内 α_有效: 中位 %.3f  10分位 %.3f  均值 %.3f**"
          % (np.median(a), np.percentile(a, 10), a.mean()))
    print("     三分量(占柱内有效像素): ①逐位不动(实心) %.1f%%  ②真洞 α<0.05 %.1f%%"
          "  ③灰雾 0.05~0.90 %.1f%%  ④其余近实心 %.1f%%"
          % (100.0 * frozen / max(1, kept), 100.0 * hole / max(1, kept),
             100.0 * fog / max(1, kept), 100.0 * solid / max(1, kept)))
    print("     (① 是**不含反演误差**的一档: 两臂那个像素逐位相同)")
    print("     α<0.05 %.1f%%   α<0.15 %.1f%%   α<0.50 %.1f%%   α≥0.95 %.1f%%"
          % (100.0 * (a < 0.05).mean(), 100.0 * (a < 0.15).mean(),
             100.0 * (a < 0.50).mean(), 100.0 * (a >= 0.95).mean()))
    hist, edges = np.histogram(a, bins=10, range=(0, 1))
    print("     直方图 " + " ".join("%.1f:%.0f%%" % (edges[i], 100.0 * hist[i] / max(1, len(a)))
                                    for i in range(10))
          + "   (>1.0: %.0f%% —— 只是 ±1 级噪声)" % (100.0 * (a > 1.0).mean()))
    # ---- 色栏: **只取"逐位不动"的像素** —— 那些像素已被证明是全不透明的,
    #      于是它们显示的就是沙**自己的颜色**, 一点透明度污染都没有。
    #      旧尺时代"亮于/暗于 base"那组数就是这么被污染的(它没排除半透明像素)。
    body_sel = np.zeros((H, W), bool)
    for y in range(int(H * 0.05), int(H * 0.55)):
        r = rows[y]
        if r and r[3] > W * 0.30:
            body_sel[y, r[0]:r[1] + 1] = True
    frz_all = (np.abs(A - B).max(-1) < 0.5)
    body_frz = body_sel & frz_all
    col_frz = sel & frz_all
    if body_frz.sum() > 2000 and col_frz.sum() > 200:
        Cw = np.median(A[body_frz], axis=0)
        Cc = np.median(A[col_frz], axis=0)
        lw = float(Cw @ np.array([0.299, 0.587, 0.114]))
        lc = float(Cc @ np.array([0.299, 0.587, 0.114]))
        dl = (A[col_frz] @ np.array([0.299, 0.587, 0.114])) - lw
        print("     色栏(只取逐位不动像素): 柱身 %s  沙体 %s  柱身−沙体 = %+.1f 级"
              % (np.round(Cc, 0), np.round(Cw, 0), lc - lw))
        print("       柱身逐像素相对沙体: 亮于 %+.0f%% / 暗于 %+.0f%% / 中位 %+.1f 级"
              % (100.0 * (dl > 1).mean(), 100.0 * (dl < -1).mean(), float(np.median(dl))))

    # ---- 洞**长在哪里**: "边缘毛糙"与"内部穿孔"是两件事, 四格占比看不见 ----
    #      v1.2 是"颗粒之间天然露背景" ⇒ 洞**散布在内部**;
    #      一块实心板只会**边缘**漏 ⇒ 四格占比可以一样, 长得完全不像。
    core_hole_rows = []
    for y in seg:
        r = rows[y]
        if not r:
            continue
        w = r[1] - r[0] + 1
        x0 = r[0] + int(w * 0.18)
        x1 = r[1] - int(w * 0.18)
        if x1 - x0 < 4:
            continue
        m = inband[y, x0:x1 + 1] & valid[y, x0:x1 + 1]
        if m.sum() < 4:
            continue
        core_hole_rows.append(float((alpha[y, x0:x1 + 1][m] < 0.05).mean()))
    if core_hole_rows:
        ch = np.array(core_hole_rows)
        print("     内部孔洞率(去掉两侧各 18%% 边缘): 均值 %.1f%%  中位 %.1f%%  逐行 std %.3f"
              "  一行洞都没有的行占 %.0f%%"
              % (100.0 * ch.mean(), 100.0 * np.median(ch), float(ch.std()),
                 100.0 * (ch < 1e-6).mean()))

    prof = []
    for y in seg:
        r = rows[y]
        if not r:
            continue
        m = valid[y, r[0]:r[1] + 1]
        if m.sum() >= 3:
            prof.append(float(np.median(alpha[y, r[0]:r[1] + 1][m])))
    if prof:
        q = np.array(prof)
        print("     沿深度(每行中位) 首/中/末 = %.3f / %.3f / %.3f   行间 std %.3f"
              % (q[0], q[len(q) // 2], q[-1], float(q.std())))
    # 沿宽度的剖面(按归一化横向位置聚合)
    uu, aa = [], []
    for y in seg:
        r = rows[y]
        if not r:
            continue
        xs = np.arange(r[0], r[1] + 1)
        u = (xs - cx) / max(1.0, (r[1] - r[0] + 1) * 0.5)
        m = valid[y, r[0]:r[1] + 1]
        uu.append(u[m]); aa.append(alpha[y, r[0]:r[1] + 1][m])
    if uu:
        uu = np.concatenate(uu); aa = np.concatenate(aa)
        bins = np.linspace(-1, 1, 9)
        idx = np.clip(np.digitize(uu, bins) - 1, 0, 7)
        prof_w = [float(np.median(aa[idx == k])) if (idx == k).sum() > 20 else float("nan")
                  for k in range(8)]
        print("     沿宽度(中轴→边缘): " + " ".join(
            "%.2f" % v if v == v else " -- " for v in prof_w))

    if save_map:
        out = ROOT / "benchmark_logs" / "_vid"
        out.mkdir(parents=True, exist_ok=True)
        vis = np.zeros((H, W), np.uint8)
        vis[inband] = np.clip(a_all * 255, 0, 255).astype(np.uint8)
        Image.fromarray(vis).save(out / ("alphamap_%s.png" % tag))
        base = np.asarray(im).copy()
        base[inband & ~valid] = (255, 0, 0)          # 被剔除的像素涂红
        Image.fromarray(base).save(out / ("alphainvalid_%s.png" % tag))
        print("     图: benchmark_logs/_vid/alphamap_%s.png / alphainvalid_%s.png" % (tag, tag))

    return dict(alpha_med=float(np.median(a)), alpha_p10=float(np.percentile(a, 10)),
                alpha_mean=float(a.mean()), kept=kept, total=tot,
                dB=dB.tolist(), halo_cos=halo_ang)


# ---------------------------------------------------------------- 对照

def _old_ruler_alpha(sub, C_bg, C_sand):
    d = C_sand - C_bg
    return np.clip(((sub - C_bg) @ d) / max(1e-9, float(d @ d)), 0.0, 1.0)


def _synth_pair(alpha_band, sand, bg1, bg2, W=240, H=200, y0=60, y1=140):
    """造一对图: 上面是纯后层, 中间一条**已知 α** 的沙带。两张只差后层色。"""
    A = np.zeros((H, W, 3)) + bg1
    B = np.zeros((H, W, 3)) + bg2
    A[y0:y1] = alpha_band * sand + (1 - alpha_band) * bg1
    B[y0:y1] = alpha_band * sand + (1 - alpha_band) * bg2
    return A, B


def selftest():
    bg1, bg2 = hex_rgb(GLASS_DEFAULT), hex_rgb("#303030")
    sand = np.array([230.0, 180.0, 110.0])
    ok = True

    def run(A, B, glass_hex=GLASS_DEFAULT):
        dB, _, _ = calibrate_dB(A, B, glass_hex)
        alpha, valid, _ = alpha_from_pair(A, B, dB)
        # 用"沙带所在行"的中段取值
        core = alpha[70:130, 100:140][valid[70:130, 100:140]]
        return float(np.median(core))

    print("== ① 标定: 已知 α 必须被报出 (±0.02) ==")
    for a_true in (0.90, 0.70, 0.40):
        got = run(*_synth_pair(a_true, sand, bg1, bg2))
        flag = "OK" if abs(got - a_true) <= 0.02 else "!! 不合格"
        ok &= abs(got - a_true) <= 0.02
        print("     α=%.2f ⇒ 读出 %.4f   %s" % (a_true, got, flag))

    print("\n== ② 色盲(判别性): 同一个 α, 沙子换色 ⇒ 读数必须**完全相同** ==")
    sand_bright = np.clip(sand + 12.0, 0, 255)
    g1 = run(*_synth_pair(0.60, sand, bg1, bg2))
    g2 = run(*_synth_pair(0.60, sand_bright, bg1, bg2))
    print("     沙色 %s ⇒ %.4f" % (sand.astype(int), g1))
    print("     沙色 %s (亮 12 级) ⇒ %.4f" % (sand_bright.astype(int), g2))
    same = abs(g1 - g2) < 1e-6
    print("     差 = %.2e   %s" % (abs(g1 - g2), "OK 逐位相同" if same else "!! 不合格"))
    ok &= same

    print("\n== ②b 同一条控制下**旧尺**的表现(它必须失败) ==")
    Ab, Bb = _synth_pair(0.60, sand, bg1, bg2)
    Ac, Bc = _synth_pair(0.60, sand_bright, bg1, bg2)
    C_bg = np.median(Ab[10:50, 100:140].reshape(-1, 3), axis=0)     # 纯后层
    for nm, X in (("原色", Ab), ("亮 12 级", Ac)):
        C_sand = np.median(X[70:130, 100:140].reshape(-1, 3), axis=0)
        old = _old_ruler_alpha(X[70:130, 100:140], C_bg, C_sand)
        print("     旧尺 %-9s ⇒ 中位 %.4f" % (nm, float(np.median(old))))
    print("     (旧尺把 C_sand 从**图里**认出来 ⇒ 调亮后它自动把新色当参照,")
    print("      于是读数看似不变; 真实图里参照同时被调亮, 它就会把'更亮'读成'更透' ——")
    print("      真图实测见 tools/_probe_oldruler_control.py: 1.000 → 0.933)")

    print("\n== ③ 分辨力(负对照): 不同 α 必须分开 ==")
    h1 = run(*_synth_pair(0.95, sand, bg1, bg2))
    h2 = run(*_synth_pair(0.45, sand, bg1, bg2))
    print("     α=0.95 ⇒ %.4f    α=0.45 ⇒ %.4f    差 %.4f" % (h1, h2, abs(h1 - h2)))
    ok &= abs(h1 - h2) > 0.4
    print("\n== 结论: %s ==" % ("全部通过, 这把尺有判别力" if ok else "!! 有不合格项, 本次不作数"))
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    A, B = Path(sys.argv[1]), Path(sys.argv[2])
    tag = sys.argv[3] if len(sys.argv) > 3 else A.stem
    glass = sys.argv[4] if len(sys.argv) > 4 else GLASS_DEFAULT
    measure(A, B, tag, glass_hex=glass, save_map=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
