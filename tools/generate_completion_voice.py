"""Bake the six-character completion announcement with Microsoft Xiaoxiao."""

import asyncio
import math
from pathlib import Path
import struct
import wave


ROOT = Path(__file__).resolve().parents[1]
TEXT = "\u6c99\u6f0f\u8ba1\u65f6\u5b8c\u6210"
VOICE = "zh-CN-XiaoxiaoNeural"


async def synthesize_speech(path):
    import edge_tts
    import miniaudio

    temporary = path.with_suffix(".tmp.mp3")
    try:
        for attempt in range(3):
            try:
                await edge_tts.Communicate(TEXT, VOICE, rate="+10%").save(str(temporary))
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.8 * (attempt + 1))
        decoded = miniaudio.mp3_read_file_s16(str(temporary))
        samples = list(decoded.samples)
        if decoded.nchannels == 2:
            samples = [(samples[i] + samples[i + 1]) // 2
                       for i in range(0, len(samples), 2)]
        elif decoded.nchannels != 1:
            raise ValueError("Unsupported speech channel count")
        audible = [i for i, value in enumerate(samples) if abs(value) >= 250]
        if not audible:
            raise ValueError("Speech recording is silent")
        pad = round(decoded.sample_rate * 0.03)
        samples = samples[max(0, audible[0] - pad):min(len(samples), audible[-1] + pad + 1)]
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(decoded.sample_rate)
            stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    finally:
        temporary.unlink(missing_ok=True)


async def main():
    output = ROOT / "sounds"
    output.mkdir(exist_ok=True)
    speech_path = output / "completion_voice.wav"
    await synthesize_speech(speech_path)
    with wave.open(str(speech_path), "rb") as stream:
        rate = stream.getframerate()
        channels = stream.getnchannels()
        width = stream.getsampwidth()
        speech = stream.readframes(stream.getnframes())
    if channels != 1 or width != 2:
        raise ValueError("Expected mono PCM16 speech")

    # A quiet, short C-major bell precedes the single spoken sentence.
    chime = []
    notes = ((523.25, 0), (659.25, 0.10), (783.99, 0.20))
    for i in range(round(rate * 0.55)):
        t = i / rate
        value = 0.0
        for frequency, start in notes:
            age = t - start
            if age >= 0:
                envelope = min(1, age / 0.012) * math.exp(-age * 12)
                value += math.sin(math.tau * frequency * age) * envelope * 0.075
        value *= min(1, (0.55 - t) / 0.05)
        chime.append(round(value * 32767))
    pcm = struct.pack(f"<{len(chime)}h", *chime) + b"\0\0" * round(rate * 0.12) + speech
    path = output / "completion.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm)
    print(f"Voice: {VOICE}; text: {ascii(TEXT)}")
    print(f"Speech: {speech_path}; {len(speech) / (rate * 2):.3f}s")
    print(f"Completion: {path}; {len(pcm) / (rate * 2):.3f}s")


if __name__ == "__main__":
    asyncio.run(main())
