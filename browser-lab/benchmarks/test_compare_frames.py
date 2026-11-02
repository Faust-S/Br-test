"""Small deterministic tests; real fidelity PASS needs external reference."""
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from compare_frames import evaluate, score

class FidelityMetricsTests(unittest.TestCase):
    def test_identical_image_is_zero_error(self):
        image = Image.new("RGB", (40, 25), "white")
        result = score(image, image.copy())
        self.assertEqual(result["rgb_mae_0_to_255"], 0)
        self.assertEqual(result["changed_pixels_over_16_fraction"], 0)
        self.assertEqual(result["exact_match_fraction"], 1)

    def test_shifted_box_is_detected_as_geometry_error(self):
        ref = Image.new("RGB", (40, 25), "white")
        actual = ref.copy()
        for x in range(10, 20):
            for y in range(10, 18):
                ref.putpixel((x, y), (0, 0, 0))
                actual.putpixel((x + 5, y), (0, 0, 0))
        metrics = score(ref, actual)
        self.assertGreater(metrics["edge_mae_0_to_255"], 0)
        self.assertGreater(metrics["changed_pixels_over_16_fraction"], 0)

    def test_requires_provenance_and_explicit_thresholds(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = Image.new("RGB", (20, 10), "white")
            for name in ("ref.png", "actual.png"):
                image.save(root / name)
            info = {"reference_engine": "independent-test-engine", "reference_version": "1",
                "viewport_css_px": [20, 10], "device_pixel_ratio": 1,
                "page_url": "http://example.test/", "capture_state": "static",
                "capture_date": "2026-11-02"}
            (root / "source.json").write_text(json.dumps(info))
            ref, actual, source = (root / p for p in ("ref.png", "actual.png", "source.json"))
            self.assertEqual(evaluate(ref, actual, source, None, None)["status"],
                "MEASURED_NOT_QUALIFIED")
            self.assertEqual(evaluate(ref, actual, source, 0, 0)["status"], "PASS")
            info.pop("capture_state")
            source.write_text(json.dumps(info))
            with self.assertRaises(ValueError):
                evaluate(ref, actual, source, 0, 0)
    def test_refuses_viewport_mismatch(self):
        with self.assertRaises(ValueError):
            score(Image.new("RGB", (10, 10)), Image.new("RGB", (11, 10)))

if __name__ == "__main__":
    unittest.main()
