"""Record real, hidden GL frames against wall time; no interpolated sand frames."""
import hashlib
import json
import subprocess
import time


def validate_recording(report):
    rows = report.get("frames", [])
    if len(rows) < 300 or report.get("duration", 0) < 59:
        raise ValueError("Incomplete full-cycle recording")
    if any(b["time"] <= a["time"] for a, b in zip(rows, rows[1:])):
        raise ValueError("Non-monotonic recording time")
    moving = [r for r in rows if r["running"] and 3 < r["elapsed"] < 40]
    if len({r["sha256"] for r in moving}) < 60:
        raise ValueError("Canvas was not refreshed during recording")
    paused = [r for r in rows if 12.2 < r["time"] < 14.8]
    if not paused or any(r["running"] for r in paused) or len({r["sha256"] for r in paused}) != 1:
        raise ValueError("Pause is missing or changes the rendered image")
    if not report.get("ended_settled"):
        raise ValueError("Recording ended before settling")


def record(widget, output, advance, capture, hidden_check):
    import imageio_ffmpeg
    frames = output / "video-frames"
    frames.mkdir()
    started = time.perf_counter()
    rows = []
    paused = resumed = False
    while True:
        now = time.perf_counter() - started
        advance(now)
        if now >= 12 and not paused:
            widget.toggle()
            paused = True
        if now >= 15 and not resumed:
            widget.toggle()
            resumed = True
        hidden = hidden_check()
        path = frames / ("%05d.png" % len(rows))
        widget.redraw()
        capture(path)
        rows.append({"time": now, "elapsed": widget.elapsed, "running": bool(widget.running),
                     "particles": widget.pn, "splashes": widget._sn,
                     "hidden": hidden, "file": path.name,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if now >= 60:
            break
        time.sleep(max(0, 1 / 30 - (time.perf_counter() - started - now)))
    # Variable timestamps preserve actual wall duration even if capture is slow.
    manifest = frames / "frames.ffconcat"
    lines = ["ffconcat version 1.0"]
    for i, row in enumerate(rows):
        duration = rows[i + 1]["time"] - row["time"] if i + 1 < len(rows) else 1 / 30
        lines.extend(["file '%s'" % row["file"], "duration %.9f" % duration])
    lines.append("file '%s'" % rows[-1]["file"])
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    movie = output / "full-cycle.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-y", "-f", "concat", "-safe", "0", "-i", str(manifest.resolve()),
        "-vsync", "vfr", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(movie.resolve())], check=True)
    report = {"completed": True, "scope": "wall-clock visual recording; no FPS/audio acceptance",
              "frames": rows, "duration": rows[-1]["time"], "pause_at": 12, "resume_at": 15,
              "movie": movie.name, "sha256": hashlib.sha256(movie.read_bytes()).hexdigest(),
              "ended_settled": widget.pn == 0 and widget._sn == 0 and not widget.running}
    validate_recording(report)
    (output / "video.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {key: value for key, value in report.items() if key != "frames"}
