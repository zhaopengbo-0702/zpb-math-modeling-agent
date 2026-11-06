"""Packaging tests use synthetic files only and never require TeX or network access."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zpb_package", ROOT / "scripts/package_submission.py")
pack = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pack
SPEC.loader.exec_module(pack)


def project_at(base: Path, required: list[str] | None = None) -> Path:
    project = base / "项目"
    project.mkdir()
    (project / "delivery").mkdir()
    (project / "delivery/paper.pdf").write_bytes(b"synthetic final PDF fixture")
    (project / "results").mkdir()
    (project / "results/result1.xlsx").write_bytes(b"synthetic numerical result fixture")
    (project / "project.json").write_text(json.dumps({"title": "测试论文", "required_deliverables": required if required is not None else ["delivery/paper.pdf", "results/result1.xlsx"]}), encoding="utf-8")
    return project


class PackagingTests(unittest.TestCase):
    def test_core_flattening_and_hashes_match_both_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary))
            (project / "code").mkdir()
            (project / "code/solve.py").write_text("print('computed')")
            report = pack.package_project(project)
            delivery = project / "delivery"
            with zipfile.ZipFile(delivery / "submission.zip") as archive:
                self.assertEqual(set(archive.namelist()), {"paper.pdf", "result1.xlsx", "MANIFEST.json"})
                ledger = json.loads(archive.read("MANIFEST.json"))
                for record in ledger["files"]:
                    payload = archive.read(record["path"])
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), record["sha256"])
                    self.assertEqual(len(payload), record["size_bytes"])
            with zipfile.ZipFile(delivery / "reproducibility.zip") as archive:
                self.assertIn("delivery/paper.pdf", archive.namelist())
                self.assertIn("code/solve.py", archive.namelist())
                ledger = json.loads(archive.read("MANIFEST.json"))
                for record in ledger["files"]:
                    self.assertEqual(hashlib.sha256(archive.read(record["path"])).hexdigest(), record["sha256"])
            self.assertEqual(hashlib.sha256((delivery / "submission.zip").read_bytes()).hexdigest(), report["submission"]["sha256"])
            self.assertEqual(json.loads((delivery / "submission-manifest.json").read_text(encoding="utf-8")), report)

    def test_missing_and_empty_required_files_fail_without_package(self):
        for relative in ("results/missing.xlsx", "results/empty.csv"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                project = project_at(Path(temporary), [relative])
                (project / "results/empty.csv").touch()
                with self.assertRaises(pack.PackagingError):
                    pack.package_project(project)
                self.assertFalse((project / "delivery/submission.zip").exists())

    def test_empty_contract_is_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary), [])
            with self.assertRaisesRegex(pack.PackagingError, "required_deliverables"):
                pack.package_project(project)

    def test_basename_collision_is_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary), ["delivery/paper.pdf", "results/paper.pdf"])
            (project / "results/paper.pdf").write_bytes(b"different file")
            with self.assertRaisesRegex(pack.PackagingError, "basename"):
                pack.package_project(project)

    def test_escape_absolute_and_hidden_required_paths_are_rejected(self):
        for relative in ("../outside.pdf", "/outside.pdf", "C:\\outside.pdf", "results/../delivery/paper.pdf", ".env"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                project = project_at(Path(temporary), [relative])
                with self.assertRaises(pack.PackagingError):
                    pack.package_project(project)

    def test_self_inclusion_is_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary), ["delivery/submission.zip"])
            (project / "delivery/submission.zip").write_bytes(b"old package")
            with self.assertRaisesRegex(pack.PackagingError, "own output"):
                pack.package_project(project)

    def test_support_filters_credentials_old_versions_caches_and_private_notes(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary))
            values = {
                "code/solve.py": "compute", "code/.env": "private", "code/api_token.json": "private",
                "code/node_modules/library/index.js": "dependency", "code/__pycache__/module.pyc": "cache",
                "data/manuscript_before_restructure.tex": "old manuscript", "data/result.npz": "numerical data",
                "figures/field.pdf": "final plot", "figures/field_old.pdf": "old plot",
                "notes/audit.json": "audit", "notes/method-ledger.json": "methods",
                "notes/reference-evidence.json": "references", "notes/私密聊天.md": "private conversation",
                "notes/文献核验.json": "Chinese evidence filename",
                "notes/reference-cache/key.json": "cached source", "notes/build/report.json": "final build",
                "notes/build/01-xelatex.txt": "old build log", "code/solve.py.bak": "backup",
                "delivery/old.zip": "previous delivery", "inputs/.hidden/data.csv": "hidden data",
            }
            for relative, content in values.items():
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            pack.package_project(project)
            with zipfile.ZipFile(project / "delivery/reproducibility.zip") as archive:
                names = set(archive.namelist())
                included = {"code/solve.py", "data/result.npz", "figures/field.pdf", "notes/audit.json",
                            "notes/method-ledger.json", "notes/reference-evidence.json", "notes/build/report.json", "notes/文献核验.json"}
                self.assertTrue(included <= names)
                self.assertTrue(set(values) - included - names)
                self.assertFalse((set(values) - included) & names)

    def test_only_active_tex_sections_are_archived(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary))
            (project / "sections").mkdir()
            (project / "main.tex").write_text(r"\input{sections/final.tex}")
            (project / "sections/final.tex").write_text(r"\input{sections/table.tex}")
            (project / "sections/table.tex").write_text("final table")
            (project / "sections/unused.tex").write_text("unused earlier reasoning")
            pack.package_project(project)
            with zipfile.ZipFile(project / "delivery/reproducibility.zip") as archive:
                self.assertIn("sections/final.tex", archive.namelist())
                self.assertIn("sections/table.tex", archive.namelist())
                self.assertNotIn("sections/unused.tex", archive.namelist())

    def test_custom_main_template_and_local_dependencies_are_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary))
            config_path = project / "project.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update(manuscript={"main": "example.tex"}, template={"entry": "template/original.tex"})
            config_path.write_text(json.dumps(config), encoding="utf-8")
            files = {
                "example.tex": r"\documentclass{localclass}\usepackage{customstyle,amsmath}"
                               r"\input{chapters/body}\bibliography{custom}\bibliographystyle{custom}"
                               r"\includegraphics{assets/中文科学图}",
                "localclass.cls": r"\LoadClass{article}\RequirePackage{nestedstyle}",
                "customstyle.sty": r"\ProvidesPackage{customstyle}",
                "nestedstyle.sty": r"\ProvidesPackage{nestedstyle}",
                "custom.bib": "@article{verified,title={Verified source}}",
                "custom.bst": "ENTRY{}{}{}",
                "custom.bbx": r"\ProvidesFile{custom.bbx}",
                "custom.cbx": r"\ProvidesFile{custom.cbx}",
                "custom.lbx": r"\ProvidesFile{custom.lbx}",
                "custom.dbx": r"\ProvidesFile{custom.dbx}",
                "chapters/body.tex": r"\input{appendix/derivation.tex}",
                "appendix/derivation.tex": "Final derivation",
                "assets/中文科学图.pdf": "final graphic fixture",
                "template/original.tex": "user's preserved original template",
                "main.tex": r"\input{sections/unused.tex}",
                "sections/unused.tex": "an inactive fallback chapter",
            }
            for relative, content in files.items():
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            (project / "example.bcf").write_text("generated Biber control cache", encoding="utf-8")
            (project / "example.run.xml").write_text("generated bibliography run cache", encoding="utf-8")
            report = pack.package_project(project)
            with zipfile.ZipFile(project / "delivery/reproducibility.zip") as archive:
                names = set(archive.namelist())
                expected = set(files) - {"main.tex", "sections/unused.tex"}
                self.assertTrue(expected <= names, expected - names)
                self.assertNotIn("main.tex", names)
                self.assertNotIn("sections/unused.tex", names)
                self.assertNotIn("example.bcf", names)
                self.assertNotIn("example.run.xml", names)
                for record in report["reproducibility"]["files"]:
                    source = project / record["source_rel"]
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), record["sha256"])
                    self.assertEqual(archive.read(record["path"]), source.read_bytes())

    def test_template_entry_precedes_fallback_main_and_resolves_graphicspath(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary))
            config_path = project / "project.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["template"] = {"entry": "report.tex"}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (project / "report.tex").write_text(r"\graphicspath{{pictures/}}\includegraphics[width=4cm]{field}")
            (project / "main.tex").write_text("inactive fallback")
            (project / "pictures").mkdir()
            (project / "pictures/field.png").write_bytes(b"graphic fixture")
            pack.package_project(project)
            with zipfile.ZipFile(project / "delivery/reproducibility.zip") as archive:
                self.assertIn("report.tex", archive.namelist())
                self.assertIn("pictures/field.png", archive.namelist())
                self.assertNotIn("main.tex", archive.namelist())

    def test_tables_and_source_spec_are_support_material_not_automatic_deliverables(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary))
            files = {
                "main.tex": r"\input{tables/validation.tex}",
                "tables/validation.tex": r"\begin{table}final validation results\end{table}",
                "tables/tables.xlsx": "generated workbook fixture",
                "tables/table-manifest.json": '{"tables":[{"id":"validation"}]}',
                "data/table-spec.json": '{"tables":[{"id":"validation","rows":[{"value":1.23456789}]}]}',
                "tables/unused.tex": "an earlier unused table",
            }
            for relative, content in files.items():
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            report = pack.package_project(project)
            with zipfile.ZipFile(project / "delivery/submission.zip") as archive:
                self.assertEqual(set(archive.namelist()), {"paper.pdf", "result1.xlsx", "MANIFEST.json"})
            with zipfile.ZipFile(project / "delivery/reproducibility.zip") as archive:
                included = set(files) - {"tables/unused.tex"}
                self.assertTrue(included <= set(archive.namelist()))
                self.assertNotIn("tables/unused.tex", archive.namelist())
                for record in report["reproducibility"]["files"]:
                    payload = archive.read(record["path"])
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), record["sha256"])
                    self.assertEqual(payload, (project / record["source_rel"]).read_bytes())

    def test_version_change_preserves_previous_complete_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary))
            first = pack.package_project(project)
            before = {name: (project / "delivery" / name).read_bytes() for name in pack.OUTPUT_NAMES}
            original = pack.write_zip
            changed = False

            def mutate_after_read(*args, **kwargs):
                nonlocal changed
                original(*args, **kwargs)
                if not changed:
                    (project / "results/result1.xlsx").write_bytes(b"changed while archiving")
                    changed = True

            with patch.object(pack, "write_zip", side_effect=mutate_after_read):
                with self.assertRaisesRegex(pack.PackagingError, "changed"):
                    pack.package_project(project)
            for name, payload in before.items():
                self.assertEqual((project / "delivery" / name).read_bytes(), payload)
            self.assertEqual(first["status"], "passed")
            self.assertFalse(list((project / "delivery").glob(".zpb-stage-*")))

    def test_explicit_output_directory_works(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = project_at(base)
            output = base / "交付包"
            pack.package_project(project, output)
            self.assertTrue((output / "submission.zip").is_file())
            self.assertFalse((project / "delivery/submission.zip").exists())

    def test_failed_output_commit_rolls_back_previous_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = project_at(Path(temporary))
            pack.package_project(project)
            before = {name: (project / "delivery" / name).read_bytes() for name in pack.OUTPUT_NAMES}
            original_replace = Path.replace

            def fail_manifest_publish(path, target):
                if Path(target).name == "submission-manifest.json" and path.name.startswith(".zpb-stage-"):
                    raise OSError("simulated filesystem failure")
                return original_replace(path, target)

            with patch.object(Path, "replace", fail_manifest_publish):
                with self.assertRaisesRegex(OSError, "simulated"):
                    pack.package_project(project)
            for name, payload in before.items():
                self.assertEqual((project / "delivery" / name).read_bytes(), payload)

    def test_symlink_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = project_at(base, ["results/linked.xlsx"])
            external = base / "external.xlsx"
            external.write_bytes(b"outside data")
            try:
                (project / "results/linked.xlsx").symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("This account cannot create symlinks")
            with self.assertRaisesRegex(pack.PackagingError, "Symlink"):
                pack.package_project(project)


if __name__ == "__main__":
    unittest.main()
