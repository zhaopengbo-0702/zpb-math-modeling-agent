#!/usr/bin/env python3
"""Package explicit deliverables and a clean reproducibility archive with SHA256 manifests."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import zipfile


class PackagingError(ValueError):
    """A project cannot be packaged without changing or omitting required work."""


SUPPORT_DIRS = ("inputs", "code", "data", "results", "figures", "tables", "sections", "notes", "template")
ROOT_FILES = ("main.tex", "project.json", "numeric_facts.json", "references.bib", "README.md", "main.pdf", "requirements.txt")
SKIP_DIRS = {"node_modules", "venv", ".venv", "env", "__pycache__", "cache", "caches",
             "reference-cache", "reference_cache", "tmp", "temp", "previews", "archive", "archives", "history", "backups"}
SKIP_SUFFIXES = {".aux", ".log", ".toc", ".out", ".bbl", ".blg", ".fls", ".fdb_latexmk",
                 ".synctex", ".zip", ".7z", ".rar", ".pyc", ".pyo", ".bak", ".tmp", ".lock"}
SUPPORT_SUFFIXES = {".py", ".m", ".r", ".jl", ".c", ".cpp", ".h", ".hpp", ".mjs", ".js", ".ts",
                    ".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".ps1", ".sh", ".bat", ".ipynb",
                    ".csv", ".tsv", ".xlsx", ".xls", ".npz", ".npy", ".mat", ".h5", ".hdf5", ".parquet",
                    ".feather", ".arrow", ".pdf", ".svg", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".eps",
                    ".tex", ".bib", ".cls", ".sty", ".bst", ".bbx", ".cbx", ".lbx", ".dbx",
                    ".cfg", ".dtx", ".ins", ".docx", ".odt"}
NOTE_NAMES = {"task-contract.md", "task-contract.json", "problem-contract.md", "problem-contract.json",
              "method-plan.md", "model-plan.md", "method-ledger.json", "methods-ledger.json", "model-ledger.json",
              "claim-ledger.json", "claims-ledger.json", "calculation-ledger.json", "implementation-ledger.json",
              "template-baseline.json", "input-manifest.json", "audit.json", "audit-report.json", "quality-report.json",
              "numerical-validation.md", "numerical-validation.json", "numerical-audit.json", "diagnostics.json",
              "environment.json", "environment-report.json", "project-environment.json", "readme.md"}
OUTPUT_NAMES = ("submission.zip", "reproducibility.zip", "submission-manifest.json")


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise PackagingError("Deliverable paths must be nonempty relative strings.")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or any(part in {"", ".", ".."} for part in parts):
        raise PackagingError(f"Unsafe project-relative path: {value!r}")
    return PurePosixPath(normalized)


def hidden_or_private(name: str) -> bool:
    lower = name.casefold()
    if lower.startswith(".") or lower.endswith("~"):
        return True
    stem_tokens = set(re.split(r"[_.\s-]+", Path(lower).stem))
    if stem_tokens & {"secret", "secrets", "credential", "credentials", "password", "passwords", "passwd", "private", "token", "tokens", "apikey"}:
        return True
    if lower in {"id_rsa", "id_ed25519", "id_dsa", "authorized_keys", "known_hosts"}:
        return True
    return Path(lower).suffix in {".key", ".pem", ".p12", ".pfx", ".crt"}


def obsolete(name: str) -> bool:
    lower = name.casefold()
    tokens = set(re.split(r"[_.\s-]+", Path(lower).stem))
    if tokens & {"old", "draft", "backup", "bak", "history", "historical", "legacy", "obsolete", "previous"}:
        return True
    if lower.startswith(("manuscript_before_", "paper_before_", "main_before_")):
        return True
    return any(marker in name for marker in ("旧稿", "草稿", "历史备份", "旧版本", "修订前"))


def secure_file(project: Path, relative: str, *, required: bool = True) -> Path:
    rel = relative_path(relative)
    path = project
    for part in rel.parts:
        if hidden_or_private(part):
            raise PackagingError(f"Hidden or credential-like path cannot be packaged: {relative}")
        path = path / part
        if is_link(path):
            raise PackagingError(f"Symlink or junction cannot be packaged: {relative}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PackagingError(f"Missing deliverable: {relative}") from error
    if not resolved.is_relative_to(project) or not resolved.is_file():
        raise PackagingError(f"Not a regular file within the project: {relative}")
    attributes = getattr(resolved.stat(), "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2):
        raise PackagingError(f"Hidden file cannot be packaged: {relative}")
    if required and resolved.stat().st_size == 0:
        raise PackagingError(f"Required deliverable is empty: {relative}")
    return resolved


def signature(path: Path) -> tuple:
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


@dataclass(frozen=True)
class Snapshot:
    path: Path
    source_rel: str
    sha256: str
    size_bytes: int
    version: tuple


def capture(project: Path, relative: str, *, required: bool = True) -> Snapshot:
    path = secure_file(project, relative, required=required)
    before = signature(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = signature(path)
    if before != after:
        raise PackagingError(f"File changed while hashing: {relative}")
    return Snapshot(path, PurePosixPath(relative.replace("\\", "/")).as_posix(), digest.hexdigest(), after[0], after)


def note_allowed(relative: PurePosixPath) -> bool:
    tail = relative.parts[1:]
    if tail == ("build", "report.json"):
        return True
    if len(tail) != 1:
        return False
    name = tail[0].casefold()
    if name in NOTE_NAMES:
        return True
    if Path(name).suffix not in {".json", ".md"}:
        return False
    return bool(re.match(r"^(?:references?|citations?|recent[_-].*[_-]refs)[_.\s-]", name)
                or name.startswith(("文献", "引用", "数值检验", "计算验证", "方法台账")))


def support_allowed(relative: PurePosixPath) -> bool:
    if any(hidden_or_private(part) or obsolete(part) or part.casefold() in SKIP_DIRS for part in relative.parts):
        return False
    name = relative.name.casefold()
    if any(suffix in SKIP_SUFFIXES for suffix in Path(name).suffixes) or name.endswith(".synctex.gz"):
        return False
    if relative.suffix.casefold() not in SUPPORT_SUFFIXES:
        return False
    if relative.parts[0] == "notes":
        return note_allowed(relative)
    if relative.parts[0] == "data" and relative.suffix.casefold() in {".tex", ".md"}:
        return False
    return True


def main_source(project: Path, config: dict) -> Path | None:
    manuscript = config.get("manuscript") if isinstance(config.get("manuscript"), dict) else {}
    template = config.get("template") if isinstance(config.get("template"), dict) else {}
    for name in (manuscript.get("main"), template.get("entry"), "main.tex"):
        if not isinstance(name, str) or not name:
            continue
        if name == "main.tex" and not (project / name).exists():
            continue
        path = secure_file(project, name)
        if path.suffix.casefold() != ".tex":
            raise PackagingError(f"The active manuscript must be a .tex file: {name}")
        return path
    return None


def local_dependency(project: Path, name: str, bases: tuple[Path, ...], extensions: tuple[str, ...]) -> Path | None:
    name = name.strip()
    if not name or any(marker in name for marker in ("#", "{", "}")):
        return None
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise PackagingError(f"An active TeX dependency is outside the project: {name}")
    if re.search(r"\\[A-Za-z@]", name):
        return None
    name = name.replace("\\", "/")
    unsafe = False
    for base in dict.fromkeys(bases):
        for suffix in ("",) if Path(name).suffix else extensions:
            path = base
            for part in PurePosixPath(name + suffix).parts:
                path = path.parent if part == ".." else path / part
                if not path.is_relative_to(project):
                    unsafe = True
                    break
                if is_link(path):
                    raise PackagingError(f"An active TeX dependency uses a symlink: {name}")
            else:
                if path.is_file():
                    return secure_file(project, path.relative_to(project).as_posix())
    if unsafe:
        raise PackagingError(f"An active TeX dependency escapes the project: {name}")
    return None


def active_tex_files(project: Path, config: dict) -> set[Path] | None:
    main = main_source(project, config)
    if main is None:
        return None
    active, pending = set(), [main]
    commands = re.compile(
        r"\\(input|include|documentclass|LoadClass|LoadClassWithOptions|usepackage|RequirePackage|"
        r"bibliography|addbibresource|bibliographystyle|includegraphics|includepdf|lstinputlisting|verbatiminput)"
        r"\*?\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
    while pending:
        path = pending.pop()
        if path in active:
            continue
        active.add(path)
        if path.suffix.casefold() not in {".tex", ".cls", ".sty", ".cfg", ".bst"}:
            continue
        text = re.sub(r"(?<!\\)%[^\n]*", "", path.read_text(encoding="utf-8", errors="replace"))
        graphics_bases = []
        for group in re.finditer(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\})+)\s*\}", text):
            for directory in re.findall(r"\{([^{}]*)\}", group.group(1)):
                for base in (main.parent, project):
                    candidate = (base / directory).absolute()
                    if candidate.is_relative_to(project):
                        graphics_bases.append(candidate)
        for match in commands.finditer(text):
            command, argument = match.groups()
            bases = (main.parent, path.parent, project)
            if command in {"documentclass", "LoadClass", "LoadClassWithOptions"}:
                extensions, names = (".cls",), [argument]
            elif command in {"usepackage", "RequirePackage"}:
                extensions, names = (".sty",), argument.split(",")
            elif command in {"bibliography", "addbibresource"}:
                extensions, names = (".bib",), argument.split(",")
            elif command == "bibliographystyle":
                extensions, names = (".bst",), [argument]
            elif command in {"includegraphics", "includepdf"}:
                extensions, names = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"), [argument]
                bases += tuple(graphics_bases)
            elif command in {"input", "include"}:
                extensions, names = (".tex",), [argument]
            else:
                extensions, names = ("",), [argument]
            for name in names:
                dependency = local_dependency(project, name, bases, extensions)
                if dependency is not None:
                    pending.append(dependency)
    return active


def support_files(project: Path, config: dict, output_files: set[Path]) -> dict[str, Path]:
    selected: dict[str, Path] = {}
    active = active_tex_files(project, config)
    if active is not None:
        for path in active:
            rel = path.relative_to(project).as_posix()
            if not support_allowed(PurePosixPath(rel)):
                raise PackagingError(f"Active TeX dependency is excluded from a clean archive: {rel}")
            if path.resolve() not in output_files:
                selected[rel] = secure_file(project, rel)
    for name in ROOT_FILES:
        path = project / name
        if path.exists() and path.is_file() and path.stat().st_size and path.resolve() not in output_files:
            if path.suffix.casefold() == ".tex" and active is not None and path.resolve() not in active:
                continue
            selected[name] = secure_file(project, name)
    for pattern in ("*.cls", "*.sty", "*.bst", "*.bbx", "*.cbx", "*.lbx", "*.dbx"):
        for path in sorted(project.glob(pattern)):
            if support_allowed(PurePosixPath(path.name)):
                selected[path.name] = secure_file(project, path.name)
    for directory in SUPPORT_DIRS:
        root = project / directory
        if not root.exists():
            continue
        if is_link(root):
            raise PackagingError(f"Support directory cannot be a symlink: {directory}")
        pending = [root]
        while pending:
            parent = pending.pop()
            for path in sorted(parent.iterdir()):
                rel = PurePosixPath(path.relative_to(project).as_posix())
                if hidden_or_private(path.name) or obsolete(path.name) or path.name.casefold() in SKIP_DIRS:
                    continue
                if is_link(path):
                    raise PackagingError(f"Symlink or junction in a support directory: {rel}")
                if path.is_dir():
                    pending.append(path)
                    continue
                if not path.is_file() or not path.stat().st_size or not support_allowed(rel):
                    continue
                if path.resolve() in output_files:
                    continue
                if directory in {"sections", "tables"} and path.suffix.casefold() == ".tex" and active is not None and path.resolve() not in active:
                    continue
                selected[rel.as_posix()] = secure_file(project, rel.as_posix())
    return selected


def manifest_entries(mapping: dict[str, str], snapshots: dict[str, Snapshot]) -> list[dict]:
    return [{"path": archive_name, "source_rel": source_rel,
             "sha256": snapshots[source_rel].sha256, "size_bytes": snapshots[source_rel].size_bytes}
            for archive_name, source_rel in sorted(mapping.items())]


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_zip(target: Path, mapping: dict[str, str], snapshots: dict[str, Snapshot], manifest: dict,
              timestamp: datetime) -> None:
    zip_time = timestamp.utctimetuple()[:6]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for archive_name, relative in sorted(mapping.items()):
            source = snapshots[relative]
            if signature(source.path) != source.version or is_link(source.path):
                raise PackagingError(f"File changed before archiving: {relative}")
            info = zipfile.ZipInfo(archive_name, date_time=zip_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            digest = hashlib.sha256()
            with source.path.open("rb") as stream, archive.open(info, "w", force_zip64=True) as destination:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    destination.write(block)
            if digest.hexdigest() != source.sha256 or signature(source.path) != source.version:
                raise PackagingError(f"File changed while archiving: {relative}")
        info = zipfile.ZipInfo("MANIFEST.json", date_time=zip_time)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, json_bytes(manifest))


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def commit_outputs(staged: dict[Path, Path]) -> None:
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for final, temporary in staged.items():
            if final.exists():
                if is_link(final) or not final.is_file():
                    raise PackagingError(f"Output is not an ordinary file: {final.name}")
                handle = tempfile.NamedTemporaryFile(prefix=".zpb-backup-", dir=final.parent, delete=False)
                backup = Path(handle.name)
                handle.close()
                shutil.copy2(final, backup)
                backups[final] = backup
            temporary.replace(final)
            published.append(final)
    except BaseException:
        for final in reversed(published):
            if final in backups:
                backups[final].replace(final)
            else:
                final.unlink(missing_ok=True)
        raise
    finally:
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def package_project(project: Path, output: Path | None = None) -> dict:
    project = project.expanduser().resolve(strict=True)
    if not project.is_dir():
        raise PackagingError("--project must be a directory.")
    output = (output.expanduser() if output is not None else project / "delivery").resolve()
    finals = {name: output / name for name in OUTPUT_NAMES}
    output_files = {path.resolve() for path in finals.values()}
    configuration = capture(project, "project.json")
    config = json.loads(configuration.path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise PackagingError("project.json must contain a JSON object.")
    required = config.get("required_deliverables")
    if not isinstance(required, list) or not required or any(not isinstance(item, str) for item in required):
        raise PackagingError("Fill project.json required_deliverables with the final files required by the problem before packaging.")
    core_mapping: dict[str, str] = {}
    snapshots: dict[str, Snapshot] = {"project.json": configuration}
    seen_names, seen_paths = set(), set()
    for name in required:
        rel = relative_path(name).as_posix()
        source = secure_file(project, rel)
        if source in output_files:
            raise PackagingError(f"A package cannot contain its own output: {rel}")
        basename = source.name
        if rel.casefold() in seen_paths or basename.casefold() in seen_names or basename.casefold() == "manifest.json":
            raise PackagingError(f"Duplicate or reserved deliverable basename: {basename}")
        seen_paths.add(rel.casefold())
        seen_names.add(basename.casefold())
        core_mapping[basename] = rel
        if rel not in snapshots:
            snapshots[rel] = capture(project, rel)
    support = support_files(project, config, output_files)
    repro_mapping = {relative: relative for relative in support}
    for relative in core_mapping.values():
        repro_mapping[relative] = relative
    for relative in repro_mapping.values():
        if relative not in snapshots:
            snapshots[relative] = capture(project, relative)

    timestamp = datetime.now(timezone.utc)
    common = {"schema_version": 1, "created_utc": timestamp.isoformat(),
              "project_title": config.get("title", ""), "hash_algorithm": "sha256"}
    core_manifest = {**common, "package_type": "submission", "files": manifest_entries(core_mapping, snapshots)}
    repro_manifest = {**common, "package_type": "reproducibility", "files": manifest_entries(repro_mapping, snapshots)}
    output.mkdir(parents=True, exist_ok=True)
    staged: dict[Path, Path] = {}
    try:
        for name in OUTPUT_NAMES:
            handle = tempfile.NamedTemporaryFile(prefix=".zpb-stage-", suffix=".tmp", dir=output, delete=False)
            temporary = Path(handle.name)
            handle.close()
            staged[finals[name]] = temporary
        write_zip(staged[finals["submission.zip"]], core_mapping, snapshots, core_manifest, timestamp)
        write_zip(staged[finals["reproducibility.zip"]], repro_mapping, snapshots, repro_manifest, timestamp)
        for relative, original in snapshots.items():
            current = capture(project, relative)
            if current.sha256 != original.sha256 or current.version != original.version:
                raise PackagingError(f"Source changed during packaging; retry after computation stops: {relative}")
        report = {**common, "status": "passed", "submission": {
            "archive": "submission.zip", "sha256": file_digest(staged[finals["submission.zip"]]),
            "size_bytes": staged[finals["submission.zip"]].stat().st_size, "files": core_manifest["files"]},
                  "reproducibility": {
            "archive": "reproducibility.zip", "sha256": file_digest(staged[finals["reproducibility.zip"]]),
            "size_bytes": staged[finals["reproducibility.zip"]].stat().st_size, "files": repro_manifest["files"]}}
        staged[finals["submission-manifest.json"]].write_bytes(json_bytes(report))
        commit_outputs(staged)
        return report
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Default: PROJECT/delivery")
    args = parser.parse_args(argv)
    try:
        report = package_project(args.project, args.output)
    except (PackagingError, OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Packaging failed: {error}\n")
    destination = (args.output or args.project / "delivery").expanduser().resolve()
    print(f"Submission: {destination / 'submission.zip'} ({len(report['submission']['files'])} files)")
    print(f"Reproducibility: {destination / 'reproducibility.zip'} ({len(report['reproducibility']['files'])} files)")
    print(f"Manifest: {destination / 'submission-manifest.json'}")
    return 0


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    sys.exit(main())
