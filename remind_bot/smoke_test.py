from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import discord

from .config import Config
from . import messages
from .discord_helpers import interaction_error, private_reply

log = logging.getLogger(__name__)

TEST_CONTENT = messages.SMOKE_PROMPT


class SmokeTestView(discord.ui.View):
    def __init__(self, client: SmokeTestClient):
        super().__init__(timeout=None)
        self.client = client

    @discord.ui.button(label="回収テスト", style=discord.ButtonStyle.success, custom_id="class-diary:smoke-test:v1")
    async def collect_test(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.channel_id != self.client.config.channel_id or interaction.guild_id != self.client.guild_id:
            await private_reply(interaction, messages.SMOKE_WRONG_CHANNEL)
            return
        async with self.client.click_lock:
            if self.client.clicked.is_set():
                await private_reply(interaction, messages.SMOKE_FINISHED)
                return
            stamp = datetime.now(self.client.config.timezone)
            button.disabled = True
            button.label = "テスト完了"
            try:
                await interaction.response.edit_message(
                    content=TEST_CONTENT + "\n" + messages.smoke_success(stamp),
                    view=self, allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                button.disabled = False
                button.label = "回収テスト"
                raise
            print("BUTTON OK: 回収テストの受信とメッセージ更新に成功しました。", flush=True)
            self.client.clicked.set()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        log.error("回収テストの処理に失敗 (%s)", type(error).__name__)
        await interaction_error(interaction, messages.SMOKE_ERROR)


class SmokeTestClient(discord.Client):
    """通知予定・当番DB・本番コマンドを使わず、実際の送受信だけを検証する。"""

    def __init__(self, config: Config, duration: int = 300):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.duration = duration
        self.guild_id = config.guild_id
        self.clicked = asyncio.Event()
        self.click_lock = asyncio.Lock()
        self.started_test = False
        self.failure: str | None = None
        self.expiry_task: asyncio.Task | None = None
        self.message: discord.Message | None = None
        self.view: SmokeTestView | None = None

    async def on_ready(self):
        # 再接続で同じテストを重複投稿しない。
        if self.started_test:
            return
        self.started_test = True
        try:
            user = self.user
            if user is None:
                raise RuntimeError("Botの接続状態を確認できません。")
            channel = self.get_channel(self.config.channel_id) or await self.fetch_channel(self.config.channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError("送信先がテキストチャンネルではありません。")
            if self.guild_id and self.guild_id != channel.guild.id:
                raise ValueError("チャンネルとサーバーIDが一致しません。")
            self.guild_id = channel.guild.id
            member = channel.guild.me or await channel.guild.fetch_member(user.id)
            permissions = channel.permissions_for(member)
            for name in ("view_channel", "send_messages", "read_message_history", "mention_everyone"):
                print(f"PERMISSION {name}: {'OK' if getattr(permissions, name) else '不足'}", flush=True)
            self.view = SmokeTestView(self)
            self.message = await channel.send(
                TEST_CONTENT, view=self.view,
                allowed_mentions=discord.AllowedMentions.none(), silent=True,
            )
            print(f"MESSAGE OK: {self.message.jump_url}", flush=True)
            if permissions.read_message_history:
                read_back = await channel.fetch_message(self.message.id)
                if read_back.author.id != user.id or read_back.content != TEST_CONTENT:
                    raise ValueError("投稿内容の読み戻しが一致しません。")
                print("HISTORY OK: 投稿したメッセージを取得できました。", flush=True)
            print(f"ボタン操作を最大{self.duration}秒待ちます。本番の当番・回収記録は変更しません。", flush=True)
            self.expiry_task = asyncio.create_task(self.finish_test())
        except Exception as exc:
            self.failure = f"テスト接続または投稿に失敗しました ({type(exc).__name__})"
            log.error(self.failure)
            await self.close()

    async def finish_test(self):
        try:
            await asyncio.wait_for(self.clicked.wait(), timeout=self.duration)
        except asyncio.TimeoutError:
            print("BUTTON TIMEOUT: 投稿は成功しましたが、ボタンの操作は確認できませんでした。", flush=True)
            if self.message is not None and self.view is not None:
                self.view.collect_test.disabled = True
                self.view.collect_test.label = "テスト終了"
                try:
                    await self.message.edit(
                        content=TEST_CONTENT + "\n" + messages.SMOKE_EXPIRED,
                        view=self.view, allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException as exc:
                    log.warning("テスト終了表示の更新に失敗 (%s)", type(exc).__name__)
        finally:
            await self.close()

    async def close(self):
        if self.expiry_task is not None and self.expiry_task is not asyncio.current_task():
            self.expiry_task.cancel()
        await super().close()
