"""Прогон захвата через обработку — как проигрыватель: старт, пауза, стоп, сначала, скорость.

Прогон читает захват по записи (chtec.py) и каждую запись пропускает через ту же
обработку, что и анализатор пакетов: разбор по уровням (``razbor.разобрать_пакет``
с правилами «разбирать как» и «Декодировать как» человека), дерево протоколов
(как «Protocol Hierarchy»: пакеты и байты на каждом узле), статистика (порты,
диалоги IP, ошибки разбора) и выходные данные — нагрузка выбранного порта UDP
потоком в файл (со срезом N байт или заголовка RTP и упорядочиванием по номеру
RTP в окне). Всё растёт по ходу и видно странице: состояние прогона пишется в
``ход.json`` дважды в секунду.

Скорость — как записано (по отметкам времени пакетов, ×1), ×N или «максимально»
(0 — без ожидания). Источник — файлы захвата (pcap/pcapng из «Пакетов») или папка
захвата с сети: тогда прогон идёт следом за захватом по его кускам и ждёт новых,
пока захват идёт («обработка на лету»); кусок, который кольцо удалило раньше,
чем до него дошёл прогон, пропускается и отмечается.

Прогон работает отдельным процессом (``python -m reportgen.setevoy.zahvat_seti.progon
<папка>``): разбор пакета на Python — десятки и сотни микросекунд, и в процессе
сервера он отнимал бы время у приёма захвата и у страниц других людей (одна
блокировка интерпретатора на всех). Команды — строками JSON в stdin процесса;
закрытый stdin (сервер выключился или упал) — стоп. Если процесс не запустить,
прогон идёт потоком в самом сервере — с тем же результатом. Память прогона не
растёт с длиной захвата: пакеты не копятся, у дерева и таблиц — потолки узлов.
"""

from __future__ import annotations

import contextlib
import heapq
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...fayly import записать_атомарно
from . import chtec, zapis
from .obrabotka import MAX_DROPOUT, КРУГ, ПОЛКРУГА, заголовок_rtp

#: Потолки таблиц прогона: сверх них — узел «прочие».
УЗЛОВ_ДО = 3000
ПОРТОВ_ДО = 64
ДИАЛОГОВ_ДО = 256
#: Окно упорядочивания RTP: сколько датаграмм одного SSRC держать, ожидая опоздавших.
ОКНО_RTP = 64
#: Как часто писать ход (секунд) и сколько законченных прогонов хранить на человека.
ХОД_КАЖДЫЕ = 0.5
ХРАНИТЬ = 10
ОДНОВРЕМЕННО = 4
НА_ЧЕЛОВЕКА = 2
ЗАПАС_ДИСКА = 256 << 20
СКОРОСТЬ_ДО = 1000.0
ИД = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
ИДУТ = ("идёт", "пауза", "ждёт данных", "запуск")


def новый_ид() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


# -- дерево протоколов и статистика -------------------------------------------------------------

class Дерево:
    """Иерархия протоколов по ходу прогона: у узла — пакеты и байты (исходная длина кадра)."""

    def __init__(self, узлов_до: int = УЗЛОВ_ДО):
        self.корень: list[Any] = [0, 0, {}]
        self.узлов = 1
        self.узлов_до = узлов_до

    def учесть(self, стек: list[str], длина: int) -> None:
        узел = self.корень
        узел[0] += 1
        узел[1] += длина
        for протокол in стек:
            дети = узел[2]
            следующий = дети.get(протокол)
            if следующий is None:
                if self.узлов >= self.узлов_до:
                    протокол = "…прочие"
                    следующий = дети.get(протокол)
                if следующий is None:
                    следующий = дети[протокол] = [0, 0, {}]
                    self.узлов += 1
            следующий[0] += 1
            следующий[1] += длина
            узел = следующий

    def в_список(self) -> list[dict[str, Any]]:
        """Тот же вид, что у statistika.иерархия: протокол, пакетов, байт, уровень, кончаются, дети."""
        from ..statistika import уровень_протокола  # noqa: PLC0415

        def узел(имя: str, у: list[Any], предки: tuple[str, ...], корень: bool) -> dict[str, Any]:
            дети = sorted((узел(к, д, предки + (() if корень else (имя,)), False) for к, д in у[2].items()),
                          key=lambda д: -д["пакетов"])
            return {"протокол": имя, "пакетов": у[0], "байт": у[1],
                    "уровень": "все" if корень else уровень_протокола(имя, предки),
                    "кончаются": у[0] - sum(д["пакетов"] for д in дети), "дети": дети}

        return [узел("Кадры", self.корень, (), True)]


