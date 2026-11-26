#!/usr/bin/env python3
"""Build a saved local manuscript with XeLaTeX and BibTeX or Biber."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def executable(name: str, explicit: str | None = None) -> str:
    if explicit:
        path = Path(explicit).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"Not an executable file: {path}")
        return str(path)
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        path = Path(os.environ["LOCALAPPDATA"]) / "Programs/MiKTeX/miktex/bin/x64" / (name + ".exe")
        if path.is_file():
            return str(path)
    raise FileNotFoundError(f"{name} is unavailable. Install a TeX distribution or pass its path.")


def run_pass(command: list[str], project: Path, log: Path, timeout: int) -> None:
    completed = subprocess.run(command, cwd=project, capture_output=True,
                               timeout=timeout, check=False)
    output = completed.stdout.decode("utf-8", "replace")
    errors = completed.stderr.decode("utf-8", "replace")
    log.write_text(output + "\n" + errors, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"Build pass failed ({completed.returncode}); read {log}")


def manuscript_pdf_path(project: Path, manuscript: dict) -> Path:
    if not isinstance(manuscript, dict):
        raise ValueError("The manuscript setting in project.json must be an object.")
    name = manuscript.get("output_pdf", "delivery/paper.pdf")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("manuscript.output_pdf must be a project-relative .pdf path.")
    relative = Path(name.replace("\\", "/"))
    if relative.anchor or ":" in name or ".." in relative.parts or relative.suffix.casefold() != ".pdf":
        raise ValueError("manuscript.output_pdf must be a project-relative .pdf path.")
    target = (project / relative).resolve()
    if not target.is_relative_to(project) or target.suffix.casefold() != ".pdf":
        raise ValueError("manuscript.output_pdf must resolve to a .pdf file within the project.")
    return target


def compile_document(document: Path, project: Path, work: Path, logs: Path,
                     engine: str | None, bibtex: str | None, biber: str | None, timeout: int) -> tuple[bool, bool]:
    # Nested \include commands write auxiliary files alongside their relative source paths.
    for source in project.rglob("*.tex"):
        (work / source.parent.relative_to(project)).mkdir(parents=True, exist_ok=True)
    work_relative = work.relative_to(project)
    xelatex = executable("xelatex", engine)
    args = [xelatex, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
            "-file-line-error", f"-output-directory={work_relative.as_posix()}", document.name]
    bcf = work / document.with_suffix(".bcf").name
    run_pass(args, project, logs / "xelatex-1.txt", timeout)
    aux = work / document.with_suffix(".aux").name
    aux_text = aux.read_text(encoding="utf-8", errors="replace") if aux.exists() else ""
    used_bibtex = bool(re.search(r"\\bibdata\{", aux_text)) and any(
        r"\citation{" in path.read_text(encoding="utf-8", errors="replace")
        for path in work.rglob("*.aux")
    )
    biblatex_aux = bool(re.search(r"\\abx@aux@", aux_text))
    # Biblatex's BibTeX backend also writes abx markers; its bibdata takes precedence.
    used_biber = not used_bibtex and (bcf.is_file() or biblatex_aux)
    if used_bibtex:
        run_pass([executable("bibtex", bibtex), (work_relative / aux.name).as_posix()], project,
                 logs / "bibtex.txt", timeout)
    elif used_biber:
        if not bcf.is_file():
            raise RuntimeError("Biblatex requires Biber, but the first XeLaTeX pass produced no .bcf control file.")
        run_pass([executable("biber", biber), "--input-directory", work_relative.as_posix(),
                  "--output-directory", work_relative.as_posix(), bcf.name], project,
                 logs / "biber.txt", timeout)
    for index in (2, 3):
        run_pass(args, project, logs / f"xelatex-{index}.txt", timeout)
    log_text = (work / document.with_suffix(".log").name).read_text(encoding="utf-8", errors="replace")
    failures = re.findall(
        r"^(?:.*(?:Citation .+ undefined|Reference .+ undefined|There were undefined references|"
        r"Missing character:|Overfull \\[hv]box).*)$", log_text, re.MULTILINE)
    if failures:
        raise RuntimeError("Layout/reference errors require repair:\n" + "\n".join(failures[:20]))
    return used_bibtex, used_biber


def build(project: Path, main: str | None = None, engine: str | None = None,
          bibtex: str | None = None, timeout: int = 180, biber: str | None = None) -> dict:
    project = project.expanduser().resolve(strict=True)
    config_path = project / "project.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("project.json must contain an object.")
    manuscript = config.setdefault("manuscript", {})
    target = manuscript_pdf_path(project, manuscript)
    entry = main or manuscript.get("main") or config.get("template", {}).get("entry") or "main.tex"
    document = (project / entry).resolve(strict=True)
    if document.parent != project or document.suffix != ".tex":
        raise ValueError("--main must be a .tex file directly inside the project root.")
    if document.name.startswith("-"):
        raise ValueError("The manuscript filename cannot start with a command-line option.")
    logs = project / "notes" / "build"
    if not logs.resolve().is_relative_to(project):
        raise ValueError("The build directory must resolve within the project.")
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tex-", dir=logs) as directory:
        work = Path(directory)
        used_bibtex, used_biber = compile_document(document, project, work, logs, engine, bibtex, biber, timeout)
        pdf = work / document.with_suffix(".pdf").name
        if not pdf.is_file() or pdf.stat().st_size == 0:
            raise RuntimeError("The compiler did not produce a nonempty PDF.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".zpb-pdf-", dir=target.parent, delete=False) as stream:
            temporary_pdf = Path(stream.name)
        try:
            shutil.copy2(pdf, temporary_pdf)
            temporary_pdf.replace(target)
        finally:
            temporary_pdf.unlink(missing_ok=True)
    report = {"main": document.name, "pdf": target.relative_to(project).as_posix(), "bytes": target.stat().st_size,
              "bibtex_run": used_bibtex, "biber_run": used_biber,
              "bibliography_backend": "bibtex" if used_bibtex else "biber" if used_biber else None,
              "xelatex_passes": 3,
              "logs": "notes/build", "requires_visual_review": True}
    (logs / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manuscript["main"] = document.name
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=project,
                                     prefix=".zpb-project-", suffix=".json", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        temporary.replace(config_path)
    finally:
        temporary.unlink(missing_ok=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--main", help="Root-level .tex entry; defaults to the saved manuscript setting.")
    parser.add_argument("--engine")
    parser.add_argument("--bibtex")
    parser.add_argument("--biber")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds per compiler pass.")
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive.")
        report = build(args.project, args.main, args.engine, args.bibtex, args.timeout, args.biber)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Build failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
