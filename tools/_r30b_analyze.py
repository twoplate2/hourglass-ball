"""r30b 观感漂移分析: 分区域像素差 + 单图几何/纹理指标。

用法:
    python tools/_r30b_analyze.py --diff A.png B.png [--label L]
    python tools/_r30b_analyze.py --metrics A.png [--label L]
    python tools/_r30b_analyze.py --montage A.png B.png --out out.png
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

W, H = 1080, 1920

# 区域定义 (y0, y1, x0, x1) —— 1080x1920 竖屏
REGIONS = {
    "topbar":    (100, 280, 15, 1065),    # 6 色块
    "countdown": (282, 392, 15, 1065),    # 倒计时数字(会变)
    "upperball": (392, 1008, 215, 865),   # 上球
    "neck":      (1008, 1098, 470, 610),  # 颈部直筒
    "lowerball": (1098, 1712, 215, 865),  # 下球
    "buttons":   (1742, 1912, 15, 1065),  # 底部按钮行
}

# 几何量测用的窄窗口(避开玻璃壁)
SURF_X0, SURF_X1 = 400, 680          # 上球沙面扫描 x 范围
SURF_Y0, SURF_Y1 = 392, 1010
NECK_WIN = (1020, 1080, 495, 585)    # 颈部直筒量测窗
JET_WIN = (1102, 1200, 400, 680)     # 颈出口下方粒子带


def gray(arr):
    a = arr.astype(np.float32)
    return a[:, :, 0] * 0.299 + a[:, :, 1] * 0.587 + a[:, :, 2] * 0.114


def load(path):
    return np.asarray(Image.open(path).convert("RGB")).astype(np.int16)


def crop(a, r):
    y0, y1, x0, x1 = r
    return a[y0:y1, x0:x1]


def diff_stats(a, b, region=None):
    if region is not None:
        a, b = crop(a, region), crop(b, region)
    d = np.abs(a - b).max(axis=2)
    return {
        "mean": round(float(d.mean()), 4),
        "p99": round(float(np.percentile(d, 99)), 2),
        "max": int(d.max()),
        "n_gt8": int((d > 8).sum()),
        "frac_gt8": round(float((d > 8).mean()), 6),
    }


def sand_surface_curve(a, x0=SURF_X0, x1=SURF_X1, y0=SURF_Y0, y1=SURF_Y1,
                       thr=95.0, run=10):
    """逐列找"第一个连续 run 行都是暗"的 y —— 上球沙面。返回 (xs, ys), 找不到为 -1。

    ⚠️ thr 必须 < 玻璃描边灰度(实测 ≈121, #6e7d85), 否则描边被当成沙。
       黑沙灰度 ≈58-70 ⇒ 取 95 有 25 以上的两侧余量。
    """
    g = gray(a)
    sub = (g[y0:y1, x0:x1] < thr)
    ker = np.ones(run, dtype=np.int32)
    xs, ys = [], []
    for xi in range(sub.shape[1]):
        col = sub[:, xi].astype(np.int32)
        cs = np.convolve(col, ker, "valid")
        hit = np.nonzero(cs == run)[0]
        xs.append(x0 + xi)
        ys.append(y0 + int(hit[0]) if len(hit) else -1)
    return np.array(xs), np.array(ys, dtype=np.int32)


def upper_metrics(a):
    xs, ys = sand_surface_curve(a)
    ok = ys > 0
    g = gray(a)
    # 上球内暗像素面积(沙量代理) —— 避开采玻璃描边, 用中央窄窗
    ub = g[430:1005, 380:700] < 95
    out = {"surf_n": int(ok.sum()), "up_dark_px": int(ub.sum())}
    if ok.sum() < 20:
        return out
    yv = ys[ok]
    # 跟随沙面的窗口灰度(检测整体变淡/变暗) —— 固定 y 窗口会随沙面下沉跑进空腔
    ytop = int(np.median(yv)) + 30
    win = g[ytop:ytop + 100, 470:610]
    if win.size:
        out["up_gray_med"] = round(float(np.median(win)), 3)
        out["up_gray_std"] = round(float(win.std()), 3)
    out["surf_y_med"] = float(np.median(yv))
    out["surf_y_min"] = int(yv.min())
    out["surf_y_max"] = int(yv.max())
    # 去趋势后的轮廓起伏(高频): 移动平均窗口 31 减掉
    k = 31
    if len(yv) > k:
        pad = np.pad(yv.astype(np.float64), k // 2, mode="edge")
        ma = np.convolve(pad, np.ones(k) / k, "valid")[:len(yv)]
        det = yv - ma
        out["contour_hf_std"] = round(float(det.std()), 4)
        out["contour_hf_p95"] = round(float(np.percentile(np.abs(det), 95)), 4)
        out["contour_hf_max"] = round(float(np.abs(det).max()), 4)
    # 沙面下方纹理: 逐列取 [y+below, y+below+depth] 的 std
    g = gray(a)
    x_ok = xs[ok]
    yv_i = ys[ok]
    amps = []
    for xi, yi in zip(x_ok, yv_i):
        seg = g[yi + 18: yi + 78, xi]
        if len(seg) == 60:
            amps.append(seg.std())
    if amps:
        out["tex_amp_med"] = round(float(np.median(amps)), 4)
        out["tex_amp_p25"] = round(float(np.percentile(amps, 25)), 4)
    # 2D 纹理带的高频能量(锐度): 取沙面下 12..60 的矩形带
    ytop = int(np.median(yv_i)) + 12
    band = g[ytop:ytop + 48, x_ok.min():x_ok.max()]
    if band.size > 100:
        out["tex_hf_h"] = round(float(np.abs(np.diff(band, axis=1)).mean()), 4)  # 水平相邻
        out["tex_hf_v"] = round(float(np.abs(np.diff(band, axis=0)).mean()), 4)  # 垂直相邻
        out["tex_std2d"] = round(float(band.std()), 4)
    return out


def lower_metrics(a):
    """下球沙堆: 中轴附近找**最长连续暗段**的顶端(避开上方稀疏沙流粒子);
    另量堆体暗像素面积。"""
    g = gray(a)
    y0, y1, x0, x1 = 1100, 1712, 430, 650
    sub = g[y0:y1, x0:x1] < 95
    cx = 540 - x0
    mid = sub[:, cx - 40: cx + 40]
    out = {}
    colhits = []
    for xi in range(mid.shape[1]):
        col = mid[:, xi].astype(np.int8)
        if not col.any():
            colhits.append(-1)
            continue
        # 最长连续 True 段的起点
        d = np.diff(np.concatenate(([0], col, [0])))
        starts = np.nonzero(d == 1)[0]
        ends = np.nonzero(d == -1)[0]
        lens = ends - starts
        best = int(np.argmax(lens))
        if lens[best] >= 8:
            colhits.append(y0 + int(starts[best]))
        else:
            colhits.append(-1)
    ch = np.array(colhits)
    good = ch > 0
    out["mound_top_y"] = float(np.median(ch[good])) if good.sum() > 5 else -1.0
    out["mound_dark_px"] = int(sub.sum())
    out["mound_dark_frac"] = round(float(sub.mean()), 5)
    return out


def mound_surface_curve(a, y_bot=1718, y_top=1250, x0=300, x1=780, thr=95.0):
    """下球沙堆表面轮廓: 每列**最底部那段连续暗**的顶端。

    ⚠️ 两个已踩的坑: ①"最长连续暗段"会抓到竖直沙流柱; ②固定 y_bot 会被球的弧形底
       判空(球底 y 随 x 变, x=540 处 ≈1682, x=400 处 ≈1647)。这里从每列**最后一个**
       暗像素起往上扩, 天然跟随球底弧形, 且沙流悬空在堆之上、不会被算进来。
    """
    g = gray(a)
    xs, ys = [], []
    for xi in range(x0, x1):
        col = (g[y_top:y_bot, xi] < thr)
        nz = np.nonzero(col)[0]
        if not len(nz):
            xs.append(xi); ys.append(-1); continue
        j = int(nz[-1])                      # 该列最后一个暗像素
        while j >= 0 and col[j]:
            j -= 1
        xs.append(xi)
        ys.append(y_top + j + 1)
    return np.array(xs), np.array(ys, dtype=np.int32)


def mound_surface_metrics(a):
    xs, ys = mound_surface_curve(a)
    ok = ys > 0
    out = {"mound_curve_n": int(ok.sum())}
    if ok.sum() < 40:
        return out
    yv = ys[ok].astype(np.float64)
    out["mound_surf_y_med"] = float(np.median(yv))
    k = 25
    if len(yv) > k:
        pad = np.pad(yv, k // 2, mode="edge")
        ma = np.convolve(pad, np.ones(k) / k, "valid")[:len(yv)]
        det = yv - ma
        out["mound_curve_hf"] = round(float(det.std()), 4)
    return out


def neck_metrics(a):
    g = gray(a)
    w = crop(g, NECK_WIN)
    out = {"neck_mean": round(float(w.mean()), 3),
           "neck_std": round(float(w.std()), 3)}
    # 直筒内暗像素占比(沙柱)
    out["neck_dark_frac"] = round(float((w < 95).mean()), 4)
    return out


def jet_metrics(a):
    g = gray(a)
    y0, y1, x0, x1 = JET_WIN
    sub = g[y0:y1, x0:x1]
    return {"jet_dark_frac": round(float((sub < 95).mean()), 5),
            "jet_mean": round(float(sub.mean()), 3)}


def all_metrics(path):
    a = load(path)
    m = {}
    m.update(upper_metrics(a))
    m.update(lower_metrics(a))
    m.update(neck_metrics(a))
    m.update(jet_metrics(a))
    return m


def montage(pa, pb, out):
    A, B = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
    w, h = A.size
    C = Image.new("RGB", (w * 2 + 12, h), (255, 0, 255))
    C.paste(A, (0, 0))
    C.paste(B, (w + 12, 0))
    C.save(out)
    return out


def diffmap(pa, pb, out, only=None):
    """差异图: 差异>8 的像素画红, 并打印行/列分布 + 包围盒。"""
    a, b = load(pa), load(pb)
    d = np.abs(a - b).max(axis=2)
    m = d > 8
    if only is not None:
        y0, y1, x0, x1 = only
        mm = np.zeros_like(m)
        mm[y0:y1, x0:x1] = m[y0:y1, x0:x1]
        m = mm
    vis = np.stack([np.where(m, 255, a[:, :, 0] // 3),
                    np.where(m, 0, a[:, :, 1] // 3),
                    np.where(m, 0, a[:, :, 2] // 3)], axis=2).astype(np.uint8)
    Image.fromarray(vis).save(out)
    ys, xs = np.nonzero(m)
    info = {"n": int(m.sum()), "out": out}
    if len(ys):
        info["bbox_y"] = [int(ys.min()), int(ys.max())]
        info["bbox_x"] = [int(xs.min()), int(xs.max())]
        hist = np.bincount(ys, minlength=H)
        top = np.argsort(hist)[::-1][:12]
        info["top_rows"] = sorted([[int(r), int(hist[r])] for r in top if hist[r] > 0])
    print(json.dumps(info, ensure_ascii=False))


def series(dirpath, pattern="s*_t*s_a.png", out_csv=None):
    """扫描采样目录, 逐张出指标 + 相邻差分, 打印表。a/b 两张都算(间隔~2s ⇒ 短期噪声带)。"""
    import glob
    import re
    rows = []
    files = glob.glob(os.path.join(dirpath, pattern))
    files += glob.glob(os.path.join(dirpath, pattern.replace("_a.", "_b.")))
    def keyfn(p):
        m = re.search(r"_t(\d+)s_([ab])\.png$", p)
        return (int(m.group(1)), m.group(2)) if m else (10 ** 9, "z")
    files.sort(key=keyfn)
    for f in files:
        m = re.search(r"_t(\d+)s_([ab])\.png$", f)
        if not m:
            continue
        t, ab = int(m.group(1)), m.group(2)
        a = load(f)
        met = upper_metrics(a)
        met.update(lower_metrics(a))
        met.update(mound_surface_metrics(a))
        met.update(neck_metrics(a))
        met.update(jet_metrics(a))
        row = {"t": t, "ab": ab, "file": os.path.basename(f)}
        row.update(met)
        rows.append(row)
    cols = ["t", "ab", "up_dark_px", "surf_y_med", "up_gray_med", "up_gray_std",
            "contour_hf_std", "contour_hf_p95",
            "tex_amp_med", "tex_hf_h", "mound_top_y", "mound_dark_px", "neck_mean",
            "neck_std", "neck_dark_frac", "jet_dark_frac"]
    print("  ".join(f"{c:>15}" for c in cols))
    for r in rows:
        print("  ".join(f"{r.get(c, float('nan'))!s:>15}" for c in cols))
    # 组内 a-vs-b 的指标差(短期噪声带)
    print("\n--- pair a/b deltas (t, d_up_dark, d_surf_y, d_contour_hf, d_tex_amp, d_neck_mean, d_jet) ---")
    i = 0
    while i + 1 < len(rows):
        if rows[i]["ab"] == "a" and rows[i + 1]["ab"] == "b":
            A, B = rows[i], rows[i + 1]
            def g(k):
                va, vb = A.get(k, float("nan")), B.get(k, float("nan"))
                try:
                    return round(float(vb) - float(va), 4)
                except Exception:
                    return float("nan")
            print(A["t"], g("up_dark_px"), g("surf_y_med"), g("contour_hf_std"),
                  g("tex_amp_med"), g("tex_hf_h"), g("mound_top_y"), g("mound_dark_px"),
                  g("neck_mean"), g("neck_dark_frac"), g("jet_dark_frac"))
            i += 2
        else:
            i += 1
    if out_csv:
        import csv
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("csv:", out_csv)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"))
    ap.add_argument("--metrics", metavar="A")
    ap.add_argument("--montage", nargs=2, metavar=("A", "B"))
    ap.add_argument("--diffmap", nargs=2, metavar=("A", "B"))
    ap.add_argument("--series", metavar="DIR")
    ap.add_argument("--pattern", default="s*_t*s_a.png")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--only", default=None, help="y0,y1,x0,x1")
    ap.add_argument("--out")
    ap.add_argument("--label", default="")
    ap.add_argument("--region", default=None)
    args = ap.parse_args()

    if args.series:
        series(args.series, args.pattern, args.csv)
    if args.diffmap:
        only = tuple(int(v) for v in args.only.split(",")) if args.only else None
        diffmap(args.diffmap[0], args.diffmap[1], args.out or "diffmap.png", only)

    if args.metrics:
        m = all_metrics(args.metrics)
        print(json.dumps({"label": args.label, "file": os.path.basename(args.metrics),
                          "m": m}, ensure_ascii=False))
    if args.diff:
        a, b = load(args.diff[0]), load(args.diff[1])
        res = {"label": args.label,
               "A": os.path.basename(args.diff[0]), "B": os.path.basename(args.diff[1]),
               "all_excl_statusbar": diff_stats(a[100:], b[100:]),
               "regions": {k: diff_stats(a, b, r) for k, r in REGIONS.items()}}
        print(json.dumps(res, ensure_ascii=False))
    if args.montage:
        o = args.out or "montage.png"
        montage(args.montage[0], args.montage[1], o)
        print("wrote", o)


if __name__ == "__main__":
    main()
