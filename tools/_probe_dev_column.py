# -*- coding: utf-8 -*-
"""量**设备帧**(1080×1920 之类)里"出口以下那根柱子"的四个量 —— 判"实心/纯色"用。

为什么不能拿桌面渲染代替: 本项目反复栽在"桌面看不见 ≠ 设备看不见"(柱宽随窗口变 ⇒
同一个 rate 在桌面密、在设备疏)。设备帧是**无压缩 PNG**(`adb exec-out screencap -p`),
所以可以直接量, **不要拿 mp4 的抽帧量**(编码器码率呼吸会把 1~2px 的细节糊掉)。

四个量(与 `_probe_n1_tone.py` 同源, 但窗口按"实测找柱"而不是写死):
  1. `柱宽`(行内沙色像素的外跨度中位) —— 与管径比, 判"有没有超出瓶颈"
  2. `墨/行`(行内沙色像素数中位) —— 判"实心"
  3. `行内高频 std`(去趋势后) —— 判"有没有颗粒"
  4. `沙像素亮度 均值/std/不同色数` —— 判"纯色"(不同色数少 = 一块平色)

跑法:
    python tools/_probe_dev_column.py benchmark_logs/_dev/mumu294/m_45.png ...
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

BG = np.array([253.0, 246.0, 227.0])       # #fdf6e3
GLASS = np.array([234.0, 243.0, 248.0])    # #eaf3f8
LW = np.array([0.299, 0.587, 0.114])


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), dtype=float)


def sand_ref(X):
    """沙的代表色: 从**下球沙堆内部**自标定(那里永远是沙), 与配色无关。"""
    H, W = X.shape[:2]
    return X[int(H * 0.80):int(H * 0.86), int(W * 0.42):int(W * 0.58)].reshape(-1, 3).mean(axis=0)


def mask(X, ref):
    refs = np.stack([ref, BG, GLASS])
    d = np.abs(X[:, :, None, :] - refs[None, None, :, :]).max(axis=3)
    return d.argmin(axis=2) == 0


def hp_std(seg, k=5):
    hp = seg - np.convolve(seg, np.ones(k) / k, "same")
    return float(hp[k // 2:-(k // 2)].std())


def analyze(p):
    X = load(p)
    H, W = X.shape[:2]
    ref = sand_ref(X)
    M = mask(X, ref)
    cx = W // 2
    rows = []
    for y in range(int(H * 0.30), int(H * 0.95)):
        xs = np.nonzero(M[y])[0]
        if xs.size < 4:
            continue
        # 只认"贴着中轴那一撮" = 柱子; 撒开的飞溅不参与
        near = xs[np.abs(xs - cx) < W * 0.16]
        if near.size < 4:
            continue
        span = int(near[-1] - near[0] + 1)
        # ⚠️ 门槛要**紧**: 0.35·W(=378px)会把上球沙体下缘那段"球在收窄"的行也放进来
        #    (实测就是这么把"柱宽 345px"量出来的 —— 量的其实是球)。真柱子在 1080 口径只有
        #    ~40px、平板口径 ~82px ⇒ 取 0.12·W(=130px) 既容得下加宽也不放球进来。
        if span <= 0.12 * W:
            rows.append((y, int(near[0]), int(near[-1]), span, int(near.size)))
    if len(rows) < 10:
        return None
    # 取最长连续段 = 出口以下那根(上球的沙面不算: 它不在中轴细柱判据里)
    best, cur = [], [rows[0]]
    for a, b in zip(rows, rows[1:]):
        if b[0] - a[0] <= 2:
            cur.append(b)
        else:
            best = cur if len(cur) > len(best) else best
            cur = [b]
    best = cur if len(cur) > len(best) else best
    spans = np.array([r[3] for r in best])
    inks = np.array([r[4] for r in best])
    stds, lum = [], []
    for y, x0, x1, _s, _n in best:
        seg = X[y, x0:x1 + 1] @ LW
        stds.append(hp_std(seg))
        lum.extend((X[y, x0:x1 + 1] @ LW).tolist())
    lum = np.array(lum)
    cols = {tuple(v) for v in X[np.ix_([r[0] for r in best],
                                       [r[1] for r in best])].reshape(-1, 3).astype(int).tolist()}
    # 不同色数用整段柱带统计(更稳)
    ys = np.array([r[0] for r in best]); x0s = np.array([r[1] for r in best]); x1s = np.array([r[2] for r in best])
    band = np.concatenate([X[y, a:b + 1] for y, a, b in zip(ys, x0s, x1s)])
    uniq = len(np.unique(band.astype(np.uint8), axis=0))
    return dict(rows=len(best), y=(int(ys[0]), int(ys[-1])),
                span=int(np.median(spans)), ink=float(np.median(inks)),
                std=float(np.median(stds)), lum_mean=float(lum.mean()),
                lum_std=float(lum.std()),
                p5=float(np.percentile(lum, 5)), p95=float(np.percentile(lum, 95)),
                colors=uniq)


def main():
    for p in sys.argv[1:]:
        r = analyze(p)
        if r is None:
            print("  %-28s 找不到柱带(不作数)" % Path(p).name); continue
        print("  %-18s 柱带 y=%d..%d(%d 行) | 宽 %3dpx | 墨/行 %5.1f | 行内std %5.2f | "
              "亮度 %.1f±%.1f (p5 %.0f p95 %.0f) | 不同色 %5d"
              % (Path(p).name, r["y"][0], r["y"][1], r["rows"], r["span"], r["ink"],
                 r["std"], r["lum_mean"], r["lum_std"], r["p5"], r["p95"], r["colors"]))


if __name__ == "__main__":
    main()
