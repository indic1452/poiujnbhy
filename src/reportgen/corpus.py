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
from typing import Any, Dict, Iterable, Iterator, List, Sequence

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


#: Сколько нового текста помещается во фрагмент: остальное место занято
#: перекрытием с предыдущим фрагментом.
BODY_CHARS = TARGET_CHARS - OVERLAP_CHARS

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

#: Строка таблицы и разделитель под её шапкой. Шапку повторяем в каждом куске
#: разрезанной таблицы: без неё столбцы безымянные, и «40,5» во втором куске
#: непонятно к чему относится.
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

#: Конец предложения — по нему делим сплошной текст, снятый распознаванием.
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")

#: Начало предложения или строки в хвосте — по нему подравнивается перекрытие,
#: чтобы оно не начиналось с середины слова.
_OVERLAP_START = re.compile(r"(?:[.!?;]\s+|\n)")


#: Служебная пометка в строке: так приём вставляет номер страницы, приклеивая
#: его к первой строке текста этой страницы. Для разбора строки она невидима —
#: строка таблицы остаётся строкой таблицы. И повторять её нельзя: маркер,
#: размноженный вместе с шапкой таблицы, отправил бы все куски таблицы на
#: первую страницу книги.
_MARKUP_NOTE = re.compile(r"<!--.*?-->")


def _bare(line: str) -> str:
    """Строка без служебных пометок."""
    return _MARKUP_NOTE.sub("", line).strip()


def _is_row(line: str) -> bool:
    return bool(_TABLE_ROW.match(_bare(line)))


def _table_head(lines: Sequence[str]) -> List[str]:
    """Шапка таблицы: строка заголовков и разделитель под ней."""
    if len(lines) >= 2 and _is_row(lines[0]) and _TABLE_RULE.match(_bare(lines[1])):
        return [lines[0], lines[1]]
    return []