def _учесть(таблица: dict[str, list[int]], ключ: str, байт: int, предел: int) -> None:
    if ключ not in таблица and len(таблица) >= предел:
        ключ = "прочие"
    з = таблица.setdefault(ключ, [0, 0])
    з[0] += 1
    з[1] += байт


# -- выходные данные: нагрузка порта UDP ----------------------------------------------------------

class Упорядочение:
    """Номера RTP в окне: датаграммы одного SSRC выходят по возрастанию расширенного номера
    (16-битный номер с числом кругов, RFC 3550 §A.1), опоздавшие больше чем на окно — отбрасываются
    и считаются; пропуски, повторы и разрывы нумерации (скачок больше MAX_DROPOUT) — тоже."""

    def __init__(self, окно: int = ОКНО_RTP):
        self.окно = окно
        self.наибольший: dict[int, int] = {}
        self.последний: dict[int, int] = {}
        self.очередь: dict[int, list[tuple[int, int, bytes]]] = {}
        self.счёт = 0
        self.сводка = {"переставлено_rtp": 0, "пропущено_rtp": 0, "повторы_rtp": 0, "разрывы_rtp": 0,
                       "опоздали_rtp": 0}

    def расширить(self, ssrc: int, номер: int) -> int:
        наибольший = self.наибольший.get(ssrc)
        if наибольший is None:
            self.наибольший[ssrc] = номер
            return номер
        разница = (номер - наибольший) % КРУГ
        if разница < ПОЛКРУГА:
            self.наибольший[ssrc] = наибольший + разница
            return наибольший + разница
        self.сводка["переставлено_rtp"] += 1
        return наибольший - (КРУГ - разница)

    def добавить(self, ssrc: int, номер: int, данные: bytes) -> list[bytes]:
        р = self.расширить(ssrc, номер)
        очередь = self.очередь.setdefault(ssrc, [])
        self.счёт += 1
        heapq.heappush(очередь, (р, self.счёт, данные))
        итог = []
        while len(очередь) > self.окно:
            итог += self._выдать(ssrc, heapq.heappop(очередь))
        return итог

    def _выдать(self, ssrc: int, элемент: tuple[int, int, bytes]) -> list[bytes]:
        р, _, данные = элемент
        прежний = self.последний.get(ssrc)
        if прежний is not None:
            if р <= прежний:
                self.сводка["повторы_rtp" if р == прежний else "опоздали_rtp"] += 1
                return []
            скачок = р - прежний - 1
            if скачок > MAX_DROPOUT:
                self.сводка["разрывы_rtp"] += 1
            else:
                self.сводка["пропущено_rtp"] += скачок
        self.последний[ssrc] = р
        return [данные]

    def дожать(self) -> list[bytes]:
        итог = []
        for ssrc, очередь in self.очередь.items():
            while очередь:
                итог += self._выдать(ssrc, heapq.heappop(очередь))
        return итог


