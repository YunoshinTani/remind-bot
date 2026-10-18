"""ソース配布・通常更新の許可パス。本番の設定・名簿・記録は含めない。"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

CODE_DIRECTORIES = {"remind_bot", "deployment", "tests"}
ROOT_FILES = {
    "README.md", "LICENCE", "pyproject.toml", "requirements.lock", ".env.example", "config.example.toml",
}
PUBLIC_EXTRA_FILES = {
    ".gitignore", ".vscode/settings.json", "deployment/bot.sh", "deployment/update_school.ps1",
    "deployment/runtime.json", "deployment/requirements-extra.lock", "deployment/connection.example.json",
}
RETIRED_FILES = (
    "CENTOS7.md", "OPERATIONS.md", "START_HERE.md", "TEST_RESULTS.md", "DISPLAY_EXAMPLES.md",
    "tests/test_bot.py",
)


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError("配布ファイルに不正な相対パスがあります。")
    return path


def is_source_path(value: str) -> bool:
    path = safe_relative(value)
    if str(path) in ROOT_FILES:
        return True
    if len(path.parts) < 2 or any(
        part.startswith(".") or part == "__pycache__" or ".local." in part for part in path.parts
    ):
        return False
    return (
        (path.parts[0] in CODE_DIRECTORIES and path.suffix == ".py")
        or (path.parts[0] == "docs" and path.suffix == ".md")
        or (path.parts[0] == "examples" and path.suffix in {".toml", ".json", ".csv"})
    )


def source_files(root: Path) -> tuple[str, ...]:
    files = set()
    for directory in sorted(CODE_DIRECTORIES | {"docs", "examples"}):
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and is_source_path(relative):
                files.add(relative)
    files.update(name for name in ROOT_FILES if (root / name).is_file())
    for relative in files:
        path = root / relative
        path.resolve().relative_to(root.resolve())
        if path.is_symlink():
            raise ValueError(f"配布対象にリンクがあります: {relative}")
    return tuple(sorted(files))


def is_update_path(value: str) -> bool:
    return is_source_path(value)


def update_files(root: Path) -> tuple[str, ...]:
    return source_files(root)


def public_files(root: Path) -> tuple[str, ...]:
    """履歴・運用データ・個人用接続設定を含めない公開用ソース。"""
    files = set(source_files(root))
    files.update(name for name in PUBLIC_EXTRA_FILES if (root / name).is_file())
    for name in files:
        path = root / name
        path.resolve().relative_to(root.resolve())
        if path.is_symlink():
            raise ValueError(f"公開対象にリンクがあります: {name}")
    return tuple(sorted(files))
