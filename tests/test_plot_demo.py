"""Behavior checks for the executed numerical example and standalone exports."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
HAS_NUMERICS = all(importlib.util.find_spec(name) for name in ("numpy", "scipy"))
HAS_PLOTTING = all(importlib.util.find_spec(name) for name in ("numpy", "matplotlib", "PIL"))


@unittest.skipUnless(HAS_NUMERICS, "Install numpy and scipy to test the numerical demonstration.")
class NumericalDemoTests(unittest.TestCase):
    def test_grid_convergence_against_analytic_event(self):
        from run_demo import solve_diffusion
        results = [solve_diffusion(n) for n in (20, 40, 80)]
        errors = [result["stop_absolute_error"] for result in results]
        self.assertGreater(errors[0], errors[1])
        self.assertGreater(errors[1], errors[2])
        for coarse, fine in zip(errors, errors[1:]):
            self.assertGreater(coarse / fine, 3.7)
            self.assertLess(coarse / fine, 4.3)
        self.assertLess(results[-1]["max_l2_error"], 0.00005)
        self.assertGreaterEqual(results[-1]["field"].min(), -1e-12)
        self.assertLess(abs(results[-1]["field"][-1].max() - 0.2), 1e-8)

    def test_compute_only_cli_and_fact_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "中文-demo"
            completed = subprocess.run([
                sys.executable, str(ROOT / "scripts" / "run_demo.py"),
                "--output", str(output), "--compute-only", "--skip-xlsx",
            ], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            facts = json.loads((output / "numeric_facts.json").read_text(encoding="utf-8"))["facts"]
            for fact in facts:
                self.assertEqual(fact["source"]["artifact"], "summary.json")
                pointer = fact["source"]["locator"].lstrip("/")
                self.assertEqual(fact["value"], summary[pointer])
            self.assertTrue((output / "field.csv").is_file())
            self.assertTrue((output / "field.npz").is_file())
            self.assertFalse((output / "figures").exists())


@unittest.skipUnless(HAS_PLOTTING, "Install matplotlib, numpy and Pillow for export tests.")
class PlotTests(unittest.TestCase):
    def test_refuses_collaged_panels(self):
        import matplotlib.pyplot as plt
        from zpb_plot import save_figure
        fig, _ = plt.subplots(1, 2)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                save_figure(fig, temporary, "panels")
        plt.close(fig)

    def test_log_scale_rejects_nonpositive_values(self):
        from zpb_plot import positive_log_norm
        with self.assertRaises(ValueError):
            positive_log_norm([0, 1, 2])
        self.assertEqual(positive_log_norm([0.1, 10]).vmin, 0.1)

    @unittest.skipUnless(HAS_NUMERICS, "Install scipy for rendered-demo tests.")
    def test_chinese_standalone_exports_are_high_resolution(self):
        from PIL import Image
        from run_demo import compute_demo, draw_figure
        from zpb_plot import find_chinese_font
        try:
            font, _ = find_chinese_font()
        except RuntimeError:
            self.skipTest("A Chinese font is not installed; numeric tests still run.")
        with tempfile.TemporaryDirectory() as temporary:
            result, summary = compute_demo(temporary, xlsx=False)
            for figure in ("field", "surface", "convergence"):
                paths = draw_figure(result, summary, temporary, figure=figure, font=font)
                self.assertEqual({path.suffix for path in paths}, {".pdf", ".svg", ".png"})
                for path in paths:
                    self.assertGreater(path.stat().st_size, 1000)
                with Image.open(Path(temporary) / "figures" / f"{figure}.png") as image:
                    self.assertGreater(image.width, 3000)
                    self.assertGreater(image.height, 2000)
                    self.assertAlmostEqual(image.info["dpi"][0], 600, delta=0.1)


if __name__ == "__main__":
    unittest.main()
