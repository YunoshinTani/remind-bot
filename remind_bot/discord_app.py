from __future__ import annotations

import logging
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date, datetime
from time import monotonic

import discord
from discord import app_commands
from discord.ext import tasks

from .calendar import Calendar
from .admin import ReminderView, is_admin
from .config import Config
from .members import resolve_pairs
from . import messages
from .discord_helpers import interaction_error, private_reply
from .service import DiaryService, Notification
from .schedule import ScheduleQuery
from .store import Store

log = logging.getLogger(__name__)


class CollectionView(discord.ui.View):
    def __init__(self, client: DiaryClient, *, disabled: bool = False):
        super().__init__(timeout=None)
        self.client = client
        self.collected.disabled = disabled
        if disabled:
            self.collected.label = "回収済み"

    @discord.ui.button(label="回収しました", style=discord.ButtonStyle.success, custom_id="class-diary:collected:v1")
    async def collected(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.client.in_target(interaction) or interaction.message is None:
            await private_reply(interaction, messages.COLLECTION_WRONG_CHANNEL)
            return
        day = self.client.store.day_for_message(interaction.message.id)
        if day is None:
            await private_reply(interaction, messages.MESSAGE_NOT_FOUND)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.client.service.collect(day, interaction.user.id, datetime.now(self.client.config.timezone))
        await private_reply(interaction, result, followup=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        log.error("回収ボタンの処理に失敗 (%s)", type(error).__name__)
        await interaction_error(interaction, messages.COLLECTION_ERROR)


class DiscordSender:
    def __init__(self, client: DiaryClient):
        self.client = client
        self.channel: discord.TextChannel | None = None

    async def ready(self):
        channel = await self.client.target_channel()
        member = channel.guild.me
        if member is None:
            raise ValueError("Bot のサーバー参加状態を確認できません。")
        permissions = channel.permissions_for(member)
        required = ("view_channel", "send_messages", "read_message_history", "mention_everyone")
        if any(not getattr(permissions, name) for name in required):
            raise PermissionError("Bot にチャンネル表示・送信・履歴閲覧・everyone メンション権限が必要です。")
        self.channel = channel
        await self.client.refresh_members()

    async def send(self, notification: Notification) -> int:
        if self.channel is None:
            raise RuntimeError("送信先が未確認です。")
        nonce_key = f"{self.channel.id}:{notification.day}:{notification.kind}"
        if notification.delivery_id is not None:
            nonce_key += f":manual:{notification.delivery_id}"
        message = await self.channel.send(
            notification.content,
            nonce=hashlib.sha256(nonce_key.encode()).hexdigest()[:24],
            allowed_mentions=discord.AllowedMentions(
                everyone=notification.everyone,
                users=[discord.Object(id=user_id) for user_id in notification.user_ids],
                roles=False, replied_user=False,
            ),
            view=CollectionView(self.client) if notification.collection_button else discord.utils.MISSING,
        )
        return message.id

    async def show_collected(self, day: date, message_ids: list[int]):
        await self.ready()
        channel = self.channel
        if channel is None:
            raise RuntimeError("送信先が未確認です。")
        row = self.client.store.assignment(day)
        if row is None or row["collected_at"] is None:
            raise ValueError("回収記録がありません。")
        stamp = datetime.fromisoformat(row["collected_at"])
        for message_id in message_ids:
            try:
                message = await channel.fetch_message(message_id)
                content = messages.show_collected(message.content, stamp, row["collected_by"])
                await message.edit(
                    content=content, view=CollectionView(self.client, disabled=True),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.NotFound:
                log.warning("回収表示の更新対象が削除されています: %s", message_id)


class DiaryClient(discord.Client):
    def __init__(self, config: Config, store: Store):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = config.resolve_members
        super().__init__(intents=intents, chunk_guilds_at_startup=False, member_cache_flags=discord.MemberCacheFlags.none(), allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.store = store
        self.tree = app_commands.CommandTree(self)
        self.service = DiaryService(config, store, Calendar(config, store), DiscordSender(self))
        self.schedules = ScheduleQuery(self.service.calendar)
        self.guild = discord.Object(id=config.guild_id)
        self.members_checked_at: float | None = None

    async def resolve_member_ids(self):
        if not self.config.resolve_members:
            return
        if self.members_checked_at is not None and monotonic() - self.members_checked_at < 300:
            return
        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            guild = await self.fetch_guild(self.config.guild_id)
        members = [member async for member in guild.fetch_members(limit=None)]
        result = resolve_pairs(self.config.pairs, members)
        self.config = replace(self.config, pairs=result.pairs)
        self.service.config = self.config
        self.members_checked_at = monotonic()
        for problem in result.problems:
            log.warning("メンバー照合: %s", problem)
        if not result.problems:
            log.info("名簿のDiscord ID照合が完了しました。")

    def in_target(self, interaction: discord.Interaction) -> bool:
        return interaction.guild_id == self.config.guild_id and interaction.channel_id == self.config.channel_id

    async def target_channel(self) -> discord.TextChannel:
        channel = self.get_channel(self.config.channel_id) or await self.fetch_channel(self.config.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("送信先はテキストチャンネルにしてください。")
        if self.config.guild_id and self.config.guild_id != channel.guild.id:
            raise ValueError("チャンネルとサーバーIDが一致しません。")
        return channel

    async def refresh_members(self):
        try:
            await self.resolve_member_ids()
        except (discord.HTTPException, discord.ClientException) as exc:
            # 照合障害だけでは、通知や保存済みIDでの照会を止めない。
            log.error("メンバー照合に失敗 (%s)。Server Members Intentと接続を確認してください。", type(exc).__name__)

    async def setup_hook(self):
        channel = await self.target_channel()
        self.config = replace(self.config, guild_id=channel.guild.id)
        self.service.config = self.config
        self.guild = discord.Object(id=channel.guild.id)
        self.tree.add_command(app_commands.Command(name="today", description="今日の日誌当番と回収状況を確認", callback=self.diary_status), guild=self.guild)
        self.tree.add_command(app_commands.Command(name="next", description="自分の次の日誌当番の日付と担当ペアを確認（今日を含む）", callback=self.my_turn), guild=self.guild)
        self.tree.add_command(app_commands.Command(name="remind", description="管理者用：朝または昼の日誌リマインドを手動送信", callback=self.manual_remind), guild=self.guild)
        self.tree.add_command(app_commands.Command(name="schedule", description="今日または指定日の時間割を確認", callback=self.schedule), guild=self.guild)
        self.add_view(CollectionView(self))
        await self.tree.sync(guild=self.guild)
        for row in self.store.uncertain_notifications():
            log.warning("前回の送信結果が不明です。自動再送しません: %s %s", row["day"], row["kind"])
        self.scheduler.change_interval(seconds=self.config.poll_seconds)
        self.scheduler.start()

    async def on_ready(self):
        if self.user is not None:
            log.info("Discord に接続しました: Bot ID %s", self.user.id)

    async def _query(self, interaction: discord.Interaction, lookup: Callable[[], Awaitable[str | tuple[str, ...]]], *,
                     context: str, failure: str, timeout: str | None = None):
        if not self.in_target(interaction):
            await private_reply(interaction, messages.WRONG_CHANNEL)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await lookup()
        except Exception as exc:
            log.error("%sに失敗 (%s)", context, type(exc).__name__)
            result = timeout if isinstance(exc, TimeoutError) and timeout is not None else failure
        for part in (result,) if isinstance(result, str) else result:
            await private_reply(interaction, part, followup=True)

    async def diary_status(self, interaction: discord.Interaction):
        await self._query(interaction, lambda: self.service.status(datetime.now(self.config.timezone)),
                          context="状況確認", failure=messages.STATUS_ERROR)

    async def my_turn(self, interaction: discord.Interaction):
        async def lookup():
            await self.refresh_members()
            return await self.service.next_turn(interaction.user.id, datetime.now(self.config.timezone))
        await self._query(interaction, lookup, context="担当日照会", failure=messages.FORECAST_ERROR,
                          timeout=messages.FORECAST_TIMEOUT)

    @app_commands.describe(date="日付（YYYY-MM-DD または YYYY/MM/DD。省略すると今日）")
    async def schedule(self, interaction: discord.Interaction, date: str | None = None):
        await self._query(interaction, lambda: self.schedules.lookup(date, datetime.now(self.config.timezone)),
                          context="時間割照会", failure=messages.SCHEDULE_ERROR, timeout=messages.SCHEDULE_TIMEOUT)

    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(kind="送信する通知を選んでください")
    @app_commands.choices(kind=[app_commands.Choice(name="朝の通知（担当者へのメンション）", value="morning"),
                               app_commands.Choice(name="昼の通知（全員へのメンション）", value="lunch")])
    async def manual_remind(self, interaction: discord.Interaction, kind: app_commands.Choice[str]):
        if not self.in_target(interaction):
            await private_reply(interaction, messages.WRONG_CHANNEL)
            return
        if not is_admin(interaction):
            await private_reply(interaction, messages.REMIND_ADMIN_ONLY)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            preview = await self.service.preview_reminder(kind.value)
        except Exception as exc:
            log.error("手動通知の準備に失敗 (%s)", type(exc).__name__)
            preview = messages.REMIND_ERROR
        if isinstance(preview, str):
            await private_reply(interaction, preview, followup=True)
            return
        view = ReminderView(self, interaction.user.id, preview)
        text = messages.reminder_preview(preview.notification.day, preview.notification.kind,
                                         preview.students, preview.stored_numbers, preview.notification.content)
        view.message = await interaction.followup.send(text, view=view, ephemeral=True,
                                                       allowed_mentions=discord.AllowedMentions.none(), wait=True)

    @tasks.loop(seconds=20)
    async def scheduler(self):
        if not self.is_ready():
            return
        try:
            await self.service.tick()
        except Exception as exc:
            # API 障害・欠けた日程・権限不足でもループを止めず、次回確認する。
            # 通信例外の URL に認証情報が含まれる場合を考慮し、型だけを記録。
            log.error("通知の判定を保留しました (%s)。check で設定・日程を確認してください。", type(exc).__name__)

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.wait_until_ready()

    async def close(self):
        self.scheduler.cancel()
        await super().close()
