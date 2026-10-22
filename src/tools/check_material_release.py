"""Bind this scoped material release to reviewed PC captures and exact sources."""

import ast
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from compare_material_frames import compare
from check_release_version import parse_version, validate_increment

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "design/checks/records/v3.4/material-review.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_equal(measured, current, version=False):
    if not version:
        return measured.replace(b"\r\n", b"\n") == current.replace(b"\r\n", b"\n")
    old_version, new_version = parse_version(measured.decode()), parse_version(current.decode())
    if new_version != old_version:
        validate_increment(old_version, new_version)
    trees = [ast.parse(measured), ast.parse(current)]
    for tree in trees:
        nodes = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "APP_VERSION" for t in node.targets)]
        if len(nodes) != 1:
            return False
        nodes[0].value = ast.Constant(value="measured-version")
    return ast.dump(trees[0]) == ast.dump(trees[1])


def require_capture(report, cases, frames):
    if not all(report.get(key) is True for key in ("completed", "passed", "source_unchanged")):
        raise ValueError("Incomplete or failed capture")
    if len(report.get("cases", [])) != cases or len(report.get("frames", [])) != frames:
        raise ValueError("Missing capture cases or frames")
    for key in ("hidden_at_creation", "hidden_at_finish"):
        hidden = report.get(key, {})
        if hidden.get("IsWindow") is not True or hidden.get("IsWindowVisible") is not False:
            raise ValueError("Native hidden window evidence missing")
    for frame in report["frames"]:
        hidden = frame.get("native_hidden", {})
        if hidden.get("IsWindowVisible") is not False or hidden.get("IsIconic") is not False:
            raise ValueError("Capture window was not truly hidden")


def validate(record=RECORD):
    review = json.loads(record.read_text(encoding="utf-8"))
    if review.get("scope") != "pc-material-appearance" or review.get("appearance") != "passed" or not review.get("reviewer"):
        raise ValueError("Missing scoped visual review")
    archive = record.parent / "evidence.zip"
    if digest(archive.read_bytes()) != review["archive_sha256"]:
        raise ValueError("Evidence archive changed")
    with tempfile.TemporaryDirectory(prefix="material-release-") as folder:
        folder = Path(folder)
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if not (folder / name).resolve().is_relative_to(folder.resolve()):
                    raise ValueError("Unsafe evidence path")
            bundle.extractall(folder)
        reports = {}
        for arm in ("pc-before", "pc-after", "pc-native-before", "pc-native-after"):
            reports[arm] = json.loads((folder / arm / "result.json").read_text(encoding="utf-8"))
            require_capture(reports[arm], 3 if "native" in arm else 31, 3 if "native" in arm else 50)
        measured = reports["pc-after"]["source_files"]
        current_paths = {"main.py"} | {p.relative_to(ROOT).as_posix() for p in (ROOT / "src/app").glob("*.py")}
        if current_paths != set(measured):
            raise ValueError("Runtime source inventory differs from tested source")
        for name, expected in measured.items():
            original = (folder / "measured-source" / name).read_bytes()
            if digest(original) != expected:
                raise ValueError("Measured source corrupted: " + name)
            if not source_equal(original, (ROOT / name).read_bytes(), name == "src/app/app_version.py"):
                raise ValueError("Runtime changed after acceptance: " + name)
        version = ".".join(map(str, parse_version((ROOT / "src/app/app_version.py").read_text(encoding="utf-8"))))
        if version != review["release_version"]:
            raise ValueError("Review belongs to another release")
        if digest((ROOT / "buildozer.spec").read_bytes().replace(b"\r\n", b"\n")) != review["build_spec_sha256_lf"]:
            raise ValueError("Build configuration changed after review")
        for name, expected in review["reviewed_images"].items():
            if digest((record.parent / name).read_bytes()) != expected:
                raise ValueError("Reviewed image changed")
        for profile in ("", "native-"):
            before = folder / ("pc-" + profile + "before") / "result.json"
            after = folder / ("pc-" + profile + "after") / "result.json"
            result, _ = compare(before, after, folder / ("comparison-" + profile),
                                before if not profile else None, after if not profile else None)
            if not result["passed"]:
                raise ValueError("Replayed pixel/state comparison failed: " + str(result["counts"]))
    print("MATERIAL_RELEASE_OK PC=50+3 source=matched scope=appearance+state only")


if __name__ == "__main__":
    validate()
