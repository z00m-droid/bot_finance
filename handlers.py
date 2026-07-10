"""
Обработчики сообщений и callback-запросов бота.
Вся работа с Google Sheets вызывается через google_sheets.sheets_client,
все клавиатуры берутся из keyboards.py, все состояния — из states.py.

Регистрация происходит через register_handlers(bot), внутри которой
декораторы require_access и safe_handler замыкают конкретный экземпляр бота.

Сценарии "Добавить операцию" и "Транзит" почти идентичны по шагам
(дата → счета/категория → сумма → комментарий), поэтому там, где это не
ухудшает читаемость, обработчики шагов общие для обоих сценариев и
различают контекст по текущему UserState.
"""
from __future__ import annotations

import datetime
import functools
import logging
import math
import time
from typing import Callable

from telebot import TeleBot
from telebot.types import CallbackQuery, Message

import config
import keyboards
from config import ALLOWED_USER_IDS, Callback, OPERATION_TYPE_EXPENSE, OPERATION_TYPE_INCOME, Text
from google_sheets import GoogleSheetsError, sheets_client
from states import OperationDraft, TransitDraft, UserState, session_storage

logger = logging.getLogger(__name__)

Event = Message | CallbackQuery

_DATE_CHOICE_STATES = (UserState.WAITING_DATE_CHOICE, UserState.WAITING_TRANSIT_DATE_CHOICE)
_DATE_INPUT_STATES = (UserState.WAITING_DATE_INPUT, UserState.WAITING_TRANSIT_DATE_INPUT)
_AMOUNT_STATES = (UserState.WAITING_AMOUNT, UserState.WAITING_TRANSIT_AMOUNT)
_COMMENT_CHOICE_STATES = (UserState.WAITING_COMMENT_CHOICE, UserState.WAITING_TRANSIT_COMMENT_CHOICE)
_COMMENT_INPUT_STATES = (UserState.WAITING_COMMENT_INPUT, UserState.WAITING_TRANSIT_COMMENT_INPUT)


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
    return datetime.datetime.strptime(raw_text.strip(), config.DATE_INPUT_FORMAT).date()


def _format_amount_for_display(amount: float) -> str:
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.2f}"


