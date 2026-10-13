"""Run background checks only on the reserved MuMu and restore its normal entry."""

import argparse
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SERIAL = "127.0.0.1:16416"
PACKAGE = "org.shalou.hourglass"
APP = "/data/data/%s/files/app" % PACKAGE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--mode", choices=("units", "matrix", "benchmark"), required=True)
    parser.add_argument("--arm", choices=("current", "apk296", "source"), default="current")
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--cases", type=Path, help="Explicit cases for a narrowly scoped matrix")
    args = parser.parse_args()
    if not args.label or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in args.label):
        raise ValueError("Label must use lowercase ASCII letters, digits, '-' or '_'")

    def adb(*command, binary=False):
        return subprocess.check_output(
            [args.adb, "-s", SERIAL, *command], text=not binary,
            encoding=None if binary else "utf-8", creationflags=subprocess.CREATE_NO_WINDOW)

    if "uid=0" not in adb("shell", "id"):
        raise RuntimeError("Reserved MuMu root is required")
    if "1080x1920" not in adb("shell", "wm", "size"):
        raise RuntimeError("Unexpected reserved MuMu size")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    identifier = stamp + "-" + args.label
    folder = ROOT / "_temp/mumu-checks" / identifier
    folder.mkdir(parents=True, exist_ok=False)
    entry = adb("exec-out", "cat", APP + "/main.py", binary=True)
    local = (ROOT / "main.py").read_bytes()
    if entry != local:
        raise RuntimeError("MuMu entry does not match current worktree")
    source_files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (ROOT / "src/app").glob("*.py")}
    deployed = adb("shell", "sha256sum", *(APP + "/" + name for name in source_files))
    actual_sources = {line.split(None, 1)[1].strip(): line.split()[0]
                      for line in deployed.splitlines()}
    if any(actual_sources.get(APP + "/" + name) != value for name, value in source_files.items()):
        raise RuntimeError("MuMu application modules do not match current worktree")
    backup = folder / "main-before.py"
    backup.write_bytes(entry)
    request = {"id": identifier, "mode": args.mode, "arm": args.arm,
               "periods": [1, 5, 15, 50], "cases": []}
    if args.mode == "matrix":
        for period in (1, 5, 50):
            stages = sorted(set(min(period, value) for value in
                                (0.2, 0.6, 1.2, 1.8, 2.1, period * 0.5, period * 0.9)))
            for i, elapsed in enumerate(stages):
                request["cases"].append({"name": "p%d-stage%d" % (period, i),
                                         "period": period, "color": 0, "elapsed": elapsed})
        for color in range(1, 6):
            for elapsed in (1.2, 25, 45):
                request["cases"].append({"name": "color%d-t%d" % (color, elapsed * 10),
                                         "period": 50, "color": color, "elapsed": elapsed})
        for i, elapsed in enumerate((63.96, 63.98, 64.0, 64.02, 64.04)):
            request["cases"].append({"name": "long64-%d" % i, "period": 600,
                                     "color": 0, "elapsed": elapsed})
        if args.cases:
            request["cases"] = json.loads(args.cases.read_text(encoding="utf-8"))
            if not request["cases"]:
                raise ValueError("An empty appearance scope cannot pass")
    request_path = folder / "request.json"
    request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    archive = folder / "probe.tar"
    files = []
    with tarfile.open(archive, "w") as bundle:
        for path, name in ((ROOT / "src/tools/mumu_probe_entry.py", "main.py"),
                           (backup, "qa_runtime.py"), (request_path, "_qa/request.json"),
                           (ROOT / "src/tools/test_stream_rendering.py", "_qa/test_stream_rendering.py")):
            bundle.add(path, arcname=name, recursive=False)
            files.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if args.arm == "source":
            if not args.source_archive:
                raise ValueError("The source baseline requires its original archive")
            with tarfile.open(args.source_archive) as previous:
                for member in previous.getmembers():
                    name = PurePosixPath(member.name)
                    if not member.isfile() or name.is_absolute() or ".." in name.parts:
                        continue
                    content = previous.extractfile(member).read()
                    target = "_qa/baseline/" + name.as_posix()
                    info = tarfile.TarInfo(target)
                    info.size, info.mode = len(content), 0o644
                    bundle.addfile(info, io.BytesIO(content))
                    files.append({"path": target, "sha256": hashlib.sha256(content).hexdigest()})
        if args.arm == "apk296":
            if not args.apk:
                raise ValueError("The baseline arm requires the original APK")
            with zipfile.ZipFile(args.apk) as apk:
                with tarfile.open(fileobj=io.BytesIO(apk.read("assets/private.tar"))) as private:
                    for member in private.getmembers():
                        name = PurePosixPath(member.name)
                        if (not member.isfile() or name.is_absolute() or ".." in name.parts
                                or member.name.startswith("_python_bundle/")
                                or name.suffix not in (".pyc", ".otf", ".png", ".wav")):
                            continue
                        content = private.extractfile(member).read()
                        target = "_qa/baseline/" + name.as_posix()
                        info = tarfile.TarInfo(target)
                        info.size, info.mode = len(content), 0o644
                        bundle.addfile(info, io.BytesIO(content))
                        files.append({"path": target, "sha256": hashlib.sha256(content).hexdigest()})
    receipt = {"request": request, "files": files,
               "current_source_sha256": hashlib.sha256(entry).hexdigest(),
               "source_files": source_files,
               "apk_sha256": hashlib.sha256(args.apk.read_bytes()).hexdigest() if args.apk else None}
    (folder / "identity.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    adb("shell", "am", "force-stop", PACKAGE)
    installed = False
    try:
        remote = "/sdcard/shalou-probe-" + identifier + ".tar"
        adb("push", str(archive), remote)
        installed = True
        adb("shell", "tar", "-xf", remote, "-C", APP)
        actual = adb("shell", "sha256sum", *(APP + "/" + item["path"] for item in files))
        hashes = {line.split(None, 1)[1].strip(): line.split()[0] for line in actual.splitlines()}
        if any(hashes.get(APP + "/" + item["path"]) != item["sha256"] for item in files):
            raise RuntimeError("Probe/baseline hash mismatch")
        adb("shell", "am", "start", "-n", PACKAGE + "/org.kivy.android.PythonActivity")
        deadline = time.monotonic() + args.timeout
        next_heartbeat = 0.0
        result = APP + "/_qa/" + identifier + "/result.json"
        pid = ""
        while time.monotonic() < deadline:
            time.sleep(2)
            present = adb("shell", "sh", "-c",
                          "'if [ -f " + result + " ]; then echo ready; fi'").strip()
            if present:
                adb("pull", APP + "/_qa/" + identifier, str(folder / "device"))
                parsed = json.loads((folder / "device/result.json").read_text(encoding="utf-8"))
                if parsed.get("exception") or parsed.get("cancelled") or parsed.get("passed") is False:
                    raise RuntimeError("MuMu check failed: " + json.dumps(parsed)[:1500])
                print("MUMU_CHECK_COMPLETE " + str(folder), flush=True)
                return
            pid = adb("shell", "pidof", PACKAGE).strip()
            if time.monotonic() >= next_heartbeat:
                print("MUMU_CHECK_RUNNING id=%s pid=%s" % (identifier, pid), flush=True)
                next_heartbeat = time.monotonic() + 10
        raise TimeoutError("MuMu check did not complete within deadline")
    finally:
        if installed:
            adb("shell", "am", "force-stop", PACKAGE)
            (folder / "logcat.txt").write_text(adb("logcat", "-d", "-s", "python:I"),
                                             encoding="utf-8")
            adb("push", str(backup), APP + "/main.py")
            actual = adb("shell", "sha256sum", APP + "/main.py").split()[0]
            if actual != hashlib.sha256(entry).hexdigest():
                raise RuntimeError("Normal app entry was not restored")
            print("MUMU_ENTRY_RESTORED", flush=True)
            adb("shell", "am", "start", "-n", PACKAGE + "/org.kivy.android.PythonActivity")


if __name__ == "__main__":
    main()
