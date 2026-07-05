"""
Собственная реализация машины состояний пользователя (без сторонних FSM-библиотек).
Хранит промежуточные данные пользователя во время заполнения операции
и гарантирует их полную очистку при отмене, ошибке или завершении сценария.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum, auto
from threading import Lock


class UserState(Enum):
    MAIN_MENU = auto()
    WAITING_DATE_CHOICE = auto()
    WAITING_DATE_INPUT = auto()
    WAITING_TYPE = auto()
    WAITING_CATEGORY = auto()
    WAITING_ACCOUNT = auto()
    WAITING_AMOUNT = auto()
    WAITING_COMMENT_CHOICE = auto()
    WAITING_COMMENT_INPUT = auto()


@dataclass
class OperationDraft:
    date: datetime.date | None = None
    operation_type: str | None = None
    category: str | None = None
    account: str | None = None
    amount: float | None = None
    comment: str = ""


@dataclass
class UserSession:
    state: UserState = UserState.MAIN_MENU
    draft: OperationDraft = field(default_factory=OperationDraft)
    last_menu_message_id: int | None = None
    pending_options: list[str] = field(default_factory=list)


class SessionStorage:
    """Потокобезопасное хранилище состояний пользователей в памяти."""

    def __init__(self) -> None:
        self._sessions: dict[int, UserSession] = {}
        self._lock = Lock()

    def get(self, user_id: int) -> UserSession:
        with self._lock:
            if user_id not in self._sessions:
                self._sessions[user_id] = UserSession()
            return self._sessions[user_id]

    def reset(self, user_id: int) -> UserSession:
        with self._lock:
            self._sessions[user_id] = UserSession()
            return self._sessions[user_id]

    def set_state(self, user_id: int, state: UserState) -> None:
        with self._lock:
            self._sessions.setdefault(user_id, UserSession()).state = state

    def clear(self, user_id: int) -> None:
        with self._lock:
            self._sessions.pop(user_id, None)


session_storage = SessionStorage()
