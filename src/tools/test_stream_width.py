"""Pure PNG fixtures for the visible-width tool; never creates a GL window."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from measure_stream_width import measure_report, read_motion, report_metrics, require_nearfield


BG = (234, 243, 248)
GOLD = (232, 177, 101)
HIDDEN = {"IsWindow": True, "IsWindowVisible": False, "IsIconic": False}


def fixture(folder, width_at_depth=lambda depth: 40, *, isolated=True, lone=False,
            background=False, surface=180, outlet=35, splash=False):
    folder.mkdir(parents=True, exist_ok=True)
    report = {"completed": True, "version": "fixture", "period": 50, "grain": 3,
              "dimensions": [120, 200], "sample_rate": 60, "expected_frames": 5,
              "source_files": {"main.py": "a" * 64}, "source_files_end": {"main.py": "a" * 64},
              "hidden_at_creation": HIDDEN, "hidden_at_finish": HIDDEN, "frames": []}
    for index in range(5):
        normal = np.full((200, 120, 3), BG, dtype=np.uint8)
        stream = normal.copy()
        if not background:
            normal[0:outlet, 40:80] = GOLD
            for y in range(outlet, min(200, surface)):
                width = int(round(width_at_depth(y - outlet)))
                left = 60 - width // 2
                normal[y, left:left + width] = GOLD
                stream[y, left:left + width] = GOLD
            if lone:
                normal[outlet + 3:outlet + 12, 86:89] = GOLD
                stream[outlet + 3:outlet + 12, 86:89] = GOLD
            if splash:
                normal[outlet + 100:outlet + 130, 30:90] = GOLD
        frame = {"time": 8 + index / 60, "elapsed": 8 + index / 60,
                 "file": "%04d.png" % index, "native_hidden": HIDDEN,
                 "geometry": {"cx": 60, "outlet_y": outlet, "surface_y": surface,
                              "half_width": 20, "radius": 55, "background_rgb": list(BG)}}
        for image, name, hash_key in ((normal, frame["file"], "sha256"),
                                      (stream, "stream-%04d.png" % index, "stream_sha256")):
            if hash_key == "stream_sha256" and not isolated:
                continue
            Image.fromarray(image).save(folder / name)
            frame[hash_key] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
            if hash_key == "stream_sha256":
                frame["stream_file"] = name
        report["frames"].append(frame)
    (folder / "motion.json").write_text(json.dumps(report), encoding="utf-8")
    return report


def write_report(folder, report):
    (folder / "motion.json").write_text(json.dumps(report), encoding="utf-8")


class StreamWidthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_full_body_passes_and_keeps_strength_profiles(self):
        fixture(self.folder)
        metrics = report_metrics(self.folder)
        self.assertTrue(require_nearfield(metrics))
        self.assertEqual(metrics["nearfield"]["ratio"]["p50"], 1)
        self.assertEqual(metrics["body_source"], "isolated_stream_gl")
        self.assertIsNone(metrics["visual_verdict"])
        self.assertIn("column_strength", metrics["profiles"][0]["rows"][0])

    def test_narrow_step_fails_without_hiding_behind_full_neck(self):
        fixture(self.folder, lambda depth: 34)
        metrics = report_metrics(self.folder)
        self.assertEqual(metrics["nearfield"]["ratio"]["p50"], 0.85)
        with self.assertRaises(AssertionError):
            require_nearfield(metrics)
        with self.assertRaises(AssertionError):
            require_nearfield(metrics, pixel_slack=1)

    def test_smooth_contraction_preserves_nearfield_and_reports_profile(self):
        fixture(self.folder, lambda depth: max(34, 40 - depth / 20))
        metrics = report_metrics(self.folder)
        self.assertTrue(require_nearfield(metrics))
        rows = metrics["profiles"][0]["rows"]
        self.assertGreater(rows[15]["width"], rows[-10]["width"])
        self.assertTrue(metrics["waist"]["diagnostic_only"])

    def test_distant_lone_grain_does_not_widen_body(self):
        fixture(self.folder, lambda depth: 34, lone=True)
        metrics = report_metrics(self.folder)
        self.assertEqual(metrics["nearfield"]["stream_width"]["p50"], 34)
        with self.assertRaises(AssertionError):
            require_nearfield(metrics)

    def test_single_central_grain_is_not_a_body(self):
        fixture(self.folder, lambda depth: 3)
        metrics = report_metrics(self.folder)
        self.assertEqual(metrics["nearfield"]["stream_width"]["p50"], 0)
        with self.assertRaises(AssertionError):
            require_nearfield(metrics)

    def test_background_is_not_a_stream(self):
        fixture(self.folder, background=True)
        metrics = report_metrics(self.folder)
        self.assertEqual(metrics["nearfield"]["valid_frames"], 0)
        with self.assertRaises(AssertionError):
            require_nearfield(metrics)

    def test_pile_truncation_is_not_a_valid_nearfield(self):
        fixture(self.folder, surface=44)
        metrics = report_metrics(self.folder)
        self.assertEqual(metrics["nearfield"]["valid_frames"], 0)
        with self.assertRaises(AssertionError):
            require_nearfield(metrics)

    def test_upper_canvas_truncation_is_rejected(self):
        fixture(self.folder, outlet=5)
        with self.assertRaises(AssertionError):
            require_nearfield(report_metrics(self.folder))

    def test_isolated_body_excludes_splash_from_waist(self):
        fixture(self.folder, lambda depth: 26 if 107 <= depth < 115 else 40, splash=True)
        metrics = report_metrics(self.folder)
        waist = metrics["frames"][0]["local_waist"]
        self.assertIsNotNone(waist)
        self.assertLess(waist["ratio_to_neighbors"], 0.75)
        self.assertTrue(metrics["waist"]["splashes_excluded"])

    def test_plain_reference_is_explicit_and_can_fail_old_width(self):
        fixture(self.folder, lambda depth: 34, isolated=False)
        metrics = report_metrics(self.folder)
        self.assertTrue(metrics["plain"])
        self.assertEqual(metrics["body_source"], "raw_gold_reference")
        self.assertFalse(metrics["waist"]["splashes_excluded"])
        with self.assertRaises(AssertionError):
            require_nearfield(metrics)

    def test_hash_mutation_before_or_after_read_is_rejected(self):
        fixture(self.folder)
        report = read_motion(self.folder)
        (self.folder / "stream-0000.png").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            read_motion(self.folder)
        with self.assertRaises(ValueError):
            measure_report(report)

    def test_incomplete_changed_source_visible_and_missing_frames_rejected(self):
        original = fixture(self.folder)
        for mutate in (lambda r: r.update(completed=False),
                       lambda r: r["source_files_end"].update({"main.py": "b" * 64}),
                       lambda r: r["frames"][0].update(native_hidden={**HIDDEN, "IsWindowVisible": True}),
                       lambda r: r["frames"].pop(),
                       lambda r: r["frames"][1].update(time=9, elapsed=9)):
            report = copy.deepcopy(original)
            mutate(report)
            write_report(self.folder, report)
            with self.assertRaises(ValueError):
                read_motion(self.folder)

    def test_unsafe_or_partial_stream_paths_rejected(self):
        original = fixture(self.folder)
        for mutate in (lambda r: r["frames"][0].update(stream_file="../escape.png"),
                       lambda r: r["frames"][0].update(stream_file="C:/outside.png"),
                       lambda r: r["frames"][0].pop("stream_file"),
                       lambda r: (r["frames"][0].pop("stream_file"),
                                  r["frames"][0].pop("stream_sha256"))):
            report = copy.deepcopy(original)
            mutate(report)
            write_report(self.folder, report)
            with self.assertRaises(ValueError):
                read_motion(self.folder)


if __name__ == "__main__":
    unittest.main()
