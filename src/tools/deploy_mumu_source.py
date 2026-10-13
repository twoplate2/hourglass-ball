"""Back up and overlay the reserved MuMu app; this does NOT install a new APK."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
SERIAL = "127.0.0.1:16416"
PACKAGE = "org.shalou.hourglass"
APP = "/data/data/%s/files/app" % PACKAGE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    args = parser.parse_args()

    def adb(*command, binary=False):
        return subprocess.check_output(
            [args.adb, "-s", SERIAL, *command], text=not binary,
            encoding=None if binary else "utf-8", creationflags=subprocess.CREATE_NO_WINDOW)

    if "uid=0" not in adb("shell", "id"):
        raise RuntimeError("The reserved MuMu must support root")
    if not adb("shell", "pm", "path", PACKAGE).startswith("package:"):
        raise RuntimeError("The hourglass APK is not installed")
    if "1080x1920" not in adb("shell", "wm", "size"):
        raise RuntimeError("Unexpected reserved instance display size")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = ROOT / "_temp/mumu-fixes" / stamp
    folder.mkdir(parents=True, exist_ok=False)
    adb("shell", "am", "force-stop", PACKAGE)
    config_present = adb("shell", "sh", "-c",
                         "'if [ -f /data/data/" + PACKAGE
                         + "/files/.hourglass_config.json ]; then echo yes; fi'").strip()
    (folder / "before.tar").write_bytes(adb(
        "exec-out", "tar", "-cf", "-", "-C", "/data/data/%s/files" % PACKAGE,
        "app", *([".hourglass_config.json"] if config_present else []), binary=True))
    paths = [ROOT / "main.py"] + sorted((ROOT / "src/app").glob("*.py"))
    paths += sorted(path for directory in ("ui", "sounds")
                    for path in (ROOT / directory).rglob("*")
                    if path.is_file() and path.suffix in (".png", ".otf", ".wav")
                    and not path.is_relative_to(ROOT / "sounds/source"))
    receipt = {"serial": SERIAL, "mode": "source-overlay-not-new-apk",
               "before_backup": str(folder / "before.tar"),
               "package_info": adb("shell", "dumpsys", "package", PACKAGE),
               "files": []}
    archive = folder / "source.tar"
    with tarfile.open(archive, "w") as bundle:
        for path in paths:
            name = path.relative_to(ROOT).as_posix()
            bundle.add(path, arcname=name, recursive=False)
            receipt["files"].append({"path": name, "target": APP + "/" + name,
                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    remote = "/sdcard/shalou-source-" + stamp + ".tar"
    adb("push", str(archive), remote)
    # p4a prefers the old sourceless entry if it remains alongside main.py.
    adb("shell", "sh", "-c",
        "'if [ -f " + APP + "/main.pyc ]; then mv " + APP + "/main.pyc "
        + APP + "/main.pyc.before-source-" + stamp + "; fi'")
    adb("shell", "tar", "-xf", remote, "-C", APP)
    hashes = adb("shell", "sha256sum", *(item["target"] for item in receipt["files"]))
    actual = {line.split(None, 1)[1].strip(): line.split()[0] for line in hashes.splitlines()}
    if any(actual.get(item["target"]) != item["sha256"] for item in receipt["files"]):
        raise RuntimeError("Source/resource hash mismatch after deployment")
    receipt["device_hashes"] = hashes
    receipt["deployed_at"] = datetime.now().astimezone().isoformat()
    output = folder / "deployed-files.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print("DEPLOY_RECEIPT " + str(output), flush=True)
    print(adb("shell", "am", "start", "-n", PACKAGE + "/org.kivy.android.PythonActivity"))


if __name__ == "__main__":
    main()
