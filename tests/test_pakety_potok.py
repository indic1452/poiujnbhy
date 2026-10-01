"""«Анализ пакетов» потоком: чтение по записи, хранилище на диске, статистика по ходу, отборы,
загрузка кусками, файлы по ссылке, очередь работ, страница (функции app.js в node).

Главная сверка — итог потокового разбора равен итогу разбора всего файла сразу: те же
записи (chtenie), те же сводки, та же статистика (statistika над всеми сводками).
"""

import json
import os
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import obekty_sintez as ос
import setevoy_sintez as с
from pakety_generator import записать as записать_генератором
from test_potok_sessii import функции_js

from reportgen.setevoy import statistika, vydacha
from reportgen.setevoy.chtenie import ЧтецЗахвата, вид_по_началу, выбрать_разметку_sig, прочитать_захват
from reportgen.setevoy.hranilishe import Писатель, Ряд, Хранилище, нагрузки, отпечаток_потока
from reportgen.setevoy.nakopitel import Накопитель, узел_из_снимка
from reportgen.setevoy.raboty import Диспетчер, Работа
from reportgen.setevoy.sborka import _для_фильтра, _сводка
from reportgen.setevoy.zahvaty import Захваты, ОшибкаЗагрузки


def смесь(n: int = 60) -> list[bytes]:
    """Пакеты разных протоколов: DNS, HTTP, TCP с ошибкой суммы, ICMP, ARP, IPv6, неизвестный UDP."""
    кадры = []
    for i in range(n):
        вид = i % 7
        if вид == 0:
            кадры.append(с.eth(с.ip(с.udp(с.dns_запрос(f"h{i}.example.ru", ид=i + 1), 40000 + i, 53), 17)))
        elif вид == 1:
            кадры.append(с.eth(с.ip(с.udp(с.dns_ответ(f"h{i - 1}.example.ru", ["192.0.2.1"], ид=i), 53, 40000 + i - 1,
                                          src="10.0.0.2", dst="10.0.0.1"), 17, src="10.0.0.2", dst="10.0.0.1")))
        elif вид == 2:
            кадры.append(с.eth(с.ip(с.tcp(f"GET /{i} HTTP/1.1\r\nHost: s{i % 3}.local\r\n\r\n".encode(), 41000 + i, 80), 6)))
        elif вид == 3:
            сег = bytearray(с.tcp(bytes(range(200)), 445, 50000 + i, src="10.0.0.3", dst="10.0.0.4"))
            сег[16] ^= 0xFF                       # испорченная сумма — для «Ошибок»
            кадры.append(с.eth(с.ip(bytes(сег), 6, src="10.0.0.3", dst="10.0.0.4")))
        elif вид == 4:
            кадры.append(с.eth(с.ip(с.icmp(8, 0, b"abcd" * 4), 1, src="10.0.0.5", dst="10.0.0.6")))
        elif вид == 5:
            кадры.append(с.eth(с.ip6(с.udp(b"\x11" * 30, 5000, 6000, src="2001:db8::1", dst="2001:db8::2", v6=True), 17),
                               тип=0x86DD))
        else:
            кадры.append(с.eth(с.ip(с.udp(struct.pack(">HHI", 0xA55A, 20, i) + b"\x00" * 20, 7000, 7001), 17)))
    return кадры


def времена(n: int) -> list[float]:
    return [1_760_000_000 + i * 0.013 for i in range(n)]


def дождаться(условие, секунд: float = 60.0) -> bool:
    конец = time.monotonic() + секунд
    while time.monotonic() < конец:
        if условие():
            return True
        time.sleep(0.02)
    return False


