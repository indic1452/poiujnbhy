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
from typing import Any, Dict, Iterator, List

DOC_TYPES = ("literature", "standards", "datasheets", "reports", "regulations")

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
