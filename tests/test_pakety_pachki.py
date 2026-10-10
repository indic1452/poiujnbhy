"""Разбор захвата пачками (setevoy.pachki): итог ровно как у разбора подряд — на смеси протоколов, где сборка
фрагментов, сегментов, MP, объекты, потоки RTP по SDP и шаблоны NetFlow тянутся через стыки пачек; повреждённый
хвост, пауза и продолжение, стоп, перезапуск посреди разбора, загрузка кусками, процессы, большой файл."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

import pakety_smes as смесь
from reportgen.fayly_ssylki import отпечаток
from reportgen.potok import ispolniteli
from reportgen.setevoy import fon, obekty, pachki, vygruzka
from reportgen.setevoy.hranilishe import Ряд, Хранилище

#: Маленькие пачки: стыков много, и через них идут фрагменты, сегменты, объекты, потоки по SDP.
РАЗМЕР = {"пакетов": 173, "байт": 1 << 30, "первые": [41, 97]}


def мета(папка: Path, файл: Path, **ещё) -> None:
    папка.mkdir(parents=True, exist_ok=True)
    м = {"ид": папка.name, "владелец": 1, "имя": файл.name, "поколение": 1,
         "источник": {"вид": "ссылка", "путь": str(файл), **отпечаток(файл)}, **ещё}
    (папка / "захват.json").write_text(json.dumps(м, ensure_ascii=False), encoding="utf-8")


def подряд(папка: Path, файл: Path) -> dict:
    мета(папка, файл)
    fon.Разбор(папка, queue.Queue()).работать()
    return json.loads((папка / "ход.json").read_text(encoding="utf-8"))


def собранные_из(файл: Path) -> dict[int, tuple[str, list[int]]]:
    """Собранные пакеты разбора подряд: номер → (что собрано, номера частей)."""
    from reportgen.setevoy import sborka  # noqa: PLC0415
    from reportgen.setevoy.chtenie import ЧтецЗахвата  # noqa: PLC0415
    from reportgen.setevoy.razbor import разобрать_пакет  # noqa: PLC0415
    итог: dict[int, tuple[str, list[int]]] = {}
    исходная = sborka.собранный

    def ловушка(куда, п, собрано, номера, как=None, **ещё):
        итог[п.номер] = (ещё.get("имя", "IPv4"), list(номера))
        return исходная(куда, п, собрано, номера, как, **ещё)
    sborka.собранный = ловушка
    try:
        with tempfile.TemporaryDirectory() as т:
            сб = sborka.Сборка(Path(т), {})
            шаблоны: dict = {}
            with ЧтецЗахвата(файл) as чтец:
                n = 0
                while запись := чтец.следующий():
                    n += 1
                    сб.пакет(разобрать_пакет(запись[1], запись[3], номер=n, время=запись[0], исходная_длина=запись[2],
                                             как=сб.как, шаблоны=шаблоны))
            сб.закрыть()
    finally:
        sborka.собранный = исходная
    return итог


class Пул(ispolniteli.Пул):
    """Пул исполнителей, который помнит поданные задачи (имя, начало и конец)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.задачи: list = []

    def подать(self, функция, *args, **kwargs):
        задача = super().подать(функция, *args, **kwargs)
        self.задачи.append(задача)
        return задача

    def имена(self, вид: str) -> list[str]:
        return [з.имя for з in self.задачи if з.имя.startswith(вид)]


def ведущий(папка: Path, файл: Path, пул, *, размер=РАЗМЕР, **ещё) -> pachki.Ведущий:
    if not (папка / "захват.json").exists():
        мета(папка, файл)
    в = pachki.Ведущий(папка, пул, владелец=1, путь=файл, как={}, поколение=1, размер=размер, **ещё)
    в.запустить()
    return в


def содержимое(папка: Path, файл: Path) -> dict:
    """Всё, что видно читателям хранилища записи, — для сравнения разборов (места блоков — не в счёт)."""
    х = Хранилище(папка, файл)
    n = х.число()
    записи = х.записи(0, n)
    итог = {"число": n, "каналы": х.каналы(), "сводки": х.сводки(0, n), "поля": х.поля(0, n),
            "кадры": [hashlib.sha1(к).hexdigest() for к in х.кадры(0, n)],
            "места": записи["место"].tolist(), "каналы_пакетов": записи["канал"].tolist(),
            "признаки": записи["признаки"].tolist(), "время": х.времена().tolist(), "длины": х.длины().tolist(),
            "потоки": х.потоки().tolist(),
            "собранные": {н: х.собранный_с_каналом(н) for н in range(1, n + 1)
                          if записи["признаки"][н - 1] & 1},
            "шаблоны": json.loads((папка / "шаблоны.json").read_text(encoding="utf-8"))
            if (папка / "шаблоны.json").exists() else {}}
    стат = папка / "статистика"
    итог["статистика"] = {п.name: json.loads(п.read_text(encoding="utf-8")) for п in sorted(стат.glob("*.json"))}
    итог["объекты"] = объекты(х, итог["шаблоны"])
    итог["потоки_tcp_udp"] = [(имя, hashlib.sha1(данные).hexdigest(), т)
                              for имя, данные, _, т in vygruzka.потоки(Ряд(х, "сводки"), Ряд(х, "нагрузки_тр"))]
    return итог


