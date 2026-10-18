from __future__ import annotations

import unicodedata
import csv
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .config import ConfigError, Pair, load_pairs


@dataclass(frozen=True)
class Resolution:
    pairs: tuple[Pair, ...]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class SavedRoster:
    changed: int
    backup: Path | None


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def resolve_pairs(pairs: tuple[Pair, ...], members: Iterable) -> Resolution:
    """番号と氏名の完全一致でIDを解決する。部分一致や番号だけの推測はしない。"""
    members = [member for member in members if not member.bot]
    by_id = {member.id: member for member in members}
    by_alias: dict[str, dict] = {}
    for member in members:
        for value in (member.name, member.nick, member.global_name):
            if value:
                by_alias.setdefault(normalized(value), {})[member.id] = member
    pairs_out, problems, used = [], [], set()
    for pair in pairs:
        students = []
        for student in pair.students:
            if student.discord_id is not None:
                candidates = {student.discord_id: by_id[student.discord_id]} if student.discord_id in by_id else {}
            else:
                aliases = {student.discord_name} if student.discord_name else set()
                if student.name:
                    aliases.update({f"{student.number:02d}-{student.name}", f"{student.number}-{student.name}"})
                candidates = {}
                for alias in aliases:
                    candidates.update(by_alias.get(normalized(alias), {}))
            if len(candidates) != 1:
                problems.append(f"{student.number:02d}番: " + ("一致するメンバーがいません" if not candidates else "複数人に一致します"))
                students.append(replace(student, discord_id=None))
                continue
            member_id = next(iter(candidates))
            if member_id in used:
                # 同じアカウントを複数の出席番号に割り当てない。
                problems.append(f"{student.number:02d}番: 別の出席番号とIDが重複しています")
                students.append(replace(student, discord_id=None))
                continue
            used.add(member_id)
            students.append(replace(student, discord_id=member_id))
        pairs_out.append(replace(pair, students=tuple(students)))
    return Resolution(tuple(pairs_out), tuple(problems))


def save_member_matches(path: Path, resolution: Resolution, members: Iterable, *, backup_dir: Path, now: datetime) -> SavedRoster:
    """確認済みのIDと表示名を保存し、未解決の行・氏名・在籍状態は保持する。"""
    path = path.resolve()
    original = path.read_bytes()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not {"number", "discord_id", "active"}.issubset(fields):
        raise ConfigError("名簿 CSV には number,discord_id,active が必要です。")
    if "discord_name" not in fields:
        fields.append("discord_name")
    resolved = {student.number: student for pair in resolution.pairs for student in pair.students if student.discord_id is not None}
    by_id = {member.id: member for member in members if not member.bot}
    changed = 0
    for row in rows:
        student = resolved.get(int(row["number"]))
        if student is None:
            continue
        if row["active"].strip().lower() != "true" or row.get("name", "").strip() != student.name:
            raise ConfigError("照合中に名簿が変更されました。もう一度同期してください。")
        member = by_id.get(student.discord_id)
        if member is None:
            raise ConfigError("保存対象のメンバー情報がありません。もう一度同期してください。")
        current_name = member.nick or member.global_name or member.name
        if row["discord_id"] != str(member.id) or row.get("discord_name", "") != current_name:
            row["discord_id"] = str(member.id)
            row["discord_name"] = current_name
            changed += 1
    if not changed:
        return SavedRoster(0, None)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, prefix=".roster-sync-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        load_pairs(temporary, require_ids=False)
        if path.read_bytes() != original:
            raise ConfigError("保存前に名簿が変更されました。もう一度同期してください。")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"roster-{now:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.csv"
        backup.write_bytes(original)
        temporary.replace(path)
        return SavedRoster(changed, backup)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
