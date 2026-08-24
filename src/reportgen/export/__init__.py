"""Экспорт готового отчёта во внешние форматы — слой 7 архитектуры (док. 01).

Пакет намеренно вынесен из ядра: ядро (facts/corpus/retrieval/pipeline/verify)
остаётся библиотекой без внешних зависимостей, а экспорт тянет ``python-docx``
и работает уже с готовым Markdown. Сам ``python-docx`` подгружается лениво,
поэтому импорт пакета безопасен и в контуре без установленных зависимостей.
"""

from __future__ import annotations

from .docx import (
    DRAFT_NOTICE,
    ExportOptions,
    MissingDependencyError,
    export_report,
    markdown_to_docx,
)

__all__ = [
    "DRAFT_NOTICE",
    "ExportOptions",
    "MissingDependencyError",
    "export_report",
    "markdown_to_docx",
]
