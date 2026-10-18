from __future__ import annotations

import asyncio
import json
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

from discord import app_commands

from remind_bot import messages
from remind_bot.admin import ReminderView
from remind_bot.calendar import Calendar
from remind_bot.discord_app import DiaryClient, DiscordSender
from remind_bot.service import DiaryService, ReminderPreview
from remind_bot.store import Store
from tests.helpers import DiaryFixture, START, make_interaction, stamp


class ManualReminderTests(DiaryFixture):
    async def preview(self, kind="morning", clock="07:00") -> ReminderPreview:
        preview = await self.service.preview_reminder(kind, stamp(clock=clock))
        assert isinstance(preview, ReminderPreview), preview
        return preview

    async def test_manual_morning_ignores_schedule_but_suppresses_same_automatic_event(self):
        preview = await self.preview()
        self.assertEqual(self.sender.sent, [])
        result = await self.service.send_reminder(preview, "request-1", 900, stamp(clock="07:01"))
        self.assertIn("手動送信", result)
        self.assertEqual(self.sender.sent[0].user_ids, (100000000000000032,))
        self.assertFalse(self.sender.sent[0].everyone)
        await self.service.tick(stamp(clock="08:10"))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.store.notification(START, "morning")["reason"], "manual")
        await self.service.tick(stamp())
        self.assertEqual([item.kind for item in self.sender.sent], ["morning", "lunch"])

    async def test_manual_lunch_can_send_outside_schedule_with_everyone(self):
        preview = await self.preview("lunch", clock="17:00")
        await self.service.send_reminder(preview, "request-1", 900, stamp(clock="17:01"))
        self.assertTrue(self.sender.sent[0].everyone)
        self.assertEqual(self.sender.sent[0].user_ids, ())
        self.assertTrue(self.sender.sent[0].collection_button)
        self.assertEqual(self.store.day_for_message(1001), START)

    async def test_same_request_is_not_repeated_but_explicit_new_request_can_resend(self):
        preview = await self.preview()
        await asyncio.gather(*[self.service.send_reminder(preview, "one", 900, stamp(clock="07:01")) for _ in range(2)])
        self.assertEqual(len(self.sender.sent), 1)
        await self.service.send_reminder(preview, "two", 900, stamp(clock="07:02"))
        self.assertEqual(len(self.sender.sent), 2)
        self.assertNotEqual(self.sender.sent[0].delivery_id, self.sender.sent[1].delivery_id)
        await self.service.collect(START, 777, stamp(clock="07:03"))
        self.assertCountEqual(self.sender.edits[-1][1], [1001, 1002])

    async def test_resend_preserves_original_notification_and_collection_updates_all_posts(self):
        await self.service.tick(stamp(clock="08:10"))
        original = dict(self.store.notification(START, "morning"))
        preview = await self.preview(clock="09:00")
        await self.service.send_reminder(preview, "manual", 900, stamp(clock="09:01"))
        self.assertEqual(dict(self.store.notification(START, "morning")), original)
        await self.service.collect(START, 777, stamp(clock="09:02"))
        self.assertCountEqual(self.sender.edits[-1][1], [1001, 1002])
        self.assertEqual(self.store.manual_notification("manual")["requested_by"], "900")

    async def test_anchor_correction_requires_confirm_and_preserves_existing_history(self):
        self.service.config = replace(self.config, first_pair=4)
        self.store.ensure_assignment(START, (5, 6))
        self.store.claim(START, "morning")
        self.store.finish(START, "morning", "sent", message_id=2000)
        original = dict(self.store.notification(START, "morning"))
        preview = await self.preview(clock="09:00")
        self.assertEqual(preview.stored_numbers, (5, 6))
        self.assertEqual(preview.numbers, (7, 8))
        self.assertEqual(json.loads(self.store.assignment(START)["numbers"]), [5, 6])
        await self.service.send_reminder(preview, "fix", 900, stamp(clock="09:01"))
        self.assertEqual(json.loads(self.store.assignment(START)["numbers"]), [7, 8])
        self.assertEqual(dict(self.store.notification(START, "morning")), original)
        self.assertEqual(json.loads(self.store.manual_notification("fix")["previous_numbers"]), [5, 6])
        await self.service.collect(START, 777, stamp(clock="09:02"))
        self.assertCountEqual(self.sender.edits[-1][1], [1001, 2000])

    async def test_collected_or_changed_assignments_cancel_the_confirmed_send(self):
        preview = await self.preview()
        await self.service.collect(START, 777, stamp(clock="07:01"))
        result = await self.service.send_reminder(preview, "collected", 900, stamp(clock="07:02"))
        self.assertEqual(result, messages.REMIND_COLLECTED)
        self.assertIsNone(self.store.manual_notification("collected"))
        self.assertEqual(self.sender.sent, [])

    async def test_changed_day_or_saved_pair_is_not_sent_using_stale_confirmation(self):
        preview = await self.preview()
        result = await self.service.send_reminder(preview, "old-day", 900, stamp(START + timedelta(days=1)))
        self.assertEqual(result, messages.REMIND_CHANGED)
        with self.store.connection:
            self.store.connection.execute("UPDATE assignments SET numbers='[23,24]' WHERE day=?", (START.isoformat(),))
        result = await self.service.send_reminder(preview, "changed", 900, stamp(clock="07:02"))
        self.assertEqual(result, messages.REMIND_CHANGED)
        self.assertEqual(self.sender.sent, [])

    async def test_latest_timetable_is_loaded_again_and_new_closure_blocks_send(self):
        config = replace(self.config, calendar_source="http")
        calendar = Calendar(config, self.store)
        calendar.get_http = Mock(side_effect=[{"date": START.isoformat(), "is_school_day": True},
                                               {"date": START.isoformat(), "is_school_day": False}])
        self.service = DiaryService(config, self.store, calendar, self.sender)
        preview = await self.preview()
        result = await self.service.send_reminder(preview, "closed", 900, stamp(clock="07:01"))
        self.assertIn("登校日ではない", result)
        self.assertEqual(calendar.get_http.call_count, 2)
        self.assertEqual(self.sender.sent, [])

    async def test_uncertain_send_is_recorded_without_retry_and_is_visible_in_status(self):
        preview = await self.preview()
        self.sender.fail = True
        await self.service.send_reminder(preview, "uncertain", 900, stamp(clock="07:01"))
        await self.service.send_reminder(preview, "uncertain", 900, stamp(clock="07:02"))
        await self.service.tick(stamp(clock="08:10"))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.store.manual_notification("uncertain")["status"], "uncertain")
        self.assertIn("送信結果が不明", await self.service.status(stamp(clock="08:11")))

    async def test_manual_records_and_duplicate_protection_survive_restart(self):
        preview = await self.preview()
        await self.service.send_reminder(preview, "persisted", 900, stamp(clock="07:01"))
        with closing(Store(self.config.database)) as reopened:
            service = DiaryService(self.config, reopened, Calendar(self.config, reopened), self.sender)
            await service.send_reminder(preview, "persisted", 900, stamp(clock="07:02"))
            await service.collect(START, 777, stamp(clock="07:03"))
            self.assertEqual(reopened.day_for_message(1001), START)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.sender.edits[-1][1], [1001])

    async def test_claim_rolls_back_if_collection_changed_before_database_write(self):
        preview = await self.preview()
        self.store.collect(START, 777, stamp(clock="07:01"))
        with self.assertRaises(ValueError):
            self.store.claim_manual(START, "morning", "stale", 900, stamp(clock="07:02"),
                                    preview.numbers, preview.stored_numbers)
        self.assertIsNone(self.store.manual_notification("stale"))
        self.assertIsNone(self.store.notification(START, "morning"))

    async def test_force_refresh_still_honors_calendar_override(self):
        override = self.root / "overrides.json"
        override.write_text(json.dumps({"days": [{"date": START.isoformat(), "is_school_day": False}]}), encoding="utf-8")
        config = replace(self.config, calendar_source="http", overrides_file=override)
        calendar = Calendar(config, self.store)
        calendar.get_http = Mock(side_effect=AssertionError("override should be used"))
        service = DiaryService(config, self.store, calendar, self.sender)
        result = await service.preview_reminder("morning", stamp(clock="07:00"))
        assert isinstance(result, str), result
        self.assertIn("登校日ではない", result)
        calendar.get_http.assert_not_called()

    async def test_new_manual_table_keeps_legacy_assignments_and_notification_records(self):
        import sqlite3
        path = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as database:
            database.executescript("""
                CREATE TABLE assignments(day TEXT PRIMARY KEY,numbers TEXT NOT NULL,collected_at TEXT,collected_by TEXT);
                CREATE TABLE notifications(day TEXT,kind TEXT,status TEXT,message_id TEXT,reason TEXT,PRIMARY KEY(day,kind));
                INSERT INTO assignments VALUES ('2026-10-15','[32]',NULL,NULL);
                INSERT INTO notifications VALUES ('2026-10-15','morning','sent','5000',NULL);
            """)
            database.commit()
        with closing(Store(path)) as upgraded:
            self.assertEqual(json.loads(upgraded.assignment(START)["numbers"]), [32])
            self.assertEqual(upgraded.day_for_message(5000), START)
            upgraded.claim_manual(START, "lunch", "new", 900, stamp(), (32,), (32,))
            upgraded.finish_manual("new", "sent", message_id=5001)
            self.assertCountEqual(upgraded.messages_for_day(START), [5000, 5001])

    async def test_manual_and_automatic_nonces_are_distinct(self):
        client = DiaryClient(self.config, self.store)
        sender = DiscordSender(client)
        sender.channel = AsyncMock()
        sender.channel.id = 200
        plan = await self.service.prepare(START, stamp())
        assert plan is not None
        notification = self.service.notification(plan, "morning")
        try:
            for item in (notification, replace(notification, delivery_id="one"), replace(notification, delivery_id="two")):
                await sender.send(item)
            nonces = [call.kwargs["nonce"] for call in sender.channel.send.call_args_list]
            self.assertEqual(len(set(nonces)), 3)
        finally:
            await client.close()


