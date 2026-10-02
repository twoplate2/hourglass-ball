"""Validate one patch-version increment per push and a Chinese commit title."""

import argparse
import ast
from configparser import ConfigParser
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def parse_version(text):
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "APP_VERSION"
                for target in node.targets):
            version = ast.literal_eval(node.value)
            if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ValueError("APP_VERSION must have the form major.minor.patch")
            return tuple(map(int, version.split(".")))
    raise ValueError("APP_VERSION is missing")


def version_at(ref):
    result = git("show", f"{ref}:app_version.py")
    if result.returncode == 0:
        return parse_version(result.stdout)
    legacy = git("show", f"{ref}:buildozer.spec")
    if legacy.returncode:
        raise ValueError(f"Cannot read version at {ref}: {legacy.stderr.strip()}")
    spec = ConfigParser(interpolation=None)
    spec.read_string(legacy.stdout)
    version = spec.get("app", "version")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid legacy APK version")
    return tuple(map(int, version.split(".")))


def validate_increment(before, after):
    expected = (*before[:2], before[2] + 1)
    if after != expected:
        raise ValueError(f"Each push must increment the patch version: "
                         f"expected {'.'.join(map(str, expected))}, "
                         f"found {'.'.join(map(str, after))}")


def validate_title(title, version):
    prefix = ".".join(map(str, version)) + " "
    if not title.startswith(prefix) or not re.search(r"[\u4e00-\u9fff]", title):
        raise ValueError(f"Commit title must start with '{prefix}' and contain Chinese")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--check-title", action="store_true")
    args = parser.parse_args()
    try:
        current = version_at(args.head)
        if args.base and set(args.base) != {"0"}:
            validate_increment(version_at(args.base), current)
        if args.check_title:
            result = git("log", "-1", "--format=%s", args.head)
            if result.returncode:
                raise ValueError(result.stderr.strip())
            validate_title(result.stdout.strip(), current)
    except (ValueError, SyntaxError, OSError) as exc:
        parser.exit(1, f"Release check failed: {exc}\n")
    print("Release check passed:", ".".join(map(str, current)))


if __name__ == "__main__":
    main()
