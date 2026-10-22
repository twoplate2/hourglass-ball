"""Negative controls for the scoped persistent-particle release gate; no GL."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import check_particle_release as gate
from benchmark_particle_renderer import SUGGESTED_BUDGET, summarize


HIDDEN = {"IsWindow": True, "IsWindowVisible": False, "IsIconic": False,
          "window_state_config": "hidden"}


def timing_fixture():
    start = dict(application_clock=1025.0, elapsed=25.0, duration=50.0,
                 dimensions=[1080.0, 1848.0], running=True)
    runs = [dict(round=i+1, completed=True, passed=True, frame_ms=[5.0]*300,
                 summary=summarize([5.0]*300), start_state=copy.deepcopy(start)) for i in range(3)]
    return dict(completed=True, passed=True, status="completed",
        completion_marker="PARTICLE_RENDERER_BENCHMARK_COMPLETE",
        protocol=dict(rounds=3, frames_per_round=300, logical_step_seconds=1/60),
        suggested_budget=dict(SUGGESTED_BUDGET), runs=runs, summary=summarize([5.0]*900))


class CaptureControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        manifest = {"main.py": "a"*64, "src/app/app_version.py": "b"*64}
        frames = []
        for i in range(2):
            path = self.root / (str(i)+".png")
            image = Image.new("RGB", (9, 24), (234, 243, 248))
            ImageDraw.Draw(image).rectangle((3, 0, 5, 23), fill=(210+i, 160, 90))
            image.save(path)
            frames.append(dict(file=path.name, sha256=gate.sha(path), time=1+i/60,
                native_hidden=copy.deepcopy(HIDDEN),
                geometry=dict(cx=4.5, outlet_y=0.0, surface_y=24.0, half_width=2.0,
                              background_rgb=[234, 243, 248]),
                draw_state=dict(particles=12, rendered_particles=12, active=True, birth_identity="pwp+ptl")))
        self.report = dict(completed=True, version="3.7", renderer="particle_sprite_pool", grain=3,
            period=50, source_files=manifest, source_files_end=dict(manifest),
            hidden_at_creation=copy.deepcopy(HIDDEN), hidden_at_finish=copy.deepcopy(HIDDEN),
            expected_frames=2, sample_rate=60, dimensions=[9,24], frames=frames,
            continuity=dict(passed=True, births=3, contacts=2, survivor_steps=20, mesh_frames=2))

    def check(self):
        gate.require_trace(self.report, self.root, 3, 2, 1, 1+1/60)

    def test_complete_trace(self):
        self.check()

    def test_incomplete(self):
        self.report["completed"] = False
        with self.assertRaises(ValueError): self.check()

    def test_wrong_renderer(self):
        self.report["renderer"] = "short_grain_field"
        with self.assertRaises(ValueError): self.check()

    def test_wrong_version(self):
        self.report["version"] = "3.6"
        with self.assertRaises(ValueError): self.check()

    def test_visible_window(self):
        self.report["frames"][1]["native_hidden"]["IsWindowVisible"] = True
        with self.assertRaises(ValueError): self.check()

    def test_changed_source(self):
        self.report["source_files_end"]["main.py"] = "f"*64
        with self.assertRaises(ValueError): self.check()

    def test_missing_pixels(self):
        (self.root/"1.png").unlink()
        with self.assertRaises(ValueError): self.check()

    def test_changed_pixels(self):
        Image.new("RGB", (3,5), "red").save(self.root/"1.png")
        with self.assertRaises(ValueError): self.check()

    def test_missing_frame(self):
        self.report["frames"].pop()
        with self.assertRaises(ValueError): self.check()

    def test_timestamp_gap(self):
        self.report["frames"][1]["time"] += .1
        with self.assertRaises(ValueError): self.check()

    def test_duplicate_frame(self):
        self.report["frames"][1].update(file="0.png", sha256=self.report["frames"][0]["sha256"])
        with self.assertRaises(ValueError): self.check()

    def test_missing_trace(self):
        self.report.pop("continuity")
        with self.assertRaises(ValueError): self.check()

    def test_missing_birth_contact_witness(self):
        self.report["continuity"]["contacts"] = 0
        with self.assertRaises(ValueError): self.check()

    def test_mesh_particle_mismatch(self):
        self.report["frames"][0]["draw_state"]["rendered_particles"] = 11
        with self.assertRaises(ValueError): self.check()

    def test_particles_but_actual_pixels_are_background(self):
        path = self.root / "1.png"
        Image.new("RGB", (9,24), (234,243,248)).save(path)
        self.report["frames"][1]["sha256"] = gate.sha(path)
        with self.assertRaisesRegex(ValueError, "invisible in actual pixels"):
            self.check()

    def test_short_contact_gap_is_not_false_invisible(self):
        frame = self.report["frames"][0]
        frame["geometry"]["surface_y"] = 3.0
        self.assertIsNone(gate.require_stream_pixels(frame, self.root))


class TimingControls(unittest.TestCase):
    def test_complete_raw_timing(self):
        self.assertEqual(gate.require_timing(timing_fixture(), candidate=True)["frames"], 900)

    def test_partial_round(self):
        r = timing_fixture(); r["runs"][1]["frame_ms"].pop()
        with self.assertRaises(ValueError): gate.require_timing(r, candidate=True)

    def test_cancelled(self):
        r = timing_fixture(); r["completed"] = False
        with self.assertRaises(ValueError): gate.require_timing(r, candidate=True)

    def test_forged_summary(self):
        r = timing_fixture(); r["runs"][0]["frame_ms"][0] = 15
        with self.assertRaises(ValueError): gate.require_timing(r, candidate=True)

    def test_slow_raw_timing(self):
        r = timing_fixture()
        for run in r["runs"]:
            run.update(frame_ms=[100.0]*300, summary=summarize([100.0]*300))
        r["summary"] = summarize([100.0]*900)
        with self.assertRaises(ValueError): gate.require_timing(r, candidate=True)
        self.assertEqual(gate.require_timing(r, candidate=False)["max_frame_ms"], 100)

    def test_changed_start(self):
        r = timing_fixture(); r["runs"][1]["start_state"]["application_clock"] += .1
        with self.assertRaises(ValueError): gate.require_timing(r, candidate=True)


class SourceControls(unittest.TestCase):
    def test_sources_bind_saved_and_current(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"root"; saved = Path(temp)/"saved"
            manifest = {}
            for name, content in (("main.py", b"# runtime\n"), ("src/app/app_version.py", b'APP_VERSION = "3.7"\n')):
                for folder in (root, saved):
                    p = folder/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(content)
                manifest[name] = hashlib.sha256(content).hexdigest()
            with patch.object(gate, "ROOT", root):
                gate.require_sources(saved, manifest)
                (root/"main.py").write_text("# changed", encoding="utf-8")
                with self.assertRaises(ValueError): gate.require_sources(saved, manifest)

    def test_missing_review(self):
        with tempfile.TemporaryDirectory() as temp:
            record = Path(temp)/"review.json"
            record.write_text(json.dumps({"appearance":"passed"}), encoding="utf-8")
            with self.assertRaises(ValueError): gate.validate(record)


if __name__ == "__main__":
    unittest.main()
