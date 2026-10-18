from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands
from remind_bot.calendar import Calendar
from remind_bot.config import Pair, Student
from remind_bot.discord_app import CollectionView, DiaryClient, DiscordSender
from remind_bot.service import DiaryService
from remind_bot.smoke_test import SmokeTestClient, TEST_CONTENT

from tests.helpers import DiaryFixture, START, stamp


class DiscordTests(DiaryFixture):
    async def test_persistent_button_and_mentions_in_discord_adapter(self):
        client = DiaryClient(self.config, self.store)
        view = CollectionView(client)
        self.assertTrue(view.is_persistent())
        client.add_view(view)
        self.assertTrue(any(v.is_persistent() for v in client.persistent_views))
        sender = DiscordSender(client)
        sender.channel = AsyncMock()
        sender.channel.send.return_value.id = 321
        plan = await self.service.prepare(START, stamp())
        assert plan is not None
        await sender.send(self.service.notification(plan, "morning"))
        morning = sender.channel.send.call_args.kwargs
        self.assertEqual(morning["allowed_mentions"].to_dict(), {"users": [100000000000000032], "parse": []})
        await sender.send(self.service.notification(plan, "lunch"))
        lunch = sender.channel.send.call_args.kwargs
        self.assertEqual(lunch["allowed_mentions"].to_dict(), {"users": [], "parse": ["everyone"]})
        self.assertTrue(lunch["view"].is_persistent())
        await client.close()

    async def test_collection_edit_does_not_ping_again(self):
        await self.service.tick(stamp(clock="08:10"))
        self.store.collect(START, 777, stamp(clock="09:00"))
        client = DiaryClient(self.config, self.store)
        sender = DiscordSender(client)
        sender.ready = AsyncMock()
        sender.channel = AsyncMock()
        message = AsyncMock()
        message.content = "@everyone 日誌を回収してください"
        sender.channel.fetch_message.return_value = message
        await sender.show_collected(START, [1001])
        kwargs = message.edit.call_args.kwargs
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertTrue(kwargs["view"].collected.disabled)
        self.assertIn("記録者：<@777>", kwargs["content"])
        self.assertIn("記録時刻：**09:00**", kwargs["content"])
        # 後から別の人が押しても、表示には保存済みの最初の記録者を使う。
        self.assertFalse(self.store.collect(START, 888, stamp(clock="09:05")))
        # 新しい書式と旧版の投稿のどちらも、再押下で回収表示を重ねない。
        for already_collected in (kwargs["content"], "元の通知\n回収状況：**✅回収済み**\n記録時刻：**09:00**",
                                  "元の通知\n\n✅ 回収済み（09:00）",
                                  "元の通知\n\n✅ **回収済み**\n\n記録時刻：**09:00**"):
            message.content = already_collected
            await sender.show_collected(START, [1001])
            updated = message.edit.call_args.kwargs["content"]
            expected = already_collected if "\n記録者：" in already_collected else already_collected + "\n記録者：<@777>"
            self.assertEqual(updated, expected)
            self.assertEqual(updated.count("記録者："), 1)
            self.assertNotIn("<@888>", updated)
            self.assertEqual(message.edit.call_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        await client.close()

    async def test_mixed_pair_discord_payload_only_allows_resolved_user(self):
        known_id = 100000000000000001
        config = replace(self.config, first_pair=1, pairs=(Pair(1, (Student(1, known_id, True, "山田太郎"), Student(2, None, True, "山田花子"))),))
        service = DiaryService(config, self.store, Calendar(config, self.store))
        plan = await service.prepare(START, stamp())
        assert plan is not None
        client = DiaryClient(config, self.store)
        sender = DiscordSender(client)
        sender.channel = AsyncMock()
        sender.channel.send.return_value.id = 321
        await sender.send(service.notification(plan, "morning"))
        args = sender.channel.send.call_args
        self.assertIn("2番 山田花子", args.args[0])
        self.assertEqual(args.kwargs["allowed_mentions"].to_dict(), {"users": [known_id], "parse": []})
        await client.close()

    async def test_channel_id_discovers_guild_before_registering_commands(self):
        client = DiaryClient(replace(self.config, guild_id=0), self.store)
        channel = MagicMock(spec=discord.TextChannel)
        channel.guild.id = 987654321
        client.fetch_channel = AsyncMock(return_value=channel)
        client.tree.sync = AsyncMock(return_value=[])
        with patch.object(client.scheduler, "start"):
            await client.setup_hook()
        self.assertEqual(client.config.guild_id, 987654321)
        self.assertEqual(client.service.config.guild_id, 987654321)
        registered = client.tree.get_commands(guild=client.guild)
        commands = {command.name: command for command in registered if isinstance(command, app_commands.Command)}
        self.assertEqual(len(commands), len(registered))
        self.assertEqual(set(commands), {"today", "next", "remind", "schedule"})
        self.assertIs(commands["today"].callback, DiaryClient.diary_status)
        self.assertIs(commands["next"].callback, DiaryClient.my_turn)
        permissions = commands["remind"].default_permissions
        assert permissions is not None
        self.assertTrue(permissions.administrator)
        self.assertEqual({choice.value for choice in commands["remind"].parameters[0].choices}, {"morning", "lunch"})
        self.assertIs(commands["schedule"].callback, DiaryClient.schedule)
        self.assertIsNone(commands["schedule"].default_permissions)
        self.assertEqual(commands["schedule"].parameters[0].name, "date")
        self.assertFalse(commands["schedule"].parameters[0].required)
        client.tree.sync.assert_awaited_once_with(guild=client.guild)
        await client.close()

    async def test_smoke_message_and_button_do_not_notify_or_change_production_records(self):
        client = SmokeTestClient(self.config, duration=10)
        client._connection.user = MagicMock(spec=discord.ClientUser, id=900)
        channel = MagicMock(spec=discord.TextChannel)
        channel.guild.id = self.config.guild_id
        channel.guild.me = SimpleNamespace(id=900)
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True, read_message_history=True, mention_everyone=True)
        message = MagicMock()
        message.jump_url = "https://discord.com/channels/test/test/test"
        message.author.id = 900
        message.content = TEST_CONTENT
        channel.send = AsyncMock(return_value=message)
        channel.fetch_message = AsyncMock(return_value=message)
        client.get_channel = MagicMock(return_value=channel)
        before = self.store.connection.total_changes
        await client.on_ready()
        assert client.view is not None
        assert client.expiry_task is not None
        payload = channel.send.call_args.kwargs
        self.assertEqual(payload["allowed_mentions"].to_dict(), {"parse": []})
        self.assertTrue(payload["silent"])
        interaction = AsyncMock()
        interaction.channel_id = self.config.channel_id
        interaction.guild_id = self.config.guild_id
        await client.view.collect_test.callback(interaction)
        await client.expiry_task
        self.assertTrue(client.clicked.is_set())
        self.assertTrue(client.view.collect_test.disabled)
        self.assertEqual(interaction.response.edit_message.call_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertEqual(self.store.connection.total_changes, before)
        self.assertTrue(client.is_closed())
