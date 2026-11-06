#!/usr/bin/env python3
"""Initialize an isolated modeling project without replacing existing work."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIRS = ("inputs", "code", "data", "results", "figures", "sections", "notes", "delivery")
SECTIONS = (
    "简介", "问题分析", "模型假设", "名词解释及主要符号说明",
    "模型建立与求解", "灵敏度分析与鲁棒性检验", "模型的评价、改进与推广",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_file(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def initialize(output: Path, inputs: list[Path], title: str, template: Path | None = None,
               assets: Path | None = None) -> dict:
    output = output.expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"Output must be a new or empty directory: {output}")
    sources = [path.expanduser().resolve(strict=True) for path in inputs]
    if any(not path.is_file() for path in sources):
        raise ValueError("Each --input must name a regular file; repeat it for several files.")
    names = [path.name.casefold() for path in sources]
    if len(names) != len(set(names)):
        raise ValueError("Input basenames must be unique; rename ambiguous copies before importing.")
    if template is not None:
        template = template.expanduser().resolve(strict=True)
        if not template.is_dir() or not any(template.rglob("*.tex")):
            raise ValueError("The template directory must contain at least one .tex file.")
        if output == template or output.is_relative_to(template):
            raise ValueError("Output cannot be inside the template directory.")
        if any(path.is_symlink() for path in template.rglob("*")):
            raise ValueError("Template symlinks are not copied; provide ordinary local files.")
    else:
        assets = assets or SKILL_ROOT / "assets" / "templates"
        if not (assets / "main.tex").is_file():
            raise FileNotFoundError(f"Fallback template not found: {assets / 'main.tex'}")

    output.mkdir(parents=True, exist_ok=True)
    for name in PROJECT_DIRS:
        (output / name).mkdir()
    imported = []
    for source in sources:
        target = output / "inputs" / source.name
        shutil.copy2(source, target)
        imported.append({"file": target.relative_to(output).as_posix(),
                         "sha256": sha256(target), "bytes": target.stat().st_size})
    template_info: dict = {"mode": "original-fallback", "entry": "main.tex"}
    if template is not None:
        shutil.copytree(template, output / "template")
        baseline = {
            path.relative_to(output / "template").as_posix(): sha256(path)
            for path in sorted((output / "template").rglob("*")) if path.is_file()
        }
        json_file(output / "notes" / "template-baseline.json", baseline)
        template_info = {"mode": "user-provided", "directory": "template",
                         "baseline": "notes/template-baseline.json",
                         "entry": None, "preserve_class_and_style": True}
    else:
        for source in sorted(assets.iterdir()):
            if source.is_file():
                shutil.copy2(source, output / source.name)
    manifest = {"schema_version": 1, "inputs": imported}
    json_file(output / "notes" / "input-manifest.json", manifest)
    config = {
        "schema_version": 1,
        "skill": "zpb-math-modeling-agent",
        "display_name": "zpb数学建模agent",
        "title": title,
        "language": "zh-CN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sections": list(SECTIONS),
        "include_literature_review": False,
        "recent_years": 6,
        "figures": {"dpi": 600, "single_figure_per_file": True,
                    "formats": ["pdf", "svg", "png"], "preferred_font": "SimHei",
                    "plan_per_question": True},
        "tables": {"per_question_results": True, "symbols_and_units": True,
                   "solver_settings": True, "validation": True,
                   "sensitivity_and_robustness": True, "formats": ["tex", "xlsx"]},
        "abstract": {"target_page": 1, "stay_on_one_page": True,
                     "emphasis": "black-bold", "include_per_question_results": True},
        "template": template_info,
        "input_manifest": "notes/input-manifest.json",
        "required_deliverables": [],
    }
    json_file(output / "project.json", config)
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", type=Path, action="append", default=[], help="Input file; repeat as needed.")
    parser.add_argument("--template", type=Path, help="User's complete LaTeX template directory.")
    parser.add_argument("--title", default="数学建模论文")
    args = parser.parse_args(argv)
    try:
        initialize(args.output, args.input, args.title, args.template)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Initialization failed: {error}\n")
    print(f"Initialized: {args.output.expanduser().resolve()}")
    print("Next: read the problem, fill the task contract and choose methods before computing.")
    return 0


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