class ЧтениеПотокомTests(unittest.TestCase):
    """ЧтецЗахвата: те же записи, что у чтения файла целиком, и для растущего файла."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.папка = Path(self._tmp.name)

    def все(self, путь, готов=lambda: True):
        итог = []
        with ЧтецЗахвата(путь, готов=готов) as ч:
            while (з := ч.следующий()) not in (None, False):
                итог.append(з)
            return итог, ч

    def test_pcap_pcapng_sig_как_целиком(self):
        кадры = смесь(40)
        for имя, данные in (("a.pcap", с.pcap(кадры, времена=времена(40))), ("a.pcapng", с.pcapng(кадры)),
                            ("a.sig", b"".join(struct.pack(">H", len(к)) + к for к in кадры))):
            with self.subTest(имя):
                путь = self.папка / имя
                путь.write_bytes(данные)
                записи, ч = self.все(путь)
                целиком = прочитать_захват(путь)
                self.assertEqual([(з.время, з.данные, з.длина, з.канал) for з in целиком.записи],
                                 [(в, д, и, к) for в, д, и, к, _ in записи])
                # Место данных — там, где байты пакета лежат в файле (хранилище читает их оттуда).
                for в, д, и, к, место in записи:
                    self.assertEqual(д, данные[место:место + len(д)])
                self.assertEqual(целиком.формат if целиком.формат != ".sig" else ".sig", ч.формат)

    def test_оборванный_хвост_и_испорченная_запись(self):
        кадры = смесь(10)
        данные = с.pcap(кадры)
        путь = self.папка / "x.pcap"
        путь.write_bytes(данные[:-7])
        записи, ч = self.все(путь)
        self.assertEqual(9, len(записи))
        self.assertIn("файл оборван: последний пакет записан не целиком", ч.заметки)
        испорчен = bytearray(данные)
        struct.pack_into("<I", испорчен, 24 + 8, 1 << 30)          # длина первой записи — гигабайт
        путь.write_bytes(bytes(испорчен))
        записи, ч = self.все(путь)
        self.assertEqual([], записи)
        self.assertTrue(any("испорчена" in з for з in ч.заметки), ч.заметки)

    def test_растущий_файл_ждёт_и_продолжает(self):
        кадры = смесь(30)
        данные = с.pcap(кадры)
        путь = self.папка / "g.pcap"
        путь.write_bytes(данные[:100])
        готов = {"да": False}
        ч = ЧтецЗахвата(путь, готов=lambda: готов["да"])
        прочитано = []
        for кусок in range(100, len(данные) + 333, 333):
            while (з := ч.следующий()) not in (None, False):
                прочитано.append(з[1])
            self.assertIsNone(ч.следующий(), "пока загрузка идёт — «подождать», а не конец")
            with open(путь, "ab") as ф:
                ф.write(данные[путь.stat().st_size:кусок])
        готов["да"] = True
        while (з := ч.следующий()) not in (None, False):
            прочитано.append(з[1])
        self.assertIs(False, ч.следующий())
        ч.закрыть()
        self.assertEqual(кадры, прочитано)
        self.assertEqual([], ч.заметки)

    def test_sig_большой_и_растущий_по_пробе(self):
        кадры = [bytes([i % 251]) * (40 + i % 17) for i in range(3000)]
        данные = b"".join(struct.pack(">H", len(к)) + к for к in кадры)
        self.assertEqual((0, "big", False), выбрать_разметку_sig(данные[:5000]))
        self.assertIsNone(выбрать_разметку_sig(b"\xff" * 300))
        путь = self.папка / "b.sig"
        путь.write_bytes(данные[:1000])
        ч = ЧтецЗахвата(путь, готов=lambda: False)
        self.assertIsNone(ч.следующий(), "по неполной пробе .sig не судим")
        ч.закрыть()
        from reportgen.setevoy import chtenie
        старое = chtenie.SIG_ПРОБА
        chtenie.SIG_ПРОБА = 4096
        self.addCleanup(setattr, chtenie, "SIG_ПРОБА", старое)
        путь.write_bytes(данные)
        старое_целиком = chtenie.SIG_ЦЕЛИКОМ_ДО
        chtenie.SIG_ЦЕЛИКОМ_ДО = 1000
        self.addCleanup(setattr, chtenie, "SIG_ЦЕЛИКОМ_ДО", старое_целиком)
        записи, ч = self.все(путь)
        self.assertEqual(кадры, [з[1] for з in записи])
        self.assertIn("разметка выбрана по первым", ч.заметки[0])

    def test_вид_по_началу(self):
        self.assertEqual("pcap", вид_по_началу(с.pcap(смесь(2)), весь=True))
        self.assertEqual("pcapng", вид_по_началу(с.pcapng(смесь(2)), весь=False))
        self.assertIsNone(вид_по_началу(b"not a capture at all" * 5, весь=True))
        self.assertEqual("sig", вид_по_началу(b"".join(struct.pack(">H", 20) + bytes(20) for _ in range(5)), весь=True))


class ХранилищеTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.папка = Path(self._tmp.name)

    def test_запись_и_чтение_блоками(self):
        п = Писатель(self.папка, копировать=True)
        for i in range(1000):
            п.добавить(данные=bytes([i % 256]) * (i % 50 + 1), место=None, канал="Ethernet" if i % 3 else "IP",
                       сводка={"номер": i + 1, "время": float(i), "длина": i, "стек": ["x"], "нагр": [0, 1]},
                       поля={"frame.number": [i + 1]}, время=float(i), длина=i % 50 + 1, поток=i % 7)
            if i == 600:
                self.assertEqual(512, Хранилище(self.папка).число(), "видно только сброшенное блоками")
        п.закрыть()
        х = Хранилище(self.папка)
        self.assertEqual(1000, х.число())
        self.assertEqual([998, 999, 1000], [с_["номер"] for с_ in х.сводки(997, 1000)])
        self.assertEqual([{"frame.number": [5]}, {"frame.number": [900]}], х.поля_по([4, 899]))
        self.assertEqual((bytes([77]) * 28, "Ethernet"), х.кадр(77))
        self.assertEqual((bytes([78]) * 29, "IP"), х.кадр(78))
        self.assertEqual(list(range(1000)), х.времена().astype(int).tolist())
        self.assertEqual([i % 7 for i in range(1000)], х.потоки().tolist())
        ряд = Ряд(х, "сводки", [3, 500, 999])
        self.assertEqual([4, 501, 1000], [с_["номер"] for с_ in ряд])
        self.assertEqual(3, len(ряд))
        self.assertEqual(1000, len(Ряд(х, "кадры")))
        self.assertEqual([bytes([5])], нагрузки(х, [{"нагр": [0, 1], "номер": 6}], [bytes([5]) * 6]))
        # Копии верхнего уровня: правка выданной сводки не портит кэш блоков.
        х.сводки(0, 1)[0]["номер"] = -1
        self.assertEqual(1, х.сводки(0, 1)[0]["номер"])

    def test_отпечаток_потока(self):
        а = отпечаток_потока(("TCP", ("10.0.0.1", 80), ("10.0.0.2", 5000)))
        self.assertEqual(а, отпечаток_потока(("TCP", ("10.0.0.1", 80), ("10.0.0.2", 5000))))
        self.assertNotEqual(а, отпечаток_потока(("UDP", ("10.0.0.1", 80), ("10.0.0.2", 5000))))
        self.assertEqual(0, отпечаток_потока(None))
        self.assertGreater(а, 0)
        self.assertLess(а, 1 << 64)


class НакопительTests(unittest.TestCase):
    """Статистика по ходу — та же, что у statistika над всеми сводками сразу."""

    @classmethod
    def setUpClass(cls):
        кадры = ос.захват_всех_видов() + смесь(70)
        cls.сводки, _, _ = ос.разобрать(кадры, времена(len(кадры)))
        from reportgen.setevoy.razbor import разобрать_пакет
        cls.поля = []
        for i, к in enumerate(кадры, start=1):
            п = разобрать_пакет(к, "Ethernet", номер=i, время=времена(len(кадры))[i - 1])
            cls.поля.append(_для_фильтра(п.поля_фильтра()))
        cls.н = Накопитель()
        for с_, п in zip(cls.сводки, cls.поля, strict=True):
            cls.н.добавить(с_, п)
        cls.снимок = json.loads(json.dumps(cls.н.снимок(), default=str))

    def как_json(self, x):
        return json.loads(json.dumps(x, default=str))

    def test_дерево_диалоги_узлы_ошибки(self):
        self.assertEqual(self.как_json(statistika.иерархия(self.сводки)), self.снимок["иерархия"])
        for уровень in ("eth", "ip", "tcp", "udp"):
            with self.subTest(уровень):
                self.assertEqual(self.как_json(statistika.диалоги(self.сводки, уровень)), self.снимок["диалоги"][уровень])
        self.assertEqual(self.как_json(statistika.узлы(self.сводки)), self.снимок["узлы"])
        self.assertEqual(self.как_json(statistika.ошибки(self.сводки)), self.снимок["ошибки"])
        self.assertTrue(self.снимок["ошибки"], "в смеси есть испорченные суммы TCP")

    def test_dns_http_tls_и_обзор(self):
        for вид in ("dns", "http", "tls"):
            with self.subTest(вид):
                self.assertEqual(self.как_json(getattr(statistika, вид)(self.сводки, self.поля)), self.снимок[вид])
        полный = self.как_json(statistika.обзор(self.сводки, self.поля, [None] * len(self.сводки)))
        мой = dict(self.снимок["обзор"])
        for к in ("файлов", "файлы"):
            полный.pop(к)
        self.assertEqual(полный, мой)

    def test_узлы_дерева_как_узел_протокола(self):
        путей = set()
        for с_ in self.сводки:
            for i in range(len(с_["стек"]) + 1):
                путей.add(tuple(с_["стек"][:i]))
        for путь in sorted(путей):
            with self.subTest(путь="/".join(путь)):
                self.assertEqual(self.как_json(statistika.узел_протокола(self.сводки, list(путь))),
                                 self.как_json(узел_из_снимка(self.снимок["узлы_дерева"], list(путь))))
        self.assertEqual({"направления": [], "диалогов": 0, "узлов": 0, "первый": None, "последний": None},
                         узел_из_снимка({}, ["Нет"]))

    def test_неизвестные_группы(self):
        нагрузки_ = []
        for с_ in self.сводки:
            нагрузки_.append(b"\x01" * с_["нагр"][1] if с_.get("нагр") else None)
        эталон = statistika.неизвестные(self.сводки, нагрузки_)
        мои = self.снимок["неизвестные"]
        self.assertEqual([(г["группа"], г["пакетов"], г["первые"], г["длины"]) for г in эталон],
                         [(г["группа"], г["пакетов"], г["первые"], г["длины"]) for г in мои])

    def test_потолки_честно_отмечены(self):
        from reportgen.setevoy import nakopitel
        старое = nakopitel.ДИАЛОГОВ_ДО
        nakopitel.ДИАЛОГОВ_ДО = 2
        self.addCleanup(setattr, nakopitel, "ДИАЛОГОВ_ДО", старое)
        н = Накопитель()
        for с_, п in zip(self.сводки, self.поля, strict=True):
            н.добавить(с_, п)
        счёт = н.раздел("счёт")
        self.assertEqual(2, счёт["диалогов"]["ip"])
        self.assertGreater(счёт["диалогов_мимо"]["ip"], 0)
        self.assertEqual(len(self.сводки), счёт["пакетов"], "числа пакетов точные и сверх потолка")


class ЗахватыПотокомTests(unittest.TestCase):
    """Захваты: разбор в фоне (потоком и процессом), загрузка кусками, отборы следом за разбором."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.папка = Path(self._tmp.name)
        self.з = Захваты(self.папка / "pakety", процессы=False)
        self.addCleanup(self.з.закрыть)
        кадры = смесь(140)
        self.данные = с.pcap(кадры, времена=времена(len(кадры)))
        self.кадры = кадры

    def test_загрузка_кусками_равна_разбору_целиком(self):
        целиком = self.з.создать(владелец=1, имя="a.pcap", данные=self.данные)
        кусками = self.з.создать(владелец=1, имя="b.pcap", загрузка=len(self.данные))
        for от in range(0, len(self.данные), 997):
            self.assertEqual(min(len(self.данные), от + 997), self.з.дописать(кусками, от, self.данные[от:от + 997]))
            if от == 997:
                with self.assertRaises(ОшибкаЗагрузки):
                    self.з.дописать(кусками, 5000, b"x")            # не с того места
                with self.assertRaises(ОшибкаЗагрузки):
                    self.з.дописать(кусками, 5, b"\xee\xee")       # «повтор» с другими байтами
                self.assertEqual(1994, self.з.дописать(кусками, 0, self.данные[:997]), "повтор принятого — не ошибка")
            time.sleep(0.005)
        self.з.закончить_загрузку(кусками)
        а, б = self.з.дождаться(целиком), self.з.дождаться(кусками)
        self.assertEqual(("готово", "готово", 140, 140), (а["состояние"], б["состояние"], а["разобрано"], б["разобрано"]))
        self.assertEqual(list(self.з.сводки(целиком)), list(self.з.сводки(кусками)))
        self.assertEqual(self.з.отбор(целиком, "").снимок("иерархия"), self.з.отбор(кусками, "").снимок("иерархия"))
        self.assertEqual(self.з.отобрать(целиком, "dns"), self.з.отобрать(кусками, "dns"))
        # Итог равен пакетному разбору всего файла (statistika над всеми сводками).
        сводки = list(self.з.сводки(кусками))
        self.assertEqual(json.loads(json.dumps(statistika.иерархия(сводки))), self.з.отбор(кусками, "").снимок("иерархия"))
        self.assertEqual(statistika.по_времени(сводки), vydacha.по_времени(self.з.отбор(кусками, "")))
        отбор = self.з.отбор(кусками, "tcp", ждать=10)
        tcp = [сводки[i] for i in self.з.отобрать(кусками, "tcp")]
        self.assertEqual(statistika.по_времени(tcp), vydacha.по_времени(отбор))
        self.assertEqual(json.loads(json.dumps(statistika.диалоги(tcp, "tcp"))), отбор.снимок("диалоги")["tcp"])

    def test_отбор_идёт_следом_за_разбором(self):
        ид = self.з.создать(владелец=1, имя="a.pcap", загрузка=len(self.данные))
        половина = len(self.данные) // 2
        self.з.дописать(ид, 0, self.данные[:половина])
        self.assertTrue(дождаться(lambda: self.з.хранилище(ид).число() > 10))
        отбор = self.з.отбор(ид, "dns")
        self.assertTrue(дождаться(lambda: отбор.ход()["проверено"] >= self.з.хранилище(ид).число() > 0))
        self.assertFalse(отбор.ход()["готово"], "разбор не кончился — отбор не готов")
        self.з.дописать(ид, половина, self.данные[половина:])
        self.з.закончить_загрузку(ид)
        self.з.дождаться(ид)
        self.assertTrue(дождаться(lambda: отбор.ход()["готово"]))
        все = list(self.з.поля(ид))
        self.assertEqual([i for i, п in enumerate(все) if "dns" in п], отбор.номера().tolist())
        self.assertEqual(отбор.номера().tolist().index(14), отбор.место(15))
        self.assertIsNone(отбор.место(3))
        self.assertEqual(9, self.з.отбор(ид, "").место(10))

    def test_стоп_пауза_удаление_посреди_разбора(self):
        ид = self.з.создать(владелец=1, имя="a.pcap", загрузка=len(self.данные))
        self.з.дописать(ид, 0, self.данные[:3000])
        self.assertTrue(дождаться(lambda: self.з.прочитать(ид)["состояние"] == "ждёт данных"))
        self.assertTrue(self.з.команда(ид, "пауза"))
        self.assertTrue(дождаться(lambda: self.з.прочитать(ид)["состояние"] == "пауза"))
        self.assertTrue(self.з.команда(ид, "продолжить"))
        self.assertTrue(дождаться(lambda: self.з.прочитать(ид)["состояние"] != "пауза"))
        разобрано = self.з.хранилище(ид).число()
        self.з.команда(ид, "стоп")
        с_ = self.з.прочитать(ид)
        self.assertEqual("остановлен", с_["состояние"])
        self.assertTrue(с_["загрузка"]["прервана"])
        self.assertEqual(разобрано, self.з.хранилище(ид).число(), "разобранное до стопа осталось")
        with self.assertRaises(ОшибкаЗагрузки):
            self.з.дописать(ид, 3000, self.данные[3000:4000])
        второй = self.з.создать(владелец=1, имя="b.pcap", загрузка=len(self.данные))
        self.з.дописать(второй, 0, self.данные[:3000])
        self.з.удалить(второй)
        self.assertFalse((self.папка / "pakety" / второй).exists())

    def test_загрузка_после_долгого_перерыва_разбирается_заново(self):
        from reportgen.setevoy import fon
        старое = fon.ЖДАТЬ_ЗАГРУЗКУ
        fon.ЖДАТЬ_ЗАГРУЗКУ = 0.3
        self.addCleanup(setattr, fon, "ЖДАТЬ_ЗАГРУЗКУ", старое)
        ид = self.з.создать(владелец=1, имя="a.pcap", загрузка=len(self.данные))
        self.з.дописать(ид, 0, self.данные[:3000])
        self.assertTrue(дождаться(lambda: self.з.прочитать(ид)["состояние"] == "прерван"))
        self.assertIn("загрузка прервалась", self.з.прочитать(ид)["ошибка"])
        fon.ЖДАТЬ_ЗАГРУЗКУ = старое
        self.з.дописать(ид, 3000, self.данные[3000:])
        self.з.закончить_загрузку(ид)
        с_ = self.з.дождаться(ид)
        self.assertEqual(("готово", 140), (с_["состояние"], с_["разобрано"]))

    def test_оборванный_хвост_файла(self):
        ид = self.з.создать(владелец=1, имя="t.pcap", данные=self.данные[:-11])
        с_ = self.з.дождаться(ид)
        self.assertEqual(("готово", 139), (с_["состояние"], с_["разобрано"]))
        self.assertIn("файл оборван: последний пакет записан не целиком", с_["заметки"])

    def test_разбор_процессом(self):
        з = Захваты(self.папка / "p2", процессы=True, процессом_от=0)
        self.addCleanup(з.закрыть)
        ид = з.создать(владелец=1, имя="a.pcap", данные=self.данные)
        с_ = з.дождаться(ид, 120)
        self.assertEqual(("готово", 140), (с_["состояние"], с_["разобрано"]))
        self.assertEqual([i for i, п in enumerate(з.поля(ид)) if "dns" in п], з.отобрать(ид, "dns"))

    def test_генератор_большого_файла_сверка_счёта(self):
        путь = self.папка / "g.pcap"
        пакетов, хэш = записать_генератором(путь, 1, зерно=3)
        import hashlib
        self.assertEqual(хэш, hashlib.sha256(путь.read_bytes()).hexdigest())
        ид = self.з.создать(владелец=1, имя="g.pcap", путь=путь)
        с_ = self.з.дождаться(ид, 300)
        self.assertEqual(("готово", пакетов), (с_["состояние"], с_["разобрано"]))
        self.assertEqual(пакетов, self.з.отбор(ид, "").снимок("счёт")["пакетов"])
        _, обрыв = записать_генератором(self.папка / "o.pcap", 1, зерно=3, обрыв=True)
        ид = self.з.создать(владелец=1, имя="o.pcap", путь=self.папка / "o.pcap")
        self.assertEqual(пакетов, self.з.дождаться(ид, 300)["разобрано"])

    def test_выгрузки_потоком_совпадают_с_исходником(self):
        ид = self.з.создать(владелец=1, имя="a.pcap", данные=self.данные)
        self.з.дождаться(ид)
        выгрузка = self.з.выгрузить_pcap(ид, list(range(140)))
        self.assertEqual(self.кадры, [з.данные for з in прочитать_захват(данные=выгрузка).записи])
        ng = self.з.выгрузить_pcapng(ид, [0, 5, 139])
        self.assertEqual([self.кадры[0], self.кадры[5], self.кадры[139]], [з.данные for з in прочитать_захват(данные=ng).записи])


