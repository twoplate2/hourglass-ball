"""Positive and negative controls for related-test selection and release blocking."""

import base64
from copy import deepcopy
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import check_design_pages as pages
import run_regression as gate


class GateTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "core": ["base"], "tests": [{"id": "base"}, {"id": "other"}],
            "rules": [
                {"paths": ["a.py"], "tests": ["other"], "reason": "A affects B"},
                {"paths": ["docs/**"], "tests": [], "reason": "docs only"}]}
        self.test = {"id": "fixture", "markers": ["DONE"]}

    def ids(self, paths, profile="changed"):
        return [test["id"] for test in gate.select_tests(self.policy, paths, profile)[0]]

    def test_change_a_also_tests_b(self):
        self.assertEqual(self.ids(["a.py"]), ["base", "other"])

    def test_unknown_file_runs_everything(self):
        self.assertEqual(self.ids(["new.py"]), ["base", "other"])

    def test_docs_keep_core(self):
        self.assertEqual(self.ids(["docs/note.md"]), ["base"])

    def test_full_ignores_change_filter(self):
        self.assertEqual(self.ids(["docs/note.md"], "full"), ["base", "other"])

    def test_real_runtime_and_test_changes_run_all(self):
        policy = gate.load_policy()
        expected = [test["id"] for test in policy["tests"]]
        for path in ("main.py", "src/app/flow_numpy.py", "src/tools/test_pack_equiv.py",
                     "ui/icon.png", "sounds/clock.wav", "new_module.py"):
            with self.subTest(path=path):
                selected, _ = gate.select_tests(policy, [path], "changed")
                self.assertEqual([test["id"] for test in selected], expected)

    def test_success_control(self):
        self.assertEqual(gate.validate_result(self.test, 0, "DONE"), [])

    def test_failure_exit_cannot_be_hidden(self):
        self.assertTrue(gate.validate_result(self.test, 1, "DONE"))

    def test_timeout_never_passes(self):
        self.assertTrue(gate.validate_result(self.test, 0, "DONE", True))

    def test_silent_early_return_never_passes(self):
        self.assertTrue(gate.validate_result(self.test, 0, ""))

    def test_traceback_and_failure_never_pass(self):
        for text in ("DONE\nTraceback (most recent call last):",
                     "DONE\nFAIL something", "DONE\nFAILED: x"):
            self.assertTrue(gate.validate_result(self.test, 0, text))

    def test_shorter_suite_and_skip_never_pass(self):
        test = {"unittest_min": 5}
        self.assertEqual(gate.validate_result(test, 0, "Ran 5 tests in 0.1s\nOK\n"), [])
        for text in ("Ran 4 tests in 0.1s\nOK\n", "Ran 5 tests in 0.1s\nOK (skipped=1)\n"):
            self.assertTrue(gate.validate_result(test, 0, text))

    def test_missing_assertions_never_pass(self):
        test = {"markers": ["DONE"], "pass_min": 2}
        self.assertTrue(gate.validate_result(test, 0, "PASS a\nDONE"))
        self.assertEqual(gate.validate_result(test, 0, "PASS a\nPASS b\nDONE"), [])

    def test_collects_committed_and_untracked_changes(self):
        with patch.object(gate, "git", side_effect=["old.py\0new.py\0", "extra.py\0"]):
            self.assertEqual(gate.changed_paths("base"), ["extra.py", "new.py", "old.py"])

    def test_source_changes_invalidate_fingerprint(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            path = root / "main.py"
            path.write_text("old", encoding="utf-8")
            before = gate.fingerprint(root)
            path.write_text("new", encoding="utf-8")
            self.assertNotEqual(before, gate.fingerprint(root))
            current = gate.fingerprint(root)
            output = root / "_temp"
            output.mkdir()
            (output / "log.txt").write_text("result", encoding="utf-8")
            self.assertEqual(current, gate.fingerprint(root))

    def test_real_subprocess_success_and_failure(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            run = root / "run"
            run.mkdir()
            script = root / "fixture.py"
            test = {"id": "fixture", "command": ["fixture.py"], "timeout": 5,
                    "markers": ["DONE"]}
            with patch.object(gate, "ROOT", root):
                for source, status in (("print('DONE')", "PASS"),
                                       ("print('DONE'); raise SystemExit(1)", "FAIL"),
                                       ("print('early return')", "FAIL")):
                    script.write_text(source, encoding="utf-8")
                    self.assertEqual(gate.run_test(test, run, os.environ.copy())["status"], status)

    def test_real_subprocess_timeout(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            run = root / "run"
            run.mkdir()
            (root / "fixture.py").write_text("import time; time.sleep(10)", encoding="utf-8")
            test = {"id": "fixture", "command": ["fixture.py"], "timeout": 0.1,
                    "markers": ["DONE"]}
            with patch.object(gate, "ROOT", root):
                result = gate.run_test(test, run, os.environ.copy())
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(result["timed_out"])

    def make_review(self, root):
        evidence = root / "evidence.txt"
        evidence.write_text("positive control", encoding="utf-8")
        return {"fingerprint": "current", "items": [
            {"id": "visual", "status": "PASS", "reviewer": "fixture",
             "evidence": [{"path": "evidence.txt", "sha256": gate.digest(evidence)}]}]}

    def test_manual_evidence_positive_control(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            self.assertEqual(gate.review_errors(self.make_review(root), "current",
                                               [{"id": "visual"}], root), [])

    def test_manual_missing_stale_or_empty_never_passes(self):
        manual = [{"id": "visual"}]
        for review in (None, {"fingerprint": "old"}, {"fingerprint": "current", "items": []}):
            self.assertTrue(gate.review_errors(review, "current", manual))

    def test_modified_and_deleted_evidence_never_passes(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            review = self.make_review(root)
            (root / "evidence.txt").write_text("changed", encoding="utf-8")
            self.assertTrue(gate.review_errors(review, "current", [{"id": "visual"}], root))
            (root / "evidence.txt").unlink()
            self.assertTrue(gate.review_errors(review, "current", [{"id": "visual"}], root))

    def test_duplicate_review_items_never_pass(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            review = self.make_review(root)
            review["items"] *= 2
            self.assertTrue(gate.review_errors(review, "current", [{"id": "visual"}], root))

    def test_full_report_control_and_negative_controls(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            log = root / "check.log"
            log.write_text("DONE\n", encoding="utf-8")
            policy = {"tests": [self.test], "manual": [{"id": "visual"}]}
            report = {"schema": 1, "profile": "full", "fingerprint": "current",
                      "automation_only": False, "review": self.make_review(root),
                      "results": [{"id": "fixture", "status": "PASS", "returncode": 0,
                                   "timed_out": False, "log": "check.log",
                                   "log_sha256": gate.digest(log)}]}
            self.assertEqual(gate.report_errors(report, policy, "current", root), [])
            for key, value in (("profile", "core"), ("automation_only", True),
                               ("results", []), ("fingerprint", "old"), ("review", None)):
                bad = deepcopy(report)
                bad[key] = value
                self.assertTrue(gate.report_errors(bad, policy, "current", root))
            log.write_text("DONE\nFAIL B\n", encoding="utf-8")
            self.assertTrue(gate.report_errors(report, policy, "current", root))
            report["results"][0]["log_sha256"] = gate.digest(log)
            self.assertTrue(gate.report_errors(report, policy, "current", root))

    def test_html_missing_image_and_bad_embedded_image(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            page = root / "page.html"
            for source in ("missing.png", "data:image/png;base64,aW52YWxpZA=="):
                page.write_text('<img src="%s">' % source, encoding="utf-8")
                self.assertTrue(pages.check_page(page, root)["failures"])

    def test_html_local_and_embedded_positive_control(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            data = io.BytesIO()
            Image.new("RGB", (2, 2), "red").save(data, "PNG")
            (root / "image.png").write_bytes(data.getvalue())
            page = root / "page.html"
            page.write_text('<h1 id="test">Test</h1><a href="#test">jump</a>'
                            '<img src="image.png"><img src="data:image/png;base64,%s">' %
                            base64.b64encode(data.getvalue()).decode(), encoding="utf-8")
            result = pages.check_page(page, root)
            self.assertEqual(result["failures"], [])
            self.assertEqual(result["images"], 2)
            page.write_text('<a href="#missing">bad anchor</a>', encoding="utf-8")
            self.assertTrue(pages.check_page(page, root)["failures"])


if __name__ == "__main__":
    unittest.main()
