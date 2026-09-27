"""Конфигурации обработки потоков: именованные цепочки шагов, которые аналитик
составляет сам и применяет к любому потоку.

Шаг — то же, что в дереве обработки: канал по маске растра или снятие слоя
(«синхро 0x47», «стаффинг период 200 …», «аддитивный отводы 14,15 кадр 1000»,
«ldpc …»). Конфигурация хранит их по порядку вместе с названием и описанием —
«как разбирать поток такого-то вида». Своя конфигурация правится и удаляется
только автором; «общая» видна всему отделу и применяется кем угодно.

Это труд аналитика, пересчитать его не из чего, — папка идёт в резервную копию.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .rastr import проверить_шаги

#: Сколько конфигураций у одного автора.
СВОИХ_ДО = 500
ИМЯ_ДО = 80
ОПИСАНИЕ_ДО = 4000


class НетДоступа(PermissionError):
    pass


class Конфигурации:
    def __init__(self, папка: Path):
        self.папка = Path(папка)
        self._lock = threading.Lock()

    # -- чтение --------------------------------------------------------------------

    def _путь(self, ид: str) -> Path:
        if not isinstance(ид, str) or len(ид) != 12 or not all(ч in "0123456789abcdef" for ч in ид):
            raise KeyError(ид)
        return self.папка / f"{ид}.json"

    def _все(self) -> list[dict[str, Any]]:
        if not self.папка.exists():
            return []
        итог = []
        for путь in self.папка.glob("*.json"):
            try:
                итог.append(json.loads(путь.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue                                  # испорченный файл не роняет список
        return итог

    def список(self, владелец: int) -> list[dict[str, Any]]:
        """Свои и общие — свои первыми, затем по имени."""
        видно = [к for к in self._все() if к.get("владелец") == владелец or к.get("общая")]
        for к in видно:
            к["своя"] = к.get("владелец") == владелец
        return sorted(видно, key=lambda к: (not к["своя"], к.get("имя", "").lower()))

    def прочитать(self, ид: str, владелец: int) -> dict[str, Any]:
        путь = self._путь(ид)
        if not путь.exists():
            raise KeyError(ид)
        к = json.loads(путь.read_text(encoding="utf-8"))
        if к.get("владелец") != владелец and not к.get("общая"):
            raise KeyError(ид)                            # чужая личная — как будто нет
        к["своя"] = к.get("владелец") == владелец
        return к

    # -- запись --------------------------------------------------------------------

    def сохранить(self, владелец: int, *, имя: str, шаги: Sequence[dict[str, Any]], описание: str = "",
                  общая: bool = False, автор: str = "", ид: str | None = None) -> dict[str, Any]:
        """Новая (без ``ид``) или правка своей. Ошибки формы — ValueError."""
        имя = " ".join(str(имя or "").split())
        if not имя:
            raise ValueError("у конфигурации нет названия")
        if len(имя) > ИМЯ_ДО:
            raise ValueError(f"название длиннее {ИМЯ_ДО} знаков")
        описание = str(описание or "").strip()
        if len(описание) > ОПИСАНИЕ_ДО:
            raise ValueError(f"описание длиннее {ОПИСАНИЕ_ДО} знаков")
        шаги = проверить_шаги(list(шаги or []))
        if not шаги:
            raise ValueError("в конфигурации нет ни одного шага")
        with self._lock:
            if ид is None:
                if sum(1 for к in self._все() if к.get("владелец") == владелец) >= СВОИХ_ДО:
                    raise ValueError(f"конфигураций больше {СВОИХ_ДО} — удалите ненужные")
                ид = secrets.token_hex(6)
                прежняя = {"ид": ид, "владелец": владелец, "создано": time.time(), "автор": автор}
            else:
                путь = self._путь(ид)
                if not путь.exists():
                    raise KeyError(ид)
                прежняя = json.loads(путь.read_text(encoding="utf-8"))
                if прежняя.get("владелец") != владелец:
                    raise НетДоступа("чужую конфигурацию можно применить или скопировать, но не править")
            запись = {**прежняя, "имя": имя, "описание": описание, "шаги": шаги,
                      "общая": bool(общая), "изменено": time.time()}
            self.папка.mkdir(parents=True, exist_ok=True)
            путь = self._путь(ид)
            временный = путь.with_suffix(".tmp")
            временный.write_text(json.dumps(запись, ensure_ascii=False, indent=1), encoding="utf-8")
            временный.replace(путь)
        return {**запись, "своя": True}

    def удалить(self, ид: str, владелец: int) -> None:
        к = self.прочитать(ид, владелец)
        if not к["своя"]:
            raise НетДоступа("удалить можно только свою конфигурацию")
        self._путь(ид).unlink(missing_ok=True)

    # -- перенос между машинами ------------------------------------------------------

    @staticmethod
    def выгрузка(к: dict[str, Any]) -> dict[str, Any]:
        """То, что уходит в файл: без владельца и служебных отметок."""
        return {"вид": "конфигурация обработки потока", "версия": 1, "имя": к["имя"],
                "описание": к.get("описание", ""), "шаги": к["шаги"]}

    def загрузить(self, владелец: int, данные: dict[str, Any], автор: str = "") -> dict[str, Any]:
        if not isinstance(данные, dict) or данные.get("вид") != "конфигурация обработки потока":
            raise ValueError("это не файл конфигурации обработки потока")
        return self.сохранить(владелец, имя=данные.get("имя", ""), описание=данные.get("описание", ""),
                              шаги=данные.get("шаги") or [], автор=автор)
