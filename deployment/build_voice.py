"""既存CentOS 7同梱版へ追加する音声用ライブラリだけをまとめる。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment.build_portable import digest, extract_wheel

VOICE_PACKAGES = {"pynacl", "davey", "cffi", "pycparser"}


def build(root: Path, output: Path) -> None:
    expected = {}
    for line in (root / "requirements.lock").read_text(encoding="utf-8-sig").splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==")
        if name.lower() in VOICE_PACKAGES:
            expected[name.lower()] = version
    if set(expected) != VOICE_PACKAGES:
        raise ValueError("requirements.lockに音声用ライブラリ4件の固定バージョンが必要です。")
    cache = root / ".cache/centos7-voice-wheels"
    cache.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, "-X", "utf8", "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
        "--only-binary=:all:", "--no-deps", "--platform", "manylinux2014_x86_64",
        "--platform", "manylinux_2_17_x86_64", "--implementation", "cp", "--python-version", "3.11",
        "--abi", "cp311", "--dest", str(cache),
        *(f"{name}=={version}" for name, version in sorted(expected.items())),
    ], check=True)
    selected = []
    for wheel in sorted(cache.glob("*.whl")):
        name, version = wheel.name.split("-", 2)[:2]
        if expected.get(name.lower()) == version:
            selected.append(wheel)
    if len(selected) != len(expected) or len({path.name.split('-')[0].lower() for path in selected}) != len(expected):
        raise ValueError("必要なwheelが不足または重複しています。専用キャッシュを確認してください。")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    with tempfile.TemporaryDirectory(prefix="voice-stage-") as temporary:
        stage = Path(temporary)
        for wheel in selected:
            extract_wheel(wheel, stage / "vendor")
        shutil.copy2(root / "deployment/check_voice.py", stage / "check_voice.py")
        manifest = {
            "python": "CPython 3.11", "platform": "Linux x86_64 / glibc >= 2.17",
            "packages": expected, "wheels": {wheel.name: digest(wheel) for wheel in selected},
            "files": {path.relative_to(stage).as_posix(): digest(path) for path in sorted(stage.rglob("*")) if path.is_file()},
            "contains_private_configuration": False,
        }
        (stage / "voice-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with tarfile.open(partial, "w:gz") as archive:
            for path in sorted(stage.rglob("*")):
                if not path.is_file():
                    continue
                info = archive.gettarinfo(str(path), path.relative_to(stage).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o600
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
    partial.replace(output)
    checksum = digest(output)
    output.with_name(output.name + ".sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(f"VOICE PACKAGE OK: {output} ({output.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"SHA256: {checksum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/voice-libs-centos7.tar.gz")
    args = parser.parse_args()
    build(ROOT, args.output)


if __name__ == "__main__":
    main()
