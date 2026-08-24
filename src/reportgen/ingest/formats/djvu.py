"""DjVu: сканы книг, с текстовым слоем и без него.

DjVu — основной формат русской технической литературы в библиотеке заказчика.
Справочник по антенно-фидерным устройствам, методичка по настройке станции,
сборник ГОСТов — всё это сканы, склеенные в DjVu ради размера. Часть из них
прогнали через FineReader, и там есть текстовый слой; остальные — картинки.

Порядок разбора отсюда и следует:

1. :func:`djvu_page_count` — сколько в книге страниц (``djvused``, при неудаче
   ``djvutxt --detail=page``);
2. :func:`djvu_text_layer` — текстовый слой через ``djvutxt``. Он точный и
   достаётся мгновенно, поэтому пробуется всегда и первым. Слой проверяется на
   осмысленность: страница, где нашлось меньше :data:`MIN_LAYER_CHARS`
   символов, считается страницей без текста — обычно это колонцифра,
   случайно попавшая в слой;
3. страницы без слоя рендерятся ``ddjvu`` в PNM и распознаются через
   :mod:`reportgen.ingest.formats.ocr`. Распознавание в системе одно и то же
   для PDF, картинок и DjVu — иначе качество разошлось бы по форматам.

Все внешние вызовы идут с таймаутом, временные файлы удаляются в любом случае,
а любая беда — битый файл, отсутствие ``ddjvu``, зависший процесс — становится
предупреждением по-русски, а не исключением: приём каталога на тысячу файлов
не должен падать на одном скане.

Почему число страниц не берётся из ``ddjvu``: номер за пределами книги он
молча приводит к существующей странице и завершается успешно, так что
перебором границу не нащупать — вместо этого вернётся дубль последней
страницы. ``djvused -e n`` отвечает на вопрос прямо, а ``djvutxt`` печатает
по выражению на страницу и годится запасным вариантом.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from .. import registry
from ..convert import external_path, ConvertedDocument, _first_markdown_heading, _reason, page_marker
from . import ocr

__all__ = [
    "DJVU_SUFFIXES",
    "DjvuToolError",
    "convert_djvu",
    "djvu_binary",
    "djvu_page_count",
    "djvu_text",
    "djvu_text_layer",
    "render_djvu_page",
]

DJVU_SUFFIXES = (".djvu", ".djv")

#: Таймаут на служебный вызов (djvused, djvutxt). Эти программы читают
#: оглавление и текстовый слой — секунды; минута с запасом.
TOOL_TIMEOUT = 60.0

#: Таймаут на рендеринг одной страницы. Разворот А3 в 300 dpi декодируется
#: заметно дольше обычной страницы, поэтому запас больше.
RENDER_TIMEOUT = 180.0

#: Разрешение рендеринга для распознавания. Меньше 300 dpi tesseract на
#: кириллице начинает путать «н» и «и», больше — только замедляет работу.
RENDER_DPI = 300

#: Порог осмысленности текстового слоя на странице.
MIN_LAYER_CHARS = ocr.MIN_PAGE_CHARS

#: Сколько страниц распознаём за один приём файла (см. :mod:`...formats.ocr`).
MAX_OCR_PAGES = ocr.MAX_OCR_PAGES

#: Предел на вычитывание текстового слоя. Один вызов djvutxt на страницу стоит
#: миллисекунды, но на десятитысячестраничной подшивке и они складываются.
MAX_TEXT_PAGES = 2000

#: Сколько неудач подряд терпим, прежде чем бросить книгу.
MAX_CONSECUTIVE_FAILURES = ocr.MAX_CONSECUTIVE_FAILURES

#: Как поставить djvulibre. Уходит в диагностику реестра, поэтому оба контура.
DJVULIBRE_HINT = (
    "пакет djvulibre: Linux — apt install djvulibre-bin (или dnf install "
    "djvulibre); Windows — DjVuLibre с sourceforge.net/projects/djvu, каталог "
    r"«C:\Program Files (x86)\DjVuZone\DjVuLibre» добавить в PATH"
)

#: Куда установщик DjVuLibre кладёт программы на Windows, если PATH не правили.
_WINDOWS_DIRS = (
    r"C:\Program Files\DjVuZone\DjVuLibre",
    r"C:\Program Files (x86)\DjVuZone\DjVuLibre",
    r"C:\Program Files\DjVuLibre",
    r"C:\Program Files (x86)\DjVuLibre",
)

#: Строка вывода ``djvutxt --detail=page``, открывающая очередную страницу.
#: Страница без текста печатается как «()», с текстом — как «(page …».
_PAGE_LINE_RE = re.compile(rb"^\(")


class DjvuToolError(RuntimeError):
    """Внешняя программа djvulibre не смогла отработать."""


# ------------------------------------------------------ вызов инструментов ---

def djvu_binary(name: str) -> str | None:
    """Путь к программе djvulibre или ``None``.

    Сначала PATH, затем — на Windows — каталоги установщика: DjVuLibre PATH
    после установки не правит, а искать программу руками в изолированном
    контуре некому.
    """
    found = shutil.which(name)
    if found:
        return found
    if os.name != "nt":
        return None
    for directory in _WINDOWS_DIRS:
        candidate = Path(directory) / f"{name}.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _run(name: str, arguments: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    """Запустить программу djvulibre с таймаутом. Любая беда — :class:`DjvuToolError`."""
    binary = djvu_binary(name)
    if binary is None:
        raise DjvuToolError(f"не найдена программа {name}; {DJVULIBRE_HINT}")
    try:
        return subprocess.run(
            [binary, *arguments], capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise DjvuToolError(
            f"{name} не уложился в {float(timeout):.0f} с и был снят — "
            "файл слишком большой или программа зависла"
        ) from error
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise DjvuToolError(f"не удалось запустить {name}: {_reason(error)}") from error


def _stderr_tail(completed: subprocess.CompletedProcess) -> str:
    lines = [
        line.strip()
        for line in completed.stderr.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]
    for line in lines:
        if not line.startswith("***"):
            return line
    return lines[0] if lines else f"код возврата {completed.returncode}"


# --------------------------------------------------------- число страниц ---

def djvu_page_count(
    path: str | Path,
    *,
    timeout: float = TOOL_TIMEOUT,
    warnings: List[str] | None = None,
) -> int:
    """Сколько страниц в книге. ``0`` — выяснить не удалось (обычно битый файл)."""
    notes = warnings if warnings is not None else []
    reasons: List[str] = []

    try:
        completed = _run("djvused", ["-e", "n", str(path)], timeout)
    except DjvuToolError as error:
        reasons.append(str(error))
    else:
        if completed.returncode == 0:
            text = completed.stdout.decode("utf-8", "replace").strip().splitlines()
            for line in text:
                if line.strip().isdigit():
                    return int(line.strip())
        reasons.append(f"djvused: {_stderr_tail(completed)}")

    # Запасной путь: djvutxt печатает по одному выражению на страницу — даже
    # для страниц без текста (пустое «()»), так что их можно просто сосчитать.
    try:
        completed = _run("djvutxt", ["--detail=page", str(path)], timeout)
    except DjvuToolError as error:
        reasons.append(str(error))
    else:
        if completed.returncode == 0:
            count = sum(1 for line in completed.stdout.splitlines() if _PAGE_LINE_RE.match(line))
            if count:
                return count
        reasons.append(f"djvutxt: {_stderr_tail(completed)}")

    if reasons:
        notes.append("число страниц DjVu не определено (" + "; ".join(reasons) + ")")
    return 0


# --------------------------------------------------------- текстовый слой ---

def djvu_text(
    path: str | Path,
    page: int | None = None,
    *,
    timeout: float = TOOL_TIMEOUT,
) -> str:
    """Текстовый слой всей книги или одной страницы (нумерация с единицы)."""
    arguments = [str(path)] if page is None else [f"--page={int(page)}", str(path)]
    completed = _run("djvutxt", arguments, timeout)
    if completed.returncode != 0:
        raise DjvuToolError(f"djvutxt: {_stderr_tail(completed)}")
    return completed.stdout.decode("utf-8", "replace")


def djvu_text_layer(
    path: str | Path,
    pages: Iterable[int],
    *,
    timeout: float = TOOL_TIMEOUT,
    warnings: List[str] | None = None,
) -> Dict[int, str]:
    """Текстовый слой по страницам. В словарь попадают только осмысленные страницы.

    Страница с двумя десятками символов — это колонцифра или штамп, а не текст;
    такую страницу лучше распознать заново, чем оставить в индексе огрызок.
    """
    notes = warnings if warnings is not None else []
    layer: Dict[int, str] = {}
    failures = 0
    for number in pages:
        try:
            text = djvu_text(path, number, timeout=timeout)
        except DjvuToolError as error:
            notes.append(f"страница {number}: текстовый слой не прочитан ({_reason(error)})")
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                notes.append(
                    f"чтение текстового слоя прервано после {failures} неудач подряд"
                )
                break
            continue
        failures = 0
        if len(text.strip()) >= MIN_LAYER_CHARS:
            layer[int(number)] = text
    return layer


# -------------------------------------------------------------- рендеринг ---

def render_djvu_page(
    path: str | Path,
    page: int,
    target: str | Path,
    *,
    dpi: int = RENDER_DPI,
    timeout: float = RENDER_TIMEOUT,
) -> Path:
    """Страницу DjVu → файл PNM (PBM для чёрно-белых сканов, PPM для цветных).

    ``ddjvu`` сам выбирает подвид PNM по содержимому страницы, а tesseract
    читает любой из них, поэтому явный формат навязывать не нужно.
    """
    target = Path(target)
    completed = _run(
        "ddjvu",
        [
            "-format=pnm",
            f"-page={int(page)}",
            f"-scale={int(dpi)}",
            "-skip",
            str(path),
            str(target),
        ],
        timeout,
    )
    if completed.returncode != 0:
        raise DjvuToolError(f"ddjvu: {_stderr_tail(completed)}")
    if not target.is_file() or target.stat().st_size == 0:
        # Бывает, что ddjvu сообщает об успехе, но файла не пишет (нулевая
        # площадь страницы, отказ кодировщика). Для распознавания это то же
        # самое, что ошибка, и обрабатывать надо так же.
        raise DjvuToolError("страница не отрисована (пустая или повреждённая)")
    return target


# -------------------------------------------------------------- конвертер ---

def _ocr_pages(
    path: Path,
    pages: Sequence[int],
    result: ConvertedDocument,
    text_pages: Dict[int, str],
) -> int:
    """Распознать страницы без текстового слоя. Возвращает число распознанных."""
    recognised = 0
    failures = 0
    with tempfile.TemporaryDirectory(prefix="reportgen-djvu-") as directory:
        for number in pages:
            image = Path(directory) / f"page-{number:05d}.pnm"
            try:
                render_djvu_page(path, number, image)
            except DjvuToolError as error:
                result.warnings.append(f"страница {number}: {_reason(error)}")
                failures += 1
            else:
                try:
                    raw = ocr.ocr_image(image, timeout=ocr.DEFAULT_TIMEOUT)
                except ocr.OcrUnavailableError as error:
                    result.warnings.append(str(error))
                    break
                except ocr.OcrError as error:
                    result.warnings.append(f"страница {number}: {_reason(error)}")
                    failures += 1
                else:
                    failures = 0
                    body = ocr.ocr_text_to_markdown(raw)
                    quality = ocr.page_quality_warning(number, body)
                    if quality:
                        result.warnings.append(quality)
                    if body.strip():
                        text_pages[number] = body
                        recognised += 1
                finally:
                    try:
                        image.unlink()
                    except OSError:  # pragma: no cover — файла уже нет
                        pass
            if failures >= MAX_CONSECUTIVE_FAILURES:
                result.warnings.append(
                    f"распознавание прервано после {failures} неудач подряд — "
                    "остальные страницы не обрабатывались"
                )
                break
    return recognised


def convert_djvu(path: Path) -> ConvertedDocument:
    """DjVu → Markdown: текстовый слой там, где он есть, распознавание там, где нет.

    Программы djvulibre в стандартных сборках не открывают файлы с кириллицей
    в пути (проверено: cjb2 на «скан.pbm» падает, на «scan.pbm» работает), а в
    библиотеке заказчика по-русски названо почти всё. Поэтому весь разбор идёт
    по временной копии с латинским именем — см. convert.external_path.
    """
    with external_path(path) as safe:
        return _convert_djvu(safe, title=path.stem)


def _convert_djvu(path: Path, *, title: str) -> ConvertedDocument:
    result = ConvertedDocument(title=title, meta={"source_format": "djvu"})

    if djvu_binary("djvutxt") is None and djvu_binary("ddjvu") is None:
        result.needs_ocr = True
        result.warnings.append(
            f"DjVu не разобран: не найдены программы djvutxt и ddjvu; {DJVULIBRE_HINT}"
        )
        return result

    count = djvu_page_count(path, warnings=result.warnings)
    if count <= 0:
        result.warnings.append(
            "DjVu не прочитан: файл повреждён, не дочитан до конца или это вообще не DjVu"
        )
        return result
    result.page_count = count
    result.meta["page_count"] = count

    # Один вызов на весь файл: если текста нет нигде, незачем опрашивать
    # djvutxt по каждой из трёхсот страниц.
    whole = ""
    try:
        whole = djvu_text(path)
    except DjvuToolError as error:
        result.warnings.append(f"текстовый слой не прочитан ({_reason(error)})")

    text_pages: Dict[int, str] = {}
    # До какой страницы разбираем книгу. Обычно до последней; предел нужен
    # только для подшивок на тысячи страниц, где один вызов djvutxt на
    # страницу уже складывается в минуты.
    limit = count
    if len(whole.strip()) >= MIN_LAYER_CHARS:
        limit = min(count, MAX_TEXT_PAGES)
        if count > limit:
            result.warnings.append(
                f"прочитаны только первые страницы книги: {limit} из {count} — "
                "остальные не разбирались, подайте хвост отдельным файлом"
            )
        layer = djvu_text_layer(path, range(1, limit + 1), warnings=result.warnings)
        for number, text in layer.items():
            body = ocr.ocr_text_to_markdown(text)
            if body.strip():
                text_pages[number] = body
    result.meta["text_layer"] = bool(text_pages)
    result.meta["text_layer_pages"] = len(text_pages)

    targets = [number for number in range(1, limit + 1) if number not in text_pages]
    recognised = 0
    if targets:
        if djvu_binary("ddjvu") is None:
            result.warnings.append(
                f"страниц без текстового слоя: {len(targets)} — распознать их нечем, "
                f"не найдена программа ddjvu; {DJVULIBRE_HINT}"
            )
        elif not ocr.ocr_available():
            result.warnings.append(
                f"страниц без текстового слоя: {len(targets)} — распознать их нечем: "
                f"{ocr.TESSERACT_HINT}"
            )
        else:
            note = ocr.language_warning(ocr.DEFAULT_LANGUAGES)
            if note:
                result.warnings.append(note)
            limited = list(targets[:MAX_OCR_PAGES])
            if len(targets) > len(limited):
                result.warnings.append(
                    f"распознавание ограничено {MAX_OCR_PAGES} страницами: пропущено "
                    f"страниц {len(targets) - len(limited)} — распознайте остаток отдельно"
                )
            recognised = _ocr_pages(path, limited, result, text_pages)

    result.meta["ocr"] = bool(recognised)
    if recognised:
        result.meta["ocr_pages"] = recognised
        result.meta["ocr_languages"] = ocr.resolve_languages(ocr.DEFAULT_LANGUAGES)[0]
        result.warnings.insert(
            0,
            f"распознано страниц {recognised} из {len(targets)} без текстового слоя "
            f"(tesseract, {result.meta['ocr_languages']}); текст получен машинно — "
            "числа и обозначения перед использованием сверяйте с оригиналом",
        )

    pieces: List[str] = []
    for number in sorted(text_pages):
        body = text_pages[number].strip()
        if not body:
            continue
        pieces.append(page_marker(number))
        pieces.append(body)
    result.text = "\n\n".join(pieces)
    result.title = _first_markdown_heading(result.text) or path.stem
    # «Нужен OCR» — это про документ, из которого не достали ничего: страницы
    # есть, текста нет. Если хоть часть книги прочиталась, документ в индекс
    # берётся, а о непрочитанном говорят предупреждения.
    result.needs_ocr = bool(targets) and not text_pages

    if result.is_empty:
        result.warnings.append(
            "из DjVu не извлечено ни строки: в книге нет текстового слоя и "
            "распознать её не удалось"
        )
    elif len(text_pages) < count:
        result.warnings.append(
            f"текст получен не со всех страниц: {len(text_pages)} из {count}"
        )
    return result


# -------------------------------------------------------- регистрация ----

registry.register(registry.ConverterSpec(
    name="djvu",
    suffixes=DJVU_SUFFIXES,
    convert=convert_djvu,
    requires=(
        registry.Requirement("binary", "djvutxt", DJVULIBRE_HINT),
        registry.Requirement("binary", "ddjvu", DJVULIBRE_HINT),
    ),
    note=(
        "сканы книг: текстовый слой через djvutxt, страницы без слоя — "
        "ddjvu в PNM и распознавание tesseract (нужен для сканов без слоя)"
    ),
))
