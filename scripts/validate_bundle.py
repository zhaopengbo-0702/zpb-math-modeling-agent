#!/usr/bin/env python3
"""Check the release's skill metadata, links, scripts and portable contents."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys


def validate(root: Path) -> dict:
    import yaml

    root = root.resolve(strict=True)
    failures, warnings = [], []
    source = root / "SKILL.md"
    if not source.is_file():
        return {"passed": False, "failures": ["SKILL.md is missing"], "warnings": []}
    text = source.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    try:
        meta = yaml.safe_load(match.group(1)) if match else {}
        if not isinstance(meta, dict):
            raise ValueError("frontmatter must be an object")
        if meta.get("name") != "zpb-math-modeling-agent" or not meta.get("description"):
            failures.append("Skill name or description is invalid")
    except (ValueError, yaml.YAMLError) as error:
        failures.append(f"Invalid frontmatter: {error}")
    ui = root / "agents/openai.yaml"
    try:
        data = yaml.safe_load(ui.read_text(encoding="utf-8"))
        if data["interface"]["display_name"] != "zpb-math-modeling-agent":
            failures.append("Display name differs from requested name")
        if "$zpb-math-modeling-agent" not in data["interface"].get("default_prompt", ""):
            failures.append("Default prompt must invoke the skill")
    except (OSError, TypeError, KeyError, yaml.YAMLError) as error:
        failures.append(f"Invalid agents/openai.yaml: {error}")
    required = ["LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "requirements.txt",
                "assets/templates/main.tex", "scripts/init_project.py", "scripts/audit_project.py",
                "scripts/build_paper.py", "scripts/package_submission.py", "scripts/install_skill.py"]
    for name in required:
        if not (root / name).is_file():
            failures.append(f"Missing release file: {name}")
    checked = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(p in {".git", "__pycache__", ".venv", "dist", "tmp", "_work"} for p in relative.parts):
            continue
        if path.is_symlink():
            failures.append(f"Symlink in release: {relative}")
            continue
        if not path.is_file():
            continue
        checked += 1
        if path.suffix.lower() in {".ttf", ".otf", ".ttc", ".exe", ".dll", ".npz", ".zip"}:
            failures.append(f"Unexpected bundled binary: {relative}")
        if path.suffix in {".py", ".md", ".json", ".yaml", ".tex", ".yml"}:
            content = path.read_text(encoding="utf-8")
            if re.search(r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]", content):
                failures.append(f"Machine-specific home path: {relative}")
            if "[TO" + "DO:" in content:
                failures.append(f"Unfinished scaffold: {relative}")
            if path.suffix == ".py":
                try:
                    ast.parse(content, filename=str(relative))
                except SyntaxError as error:
                    failures.append(f"Python syntax: {relative}: {error}")
            if path.suffix == ".md":
                for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", content):
                    link = link.split("#", 1)[0].strip("<>")
                    if not link or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", link):
                        continue
                    target = (path.parent / link).resolve()
                    if not target.is_relative_to(root) or not target.exists():
                        failures.append(f"Broken local link: {relative}: {link}")
    return {"passed": not failures, "checked_files": checked,
            "failures": failures, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        report = validate(args.root)
    except (OSError, ImportError) as error:
        parser.exit(1, f"Validation unavailable: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
