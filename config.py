"""
Все настройки проекта, кроме секретных данных.
Секретные данные (токен, ID таблицы, список пользователей) хранятся в .env
и подгружаются автоматически через python-dotenv.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Переменная окружения {name} не задана в .env")
    return value


def _parse_allowed_user_ids(raw: str) -> frozenset[int]:
    return frozenset(int(item.strip()) for item in raw.split(",") if item.strip())


# --- Секретные данные из .env ---
BOT_TOKEN: str = _get_required_env("BOT_TOKEN")
SPREADSHEET_ID: str = _get_required_env("SPREADSHEET_ID")
ALLOWED_USER_IDS: frozenset[int] = _parse_allowed_user_ids(_get_required_env("ALLOWED_USER_IDS"))
GOOGLE_CREDENTIALS_PATH: str = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")

# --- Названия листов Google Sheets ---
SHEET_SETTINGS: str = "Настройки"
SHEET_OPERATIONS: str = "Операции"
SHEET_ACCOUNTS: str = "Счета"

# --- Диапазоны справочников на листе "Настройки" ---
EXPENSE_CATEGORIES_RANGE: str = "B2:B14"
INCOME_CATEGORIES_RANGE: str = "B16:B17"

# --- Диапазон счетов на листе "Счета" (первый столбец, без заголовка) ---
ACCOUNTS_COLUMN: str = "A"
ACCOUNTS_RANGE: str = "A2:A1000"

# --- Столбцы листа "Операции" в порядке записи ---
OPERATIONS_COLUMNS_COUNT: int = 6  # Дата, Тип, Категория, Счёт, Сумма, Комментарий
OPERATIONS_AMOUNT_COLUMN_INDEX: int = 5  # 1-based индекс столбца "Сумма" (E)

# --- Формат даты, который вводит пользователь ---
DATE_INPUT_FORMAT: str = "%d.%m.%Y"

# --- Формат числа для столбца "Сумма" (денежный, без десятичных знаков) ---
AMOUNT_NUMBER_FORMAT: str = "#,##0"

# --- Кеширование справочников (категории, счета) ---
CACHE_ENABLED: bool = True
CACHE_TTL_SECONDS: int = 300

# --- Типы операций ---
OPERATION_TYPE_INCOME: str = "Доход"
OPERATION_TYPE_EXPENSE: str = "Расход"

# --- Текстовые константы интерфейса ---
class Text:
    ACCESS_DENIED = "У вас нет доступа к этому боту."
    MENU_BUTTON = "Меню"
    ADD_OPERATION_BUTTON = "➕ Добавить операцию"
    CANCEL_BUTTON = "❌ Отмена"

    DATE_TODAY_BUTTON = "Сегодня"
    DATE_OTHER_BUTTON = "Другая дата"
    ASK_DATE = "Выберите дату операции:"
    ASK_DATE_MANUAL = "Введите дату в формате дд.мм.гггг:"
    INVALID_DATE = "Некорректная дата. Введите дату в формате дд.мм.гггг:"

    TYPE_INCOME_BUTTON = "Доход"
    TYPE_EXPENSE_BUTTON = "Расход"
    ASK_TYPE = "Выберите тип операции:"

    ASK_CATEGORY = "Выберите категорию:"
    NO_CATEGORIES = "Список категорий пуст. Обратитесь к администратору таблицы."

    ASK_ACCOUNT = "Выберите счёт:"
    NO_ACCOUNTS = "Список счетов пуст. Обратитесь к администратору таблицы."

    ASK_AMOUNT = "Введите сумму операции:"
    INVALID_AMOUNT = "Некорректная сумма. Введите число, например 100 или 100.50:"

    COMMENT_YES_BUTTON = "Да"
    COMMENT_NO_BUTTON = "Нет"
    ASK_COMMENT_CHOICE = "Добавить комментарий?"
    ASK_COMMENT_TEXT = "Введите комментарий:"

    OPERATION_SAVED = (
        "✅ Операция успешно добавлена.\n\n"
        "Дата: {date}\n"
        "Тип: {type}\n"
        "Категория: {category}\n"
        "Счёт: {account}\n"
        "Сумма: {amount}\n"
        "Комментарий: {comment}"
    )

    OPERATION_CANCELLED = "Операция отменена."
    SESSION_EXPIRED = "Сессия устарела. Начните заново, нажав «Меню»."
    UNEXPECTED_ERROR = "Произошла ошибка. Попробуйте начать заново, нажав «Меню»."
    SHEETS_ERROR = "Не удалось связаться с Google Sheets. Попробуйте позже."
    WELCOME = "Бот запущен. Нажмите «Меню», чтобы начать."


# --- Префиксы callback_data (чтобы не хардкодить строки в handlers.py) ---
class Callback:
    ADD_OPERATION = "add_operation"
    CANCEL = "cancel"

    DATE_TODAY = "date_today"
    DATE_OTHER = "date_other"

    TYPE_INCOME = "type_income"
    TYPE_EXPENSE = "type_expense"

    CATEGORY_PREFIX = "category:"
    ACCOUNT_PREFIX = "account:"

    COMMENT_YES = "comment_yes"
    COMMENT_NO = "comment_no"
