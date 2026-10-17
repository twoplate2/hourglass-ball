"""Prove 3.0 runtime identity except the explicit foreground power policy."""

import ast
from configparser import ConfigParser
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "c75cda0"


def baseline(name):
    return subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)


def normalized_main(source):
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and node.module == "app.keep_awake")]
    app = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "HourglassApp")
    app.body = [node for node in app.body if not (
        isinstance(node, ast.FunctionDef) and node.name == "_set_keep_awake")]
    for method in app.body:
        if isinstance(method, ast.FunctionDef) and method.name in (
                "on_start", "on_pause", "on_stop", "on_resume"):
            method.body = [node for node in method.body if not (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == "self._set_keep_awake")]
    return ast.dump(tree)


def verify():
    if normalized_main((ROOT / "main.py").read_bytes()) != normalized_main(baseline("main.py")):
        raise ValueError("Runtime differs from 3.0 beyond foreground power hooks")
    for name in subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", BASE, "src/app"], cwd=ROOT,
            text=True).splitlines():
        if name == "src/app/app_version.py":
            continue
        if (ROOT / name).read_bytes().replace(b"\r\n", b"\n") != baseline(name).replace(
                b"\r\n", b"\n"):
            raise ValueError("3.0 runtime module changed: " + name)
    current, before = ConfigParser(), ConfigParser()
    current.read(ROOT / "buildozer.spec", encoding="utf-8")
    before.read_string(baseline("buildozer.spec").decode("utf-8"))
    if current != before:
        raise ValueError("APK configuration is not the 3.0 baseline")
    if "pymunk" in current.get("app", "requirements"):
        raise ValueError("Rollback must not require Pymunk")
    print("ROLLBACK_RELEASE_OK baseline=3.0 only_changes=version+foreground-power-policy")


if __name__ == "__main__":
    verify()
