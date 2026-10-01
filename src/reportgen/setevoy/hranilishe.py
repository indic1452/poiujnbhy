"""Хранилище разобранных пакетов на диске: сколько угодно пакетов при постоянной памяти.

Папка захвата (её пишет фоновый разбор, читают страница и отборы):

* ``индекс.bin`` — по записи на пакет (``ЗАПИСЬ``, 48 байт): где байты пакета (в
  исходном файле или в ``пакеты.bin``), канал, где его сводка и плоские поля;
* ``сводки.z`` и ``поля.z`` — сводки для списка и поля для фильтров: блоками по
  ``СТРОК_В_БЛОКЕ`` пакетов (список словарей pickle, сжатый zlib) — без сжатия они в
  десяток раз больше самих пакетов, а pickle пишется и читается в разы быстрее JSON.
  Файлы пишет и читает только сам сервер (чужие данные в них не подкладываются);
* ``время.f8``, ``длина.u4``, ``поток.u8`` — столбцы на пакет: время, исходная длина,
  отпечаток потока TCP/UDP/SCTP (64 бита; 0 — не поток) — для графика времени и
  «следовать за потоком» без чтения сводок;
* ``каналы.json`` — имена каналов (в записи — номер).

Запись индекса пишется последней — после блока, столбцов и байт пакета: читатель видит
пакет целиком или не видит вовсе, даже пока разбор идёт. Блоки сбрасываются не реже
раза в ``СБРОС_КАЖДЫЕ`` секунд, поэтому страница отстаёт от разбора на доли секунды.
"""

from __future__ import annotations

import contextlib
import json
import os
import pickle
import struct
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np

#: Запись индекса: место и длина байт пакета, номер канала, признаки, блок сводки (место, длина),
#: блок полей (место, длина), строка в блоке, запас.
ЗАПИСЬ = struct.Struct("<QIHHQIQIHHI")
ЗАПИСЬ_ТИП = np.dtype([("место", "<u8"), ("длина", "<u4"), ("канал", "<u2"), ("признаки", "<u2"),
                       ("сводки", "<u8"), ("сводки_длина", "<u4"), ("поля", "<u8"), ("поля_длина", "<u4"),
                       ("в_блоке", "<u2"), ("запас", "<u2"), ("запас2", "<u4")])
assert ЗАПИСЬ.size == ЗАПИСЬ_ТИП.itemsize == 48
#: Признаки записи.
СОБРАННЫЙ = 1            # у пакета есть собранный из фрагментов (собранные/N.bin)
В_КОПИИ = 2              # байты пакета — в пакеты.bin (иначе — в исходнике)

СТРОК_В_БЛОКЕ = 256
СБРОС_КАЖДЫЕ = 0.5
СЖАТИЕ = 1

ИНДЕКС, СВОДКИ, ПОЛЯ, ПАКЕТЫ, ИСХОДНИК = "индекс.bin", "сводки.z", "поля.z", "пакеты.bin", "исходник"
ВРЕМЯ, ДЛИНА, ПОТОК, КАНАЛЫ = "время.f8", "длина.u4", "поток.u8", "каналы.json"
ФАЙЛЫ = (ИНДЕКС, СВОДКИ, ПОЛЯ, ПАКЕТЫ, ВРЕМЯ, ДЛИНА, ПОТОК, КАНАЛЫ)


def отпечаток_потока(ключ: tuple | None) -> int:
    """64-битный отпечаток ключа потока (statistika.ключ_потока); 0 — у пакета нет потока.

    Две CRC-32 (по ключу и по ключу задом наперёд) — быстро и одинаково в любом процессе;
    совпадение отпечатков у разных потоков не страшно: «следовать за потоком» сверяет ключи
    у найденных пакетов заново."""
    if ключ is None:
        return 0
    сырые = repr(ключ).encode("utf-8")
    return (zlib.crc32(сырые) << 32 | zlib.crc32(сырые[::-1])) or 1


