# -*- coding: utf-8 -*-
"""Как поставить недостающий пакет на ЭТОЙ машине.

Совет «pip install python-pptx» отделу не помогает ничем. Интернета в контуре
нет, и pip уйдёт в PyPI и умрёт по таймауту. Совет «py -m pip install ...»
хуже вдвое: py — это системный запускатель, а приложение работает в своём
окружении .venv, и пакет уехал бы не туда, где его ищут.

Поэтому подсказку собираем из того, что известно в момент беды: тем же
интерпретатором, которым запущено приложение, и из каталога колёс, если он
рядом. Получается команда, которую можно скопировать и выполнить, а не
пожелание.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["pip_hint", "wheels_dir"]

#: Где обычно лежит каталог колёс офлайн-комплекта. Порядок — от съёмного
#: носителя, с которого ставят, к развёрнутой на диске копии.
WHEEL_PLACES = (
    r"D:\reportgen-offline\wheels",
    r"E:\reportgen-offline\wheels",
    r"C:\reportgen\reportgen-offline\wheels",
    "/opt/reportgen-offline/wheels",
)


def wheels_dir() -> "Path | None":
    """Каталог колёс комплекта, если он на месте."""
    указан = os.environ.get("REPORTGEN_WHEELS", "").strip()
    if указан and Path(указан).is_dir():
        return Path(указан)
    for место in WHEEL_PLACES:
        путь = Path(место)
        if путь.is_dir():
            return путь
    return None


def pip_hint(package: str) -> str:
    """Готовая команда установки пакета — с оговоркой, если колёс не видно."""
    интерпретатор = sys.executable or "python"
    колёса = wheels_dir()
    if колёса:
        return '"%s" -m pip install --no-index --find-links "%s" %s' % (
            интерпретатор, колёса, package)
    return ('"%s" -m pip install --no-index --find-links <каталог колёс> %s '
            "(каталог колёс — в комплекте офлайн-установки, обычно "
            "D:\\reportgen-offline\\wheels)" % (интерпретатор, package))
