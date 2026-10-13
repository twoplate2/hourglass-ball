"""Capture documentation media on the reserved MuMu; never starts the PC app."""

import argparse
import ast
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SERIAL = "127.0.0.1:16416"
PACKAGE = "org.shalou.hourglass"
DEVICE_APP = "/data/data/%s/files/app" % PACKAGE


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--apk", required=True)
    parser.add_argument("--version", default="2.96")
    parser.add_argument("--overlay", type=Path,
                        help="Source deployment receipt; recorded separately from APK identity")
    args = parser.parse_args()

    def adb(*command, binary=False):
        return subprocess.check_output(
            [args.adb, "-s", SERIAL, *command],
            text=not binary, encoding=None if binary else "utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW)

    package_info = adb("shell", "dumpsys", "package", PACKAGE)
    match = re.search(r"versionName=([^\s]+)", package_info)
    if not match:
        raise RuntimeError("Installed APK version is missing")
    package_version = match[1]
    if not args.overlay and package_version != args.version:
        raise RuntimeError("The installed APK version does not match the requested version")
    focus = adb("shell", "dumpsys", "window")
    if not any("mCurrentFocus=" in line and PACKAGE in line for line in focus.splitlines()):
        raise RuntimeError("The hourglass app is not focused inside the reserved instance")
    if "1080x1920" not in adb("shell", "wm", "size"):
        raise RuntimeError("Capture coordinates require 1080x1920")
    config = json.loads(adb("shell", "cat",
                           "/data/data/%s/files/.hourglass_config.json" % PACKAGE))
    if config.get("duration") != 50 or config.get("color_name") != "\u91d1\u6c99":
        raise RuntimeError("This session requires the existing 50-second gold config")
    apk = Path(args.apk).resolve()
    apk_hash = digest(apk)
    package_path = adb("shell", "pm", "path", PACKAGE).strip().removeprefix("package:")
    if adb("shell", "sha256sum", package_path).split()[0] != apk_hash:
        raise RuntimeError("Installed APK does not match the downloaded APK")
    with zipfile.ZipFile(apk) as archive:
        with tarfile.open(fileobj=io.BytesIO(archive.read("assets/private.tar"))) as private:
            runtime = [{"path": member.name,
                        "sha256": hashlib.sha256(private.extractfile(member).read()).hexdigest()}
                       for member in private.getmembers()
                       if member.isfile() and member.name.endswith(".pyc")]
    if args.overlay:
        deployed = json.loads(args.overlay.read_text(encoding="utf-8"))
        if deployed["serial"] != SERIAL:
            raise RuntimeError("Overlay receipt belongs to another instance")
        runtime = deployed["files"]
        hashes = adb("shell", "sha256sum", *(item["target"] for item in runtime))
        current_hashes = {line.split(None, 1)[1].strip(): line.split()[0]
                          for line in hashes.splitlines()}
        if any(current_hashes.get(item["target"]) != item["sha256"] for item in runtime):
            raise RuntimeError("Deployed source/resources changed since receipt")
        text = adb("shell", "cat", DEVICE_APP + "/src/app/app_version.py")
        versions = [ast.literal_eval(node.value) for node in ast.parse(text).body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "APP_VERSION"
                            for target in node.targets)]
        if versions != [args.version]:
            raise RuntimeError("Source version does not match requested capture version")
        if adb("shell", "sh", "-c",
               "'if [ -f " + DEVICE_APP + "/main.pyc ]; then echo present; fi'").strip():
            raise RuntimeError("Old main.pyc can shadow the source overlay")
    else:
        hashes = adb("shell", "sha256sum", *(DEVICE_APP + "/" + item["path"] for item in runtime))
        current_hashes = dict(line.split(None, 1)[::-1] for line in hashes.splitlines())
        if not all(current_hashes.get(DEVICE_APP + "/" + item["path"]) == item["sha256"]
                   for item in runtime):
            raise RuntimeError("Extracted runtime code differs from APK; refusing to record")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    media = ROOT / "design/media" / ("v" + args.version + "-" + stamp)
    images = ROOT / "design/images" / ("mumu-" + args.version + "-" + stamp[:8]) / ("session-" + stamp)
    media.mkdir(parents=True, exist_ok=False)
    images.mkdir(parents=True, exist_ok=False)
    manifest = {
        "started_at": datetime.now().astimezone().isoformat(),
        "serial": SERIAL, "source_version": args.version,
        "package_version": package_version, "package_info": package_info,
        "apk_sha256": apk_hash, "runtime_files": runtime,
        "runtime_matches_apk": not bool(args.overlay),
        "deployment_mode": "source-overlay" if args.overlay else "apk-install",
        "overlay_receipt": str(args.overlay) if args.overlay else None,
        "overlay_receipt_sha256": digest(args.overlay) if args.overlay else None,
        "apk_download_url": "https://github.com/twoplate2/hourglass-ball/releases/download/"
                            "latest/hourglass-2.96-arm64-v8a-debug.apk",
        "release_commit": "7ee12ad",
        "config": config, "size": adb("shell", "wm", "size").strip(),
        "density": adb("shell", "wm", "density").strip(),
        "audio_recorded": False, "events": [], "files": [],
        "timing_note": "Host monotonic offsets bracket ADB requests, not exact app elapsed values.",
        "performance_note": "Screen recording and capture add load; this is not a performance benchmark."
    }
    manifest["device_source_hashes"] = hashes

    def screenshot(name, clock=None):
        before = time.monotonic()
        path = images / (name + ".png")
        path.write_bytes(adb("exec-out", "screencap", "-p", binary=True))
        manifest["files"].append({"path": path.relative_to(ROOT).as_posix(),
                                  "sha256": digest(path)})
        manifest["events"].append({
            "event": "screenshot", "name": name,
            "request_offset": before - clock if clock else None,
            "returned_offset": time.monotonic() - clock if clock else None})
        print("FRAME " + name, flush=True)

    def tap(x, y, label, clock=None):
        before = time.monotonic()
        adb("shell", "input", "tap", str(x), str(y))
        manifest["events"].append({
            "event": label, "request_offset": before - clock if clock else None,
            "returned_offset": time.monotonic() - clock if clock else None})

    def wait_until(clock, offset):
        time.sleep(max(0, clock + offset - time.monotonic()))

    def record(name, length, actions):
        remote = "/sdcard/shalou-docs-%s-%s.mp4" % (stamp, name)
        clock = time.monotonic()
        process = subprocess.Popen(
            [args.adb, "-s", SERIAL, "shell", "screenrecord", "--bit-rate", "8000000",
             "--time-limit", str(length), remote], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            actions(clock)
            stdout, stderr = process.communicate(timeout=length + 20)
            if process.returncode:
                raise RuntimeError(stderr.decode("utf-8", errors="replace"))
            path = media / (name + ".mp4")
            adb("pull", remote, str(path))
            manifest["files"].append({"path": path.relative_to(ROOT).as_posix(),
                                      "sha256": digest(path)})
            manifest[name] = {"record_start": clock, "time_limit": length,
                              "stderr": stderr.decode("utf-8", errors="replace")}
            print("VIDEO " + path.relative_to(ROOT).as_posix(), flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)

    screenshot("before")
    tap(955, 1830, "reset_before_cycle")
    time.sleep(0.8)
    screenshot("initial")

    def cycle(clock):
        wait_until(clock, 2)
        tap(743, 1830, "start_cycle", clock)
        start = time.monotonic()
        manifest["cycle_start_offset"] = start - clock
        for name, offset in (("before_landing", 1.2), ("first_landing", 2.0),
                             ("early", 5), ("middle", 25), ("late", 45),
                             ("tail", 49), ("after_zero", 51), ("settled", 56)):
            wait_until(start, offset)
            screenshot(name, start)

    record("full-cycle", 64, cycle)
    screenshot("after_cycle")
    tap(955, 1830, "reset_before_pause")
    time.sleep(0.8)

    def pause_resume(clock):
        wait_until(clock, 2)
        tap(743, 1830, "start_pause_run", clock)
        wait_until(clock, 7)
        screenshot("running_before_pause", clock)
        tap(743, 1830, "pause", clock)
        wait_until(clock, 9)
        screenshot("paused", clock)
        wait_until(clock, 12)
        screenshot("paused_later", clock)
        tap(743, 1830, "resume", clock)
        wait_until(clock, 16)
        screenshot("resumed", clock)
        wait_until(clock, 18)
        tap(743, 1830, "pause_after_recording", clock)

    record("pause-resume", 21, pause_resume)
    screenshot("final_paused")
    manifest_path = media / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print("CAPTURE_MANIFEST " + manifest_path.relative_to(ROOT).as_posix(), flush=True)


if __name__ == "__main__":
    main()
