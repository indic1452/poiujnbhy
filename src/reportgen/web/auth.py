"""Аутентификация, сессии и роли.

Пользователи и сессии живут в той же базе, что и кейсы: внешних сервисов
(LDAP, OAuth) в изолированном контуре может не быть, а один SQLite проще
резервировать и восстанавливать целиком.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

import secrets

from fastapi import Request

from ..store.models import User
from ..store.repo import Repositories
from .service import ServiceError

COOKIE_NAME = "rg_session"

LOCAL_LOGIN = "local"


def ensure_local_user(repos: Repositories) -> User:
    """Пользователь для режима без аутентификации (одиночная рабочая станция).

    Он должен существовать в таблице ``users`` по-настоящему: на неё ссылаются
    внешние ключи кейсов, отчётов, пар для датасета и журнала. Пароль
    случайный и нигде не сохраняется, а запись помечена неактивной — войти под
    ней нельзя, даже если позже включить аутентификацию.
    """
    existing = repos.users.by_login(LOCAL_LOGIN)
    if existing is not None:
        return existing
    user = repos.users.create(
        LOCAL_LOGIN, secrets.token_urlsafe(32), "Локальный режим (вход запрещён)", "owner"
    )
    repos.users.set_active(user.id, False)
    return repos.users.by_login(LOCAL_LOGIN) or user


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
        local = getattr(request.app.state, "local_user", None)
        if local is None:
            local = ensure_local_user(request.app.state.repos)
            request.app.state.local_user = local
        return local
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user = request.app.state.repos.sessions.resolve(token)
    if user is not None:
        # Отмечаем, что человек в системе. Не чаще раза в минуту — иначе
        # запись в базу приходилась бы на каждый щелчок в интерфейсе.
        # Неудача здесь не должна мешать работе: отметка — удобство, а не
        # условие входа.
        try:
            request.app.state.repos.users.mark_seen(user)
        except Exception:              # noqa: BLE001
            pass
    return user


def require_anyone(request: Request) -> User:
    """Просто вошедший — включая гостя. Только для помощника.

    Всё остальное закрыто по умолчанию: см. require_user ниже.
    """
    user = get_user(request)
    if user is None:
        raise ServiceError("требуется вход в систему", 401)
    return user


def require_user(request: Request) -> User:
    """Вошедший сотрудник отдела.

    Гостя сюда не пускаем НАМЕРЕННО и именно здесь, а не в каждом маршруте по
    отдельности: закрытым по умолчанию должно быть всё, а открытым — то
    немногое, что гостю положено (помощник). Новый маршрут, написанный
    завтра, окажется для гостя закрыт сам собой, и это правильный порядок:
    забыть закрыть легко, забыть открыть — заметно сразу.
    """
    user = require_anyone(request)
    if user.is_guest:
        raise ServiceError(
            "гостю доступен только помощник: вопрос-ответ по библиотеке", 403)
    return user


def require_editor(request: Request) -> User:
    """Работа с письмами и отчётами: доступна всем штатным должностям."""
    user = require_user(request)
    if not user.can_edit:
        raise ServiceError("недостаточно прав для этого действия", 403)
    return user


def require_reviewer(request: Request) -> User:
    """Проверка отчётов: начальник отдела, заместитель, создатель системы.

    Это не то же самое, что права администратора: начальник группы заводит
    людей, но отчёты проверяет не он — так устроен порядок в отделе.
    """
    user = require_user(request)
    if not user.can_review:
        raise ServiceError(
            "проверять отчёты может начальник отдела или его заместитель", 403
        )
    return user


def require_admin(request: Request) -> User:
    """Управление военнослужащими, библиотекой и журналом.

    Права администратора — у должностей до начальника группы включительно.
    """
    user = require_user(request)
    if not user.is_admin:
        raise ServiceError(
            "недостаточно прав: нужна должность не ниже начальника группы", 403
        )
    return user


def require_owner(request: Request) -> User:
    user = require_user(request)
    if not user.is_owner:
        raise ServiceError("это может только создатель системы", 403)
    return user