class Выход:
    """Нагрузка порта UDP — потоком в файл: срез N байт или заголовка RTP, окно порядка RTP."""

    def __init__(self, путь: Path, *, порт: int, срез: int | str = 0, упорядочить: bool = False,
                 источник: str = ""):
        self.путь = путь
        self.файл = open(путь, "wb")  # noqa: SIM115 — закрывает закрыть()
        self.порт, self.срез, self.источник = порт, срез, источник
        self.порядок = Упорядочение() if упорядочить else None
        self.с: dict[str, Any] = {"порт": порт, "срез": срез, "упорядочить": упорядочить, "источник": источник,
                                  "датаграмм": 0, "взято": 0, "байт": 0, "не_rtp": 0, "короче_среза": 0,
                                  "фрагменты": 0}

    def учесть(self, кадр: bytes) -> None:
        св = zapis.разобрать_кадр(кадр)
        if св.протокол != zapis.IP_UDP or св.порт_к != self.порт or (self.источник and св.от != self.источник):
            return
        с = self.с
        с["датаграмм"] += 1
        if св.нагрузка is None:
            с["фрагменты"] += 1
            return
        данные = св.нагрузка
        ssrc = номер = 0
        if self.срез == "rtp" or self.порядок is not None:
            rtp = заголовок_rtp(данные)
            if rtp is None:
                с["не_rtp"] += 1
                return
            номер, ssrc, длина, дополнение = rtp
            if self.срез == "rtp":
                данные = данные[длина:len(данные) - дополнение]
        if self.срез != "rtp" and self.срез:
            if len(данные) < int(self.срез):
                с["короче_среза"] += 1
            данные = данные[int(self.срез):]
        куски = [данные] if self.порядок is None else self.порядок.добавить(ssrc, номер, данные)
        self._писать(куски)

    def _писать(self, куски: list[bytes]) -> None:
        for к in куски:
            self.файл.write(к)
            self.с["взято"] += 1
            self.с["байт"] += len(к)

    def закрыть(self) -> None:
        if self.порядок is not None:
            self._писать(self.порядок.дожать())
        self.файл.close()

    def сводка(self) -> dict[str, Any]:
        итог = dict(self.с)
        if self.порядок is not None:
            итог.update(self.порядок.сводка)
            итог["ssrc"] = [f"{s:08x}" for s in self.порядок.последний]
        return итог


# -- источник записей: файлы или куски идущего захвата ----------------------------------------------

class Кадры:
    """Записи по порядку из файлов захвата; у захвата с сети — по его кускам, следом за записью."""

    def __init__(self, источник: dict[str, Any]):
        self.вид = источник.get("вид")
        self.файлы = [Path(п) for п in источник.get("файлы") or []]
        self.папка = Path(источник["папка"]) if источник.get("папка") else None
        self.чтец: chtec.ЧтецФайла | None = None
        self.номер = -1                         # номер текущего файла (куска)
        self.заметки: list[str] = []
        self._идёт_проверено = 0.0
        self._идёт = False

    def захват_идёт(self) -> bool:
        if self.папка is None:
            return False
        сейчас = time.monotonic()
        if сейчас - self._идёт_проверено > 0.3:
            self._идёт_проверено = сейчас
            try:
                self._идёт = json.loads((self.папка / "состояние.json").read_text(encoding="utf-8")) \
                    .get("состояние") == "идёт"
            except (OSError, ValueError):
                self._идёт = False
        return self._идёт

    def _куски(self) -> list[int]:
        assert self.папка is not None
        return sorted(int(п.stem) for п in (self.папка / zapis.КУСКИ).glob("*.pcapng") if п.stem.isdigit())

    def _следующий_файл(self) -> Path | None:
        if self.папка is None:
            self.номер += 1
            return self.файлы[self.номер] if self.номер < len(self.файлы) else None
        дальше = [н for н in self._куски() if н > self.номер]
        if not дальше:
            return None
        if дальше[0] > self.номер + 1:
            пропущено = дальше[0] - self.номер - 1
            self.заметки.append(f"куски {self.номер + 1}…{дальше[0] - 1} удалены кольцом раньше, чем до них дошёл "
                                f"прогон, — пропущено кусков: {пропущено}")
        self.номер = дальше[0]
        return self.папка / zapis.КУСКИ / zapis.имя_куска(self.номер)

    def есть_новее(self) -> bool:
        return self.папка is not None and any(н > self.номер for н in self._куски())

    def следующий(self) -> tuple[float, bytes, int, str] | None | bool:
        """Запись; None — пока нечего (ждать); False — всё прочитано."""
        while True:
            if self.чтец is None:
                путь = self._следующий_файл()
                if путь is None:
                    return None if self.захват_идёт() else False
                try:
                    self.чтец = chtec.ЧтецФайла(путь)
                except FileNotFoundError:
                    self.заметки.append(f"файл {путь.name} удалён раньше, чем до него дошёл прогон")
                    continue
                except OSError as ошибка:
                    self.заметки.append(f"файл {путь.name} не открыть: {ошибка.strerror or ошибка}")
                    continue
            запись = self.чтец.следующий()
            if запись is not None:
                return запись
            if self.чтец.испорчен:
                self.заметки.append(f"{self.чтец.путь.name}: {self.чтец.испорчен} — дальше этот файл не читается")
            elif self.папка is not None and not self.есть_новее() and self.захват_идёт():
                return None                     # идущий кусок: ждём, пока допишут
            elif self.папка is not None and not self.есть_новее():
                # Захват кончился: дочитать последний кусок (запись могла прийти между проверками).
                запись = self.чтец.следующий()
                if запись is not None:
                    return запись
            self.чтец.закрыть()
            self.чтец = None

    def закрыть(self) -> None:
        if self.чтец is not None:
            self.чтец.закрыть()
            self.чтец = None


