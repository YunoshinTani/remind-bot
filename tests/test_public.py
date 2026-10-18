from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from deployment.build_public import build
from deployment.layout import public_files


class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.allowed = {
            "README.md", "LICENCE", ".gitignore", ".env.example", "config.example.toml",
            "remind_bot/__main__.py", "docs/PUBLICATION.md", "tests/test_sample.py",
            "examples/roster.example.csv", ".vscode/settings.json",
            "deployment/build_public.py", "deployment/update_school.ps1", "deployment/bot.sh",
            "deployment/runtime.json", "deployment/connection.example.json",
        }
        self.private = {
            ".git/config", ".git/objects/sample", ".env", ".env.production", "config.toml", "roster.csv",
            "calendar-overrides.json", "data/state.sqlite3", "data/bot.log", "backups/private.zip",
            "dist/old-bundle.tar.gz", ".venv/private.txt", ".cache/private.txt", ".vscode/sftp.json",
            "deployment/connection.local.json", "deployment/operations.local.md",
            "deployment/debug.local.py", "docs/operations.local.md",
            "remind_bot/__pycache__/private.py", "docs/.private/private.md",
        }
        for name in self.allowed | self.private:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture for {name}\n", encoding="utf-8")

    def test_public_allowlist_excludes_history_runtime_and_private_files(self):
        self.assertEqual(set(public_files(self.root)), self.allowed)

    def test_export_preserves_source_and_license_without_private_files(self):
        output = self.root / "dist/public.zip"
        self.assertEqual(set(build(self.root, output)), self.allowed)
        with ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {f"remind-bot/{name}" for name in self.allowed})
            for name in self.allowed:
                self.assertEqual(archive.read(f"remind-bot/{name}"), (self.root / name).read_bytes())
            self.assertIsNone(archive.testzip())

    def test_export_does_not_overwrite_an_existing_archive(self):
        output = self.root / "public.zip"
        output.write_bytes(b"existing archive")
        with self.assertRaises(ValueError):
            build(self.root, output)
        self.assertEqual(output.read_bytes(), b"existing archive")

    def test_public_export_rejects_files_reported_as_symlinks(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(ValueError):
                public_files(self.root)
