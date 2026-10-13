"""Windowless checks for resource paths and repository organization."""

import ast
from configparser import ConfigParser
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import _pack_source_md as bundle
import _temp as temporary
import build_sand_flow_docs as sand_docs
import run_regression as regression


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.spec = ConfigParser()
        self.spec.read(ROOT / "buildozer.spec", encoding="utf-8")

    def test_build_resources_exist(self):
        for key in ("version.filename", "icon.filename", "presplash.filename", "p4a.hook"):
            with self.subTest(key=key):
                self.assertTrue((ROOT / self.spec.get("app", key)).is_file())
        self.assertTrue((ROOT / "ui/fonts/NotoSansSC-Medium.otf").is_file())
        self.assertEqual(self.spec.get("app", "version.filename"), "./src/app/app_version.py")

    def test_audio_paths_are_playable(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        constants = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "WAV_FILENAME" in names:
                constants["WAV_FILENAME"] = ast.literal_eval(node.value)
            if "SOUND_EFFECTS" in names:
                for item in node.value.elts:
                    value = item.elts[1]
                    filename = constants[value.id] if isinstance(value, ast.Name) else ast.literal_eval(value)
                    self.assertTrue(filename.startswith("sounds/"))
                    with wave.open(str(ROOT / filename), "rb") as sound:
                        self.assertGreater(sound.getnframes(), 0)

    def test_development_data_excluded_from_apk(self):
        excluded = {item.strip().lower() for item in
                    self.spec.get("app", "source.exclude_dirs").split(",")}
        for directory in ("docs", "design", "_temp", "sounds/source", "src/tools"):
            self.assertIn(directory, excluded)
        extensions = self.spec.get("app", "source.include_exts").split(",")
        self.assertNotIn("mp3", extensions)

    def test_current_document_links_exist(self):
        documents = [ROOT / name for name in ("README.md", "CLAUDE.md", "AGENTS.md")]
        documents += [ROOT / "docs/README.md", ROOT / "docs/structure.md",
                      ROOT / "docs/file-index.md", ROOT / "src/tools/README.md"]
        documents += list((ROOT / "design").rglob("*.md"))
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / target.split("#", 1)[0]).exists())

    def test_regression_policy_and_index(self):
        policy = regression.load_policy()
        required = {"layout", "version", "gate", "static", "design-pages", "physics",
                    "packing", "finish", "application", "application-cancel", "menu-levels",
                    "grain-levels", "geometry", "rendering", "splash"}
        self.assertTrue(required.issubset({test["id"] for test in policy["tests"]}))
        self.assertTrue({"layout", "version", "gate", "static", "design-pages"}.issubset(
            policy["core"]))
        self.assertTrue(regression.POLICY.is_file())
        index = (ROOT / "docs/file-index.md").read_text(encoding="utf-8")
        self.assertIn(regression.POLICY.relative_to(ROOT).as_posix(), index)
        self.assertEqual({item["id"] for item in policy["manual"]},
                         {"visual", "audio", "performance"})
        case_path = ROOT / policy["visual_case_file"]
        self.assertIn(case_path.relative_to(ROOT).as_posix(), index)
        catalog = json.loads(case_path.read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema"], 1)
        self.assertEqual({group["id"] for group in catalog["groups"]},
                         {"upper", "lower", "neck", "upper-neck", "neck-stream",
                          "stream", "landing", "state", "cross"})
        cases = [case for group in catalog["groups"] for case in group["cases"]]
        ids = {case["id"] for case in cases}
        self.assertEqual(len(ids), len(cases))
        self.assertGreaterEqual(len(cases), 33)
        self.assertEqual(set(catalog["case_images"]), ids)
        whole_ids = {"U00", "L00", "N00", "UN00", "NS00", "S00", "SL00", "C00", "X00"}
        self.assertEqual({case["id"] for case in cases if case.get("view") == "whole"},
                         whole_ids)
        automatic = {test["id"] for test in policy["tests"]}
        manual = {item["id"] for item in policy["manual"]}
        for group in catalog["groups"]:
            self.assertTrue(group["images"])
            for image in group["images"]:
                self.assertTrue((ROOT / image["path"]).is_file())
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["title"])
                self.assertTrue(case["setup"])
                self.assertTrue(case["steps"] and case["expected"] and case["evidence"])
                self.assertTrue(set(case["stages"]).issubset(catalog["stages"]))
                self.assertTrue(set(case["automated"]).issubset(automatic))
                self.assertTrue(set(case["manual"]).issubset(manual))
                self.assertTrue(set(case["related"]).issubset(ids))
                self.assertTrue(catalog["case_images"][case["id"]])
                for image in catalog["case_images"][case["id"]]:
                    self.assertTrue((ROOT / image["path"]).is_file())
                    self.assertTrue(image["path"].startswith("design/images/mumu-2.96-20261013/"))
                    if "crop" in image:
                        self.assertTrue((ROOT / image["source"]).is_file())
                for movie in catalog.get("case_videos", {}).get(case["id"], []):
                    self.assertTrue((ROOT / movie["path"]).is_file())
                    self.assertTrue((ROOT / movie["poster"]).is_file())
                    self.assertGreater(movie["width"], 0)
                    self.assertGreater(movie["height"], 0)
                    self.assertIsInstance(movie["loop"], bool)
        self.assertTrue({"U02", "L02", "N02", "N03", "UN02", "NS02", "S02", "SL02",
                         "C00", "C01", "C03"}.issubset(catalog["case_videos"]))
        self.assertTrue(all(not movie["loop"] for movie in catalog["case_videos"]["C00"]))
        self.assertTrue(all(not movie["loop"] for movie in catalog["case_videos"]["C03"]))
        for path in (ROOT / "design/checks/records").glob("v*/[0-9]*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            snapshot = json.loads((ROOT / record["rules_snapshot"]).read_text(encoding="utf-8"))
            snapshot_ids = {case["id"] for group in snapshot["groups"] for case in group["cases"]}
            sand_docs.validate_results(record, snapshot_ids)

    def test_sand_records_reject_missing_and_invalid_results(self):
        record = {"results": {"U00": {"status": "not_tested", "actual": "未执行"}}}
        sand_docs.validate_results(record, {"U00"})
        with self.assertRaises(ValueError):
            sand_docs.validate_results(record, {"U00", "U01"})
        record["results"]["U00"]["status"] = "PASS"
        with self.assertRaises(ValueError):
            sand_docs.validate_results(record, {"U00"})
        record["results"]["U00"] = {"status": "passed", "actual": "声称通过", "evidence": []}
        with self.assertRaises(ValueError):
            sand_docs.validate_results(record, {"U00"})

    def test_sand_new_record_includes_every_id_and_does_not_overwrite(self):
        data = {"revision": "test", "groups": [{"cases": [{"id": "U00"}, {"id": "U01"}]}]}
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            with patch.multiple(sand_docs, ROOT=root, CHECKS=root / "design/checks"):
                path = sand_docs.new_record(data, "2.97", "2026-10-13")
                record = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(set(record["results"]), {"U00", "U01"})
                self.assertTrue(all(row["status"] == "not_tested"
                                    for row in record["results"].values()))
                with self.assertRaises(ValueError):
                    sand_docs.new_record(data, "2.97", "2026-10-13")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), record)

    def test_sand_video_controls_loop_only_when_requested(self):
        movie = {"path": "design/media/example.mp4",
                 "poster": "design/images/example.png", "width": 320, "height": 480,
                 "caption": "真实录像", "loop": True}
        output = ROOT / "design/checks/rules/sand-flow.html"
        markup = sand_docs.videos([movie], output)
        self.assertIn(" controls muted ", markup)
        self.assertIn('preload="none"', markup)
        self.assertIn(" loop ", markup)
        self.assertNotIn("autoplay", markup)
        movie["loop"] = False
        self.assertNotIn(" loop ", sand_docs.videos([movie], output))

    def test_design_categories(self):
        design = ROOT / "design"
        self.assertFalse(list(design.glob("*.html")))
        for directory in ("pages", "history", "images", "media", "checks", "old-assets"):
            with self.subTest(directory=directory):
                self.assertTrue((design / directory).is_dir())
        for path in design.rglob("*"):
            with self.subTest(path=path.relative_to(design)):
                self.assertTrue(path.name.isascii(), "Use English names under design/")
        self.assertTrue((design / "pages/hourglass.html").is_file())
        self.assertTrue((design / "checks/rules/sand-flow.html").is_file())
        checks = design / "checks"
        self.assertEqual({path.name for path in checks.iterdir() if path.is_dir()},
                         {"rules", "records", "notes"})
        for name in ("index.html", "rules/how-to-check.md", "records/index.html",
                     "notes/index.html"):
            self.assertTrue((checks / name).is_file())

    def test_root_has_only_entry_documents(self):
        self.assertEqual({path.name for path in ROOT.glob("*.md")},
                         {"README.md", "AGENTS.md", "CLAUDE.md"})
        for old in ("mp3", "fonts", "p4a", "Temp", "_kivyhome", "benchmark_logs",
                    "app", "tools", "_shot"):
            with self.subTest(old=old):
                self.assertFalse((ROOT / old).exists())

    def test_source_files_have_index_entries(self):
        index = (ROOT / "docs/file-index.md").read_text(encoding="utf-8")
        for path in (ROOT / "src").rglob("*"):
            if path.is_file() and path.suffix in (".py", ".sh", ".json", ".md"):
                with self.subTest(path=path.name):
                    self.assertIn(path.relative_to(ROOT).as_posix(), index)

    def test_bundle_uses_current_runtime_files_only(self):
        paths = bundle.discover_code()
        self.assertIn("src/app/app_version.py", paths)
        self.assertIn("src/app/sand_flow_material.py", paths)
        self.assertFalse(any(path.startswith(("_temp/", "docs/")) for path in paths))
        self.assertEqual(bundle.discover_docs(), [])
        with tempfile.TemporaryDirectory() as home, redirect_stdout(io.StringIO()):
            output = Path(home) / "generated" / "source.md"
            bundle.main(["--output", str(output)])
            text = output.read_text(encoding="utf-8")
            self.assertTrue("## `src/app/app_version.py`" in text)
            self.assertTrue((ROOT / "src/app/app_version.py").read_text(encoding="utf-8").rstrip() in text)

    def test_user_review_files_not_cleanup_candidates(self):
        paths = [temporary.TEMP / name for name in
                 ("benchmark_logs", "retired-docs", "retired-tools", "pycache")]
        self.assertEqual(temporary.cleanup_candidates(paths, "clear", 0), paths[-1:])
        with self.assertRaises(ValueError):
            temporary.remove_entry(ROOT / "main.py")


if __name__ == "__main__":
    unittest.main()
