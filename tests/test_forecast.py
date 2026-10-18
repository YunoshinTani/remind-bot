from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from remind_bot.calendar import Calendar, CalendarError
from remind_bot.config import load_config, load_pairs
from remind_bot.discord_app import DiaryClient
from remind_bot.service import DiaryService
from remind_bot.store import Store

from tests.helpers import QuietHandler, make_interaction

START = date(2026, 10, 15)


class ForecastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        roster = root / "roster.csv"
        roster.write_text("number,name,discord_id,active\n" + "\n".join(
            f"{n},架空生徒{n},{1000+n},{'false' if n == 31 else 'true'}" for n in range(1, 41)
        ), encoding="utf-8")
        demo = load_config(Path(__file__).resolve().parents[1] / "examples/config.demo.toml")
        self.config = replace(demo, pairs=load_pairs(roster, require_ids=True), start_date=START,
                              first_pair=17, calendar_source="file", calendar_file=root / "days.json",
                              overrides_file=None, database=root / "state.sqlite3", guild_id=100,
                              channel_id=200, resolve_members=False)
        self.now = datetime(2026, 10, 15, 21, 0, tzinfo=self.config.timezone)
        self.days = [{"date": (START + timedelta(days=n)).isoformat(),
                      "is_school_day": (START + timedelta(days=n)).weekday() < 5} for n in range(120)]
        self.write_days()
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.calendar = Calendar(self.config, self.store)
        self.service = DiaryService(self.config, self.store, self.calendar)

    def write_days(self):
        self.config.calendar_file.write_text(json.dumps({"days": self.days}), encoding="utf-8")

    async def test_next_date_and_partner_do_not_freeze_future_assignments(self):
        result = await self.service.next_turn(1035, self.now)
        self.assertIn("2026/10/16", result)
        self.assertIn("35番 架空生徒35", result)
        self.assertIn("36番 架空生徒36", result)
        self.assertIsNone(self.store.assignment(START + timedelta(days=1)))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 0)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 0)
        # 照会後の休講変更が、本番の当番計算にもそのまま反映される。
        self.days[1]["is_school_day"] = False
        self.write_days()
        changed = await self.service.next_turn(1035, self.now)
        self.assertIn("2026/10/19", changed)
        plan = await self.service.prepare(START + timedelta(days=4), self.now)
        assert plan is not None
        self.assertEqual([s.number for s in plan.students], [35, 36])

    async def test_rotation_wraps_and_weekend_does_not_advance(self):
        result = await self.service.next_turn(1001, self.now)
        self.assertIn("2026/10/21", result)
        saturday = self.now + timedelta(days=2)
        self.assertIn("2026/10/19", await self.service.next_turn(1037, saturday))

    async def test_suspended_student_is_excluded_but_partner_keeps_turn(self):
        config = replace(self.config, first_pair=15)
        service = DiaryService(config, self.store, self.calendar)
        result = await service.next_turn(1032, self.now)
        self.assertIn("2026/10/16", result)
        self.assertIn("32番 架空生徒32", result)
        self.assertNotIn("31番", result)
        self.assertIn("照合できません", await service.next_turn(1031, self.now))

    async def test_today_uses_stored_assignment_and_collection_state(self):
        self.store.ensure_assignment(START, (1, 2))
        self.store.collect(START, 1001, self.now)
        before = self.store.connection.total_changes
        result = await self.service.next_turn(1001, self.now)
        # 見出しの言い回しではなく、保存された当日・担当ペアを確認する。
        self.assertIn("2026/10/15", result)
        self.assertIn("1番 架空生徒1", result)
        self.assertIn("2番 架空生徒2", result)
        self.assertIn("回収済み", result)
        self.assertEqual(self.store.connection.total_changes, before)

    async def test_unknown_user_does_not_request_timetable(self):
        self.calendar.get_range = AsyncMock()
        self.assertIn("照合できません", await self.service.next_turn(9999, self.now))
        self.calendar.get_range.assert_not_awaited()

    async def test_unpublished_future_does_not_invent_a_date(self):
        self.days = [{**day, "is_school_day": False} for day in self.days]
        self.write_days()
        result = await self.service.next_turn(1001, self.now, search_days=60)
        self.assertIn("今後60日", result)
        self.assertIn("まだ登録されていない", result)
        self.assertIsNone(self.store.assignment(START))

    async def test_forecast_does_not_block_collection_and_serializes_queries(self):
        self.store.ensure_assignment(START, (33, 34))
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.calendar.get_range
        async def slow_range(start: date, end: date, now: datetime):
            entered.set()
            await release.wait()
            return await original(start, end, now)
        self.calendar.get_range = slow_range
        task = asyncio.create_task(self.service.next_turn(1035, self.now))
        try:
            await entered.wait()
            busy = await self.service.next_turn(1036, self.now)
            self.assertIn("確認中", busy)
            self.assertIn("`/next`", busy)
            self.assertIn("回収を記録", await self.service.collect(START, 1033, self.now))
            self.assertFalse(self.service.lock.locked())
        finally:
            release.set()
            await task

    async def test_calendar_failure_does_not_guess_or_keep_query_lock(self):
        self.calendar.get_range = AsyncMock(side_effect=CalendarError("missing"))
        with self.assertRaises(CalendarError):
            await self.service.next_turn(1035, self.now)
        self.assertFalse(self.service.forecast.lock.locked())
        self.assertFalse(self.service.lock.locked())

    @patch("remind_bot.discord_app.datetime")
    async def test_command_response_is_private_and_does_not_ping(self, clock):
        clock.now.return_value = self.now
        client = DiaryClient(self.config, self.store)
        interaction = make_interaction(user=1035)
        try:
            await client.my_turn(interaction)
            self.assertTrue(interaction.response.defer.call_args.kwargs["ephemeral"])
            reply = interaction.followup.send.call_args
            self.assertIn("2026/10/16", reply.args[0])
            self.assertTrue(reply.kwargs["ephemeral"])
            self.assertEqual(reply.kwargs["allowed_mentions"].to_dict(), {"parse": []})
            interaction.channel_id = 999
            interaction.followup.reset_mock()
            await client.my_turn(interaction)
            self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
            interaction.followup.send.assert_not_awaited()
            interaction.channel_id = 200
            client.service.next_turn = AsyncMock(side_effect=TimeoutError)
            await client.my_turn(interaction)
            self.assertIn("時間がかかっています", interaction.followup.send.call_args.args[0])
            self.assertIn("`/next`", interaction.followup.send.call_args.args[0])
        finally:
            await client.close()

    async def test_myid_range_uses_one_request_and_preserves_past_cache(self):
        requests, invalid = [], []
        class Handler(QuietHandler):
            def do_GET(self):
                requests.append(parse_qs(urlsplit(self.path).query))
                params = requests[-1]
                start = params["startDayTos"][0].replace("/", "-")
                end = params["endDayTos"][0].replace("/", "-")
                lessons = [
                    {"classDate": "2026-10-15", "isCanceled": False, "startTime": "08:40:00", "endTime": "16:00:00"},
                    {"classDate": "2026-10-16", "isCanceled": True},
                ]
                payload = {"dailyPlan": [lesson for lesson in lessons if start <= lesson["classDate"] <= end] + invalid}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = replace(self.config, calendar_source="myid", calendar_url=f"http://127.0.0.1:{server.server_port}/daily")
        calendar = Calendar(config, self.store)
        try:
            days = await calendar.get_range(START, START + timedelta(days=2), self.now)
            self.assertEqual([d.is_school_day for d in days], [True, False, False])
            self.assertEqual(requests[0]["startDayTos"], ["2026/10/15"])
            self.assertEqual(requests[0]["endDayTos"], ["2026/10/17"])
            await calendar.get(START + timedelta(days=1), self.now)
            self.assertEqual(len(requests), 1)
            # 確定済みの過去キャッシュは一括取得で上書きしない。
            self.store.save_schedule(START, {"date": START.isoformat(), "is_school_day": False}, self.now)
            later = self.now + timedelta(days=1, minutes=6)
            self.assertFalse((await calendar.get_range(START, START + timedelta(days=2), later))[0].is_school_day)
            # 期間外のデータが混ざった応答では、部分的な結果も保存しない。
            invalid.append({"classDate": "2027-01-01", "isCanceled": True})
            with self.assertRaises(CalendarError):
                await calendar.get_range(START + timedelta(days=3), START + timedelta(days=5), later)
            self.assertIsNone(self.store.cached_schedule(START + timedelta(days=3)))
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
