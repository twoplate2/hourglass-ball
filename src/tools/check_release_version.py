"""Validate a two-field app version and its Chinese commit title."""

import argparse
import ast
from configparser import ConfigParser
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def git(*args):
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def version_fields(value, *, historical=False):
    pattern = r"[0-9]+\.[0-9]+(?:\.[0-9]+)?" if historical else r"[0-9]+\.[0-9]+"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid historical version: {value!r}" if historical else
                         f"APP_VERSION must have exactly two numeric fields (a.b): {value!r}")
    return tuple(map(int, value.split(".")))


def parse_version(text, *, historical=False):
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "APP_VERSION"
                for target in node.targets):
            return version_fields(ast.literal_eval(node.value), historical=historical)
    raise ValueError("APP_VERSION is missing")


def version_at(ref, *, historical=False):
    for path in ("src/app/app_version.py", "app/app_version.py", "app_version.py"):
        result = git("show", f"{ref}:{path}")
        if result.returncode == 0:
            return parse_version(result.stdout, historical=historical)
    legacy = git("show", f"{ref}:buildozer.spec")
    if legacy.returncode:
        raise ValueError(f"Cannot read version at {ref}: {legacy.stderr.strip()}")
    spec = ConfigParser(interpolation=None)
    spec.read_string(legacy.stdout)
    return version_fields(spec.get("app", "version"), historical=historical)


def validate_increment(before, after):
    """新版本固定为 a.b。同一主版本每次递增 b，历史基线可以有三段。"""
    if len(after) != 2:
        raise ValueError("APP_VERSION must have exactly two numeric fields (a.b)")
    if after <= before:
        raise ValueError(f"Version must increase: "
                         f"found {'.'.join(map(str, after))} "
                         f"after {'.'.join(map(str, before))}")
    if after[0] == before[0]:
        expected = (before[0], before[1] + 1)
        if after != expected:
            raise ValueError(f"Each push must increment the last component: "
                             f"expected {'.'.join(map(str, expected))}, "
                             f"found {'.'.join(map(str, after))}")


def validate_push(base, head):
    before = version_at(base, historical=True)
    current = version_at(head)
    # Main can catch up with a numeric release already published on a release
    # branch. Every intermediate version must be present and in ancestry order.
    if before[0] == current[0]:
        for minor in range(before[1] + 1, current[1]):
            step = (current[0], minor)
            tag = f"refs/tags/{step[0]}.{minor}"
            if (git("merge-base", "--is-ancestor", base, tag).returncode or
                    git("merge-base", "--is-ancestor", tag, head).returncode or
                    version_at(tag) != step):
                raise ValueError(f"Missing intermediate release in this commit history: {tag}")
            validate_increment(before, step)
            before, base = step, tag
    validate_increment(before, current)


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
            validate_push(args.base, args.head)
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
