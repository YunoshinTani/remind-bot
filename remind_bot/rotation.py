"""通知と担当日照会で共有する、登校日ごとの当番巡回。"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
import json

from .calendar import Calendar, SchoolDay
from .config import Config, ConfigError, Student


async def school_days(calendar: Calendar, start: date, end: date, now: datetime) -> AsyncIterator[SchoolDay]:
    """start以上end未満の登校日を、最大28日単位で読み出す。"""
    cursor = start
    while cursor < end:
        last = min(cursor + timedelta(days=27), end - timedelta(days=1))
        for day in await calendar.get_range(cursor, last, now):
            if day.is_school_day:
                yield day
        cursor = last + timedelta(days=1)


async def school_day_count(calendar: Calendar, start: date, end: date, now: datetime) -> int:
    count = 0
    async for _ in school_days(calendar, start, end, now):
        count += 1
    return count


def rotation_students(config: Config, school_day_index: int) -> tuple[Student, ...]:
    first = next(i for i, pair in enumerate(config.pairs) if pair.number == config.first_pair)
    return config.pairs[(first + school_day_index) % len(config.pairs)].students


def assigned_students(config: Config, record) -> tuple[Student, ...]:
    students = {s.number: s for pair in config.pairs for s in pair.students}
    numbers = json.loads(record["numbers"])
    if any(number not in students for number in numbers):
        raise ConfigError("保存された担当と名簿が一致しません。名簿変更の運用手順を確認してください。")
    return tuple(students[number] for number in numbers)
