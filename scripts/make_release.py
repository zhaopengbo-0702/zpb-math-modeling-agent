#!/usr/bin/env python3
"""Create a source-only GitHub-ready release with per-file SHA256 evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


VERSION = "1.0.0"
SLUG = "zpb-math-modeling-agent"
SKIP = {".git", "__pycache__", ".venv", ".runtime", "dist", "tmp", "_work",
        "demo-output", "local-reference", "node_modules", ".pytest_cache"}
EXTENSIONS = {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".tex", ".bib", ".png", ".svg"}


def release(root: Path, output: Path, validate: bool = True) -> dict:
    root = root.resolve(strict=True)
    output = output.resolve()
    if output == root or root.is_relative_to(output):
        raise ValueError("Release outputs cannot be the source directory or one of its ancestors.")
    if validate:
        subprocess.run([sys.executable, str(root / "scripts/validate_bundle.py"), "--root", str(root)], check=True)
    records, contents = [], []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIP for part in relative.parts) or path.is_relative_to(output):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink cannot enter the release: {relative}")
        if not path.is_file():
            continue
        if path.suffix.lower() not in EXTENSIONS and relative.as_posix() not in {"LICENSE", ".gitignore"}:
            raise ValueError(f"Unexpected file in source release: {relative}")
        content = path.read_bytes()
        records.append({"file": relative.as_posix(), "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest()})
        contents.append((relative.as_posix(), content))
    if not any(record["file"] == "SKILL.md" for record in records):
        raise ValueError("A source release must include SKILL.md.")
    manifest = {"name": SLUG, "display_name": "zpb-math-modeling-agent", "version": VERSION,
                "files": records, "excludes": sorted(SKIP)}
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{SLUG}-v{VERSION}.zip"
    with tempfile.NamedTemporaryFile(prefix="zpb-release-", suffix=".zip", dir=output, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for relative, content in contents:
                archive.writestr(f"{SLUG}/{relative}", content)
            archive.writestr(f"{SLUG}/RELEASE-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        with zipfile.ZipFile(temporary) as archive:
            for record in records:
                actual = archive.read(f"{SLUG}/{record['file']}")
                if hashlib.sha256(actual).hexdigest() != record["sha256"]:
                    raise RuntimeError(f"Archive verification failed: {record['file']}")
        for record in records:
            if hashlib.sha256((root / record["file"]).read_bytes()).hexdigest() != record["sha256"]:
                raise RuntimeError(f"Source changed during packaging: {record['file']}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / (target.name + ".sha256")).write_text(f"{digest}  {target.name}\n", encoding="ascii")
    report = {"archive": str(target), "source_files": len(records), "bytes": target.stat().st_size,
              "sha256": digest, "version": VERSION}
    (output / "release-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = release(args.root, args.output or args.root / "dist")
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
