from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta, timezone
from unittest.mock import AsyncMock

from remind_bot.calendar import Calendar, CalendarError
from remind_bot.config import Pair, Student
from remind_bot.service import DiaryService
from remind_bot.store import Store

from tests.helpers import DiaryFixture, START, stamp


class ServiceTests(DiaryFixture):
    async def test_notifications_use_locations_from_config_and_keep_mentions(self):
        config = replace(self.config, pickup_location="サンプル保管場所", submission_location="サンプル提出場所",
                         dismissal_enabled=True)
        service = DiaryService(config, self.store, Calendar(config, self.store), self.sender)
        await service.tick(stamp(clock="08:10"))
        await service.tick(stamp(clock="16:20"))
        morning, dismissal = self.sender.sent
        self.assertIn("サンプル保管場所から日誌を回収", morning.content)
        self.assertIn("サンプル提出場所に提出", dismissal.content)
        self.assertEqual(morning.user_ids, dismissal.user_ids)
        self.assertTrue(morning.collection_button)
        self.assertFalse(dismissal.collection_button)

    async def test_rotation_preserves_pairs_and_skips_holidays(self):
        for offset, expected in [(0, [32]), (1, [33, 34]), (4, [35, 36]), (7, [1, 2])]:
            day = START + timedelta(days=offset)
            plan = await self.service.prepare(day, stamp(day))
            assert plan is not None
            self.assertEqual([s.number for s in plan.students], expected)
        holiday = START + timedelta(days=2)
        await self.service.tick(stamp(holiday))
        self.assertIsNone(self.store.assignment(holiday))
        self.assertEqual(self.sender.sent, [])

    async def test_lunch_once_including_restart(self):
        await asyncio.gather(self.service.tick(stamp()), self.service.tick(stamp()))
        reopened = Store(self.config.database)
        try:
            other = DiaryService(self.config, reopened, Calendar(self.config, reopened), self.sender)
            await other.tick(stamp(clock="12:11"))
        finally:
            reopened.close()
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.sender.sent[0].kind, "lunch")
        self.assertTrue(self.sender.sent[0].everyone)
        self.assertEqual(self.sender.sent[0].user_ids, ())

    async def test_collection_before_lunch_survives_restart(self):
        await self.service.tick(stamp(clock="08:10"))
        self.assertEqual(self.store.day_for_message(1001), START)
        await self.service.collect(START, 777, stamp(clock="09:00"))
        restarted = DiaryService(self.config, self.store, Calendar(self.config, self.store), self.sender)
        await restarted.tick(stamp())
        self.assertEqual([n.kind for n in self.sender.sent], ["morning"])
        self.assertEqual(self.store.notification(START, "lunch")["reason"], "collected")
        await restarted.collect(START, 888, stamp(clock="12:11"))
        self.assertEqual(self.store.assignment(START)["collected_by"], "777")

    async def test_lunch_collection_updates_both_notifications(self):
        await self.service.tick(stamp(clock="08:10"))
        await self.service.tick(stamp())
        await self.service.collect(START, 777, stamp(clock="12:11"))
        self.assertEqual(self.sender.edits[-1][0], START)
        self.assertCountEqual(self.sender.edits[-1][1], [1001, 1002])

    async def test_old_button_cannot_collect_today(self):
        await self.service.prepare(START, stamp())
        result = await self.service.collect(START, 777, stamp(START + timedelta(days=1)))
        self.assertIn("当日", result)
        self.assertIsNone(self.store.assignment(START)["collected_at"])

    async def test_collection_edit_failure_reports_warning_but_keeps_collection(self):
        await self.service.prepare(START, stamp())
        self.sender.show_collected = AsyncMock(side_effect=RuntimeError("simulated edit failure"))
        result = await self.service.collect(START, 777, stamp(clock="09:00"))
        self.assertTrue(result.startswith("⚠️"))
        self.assertIn("記録しました", result)
        self.assertEqual(self.store.assignment(START)["collected_by"], "777")
        await self.service.tick(stamp())
        self.assertEqual(self.sender.sent, [])
        self.assertEqual(self.store.notification(START, "lunch")["reason"], "collected")

    async def test_uncertain_send_is_not_repeated(self):
        self.sender.fail = True
        await self.service.tick(stamp())
        await self.service.tick(stamp(clock="12:11"))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.store.notification(START, "lunch")["status"], "uncertain")
        self.assertIn("送信結果が不明", await self.service.status(stamp()))

    async def test_crash_after_claim_is_not_repeated(self):
        await self.service.prepare(START, stamp())
        self.store.claim(START, "lunch")
        await self.service.tick(stamp())
        self.assertEqual(self.sender.sent, [])
        self.assertEqual(self.store.notification(START, "lunch")["status"], "sending")

    async def test_lunch_never_sent_before_start_or_after_grace(self):
        await self.service.tick(stamp(clock="12:09"))
        self.assertEqual(self.sender.sent, [])
        await self.service.tick(stamp(clock="12:15"))
        self.assertEqual(self.sender.sent, [])
        self.assertEqual(self.store.notification(START, "lunch")["reason"], "expired")

    async def test_calendar_overrides_lunch_time(self):
        self.days[0]["lunch_time"] = "12:00"
        self.write_days()
        await self.service.tick(stamp(clock="12:00"))
        self.assertEqual([n.kind for n in self.sender.sent], ["lunch"])

    async def test_unknown_historical_day_stops_rotation_and_send(self):
        self.days = self.days[1:]
        self.write_days()
        target = START + timedelta(days=1)
        with self.assertRaises(CalendarError):
            await self.service.tick(stamp(target))
        self.assertIsNone(self.store.assignment(target))
        self.assertEqual(self.sender.sent, [])

    async def test_dismissal_is_optional_and_has_no_collection_button(self):
        config = replace(self.config, dismissal_enabled=True)
        service = DiaryService(config, self.store, Calendar(config, self.store), self.sender)
        await service.prepare(START, stamp())
        await service.collect(START, 777, stamp())
        await service.tick(stamp(clock="16:20"))
        notification = self.sender.sent[0]
        self.assertEqual(notification.kind, "dismissal")
        self.assertFalse(notification.collection_button)
        self.assertFalse(notification.everyone)
        self.assertEqual(notification.user_ids, (100000000000000032,))

    async def test_utc_clock_uses_japan_day(self):
        await self.service.tick(stamp().astimezone(timezone.utc))
        self.assertEqual(self.sender.sent[0].day, START)

    async def test_both_unresolved_still_send_names_and_lunch_everyone_once(self):
        config = replace(self.config, first_pair=1, pairs=(Pair(1, (Student(1, None, True, "山田太郎"), Student(2, None, True, "山田花子"))),))
        service = DiaryService(config, self.store, Calendar(config, self.store), self.sender)
        await service.tick(stamp(clock="08:10"))
        morning = self.sender.sent[0]
        self.assertIn("1番 山田太郎", morning.content)
        self.assertIn("2番 山田花子", morning.content)
        self.assertEqual(morning.user_ids, ())
        self.assertFalse(morning.everyone)
        self.assertTrue(morning.collection_button)
        await service.tick(stamp())
        await service.tick(stamp(clock="12:11"))
        self.assertEqual([item.kind for item in self.sender.sent], ["morning", "lunch"])
        lunch = self.sender.sent[1]
        self.assertTrue(lunch.everyone)
        self.assertEqual(lunch.user_ids, ())
        self.assertIn("1番 山田太郎", lunch.content)
        self.assertIn("2番 山田花子", lunch.content)
        self.assertIn("12:50", lunch.content)

    async def test_mixed_pair_mentions_known_member_and_displays_other_name(self):
        known_id = 100000000000000001
        config = replace(self.config, first_pair=1, dismissal_enabled=True, pairs=(Pair(1, (Student(1, known_id, True, "山田太郎"), Student(2, None, True, "山田花子"))),))
        service = DiaryService(config, self.store, Calendar(config, self.store), self.sender)
        await service.tick(stamp(clock="08:10"))
        await service.tick(stamp(clock="08:11"))
        morning = self.sender.sent[0]
        self.assertEqual(morning.kind, "morning")
        self.assertEqual(morning.user_ids, (known_id,))
        self.assertIn(f"<@{known_id}>（1番）", morning.content)
        self.assertIn("2番 山田花子", morning.content)
        self.assertFalse(morning.everyone)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("2番 山田花子", await service.status(stamp(clock="09:00")))
        await service.collect(START, known_id, stamp(clock="09:00"))
        await service.tick(stamp())
        self.assertEqual(self.store.notification(START, "lunch")["reason"], "collected")
        await service.tick(stamp(clock="16:20"))
        dismissal = self.sender.sent[1]
        self.assertEqual(dismissal.kind, "dismissal")
        self.assertEqual(dismissal.user_ids, (known_id,))
        self.assertIn("2番 山田花子", dismissal.content)
        self.assertFalse(dismissal.everyone)
        self.assertFalse(dismissal.collection_button)
