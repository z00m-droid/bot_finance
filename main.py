"""
Точка входа в приложение.
Инициализирует бота, регистрирует обработчики и запускает polling.
"""
from __future__ import annotations

import logging

from telebot import TeleBot
from telebot.apihelper import ApiTelegramException

import config
from handlers import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def create_bot() -> TeleBot:
    bot = TeleBot(config.BOT_TOKEN, parse_mode=None)
    register_handlers(bot)
    return bot


def main() -> None:
    bot = create_bot()
    logger.info("Бот запущен и готов к работе.")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except ApiTelegramException as exc:
            logger.error("Ошибка Telegram API, перезапуск polling: %s", exc)
        except Exception:
            logger.exception("Непредвиденная ошибка, перезапуск polling через несколько секунд.")


if __name__ == "__main__":
    main()
