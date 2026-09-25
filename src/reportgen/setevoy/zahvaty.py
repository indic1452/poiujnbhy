# -*- coding: utf-8 -*-
"""Захваты для страницы «Пакеты»: приём, разбор в фоне, хранение, выборки.

Каждый захват — папка: исходный файл, пакеты подряд (пакеты.bin) с
индексом, сводки для списка и плоские поля для фильтров (по строке JSON на
пакет). Разбор идёт в фоновом потоке, страница видит, сколько разобрано.
Подробный разбор одного пакета (дерево полей) не хранится — он строится
заново по запросу: это доли миллисекунды, а хранить его для сотни тысяч
пакетов — сотни мегабайт.

IPv4, разрезанный на фрагменты, собирается: к последнему фрагменту
добавляется разбор собранного пакета (уровни «собранный …»), а его байты
лежат отдельно — их показывает шестнадцатеричный дамп.
"""

from __future__ import annotations

import json
import secrets
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .chtenie import прочитать_захват
from .filtr import собрать
from .pole import Пакет, сумма16
from .razbor import разобрать_пакет

#: Сколько захватов хранить на пользователя.
ХРАНИТЬ = 30
#: Поля, которые в фильтр не берём: длинные и бесполезные для отбора.
БЕЗ_ФИЛЬТРА = {"data.data", "tls.handshake.random", "radius.authenticator", "tcp.options"}
#: Сколько захватов держать разобранными в памяти (для фильтров и статистики).
В_ПАМЯТИ = 2


def _сводка(п: Пакет, поля: Dict[str, List[Any]]) -> Dict[str, Any]:
    с = п.сводка()
    с["порт_от"], с["порт_к"] = п.порт_от, п.порт_к
    if "eth.src" in поля and "eth.dst" in поля:
        с["mac"] = [поля["eth.src"][0], поля["eth.dst"][0]]
    if "tcp.seq_raw" in поля:
        с["seq"] = поля["tcp.seq_raw"][-1]
    if п.нагрузка is not None:
        с["нагр"] = [п.нагрузка_смещение, len(п.нагрузка)]
    return с


def _для_фильтра(поля: Dict[str, List[Any]]) -> Dict[str, List[Any]]:
    итог = {}
    for к, значения in поля.items():
        if к in БЕЗ_ФИЛЬТРА:
            continue
        итог[к] = [з if isinstance(з, (int, float, bool)) or з is None else str(з)[:200] for з in значения]
    return итог


class Сборщик:
    """Сборка фрагментов IPv4 по (источник, получатель, id, протокол)."""

    def __init__(self):
        self.куски: Dict[Tuple, Dict[str, Any]] = {}

    def добавить(self, п: Пакет, поля: Dict[str, List[Any]]) -> Optional[bytes]:
        if "ipv4" not in поля or not поля.get("ip.flags.mf") or "ip.frag_offset" not in поля:
            return None
        mf, смещение = поля["ip.flags.mf"][0], поля["ip.frag_offset"][0]
        if not mf and not смещение:
            return None
        уровень = next(у for у in п.уровни if у.протокол == "IPv4")
        начало = уровень.смещение
        ihl, всего = уровень.длина, struct.unpack_from(">H", п.данные, начало + 2)[0]
        ключ = (поля["ip.src"][0], поля["ip.dst"][0], поля["ip.id"][0], поля["ip.proto"][0])
        з = self.куски.setdefault(ключ, {"части": {}, "конец": None, "заголовок": None, "номера": []})
        з["части"][смещение] = п.данные[начало + ihl:начало + всего]
        з["номера"].append(п.номер)
        if смещение == 0:
            з["заголовок"] = п.данные[начало:начало + ihl]
        if not mf:
            з["конец"] = смещение + всего - ihl
        if з["заголовок"] is None or з["конец"] is None:
            return None
        собрано, место = b"", 0
        while место < з["конец"]:
            часть = з["части"].get(место)
            if часть is None or not часть:
                return None
            собрано += часть
            место += len(часть)
        del self.куски[ключ]
        заголовок = bytearray(з["заголовок"])
        struct.pack_into(">HH", заголовок, 2, len(заголовок) + len(собрано), 0)
        struct.pack_into(">H", заголовок, 10, 0)
        struct.pack_into(">H", заголовок, 10, сумма16(bytes(заголовок)))
        self.последние_номера = з["номера"]
        return bytes(заголовок) + собрано


