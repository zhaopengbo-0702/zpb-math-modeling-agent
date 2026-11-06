#!/usr/bin/env python3
"""Report scientific Python, Chinese fonts and LaTeX tooling without installing software."""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys


MODULES = {"numpy": "numpy", "scipy": "scipy", "matplotlib": "matplotlib",
           "openpyxl": "openpyxl", "pypdf": "pypdf", "pdfplumber": "pdfplumber",
           "PIL": "Pillow", "yaml": "PyYAML"}
FONT_CANDIDATES = ("SimHei", "Noto Sans CJK SC", "Source Han Sans SC",
                   "Microsoft YaHei", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
                   "PingFang SC", "Heiti SC", "Arial Unicode MS")


def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt" and name in {"xelatex", "bibtex", "biber"}:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            candidate = Path(base) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / (name + ".exe")
            if candidate.is_file():
                return str(candidate)
    return None


def inspect_environment() -> dict:
    modules = {}
    for module, distribution in MODULES.items():
        available = importlib.util.find_spec(module) is not None
        try:
            version = importlib.metadata.version(distribution) if available else None
        except importlib.metadata.PackageNotFoundError:
            version = None
        modules[module] = {"available": available, "version": version}
    tools = {name: find_tool(name) for name in ("xelatex", "bibtex", "biber", "pdftoppm", "pdfinfo")}
    font_report: dict = {"preferred": "SimHei", "selected": None, "path": None, "available": []}
    if modules["matplotlib"]["available"]:
        try:
            from matplotlib import font_manager
            for name in FONT_CANDIDATES:
                try:
                    path = font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
                except ValueError:
                    continue
                font_report["available"].append(name)
                if font_report["selected"] is None:
                    font_report.update(selected=name, path=path)
        except Exception as error:
            font_report["error"] = str(error)
    compute = all(modules[name]["available"] for name in ("numpy", "scipy", "matplotlib", "openpyxl"))
    return {"schema_version": 1, "python": sys.version.split()[0], "platform": platform.system(),
            "libraries": modules, "tools": tools, "chinese_font": font_report,
            "ready_for_compute": compute,
            "ready_for_chinese_figures": bool(modules["matplotlib"]["available"] and font_report["selected"]),
            "ready_for_pdf": bool(tools["xelatex"] and tools["bibtex"] and modules["pypdf"]["available"]
                                  and modules["pdfplumber"]["available"]),
            "tex_compilation_tested": False,
            "pdf_check_scope": "Tool presence only; compile the chosen template to verify its fonts and packages.",
            "actions_performed": "read-only inspection; no packages installed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save the JSON report here.")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero when compute, Chinese fonts or PDF tools are missing.")
    args = parser.parse_args(argv)
    report = inspect_environment()
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    ready = all(report[key] for key in ("ready_for_compute", "ready_for_chinese_figures", "ready_for_pdf"))
    return 1 if args.strict and not ready else 0


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
