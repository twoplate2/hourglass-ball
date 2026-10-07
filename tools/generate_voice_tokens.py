"""Bake the completion announcement as splicable word tokens (Microsoft Xiaoxiao).

The completion sentence depends on the timer length ("两小时三十分零五秒的沙漏计时完成"),
so it cannot be pre-recorded as one clip.  Instead every word that can appear is baked
once into sounds/voice/, and main.py concatenates the PCM at the moment the sand runs
out -- one buffer, one AudioTrack, no gaps.

Tokens are normalised to a common RMS so spliced sentences have no loud/quiet steps.
"""

import asyncio
from pathlib import Path
import struct
import wave

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VOICE = "zh-CN-XiaoxiaoNeural"
TARGET_RMS = 2400.0          # 16bit 满量程 32767, 语音取 ~7% 作统一响度
SILENCE_FLOOR = 250
PAD_SEC = 0.025

DIGITS = "零一二三四五六七八九"


def _numbers():
    # 0..60 整词(分/秒 全域 + 小时 1..60); >60 的小时由数字合成
    for n in range(61):
        if n < 10:
            yield f"n{n}", DIGITS[n]
        elif n < 20:
            yield f"n{n}", ("十" if n == 10 else "十" + DIGITS[n % 10])
        else:
            yield f"n{n}", DIGITS[n // 10] + "十" + (DIGITS[n % 10] if n % 10 else "")


def _tokens():
    yield from _numbers()
    for d in range(1, 10):
        yield f"d{d}", DIGITS[d]
    yield "ten", "十"
    yield "hundred", "百"
    yield "hour", "小时"
    yield "min", "分"
    yield "sec", "秒"
    yield "tail", "的沙漏计时完成"
    # ---- 操作提示音(2026-10-07): 换沙色 / 改周期 / 换提示音 各播一句 ----
    # 🔴 **`c*` 与 `SAND_PRESETS` 同序、`e*` 与 `SOUND_OPTIONS` 同序** ——
    #    改那两张表的顺序**必须重生这批词块**, 否则会念错颜色/音效名
    #    (与 pc 版 `hourglass_v4.py` 的 `_say_color`/`_say_effect` 同一条约定)。
    for i, name in enumerate(("金沙", "红沙", "蓝沙", "绿沙", "紫沙", "黑沙")):
        yield f"c{i}", name
    for i, name in enumerate(("沙沙声", "水流声", "风声", "钟表声", "无声音")):
        yield f"e{i}", name
    # 前缀整句念, 比把「计时」「时间」「设定为」拼起来自然(拼接会有顿挫)
    yield "pre_time", "计时时间设定为"
    yield "pre_sound", "提示音设定为"
    # 🔴 选「无声音」时**不**说"设定为无声音"(绕), 用户 2026-10-07 定: 直接说这句
    yield "pre_silent", "提示音改为无声"


async def _speak(text, path):
    import edge_tts
    import miniaudio

    temporary = path.with_suffix(".tmp.mp3")
    try:
        for attempt in range(4):
            try:
                await edge_tts.Communicate(text, VOICE, rate="+10%").save(str(temporary))
                break
            except Exception:
                if attempt == 3:
                    raise
                await asyncio.sleep(0.8 * (attempt + 1))
        decoded = miniaudio.mp3_read_file_s16(str(temporary))
        samples = np.fromiter(decoded.samples, dtype=np.int16)
        if decoded.nchannels == 2:
            samples = samples.reshape(-1, 2).mean(axis=1).astype(np.int16)
        elif decoded.nchannels != 1:
            raise ValueError("Unsupported speech channel count")

        audible = np.flatnonzero(np.abs(samples) >= SILENCE_FLOOR)
        if not len(audible):
            raise ValueError("Speech recording is silent")
        pad = round(decoded.sample_rate * PAD_SEC)
        samples = samples[max(0, audible[0] - pad):min(len(samples), audible[-1] + pad + 1)]

        body = samples[pad:len(samples) - pad] if len(samples) > 2 * pad else samples
        rms = float(np.sqrt(np.mean(body.astype(np.float64) ** 2))) if len(body) else 0.0
        if rms > 1.0:
            samples = np.clip(
                samples.astype(np.float64) * (TARGET_RMS / rms), -32768, 32767
            ).astype(np.int16)

        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(decoded.sample_rate)
            stream.writeframes(samples.astype("<i2").tobytes())
        return decoded.sample_rate, len(samples)
    finally:
        temporary.unlink(missing_ok=True)


async def main():
    output = ROOT / "sounds" / "voice"
    output.mkdir(parents=True, exist_ok=True)
    rates, total = set(), 0
    for key, text in _tokens():
        rate, count = await _speak(text, output / f"{key}.wav")
        rates.add(rate)
        total += count
        print(f"  {key:<8} {text:<8} {count / rate:.3f}s")
    if len(rates) != 1:
        raise SystemExit(f"Sample rates differ across tokens: {sorted(rates)}")
    rate = rates.pop()
    print(f"\n{len(list(_tokens()))} tokens @ {rate}Hz, "
          f"{total / rate:.2f}s of speech, {total * 2 / 1024:.0f} KiB PCM")


if __name__ == "__main__":
    asyncio.run(main())