class ОчередьTests(unittest.TestCase):
    """Диспетчер: места по видам, честно между людьми, ждущие данных места не занимают."""

    def test_честная_очередь(self):
        from reportgen.setevoy import raboty
        порядок, ворота = [], {}
        замок = threading.Lock()

        def исполнить(вид, аргументы, команды):
            with замок:
                порядок.append(аргументы[0])
            ворота[аргументы[0]].wait(10)

        старое = raboty._исполнить
        raboty._исполнить = исполнить
        self.addCleanup(setattr, raboty, "_исполнить", старое)
        д = Диспетчер(разборов=1, отборов=1, процессы=False)
        self.addCleanup(д.закрыть)
        for ключ, владелец in (("а1", 1), ("а2", 1), ("а3", 1), ("б1", 2)):
            ворота[ключ] = threading.Event()
            д.поставить(Работа("разбор", ключ, владелец, [ключ], процессом=False))
        self.assertTrue(дождаться(lambda: порядок == ["а1"]))
        self.assertEqual(2, д.в_очереди("а3"))
        ворота["а1"].set()
        self.assertTrue(дождаться(lambda: len(порядок) == 2))
        self.assertEqual("б1", порядок[1], "второй человек не ждёт все файлы первого")
        for ключ in ("б1", "а2", "а3"):
            ворота[ключ].set()
            time.sleep(0.05)
        self.assertTrue(дождаться(lambda: len(порядок) == 4))
        self.assertEqual(["а1", "б1", "а2", "а3"], порядок)

    def test_спящая_работа_места_не_занимает(self):
        from reportgen.setevoy import raboty
        запущено, ворота = [], threading.Event()

        def исполнить(вид, аргументы, команды):
            запущено.append(аргументы[0])
            ворота.wait(10)

        старое = raboty._исполнить
        raboty._исполнить = исполнить
        self.addCleanup(setattr, raboty, "_исполнить", старое)
        self.addCleanup(ворота.set)
        with tempfile.TemporaryDirectory() as t:
            ход = Path(t) / "ход.json"
            ход.write_text(json.dumps({"состояние": "ждёт данных"}), encoding="utf-8")
            д = Диспетчер(разборов=1, процессы=False)
            self.addCleanup(д.закрыть)
            д.поставить(Работа("разбор", "спит", 1, ["спит"], процессом=False, ход_файл=ход))
            д.поставить(Работа("разбор", "второй", 2, ["второй"], процессом=False))
            self.assertTrue(дождаться(lambda: запущено == ["спит", "второй"]))


