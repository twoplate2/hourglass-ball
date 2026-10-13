"""Positive/negative evidence checks for the scoped Pymunk push gate."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import check_pymunk_release as gate


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.root = Path(self.home.name)
        self.override = patch.object(gate, "ROOT", self.root)
        self.override.start()
        self.addCleanup(self.override.stop)
        sources = {}
        for name in ("main.py", "buildozer.spec", "src/tools/verify_pymunk_integration.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
            sources[name] = gate.digest(path)
        frames = []
        for name in ("initial", "opening", "first-landing", "stream", "middle", "late", "tail",
                     "timer-end", "drained", "duration-1", "duration-5", "duration-36000",
                     "landscape", "resized", "reset", "grain-fine", "grain-coarse"):
            path = self.root / (name + ".png")
            image = Image.new("RGB", (128, 128), "white")
            image.paste("black", (0, 0, 64, 128))
            image.save(path)
            frames.append({"path": path.name, "sha256": gate.digest(path)})
        self.report = {"scope": "pymunk-integration-pc", "full": True, "finished": True,
                       "version": "3.1", "checks": [{"id": key, "passed": True}
                                                  for key in sorted(gate.REQUIRED)],
                       "frames": frames, "sources": sources}
        self.review = {"scope": "pymunk-integration-pc", "appearance": "passed",
                       "reviewer": "fixture", "notes": "Fixture, not actual appearance approval.",
                       "report": "report.json"}

    def write_report(self):
        path = self.root / "report.json"
        path.write_text(json.dumps(self.report), encoding="utf-8")
        self.review["report_sha256"] = gate.digest(path)
        return self.review

    def validate(self):
        return gate.validate(self.write_report(), committed=False)

    def test_accepts_complete_bound_evidence(self):
        self.assertEqual(self.validate()["version"], "3.1")

    def test_rejects_interrupted_and_partial_runs(self):
        original = copy.deepcopy(self.report)
        for field in ("full", "finished"):
            self.report = copy.deepcopy(original)
            self.report[field] = False
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate()

    def test_rejects_missing_or_failed_check(self):
        self.report["checks"][0]["passed"] = False
        with self.assertRaises(ValueError):
            self.validate()
        self.report["checks"] = self.report["checks"][1:]
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_changed_source(self):
        (self.root / "main.py").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_missing_source_identity(self):
        del self.report["sources"]["main.py"]
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_missing_stage_and_blank_image(self):
        frame = self.report["frames"].pop()
        with self.assertRaises(ValueError):
            self.validate()
        self.report["frames"].append(frame)
        path = self.root / frame["path"]
        Image.new("RGB", (128, 128), "white").save(path)
        frame["sha256"] = gate.digest(path)
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_stale_review_and_changed_capture(self):
        review = self.write_report()
        (self.root / "report.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            gate.validate(review, committed=False)
        path = self.root / self.report["frames"][0]["path"]
        path.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_unreviewed_appearance_and_path_escape(self):
        self.review["appearance"] = "pending"
        with self.assertRaises(ValueError):
            self.validate()
        self.review["appearance"] = "passed"
        self.report["frames"][0]["path"] = "../outside.png"
        with self.assertRaises(ValueError):
            self.validate()

    def test_checks_pushed_commit_content(self):
        with patch.object(gate.subprocess, "check_output", return_value=b"fixture\r\n"):
            gate.validate(self.write_report(), committed=True)
        with patch.object(gate.subprocess, "check_output", return_value=b"old\n"):
            with self.assertRaises(ValueError):
                gate.validate(self.write_report(), committed=True)


if __name__ == "__main__":
    unittest.main()
