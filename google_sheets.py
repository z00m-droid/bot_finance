"""
Модуль полностью инкапсулирует работу с Google Sheets через gspread.
Остальной код проекта не должен импортировать gspread напрямую.
"""
from __future__ import annotations

import datetime
import time
from threading import Lock
from typing import Any

import json

import gspread
from google.auth.exceptions import GoogleAuthError
from gspread.exceptions import APIError, GSpreadException
from gspread.utils import ValueInputOption

from dataclasses import dataclass

import config


class GoogleSheetsError(Exception):
    """Единая ошибка для всех сбоев при работе с Google Sheets."""


@dataclass(frozen=True)
class RoundupSettings:
    enabled: bool
    step: float
    target_account: str


class _TTLCache:
    """Простой потокобезопасный кеш с истечением по времени."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        if not config.CACHE_ENABLED:
            return None
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        if not config.CACHE_ENABLED:
            return
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl_seconds, value)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


class GoogleSheetsClient:
    """Клиент для чтения справочников и записи операций в Google Sheets."""

    def __init__(self) -> None:
        self._cache = _TTLCache(config.CACHE_TTL_SECONDS)
        self._client: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None

    def _create_client(self) -> gspread.Client:
        if config.GOOGLE_CREDENTIALS_JSON:
            try:
                credentials_info = json.loads(config.GOOGLE_CREDENTIALS_JSON)
            except json.JSONDecodeError as exc:
                raise GoogleSheetsError(
                    f"Переменная GOOGLE_CREDENTIALS_JSON содержит некорректный JSON: {exc}"
                ) from exc
            return gspread.service_account_from_dict(credentials_info)
        return gspread.service_account(filename=config.GOOGLE_CREDENTIALS_PATH)

    def _connect(self) -> gspread.Spreadsheet:
        if self._spreadsheet is not None:
            return self._spreadsheet
        try:
            client = self._create_client()
            spreadsheet = client.open_by_key(config.SPREADSHEET_ID)
        except (GoogleAuthError, FileNotFoundError) as exc:
            raise GoogleSheetsError(f"Ошибка авторизации Service Account: {exc}") from exc
        except APIError as exc:
            raise GoogleSheetsError(f"Не удалось открыть таблицу: {exc}") from exc
        self._client = client
        self._spreadsheet = spreadsheet
        return spreadsheet

    def _get_worksheet(self, title: str) -> gspread.Worksheet:
        spreadsheet = self._connect()
        try:
            return spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound as exc:
            raise GoogleSheetsError(f"Лист '{title}' не найден в таблице") from exc

    def _read_flat_range(self, sheet_title: str, cell_range: str) -> list[str]:
        worksheet = self._get_worksheet(sheet_title)
        try:
            raw_values = worksheet.get(cell_range)
        except APIError as exc:
            raise GoogleSheetsError(f"Ошибка чтения диапазона {cell_range}: {exc}") from exc
        return [
            str(row[0]).strip()
            for row in raw_values
            if row and row[0] is not None and str(row[0]).strip()
        ]

    def get_expense_categories(self) -> list[str]:
        cache_key = "expense_categories"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        values = self._read_flat_range(config.SHEET_SETTINGS, config.EXPENSE_CATEGORIES_RANGE)
        self._cache.set(cache_key, values)
        return values

    def get_income_categories(self) -> list[str]:
        cache_key = "income_categories"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        values = self._read_flat_range(config.SHEET_SETTINGS, config.INCOME_CATEGORIES_RANGE)
        self._cache.set(cache_key, values)
        return values

    def get_accounts(self) -> list[str]:
        cache_key = "accounts"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        values = self._read_flat_range(config.SHEET_ACCOUNTS, config.ACCOUNTS_RANGE)
        self._cache.set(cache_key, values)
        return values

    def append_operation(
        self,
        date: datetime.date,
        operation_type: str,
        category: str,
        account: str,
        amount: float,
        comment: str,
    ) -> None:
        worksheet = self._get_worksheet(config.SHEET_OPERATIONS)
        row = [
            date.strftime(config.DATE_INPUT_FORMAT),
            operation_type,
            category,
            account,
            amount,
            comment,
        ]
        try:
            result = worksheet.append_row(
                row,
                value_input_option=ValueInputOption.user_entered,
                table_range=config.OPERATIONS_TABLE_RANGE,
            )
        except (APIError, GSpreadException) as exc:
            raise GoogleSheetsError(f"Ошибка записи операции: {exc}") from exc
        self._reinforce_amount_format(worksheet, result, config.OPERATIONS_AMOUNT_COLUMN_INDEX)

    def append_transit(
        self,
        date: datetime.date,
        from_account: str,
        to_account: str,
        amount: float,
        comment: str,
    ) -> None:
        worksheet = self._get_worksheet(config.SHEET_TRANSIT)
        row = [
            date.strftime(config.DATE_INPUT_FORMAT),
            from_account,
            to_account,
            amount,
            comment,
        ]
        try:
            result = worksheet.append_row(
                row,
                value_input_option=ValueInputOption.user_entered,
                table_range=config.TRANSIT_TABLE_RANGE,
            )
        except (APIError, GSpreadException) as exc:
            raise GoogleSheetsError(f"Ошибка записи перевода: {exc}") from exc
        self._reinforce_amount_format(worksheet, result, config.TRANSIT_AMOUNT_COLUMN_INDEX)

    def _reinforce_amount_format(
        self, worksheet: gspread.Worksheet, append_result: dict[str, Any], amount_column_index: int
    ) -> None:
        try:
            updated_range = append_result["updates"]["updatedRange"]
            row_number = int("".join(filter(str.isdigit, updated_range.split(":")[0])))
            amount_cell = f"{chr(ord('A') + amount_column_index - 1)}{row_number}"
            worksheet.format(amount_cell, {"numberFormat": {"type": "NUMBER", "pattern": config.AMOUNT_NUMBER_FORMAT}})
        except (KeyError, ValueError, APIError, GSpreadException):
            pass

    def get_roundup_settings(self) -> dict[str, RoundupSettings]:
        """Настройки копилки читаются без кеша, чтобы изменения в таблице применялись сразу же."""
        worksheet = self._get_worksheet(config.SHEET_SETTINGS)
        try:
            raw_rows = worksheet.get(config.ROUNDUP_SETTINGS_RANGE)
        except APIError as exc:
            raise GoogleSheetsError(f"Ошибка чтения настроек копилки: {exc}") from exc

        settings: dict[str, RoundupSettings] = {}
        for row in raw_rows:
            if not row or row[0] is None or not str(row[0]).strip():
                continue
            account_name = str(row[0]).strip()
            enabled_raw = str(row[1]).strip().lower() if len(row) > 1 and row[1] is not None else ""
            step_raw = row[2] if len(row) > 2 else None
            target_account = str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""

            enabled = enabled_raw == config.ROUNDUP_ENABLED_VALUE
            try:
                step = float(str(step_raw).replace(",", ".")) if step_raw not in (None, "") else 0.0
            except ValueError:
                step = 0.0

            settings[account_name] = RoundupSettings(
                enabled=enabled and step > 0 and bool(target_account),
                step=step,
                target_account=target_account,
            )
        return settings

    def get_account_balance(self, account_name: str) -> float | None:
        worksheet = self._get_worksheet(config.SHEET_ACCOUNTS)
        try:
            account_names = worksheet.col_values(config.ACCOUNTS_NAME_COLUMN_INDEX)
        except APIError as exc:
            raise GoogleSheetsError(f"Ошибка чтения списка счетов: {exc}") from exc

        row_number = next(
            (index + 1 for index, value in enumerate(account_names) if value.strip() == account_name),
            None,
        )
        if row_number is None:
            return None

        balance_column_letter = chr(ord("A") + config.ACCOUNTS_BALANCE_COLUMN_INDEX - 1)
        try:
            raw_values = worksheet.get(
                f"{balance_column_letter}{row_number}",
                value_render_option=gspread.utils.ValueRenderOption.unformatted,
            )
        except APIError as exc:
            raise GoogleSheetsError(f"Ошибка чтения баланса счёта: {exc}") from exc

        if not raw_values or not raw_values[0]:
            return 0.0
        try:
            return float(raw_values[0][0])
        except (ValueError, TypeError):
            return None


sheets_client = GoogleSheetsClient()
