"""Git履歴・接続設定・運用データを含めず、新規公開用のソースZIPを作る。"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment.layout import public_files


def build(root: Path, output: Path) -> tuple[str, ...]:
    files = public_files(root)
    if output.exists():
        raise ValueError("既存の公開用ZIPは上書きしません。出力名を変更してください。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(root / name, f"remind-bot/{name}")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    output = args.output or ROOT / "dist" / f"remind-bot-public-{config['project']['version']}.zip"
    files = build(ROOT, output)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"PUBLIC SOURCE OK: {len(files)} files / {output}")
    print(f"SHA256: {checksum}")
    print("Git履歴・本番ファイル・個人用接続設定は含みません。展開先で新しいリポジトリを作成してください。")


if __name__ == "__main__":
    main()
