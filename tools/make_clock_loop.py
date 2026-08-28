"""从 mp3/zhongbiao.mp3 生成节奏正确、接缝自然的钟表循环 sounds/clock.wav。

⚠️ 钟表和噪声类音效(沙沙/水流/风)**不能用同一套加工流程**:
  - 噪声只要求接缝电平/频谱连续,任意长度 crossfade 都行;
  - 钟表是**有拍子**且**大部分时间是静音**的,要同时满足三件事,缺一个都会"衔接不自然"。

本脚本要同时保证的三件事:

1. **节奏**:循环长度必须是整数个"滴答对"(源是滴/答强弱交替,跨奇数拍绕回后强弱会翻转),
   且两端切点到各自下一拍的距离必须**完全相同** —— 用同一个 offset 切两端,
   回绕间隔就恒等于源里的天然拍距,节奏误差**恒为 0**。
   踩坑:6s 版回绕比拍距短 10.8%(实为阈值误测,真值 +3.7%),7s 版短 46.6%(抢半拍)。

2. **底噪**:切点必须落在**拍间静音谷**。固定偏移(如"拍前 60ms")靠不住 —— 实测全曲
   拍前 60ms 窗的 RMS 在 0.00005~0.0025 之间摆动 34dB(有些拍前 60ms 还在上一拍余韵里),
   而拍间真正的谷底处处一致(~0.00003)。所以要**搜索 offset**,让两端同时落在谷底。
   踩坑:上一版固定 60ms,起点窗 0.000988 vs 终点窗 0.000073,失配 -22.7dB,
   每绕一圈底噪跳一下 → 用户报"衔接不够自然"。

3. **别切进 MP3 编码器延迟**:解码出来的头 ~30ms 是**纯数字静音**(peak 恰为 0)。
   切在那里等于把上一拍余韵淡入绝对静音,再跳回正常底噪。用 GUARD 躲开。

另外**不做压缩**:钟表靠瞬态,压扁了强弱交替就没了(6s 版 crest 26.8→16.9 就是这么坏的),
只做峰值归一化到 PEAK_TARGET。

用法: python tools/make_clock_loop.py   (需 miniaudio + numpy;源 mp3 仅本机,不入库)
"""
import os
import sys
import wave

import numpy as np

# Windows 控制台默认 GBK, 装不下 ⟹ 等字符 → print 崩 Exit 1(wav 其实已写入)。
# 强制 stdout 用 UTF-8, 全平台中文/符号都正常打印。
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

try:
    import miniaudio
except ImportError:
    sys.exit("需要 miniaudio: pip install miniaudio")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "mp3", "zhongbiao.mp3")
DST = os.path.join(ROOT, "sounds", "clock.wav")

SR = 48000              # 与其它音效一致(Android 设备原生率, 见 CLAUDE.md)
XFADE = 0.012           # crossfade 长度(秒)。谷底很安静, 12ms 够用且不吃余韵
GUARD = 0.080           # 起点必须晚于此(躲开 MP3 编码器延迟的数字静音)
OFF_MIN, OFF_MAX = 0.020, 0.130   # 切点到下一拍的距离, 搜索范围(秒)
OFF_STEP = 0.002
FLOOR_MULT = 4.0        # 切点窗 RMS 允许高出全曲底噪的倍数
MISMATCH_DB = 4.0       # 两端底噪允许的失配上限
PEAK_TARGET = 0.90      # 峰值归一化目标(不压缩, 保住瞬态)
ONSET_RATIO = 0.12      # 包络峰值的多少倍算一次滴答(要能抓到弱拍"答")
MIN_GAP = 0.15          # 两次滴答的最小间隔(去重)


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


def pick_cut(a, on, strong_parity, xf_n, floor):
    """搜 (i, j, offset):同奇偶强拍 + 跨偶数拍 + 两端同 offset(节奏误差恒 0)
    + 两端窗都在底噪级且互相匹配 → 在此前提下取最长。返回 (i, j, off_n)。"""
    guard_n = int(GUARD * SR)
    limit = floor * FLOOR_MULT
    best = None
    for i in range(1, len(on)):
        if i % 2 != strong_parity:
            continue
        for j in range(i + 2, len(on)):
            if (j - i) % 2:
                continue
            for off_n in range(int(OFF_MIN * SR), int(OFF_MAX * SR), int(OFF_STEP * SR)):
                c0, c1 = on[i] - off_n, on[j] - off_n
                if c0 < guard_n or c1 + xf_n > len(a):
                    continue
                r0 = np.sqrt((a[c0:c0 + xf_n] ** 2).mean())
                r1 = np.sqrt((a[c1:c1 + xf_n] ** 2).mean())
                if max(r0, r1) > limit:
                    continue
                if abs(20 * np.log10(max(r1, 1e-12) / max(r0, 1e-12))) > MISMATCH_DB:
                    continue
                length = c1 - c0
                if best is None or length > best[0]:
                    best = (length, i, j, off_n, r0, r1)
    return best


def main():
    if not os.path.exists(SRC):
        sys.exit(f"源文件不存在: {SRC}(源 mp3 仅本机保存, 已 .gitignore)")
    dec = miniaudio.decode_file(SRC, output_format=miniaudio.SampleFormat.SIGNED16,
                                nchannels=1, sample_rate=SR)
    a = np.array(dec.samples, dtype=np.float64) / 32768.0
    on, env = find_onsets(a, SR)
    if len(on) < 6:
        sys.exit(f"只检测到 {len(on)} 次滴答, 无法按拍切")

    amps = np.array([env[max(0, i - 200):i + 600].max() for i in on])
    strong_parity = 0 if amps[::2].mean() >= amps[1::2].mean() else 1

    xf_n = int(XFADE * SR)
    # 全曲底噪 = 12ms 窗 RMS 的 5 分位(拍间谷底处处一致, 用它当"够安静"的标尺)
    step = max(1, xf_n // 2)
    all_rms = np.array([np.sqrt((a[p:p + xf_n] ** 2).mean())
                        for p in range(0, len(a) - xf_n, step)])
    floor = np.percentile(all_rms, 5)

    best = pick_cut(a, on, strong_parity, xf_n, floor)
    if best is None:
        sys.exit("找不到同时满足[零节奏误差 + 两端落谷底 + 底噪匹配]的切法, "
                 "请放宽 FLOOR_MULT / MISMATCH_DB 后重试")
    length_n, i, j, off_n, r0, r1 = best
    c0, c1 = on[i] - off_n, on[j] - off_n

    seg = a[c0:c1].copy()
    # 等功率 crossfade:段首混入"段尾之后的同相位素材" → 绕回时波形连续
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
          f"{amps[::2].mean()/amps[1::2].mean():.2f}, 底噪(5分位)={floor:.6f}")
    print(f"切点: onset[{i}]→onset[{j}] = {ticks} 拍 = {ticks//2} 个滴答对, "
          f"offset={off_n/SR*1000:.1f}ms(两端同值 ⟹ 节奏误差 0)")
    print(f"两端窗 RMS: {r0:.6f} / {r1:.6f} "
          f"(失配 {20*np.log10(max(r1,1e-12)/max(r0,1e-12)):+.2f} dB, "
          f"底噪的 {max(r0,r1)/floor:.1f} 倍)")
    print(f"输出 {DST}: {len(seg)/SR:.4f}s  峰值={np.abs(seg).max():.3f}  "
          f"rms={np.sqrt((seg*seg).mean()):.4f}  "
          f"crest={np.abs(seg).max()/np.sqrt((seg*seg).mean()):.1f}")


if __name__ == "__main__":
    main()