def объекты(х: Хранилище, шаблоны: dict) -> list:
    итог = obekty.собрать(Ряд(х, "сводки"), Ряд(х, "нагрузки_тр"), sdp=шаблоны.get("sdp-потоки") or {})
    return [(о.вид, о.имя, о.тип, len(о.данные), hashlib.sha1(о.данные).hexdigest(), list(о.пакеты), list(о.заметки))
            for о in итог["объекты"]]


class ПачкиОбщее(unittest.TestCase):
    процессы = False

    @classmethod
    def setUpClass(cls):
        cls._папка = tempfile.TemporaryDirectory()
        cls.папка = Path(cls._папка.name)
        cls.кадры = смесь.кадры(11, 2600, lcp=1300)
        cls.sig = cls.папка / "смесь.sig"
        cls.sig.write_bytes(смесь.sig(cls.кадры))
        cls.эталон_sig = cls.папка / "эталон-sig"
        cls.ход_подряд = подряд(cls.эталон_sig, cls.sig)
        cls.подряд_sig = содержимое(cls.эталон_sig, cls.sig)

    @classmethod
    def tearDownClass(cls):
        cls._папка.cleanup()

    def setUp(self):
        self.пул = Пул("тест-пачки", 3, процессы=self.процессы, на_владельца=3)
        self.addCleanup(self.пул.закрыть)

    def сверить(self, а: dict, б: dict) -> None:
        for ключ in а:
            with self.subTest(ключ=ключ):
                if а[ключ] != б[ключ] and isinstance(а[ключ], list):
                    i = next((i for i, (x, y) in enumerate(zip(а[ключ], б[ключ])) if x != y), None)
                    self.fail(f"{ключ}: первое расхождение на {i}: {str(а[ключ][i])[:300] if i is not None else ''} | "
                              f"{str(б[ключ][i])[:300] if i is not None else ''}; длины {len(а[ключ])} и {len(б[ключ])}")
                self.assertEqual(а[ключ], б[ключ])


class РавенствоTests(ПачкиОбщее):
    def test_sig_пачками_как_подряд(self):
        """.sig пачками по 173 (первые — 41 и 97): всё, что видно читателям, — как у разбора подряд."""
        self.assertEqual("готово", self.ход_подряд["состояние"], self.ход_подряд)
        папка = self.папка / "пачками-sig"
        в = ведущий(папка, self.sig, self.пул)
        self.assertTrue(в.ждать(300))
        self.assertEqual(("готово", ""), (в.состояние, в.ошибка))
        ход = json.loads((папка / "ход.json").read_text(encoding="utf-8"))
        self.assertEqual(len(self.кадры), ход["разобрано"])
        self.assertEqual(ход["пачки"]["сшито"], ход["пачки"]["всего"])
        self.assertTrue(ход["пачки"]["точно"])
        self.assertGreater(ход["пачки"]["всего"], 12)
        self.assertEqual(self.ход_подряд["формат"], ход["формат"])
        self.assertEqual(self.ход_подряд["заметки"], ход["заметки"])
        self.assertIsNotNone(ход.get("mp_короткие_с"), "LCP с опцией 18 посреди записи")
        self.assertEqual(self.ход_подряд.get("mp_короткие_с"), ход.get("mp_короткие_с"))
        self.сверить(self.подряд_sig, содержимое(папка, self.sig))
        # Пачки после сшивки убраны, состояние сшивки — тоже; остаётся план и журнал разведки.
        self.assertEqual([], [п.name for п in (папка / "пачки").iterdir() if п.is_dir()])
        self.assertFalse((папка / "сшивка").exists())

    def test_смесь_проверяет_стыки(self):
        """Смесь и пачки подобраны так, что через стыки идут фрагменты IPv4, MP, сегменты SCCP, объекты, потоки
        RTP по SDP и записи NetFlow по шаблону — иначе равенство мало что доказывало бы."""
        с = self.подряд_sig
        границы = [1]
        while границы[-1] <= с["число"]:
            k = len(границы) - 1
            границы.append(границы[-1] + pachki.предел_пачки(k, РАЗМЕР))

        def пачка(номер: int) -> int:
            return int(np.searchsorted(границы, номер, side="right")) - 1

        через: dict[str, int] = {}
        for номер, (имя, номера) in собранные_из(self.sig).items():
            if пачка(min(номера)) != пачка(номер):
                через[имя] = через.get(имя, 0) + 1
        self.assertEqual({"IPv4", "IPv6", "PPP", "TCAP"}, set(через), через)
        # RTP по SDP, объявленному в прежней пачке.
        потоки = с["шаблоны"].get("sdp-потоки") or {}
        rtp = 0
        for сводка in с["сводки"]:
            if сводка["протокол"] != "RTP":
                continue
            for ключ in (f"{сводка['получатель']}|{сводка['порт_к']}", f"{сводка['источник']}|{сводка['порт_от']}"):
                if ключ in потоки and пачка(потоки[ключ][0]) < пачка(сводка["номер"]):
                    rtp += 1
                    break
        self.assertGreater(rtp, 50)
        # Записи NetFlow по шаблону из прежней пачки.
        шаблоны_nf = [в[0][0] for к, в in с["шаблоны"].items() if к.count("|") == 3 and isinstance(в, list)]
        self.assertTrue(шаблоны_nf)
        nf = [с_["номер"] for с_ in с["сводки"] if с_["протокол"] in ("NetFlow", "NetFlow v9", "CFLOW")]
        self.assertTrue(any(пачка(н) > пачка(min(шаблоны_nf)) for н in nf), nf[:5])
        # Объекты HTTP и FTP из пакетов разных пачек.
        виды = {о[0] for о in с["объекты"] if len({пачка(н) for н in о[5]}) > 1}
        self.assertTrue({"HTTP", "FTP"} <= виды, виды)

    def test_pcapng_и_pcap_пачками_как_подряд(self):
        for имя, данные in (("смесь.pcapng", смесь.pcapng(self.кадры)), ("смесь.pcap", смесь.pcap(self.кадры))):
            with self.subTest(файл=имя):
                файл = self.папка / имя
                файл.write_bytes(данные)
                эталон = self.папка / f"эталон-{имя}"
                ход_п = подряд(эталон, файл)
                папка = self.папка / f"пачками-{имя}"
                в = ведущий(папка, файл, self.пул)
                self.assertTrue(в.ждать(300))
                self.assertEqual(("готово", ""), (в.состояние, в.ошибка))
                ход = json.loads((папка / "ход.json").read_text(encoding="utf-8"))
                self.assertEqual((ход_п["разобрано"], ход_п["формат"], ход_п["заметки"]),
                                 (ход["разобрано"], ход["формат"], ход["заметки"]))
                self.сверить(содержимое(эталон, файл), содержимое(папка, файл))


