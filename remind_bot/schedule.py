"""MyiDの時間割照会。通知・巡回用の記録やキャッシュは変更しない。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time
import re

from . import messages
from .calendar import Calendar, CalendarError


@dataclass(frozen=True)
class Lesson:
    period: int | None
    start: time | None
    end: time | None
    subject: str
    room: str
    lecturer: str
    cancelled: bool


def schedule_date(value: str | None, today: date) -> date:
    if value is None:
        return today
    value = value.strip()
    if not re.fullmatch(r"\d{4}([-/])\d{2}\1\d{2}", value):
        raise ValueError("日付の書式を確認してください。")
    return date.fromisoformat(value.replace("/", "-"))


def parse_lessons(calendar: Calendar, payload: dict, target: date) -> tuple[Lesson, ...]:
    # 日付・休講・有効授業の時刻は、登校日判定と同じ検証を通す。
    calendar.normalize_myid(payload, target)

    def text(row: dict, key: str) -> str:
        value = row.get(key)
        if value is None:
            return ""
        if not isinstance(value, str):
            raise CalendarError("MyiDの時間割の表示項目が不正です。")
        return " ".join(value.split())

    def clock(row: dict, key: str) -> time | None:
        value = row.get(key)
        if value in (None, "") and row["isCanceled"]:
            return None
        if not isinstance(value, str):
            raise CalendarError("MyiDの時間割の時刻が不正です。")
        try:
            result = time.fromisoformat(value)
            if result.tzinfo is not None:
                raise ValueError
            return result
        except (TypeError, ValueError):
            raise CalendarError("MyiDの時間割の時刻が不正です。") from None

    lessons = []
    for row in payload["dailyPlan"]:
        period = row.get("period")
        if period is not None and (type(period) is not int or period < 1):
            raise CalendarError("MyiDの時間割の時限が不正です。")
        start, end = clock(row, "startTime"), clock(row, "endTime")
        if start is not None and end is not None and start >= end:
            raise CalendarError("MyiDの時間割の開始・終了時刻が不正です。")
        lessons.append(Lesson(period, start, end, text(row, "subjectName") or "科目名未登録",
                              text(row, "facilityName"), text(row, "lecturerName"), row["isCanceled"]))
    return tuple(sorted(lessons, key=lambda lesson: (lesson.start or time.max, lesson.period or 999, lesson.subject)))


class ScheduleQuery:
    def __init__(self, calendar: Calendar):
        self.calendar = calendar
        # 時間割の照会同士だけを制限し、通知・回収のロックは保持しない。
        self.lock = asyncio.Lock()

    async def lookup(self, value: str | None, now: datetime) -> str | tuple[str, ...]:
        today = now.astimezone(self.calendar.config.timezone).date()
        try:
            target = schedule_date(value, today)
        except ValueError:
            return messages.SCHEDULE_INVALID_DATE
        if self.calendar.config.calendar_source != "myid":
            return messages.SCHEDULE_UNAVAILABLE
        if self.lock.locked():
            return messages.SCHEDULE_BUSY
        async with self.lock:
            async with asyncio.timeout(20):
                payload = await asyncio.to_thread(self.calendar.request_json, target)
                lessons = parse_lessons(self.calendar, payload, target)
                return messages.schedule_pages(target, lessons)
