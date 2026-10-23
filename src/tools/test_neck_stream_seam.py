"""NS03 diagnostic controls; no numerical measurement grants visual approval."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from measure_neck_stream_seam import file_hash, from_capture, measure

ROOT = Path(__file__).resolve().parents[2]


def textured_image(width=160, height=220, boundary=None, shift=0, amplitude=1):
    x = np.arange(width)
    noise = ((x % 4) - 1.5)[None, :, None]
    pixels = np.full((height, width, 3), 130., dtype=float) + noise * 2
    if boundary is not None:
        pixels[boundary:] = 130. + noise * 2 * amplitude + shift
    return Image.fromarray(np.rint(pixels).astype(np.uint8), "RGB")


class NeckStreamSeamDiagnostics(unittest.TestCase):
    def test_continuous_material_has_no_colour_or_texture_step(self):
        result = measure(textured_image(), dict(cx=80, outlet_y=110, half_width=20))
        row = result["at_outlet"]
        self.assertEqual(row["rgb_delta_255"], 0)
        self.assertEqual(row["texture_ratio_below_over_above"], 1)

    def test_colour_and_texture_steps_are_measured_independently(self):
        geometry = dict(cx=80, outlet_y=110, half_width=20)
        colour = measure(textured_image(boundary=110, shift=20), geometry)["at_outlet"]
        texture = measure(textured_image(boundary=110, amplitude=5), geometry)["at_outlet"]
        self.assertAlmostEqual(colour["rgb_delta_255"], 20 * 3**.5)
        self.assertEqual(colour["texture_ratio_below_over_above"], 1)
        self.assertEqual(texture["rgb_delta_255"], 0)
        self.assertEqual(texture["texture_ratio_below_over_above"], 5)

    def test_flat_material_reports_undefined_ratio_without_nan(self):
        result = measure(Image.new("RGB", (160, 220), "gray"),
                         dict(cx=80, outlet_y=110, half_width=20))
        self.assertIsNone(result["at_outlet"]["texture_ratio_below_over_above"])
        json.dumps(result, allow_nan=False)

    def test_invalid_or_missing_geometry_is_not_silently_cropped(self):
        im = textured_image()
        for geometry in (dict(cx=80, outlet_y=110, half_width=0),
                         dict(cx=80, outlet_y=float("nan"), half_width=20),
                         dict(cx=4, outlet_y=110, half_width=20),
                         dict(cx=80, outlet_y=4, half_width=20)):
            with self.subTest(geometry=geometry), self.assertRaises(ValueError):
                measure(im, geometry)

    def test_overview_geometry_comes_from_capture_crop_not_a_new_estimate(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            textured_image(boundary=110, amplitude=3).save(p/"overview.png")
            crop = [40, 70, 120, 220]
            Image.open(p/"overview.png").crop(crop).save(p/"0000.png")
            report = dict(completed=True, source_files={"main.py":"identity"},
                          source_files_end={"main.py":"identity"}, dimensions=[80,150],
                          canvas_dimensions=[160,220], crop_box=crop,
                          frames=[dict(time=8., file="0000.png", sha256=file_hash(p/"0000.png"),
                                       geometry=dict(cx=40.,outlet_y=40.,surface_y=130.,half_width=20.))])
            (p/"motion.json").write_text(json.dumps(report), encoding="utf-8")
            full = from_capture(p/"motion.json", overview=True)
            cropped = from_capture(p/"motion.json")
            self.assertEqual(full["status"], "diagnostic_only")
            self.assertIsNone(full["visual_verdict"])
            self.assertEqual(full["frames"][0]["geometry"]["outlet_y"], 110.)
            self.assertEqual(full["frames"][0]["geometry"]["surface_y"], 200.)
            a = full["frames"][0]["measurement"]["at_outlet"]
            b = cropped["frames"][0]["measurement"]["at_outlet"]
            for k in ("rgb_delta_255", "texture_ratio_below_over_above"):
                self.assertEqual(a[k], b[k])
            (p/"0000.png").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash differs"):
                from_capture(p/"motion.json")

    def test_cancelled_and_changed_source_captures_cannot_supply_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/"motion.json"
            for report in (dict(completed=False, frames=[{}]),
                           dict(completed=True, frames=[{}], source_files={"a":"one"},
                                source_files_end={"a":"two"}),
                           dict(completed=True, frames=[{}])):
                p.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "complete.*unchanged"):
                    from_capture(p)

    def test_known_311_failure_evidence_is_preserved_with_its_original_measurements(self):
        folder = ROOT/"design/checks/records/v3.11/neck-seam-followup"
        manifest = json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["case"], manifest["status"]), ("NS03", "failed"))
        path = folder/"native-overview.png"
        self.assertEqual(file_hash(path),
                         "322f22aa2bb20dfb82dc97da627ff2dcf3bb949b69bec86205abb0c75a7b1ee8")
        # From the original final-normal/motion.json: first-frame geometry plus
        # crop_box [228,433,312,924]. These are provenance, not tuned thresholds.
        with Image.open(path) as im:
            row = measure(im, dict(cx=42.+228, outlet_y=41.58969417042209+433,
                                   half_width=20.639974105548184))["at_outlet"]
        self.assertAlmostEqual(row["rgb_delta_255"], 5.266750623631167, places=10)
        self.assertAlmostEqual(row["texture_ratio_below_over_above"], 1.6544637122671804, places=10)


if __name__ == "__main__":
    unittest.main()