class ХвостTests(ПачкиОбщее):
    def test_повреждённый_хвост(self):
        """Оборванный последний пакет .sig, испорченный блок pcapng посреди файла: те же пакеты и заметки."""
        оборван = self.папка / "оборван.sig"
        сырые = смесь.sig(self.кадры[:900])
        оборван.write_bytes(сырые[:-7])
        ng = bytearray(смесь.pcapng(self.кадры[:900]))
        место = 0
        while место < len(ng) // 2:                                   # блоки по длинам — до середины файла
            место += int.from_bytes(ng[место + 4:место + 8], "little")
        ng[место + 4:место + 8] = (13).to_bytes(4, "little")           # длина блока не кратна 4 — испорчен
        испорчен = self.папка / "испорчен.pcapng"
        испорчен.write_bytes(bytes(ng))
        from unittest import mock  # noqa: PLC0415

        from reportgen.setevoy import chtenie  # noqa: PLC0415
        # Небольшой .sig проверяется целиком и с оборванным хвостом не принимается вовсе (оба разбора — «ошибка»);
        # оборванный хвост большого .sig (разметка по пробе) — заметка: его и проверяем.
        заплатка = mock.patch.object(chtenie, "SIG_ЦЕЛИКОМ_ДО", 0)
        заплатка.start()
        self.addCleanup(заплатка.stop)
        for файл in (оборван, испорчен):
            with self.subTest(файл=файл.name):
                ход_п = подряд(self.папка / f"эталон-{файл.name}", файл)
                в = ведущий(self.папка / f"пачками-{файл.name}", файл, self.пул)
                self.assertTrue(в.ждать(300))
                ход = json.loads((self.папка / f"пачками-{файл.name}" / "ход.json").read_text(encoding="utf-8"))
                self.assertEqual("готово", ход["состояние"])
                self.assertTrue(any("оборван" in з or "испорчен" in з for з in ход["заметки"]), ход["заметки"])
                self.assertEqual((ход_п["разобрано"], ход_п["заметки"]), (ход["разобрано"], ход["заметки"]))
                self.assertLess(ход["разобрано"], 900)
                self.сверить(содержимое(self.папка / f"эталон-{файл.name}", файл),
                             содержимое(self.папка / f"пачками-{файл.name}", файл))

    def test_не_захват(self):
        """Файл — не захват: ошибка разметки, понятная словами."""
        файл = self.папка / "шум.bin"
        файл.write_bytes(b"x" * 100_000)
        в = ведущий(self.папка / "шум", файл, self.пул)
        self.assertTrue(в.ждать(60))
        self.assertEqual("ошибка", в.состояние)
        self.assertIn("не похож на сетевую запись", в.ошибка)


