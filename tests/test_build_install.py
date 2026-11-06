from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildInstallTests(unittest.TestCase):
    def test_initializer_prints_chinese_path_under_legacy_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "中文路径"
            environment = dict(os.environ, PYTHONIOENCODING="cp1252")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/init_project.py"), "--output", str(target)],
                env=environment, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
            self.assertIn("中文路径", completed.stdout.decode("utf-8"))
            self.assertTrue((target / "project.json").is_file())

    def test_install_preserves_source_and_backs_up_existing(self):
        module = load("install_skill")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: zpb-math-modeling-agent\n---\n", encoding="utf-8")
            (source / "text.md").write_text("first", encoding="utf-8")
            installed = module.install(source, base / "skills")
            with self.assertRaises(FileExistsError):
                module.install(source, base / "skills")
            (source / "text.md").write_text("second", encoding="utf-8")
            module.install(source, base / "skills", replace=True)
            self.assertEqual((installed / "text.md").read_text(), "second")
            backups = list((base / "skills").glob(".zpb-backup-*"))
            self.assertEqual((backups[0] / "text.md").read_text(), "first")

    def test_build_runs_bibtex_only_when_citations_are_active(self):
        module = load("build_paper")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "main.tex").write_text("hello", encoding="utf-8")
            commands = []

            def fake_run(command, cwd, log, timeout):
                commands.append(command)
                (cwd / "main.aux").write_text(r"\bibdata{references}\citation{item}", encoding="utf-8")
                (cwd / "main.pdf").write_bytes(b"%PDF-test")
                (cwd / "main.log").write_text("successful", encoding="utf-8")

            with patch.object(module, "executable", side_effect=lambda name, explicit=None: name), patch.object(module, "run_pass", side_effect=fake_run):
                report = module.build(project)
            self.assertTrue(report["bibtex_run"])
            self.assertFalse(report["biber_run"])
            self.assertEqual(report["bibliography_backend"], "bibtex")
            self.assertEqual([cmd[0] for cmd in commands], ["xelatex", "bibtex", "xelatex", "xelatex"])
            self.assertEqual((project / "delivery/paper.pdf").read_bytes(), b"%PDF-test")

    def test_build_runs_biber_for_current_biblatex_aux_and_for_new_bcf(self):
        module = load("build_paper")
        for detection in ("aux-markers", "new-bcf"):
            with self.subTest(detection=detection), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                (project / "main.tex").write_text("hello", encoding="utf-8")
                if detection == "aux-markers":
                    (project / "main.bcf").write_text("existing biber control", encoding="utf-8")
                commands = []

                def fake_run(command, cwd, log, timeout):
                    commands.append(command)
                    if command[0] == "xelatex":
                        aux = r"\abx@aux@refcontext{none/global//global/global}" if detection == "aux-markers" else ""
                        (cwd / "main.aux").write_text(aux, encoding="utf-8")
                        if detection == "new-bcf" and not (cwd / "main.bcf").exists():
                            (cwd / "main.bcf").write_text("new biber control", encoding="utf-8")
                        (cwd / "main.pdf").write_bytes(b"%PDF-test")
                        (cwd / "main.log").write_text("successful", encoding="utf-8")

                with patch.object(module, "executable", side_effect=lambda name, explicit=None: explicit or name), patch.object(module, "run_pass", side_effect=fake_run):
                    report = module.build(project, biber="chosen-biber")
                self.assertEqual([command[0] for command in commands], ["xelatex", "chosen-biber", "xelatex", "xelatex"])
                self.assertFalse(report["bibtex_run"])
                self.assertTrue(report["biber_run"])
                self.assertEqual(report["bibliography_backend"], "biber")

    def test_stale_bcf_does_not_select_biber(self):
        module = load("build_paper")
        for aux_text in ("", r"\bibdata{references}\citation{item}\abx@aux@refcontext{none}"):
            with self.subTest(aux=aux_text), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                (project / "main.tex").write_text("hello", encoding="utf-8")
                (project / "main.bcf").write_text("stale biber control", encoding="utf-8")
                commands = []

                def fake_run(command, cwd, log, timeout):
                    commands.append(command)
                    (cwd / "main.aux").write_text(aux_text, encoding="utf-8")
                    (cwd / "main.pdf").write_bytes(b"%PDF-test")
                    (cwd / "main.log").write_text("successful", encoding="utf-8")

                with patch.object(module, "executable", side_effect=lambda name, explicit=None: name), patch.object(module, "run_pass", side_effect=fake_run):
                    report = module.build(project)
                self.assertFalse(report["biber_run"])
                self.assertNotIn("biber", [command[0] for command in commands])
                self.assertEqual(report["bibliography_backend"], "bibtex" if aux_text else None)

    def test_missing_biber_fails_before_delivering_pdf(self):
        module = load("build_paper")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "main.tex").write_text("hello", encoding="utf-8")

            def fake_executable(name, explicit=None):
                if name == "biber":
                    raise FileNotFoundError("biber is unavailable. Install a TeX distribution or pass its path.")
                return name

            def fake_run(command, cwd, log, timeout):
                (cwd / "main.aux").write_text(r"\abx@aux@refcontext{none}", encoding="utf-8")
                (cwd / "main.bcf").write_text("biber control", encoding="utf-8")
                (cwd / "main.pdf").write_bytes(b"%PDF-test")
                (cwd / "main.log").write_text("successful", encoding="utf-8")

            with patch.object(module, "executable", side_effect=fake_executable), patch.object(module, "run_pass", side_effect=fake_run):
                with self.assertRaisesRegex(FileNotFoundError, "biber is unavailable"):
                    module.build(project)
            self.assertFalse((project / "delivery/paper.pdf").exists())

    def test_biblatex_without_control_file_reports_missing_bcf(self):
        module = load("build_paper")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "main.tex").write_text("hello", encoding="utf-8")

            def fake_run(command, cwd, log, timeout):
                (cwd / "main.aux").write_text(r"\abx@aux@refcontext{none}", encoding="utf-8")

            with patch.object(module, "executable", return_value="xelatex"), patch.object(module, "run_pass", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "no .bcf control file"):
                    module.build(project)

    def test_build_rejects_overfull_content(self):
        module = load("build_paper")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "main.tex").write_text("hello", encoding="utf-8")

            def fake_run(command, cwd, log, timeout):
                (cwd / "main.aux").write_text("", encoding="utf-8")
                (cwd / "main.pdf").write_bytes(b"%PDF-test")
                (cwd / "main.log").write_text(r"Overfull \hbox (10pt too wide)", encoding="utf-8")

            with patch.object(module, "executable", return_value="xelatex"), patch.object(module, "run_pass", side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    module.build(project)
            self.assertFalse((project / "delivery/paper.pdf").exists())

    def test_custom_main_is_saved_and_reused_by_later_builds(self):
        module = load("build_paper")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "example.tex").write_text("hello", encoding="utf-8")
            (project / "project.json").write_text(json.dumps({"title": "保留标题"}), encoding="utf-8")

            def fake_run(command, cwd, log, timeout):
                (cwd / "example.aux").write_text("", encoding="utf-8")
                (cwd / "example.pdf").write_bytes(b"%PDF-test")
                (cwd / "example.log").write_text("successful", encoding="utf-8")

            with patch.object(module, "executable", return_value="xelatex"), patch.object(module, "run_pass", side_effect=fake_run):
                module.build(project, main="example.tex")
                report = module.build(project)
            config = json.loads((project / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(config["manuscript"]["main"], "example.tex")
            self.assertEqual(config["title"], "保留标题")
            self.assertEqual(report["main"], "example.tex")


if __name__ == "__main__":
    unittest.main()
