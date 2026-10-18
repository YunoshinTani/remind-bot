from __future__ import annotations

from pathlib import Path
from contextlib import redirect_stderr
import io
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from deployment.build_portable import extract_wheel, main, runtime_member, safe_relative
from deployment.control import same_process


class PortableTests(unittest.TestCase):
    def test_local_runtime_cannot_be_included_in_a_portable_bundle(self):
        with patch.object(sys, "argv", ["build_portable.py", "--include-local"]), redirect_stderr(io.StringIO()), \
                patch("deployment.build_portable.urllib.request.urlopen") as download:
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)
        download.assert_not_called()

    def test_runtime_keeps_relative_symlinks_and_rejects_escaping_links(self):
        member = tarfile.TarInfo("python/bin/python3")
        member.type = tarfile.SYMTYPE
        member.linkname = "python3.11"
        result = runtime_member(member, "bundle")
        self.assertEqual(result.name, "bundle/python/bin/python3")
        self.assertEqual(result.linkname, "python3.11")
        member.linkname = "../../../other"
        with self.assertRaises(ValueError):
            runtime_member(member, "bundle")
        for name in ("/etc/passwd", "python/../../other", "C:/other", "python\\other"):
            with self.assertRaises(ValueError):
                safe_relative(name)

    def test_wheel_paths_cannot_escape_vendor_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel = root / "sample.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("../../outside", "invalid")
            with self.assertRaises(ValueError):
                extract_wheel(wheel, root / "vendor")
            self.assertFalse((root / "outside").exists())

    def test_stop_identity_requires_start_time_boot_and_bot_command(self):
        record = {"pid": 12345, "start_ticks": "999", "boot_id": "boot", "python": str(Path(sys.executable).resolve())}
        actual = {"pid": 12345, "start_ticks": "999", "boot_id": "boot", "argv": [sys.executable, "-s", "-m", "remind_bot", "run"]}
        self.assertTrue(same_process(record, actual))
        for key, value in (("start_ticks", "1000"), ("boot_id", "other"), ("pid", 54321), ("argv", [sys.executable, "other.py"])):
            self.assertFalse(same_process(record, {**actual, key: value}))
        self.assertFalse(same_process(record, None))
