"""Запись файла целиком «атомарно»: читатель видит либо прежний файл, либо новый.

Файл пишется рядом во временный и подменяет прежний (``os.replace``). На Windows
подмена отказывает («[WinError 5] Отказано в доступе»), пока прежний файл открыт
кем-то ещё: другим потоком сервера, который его как раз читает, антивирусом,
индексатором. Такие отказы короткие — подмена повторяется с растущей паузой.
Временное имя у каждой записи своё: две одновременные записи одного файла не
пишут в один и тот же временный.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from itertools import count
from pathlib import Path

#: Паузы между попытками подменить файл, с: всего около пяти секунд.
ПАУЗЫ = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.0, 1.0)

_номера = count()


def _временный(путь: Path) -> Path:
    return путь.with_name(f"{путь.name}.{os.getpid()}-{threading.get_ident()}-{next(_номера)}.tmp")


def записать_атомарно(путь: Path | str, данные: str | bytes, *, кодировка: str = "utf-8",
                      заменить: Callable[[Path, Path], None] = os.replace,
                      ждать: Callable[[float], None] = time.sleep) -> None:
    """Записать ``данные`` (текст или байты) в ``путь`` целиком, подменой временного файла."""
    путь = Path(путь)
    временный = _временный(путь)
    try:
        if isinstance(данные, bytes):
            временный.write_bytes(данные)
        else:
            временный.write_text(данные, encoding=кодировка)
        for пауза in (*ПАУЗЫ, None):
            try:
                заменить(временный, путь)
                return
            except PermissionError:
                if пауза is None:
                    raise
                ждать(пауза)
    finally:
        временный.unlink(missing_ok=True)