# -- сам прогон ---------------------------------------------------------------------------------------

class Прогон:
    """Один прогон: читает, выдерживает темп, разбирает, считает; команды — из очереди."""

    def __init__(self, папка: Path, задание: dict[str, Any], команды: queue.Queue):
        self.папка = Path(папка)
        self.задание = задание
        self.команды = команды
        self.скорость = float(задание.get("скорость") or 0)
        self.как = dict(задание.get("как") or {})
        if задание.get("правила"):
            self.как["правила"] = list(задание["правила"])
        self.шаблоны: dict[str, Any] = {}
        self.дерево = Дерево()
        self.порты: dict[str, list[int]] = {}
        self.диалоги: dict[str, list[int]] = {}
        self.ошибок = 0
        self.правил_сработало: dict[str, int] = {}
        self.пакетов = 0
        self.байт = 0
        self.первое: float | None = None
        self.текущее: float | None = None
        self.состояние = "запуск"
        self.причина = ""
        self.ошибка = ""
        self.пауза = False
        self.стоп = False
        self.начато = time.time()
        self.темп: list[tuple[float, int]] = []
        self.выход: Выход | None = None
        выход = задание.get("выход") or None
        if выход:
            self.выход = Выход(self.папка / "выход.bin", порт=int(выход["порт"]), срез=выход.get("срез") or 0,
                               упорядочить=bool(выход.get("упорядочить")), источник=выход.get("источник") or "")
        self.кадры = Кадры(задание["источник"])
        self._записано = 0.0
        self._основа: tuple[float, float] | None = None       # (стена, время пакета) — для темпа

    # -- команды и темп --

    def _команды(self, ждать: float = 0.0) -> None:
        конец = time.monotonic() + ждать
        while True:
            остаток = конец - time.monotonic()
            try:
                к = self.команды.get(timeout=остаток) if остаток > 0 else self.команды.get_nowait()
            except queue.Empty:
                return
            вид = к.get("к")
            if вид == "стоп":
                self.стоп = True
                self.причина = к.get("почему") or "остановлен"
                return
            if вид == "пауза":
                self.пауза = True
            elif вид == "продолжить":
                self.пауза = False
                self._основа = None
            elif вид == "скорость":
                self.скорость = max(0.0, min(СКОРОСТЬ_ДО, float(к.get("з") or 0)))
                self._основа = None

    def _выдержать(self, время: float) -> None:
        """Ждать до срока пакета по его отметке времени (скорость 0 — не ждать)."""
        while not self.стоп:
            if self.пауза:
                self._ход("пауза")
                self._команды(0.25)
                continue
            if self.скорость <= 0:
                self._команды()
                return
            сейчас = time.monotonic()
            if self._основа is None:
                self._основа = (сейчас, время)
            стена, начало = self._основа
            ждать = стена + (время - начало) / self.скорость - сейчас
            if ждать <= 0:
                self._команды()
                return
            self._ход("идёт")
            self._команды(min(ждать, 0.25))

    # -- обработка --

    def _пакет(self, время: float, данные: bytes, длина: int, канал: str) -> None:
        from ..razbor import разобрать_пакет  # noqa: PLC0415
        self.пакетов += 1
        self.байт += длина
        if self.первое is None:
            self.первое = время
        self.текущее = время
        п = разобрать_пакет(данные, канал, номер=self.пакетов, время=время, исходная_длина=длина,
                            как=self.как or None, шаблоны=self.шаблоны)
        self.дерево.учесть(п.стек, длина)
        if п.ошибки:
            self.ошибок += 1
        for ид in getattr(п, "правила", ()):
            self.правил_сработало[ид] = self.правил_сработало.get(ид, 0) + 1
        стек = п.стек
        if п.порт_к is not None and ("UDP" in стек or "TCP" in стек):
            _учесть(self.порты, f"{'UDP' if 'UDP' in стек else 'TCP'} {п.порт_к}", длина, ПОРТОВ_ДО)
        if п.источник and п.получатель and ("IPv4" in стек or "IPv6" in стек):
            а, б = sorted((п.источник, п.получатель))
            _учесть(self.диалоги, f"{а} ↔ {б}", длина, ДИАЛОГОВ_ДО)
        if self.выход is not None:
            кадр = данные if канал == "Ethernet" else (zapis.кадр_из_ip(данные) if канал in ("IP", "IPv4", "IPv6")
                                                        else None)
            if кадр is not None:
                self.выход.учесть(кадр)

    def работать(self) -> None:
        try:
            # Разборщики грузятся заранее (сотни модулей протоколов): пауза и счётчики сразу живые.
            from .. import razbor  # noqa: F401, PLC0415
            self._ход("идёт", сразу=True)
            while not self.стоп:
                self._команды()
                if self.пауза:
                    self._выдержать(self.текущее or 0.0)
                    continue
                запись = self.кадры.следующий()
                if запись is False:
                    self.причина = self.причина or "захват пройден до конца"
                    break
                if запись is None:
                    self._ход("ждёт данных")
                    self._команды(0.2)
                    continue
                время, данные, длина, канал = запись
                self._выдержать(время)
                if self.стоп:
                    break
                self._пакет(время, данные, длина, канал)
                if time.monotonic() - self._записано >= ХОД_КАЖДЫЕ:
                    self._ход("идёт")
                    if self.выход is not None and shutil.disk_usage(self.папка).free < ЗАПАС_ДИСКА:
                        self.причина = "мало места на диске сервера — прогон остановлен, выходные данные целы"
                        break
            итог = "остановлен" if self.стоп else "готово"
        except Exception as ошибка:          # noqa: BLE001 — прогон не роняет сервер
            self.ошибка = f"{type(ошибка).__name__}: {ошибка}"
            итог = "ошибка"
        finally:
            self.кадры.закрыть()
            if self.выход is not None:
                with contextlib.suppress(OSError):
                    self.выход.закрыть()
        self._ход(итог, сразу=True)

    def _ход(self, состояние: str, *, сразу: bool = False) -> None:
        self.состояние = состояние
        сейчас = time.monotonic()
        if not сразу and сейчас - self._записано < ХОД_КАЖДЫЕ:
            return
        self._записано = сейчас
        self.темп.append((сейчас, self.пакетов))
        self.темп = [т for т in self.темп if сейчас - т[0] <= 3.0]
        темп = 0.0
        if len(self.темп) > 1 and self.темп[-1][0] > self.темп[0][0]:
            темп = (self.темп[-1][1] - self.темп[0][1]) / (self.темп[-1][0] - self.темп[0][0])
        ход = {
            "состояние": состояние, "причина": self.причина, "ошибка": self.ошибка, "скорость": self.скорость,
            "пакетов": self.пакетов, "байт": self.байт, "ошибок": self.ошибок,
            "первое": self.первое, "текущее": self.текущее,
            "прошло_записи": (self.текущее - self.первое) if self.первое is not None and self.текущее is not None else 0,
            "темп": round(темп, 1), "файл": self.кадры.номер, "заметки": self.кадры.заметки[-20:],
            "дерево": self.дерево.в_список(), "узлов": self.дерево.узлов,
            "порты": sorted(([к, *з] for к, з in self.порты.items()), key=lambda р: -р[1])[:ПОРТОВ_ДО],
            "диалоги": sorted(([к, *з] for к, з in self.диалоги.items()), key=lambda р: -р[2])[:32],
            "правила": self.правил_сработало, "выход": self.выход.сводка() if self.выход is not None else None,
            "обновлено": time.time(), "начато": self.начато,
        }
        with contextlib.suppress(OSError):
            записать_атомарно(self.папка / "ход.json", json.dumps(ход, ensure_ascii=False))


