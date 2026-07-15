"""Разовый интерактивный вход в Telegram (Telethon) — создаёт файл сессии.

Запуск (на машине с доступом к Telegram):
    cd backend && python -m app.telegram_login

Спросит номер телефона, код из Telegram и, при включённой 2FA, пароль. После
успешного входа создаётся <TELEGRAM_SESSION>.session, и опросы идут неинтерактивно.
"""
from __future__ import annotations

import asyncio

from .config import settings


async def _main() -> None:
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        raise SystemExit(
            "Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH в .env "
            "(получить: https://my.telegram.org/apps)"
        )
    try:
        from telethon import TelegramClient
    except ImportError:
        raise SystemExit("Установите telethon: pip install telethon")

    client = TelegramClient(
        settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash
    )
    await client.start()  # интерактивно: телефон → код → (2FA пароль)
    me = await client.get_me()
    uname = f"@{me.username}" if getattr(me, "username", None) else ""
    print(f"Успешный вход: {me.first_name} {uname}. Сессия: {settings.telegram_session}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
