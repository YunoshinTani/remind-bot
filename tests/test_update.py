from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from deployment.apply_update import apply_update, validate_update
from deployment import build_update
from deployment.layout import source_files, update_files


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "bot"
        self.payload = self.root / "updates/test"
        self.payload.mkdir(parents=True)
        (self.root / "remind_bot").mkdir()
        (self.root / "remind_bot/service.py").write_bytes(b"old = True\n")
        (self.root / "data").mkdir()
        (self.root / "data/state.sqlite3").write_bytes(b"production diary records")
        (self.root / ".env").write_bytes(b"private settings")
        (self.root / "config.toml").write_bytes(self.valid_config())

    def manifest(self, contents):
        entries = []
        for relative, content in contents.items():
            source = self.payload / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(content)
            entries.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
        (self.payload / "update-manifest.json").write_text(json.dumps({"files": entries}), encoding="utf-8")

    def test_validation_rejects_credentials_and_corrupted_files(self):
        self.manifest({".env": b"must not update credentials"})
        with self.assertRaises(ValueError):
            validate_update(self.root, self.payload)
        self.manifest({"remind_bot/service.py": b"new = True\n"})
        (self.payload / "remind_bot/service.py").write_bytes(b"tampered = True\n")
        with self.assertRaises(ValueError):
            validate_update(self.root, self.payload)
        self.assertEqual((self.root / ".env").read_bytes(), b"private settings")
        self.manifest({"remind_bot/service.py": b"new = True\n"})
        manifest = self.payload / "update-manifest.json"
        for protected in ("config.toml", "roster.csv", ".env", "calendar-overrides.json", "data/state.sqlite3"):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["remove"] = [protected]
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_update(self.root, self.payload)

    def test_failed_checks_restore_old_program_and_remove_added_file(self):
        self.manifest({"remind_bot/service.py": b"new = True\n", "tests/new_test.py": b"new_test = True\n"})
        def run(root, command, **kwargs):
            if command == "unit-tests":
                raise RuntimeError("simulated test failure")
            return 0
        with patch("deployment.apply_update.run_bot", side_effect=run) as calls, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "simulated test failure"):
                apply_update(self.root, self.payload)
        self.assertEqual((self.root / "remind_bot/service.py").read_bytes(), b"old = True\n")
        self.assertFalse((self.root / "tests/new_test.py").exists())
        self.assertEqual((self.root / "data/state.sqlite3").read_bytes(), b"production diary records")
        self.assertEqual(calls.call_args.args[1], "start")

    def test_success_backs_up_program_without_copying_or_replacing_live_data(self):
        self.manifest({"remind_bot/service.py": b"new = True\n"})
        with patch("deployment.apply_update.run_bot", return_value=0), patch("deployment.apply_update.wait_for_connection"), redirect_stdout(io.StringIO()):
            backup = apply_update(self.root, self.payload)
        self.assertEqual((backup / "remind_bot/service.py").read_bytes(), b"old = True\n")
        self.assertEqual((self.root / "remind_bot/service.py").read_bytes(), b"new = True\n")
        self.assertEqual((self.root / "data/state.sqlite3").read_bytes(), b"production diary records")
        self.assertEqual((self.root / ".env").read_bytes(), b"private settings")
        self.assertFalse((backup / "data").exists())

    def test_local_test_failure_stops_before_creating_the_update(self):
        output = self.root / "must-not-exist.tar.gz"
        failed = subprocess.CompletedProcess([], 1, stdout="simulated failing test")
        with patch("deployment.build_update.subprocess.run", return_value=failed), patch("deployment.build_update.build") as create, redirect_stdout(io.StringIO()) as messages:
            with self.assertRaises(SystemExit) as error:
                build_update.main(["--output", str(output), "--check-tests"])
        self.assertEqual(error.exception.code, 1)
        create.assert_not_called()
        self.assertFalse(output.exists())
        self.assertIn("Nothing was uploaded", messages.getvalue())

    def test_update_members_keep_timestamps_instead_of_the_1970_default(self):
        output = self.root / "update.tar.gz"
        with patch.object(build_update, "ROOT", self.root), patch.object(build_update, "update_files", return_value=("remind_bot/service.py",)), redirect_stdout(io.StringIO()):
            build_update.build(output)
        with tarfile.open(output) as bundle:
            self.assertEqual(bundle.getmember("remind_bot/service.py").mtime,
                             int((self.root / "remind_bot/service.py").stat().st_mtime))
            self.assertGreater(bundle.getmember("update-manifest.json").mtime, 1_700_000_000)

    def test_new_nested_sources_and_docs_are_discovered_without_private_data(self):
        (self.root / "remind_bot/new_feature").mkdir()
        (self.root / "remind_bot/new_feature/task.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs/GUIDE.md").write_text("guide\n", encoding="utf-8")
        (self.root / "remind_bot/__pycache__").mkdir()
        (self.root / "remind_bot/__pycache__/ignored.py").write_text("cached\n", encoding="utf-8")
        files = source_files(self.root)
        self.assertIn("remind_bot/new_feature/task.py", files)
        self.assertIn("docs/GUIDE.md", files)
        self.assertNotIn(".env", files)
        self.assertNotIn("data/state.sqlite3", files)
        self.assertNotIn("remind_bot/__pycache__/ignored.py", files)

    def migration_manifest(self):
        (self.root / "OPERATIONS.md").write_bytes(b"old guide")
        self.manifest({"docs/OPERATIONS.md": b"new guide"})
        path = self.payload / "update-manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["remove"] = ["OPERATIONS.md"]
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_document_move_is_backed_up_and_preserves_diary_records(self):
        self.migration_manifest()
        with patch("deployment.apply_update.run_bot", return_value=0), patch("deployment.apply_update.wait_for_connection"), redirect_stdout(io.StringIO()):
            backup = apply_update(self.root, self.payload)
        self.assertFalse((self.root / "OPERATIONS.md").exists())
        self.assertEqual((self.root / "docs/OPERATIONS.md").read_bytes(), b"new guide")
        self.assertEqual((backup / "OPERATIONS.md").read_bytes(), b"old guide")
        self.assertEqual((self.root / "data/state.sqlite3").read_bytes(), b"production diary records")

    def test_failed_document_move_restores_the_old_location(self):
        self.migration_manifest()
        def run(root, command, **kwargs):
            if command == "unit-tests":
                raise RuntimeError("simulated failure")
            return 0
        with patch("deployment.apply_update.run_bot", side_effect=run), redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                apply_update(self.root, self.payload)
        self.assertEqual((self.root / "OPERATIONS.md").read_bytes(), b"old guide")
        self.assertFalse((self.root / "docs/OPERATIONS.md").exists())
        self.assertEqual((self.root / ".env").read_bytes(), b"private settings")

    def valid_config(self):
        self.root.joinpath("roster.csv").write_text(
            "number,discord_id,active\n1,1001,true\n2,1002,true\n", encoding="utf-8")
        example = Path(__file__).resolve().parents[1] / "config.example.toml"
        return example.read_text(encoding="utf-8").replace("guild_id = 0", "guild_id = 100").replace("channel_id = 0", "channel_id = 200").encode("utf-8")

    def test_updates_and_source_distribution_exclude_all_server_owned_files(self):
        for protected in ("config.toml", ".env", "roster.csv", "calendar.json", "calendar-overrides.json", "data/state.sqlite3"):
            self.assertNotIn(protected, source_files(self.root))
            self.assertNotIn(protected, update_files(self.root))
            self.manifest({protected: b"protected"})
            with self.assertRaises(ValueError):
                validate_update(self.root, self.payload)

    def test_code_update_preserves_settings_roster_and_live_data(self):
        protected = ("config.toml", "roster.csv", ".env", "data/state.sqlite3")
        previous = {name: (self.root / name).read_bytes() for name in protected}
        self.manifest({"remind_bot/service.py": b"new = True\n"})
        with patch("deployment.apply_update.run_bot", return_value=0), patch("deployment.apply_update.wait_for_connection"), redirect_stdout(io.StringIO()):
            backup = apply_update(self.root, self.payload)
        self.assertEqual((self.root / "remind_bot/service.py").read_bytes(), b"new = True\n")
        for name in protected:
            self.assertEqual((self.root / name).read_bytes(), previous[name])
            self.assertFalse((backup / name).exists())

    def test_invalid_server_settings_fail_validation_and_restore_program(self):
        original = b"valid_toml_but_missing_settings = true\n"
        self.root.joinpath("config.toml").write_bytes(original)
        self.manifest({"remind_bot/service.py": b"new = True\n"})
        with patch("deployment.apply_update.run_bot", return_value=0), redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                apply_update(self.root, self.payload)
        self.assertEqual((self.root / "config.toml").read_bytes(), original)
        self.assertEqual((self.root / "remind_bot/service.py").read_bytes(), b"old = True\n")


if __name__ == "__main__":
    unittest.main()
