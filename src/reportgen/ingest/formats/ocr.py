"""Распознавание текста (OCR): изображения и PDF-сканы.

Русская техническая литература в библиотеке заказчика — это чаще всего скан.
Постановление, методичка, справочник по волноводам: страница есть, текста в
файле нет. Без распознавания такой книги для системы не существует, поэтому
OCR здесь не украшение, а условие того, что библиотека вообще наполнится.

Модуль решает три задачи:

* **общий механизм распознавания** — :func:`ocr_image` (одна картинка),
  :func:`ocr_pdf_pages` (страницы PDF через рендеринг в PyMuPDF) и
  :func:`ocr_text_to_markdown` (сырой вывод tesseract → Markdown). Ими же
  пользуется разбор DjVu (:mod:`reportgen.ingest.formats.djvu`), чтобы
  распознавание в системе было одно, а не два расходящихся;
* **конвертер изображений** — ``.png``, ``.jpg``, ``.tif`` и прочие сканы
  отдельными файлами: одна страница, распознанный текст, ``meta['ocr']``;
* **конвертер PDF поверх обычного** — с приоритетом выше, чем у разбора
  текстового слоя (:func:`reportgen.ingest.convert._convert_pdf`). Сначала
  всегда пробуется текстовый слой: он точный и мгновенный. Распознаются
  только страницы, на которых текста нет, — переоцифровывать нормальный PDF
  бессмысленно и дорого.

Что важно знать про результат:

* **Числа из OCR ненадёжны.** «3,5 дБ» и «8,5 дБ» на плохом скане различаются
  одним штрихом. Поэтому в предупреждения документа всегда попадает строка о
  машинном распознавании: в отчёт числа берутся из факт-пакета (инвариант
  док. 01), а библиотека даёт формулировки и ссылки.
* **Плохо распознанная страница — это сигнал.** Меньше
  :data:`MIN_PAGE_CHARS` символов обычно означает чертёж, фотографию или
  пустой лист; инженеру полезно знать, что там нечего искать.
* **Объём ограничен.** Сотня страниц распознаётся минутами, поэтому за раз
  распознаётся не больше :data:`MAX_OCR_PAGES` страниц, а о пропущенных
  говорится в предупреждении.
* **Ничего не бросается наружу.** Нет tesseract, битый файл, зависший
  процесс — это предупреждение по-русски и пустой текст, а не исключение:
  приём каталога на тысячу файлов не должен падать на одном скане.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from .. import registry
from ..convert import (
    _PAGE_MARKER_RE,
    ConvertedDocument,
    external_path,
    MissingDependencyError,
    _clean_line,
    _first_markdown_heading,
    _join_wrapped,
    _reason,
    page_marker,
)

__all__ = [
    "IMAGE_SUFFIXES",
    "MAX_OCR_PAGES",
    "MIN_PAGE_CHARS",
    "OcrError",
    "OcrTimeoutError",
    "OcrUnavailableError",
    "available_languages",
    "convert_image",
    "convert_pdf_ocr",
    "language_warning",
    "ocr_available",
    "ocr_image",
    "ocr_pdf_pages",
    "ocr_text_to_markdown",
    "page_quality_warning",
    "render_pdf_page",
    "reset_caches",
    "resolve_languages",
    "tesseract_binary",
]

#: Языки распознавания по умолчанию. Русский первым: библиотека русская, а
#: английский нужен для обозначений, единиц и англоязычных вставок в ГОСТах.
DEFAULT_LANGUAGES = "rus+eng"

#: Таймаут на одну страницу. Страница А4 распознаётся за 1–3 с; две минуты —
#: это уже не «медленно», а «процесс завис», и его надо снимать.
DEFAULT_TIMEOUT = 120.0

#: Таймаут на служебный опрос tesseract (список языков).
PROBE_TIMEOUT = 20.0

#: Масштаб рендеринга страницы PDF. При 1.0 (72 dpi) tesseract на кириллице
#: ошибается через слово; 2.0 даёт ~144 dpi и приемлемое качество при
#: разумном размере картинки.
RENDER_SCALE = 2.0

#: Сколько страниц распознаём за один приём файла. Триста страниц — это уже
#: 10–15 минут работы; остальное лучше подать отдельно и осознанно.
MAX_OCR_PAGES = 300

#: Порог «на странице нет текста». Используется дважды: чтобы решить, что
#: текстового слоя на странице нет и её надо распознавать, и чтобы предупредить
#: о плохо распознанной странице.
MIN_PAGE_CHARS = 40

#: Сколько подряд идущих неудач терпим, прежде чем бросить распознавание файла.
#: Если tesseract падает на трёх страницах подряд, он упадёт и на остальных
#: трёхстах — незачем ждать таймаут по каждой.
MAX_CONSECUTIVE_FAILURES = 3

#: Растровые форматы, которые встречаются в библиотеке отдельными файлами:
#: страница, вырезанная из книги, схема из письма, фотография стенда.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")

#: Как поставить tesseract. Текст уходит в диагностику реестра, поэтому в нём
#: сразу оба контура — и Linux, и Windows.
TESSERACT_HINT = (
    "пакет tesseract-ocr с языками rus+eng: Linux — apt install tesseract-ocr "
    "tesseract-ocr-rus tesseract-ocr-eng (или dnf install tesseract "
    "tesseract-langpack-rus); Windows — установщик tesseract-ocr-w64-setup "
    r"в «C:\Program Files\Tesseract-OCR» (языки Russian и English отметить при "
    "установке), каталог добавить в PATH"
)

PYMUPDF_HINT = "pip install pymupdf"

#: Где искать tesseract на Windows, если его нет в PATH. Установщик по
#: умолчанию кладёт программу именно сюда, а PATH правит не всегда.
_WINDOWS_TESSERACT = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)

#: Подстрока предупреждения, которым обычный разбор PDF помечает скан. Если мы
#: этот скан распознали, предупреждение «нужен слой OCR» становится неверным и
#: снимается. Связка по тексту хрупкая, поэтому она в одном месте.
_SCAN_WARNING_MARK = "нужен слой OCR"

#: «osd» — это модель определения ориентации, а не язык; в подбор не годится.
_NOT_A_LANGUAGE = frozenset({"osd", "equ"})

_LANGUAGE_SPLIT_RE = re.compile(r"[+,;\s]+")

#: Строки, с которых начинается заголовок в книжной вёрстке.
_HEADING_WORD_RE = re.compile(
    r"^(?:глава|раздел|часть|приложение|введение|заключение|содержание|оглавление)\b",
    re.IGNORECASE,
)
#: «2.1.3 Модуляция» — нумерованный заголовок; уровень равен глубине номера.
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,2})\.?\s+(\S.*)$")
#: Заголовок не кончается знаком, которым кончается предложение.
_SENTENCE_END = ".,;:!?"
_HEADING_MAX_CHARS = 80
_HYPHENS = "-‐‑­−"

_LANGUAGES_CACHE: Dict[str, frozenset] = {}


class OcrError(RuntimeError):
    """Распознать не удалось. Текст исключения — готовое предупреждение."""


class OcrUnavailableError(OcrError):
    """В системе нет tesseract."""


class OcrTimeoutError(OcrError):
    """Распознавание не уложилось в отведённое время."""


# --------------------------------------------------------- где tesseract ---

def tesseract_binary() -> str | None:
    """Путь к tesseract или ``None``.

    Сначала PATH, затем — на Windows — стандартные каталоги установщика:
    в контуре заказчика PATH после установки правят далеко не всегда.
    """
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name != "nt":
        return None
    candidates: List[str] = list(_WINDOWS_TESSERACT)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe"))
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


def ocr_available() -> bool:
    """Есть ли чем распознавать."""
    return tesseract_binary() is not None


def reset_caches() -> None:
    """Забыть, какие языки нашлись у tesseract. Нужно тестам и после доустановки."""
    _LANGUAGES_CACHE.clear()


def available_languages() -> frozenset:
    """Языковые пакеты, установленные у tesseract.

    Пустое множество означает «выяснить не удалось» — в этом случае языки не
    фильтруются: пусть лучше tesseract сам скажет, чего ему не хватает.
    """
    binary = tesseract_binary()
    if binary is None:
        return frozenset()
    cached = _LANGUAGES_CACHE.get(binary)
    if cached is not None:
        return cached
    languages: frozenset = frozenset()
    try:
        completed = subprocess.run(
            [binary, "--list-langs"],
            capture_output=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        completed = None
    if completed is not None and completed.returncode == 0:
        # Первая строка — «List of available languages…», дальше по языку в строке.
        text = completed.stdout.decode("utf-8", "replace")
        if not text.strip():
            text = completed.stderr.decode("utf-8", "replace")
        names = [
            line.strip()
            for line in text.splitlines()[1:]
            if line.strip() and " " not in line.strip()
        ]
        languages = frozenset(names)
    _LANGUAGES_CACHE[binary] = languages
    return languages


def resolve_languages(languages: str = DEFAULT_LANGUAGES) -> Tuple[str, Tuple[str, ...]]:
    """Отобрать из запрошенных языков установленные.

    Возвращает пару «строка для ``-l``, недостающие языки». Если нет ни одного
    из запрошенных, берём любой установленный: распознать латиницу русской
    модели лучше, чем не распознать ничего.
    """
    requested = [part for part in _LANGUAGE_SPLIT_RE.split(languages or "") if part]
    if not requested:
        requested = [part for part in _LANGUAGE_SPLIT_RE.split(DEFAULT_LANGUAGES) if part]
    known = available_languages()
    if not known:
        return "+".join(requested), ()
    usable = [name for name in requested if name in known]
    missing = tuple(name for name in requested if name not in known)
    if not usable:
        spare = sorted(name for name in known if name not in _NOT_A_LANGUAGE)
        if "eng" in spare:
            usable = ["eng"]
        elif spare:
            usable = [spare[0]]
    return "+".join(usable), missing


def language_warning(languages: str = DEFAULT_LANGUAGES) -> str | None:
    """Предупреждение о недостающих языковых пакетах (или ``None``)."""
    _, missing = resolve_languages(languages)
    if not missing:
        return None
    names = ", ".join(missing)
    return (
        f"у tesseract нет языковых пакетов: {names} — текст на этих языках "
        f"распознается с ошибками; {TESSERACT_HINT}"
    )


# ------------------------------------------------------- распознавание ----

def _normalise(text: str) -> str:
    """Сырой вывод tesseract → аккуратный текст без служебных символов."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_clean_line(line) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def ocr_image(
    source: str | Path | bytes | bytearray | memoryview,
    *,
    languages: str = DEFAULT_LANGUAGES,
    timeout: float = DEFAULT_TIMEOUT,
    psm: int | None = None,
) -> str:
    """Распознать одну картинку: путь к файлу или содержимое в памяти.

    Бросает :class:`OcrError` (и его наследников), если распознать не удалось.
    Вызывающий обязан превратить исключение в предупреждение документа — сам
    файл при этом не считается сломанным.
    """
    binary = tesseract_binary()
    if binary is None:
        raise OcrUnavailableError(
            f"не найден tesseract, распознавание невозможно: {TESSERACT_HINT}"
        )

    temporary: Path | None = None
    if isinstance(source, (bytes, bytearray, memoryview)):
        handle = tempfile.NamedTemporaryFile(prefix="reportgen-ocr-", suffix=".img", delete=False)
        try:
            handle.write(bytes(source))
        finally:
            handle.close()
        temporary = Path(handle.name)
        image = temporary
    else:
        image = Path(source)
        if not image.is_file():
            raise OcrError(f"изображение не найдено: {image}")

    usable, _missing = resolve_languages(languages)
    # tesseract в ряде сборок не открывает пути с кириллицей, а в библиотеке
    # заказчика по-русски названо почти всё: работаем по временной копии с
    # латинским именем (см. convert.external_path).
    with external_path(image) as safe:
        return _run_tesseract(binary, safe, usable, psm, timeout, temporary)


