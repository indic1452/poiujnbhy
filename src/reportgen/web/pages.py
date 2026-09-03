"""Показ страниц документа картинками.

PDF в окне предпросмотра не открывался вовсе. Чужой файл в своей странице —
это чужой код в своей странице, поэтому встроенное окно стояло в песочнице и
с запретом «default-src 'none'». Встроенный просмотрщик браузера — тоже код, и
запрет глушил и его: человек нажимал на справку-объективку и получал пустой
серый прямоугольник со словами «This page has been blocked by Chromium».
Ждал, ничего не дождавшись, и шёл скачивать файл — отсюда и «долгое
скачивание справки».

Ставить чужой PDF в страницу и снимать запреты нельзя: PDF умеет исполнять
свой код. Поэтому страницу рисуем у себя и отдаём картинкой. Картинка кода не
несёт ни при каком браузере, показывается всюду одинаково — и заодно так
показываются сканы TIFF, которые не открывает ни один браузер.

Рисует MuPDF — тот же, которым система читает текст PDF: новых зависимостей
не появляется. Нарисованная страница кладётся в кэш на диск: справку смотрят
не один раз, а каждый раз рисовать её незачем.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Tuple

__all__ = [
    "RENDERABLE",
    "PageRenderError",
    "is_renderable",
    "page_count",
    "render_page",
]

log = logging.getLogger("reportgen.web.pages")

#: Что умеем рисовать страницами. PDF — главное; TIFF и его многостраничные
#: сканы браузер не показывает вообще; XPS и CBZ встречаются в чужих
#: библиотеках. DjVu тут нет: MuPDF его не открывает, для него свой разборщик.
RENDERABLE = (".pdf", ".tif", ".tiff", ".xps", ".oxps", ".epub", ".cbz")

#: Разрешение отрисовки. 144 точки на дюйм — страница А4 выходит около
#: 1190×1680. Столько нужно, чтобы страница читалась и растянутой по ширине
#: окна на большом экране; в кэше она занимает около шестидесяти килобайт.
DPI = 144

#: Сколько страниц готовы нарисовать. Книга на шестьсот страниц — не повод
#: заполнить диск кэшем: дальше человек всё равно скачивает файл.
MAX_PAGE = 2000

_lock = threading.Lock()


class PageRenderError(RuntimeError):
    """Страницу нарисовать не удалось — с причиной, понятной человеку."""


def is_renderable(name: str) -> bool:
    return Path(name).suffix.lower() in RENDERABLE


def _pymupdf():
    try:
        import pymupdf                          # noqa: PLC0415
    except ImportError as error:                # pragma: no cover — зависит от среды
        raise PageRenderError(
            "показ страниц требует пакет pymupdf (pip install pymupdf); "
            "сам файл при этом скачивается и открывается как обычно"
        ) from error
    return pymupdf


def page_count(path: Path) -> int:
    """Сколько страниц в файле. Ноль — значит, страницами его не показать."""
    if not is_renderable(path.name) or not path.is_file():
        return 0
    try:
        pymupdf = _pymupdf()
    except PageRenderError:
        return 0
    try:
        with pymupdf.open(str(path)) as document:
            if getattr(document, "needs_pass", False):
                return 0
            return int(getattr(document, "page_count", 0) or 0)
    except Exception as error:                  # noqa: BLE001 — битый файл не беда окна
        log.debug("не удалось узнать число страниц %s: %s", path, error)
        return 0


def _cache_path(root: Path, path: Path, number: int) -> Path:
    """Куда кладём нарисованную страницу.

    В имя входят путь, размер и время правки файла: заменили справку новой —
    старые картинки сами перестают подходить, и чужая страница из кэша не
    попадёт в окно.
    """
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{DPI}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return root / "stranicy" / digest[:2] / f"{digest}-{number:04d}.png"


def render_page(path: Path, number: int, cache_root: Path | None = None) -> bytes:
    """Одна страница файла — картинкой PNG. Нумерация с единицы."""
    if not path.is_file():
        raise PageRenderError("файл не найден на диске")
    if not is_renderable(path.name):
        raise PageRenderError("такой файл страницами не показывается")
    if number < 1 or number > MAX_PAGE:
        raise PageRenderError("такой страницы в файле нет")

    cached: Path | None = None
    if cache_root is not None:
        try:
            cached = _cache_path(Path(cache_root), path, number)
            if cached.is_file():
                return cached.read_bytes()
        except OSError:                          # noqa: PERF203 — кэш не обязателен
            cached = None

    pymupdf = _pymupdf()
    try:
        with pymupdf.open(str(path)) as document:
            if getattr(document, "needs_pass", False):
                raise PageRenderError(
                    "файл защищён паролем — страницы не показать; "
                    "расшифруйте его или скачайте и откройте своей программой")
            total = int(getattr(document, "page_count", 0) or 0)
            if number > total:
                raise PageRenderError(
                    f"в файле {total} страниц, а запрошена {number}-я")
            pixmap = document[number - 1].get_pixmap(dpi=DPI)
            data = pixmap.tobytes("png")
    except PageRenderError:
        raise
    except Exception as error:                  # noqa: BLE001
        raise PageRenderError(
            f"страницу {number} нарисовать не удалось: {error}") from error

    if cached is not None:
        # Кэш — удобство: не записался, значит, нарисуем ещё раз.
        try:
            with _lock:
                cached.parent.mkdir(parents=True, exist_ok=True)
                temporary = cached.with_suffix(".part")
                temporary.write_bytes(data)
                temporary.replace(cached)
        except OSError as error:
            log.debug("страница не легла в кэш %s: %s", cached, error)
    return data


def preview_pages(path: Path) -> "Tuple[int, bool]":
    """Число страниц и можно ли вообще показать файл страницами."""
    if not is_renderable(path.name):
        return 0, False
    return page_count(path), True
