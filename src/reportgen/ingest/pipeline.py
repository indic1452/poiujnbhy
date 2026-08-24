"""Приём документов библиотеки: файл → Markdown → чанки → база.

Это слой между «что лежит на диске у инженеров» и хранилищем. Он отвечает за
три вещи, которых нет ни в конвертере, ни в репозиториях:

* **инкрементальность** — файл, который не изменился с прошлого раза (совпал
  SHA-256), заново не разбирается: полная переиндексация библиотеки на
  несколько тысяч PDF занимает часы, а меняются обычно единицы файлов;
* **атомарность документа** — чанки документа всегда заменяются целиком
  (``ChunkRepo.replace_for_document``), поэтому в индексе не остаётся половины
  старой редакции стандарта рядом с половиной новой;
* **честный отчёт о приёме** — :class:`IngestResult` с числами и списком
  предупреждений: что взяли, что пропустили, что требует OCR.

Числа в отчёты заказчику из библиотеки не попадают (это инвариант из док. 01):
библиотека нужна для формулировок и ссылок на нормативы, а все значения
приходят из факт-пакета.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Sequence, Tuple

from .. import corpus
from ..corpus import Chunk
from . import convert
from .convert import ConvertedDocument, convert_file, guess_doc_type, sha256_file

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from ..store.repo import Repositories

__all__ = [
    "IngestResult",
    "DEFAULT_PATTERNS",
    "chunks_from_markdown",
    "ingest_path",
    "ingest_directory",
    "remove_document",
]

#: Устаревшая константа: осталась для совместимости. Настоящий список форматов
#: берётся из реестра конвертеров — см. library_patterns().
DEFAULT_PATTERNS: Tuple[str, ...] = ("*.pdf", "*.docx", "*.md", "*.txt")


def library_patterns(*, only_available: bool = True) -> Tuple[str, ...]:
    """Маски файлов по всем зарегистрированным конвертерам.

    По умолчанию берутся только доступные форматы: незачем поднимать со всего
    каталога файлы, которые всё равно не разобрать без недостающего пакета.
    """
    from .convert import supported_suffixes

    suffixes = supported_suffixes(only_available=only_available)
    return tuple(f"*{suffix}" for suffix in suffixes) or DEFAULT_PATTERNS

#: Служебные файлы, которые Word и файловые менеджеры оставляют рядом с документами.
_SKIP_PREFIXES = ("~$", ".")

ProgressFn = Callable[[str], None]


@dataclass
class IngestResult:
    """Итог приёма одного файла или целого каталога."""

    added: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    #: doc_id документов, которые были проиндексированы в этом запуске.
    documents: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.added + self.updated + self.skipped + self.failed

    @property
    def indexed(self) -> int:
        return self.added + self.updated

    def merge(self, other: "IngestResult") -> "IngestResult":
        """Присоединяет итог по одному файлу к общему итогу по каталогу."""
        self.added += other.added
        self.updated += other.updated
        self.skipped += other.skipped
        self.failed += other.failed
        self.chunks += other.chunks
        self.documents.extend(other.documents)
        self.warnings.extend(other.warnings)
        return self

    def summary(self) -> str:
        """Человекочитаемый итог — то, что печатает CLI и показывает веб."""
        if not self.total:
            return "Приём документов: подходящих файлов не найдено."
        parts = [
            f"добавлено {self.added}",
            f"обновлено {self.updated}",
            f"без изменений {self.skipped}",
            f"с ошибками {self.failed}",
        ]
        text = (
            f"Приём документов: обработано файлов {self.total} "
            f"({', '.join(parts)}); записано чанков: {self.chunks}."
        )
        if self.warnings:
            text += f" Предупреждений: {len(self.warnings)}."
        return text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "chunks": self.chunks,
            "documents": list(self.documents),
            "warnings": list(self.warnings),
            "summary": self.summary(),
        }


# ------------------------------------------------------------- нарезка ----

def _page_bounds(piece: str, current: int | None) -> Tuple[int | None, int | None]:
    """Страница, на которой начинается фрагмент, и страница, на которой он кончается."""
    markers = convert.page_markers(piece)
    if not markers:
        return current, current
    first_page, offset = markers[0]
    if current is None or not piece[:offset].strip():
        return first_page, markers[-1][0]
    # Фрагмент начался ещё на предыдущей странице — ссылаться надо на неё.
    return current, markers[-1][0]


def chunks_from_markdown(
    text: str,
    doc_id: str,
    doc_type: str = convert.DEFAULT_DOC_TYPE,
    meta: Dict[str, Any] | None = None,
) -> List[Chunk]:
    """Режет Markdown на чанки ровно так же, как это делает `corpus.load_file`.

    Отличий от корпуса два, и оба нужны для приёма произвольных файлов:
    метаданные приходят снаружи (из конвертера, а не только из front matter) и
    в текст могут быть вставлены маркеры страниц — они вырезаются, а номер
    страницы попадает в ``meta['page']`` и потом в ссылку под цитатой.
    """
    front, body = corpus.parse_front_matter(text)
    merged: Dict[str, Any] = dict(front)
    for key, value in (meta or {}).items():
        if value is not None:
            merged[key] = value
    merged.setdefault("title", doc_id.rsplit("/", 1)[-1])
    merged.setdefault("path", doc_id)
    title = str(merged["title"])

    chunks: List[Chunk] = []
    page: int | None = None
    for title_path, piece in corpus.split_document(body):
        start_page, page = _page_bounds(piece, page)
        cleaned = convert.strip_page_markers(piece)
        if not cleaned:
            continue
        if len(cleaned) < corpus.MIN_CHARS and chunks:
            # Короткий хвост присоединяем к предыдущему чанку — как в корпусе,
            # чтобы не засорять индекс обрывками в одну строку.
            previous = chunks[-1]
            previous.text = f"{previous.text}\n\n{cleaned}"
            continue
        # Заголовок первого уровня обычно дублирует название документа —
        # в крошках он лишний.
        tail = [step for step in title_path if not title.lower().startswith(step.lower())]
        chunk_meta = dict(merged)
        if start_page:
            chunk_meta["page"] = start_page
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#{len(chunks):04d}",
                doc_id=doc_id,
                doc_type=doc_type,
                title_path=[title, *tail],
                text=cleaned,
                meta=chunk_meta,
            )
        )
    return chunks


# --------------------------------------------------------- приём файла ----

def _doc_id_for(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        relative = Path(path.name)
    return str(relative.with_suffix("")).replace("\\", "/")


def _relative_for(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path.name


def ingest_path(
    repos: "Repositories",
    path: str | Path,
    *,
    root: str | Path | None = None,
    doc_type: str | None = None,
    confidentiality: str = "internal",
    force: bool = False,
    domain: str | None = None,
) -> IngestResult:
    """Принимает один файл: SHA-256 → конвертация → чанки → база.

    ``domain`` — направление техники (спутник, релейка, протоколы …). Если не
    задано, определяется автоматически по названию и тексту документа
    (:mod:`reportgen.domains`). При неуверенности остаётся пустым — и тогда
    документ находится только поиском БЕЗ фильтра по направлению: с фильтром
    он невидим. Поэтому долю неразмеченных документов надо держать низкой
    (док. 13) и доразмечать вручную через интерфейс библиотеки.

    Если документ с тем же ``doc_id`` уже проиндексирован и его SHA-256 не
    изменился, файл пропускается (``skipped``) — кроме случая ``force=True``,
    когда он переиндексируется принудительно (например, после изменения правил
    нарезки).
    """
    result = IngestResult()
    path = Path(path)
    base = Path(root) if root is not None else path.parent
    label = _relative_for(path, base)

    if not path.is_file():
        result.failed = 1
        result.warnings.append(f"{label}: файл не найден")
        return result

    try:
        digest = sha256_file(path)
    except OSError as error:
        result.failed = 1
        result.warnings.append(f"{label}: файл не прочитан ({error})")
        return result

    doc_id = _doc_id_for(path, base)
    existing = repos.documents.by_doc_id(doc_id)
    if existing is not None and existing.sha256 == digest and existing.indexed_at and not force:
        result.skipped = 1
        return result
    if existing is not None and Path(existing.source_path).suffix.lower() != path.suffix.lower():
        result.warnings.append(
            f"{label}: идентификатор документа «{doc_id}» уже занят файлом "
            f"{existing.source_path} — прежняя версия будет заменена"
        )

    converted = convert_file(path)
    result.warnings.extend(f"{label}: {warning}" for warning in converted.warnings)
    if converted.is_empty:
        result.failed = 1
        if not converted.warnings:
            result.warnings.append(f"{label}: не удалось извлечь текст")
        return result

    resolved_type = doc_type or guess_doc_type(path, base)
    title = converted.title.strip() or doc_id.rsplit("/", 1)[-1]
    meta = _document_meta(converted, title=title, relative=_relative_for(path, base))
    resolved_domain = domain if domain is not None else _detect_domain(title, converted.text)
    if resolved_domain:
        meta["domain"] = resolved_domain

    document = repos.documents.upsert(
        doc_id=doc_id,
        doc_type=resolved_type,
        title=title,
        source_path=str(path.resolve()),
        sha256=digest,
        confidentiality=confidentiality,
        meta=meta,
        domain=resolved_domain,
    )
    chunks = chunks_from_markdown(converted.text, doc_id, resolved_type, meta)
    result.chunks = repos.chunks.replace_for_document(document, chunks)
    result.documents.append(doc_id)
    if existing is None:
        result.added = 1
    else:
        result.updated = 1
    return result


def _detect_domain(title: str, text: str) -> str:
    """Определить направление документа. Ошибка классификатора не критична:
    при неуверенности возвращается пустая строка, и фильтр просто не применяется."""
    try:
        from ..domains import registry  # noqa: PLC0415

        return registry().classify(title, text)
    except Exception:  # noqa: BLE001 — справочник направлений не должен ломать приём
        return ""


def _document_meta(converted: ConvertedDocument, *, title: str, relative: str) -> Dict[str, Any]:
    """Метаданные документа: то, что уйдёт и в карточку документа, и в каждый чанк."""
    meta: Dict[str, Any] = {
        key: value
        for key, value in converted.meta.items()
        if value not in (None, "", 0) and key not in ("title", "path")
    }
    meta["title"] = title
    meta["path"] = relative
    if converted.page_count:
        meta["page_count"] = converted.page_count
    return meta


# ------------------------------------------------------- приём каталога ---

def _iter_library_files(root: Path, patterns: Sequence[str]) -> List[Path]:
    """Файлы корпуса: рекурсивно, без служебных и без файлов в корне."""
    found: Dict[str, Path] = {}
    for pattern in patterns:
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if len(relative.parts) == 1:
                # Файлы в корне корпуса — это README и служебные заметки,
                # в индекс они не попадают (как в corpus.load_corpus).
                continue
            if any(part.startswith(_SKIP_PREFIXES) for part in relative.parts):
                continue
            found[str(relative).replace("\\", "/")] = path
    return [found[key] for key in sorted(found)]


def ingest_directory(
    repos: "Repositories",
    root: str | Path,
    *,
    patterns: Sequence[str] | None = None,
    force: bool = False,
    progress: ProgressFn | None = None,
    confidentiality: str = "internal",
    domain: str | None = None,
) -> IngestResult:
    """Принимает каталог библиотеки целиком.

    Тип документа берётся из имени каталога верхнего уровня
    (``standards/…`` → ``standards``), файлы в корне корпуса пропускаются.
    ``progress`` вызывается перед разбором каждого файла — CLI печатает это в
    консоль, веб пишет в журнал приёма.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"каталог корпуса не найден: {root}")

    files = _iter_library_files(root, tuple(patterns) if patterns else library_patterns())
    result = IngestResult()
    for number, path in enumerate(files, start=1):
        relative = _relative_for(path, root)
        if progress is not None:
            progress(f"[{number}/{len(files)}] {relative}")
        result.merge(
            ingest_path(
                repos,
                path,
                root=root,
                force=force,
                confidentiality=confidentiality,
                domain=domain,
            )
        )
    return result


def remove_document(repos: "Repositories", doc_id: str) -> bool:
    """Удаляет документ вместе с чанками, эмбеддингами и записями FTS.

    Возвращает ``False``, если такого документа в библиотеке не было.
    """
    if repos.documents.by_doc_id(doc_id) is None:
        return False
    repos.documents.delete(doc_id)
    return True


def library_stats(repos: "Repositories") -> Dict[str, Any]:
    """Сводка по библиотеке: сколько документов и чанков по каждому типу."""
    stats = repos.documents.stats()
    return {
        "by_type": stats,
        "documents": sum(item["documents"] for item in stats.values()),
        "chunks": repos.chunks.count(),
    }


def iter_library_files(root: str | Path, patterns: Iterable[str] | None = None) -> List[Path]:
    """Список файлов каталога, которые попадут в приём (для предпросмотра в UI)."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"каталог корпуса не найден: {root}")
    return _iter_library_files(root, tuple(patterns) if patterns else library_patterns())