class СтраницаЧерезСерверTests(unittest.TestCase):
    """API: загрузка кусками, ход, управление, место пакета, файлы по ссылке и их защита."""

    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.к = self.сеть.client
        self.сеть.login("engineer")
        self.данные = с.pcap(смесь(70), времена=времена(70))

    def дождаться_записи(self, ид):
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/pakety/{ид}").json()["состояние"] in ("готово", "ошибка")))
        return self.к.get(f"/api/pakety/{ид}").json()

    def test_загрузка_кусками_через_сервер(self):
        ответ = self.к.post("/api/pakety/upload", json={"name": "k.pcap", "size": len(self.данные)})
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид, кусок = ответ.json()["id"], ответ.json()["chunk"]
        self.assertGreater(кусок, 0)
        мест = list(range(0, len(self.данные), 1500))
        for от in мест:
            р = self.к.put(f"/api/pakety/{ид}/chunk?offset={от}", content=self.данные[от:от + 1500],
                           headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(200, р.status_code, р.text)
            if от == 0:
                self.assertIn(self.к.get(f"/api/pakety/{ид}").json()["состояние"], ("в очереди", "ждёт", "идёт", "ждёт данных"))
        плохо = self.к.put(f"/api/pakety/{ид}/chunk?offset=7", content=b"xx", headers={"Content-Type": "application/octet-stream"})
        self.assertEqual((409, len(self.данные)), (плохо.status_code, плохо.json()["received"]))
        self.assertEqual(200, self.к.post(f"/api/pakety/{ид}/upload-done", json={}).status_code)
        с_ = self.дождаться_записи(ид)
        self.assertEqual(("готово", 70, False), (с_["состояние"], с_["разобрано"], с_["загрузка"]["идёт"]))
        список = self.к.get(f"/api/pakety/{ид}/list?filter=dns&limit=3").json()
        self.assertEqual((True, 70, 3), (список["готово"], список["всего"], len(список["items"])))
        номер = список["items"][1]["номер"]
        self.assertEqual(1, self.к.get(f"/api/pakety/{ид}/position?number={номер}&filter=dns").json()["место"])
        self.assertIsNone(self.к.get(f"/api/pakety/{ид}/position?number=3&filter=dns").json()["место"])
        self.assertEqual(200, self.к.get(f"/api/pakety/{ид}/stats?kind=protocol&path=Ethernet/IPv4").status_code)
        self.assertIn("ход", self.к.get(f"/api/pakety/{ид}/stats?kind=hierarchy").json())

    def test_не_захват_сразу_отказ_и_запись_удалена(self):
        ид = self.к.post("/api/pakety/upload", json={"name": "x.pcap", "size": 400}).json()["id"]
        р = self.к.put(f"/api/pakety/{ид}/chunk?offset=0", content=b"not a capture at all" * 20,
                       headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(400, р.status_code)
        self.assertIn("не похож на сетевую запись", р.json()["error"])
        self.assertEqual(404, self.к.get(f"/api/pakety/{ид}").status_code)
        self.assertEqual(400, self.к.post("/api/pakety/upload", json={"name": "x.pcap", "size": 0}).status_code)

    def test_управление_и_чужая_запись(self):
        ид = self.к.post("/api/pakety/upload", json={"name": "k.pcap", "size": len(self.данные)}).json()["id"]
        self.к.put(f"/api/pakety/{ид}/chunk?offset=0", content=self.данные[:2000],
                   headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(400, self.к.post(f"/api/pakety/{ид}/control", json={"action": "взорвать"}).status_code)
        с_ = self.к.post(f"/api/pakety/{ид}/control", json={"action": "stop"}).json()
        self.assertEqual("остановлен", с_["состояние"])
        self.сеть.login("gruppa")
        self.assertEqual(404, self.к.get(f"/api/pakety/{ид}").status_code)
        self.assertEqual(404, self.к.post(f"/api/pakety/{ид}/control", json={"action": "stop"}).status_code)

    def test_файл_по_ссылке(self):
        vhod = self.сеть.tmp / "data" / "vhod"
        корни = self.к.get("/api/files/roots").json()["items"]
        self.assertEqual(1, len(корни))
        корень = корни[0]["id"]
        vhod = next(p for p in self.сеть.tmp.rglob("vhod") if p.is_dir())
        (vhod / "тест").mkdir()
        (vhod / "тест" / "a.pcap").write_bytes(self.данные)
        (vhod / "шум.txt").write_text("x", encoding="utf-8")
        снаружи = self.сеть.tmp / "секрет.pcap"
        снаружи.write_bytes(self.данные)
        try:
            os.symlink(снаружи, vhod / "ссылка.pcap")
            есть_ссылки = True
        except OSError:
            есть_ссылки = False
        список = self.к.get(f"/api/files/list?root={корень}").json()
        self.assertEqual(["тест", "шум.txt"], [э["имя"] for э in список["элементы"]], "ссылка наружу не видна")
        найдено = self.к.get(f"/api/files/list?root={корень}&q=a.pc").json()["элементы"]
        self.assertEqual([("тест/a.pcap", len(self.данные))], [(э["путь"], э["размер"]) for э in найдено])
        for плохой in ("../секрет.pcap", "/etc/passwd", "тест/../../секрет.pcap", "C:/x", "тест/\x00"):
            with self.subTest(плохой):
                self.assertIn(self.к.post("/api/pakety/from-file", json={"root": корень, "path": плохой}).status_code, (400, 404))
        if есть_ссылки:
            self.assertEqual(400, self.к.post("/api/pakety/from-file", json={"root": корень, "path": "ссылка.pcap"}).status_code)
        self.assertEqual(404, self.к.post("/api/pakety/from-file", json={"root": "нет", "path": "тест/a.pcap"}).status_code)
        ид = self.к.post("/api/pakety/from-file", json={"root": корень, "path": "тест/a.pcap"}).json()["id"]
        с_ = self.дождаться_записи(ид)
        self.assertEqual(("готово", 70, "ссылка"), (с_["состояние"], с_["разобрано"], с_["источник"]["вид"]))
        папка_записи = next(p for p in self.сеть.tmp.rglob(ид) if p.is_dir())
        self.assertFalse((папка_записи / "исходник").exists(), "по ссылке — без копии")
        self.assertEqual(200, self.к.get(f"/api/pakety/{ид}/packet/3").status_code)
        # Файл на сервере поменяли — понятный отказ, а не чужие байты.
        time.sleep(1.1)
        with open(vhod / "тест" / "a.pcap", "ab") as ф:
            ф.write(b"\0" * 16)
        ответ = self.к.get(f"/api/pakety/{ид}/packet/4")
        self.assertEqual(409, ответ.status_code)
        self.assertIn("изменён на сервере после открытия", ответ.json()["error"])
        self.assertEqual(200, self.к.get(f"/api/pakety/{ид}/list").status_code, "список (сводки) — свой, на диске записи")
        self.assertTrue((vhod / "тест" / "a.pcap").exists())
        self.assertEqual(200, self.к.delete(f"/api/pakety/{ид}").status_code)
        self.assertTrue((vhod / "тест" / "a.pcap").exists(), "удаление записи не трогает файл сервера")

    def test_папки_по_должности(self):
        from reportgen import fayly_ssylki
        старые = self.сеть.app.state.settings.input_dirs
        a, b = self.сеть.tmp / "A", self.сеть.tmp / "B"
        a.mkdir()
        b.mkdir()
        self.сеть.app.state.settings.input_dirs = [str(a), {"имя": "Начальству", "путь": str(b), "роль": "head"}]
        self.addCleanup(setattr, self.сеть.app.state.settings, "input_dirs", старые)
        self.assertEqual(["A"], [к["имя"] for к in self.к.get("/api/files/roots").json()["items"]])
        чужая = fayly_ssylki.папки(self.сеть.app.state.settings)[1].ид
        self.assertEqual(404, self.к.get(f"/api/files/list?root={чужая}").status_code)
        self.сеть.login("nachalnik")
        self.assertEqual(["A", "Начальству"], [к["имя"] for к in self.к.get("/api/files/roots").json()["items"]])


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ФункцииСтраницыTests(unittest.TestCase):
    """Виртуальный список, куски загрузки, ход разбора — функции app.js в node."""

    def выполнить(self, код_проверки: str):
        код = функции_js(["кускиФайла", "fmtОсталось", "ходРазбора", "окноСписка", "страницыСписка", "fmtBytes", "fmtNumber"], [])
        код += "\nconst итог = (() => {" + код_проверки + "})();\nprocess.stdout.write(JSON.stringify(итог));"
        готово = subprocess.run(["node", "-e", код], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, готово.returncode, готово.stderr)
        return json.loads(готово.stdout)

    def test_куски_файла(self):
        self.assertEqual([[[0, 4], [4, 8], [8, 10]], [], [[0, 1]], [[0, 5]]], self.выполнить(
            "return [кускиФайла(10, 4), кускиФайла(0, 4), кускиФайла(1, 4), кускиФайла(5, 5)];"))

    def test_окно_списка_малое_и_огромное(self):
        малое, сдвиг, огромное, конец, пусто = self.выполнить("""
            return [окноСписка(240, 480, 1000, 24, 0), окноСписка(250, 480, 1000, 24, 2),
                    окноСписка(0, 480, 10000000, 24, 0), окноСписка(8000000 - 480, 480, 10000000, 24, 0),
                    окноСписка(0, 480, 0, 24, 3)];""")
        self.assertEqual({"первая": 10, "видимая": 10, "последняя": 31, "прокрутка": 24000, "сдвиг": 0}, малое)
        self.assertEqual((8, 10, 10 + 2 * 24), (сдвиг["первая"], сдвиг["видимая"], сдвиг["сдвиг"]))
        self.assertEqual((0, 8000000), (огромное["видимая"], огромное["прокрутка"]))
        self.assertEqual(10000000, конец["последняя"], "конец прокрутки — последние строки")
        self.assertEqual((0, 0), (пусто["первая"], пусто["последняя"]))

    def test_страницы_и_ход(self):
        страницы, пусто, текст, очередь, ждём, осталось = self.выполнить("""
            return [страницыСписка(150, 450, 200), страницыСписка(5, 5, 200),
                ходРазбора({состояние: 'идёт', разобрано: 1234567, прочитано: 536870912, всего_байт: 1073741824,
                            скорость_пакетов: 12345.6, скорость_байт: 8912896, осталось: 62}),
                ходРазбора({состояние: 'в очереди', очередь: 2, разобрано: 0, байт: 0}),
                ходРазбора({состояние: 'ждёт данных', разобрано: 5, загрузка: {идёт: true, всего: 100}, прочитано: 50}),
                [fmtОсталось(5.4), fmtОсталось(65), fmtОсталось(7385), fmtОсталось(null), fmtОсталось(-1)]];""")
        self.assertEqual([[0, 1, 2], []], [страницы, пусто])
        self.assertIn("512,0 МБ из 1,0 ГБ (50 %)", текст)
        self.assertIn("осталось ≈ 1 мин 02 с", текст)
        self.assertIn("пакетов/с", текст)
        self.assertTrue(очередь.startswith("в очереди (2-й)"))
        self.assertIn("ждём следующий кусок файла", ждём)
        self.assertEqual(["≈ 5 с", "≈ 1 мин 05 с", "≈ 2 ч 03 мин", "", ""], осталось)


if __name__ == "__main__":
    unittest.main()
