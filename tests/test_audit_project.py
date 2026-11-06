"""Observable audit checks using tiny portable PDFs and temporary projects."""
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_project.py"
SPEC = importlib.util.spec_from_file_location("zpb_audit_project", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
HAS_PYPDF = importlib.util.find_spec("pypdf") is not None
HAS_LAYOUT = importlib.util.find_spec("pdfplumber") is not None


@unittest.skipUnless(HAS_PYPDF and HAS_LAYOUT, "pypdf and pdfplumber are required")
class ProjectAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        (self.project / "sections").mkdir()
        (self.project / "delivery").mkdir()
        self.config = {"manuscript": {"main": "main.tex"}, "paper": {"abstract_one_page": True}, "recent_years": 6}
        self.write_config()
        (self.project / "main.tex").write_text("\\input{sections/method}\n\\bibliography{references}\n", encoding="utf-8")
        (self.project / "sections" / "method.tex").write_text("Method \\cite{recent}. % \\cite{ignored}\n", encoding="utf-8")
        self.current_year = datetime.now(timezone.utc).year
        self.write_bib(self.current_year)
        self.make_pdf(["Keywords: diffusion", "Contents"])

    def tearDown(self):
        self.temporary.cleanup()

    def write_config(self):
        (self.project / "project.json").write_text(json.dumps(self.config), encoding="utf-8")

    def write_bib(self, year):
        (self.project / "references.bib").write_text(f"@article{{recent,\n author={{A, B}},\n title={{Example}},\n year={{{year}}}\n}}\n", encoding="utf-8")

    def make_pdf(self, texts, x=50):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        for text in texts:
            page = writer.add_blank_page(width=595, height=842)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf {x} 780 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(self.project / "delivery" / "paper.pdf")

    def run_audit(self):
        return AUDIT.audit_project(self.project)

    def failed_codes(self, report):
        return {item["code"] for item in report["failed"]}

    def test_valid_project_expands_active_inputs(self):
        (self.project / "sections" / "unused.tex").write_text("\\cite{unavailable}", encoding="utf-8")
        report = self.run_audit()
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["checks"]["bibliography"]["cited_keys"], ["recent"])
        self.assertTrue(report["checks"]["pdf"]["page_two_contains_contents_label"])
        self.assertEqual(len(report["checks"]["manuscript"]["active_files"]), 2)

    def test_missing_citation_fails(self):
        (self.project / "sections" / "method.tex").write_text("\\citep[see][p. 2]{missing}", encoding="utf-8")
        self.assertIn("bibliography.undefined_citations", self.failed_codes(self.run_audit()))

    def test_duplicate_bibliography_keys_fail(self):
        path = self.project / "references.bib"
        path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")
        self.assertIn("bibliography.duplicate_keys", self.failed_codes(self.run_audit()))

    def test_old_reference_requires_explicit_exception(self):
        self.write_bib(1990)
        self.assertIn("bibliography.recency", self.failed_codes(self.run_audit()))
        self.config["citations"] = {"allowed_classics": ["recent"]}
        self.write_config()
        self.assertNotIn("bibliography.recency", self.failed_codes(self.run_audit()))

    def test_min_year_overrides_default_window(self):
        self.write_bib(self.current_year - 4)
        self.config["citations"] = {"min_year": self.current_year - 2}
        self.write_config()
        self.assertIn("bibliography.recency", self.failed_codes(self.run_audit()))

    def test_inline_bibliography_resolves_active_keys_without_stale_bib_fallback(self):
        (self.project / "main.tex").write_text(
            "\\input{sections/method}\n\\begin{thebibliography}{99}\n"
            f"\\bibitem[Researcher({self.current_year})]{{recent}} Author. A current article[J]. Journal, {self.current_year}, 1: 1--9.\n"
            "\\end{thebibliography}\n", encoding="utf-8")
        report = self.run_audit()
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["checks"]["bibliography"]["resources"], [])
        self.assertEqual(report["checks"]["bibliography"]["entries"]["recent"]["year"], self.current_year)
        self.assertNotIn("bibliography.undefined_citations", self.failed_codes(report))

    def test_inline_old_or_future_publication_year_fails_recency(self):
        for year in (1990, self.current_year + 1):
            with self.subTest(year=year):
                (self.project / "main.tex").write_text(
                    f"\\cite{{item}}\\begin{{thebibliography}}{{9}}\\bibitem{{item}} Author. Title. {year}.\\end{{thebibliography}}",
                    encoding="utf-8")
                self.assertIn("bibliography.recency", self.failed_codes(self.run_audit()))

    def test_inline_ambiguous_or_missing_year_requires_review_without_false_undefined(self):
        for body in ("Author. Undated title. \\url{https://example.org/2025/article}",
                     f"Author. The 1999 dataset. Journal, {self.current_year}."):
            with self.subTest(body=body):
                (self.project / "main.tex").write_text(
                    "\\cite{item}\\begin{thebibliography}{9}\\bibitem{item}" + body + "\\end{thebibliography}",
                    encoding="utf-8")
                report = self.run_audit()
                self.assertNotIn("bibliography.undefined_citations", self.failed_codes(report))
                self.assertNotIn("bibliography.recency", self.failed_codes(report))
                self.assertIn("bibliography.inline_year_manual", {entry["code"] for entry in report["warnings"]})

    def test_duplicate_inline_keys_are_reported(self):
        (self.project / "main.tex").write_text(
            "\\cite{item}\\begin{thebibliography}{9}"
            f"\\bibitem{{item}} Author. First. {self.current_year}."
            f"\\bibitem{{item}} Author. Second. {self.current_year}."
            "\\end{thebibliography}", encoding="utf-8")
        self.assertIn("bibliography.duplicate_keys", self.failed_codes(self.run_audit()))

    def test_template_hash_is_checked(self):
        directory = self.project / "template"
        directory.mkdir()
        template = directory / "example.cls"
        template.write_text("original", encoding="utf-8")
        (self.project / "notes").mkdir()
        baseline = {"example.cls": hashlib.sha256(template.read_bytes()).hexdigest()}
        (self.project / "notes" / "template-baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        self.config["template"] = {"mode": "user-provided", "directory": "template", "baseline": "notes/template-baseline.json"}
        self.write_config()
        self.assertNotIn("template.changed", self.failed_codes(self.run_audit()))
        template.write_text("modified", encoding="utf-8")
        self.assertIn("template.changed", self.failed_codes(self.run_audit()))

    def test_numeric_facts_check_finite_values_and_sources(self):
        (self.project / "result.json").write_text('{"result": 1}', encoding="utf-8")
        facts = {"facts": [{"id": "time", "value": 1.0, "unit": "h", "source": {"artifact": "result.json", "locator": "$.result"}}]}
        path = self.project / "numeric_facts.json"
        path.write_text(json.dumps(facts), encoding="utf-8")
        self.assertNotIn("facts.traceability", self.failed_codes(self.run_audit()))
        facts["facts"][0]["value"] = float("nan")
        facts["facts"][0]["source"]["artifact"] = "absent.json"
        path.write_text(json.dumps(facts), encoding="utf-8")
        self.assertIn("facts.traceability", self.failed_codes(self.run_audit()))

    def test_keywords_on_second_page_fail_one_page_requirement(self):
        self.make_pdf(["Abstract text", "Keywords: diffusion"])
        self.assertIn("pdf.abstract_page_one", self.failed_codes(self.run_audit()))
        self.config["paper"]["abstract_one_page"] = False
        self.write_config()
        self.assertNotIn("pdf.abstract_page_one", self.failed_codes(self.run_audit()))
        report = AUDIT.audit_project(self.project, require_abstract_page_one=True)
        self.assertIn("pdf.abstract_page_one", self.failed_codes(report))

    def test_initializer_abstract_schema_is_supported(self):
        self.config.pop("paper")
        self.config["abstract"] = {"stay_on_one_page": False}
        self.write_config()
        self.make_pdf(["Abstract text", "Keywords: diffusion"])
        self.assertNotIn("pdf.abstract_page_one", self.failed_codes(self.run_audit()))

    def test_input_cycles_and_project_escape_fail(self):
        (self.project / "sections" / "method.tex").write_text("\\input{main.tex}", encoding="utf-8")
        self.assertIn("tex.input_cycle", self.failed_codes(self.run_audit()))
        (self.project / "sections" / "method.tex").write_text("\\input{../outside.tex}", encoding="utf-8")
        self.assertIn("tex.input_path", self.failed_codes(self.run_audit()))

    def test_outside_page_characters_fail(self):
        self.make_pdf(["Keywords: diffusion"], x=-30)
        self.assertIn("pdf.characters_outside_page", self.failed_codes(self.run_audit()))

    def test_cli_writes_report_without_changing_sources(self):
        original = (self.project / "main.tex").read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            code = AUDIT.main(["--project", str(self.project)])
        self.assertEqual(code, 0)
        report = json.loads((self.project / "notes" / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "passed")
        self.assertEqual(original, (self.project / "main.tex").read_bytes())

    def test_cli_rejects_overwriting_project_configuration(self):
        path = self.project / "project.json"
        original = path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            code = AUDIT.main(["--project", str(self.project), "--output", "project.json"])
        self.assertEqual(code, 1)
        self.assertEqual(original, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
