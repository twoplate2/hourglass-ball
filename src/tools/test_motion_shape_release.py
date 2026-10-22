"""Reject incomplete, mismatched or misleading PC motion/shape evidence."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import numpy as np
from PIL import Image

from check_motion_shape_release import (
    SAMPLINGS, SHAPE_FAILURE, SPEED_FAILURE, relative_file, require_cross_checks,
    require_hidden, require_manifest, require_motion_capture, require_pixel_result, safe_extract,
    source_digest_matches,
)
from check_stream_wrap import inspect as inspect_wrap


class ReleaseTests(unittest.TestCase):
    def hidden(self):
        return dict(IsWindow=True, IsWindowVisible=False, IsIconic=False, window_state_config="hidden")

    def test_hidden_requires_actual_window_and_hidden_creation(self):
        require_hidden(self.hidden())
        for key, value in (("IsWindow", False), ("IsWindowVisible", True),
                           ("IsIconic", True), ("window_state_config", "normal")):
            row = self.hidden(); row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                require_hidden(row)

    def manifest(self):
        return {"main.py": "a" * 64, "src/app/app_version.py": "b" * 64}

    def test_source_manifest_requires_end_identity(self):
        manifest = self.manifest()
        self.assertEqual(require_manifest(dict(source_files=manifest, source_files_end=manifest)), manifest)
        for end in (None, {}, {**manifest, "main.py": "c" * 64}):
            with self.assertRaises(ValueError):
                require_manifest(dict(source_files=manifest, source_files_end=end))

    def test_source_paths_cannot_escape(self):
        manifest = {**self.manifest(), "../secret.py": "c" * 64}
        with self.assertRaises(ValueError):
            require_manifest(dict(source_files=manifest, source_files_end=manifest))

    def test_baseline_line_endings_only_when_explicitly_allowed(self):
        data = b"APP_VERSION = '3.5'\nx = 1\n"
        crlf = data.replace(b"\n", b"\r\n")
        exact = hashlib.sha256(data).hexdigest()
        windows = hashlib.sha256(crlf).hexdigest()
        self.assertTrue(source_digest_matches(data, exact))
        self.assertFalse(source_digest_matches(data, windows))
        self.assertTrue(source_digest_matches(data, windows, allow_newlines=True))
        self.assertTrue(source_digest_matches(crlf, exact, allow_newlines=True))

    def test_baseline_rejects_changes_beyond_line_endings(self):
        data = b"APP_VERSION = '3.5'\nx = 1\n"
        for changed in (data.replace(b"3.5", b"3.6"), data.replace(b"x = 1", b"x = 2"),
                        data + b"extra = True\n"):
            digest = hashlib.sha256(changed).hexdigest()
            self.assertFalse(source_digest_matches(data, digest, allow_newlines=True))

    def test_artifact_paths_are_relative(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ("../escape", "/absolute", "C:/absolute", "..\\escape", "", "."):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    relative_file(temp, name)
            self.assertEqual(relative_file(temp, "frames/0000.png"), Path(temp).resolve() / "frames/0000.png")

    def make_archive(self, path, members):
        with tarfile.open(path, "w:xz") as bundle:
            for name, kind in members:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    info.size = 4
                    bundle.addfile(info, io.BytesIO(b"data"))
                else:
                    info.type = kind
                    info.linkname = "../outside"
                    bundle.addfile(info)

    def test_regular_archive_extracts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); archive = root / "evidence.tar.xz"
            self.make_archive(archive, [("folder/frame.png", "file")])
            safe_extract(archive, root / "out")
            self.assertEqual((root / "out/folder/frame.png").read_bytes(), b"data")

    def test_archive_rejects_links_devices_and_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for i, (name, kind) in enumerate((("../outside", "file"), ("/absolute", "file"),
                    ("C:/absolute", "file"), ("..\\outside", "file"),
                    ("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE),
                    ("pipe", tarfile.FIFOTYPE), ("device", tarfile.CHRTYPE))):
                archive = root / (str(i) + ".tar.xz")
                self.make_archive(archive, [(name, kind)])
                with self.subTest(name=name), self.assertRaises(ValueError):
                    safe_extract(archive, root / ("out" + str(i)))
            self.assertFalse((root / "outside").exists())

    def test_archive_rejects_duplicate_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); archive = root / "dup.tar.xz"
            self.make_archive(archive, [("same", "file"), ("same", "file")])
            with self.assertRaises(ValueError):
                safe_extract(archive, root / "out")

    def motion_fixture(self, folder):
        buffer = io.BytesIO(); Image.new("RGB", (32, 32), "gold").save(buffer, format="PNG")
        data = buffer.getvalue(); digest = hashlib.sha256(data).hexdigest()
        frames = []
        for i in range(241):
            name = "%04d.png" % i; (folder / name).write_bytes(data)
            frames.append(dict(time=8 + i / 60, file=name, sha256=digest,
                               native_hidden=self.hidden(), geometry={}))
        manifest = self.manifest()
        return dict(completed=True, version="3.6", renderer="short_grain_field", grain=3,
                    source_files=manifest, source_files_end=manifest, hidden_at_creation=self.hidden(),
                    hidden_at_finish=self.hidden(), dimensions=[32, 32], frames=frames,
                    expected_frames=241, sample_rate=60)

    def test_complete_motion_capture_structure(self):
        # Appearance is checked separately; this test only validates provenance.
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); report = self.motion_fixture(folder)
            self.assertEqual(require_motion_capture(report, folder, "3.6", 3), (8.0, 12.0))

    def test_cancelled_missing_wrong_renderer_or_setting(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); report = self.motion_fixture(folder)
            for key, value in (("completed", False), ("renderer", "line_pool"),
                               ("grain", 6), ("version", "3.5"), ("sample_rate", 30)):
                bad = dict(report); bad[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    require_motion_capture(bad, folder, "3.6", 3)
            bad = dict(report); bad["frames"] = report["frames"][:-1]
            with self.assertRaises(ValueError):
                require_motion_capture(bad, folder, "3.6", 3)

    def test_motion_rejects_tampered_image(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); report = self.motion_fixture(folder)
            (folder / "0042.png").write_bytes(b"different pixels")
            with self.assertRaises(ValueError):
                require_motion_capture(report, folder, "3.6", 3)

    def test_motion_rejects_duplicate_or_reversed_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); report = self.motion_fixture(folder)
            for field, value in (("file", "0000.png"), ("time", 7.9)):
                bad = deepcopy(report); bad["frames"][1][field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    require_motion_capture(bad, folder, "3.6", 3)

    def pixel_result(self):
        return dict(passed=True, motion_shape_passed=True, pending=[], failures=[])

    def test_current_pixel_goals_must_both_pass(self):
        require_pixel_result(self.pixel_result())
        for field, value in (("passed", False), ("motion_shape_passed", False),
                              ("pending", ["missing_sequence"]), ("failures", [SHAPE_FAILURE])):
            result = self.pixel_result(); result[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                require_pixel_result(result)

    def test_baseline_must_fail_speed_and_shape_independently(self):
        result = self.baseline_result()
        require_pixel_result(result, baseline=True)
        for failures in ([SPEED_FAILURE], [SHAPE_FAILURE],
                         [SPEED_FAILURE, SHAPE_FAILURE, "unrelated_missing_data"]):
            with self.subTest(failures=failures), self.assertRaises(ValueError):
                require_pixel_result(dict(result, failures=failures), baseline=True)

    def baseline_result(self):
        return dict(passed=False, pending=[], failures=[SPEED_FAILURE, SHAPE_FAILURE],
                    reliable_pairs=12, upper_pixels_per_second=20, lower_pixels_per_second=20)

    def test_baseline_reliability_failure_is_retained_as_failure(self):
        result = self.baseline_result()
        result["failures"].append("insufficient_reliable_pixel_motion")
        require_pixel_result(result, baseline=True)
        self.assertFalse(result["passed"])
        self.assertIn("insufficient_reliable_pixel_motion", result["failures"])

    def test_baseline_rejects_too_few_speed_witnesses(self):
        for count in (0, 7, True, None):
            result = self.baseline_result(); result["reliable_pairs"] = count
            with self.subTest(count=count), self.assertRaises(ValueError):
                require_pixel_result(result, baseline=True)

    def test_baseline_rejects_nonpositive_or_nonfinite_speed(self):
        for field in ("upper_pixels_per_second", "lower_pixels_per_second"):
            for value in (0, -1, float("nan"), float("inf"), float("-inf"), None):
                result = self.baseline_result(); result[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    require_pixel_result(result, baseline=True)

    def cross_fixture(self):
        return {arm: {sampling: dict(upper_pixels_per_second=100, lower_pixels_per_second=150,
                      shape_samples=[dict(mean_width=20)]) for sampling in SAMPLINGS}
                for arm in ("baseline-motion", "normal", "fine", "coarse")}

    def test_grain_speed_and_mean_width_invariants(self):
        require_cross_checks(self.cross_fixture())
        result = self.cross_fixture()
        result["coarse"][SAMPLINGS[0]]["upper_pixels_per_second"] = 160
        with self.assertRaises(ValueError): require_cross_checks(result)
        result = self.cross_fixture()
        result["normal"][SAMPLINGS[0]]["shape_samples"][0]["mean_width"] = 17
        with self.assertRaises(ValueError): require_cross_checks(result)

    def wrap_fixture(self, folder, mode="moving"):
        base = np.broadcast_to((100 + np.arange(32, dtype=np.uint8) * 2)[:, None, None], (32,32,3)).copy()
        frames = []
        for i in range(25):
            pixels = base if mode == "frozen" else np.roll(base, i, axis=0)
            if mode == "jump" and i >= 12: pixels = pixels + 60
            name = "%04d.png" % i; Image.fromarray(pixels).save(folder / name)
            frames.append(dict(time=i/60, file=name,
                               geometry=dict(cx=16, half_width=5, outlet_y=2, surface_y=20)))
        report = dict(completed=True, frames=frames, boundary_time=.2, boundary_type="birth")
        path = folder / "motion.json"; path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_wrap_accepts_moving_continuity(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(inspect_wrap(self.wrap_fixture(Path(temp)))["passed"])

    def test_wrap_rejects_frozen_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                inspect_wrap(self.wrap_fixture(Path(temp), "frozen"))

    def test_wrap_rejects_visible_reset_jump(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(inspect_wrap(self.wrap_fixture(Path(temp), "jump"))["passed"])

    def test_wrap_requires_boundary_inside_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.wrap_fixture(Path(temp)); report = json.loads(path.read_text(encoding="utf-8"))
            report["boundary_time"] = 99; path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(ValueError): inspect_wrap(path)


if __name__ == "__main__":
    unittest.main()
