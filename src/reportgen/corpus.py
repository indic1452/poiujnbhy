"""Загрузка библиотеки и нарезка её на чанки.

Тип документа определяется именем каталога верхнего уровня внутри корпуса:

    corpus/
      literature/   — техническая литература (индекс A)
      standards/    — стандарты, ГОСТ, рекомендации (индекс A)
      datasheets/   — даташиты и руководства (индекс A)
      reports/      — прошлые отчёты (индекс B)
      regulations/  — внутренние регламенты и глоссарий (индекс C)

В начале файла допустим простой блок метаданных между строками '---':

    ---
    title: Руководство по эксплуатации XYZ
    vendor: ACME
    protocol: E1
    year: 2019
    ---

Реальный конвейер подставляет сюда результат конвертации PDF/DOCX → Markdown
(см. док. 02, раздел про разбор PDF); формат чанка при этом не меняется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence

#: Типы документов библиотеки. «misc» — полка для того, что не относится
#: ни к одному из остальных: без неё такие файлы молча становились
#: «литературой» и портили выдачу по ней.
DOC_TYPES = ("literature", "standards", "datasheets", "reports", "regulations", "misc")

#: Состояния документа, которые участвуют в поиске. Заменённая редакция
#: стандарта в выдачу не идёт: сослаться на отменённую норму в отчёте — тот
#: самый случай, ради которого система и заводилась. Константа живёт в ядре,
#: потому что фильтруют по ней оба поиска: и по базе, и по файловому указателю.
SEARCHABLE_STATUSES = ("current",)

TARGET_CHARS = 2200
OVERLAP_CHARS = 250
MIN_CHARS = 200

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Chunk:
    """Фрагмент документа вместе со «хлебными крошками» и метаданными."""

    chunk_id: str
    doc_id: str
    doc_type: str
    title_path: List[str]
    text: str
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def breadcrumbs(self) -> str:
        return " → ".join(self.title_path)

    @property
    def indexed_text(self) -> str:
        """Текст, который попадает в поисковый индекс.

        Крошки добавляются в индексируемый текст намеренно: запрос «маска
        передатчика» должен находить раздел, даже если в теле раздела этих
        слов нет, а есть только в заголовке.
        """
        return f"{self.breadcrumbs}\n{self.text}"

    @property
    def citation(self) -> str:
        """Ссылка вида «Документ, с. 42 — Глава → Раздел»."""
        page = self.meta.get("page")
        suffix = f", с. {page}" if page else ""
        title = self.meta.get("title", self.doc_id)
        # Первый элемент крошек — название документа, в ссылке оно уже есть.
        path = " → ".join(self.title_path[1:]) if len(self.title_path) > 1 else ""
        return f"{title}{suffix}" + (f" — {path}" if path else "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "title_path": self.title_path,
            "text": self.text,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Chunk":
        return cls(
            chunk_id=raw["chunk_id"],
            doc_id=raw["doc_id"],
            doc_type=raw["doc_type"],
            title_path=list(raw["title_path"]),
            text=raw["text"],
            meta=dict(raw.get("meta", {})),
        )


def parse_front_matter(text: str) -> tuple[Dict[str, str], str]:
    """Разбирает простой блок 'ключ: значение' в начале файла."""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    meta: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[match.end():]


def _split_long(text: str) -> Iterator[str]:
    """Режет длинный текст по абзацам с перекрытием."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    buffer: List[str] = []
    size = 0
    for paragraph in paragraphs:
        # Таблицу не режем: разорванная пополам таблица бесполезна для поиска.
        if size and size + len(paragraph) > TARGET_CHARS:
            yield "\n\n".join(buffer)
            tail: List[str] = []
            tail_size = 0
            for previous in reversed(buffer):
                if tail_size >= OVERLAP_CHARS:
                    break
                tail.insert(0, previous)
                tail_size += len(previous)
            buffer = tail
            size = tail_size
        buffer.append(paragraph)
        size += len(paragraph)
    if buffer:
        yield "\n\n".join(buffer)


def split_document(text: str) -> Iterator[tuple[List[str], str]]:
    """Режет Markdown по заголовкам, затем длинные секции — по абзацам."""
    stack: List[str] = []
    body: List[str] = []

    def flush() -> Iterator[tuple[List[str], str]]:
        content = "\n".join(body).strip()
        if content:
            for piece in _split_long(content):
                yield list(stack), piece

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            yield from flush()
            body.clear()
            level = len(heading.group(1))
            del stack[level - 1:]
            stack.append(heading.group(2).strip())
        else:
            body.append(line)
    yield from flush()


