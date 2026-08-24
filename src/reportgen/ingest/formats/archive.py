"""Архивы: ZIP, 7z, RAR — «материалы одним файлом».

Заказчик редко присылает документы по одному. Приходит `материалы_РРЛ.zip`, а
внутри — методика в DOCX, протокол измерений в XLSX, скан ГОСТа в PDF и десяток
фотографий стойки. Разбирать такой архив руками означает раскладывать его по
каталогу библиотеки перед каждым приёмом; вместо этого архив принимается как
один документ, а каждый вложенный файл становится его разделом.

Как устроен результат:

* документ открывается заголовком первого уровня — именем архива, дальше
  раздел ``## имя/файла.ext`` на каждый разобранный файл, сразу за заголовком —
  ``page_marker``. «Страница» архива — это порядковый номер вложенного файла,
  поэтому под цитатой видно, из какого именно файла архива она взята;
* собственная нумерация страниц вложенного PDF снимается
  (:func:`~reportgen.ingest.convert.strip_page_markers`): в одной ссылке не
  могут уживаться две нумерации, а номер файла в архиве полезнее;
* заголовки вложенного документа опускаются на два уровня
  (:func:`shift_headings`), чтобы они оказались внутри раздела своего файла, а
  не рвали структуру архива;
* файлы, которые разобрать нечем, перечисляются разделом «Состав архива» с
  причиной. Это не ошибка: инженеру важно видеть, что в архиве были ещё и
  чертежи DWG, даже если их содержимое в индекс не попало.

Защита (архив приходит извне, доверять ему нельзя):

* **обход каталога** — элемент с ``..`` в имени, с абсолютным путём или с
  буквой диска отбрасывается (:func:`safe_member_path`), символьные ссылки
  пропускаются: распаковка не должна писать ничего за пределы своего
  временного каталога;
* **архивная бомба** — ограничены и число файлов (:data:`MAX_MEMBERS`), и
  суммарный распакованный объём (:data:`MAX_TOTAL_BYTES`). Предел общий на
  весь архив, включая вложенные, и о его превышении сказано предупреждением;
* **вложенные архивы** — открывается только сам архив (:data:`MAX_DEPTH`);
  архив внутри архива перечисляется в составе, но не распаковывается, иначе
  на подшивке «архив в архиве в архиве» приём не закончится никогда;
* **пароль** — зашифрованные элементы пропускаются с предупреждением, приём
  на них не падает;
* временный каталог удаляется всегда, в том числе при любой ошибке.

Имена файлов в ZIP, собранных русским проводником или старым WinRAR, лежат не
в UTF-8, а в кодировке DOS (:func:`decode_member_name`) — без перекодировки
вместо «отчёт.docx» в заголовке раздела оказывается «®в票 .docx».

ZIP разбирается стандартной библиотекой и не требует ничего стороннего. 7z и
RAR — только через внешний архиватор (7-Zip или unrar); если его нет,
конвертер объявлен, но недоступен, и реестр скажет, что именно доложить в
комплект поставки.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .. import registry
from ..convert import (
    ConvertedDocument,
    _clean_line,
    _reason,
    convert_file,
    page_marker,
    strip_page_markers,
)

__all__ = [
    "ARCHIVE_SUFFIXES",
    "ArchiveEntry",
    "Limits",
    "MAX_DEPTH",
    "MAX_MEMBERS",
    "MAX_TOTAL_BYTES",
    "convert_rar",
    "convert_seven_zip",
    "convert_zip",
    "decode_member_name",
    "safe_member_path",
    "shift_headings",
    "unique_target",
]

#: Сколько файлов из одного архива берём в разбор. Двести документов — это уже
#: не «материалы к письму», а выгрузка каталога: её надо класть в библиотеку
#: каталогом, а не архивом, иначе весь массив станет одним документом.
MAX_MEMBERS = 200

#: Предел суммарного распакованного объёма. Защита от архивной бомбы: файл на
#: сорок килобайт разворачивается в гигабайты нулей и забивает диск.
MAX_TOTAL_BYTES = 500 * 1024 * 1024

#: Сколько уровней архивов открываем. 1 — только сам архив.
MAX_DEPTH = 1

#: Расширения, которые считаются архивами: их не отдают обычным конвертерам.
ARCHIVE_SUFFIXES = (
    ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".xz",
    ".txz", ".cab", ".arj", ".lzh", ".iso",
)

#: Архивы, которые система умеет открывать сама, и вид каждого из них.
_ARCHIVE_KINDS: Dict[str, str] = {".zip": "zip", ".7z": "7z", ".rar": "rar"}

#: Размер блока при распаковке. Читаем потоком: файл на 300 МБ в память не берём.
_BLOCK = 1 << 20

#: Сколько предупреждений от вложенных файлов пропускаем наружу. На архиве из
#: двухсот сканов их иначе будут сотни, и итог приёма перестанет читаться.
MAX_MEMBER_WARNINGS = 40

#: Таймаут на перечисление содержимого внешним архиватором.
LIST_TIMEOUT = 60.0

#: Таймаут на распаковку. Полгигабайта LZMA распаковывается минуты.
EXTRACT_TIMEOUT = 900.0

#: Как поставить 7-Zip. Уходит в диагностику реестра, поэтому оба контура.
SEVEN_ZIP_HINT = (
    "архиватор 7-Zip: Linux — apt install p7zip-full (или dnf install p7zip "
    "p7zip-plugins); Windows — 7-zip.org, каталог «C:\\Program Files\\7-Zip» "
    "добавить в PATH"
)

#: Как поставить unrar. RAR закрытый, свободного распаковщика в базовой
#: поставке дистрибутивов обычно нет — это надо знать заранее.
UNRAR_HINT = (
    "распаковщик RAR: Linux — apt install unrar (в Debian это несвободный "
    "репозиторий non-free; альтернатива — p7zip-rar); Windows — WinRAR с "
    "rarlab.com, каталог «C:\\Program Files\\WinRAR» добавить в PATH"
)

#: Порядок перебора кодировок вывода внешнего архиватора: русская консоль
#: Windows отвечает в cp866, Linux — в UTF-8.
_OUTPUT_ENCODINGS = ("utf-8", "cp866", "cp1251")

#: Служебный мусор файловых менеджеров: в библиотеке ему делать нечего.
_JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

#: Символы, недопустимые в имени файла на Windows, плюс управляющие.
_BAD_CHARS_RE = re.compile(r'[\x00-\x1f<>:"|?*\\]')

#: Имена устройств DOS: файл «CON.txt» на Windows не создать.
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_HEADING_LINE_RE = re.compile(r"^(#{1,6})(\s+)(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LISTING_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _-]*?)\s*[:=]\s*(.*?)\s*$")
_DASH_LINE_RE = re.compile(r"^-{10,}\s*$", re.MULTILINE)


# ------------------------------------------------------------ пределы ----

@dataclass(frozen=True)
class Limits:
    """Пределы разбора одного архива.

    Вынесены в параметр, а не только в константы, чтобы их можно было сузить в
    тестах и в будущем — в настройках приёма, не трогая код конвертера.
    """

    max_members: int = MAX_MEMBERS
    max_total_bytes: int = MAX_TOTAL_BYTES
    max_depth: int = MAX_DEPTH


@dataclass
class _Budget:
    """Остаток пределов. Один на весь архив, включая вложенные."""

    members: int
    space: int
    unpacked: int = 0
    members_hit: bool = False
    space_hit: bool = False

    def take_member(self) -> bool:
        """Разрешить ещё один файл. ``False`` — предел числа файлов исчерпан."""
        if self.members <= 0:
            self.members_hit = True
            return False
        self.members -= 1
        return True

    def take_space(self, size: int) -> bool:
        """Разрешить ``size`` байт. ``False`` — предел объёма исчерпан."""
        if size > self.space:
            self.space_hit = True
            return False
        self.space -= size
        self.unpacked += size
        return True


@dataclass
class ArchiveEntry:
    """Распакованный файл архива."""

    #: Имя внутри архива — то, что видно в заголовке раздела.
    name: str
    #: Где файл лежит распакованным (внутри временного каталога).
    path: Path
    size: int = 0


@dataclass
class _Unpacked:
    """Итог распаковки: что удалось достать и что при этом случилось."""

    opened: bool = False
    entries: List[ArchiveEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: Сколько файлов было в архиве (без каталогов и служебного мусора).
    total: int = 0
    encrypted: int = 0
    unsafe: int = 0
    #: Готовые строки «имя — причина» по файлам, которые распаковать не вышло.
    #: Попадают в состав архива: молча потерянный файл хуже отсутствующего.
    failed: List[str] = field(default_factory=list)


# ------------------------------------------------------------ утилиты ----

def _human_size(size: int) -> str:
    """Размер по-русски: «1,2 МБ»."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} МБ".replace(".", ",")
    if size >= 1024:
        return f"{size / 1024:.1f} КБ".replace(".", ",")
    return f"{size} байт"


