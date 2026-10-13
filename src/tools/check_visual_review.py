"""Validate this patch's scoped MuMu appearance evidence before pushing."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("Evidence leaves the workspace")
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    review = json.loads(args.review.read_text(encoding="utf-8"))
    if review.get("scope") != "appearance" or not review.get("reviewer"):
        raise ValueError("Missing scoped appearance review")
    if review.get("results") != {"S04": "preliminary_pass", "NS03": "preliminary_pass",
                                  "S05": "preliminary_pass"}:
        raise ValueError("The three appearance checks are incomplete")
    for entry in review["evidence"]:
        path = (ROOT / entry["path"]).resolve()
        if not path.is_relative_to(ROOT.resolve()) or digest(path) != entry["sha256"]:
            raise ValueError("Missing or changed evidence: " + entry["path"])
    identity = read(review["identity"])
    sources = {"main.py": identity["current_source_sha256"], **identity["source_files"]}
    for name, value in sources.items():
        path = ROOT / name
        if digest(path) != value:
            raise ValueError("Code changed since MuMu appearance review: " + name)
        committed = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT)
        if committed.replace(b"\r\n", b"\n") != path.read_bytes().replace(b"\r\n", b"\n"):
            raise ValueError("Pushed commit does not contain reviewed code: " + name)
    for key in ("before", "after"):
        report = read(review[key])
        if report.get("exception") or report["mode"] != "matrix":
            raise ValueError("Incomplete appearance capture")
        folder = (ROOT / review[key]).parent
        for frame in report["frames"]:
            if digest(folder / frame["file"]) != frame["sha256"]:
                raise ValueError("Missing or changed captured frame")
    compared = read(review["comparison"])
    if (compared["cases"] != review["expected_cases"] or not compared["same_particle_counts"]
            or not compared["same_sand_ledgers"] or compared["outside_expected_pixels"]):
        raise ValueError("Unexpected collateral appearance changes")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("Commit the reviewed work before pushing")
    print("VISUAL_REVIEW_OK scope=appearance cases=%s; not a full or performance acceptance" %
          compared["cases"])


if __name__ == "__main__":
    main()