def load_file(path: Path, root: Path) -> List[Chunk]:
    text = path.read_text(encoding="utf-8")
    meta, text = parse_front_matter(text)
    relative = path.relative_to(root)
    doc_type = relative.parts[0] if relative.parts[0] in DOC_TYPES else "literature"
    doc_id = str(relative.with_suffix("")).replace("\\", "/")
    meta.setdefault("title", doc_id.rsplit("/", 1)[-1])
    meta["path"] = str(relative)

    chunks: List[Chunk] = []
    for index, (title_path, body) in enumerate(split_document(text)):
        if len(body) < MIN_CHARS and chunks:
            # Короткий хвост присоединяем к предыдущему чанку, чтобы не
            # засорять индекс обрывками в одну строку.
            previous = chunks[-1]
            chunks[-1] = Chunk(
                chunk_id=previous.chunk_id,
                doc_id=previous.doc_id,
                doc_type=previous.doc_type,
                title_path=previous.title_path,
                text=f"{previous.text}\n\n{body}",
                meta=previous.meta,
            )
            continue
        # Заголовок первого уровня обычно дублирует название документа —
        # в крошках он лишний.
        tail = [t for t in title_path if not meta["title"].lower().startswith(t.lower())]
        full_path = [meta["title"], *tail]
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#{index:04d}",
                doc_id=doc_id,
                doc_type=doc_type,
                title_path=full_path,
                text=body,
                meta=dict(meta),
            )
        )
    return chunks


def load_corpus(root: str | Path, patterns: tuple[str, ...] = ("*.md", "*.txt")) -> List[Chunk]:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"каталог корпуса не найден: {root}")
    chunks: List[Chunk] = []
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            if len(path.relative_to(root).parts) == 1:
                # Файлы в корне корпуса — это README и служебные заметки,
                # в индекс они не попадают.
                continue
            chunks.extend(load_file(path, root))
    return chunks


# ------------------------------------------------- подготовка цитат ------

#: Признаки того, что в строке значима не только последовательность слов, но и
#: их положение: рамка диаграммы, разделитель таблицы, колонки, выровненные
#: пробелами.
_LAYOUT_MARKS = ("|", "+-", "-+", "──", "│", "┼")

#: Столько пробелов подряд — это уже колонка, а не двойной пробел после точки.
_COLUMN_GAP = 3

#: Сколько строк с колонками должно набраться, чтобы считать фрагмент таблицей.
#: Одна такая строка — обычно просто рваные пробелы после распознавания.
_MIN_COLUMN_LINES = 2


def _has_frame(line: str) -> bool:
    """Рамка диаграммы или разделитель таблицы."""
    stripped = line.strip()
    return bool(stripped) and any(mark in stripped for mark in _LAYOUT_MARKS)


def _has_columns(line: str) -> bool:
    """Строка, разбитая на колонки пробелами."""
    return " " * _COLUMN_GAP in line.strip()


def _is_structured(lines: Sequence[str]) -> bool:
    """Значимо ли во фрагменте положение символов, а не только их порядок."""
    if any(_has_frame(line) for line in lines):
        return True
    return sum(1 for line in lines if _has_columns(line)) >= _MIN_COLUMN_LINES


def tidy_quote(text: str, limit: int) -> str:
    """Фрагмент документа для промпта и панели источников.

    Схлопывать переводы строк нельзя: таблицы допусков в стандартах и поля
    кадров в описаниях протоколов — это как раз то, ради чего фрагмент нашли.
    Но и пробелы ВНУТРИ строки схлопывать нельзя ровно по той же причине, а
    раньше они схлопывались. В RFC разрядность поля задана ШИРИНОЙ ячейки в
    битовой диаграмме::

        |V=2|P|X|  CC   |M|     PT      |       sequence number         |

    После выравнивания по одному пробелу от неё остаётся ``|V=2|P|X| CC |M|
    PT | sequence number |`` — ширины больше ничего не значат, и модели
    приходится угадывать разрядность. А именно её инженер и спрашивал.
    Колоночная таблица «Frame Count      8 bits     0..255» слипается в
    «Frame Count 8 bits 0..255», где граница между многословным названием поля
    и его длиной уже неопределима.

    Поэтому строки с разметкой (рамки, разделители, колонки) сохраняются как
    есть, а обычная проза по-прежнему выравнивается: в ней лишние пробелы —
    это мусор распознавания, и они только занимают место в промпте.

    Общий отступ блока снимается: он ничего не значит, а места занимает.
    """
    lines = [line.rstrip() for line in (text or "").strip("\n").splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return ""

    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    common = min(indents) if indents else 0

    # Решение принимается один раз на весь фрагмент. Построчно нельзя: линейка
    # разрядов «0 1 2 3 4 5 …» набрана ОДИНОЧНЫМИ пробелами и признаков
    # разметки в себе не несёт, но выровнена она по ячейкам диаграммы строкой
    # ниже. Выправить её отдельно — значит сдвинуть на символ и соврать про
    # номера битов.
    structured = _is_structured([line[common:] if common else line for line in lines])

    cleaned: List[str] = []
    for line in lines:
        if not line.strip():
            if cleaned and not cleaned[-1]:
                continue
            cleaned.append("")
            continue
        body = line[common:] if common else line
        cleaned.append(body if structured else " ".join(body.split()))

    # strip() здесь применять нельзя: он срезал бы ведущий пробел ПЕРВОЙ
    # строки, а в битовой диаграмме это сдвиг линейки разрядов относительно
    # ячеек на один символ — то есть ровно та ошибка, от которой всё и
    # затевалось.
    quote = "\n".join(cleaned).strip("\n")
    return quote if len(quote) <= limit else quote[:limit].rstrip() + "…"
