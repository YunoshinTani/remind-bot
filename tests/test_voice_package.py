from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from deployment.build_voice import build, VOICE_PACKAGES


class VoicePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "requirements.lock").write_text(
            "\n".join(f"{name}==1.0" for name in VOICE_PACKAGES) + "\ndiscord.py==2.7.1\n", encoding="utf-8")
        checker = self.root / "deployment/check_voice.py"
        checker.parent.mkdir()
        checker.write_text("# import-only check\n", encoding="utf-8")
        self.cache = self.root / ".cache/centos7-voice-wheels"
        self.cache.mkdir(parents=True)
        for name in VOICE_PACKAGES:
            with ZipFile(self.cache / f"{name}-1.0-py3-none-any.whl", "w") as wheel:
                wheel.writestr(f"{name}/__init__.py", "# fixture library\n")
        (self.root / ".env").write_text("private token", encoding="utf-8")
        (self.root / "roster.csv").write_text("private roster", encoding="utf-8")

    def test_voice_archive_contains_only_libraries_checker_and_verified_manifest(self):
        output = self.root / "dist/voice.tar.gz"
        with patch("deployment.build_voice.subprocess.run"), redirect_stdout(io.StringIO()):
            build(self.root, output)
        with tarfile.open(output) as archive:
            self.assertEqual(set(archive.getnames()), {
                *(f"vendor/{name}/__init__.py" for name in VOICE_PACKAGES),
                "check_voice.py", "voice-manifest.json",
            })
            stream = archive.extractfile("voice-manifest.json")
            assert stream is not None
            manifest = json.load(stream)
            self.assertFalse(manifest["contains_private_configuration"])
            for name, expected in manifest["files"].items():
                stream = archive.extractfile(name)
                assert stream is not None
                self.assertEqual(hashlib.sha256(stream.read()).hexdigest(), expected)
            self.assertTrue(all(member.uid == 0 and member.gid == 0 for member in archive.getmembers()))

    def test_failed_download_does_not_replace_a_previous_package(self):
        output = self.root / "voice.tar.gz"
        output.write_bytes(b"previous package")
        with patch("deployment.build_voice.subprocess.run", side_effect=subprocess.CalledProcessError(1, "pip")):
            with self.assertRaises(subprocess.CalledProcessError):
                build(self.root, output)
        self.assertEqual(output.read_bytes(), b"previous package")