def _cut_to_size(part: str, limit: int) -> Iterator[str]:
    """Последнее средство: часть, которая и сама не влезает во фрагмент.

    Режем по границе слова, а если и слов нет (сплошной ряд цифр из таблицы,
    снятой распознаванием), — по счёту знаков. Молча выбросить остаток нельзя:
    он есть в документе, значит обязан быть и в указателе.
    """
    while len(part) > limit:
        cut = part.rfind(" ", limit // 2, limit)
        if cut <= 0:
            cut = limit
        head, part = part[:cut].strip(), part[cut:].strip()
        if head:
            yield head
    if part:
        yield part


def _parts_of(paragraph: str) -> "tuple[str, List[str], str]":
    """На что делить абзац: на строки (таблица, распознанный текст) или на
    предложения — и какая у него шапка, если это таблица."""
    lines = paragraph.split("\n")
    if len(lines) > 1:
        head = _table_head(lines)
        return "\n".join(head), lines[len(head):], "\n"
    return "", [p for p in _SENTENCE_END.split(paragraph) if p], " "


def _pieces_of(paragraph: str, limit: int = BODY_CHARS) -> Iterator[str]:
    """Абзац, разложенный на куски не длиннее ``limit``.

    Абзац короче предела отдаётся как есть — деление начинается там, где абзац
    сам по себе больше фрагмента. Раньше предела не было вовсе: условие
    ``if size and …`` при пустом буфере ложно всегда, поэтому первый абзац
    ложился в буфер целиком, какой бы он ни был. Скан книги, где абзац равен
    странице, давал фрагменты по 17 тысяч знаков, а до модели от такого
    фрагмента доходила восьмая часть — остальное отрезалось по
    ``assistant_source_chars``. Оговорка «таблицу не режем» защищала таблицу,
    но заодно накрывала любой длинный абзац.
    """
    if len(paragraph) <= limit:
        yield paragraph
        return
    head, parts, joiner = _parts_of(paragraph)
    # Шапка таблицы остаётся при своём куске — при первом. Остальным её
    # допишет тот, кто начнёт с них новый фрагмент (см. _split_long): пока
    # кусок лежит в одном фрагменте со своим началом, шапка у него уже над
    # головой. Место под неё держим в каждом куске, чтобы дописанная шапка
    # не выводила фрагмент за предел длины.
    room = max(limit - (len(head) + len(joiner) if head else 0), MIN_CHARS)
    buffer: List[str] = []
    size = 0
    first = True

    def collect() -> str:
        nonlocal first
        top = head if first else ""
        first = False
        return joiner.join([top, *buffer] if top else buffer)

    for part in parts:
        for unit in (_cut_to_size(part, room) if len(part) > room else (part,)):
            if buffer and size + len(joiner) + len(unit) > room:
                yield collect()
                buffer, size = [], 0
            buffer.append(unit)
            size += len(unit) + (len(joiner) if size else 0)
    if buffer:
        yield collect()


def _overlap(text: str) -> str:
    """Перекрытие: последние ``OVERLAP_CHARS`` знаков предыдущего фрагмента.

    Раньше в перекрытие уходил ЦЕЛЫЙ последний абзац, каким бы длинным он ни
    был: порог проверялся до вставки, поэтому один абзац попадал в хвост
    всегда. У скана книги абзац равен странице — и текст каждой страницы
    оседал в указателе дважды, а на замере смешанной библиотеки указатель
    держал 186% исходного текста вместо 118%. Лишнее место под векторы, лишнее
    время на их построение и выдача, забитая почти одинаковыми фрагментами.

    Берём знаки, а начало подравниваем по границе предложения или строки,
    чтобы перекрытие не начиналось с середины слова.
    """
    if len(text) <= OVERLAP_CHARS:
        return text
    tail = text[-OVERLAP_CHARS:]
    match = _OVERLAP_START.search(tail)
    if match and len(tail) - match.end() >= OVERLAP_CHARS // 2:
        tail = tail[match.end():]
    return tail.strip()


def _is_table(text: str) -> bool:
    """Похож ли кусок на строки таблицы."""
    return _is_row(text.split("\n", 1)[0])


def _starts_with_head(piece: str, head: str) -> bool:
    """Стоит ли шапка в начале куска. Пометку о странице в расчёт не берём:
    она приклеена к первой строке страницы и мешает сравнить строки как есть.
    """
    lines = piece.split("\n")[:len(head.split("\n"))]
    return "\n".join(_bare(line) for line in lines) == head


def _trim(paragraph: str) -> str:
    """Снять пустые строки вокруг абзаца, НЕ трогая отступ первой строки.

    Обычный strip() съедал отступ ровно у первой строки абзаца. В прозе это
    незаметно, а в битовой диаграмме — линейка разрядов уезжает на четыре знака
    влево относительно рамки под ней, и по такой картинке номер разряда уже не
    прочитать. Диаграмм в литературе отдела много.
    """
    return paragraph.strip("\n").rstrip()


def _split_long(text: str) -> Iterator[str]:
    """Режет длинный текст на фрагменты с перекрытием."""
    paragraphs = [_trim(p) for p in re.split(r"\n\s*\n", text) if p.strip()]
    buffer: List[str] = []
    # Размер СВОЕГО текста, без перекрытия: иначе фрагмент, у которого
    # перекрытие уже занимает место, отдавался бы одним перекрытием.
    size = 0
    # Шапка таблицы, которая тянется дальше своего абзаца. Разбирая PDF,
    # конвертер разрывает длинную таблицу по страницам: шапка остаётся на
    # первой, а дальше идут одни ряды цифр. Такой фрагмент бесполезен — «1,5»
    # без имени столбца не значит ничего, ни для поиска, ни для ответа.
    head = ""
    for paragraph in paragraphs:
        lines = paragraph.split("\n")
        found = _table_head(lines)
        if found:
            head = "\n".join(_bare(line) for line in found)
        elif not _is_table(paragraph):
            head = ""                        # таблица кончилась
        for piece in _pieces_of(paragraph):
            if size and size + len(piece) > BODY_CHARS:
                whole = "\n\n".join(buffer)
                yield whole
                carry = _overlap(whole)
                buffer = [carry] if carry else []
                size = 0
            if (not size and head and _is_table(piece)
                    and not _starts_with_head(piece, head)):
                piece = f"{head}\n{piece}"
            buffer.append(piece)
            size += len(piece) + 2
    if buffer:
        yield "\n\n".join(buffer)


def split_document(text: str) -> Iterator[tuple[List[str], str]]:
    """Режет Markdown по заголовкам, затем длинные секции — по абзацам."""
    stack: List[str] = []
    # Уровень каждого заголовка в стопке. Раньше стопка резалась по номеру
    # места (``del stack[level - 1:]``), а не по уровню, — и документ,
    # начинающийся с «###» (обычное дело после разбора DOCX, где H1 остался
    # в шапке), делал следующий «##» потомком предыдущего раздела: крошки
    # врали, а вместе с ними врала и ссылка под цитатой.
    levels: List[int] = []
    # Дал ли раздел хоть один фрагмент — свой или потомка. Раздел, который не
    # дал ничего, отдаём заголовком: документ из одних заголовков (перечень
    # контрольных точек, оглавление стандарта) принимался с нулём фрагментов
    # и терялся целиком, молча.
    produced: List[bool] = []
    body: List[str] = []

    def flush() -> Iterator[tuple[List[str], str]]:
        content = "\n".join(body).strip()
        if content:
            for piece in _split_long(content):
                yield list(stack), piece

    def close(level: int) -> Iterator[tuple[List[str], str]]:
        """Закрывает разделы глубже заданного уровня."""
        while levels and levels[-1] >= level:
            if not produced[-1]:
                yield list(stack), stack[-1]
                produced[:] = [True] * len(produced)
            stack.pop()
            levels.pop()
            produced.pop()

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if not heading:
            body.append(line)
            continue
        for item in flush():
            produced[:] = [True] * len(produced)
            yield item
        body.clear()
        level = len(heading.group(1))
        yield from close(level)
        stack.append(heading.group(2).strip())
        levels.append(level)
        produced.append(False)
    for item in flush():
        produced[:] = [True] * len(produced)
        yield item
    yield from close(1)


def _starts_new_page(text: str) -> bool:
    """Начинается ли кусок с новой страницы документа."""
    return bool(_MARKUP_NOTE.match(text.lstrip()))


def _other_section(title_path: Sequence[str], path: Sequence[str], text: str) -> bool:
    """Стоит ли на этом месте граница, через которую склеивать нельзя.

    Склейка коротких разделов вперёд — вещь нужная: без неё в указателе
    оседают фрагменты в одну строку. Но у слайда доклада, листа книги Excel и
    страницы сканированного доклада раздел совпадает со страницей, и склеенный
    через такую границу фрагмент получал и чужие крошки, и чужой номер
    страницы: текст со второго слайда лежал во фрагменте, подписанном первым.
    Человек шёл по ссылке и текста там не находил.

    Поэтому запрет узкий: разные разделы ВЕРХНЕГО уровня И новая страница.
    Обычные соседние разделы одного документа склеиваются как раньше.
    """
    return (bool(path) and bool(title_path) and title_path[0] != path[0]
            and _starts_new_page(text))


def merge_short_sections(
    pieces: Iterable[tuple[List[str], str]],
    min_chars: int = MIN_CHARS,
) -> Iterator[tuple[List[str], str]]:
    """Склеивает куцые разделы с СОСЕДНИМИ, а не бросает их поодиночке.

    Документ отдела редко устроен ровно: за заголовком «4. Приложения» идёт
    одна строка, за ней — таблица на две страницы. Раньше короткий раздел
    приклеивался только к ПРЕДЫДУЩЕМУ куску, и если предыдущего не было
    (раздел первый) или короткие шли подряд, в указателе оседали фрагменты
    в одну строку. Искать по такому фрагменту нечего: слов в нём меньше,
    чем в запросе, а место в выдаче он занимает.

    Здесь склейка идёт ВПЕРЁД: копим, пока не наберётся осмысленный размер,
    и только тогда отдаём. Заголовок приклеиваемого раздела дописываем
    строкой — иначе слова из него пропали бы из поиска вместе с ним.

    Куску, которому склеиваться вперёд не с чем — последнему в документе или
    последнему в своём разделе, — деваться было некуда, и он уходил в
    указатель какой есть. Отсюда фрагменты в 134, 175, 191 знак: «Приложение
    3», строка подписи, хвост оглавления. Такой кусок приклеиваем НАЗАД, к
    предыдущему фрагменту: в документе он идёт сразу за ним, и отдельного
    места в выдаче не заслуживает.
    """
    ready: List[tuple[List[str], str]] = []
    buffer: List[str] = []
    path: List[str] = []
    size = 0

    def close() -> None:
        nonlocal buffer, path, size
        if not buffer:
            return
        text = "\n\n".join(buffer)
        if ready and size < min_chars:
            # Заголовок хвоста — строкой в текст, по тому же правилу, что и
            # при склейке вперёд: у предыдущего фрагмента крошки свои, и без
            # этого «Приложение 3» пропало бы и из текста, и из крошек.
            previous_path, previous_text = ready[-1]
            head = path[-1] if path and path != previous_path else ""
            ready[-1] = (previous_path,
                         f"{previous_text}\n\n{head}\n{text}" if head
                         else f"{previous_text}\n\n{text}")
        else:
            ready.append((path, text))
        buffer, path, size = [], [], 0

    for title_path, text in pieces:
        text = _trim(text)
        if not text:
            continue
        if buffer and _other_section(title_path, path, text):
            close()
        if not buffer:
            path = list(title_path)
            buffer = [text]
            size = len(text)
        else:
            # Заголовок склеиваемого раздела — частью текста: «4. Приложения»
            # это тоже слова, по которым ищут.
            head = title_path[-1] if title_path and title_path != path else ""
            piece = f"{head}\n{text}" if head else text
            buffer.append(piece)
            size += len(piece)
        if size >= min_chars:
            close()
    close()
    return iter(ready)


def load_file(path: Path, root: Path) -> List[Chunk]:
    text = path.read_text(encoding="utf-8")
    meta, text = parse_front_matter(text)
    relative = path.relative_to(root)
    doc_type = relative.parts[0] if relative.parts[0] in DOC_TYPES else "literature"
    doc_id = str(relative.with_suffix("")).replace("\\", "/")
    meta.setdefault("title", doc_id.rsplit("/", 1)[-1])
    meta["path"] = str(relative)

    chunks: List[Chunk] = []
    for index, (title_path, body) in enumerate(
            merge_short_sections(split_document(text))):
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