# -- отдельный процесс ---------------------------------------------------------------------------------

def _читать_команды(команды: queue.Queue) -> None:
    """Команды из stdin строками JSON; конец stdin (сервер выключился) — стоп."""
    for строка in sys.stdin:
        with contextlib.suppress(ValueError):
            к = json.loads(строка)
            if isinstance(к, dict):
                команды.put(к)
    команды.put({"к": "стоп", "почему": "сервер закрыл связь с прогоном"})


def main(аргументы: list[str]) -> int:
    папка = Path(аргументы[1])
    задание = json.loads((папка / "задание.json").read_text(encoding="utf-8"))
    команды: queue.Queue = queue.Queue()
    threading.Thread(target=_читать_команды, args=(команды,), daemon=True).start()
    Прогон(папка, задание, команды).работать()
    return 0


# -- прогоны на сервере -----------------------------------------------------------------------------

class _Ход:
    """Идущий прогон на сервере: процесс (или поток) и способ дать ему команду."""

    def __init__(self, процесс: subprocess.Popen | None = None, поток: threading.Thread | None = None,
                 команды: queue.Queue | None = None):
        self.процесс, self.поток, self.команды = процесс, поток, команды

    def жив(self) -> bool:
        if self.процесс is not None:
            return self.процесс.poll() is None
        return self.поток is not None and self.поток.is_alive()

    def послать(self, команда: dict[str, Any]) -> None:
        if self.процесс is not None:
            with contextlib.suppress(OSError, ValueError, AttributeError):
                self.процесс.stdin.write((json.dumps(команда, ensure_ascii=False) + "\n").encode("utf-8"))
                self.процесс.stdin.flush()
        elif self.команды is not None:
            self.команды.put(команда)

    def ждать(self, секунд: float) -> None:
        if self.процесс is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.процесс.wait(секунд)
        elif self.поток is not None:
            self.поток.join(секунд)

    def закрыть(self) -> None:
        if self.процесс is not None:
            with contextlib.suppress(OSError, ValueError, AttributeError):
                self.процесс.stdin.close()
            if self.процесс.poll() is None:
                with contextlib.suppress(OSError):
                    self.процесс.wait(3)
            if self.процесс.poll() is None:
                with contextlib.suppress(OSError):
                    self.процесс.kill()


