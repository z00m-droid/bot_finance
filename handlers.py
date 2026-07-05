"""
Обработчики сообщений и callback-запросов бота.
Вся работа с Google Sheets вызывается через google_sheets.sheets_client,
все клавиатуры берутся из keyboards.py, все состояния — из states.py.

Регистрация происходит через register_handlers(bot), внутри которой
декораторы require_access и safe_handler замыкают конкретный экземпляр бота.
"""
from __future__ import annotations

import datetime
import functools
import logging
from typing import Callable

from telebot import TeleBot
from telebot.types import CallbackQuery, Message

import keyboards
from config import ALLOWED_USER_IDS, Callback, OPERATION_TYPE_EXPENSE, OPERATION_TYPE_INCOME, Text
from google_sheets import GoogleSheetsError, sheets_client
from states import OperationDraft, UserState, session_storage

logger = logging.getLogger(__name__)

Event = Message | CallbackQuery


def _extract_user_id(event: Event) -> int:
    return event.from_user.id


def _extract_chat_id(event: Event) -> int:
    if isinstance(event, CallbackQuery):
        return event.message.chat.id
    return event.chat.id


def _parse_amount(raw_text: str) -> float:
    normalized = raw_text.strip().replace(",", ".")
    amount = float(normalized)
    if amount <= 0:
        raise ValueError("Сумма должна быть положительным числом")
    return amount


def _parse_date(raw_text: str) -> datetime.date:
    from config import DATE_INPUT_FORMAT

    return datetime.datetime.strptime(raw_text.strip(), DATE_INPUT_FORMAT).date()


def _format_amount_for_display(amount: float) -> str:
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.2f}"


