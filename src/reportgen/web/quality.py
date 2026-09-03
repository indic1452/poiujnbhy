"""Проверка качества разбора по УЖЕ загруженной библиотеке.

Склейку текста («Методыцифровогокодирования») система замечает при приёме
документа. Но библиотека отдела собрана раньше: тринадцать с половиной
тысяч документов лежат в базе без единой пометки, и найти среди них плохо
разобранные было нечем. Перезагружать библиотеку ради этого нельзя —
повторный разбор PDF занимает часы, а сам разбор ничего не изменит: файл
как разобрался, так и разберётся.

Поэтому проверяем не файлы, а то, что уже лежит в базе: по куску текста от
каждого документа. Склейка видна на первых же абзацах, читать полторы
тысячи фрагментов книги ради этого незачем.

Проверка идёт в фоне, как построение векторов, и по тем же правилам: одна
работа за раз, состояние видно числами, приложение от неё не встаёт.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Dict

from ..ingest.convert import glued_text_warning

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from ..store.repo import Repositories

__all__ = ["QualityChecker", "TEXT_QUALITY_GLUED"]

#: Пометка в meta документа. Она же уходит в интерфейс отдельным полем.
TEXT_QUALITY_GLUED = "glued"

#: Сколько документов берём из базы за раз. Тринадцать тысяч записей с
#: куском текста каждая — это десятки мегабайт, и поднимать их разом
#: незачем.
PAGE = 500


class QualityChecker:
    """Проходит по библиотеке и метит документы, разобранные плохо."""

    def __init__(self, repos: "Repositories"):
        self.repos = repos
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._done = 0
        self._total = 0
        self._found = 0
        self._cleared = 0
        self._error = ""
        self._finished_at = 0.0

    # -- состояние ----------------------------------------------------------

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            state = {
                "running": self._thread is not None and self._thread.is_alive(),
                "done": self._done,
                "total": self._total,
                "found": self._found,
                "cleared": self._cleared,
                "error": self._error,
                "finished_at": self._finished_at,
            }
        state["glued"] = self.count_glued()
        state["hint"] = _hint(state)
        return state

    def count_glued(self) -> int:
        """Сколько документов сейчас помечено как плохо разобранные."""
        return int(self.repos.db.scalar(
            "SELECT count(*) FROM documents "
            "WHERE meta_json LIKE '%\"text_quality\"%'"
            "  AND meta_json LIKE ?", (f'%"{TEXT_QUALITY_GLUED}"%',)) or 0)

    # -- работа -------------------------------------------------------------

    def start(self) -> Dict[str, Any]:
        """Запустить проверку в фоне. Уже идёт — вернуть текущее состояние."""
        started = False
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._done = 0
                self._found = 0
                self._cleared = 0
                self._error = ""
                self._total = int(self.repos.db.scalar(
                    "SELECT count(*) FROM documents") or 0)
                self._thread = threading.Thread(
                    target=self._run, name="reportgen-quality", daemon=True)
                self._thread.start()
                started = True
        return self.status() if not started else self.status()

    def wait(self, timeout: float | None = None) -> None:
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self) -> None:
        try:
            offset = 0
            while True:
                rows = self.repos.documents.text_samples(limit=PAGE, offset=offset)
                if not rows:
                    break
                for row in rows:
                    self._check_one(row)
                offset += len(rows)
                with self._lock:
                    self._done = offset
        except Exception as error:              # noqa: BLE001 — поток не роняет приложение
            with self._lock:
                self._error = f"проверка прервалась: {error}"
        finally:
            release = getattr(self.repos.db, "release", None)
            if release is not None:
                try:
                    release()
                except Exception:               # noqa: BLE001
                    pass
            with self._lock:
                self._finished_at = time.monotonic()

    def _check_one(self, row: Dict[str, Any]) -> None:
        sample = str(row.get("sample") or "")
        # Пустой образец — это документ без фрагментов: разбираться с ним
        # надо иначе (скан без распознавания), и на склейку он не похож.
        verdict = TEXT_QUALITY_GLUED if sample and glued_text_warning(sample) else ""
        changed = self.repos.documents.set_meta_flag(
            row["id"], row.get("meta_json") or "{}", "text_quality", verdict)
        if not changed:
            return
        with self._lock:
            if verdict:
                self._found += 1
            else:
                # Документ перезалили нормально — пометку снимаем. Иначе она
                # висела бы вечно и человек чинил бы уже починенное.
                self._cleared += 1


def _hint(state: Dict[str, Any]) -> str:
    """Одна строка о проверке — та, что читает человек."""
    if state["error"]:
        return state["error"]
    if state["running"]:
        total = state["total"] or 0
        if not total:
            return "проверяю разбор документов"
        return f"проверяю разбор: {state['done']} из {total}"
    glued = state["glued"]
    if not state["finished_at"] and not glued:
        return ""
    if not glued:
        return "плохо разобранных документов не нашлось"
    return (f"плохо разобранных документов: {glued} — текст склеен без "
            f"пробелов, поиск по ним почти ничего не найдёт")
