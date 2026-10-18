"""Windowsからも作成できるCentOS 7用のオフライン配布パッケージ。"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment.layout import safe_relative, source_files


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_member(member: tarfile.TarInfo, prefix: str) -> tarfile.TarInfo:
    path = safe_relative(member.name)
    if not path.parts or path.parts[0] != "python":
        raise ValueError("Python配布物のディレクトリ構成が不正です。")
    result = copy.copy(member)
    result.name = f"{prefix}/{path}"
    result.uid = result.gid = 0
    result.uname = result.gname = ""
    if member.issym():
        if PurePosixPath(member.linkname).is_absolute() or "\\" in member.linkname:
            raise ValueError("Python配布物に絶対パスのリンクがあります。")
        target = PurePosixPath(posixpath.normpath(posixpath.join(str(path.parent), member.linkname)))
        if not target.parts or target.parts[0] != "python":
            raise ValueError("Python配布物のリンクがフォルダ外を指しています。")
    elif member.islnk():
        target = safe_relative(member.linkname)
        if not target.parts or target.parts[0] != "python":
            raise ValueError("Python配布物のハードリンクが不正です。")
        result.linkname = f"{prefix}/{target}"
    elif not (member.isfile() or member.isdir()):
        raise ValueError("Python配布物に通常ファイル以外の項目があります。")
    return result


def extract_wheel(wheel: Path, target: Path):
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            path = safe_relative(member.filename)
            if not path.parts:
                continue
            if path.parts[0].endswith(".data"):
                if len(path.parts) < 3 or path.parts[1] not in {"purelib", "platlib"}:
                    continue
                path = PurePosixPath(*path.parts[2:])
            destination = target.joinpath(*path.parts)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)


def inside_project(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("設定ファイルの参照先が作業フォルダ外です。配布用に相対パスへ変更してください。")
    return resolved.relative_to(ROOT)


def copy_file(source: Path, stage: Path):
    relative = inside_project(source)
    destination = stage / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    runtime = json.loads((ROOT / "deployment/runtime.json").read_text(encoding="utf-8"))
    cache = ROOT / ".cache/centos7-build"
    wheels = ROOT / ".cache/centos7-wheel-check"
    cache.mkdir(parents=True, exist_ok=True)
    wheels.mkdir(parents=True, exist_ok=True)
    python_archive = cache / runtime["filename"]
    if not python_archive.exists():
        request = urllib.request.Request(runtime["url"], headers={"User-Agent": "class-diary-remind-bot-packager"})
        with urllib.request.urlopen(request, timeout=60) as source, python_archive.open("wb") as output:
            shutil.copyfileobj(source, output)
    if digest(python_archive) != runtime["sha256"]:
        raise ValueError("Python配布物のSHA-256が一致しません。")
    subprocess.run([
        sys.executable, "-X", "utf8", "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
        "-r", str(ROOT / "requirements.lock"), "-r", str(ROOT / "deployment/requirements-extra.lock"),
        "--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_17_x86_64",
        "--implementation", "cp", "--python-version", "3.11", "--abi", "cp311",
        "--only-binary=:all:", "--dest", str(wheels),
    ], check=True)
    # このビルド専用のロックファイルで、キャッシュ内の余分なwheel混入を避ける。
    expected = {}
    for lock in (ROOT / "requirements.lock", ROOT / "deployment/requirements-extra.lock"):
        for line in lock.read_text(encoding="utf-8-sig").splitlines():
            if line and not line.startswith("#"):
                name, version = line.split("==")
                expected[name.lower().replace("-", "_").replace(".", "_")] = version
    selected = []
    for wheel in sorted(wheels.glob("*.whl")):
        name, version = wheel.name.split("-", 2)[:2]
        if expected.get(name.lower()) == version:
            selected.append(wheel)
    if len(selected) != len(expected):
        raise ValueError("必要なwheelの数が一致しません。キャッシュを確認してください。")
    output_dir = ROOT / "dist"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / f"remind-bot-centos7-{now:%Y%m%d-%H%M%S}.tar.gz"
    partial_output = output.with_name(output.name + ".partial")
    # Windowsの共有ドライブで索引作成等が一時的にファイルを掴んでも、
    # 完成した配布物をクリーンアップの失敗だけで失敗扱いにしない。
    with tempfile.TemporaryDirectory(prefix="stage-", dir=cache, ignore_cleanup_errors=True) as temporary:
        stage = Path(temporary).resolve()
        # 自動クリーンアップする対象が、意図したビルド用キャッシュの直下にあることを確認。
        if stage.parent != cache.resolve():
            raise ValueError("ビルド作業フォルダの場所が不正です。")
        for relative in source_files(ROOT):
            copy_file(ROOT / relative, stage)
        for name in ("deployment/runtime.json", "deployment/requirements-extra.lock"):
            copy_file(ROOT / name, stage)
        # ランチャーは配布フォルダのルートへ配置。
        (stage / "bot.sh").write_bytes((ROOT / "deployment/bot.sh").read_bytes().replace(b"\r\n", b"\n"))
        vendor = stage / "vendor"
        vendor.mkdir()
        for wheel in selected:
            extract_wheel(wheel, vendor)
        manifest = {
            "created_at": now.isoformat(), "contains_private_configuration": False,
            "database_snapshot": False, "python": runtime,
            "wheels": {wheel.name: digest(wheel) for wheel in selected},
            "files": {str(path.relative_to(stage)).replace("\\", "/"): digest(path) for path in sorted(stage.rglob("*")) if path.is_file()},
            "linux_runtime_tested_here": False,
        }
        (stage / "bundle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        prefix = "remind-bot-centos7"
        with tarfile.open(partial_output, "w:gz", compresslevel=6) as destination:
            root_info = tarfile.TarInfo(prefix)
            root_info.type, root_info.mode = tarfile.DIRTYPE, 0o700
            destination.addfile(root_info)
            with tarfile.open(python_archive, "r:gz") as source:
                for member in source:
                    destination.addfile(runtime_member(member, prefix), source.extractfile(member) if member.isfile() else None)
            for path in sorted(stage.rglob("*")):
                relative = path.relative_to(stage).as_posix()
                info = destination.gettarinfo(str(path), f"{prefix}/{relative}")
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if path.is_dir():
                    info.mode = 0o700
                elif relative == "bot.sh":
                    info.mode = 0o700
                else:
                    info.mode = 0o600
                with path.open("rb") if path.is_file() else open(os.devnull, "rb") as stream:
                    destination.addfile(info, stream if path.is_file() else None)
    checksum = digest(partial_output)
    partial_output.replace(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(f"作成完了: {output}")
    print(f"サイズ: {output.stat().st_size / 1024 / 1024:.1f} MiB")
    print(f"SHA-256: {checksum}")
    print("本番の設定・名簿・トークン・DBは含みません。学校サーバー側で用意してください。")
    print("Linux上での起動は、転送後の ./bot.sh doctor で確認してください。")


if __name__ == "__main__":
    main()
