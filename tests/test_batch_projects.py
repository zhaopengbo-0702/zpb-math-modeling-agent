"""Offline checks for isolated batch projects and safe resumability."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zpb_batch_projects", ROOT / "scripts/batch_projects.py")
BATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BATCH)


class BatchProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.cases = self.base / "cases"
        self.cases.mkdir()
        self.output = self.base / "output"
        self.manifest = self.cases / "batch.json"
        self.jobs = []
        for number, text in ((1, "第一道题的数据"), (2, "第二道题的数据")):
            directory = self.cases / f"题目{number}"
            directory.mkdir()
            (directory / "附件.csv").write_text(text, encoding="utf-8")
            self.jobs.append({"id": f"paper-00{number}", "title": f"中文题目{number}",
                              "inputs": [f"题目{number}/附件.csv"]})
        self.save()

    def tearDown(self):
        self.temporary.cleanup()

    def save(self):
        self.manifest.write_text(json.dumps({"jobs": self.jobs}, ensure_ascii=False), encoding="utf-8")

    def run_batch(self, stage="initialize", **kwargs):
        return BATCH.run_batch(self.manifest, self.output, stage, **kwargs)

    def test_two_chinese_inputs_are_isolated_and_resumable(self):
        report = self.run_batch()
        self.assertEqual(report["summary"], {"total": 2, "passed": 2, "failed": 0})
        for index, job in enumerate(self.jobs, 1):
            project = self.output / job["id"]
            self.assertEqual((project / "inputs/附件.csv").read_text(encoding="utf-8"),
                             (self.cases / f"题目{index}/附件.csv").read_text(encoding="utf-8"))
            marker = (project / "notes/batch-job.json").read_text(encoding="utf-8")
            self.assertNotIn(str(self.base), marker)
            (project / "main.tex").write_text("user edits", encoding="utf-8")
        resumed = self.run_batch()
        self.assertEqual([job["action"] for job in resumed["jobs"]], ["resumed", "resumed"])
        self.assertEqual((self.output / "paper-001/main.tex").read_text(), "user edits")

    def test_failed_input_does_not_block_later_job(self):
        self.jobs[0]["inputs"] = ["missing.csv"]
        self.save()
        report = self.run_batch()
        self.assertEqual([job["status"] for job in report["jobs"]], ["failed", "passed"])
        self.assertFalse((self.output / "paper-001").exists())
        self.assertTrue((self.output / "paper-002/project.json").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            code = BATCH.main(["--manifest", str(self.manifest), "--output-root", str(self.output),
                               "--stage", "initialize"])
        self.assertEqual(code, 1)

    def test_duplicate_and_escape_ids_are_rejected_before_writing(self):
        for identifier in ("paper-001", "../escape", "C:/escape", "aux", "UPPER", "a/b"):
            with self.subTest(identifier=identifier):
                self.jobs[1]["id"] = identifier
                self.save()
                with self.assertRaises(ValueError):
                    self.run_batch()
                self.assertFalse(self.output.exists())

    def test_changed_original_input_fails_resume_but_other_job_passes(self):
        self.run_batch()
        source = self.cases / "题目1/附件.csv"
        source.write_text("different problem", encoding="utf-8")
        report = self.run_batch()
        self.assertEqual([job["status"] for job in report["jobs"]], ["failed", "passed"])
        self.assertIn("hashes changed", report["jobs"][0]["error"])
        self.assertEqual((self.output / "paper-001/inputs/附件.csv").read_text(encoding="utf-8"),
                         "第一道题的数据")

    def test_changed_local_input_and_title_fail_resume(self):
        self.run_batch()
        (self.output / "paper-001/inputs/附件.csv").write_text("tampered", encoding="utf-8")
        self.jobs[1]["title"] = "另一篇论文"
        self.save()
        report = self.run_batch()
        self.assertEqual(report["summary"]["failed"], 2)

    def test_template_changes_fail_resume(self):
        template = self.cases / "template"
        template.mkdir()
        (template / "example.tex").write_text("original", encoding="utf-8")
        self.jobs[0]["template"] = "template"
        self.save()
        self.assertEqual(self.run_batch()["summary"]["failed"], 0)
        (template / "example.tex").write_text("changed", encoding="utf-8")
        report = self.run_batch()
        self.assertEqual([job["status"] for job in report["jobs"]], ["failed", "passed"])

    def test_nonempty_unowned_directory_is_not_replaced(self):
        directory = self.output / "paper-001"
        directory.mkdir(parents=True)
        sentinel = directory / "important.txt"
        sentinel.write_text("keep")
        report = self.run_batch()
        self.assertEqual([job["status"] for job in report["jobs"]], ["failed", "passed"])
        self.assertEqual(sentinel.read_text(), "keep")

    def test_input_output_overlap_and_report_overwrite_are_rejected(self):
        with self.assertRaises(ValueError):
            BATCH.run_batch(self.manifest, self.cases, "initialize")
        with self.assertRaises(ValueError):
            self.run_batch(report_path=Path("paper-001/project.json"))
        with self.assertRaises(ValueError):
            self.run_batch(report_path=self.base / "outside.json")
        self.assertFalse(self.output.exists())

    def test_selected_subprocess_stages_are_serial_and_fail_independently(self):
        self.run_batch()
        events = []

        class Process:
            def __init__(self, command, **kwargs):
                self.identifier = Path(command[-1]).name
                self.pid = 101 if self.identifier == "paper-001" else 102
                self.returncode = 1 if self.identifier == "paper-001" else 0
                events.append(("start", self.identifier))

            def communicate(self):
                events.append(("finish", self.identifier))
                return "compiler output", None

        with patch.object(BATCH.subprocess, "Popen", Process):
            report = self.run_batch("build")
        self.assertEqual(events, [("start", "paper-001"), ("finish", "paper-001"),
                                  ("start", "paper-002"), ("finish", "paper-002")])
        self.assertEqual([job["pid"] for job in report["jobs"]], [101, 102])
        self.assertEqual([job["status"] for job in report["jobs"]], ["failed", "passed"])
        self.assertEqual((self.output / "paper-001/notes/batch/build.log").read_text(), "compiler output")
        for stage in ("audit", "package"):
            with patch.object(BATCH.subprocess, "Popen", Process):
                selected = self.run_batch(stage, requested=["paper-002"], report_path=Path(f"reports/{stage}.json"))
            self.assertEqual([job["id"] for job in selected["jobs"]], ["paper-002"])
            self.assertEqual(selected["status"], "passed")

    def test_unknown_job_is_rejected_and_cli_success_is_zero(self):
        with self.assertRaises(ValueError):
            self.run_batch(requested=["unknown"])
        self.assertFalse(self.output.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            code = BATCH.main(["--manifest", str(self.manifest), "--output-root", str(self.output),
                               "--stage", "initialize", "--job", "paper-002"])
        self.assertEqual(code, 0)
        self.assertFalse((self.output / "paper-001").exists())

    def test_real_package_subprocess_creates_separate_archives(self):
        self.run_batch()
        for job in self.jobs:
            project = self.output / job["id"]
            (project / "results/result.csv").write_text("value\n1\n", encoding="utf-8")
            config_path = project / "project.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["required_deliverables"] = ["results/result.csv"]
            config_path.write_text(json.dumps(config), encoding="utf-8")
        report = self.run_batch("package")
        self.assertEqual(report["summary"]["failed"], 0, report)
        for job in report["jobs"]:
            self.assertIsInstance(job["pid"], int)
            self.assertTrue((self.output / job["id"] / "delivery/submission.zip").is_file())
            self.assertTrue((self.output / job["id"] / "delivery/reproducibility.zip").is_file())


if __name__ == "__main__":
    unittest.main()
