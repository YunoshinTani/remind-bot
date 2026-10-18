"""ソース・文書・テストの更新をまとめる。本番の運用ファイルは含めない。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment.layout import RETIRED_FILES, update_files


def build(output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with tarfile.open(output, "w:gz") as archive:
        for relative in update_files(ROOT):
            content = (ROOT / relative).read_bytes()
            info = tarfile.TarInfo(relative)
            info.size, info.mode = len(content), 0o600
            info.mtime = int((ROOT / relative).stat().st_mtime)
            archive.addfile(info, io.BytesIO(content))
            entries.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
        manifest = json.dumps({"format": 1, "created_at": datetime.now().isoformat(), "files": entries,
                               "remove": [name for name in RETIRED_FILES if not (ROOT / name).exists()]},
                              ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("update-manifest.json")
        info.size, info.mode = len(manifest), 0o600
        info.mtime = int(datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(manifest))
    print(f"Update files: {len(entries)} / {output.stat().st_size:,} bytes")
    print("Server settings, roster, credentials and diary records: preserved (not included)")


def check_tests() -> bool:
    print("Checking tests on this PC before upload...", flush=True)
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    output = result.stdout.strip()
    print("\n".join(output.splitlines()[-4:]) if result.returncode == 0 else output, flush=True)
    return result.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-tests", action="store_true", help="転送用ファイルの作成前にPCで自動テストを実行")
    args = parser.parse_args(argv)
    if args.check_tests and not check_tests():
        print("Local tests failed. Nothing was uploaded; the server bot was not changed.", flush=True)
        raise SystemExit(1)
    build(args.output)


if __name__ == "__main__":
    main()