class AdminCommandTests(DiaryFixture):
    interaction = staticmethod(make_interaction)

    async def client_and_preview(self):
        client = DiaryClient(self.config, self.store)
        self.addAsyncCleanup(client.close)
        preview = await self.service.preview_reminder("lunch", stamp(clock="07:00"))
        assert isinstance(preview, ReminderPreview), preview
        service = Mock(spec=DiaryService, preview_reminder=AsyncMock(return_value=preview),
                       send_reminder=AsyncMock(return_value="sent"))
        client.service = service
        return client, preview, service

    async def test_non_admin_or_wrong_channel_cannot_prepare_a_send(self):
        client, _, service = await self.client_and_preview()
        for interaction in (self.interaction(admin=False), self.interaction(channel=999)):
            await client.manual_remind(interaction, app_commands.Choice(name="昼", value="lunch"))
            service.preview_reminder.assert_not_awaited()
            self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_initial_command_is_private_and_never_sends_before_confirmation(self):
        client, _, service = await self.client_and_preview()
        interaction = self.interaction()
        await client.manual_remind(interaction, app_commands.Choice(name="昼", value="lunch"))
        service.send_reminder.assert_not_awaited()
        reply = interaction.followup.send.call_args
        self.assertTrue(reply.kwargs["ephemeral"])
        self.assertEqual(reply.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertIn("全員", reply.args[0])
        self.assertIsInstance(reply.kwargs["view"], ReminderView)
        reply.kwargs["view"].stop()

    async def test_button_checks_current_permissions_and_original_user_and_sends_once(self):
        client, preview, service = await self.client_and_preview()
        view = ReminderView(client, 900, preview)
        try:
            for interaction in (self.interaction(admin=False), self.interaction(user=901), self.interaction(channel=999)):
                await view.confirm.callback(interaction)
            service.send_reminder.assert_not_awaited()
            interaction = self.interaction()
            await view.confirm.callback(interaction)
            await view.confirm.callback(interaction)
            service.send_reminder.assert_awaited_once_with(preview, "10000", 900)
            self.assertEqual(interaction.edit_original_response.call_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        finally:
            view.stop()

    async def test_cancel_or_timeout_never_sends(self):
        client, preview, service = await self.client_and_preview()
        view = ReminderView(client, 900, preview)
        await view.cancel.callback(self.interaction())
        await view.confirm.callback(self.interaction())
        service.send_reminder.assert_not_awaited()
        expired = ReminderView(client, 900, preview)
        expired.message = AsyncMock()
        await expired.on_timeout()
        expired.message.edit.assert_awaited_once()
        service.send_reminder.assert_not_awaited()
        expired.stop()