def прочитать_json(путь: Path, по_умолчанию: Any = None) -> Any:
    """JSON-файл, который другой процесс подменяет атомарно: в Windows подмена на миг мешает
    открыть — повторяем; нет файла или он битый — ``по_умолчанию``."""
    for пауза in (0.01, 0.03, 0.1, None):
        try:
            return json.loads(Path(путь).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return по_умолчанию
        except PermissionError:
            if пауза is None:
                return по_умолчанию
            time.sleep(пауза)
        except (OSError, ValueError):
            return по_умолчанию
    return по_умолчанию


class Писатель:
    """Дописывает пакеты в хранилище; ``сбросить()`` делает накопленное видимым читателям."""

    def __init__(self, папка: Path, *, копировать: bool):
        self.папка = Path(папка)
        self.копировать = копировать
        self._ф = {имя: open(self.папка / имя, "wb") for имя in (ИНДЕКС, СВОДКИ, ПОЛЯ, ВРЕМЯ, ДЛИНА, ПОТОК)}  # noqa: SIM115
        self._пакеты = open(self.папка / ПАКЕТЫ, "wb") if копировать else None  # noqa: SIM115
        self._место_пакетов = 0
        self._места = {СВОДКИ: 0, ПОЛЯ: 0}
        self.каналы: list[str] = []
        self._каналов_записано = -1
        self._ждут: list[tuple] = []          # (место, длина, канал, признаки, время, длина_исх, поток)
        self._сводки: list[dict[str, Any]] = []
        self._поля: list[dict[str, Any]] = []
        self.записано = 0                     # видно читателям
        self._сброшено = time.monotonic()
        with contextlib.suppress(OSError):
            (self.папка / КАНАЛЫ).unlink()

    def номер_канала(self, канал: str) -> int:
        try:
            return self.каналы.index(канал)
        except ValueError:
            self.каналы.append(канал)
            return len(self.каналы) - 1

    def добавить(self, *, данные: bytes, место: int | None, канал: str, сводка: dict[str, Any],
                 поля: dict[str, Any], время: float, длина: int, поток: int, признаки: int = 0) -> None:
        """Пакет: байты берутся из исходника по ``место`` или копируются (``место`` None, копирование)."""
        if место is None or self.копировать:
            if self._пакеты is None:
                self._пакеты = open(self.папка / ПАКЕТЫ, "wb")  # noqa: SIM115
            место = self._место_пакетов
            self._пакеты.write(данные)
            self._место_пакетов += len(данные)
            признаки |= В_КОПИИ
        self._ждут.append((место, len(данные), self.номер_канала(канал), признаки, время, длина, поток))
        self._сводки.append(сводка)
        self._поля.append(поля)
        if len(self._ждут) >= СТРОК_В_БЛОКЕ:
            self._блок()
        if time.monotonic() - self._сброшено >= СБРОС_КАЖДЫЕ:
            self.сбросить()

    def отметить_собранный(self) -> None:
        """У последнего добавленного пакета есть собранный (файл уже записан)."""
        место, длина, канал, признаки, *прочее = self._ждут[-1]
        self._ждут[-1] = (место, длина, канал, признаки | СОБРАННЫЙ, *прочее)

    def _блок(self) -> None:
        if not self._ждут:
            return
        места = {}
        for имя, строки in ((СВОДКИ, self._сводки), (ПОЛЯ, self._поля)):
            сжато = zlib.compress(pickle.dumps(строки, pickle.HIGHEST_PROTOCOL), СЖАТИЕ)
            места[имя] = (self._места[имя], len(сжато))
            self._ф[имя].write(сжато)
            self._места[имя] += len(сжато)
        for имя in (СВОДКИ, ПОЛЯ):
            self._ф[имя].flush()
        if self._пакеты is not None:
            self._пакеты.flush()
        if len(self.каналы) != self._каналов_записано:
            from ..fayly import записать_атомарно  # noqa: PLC0415
            записать_атомарно(self.папка / КАНАЛЫ, json.dumps(self.каналы, ensure_ascii=False))
            self._каналов_записано = len(self.каналы)
        ждут = self._ждут
        self._ф[ВРЕМЯ].write(np.array([з[4] for з in ждут], dtype="<f8").tobytes())
        self._ф[ДЛИНА].write(np.array([з[5] for з in ждут], dtype="<u4").tobytes())
        self._ф[ПОТОК].write(np.array([з[6] for з in ждут], dtype="<u8").tobytes())
        for имя in (ВРЕМЯ, ДЛИНА, ПОТОК):
            self._ф[имя].flush()
        с_место, с_длина = места[СВОДКИ]
        п_место, п_длина = места[ПОЛЯ]
        self._ф[ИНДЕКС].write(b"".join(ЗАПИСЬ.pack(з[0], з[1], з[2], з[3], с_место, с_длина, п_место, п_длина, i, 0, 0)
                                       for i, з in enumerate(ждут)))
        self._ф[ИНДЕКС].flush()
        self.записано += len(ждут)
        self._ждут, self._сводки, self._поля = [], [], []

    def сбросить(self) -> None:
        self._блок()
        self._сброшено = time.monotonic()

    def закрыть(self) -> None:
        self.сбросить()
        for ф in self._ф.values():
            ф.close()
        if self._пакеты is not None:
            self._пакеты.close()


class Хранилище:
    """Чтение хранилища захвата; безопасно, пока разбор ещё пишет (видно ``число()`` пакетов)."""

    #: Сколько распакованных блоков держать (на захват).
    БЛОКОВ_В_ПАМЯТИ = 48

    def __init__(self, папка: Path):
        self.папка = Path(папка)
        self._блоки: OrderedDict[tuple[str, int], list[dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()
        self._каналы: list[str] = []

    def число(self) -> int:
        try:
            return (self.папка / ИНДЕКС).stat().st_size // ЗАПИСЬ.size
        except OSError:
            return 0

    def есть(self) -> bool:
        return (self.папка / ИНДЕКС).exists()

    # -- записи индекса --

    def записи(self, от: int, до: int) -> np.ndarray:
        """Записи индекса [от, до) (с 0)."""
        до = min(до, self.число())
        if до <= от:
            return np.zeros(0, dtype=ЗАПИСЬ_ТИП)
        with open(self.папка / ИНДЕКС, "rb") as ф:
            ф.seek(от * ЗАПИСЬ.size)
            сырые = ф.read((до - от) * ЗАПИСЬ.size)
        return np.frombuffer(сырые[:len(сырые) // ЗАПИСЬ.size * ЗАПИСЬ.size], dtype=ЗАПИСЬ_ТИП)

    def записи_по(self, номера: Sequence[int] | np.ndarray) -> np.ndarray:
        """Записи индекса для возрастающих номеров (с 0): подряд идущие читаются одним куском."""
        номера = np.asarray(номера, dtype=np.int64)
        if not len(номера):
            return np.zeros(0, dtype=ЗАПИСЬ_ТИП)
        от, до = int(номера.min()), int(номера.max()) + 1
        if до - от <= max(4 * len(номера), 1 << 16):
            return self.записи(от, до)[номера - от]
        итог = np.zeros(len(номера), dtype=ЗАПИСЬ_ТИП)
        with open(self.папка / ИНДЕКС, "rb") as ф:
            for j, н in enumerate(номера.tolist()):
                ф.seek(н * ЗАПИСЬ.size)
                итог[j] = np.frombuffer(ф.read(ЗАПИСЬ.size), dtype=ЗАПИСЬ_ТИП)[0]
        return итог

    def каналы(self) -> list[str]:
        if not self._каналы or len(self._каналы) < 64:
            self._каналы = прочитать_json(self.папка / КАНАЛЫ, []) or self._каналы
        return self._каналы

    # -- блоки сводок и полей --

    def _блок(self, вид: str, место: int, длина: int, ф=None) -> list[dict[str, Any]]:
        ключ = (вид, место)
        with self._lock:
            строки = self._блоки.get(ключ)
            if строки is not None:
                self._блоки.move_to_end(ключ)
                return строки
        if ф is None:
            with open(self.папка / вид, "rb") as ф_:
                ф_.seek(место)
                сжато = ф_.read(длина)
        else:
            ф.seek(место)
            сжато = ф.read(длина)
        строки = pickle.loads(zlib.decompress(сжато))  # noqa: S301 — файл пишет сам сервер
        with self._lock:
            self._блоки[ключ] = строки
            while len(self._блоки) > self.БЛОКОВ_В_ПАМЯТИ:
                self._блоки.popitem(last=False)
        return строки

    def _строки(self, вид: str, записи: np.ndarray) -> list[dict[str, Any]]:
        """Словари пакетов (копии верхнего уровня: блок в кэше остаётся нетронутым)."""
        if not len(записи):
            return []
        места = записи["сводки" if вид == СВОДКИ else "поля"]
        длины = записи["сводки_длина" if вид == СВОДКИ else "поля_длина"]
        в_блоке = записи["в_блоке"]
        итог = []
        with open(self.папка / вид, "rb") as ф:
            прежнее, строки = -1, []
            for место, длина, i in zip(места.tolist(), длины.tolist(), в_блоке.tolist(), strict=True):
                if место != прежнее:
                    строки = self._блок(вид, место, длина, ф)
                    прежнее = место
                итог.append(dict(строки[i]))
        return итог

    def сводки(self, от: int, до: int) -> list[dict[str, Any]]:
        return self._строки(СВОДКИ, self.записи(от, до))

    def поля(self, от: int, до: int) -> list[dict[str, Any]]:
        return self._строки(ПОЛЯ, self.записи(от, до))

    def сводки_по(self, номера) -> list[dict[str, Any]]:
        return self._строки(СВОДКИ, self.записи_по(номера))

    def поля_по(self, номера) -> list[dict[str, Any]]:
        return self._строки(ПОЛЯ, self.записи_по(номера))

    def сводка(self, номер: int) -> dict[str, Any]:
        """Сводка пакета (номер с 0)."""
        if not 0 <= номер < self.число():
            raise IndexError(номер)
        return self.сводки(номер, номер + 1)[0]

    # -- байты пакетов --

    def _кадры(self, записи: np.ndarray) -> list[bytes]:
        итог: list[bytes] = []
        файлы: dict[bool, Any] = {}
        try:
            for место, длина, признаки in zip(записи["место"].tolist(), записи["длина"].tolist(),
                                              записи["признаки"].tolist(), strict=True):
                копия = bool(признаки & В_КОПИИ)
                ф = файлы.get(копия)
                if ф is None:
                    ф = файлы[копия] = open(self.папка / (ПАКЕТЫ if копия else ИСХОДНИК), "rb")  # noqa: SIM115
                ф.seek(место)
                итог.append(ф.read(длина))
        finally:
            for ф in файлы.values():
                ф.close()
        return итог

    def кадры(self, от: int, до: int) -> list[bytes]:
        return self._кадры(self.записи(от, до))

    def кадры_по(self, номера) -> list[bytes]:
        return self._кадры(self.записи_по(номера))

    def кадр(self, номер: int) -> tuple[bytes, str]:
        """Байты и канал пакета (номер с 0)."""
        з = self.записи(номер, номер + 1)
        if not len(з):
            raise IndexError(номер)
        каналы = self.каналы()
        к = int(з["канал"][0])
        return self._кадры(з)[0], каналы[к] if к < len(каналы) else "Ethernet"

    def собранный(self, номер: int) -> bytes | None:
        """Байты собранного из фрагментов пакета (номер с 1), если есть."""
        путь = self.папка / "собранные" / f"{номер}.bin"
        try:
            return путь.read_bytes()
        except OSError:
            return None

    # -- столбцы --

    def _столбец(self, имя: str, тип: str, от: int = 0, до: int | None = None) -> np.ndarray:
        n = self.число()
        до = n if до is None else min(до, n)
        if до <= от:
            return np.zeros(0, dtype=тип)
        размер = np.dtype(тип).itemsize
        with open(self.папка / имя, "rb") as ф:
            ф.seek(от * размер)
            сырые = ф.read((до - от) * размер)
        return np.frombuffer(сырые, dtype=тип)

    def времена(self, от: int = 0, до: int | None = None) -> np.ndarray:
        return self._столбец(ВРЕМЯ, "<f8", от, до)

    def длины(self, от: int = 0, до: int | None = None) -> np.ndarray:
        return self._столбец(ДЛИНА, "<u4", от, до)

    def потоки(self, от: int = 0, до: int | None = None) -> np.ndarray:
        return self._столбец(ПОТОК, "<u8", от, до)

    def сбросить_кэш(self) -> None:
        with self._lock:
            self._блоки.clear()
        self._каналы = []


class Ряд(Sequence):
    """Сводки, поля, байты или нагрузки пакетов хранилища как список — читаются с диска по мере
    обращения (по ``ПОРЦИЯ`` подряд), в памяти не копятся. ``номера`` — только эти пакеты (с 0)."""

    ПОРЦИЯ = 2048

    def __init__(self, хранилище: Хранилище, вид: str, номера: Sequence[int] | np.ndarray | None = None,
                 число: int | None = None):
        self.х = хранилище
        self.вид = вид
        self.номера = None if номера is None else np.asarray(номера, dtype=np.int64)
        self._число = хранилище.число() if число is None else число
        self._кэш: tuple[int, list[Any]] | None = None

    def __len__(self) -> int:
        return self._число if self.номера is None else len(self.номера)

    def _порция(self, от: int, до: int) -> list[Any]:
        if self.номера is None:
            сводки_нужны = self.вид in ("нагрузки", "нагрузки_тр")
            if self.вид == "сводки":
                return self.х.сводки(от, до)
            if self.вид == "поля":
                return self.х.поля(от, до)
            if self.вид == "кадры":
                return self.х.кадры(от, до)
            assert сводки_нужны
            return нагрузки(self.х, self.х.сводки(от, до), self.х.кадры(от, до), self.вид)
        номера = self.номера[от:до]
        if self.вид == "сводки":
            return self.х.сводки_по(номера)
        if self.вид == "поля":
            return self.х.поля_по(номера)
        if self.вид == "кадры":
            return self.х.кадры_по(номера)
        return нагрузки(self.х, self.х.сводки_по(номера), self.х.кадры_по(номера), self.вид)

    def __getitem__(self, i):
        if isinstance(i, slice):
            от, до, шаг = i.indices(len(self))
            if шаг != 1:
                return [self[j] for j in range(от, до, шаг)]
            итог: list[Any] = []
            while от < до:
                кусок = min(до, от + self.ПОРЦИЯ)
                итог += self._порция(от, кусок)
                от = кусок
            return итог
        i = int(i)
        if i < 0:
            i += len(self)
        if not 0 <= i < len(self):
            raise IndexError(i)
        if self._кэш is not None and self._кэш[0] <= i < self._кэш[0] + len(self._кэш[1]):
            return self._кэш[1][i - self._кэш[0]]
        начало = i // self.ПОРЦИЯ * self.ПОРЦИЯ
        self._кэш = (начало, self._порция(начало, min(len(self), начало + self.ПОРЦИЯ)))
        return self._кэш[1][i - начало]

    def __iter__(self) -> Iterator[Any]:
        for от in range(0, len(self), self.ПОРЦИЯ):
            yield from self._порция(от, min(len(self), от + self.ПОРЦИЯ))

    def __eq__(self, другое: object) -> bool:
        if isinstance(другое, (list, tuple, Ряд)):
            return len(self) == len(другое) and all(а == б for а, б in zip(self, другое, strict=True))
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]


def нагрузки(х: Хранилище, сводки: list[dict[str, Any]], кадры: list[bytes], вид: str = "нагрузки"
             ) -> list[bytes | None]:
    """Нагрузки пакетов по сводкам: «нагр» — верхнего уровня; ``нагрузки_тр`` — TCP/UDP целиком
    («тр» из кадра, «тр_с» из собранного, иначе как «нагр»)."""
    итог: list[bytes | None] = []
    for с, к in zip(сводки, кадры, strict=True):
        нагр = к[с["нагр"][0]:с["нагр"][0] + с["нагр"][1]] if с.get("нагр") else None
        if вид == "нагрузки_тр":
            if с.get("тр"):
                нагр = к[с["тр"][0]:с["тр"][0] + с["тр"][1]]
            elif с.get("тр_с"):
                собранный = х.собранный(с["номер"]) or b""
                нагр = собранный[с["тр_с"][0]:с["тр_с"][0] + с["тр_с"][1]]
        итог.append(нагр)
    return итог


def убрать(папка: Path) -> None:
    """Файлы хранилища (перед разбором заново)."""
    for имя in ФАЙЛЫ:
        with contextlib.suppress(OSError):
            os.unlink(Path(папка) / имя)
