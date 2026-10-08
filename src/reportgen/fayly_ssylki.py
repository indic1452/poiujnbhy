"""Файлы на сервере по ссылке: разрешённые папки, обзор с поиском, безопасные пути.

Большой файл (гигабайты) не обязательно везти через браузер: администратор задаёт
папки входных файлов на сервере (``input_dirs`` в настройках: свои диски, сетевая
папка Windows ``\\\\сервер\\share``), человек выбирает файл в обзоре, и разбор читает его
на месте — без копии и без загрузки. По умолчанию папка одна: ``<data_dir>/vhod``.

Запись настройки — строка (путь) или объект ``{"имя": …, "путь": …, "роль": …}``:
``роль`` — наименьшая должность, которой папка видна (по умолчанию — всем, кроме гостя).

Безопасность: путь внутри папки задаётся относительным; абсолютные пути, «..», имена
дисков и NUL отвергаются, а итог после разрешения ссылок (``resolve``) обязан лежать
внутри папки — символьная ссылка наружу не открывается. Папка, которой человеку не
положено видеть, для него не существует (то же «не найдена», что и несуществующая).

Модуль общий: им пользуются «Анализ пакетов» и разбор потоков (маршруты ``/api/files/…``).
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

#: Сколько находок отдавать поиском и как глубоко/долго искать.
НАХОДОК_ДО = 500
ГЛУБИНА_ПОИСКА = 12
ВРЕМЯ_ПОИСКА = 3.0
ЭЛЕМЕНТОВ_ДО = 5000


class ОшибкаПути(ValueError):
    """Путь недопустим или его нет — словами для человека."""


class ФайлИзменён(ОшибкаПути):
    """Файл, открытый по ссылке, после открытия изменили, удалили или он недоступен."""


@dataclass(frozen=True)
class Папка:
    ид: str
    имя: str
    путь: Path
    роль: str = ""

    def в_словарь(self) -> dict[str, Any]:
        return {"id": self.ид, "имя": self.имя}


def _ид(путь: Path) -> str:
    return hashlib.sha1(str(путь).encode("utf-8")).hexdigest()[:10]


def папки(settings) -> list[Папка]:
    """Все папки входных файлов из настроек (``input_dirs``); пусто — ``<data_dir>/vhod`` (создаётся)."""
    записи = list(getattr(settings, "input_dirs", None) or [])
    итог: list[Папка] = []
    if not записи:
        путь = Path(settings.data_dir) / "vhod"
        try:
            путь.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return [Папка(_ид(путь), "Входные файлы сервера", путь)]
    for запись in записи:
        if isinstance(запись, dict):
            сырой = str(запись.get("путь") or запись.get("path") or "").strip()
            имя = str(запись.get("имя") or запись.get("name") or "").strip()
            роль = str(запись.get("роль") or запись.get("role") or "").strip().lower()
        else:
            сырой, имя, роль = str(запись).strip(), "", ""
        if not сырой:
            continue
        путь = Path(os.path.expandvars(os.path.expanduser(сырой)))
        итог.append(Папка(_ид(путь), имя or путь.name or str(путь), путь, роль))
    return итог


def доступные(settings, user) -> list[Папка]:
    """Папки, которые этому человеку видны (по должности)."""
    from .store.models import ROLE_RANK  # noqa: PLC0415
    ранг = getattr(user, "rank", 0)
    итог = []
    for п in папки(settings):
        нужно = ROLE_RANK.get(п.роль, 0) if п.роль else 0
        if п.роль and п.роль not in ROLE_RANK:
            continue                                  # неизвестная должность в настройке — папку не показываем
        if getattr(user, "role", "") == "guest" or ранг < нужно:
            continue
        итог.append(п)
    return итог


def найти_папку(settings, user, ид: str) -> Папка:
    for п in доступные(settings, user):
        if п.ид == ид:
            return п
    raise ОшибкаПути("папка не найдена")


def разрешить(папка: Папка, относительный: str) -> Path:
    """Путь внутри папки по относительному пути (через «/» или «\\»); вне папки — ОшибкаПути."""
    сырой = str(относительный or "").replace("\\", "/").strip()
    if "\x00" in сырой:
        raise ОшибкаПути("недопустимый путь")
    if сырой.startswith("/") or PureWindowsPath(сырой).drive or PureWindowsPath(сырой).root:
        raise ОшибкаПути("путь должен быть относительным — внутри выбранной папки")
    части = [ч for ч in PurePosixPath(сырой).parts if ч not in ("", ".")]
    if any(ч == ".." for ч in части):
        raise ОшибкаПути("недопустимый путь: «..»")
    корень = папка.путь.resolve()
    итог = корень.joinpath(*части).resolve()
    if итог != корень and корень not in итог.parents:
        raise ОшибкаПути("путь ведёт за пределы папки (ссылка наружу)")
    return итог


def _сведения(путь: Path, корень: Path) -> dict[str, Any] | None:
    try:
        ст = путь.stat()
    except OSError:
        return None
    папка = путь.is_dir()
    return {"имя": путь.name, "путь": путь.relative_to(корень).as_posix(), "папка": папка,
            "размер": 0 if папка else ст.st_size, "изменён": ст.st_mtime}


def список(папка: Папка, путь: str = "", поиск: str = "") -> dict[str, Any]:
    """Содержимое подпапки (папки сначала, по имени) или поиск по имени вглубь (``поиск`` — часть имени)."""
    корень = папка.путь.resolve()
    if not корень.is_dir():
        raise ОшибкаПути(f"папка «{папка.имя}» недоступна на сервере: {папка.путь}")
    где = разрешить(папка, путь)
    if not где.is_dir():
        raise ОшибкаПути("такой папки нет")
    элементы: list[dict[str, Any]] = []
    обрезано = False
    if поиск.strip():
        образец = поиск.strip().casefold()
        конец = time.monotonic() + ВРЕМЯ_ПОИСКА
        стек = [(где, 0)]
        while стек:
            текущая, глубина = стек.pop()
            try:
                входы = sorted(os.scandir(текущая), key=lambda в: в.name.casefold())
            except OSError:
                continue
            for в in входы:
                путь_в = Path(в.path)
                try:
                    if в.is_symlink() and корень not in путь_в.resolve().parents:
                        continue                      # ссылка наружу — не показываем и не обходим
                    папка_ли = в.is_dir()
                except OSError:
                    continue
                if образец in в.name.casefold():
                    с = _сведения(путь_в, корень)
                    if с is not None:
                        элементы.append(с)
                        if len(элементы) >= НАХОДОК_ДО:
                            return {"путь": где.relative_to(корень).as_posix(), "элементы": элементы, "обрезано": True}
                if папка_ли and глубина < ГЛУБИНА_ПОИСКА:
                    стек.append((путь_в, глубина + 1))
            if time.monotonic() > конец:
                обрезано = True
                break
    else:
        try:
            входы = list(os.scandir(где))
        except OSError as ошибка:
            raise ОшибкаПути(f"папку не прочитать: {ошибка.strerror or ошибка}") from None
        for в in входы:
            путь_в = Path(в.path)
            try:
                if в.is_symlink() and корень not in путь_в.resolve().parents:
                    continue
            except OSError:
                continue
            с = _сведения(путь_в, корень)
            if с is not None:
                элементы.append(с)
        элементы.sort(key=lambda э: (not э["папка"], э["имя"].casefold()))
        if len(элементы) > ЭЛЕМЕНТОВ_ДО:
            элементы, обрезано = элементы[:ЭЛЕМЕНТОВ_ДО], True
    return {"путь": где.relative_to(корень).as_posix() if где != корень else "", "элементы": элементы,
            "обрезано": обрезано}


def файл(папка: Папка, путь: str) -> Path:
    """Существующий обычный файл внутри папки — для разбора на месте."""
    итог = разрешить(папка, путь)
    if not итог.is_file():
        raise ОшибкаПути("такого файла нет")
    return итог


def отпечаток(путь: Path) -> dict[str, int]:
    """Размер и время изменения: по ним видно, что файл после открытия поменяли или удалили."""
    ст = Path(путь).stat()
    return {"размер": ст.st_size, "изменён_нс": ст.st_mtime_ns}


def проверить_отпечаток(путь: Path, прежний: dict[str, Any]) -> str:
    """Пусто — файл тот же; иначе — что случилось, словами."""
    try:
        теперь = отпечаток(путь)
    except FileNotFoundError:
        return f"файл {путь} удалён на сервере после открытия"
    except OSError as ошибка:
        return f"файл {путь} недоступен: {ошибка.strerror or ошибка}"
    if теперь["размер"] != прежний.get("размер") or теперь["изменён_нс"] != прежний.get("изменён_нс"):
        return f"файл {путь} изменён на сервере после открытия — откройте его заново"
    return ""