def _display_name(name: str) -> str:
    """Имя элемента архива, пригодное для заголовка раздела."""
    return _clean_line(name.replace("\n", " ").replace("\t", " ").replace("\\", "/"))


def decode_member_name(info: zipfile.ZipInfo) -> str:
    """Имя элемента ZIP с поправкой на кодировку.

    По спецификации имя в ZIP либо в UTF-8 (тогда взведён флаг 0x800), либо в
    cp437. Русский проводник и старые версии WinRAR пишут туда cp866, поэтому
    ``zipfile`` возвращает «®в票 .docx» вместо «отчёт.docx». Перекодируем
    обратно, но только если получилась кириллица: ломать латинские имена
    попыткой их «починить» нельзя.
    """
    name = info.filename
    if info.flag_bits & 0x800:  # имя уже в UTF-8
        return _display_name(name)
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return _display_name(name)
    for encoding in ("cp866", "cp1251"):
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _looks_cyrillic(decoded):
            return _display_name(decoded)
    return _display_name(name)


def _looks_cyrillic(text: str) -> bool:
    return any("\u0400" <= character <= "\u04ff" for character in text)


def _safe_part(part: str) -> str:
    """Одна ступень пути, пригодная для файловой системы (в том числе NTFS)."""
    cleaned = _BAD_CHARS_RE.sub("_", part).strip().rstrip(". ")
    if not cleaned:
        return "_"
    stem, dot, suffix = cleaned.rpartition(".")
    if not dot:
        stem, suffix = cleaned, ""
    if stem.lower() in _RESERVED_NAMES:
        stem = f"_{stem}"
    # Длинные имена (а в архивах встречаются на две сотни символов) упираются в
    # предел пути Windows. Расширение сохраняем: по нему выбирается конвертер.
    stem = stem[:80]
    suffix = suffix[:20]
    if not stem and not suffix:
        return "_"
    return f"{stem}.{suffix}" if suffix else stem


