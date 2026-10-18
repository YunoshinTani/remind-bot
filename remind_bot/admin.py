"""管理者の手動通知。確認画面と送信後の結果は本人だけに表示する。"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from . import messages
from .discord_helpers import interaction_error, private_reply
from .service import ReminderPreview

if TYPE_CHECKING:
    from .discord_app import DiaryClient

log = logging.getLogger(__name__)


def is_admin(interaction: discord.Interaction) -> bool:
    return isinstance(interaction.permissions, discord.Permissions) and interaction.permissions.administrator


class ReminderView(discord.ui.View):
    def __init__(self, client: DiaryClient, user_id: int, preview: ReminderPreview):
        super().__init__(timeout=180)
        self.client = client
        self.user_id = user_id
        self.preview = preview
        self.message: discord.WebhookMessage | None = None
        self.lock = asyncio.Lock()
        self.finished = False
        if preview.numbers != preview.stored_numbers:
            self.confirm.label = "担当を訂正して送信"
        if preview.notification.everyone or preview.numbers != preview.stored_numbers:
            self.confirm.style = discord.ButtonStyle.danger

    async def allowed(self, interaction: discord.Interaction) -> bool:
        if not self.client.in_target(interaction):
            await private_reply(interaction, messages.WRONG_CHANNEL)
        elif not is_admin(interaction):
            await private_reply(interaction, messages.REMIND_ADMIN_ONLY)
        elif interaction.user.id != self.user_id:
            await private_reply(interaction, messages.REMIND_OTHER_USER)
        else:
            return True
        return False

    @discord.ui.button(label="送信する", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.allowed(interaction):
            return
        async with self.lock:
            if self.finished:
                await private_reply(interaction, messages.REMIND_PROCESSED)
                return
            self.finished = True
            self.confirm.disabled = True
            self.cancel.disabled = True
            await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())
            try:
                result = await self.client.service.send_reminder(self.preview, str(interaction.id), self.user_id)
            except Exception as exc:
                log.error("手動通知に失敗 (%s)", type(exc).__name__)
                result = messages.REMIND_ERROR
            await interaction.edit_original_response(content=result, view=None,
                                                     allowed_mentions=discord.AllowedMentions.none())
            self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.allowed(interaction):
            return
        async with self.lock:
            if self.finished:
                await private_reply(interaction, messages.REMIND_PROCESSED)
                return
            self.finished = True
            await interaction.response.edit_message(content=messages.REMIND_CANCELLED, view=None,
                                                    allowed_mentions=discord.AllowedMentions.none())
            self.stop()

    async def on_timeout(self):
        async with self.lock:
            if not self.finished and self.message is not None:
                self.finished = True
                try:
                    await self.message.edit(content=messages.REMIND_EXPIRED, view=None,
                                            allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        log.error("手動通知の確認操作に失敗 (%s)", type(error).__name__)
        await interaction_error(interaction, messages.REMIND_ERROR)
