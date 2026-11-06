"""Portable, offline checks for project setup and citation metadata handling."""
from __future__ import annotations

from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init = load("init_project")
refs = load("verify_references")
preflight = load("preflight")


def metadata(**changes):
    value = {"DOI": "10.1234/example", "type": "journal-article",
             "title": ["Heat & mass transfer: 50% comparison"],
             "author": [{"family": "Researcher", "given": "A."}],
             "container-title": ["Scientific Modeling"], "volume": "12", "issue": "2",
             "page": "21-35", "published-print": {"date-parts": [[2025, 2, 1]]},
             "published-online": {"date-parts": [[2024, 11, 20]]},
             "abstract": "<jats:p>A checked <i>metadata</i> fixture.</jats:p>"}
    value.update(changes)
    return value


class InitializationTests(unittest.TestCase):
    def test_unicode_input_hash_and_existing_work_protection(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "题目.txt"
            source.write_text("当前问题", encoding="utf-8")
            assets = base / "assets"
            assets.mkdir()
            (assets / "main.tex").write_text("document", encoding="utf-8")
            project = base / "项目"
            config = init.initialize(project, [source], "论文题目", assets=assets)
            manifest = json.loads((project / "notes/input-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["inputs"][0]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual((project / "inputs/题目.txt").read_bytes(), source.read_bytes())
            self.assertNotIn(str(base), json.dumps(manifest))
            self.assertFalse(config["include_literature_review"])
            self.assertEqual(config["figures"]["dpi"], 600)
            with self.assertRaises(ValueError):
                init.initialize(project, [source], "second", assets=assets)

    def test_template_preservation_and_hash_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            template = base / "original"
            template.mkdir()
            (template / "example.tex").write_text("the user's template", encoding="utf-8")
            (template / "local.cls").write_bytes(b"class source")
            project = base / "project"
            config = init.initialize(project, [], "title", template=template)
            baseline = json.loads((project / "notes/template-baseline.json").read_text())
            self.assertEqual(baseline["local.cls"], hashlib.sha256(b"class source").hexdigest())
            self.assertEqual((project / "template/example.tex").read_bytes(), (template / "example.tex").read_bytes())
            self.assertEqual(config["template"]["mode"], "user-provided")
            self.assertFalse((project / "main.tex").exists())

    def test_duplicate_names_fail_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            files = []
            for name in ("a", "b"):
                directory = base / name
                directory.mkdir()
                source = directory / "data.csv"
                source.write_text(name)
                files.append(source)
            project = base / "project"
            with self.assertRaises(ValueError):
                init.initialize(project, files, "title")
            self.assertFalse(project.exists())


class ReferenceTests(unittest.TestCase):
    def test_issue_year_and_tex_escaping(self):
        record = refs.validate_metadata(metadata(), "10.1234/example", 2021, date(2026, 11, 6))
        self.assertEqual(record["year"], 2025)
        self.assertEqual(record["year_source"], "published-print")
        entry = refs.bibtex_entry("fixture2025", record)
        self.assertIn(r"Heat \& mass transfer: 50\% comparison", entry)
        self.assertIn("21--35", entry)
        self.assertEqual(record["abstract"], "A checked metadata fixture.")
        self.assertEqual(record["claim_status"], "requires-agent-source-review")

    def test_future_and_incomplete_references_are_rejected(self):
        future = metadata(**{"published-print": {"date-parts": [[2027, 1, 1]]},
                             "published-online": {"date-parts": [[2027, 1, 1]]}})
        with self.assertRaises(ValueError):
            refs.validate_metadata(future, "10.1234/example", 2021, date(2026, 11, 6))
        with self.assertRaises(ValueError):
            refs.validate_metadata(metadata(page=""), "10.1234/example", 2021, date(2026, 11, 6))

    def test_future_print_issue_uses_already_published_online_year(self):
        work = metadata(**{"published-print": {"date-parts": [[2027, 1, 1]]},
                           "published-online": {"date-parts": [[2026, 5, 20]]}})
        record = refs.validate_metadata(work, "10.1234/example", 2021, date(2026, 11, 6))
        self.assertEqual(record["year"], 2026)
        self.assertEqual(record["year_source"], "published-online")
        self.assertFalse(record["publication_dates"]["published-print"]["not_future"])

    def test_offline_cache_and_duplicate_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            payload = {"metadata": metadata(), "crossref_url": "https://api.crossref.org/works/fixture"}
            (cache / refs.cache_name("10.1234/example")).write_text(json.dumps(payload), encoding="utf-8")
            request = {"key": "example2025", "doi": "https://doi.org/10.1234/example", "claim": "a claim", "location": "method"}
            with patch.object(refs, "get_json", side_effect=AssertionError("network must not run")):
                report, bibliography = refs.verify_requests([request], cache=cache, min_year=2021,
                                                            as_of=date(2026, 11, 6), offline=True)
                self.assertEqual(report["status"], "passed")
                self.assertIn("@article{example2025", bibliography)
                failed, _ = refs.verify_requests([request, request], cache=cache, min_year=2021,
                                                 as_of=date(2026, 11, 6), offline=True)
                self.assertEqual(failed["status"], "failed")

    def test_cli_does_not_replace_bibliography_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            requests = base / "requests.json"
            requests.write_text(json.dumps([{"key": "missing", "doi": "10.1234/not-cached"}]))
            bibliography = base / "references.bib"
            bibliography.write_text("keep this bibliography")
            result = refs.main([str(requests), "--output", str(base / "evidence.json"),
                                "--bibtex", str(bibliography), "--cache", str(base / "cache"), "--offline"])
            self.assertEqual(result, 1)
            self.assertEqual(bibliography.read_text(), "keep this bibliography")
            self.assertEqual(json.loads((base / "evidence.json").read_text())["status"], "failed")

    def test_doi_url_is_encoded(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(refs, "get_json", return_value=({"message": metadata()}, "https://example.org")) as request:
                refs.fetch_record("10.1234/a(b)#c", Path(temporary), offline=False, resolve_doi=False)
            self.assertEqual(request.call_args.args[0], "https://api.crossref.org/works/10.1234%2Fa%28b%29%23c")


class EnvironmentTests(unittest.TestCase):
    def test_missing_tool_is_explicit(self):
        with patch.object(preflight.shutil, "which", return_value=None), patch.dict(preflight.os.environ, {}, clear=True):
            self.assertIsNone(preflight.find_tool("nonexistent-modeling-tool"))


if __name__ == "__main__":
    unittest.main()
