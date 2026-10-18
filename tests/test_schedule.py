from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from http.server import ThreadingHTTPServer
import json
from threading import Event, Thread
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

from remind_bot import messages
from remind_bot.calendar import Calendar, CalendarError
from remind_bot.discord_app import DiaryClient
from remind_bot.schedule import ScheduleQuery
from tests.helpers import DiaryFixture, QuietHandler, START, make_interaction, stamp


def lesson(day=START, period=1, *, cancelled=False, subject="科目A"):
    return {"classDate": day.isoformat(), "period": period, "startTime": "08:40:00", "endTime": "10:10:00",
            "subjectName": subject, "facilityName": "教室A", "lecturerName": "担当教員A", "isCanceled": cancelled}


class ScheduleTests(DiaryFixture):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, calendar_source="myid", calendar_url="https://example.invalid/daily")
        self.calendar = Calendar(self.config, self.store)
        self.request_json = Mock(return_value={"dailyPlan": [lesson()]})
        self.calendar.request_json = self.request_json
        self.query = ScheduleQuery(self.calendar)

    async def test_default_is_today_in_japan_and_only_requests_the_requested_day(self):
        from datetime import timezone
        result = await self.query.lookup(None, stamp(clock="00:30").astimezone(timezone.utc))
        self.request_json.assert_called_once_with(START)
        text = "\n".join(result)
        self.assertIn("2026/10/15", text)
        self.assertIn("科目A", text)
        self.assertIn("教室A", text)
        self.assertIn("担当教員A", text)
        self.assertIn("08:40〜10:10", text)

    async def test_both_date_formats_select_the_same_specific_day(self):
        target = date(2026, 10, 19)
        self.request_json.return_value = {"dailyPlan": [lesson(target)]}
        for value in ("2026-10-19", " 2026/10/19 "):
            result = await self.query.lookup(value, stamp())
            self.assertIn("2026/10/19", result[0])
            self.request_json.assert_called_with(target)

    async def test_invalid_dates_are_rejected_before_api_access(self):
        for value in ("2026-02-30", "2026-2-3", "20261015", "10/16", "2026/10-16", ""):
            self.assertEqual(await self.query.lookup(value, stamp()), messages.SCHEDULE_INVALID_DATE)
        self.request_json.assert_not_called()

    async def test_lessons_are_sorted_and_cancelled_lessons_remain_visible(self):
        self.request_json.return_value = {"dailyPlan": [
            {**lesson(period=2, cancelled=True, subject="科目B"), "startTime": "10:20:00", "endTime": "11:50:00"},
            lesson(),
        ]}
        text = "\n".join(await self.query.lookup(None, stamp()))
        self.assertLess(text.index("科目A"), text.index("科目B"))
        self.assertIn("休講", text)
        self.assertIn("~~科目B~~", text)

    async def test_empty_schedule_does_not_claim_a_definite_school_closure(self):
        self.request_json.return_value = {"dailyPlan": []}
        text = "\n".join(await self.query.lookup(None, stamp()))
        self.assertIn("未登録", text)
        self.assertIn("可能性", text)

    async def test_all_cancelled_and_missing_optional_fields_are_handled(self):
        self.request_json.return_value = {"dailyPlan": [{
            "classDate": START.isoformat(), "isCanceled": True,
            "subjectName": None, "facilityName": None, "lecturerName": None,
        }]}
        text = "\n".join(await self.query.lookup(None, stamp()))
        self.assertIn("全授業が休講", text)
        self.assertIn("科目名未登録", text)
        self.assertIn("時刻未登録", text)
        self.assertNotIn("None", text)

    async def test_bad_schema_date_cancellation_and_times_raise_without_saving_data(self):
        cases = [{}, {"dailyPlan": None}, {"dailyPlan": [lesson(START.replace(day=14))]},
                 {"dailyPlan": [{**lesson(), "isCanceled": "false"}]},
                 {"dailyPlan": [{**lesson(), "startTime": "invalid"}]},
                 {"dailyPlan": [{**lesson(), "period": True}]}]
        for payload in cases:
            self.request_json.return_value = payload
            with self.assertRaises(CalendarError):
                await self.query.lookup(None, stamp())
            self.assertFalse(self.query.lock.locked())
        self.assertEqual(self.store.connection.total_changes, 0)

    async def test_each_lookup_fetches_again_without_changing_notification_state_or_cache(self):
        self.store.save_schedule(START, {"date": START.isoformat(), "is_school_day": False}, stamp())
        before = self.store.connection.total_changes
        first = await self.query.lookup(None, stamp())
        self.request_json.return_value = {"dailyPlan": [lesson(cancelled=True)]}
        second = await self.query.lookup(None, stamp())
        self.assertNotEqual(first, second)
        self.assertEqual(self.request_json.call_count, 2)
        self.assertEqual(self.store.connection.total_changes, before)
        self.assertIsNone(self.store.assignment(START))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 0)
        self.assertFalse(json.loads(self.store.cached_schedule(START)["payload"])["is_school_day"])

    async def test_busy_lookup_does_not_block_collection(self):
        entered, release = Event(), Event()
        def slow_request(target):
            entered.set()
            if not release.wait(5):
                raise TimeoutError
            return {"dailyPlan": [lesson(target)]}
        self.request_json.side_effect = slow_request
        pending = asyncio.create_task(self.query.lookup(None, stamp()))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            self.assertEqual(await self.query.lookup(None, stamp()), messages.SCHEDULE_BUSY)
            self.store.ensure_assignment(START, (22,))
            await self.service.collect(START, 900, stamp())
            self.assertIsNotNone(self.store.assignment(START)["collected_at"])
        finally:
            release.set()
            await pending

    async def test_api_failure_releases_lock_and_unsupported_source_never_requests(self):
        self.request_json.side_effect = CalendarError("unavailable")
        with self.assertRaises(CalendarError):
            await self.query.lookup(None, stamp())
        self.assertFalse(self.query.lock.locked())
        self.calendar.config = replace(self.config, calendar_source="file")
        self.request_json.reset_mock()
        self.assertEqual(await self.query.lookup(None, stamp()), messages.SCHEDULE_UNAVAILABLE)
        self.request_json.assert_not_called()

    async def test_long_schedule_splits_at_lesson_boundaries_and_keeps_every_lesson(self):
        self.request_json.return_value = {"dailyPlan": [
            lesson(period=n, subject=f"科目{n:02}：" + "長い科目名" * 35) for n in range(1, 31)
        ]}
        pages = await self.query.lookup(None, stamp())
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 2000 for page in pages))
        text = "\n".join(pages)
        for n in range(1, 31):
            self.assertEqual(text.count(f"科目{n:02}："), 1)

    async def test_subject_markdown_is_escaped_and_newlines_do_not_change_layout(self):
        self.request_json.return_value = {"dailyPlan": [lesson(subject="科目*強調*\n改行") ]}
        text = "\n".join(await self.query.lookup(None, stamp()))
        self.assertIn(r"科目\*強調\* 改行", text)

    async def test_real_http_request_uses_myid_date_parameters(self):
        requests = []
        class Handler(QuietHandler):
            def do_GET(self):
                params = parse_qs(urlsplit(self.path).query)
                requests.append(params)
                target = date.fromisoformat(params["startDayTos"][0].replace("/", "-"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"dailyPlan": [lesson(target)]}).encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = replace(self.config, calendar_url=f"http://127.0.0.1:{server.server_port}/daily?startDayTos=old&endDayTos=old")
        try:
            query = ScheduleQuery(Calendar(config, self.store))
            result = await query.lookup("2026-10-19", stamp())
            self.assertIn("2026/10/19", result[0])
            self.assertEqual(requests[0]["startDayTos"], ["2026/10/19"])
            self.assertEqual(requests[0]["endDayTos"], ["2026/10/19"])
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()