class Захваты:
    def __init__(self, папка: Path):
        self.папка = Path(папка)
        self._lock = threading.Lock()
        self._кэш: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    # -- приём ---------------------------------------------------------------------

    def создать(self, *, владелец: int, имя: str, данные: bytes, от: str = "") -> str:
        ид = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        папка = self.папка / ид
        папка.mkdir(parents=True, exist_ok=True)
        (папка / "исходник").write_bytes(данные)
        self._записать(ид, {"ид": ид, "владелец": владелец, "имя": имя, "от": от, "состояние": "ждёт",
                            "создано": time.time(), "байт": len(данные), "пакетов": 0, "разобрано": 0,
                            "формат": "", "заметки": [], "ошибка": ""})
        threading.Thread(target=self._разобрать, args=(ид,), daemon=True, name="reportgen-pakety").start()
        self._прибрать(владелец)
        return ид

    def _разобрать(self, ид: str) -> None:
        папка = self.папка / ид
        try:
            состояние = self.прочитать(ид)
            захват = прочитать_захват(данные=(папка / "исходник").read_bytes())
            состояние.update(состояние="идёт", формат=захват.формат, заметки=захват.заметки,
                             пакетов=len(захват.записи))
            self._записать(ид, состояние)
            сборщик = Сборщик()
            индекс, каналы = [], []
            with open(папка / "пакеты.bin", "wb") as пакеты, \
                    open(папка / "сводки.jsonl", "w", encoding="utf-8") as сводки, \
                    open(папка / "поля.jsonl", "w", encoding="utf-8") as поля_ф:
                место = 0
                for номер, з in enumerate(захват.записи, start=1):
                    п = разобрать_пакет(з.данные, з.канал, номер=номер, время=з.время, исходная_длина=з.длина)
                    поля = п.поля_фильтра()
                    собрано = сборщик.добавить(п, поля)
                    if собрано is not None:
                        self._собранный(папка, п, собрано, сборщик.последние_номера)
                        поля = п.поля_фильтра()
                    if з.канал not in каналы:
                        каналы.append(з.канал)
                    пакеты.write(з.данные)
                    индекс.append([место, len(з.данные), каналы.index(з.канал)])
                    место += len(з.данные)
                    сводки.write(json.dumps(_сводка(п, поля), ensure_ascii=False) + "\n")
                    поля_ф.write(json.dumps(_для_фильтра(поля), ensure_ascii=False) + "\n")
                    if номер % 2000 == 0:
                        состояние["разобрано"] = номер
                        self._записать(ид, состояние)
            (папка / "индекс.json").write_text(json.dumps({"каналы": каналы, "пакеты": индекс}), encoding="utf-8")
            состояние.update(состояние="готово", разобрано=len(захват.записи), закончено=time.time())
        except FileNotFoundError:
            return
        except Exception as ошибка:                     # noqa: BLE001 — поток не роняет приложение
            состояние = self.прочитать(ид)
            состояние.update(состояние="ошибка", ошибка=str(ошибка))
        self._записать(ид, состояние)

    def _собранный(self, папка: Path, п: Пакет, собрано: bytes, номера: List[int]) -> None:
        (папка / "собранные").mkdir(exist_ok=True)
        (папка / "собранные" / f"{п.номер}.bin").write_bytes(собрано)
        собранный = разобрать_пакет(собрано, "IPv4", номер=п.номер, время=п.время)
        первый = собранный.уровни[0]
        первый.протокол = "собранный IPv4"
        первый.полное = f"IPv4, собран из фрагментов {', '.join(map(str, номера))}"
        п.уровни += собранный.уровни
        п.инфо = собранный.инфо + f" [собран из {len(номера)} фрагментов]"
        п.ошибки += собранный.ошибки
        п.порт_от, п.порт_к = собранный.порт_от, собранный.порт_к
        п.нагрузка, п.нагрузка_смещение = None, 0

    # -- чтение --------------------------------------------------------------------

    def прочитать(self, ид: str) -> Dict[str, Any]:
        путь = self.папка / ид / "состояние.json"
        if not путь.exists():
            raise KeyError(ид)
        return json.loads(путь.read_text(encoding="utf-8"))

    def список(self, владелец: int) -> List[Dict[str, Any]]:
        if not self.папка.exists():
            return []
        итог = []
        for папка in sorted(self.папка.iterdir(), reverse=True):
            путь = папка / "состояние.json"
            if путь.exists():
                с = json.loads(путь.read_text(encoding="utf-8"))
                if с.get("владелец") == владелец:
                    итог.append(с)
        return итог

    def _загрузить(self, ид: str) -> Dict[str, Any]:
        with self._lock:
            if ид in self._кэш:
                self._кэш.move_to_end(ид)
                return self._кэш[ид]
        папка = self.папка / ид
        if self.прочитать(ид)["состояние"] != "готово":
            raise ValueError("захват ещё разбирается")
        данные = {
            "сводки": [json.loads(с) for с in (папка / "сводки.jsonl").read_text(encoding="utf-8").splitlines()],
            "поля": [json.loads(с) for с in (папка / "поля.jsonl").read_text(encoding="utf-8").splitlines()],
            "индекс": json.loads((папка / "индекс.json").read_text(encoding="utf-8")),
            "фильтры": OrderedDict(),
        }
        with self._lock:
            self._кэш[ид] = данные
            while len(self._кэш) > В_ПАМЯТИ:
                self._кэш.popitem(last=False)
        return данные

    def отобрать(self, ид: str, фильтр: str) -> List[int]:
        """Номера (с 0) пакетов под фильтр; последние фильтры запоминаются."""
        д = self._загрузить(ид)
        фильтр = (фильтр or "").strip()
        if фильтр in д["фильтры"]:
            return д["фильтры"][фильтр]
        условие = собрать(фильтр)
        итог = [i for i, п in enumerate(д["поля"]) if условие(п)]
        д["фильтры"][фильтр] = итог
        while len(д["фильтры"]) > 8:
            д["фильтры"].popitem(last=False)
        return итог

    def сводки(self, ид: str) -> List[Dict[str, Any]]:
        return self._загрузить(ид)["сводки"]

    def поля(self, ид: str) -> List[Dict[str, List[Any]]]:
        return self._загрузить(ид)["поля"]

    def байты(self, ид: str, номер: int) -> Tuple[bytes, str]:
        д = self._загрузить(ид)
        место, длина, канал = д["индекс"]["пакеты"][номер - 1]
        with open(self.папка / ид / "пакеты.bin", "rb") as файл:
            файл.seek(место)
            return файл.read(длина), д["индекс"]["каналы"][канал]

    def пакет(self, ид: str, номер: int) -> Dict[str, Any]:
        """Подробный разбор одного пакета — заново, с деревом полей."""
        данные, канал = self.байты(ид, номер)
        с = self.сводки(ид)[номер - 1]
        п = разобрать_пакет(данные, канал, номер=номер, время=с["время"], исходная_длина=с["длина"])
        итог = п.в_словарь()
        собранный = self.папка / ид / "собранные" / f"{номер}.bin"
        if собранный.exists():
            байты = собранный.read_bytes()
            с_п = разобрать_пакет(байты, "IPv4", номер=номер, время=с["время"])
            итог["собранный"] = с_п.в_словарь()
        return итог

    def нагрузки(self, ид: str) -> List[Optional[bytes]]:
        д = self._загрузить(ид)
        итог = []
        with open(self.папка / ид / "пакеты.bin", "rb") as файл:
            весь = файл.read()
        for с, (место, длина, _) in zip(д["сводки"], д["индекс"]["пакеты"]):
            н = с.get("нагр")
            итог.append(весь[место + н[0]:место + н[0] + н[1]] if н else None)
        return итог

    def выгрузить_pcap(self, ид: str, номера: List[int]) -> bytes:
        """Отобранные пакеты — в pcap (канал — первого пакета; смешанные каналы — Ethernet)."""
        д = self._загрузить(ид)
        каналы = {д["индекс"]["каналы"][д["индекс"]["пакеты"][i][2]] for i in номера} or {"Ethernet"}
        from .chtenie import КАНАЛЫ  # noqa: PLC0415
        обратно = {имя: номер for номер, имя in КАНАЛЫ.items() if номер not in (12, 14)}
        тип = обратно.get(next(iter(каналы)), 1) if len(каналы) == 1 else 1
        итог = bytearray(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, тип))
        with open(self.папка / ид / "пакеты.bin", "rb") as файл:
            весь = файл.read()
        for i in номера:
            место, длина, _ = д["индекс"]["пакеты"][i]
            с = д["сводки"][i]
            сек = int(с["время"])
            итог += struct.pack("<IIII", сек, int(round((с["время"] - сек) * 1e6)) % 1000000, длина, с["длина"])
            итог += весь[место:место + длина]
        return bytes(итог)

    # -- хранение ------------------------------------------------------------------

    def удалить(self, ид: str) -> None:
        папка = self.папка / ид
        with self._lock:
            self._кэш.pop(ид, None)
        for путь in sorted(папка.rglob("*"), reverse=True):
            путь.unlink() if путь.is_file() else путь.rmdir()
        папка.rmdir()

    def _записать(self, ид: str, состояние: Dict[str, Any]) -> None:
        путь = self.папка / ид / "состояние.json"
        временный = путь.with_suffix(".tmp")
        временный.write_text(json.dumps(состояние, ensure_ascii=False), encoding="utf-8")
        временный.replace(путь)

    def _прибрать(self, владелец: int) -> None:
        свои = [з for з in self.список(владелец) if з["состояние"] in ("готово", "ошибка")]
        for лишнее in свои[ХРАНИТЬ:]:
            self.удалить(лишнее["ид"])
