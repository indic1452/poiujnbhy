"""Приём документов библиотеки: PDF/DOCX/TXT/MD → Markdown → чанки → база.

Точка входа для остальных слоёв — :func:`ingest_path` и :func:`ingest_directory`.
Тяжёлые зависимости (PyMuPDF, python-docx) импортируются лениво внутри
конвертеров, поэтому сам пакет можно импортировать в контуре, где их нет.
"""

from __future__ import annotations

from .convert import (
    ConvertedDocument,
    MissingDependencyError,
    convert_file,
    guess_doc_type,
    page_marker,
    page_markers,
    sha256_file,
    strip_page_markers,
)
from .pipeline import (
    DEFAULT_PATTERNS,
    IngestResult,
    chunks_from_markdown,
    ingest_directory,
    ingest_path,
    iter_library_files,
    library_stats,
    remove_document,
)

__all__ = [
    "SUPPORTED_SUFFIXES",
    "DEFAULT_PATTERNS",
    "ConvertedDocument",
    "IngestResult",
    "MissingDependencyError",
    "chunks_from_markdown",
    "convert_file",
    "guess_doc_type",
    "ingest_directory",
    "ingest_path",
    "iter_library_files",
    "library_stats",
    "page_marker",
    "page_markers",
    "remove_document",
    "sha256_file",
    "strip_page_markers",
]


def __getattr__(name: str) -> object:
    """SUPPORTED_SUFFIXES вычисляется по реестру, поэтому реэкспорт ленивый."""
    if name == "SUPPORTED_SUFFIXES":
        from .convert import supported_suffixes

        return supported_suffixes()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
