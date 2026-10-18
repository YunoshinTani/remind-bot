"""同梱Python・ライブラリ・HTTPS・WebSocketを実機で確認する。投稿は行わない。"""
from __future__ import annotations

import asyncio
from datetime import datetime
import importlib
import os
from pathlib import Path
import platform
import ssl
import sys

ROOT = Path(__file__).resolve().parents[1]


async def main():
    print(f"Python: {platform.python_version()} / {platform.machine()}")
    print(f"libc: {' '.join(platform.libc_ver())}")
    print(f"TLS: {ssl.OPENSSL_VERSION}")
    for name in ("discord", "aiohttp", "aiohappyeyeballs", "aiosignal", "attrs", "frozenlist", "idna", "multidict", "propcache", "dotenv", "typing_extensions", "tzdata", "yarl", "certifi", "sqlite3"):
        importlib.import_module(name)
    print("LIBRARIES OK: 同梱ライブラリを読み込めました。")
    import aiohttp
    from dotenv import load_dotenv
    from remind_bot.calendar import Calendar
    from remind_bot.config import load_config
    from remind_bot.store import Store
    load_dotenv(ROOT / ".env")
    config = load_config(ROOT / "config.toml", live=True)
    if not os.environ.get("DISCORD_TOKEN", "").strip():
        raise ValueError(".envにBotトークンが設定されていません。")
    print("CONFIG OK: 設定と名簿を読み込めました。")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get("https://discord.com/api/v10/gateway") as response:
            response.raise_for_status()
            gateway = (await response.json())["url"]
        if gateway != "wss://gateway.discord.gg":
            raise ValueError("DiscordのGateway URLを確認できません。")
        print("HTTPS OK: Discord APIへ接続できました。")
        async with asyncio.timeout(20):
            async with session.ws_connect(gateway + "/?v=10&encoding=json") as websocket:
                hello = await websocket.receive_json(timeout=10)
                if hello.get("op") != 10:
                    raise ValueError("Discord Gatewayの応答を確認できません。")
        print("WEBSOCKET OK: Discordの常時接続を開始できました。")
    store = Store(":memory:")
    try:
        now = datetime.now(config.timezone)
        target = max(now.date(), config.start_date)
        schedule = await Calendar(config, store).get(target, now)
        print(f"TIMETABLE OK: {target} / {'登校日' if schedule.is_school_day else '授業なし'}")
    finally:
        store.close()
    print("確認完了。通知の送信・本番データベースの変更は行っていません。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # URLやトークンを含み得る通信例外の全文は表示しない。
        print(f"CHECK FAILED: {type(exc).__name__}。直前の確認項目で失敗しました。", file=sys.stderr)
        raise SystemExit(1)
