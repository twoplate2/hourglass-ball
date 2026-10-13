"""Prepare real revision-review clips and contact sheets, without testing the app."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest_path.relative_to(ROOT)
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    media = manifest_path.parent
    version = source["source_version"]
    session = media.name.removeprefix("v" + version + "-")
    images = ROOT / "design/images" / ("mumu-" + version + "-" + session[:8]) / ("session-" + session)
    review = images / "review"
    review.mkdir(exist_ok=False)
    archive = ROOT / "_temp/mumu-media" / session
    archive.mkdir(parents=True, exist_ok=True)
    start = source["cycle_start_offset"]
    results = {"source_manifest": manifest_path.relative_to(ROOT).as_posix(),
               "source_manifest_sha256": digest(manifest_path), "files": [],
               "created_at": datetime.now().astimezone().isoformat(),
               "note": "Real frames only. Video timestamps are not app elapsed readings.",
               "deployment_mode": source.get("deployment_mode"),
               "source_version": version, "package_version": source["package_version"]}

    def relative(path):
        return path.relative_to(ROOT).as_posix()

    def register(path):
        item = {"path": relative(path), "sha256": digest(path)}
        results["files"].append(item)
        return item

    def ffmpeg(*command):
        subprocess.run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-n", *command], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

    def encode(input_path, name, offset=None, length=None, crop=None):
        output = media / (name + ".mp4")
        command = ["-ss", str(offset)] if offset is not None else []
        command += ["-i", str(input_path)]
        if length is not None:
            command += ["-t", str(length)]
        if crop:
            x, y, width, height = crop
            command += ["-vf", "crop=%d:%d:%d:%d" % (width, height, x, y)]
        command += ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-threads", "2", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
                    "-movflags", "+faststart", str(output)]
        ffmpeg(*command)
        register(output)
        if crop:
            poster = review / (name + "-poster.png")
            ffmpeg("-i", str(output), "-frames:v", "1", str(poster))
            register(poster)
        print("REVIEW_VIDEO " + relative(output), flush=True)
        return output

    raw = media / "full-cycle.mp4"
    encode(raw, "full-cycle-view")
    encode(media / "pause-resume.mp4", "pause-resume-view")
    encode(raw, "opening", start, 4.0)
    encode(raw, "outlet-loop", start + 40, 7, (460, 950, 160, 300))
    encode(raw, "edge-loop", start + 14, 6, (470, 1060, 140, 300))

    def contact_sheet(name, offsets, crop, columns):
        x, y, width, height = crop
        sheet = Image.new("RGB", (columns * width, ((len(offsets) + columns - 1) // columns)
                                  * (height + 24)), "white")
        draw = ImageDraw.Draw(sheet)
        frame_dir = archive / name
        frame_dir.mkdir(exist_ok=False)
        for i, offset in enumerate(offsets):
            frame = frame_dir / ("%02d.png" % i)
            ffmpeg("-ss", str(offset), "-i", str(raw), "-vf",
                   "crop=%d:%d:%d:%d" % (width, height, x, y),
                   "-frames:v", "1", str(frame))
            left, top = (i % columns) * width, (i // columns) * (height + 24)
            with Image.open(frame) as image:
                sheet.paste(image.convert("RGB"), (left, top + 24))
            draw.text((left + 3, top + 5), "video %.2fs" % offset, fill="black")
        output = review / (name + ".png")
        sheet.save(output)
        register(output)
        print("REVIEW_IMAGE " + relative(output), flush=True)

    contact_sheet("first-landing", [start + 1.1 + i * 0.1 for i in range(12)],
                  (490, 1010, 100, 730), 6)
    contact_sheet("outlet-late", [start + 40 + i for i in range(6)],
                  (470, 950, 140, 260), 6)
    contact_sheet("stream-edge", [start + 15 + i * 0.25 for i in range(8)],
                  (485, 1070, 110, 280), 4)
    for name in ("full-cycle", "pause-resume"):
        old = media / (name + ".mp4")
        destination = archive / (name + "-raw.mp4")
        old.resolve().relative_to(ROOT.resolve())
        destination.resolve().relative_to((ROOT / "_temp").resolve())
        if destination.exists():
            raise ValueError("Refusing to overwrite the raw archive")
        item = register(old)
        old.replace(destination)
        item.update(path=relative(destination), captured_path=relative(old), raw=True)
    path = media / "review-assets.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("REVIEW_ASSETS " + relative(path), flush=True)


if __name__ == "__main__":
    main()
