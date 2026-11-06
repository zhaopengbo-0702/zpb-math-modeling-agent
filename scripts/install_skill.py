#!/usr/bin/env python3
"""Install this portable skill in a user's personal skills directory."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile


SKIP = {".git", "__pycache__", ".venv", ".runtime", "dist", "tmp", "_work", "demo-output", "local-reference"}


def install(source: Path, skills_root: Path, replace: bool = False) -> Path:
    source = source.expanduser().resolve(strict=True)
    metadata = (source / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"^name:\s*([a-z0-9-]+)\s*$", metadata, re.MULTILINE)
    if not match or match.group(1) != "zpb-math-modeling-agent":
        raise ValueError("Source is not the expected skill.")
    skills_root = skills_root.expanduser().resolve()
    target = skills_root / match.group(1)
    if target == source or target.is_relative_to(source):
        raise ValueError("Install destination cannot be inside the release source.")
    if target.is_symlink():
        raise ValueError("Refusing to replace a symlink.")
    if target.exists() and not replace:
        raise FileExistsError(f"Already installed: {target}; use --replace to keep a backup and update.")
    for path in source.rglob("*"):
        if not any(part in SKIP for part in path.relative_to(source).parts) and path.is_symlink():
            raise ValueError(f"Source contains a symlink: {path.relative_to(source)}")
    skills_root.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".zpb-install-", dir=skills_root))
    backup = None
    try:
        def ignore(directory: str, names: list[str]) -> set[str]:
            return {name for name in names if name in SKIP or name.endswith((".pyc", ".zip"))}
        shutil.copytree(source, staged, dirs_exist_ok=True, ignore=ignore)
        if target.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = skills_root / (".zpb-backup-" + timestamp)
            target.rename(backup)
        try:
            staged.rename(target)
        except OSError:
            if backup is not None:
                backup.rename(target)
            raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--skills-root", type=Path, default=Path.home() / ".agents" / "skills")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        target = install(args.source, args.skills_root, args.replace)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Installation failed: {error}\n")
    print(json.dumps({"installed": str(target), "display_name": "zpb数学建模agent",
                      "invocation": "$zpb-math-modeling-agent"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