class УправлениеTests(ПачкиОбщее):
    def _ждать(self, условие, секунд: float = 120.0) -> None:
        конец = time.monotonic() + секунд
        while not условие():
            self.assertLess(time.monotonic(), конец, "не дождались")
            time.sleep(0.02)

    def test_пауза_и_продолжение(self):
        """Пауза: идущие пачки сняты, новые не ставятся, сшитое — статистикой целиком; продолжить — итог как подряд."""
        папка = self.папка / "пауза"
        в = ведущий(папка, self.sig, self.пул)
        self._ждать(lambda: в.сшито >= 2)
        self.assertTrue(в.команда("пауза"))
        self._ждать(lambda: not в.в_работе and в.разметка_задача is None and в.сшивка_задача is None)
        time.sleep(0.3)
        поданных = len(self.пул.имена("пачка"))
        сшито = в.сшито
        time.sleep(0.6)
        self.assertEqual(поданных, len(self.пул.имена("пачка")), "на паузе новые пачки не ставятся")
        self.assertEqual(сшито, в.сшито)
        ход = json.loads((папка / "ход.json").read_text(encoding="utf-8"))
        self.assertEqual("пауза", ход["состояние"])
        self.assertTrue(в._всё_записано, "на паузе статистика сшитого записана целиком")
        счёт = json.loads((папка / "статистика" / "счёт.json").read_text(encoding="utf-8"))
        self.assertEqual(в.пакетов, счёт["пакетов"])
        self.assertTrue(в.команда("продолжить"))
        self.assertTrue(в.ждать(300))
        self.assertEqual("готово", в.состояние)
        self.сверить(self.подряд_sig, содержимое(папка, self.sig))

    def test_стоп_оставляет_сшитое_как_подряд(self):
        """Стоп: сшитое остаётся, и оно — ровно разбор подряд того же числа первых пакетов."""
        папка = self.папка / "стоп"
        в = ведущий(папка, self.sig, self.пул)
        self._ждать(lambda: в.сшито >= 3)
        в.команда("стоп")
        self.assertTrue(в.ждать(120))
        ход = json.loads((папка / "ход.json").read_text(encoding="utf-8"))
        self.assertEqual(("остановлен", "остановлен по команде"), (ход["состояние"], ход["ошибка"]))
        n = ход["разобрано"]
        self.assertTrue(0 < n < len(self.кадры))
        self.assertEqual(n, Хранилище(папка, self.sig).число())
        часть = self.папка / "первые.sig"
        часть.write_bytes(смесь.sig(self.кадры[:n]))
        подряд(self.папка / "эталон-первые", часть)
        а, б = содержимое(self.папка / "эталон-первые", часть), содержимое(папка, self.sig)
        for ключ in ("кадры", "места"):
            а.pop(ключ), б.pop(ключ)                                   # файл другой — места те же только у .sig
        self.сверить(а, б)
        self.assertFalse(в.команда("продолжить"), "кончившийся разбор команд не принимает")

    def test_перезапуск_посреди_разбора(self):
        """Сервер выключили посреди разбора: после запуска разбор продолжается с последней сшитой пачки (сшитые
        и готовые пачки заново не разбираются), итог — как подряд."""
        папка = self.папка / "перезапуск"
        в = ведущий(папка, self.sig, self.пул)
        self._ждать(lambda: в.сшито >= 4)
        в.остановить("сервер останавливается", ждать=10, ход=False)
        self.assertEqual("остановлен", в.состояние)
        ход = json.loads((папка / "ход.json").read_text(encoding="utf-8"))
        self.assertNotIn(ход["состояние"], pachki.КОНЕЦ, "ход на диске — «идёт»: после запуска разбор продолжится")
        кратко = json.loads((папка / "сшивка" / "ход.json").read_text(encoding="utf-8"))
        сшито = кратко["сшито"]
        self.assertGreaterEqual(сшито, 1)
        готовые = sorted(int(п.name) for п in (папка / "пачки").iterdir() if п.name.isdigit())
        # Хвост файлов хранилища после сохранённого — как после сбоя: сшивка его отрежет.
        with open(папка / "сводки.z", "ab") as ф:
            ф.write(b"\0" * 1000)
        пул2 = Пул("тест-пачки-2", 3, процессы=self.процессы, на_владельца=3)
        self.addCleanup(пул2.закрыть)
        в2 = pachki.Ведущий(папка, пул2, владелец=1, путь=self.sig, как={}, поколение=1, размер=РАЗМЕР)
        self.assertEqual(сшито, в2.сшито)
        self.assertEqual(set(готовые), в2.готовые)
        в2.запустить()
        self.assertTrue(в2.ждать(300))
        self.assertEqual("готово", в2.состояние)
        заново = {int(и.split()[1]) for и in пул2.имена("пачка")}
        self.assertFalse(заново & set(range(сшито)), "сшитые пачки заново не разбираются")
        self.assertFalse(заново & set(готовые), "готовые пачки заново не разбираются")
        self.сверить(self.подряд_sig, содержимое(папка, self.sig))

    def test_загрузка_кусками(self):
        """Файл растёт (загрузка кусками): разбор ждёт данных, пачки размечаются по мере прихода, итог — как подряд."""
        папка = self.папка / "загрузка"
        файл = папка / "исходник"
        папка.mkdir()
        сырые = self.sig.read_bytes()
        файл.write_bytes(сырые[:5000])
        загрузка = {"идёт": True, "принято": 5000, "всего": len(сырые)}
        (папка / "захват.json").write_text(json.dumps({"ид": "з", "владелец": 1, "имя": "a.sig", "поколение": 1,
                                                       "загрузка": загрузка}), encoding="utf-8")
        в = pachki.Ведущий(папка, self.пул, владелец=1, путь=файл, как={}, поколение=1, размер=РАЗМЕР)
        в.запустить()
        for конец in range(5000, len(сырые) + 1, 300_000):
            time.sleep(0.3)
            with open(файл, "ab") as ф:
                ф.write(сырые[конец:конец + 300_000])
        self._ждать(lambda: в.состояние in ("ждёт данных", "идёт"))
        self.assertFalse(в.ждать(1.5), "загрузка не кончена — разбор ждёт")
        загрузка.update(идёт=False, принято=len(сырые))
        (папка / "захват.json").write_text(json.dumps({"ид": "з", "владелец": 1, "имя": "a.sig", "поколение": 1,
                                                       "загрузка": загрузка}), encoding="utf-8")
        self.assertTrue(в.ждать(300))
        self.assertEqual("готово", в.состояние)
        а = содержимое(папка, файл)
        self.сверить({к: з for к, з in self.подряд_sig.items()}, а)

    def test_заново_и_удаление(self):
        """«Разобрать заново» (новое поколение) снимает идущий разбор: прежнее разобранное убирается, новое — как подряд."""
        папка = self.папка / "заново"
        в = ведущий(папка, self.sig, self.пул)
        self._ждать(lambda: в.сшито >= 2)
        в.остановить("запись разбирается заново", ждать=10, ход=False)
        мета(папка, self.sig, поколение=2)
        м = json.loads((папка / "захват.json").read_text(encoding="utf-8"))
        м["поколение"] = 2
        (папка / "захват.json").write_text(json.dumps(м, ensure_ascii=False), encoding="utf-8")
        в2 = pachki.Ведущий(папка, self.пул, владелец=1, путь=self.sig, как={}, поколение=2, размер=РАЗМЕР)
        self.assertEqual((0, set()), (в2.сшито, в2.готовые))
        в2.запустить()
        self.assertTrue(в2.ждать(300))
        self.assertEqual("готово", в2.состояние)
        self.сверить(self.подряд_sig, содержимое(папка, self.sig))


