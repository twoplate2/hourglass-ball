"""
实录音频 → 沙漏音效无缝循环 WAV 导入管线
==========================================
把用户提供的实录 mp3(miniaudio 原生解码,无 ffmpeg 依赖)加工成 APP 需要的资产:

1. 解码:miniaudio.decode_file 直出 44100Hz / 单声道 / SIGNED16(不足时 resample_poly 兜底)
2. 选段:在长文件里滑动窗口挑"首尾最像"的 LOOP_LEN+2×CROSS 片段
   (首尾 1s 频谱余弦相似度 + 响度差 + 窗内响度波动 综合打分,越低越好)
3. 对齐:头/尾 1s 做 ±0.15s 互相关对齐,再 equal-power crossfade 焊死循环点 → 听不出接缝
4. 音量:RMS 对齐 sand_loop.wav × 每音效增益,peak clamp 0.95
5. 落盘:apk/sounds/{name}.wav 与 pc/{name}.wav 字节一致(44100Hz 16bit mono 15s,各 ~1.3MB)

MP3 源文件只留在本机(MP3/ 进 .gitignore),打得进 APK 的只有加工后的 wav。

用法:
    python tools/import_sounds.py                     # 处理 MP3/ 下全部素材
    python tools/import_sounds.py --verify            # 校验已生成文件
"""
import argparse
import gc
import math
import os
import sys
import wave

import numpy as np
from scipy.signal import resample_poly

import miniaudio

SR = 44100
LOOP_LEN = 15.0            # 循环时长(与 sand_loop.wav 一致)
CROSS = 1.0                # 循环点 crossfade 时长(秒)
N = int(SR * LOOP_LEN)
C = int(SR * CROSS)
W = N + 2 * C              # 选段窗口(含首尾各 1s crossfade 原料)
PEAK_LIMIT = 0.95

HERE = os.path.dirname(os.path.abspath(__file__))
APK_DIR = os.path.normpath(os.path.join(HERE, ".."))
SRC_DIR = os.path.join(APK_DIR, "MP3")
APK_SOUNDS = os.path.join(APK_DIR, "sounds")
PC_DIR = os.path.normpath(os.path.join(HERE, "..", ".."))
SAND_REF = os.path.join(APK_DIR, "sand_loop.wav")

# (目标名, 源文件名, RMS 增益)
TARGETS = [
    ("water",  "shuiliu.mp3",    1.00),
    ("wind",   "feng.mp3",       1.10),
    ("clock",  "zhongbiao.mp3",  1.00),
]


# ---------- 解码 ----------

def decode_mono_44k(path):
    ds = miniaudio.decode_file(path, nchannels=1,
                               output_format=miniaudio.SampleFormat.SIGNED16)
    x = np.frombuffer(ds.samples if hasattr(ds.samples, '__buffer__') else bytes(ds.samples),
                      dtype='<i2').astype(np.float64)
    sr = ds.sample_rate
    if sr != SR:
        g = math.gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g)
    del ds
    gc.collect()
    return x


# ---------- 选段 ----------

def rms(a):
    return float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))


def _spectral_similarity(head, tail):
    win = np.hanning(len(head))
    h = np.abs(np.fft.rfft((head - head.mean()) * win))
    t = np.abs(np.fft.rfft((tail - tail.mean()) * win))
    denom = (np.linalg.norm(h) * np.linalg.norm(t)) + 1e-9
    return float(np.dot(h, t) / denom)


