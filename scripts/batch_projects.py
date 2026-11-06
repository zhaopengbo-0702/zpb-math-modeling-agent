#!/usr/bin/env python3
"""Manage isolated manuscript projects from a local batch manifest."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


SKILL = "zpb-math-modeling-agent"
SCRIPT_ROOT = Path(__file__).resolve().parent
STAGE_SCRIPTS = {"build": "build_paper.py", "audit": "audit_project.py",
                 "package": "package_submission.py"}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
RESERVED = {"con", "prn", "aux", "nul", "clock$"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def contained(root: Path, path: Path) -> Path:
    result = path.resolve()
    if not result.is_relative_to(root):
        raise ValueError(f"Path leaves its project: {path.name}")
    return result


def source_path(base: Path, name: str) -> Path:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Input and template paths must be nonempty strings.")
    path = Path(name)
    if path.is_absolute() or path.drive or name.startswith(("~", "\\")):
        raise ValueError("Input and template paths must be relative to the manifest.")
    return (base / path).resolve()


def load_plan(manifest: Path, output_root: Path, requested: list[str] | None):
    document = read_json(manifest)
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("Manifest must contain a nonempty jobs array.")
    seen = set()
    for job in jobs:
        identifier = job.get("id") if isinstance(job, dict) else None
        if (not isinstance(identifier, str) or len(identifier) > 64
                or not SLUG.fullmatch(identifier) or identifier in RESERVED):
            raise ValueError("Each job id must be a safe, lowercase slug of at most 64 characters.")
        if identifier in seen:
            raise ValueError(f"Duplicate job id: {identifier}")
        seen.add(identifier)
        destination = contained(output_root, output_root / identifier)
        if destination.parent != output_root or (output_root / identifier).is_symlink():
            raise ValueError(f"Job output cannot be a link or leave the output root: {identifier}")
    selected = set(requested or seen)
    unknown = selected - seen
    if unknown:
        raise ValueError("Unknown --job ids: " + ", ".join(sorted(unknown)))
    if manifest.is_relative_to(output_root):
        raise ValueError("Keep the manifest outside --output-root to separate inputs and outputs.")
    # Validate overlap for every job, including jobs omitted from this stage.
    for job in jobs:
        names = job.get("inputs", [])
        if not isinstance(names, list):
            continue
        for name in names:
            try:
                source = source_path(manifest.parent, name)
            except ValueError:
                continue
            if source.is_relative_to(output_root):
                raise ValueError("--output-root cannot contain an original input file.")
        if job.get("template") is not None:
            try:
                template = source_path(manifest.parent, job["template"])
            except ValueError:
                continue
            if template.is_relative_to(output_root) or output_root.is_relative_to(template):
                raise ValueError("--output-root and original template directories cannot overlap.")
    return jobs, [job for job in jobs if job["id"] in selected]


def identity_for(job: dict, manifest: Path):
    title = job.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Each job needs a nonempty title.")
    names = job.get("inputs")
    if not isinstance(names, list):
        raise ValueError("Each job needs an inputs array.")
    sources, identities, seen = [], [], set()
    for name in names:
        source = source_path(manifest.parent, name)
        if not source.is_file():
            raise ValueError(f"Input file does not exist: {name}")
        if source.name.casefold() in seen:
            raise ValueError("Input basenames must be unique within each job.")
        seen.add(source.name.casefold())
        sources.append(source)
        identities.append({"file": "inputs/" + source.name, "sha256": digest(source),
                           "bytes": source.stat().st_size})
    identities.sort(key=lambda item: item["file"].casefold())
    template = None
    template_files = None
    if job.get("template") is not None:
        template = source_path(manifest.parent, job["template"])
        if not template.is_dir() or not any(template.rglob("*.tex")):
            raise ValueError("Template must be a directory containing a .tex file.")
        files = sorted(template.rglob("*"))
        if any(path.is_symlink() for path in files):
            raise ValueError("Template links are not supported.")
        template_files = {path.relative_to(template).as_posix(): digest(path)
                          for path in files if path.is_file()}
    identity = {"schema_version": 1, "id": job["id"], "title": title,
                "inputs": identities, "template_files": template_files}
    canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
    identity["manifest_job_sha256"] = hashlib.sha256(canonical).hexdigest()
    return identity, sources, template


def existing_project(project: Path, identity: dict) -> dict:
    configuration = contained(project, project / "project.json")
    if not configuration.is_file():
        raise ValueError("Existing nonempty output has no project.json; choose another job id.")
    config = read_json(configuration)
    if not isinstance(config, dict) or config.get("skill") != SKILL:
        raise ValueError("Existing project belongs to a different skill.")
    if config.get("title") != identity["title"]:
        raise ValueError("Job title differs from the existing project; use a new job id.")
    manifest_name = config.get("input_manifest", "notes/input-manifest.json")
    if not isinstance(manifest_name, str):
        raise ValueError("Project input_manifest must be a relative filename.")
    local_manifest = read_json(contained(project, project / manifest_name))
    actual = local_manifest.get("inputs") if isinstance(local_manifest, dict) else None
    if not isinstance(actual, list) or any(not isinstance(item, dict) for item in actual):
        raise ValueError("Existing input manifest is invalid.")
    actual = sorted(actual, key=lambda item: str(item.get("file", "")).casefold())
    if actual != identity["inputs"]:
        raise ValueError("Input manifest or source hashes changed; use a new job id.")
    for item in actual:
        path = contained(project, project / item["file"])
        if not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError("A copied project input changed; restore it or use a new job id.")
    template = config.get("template", {})
    if not isinstance(template, dict):
        raise ValueError("Existing template configuration is invalid.")
    expected = identity["template_files"]
    if expected is None:
        if template.get("mode") != "original-fallback":
            raise ValueError("Template selection differs from the existing project.")
    else:
        if template.get("mode") != "user-provided":
            raise ValueError("Template selection differs from the existing project.")
        baseline = read_json(contained(project, project / template.get("baseline", "notes/template-baseline.json")))
        if baseline != expected:
            raise ValueError("Original template hashes changed; use a new job id.")
        directory = contained(project, project / template.get("directory", "template"))
        for name, expected_hash in expected.items():
            path = contained(directory, directory / name)
            if not path.is_file() or digest(path) != expected_hash:
                raise ValueError("A preserved template file changed; restore it before resuming.")
    marker = contained(project, project / "notes/batch-job.json")
    if marker.exists() and read_json(marker) != identity:
        raise ValueError("Job definition differs from the initialized batch job.")
    return config


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=".zpb-batch-", suffix=".tmp",
                                         dir=path.parent, delete=False)
    temporary = Path(handle.name)
    handle.close()
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def initialize_job(project: Path, identity: dict, sources: list[Path], template: Path | None) -> str:
    if project.exists() and (not project.is_dir() or any(project.iterdir())):
        existing_project(project, identity)
        action = "resumed"
    else:
        spec = importlib.util.spec_from_file_location("zpb_batch_initializer", SCRIPT_ROOT / "init_project.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.initialize(project, sources, identity["title"], template=template)
        # Verify the copied bytes too, including changes during initialization.
        existing_project(project, identity)
        action = "initialized"
    write_json(contained(project, project / "notes/batch-job.json"), identity)
    return action


def run_stage(project: Path, stage: str) -> dict:
    logs = contained(project, project / "notes/batch")
    logs.mkdir(parents=True, exist_ok=True)
    log = contained(project, logs / (stage + ".log"))
    command = [sys.executable, str(SCRIPT_ROOT / STAGE_SCRIPTS[stage]), "--project", str(project)]
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(command, cwd=project, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                               errors="replace", env=environment)
    output, _ = process.communicate()
    log.write_text(output or "", encoding="utf-8")
    result = {"pid": process.pid, "exit_code": process.returncode,
              "log": log.relative_to(project).as_posix(),
              "status": "passed" if process.returncode == 0 else "failed"}
    if process.returncode:
        result["error"] = f"{stage} failed; inspect the job log."
    return result


def run_batch(manifest: Path, output_root: Path, stage: str,
              requested: list[str] | None = None, report_path: Path | None = None) -> dict:
    if stage not in {"initialize", *STAGE_SCRIPTS}:
        raise ValueError("Unknown batch stage.")
    manifest = manifest.expanduser().resolve(strict=True)
    output_root = output_root.expanduser().resolve()
    if output_root.exists() and not output_root.is_dir():
        raise ValueError("--output-root must be a directory.")
    all_jobs, jobs = load_plan(manifest, output_root, requested)
    target = report_path or output_root / "batch-report.json"
    if not target.is_absolute():
        target = output_root / target
    target = contained(output_root, target)
    if target.suffix.lower() != ".json" or target == output_root:
        raise ValueError("Batch report must be a .json file inside --output-root.")
    if any(target.is_relative_to(output_root / job["id"]) for job in all_jobs):
        raise ValueError("Batch report cannot overwrite files inside a job project.")
    report = {"schema_version": 1, "stage": stage,
              "started_utc": datetime.now(timezone.utc).isoformat(), "jobs": []}
    output_root.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        result = {"id": job["id"], "stage": stage, "pid": os.getpid(),
                  "project": job["id"], "status": "failed"}
        try:
            project = contained(output_root, output_root / job["id"])
            identity, sources, template = identity_for(job, manifest)
            if stage == "initialize":
                result.update(status="passed", action=initialize_job(project, identity, sources, template))
            else:
                existing_project(project, identity)
                result.update(run_stage(project, stage))
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
            result["error"] = str(error)
        report["jobs"].append(result)
        report["summary"] = {"total": len(report["jobs"]),
                             "passed": sum(item["status"] == "passed" for item in report["jobs"]),
                             "failed": sum(item["status"] == "failed" for item in report["jobs"])}
        report["status"] = "failed" if report["summary"]["failed"] else "passed"
        write_json(target, report)
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(target, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--stage", choices=("initialize", "build", "audit", "package"), required=True)
    parser.add_argument("--job", action="append", dest="jobs", help="Select a job id; repeat for several jobs.")
    parser.add_argument("--report", type=Path, help="JSON report inside output root; default: batch-report.json")
    args = parser.parse_args(argv)
    try:
        report = run_batch(args.manifest, args.output_root, args.stage, args.jobs, args.report)
    except (OSError, ValueError, TypeError) as error:
        print(f"Batch failed: {error}", file=sys.stderr)
        return 1
    for job in report["jobs"]:
        print(f"{job['id']}: {job['stage']} {job['status']}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    from _console import utf8_console
    utf8_console()
    raise SystemExit(main())