def _tesseract_env() -> Dict[str, str]:
    """Окружение для tesseract: по одному потоку OpenMP на процесс.

    По умолчанию tesseract разворачивает OpenMP на все ядра. На одной странице
    это даёт около 20 % — а при параллельном приёме библиотеки превращается в
    катастрофу: четыре процесса по четыре потока на четырёхъядерной машине
    молотят друг друга, и распознавание, занимающее секунду, не заканчивается
    и за девять минут. Измерено на этой сборке: 0.99 с против 0.80 с на одном
    файле и полное зависание против 3 с на четырёх.

    Параллелить надо процессами, а не потоками внутри процесса: страницы
    независимы, и один поток на процесс даёт линейное ускорение.
    """
    env = dict(os.environ)
    env.setdefault("OMP_THREAD_LIMIT", "1")
    env.setdefault("OMP_NUM_THREADS", "1")
    return env


def _run_tesseract(
    binary: str,
    image: Path,
    usable: str,
    psm: int | None,
    timeout: float,
    temporary: Path | None,
) -> str:
    command = [binary, str(image), "stdout"]
    if usable:
        command += ["-l", usable]
    if psm is not None:
        command += ["--psm", str(int(psm))]

    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout,
                                   check=False, env=_tesseract_env())
    except subprocess.TimeoutExpired as error:
        raise OcrTimeoutError(
            f"tesseract не уложился в {float(timeout):.0f} с и был снят — "
            "страница слишком большая или процесс завис"
        ) from error
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise OcrError(f"не удалось запустить tesseract: {_reason(error)}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:  # pragma: no cover — файл уже удалён или занят
                pass

    if completed.returncode != 0:
        details = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = details[-1] if details else f"код возврата {completed.returncode}"
        raise OcrError(f"tesseract не смог распознать изображение: {tail}")
    return _normalise(completed.stdout.decode("utf-8", "replace"))


def render_pdf_page(document: object, number: int, scale: float = RENDER_SCALE) -> bytes:
    """Страница открытого документа PyMuPDF → PNG в памяти (нумерация с единицы)."""
    import pymupdf  # noqa: PLC0415 — тяжёлая зависимость, только по требованию

    page = document[number - 1]  # type: ignore[index]
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
    return bytes(pixmap.tobytes("png"))


def ocr_pdf_pages(
    path: str | Path,
    pages: Iterable[int],
    *,
    languages: str = DEFAULT_LANGUAGES,
    timeout: float = DEFAULT_TIMEOUT,
    scale: float = RENDER_SCALE,
    max_pages: int = MAX_OCR_PAGES,
    warnings: List[str] | None = None,
) -> Dict[int, str]:
    """Распознать указанные страницы PDF. Возвращает текст по номерам страниц.

    Страницы рендерятся PyMuPDF в PNG и уходят в :func:`ocr_image`. В словаре
    оказываются все страницы, которые удалось обработать, — в том числе с
    пустым текстом (чертёж, фотография): вызывающий по этому отличает «не
    распознали» от «распознавать было нечего».

    Всё, что мешало работе (пропущенные по лимиту страницы, сбои на отдельных
    страницах), дописывается в ``warnings`` по-русски.
    """
    collected: Dict[int, str] = {}
    notes = warnings if warnings is not None else []
    wanted = sorted({int(number) for number in pages if int(number) > 0})
    if not wanted:
        return collected
    if max_pages and len(wanted) > max_pages:
        skipped = len(wanted) - max_pages
        wanted = wanted[:max_pages]
        notes.append(
            f"распознавание ограничено {max_pages} страницами: пропущено страниц "
            f"{skipped} — распознайте остаток отдельным файлом или поднимите "
            "предел, если готовы ждать"
        )

    try:
        import pymupdf  # noqa: PLC0415 — тяжёлая зависимость, только по требованию
    except ImportError as error:  # pragma: no cover — зависит от окружения
        raise MissingDependencyError(
            f"для распознавания PDF нужен пакет pymupdf ({PYMUPDF_HINT})"
        ) from error

    try:
        document = pymupdf.open(str(path))
    except Exception as error:  # noqa: BLE001 — содержимое файла не должно ронять приём
        raise OcrError(f"не удалось открыть PDF для распознавания: {_reason(error)}") from error

    failures = 0
    try:
        total = int(getattr(document, "page_count", 0) or 0)
        for number in wanted:
            if total and number > total:
                notes.append(f"страницы {number} в документе нет — распознавать нечего")
                continue
            try:
                image = render_pdf_page(document, number, scale)
            except Exception as error:  # noqa: BLE001 — сбой рендеринга одной страницы
                notes.append(
                    f"страница {number}: не удалось получить изображение ({_reason(error)})"
                )
                failures += 1
            else:
                try:
                    collected[number] = ocr_image(image, languages=languages, timeout=timeout)
                    failures = 0
                    continue
                except OcrUnavailableError:
                    raise
                except OcrError as error:
                    notes.append(f"страница {number}: {_reason(error)}")
                    failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                notes.append(
                    f"распознавание прервано после {failures} неудач подряд — "
                    "остальные страницы не обрабатывались"
                )
                break
    finally:
        try:
            document.close()
        except Exception:  # pragma: no cover — документ уже закрыт
            pass
    return collected


def page_quality_warning(page: int, text: str) -> str | None:
    """Предупреждение о плохо распознанной странице (или ``None``, если всё хорошо)."""
    count = len(text.strip())
    if count >= MIN_PAGE_CHARS:
        return None
    if not count:
        return (
            f"страница {page}: текст не распознан — вероятно, чертёж, "
            "фотография или пустой лист"
        )
    return (
        f"страница {page} распозналась плохо: символов {count} — "
        "вероятно, чертёж или фотография с подписями"
    )


# ------------------------------------------- сырой текст OCR → Markdown ----

def _heading_level(line: str) -> int | None:
    """Уровень заголовка для строки распознанного текста или ``None``.

    Правила намеренно осторожные: ложный заголовок рвёт чанк посередине
    абзаца, и это хуже, чем пропущенный заголовок.
    """
    if len(line) > _HEADING_MAX_CHARS or line[-1:] in _SENTENCE_END:
        return None
    letters = sum(1 for character in line if character.isalpha())
    if letters < 3:
        return None
    if _HEADING_WORD_RE.match(line):
        return 1
    match = _NUMBERED_HEADING_RE.match(line)
    if match and match.group(2)[:1].isupper():
        return min(match.group(1).count(".") + 1, 3)
    if line == line.upper() and letters >= 4:
        return 2
    return None


def _continues(previous: str, current: str) -> bool:
    """Продолжает ли строка предыдущую (перенос слова или разрыв предложения)."""
    if previous[-1:] in _HYPHENS:
        return True
    if previous[-1:] in _SENTENCE_END:
        return False
    first = current[:1]
    return bool(first) and first.isalpha() and first.islower()


def ocr_text_to_markdown(text: str) -> str:
    """Вывод tesseract → Markdown: заголовки решётками, склеенные переносы.

    Строки внутри абзаца склеиваются, только если видно, что предложение
    продолжается. Иначе перевод строки сохраняется: в сканах технических книг
    построчно набраны таблицы и перечни параметров, и слитый в один абзац
    столбец значений теряет смысл.
    """
    blocks: List[str] = []
    current: List[str] = []

    def flush() -> None:
        if not current:
            return
        joined = current[0]
        for line in current[1:]:
            if _continues(joined, line):
                joined = _join_wrapped(joined, line)
            else:
                joined = f"{joined}\n{line}"
        blocks.append(joined)
        current.clear()

    for raw in str(text).split("\n"):
        line = _clean_line(raw)
        if not line:
            flush()
            continue
        level = _heading_level(line)
        if level:
            flush()
            blocks.append(f"{'#' * level} {line}")
            continue
        current.append(line)
    flush()
    return "\n\n".join(blocks)


# ------------------------------------------------- конвертер изображений ---

def _image_meta(path: Path) -> Dict[str, object]:
    """Размер и режим картинки, если Pillow есть. Без Pillow просто нет этих полей."""
    try:
        from PIL import Image  # noqa: PLC0415 — необязательная зависимость
    except ImportError:
        return {}
    try:
        with Image.open(path) as image:
            return {"image_size": f"{image.width}×{image.height}", "image_mode": str(image.mode)}
    except Exception:  # noqa: BLE001 — битую картинку разберёт сам tesseract
        return {}


def convert_image(path: Path) -> ConvertedDocument:
    """Скан отдельной страницей (PNG, JPEG, TIFF, BMP) → распознанный Markdown."""
    suffix = path.suffix.lower().lstrip(".")
    result = ConvertedDocument(
        title=path.stem,
        page_count=1,
        meta={"source_format": suffix or "image", "ocr": True, "page_count": 1},
    )
    result.meta.update(_image_meta(path))

    note = language_warning(DEFAULT_LANGUAGES)
    if note:
        result.warnings.append(note)
    try:
        raw = ocr_image(path, languages=DEFAULT_LANGUAGES)
    except OcrError as error:
        result.needs_ocr = True
        result.warnings.append(str(error))
        return result

    body = ocr_text_to_markdown(raw)
    if body:
        result.text = f"{page_marker(1)}\n\n{body}"
    result.meta["ocr_languages"] = resolve_languages(DEFAULT_LANGUAGES)[0]
    result.title = _first_markdown_heading(body) or path.stem

    characters = len(body.strip())
    if not characters:
        result.warnings.append(
            "текст не распознан: на изображении, похоже, нет читаемого текста "
            "(фотография, чертёж или пустой лист)"
        )
    elif characters < MIN_PAGE_CHARS:
        result.warnings.append(
            f"распозналось мало текста (символов {characters}) — проверьте "
            "качество скана: перекос, низкое разрешение, слабый контраст"
        )
    else:
        result.warnings.append(
            "текст получен машинным распознаванием — числа и обозначения "
            "перед использованием сверяйте с оригиналом"
        )
    return result


# --------------------------------------------------- конвертер PDF + OCR ---


# ------------------------------------------- картинки внутри документов ----

#: Меньше этого картинку не распознаём: логотипы, маркеры списков, линейки.
#: 200×80 — примерно подпись под схемой, ниже осмысленного текста не бывает.
MIN_EMBEDDED_WIDTH = 200
MIN_EMBEDDED_HEIGHT = 80

#: Сколько картинок разбирать в одном документе. Презентация на двести
#: слайдов иначе распознавалась бы полчаса.
MAX_EMBEDDED_IMAGES = 40

#: Где лежат вложенные картинки внутри контейнеров.
EMBEDDED_MEDIA_DIRS = ("word/media/", "ppt/media/", "xl/media/", "Pictures/")

_EMBEDDED_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif")


def _embedded_size(payload: bytes) -> Tuple[int, int] | None:
    """Размер картинки без её полной распаковки, если Pillow доступен."""
    try:
        import io  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(payload)) as image:
            return image.size
    except Exception:  # noqa: BLE001 — битая картинка не должна ронять разбор
        return None


