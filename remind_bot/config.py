from __future__ import annotations

import csv
import tomllib
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    pass


def parse_time(value: object) -> time:
    if not isinstance(value, str) or len(value) != 5:
        raise ConfigError("時刻は HH:MM で指定してください。")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError("時刻は HH:MM で指定してください。") from exc
    if parsed.tzinfo is not None:
        raise ConfigError("時刻にタイムゾーンは含めないでください。")
    return parsed


def positive_int(value: object, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{label} は整数にしてください。")
    try:
        result = int(str(value))
    except ValueError as exc:
        raise ConfigError(f"{label} は整数にしてください。") from exc
    if result < (0 if allow_zero else 1):
        raise ConfigError(f"{label} の値が小さすぎます。")
    return result


@dataclass(frozen=True)
class Student:
    number: int
    discord_id: int | None
    active: bool
    name: str = ""
    discord_name: str = ""


@dataclass(frozen=True)
class Pair:
    number: int
    students: tuple[Student, ...]


def load_pairs(path: Path, *, require_ids: bool) -> tuple[Pair, ...]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        if not {"number", "discord_id", "active"}.issubset(rows.fieldnames or []):
            raise ConfigError("名簿 CSV には number,discord_id,active が必要です。")
        students = []
        for row in rows:
            number = positive_int(row["number"], "出席番号")
            active_text = row["active"].strip().lower()
            if active_text not in {"true", "false"}:
                raise ConfigError(f"{number} 番の active は true / false にしてください。")
            active = active_text == "true"
            raw_id = row["discord_id"].strip()
            discord_id = positive_int(raw_id, "Discord ID") if raw_id else None
            if require_ids and active and discord_id is None:
                raise ConfigError(f"{number} 番の Discord ID が未設定です。")
            students.append(Student(number, discord_id, active, row.get("name", "").strip(), row.get("discord_name", "").strip()))
    students.sort(key=lambda student: student.number)
    if not students or [s.number for s in students] != list(range(1, len(students) + 1)):
        raise ConfigError("名簿は1番から最終番号まで重複・欠番なしで記載してください。休学者は active=false にします。")
    ids = [s.discord_id for s in students if s.discord_id is not None]
    if len(ids) != len(set(ids)):
        raise ConfigError("名簿に重複した Discord ID があります。")
    pairs = tuple(
        Pair(index // 2 + 1, tuple(s for s in students[index:index + 2] if s.active))
        for index in range(0, len(students), 2)
    )
    pairs = tuple(pair for pair in pairs if pair.students)
    if not pairs:
        raise ConfigError("担当できる生徒がいません。")
    return pairs


@dataclass(frozen=True)
class Config:
    path: Path
    guild_id: int
    channel_id: int
    pairs: tuple[Pair, ...]
    start_date: date
    first_pair: int
    timezone: ZoneInfo
    morning_time: time
    lunch_time: time
    dismissal_time: time
    dismissal_enabled: bool
    grace_minutes: int
    poll_seconds: int
    calendar_source: str
    calendar_file: Path
    calendar_url: str
    calendar_fields: dict[str, str]
    overrides_file: Path | None
    timeout_seconds: int
    refresh_seconds: int
    database: Path
    resolve_members: bool = False
    lunch_end_time: time = time(12, 50)
    roster_file: Path | None = None
    pickup_location: str = "担当教員の部屋の前"
    submission_location: str = "担当教員の部屋の前"


def load_config(path: Path, *, live: bool = False) -> Config:
    path = path.resolve()
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    try:
        discord, rotation = raw["discord"], raw["rotation"]
        notifications, calendar = raw["notifications"], raw["calendar"]
        resolve = lambda value: (path.parent / value).resolve()
        resolve_members = discord.get("resolve_members", False)
        if not isinstance(resolve_members, bool):
            raise ConfigError("resolve_members は true / false にしてください。")
        pairs = load_pairs(resolve(rotation["roster_file"]), require_ids=live and not resolve_members)
        if resolve_members and any(s.discord_id is None and not (s.name or s.discord_name) for p in pairs for s in p.students):
            raise ConfigError("名前から照合する場合、名簿の name または discord_name が必要です。")
        first_pair = positive_int(rotation["first_pair"], "first_pair")
        if first_pair not in {pair.number for pair in pairs}:
            raise ConfigError("first_pair に指定した組に担当者がいません。")
        source = calendar["source"]
        if source not in {"file", "http", "myid"}:
            raise ConfigError("calendar.source は file / http / myid にしてください。")
        url = calendar.get("url", "")
        if source == "http" and (not url.startswith(("https://", "http://")) or "{date}" not in url):
            raise ConfigError("calendar.url は {date} を含む HTTP(S) URL にしてください。")
        if source == "myid" and not url.startswith(("https://", "http://")):
            raise ConfigError("calendar.url に MyiD の HTTP(S) URL を指定してください。")
        enabled = notifications.get("dismissal_enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError("dismissal_enabled は true / false にしてください。")
        locations = {key: notifications.get(key, "担当教員の部屋の前")
                     for key in ("pickup_location", "submission_location")}
        if any(not isinstance(value, str) or not value.strip() or len(value) > 200
               or "\n" in value or "\r" in value for value in locations.values()):
            raise ConfigError("日誌の回収・提出場所は1〜200文字の1行で指定してください。")
        config = Config(
            path=path,
            guild_id=positive_int(discord.get("guild_id", 0), "guild_id", allow_zero=True),
            channel_id=positive_int(discord["channel_id"], "channel_id", allow_zero=not live),
            pairs=pairs,
            start_date=date.fromisoformat(rotation["start_date"]),
            first_pair=first_pair,
            timezone=ZoneInfo(notifications.get("timezone", "Asia/Tokyo")),
            morning_time=parse_time(notifications["morning_time"]),
            lunch_time=parse_time(notifications["lunch_time"]),
            dismissal_time=parse_time(notifications["dismissal_time"]),
            dismissal_enabled=enabled,
            grace_minutes=positive_int(notifications.get("grace_minutes", 5), "grace_minutes"),
            poll_seconds=positive_int(notifications.get("poll_seconds", 20), "poll_seconds"),
            calendar_source=source,
            calendar_file=resolve(calendar.get("file", "calendar.json")),
            calendar_url=url,
            calendar_fields=calendar.get("fields", {}),
            overrides_file=resolve(calendar["overrides_file"]) if calendar.get("overrides_file") else None,
            timeout_seconds=positive_int(calendar.get("timeout_seconds", 10), "timeout_seconds"),
            refresh_seconds=positive_int(calendar.get("refresh_seconds", 300), "refresh_seconds"),
            database=resolve(raw["storage"]["database"]),
            resolve_members=resolve_members,
            lunch_end_time=parse_time(notifications.get("lunch_end_time", "12:50")),
            roster_file=resolve(rotation["roster_file"]),
            pickup_location=locations["pickup_location"].strip(),
            submission_location=locations["submission_location"].strip(),
        )
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"設定ファイルの形式が不正です: {exc}") from exc
    if config.poll_seconds > config.grace_minutes * 60:
        raise ConfigError("poll_seconds は通知の猶予時間以内にしてください。")
    if config.lunch_time >= config.lunch_end_time:
        raise ConfigError("昼休み終了時刻は開始時刻より後にしてください。")
    return config