class ScheduleCommandTests(DiaryFixture):
    interaction = staticmethod(make_interaction)

    async def test_optional_date_and_multiple_pages_are_private_and_do_not_ping(self):
        client = DiaryClient(self.config, self.store)
        try:
            client.schedules.lookup = AsyncMock(return_value=("page1", "page2"))
            interaction = self.interaction()
            await client.schedule(interaction)
            self.assertIsNone(client.schedules.lookup.call_args.args[0])
            self.assertTrue(interaction.response.defer.call_args.kwargs["ephemeral"])
            self.assertEqual(interaction.followup.send.await_count, 2)
            for call in interaction.followup.send.call_args_list:
                self.assertTrue(call.kwargs["ephemeral"])
                self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})
            await client.schedule(self.interaction(), "2026/10/19")
            self.assertEqual(client.schedules.lookup.call_args.args[0], "2026/10/19")
        finally:
            await client.close()

    async def test_wrong_channel_does_not_fetch_and_failures_return_safe_private_replies(self):
        client = DiaryClient(self.config, self.store)
        try:
            client.schedules.lookup = AsyncMock()
            wrong = self.interaction(channel=999)
            await client.schedule(wrong)
            client.schedules.lookup.assert_not_awaited()
            self.assertTrue(wrong.response.send_message.call_args.kwargs["ephemeral"])
            for error, expected in ((CalendarError("private URL must not be echoed"), messages.SCHEDULE_ERROR),
                                    (TimeoutError(), messages.SCHEDULE_TIMEOUT)):
                client.schedules.lookup.side_effect = error
                interaction = self.interaction()
                await client.schedule(interaction)
                reply = interaction.followup.send.call_args
                self.assertEqual(reply.args[0], expected)
                self.assertTrue(reply.kwargs["ephemeral"])
                self.assertNotIn("private URL", reply.args[0])
        finally:
            await client.close()