def pick_loop_segment(x, loop_len):
    """滑动窗口选首尾最像的片段。返回 (start, 得分)。
    打分 = 频谱相似度 + 响度差 + 窗内响度波动 + 接缝低谷惩罚
    (最后一项让海浪这类慢涌流避免把循环点落在涌浪低谷)。"""
    wnd = loop_len + 2 * C
    assert len(x) >= wnd, f"音频太短: {len(x) / SR:.1f}s < 需要 {(wnd / SR):.1f}s"
    starts = range(0, len(x) - wnd + 1, C // 2)   # 0.5s 步进;短源只剩起点一个候选
    best_s, best_score = 0, float('inf')
    win = 512
    for s in starts:
        head = x[s:s + C]
        tail = x[s + loop_len + C:s + loop_len + 2 * C]
        rh, rt = rms(head), rms(tail)
        sim = _spectral_similarity(head, tail)
        amp_diff = abs(math.log((rt + 1e-9) / (rh + 1e-9)))
        body = x[s:s + wnd]
        body_rms = rms(body)
        seg = body[:len(body) // win * win].reshape(-1, win)
        amp_var = float(np.std(seg.std(axis=1))) / (body_rms + 1e-9)
        seam_dip = max(0.0, body_rms - min(rh, rt)) / (body_rms + 1e-9)
        score = ((1.0 - sim) * 3.0 + amp_diff * 2.0
                 + amp_var * 1.5 + seam_dip * 2.5)
        if score < best_score:
            best_score, best_s = score, s
    return best_s, best_score


def align_tail(x, s, loop_len, head):
    """互相关微调尾段起点(±0.15s),让 crossfade 前的头尾相位最吻合。"""
    base = s + loop_len + C
    sm = int(SR * 0.15)
    lo = max(-sm, -base)                       # 钳到文件边界,防尾部取不满 C
    hi = min(sm, len(x) - (base + C))
    best_lag, best_corr = 0, -np.inf
    for lag in range(lo, hi + 1, 8):
        t = x[base + lag:base + lag + C]
        corr = float(np.dot(head - head.mean(), t - t.mean()))
        if corr > best_corr:
            best_corr, best_lag = corr, lag
    return base + best_lag


def make_loop(x, loop_len, s, tail_start, head):
    """equal-power crossfade 焊死循环点(与 sand_loop.wav 同工艺,改进:尾段相位对齐)。"""
    tail = x[tail_start:tail_start + C]
    loop = x[s:s + loop_len].copy()
    t = (np.arange(C) + 0.5) / C
    a = np.cos(t * (np.pi / 2))
    b = np.sin(t * (np.pi / 2))
    loop[:C] = tail * a + head * b
    return loop


def mild_compress(x, power=0.72):
    """幂律软压缩:压低突发瞬态的 crest factor,让稳态响度更饱满
    (实录素材动态范围大,RMS 直接对齐会被峰值限制压得很小声)。"""
    peak = float(np.abs(x).max())
    if peak <= 1e-9:
        return x
    xn = x / peak
    return np.sign(xn) * np.power(np.abs(xn), power) * peak


def normalize_to(x, target_rms):
    """RMS 目标 + 峰值限制都在 int16 刻度上比较(解码产物是 int16 刻度)。"""
    r = rms(x)
    if not math.isfinite(r) or r <= 0:
        return x * 0.0
    peak = float(np.abs(x).max())
    amp = target_rms / r
    if peak > 0:
        amp = min(amp, (PEAK_LIMIT * 32767.0) / peak)
    return x * amp


def to_int16_bytes(x):
    x = x - float(x.mean())                       # 去直流
    lim = PEAK_LIMIT * 32767.0                    # int16 刻度下的峰值上限
    a = np.round(np.clip(x, -lim, lim))
    return np.clip(a, -32768, 32767).astype('<i2').tobytes()


def _write_wav(path, payload):
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(payload)


# ---------- 主流程 ----------

def build(apk_sounds, pc_dir, src_dir, sand_ref):
    if not os.path.exists(sand_ref):
        print(f"[警告] 未找到音量基准 {sand_ref},使用默认 RMS 2800")
        sand_rms = 2800.0
    else:
        with open(sand_ref, 'rb') as f:
            raw = f.read()
        sand_rms = rms(np.frombuffer(raw[-N * 2:], dtype='<i2'))
    print(f"sand_loop.wav RMS = {sand_rms:.0f}")

    os.makedirs(apk_sounds, exist_ok=True)
    os.makedirs(pc_dir, exist_ok=True)
    for name, fname, gain in TARGETS:
        src = os.path.join(src_dir, fname)
        if not os.path.exists(src):
            print(f"[跳过] {name}: 源文件缺失 {src}")
            continue
        print(f"处理 {name} <- {fname} ...")
        x = decode_mono_44k(src)
        # 循环时长:标准 15s;源不足(N+2C)时整段减 crossfade(如 9s 钟表 → 7s 循环)
        loop_len = N if len(x) >= N + 2 * C else max(int(SR * 2.0), len(x) - 2 * C)
        s, score = pick_loop_segment(x, loop_len)
        head = x[s:s + C]
        tail_start = align_tail(x, s, loop_len, head)
        loop = make_loop(x, loop_len, s, tail_start, head)
        loop = mild_compress(loop)
        loop = normalize_to(loop, sand_rms * gain)
        payload = to_int16_bytes(loop)
        pa = os.path.join(apk_sounds, f"{name}.wav")
        pb = os.path.join(pc_dir, f"{name}.wav")
        _write_wav(pa, payload)
        _write_wav(pb, payload)
        lag_ms = (tail_start - (s + loop_len + C)) / SR * 1000.0
        print(f"  时长 {len(x) / SR:.1f}s | 循环 {loop_len / SR:.1f}s | 起点 {s / SR:.1f}s "
              f"| 得分 {score:.3f} | 对齐 {lag_ms:.0f}ms | "
              f"{len(payload) / 1048576.0:.2f} MB → {pa} / {pb}")
        del x, loop
        gc.collect()
    print("完成")
    return 0


def verify(apk_sounds, pc_dir):
    ok = True
    for name, _, _ in TARGETS:
        pa = os.path.join(apk_sounds, f"{name}.wav")
        pb = os.path.join(pc_dir, f"{name}.wav")
        if not (os.path.exists(pa) and os.path.exists(pb)):
            print(f"[FAIL] {name}: 文件缺失")
            ok = False
            continue
        with open(pa, 'rb') as f:
            da = f.read()
        with open(pb, 'rb') as f:
            db = f.read()
        if da != db:
            print(f"[FAIL] {name}: apk/pc 字节不一致")
            ok = False
            continue
        with wave.open(pa, 'rb') as wf:
            mono = wf.getnchannels() == 1
            bits = wf.getsampwidth() == 2
            rate = wf.getframerate() == SR
            n = wf.getnframes()
            raw = wf.readframes(n)
        smp = np.frombuffer(raw, dtype='<i2').astype(np.float64)
        finite = bool(np.isfinite(smp).all())
        peak = int(np.abs(smp).max())
        peak_ok = peak <= round(PEAK_LIMIT * 32767)
        rms_all = rms(smp)
        rms_cf = rms(smp[:C])
        ratio = rms_cf / (rms_all + 1e-9)
        # 帧数:标准 N(15s);短源允许 ≥4s(如 9s 钟表源 → 7s 循环)
        frames_ok = int(SR * 4) <= n <= N
        if not all([mono, bits, rate, frames_ok, finite, peak_ok, 0.5 < ratio < 1.8]):
            print(f"[FAIL] {name}: mono={mono} bits={bits} rate={rate} frames={n} "
                  f"finite={finite} peak={peak} cf_ratio={ratio:.2f}")
            ok = False
        else:
            print(f"[OK] {name}: {n} 帧 {peak} peak RMS {rms_all:.0f} cf_ratio {ratio:.2f}")
    print("全部通过" if ok else "存在失败项")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="实录 mp3 → 无缝循环 wav 导入")
    ap.add_argument("--src-dir", default=SRC_DIR)
    ap.add_argument("--apk-sounds", default=APK_SOUNDS)
    ap.add_argument("--pc-dir", default=PC_DIR)
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        sys.exit(verify(a.apk_sounds, a.pc_dir))
    sys.exit(build(a.apk_sounds, a.pc_dir, a.src_dir, SAND_REF))


if __name__ == "__main__":
    main()