def register_handlers(bot: TeleBot) -> None:

    def require_access(handler: Callable[[Event], None]) -> Callable[[Event], None]:
        @functools.wraps(handler)
        def wrapper(event: Event) -> None:
            user_id = _extract_user_id(event)
            if user_id not in ALLOWED_USER_IDS:
                bot.send_message(_extract_chat_id(event), Text.ACCESS_DENIED)
                return
            handler(event)

        return wrapper

    def safe_handler(handler: Callable[[Event], None]) -> Callable[[Event], None]:
        @functools.wraps(handler)
        def wrapper(event: Event) -> None:
            user_id = _extract_user_id(event)
            chat_id = _extract_chat_id(event)
            try:
                handler(event)
            except GoogleSheetsError as exc:
                logger.error("Google Sheets error for user %s: %s", user_id, exc)
                bot.send_message(chat_id, Text.SHEETS_ERROR)
                session_storage.clear(user_id)
            except Exception:
                logger.exception("Unexpected error for user %s", user_id)
                bot.send_message(chat_id, Text.UNEXPECTED_ERROR)
                session_storage.clear(user_id)

        return wrapper

    def finalize_operation(user_id: int, chat_id: int, message_id: int | None) -> None:
        session = session_storage.get(user_id)
        draft: OperationDraft = session.draft

        sheets_client.append_operation(
            date=draft.date,
            operation_type=draft.operation_type,
            category=draft.category,
            account=draft.account,
            amount=draft.amount,
            comment=draft.comment,
        )

        confirmation = Text.OPERATION_SAVED.format(
            date=draft.date.strftime("%d.%m.%Y"),
            type=draft.operation_type,
            category=draft.category,
            account=draft.account,
            amount=_format_amount_for_display(draft.amount),
            comment=draft.comment if draft.comment else "—",
        )

        session_storage.clear(user_id)

        if message_id is not None:
            bot.edit_message_text(confirmation, chat_id, message_id)
        else:
            bot.send_message(chat_id, confirmation)

    @bot.message_handler(commands=["start"])
    @require_access
    def handle_start(message: Message) -> None:
        session_storage.reset(message.from_user.id)
        bot.send_message(message.chat.id, Text.WELCOME, reply_markup=keyboards.main_reply_keyboard())

    @bot.message_handler(func=lambda message: message.text == Text.MENU_BUTTON)
    @require_access
    def handle_menu_button(message: Message) -> None:
        session_storage.reset(message.from_user.id)
        bot.send_message(message.chat.id, "Главное меню:", reply_markup=keyboards.main_menu_inline_keyboard())

    @bot.callback_query_handler(func=lambda call: call.data == Callback.ADD_OPERATION)
    @require_access
    @safe_handler
    def handle_add_operation(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        session = session_storage.reset(user_id)
        session.state = UserState.WAITING_DATE_CHOICE
        bot.edit_message_text(
            Text.ASK_DATE,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.date_choice_keyboard(),
        )

    @bot.callback_query_handler(func=lambda call: call.data == Callback.CANCEL)
    @require_access
    def handle_cancel(call: CallbackQuery) -> None:
        session_storage.reset(call.from_user.id)
        bot.edit_message_text(
            Text.OPERATION_CANCELLED,
            call.message.chat.id,
            call.message.message_id,
        )

    @bot.callback_query_handler(func=lambda call: call.data == Callback.DATE_TODAY)
    @require_access
    @safe_handler
    def handle_date_today(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        if session.state != UserState.WAITING_DATE_CHOICE:
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            return
        session.draft.date = datetime.date.today()
        session.state = UserState.WAITING_TYPE
        bot.edit_message_text(
            Text.ASK_TYPE,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.operation_type_keyboard(),
        )

    @bot.callback_query_handler(func=lambda call: call.data == Callback.DATE_OTHER)
    @require_access
    @safe_handler
    def handle_date_other(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        if session.state != UserState.WAITING_DATE_CHOICE:
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            return
        session.state = UserState.WAITING_DATE_INPUT
        bot.edit_message_text(
            Text.ASK_DATE_MANUAL,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.cancel_only_keyboard(),
        )

    @bot.message_handler(
        func=lambda message: session_storage.get(message.from_user.id).state == UserState.WAITING_DATE_INPUT
    )
    @require_access
    @safe_handler
    def handle_date_input(message: Message) -> None:
        session = session_storage.get(message.from_user.id)
        try:
            session.draft.date = _parse_date(message.text)
        except ValueError:
            bot.send_message(message.chat.id, Text.INVALID_DATE, reply_markup=keyboards.cancel_only_keyboard())
            return
        session.state = UserState.WAITING_TYPE
        bot.send_message(message.chat.id, Text.ASK_TYPE, reply_markup=keyboards.operation_type_keyboard())

    @bot.callback_query_handler(func=lambda call: call.data in (Callback.TYPE_INCOME, Callback.TYPE_EXPENSE))
    @require_access
    @safe_handler
    def handle_type_choice(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        if session.state != UserState.WAITING_TYPE:
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            return

        if call.data == Callback.TYPE_INCOME:
            session.draft.operation_type = OPERATION_TYPE_INCOME
            categories = sheets_client.get_income_categories()
        else:
            session.draft.operation_type = OPERATION_TYPE_EXPENSE
            categories = sheets_client.get_expense_categories()

        if not categories:
            bot.edit_message_text(Text.NO_CATEGORIES, call.message.chat.id, call.message.message_id)
            session_storage.clear(call.from_user.id)
            return

        session.pending_options = categories
        session.state = UserState.WAITING_CATEGORY
        bot.edit_message_text(
            Text.ASK_CATEGORY,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.options_keyboard(categories, Callback.CATEGORY_PREFIX),
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith(Callback.CATEGORY_PREFIX))
    @require_access
    @safe_handler
    def handle_category_choice(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        if session.state != UserState.WAITING_CATEGORY:
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            return

        index = int(call.data.removeprefix(Callback.CATEGORY_PREFIX))
        if index < 0 or index >= len(session.pending_options):
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            session_storage.clear(call.from_user.id)
            return
        session.draft.category = session.pending_options[index]

        accounts = sheets_client.get_accounts()
        if not accounts:
            bot.edit_message_text(Text.NO_ACCOUNTS, call.message.chat.id, call.message.message_id)
            session_storage.clear(call.from_user.id)
            return

        session.pending_options = accounts
        session.state = UserState.WAITING_ACCOUNT
        bot.edit_message_text(
            Text.ASK_ACCOUNT,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.options_keyboard(accounts, Callback.ACCOUNT_PREFIX),
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith(Callback.ACCOUNT_PREFIX))
    @require_access
    @safe_handler
    def handle_account_choice(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        if session.state != UserState.WAITING_ACCOUNT:
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            return

        index = int(call.data.removeprefix(Callback.ACCOUNT_PREFIX))
        if index < 0 or index >= len(session.pending_options):
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            session_storage.clear(call.from_user.id)
            return
        session.draft.account = session.pending_options[index]
        session.pending_options = []
        session.state = UserState.WAITING_AMOUNT
        bot.edit_message_text(
            Text.ASK_AMOUNT,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.cancel_only_keyboard(),
        )

    @bot.message_handler(
        func=lambda message: session_storage.get(message.from_user.id).state == UserState.WAITING_AMOUNT
    )
    @require_access
    @safe_handler
    def handle_amount_input(message: Message) -> None:
        session = session_storage.get(message.from_user.id)
        try:
            amount = _parse_amount(message.text)
        except ValueError:
            bot.send_message(message.chat.id, Text.INVALID_AMOUNT, reply_markup=keyboards.cancel_only_keyboard())
            return
        session.draft.amount = amount
        session.state = UserState.WAITING_COMMENT_CHOICE
        bot.send_message(message.chat.id, Text.ASK_COMMENT_CHOICE, reply_markup=keyboards.comment_choice_keyboard())

    @bot.callback_query_handler(func=lambda call: call.data == Callback.COMMENT_NO)
    @require_access
    @safe_handler
    def handle_comment_no(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        if session.state != UserState.WAITING_COMMENT_CHOICE:
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            return
        session.draft.comment = ""
        finalize_operation(call.from_user.id, call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda call: call.data == Callback.COMMENT_YES)
    @require_access
    @safe_handler
    def handle_comment_yes(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        if session.state != UserState.WAITING_COMMENT_CHOICE:
            bot.edit_message_text(Text.SESSION_EXPIRED, call.message.chat.id, call.message.message_id)
            return
        session.state = UserState.WAITING_COMMENT_INPUT
        bot.edit_message_text(
            Text.ASK_COMMENT_TEXT,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.cancel_only_keyboard(),
        )

    @bot.message_handler(
        func=lambda message: session_storage.get(message.from_user.id).state == UserState.WAITING_COMMENT_INPUT
    )
    @require_access
    @safe_handler
    def handle_comment_input(message: Message) -> None:
        session = session_storage.get(message.from_user.id)
        session.draft.comment = message.text.strip()
        finalize_operation(message.from_user.id, message.chat.id, None)
