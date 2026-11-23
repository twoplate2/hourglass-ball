"""Windowless tests for shared APK version and push rules."""

from configparser import ConfigParser
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import android_hook
import check_release_version as release


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from app.app_version import APP_VERSION


class ReleaseTests(unittest.TestCase):
    def test_shared_apk_version(self):
        spec = ConfigParser()
        spec.read(ROOT / "buildozer.spec", encoding="utf-8")
        path = ROOT / spec.get("app", "version.filename")
        captured = re.search(spec.get("app", "version.regex"), path.read_text(encoding="utf-8"))
        self.assertEqual(captured.group(1), APP_VERSION)
        self.assertFalse(spec.has_option("app", "version"))
        self.assertIsNone(re.search(spec.get("app", "version.regex"), 'APP_VERSION = "3.29.1"'))

    def test_last_component_increment(self):
        release.validate_increment((1, 0), (1, 1))
        release.validate_increment((3, 9), (3, 10))
        release.validate_increment((3, 28, 1), (3, 29))
        release.validate_increment((0, 1, 3), (1, 0))
        for before, version in (((3, 28, 1), (3, 28)), ((3, 28, 1), (3, 28, 2)),
                                ((3, 28), (3, 29, 0)), ((1, 0), (1, 0)), ((1, 0), (1, 2))):
            with self.assertRaises(ValueError):
                release.validate_increment(before, version)

    def test_pushed_main_may_include_only_already_tagged_intermediate_versions(self):
        versions = {"base": (3, 35), "refs/tags/3.36": (3, 36), "head": (3, 37)}
        with patch.object(release, "version_at", side_effect=lambda r, **kw: versions[r]):
            with patch.object(release, "git", return_value=SimpleNamespace(returncode=0)) as git:
                release.validate_push("base", "head")
                self.assertEqual([call.args[-2:] for call in git.call_args_list],
                                 [("base", "refs/tags/3.36"), ("refs/tags/3.36", "head")])
                versions["refs/tags/3.36"] = (3, 35)
                with self.assertRaises(ValueError):
                    release.validate_push("base", "head")
            versions["refs/tags/3.36"] = (3, 36)
            with patch.object(release, "git", return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(ValueError):
                    release.validate_push("base", "head")

    def test_chinese_version_title(self):
        release.validate_title("3.29 修复沙流衔接", (3, 29))
        for title in ("3.29.1 修复沙流衔接", "3.28 修复沙流衔接", "3.29 Fix flow", "修复沙流衔接"):
            with self.assertRaises(ValueError):
                release.validate_title(title, (3, 29))

    def test_version_parser(self):
        self.assertEqual(release.parse_version('APP_VERSION = "3.29"'), (3, 29))
        for source in ('APP_VERSION = "3.29.0"', 'APP_VERSION = "3"', 'APP_VERSION = "bad"',
                       'APP_VERSION = 1', "x = 1"):
            with self.assertRaises(ValueError):
                release.parse_version(source)

    def test_old_three_field_version_is_readable_only_as_a_base(self):
        source = 'APP_VERSION = "3.28.1"'
        with patch.object(release, "git", return_value=SimpleNamespace(returncode=0, stdout=source)):
            with self.assertRaises(ValueError):
                release.version_at("head")
            self.assertEqual(release.version_at("base", historical=True), (3, 28, 1))

    def test_android_upgrade_from_existing_packages(self):
        # The previous production/preview packages have Android codes 102132801/2.
        code = android_hook.android_version_code("3.29")
        self.assertGreater(code, 102132802)
        self.assertLess(code, android_hook.android_version_code("3.30"))
        self.assertLess(android_hook.android_version_code("3.999"), android_hook.android_version_code("4.0"))
        with self.assertRaises(ValueError):
            android_hook.android_version_code("3.29.1")
        for args in (["--version=3.29"], ["--version", "3.29"]):
            toolchain = SimpleNamespace(args=SimpleNamespace(unknown_args=args))
            with patch.object(android_hook, "_inject_manifest"):
                android_hook.before_apk_build(toolchain)
                android_hook.after_apk_build(toolchain)
            self.assertEqual(args[-2:], ["--numeric-version", str(code)])
            self.assertEqual(args.count("--numeric-version"), 1)

    def test_version_at_new_and_old_paths(self):
        for path in ("src/app/app_version.py", "app/app_version.py", "app_version.py"):
            def show(*args):
                return SimpleNamespace(
                    returncode=0 if args == ("show", f"ref:{path}") else 1,
                    stdout='APP_VERSION = "2.96"',
                    stderr="missing")
            with self.subTest(path=path), patch.object(release, "git", side_effect=show):
                self.assertEqual(release.version_at("ref"), (2, 96))


if __name__ == "__main__":
    unittest.main()