def unique_target(target: Path) -> Path:
    """Свободное имя рядом с ``target``.

    ZIP допускает два элемента с одинаковым именем, и разные имена могут
    совпасть после приведения к безопасному виду. Без этой развязки второй
    файл затирает первый, и в документе один и тот же текст оказывается в двух
    разделах — незаметная подмена содержимого хуже пропуска.
    """
    if not target.exists():
        return target
    for number in range(2, 100):
        candidate = target.with_name(f"{target.stem}-{number}{target.suffix}")
        if not candidate.exists():
            return candidate
    return target  # pragma: no cover — сто одноимённых файлов в одном архиве


def safe_member_path(root: Path, name: str) -> Path | None:
    """Куда распаковать элемент архива, или ``None``, если имя опасное.

    Отбрасываются абсолютные пути (``/etc/passwd``), имена с буквой диска
    (``C:\\Windows\\…``) и любой выход вверх по дереву (``../../evil``) — это
    классический обход каталога, которым архив пишет файлы куда захочет.
    Итог дополнительно проверяется по разрешённому пути: имя может увести за
    пределы каталога и через ссылку в уже созданном подкаталоге.
    """
    cleaned = name.replace("\\", "/").strip()
    if not cleaned or cleaned.startswith("/") or _DRIVE_RE.match(cleaned):
        return None
    parts: List[str] = []
    for part in cleaned.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(_safe_part(part))
    if not parts:
        return None
    target = root.joinpath(*parts)
    try:
        base = root.resolve()
        resolved = target.resolve()
    except OSError:  # pragma: no cover — экзотические ФС
        return None
    if resolved == base or base not in resolved.parents:
        return None
    return target


def shift_headings(text: str, by: int) -> str:
    """Опускает заголовки Markdown на ``by`` уровней.

    Вложенный документ начинается со своего «# Название», и без сдвига он
    закрыл бы раздел своего файла: нарезчик корпуса разбирает уровни
    буквально. Внутри блоков кода строки не трогаем — там решётка это
    комментарий, а не заголовок.
    """
    if by <= 0 or not text:
        return text
    lines: List[str] = []
    fence = ""
    for line in text.splitlines():
        opening = _FENCE_RE.match(line)
        if opening:
            marker = opening.group(1)
            fence = "" if fence == marker else (fence or marker)
        elif not fence:
            heading = _HEADING_LINE_RE.match(line)
            if heading:
                level = min(len(heading.group(1)) + by, 6)
                line = "#" * level + heading.group(2) + heading.group(3)
        lines.append(line)
    return "\n".join(lines)


def _is_junk(name: str) -> bool:
    """Служебный мусор архиватора и файлового менеджера."""
    parts = name.split("/")
    if "__MACOSX" in parts:
        return True
    base = parts[-1].lower()
    return base in _JUNK_NAMES or base.startswith("~$") or base.startswith("._")


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    """Символьная ссылка внутри ZIP (записывается только Unix-архиваторами)."""
    if info.create_system != 3:  # 3 — Unix
        return False
    return stat.S_ISLNK(info.external_attr >> 16)


# ------------------------------------------------------- распаковка ZIP ---

class _SpaceExhausted(Exception):
    """Внутренний сигнал: распакованный объём упёрся в предел."""


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:  # pragma: no cover — файла может уже не быть
        pass


