from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from .calendar import Calendar, CalendarError
from .config import ConfigError, load_config
from .service import DiaryService
from .store import Store


async def inspect_config(config, target: date, *, preview: bool):
    store = Store(":memory:")
    try:
        now = datetime.now(config.timezone)
        service = DiaryService(config, store, Calendar(config, store))
        plan = await service.prepare(target, now)
        if plan is None:
            print("指定日は運用開始日前です。")
            return
        if not plan.school_day.is_school_day:
            print(f"{target}: 休校日（通知なし）")
            return
        numbers = "・".join(str(s.number) for s in plan.students)
        print(f"{target}: 登校日 / 担当 {numbers} 番")
        if config.resolve_members:
            print("個人メンションのIDは、members コマンドまたは本番起動時にDiscordから照合します。")
        if preview:
            for kind, clock in [("morning", plan.school_day.morning_time), ("lunch", plan.school_day.lunch_time), ("dismissal", plan.school_day.dismissal_time)]:
                if kind == "dismissal" and not config.dismissal_enabled:
                    continue
                print(f"\n[{kind} {clock:%H:%M}]" + (" ※未回収の場合のみ" if kind == "lunch" else ""))
                print(service.notification(plan, kind).content)
        print("確認完了。Discord への送信・本番の送信記録の変更は行っていません。")
    finally:
        store.close()


async def inspect_members(config, token: str, *, save: bool = False):
    import discord
    from .members import resolve_pairs, save_member_matches
    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = True
    async with discord.Client(intents=intents) as client:
        await client.login(token)
        channel = await client.fetch_channel(config.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ConfigError("送信先はテキストチャンネルにしてください。")
        if config.guild_id and config.guild_id != channel.guild.id:
            raise ConfigError("チャンネルとサーバーIDが一致しません。")
        guild = await client.fetch_guild(channel.guild.id)
        members = [member async for member in guild.fetch_members(limit=None)]
        result = resolve_pairs(config.pairs, members)
        print(f"サーバーID: {guild.id}")
        for pair in result.pairs:
            for student in pair.students:
                print(f"{student.number:02d}番: " + (f"ID {student.discord_id}" if student.discord_id else "未解決"))
        for problem in result.problems:
            print(problem)
        if save:
            if config.roster_file is None:
                raise ConfigError("保存先の名簿が指定されていません。")
            saved = save_member_matches(config.roster_file, result, members, backup_dir=config.path.parent / "backups", now=datetime.now(config.timezone))
            print(f"名簿を更新しました: {saved.changed}人。氏名・在籍状態・未解決の行は保持しています。")
            if saved.backup is not None:
                print(f"更新前のバックアップ: {saved.backup}")
        if result.problems:
            raise ConfigError(("照合できたメンバーの保存は完了しました。" if save else "") + "照合できないメンバーがいます。サーバーの表示名・名簿を確認してください。")
        print("全員の照合が完了しました。Discordへの通知は送っていません。")


def main():
    parser = argparse.ArgumentParser(description="Discord クラス日誌リマインダー")
    parser.add_argument("command", choices=("check", "preview", "members", "test", "run"))
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--date", type=date.fromisoformat, help="確認する日付 YYYY-MM-DD")
    parser.add_argument("--duration", type=int, default=300, help="動作テストでボタンを待つ秒数（1〜900、既定300）")
    parser.add_argument("--save", action="store_true", help="members で照合できたユーザーID・表示名を名簿CSVに保存")
    args = parser.parse_args()
    if not 1 <= args.duration <= 900:
        parser.error("--duration は1〜900秒で指定してください。")
    if args.save and args.command != "members":
        parser.error("--save は members コマンドでのみ使えます。")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # ライブラリーの通信ログにトークン等を出さない。
    logging.getLogger("discord").setLevel(logging.WARNING)
    try:
        if not args.config.is_file():
            raise ConfigError("設定ファイルがありません。本番の操作は学校サーバーで実行してください。"
                              "PCでの表示確認は preview --config examples/config.demo.toml を使います。")
        config = load_config(args.config, live=args.command in {"check", "members", "test", "run"})
        load_dotenv(args.config.resolve().parent / ".env")
        if args.command in {"check", "preview"}:
            asyncio.run(inspect_config(config, args.date or datetime.now(config.timezone).date(), preview=args.command == "preview"))
            return
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigError(".env の DISCORD_TOKEN を設定してください。")
        if args.command == "members":
            asyncio.run(inspect_members(config, token, save=args.save))
            return
        if args.command == "test":
            from .smoke_test import SmokeTestClient
            client = SmokeTestClient(config, args.duration)
            client.run(token, log_handler=None)
            if client.failure:
                raise ConfigError(client.failure)
            return
        from .discord_app import DiaryClient
        store = Store(config.database)
        try:
            DiaryClient(config, store).run(token, log_handler=None)
        finally:
            store.close()
    except (ConfigError, CalendarError, OSError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"エラー: {type(exc).__name__}。設定・接続状態を確認してください。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
