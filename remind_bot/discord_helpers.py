"""Discordの非公開返信を一か所で扱う。"""
from __future__ import annotations

import discord


async def private_reply(interaction: discord.Interaction, content: str, *, followup: bool = False):
    send = interaction.followup.send if followup else interaction.response.send_message
    await send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def interaction_error(interaction: discord.Interaction, content: str):
    await private_reply(interaction, content, followup=interaction.response.is_done())
