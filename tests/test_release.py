"""Verify real release archives, content hashes and output-directory boundaries."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zpb_release", ROOT / "scripts/make_release.py")
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class ReleaseTests(unittest.TestCase):
    def test_real_archive_contains_manifest_and_verified_source_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            included = {
                "SKILL.md": "---\nname: zpb-math-modeling-agent\n---\n中文技能\n".encode("utf-8"),
                "LICENSE": b"MIT test license\n",
                "scripts/helper.py": b"print('portable')\n",
                ".gitignore": b"__pycache__/\n",
                ".github/workflows/validate.yml": b"name: Validate\n",
            }
            excluded = {
                ".git/config": b"private checkout metadata",
                "__pycache__/helper.pyc": b"compiled cache",
                ".venv/private.txt": b"environment dependency",
                "demo-output/result.json": b"{}",
                "dist/old-release.zip": b"old release",
            }
            for name, content in {**included, **excluded}.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            output = root / "dist"
            report = RELEASE.release(root, output, validate=False)
            target = Path(report["archive"])
            prefix = RELEASE.SLUG + "/"
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(set(archive.namelist()),
                                 {prefix + name for name in included} | {prefix + "RELEASE-MANIFEST.json"})
                manifest = json.loads(archive.read(prefix + "RELEASE-MANIFEST.json"))
                self.assertEqual({item["file"] for item in manifest["files"]}, set(included))
                for item in manifest["files"]:
                    payload = archive.read(prefix + item["file"])
                    self.assertEqual(payload, included[item["file"]])
                    self.assertEqual(item["bytes"], len(payload))
                    self.assertEqual(item["sha256"], hashlib.sha256(payload).hexdigest())
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(report["sha256"], actual)
            self.assertEqual(report["source_files"], len(included))
            self.assertEqual((output / (target.name + ".sha256")).read_text().strip(),
                             actual + "  " + target.name)
            self.assertEqual(json.loads((output / "release-report.json").read_text())["sha256"], actual)

    def test_source_or_ancestor_output_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "source"
            root.mkdir()
            (root / "SKILL.md").write_text("skill", encoding="utf-8")
            sentinel = base / "keep.txt"
            sentinel.write_text("unchanged")
            for output in (root, base):
                with self.subTest(output=output.name), self.assertRaisesRegex(ValueError, "ancestors"):
                    RELEASE.release(root, output, validate=False)
            self.assertEqual(sentinel.read_text(), "unchanged")
            self.assertFalse(list(base.glob("*.zip")))
            self.assertFalse(list(root.glob("*.zip")))

    def test_missing_entrypoint_cannot_produce_empty_success_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "source"
            root.mkdir()
            (root / "README.md").write_text("incomplete source", encoding="utf-8")
            output = base / "releases"
            with self.assertRaisesRegex(ValueError, "SKILL.md"):
                RELEASE.release(root, output, validate=False)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