def _extract_zip_member(
    bundle: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    target: Path,
    budget: _Budget,
) -> Tuple[int, str]:
    """Распаковывает один элемент. Возвращает (сколько байт, причина отказа)."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return 0, f"не распакован ({_reason(error)})"

    written = 0
    try:
        with bundle.open(info) as source, open(target, "wb") as sink:
            while True:
                block = source.read(_BLOCK)
                if not block:
                    break
                if not budget.take_space(len(block)):
                    raise _SpaceExhausted()
                sink.write(block)
                written += len(block)
    except _SpaceExhausted:
        _remove(target)
        return 0, "не распакован: исчерпан предел суммарного объёма"
    except Exception as error:  # noqa: BLE001 — один элемент не роняет архив
        _remove(target)
        return 0, f"не распакован ({_reason(error)})"
    return written, ""


def _unpack_zip(path: Path, root: Path, budget: _Budget) -> _Unpacked:
    """Распаковывает ZIP во временный каталог, отбрасывая опасные элементы."""
    out = _Unpacked()
    try:
        bundle = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        out.warnings.append(
            f"архив не открыт: {_reason(error)} — файл повреждён или это не ZIP"
        )
        return out
    except OSError as error:
        out.warnings.append(f"архив не открыт: {_reason(error)}")
        return out
    except Exception as error:  # noqa: BLE001 — содержимое файла нам не подконтрольно
        out.warnings.append(f"архив не открыт: {_reason(error)}")
        return out

    out.opened = True
    with bundle:
        try:
            infos = bundle.infolist()
        except Exception as error:  # noqa: BLE001 — битое оглавление
            out.warnings.append(f"оглавление архива не прочитано: {_reason(error)}")
            return out

        members: List[Tuple[zipfile.ZipInfo, str]] = []
        for info in infos:
            if info.is_dir():
                continue
            name = decode_member_name(info)
            if _is_junk(name):
                continue
            members.append((info, name))
        out.total = len(members)

        for info, name in members:
            if _is_zip_symlink(info):
                out.warnings.append(f"«{name}»: символьная ссылка пропущена")
                out.failed.append(f"«{name}» — символьная ссылка, пропущена")
                continue
            if info.flag_bits & 0x1:
                out.encrypted += 1
                out.failed.append(f"«{name}» — зашифрован паролем")
                continue
            target = safe_member_path(root, name)
            if target is None:
                out.unsafe += 1
                out.warnings.append(
                    f"«{name}»: имя выводит за пределы каталога распаковки — "
                    "элемент отброшен"
                )
                out.failed.append(f"«{name}» — небезопасное имя, элемент отброшен")
                continue
            if not budget.take_member():
                break
            target = unique_target(target)
            written, problem = _extract_zip_member(bundle, info, target, budget)
            if problem:
                out.warnings.append(f"«{name}»: {problem}")
                out.failed.append(f"«{name}» — {problem}")
                continue
            out.entries.append(ArchiveEntry(name=name, path=target, size=written))
    return out


# ------------------------------------------- внешние архиваторы (7z, rar) ---

#: Куда установщик 7-Zip кладёт себя под Windows. В PATH он не прописывается,
#: и без этого списка установленный архиватор остаётся невидимым.
_WINDOWS_ARCHIVE_DIRS = (
    r"C:\Program Files\7-Zip",
    r"C:\Program Files (x86)\7-Zip",
    r"C:\Program Files\WinRAR",
    r"C:\Program Files (x86)\UnrarDLL",
)


def archive_binary(name: str) -> str | None:
    """Путь к архиватору или ``None``: сначала PATH, затем каталоги Windows."""
    found = shutil.which(name)
    if found:
        return found
    if os.name != "nt":
        return None
    for directory in _WINDOWS_ARCHIVE_DIRS:
        candidate = Path(directory) / f"{name}.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _archive_tool(kind: str) -> str | None:
    """Путь к архиватору для формата или ``None``.

    Перебор совпадает с требованиями, объявленными в реестре: если здесь
    появится ещё один вариант вызова, для него нужен и свой ``ConverterSpec``,
    иначе диспетчер сочтёт формат недоступным и до конвертера дело не дойдёт.
    """
    names = ("7z", "7za", "7zz") if kind == "7z" else ("unrar", "7z")
    for name in names:
        found = archive_binary(name)
        if found:
            return found
    return None


def _is_unrar(tool: str) -> bool:
    return Path(tool).name.lower().startswith("unrar")


def _decode_output(raw: bytes) -> str:
    for encoding in _OUTPUT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run(command: Sequence[str], timeout: float) -> Tuple[int, str]:
    """Запуск архиватора. Возвращает (код возврата, вывод); -1 — не запустился,
    -2 — не уложился в таймаут.

    Ввод закрыт: иначе архив с паролем повесит приём на приглашении «Enter
    password» — и не на секунды, а до конца таймаута.

    Путь к архиву передаётся как есть, без копирования во временный каталог с
    латинским именем (:func:`reportgen.ingest.convert.external_path`): и
    7-Zip, и unrar работают с юникодными именами, в отличие от djvulibre, ради
    которого эта уловка в системе есть. Копировать полгигабайта ради
    латинского имени дороже, чем оно стоит.
    """
    try:
        completed = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -2, ""
    except OSError as error:
        return -1, _reason(error)
    output = _decode_output(completed.stdout) + "\n" + _decode_output(completed.stderr)
    return completed.returncode, output


def _parse_listing(text: str) -> List[Dict[str, str]]:
    """Разбирает технический листинг архиватора в список записей.

    Понимает обе записи: ``7z l -slt`` печатает «Ключ = значение», ``unrar lt``
    — «Ключ: значение». Записи разделены пустой строкой. У 7-Zip перед списком
    файлов идёт блок свойств самого архива, отделённый строкой из дефисов, —
    его отбрасываем.
    """
    body = text
    parts = _DASH_LINE_RE.split(text)
    if len(parts) > 1:
        body = parts[-1]

    blocks: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in body.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        match = _LISTING_LINE_RE.match(line)
        if match:
            current[match.group(1).strip().lower()] = match.group(2)
    if current:
        blocks.append(current)

    entries: List[Dict[str, str]] = []
    for block in blocks:
        if "size" not in block:
            continue
        if "path" not in block and "name" not in block:
            continue
        entries.append(block)
    return entries


def _listing_totals(entries: Sequence[Dict[str, str]]) -> Tuple[int, bool]:
    """Суммарный распакованный объём и признак «есть зашифрованные файлы»."""
    total = 0
    encrypted = False
    for entry in entries:
        if entry.get("folder", "").strip() == "+":
            continue
        if entry.get("attributes", "").strip().upper().startswith("D"):
            continue
        if entry.get("type", "").strip().lower() in ("directory", "каталог"):
            continue
        if entry.get("encrypted", "").strip() == "+":
            encrypted = True
        if "encrypt" in entry.get("flags", "").lower():
            encrypted = True
        digits = re.sub(r"[^\d]", "", entry.get("size", ""))
        if digits:
            total += int(digits)
    return total, encrypted


def _looks_encrypted(output: str) -> bool:
    lowered = output.lower()
    return any(
        marker in lowered
        for marker in ("wrong password", "encrypted", "password is incorrect",
                       "пароль", "зашифров")
    )


def _list_external(tool: str, path: Path) -> Tuple[int, bool, bool]:
    """Оглавление архива: (объём, есть ли шифрование, удалось ли прочитать)."""
    if _is_unrar(tool):
        command = [tool, "lt", "-p-", str(path)]
    else:
        command = [tool, "l", "-slt", "-p", "--", str(path)]
    code, output = _run(command, LIST_TIMEOUT)
    if code in (-1, -2):
        return 0, False, False
    entries = _parse_listing(output)
    total, encrypted = _listing_totals(entries)
    if code != 0 and not entries:
        return 0, _looks_encrypted(output), False
    return total, encrypted or _looks_encrypted(output), True


def _extract_command(tool: str, path: Path, root: Path) -> List[str]:
    if _is_unrar(tool):
        # -p- — не спрашивать пароль, -o+ — перезаписывать, -idq — тихо.
        return [tool, "x", "-y", "-p-", "-idq", "-o+", str(path), f"{root}{os.sep}"]
    return [tool, "x", "-y", "-bd", "-p", f"-o{root}", "--", str(path)]


def _collect_extracted(root: Path, budget: _Budget, out: _Unpacked) -> None:
    """Обход распакованного каталога с теми же проверками, что и для ZIP.

    Внешнему архиватору мы верим не больше, чем самому архиву: путь каждого
    файла проверяется по каталогу распаковки, ссылки пропускаются, пределы
    считаются заново — листинг мог соврать.
    """
    try:
        base = root.resolve()
    except OSError:  # pragma: no cover — экзотические ФС
        base = root

    files: List[Path] = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(
            name for name in dirs if not os.path.islink(os.path.join(folder, name))
        )
        for name in sorted(names):
            files.append(Path(folder) / name)

    members: List[Path] = []
    for item in files:
        relative = str(item.relative_to(root)).replace("\\", "/")
        if _is_junk(relative):
            continue
        members.append(item)
    out.total = len(members)

    for item in members:
        relative = str(item.relative_to(root)).replace("\\", "/")
        if item.is_symlink():
            out.warnings.append(f"«{relative}»: символьная ссылка пропущена")
            out.failed.append(f"«{relative}» — символьная ссылка, пропущена")
            continue
        try:
            resolved = item.resolve()
        except OSError:  # pragma: no cover — гонка с удалением
            continue
        if base not in resolved.parents:
            out.unsafe += 1
            out.warnings.append(
                f"«{relative}»: файл оказался за пределами каталога распаковки — отброшен"
            )
            out.failed.append(f"«{relative}» — файл оказался за пределами каталога, отброшен")
            continue
        if not budget.take_member():
            break
        try:
            size = item.stat().st_size
        except OSError:  # pragma: no cover — гонка с удалением
            size = 0
        if not budget.take_space(size):
            out.warnings.append(
                f"«{relative}»: пропущен, исчерпан предел суммарного объёма"
            )
            out.failed.append(f"«{relative}» — не распакован: исчерпан предел суммарного объёма")
            continue
        out.entries.append(ArchiveEntry(name=relative, path=item, size=size))


def _unpack_external(path: Path, root: Path, kind: str, budget: _Budget) -> _Unpacked:
    """Распаковывает 7z или RAR внешним архиватором."""
    out = _Unpacked()
    tool = _archive_tool(kind)
    if tool is None:
        hint = SEVEN_ZIP_HINT if kind == "7z" else UNRAR_HINT
        out.warnings.append(f"архив {kind.upper()} не распакован: нет архиватора — {hint}")
        return out

    total, encrypted, listed = _list_external(tool, path)
    if encrypted:
        out.warnings.append(
            "архив защищён паролем — зашифрованные файлы пропущены; "
            "распакуйте архив вручную и подайте документы отдельно"
        )
    if listed and total > budget.space:
        out.opened = True
        out.warnings.append(
            f"в архиве заявлено {_human_size(total)} распакованных данных при "
            f"пределе {_human_size(budget.space)} — архив не распакован "
            "(защита от архивной бомбы)"
        )
        budget.space_hit = True
        return out

    code, output = _run(_extract_command(tool, path, root), EXTRACT_TIMEOUT)
    out.opened = True
    if code == -2:
        out.warnings.append(
            f"распаковка прервана: архиватор не уложился в {int(EXTRACT_TIMEOUT)} с"
        )
    elif code == -1:
        out.warnings.append(f"архиватор не запущен: {output.strip() or 'причина неизвестна'}")
        out.opened = False
        return out
    elif code not in (0, 1):
        reason = _first_meaningful_line(output) or f"код возврата {code}"
        out.warnings.append(f"архиватор сообщил об ошибке: {reason}")
        if _looks_encrypted(output) and not encrypted:
            out.warnings.append(
                "похоже, архив защищён паролем — распакуйте его вручную"
            )

    _collect_extracted(root, budget, out)
    return out


def _first_meaningful_line(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("7-Zip", "UNRAR", "Copyright", "p7zip")):
            return stripped[:200]
    return ""


# ------------------------------------------------------- сборка документа ---

def _entry_note(entry: ArchiveEntry, converted: ConvertedDocument) -> str:
    """Строка-справка под заголовком раздела: формат, страницы, размер."""
    source = str(converted.meta.get("source_format") or "").strip()
    if not source:
        source = entry.path.suffix.lstrip(".").lower() or "неизвестен"
    parts = [f"формат: {source}"]
    if converted.page_count:
        parts.append(f"страниц: {converted.page_count}")
    if entry.size:
        parts.append(f"размер: {_human_size(entry.size)}")
    return "*Файл из архива, " + "; ".join(parts) + ".*"


def _convert_entry(
    entry: ArchiveEntry,
    limits: Limits,
    budget: _Budget,
    depth: int,
) -> Tuple[ConvertedDocument | None, str]:
    """Разбирает один файл архива. ``None`` и причина — если разбирать нечем."""
    suffix = entry.path.suffix.lower()
    if suffix in ARCHIVE_SUFFIXES:
        kind = _ARCHIVE_KINDS.get(suffix)
        if kind is None:
            return None, f"вложенный архив «{suffix}» — такие архивы система не открывает"
        if depth + 1 >= limits.max_depth:
            if limits.max_depth <= 1:
                return None, (
                    "вложенный архив — распаковывается только сам архив, "
                    "вложенные подавайте отдельно"
                )
            return None, (
                f"вложенный архив — достигнут предел вложенности ({limits.max_depth})"
            )
        spec = registry.find(suffix)
        if spec is None:
            return None, f"вложенный архив «{suffix}» — конвертер не зарегистрирован"
        if not spec.is_available():
            return None, f"вложенный архив: {registry.missing_hint(spec)}"
        return _convert_archive(entry.path, kind, limits, budget, depth + 1), ""

    spec = registry.find(suffix)
    if spec is None:
        return None, f"нет конвертера для формата «{suffix or 'без расширения'}»"
    if not spec.is_available():
        return None, registry.missing_hint(spec)
    return convert_file(entry.path), ""


def _fill_document(
    result: ConvertedDocument,
    unpacked: _Unpacked,
    limits: Limits,
    budget: _Budget,
    depth: int,
) -> None:
    """Собирает Markdown архива: раздел на файл плюс состав архива."""
    sections: List[str] = []
    unparsed: List[str] = []
    parsed = 0
    ocr_needed = 0
    hidden_warnings = 0

    for entry in unpacked.entries:
        converted, reason = _convert_entry(entry, limits, budget, depth)
        if converted is None:
            unparsed.append(f"«{entry.name}» — {reason}")
            continue
        for warning in converted.warnings:
            if len(result.warnings) < MAX_MEMBER_WARNINGS:
                result.warnings.append(f"«{entry.name}»: {warning}")
            else:
                hidden_warnings += 1
        if converted.needs_ocr:
            ocr_needed += 1
        if converted.is_empty:
            unparsed.append(f"«{entry.name}» — текст не извлечён")
            continue
        parsed += 1
        sections.append(f"## {entry.name}")
        sections.append(page_marker(parsed))
        sections.append(_entry_note(entry, converted))
        sections.append(shift_headings(strip_page_markers(converted.text), 2))

    if hidden_warnings:
        result.warnings.append(
            f"ещё {hidden_warnings} предупреждений по файлам архива не показаны"
        )

    intro = _intro_section(unpacked, unparsed + unpacked.failed, parsed, budget, limits)
    body = intro + sections
    # Заголовок первого уровня — имя самого архива. Без него нарезчик корпуса
    # (`corpus.split_document`) считает первый же раздел «##» вершиной дерева,
    # и в крошках под цитатой все файлы оказываются вложенными в первый из них.
    # Своего чанка этот заголовок не создаёт: сразу за ним идёт раздел файла.
    result.text = "\n\n".join([f"# {result.title}"] + body) if body else ""
    result.page_count = parsed
    result.meta["members"] = parsed
    result.meta["entries"] = unpacked.total
    if unparsed:
        result.meta["unparsed"] = len(unparsed)
    if unpacked.encrypted:
        result.meta["encrypted"] = unpacked.encrypted
    if budget.unpacked:
        result.meta["unpacked_bytes"] = budget.unpacked

    _fill_warnings(result, unpacked, limits, budget, parsed, ocr_needed)


def _intro_section(
    unpacked: _Unpacked,
    unparsed: Sequence[str],
    parsed: int,
    budget: _Budget,
    limits: Limits,
) -> List[str]:
    """Раздел «Состав архива» — только если есть о чём сказать.

    Молча потерянный файл хуже отсутствующего: инженер должен видеть, что в
    архиве были ещё три чертежа, даже если их содержимое в индекс не попало.
    """
    notable = bool(unparsed) or unpacked.encrypted or unpacked.unsafe
    notable = notable or budget.members_hit or budget.space_hit
    if not notable:
        return []
    lines = [f"Разобрано файлов: {parsed} из {unpacked.total}."]
    if unpacked.encrypted:
        lines.append(f"Зашифровано паролем и пропущено: {unpacked.encrypted}.")
    if unpacked.unsafe:
        lines.append(f"Отброшено по небезопасному имени: {unpacked.unsafe}.")
    if budget.members_hit:
        lines.append(f"Сработал предел числа файлов: {limits.max_members}.")
    if budget.space_hit:
        lines.append(
            f"Сработал предел распакованного объёма: {_human_size(limits.max_total_bytes)}."
        )
    blocks = ["## Состав архива", "\n".join(lines)]
    if unparsed:
        blocks.append("Не разобраны:\n" + "\n".join(f"- {item}" for item in unparsed))
    return blocks


def _fill_warnings(
    result: ConvertedDocument,
    unpacked: _Unpacked,
    limits: Limits,
    budget: _Budget,
    parsed: int,
    ocr_needed: int,
) -> None:
    """Итоговые предупреждения по архиву целиком."""
    if unpacked.encrypted:
        result.warnings.append(
            f"файлов, зашифрованных паролем: {unpacked.encrypted} — они пропущены; "
            "распакуйте архив вручную и подайте документы отдельно"
        )
    if budget.members_hit:
        result.warnings.append(
            f"в архиве больше {limits.max_members} файлов — разобраны первые; "
            "такие подшивки лучше раскладывать каталогом библиотеки, а не архивом"
        )
    if budget.space_hit:
        result.warnings.append(
            f"суммарный распакованный объём упёрся в предел "
            f"{_human_size(limits.max_total_bytes)} — часть файлов пропущена "
            "(защита от архивной бомбы)"
        )
    if not unpacked.total:
        result.warnings.append("архив пуст — разбирать нечего")
        return
    if not unpacked.entries:
        result.warnings.append("из архива не удалось распаковать ни одного файла")
        return
    if not parsed:
        result.warnings.append(
            "ни один файл архива не разобран: подходящих конвертеров нет — "
            "в текст попал только состав архива"
        )
    if ocr_needed:
        result.warnings.append(
            f"файлов, похожих на скан: {ocr_needed} — им нужен слой OCR"
        )
    result.needs_ocr = bool(ocr_needed) and not parsed


def _convert_archive(
    path: Path,
    kind: str,
    limits: Limits,
    budget: _Budget,
    depth: int,
) -> ConvertedDocument:
    """Общий разбор архива: распаковка во временный каталог и сборка документа.

    Временный каталог удаляется всегда — и после ошибки распаковки, и после
    ошибки разбора вложенного файла.
    """
    result = ConvertedDocument(title=path.stem, meta={"source_format": kind})
    try:
        root = Path(tempfile.mkdtemp(prefix="reportgen-archive-"))
    except OSError as error:
        result.warnings.append(f"не создан временный каталог для распаковки: {_reason(error)}")
        return result
    try:
        if kind == "zip":
            unpacked = _unpack_zip(path, root, budget)
        else:
            unpacked = _unpack_external(path, root, kind, budget)
        result.warnings.extend(unpacked.warnings)
        if not unpacked.opened:
            return result
        _fill_document(result, unpacked, limits, budget, depth)
    except Exception as error:  # noqa: BLE001 — архив не должен ронять приём каталога
        result.warnings.append(f"архив разобран не полностью: {_reason(error)}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return result


def _budget(limits: Limits) -> _Budget:
    return _Budget(members=limits.max_members, space=limits.max_total_bytes)


# ---------------------------------------------------------- конвертеры ----

def convert_zip(path: Path, *, limits: Limits | None = None) -> ConvertedDocument:
    """ZIP → Markdown: раздел на каждый вложенный файл.

    Требует только стандартную библиотеку. ``limits`` нужны тестам и будущим
    настройкам приёма; по умолчанию действуют :data:`MAX_MEMBERS`,
    :data:`MAX_TOTAL_BYTES` и :data:`MAX_DEPTH`.
    """
    limits = limits or Limits()
    return _convert_archive(Path(path), "zip", limits, _budget(limits), 0)


def convert_seven_zip(path: Path, *, limits: Limits | None = None) -> ConvertedDocument:
    """7z → Markdown через внешний архиватор 7-Zip."""
    limits = limits or Limits()
    return _convert_archive(Path(path), "7z", limits, _budget(limits), 0)


def convert_rar(path: Path, *, limits: Limits | None = None) -> ConvertedDocument:
    """RAR → Markdown через unrar (или 7-Zip с поддержкой RAR)."""
    limits = limits or Limits()
    return _convert_archive(Path(path), "rar", limits, _budget(limits), 0)


# --------------------------------------------------------- регистрация ----

registry.register(registry.ConverterSpec(
    name="zip",
    suffixes=(".zip",),
    convert=convert_zip,
    requires=(),
    note="ZIP: раздел на каждый вложенный файл, защита от обхода каталога и бомбы",
))

# Для 7z и RAR своя запись на каждый вариант вызова архиватора: реестр решает,
# доступен ли формат, по имени программы, а один Requirement перечислить
# альтернативы не умеет. Приоритет задаёт порядок перебора.
registry.register(registry.ConverterSpec(
    name="7z",
    suffixes=(".7z",),
    convert=convert_seven_zip,
    requires=(registry.Requirement("binary", "7z", SEVEN_ZIP_HINT,
                                 locate=lambda: archive_binary("7z")),),
    priority=0,
    note="7z через 7-Zip (7z)",
))

registry.register(registry.ConverterSpec(
    name="7z-7za",
    suffixes=(".7z",),
    convert=convert_seven_zip,
    requires=(registry.Requirement("binary", "7za", SEVEN_ZIP_HINT,
                                 locate=lambda: archive_binary("7za")),),
    priority=-1,
    note="7z через 7za (p7zip без плагинов)",
))

registry.register(registry.ConverterSpec(
    name="7z-7zz",
    suffixes=(".7z",),
    convert=convert_seven_zip,
    requires=(registry.Requirement("binary", "7zz", SEVEN_ZIP_HINT,
                                 locate=lambda: archive_binary("7zz")),),
    priority=-2,
    note="7z через 7zz (официальная сборка 7-Zip для Linux)",
))

registry.register(registry.ConverterSpec(
    name="rar",
    suffixes=(".rar",),
    convert=convert_rar,
    requires=(registry.Requirement("binary", "unrar", UNRAR_HINT,
                                 locate=lambda: archive_binary("unrar")),),
    priority=0,
    note="RAR через unrar",
))

registry.register(registry.ConverterSpec(
    name="rar-7z",
    suffixes=(".rar",),
    convert=convert_rar,
    requires=(registry.Requirement("binary", "7z", SEVEN_ZIP_HINT,
                                 locate=lambda: archive_binary("7z")),),
    priority=-1,
    note="RAR через 7-Zip (нужен модуль p7zip-rar)",
))