def ocr_embedded_images(
    path: Path,
    *,
    languages: str = DEFAULT_LANGUAGES,
    limit: int = MAX_EMBEDDED_IMAGES,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Распознать картинки, вложенные в документ-контейнер.

    В технических отчётах половина существенного лежит на иллюстрациях:
    спектрограмма с подписанными частотами, снимок экрана анализатора,
    отсканированная страница методики, вставленная в DOCX. Текст разбора
    такие вставки не видел вовсе — в базу попадала подпись «рисунок 1».

    Возвращает ``(распознанное, предупреждения)``. Ошибки не бросаются:
    непрочитанная картинка — это меньше текста, а не сломанный документ.
    """
    found: List[Tuple[str, str]] = []
    warnings: List[str] = []
    if not ocr_available():
        return found, warnings

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return found, warnings

    skipped_small = 0
    with archive:
        names = [
            name for name in archive.namelist()
            if name.lower().endswith(_EMBEDDED_SUFFIXES)
            and any(name.startswith(prefix) for prefix in EMBEDDED_MEDIA_DIRS)
        ]
        names.sort()
        if len(names) > limit:
            warnings.append(
                f"картинок в документе {len(names)}, распознаны первые {limit}"
            )
            names = names[:limit]

        for name in names:
            try:
                payload = archive.read(name)
            except (OSError, zipfile.BadZipFile):
                continue
            size = _embedded_size(payload)
            if size and (size[0] < MIN_EMBEDDED_WIDTH or size[1] < MIN_EMBEDDED_HEIGHT):
                skipped_small += 1
                continue
            try:
                text = ocr_image(payload, languages=languages, timeout=timeout)
            except OcrError:
                continue
            text = _normalise(text).strip()
            if len(text) >= 12:
                found.append((Path(name).name, text))

    if skipped_small:
        warnings.append(
            f"мелких изображений пропущено: {skipped_small} (логотипы и значки не распознаются)"
        )
    return found, warnings


def embedded_images_block(found: Sequence[Tuple[str, str]]) -> str:
    """Распознанное с картинок — отдельным разделом, честно помеченным.

    Отдельным, а не вперемешку с текстом: распознанное машиной нельзя
    подавать как написанное автором, иначе инженер не поймёт, что сверять.
    """
    if not found:
        return ""
    parts = [
        "## Текст с иллюстраций (распознан машинно)",
        "",
        "Ниже — то, что удалось прочитать на вставленных в документ картинках. "
        "Числа и обозначения отсюда перед использованием сверяйте с оригиналом.",
        "",
    ]
    for name, text in found:
        parts.append(f"### {name}")
        parts.append("")
        parts.append(text)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"

def _split_pages(text: str) -> Tuple[str, Dict[int, str]]:
    """Размеченный маркерами Markdown → текст до первой страницы и текст по страницам."""
    matches = list(_PAGE_MARKER_RE.finditer(text))
    if not matches:
        return text, {}
    prefix = text[: matches[0].start()]
    pages: Dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        number = int(match.group(1))
        piece = text[match.end() : end].strip()
        pages[number] = f"{pages[number]}\n\n{piece}".strip() if number in pages else piece
    return prefix, pages


def _assemble(prefix: str, pages: Dict[int, str]) -> str:
    """Обратная сборка: маркер страницы, затем её текст."""
    pieces: List[str] = []
    if prefix.strip():
        pieces.append(prefix.strip())
    for number in sorted(pages):
        body = pages[number].strip()
        if not body:
            continue
        pieces.append(page_marker(number))
        pieces.append(body)
    return "\n\n".join(pieces)


def convert_pdf_ocr(path: Path) -> ConvertedDocument:
    """PDF: текстовый слой, а где его нет — распознавание страницы.

    Порядок именно такой. Текстовый слой точен и достаётся мгновенно, OCR
    приблизителен и стоит секунды на страницу, поэтому распознаются только
    страницы, на которых текста нет. Если весь документ разобрался обычным
    способом, конвертер не делает ничего сверх :func:`_convert_pdf`.
    """
    from ..convert import _convert_pdf  # noqa: PLC0415 — не тянуть PDF-разбор при импорте

    result = _convert_pdf(path)
    if not result.needs_ocr:
        return result

    total = int(result.page_count or 0)
    prefix, pages = _split_pages(result.text)
    if not total:
        total = max(pages) if pages else 0
    if not total:
        return result

    targets = [
        number
        for number in range(1, total + 1)
        if len(pages.get(number, "").strip()) < MIN_PAGE_CHARS
    ]
    if not targets:
        return result

    if not ocr_available():
        result.warnings.append(
            "текстового слоя в PDF нет, а распознать нечем: "
            f"{TESSERACT_HINT}. Документ принят как есть — искать в нём нечего"
        )
        return result

    note = language_warning(DEFAULT_LANGUAGES)
    if note:
        result.warnings.append(note)

    notes: List[str] = []
    try:
        recognised = ocr_pdf_pages(path, targets, warnings=notes)
    except (OcrError, MissingDependencyError) as error:
        result.warnings.extend(notes)
        result.warnings.append(f"распознавание не выполнено: {_reason(error)}")
        return result
    result.warnings.extend(notes)

    added = 0
    for number in sorted(recognised):
        body = ocr_text_to_markdown(recognised[number])
        quality = page_quality_warning(number, body)
        if quality:
            result.warnings.append(quality)
        if not body.strip():
            continue
        pages[number] = body
        added += 1

    result.meta["ocr"] = True
    result.meta["ocr_pages"] = added
    result.meta["ocr_languages"] = resolve_languages(DEFAULT_LANGUAGES)[0]
    if not added:
        result.warnings.append(
            "распознать не удалось ни одной страницы — вероятно, в файле только "
            "чертежи и фотографии"
        )
        return result

    result.text = _assemble(prefix, pages)
    result.needs_ocr = False
    # Предупреждение обычного разбора «нужен слой OCR» больше не соответствует
    # действительности: слой мы только что сделали сами.
    result.warnings = [item for item in result.warnings if _SCAN_WARNING_MARK not in item]
    result.warnings.insert(
        0,
        f"текстового слоя не было: распознано страниц {added} из {len(targets)} "
        f"(tesseract, {result.meta['ocr_languages']}); текст получен машинно — "
        "числа и обозначения перед использованием сверяйте с оригиналом",
    )
    if not result.title or result.title == path.stem:
        result.title = _first_markdown_heading(result.text) or path.stem
    return result


# -------------------------------------------------------- регистрация ----

registry.register(registry.ConverterSpec(
    name="image-ocr",
    suffixes=IMAGE_SUFFIXES,
    convert=convert_image,
    requires=(registry.Requirement("binary", "tesseract", TESSERACT_HINT, locate=lambda: tesseract_binary()),),
    note="скан страницей-картинкой: распознавание tesseract (rus+eng), одна страница",
))

registry.register(registry.ConverterSpec(
    name="pdf-ocr",
    suffixes=(".pdf",),
    convert=convert_pdf_ocr,
    requires=(
        registry.Requirement("python", "pymupdf", PYMUPDF_HINT),
        registry.Requirement("binary", "tesseract", TESSERACT_HINT, locate=lambda: tesseract_binary()),
    ),
    priority=10,
    note=(
        "PDF с распознаванием: сначала текстовый слой, затем OCR страниц без "
        "текста; без tesseract работает обычный конвертер «pdf»"
    ),
))
