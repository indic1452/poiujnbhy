"""Сессии работы с потоками: общий стол нескольких файлов и нескольких людей.

Сессия — имя, владелец и участники. Файлы сессии — задания разбора с
пометкой «сессия» (корни деревьев обработки), производные узлы наследуют
пометку. Видят и меняют сессию её владелец и участники; делиться, переименовывать
и удалять вправе только владелец. Задания сессии не убираются как старые —
уходят вместе с сессией.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from ..fayly import записать_атомарно

ИМЯ_НЕ_ДЛИННЕЕ = 120
УЧАСТНИКОВ_НЕ_БОЛЬШЕ = 200
_ИД = re.compile(r"[0-9a-f]{12}")


def годный_ид(ид: str) -> bool:
    return bool(_ИД.fullmatch(ид or ""))


def _имя(имя: str) -> str:
    имя = " ".join(str(имя or "").split())[:ИМЯ_НЕ_ДЛИННЕЕ]
    if not имя:
        raise ValueError("у сессии должно быть имя")
    return имя


class Сессии:
    """Хранилище сессий: по файлу JSON на сессию."""

    def __init__(self, папка: Path):
        self.папка = Path(папка)
        self._lock = threading.Lock()
        self._замки: dict[str, threading.Lock] = {}

    def замок(self, ид: str) -> threading.Lock:
        """Замок одной сессии: добавление файла и удаление сессии не идут одновременно."""
        with self._lock:
            return self._замки.setdefault(ид, threading.Lock())

    def _путь(self, ид: str) -> Path:
        if not годный_ид(ид):
            raise KeyError(ид)
        return self.папка / f"{ид}.json"

    def _записать(self, сессия: dict[str, Any]) -> None:
        self.папка.mkdir(parents=True, exist_ok=True)
        путь = self._путь(сессия["ид"])
        записать_атомарно(путь, json.dumps(сессия, ensure_ascii=False))

    def создать(self, *, владелец: int, имя: str) -> str:
        сессия = {"ид": secrets.token_hex(6), "имя": _имя(имя), "владелец": int(владелец),
                  "участники": [], "создано": time.time(), "изменено": time.time()}
        with self._lock:
            self._записать(сессия)
        return сессия["ид"]

    def прочитать(self, ид: str) -> dict[str, Any]:
        путь = self._путь(ид)
        if not путь.exists():
            raise KeyError(ид)
        return json.loads(путь.read_text(encoding="utf-8"))

    @staticmethod
    def доступна(сессия: dict[str, Any], пользователь: int) -> bool:
        return пользователь == сессия.get("владелец") or пользователь in (сессия.get("участники") or [])

    def для(self, ид: str, пользователь: int) -> dict[str, Any]:
        """Сессия, если пользователю она доступна; иначе KeyError — как будто её нет."""
        сессия = self.прочитать(ид)
        if not self.доступна(сессия, пользователь):
            raise KeyError(ид)
        return сессия

    def список(self, пользователь: int) -> list[dict[str, Any]]:
        """Доступные сессии, свежие сверху."""
        итог = []
        if not self.папка.exists():
            return итог
        for путь in self.папка.glob("*.json"):
            try:
                сессия = json.loads(путь.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if self.доступна(сессия, пользователь):
                итог.append(сессия)
        return sorted(итог, key=lambda с: с.get("изменено") or 0, reverse=True)

    def доступные(self, пользователь: int) -> list[str]:
        return [с["ид"] for с in self.список(пользователь)]

    def изменить(self, ид: str, пользователь: int, *, имя: str | None = None,
                 участники: list[int] | None = None) -> dict[str, Any]:
        """Переименовать и (или) задать участников — только владелец."""
        with self._lock:
            сессия = self.для(ид, пользователь)
            if сессия["владелец"] != пользователь:
                raise PermissionError("менять сессию вправе только её владелец")
            if имя is not None:
                сессия["имя"] = _имя(имя)
            if участники is not None:
                сессия["участники"] = sorted({int(у) for у in участники} - {сессия["владелец"]})[
                    :УЧАСТНИКОВ_НЕ_БОЛЬШЕ]
            сессия["изменено"] = time.time()
            self._записать(сессия)
        return сессия

    def покинуть(self, ид: str, пользователь: int) -> None:
        """Участник выходит из чужой сессии; владелец так не может — только удалить."""
        with self._lock:
            сессия = self.для(ид, пользователь)
            if сессия["владелец"] == пользователь:
                raise PermissionError("владелец не покидает свою сессию — её можно удалить")
            сессия["участники"] = [у for у in сессия["участники"] if у != пользователь]
            self._записать(сессия)

    def тронуть(self, ид: str) -> None:
        """Отметить работу в сессии: свежие — выше в списке."""
        with self._lock:
            сессия = self.прочитать(ид)
            сессия["изменено"] = time.time()
            self._записать(сессия)

    def удалить(self, ид: str, пользователь: int) -> None:
        with self._lock:
            сессия = self.для(ид, пользователь)
            if сессия["владелец"] != пользователь:
                raise PermissionError("удалить сессию вправе только её владелец")
            self._путь(ид).unlink()