class ПроцессыTests(ПачкиОбщее):
    процессы = True

    def test_параллельно_процессами(self):
        """Пачки разбираются в нескольких процессах сразу (задачи перекрываются во времени); итог — как подряд."""
        папка = self.папка / "процессы"
        в = ведущий(папка, self.sig, self.пул, размер={"пакетов": 400, "байт": 1 << 30, "первые": [100]})
        self.assertTrue(в.ждать(300))
        self.assertEqual(("готово", ""), (в.состояние, в.ошибка))
        пачки = sorted((з.начата, з.закончена) for з in self.пул.задачи if з.имя.startswith("пачка") and з.начата)
        перекрытий = sum(1 for (н1, к1), (н2, к2) in zip(пачки, пачки[1:]) if н2 < к1)
        self.assertGreater(перекрытий, 0, пачки)
        self.сверить(self.подряд_sig, содержимое(папка, self.sig))


class СборкаIPv6Tests(unittest.TestCase):
    def test_фрагменты_с_заголовком_расширения(self):
        """RFC 8200, 4.5: собранный пакет — нефрагментируемая часть первого фрагмента (с «переходами» перед
        фрагментом), «следующий заголовок» перед фрагментом — из заголовка фрагмента, длина — по собранному; порядок
        прихода фрагментов любой; чужой идентификатор не мешает; целый пакет с заголовком фрагмента — не фрагмент."""
        import struct  # noqa: PLC0415

        from reportgen.setevoy.razbor import разобрать_пакет  # noqa: PLC0415
        from reportgen.setevoy.sborka import Сборка  # noqa: PLC0415
        src, dst = смесь.a6(1), смесь.a6(2)
        udp = смесь.udp6(bytes(range(256)) * 9, 5000, 6000, src, dst)              # 2312 байт
        переходы = bytes([44, 0]) + bytes([1, 4, 0, 0, 0, 0])                    # Hop-by-Hop, PadN; дальше — фрагмент
        целый = struct.pack(">IHBB16s16s", 6 << 28, 8 + len(udp), 0, 64, src, dst) + bytes([17, 0]) + переходы[2:] + udp

        def фрагмент(смещение: int, кусок: bytes, m: int, ид: int = 77) -> bytes:
            заг = struct.pack(">BBHI", 17, 0, (смещение // 8) << 3 | m, ид)
            тело = переходы + заг + кусок
            return struct.pack(">IHBB16s16s", 6 << 28, len(тело), 0, 64, src, dst) + тело
        куски = [фрагмент(0, udp[:1000], 1), фрагмент(1000, udp[1000:2000], 1), фрагмент(2000, udp[2000:], 0)]
        чужой = фрагмент(0, udp[:1000], 1, ид=78)
        отдельный = struct.pack(">IHBB16s16s", 6 << 28, 8 + len(udp), 44, 64, src, dst) \
            + struct.pack(">BBHI", 17, 0, 0, 5) + udp                                 # смещение 0, M = 0
        with tempfile.TemporaryDirectory() as т:
            сб = Сборка(Path(т), {})
            итоги = []
            for номер, кадр in enumerate([куски[2], чужой, куски[0], отдельный, куски[1]], 1):
                п = разобрать_пакет(кадр, "IPv6", номер=номер)
                _, собран = сб.пакет(п)
                итоги.append(собран)
            self.assertEqual([False, False, False, False, True], итоги)
            self.assertEqual([1, 3, 5], сб.ip6.последние_номера)
            self.assertEqual((целый, "IPv6"), Хранилище(Path(т)).собранный_с_каналом(5))
            self.assertEqual(1, len(сб.ip6.куски), "чужая сборка ждёт своих")
            сб.закрыть()


class ЗахватыПачкамиTests(ПачкиОбщее):
    """Записи «Анализа пакетов» пачками: то же, что подряд, — список, отборы, объекты; команды, «разбирать как»,
    удаление и перезапуск сервера посреди разбора; загрузка кусками."""

    def захваты(self, имя: str, **ещё):
        from reportgen.setevoy.zahvaty import Захваты  # noqa: PLC0415
        з = Захваты(self.папка / имя, процессы=False, процессом_от=0, размер_пачки=РАЗМЕР,
                    **({"пул": self.пул} | ещё))
        self.addCleanup(з.закрыть)
        return з

    def ждать(self, з, ид: str, состояния=pachki.КОНЕЦ, секунд: float = 120.0) -> dict:
        конец = time.monotonic() + секунд
        while True:
            с = з.прочитать(ид)
            if с["состояние"] in состояния:
                return с
            self.assertLess(time.monotonic(), конец, с)
            time.sleep(0.02)

    def test_как_подряд_отборы_объекты(self):
        з = self.захваты("з1")
        ид = з.создать(владелец=1, имя="смесь.sig", ссылка=self.sig)
        self.assertIsNotNone(з.ведущий(ид), "файл от процессом_от — пачками")
        с = з.дождаться(ид, 300)
        self.assertEqual(("готово", len(self.кадры)), (с["состояние"], с["разобрано"]))
        self.assertEqual(с["пачки"]["сшито"], с["пачки"]["всего"])
        self.сверить(self.подряд_sig, содержимое(з.папка / ид, self.sig))
        подряд_з = self.захваты("з1п", пачками=False)
        ид_п = подряд_з.создать(владелец=1, имя="смесь.sig", ссылка=self.sig)
        self.assertEqual("готово", подряд_з.дождаться(ид_п, 300)["состояние"])
        for фильтр in ("sccp", "ip.flags.mf == 1 || mp", "rtp", "frame contains 47:45:54"):
            with self.subTest(фильтр=фильтр):
                self.assertEqual(подряд_з.отобрать(ид_п, фильтр), з.отобрать(ид, фильтр))
        о1, о2 = з.объекты(ид), подряд_з.объекты(ид_п)
        self.assertEqual([(о.вид, о.имя, о.данные) for о in о2["объекты"]], [(о.вид, о.имя, о.данные) for о in о1["объекты"]])
        self.assertEqual(подряд_з.пакет(ид_п, 777), з.пакет(ид, 777))

    def test_команды_заново_удаление(self):
        з = self.захваты("з2")
        ид = з.создать(владелец=1, имя="смесь.sig", ссылка=self.sig)
        self.ждать(з, ид, ("идёт",))
        self.assertTrue(з.команда(ид, "пауза"))
        self.assertEqual("пауза", self.ждать(з, ид, ("пауза",))["состояние"])
        self.assertTrue(з.команда(ид, "продолжить"))
        self.assertEqual("готово", з.дождаться(ид, 300)["состояние"])
        # «Разбирать как» посреди разбора: прежний снимается, новый — с правилами, до конца.
        правила = з.разбирать_как(ид, {"udp:5060": "DNS"})
        с = з.дождаться(ид, 300)
        self.assertEqual(("готово", 2, правила), (с["состояние"], с["поколение"], с["как"]))
        self.assertEqual(len(self.кадры), с["разобрано"])
        ид2 = з.создать(владелец=1, имя="ещё.sig", ссылка=self.sig)
        self.ждать(з, ид2, ("идёт",))
        з.удалить(ид2)
        self.assertFalse((з.папка / ид2).exists())
        ид3 = з.создать(владелец=1, имя="стоп.sig", ссылка=self.sig)
        self.ждать(з, ид3, ("идёт",))
        з.команда(ид3, "стоп")
        с = self.ждать(з, ид3)
        self.assertEqual("остановлен", с["состояние"])
        self.assertEqual(с["разобрано"], з.хранилище(ид3).число())

    def test_перезапуск_сервера(self):
        """Сервер выключили посреди разбора; после запуска разбор продолжается сам (с последней сшитой пачки)."""
        з = self.захваты("з3")
        ид = з.создать(владелец=1, имя="смесь.sig", ссылка=self.sig)
        конец = time.monotonic() + 60
        while (з.ведущий(ид) is None or з.ведущий(ид).сшито < 3) and time.monotonic() < конец:
            time.sleep(0.02)
        з.закрыть()
        self.assertNotIn(з.прочитать(ид)["состояние"], ("готово",))
        пул2 = Пул("тест-пачки-3", 3, процессы=False, на_владельца=3)
        self.addCleanup(пул2.закрыть)
        з2 = self.захваты("з3", пул=пул2)
        self.assertIsNotNone(з2.ведущий(ид), "разбор продолжился сам")
        self.assertGreaterEqual(з2.ведущий(ид).сшито, 1)
        с = з2.дождаться(ид, 300)
        self.assertEqual(("готово", len(self.кадры)), (с["состояние"], с["разобрано"]))
        self.assertFalse({int(и.split()[1]) for и in пул2.имена("пачка")} & {0})
        self.сверить(self.подряд_sig, содержимое(з2.папка / ид, self.sig))

    def test_загрузка_кусками(self):
        з = self.захваты("з4")
        сырые = self.sig.read_bytes()
        ид = з.создать(владелец=1, имя="смесь.sig", загрузка=len(сырые))
        for место in range(0, len(сырые), 1 << 20):
            з.дописать(ид, место, сырые[место:место + (1 << 20)])
            time.sleep(0.2)
        з.закончить_загрузку(ид)
        с = з.дождаться(ид, 300)
        self.assertEqual(("готово", len(self.кадры)), (с["состояние"], с["разобрано"]))
        а = содержимое(з.папка / ид, з.папка / ид / "исходник")
        self.сверить(self.подряд_sig, а)

    def test_загрузка_после_долгого_перерыва(self):
        """Загрузка встала надолго — разбор кончается «прерван» на том, что пришло; продолжили — разбор заново
        и до конца."""
        from unittest import mock  # noqa: PLC0415
        з = self.захваты("з5")
        сырые = self.sig.read_bytes()
        ид = з.создать(владелец=1, имя="смесь.sig", загрузка=len(сырые))
        with mock.patch.object(pachki, "ЖДАТЬ_ЗАГРУЗКУ", 0.5):
            з.дописать(ид, 0, сырые[:1 << 20])
            с = self.ждать(з, ид)
        self.assertEqual("прерван", с["состояние"], с)
        self.assertIn("загрузка прервалась", с["ошибка"])
        self.assertGreater(с["разобрано"], 0)
        з.дописать(ид, 1 << 20, сырые[1 << 20:])
        з.закончить_загрузку(ид)
        с = з.дождаться(ид, 300)
        self.assertEqual(("готово", len(self.кадры)), (с["состояние"], с["разобрано"]))
        self.сверить(self.подряд_sig, содержимое(з.папка / ид, з.папка / ид / "исходник"))


class ИзРазбораПотокаTests(unittest.TestCase):
    def test_пакеты_sig_в_анализ_пакетов_целиком(self):
        """«Пакеты» первого этапа разбора .sig как потока — в анализ пакетов уходит весь файл: все кадры (и не IP —
        ОКС-7, LAPD…), а не выгрузка этапа (опознанные IP из первых кадров)."""
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(себя):
                pass
        сеть = Сеть()
        сеть.setUp()
        self.addCleanup(сеть.tearDown)
        к = сеть.client
        сеть.login("engineer")
        кадры = смесь.кадры(4, 400, шум=1 << 16)
        ответ = к.post("/api/potok", data={"profile": "быстро"},
                       files={"file": ("запись.sig", смесь.sig(кадры), "application/octet-stream")})
        self.assertEqual(200, ответ.status_code, ответ.text)
        job = ответ.json()["id"]
        конец = time.monotonic() + 120
        while (состояние := к.get(f"/api/potok/{job}").json())["состояние"] not in ("готово", "ошибка"):
            self.assertLess(time.monotonic(), конец)
            time.sleep(0.2)
        этап = состояние["этапы"][0]
        self.assertEqual(("пакеты IP", "pcap"), (этап["что"], этап.get("выгрузка")))
        ид = к.post("/api/pakety/from-potok", json={"job": job, "stage": 1}).json()["id"]
        конец = time.monotonic() + 120
        while (з := к.get(f"/api/pakety/{ид}").json())["состояние"] not in pachki.КОНЕЦ:
            self.assertLess(time.monotonic(), конец)
            time.sleep(0.1)
        self.assertEqual(("готово", len(кадры), "запись.sig", ".sig"), (з["состояние"], з["разобрано"], з["имя"], з["формат"]))
        ip = sum(1 for вид, _ in кадры if вид in ("ip", "eth", "ppp", "chdlc", "fr"))
        self.assertLess(ip, len(кадры), "в смеси есть и не IP")

    def test_пакеты_pcap_целиком_и_этап_по_началу_файла(self):
        """«Пакеты» первого этапа захвата pcap — весь захват (все записи, не только IP в выгрузке этапа); выгрузка
        этапа, ещё не пересчитанного по всему файлу, — с пометкой «по началу файла» в имени."""
        import struct  # noqa: PLC0415

        import potok_sintez as с  # noqa: PLC0415
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(себя):
                pass
        сеть = Сеть()
        сеть.setUp()
        self.addCleanup(сеть.tearDown)
        к = сеть.client
        сеть.login("engineer")

        def дождаться(job):
            конец = time.monotonic() + 120
            while (состояние := к.get(f"/api/potok/{job}").json())["состояние"] not in ("готово", "ошибка"):
                self.assertLess(time.monotonic(), конец)
                time.sleep(0.2)
            return состояние

        def пакеты(ид):
            конец = time.monotonic() + 120
            while (з := к.get(f"/api/pakety/{ид}").json())["состояние"] not in pachki.КОНЕЦ:
                self.assertLess(time.monotonic(), конец)
                time.sleep(0.1)
            return з
        кадры = [с.ethernet(п) for п in с.пакеты_ip(60)] + [bytes.fromhex("ffffffffffff001122334455" "0806") + bytes(28)] * 7
        захват = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1) + b"".join(
            struct.pack("<IIII", 0, 0, len(к_), len(к_)) + к_ for к_ in кадры)
        job = к.post("/api/potok", data={"profile": "быстро"},
                     files={"file": ("захват.pcap", захват, "application/octet-stream")}).json()["id"]
        self.assertEqual("сетевой", дождаться(job)["этапы"][0]["уровень"])
        з = пакеты(к.post("/api/pakety/from-potok", json={"job": job, "stage": 1}).json()["id"])
        self.assertEqual(("готово", len(кадры), "захват.pcap"), (з["состояние"], з["разобрано"], з["имя"]))
        # Этап 2 (IP в кадрах HDLC) по выборке — имя говорит, что это начало файла.
        job = к.post("/api/potok", data={"profile": "быстро"},
                     files={"file": ("поток.bin", с.hdlc(с.пакеты_ip(40)), "application/octet-stream")}).json()["id"]
        self.assertEqual(["sig", "pcap"], [э.get("выгрузка") for э in дождаться(job)["этапы"]])
        задания = сеть.app.state.potok
        состояние = задания._прочитать_файл(job)
        состояние["этапы"][1]["по_выборке"] = True
        задания._записать(job, состояние)
        з = пакеты(к.post("/api/pakety/from-potok", json={"job": job, "stage": 2}).json()["id"])
        self.assertEqual("поток — этап 2 — по началу файла.pcap", з["имя"])


@unittest.skipUnless(os.environ.get("REPORTGEN_MEDLENNO"), "медленный (сотни мегабайт): REPORTGEN_MEDLENNO=1")
class БольшойФайлTests(unittest.TestCase):
    def test_большой_файл_процессами(self):
        """Смесь на 200 МБ пачками обычного размера в процессах: пакетов — сколько записано, собранного — сколько
        собрано подряд за первую часть, память сервера не растёт с размером."""
        with tempfile.TemporaryDirectory() as т:
            т = Path(т)
            файл = т / "большой.sig"
            опись = смесь.записать(файл, 200, 5, None)
            пул = Пул("тест-большой", ispolniteli.ядер(), процессы=True, на_владельца=max(1, ispolniteli.ядер() - 1))
            self.addCleanup(пул.закрыть)
            начато = time.monotonic()
            в = ведущий(т / "запись", файл, пул, размер=None)
            self.assertTrue(в.ждать(3600))
            self.assertEqual(("готово", ""), (в.состояние, в.ошибка))
            х = Хранилище(т / "запись", файл)
            self.assertEqual(опись["кадров"], х.число())
            счёт = json.loads((т / "запись" / "статистика" / "счёт.json").read_text(encoding="utf-8"))
            self.assertEqual(опись["кадров"], счёт["пакетов"])
            собрано = int(np.count_nonzero(х.записи(0, х.число())["признаки"] & 1))
            полных = опись["счёт"].get("ipv4_фрагм_полных", 0) + опись["счёт"].get("mp_полных", 0)
            self.assertGreater(собрано, полных // 2)
            print(f"\nбольшой файл: {опись['байт'] >> 20} МБ, {опись['кадров']} пакетов за "
                  f"{time.monotonic() - начато:.0f} с, собрано {собрано}")


if __name__ == "__main__":
    unittest.main()
