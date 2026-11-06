#!/usr/bin/env python3
"""Audit a manuscript project without modifying its sources or template."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys


INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
CITE_RE = re.compile(
    r"\\(?:cite[a-zA-Z]*|parencite|textcite|autocite|supercite|nocite)\*?"
    r"(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]+)\}"
)
BIB_RE = re.compile(r"\\bibliography\s*\{([^}]+)\}|\\addbibresource(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}")
ENTRY_RE = re.compile(r"(?m)^\s*@(?P<type>[a-zA-Z]+)\s*[({]\s*(?P<key>[^,\s]+)\s*,")
YEAR_RE = re.compile(r"\byear\s*=\s*(?:\{\s*(\d{4})\s*\}|\"\s*(\d{4})\s*\"|(\d{4}))", re.I)
THEBIB_RE = re.compile(
    r"\\begin\s*\{thebibliography\}\s*\{[^}]*\}(.*?)"
    r"\\end\s*\{thebibliography\}", re.S
)
BIBITEM_RE = re.compile(r"\\bibitem(?:\s*\[([^\]]*)\])?\s*\{([^{}]+)\}")
KEYWORDS_RE = re.compile(r"关键词|关键字|keywords?[:：]?", re.I)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def inside(project, path):
    candidate = Path(path)
    candidate = (candidate if candidate.is_absolute() else project / candidate).resolve()
    try:
        candidate.relative_to(project)
    except ValueError as exc:
        raise ValueError(f"Path leaves the project: {path}") from exc
    return candidate


def strip_comments(text):
    lines = []
    for line in text.splitlines():
        for index, char in enumerate(line):
            if char != "%":
                continue
            slash_count = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                slash_count += 1
                cursor -= 1
            if slash_count % 2 == 0:
                line = line[:index]
                break
        lines.append(line)
    return "\n".join(lines)


def inline_bibliography(tex):
    entries = []
    for environment in THEBIB_RE.finditer(tex):
        body = environment.group(1)
        items = list(BIBITEM_RE.finditer(body))
        for index, item in enumerate(items):
            end = items[index + 1].start() if index + 1 < len(items) else len(body)
            text = body[item.end():end]
            # DOI digits and retrieval URLs are not publication-date evidence.
            text = re.sub(r"\\(?:url|doi)\s*\{[^}]*\}", " ", text)
            text = re.sub(r"https?://[^\s{}]+|\b10\.\d{4,9}/[^\s{}]+", " ", text)
            candidates = sorted({int(year) for year in re.findall(
                r"(?<!\d)((?:18|19|20|21)\d{2})(?!\d)", text
            )})
            if not candidates and item.group(1):
                candidates = sorted({int(year) for year in re.findall(
                    r"(?<!\d)((?:18|19|20|21)\d{2})(?!\d)", item.group(1)
                )})
            entries.append((item.group(2).strip(), {
                "year": candidates[0] if len(candidates) == 1 else None,
                "file": "active TeX sources", "format": "inline",
                "year_candidates": candidates,
            }))
    return entries


class Audit:
    def __init__(self, project):
        self.project = project
        self.config = {}
        self.report = {
            "schema_version": 1,
            "audited_utc": datetime.now(timezone.utc).isoformat(),
            "passed": [],
            "failed": [],
            "warnings": [],
            "checks": {},
            "limitations": [
                "PDF character bounds are an automated geometry check; they do not detect all overlaps, clipping, poor figures, or visual defects. Rendered pages still require visual review.",
                "Literal TeX input/citation parsing does not execute TeX macros or conditionals. Dynamic inputs require manual inspection.",
                "A finite numeric fact with an existing source file is traceable, but this audit does not prove its scientific correctness or resolve its locator.",
                "Reference years and keys are checked locally; DOI, metadata, and claim support require the separate reference-verification workflow.",
            ],
        }
        self.protected = {project / "project.json", project / "numeric_facts.json"}

    def record(self, level, code, message, **details):
        item = {"code": code, "message": message}
        if details:
            item["details"] = details
        self.report[level].append(item)

    def relative(self, path):
        return path.relative_to(self.project).as_posix()

    def load_config(self):
        path = self.project / "project.json"
        if not path.exists():
            self.record("warnings", "config.defaults", "project.json is absent; audit defaults are used.")
            return {}
        try:
            config = read_json(path)
            if not isinstance(config, dict):
                raise ValueError("project.json must contain an object")
            self.record("passed", "config.readable", "Project configuration is readable.")
            return config
        except (OSError, ValueError) as exc:
            self.record("failed", "config.invalid", str(exc))
            return {}

    def sources(self, config):
        manuscript = config.get("manuscript", {})
        template = config.get("template", {})
        main_name = manuscript.get("main") or template.get("entry") or "main.tex"
        try:
            main = inside(self.project, main_name)
        except ValueError as exc:
            self.record("failed", "tex.main_path", str(exc))
            return "", [], []
        files, blocks, bib_names = [], [], []

        def visit(path, stack):
            self.protected.add(path)
            if path in stack:
                self.record("failed", "tex.input_cycle", "Recursive TeX input cycle.", file=self.relative(path))
                return
            if not path.is_file():
                self.record("failed", "tex.input_missing", "Active TeX source is missing.", file=self.relative(path))
                return
            try:
                text = strip_comments(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError) as exc:
                self.record("failed", "tex.unreadable", str(exc), file=self.relative(path))
                return
            if path not in files:
                files.append(path)
            for match in BIB_RE.finditer(text):
                bib_names.extend(name.strip() for name in (match.group(1) or match.group(2)).split(","))
            cursor = 0
            for match in INPUT_RE.finditer(text):
                blocks.append(text[cursor:match.start()])
                cursor = match.end()
                name = match.group(1).strip()
                if any(char in name for char in ("#", "\\", "$")):
                    self.record("warnings", "tex.dynamic_input", "Dynamic TeX input was not expanded.", input=name)
                    continue
                source_name = Path(name)
                if not source_name.suffix:
                    source_name = source_name.with_suffix(".tex")
                try:
                    target = inside(self.project, main.parent / source_name)
                    if not target.exists() and path.parent != main.parent:
                        local = inside(self.project, path.parent / source_name)
                        if local.exists():
                            target = local
                            self.record("warnings", "tex.local_input_resolution", "Input resolved relative to its including file; confirm the TeX compiler uses the same search path.", file=self.relative(target))
                except ValueError as exc:
                    self.record("failed", "tex.input_path", str(exc))
                    continue
                visit(target, stack + [path])
            blocks.append(text[cursor:])

        visit(main, [])
        if files:
            self.record("passed", "tex.sources", "Active literal TeX inputs were inspected.", files=len(files))
        self.report["checks"]["manuscript"] = {
            "main": self.relative(main), "active_files": [self.relative(p) for p in files]
        }
        if not bib_names and not THEBIB_RE.search("\n".join(blocks)) and (main.parent / "references.bib").is_file():
            bib_names.append("references.bib")
        bib_files = []
        for name in dict.fromkeys(bib_names):
            if any(char in name for char in ("#", "\\", "$")):
                self.record("warnings", "bibliography.dynamic", "Dynamic bibliography resource needs manual review.", resource=name)
                continue
            try:
                path = Path(name)
                if not path.suffix:
                    path = path.with_suffix(".bib")
                path = inside(self.project, main.parent / path)
            except ValueError as exc:
                self.record("failed", "bibliography.path", str(exc))
                continue
            self.protected.add(path)
            if path not in bib_files:
                bib_files.append(path)
        return "\n".join(blocks), files, bib_files

    def bibliography(self, config, tex, paths):
        entries = {}
        duplicate_keys = []
        for path in paths:
            if not path.is_file():
                self.record("failed", "bibliography.missing", "Bibliography resource is missing.", file=self.relative(path))
                continue
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                self.record("failed", "bibliography.unreadable", str(exc), file=self.relative(path))
                continue
            matches = list(ENTRY_RE.finditer(text))
            for index, match in enumerate(matches):
                if match.group("type").lower() in {"comment", "preamble", "string"}:
                    continue
                key = match.group("key")
                block = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
                year_match = YEAR_RE.search(block)
                year = next((int(g) for g in year_match.groups() if g), None) if year_match else None
                if key in entries:
                    duplicate_keys.append(key)
                entries[key] = {"year": year, "file": self.relative(path)}
        inline = inline_bibliography(tex)
        for key, entry in inline:
            if key in entries:
                duplicate_keys.append(key)
            entries[key] = entry
        if inline:
            self.record("warnings", "bibliography.inline_metadata_review",
                        "Inline bibliography keys were recognized; verify publication metadata and claim support against the cited sources.",
                        keys=[key for key, _ in inline])
        cited = []
        cite_all = False
        for match in CITE_RE.finditer(tex):
            for key in match.group(1).split(","):
                key = key.strip()
                if key == "*":
                    cite_all = True
                elif key and key not in cited:
                    cited.append(key)
        if cite_all:
            cited.extend(key for key in entries if key not in cited)
        missing = sorted(set(cited) - set(entries))
        if duplicate_keys:
            self.record("failed", "bibliography.duplicate_keys", "Duplicate bibliography keys.", keys=sorted(set(duplicate_keys)))
        else:
            self.record("passed", "bibliography.unique_keys", "Bibliography keys are unique.", count=len(entries))
        if missing:
            self.record("failed", "bibliography.undefined_citations", "Cited keys are absent from active bibliography resources.", keys=missing)
        else:
            self.record("passed", "bibliography.citations_resolve", "All parsed citation keys resolve.", count=len(cited))
        unused = sorted(set(entries) - set(cited))
        if unused:
            self.record("warnings", "bibliography.unused_entries", "Bibliography contains uncited entries; only cited entries are checked for recency.", keys=unused)
        if not cited:
            self.record("warnings", "bibliography.no_citations", "No external citations were found in active literal TeX sources.")
        options = config.get("citations", {})
        current_year = datetime.now(timezone.utc).year
        try:
            recent_years = int(options.get("recent_years", config.get("recent_years", 6)))
            if recent_years < 1:
                raise ValueError("recent_years must be positive")
            min_year = int(options.get("min_year", current_year - recent_years + 1))
        except (TypeError, ValueError) as exc:
            self.record("failed", "bibliography.year_config", str(exc))
            min_year = current_year - 5
        allowed = options.get("allowed_classics", config.get("allowed_classics", []))
        if not isinstance(allowed, list):
            self.record("failed", "bibliography.classics_config", "allowed_classics must be a list of citation keys.")
            allowed = []
        allowed = {item for item in allowed if isinstance(item, str)}
        age_failures = []
        classic_keys = []
        manual_year_keys = []
        for key in cited:
            if key not in entries:
                continue
            year = entries[key]["year"]
            if year is None:
                if entries[key].get("format") == "inline":
                    manual_year_keys.append(key)
                else:
                    age_failures.append({"key": key, "year": None, "reason": "missing literal year"})
            elif year > current_year:
                age_failures.append({"key": key, "year": year, "reason": "future year"})
            elif year < min_year and key not in allowed:
                age_failures.append({"key": key, "year": year, "reason": "outside configured recent window"})
            elif year < min_year:
                classic_keys.append(key)
        if age_failures:
            self.record("failed", "bibliography.recency", "Some cited publications need a year correction, a recent replacement, or an explicit classic exception.", entries=age_failures, min_year=min_year)
        else:
            self.record("passed", "bibliography.recency", "Extractable cited years satisfy the recent window or explicit classic exceptions.", min_year=min_year, max_year=current_year, allowed_classics=classic_keys, inline_years_requiring_review=manual_year_keys)
        if manual_year_keys:
            self.record("warnings", "bibliography.inline_year_manual",
                        "Some inline entries have no single recognizable publication year; verify these years manually.",
                        entries=[{"key": key, "year_candidates": entries[key]["year_candidates"]} for key in manual_year_keys])
        self.report["checks"]["bibliography"] = {
            "resources": [self.relative(p) for p in paths], "cited_keys": cited,
            "entry_count": len(entries), "entries": entries, "min_year": min_year,
        }

    def template(self, config):
        options = config.get("template", {})
        baseline_name = options.get("baseline", "notes/template-baseline.json")
        try:
            baseline = inside(self.project, baseline_name)
            directory = inside(self.project, options.get("directory", "template"))
        except (TypeError, ValueError) as exc:
            self.record("failed", "template.path", str(exc))
            return
        self.protected.add(baseline)
        if not baseline.is_file():
            if options.get("mode") == "user-provided":
                self.record("warnings", "template.baseline_missing", "User-template baseline is absent; original-file preservation cannot be checked.")
            self.report["checks"]["template"] = {"baseline_available": False}
            return
        try:
            values = read_json(baseline)
            if isinstance(values, dict) and "files" in values:
                values = values["files"]
            if isinstance(values, list):
                values = {item["path"]: item["sha256"] for item in values}
            if not isinstance(values, dict):
                raise ValueError("Template baseline must map relative file names to SHA-256 hashes")
            differences = []
            for name, expected in values.items():
                path = inside(self.project, directory / name)
                try:
                    path.relative_to(directory)
                except ValueError as exc:
                    raise ValueError(f"Template baseline path leaves template directory: {name}") from exc
                self.protected.add(path)
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                    raise ValueError(f"Invalid template SHA-256 for {name}")
                actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                if actual != expected.lower():
                    differences.append({"file": name, "expected_sha256": expected, "actual_sha256": actual})
            if differences:
                self.record("failed", "template.changed", "Original template files differ from their baseline.", files=differences)
            elif values:
                self.record("passed", "template.unchanged", "Original template files match their baseline.", files=len(values))
            else:
                self.record("warnings", "template.baseline_empty", "Template baseline contains no files.")
            self.report["checks"]["template"] = {"baseline_available": True, "file_count": len(values), "differences": differences}
        except (KeyError, OSError, TypeError, ValueError) as exc:
            self.record("failed", "template.baseline_invalid", str(exc))

    def facts(self, config):
        try:
            path = inside(self.project, config.get("numeric_facts", "numeric_facts.json"))
        except (TypeError, ValueError) as exc:
            self.record("failed", "facts.path", str(exc))
            return
        self.protected.add(path)
        if not path.is_file():
            self.report["checks"]["numeric_facts"] = {"available": False}
            return
        try:
            document = read_json(path)
            facts = document.get("facts") if isinstance(document, dict) else None
            if not isinstance(facts, list):
                raise ValueError("numeric_facts.json must contain a facts array")
            ids, failures = set(), []
            for index, fact in enumerate(facts):
                if not isinstance(fact, dict):
                    failures.append({"index": index, "reason": "fact must be an object"})
                    continue
                identifier = fact.get("id")
                if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
                    failures.append({"index": index, "reason": "missing or duplicate fact id"})
                else:
                    ids.add(identifier)
                value = fact.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    failures.append({"id": identifier, "reason": "value must be a finite numeric scalar"})
                source = fact.get("source", {})
                artifact = source.get("artifact") if isinstance(source, dict) else None
                if not isinstance(artifact, str) or not artifact.strip():
                    failures.append({"id": identifier, "reason": "source artifact is required"})
                    continue
                try:
                    artifact_path = inside(self.project, artifact)
                    self.protected.add(artifact_path)
                    if not artifact_path.is_file():
                        failures.append({"id": identifier, "reason": "source artifact does not exist", "artifact": artifact})
                except ValueError as exc:
                    failures.append({"id": identifier, "reason": str(exc)})
                if not source.get("locator"):
                    self.record("warnings", "facts.locator_missing", "Numeric fact source has no locator.", id=identifier)
            if failures:
                self.record("failed", "facts.traceability", "Numeric facts have missing sources, invalid values, or invalid IDs.", facts=failures)
            else:
                self.record("passed", "facts.traceability", "Numeric facts have finite values and existing source artifacts.", count=len(facts))
            self.report["checks"]["numeric_facts"] = {"available": True, "file": self.relative(path), "fact_count": len(facts)}
        except (OSError, TypeError, ValueError) as exc:
            self.record("failed", "facts.invalid", str(exc))

    def pdf(self, config, pdf_name, require_abstract):
        try:
            path = inside(self.project, pdf_name)
        except ValueError as exc:
            self.record("failed", "pdf.path", str(exc))
            return
        self.protected.add(path)
        if not path.is_file():
            self.record("failed", "pdf.missing", "Final PDF is missing.", file=self.relative(path))
            return
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            page_count = len(reader.pages)
            if page_count == 0:
                raise ValueError("PDF contains no pages")
            texts = [page.extract_text() or "" for page in reader.pages]
            self.record("passed", "pdf.readable", "PDF is readable.", pages=page_count)
        except ImportError:
            self.record("failed", "pdf.reader_missing", "Install pypdf to audit the PDF.")
            return
        except Exception as exc:
            self.record("failed", "pdf.unreadable", str(exc))
            return
        paper = config.get("paper", {})
        abstract = config.get("abstract", {})
        enforce_abstract = require_abstract or bool(paper.get("abstract_one_page", abstract.get("stay_on_one_page", True)))
        compact = [re.sub(r"\s+", "", text) for text in texts]
        keyword_pages = [index + 1 for index, text in enumerate(compact) if KEYWORDS_RE.search(text)]
        if enforce_abstract:
            if 1 in keyword_pages:
                self.record("passed", "pdf.abstract_page_one", "Keywords were found on page one; visually confirm the complete abstract and its layout.")
            else:
                self.record("failed", "pdf.abstract_page_one", "One-page abstract is required, but page one has no recognized keywords label.", keyword_pages=keyword_pages)
        contents_on_two = len(compact) > 1 and bool(re.search(r"目录|tableofcontents|contents", compact[1], re.I))
        self.report["checks"]["pdf"] = {
            "file": self.relative(path), "page_count": page_count,
            "abstract_page_one_required": enforce_abstract,
            "keyword_pages": keyword_pages,
            "page_two_contains_contents_label": contents_on_two,
            "visual_review_required": True,
        }
        try:
            import pdfplumber
        except ImportError:
            self.record("failed", "pdf.layout_dependency_missing", "Install pdfplumber for character-bound checks.")
            return
        try:
            tolerance = float(config.get("audit", {}).get("page_bound_tolerance_pt", 1.0))
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError("page_bound_tolerance_pt must be finite and nonnegative")
            pages, overflow, empty = [], [], []
            with pdfplumber.open(path) as pdf:
                for index, page in enumerate(pdf.pages):
                    chars = page.chars
                    bounds = None
                    if chars:
                        bounds = {
                            "x0": min(c["x0"] for c in chars), "top": min(c["top"] for c in chars),
                            "x1": max(c["x1"] for c in chars), "bottom": max(c["bottom"] for c in chars),
                        }
                    else:
                        empty.append(index + 1)
                    outside = [c for c in chars if c["x0"] < -tolerance or c["top"] < -tolerance or c["x1"] > page.width + tolerance or c["bottom"] > page.height + tolerance]
                    if outside:
                        overflow.append({"page": index + 1, "character_count": len(outside), "sample": "".join(c.get("text", "") for c in outside[:40])})
                    pages.append({"page": index + 1, "width_pt": page.width, "height_pt": page.height,
                                  "character_count": len(chars), "character_bounds_pt": bounds,
                                  "outside_page_character_count": len(outside)})
            self.report["checks"]["pdf"]["character_geometry"] = pages
            self.report["checks"]["pdf"]["bound_tolerance_pt"] = tolerance
            if overflow:
                self.record("failed", "pdf.characters_outside_page", "Characters extend outside PDF page bounds.", pages=overflow)
            else:
                self.record("passed", "pdf.character_bounds", "Extracted character boxes lie within page bounds.", tolerance_pt=tolerance)
            if empty:
                self.record("warnings", "pdf.no_extractable_characters", "Some pages contain no extractable characters; inspect scanned or graphical pages visually.", pages=empty)
        except Exception as exc:
            self.record("failed", "pdf.layout_check", str(exc))

    def run(self, pdf_name, require_abstract=False):
        config = self.load_config()
        self.config = config
        tex, _, bib_paths = self.sources(config)
        self.bibliography(config, tex, bib_paths)
        self.template(config)
        self.facts(config)
        self.pdf(config, pdf_name, require_abstract)
        self.report["summary"] = {key: len(self.report[key]) for key in ("passed", "failed", "warnings")}
        self.report["status"] = "failed" if self.report["failed"] else "passed"
        return self.report


def audit_project(project, pdf="delivery/paper.pdf", require_abstract_page_one=False):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError(f"Project directory does not exist: {project}")
    return Audit(project).run(pdf, require_abstract_page_one)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Manuscript project directory")
    parser.add_argument("--pdf", default="delivery/paper.pdf", help="PDF path relative to project")
    parser.add_argument("--output", default="notes/audit.json", help="JSON report path relative to project")
    parser.add_argument("--require-abstract-page-one", action="store_true", help="Require a keywords label on the first PDF page")
    args = parser.parse_args(argv)
    try:
        project = Path(args.project).resolve()
        if not project.is_dir():
            raise ValueError(f"Project directory does not exist: {project}")
        audit = Audit(project)
        report = audit.run(args.pdf, args.require_abstract_page_one)
        output = inside(project, args.output)
        template_dir = inside(project, audit.config.get("template", {}).get("directory", "template"))
        if output.suffix.lower() != ".json":
            raise ValueError("Audit report must use a .json extension")
        if output in audit.protected or output == template_dir or template_dir in output.parents:
            raise ValueError("Audit output would overwrite a source, template, configuration, PDF, or numeric evidence file")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"{report['status'].upper()}: {report['summary']['passed']} passed, {report['summary']['failed']} failed, {report['summary']['warnings']} warnings")
        print(output)
        print("Automated audit does not replace visual or scientific review.")
        return 1 if report["failed"] else 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"Audit error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    raise SystemExit(main())
