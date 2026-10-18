"""未来の当番を照会する。通知・回収の確定記録は作らない。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from .calendar import Calendar
from .config import Config
from . import messages
from .rotation import assigned_students, rotation_students, school_day_count, school_days
from .store import Store


class DutyForecast:
    def __init__(self, store: Store, calendar: Calendar):
        self.store = store
        self.calendar = calendar
        self.lock = asyncio.Lock()

    async def next_turn(self, config: Config, user_id: int, now: datetime, *, search_days: int = 90) -> str:
        now = now.astimezone(config.timezone)
        matches = [s for pair in config.pairs for s in pair.students if s.discord_id == user_id]
        if len(matches) != 1:
            return messages.MEMBER_NOT_FOUND
        if self.lock.locked():
            return messages.FORECAST_BUSY
        async with self.lock:
            async with asyncio.timeout(25):
                first = max(now.date(), config.start_date)
                end = first + timedelta(days=search_days)
                count = await school_day_count(self.calendar, config.start_date, first, now)
                async for school in school_days(self.calendar, first, end, now):
                    saved = self.store.assignment(school.date)
                    students = assigned_students(config, saved) if saved is not None else rotation_students(config, count)
                    count += 1
                    if any(s.number == matches[0].number for s in students):
                        return messages.next_turn_text(school.date, students, today=now.date(),
                                                       collected=saved is not None and bool(saved["collected_at"]))
                return messages.no_upcoming(search_days)
