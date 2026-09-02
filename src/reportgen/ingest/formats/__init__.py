"""Конвертеры отдельных форматов.

Каждый модуль регистрирует себя в :mod:`reportgen.ingest.registry` при импорте.
Импорт модуля не должен требовать сторонних пакетов: тяжёлые зависимости
подключаются лениво, внутри функции конвертации, — иначе один недостающий
пакет лишил бы систему всех остальных форматов.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

#: Порядок не важен: каждый модуль регистрируется сам.
_MODULES = (
    "office",
    "opendoc",
    "legacy",
    "word97",
    "djvu",
    "ocr",
    "web",
    "archive",
    "rfc",
)

for _name in _MODULES:
    try:
        importlib.import_module(f"{__name__}.{_name}")
    except Exception as _error:  # noqa: BLE001 — один сломанный формат не должен ронять остальные
        logger.warning("модуль формата %s не загружен: %s", _name, _error)
