from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import discord

from remind_bot.calendar import Calendar
from remind_bot.config import Config, load_pairs
from remind_bot.service import DiaryService, Notification
from remind_bot.store import Store

JST = ZoneInfo("Asia/Tokyo")

START = date(2026, 10, 15)


def stamp(day: date = START, clock: str = "12:10") -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=JST)


class FakeSender:
    def __init__(self):
        self.sent: list[Notification] = []
        self.edits: list[tuple[date, list[int]]] = []
        self.fail = False

    async def ready(self) -> None:
        pass

    async def send(self, notification: Notification) -> int:
        self.sent.append(notification)
        if self.fail:
            raise TimeoutError("simulated disconnect after server accepted")
        return 1000 + len(self.sent)

    async def show_collected(self, day: date, message_ids: list[int]) -> None:
        self.edits.append((day, message_ids))


class QuietHandler(BaseHTTPRequestHandler):
    """テスト用HTTP応答のアクセスログを抑止する。"""

    def log_message(self, format: str, *args: object) -> None:
        pass


def make_interaction(*, admin: bool = True, user: int = 900, channel: int = 200) -> Mock:
    """Discordへ接続せず、返信の公開範囲や権限を検証する代用品。"""
    return Mock(spec=discord.Interaction, id=10000, user=SimpleNamespace(id=user), guild_id=100,
                channel_id=channel, permissions=discord.Permissions(administrator=admin),
                response=AsyncMock(is_done=Mock(return_value=False)), followup=AsyncMock(),
                edit_original_response=AsyncMock())


class DiaryFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        roster = "number,discord_id,active\n" + "\n".join(
            f"{n},{100000000000000000 + n},{'false' if n == 31 else 'true'}" for n in range(1, 41)
        )
        (self.root / "roster.csv").write_text(roster, encoding="utf-8")
        self.config = Config(
            path=self.root / "config.toml", guild_id=100, channel_id=200,
            pairs=load_pairs(self.root / "roster.csv", require_ids=True),
            start_date=START, first_pair=16, timezone=JST,
            morning_time=datetime.strptime("08:10", "%H:%M").time(),
            lunch_time=datetime.strptime("12:10", "%H:%M").time(),
            dismissal_time=datetime.strptime("16:20", "%H:%M").time(),
            dismissal_enabled=False, grace_minutes=5, poll_seconds=20,
            calendar_source="file", calendar_file=self.root / "calendar.json",
            calendar_url="", calendar_fields={}, overrides_file=None,
            timeout_seconds=2, refresh_seconds=300, database=self.root / "state.sqlite3",
        )
        self.days = [
            {"date": (START + timedelta(days=n)).isoformat(), "is_school_day": n not in (2, 3)}
            for n in range(35)
        ]
        self.write_days()
        self.store = Store(self.config.database)
        self.addCleanup(self.store.close)
        self.sender = FakeSender()
        self.service = DiaryService(self.config, self.store, Calendar(self.config, self.store), self.sender)

    def write_days(self):
        self.config.calendar_file.write_text(json.dumps({"days": self.days}), encoding="utf-8")
