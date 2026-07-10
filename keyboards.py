"""
Все клавиатуры бота: постоянная (ReplyKeyboardMarkup) и inline-клавиатуры.
Клавиатуры не содержат бизнес-логики — только разметку.
"""
from __future__ import annotations

from telebot import types

from config import Callback, Text


def main_reply_keyboard() -> types.ReplyKeyboardMarkup:
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton(Text.MENU_BUTTON))
    return keyboard


def main_menu_inline_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(Text.ADD_OPERATION_BUTTON, callback_data=Callback.ADD_OPERATION),
        types.InlineKeyboardButton(Text.ADD_TRANSIT_BUTTON, callback_data=Callback.ADD_TRANSIT),
    )
    return keyboard


def _with_cancel(keyboard: types.InlineKeyboardMarkup) -> types.InlineKeyboardMarkup:
    keyboard.add(types.InlineKeyboardButton(Text.CANCEL_BUTTON, callback_data=Callback.CANCEL))
    return keyboard


def date_choice_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(Text.DATE_TODAY_BUTTON, callback_data=Callback.DATE_TODAY),
        types.InlineKeyboardButton(Text.DATE_OTHER_BUTTON, callback_data=Callback.DATE_OTHER),
    )
    return _with_cancel(keyboard)


def cancel_only_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    return _with_cancel(keyboard)


def operation_type_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(Text.TYPE_INCOME_BUTTON, callback_data=Callback.TYPE_INCOME),
        types.InlineKeyboardButton(Text.TYPE_EXPENSE_BUTTON, callback_data=Callback.TYPE_EXPENSE),
    )
    return _with_cancel(keyboard)


def options_keyboard(options: list[str], callback_prefix: str, columns: int = 2) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=columns)
    buttons = [
        types.InlineKeyboardButton(option, callback_data=f"{callback_prefix}{index}")
        for index, option in enumerate(options)
    ]
    keyboard.add(*buttons)
    return _with_cancel(keyboard)


def comment_choice_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(Text.COMMENT_YES_BUTTON, callback_data=Callback.COMMENT_YES),
        types.InlineKeyboardButton(Text.COMMENT_NO_BUTTON, callback_data=Callback.COMMENT_NO),
    )
    return _with_cancel(keyboard)
