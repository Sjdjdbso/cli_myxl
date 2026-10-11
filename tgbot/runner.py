import logging
import os

from tgbot.bot import TelegramBot


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not token or not allowed_user_id:
        raise SystemExit(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID in .env before starting the bot."
        )
    try:
        allowed_user_id = int(allowed_user_id)
    except ValueError as error:
        raise SystemExit("TELEGRAM_ALLOWED_USER_ID must be a Telegram numeric user ID.") from error

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    TelegramBot(token, allowed_user_id).poll()