def _compute_roundup_amount(amount: float, step: float) -> float:
    """Сумма, которую нужно доложить в копилку, чтобы округлить покупку вверх до кратного step."""
    if step <= 0:
        return 0.0
    multiples = math.ceil(round(amount / step, 6))
    roundup = round(multiples * step - amount, 2)
    return roundup if roundup > 0.004 else 0.0


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

    def show_main_menu(chat_id: int) -> None:
        bot.send_message(chat_id, Text.MAIN_MENU_PROMPT, reply_markup=keyboards.main_menu_inline_keyboard())

    def apply_autoround_if_needed(draft: OperationDraft) -> str:
        """Для расходных операций проверяет копилку счёта и при необходимости создаёт запись в "Транзит"."""
        if draft.operation_type != OPERATION_TYPE_EXPENSE:
            return ""

        roundup_settings = sheets_client.get_roundup_settings().get(draft.account)
        if roundup_settings is None or not roundup_settings.enabled:
            return ""

        roundup_amount = _compute_roundup_amount(draft.amount, roundup_settings.step)
        if roundup_amount <= 0:
            return ""

        sheets_client.append_transit(
            date=draft.date,
            from_account=draft.account,
            to_account=roundup_settings.target_account,
            amount=roundup_amount,
            comment=config.TRANSIT_AUTOROUND_COMMENT,
        )
        return Text.AUTOROUND_NOTICE.format(
            amount=_format_amount_for_display(roundup_amount),
            target_account=roundup_settings.target_account,
        )

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

        autoround_notice = apply_autoround_if_needed(draft)

        time.sleep(config.BALANCE_READ_DELAY_SECONDS)
        balance = sheets_client.get_account_balance(draft.account)
        balance_display = _format_amount_for_display(balance) if balance is not None else "—"

        confirmation = Text.OPERATION_SAVED.format(
            date=draft.date.strftime("%d.%m.%Y"),
            type=draft.operation_type,
            category=draft.category,
            account=draft.account,
            amount=_format_amount_for_display(draft.amount),
            comment=draft.comment if draft.comment else "—",
            balance=balance_display,
        ) + autoround_notice

        session_storage.clear(user_id)

        if message_id is not None:
            bot.edit_message_text(confirmation, chat_id, message_id)
        else:
            bot.send_message(chat_id, confirmation)

        show_main_menu(chat_id)

    def finalize_transit(user_id: int, chat_id: int, message_id: int | None) -> None:
        session = session_storage.get(user_id)
        draft: TransitDraft = session.transit_draft

        sheets_client.append_transit(
            date=draft.date,
            from_account=draft.from_account,
            to_account=draft.to_account,
            amount=draft.amount,
            comment=draft.comment,
        )

        time.sleep(config.BALANCE_READ_DELAY_SECONDS)
        from_balance = sheets_client.get_account_balance(draft.from_account)
        to_balance = sheets_client.get_account_balance(draft.to_account)

        confirmation = Text.TRANSIT_SAVED.format(
            date=draft.date.strftime("%d.%m.%Y"),
            from_account=draft.from_account,
            to_account=draft.to_account,
            amount=_format_amount_for_display(draft.amount),
            comment=draft.comment if draft.comment else "—",
            from_balance=_format_amount_for_display(from_balance) if from_balance is not None else "—",
            to_balance=_format_amount_for_display(to_balance) if to_balance is not None else "—",
        )

        session_storage.clear(user_id)

        if message_id is not None:
            bot.edit_message_text(confirmation, chat_id, message_id)
        else:
            bot.send_message(chat_id, confirmation)

        show_main_menu(chat_id)

    @bot.message_handler(commands=["start"])
    @require_access
    def handle_start(message: Message) -> None:
        session_storage.reset(message.from_user.id)
        bot.send_message(message.chat.id, Text.WELCOME, reply_markup=keyboards.main_reply_keyboard())

    @bot.message_handler(func=lambda message: message.text == Text.MENU_BUTTON)
    @require_access
    def handle_menu_button(message: Message) -> None:
        session_storage.reset(message.from_user.id)
        show_main_menu(message.chat.id)

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

    @bot.callback_query_handler(func=lambda call: call.data == Callback.ADD_TRANSIT)
    @require_access
    @safe_handler
    def handle_add_transit(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        session = session_storage.reset(user_id)
        session.state = UserState.WAITING_TRANSIT_DATE_CHOICE
        bot.edit_message_text(
            Text.ASK_DATE,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.date_choice_keyboard(),
        )

    @bot.callback_query_handler(func=lambda call: call.data == Callback.CANCEL)
    @require_access
    @safe_handler
    def handle_cancel(call: CallbackQuery) -> None:
        session_storage.reset(call.from_user.id)
        bot.edit_message_text(
            Text.OPERATION_CANCELLED,
            call.message.chat.id,
            call.message.message_id,
        )
        show_main_menu(call.message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data == Callback.DATE_TODAY)
    @require_access
    @safe_handler
    def handle_date_today(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id

        if session.state == UserState.WAITING_DATE_CHOICE:
            session.draft.date = datetime.date.today()
            session.state = UserState.WAITING_TYPE
            bot.edit_message_text(Text.ASK_TYPE, chat_id, message_id, reply_markup=keyboards.operation_type_keyboard())
            return

        if session.state == UserState.WAITING_TRANSIT_DATE_CHOICE:
            session.transit_draft.date = datetime.date.today()
            ask_transit_from_account(session, call.from_user.id, chat_id, message_id)
            return

        bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)

    @bot.callback_query_handler(func=lambda call: call.data == Callback.DATE_OTHER)
    @require_access
    @safe_handler
    def handle_date_other(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id

        if session.state not in _DATE_CHOICE_STATES:
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            return

        session.state = (
            UserState.WAITING_DATE_INPUT
            if session.state == UserState.WAITING_DATE_CHOICE
            else UserState.WAITING_TRANSIT_DATE_INPUT
        )
        bot.edit_message_text(
            Text.ASK_DATE_MANUAL, chat_id, message_id, reply_markup=keyboards.cancel_only_keyboard()
        )

    def ask_transit_from_account(session, user_id: int, chat_id: int, message_id: int) -> None:
        accounts = sheets_client.get_accounts()
        if not accounts:
            bot.edit_message_text(Text.NO_ACCOUNTS, chat_id, message_id)
            session_storage.clear(user_id)
            return
        session.pending_options = accounts
        session.state = UserState.WAITING_TRANSIT_FROM_ACCOUNT
        bot.edit_message_text(
            Text.ASK_TRANSIT_FROM_ACCOUNT,
            chat_id,
            message_id,
            reply_markup=keyboards.options_keyboard(accounts, Callback.TRANSIT_FROM_PREFIX),
        )

    @bot.message_handler(func=lambda message: session_storage.get(message.from_user.id).state in _DATE_INPUT_STATES)
    @require_access
    @safe_handler
    def handle_date_input(message: Message) -> None:
        session = session_storage.get(message.from_user.id)
        chat_id = message.chat.id
        try:
            parsed_date = _parse_date(message.text)
        except ValueError:
            bot.send_message(chat_id, Text.INVALID_DATE, reply_markup=keyboards.cancel_only_keyboard())
            return

        if session.state == UserState.WAITING_DATE_INPUT:
            session.draft.date = parsed_date
            session.state = UserState.WAITING_TYPE
            bot.send_message(chat_id, Text.ASK_TYPE, reply_markup=keyboards.operation_type_keyboard())
            return

        session.transit_draft.date = parsed_date
        accounts = sheets_client.get_accounts()
        if not accounts:
            bot.send_message(chat_id, Text.NO_ACCOUNTS)
            session_storage.clear(message.from_user.id)
            return
        session.pending_options = accounts
        session.state = UserState.WAITING_TRANSIT_FROM_ACCOUNT
        bot.send_message(
            chat_id,
            Text.ASK_TRANSIT_FROM_ACCOUNT,
            reply_markup=keyboards.options_keyboard(accounts, Callback.TRANSIT_FROM_PREFIX),
        )

    @bot.callback_query_handler(func=lambda call: call.data in (Callback.TYPE_INCOME, Callback.TYPE_EXPENSE))
    @require_access
    @safe_handler
    def handle_type_choice(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id
        if session.state != UserState.WAITING_TYPE:
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            return

        if call.data == Callback.TYPE_INCOME:
            session.draft.operation_type = OPERATION_TYPE_INCOME
            categories = sheets_client.get_income_categories()
        else:
            session.draft.operation_type = OPERATION_TYPE_EXPENSE
            categories = sheets_client.get_expense_categories()

        if not categories:
            bot.edit_message_text(Text.NO_CATEGORIES, chat_id, message_id)
            session_storage.clear(call.from_user.id)
            return

        session.pending_options = categories
        session.state = UserState.WAITING_CATEGORY
        bot.edit_message_text(
            Text.ASK_CATEGORY,
            chat_id,
            message_id,
            reply_markup=keyboards.options_keyboard(categories, Callback.CATEGORY_PREFIX),
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith(Callback.CATEGORY_PREFIX))
    @require_access
    @safe_handler
    def handle_category_choice(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id
        if session.state != UserState.WAITING_CATEGORY:
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            return

        index = int(call.data.removeprefix(Callback.CATEGORY_PREFIX))
        if index < 0 or index >= len(session.pending_options):
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            session_storage.clear(call.from_user.id)
            return
        session.draft.category = session.pending_options[index]

        accounts = sheets_client.get_accounts()
        if not accounts:
            bot.edit_message_text(Text.NO_ACCOUNTS, chat_id, message_id)
            session_storage.clear(call.from_user.id)
            return

        session.pending_options = accounts
        session.state = UserState.WAITING_ACCOUNT
        bot.edit_message_text(
            Text.ASK_ACCOUNT,
            chat_id,
            message_id,
            reply_markup=keyboards.options_keyboard(accounts, Callback.ACCOUNT_PREFIX),
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith(Callback.ACCOUNT_PREFIX))
    @require_access
    @safe_handler
    def handle_account_choice(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id
        if session.state != UserState.WAITING_ACCOUNT:
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            return

        index = int(call.data.removeprefix(Callback.ACCOUNT_PREFIX))
        if index < 0 or index >= len(session.pending_options):
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            session_storage.clear(call.from_user.id)
            return
        session.draft.account = session.pending_options[index]
        session.pending_options = []
        session.state = UserState.WAITING_AMOUNT
        bot.edit_message_text(Text.ASK_AMOUNT, chat_id, message_id, reply_markup=keyboards.cancel_only_keyboard())

    @bot.callback_query_handler(func=lambda call: call.data.startswith(Callback.TRANSIT_FROM_PREFIX))
    @require_access
    @safe_handler
    def handle_transit_from_choice(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id
        if session.state != UserState.WAITING_TRANSIT_FROM_ACCOUNT:
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            return

        index = int(call.data.removeprefix(Callback.TRANSIT_FROM_PREFIX))
        if index < 0 or index >= len(session.pending_options):
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            session_storage.clear(call.from_user.id)
            return

        from_account = session.pending_options[index]
        session.transit_draft.from_account = from_account
        remaining_accounts = [account for account in session.pending_options if account != from_account]

        if not remaining_accounts:
            bot.edit_message_text(Text.NO_OTHER_ACCOUNTS_FOR_TRANSIT, chat_id, message_id)
            session_storage.clear(call.from_user.id)
            return

        session.pending_options = remaining_accounts
        session.state = UserState.WAITING_TRANSIT_TO_ACCOUNT
        bot.edit_message_text(
            Text.ASK_TRANSIT_TO_ACCOUNT,
            chat_id,
            message_id,
            reply_markup=keyboards.options_keyboard(remaining_accounts, Callback.TRANSIT_TO_PREFIX),
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith(Callback.TRANSIT_TO_PREFIX))
    @require_access
    @safe_handler
    def handle_transit_to_choice(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id
        if session.state != UserState.WAITING_TRANSIT_TO_ACCOUNT:
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            return

        index = int(call.data.removeprefix(Callback.TRANSIT_TO_PREFIX))
        if index < 0 or index >= len(session.pending_options):
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            session_storage.clear(call.from_user.id)
            return

        session.transit_draft.to_account = session.pending_options[index]
        session.pending_options = []
        session.state = UserState.WAITING_TRANSIT_AMOUNT
        bot.edit_message_text(Text.ASK_AMOUNT, chat_id, message_id, reply_markup=keyboards.cancel_only_keyboard())

    @bot.message_handler(func=lambda message: session_storage.get(message.from_user.id).state in _AMOUNT_STATES)
    @require_access
    @safe_handler
    def handle_amount_input(message: Message) -> None:
        session = session_storage.get(message.from_user.id)
        chat_id = message.chat.id
        try:
            amount = _parse_amount(message.text)
        except ValueError:
            bot.send_message(chat_id, Text.INVALID_AMOUNT, reply_markup=keyboards.cancel_only_keyboard())
            return

        if session.state == UserState.WAITING_AMOUNT:
            session.draft.amount = amount
            session.state = UserState.WAITING_COMMENT_CHOICE
        else:
            session.transit_draft.amount = amount
            session.state = UserState.WAITING_TRANSIT_COMMENT_CHOICE

        bot.send_message(chat_id, Text.ASK_COMMENT_CHOICE, reply_markup=keyboards.comment_choice_keyboard())

    @bot.callback_query_handler(func=lambda call: call.data == Callback.COMMENT_NO)
    @require_access
    @safe_handler
    def handle_comment_no(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id
        if session.state not in _COMMENT_CHOICE_STATES:
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            return

        if session.state == UserState.WAITING_COMMENT_CHOICE:
            session.draft.comment = ""
            finalize_operation(call.from_user.id, chat_id, message_id)
        else:
            session.transit_draft.comment = ""
            finalize_transit(call.from_user.id, chat_id, message_id)

    @bot.callback_query_handler(func=lambda call: call.data == Callback.COMMENT_YES)
    @require_access
    @safe_handler
    def handle_comment_yes(call: CallbackQuery) -> None:
        session = session_storage.get(call.from_user.id)
        chat_id, message_id = call.message.chat.id, call.message.message_id
        if session.state not in _COMMENT_CHOICE_STATES:
            bot.edit_message_text(Text.SESSION_EXPIRED, chat_id, message_id)
            return

        session.state = (
            UserState.WAITING_COMMENT_INPUT
            if session.state == UserState.WAITING_COMMENT_CHOICE
            else UserState.WAITING_TRANSIT_COMMENT_INPUT
        )
        bot.edit_message_text(
            Text.ASK_COMMENT_TEXT, chat_id, message_id, reply_markup=keyboards.cancel_only_keyboard()
        )

    @bot.message_handler(
        func=lambda message: session_storage.get(message.from_user.id).state in _COMMENT_INPUT_STATES
    )
    @require_access
    @safe_handler
    def handle_comment_input(message: Message) -> None:
        session = session_storage.get(message.from_user.id)
        chat_id = message.chat.id
        comment = message.text.strip()

        if session.state == UserState.WAITING_COMMENT_INPUT:
            session.draft.comment = comment
            finalize_operation(message.from_user.id, chat_id, None)
        else:
            session.transit_draft.comment = comment
            finalize_transit(message.from_user.id, chat_id, None)
