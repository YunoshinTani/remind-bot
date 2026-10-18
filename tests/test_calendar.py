from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from remind_bot.calendar import Calendar, CalendarError

from tests.helpers import DiaryFixture, QuietHandler, START, stamp


class CalendarTests(DiaryFixture):
    async def test_invalid_calendar_is_not_treated_as_holiday(self):
        calendar = Calendar(self.config, self.store)
        for payload in [
            {"date": "2026-10-14", "is_school_day": True},
            {"date": "2026-10-15", "is_school_day": "false"},
            {"date": "2026-10-15", "is_school_day": True, "lunch_time": "07:00"},
        ]:
            with self.assertRaises(CalendarError):
                calendar.parse(payload, START)

    async def test_http_get_mapping_cache_and_error(self):
        requests = []
        class Handler(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                requested = self.path.split("=")[-1]
                payload = {"data": {"day": requested, "open": True, "lunch": "12:00"}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = replace(self.config, calendar_source="http", calendar_url=f"http://127.0.0.1:{server.server_port}/timetable?date={{date}}", calendar_fields={"date": "data.day", "is_school_day": "data.open", "lunch_time": "data.lunch"})
        calendar = Calendar(config, self.store)
        try:
            first = await calendar.get(START, stamp(clock="11:00"))
            await calendar.get(START, stamp(clock="11:04"))
            self.assertEqual(len(requests), 1)
            self.assertEqual(first.lunch_time.hour, 12)
            self.assertEqual(first.lunch_time.minute, 0)
            await calendar.get(START, stamp(clock="11:05"))
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0], "/timetable?date=2026-10-15")
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()
        failing = Calendar(replace(config, calendar_url="http://127.0.0.1:1/?date={date}"), self.store)
        with self.assertRaises(CalendarError):
            await failing.get(START, stamp(clock="11:10"))

    async def test_myid_get_uses_requested_date_and_excludes_cancelled_lessons(self):
        requests = []
        class Handler(QuietHandler):
            def do_GET(self):
                params = parse_qs(urlsplit(self.path).query)
                requests.append(params)
                requested = params["startDayTos"][0].replace("/", "-")
                payload = {"dailyPlan": [
                    {"classDate": requested, "startTime": "08:40:00", "endTime": "10:10:00", "isCanceled": False, "subjectName": "sample"},
                    {"classDate": requested, "startTime": "14:30:00", "endTime": "16:00:00", "isCanceled": False},
                    {"classDate": requested, "startTime": "16:10:00", "endTime": "17:00:00", "isCanceled": True},
                ]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = replace(self.config, calendar_source="myid", calendar_url=f"http://127.0.0.1:{server.server_port}/daily?startDayTos=old&endDayTos=old&organizationCd=example")
        calendar = Calendar(config, self.store)
        try:
            result = await calendar.get(START, stamp())
            self.assertTrue(result.is_school_day)
            self.assertEqual(result.dismissal_time.strftime("%H:%M"), "16:00")
            self.assertEqual(requests[0]["startDayTos"], ["2026/10/15"])
            self.assertEqual(requests[0]["endDayTos"], ["2026/10/15"])
            self.assertEqual(requests[0]["organizationCd"], ["example"])
            cached = json.loads(self.store.cached_schedule(START)["payload"])
            self.assertNotIn("dailyPlan", cached)
            self.assertNotIn("subjectName", cached)
            await calendar.get(START, stamp(clock="12:14"))
            self.assertEqual(len(requests), 1)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()

    async def test_myid_empty_cancelled_and_invalid_responses(self):
        calendar = Calendar(self.config, self.store)
        self.assertFalse(calendar.normalize_myid({"dailyPlan": []}, START)["is_school_day"])
        self.assertFalse(calendar.normalize_myid({"dailyPlan": [{"classDate": START.isoformat(), "isCanceled": True}]}, START)["is_school_day"])
        for payload in [{}, {"dailyPlan": None}, {"dailyPlan": [{"classDate": "2026-10-14", "isCanceled": False}]}, {"dailyPlan": [{"classDate": START.isoformat(), "isCanceled": "false"}]}]:
            with self.assertRaises(CalendarError):
                calendar.normalize_myid(payload, START)
        # 午前中だけの授業でも登校日として扱う。
        short_day = calendar.normalize_myid({"dailyPlan": [{"classDate": START.isoformat(), "isCanceled": False, "startTime": "08:40:00", "endTime": "10:10:00"}]}, START)
        self.assertTrue(calendar.parse(short_day, START).is_school_day)
