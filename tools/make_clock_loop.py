"""从 mp3/zhongbiao.mp3 生成节奏正确的钟表循环 sounds/clock.wav。

⚠️ 钟表和噪声类音效(沙沙/水流/风)**不能用同一套加工流程**:
  - 噪声只要求接缝电平/频谱连续,任意长度 crossfade 都行;
  - 钟表是**有拍子的**,循环长度必须是整数个"滴答对",否则每绕一圈就抢/拖一拍。
    历史教训:6s 版回绕间隔比拍距短 10.8%,7s 版短 46.6%(抢半拍),都是随便切的结果。

本脚本的做法(按拍切,不是按秒切):
  1. 包络峰值检测出所有滴答时刻 t[0..n)
  2. 源是**滴/答强弱交替**(强 ≈ 2.5x 弱),所以起止都取"强拍",且跨过**偶数**个滴答
     → 循环含整数个滴答对,绕回后强弱不会翻转
  3. 切点取 `t[i] - PRE`(滴答前的静音里),长度 = t[j] - t[i] = 天然拍距之和
     → 回绕间隔就是源里 t[j-1]→t[j] 的真实间隔,零系统误差
  4. crossfade 只有 XFADE 长且整段落在静音里 → 听不见,也不会糊掉瞬态
  5. **不做压缩**:钟表靠瞬态,压扁了强弱交替就没了(6s 版 crest 26.8→16.9 就是这么坏的)。
     只做峰值归一化到 PEAK_TARGET。

用法: python tools/make_clock_loop.py   (需 miniaudio + numpy;源 mp3 仅本机,不入库)
"""
import os
import sys
import wave

import numpy as np

try:
    import miniaudio
except ImportError:
    sys.exit("需要 miniaudio: pip install miniaudio")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "mp3", "zhongbiao.mp3")
DST = os.path.join(ROOT, "sounds", "clock.wav")

SR = 48000            # 与其它音效一致(Android 设备原生率, 见 CLAUDE.md)
PRE = 0.060           # 切点落在滴答前多少秒的静音里
XFADE = 0.040         # crossfade 长度, 必须 < PRE 才能整段待在静音里
PEAK_TARGET = 0.90    # 峰值归一化目标(不压缩, 保住瞬态)
ONSET_RATIO = 0.12    # 包络峰值的多少倍算一次滴答(要能抓到弱拍"答")
MIN_GAP = 0.15        # 两次滴答的最小间隔(去重)


def envelope(a, sr):
    win = int(sr * 0.005)
    return np.sqrt(np.convolve(a * a, np.ones(win) / win, mode="same"))


def find_onsets(a, sr):
    env = envelope(a, sr)
    above = env > env.max() * ONSET_RATIO
    idx = np.flatnonzero(above[1:] & ~above[:-1])
    out = []
    for i in idx:
        if not out or (i - out[-1]) / sr >= MIN_GAP:
            out.append(int(i))
    return np.array(out), env


def main():
    if not os.path.exists(SRC):
        sys.exit(f"源文件不存在: {SRC}(源 mp3 仅本机保存, 已 .gitignore)")
    dec = miniaudio.decode_file(SRC, output_format=miniaudio.SampleFormat.SIGNED16,
                                nchannels=1, sample_rate=SR)
    a = np.array(dec.samples, dtype=np.float64) / 32768.0
    on, env = find_onsets(a, SR)
    if len(on) < 4:
        sys.exit(f"只检测到 {len(on)} 次滴答, 无法按拍切")

    amps = np.array([env[max(0, i - 200):i + 600].max() for i in on])
    # 强拍在偶数还是奇数位?(源是强/弱交替)
    strong_first = amps[::2].mean() >= amps[1::2].mean()
    start_parity = 0 if strong_first else 1

    pre_n, xf_n = int(PRE * SR), int(XFADE * SR)
    # i = 第一个"强拍"且前面留得出 PRE; j = 最靠后的同奇偶强拍(⟹ 跨偶数个滴答),
    # 且 j 之后还要留得出 crossfade 用的素材
    cand_i = [k for k in range(len(on)) if k % 2 == start_parity and on[k] - pre_n >= 0]
    cand_j = [k for k in range(len(on)) if k % 2 == start_parity
              and on[k] - pre_n + xf_n <= len(a)]
    if not cand_i or not cand_j or cand_j[-1] <= cand_i[0]:
        sys.exit("找不到合法的按拍切点")
    i, j = cand_i[0], cand_j[-1]

    c0, c1 = on[i] - pre_n, on[j] - pre_n
    seg = a[c0:c1].copy()
    # 等功率 crossfade: 段首与"段尾之后的同相位素材"混合 → 绕回时波形连续
    w = np.linspace(0, np.pi / 2, xf_n)
    seg[:xf_n] = seg[:xf_n] * np.sin(w) + a[c1:c1 + xf_n] * np.cos(w)

    peak = np.abs(seg).max()
    if peak > 0:
        seg *= PEAK_TARGET / peak

    pcm = np.clip(np.round(seg * 32767), -32768, 32767).astype("<i2")
    with wave.open(DST, "wb") as w_out:
        w_out.setnchannels(1)
        w_out.setsampwidth(2)
        w_out.setframerate(SR)
        w_out.writeframes(pcm.tobytes())

    ticks = j - i
    print(f"源 {len(a)/SR:.3f}s, 检出 {len(on)} 次滴答, 强弱比 "
          f"{amps[::2].mean()/amps[1::2].mean():.2f}")
    print(f"按拍切: onset[{i}]→onset[{j}] = {ticks} 拍 = {ticks//2} 个滴答对")
    print(f"输出 {DST}: {len(seg)/SR:.4f}s  峰值={np.abs(seg).max():.3f}  "
          f"rms={np.sqrt((seg*seg).mean()):.4f}  "
          f"crest={np.abs(seg).max()/np.sqrt((seg*seg).mean()):.1f}")


if __name__ == "__main__":
    main()
