"""Наследие девяностых и двухтысячных: .doc, .xls, .ppt, .rtf, .wps, .pub.

У заказчика библиотека копилась десятилетиями, и половина методичек и
протоколов лежит в форматах, которые Microsoft закрыла ещё до появления OOXML.
Читать их напрямую нечем: двоичный ``.doc`` — это дерево OLE со структурами
Word 97, ``.ppt`` — свой формат записей, ``.pub`` — вообще Publisher. Писать
разборщики этих форматов бессмысленно, зато рядом есть готовый: **headless
LibreOffice**, который умеет всё перечисленное и работает офлайн.

Схема одна на все старые форматы:

    старый файл → LibreOffice → современный формат → существующий конвертер

Так ``.doc`` превращается в ``.docx`` и попадает к разборщику DOCX из
:mod:`reportgen.ingest.convert` (заголовки по стилям, таблицы целиком),
``.ppt`` — в ``.pptx`` и уходит к конвертеру презентаций из
:mod:`reportgen.ingest.formats.office` (слайд разделом, заметки докладчика),
``.xls`` — в ``.xlsx``. Ничего из разметки не теряется дважды: конвертер
формата ровно один, LibreOffice только меняет упаковку.

Три ловушки headless-LibreOffice, из-за которых наивный вызов не работает:

* **Общий профиль пользователя.** Два одновременных запуска ``soffice`` с одним
  профилем не работают: второй молча присоединяется к первому, ничего не
  конвертирует и выходит с кодом 0. Поэтому каждому запуску выдаётся свой
  профиль во временном каталоге (``-env:UserInstallation=file:///...``), и приём
  каталога можно вести в несколько потоков.
* **Код возврата не значит ничего.** На нечитаемом файле ``soffice`` печатает
  ``Error: source file could not be loaded`` и всё равно завершается с кодом 0.
  Единственный надёжный признак успеха — появившийся файл в целевом каталоге.
* **Зависания.** На битом файле LibreOffice умеет висеть вечно, поэтому у
  запуска обязательный таймаут (:data:`SOFFICE_TIMEOUT`), а по таймауту
  убивается вся группа процессов: ``soffice`` — это лаунчер, который порождает
  ``soffice.bin``.

RTF стоит особняком: это текстовый формат, и для него есть чистый Python —
пакет ``striprtf``. Он быстрее LibreOffice на два порядка и не требует ничего
стороннего, поэтому основной конвертер RTF — на нём, а LibreOffice остаётся
запасным путём (отдельная запись в реестре с меньшим приоритетом).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Sequence, Tuple

from .. import registry
from ..convert import ConvertedDocument, _clean_line, _read_text, page_marker

__all__ = [
    "SOFFICE_ENV_VAR",
    "SOFFICE_TIMEOUT",
    "SofficeResult",
    "convert_doc",
    "convert_opendocument",
    "convert_ppt",
    "convert_pub",
    "convert_rtf",
    "convert_rtf_soffice",
    "convert_with_soffice",
    "convert_xls_soffice",
    "soffice_binary",
]

#: Сколько секунд ждать LibreOffice. Двух минут хватает на книгу Excel в
#: несколько десятков листов; всё, что дольше, — это зависание на битом файле,
#: а не работа. Значение вынесено в константу: тестам и медленным машинам его
#: удобно менять.
SOFFICE_TIMEOUT = 120.0

#: Переменная окружения с путём к soffice. Нужна там, где LibreOffice стоит
#: не в стандартном каталоге (портативная сборка на флешке в изолированном
#: контуре — обычное дело).
SOFFICE_ENV_VAR = "REPORTGEN_SOFFICE"

#: Имена исполняемого файла в PATH.
_SOFFICE_NAMES = ("soffice", "libreoffice")

#: Каталоги установки в Windows: LibreOffice не прописывает себя в PATH, так
#: что без этого списка на машине заказчика ничего не найдётся.
_WINDOWS_ENV_DIRS = ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA")
_WINDOWS_SUITES = (
    "LibreOffice",
    "LibreOffice 7",
    "LibreOffice 6",
    "LibreOffice 5",
    r"Programs\LibreOffice",
    "OpenOffice 4",
    "OpenOffice.org 3",
)
#: ``soffice.exe`` в Windows — это лаунчер: он возвращает управление сразу, и
#: конвертация обрывается на полуслове. Консольный ``soffice.com`` дожидается
#: конца работы, поэтому он первый в списке.
_WINDOWS_BINARIES = ("soffice.com", "soffice.exe")

#: Стандартные каталоги в Linux и macOS — на случай установки мимо PATH.
_POSIX_CANDIDATES = (
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/lib/libreoffice/program/soffice",
    "/usr/local/bin/soffice",
    "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)

_SOFFICE_HINT = (
    "поставьте LibreOffice (Linux: apt install libreoffice-writer libreoffice-calc "
    "libreoffice-impress; Windows: установщик с ru.libreoffice.org, каталог по "
    "умолчанию C:\\Program Files\\LibreOffice), либо укажите путь к soffice в "
    f"переменной окружения {SOFFICE_ENV_VAR}"
)
_NO_SOFFICE_WARNING = (
    "старый формат разбирается через LibreOffice, а он не найден: " + _SOFFICE_HINT
)

_STRIPRTF_HINT = "pip install striprtf; в Windows — py -m pip install striprtf"

#: Кэш поиска: soffice ищется по PATH и по десятку каталогов, а спрашивают о
#: нём при каждом обращении к реестру форматов.
_UNKNOWN = object()
_BINARY_CACHE: Any = _UNKNOWN
_BINARY_LOCK = threading.Lock()


def _reason(error: BaseException) -> str:
    text = str(error).strip()
    return text or error.__class__.__name__


def _windows() -> bool:
    """Отдельная функция, а не ``os.name`` по месту: так её видно тестам."""
    return os.name == "nt"


# --------------------------------------------------------- поиск LibreOffice ---

def _executable(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
    except OSError:  # pragma: no cover — недоступный сетевой путь
        return False
    return _windows() or os.access(str(path), os.X_OK)


def _candidate_paths() -> List[Path]:
    """Стандартные места установки для текущей операционной системы."""
    candidates: List[Path] = []
    if _windows():
        for variable in _WINDOWS_ENV_DIRS:
            base = os.environ.get(variable, "").strip()
            if not base:
                continue
            for suite in _WINDOWS_SUITES:
                for binary in _WINDOWS_BINARIES:
                    candidates.append(Path(base) / suite / "program" / binary)
        return candidates
    candidates.extend(Path(item) for item in _POSIX_CANDIDATES)
    try:
        # Сборки вида /opt/libreoffice24.2/program/soffice — так ставится
        # официальный пакет с сайта, минуя репозиторий дистрибутива.
        candidates.extend(sorted(Path("/opt").glob("libreoffice*/program/soffice"), reverse=True))
    except OSError:  # pragma: no cover — нет каталога /opt
        pass
    return candidates


def _from_env() -> str | None:
    """Путь из переменной окружения: файл или каталог установки."""
    raw = os.environ.get(SOFFICE_ENV_VAR, "").strip().strip('"')
    if not raw:
        return None
    path = Path(raw)
    if _executable(path):
        return str(path)
    names = _WINDOWS_BINARIES if _windows() else _SOFFICE_NAMES
    for directory in (path, path / "program"):
        for name in names:
            candidate = directory / name
            if _executable(candidate):
                return str(candidate)
    return None


def _prefer_console_binary(found: str) -> str:
    """В Windows рядом с ``soffice.exe`` лежит консольный ``soffice.com`` — он лучше."""
    if not _windows():
        return found
    path = Path(found)
    if path.suffix.lower() == ".exe":
        console = path.with_suffix(".com")
        if _executable(console):
            return str(console)
    return found


def _find_soffice() -> str | None:
    from_env = _from_env()
    if from_env:
        return _prefer_console_binary(from_env)
    for name in _SOFFICE_NAMES:
        found = shutil.which(name)
        if found:
            return _prefer_console_binary(found)
    for candidate in _candidate_paths():
        if _executable(candidate):
            return str(candidate)
    return None


def soffice_binary(*, refresh: bool = False) -> str | None:
    """Путь к ``soffice`` или ``None``, если LibreOffice не установлен.

    Ищет по порядку: переменная окружения :data:`SOFFICE_ENV_VAR`, PATH,
    стандартные каталоги установки (в Windows — ``C:\\Program Files\\LibreOffice\\
    program\\soffice.com`` и соседние варианты, включая 32-разрядную установку и
    установку в профиль пользователя).

    Результат кэшируется: поиск дёргается при каждом отчёте о поддержке
    форматов. Если LibreOffice поставили уже во время работы системы, кэш
    сбрасывается вызовом с ``refresh=True``.
    """
    global _BINARY_CACHE
    with _BINARY_LOCK:
        if refresh or _BINARY_CACHE is _UNKNOWN:
            _BINARY_CACHE = _find_soffice()
        return _BINARY_CACHE


class _SofficeRequirement(registry.Requirement):
    """Требование «установлен LibreOffice».

    Обычная проверка требования смотрит в PATH, а в Windows LibreOffice себя
    туда не прописывает — на рабочей машине с установленным пакетом система
    сообщала бы, что старые форматы не поддерживаются. Поэтому доступность
    проверяется тем же поиском, что и при конвертации.
    """

    def is_available(self) -> bool:
        return soffice_binary() is not None


_SOFFICE_REQUIRED = _SofficeRequirement("binary", "soffice", _SOFFICE_HINT)
_DOCX_REQUIRED = registry.Requirement(
    "python", "docx", "pip install python-docx; в Windows — py -m pip install python-docx"
)
_PPTX_REQUIRED = registry.Requirement(
    "python", "pptx", "pip install python-pptx; в Windows — py -m pip install python-pptx"
)
_XLSX_REQUIRED = registry.Requirement(
    "python", "openpyxl", "pip install openpyxl; в Windows — py -m pip install openpyxl"
)
_PDF_REQUIRED = registry.Requirement(
    "python", "pymupdf", "pip install pymupdf; в Windows — py -m pip install pymupdf"
)
_STRIPRTF_REQUIRED = registry.Requirement("python", "striprtf", _STRIPRTF_HINT)


# ------------------------------------------------------------- запуск soffice ---

@dataclass
class SofficeResult:
    """Что получилось у LibreOffice.

    ``path`` — файл в целевом формате; он живёт только внутри блока ``with``,
    после выхода временный каталог удаляется. Если ``path`` пуст, в
    ``warning`` лежит готовое объяснение по-русски.
    """

    path: Path | None = None
    warning: str = ""
    #: Как получен результат — уходит в ``meta['converted_via']``.
    via: str = ""
    command: Tuple[str, ...] = ()
    #: Профиль пользователя этого запуска (нужен тестам и диагностике).
    profile: Path | None = None


def _kill_tree(process: "subprocess.Popen[bytes]") -> None:
    """Убить soffice вместе с порождённым soffice.bin."""
    if os.name == "posix":
        try:
            import signal

            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return
        except (OSError, ImportError):  # pragma: no cover — процесс уже умер
            pass
    try:
        process.kill()
    except OSError:  # pragma: no cover — процесс уже умер
        pass


def _run(command: Sequence[str], timeout: float, cwd: Path) -> Tuple[int, str]:
    """Запустить LibreOffice. Бросает :class:`subprocess.TimeoutExpired` по таймауту."""
    options: Dict[str, Any] = {}
    if os.name == "posix":
        # Своя группа процессов: иначе по таймауту умрёт лаунчер, а soffice.bin
        # останется висеть и держать временный каталог.
        options["start_new_session"] = True
    elif _windows():  # pragma: no cover — ветка только для Windows
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=str(cwd),
        **options,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.communicate(timeout=10)
        except Exception:  # pragma: no cover — процесс уже убит
            pass
        raise
    text = (output or b"").decode("utf-8", errors="replace").strip()
    return int(process.returncode or 0), text


def _output_tail(text: str, limit: int = 200) -> str:
    """Хвост вывода LibreOffice для предупреждения: одной строкой и покороче."""
    joined = " ".join(part.strip() for part in text.splitlines() if part.strip())
    if len(joined) > limit:
        joined = joined[: limit - 1].rstrip() + "…"
    return joined


def _pick_output(outdir: Path, suffix: str, stem: str) -> Path | None:
    """Найти результат: файл нужного расширения в целевом каталоге."""
    try:
        files = [item for item in sorted(outdir.iterdir()) if item.is_file()]
    except OSError:  # pragma: no cover — каталог не создан
        return None
    matching = [item for item in files if item.suffix.lower() == suffix]
    if not matching:
        return None
    exact = [item for item in matching if item.stem == stem]
    chosen = exact or matching
    return max(chosen, key=lambda item: item.stat().st_size)


@contextmanager
def convert_with_soffice(
    path: str | Path,
    target_format: str,
    *,
    timeout: float | None = None,
) -> Iterator[SofficeResult]:
    """Преобразовать файл силами headless LibreOffice.

    Используется как контекстный менеджер — так временные файлы удаляются
    всегда, в том числе если разбор результата упал с исключением::

        with convert_with_soffice(path, "docx") as produced:
            if produced.path is None:
                result.warnings.append(produced.warning)
                return result
            inner = _convert_docx(produced.path)

    ``target_format`` — то же, что у ключа ``--convert-to``: ``"docx"``,
    ``"pdf"``, при необходимости с именем фильтра (``"docx:MS Word 2007 XML"``).

    Исходный файл копируется во временный каталог. Так библиотека заказчика
    остаётся чистой (LibreOffice любит оставить рядом с документом файл
    блокировки ``.~lock.имя#``) и не страдает от прав «только чтение» на сетевой
    папке. Исключений функция не бросает: любая беда превращается в
    ``warning`` по-русски.
    """
    source = Path(path)
    extension = "." + str(target_format).split(":", 1)[0].strip().lstrip(".").lower()
    binary = soffice_binary()
    if binary is None:
        yield SofficeResult(warning=_NO_SOFFICE_WARNING)
        return

    limit = float(SOFFICE_TIMEOUT if timeout is None else timeout)
    workdir = Path(tempfile.mkdtemp(prefix="reportgen-soffice-"))
    try:
        indir = workdir / "in"
        outdir = workdir / "out"
        profile = workdir / "profile"
        indir.mkdir()
        outdir.mkdir()
        # Имя копии — латиницей: LibreOffice в части сборок не открывает
        # файлы с кириллицей в пути, а в библиотеке так названо почти всё.
        local = indir / f"document{source.suffix.lower() or '.bin'}"
        try:
            shutil.copyfile(str(source), str(local))
        except OSError as error:
            yield SofficeResult(warning=f"файл не скопирован для конвертации: {_reason(error)}")
            return

        command = (
            binary,
            "--headless",
            "--invisible",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            str(target_format),
            "--outdir",
            str(outdir),
            str(local),
        )
        try:
            code, output = _run(command, limit, workdir)
        except subprocess.TimeoutExpired:
            yield SofficeResult(
                warning=(
                    f"LibreOffice не уложился в {limit:g} с и был остановлен: файл "
                    "пропущен. Обычно так ведёт себя повреждённый документ; "
                    "откройте его вручную или увеличьте SOFFICE_TIMEOUT"
                ),
                command=command,
                profile=profile,
            )
            return
        except OSError as error:
            yield SofficeResult(
                warning=f"не удалось запустить LibreOffice ({binary}): {_reason(error)}",
                command=command,
            )
            return

        produced = _pick_output(outdir, extension, local.stem)
        if produced is None:
            # Код возврата у soffice почти всегда 0, даже когда он ничего не
            # сделал, поэтому в предупреждение идёт его собственный вывод.
            tail = _output_tail(output)
            detail = f": {tail}" if tail else ""
            yield SofficeResult(
                warning=(
                    f"LibreOffice не преобразовал файл в {extension.lstrip('.').upper()}"
                    f" (код {code}){detail}. Возможные причины: файл повреждён, "
                    "защищён паролем или в установке нет нужного модуля "
                    "(libreoffice-writer, libreoffice-calc, libreoffice-impress)"
                ),
                command=command,
                profile=profile,
            )
            return

        yield SofficeResult(
            path=produced,
            via=f"libreoffice→{extension.lstrip('.')}",
            command=command,
            profile=profile,
        )
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)


# ------------------------------------------- передача существующим конвертерам ---

def _docx_converter() -> Tuple[Callable[[Path], ConvertedDocument] | None, str]:
    from ..convert import _convert_docx

    return _convert_docx, ""


def _pdf_converter() -> Tuple[Callable[[Path], ConvertedDocument] | None, str]:
    from ..convert import _convert_pdf

    return _convert_pdf, ""


def _pptx_converter() -> Tuple[Callable[[Path], ConvertedDocument] | None, str]:
    try:
        from .office import convert_pptx
    except ImportError as error:
        return None, (
            "презентацию преобразовывать некому: модуль "
            f"reportgen.ingest.formats.office не загружен ({_reason(error)}) — "
            "проверьте комплект поставки и пакет python-pptx"
        )
    return convert_pptx, ""


def _xlsx_converter() -> Tuple[Callable[[Path], ConvertedDocument] | None, str]:
    try:
        from .office import convert_xlsx
    except ImportError as error:
        return None, (
            "книгу Excel преобразовывать некому: модуль "
            f"reportgen.ingest.formats.office не загружен ({_reason(error)}) — "
            "проверьте комплект поставки и пакет openpyxl"
        )
    return convert_xlsx, ""


def _adopt(
    result: ConvertedDocument,
    inner: ConvertedDocument,
    path: Path,
    via: str,
    extension: str,
) -> None:
    """Перенести результат промежуточного конвертера в итоговый документ."""
    source_format = result.meta.get("source_format", "")
    meta = dict(inner.meta)
    meta.pop("source_format", None)
    result.meta.update(meta)
    result.meta["source_format"] = source_format
    result.meta["converted_via"] = via
    result.text = inner.text
    result.page_count = inner.page_count
    result.needs_ocr = inner.needs_ocr
    result.warnings.extend(inner.warnings)
    result.title = inner.title or path.stem
    if inner.is_empty and not inner.warnings:
        result.warnings.append(
            f"LibreOffice преобразовал файл в {extension.upper()}, "
            "но текста в нём не оказалось"
        )


def _via_soffice(
    path: str | Path,
    *,
    target: str,
    source_format: str,
    inner: Callable[[], Tuple[Callable[[Path], ConvertedDocument] | None, str]],
    timeout: float | None = None,
) -> ConvertedDocument:
    """Общая схема: LibreOffice → современный формат → готовый конвертер."""
    path = Path(path)
    result = ConvertedDocument(title=path.stem, meta={"source_format": source_format})
    converter, problem = inner()
    if converter is None:
        # Проверяем до запуска LibreOffice: незачем тратить минуту на файл,
        # результат которого всё равно некому разобрать.
        result.warnings.append(problem)
        return result

    with convert_with_soffice(path, target, timeout=timeout) as produced:
        if produced.path is None:
            result.warnings.append(produced.warning)
            return result
        try:
            inner_result = converter(produced.path)
        except Exception as error:  # noqa: BLE001 — один файл не роняет приём каталога
            result.warnings.append(
                f"промежуточный {target.upper()} не разобран: {_reason(error)}"
            )
            return result
        _adopt(result, inner_result, path, produced.via, target)
    return result


def _source_format(path: Path, default: str) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix or default


def convert_doc(path: Path) -> ConvertedDocument:
    """DOC/DOT/WPS → DOCX силами LibreOffice → разбор конвертером DOCX.

    Номеров страниц у результата нет: разбивка на страницы в Word живёт не в
    файле, а в раскладке при печати, и в DOCX её тоже нет. Зато сохраняются
    заголовки по стилям и таблицы целиком — для таблицы допусков это важнее.
    """
    path = Path(path)
    return _via_soffice(
        path,
        target="docx",
        source_format=_source_format(path, "doc"),
        inner=_docx_converter,
    )


def convert_ppt(path: Path) -> ConvertedDocument:
    """PPT/PPS/POT → PPTX силами LibreOffice → разбор конвертером презентаций."""
    path = Path(path)
    return _via_soffice(
        path,
        target="pptx",
        source_format=_source_format(path, "ppt"),
        inner=_pptx_converter,
    )


def convert_xls_soffice(path: Path) -> ConvertedDocument:
    """XLS/XLT → XLSX силами LibreOffice → разбор конвертером книг Excel.

    Запасной путь: обычно двоичные книги читает xlrd — он быстрее и не требует
    LibreOffice, поэтому у этой записи в реестре приоритет ниже. LibreOffice
    выручает там, где xlrd не поставили или книга слишком новая для него.
    """
    path = Path(path)
    return _via_soffice(
        path,
        target="xlsx",
        source_format=_source_format(path, "xls"),
        inner=_xlsx_converter,
    )


def convert_pub(path: Path) -> ConvertedDocument:
    """PUB (Microsoft Publisher) → PDF силами LibreOffice → разбор конвертером PDF.

    Publisher открывается в LibreOffice Draw, а из Draw в текстовый формат
    ничего осмысленного не сохранить: буклет — это не поток абзацев, а набор
    врезок на листе. PDF сохраняет и текст, и разбивку на страницы, поэтому под
    цитатой из буклета будет номер страницы.
    """
    path = Path(path)
    return _via_soffice(
        path,
        target="pdf",
        source_format=_source_format(path, "pub"),
        inner=_pdf_converter,
    )


#: Во что превращать OpenDocument, если родной конвертер недоступен.
_ODF_TARGETS: Dict[str, Tuple[str, str]] = {
    ".odt": ("docx", "text"),
    ".ott": ("docx", "text"),
    ".fodt": ("docx", "text"),
    ".ods": ("xlsx", "spreadsheet"),
    ".ots": ("xlsx", "spreadsheet"),
    ".fods": ("xlsx", "spreadsheet"),
    ".odp": ("pptx", "presentation"),
    ".otp": ("pptx", "presentation"),
    ".fodp": ("pptx", "presentation"),
}


def convert_opendocument(path: Path) -> ConvertedDocument:
    """ODT/ODS/ODP через LibreOffice — запасной путь.

    Обычно OpenDocument разбирается напрямую (:mod:`reportgen.ingest.formats.opendoc`,
    без сторонних пакетов), и приоритет у того конвертера выше. Эта запись
    страхует случай, когда модуль прямого разбора недоступен или споткнулся на
    экзотическом файле, зато LibreOffice в контуре есть.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    target, _kind = _ODF_TARGETS.get(suffix, ("docx", "text"))
    inner = {
        "docx": _docx_converter,
        "xlsx": _xlsx_converter,
        "pptx": _pptx_converter,
    }[target]
    return _via_soffice(
        path,
        target=target,
        source_format=_source_format(path, "odf"),
        inner=inner,
    )


def convert_rtf_soffice(path: Path) -> ConvertedDocument:
    """RTF → DOCX силами LibreOffice. Запасной путь, когда нет striprtf."""
    path = Path(path)
    return _via_soffice(
        path,
        target="docx",
        source_format="rtf",
        inner=_docx_converter,
    )


# ------------------------------------------------------------------------ RTF ---

#: Начало файла: {\rtf1 у Word и LibreOffice, {\urtf у Cocoa из macOS.
_RTF_SIGNATURE_RE = re.compile(r"^\s*\{\s*\\u?rtf\d", re.IGNORECASE)
#: Управляющее слово заканчивается на не-букве, иначе \par поймает \pard.
_PAR_RE = re.compile(r"\\par(?![a-zA-Z])")
_PAGE_RE = re.compile(r"\\page(?![a-zA-Z])")
_OUTLINE_RE = re.compile(r"\\outlinelevel(\d+)")
_STYLE_RE = re.compile(r"\\s(\d+)(?![0-9])")
_ANSICPG_RE = re.compile(r"\\ansicpg(\d+)")
_CONTROL_WORD_RE = re.compile(r"\\[a-zA-Z]+-?\d* ?")
_CONTROL_SYMBOL_RE = re.compile(r"\\[^a-zA-Z]")
_HEADING_NAME_RE = re.compile(r"^(?:heading|заголовок)\s*(\d+)$", re.IGNORECASE)

#: Метки, которые подмешиваются в исходный RTF до разбора. striprtf отдаёт
#: только плоский текст, поэтому структуру приходится помечать заранее: метка
#: — это обычный текст, она доезжает до результата и там снимается.
_PAGE_TOKEN = "@@RG-PAGE@@"
_HEAD_TOKEN_RE = re.compile(r"@@RG-H([1-6])@@\s*$")
_TOKEN_CLEANUP_RE = re.compile(r"@@RG-(?:PAGE|H[1-6])@@")

#: Символы маркированного списка, которые Word кладёт в {\listtext …}.
_BULLETS = "·•▪◦‣–—-*o"
_BULLET_RE = re.compile(rf"^\s*[{re.escape(_BULLETS)}]\s*\t\s*")
_NUMBER_RE = re.compile(r"^\s*(\d{1,3})[.)]\s*\t\s*")
#: В верстке кириллицей кегль ставится, а вот кодовая страница — не всегда.
_DEFAULT_CODEPAGE = 1251


def _head_token(level: int) -> str:
    return f"@@RG-H{level}@@"


def _brace_group(text: str, start: int) -> Tuple[str, int]:
    """Содержимое группы, начинающейся с ``{`` в позиции ``start``.

    Возвращает пару (содержимое без внешних скобок, позиция за закрывающей
    скобкой). Экранированные скобки ``\\{`` и ``\\}`` не считаются.
    """
    depth = 0
    index = start
    length = len(text)
    while index < length:
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return text[start + 1 :], length


def _top_level_groups(text: str) -> List[str]:
    """Группы первого уровня внутри содержимого группы."""
    groups: List[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "{":
            body, index = _brace_group(text, index)
            groups.append(body)
            continue
        index += 1
    return groups


def _plain_fragment(text: str) -> str:
    """Грубо очищенный от разметки кусок RTF — для имён стилей."""
    without_groups = ""
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == "\\":
            without_groups += text[index : index + 2]
            index += 2
            continue
        if character == "{":
            _body, index = _brace_group(text, index)
            continue
        without_groups += character
        index += 1
    stripped = _CONTROL_WORD_RE.sub("", without_groups)
    stripped = _CONTROL_SYMBOL_RE.sub("", stripped)
    return stripped.replace(";", " ").strip()


def _codepage(text: str) -> int:
    match = _ANSICPG_RE.search(text)
    if not match:
        return _DEFAULT_CODEPAGE
    try:
        page = int(match.group(1))
    except ValueError:  # pragma: no cover — \ansicpg без числа
        return _DEFAULT_CODEPAGE
    return page if page > 0 else _DEFAULT_CODEPAGE


def _heading_styles(text: str) -> Dict[int, int]:
    """Номера стилей заголовков из таблицы стилей: ``{\\s1 … heading 1;}``."""
    start = text.find("{\\stylesheet")
    if start < 0:
        return {}
    body, _end = _brace_group(text, start)
    styles: Dict[int, int] = {}
    for group in _top_level_groups(body):
        number = _STYLE_RE.search(group)
        if not number:
            continue
        name = _plain_fragment(group)
        match = _HEADING_NAME_RE.match(name)
        if not match:
            continue
        try:
            styles[int(number.group(1))] = min(max(int(match.group(1)), 1), 6)
        except ValueError:  # pragma: no cover — нечисловой номер стиля
            continue
    return styles


def _paragraph_level(chunk: str, styles: Dict[int, int]) -> int | None:
    """Уровень заголовка абзаца: по ``\\outlinelevelN`` или по номеру стиля."""
    if "\\intbl" in chunk or "\\cell" in chunk:
        # Абзац внутри таблицы заголовком не бывает, а метка сломала бы строку.
        return None
    outline = _OUTLINE_RE.search(chunk)
    if outline:
        try:
            return min(int(outline.group(1)) + 1, 6)
        except ValueError:  # pragma: no cover — \outlinelevel без числа
            return None
    if styles:
        for match in _STYLE_RE.finditer(chunk):
            level = styles.get(int(match.group(1)))
            if level:
                return level
    return None


def _mark_rtf_structure(text: str) -> Tuple[str, int, int]:
    """Расставить в исходном RTF метки заголовков и разрывов страниц.

    Метка заголовка приписывается **в конец** абзаца, перед ``\\par``: в начале
    абзаца стоит преамбула файла (таблицы шрифтов и стилей), туда обычный текст
    вставлять нельзя.
    """
    styles = _heading_styles(text)
    pieces: List[str] = []
    headings = 0
    position = 0
    for match in _PAR_RE.finditer(text):
        chunk = text[position : match.start()]
        level = _paragraph_level(chunk, styles)
        if level:
            chunk += _head_token(level)
            headings += 1
        pieces.append(chunk)
        pieces.append(match.group(0))
        position = match.end()
    pieces.append(text[position:])
    prepared = "".join(pieces)
    prepared, breaks = _PAGE_RE.subn(_PAGE_TOKEN, prepared)
    return prepared, breaks, headings


def _rows_to_markdown(rows: Sequence[Sequence[str]]) -> str:
    """Строки таблицы → таблица Markdown целиком, первая строка — шапка."""
    filled = [list(row) for row in rows if any(cell.strip() for cell in row)]
    if not filled:
        return ""
    width = max(len(row) for row in filled)
    filled = [row + [""] * (width - len(row)) for row in filled]
    lines = [
        "| " + " | ".join(filled[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in filled[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _is_table_row(line: str) -> bool:
    """Строку таблицы striprtf отдаёт как «ячейка|ячейка|»."""
    stripped = line.rstrip()
    return stripped.endswith("|") and len(stripped) > 1


def _row_cells(line: str) -> List[str]:
    cells = line.rstrip().split("|")
    if cells and not cells[-1].strip():
        cells.pop()
    return [_clean_line(cell.replace("\t", " ")) for cell in cells]


def _list_item(line: str) -> str | None:
    """Пункт списка: Word кладёт маркер или номер в текст перед табуляцией."""
    bullet = _BULLET_RE.match(line)
    if bullet:
        return "- " + _clean_line(line[bullet.end() :])
    number = _NUMBER_RE.match(line)
    if number:
        return f"{number.group(1)}. " + _clean_line(line[number.end() :])
    return None


def _rtf_markdown(plain: str, breaks: int) -> Tuple[str, int, int]:
    """Плоский текст striprtf → Markdown.

    Возвращает (текст, число таблиц, число страниц). Страницы считаются по
    фактически расставленным маркерам, а не по числу найденных ``\\page``:
    разрыв мог оказаться в колонтитуле, который striprtf выбрасывает.
    """
    blocks: List[str] = []
    rows: List[List[str]] = []
    tables = 0
    page = 1

    def flush() -> None:
        nonlocal tables
        if not rows:
            return
        markdown = _rows_to_markdown(rows)
        rows.clear()
        if markdown:
            tables += 1
            blocks.append(markdown)

    if breaks:
        blocks.append(page_marker(page))
    for raw_line in plain.split("\n"):
        line = raw_line.replace("\r", "")
        if _PAGE_TOKEN in line:
            flush()
            page += 1
            line = line.replace(_PAGE_TOKEN, "")
            blocks.append(page_marker(page))
        heading = _HEAD_TOKEN_RE.search(line)
        if heading:
            flush()
            text = _clean_line(_TOKEN_CLEANUP_RE.sub("", line))
            if text:
                blocks.append("#" * int(heading.group(1)) + " " + text)
            continue
        if _is_table_row(line):
            cells = _row_cells(line)
            if any(cells):
                rows.append(cells)
                continue
        flush()
        item = _list_item(line)
        if item is not None:
            if item.strip() in ("-", ""):
                continue
            if blocks and _same_list(blocks[-1], item):
                blocks[-1] = f"{blocks[-1]}\n{item}"
            else:
                blocks.append(item)
            continue
        text = _clean_line(_TOKEN_CLEANUP_RE.sub("", line))
        if text:
            blocks.append(text)
    flush()
    return "\n\n".join(blocks), tables, (page if breaks else 0)


def _same_list(previous: str, item: str) -> bool:
    """Соседние пункты одного списка держим одним блоком."""
    last = (previous.splitlines() or [""])[-1]
    if item[:2] == "- ":
        return last.startswith("- ")
    numbered = re.compile(r"^\d{1,3}\. ")
    return bool(numbered.match(last)) and bool(numbered.match(item))


def _rtf_field(text: str, word: str, codepage: int) -> str:
    """Поле из ``{\\info}``: заголовок документа, автор."""
    needle = "{\\" + word
    start = text.find(needle)
    if start < 0:
        return ""
    after = start + len(needle)
    if after < len(text) and text[after].isalpha():
        return ""
    body, _end = _brace_group(text, start)
    fragment = body[len(needle) - 1 :]
    return _rtf_fragment_text(fragment, codepage)


def _rtf_fragment_text(fragment: str, codepage: int) -> str:
    """Кусок RTF → текст: заворачиваем в минимальный документ и отдаём striprtf."""
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError:  # pragma: no cover — проверено вызывающим кодом
        return ""
    document = "{\\rtf1\\ansi\\ansicpg%d %s}" % (codepage, fragment)
    try:
        return _clean_line(rtf_to_text(document, encoding=f"cp{codepage}", errors="ignore"))
    except Exception:  # noqa: BLE001 — на служебном поле не падаем
        return ""


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def convert_rtf(path: Path) -> ConvertedDocument:
    """RTF → Markdown через striprtf: заголовки, таблицы, списки, страницы.

    striprtf отдаёт плоский текст, помечая ячейки таблицы вертикальной чертой,
    а строки — переводом строки. Структуру, которой в плоском тексте нет
    (заголовки и разрывы страниц), помечаем прямо в исходном RTF обычным
    текстом-меткой до разбора — см. :func:`_mark_rtf_structure`.
    """
    path = Path(path)
    result = ConvertedDocument(title=path.stem, meta={"source_format": "rtf"})
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError:
        if soffice_binary() is not None:
            # Пакета нет, зато есть LibreOffice — идём длинным путём.
            return convert_rtf_soffice(path)
        result.warnings.append(
            f"для разбора RTF нужен пакет striprtf ({_STRIPRTF_HINT}) "
            "либо установленный LibreOffice; сейчас нет ни того, ни другого"
        )
        return result

    text, encoding, problem = _read_text(path)
    if problem:
        result.warnings.append(problem)
    if encoding:
        result.meta["encoding"] = encoding
    if not text.strip():
        result.warnings.append("файл пуст")
        return result
    if not _RTF_SIGNATURE_RE.match(text):
        head = _clean_line(text[:40])
        result.warnings.append(
            "файл не похож на RTF: нет заголовка «{\\rtf» "
            f"(начало файла: «{head}») — проверьте, тот ли это файл"
        )
        return result

    codepage = _codepage(text)
    result.meta["codepage"] = codepage
    prepared, breaks, headings = _mark_rtf_structure(text)
    try:
        plain = rtf_to_text(prepared, encoding=f"cp{codepage}", errors="ignore")
    except LookupError:
        # Неизвестная кодовая страница из \ansicpg — читаем как cp1251.
        result.warnings.append(
            f"кодовая страница cp{codepage} неизвестна, текст прочитан как cp{_DEFAULT_CODEPAGE}"
        )
        try:
            plain = rtf_to_text(prepared, encoding=f"cp{_DEFAULT_CODEPAGE}", errors="ignore")
        except Exception as error:  # noqa: BLE001 — битый файл не роняет приём
            result.warnings.append(f"RTF не разобран: {_reason(error)}")
            return result
    except Exception as error:  # noqa: BLE001 — битый файл не роняет приём каталога
        result.warnings.append(f"RTF не разобран: {_reason(error)}")
        return result

    result.text, tables, pages = _rtf_markdown(plain, breaks)
    result.meta["converted_via"] = "striprtf"
    if tables:
        result.meta["tables"] = tables
    if headings:
        result.meta["headings"] = headings
    if pages:
        # Номер страницы есть только там, где в файле стоял явный разрыв:
        # автоматическую разбивку RTF не хранит, и выдумывать её нельзя.
        result.page_count = pages
        result.meta["page_count"] = pages

    title = _rtf_field(text, "title", codepage)
    author = _rtf_field(text, "author", codepage)
    if author:
        result.meta["author"] = author
    result.title = title or _first_heading(result.text) or path.stem
    if result.is_empty:
        result.warnings.append(
            "в RTF не найдено текста — возможно, документ состоит из картинок"
        )
    return result


# ------------------------------------------------------------- регистрация ----

registry.register(registry.ConverterSpec(
    name="doc-libreoffice",
    suffixes=(".doc", ".dot", ".wps"),
    convert=convert_doc,
    requires=(_SOFFICE_REQUIRED, _DOCX_REQUIRED),
    note="Word 97–2003 и Works: LibreOffice → DOCX → штатный разбор DOCX",
))

registry.register(registry.ConverterSpec(
    name="ppt-libreoffice",
    suffixes=(".ppt", ".pps", ".pot"),
    convert=convert_ppt,
    requires=(_SOFFICE_REQUIRED, _PPTX_REQUIRED),
    note="PowerPoint 97–2003: LibreOffice → PPTX → разбор со слайдами и заметками",
))

registry.register(registry.ConverterSpec(
    name="xls-libreoffice",
    suffixes=(".xls", ".xlt"),
    convert=convert_xls_soffice,
    requires=(_SOFFICE_REQUIRED, _XLSX_REQUIRED),
    # Ниже конвертера на xlrd: тот читает .xls сам, за миллисекунды и без
    # LibreOffice. Эта запись подхватывает формат там, где xlrd не поставили.
    priority=-10,
    note="Excel 97–2003 через LibreOffice → XLSX (запасной путь к xlrd)",
))

registry.register(registry.ConverterSpec(
    name="pub-libreoffice",
    suffixes=(".pub",),
    convert=convert_pub,
    requires=(_SOFFICE_REQUIRED, _PDF_REQUIRED),
    note="Microsoft Publisher: LibreOffice → PDF → разбор с номерами страниц",
))

registry.register(registry.ConverterSpec(
    name="opendocument-libreoffice",
    suffixes=(".odt", ".ott", ".fodt", ".ods", ".ots", ".fods", ".odp", ".otp", ".fodp"),
    convert=convert_opendocument,
    # Прямой разбор OpenDocument не требует ничего стороннего и всегда лучше;
    # эта запись — страховка на случай, если он недоступен.
    requires=(_SOFFICE_REQUIRED,),
    priority=-20,
    note="OpenDocument через LibreOffice — запасной путь к прямому разбору",
))

registry.register(registry.ConverterSpec(
    name="rtf",
    suffixes=(".rtf",),
    convert=convert_rtf,
    requires=(_STRIPRTF_REQUIRED,),
    note="RTF на чистом Python: таблицы, заголовки по стилям, разрывы страниц",
))

registry.register(registry.ConverterSpec(
    name="rtf-libreoffice",
    suffixes=(".rtf",),
    convert=convert_rtf_soffice,
    requires=(_SOFFICE_REQUIRED, _DOCX_REQUIRED),
    # Только если striprtf не установлен: он и быстрее, и не тянет LibreOffice.
    priority=-10,
    note="RTF через LibreOffice → DOCX (запасной путь к striprtf)",
))
