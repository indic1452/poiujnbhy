"""Аутентификация, сессии и роли.

Пользователи и сессии живут в той же базе, что и кейсы: внешних сервисов
(LDAP, OAuth) в изолированном контуре может не быть, а один SQLite проще
резервировать и восстанавливать целиком.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

from fastapi import Request

from ..store.models import User
from .service import ServiceError

COOKIE_NAME = "rg_session"

# Пользователь, от имени которого работает система при auth_enabled = false
# (одиночная установка на рабочей станции инженера).
LOCAL_USER = User(id=0, login="local", full_name="Локальный режим", role="admin")


@dataclass
class LoginThrottle:
    """Простой ограничитель перебора паролей: N неудач — пауза."""

    max_failures: int = 5
    block_minutes: int = 10
    _state: Dict[str, Tuple[int, datetime | None]] = field(default_factory=dict)

    def check(self, key: str) -> None:
        failures, blocked_until = self._state.get(key, (0, None))
        if blocked_until and blocked_until > datetime.now(timezone.utc):
            left = int((blocked_until - datetime.now(timezone.utc)).total_seconds() // 60) + 1
            raise ServiceError(
                f"слишком много неудачных попыток входа, повторите через {left} мин", 429
            )
        if blocked_until and blocked_until <= datetime.now(timezone.utc):
            self._state.pop(key, None)

    def failure(self, key: str) -> None:
        failures, _ = self._state.get(key, (0, None))
        failures += 1
        blocked_until = (
            datetime.now(timezone.utc) + timedelta(minutes=self.block_minutes)
            if failures >= self.max_failures
            else None
        )
        self._state[key] = (failures, blocked_until)

    def success(self, key: str) -> None:
        self._state.pop(key, None)


def auth_enabled(request: Request) -> bool:
    return bool(request.app.state.settings.auth_enabled)


def get_user(request: Request) -> User | None:
    """Текущий пользователь или None. При выключенной аутентификации — локальный."""
    if not auth_enabled(request):
        return LOCAL_USER
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return request.app.state.repos.sessions.resolve(token)


def require_user(request: Request) -> User:
    user = get_user(request)
    if user is None:
        raise ServiceError("требуется вход в систему", 401)
    return user


def require_editor(request: Request) -> User:
    user = require_user(request)
    if not user.can_edit:
        raise ServiceError("недостаточно прав: нужна роль инженера", 403)
    return user


def require_admin(request: Request) -> User:
    user = require_user(request)
    if not user.is_admin:
        raise ServiceError("недостаточно прав: нужна роль администратора", 403)
    return user
