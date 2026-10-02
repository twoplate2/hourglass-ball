"""Windowless tests for shared APK version and push rules."""

from configparser import ConfigParser
from pathlib import Path
import re
import sys
import unittest

import check_release_version as release


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_version import APP_VERSION


class ReleaseTests(unittest.TestCase):
    def test_shared_apk_version(self):
        spec = ConfigParser()
        spec.read(ROOT / "buildozer.spec", encoding="utf-8")
        path = ROOT / spec.get("app", "version.filename")
        captured = re.search(spec.get("app", "version.regex"), path.read_text(encoding="utf-8"))
        self.assertEqual(captured.group(1), APP_VERSION)
        self.assertFalse(spec.has_option("app", "version"))

    def test_last_component_increment(self):
        release.validate_increment((0, 1, 0), (0, 1, 1))
        release.validate_increment((0, 1, 9), (0, 1, 10))
        release.validate_increment((1, 0), (1, 1))          # 两段号同样适用
        for before, version in (((0, 1, 0), (0, 1, 0)), ((0, 1, 0), (0, 1, 2)),
                                ((1, 0), (1, 0)), ((1, 0), (1, 2))):
            with self.assertRaises(ValueError):
                release.validate_increment(before, version)
        # 里程碑式跃迁允许(段数或前几段变化), 但必须严格递增
        release.validate_increment((0, 1, 3), (1, 0))
        release.validate_increment((0, 1, 3), (1, 0, 0))
        for version in ((0, 1, 3), (0, 0, 9), (0, 1)):
            with self.assertRaises(ValueError):
                release.validate_increment((0, 1, 3), version)
        # 同一版本换写法(补零对齐后相等)放行
        release.validate_increment((1, 0, 0), (1, 0))
        release.validate_increment((1, 0), (1, 0, 0))

    def test_chinese_version_title(self):
        release.validate_title("0.1.1 修复沙流衔接", (0, 1, 1))
        for title in ("0.1.0 修复沙流衔接", "0.1.1 Fix flow", "修复沙流衔接"):
            with self.assertRaises(ValueError):
                release.validate_title(title, (0, 1, 1))

    def test_version_parser(self):
        self.assertEqual(release.parse_version('APP_VERSION = "0.1.1"'), (0, 1, 1))
        for source in ('APP_VERSION = "bad"', 'APP_VERSION = 1', "x = 1"):
            with self.assertRaises(ValueError):
                release.parse_version(source)


if __name__ == "__main__":
    unittest.main()