class Прогоны:
    """Прогоны всех людей: запуск, команды, состояние, выходные данные. Папка —
    ``<data_dir>/progon/<ид>/``: ``прогон.json`` (чей, откуда), ``задание.json``,
    ``ход.json`` (пишет прогон), ``выход.bin``."""

    def __init__(self, папка: Path, *, процессом: bool = True,
                 фабрика_процесса: Callable[..., subprocess.Popen] | None = None):
        self.папка = Path(папка)
        self.процессом = процессом
        self.фабрика = фабрика_процесса or subprocess.Popen
        self._lock = threading.Lock()
        self._идут: dict[str, _Ход] = {}
        self._прервать_прежние()

    # -- запуск --

    def начать(self, *, владелец: int, кто: str, источник: dict[str, Any], задание: dict[str, Any],
               имя: str = "") -> str:
        with self._lock:
            живые = {ид: х for ид, х in self._идут.items() if х.жив()}
            if len(живые) >= ОДНОВРЕМЕННО:
                raise ValueError(f"на сервере уже идут {ОДНОВРЕМЕННО} прогона — дождитесь конца одного")
            свои = [ид for ид in живые if self._мета(ид).get("владелец") == владелец]
            if len(свои) >= НА_ЧЕЛОВЕКА:
                raise ValueError(f"у вас уже идут {НА_ЧЕЛОВЕКА} прогона — остановите один")
            ид = новый_ид()
            папка = self.папка / ид
            папка.mkdir(parents=True, exist_ok=True)
            мета = {"ид": ид, "владелец": владелец, "кто": кто, "источник": источник, "имя": имя,
                    "создан": time.time(), "выход": задание.get("выход"), "скорость": задание.get("скорость") or 0,
                    "правил": len(задание.get("правила") or [])}
            записать_атомарно(папка / "прогон.json", json.dumps(мета, ensure_ascii=False))
            записать_атомарно(папка / "задание.json", json.dumps(задание, ensure_ascii=False))
            self._идут[ид] = self._запустить(папка, задание)
        self._прибрать(владелец)
        return ид

    def _запустить(self, папка: Path, задание: dict[str, Any]) -> _Ход:
        if self.процессом:
            import reportgen  # noqa: PLC0415
            корень = str(Path(reportgen.__file__).resolve().parents[1])
            среда = dict(os.environ)
            среда["PYTHONPATH"] = корень + (os.pathsep + среда["PYTHONPATH"] if среда.get("PYTHONPATH") else "")
            try:
                журнал = open(папка / "журнал.txt", "ab")  # noqa: SIM115 — наследует процесс
                try:
                    процесс = self.фабрика([sys.executable, "-m", "reportgen.setevoy.zahvat_seti.progon", str(папка)],
                                           stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=журнал,
                                           env=среда, cwd=str(папка))
                finally:
                    журнал.close()
                return _Ход(процесс=процесс)
            except OSError as ошибка:
                with contextlib.suppress(OSError):
                    (папка / "журнал.txt").write_text(f"процесс не запустился ({ошибка}) — прогон идёт потоком",
                                                      encoding="utf-8")
        команды: queue.Queue = queue.Queue()
        поток = threading.Thread(target=lambda: Прогон(папка, задание, команды).работать(), daemon=True,
                                 name=f"reportgen-progon-{папка.name}")
        поток.start()
        return _Ход(поток=поток, команды=команды)

    # -- команды --

    def команда(self, ид: str, команда: str, значение: Any = None) -> None:
        if команда not in ("пауза", "продолжить", "стоп", "скорость"):
            raise ValueError("команда: пауза, продолжить, стоп, скорость или сначала")
        к: dict[str, Any] = {"к": команда}
        if команда == "скорость":
            к["з"] = проверить_скорость(значение)
        with self._lock:
            х = self._идут.get(ид)
        if х is None or not х.жив():
            if команда == "стоп":
                return
            raise ValueError("прогон не идёт — начните его заново («Сначала»)")
        х.послать(к)
        if команда == "скорость":
            self._дописать_мету(ид, скорость=к["з"])

    def остановить(self, ид: str, ждать: float = 5.0) -> None:
        with self._lock:
            х = self._идут.get(ид)
        if х is None:
            return
        х.послать({"к": "стоп", "почему": "остановлен"})
        х.ждать(ждать)
        х.закрыть()
        with self._lock:
            if self._идут.get(ид) is х:
                del self._идут[ид]

    def сначала(self, ид: str, задание: dict[str, Any] | None = None) -> None:
        """Остановить и начать тот же прогон с начала (с новым заданием — например, с новыми правилами)."""
        мета = self._мета(ид)
        if not мета:
            raise KeyError(ид)
        self.остановить(ид)
        папка = self.папка / ид
        if задание is None:
            задание = json.loads((папка / "задание.json").read_text(encoding="utf-8"))
        for имя in ("ход.json", "выход.bin"):
            with contextlib.suppress(OSError):
                (папка / имя).unlink()
        записать_атомарно(папка / "задание.json", json.dumps(задание, ensure_ascii=False))
        self._дописать_мету(ид, скорость=задание.get("скорость") or 0, выход=задание.get("выход"),
                            правил=len(задание.get("правила") or []), перезапущен=time.time())
        with self._lock:
            self._идут[ид] = self._запустить(папка, задание)

    def остановить_все(self) -> None:
        with self._lock:
            иды = list(self._идут)
        for ид in иды:
            self.остановить(ид, ждать=3.0)

    # -- чтение --

    def _мета(self, ид: str) -> dict[str, Any]:
        try:
            return json.loads((self.папка / ид / "прогон.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _дописать_мету(self, ид: str, **поля: Any) -> None:
        мета = self._мета(ид)
        if мета:
            мета.update(поля)
            with contextlib.suppress(OSError):
                записать_атомарно(self.папка / ид / "прогон.json", json.dumps(мета, ensure_ascii=False))

    def состояние(self, ид: str) -> dict[str, Any]:
        """Мета и ход прогона; KeyError — нет такого."""
        if not ИД.fullmatch(ид or ""):
            raise KeyError(ид)
        мета = self._мета(ид)
        if not мета:
            raise KeyError(ид)
        try:
            ход = json.loads((self.папка / ид / "ход.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ход = {"состояние": "запуск", "пакетов": 0, "байт": 0, "дерево": [], "заметки": []}
        with self._lock:
            х = self._идут.get(ид)
        жив = х is not None and х.жив()
        if ход.get("состояние") in ИДУТ and not жив:
            журнал = ""
            with contextlib.suppress(OSError):
                журнал = (self.папка / ид / "журнал.txt").read_text(encoding="utf-8", errors="replace")[-600:]
            ход.update(состояние="ошибка", ошибка="прогон прервался" + (f": {журнал.strip()}" if журнал.strip() else ""))
        return {**мета, **ход, "жив": жив}

    def найти(self, владелец: int, источник: dict[str, Any]) -> str | None:
        """Последний прогон этого источника у человека."""
        for папка in sorted(self.папка.glob("*"), reverse=True) if self.папка.is_dir() else []:
            if not ИД.fullmatch(папка.name):
                continue
            мета = self._мета(папка.name)
            if мета.get("владелец") == владелец and мета.get("источник") == источник:
                return папка.name
        return None

    def выход(self, ид: str) -> Path:
        путь = self.папка / ид / "выход.bin"
        if not ИД.fullmatch(ид or "") or not путь.is_file():
            raise KeyError(ид)
        return путь

    def удалить(self, ид: str) -> None:
        if not ИД.fullmatch(ид or ""):
            raise KeyError(ид)
        self.остановить(ид)
        shutil.rmtree(self.папка / ид, ignore_errors=True)

    # -- служебное --

    def _прервать_прежние(self) -> None:
        for путь in self.папка.glob("*/ход.json") if self.папка.is_dir() else []:
            with contextlib.suppress(OSError, ValueError):
                ход = json.loads(путь.read_text(encoding="utf-8"))
                if ход.get("состояние") in ИДУТ:
                    ход.update(состояние="остановлен", причина="сервер перезапускался во время прогона")
                    записать_атомарно(путь, json.dumps(ход, ensure_ascii=False))

    def _прибрать(self, владелец: int) -> None:
        свои = []
        for папка in sorted(self.папка.glob("*"), reverse=True) if self.папка.is_dir() else []:
            if ИД.fullmatch(папка.name) and self._мета(папка.name).get("владелец") == владелец:
                свои.append(папка.name)
        with self._lock:
            живые = {ид for ид, х in self._идут.items() if х.жив()}
        for ид in [и for и in свои if и not in живые][ХРАНИТЬ:]:
            shutil.rmtree(self.папка / ид, ignore_errors=True)


def проверить_скорость(значение: Any) -> float:
    """Скорость прогона: 0 — максимально, 1 — как записано, иначе ×N (0,01…1000)."""
    if isinstance(значение, bool):
        raise ValueError("скорость: число")
    try:
        с = float(str(значение).replace(",", ".").strip() or 0)
    except ValueError:
        raise ValueError("скорость: 0 (максимально), 1 (как записано) или множитель") from None
    if с != с or с < 0 or (0 < с < 0.01) or с > СКОРОСТЬ_ДО:
        raise ValueError(f"скорость: 0 (максимально) или от 0,01 до {СКОРОСТЬ_ДО:g}")
    return с


def проверить_выход(данные: Any) -> dict[str, Any] | None:
    """Выходные данные прогона: {порт, срез (число байт или "rtp"), упорядочить, источник} или None."""
    if not данные:
        return None
    if not isinstance(данные, dict):
        raise ValueError("выходные данные — объект: порт, срез, упорядочить, источник")
    from .parametry import адрес_ip, целое  # noqa: PLC0415
    порт = целое(данные.get("порт"), "выход: порт UDP", 1, 65535)
    срез_ = данные.get("срез") or 0
    срез: int | str = "rtp" if str(срез_).strip().lower() == "rtp" else целое(срез_, "выход: срез, байт", 0, 65535)
    return {"порт": порт, "срез": срез, "упорядочить": bool(данные.get("упорядочить")),
            "источник": адрес_ip(данные.get("источник"), "выход: отправитель")}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
