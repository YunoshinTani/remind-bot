"""差分更新の検証・バックアップ・適用。学校サーバーの同梱Pythonで実行する。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.layout import RETIRED_FILES, is_update_path, safe_relative


@dataclass(frozen=True)
class UpdatePlan:
    files: tuple[str, ...]
    remove: tuple[str, ...]


def validate_update(root: Path, payload: Path) -> UpdatePlan:
    manifest = json.loads((payload / "update-manifest.json").read_text(encoding="utf-8"))
    paths = []
    for entry in manifest["files"]:
        relative = Path(*safe_relative(entry["path"]).parts)
        if not is_update_path(entry["path"]) or str(relative) in paths:
            raise ValueError("更新対象外または重複したファイルがあります。")
        source, target = payload / relative, root / relative
        source.resolve().relative_to(payload.resolve())
        target.resolve().relative_to(root.resolve())
        if source.is_symlink() or target.is_symlink():
            raise ValueError("更新対象がシンボリックリンクです。")
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"転送したファイルのハッシュが一致しません: {relative}")
        if relative.suffix == ".py":
            compile(content, str(relative), "exec")
        paths.append(str(relative))
    if not paths:
        raise ValueError("更新するファイルがありません。")
    remove = []
    for value in manifest.get("remove", []):
        relative = Path(*safe_relative(value).parts)
        if value not in RETIRED_FILES or str(relative) in paths or str(relative) in remove:
            raise ValueError("廃止対象外または重複した削除パスがあります。")
        target = root / relative
        target.resolve().relative_to(root.resolve())
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("廃止対象は通常ファイルに限定しています。")
        remove.append(str(relative))
    return UpdatePlan(tuple(paths), tuple(remove))


def replace_file(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(source.read_bytes())
    try:
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def run_bot(root: Path, command: str, *, check: bool = False) -> int:
    result = subprocess.run([str(root / "bot.sh"), command], cwd=root, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    output = (result.stdout + result.stderr).strip()
    if command == "unit-tests" and result.returncode == 0:
        print("\n".join(output.splitlines()[-4:]), flush=True)
    elif output:
        if command == "unit-tests":
            print("以下は一時データとモックを使用した自動テストの出力です。", flush=True)
        print(output, flush=True)
    if check and result.returncode:
        raise RuntimeError(f"{command} が失敗しました。上の出力を確認してください。")
    return result.returncode


def wait_for_connection(root: Path, offset: int):
    log = root / "data/bot.log"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if log.exists():
            with log.open("rb") as stream:
                stream.seek(offset)
                output = stream.read().decode("utf-8", "replace")
            if "Discord に接続しました" in output:
                print("Discord connection: OK", flush=True)
                return
        time.sleep(1)
    raise RuntimeError("30秒以内に今回の起動のDiscord接続を確認できませんでした。data/bot.logを確認してください。")


def validate_server_config(root: Path):
    # 検証だけを行い、サーバー側の設定と名簿を書き換えない。
    sys.path.insert(0, str(root / "vendor"))
    from remind_bot.config import load_config
    config = load_config(root / "config.toml", live=True)
    print(f"CONFIG OK: server settings / start_date={config.start_date}, first_pair={config.first_pair}", flush=True)


def apply_update(root: Path, payload: Path) -> Path:
    plan = validate_update(root, payload)
    paths = plan.files + plan.remove
    status = run_bot(root, "status")
    if status not in {0, 1}:
        raise RuntimeError("起動状態を確認できないため更新を中止しました。")
    was_running = status == 0
    if was_running:
        run_bot(root, "stop", check=True)
    backup = root / "backups" / ("update-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    originals: dict[str, Path | None] = {}
    try:
        for relative in paths:
            target = root / relative
            if target.exists():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
                originals[relative] = saved
            else:
                originals[relative] = None
        for relative in plan.files:
            replace_file(payload / relative, root / relative)
        for relative in plan.remove:
            (root / relative).unlink(missing_ok=True)
        validate_server_config(root)
        run_bot(root, "unit-tests", check=True)
        log = root / "data/bot.log"
        offset = log.stat().st_size if log.exists() else 0
        run_bot(root, "start", check=True)
        wait_for_connection(root, offset)
        run_bot(root, "status", check=True)
    except Exception:
        print("Update failed; restoring previous program files.", flush=True)
        # 停止を確認できない場合は、稼働中のファイルをさらに書き換えない。
        run_bot(root, "stop", check=True)
        for relative, saved in originals.items():
            if saved is None:
                (root / relative).unlink(missing_ok=True)
            else:
                replace_file(saved, root / relative)
        if was_running:
            run_bot(root, "start", check=True)
        raise
    print(f"Program backup: {backup}", flush=True)
    print("UPDATE OK", flush=True)
    return backup


def main():
    if sys.platform == "win32":
        raise RuntimeError("更新の適用は学校サーバーのLinuxで実行してください。")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if root != (Path.home() / "remind-bot-centos7").resolve() or not (root / "bot.sh").is_file():
        raise ValueError("更新先は自分のホーム内の既存Botに限定しています。")
    payload = Path(__file__).resolve().parents[1]
    payload.relative_to(root / "updates")
    os.umask(0o077)
    import fcntl
    with (root / "data/update.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        apply_update(root, payload)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"UPDATE FAILED: {exc}", flush=True)
        raise SystemExit(1)
