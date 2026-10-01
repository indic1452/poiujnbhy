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
        elif вид == 5 and i % 3 == 0:
            кадры.append(с.eth(с.ip(с.udp(с.dns_запрос(f"m{i}.local", ид=i), 5353 if i % 2 else 5355,
                                          5353 if i % 2 else 5355), 17)))
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

    def test_разреженные_номера_и_ряд(self):
        from reportgen.setevoy import hranilishe
        п = Писатель(self.папка, копировать=False)
        исходник = bytearray()
        for i in range(300):
            кадр = bytes([i % 256]) * 3
            п.добавить(данные=кадр, место=len(исходник), канал="Ethernet", сводка={"номер": i + 1, "нагр": None},
                       поля={"i": [i]}, время=i * 0.5, длина=3, поток=0)
            исходник += кадр
        п.закрыть()
        (self.папка / "исходник").write_bytes(bytes(исходник))
        х = Хранилище(self.папка)
        self.assertEqual(300, х.число())
        self.assertEqual([bytes([7]) * 3, bytes([299 % 256]) * 3], х.кадры_по([7, 299]))
        # Номера далеко друг от друга — записи читаются по одной (не одним куском между ними).
        старое = hranilishe.ЗАПИСЬ
        self.assertEqual([1, 300], [з["номер"] for з in х.сводки_по(np.array([0, 299]))])
        self.assertEqual(старое.size, 48)
        self.assertEqual(0, len(х.записи(5, 5)))
        self.assertEqual(0, len(х.записи_по([])))
        self.assertEqual([0.0, 0.5], х.времена(0, 2).tolist())
        self.assertEqual([3, 3], х.длины(298, 400).tolist())
        with self.assertRaises(IndexError):
            х.сводка(300)
        with self.assertRaises(IndexError):
            х.кадр(300)
        ряд = Ряд(х, "поля")
        self.assertEqual([{"i": [299]}, {"i": [0]}], [ряд[-1], ряд[0]])
        self.assertEqual([{"i": [0]}, {"i": [2]}], ряд[0:4:2])
        self.assertEqual(300, len(ряд[:]))
        with self.assertRaises(IndexError):
            ряд[300]
        self.assertEqual(Ряд(х, "кадры", [1, 2]), [bytes([1]) * 3, bytes([2]) * 3])
        self.assertNotEqual(Ряд(х, "кадры", [1, 2]), [bytes([1]) * 3])
        Ряд.ПОРЦИЯ, старое_п = 7, Ряд.ПОРЦИЯ
        self.addCleanup(setattr, Ряд, "ПОРЦИЯ", старое_п)
        self.assertEqual([з["i"][0] for з in Ряд(х, "поля")], list(range(300)))
        self.assertEqual([з["i"][0] for з in Ряд(х, "поля")[5:20]], list(range(5, 20)))
        self.assertEqual(Ряд(х, "поля")[13], {"i": [13]})
        # Файл по ссылке изменили — кадры не читаются, сводки — читаются.
        from reportgen.fayly_ssylki import ФайлИзменён, отпечаток
        чужой = Хранилище(self.папка, self.папка / "исходник", {**отпечаток(self.папка / "исходник"), "размер": 1})
        with self.assertRaises(ФайлИзменён):
            чужой.кадр(1)
        self.assertEqual(2, чужой.сводка(1)["номер"])

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
        cls.кадры = кадры
        cls.н = Накопитель()
        for с_, п, к in zip(cls.сводки, cls.поля, кадры, strict=True):
            cls.н.добавить(с_, п, len(к))
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

    def test_счёт(self):
        счёт = self.снимок["счёт"]
        сводки = self.сводки
        self.assertEqual((len(сводки), sum(с_["длина"] for с_ in сводки)), (счёт["пакетов"], счёт["байт"]))
        self.assertEqual(max(len(к) for к in self.кадры), счёт["макс_кадр"])
        self.assertEqual(max(с_["нагр"][1] for с_ in сводки if с_.get("нагр")), счёт["макс_нагрузка"])
        self.assertEqual((len(statistika.dns(сводки, self.поля)), len(statistika.http(сводки, self.поля)),
                          len(statistika.tls(сводки, self.поля))), (счёт["dns"], счёт["http"], счёт["tls"]))
        self.assertTrue({"mDNS", "LLMNR"} <= {п for с_ in сводки for п in с_["стек"]}, "в смеси есть mDNS и LLMNR")
        self.assertEqual(sum(1 for с_ in сводки if с_["ошибки"]), счёт["с_ошибками"])
        tcp = [с_ for с_ in сводки if "TCP" in с_["стек"]]
        self.assertEqual((len(tcp), sum(1 for с_ in tcp if any(о.startswith("TCP: контрольная") for о in с_["ошибки"]))),
                         (счёт["tcp"], счёт["tcp_суммы"]))
        self.assertGreater(счёт["tcp_суммы"], 0)
        # Потоки — как «Выходные данные» считали прежде (по всем сводкам).
        потоков = len({("TCP" in с_["стек"], *sorted(((с_["источник"], с_["порт_от"]), (с_["получатель"], с_["порт_к"]))))
                      for с_ in сводки if с_.get("порт_от") is not None and ({"TCP", "UDP"} & set(с_["стек"]))})
        self.assertEqual(потоков, счёт["потоков"])
        пар = {(путь, tuple(sorted((с_["источник"], с_["получатель"]))))
               for с_ in сводки if с_["источник"] and с_["получатель"]
               for путь in [tuple(с_["стек"][:i]) for i in range(len(с_["стек"]) + 1)]}
        self.assertEqual(len(пар), счёт["пар"])
        узлов = {tuple(с_["стек"][:i]) for с_ in сводки for i in range(1, len(с_["стек"]) + 1)}
        self.assertEqual(len(узлов) + 1, счёт["узлов_дерева"], "узлы дерева и корень")
        self.assertEqual((0, {}, []), (счёт["узлов_мимо"], счёт["диалогов_мимо"], счёт["неполно"]))
        # Примета «суммы TCP» — когда у большинства TCP сумма не сошлась.
        плохие = Накопитель()
        for с_, п in zip(сводки, self.поля, strict=True):
            if any(о.startswith("TCP: контрольная") for о in с_["ошибки"]):
                плохие.добавить(с_, п)
        self.assertTrue(any("сумма" in п["что"] for п in плохие.раздел("обзор")["приметы"]))
        self.assertFalse(any("сумма" in п["что"] for п in self.снимок["обзор"]["приметы"]))

    def test_потолки_точно_на_границе(self):
        from reportgen.setevoy import nakopitel
        for имя, значение in (("УЗЛОВ_ДЕРЕВА_ДО", 4), ("ПАР_ВСЕГО_ДО", 5), ("УЗЛОВ_ДО", 3), ("ЖДУТ_HTTP_ДО", 2)):
            старое = getattr(nakopitel, имя)
            setattr(nakopitel, имя, значение)
            self.addCleanup(setattr, nakopitel, имя, старое)
        н = Накопитель()
        for с_, п in zip(self.сводки, self.поля, strict=True):
            н.добавить(с_, п)
        счёт = н.раздел("счёт")
        self.assertEqual((5, 3), (счёт["пар"], счёт["узлов"]))

        def обойти(у):
            yield у
            for д in у["дети"]:
                yield from обойти(д)
        узлы = list(обойти(н.раздел("иерархия")[0]))
        self.assertEqual(4, sum(1 for у in узлы if у["протокол"] != "…прочие"), "своих узлов (с корнем) — ровно потолок")
        self.assertEqual(счёт["узлов_дерева"], len(узлы))
        self.assertGreater(счёт["узлов_мимо"], 0)
        имена = vydacha.имена_протоколов(н.раздел("иерархия"))
        self.assertIn("…прочие", имена)
        self.assertTrue(any("дерево протоколов" in з for з in счёт["неполно"]))
        self.assertTrue(any("HTTP" in з for з in счёт["неполно"]), "ждущих ответа HTTP больше потолка")
        # Ждущих ответа HTTP — не больше потолка: сверх него запросы не ждут, и их ответ не сопоставится.
        ждут: dict = {}
        ждёт, совпало = 0, 0
        for с_, п in zip(self.сводки, self.поля, strict=True):
            if "HTTP" not in с_["стек"]:
                continue
            if "http.request.method" in п:
                if ждёт < 2:
                    ждут.setdefault((с_["источник"], с_.get("порт_от"), с_["получатель"], с_.get("порт_к")), []).append(1)
                    ждёт += 1
            elif "http.response.code" in п:
                обратный = (с_["получатель"], с_.get("порт_к"), с_["источник"], с_.get("порт_от"))
                if ждут.get(обратный):
                    ждут[обратный].pop()
                    ждёт -= 1
                    совпало += 1
        self.assertGreater(совпало, 0)
        self.assertEqual(совпало, sum(1 for з in н.раздел("http") if з["код"] != ""))

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


class НакопительСлучайноTests(unittest.TestCase):
    """Сводки-заготовки с краевыми случаями (пустые адреса, равные времена, пустая нагрузка, свои пакеты на себя)
    — накопитель против statistika на сотнях случайных наборов."""

    def сводки(self, г, n):
        стеки = [["Ethernet", "IPv4", "UDP", "Данные"], ["Ethernet", "IPv4", "TCP", "HTTP"], ["Ethernet", "ARP"],
                 ["Ethernet", "IPv6", "UDP", "DNS"], ["IP", "Данные"], ["Ethernet", "IPv4", "TCP", "Данные"], ["Данные"]]
        адреса = ["", "10.0.0.1", "10.0.0.2", "10.0.0.3"]
        итог = []
        for i in range(n):
            стек = list(г.choice(стеки))
            с_ = {"номер": i + 1, "время": float(г.choice([1, 1, 2, 3, 5, 8])), "длина": г.randrange(0, 1500),
                  "стек": стек, "протокол": стек[-1] if стек else "?", "источник": г.choice(адреса),
                  "получатель": г.choice(адреса), "инфо": "", "ошибки": г.choice([[], [], ["TCP: контрольная сумма x"],
                                                                                 ["IPv4: обрыв (5)"]])}
            if "TCP" in стек or "UDP" in стек:
                с_["порт_от"], с_["порт_к"] = г.choice([(80, 5000), (5000, 80), (53, 53), (None, None)])
            else:
                с_["порт_от"] = с_["порт_к"] = None
            if г.random() < 0.3:
                с_["mac"] = [г.choice(["aa", "bb"]), г.choice(["aa", "cc"])]
            нагр = г.choice([None, [0, 0], [10, 5], [0, 7]])
            if нагр is not None:
                с_["нагр"] = нагр
            итог.append(с_)
        return итог

    def test_как_statistika(self):
        import random
        г = random.Random(5)
        for k in range(150):
            сводки = self.сводки(г, г.randrange(0, 40))
            н = Накопитель()
            for с_ in сводки:
                н.добавить(с_, {})
            с = json.loads(json.dumps(н.снимок()))
            with self.subTest(k=k):
                self.assertEqual(json.loads(json.dumps(statistika.иерархия(сводки))), с["иерархия"])
                for уровень in ("eth", "ip", "tcp", "udp"):
                    self.assertEqual(json.loads(json.dumps(statistika.диалоги(сводки, уровень))), с["диалоги"][уровень])
                self.assertEqual(json.loads(json.dumps(statistika.узлы(сводки))), с["узлы"])
                self.assertEqual(statistika.ошибки(сводки), с["ошибки"])
                пути = {tuple(с_["стек"][:i]) for с_ in сводки for i in range(len(с_["стек"]) + 1)} | {()}
                for путь in пути:
                    self.assertEqual(json.loads(json.dumps(statistika.узел_протокола(сводки, list(путь)))),
                                     узел_из_снимка(с["узлы_дерева"], list(путь)))
                груз = [b"x" * с_["нагр"][1] if с_.get("нагр") else None for с_ in сводки]
                эталон = statistika.неизвестные(сводки, груз)
                self.assertEqual([(г_["группа"], г_["пакетов"], г_["первые"], г_["длины"]) for г_ in эталон],
                                 [(г_["группа"], г_["пакетов"], г_["первые"], г_["длины"]) for г_ in с["неизвестные"]])


class НакопительБогатоTests(unittest.TestCase):
    """Много разных имён, узлов, диалогов, групп и ошибок (сверх «первых N» снимка), поля DNS/HTTP/TLS,
    дробные времена — накопитель против statistika по всем разделам, включая обзор."""

    def набор(self, г, n):
        адреса = [f"10.0.{i // 50}.{i % 50}" for i in range(40)] + [""]
        сводки, поля = [], []
        for i in range(n):
            вид = г.randrange(7)
            а, б = г.choice(адреса), г.choice(адреса)
            порты = (г.choice([53, 80, 443, 1023, 1024, 1025, 40000 + г.randrange(30)]), г.choice([53, 80, 1024, 5000]))
            п: dict = {}
            if вид == 0:
                стек = ["Ethernet", "IPv4", "UDP", г.choice(["DNS", "mDNS", "LLMNR"])]
                ответ = г.random() < 0.5
                п = {"dns.flags.response": [int(ответ)], "dns.qry.name": [f"n{г.randrange(15)}.ru"], "dns.qry.type": ["A"],
                     "dns.id": [г.randrange(5)], **({"dns.a": ["1.2.3.4"], "dns.flags.rcode": [г.randrange(3)]} if ответ else {})}
                if г.random() < 0.2:
                    п = {k: v for k, v in п.items() if k != "dns.flags.response"}
            elif вид == 1:
                стек = ["Ethernet", "IPv4", "TCP", "HTTP"]
                if г.random() < 0.6:
                    п = {"http.request.method": ["GET"], "http.host": [f"h{г.randrange(14)}"], "http.request.uri": ["/"],
                         **({"http.user_agent": ["ua"]} if г.random() < 0.5 else {})}
                    if г.random() < 0.2:
                        del п["http.host"]
                else:
                    п = {"http.response.code": [г.choice([200, 404])]}
            elif вид == 2:
                стек = ["Ethernet", "IPv4", "TCP", "TLS"]
                п = {"tls.handshake.type": [г.choice(["ClientHello", "ServerHello"])]}
                for ключ in ("tls.handshake.extensions_server_name", "tls.handshake.extensions_alpn_str",
                             "tls.handshake.extensions.supported_version"):
                    if г.random() < 0.7:
                        п[ключ] = [f"s{г.randrange(14)}"]
            elif вид == 3:
                стек = ["Ethernet", "IPv4", "UDP", "Данные"]
            elif вид == 4:
                стек = [г.choice(["SLL", "Ethernet"]), "IPv6", "TCP", "Данные"]
            elif вид == 5:
                стек = ["Ethernet", "IPv4", "TCP"]
            else:
                стек = ["Ethernet", "ARP"]
            с_ = {"номер": i + 1, "время": round(1000 + г.random() * 3.3333333, 7) if г.random() < 0.9 else 1000.5,
                  "длина": г.randrange(40, 1500), "стек": стек, "протокол": стек[-1] if стек[-1] != "Данные" else стек[-2],
                  "источник": а, "получатель": б, "инфо": "",
                  "ошибки": г.choice([[], [], [], [f"TCP: контрольная сумма {г.randrange(9)} (x)"],
                                      [f"IPv{г.randrange(9)}: обрыв: ещё {г.randrange(3)} (5)"], ["без двоеточия"]])}
            if "TCP" in стек or "UDP" in стек:
                с_["порт_от"], с_["порт_к"] = порты
            else:
                с_["порт_от"] = с_["порт_к"] = None
            if "Ethernet" in стек:
                с_["mac"] = [f"aa:{г.randrange(6)}", f"bb:{г.randrange(6)}"]
            if стек[-1] == "Данные":
                с_["нагр"] = [0, г.randrange(1, 60)]
                if г.random() < 0.5:
                    с_["порт_от"] = с_.get("порт_от") or 7000 + г.randrange(40)
                    с_["порт_к"] = с_.get("порт_к") or 8000 + г.randrange(40)
            сводки.append(с_)
            поля.append(п)
        return сводки, поля

    def test_всё_как_statistika(self):
        import random
        г = random.Random(17)
        for k in range(12):
            сводки, поля = self.набор(г, г.choice([300, 900]))
            н = Накопитель()
            for с_, п in zip(сводки, поля, strict=True):
                н.добавить(с_, п)
            с = json.loads(json.dumps(н.снимок()))
            j = lambda x: json.loads(json.dumps(x))  # noqa: E731
            with self.subTest(k=k):
                self.assertEqual(j(statistika.иерархия(сводки)), с["иерархия"])
                for уровень in ("eth", "ip", "tcp", "udp"):
                    self.assertEqual(j(statistika.диалоги(сводки, уровень)), с["диалоги"][уровень])
                self.assertEqual(j(statistika.узлы(сводки)), с["узлы"])
                self.assertEqual(j(statistika.ошибки(сводки)), с["ошибки"])
                for вид in ("dns", "http", "tls"):
                    self.assertEqual(j(getattr(statistika, вид)(сводки, поля)), с[вид])
                обзор = j(statistika.обзор(сводки, поля, [None] * len(сводки)))
                обзор.pop("файлов")
                обзор.pop("файлы")
                self.assertEqual(обзор, с["обзор"])
                for путь in {tuple(с_["стек"][:i]) for с_ in сводки for i in range(len(с_["стек"]) + 1)}:
                    self.assertEqual(j(statistika.узел_протокола(сводки, list(путь))),
                                     узел_из_снимка(с["узлы_дерева"], list(путь)))
                груз = [b"x" * с_["нагр"][1] if с_.get("нагр") else None for с_ in сводки]
                эталон = statistika.неизвестные(сводки, груз)
                self.assertEqual([(г_["группа"], г_["пакетов"], г_["первые"], г_["длины"]) for г_ in эталон],
                                 [(г_["группа"], г_["пакетов"], г_["первые"], г_["длины"]) for г_ in с["неизвестные"]])
        self.assertEqual({"пакетов": 0}, Накопитель().раздел("обзор"))
        self.assertEqual(0.0, Накопитель().длительность())

    def test_флаги_неполноты_узла(self):
        from reportgen.setevoy import nakopitel
        for имя, значение in (("ПАР_УЗЛА_ДО", 3), ("АДРЕСОВ_УЗЛА_ДО", 4)):
            старое = getattr(nakopitel, имя)
            setattr(nakopitel, имя, значение)
            self.addCleanup(setattr, nakopitel, имя, старое)
        н = Накопитель()
        for i, (а, б) in enumerate([("1", "2"), ("1", "3"), ("1", "4"), ("2", "3"), ("2", "2")]):
            н.добавить({"номер": i + 1, "время": 1.0, "длина": 1, "стек": ["X"], "протокол": "X", "источник": а,
                        "получатель": б, "ошибки": [], "порт_от": None, "порт_к": None}, {})
        узел = н.раздел("узлы_дерева")["X"]
        self.assertEqual((3, 4, True), (узел["диалогов"], узел["узлов"], узел.get("неполно")))
        н2 = Накопитель()
        for i, (а, б) in enumerate([("1", "2"), ("3", "4"), ("1", "2")]):
            н2.добавить({"номер": i + 1, "время": 1.0, "длина": 1, "стек": ["X"], "протокол": "X", "источник": а,
                         "получатель": б, "ошибки": [], "порт_от": None, "порт_к": None}, {})
        self.assertNotIn("неполно", н2.раздел("узлы_дерева")["X"], "адресов ровно потолок — полно")
        self.assertEqual(4, н2.раздел("узлы_дерева")["X"]["узлов"])

    def сводка(self, i, а, б, **доп):
        return {"номер": i, "время": 1.0, "длина": 1, "стек": ["X"], "протокол": "X", "источник": а, "получатель": б,
                "ошибки": [], "порт_от": None, "порт_к": None, **доп}

    def test_потолок_адресов_узла(self):
        from reportgen.setevoy import nakopitel
        старое = nakopitel.АДРЕСОВ_УЗЛА_ДО
        nakopitel.АДРЕСОВ_УЗЛА_ДО = 4
        self.addCleanup(setattr, nakopitel, "АДРЕСОВ_УЗЛА_ДО", старое)
        for пары, полно, узлов in (([("1", "2"), ("3", "4"), ("1", "3")], True, 4),
                                   ([("1", "2"), ("3", "4"), ("5", "1")], False, 4),
                                   ([("1", "2"), ("3", "4"), ("1", "5")], False, 4),
                                   ([("1", "2"), ("3", "4"), ("5", "6")], False, 4)):
            н = Накопитель()
            for i, (а, б) in enumerate(пары, start=1):
                н.добавить(self.сводка(i, а, б), {})
            узел = н.раздел("узлы_дерева")["X"]
            with self.subTest(пары=пары):
                self.assertEqual((узлов, полно), (узел["узлов"], "неполно" not in узел))

    def test_пустой_и_без_длины_кадра(self):
        н = Накопитель()
        счёт = н.раздел("счёт")
        self.assertEqual((0, 0, 0, 0, 0, 1), (счёт["пакетов"], счёт["макс_кадр"], счёт["макс_нагрузка"], счёт["пар"],
                                              счёт["tcp"], счёт["узлов_дерева"]))
        н.добавить(self.сводка(1, "1", "2"), {})
        self.assertEqual(0, н.раздел("счёт")["макс_кадр"], "длина кадра не задана — ноль")

    def test_два_запроса_http_одного_потока(self):
        н = Накопитель()
        запрос = dict(стек=["E", "IPv4", "TCP", "HTTP"], порт_от=5000, порт_к=80)
        ответ = dict(стек=["E", "IPv4", "TCP", "HTTP"], порт_от=80, порт_к=5000)
        н.добавить(self.сводка(1, "c", "s", **запрос), {"http.request.method": ["GET"]})
        н.добавить(self.сводка(2, "c", "s", **запрос), {"http.request.method": ["POST"]})
        н.добавить(self.сводка(3, "s", "c", **ответ), {"http.response.code": [200]})
        н.добавить(self.сводка(4, "s", "c", **ответ), {"http.response.code": [404]})
        self.assertEqual([200, 404], [з["код"] for з in н.раздел("http")])
        self.assertEqual(0, н.ждут_http_всего)
        # TCP без портов (фрагмент) потоком не считается.
        н.добавить(self.сводка(5, "c", "s", стек=["E", "IPv4", "TCP"]), {})
        self.assertEqual(1, н.раздел("счёт")["потоков"])

    def test_потолок_ждущих_ответа_http(self):
        from reportgen.setevoy import nakopitel
        старое = nakopitel.ЖДУТ_HTTP_ДО
        nakopitel.ЖДУТ_HTTP_ДО = 1
        self.addCleanup(setattr, nakopitel, "ЖДУТ_HTTP_ДО", старое)
        н = Накопитель()
        стек = ["E", "IPv4", "TCP", "HTTP"]
        шаги = [("A", "req"), ("A", "resp"), ("B", "req"), ("C", "req"), ("B", "resp"), ("C", "resp")]
        for i, (кто, что) in enumerate(шаги, start=1):
            if что == "req":
                н.добавить(self.сводка(i, кто, "s", стек=стек, порт_от=5000, порт_к=80), {"http.request.method": ["GET"]})
            else:
                н.добавить(self.сводка(i, "s", кто, стек=стек, порт_от=80, порт_к=5000), {"http.response.code": [200]})
        self.assertEqual([200, 200, ""], [з["код"] for з in н.раздел("http")])

    def test_потолки_строк_и_групп_точно(self):
        from reportgen.setevoy import nakopitel
        for имя, значение in (("СТРОК_ДО", 3), ("ОБРАЗЦОВ", 2), ("ГРУПП_ДО", 2), ("ИМЁН_ДО", 2), ("НАБОР_DNS_ДО", 2),
                              ("ДИАЛОГОВ_ДО", 2), ("УЗЛОВ_ДО", 2)):
            старое = getattr(nakopitel, имя)
            setattr(nakopitel, имя, значение)
            self.addCleanup(setattr, nakopitel, имя, старое)
        import random
        сводки, поля = self.набор(random.Random(3), 400)
        н = Накопитель()
        for с_, п in zip(сводки, поля, strict=True):
            н.добавить(с_, п)
        self.assertEqual((3, 3, 3), (len(н.раздел("dns")), len(н.раздел("http")), len(н.раздел("tls"))))
        счёт = н.раздел("счёт")
        self.assertEqual(len(statistika.tls(сводки, поля)), счёт["tls"])
        self.assertEqual(len(statistika.dns(сводки, поля)), счёт["dns"])
        группы = н.раздел("неизвестные")
        self.assertEqual(2, len(группы))
        self.assertTrue(all(len(г_["образцы"]) == 2 for г_ in группы))
        self.assertEqual(2, len(н.имена_dns))
        self.assertLessEqual(len(н.запросы_dns), 2)
        # Сколько пакетов не попало в таблицы диалогов и узлов — ровно.
        ip = [с_ for с_ in сводки if {"IPv4", "IPv6"} & set(с_["стек"])]
        ключи, мимо = [], 0
        for с_ in ip:
            к = tuple(sorted((с_["источник"], с_["получатель"])))
            if к not in ключи:
                if len(ключи) >= 2:
                    мимо += 1
                    continue
                ключи.append(к)
        self.assertEqual(мимо, счёт["диалогов_мимо"]["ip"])
        узлы, мимо_у = [], 0
        for с_ in ip:
            for адрес in (с_["источник"], с_["получатель"]):
                if адрес not in узлы:
                    if len(узлы) >= 2:
                        мимо_у += 1
                        continue
                    узлы.append(адрес)
        self.assertEqual(мимо_у, счёт["узлов_мимо"])
        self.assertEqual(2, счёт["потоков"], "потоков — не больше потолка")


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

    def test_команды_и_остановка_работы_потоком(self):
        from reportgen.setevoy import raboty
        получено = []

        def исполнить(вид, аргументы, команды):
            while True:
                к = команды.get(timeout=10)
                получено.append(к["к"])
                if к["к"] == "стоп":
                    return

        старое = raboty._исполнить
        raboty._исполнить = исполнить
        self.addCleanup(setattr, raboty, "_исполнить", старое)
        д = Диспетчер(разборов=1, процессы=True)          # процессом=False у работы — поток и при процессах
        self.addCleanup(д.закрыть)
        self.assertEqual({"разбор": 1, "отбор": max(2, min(8, os.cpu_count() or 2))}, д.места)
        д.поставить(Работа("разбор", "к", 1, ["к"], процессом=False))
        self.assertTrue(дождаться(lambda: д.идёт("к")))
        self.assertTrue(д.команда("к", {"к": "пауза"}))
        self.assertFalse(д.команда("нет", {"к": "пауза"}))
        self.assertEqual({"места": {"разбор": 1, "отбор": д.места["отбор"]}, "идут": {"разбор": 1, "отбор": 0},
                          "ждут": {"разбор": 0, "отбор": 0}}, д.сводка())
        д.остановить("к", "проверка", ждать=5)
        self.assertEqual(["пауза", "стоп"], получено)
        self.assertFalse(д.идёт("к"))
        self.assertIsNone(д.в_очереди("к"))

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


class ФайлыПоСсылкеTests(unittest.TestCase):
    """fayly_ssylki напрямую: папки из настроек, права, разрешение путей, обзор и поиск, отпечаток."""

    def setUp(self):
        from types import SimpleNamespace
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.корень = Path(self._tmp.name)
        self.данные = self.корень / "data"
        self.настройки = SimpleNamespace(data_dir=self.данные, input_dirs=[])
        self.человек = lambda роль: SimpleNamespace(role=роль, rank={"guest": -10, "engineer": 0, "head": 40}[роль])

    def test_папки_по_умолчанию_и_из_настроек(self):
        from reportgen import fayly_ssylki as ф
        п = ф.папки(self.настройки)
        self.assertEqual([("Входные файлы сервера", self.данные / "vhod")], [(x.имя, x.путь) for x in п])
        self.assertTrue((self.данные / "vhod").is_dir())
        self.настройки.input_dirs = ["  ", str(self.корень / "A"), {"name": "Б", "path": str(self.корень / "B"), "role": "HEAD"},
                                     {"путь": str(self.корень / "C"), "роль": "нет такой"}]
        п = ф.папки(self.настройки)
        self.assertEqual(["A", "Б", "C"], [x.имя for x in п])
        self.assertEqual("head", п[1].роль)
        self.assertEqual(п[0].ид, ф.папки(self.настройки)[0].ид, "ид папки постоянен")
        self.assertEqual(["A"], [x.имя for x in ф.доступные(self.настройки, self.человек("engineer"))])
        self.assertEqual(["A", "Б"], [x.имя for x in ф.доступные(self.настройки, self.человек("head"))])
        self.assertEqual([], ф.доступные(self.настройки, self.человек("guest")))
        with self.assertRaises(ф.ОшибкаПути):
            ф.найти_папку(self.настройки, self.человек("engineer"), п[1].ид)
        self.assertEqual("Б", ф.найти_папку(self.настройки, self.человек("head"), п[1].ид).имя)
        self.assertEqual({"id": п[0].ид, "имя": "A"}, п[0].в_словарь())

    def test_пути_и_обзор(self):
        from reportgen import fayly_ssylki as ф
        корень = self.корень / "vh"
        (корень / "a" / "b").mkdir(parents=True)
        (корень / "a" / "b" / "x.pcap").write_bytes(b"12345")
        (корень / "a" / "Y.PCAP").write_bytes(b"1")
        (корень / "z.txt").write_bytes(b"")
        (self.корень / "снаружи").mkdir()
        (self.корень / "снаружи" / "s.pcap").write_bytes(b"s")
        ссылки = True
        try:
            os.symlink(self.корень / "снаружи", корень / "наружу")
            os.symlink(корень / "a", корень / "внутрь")
        except OSError:
            ссылки = False
        п = ф.Папка("1", "vh", корень)
        self.assertEqual(корень.resolve() / "a" / "b", ф.разрешить(п, "a\\b"))
        self.assertEqual(корень.resolve(), ф.разрешить(п, ""))
        self.assertEqual(корень.resolve() / "a", ф.разрешить(п, "./a/."))
        for плохой in ("..", "a/../../x", "/etc", "C:/x", "c:x", "\\\\srv\\share", "a\x00b"):
            with self.subTest(плохой), self.assertRaises(ф.ОшибкаПути):
                ф.разрешить(п, плохой)
        список = ф.список(п)
        self.assertEqual(["a", "z.txt"] + (["внутрь"] if ссылки else []), sorted(э["имя"] for э in список["элементы"]))
        self.assertEqual(("", False), (список["путь"], список["обрезано"]))
        self.assertTrue(список["элементы"][0]["папка"], "папки — первыми")
        вложенный = ф.список(п, "a")
        self.assertEqual(["b", "Y.PCAP"], [э["имя"] for э in вложенный["элементы"]])
        self.assertEqual((1, "a/Y.PCAP"), (вложенный["элементы"][1]["размер"], вложенный["элементы"][1]["путь"]))
        найдено = ф.список(п, "", "pcap")
        self.assertIn("a/b/x.pcap", [э["путь"] for э in найдено["элементы"]])
        self.assertIn("a/Y.PCAP", [э["путь"] for э in найдено["элементы"]], "поиск без учёта регистра")
        self.assertNotIn("s.pcap", [э["имя"] for э in найдено["элементы"]], "ссылка наружу не обходится")
        with self.assertRaises(ф.ОшибкаПути):
            ф.список(п, "z.txt")
        with self.assertRaises(ф.ОшибкаПути):
            ф.список(ф.Папка("2", "нет", self.корень / "нет"))
        if ссылки:
            with self.assertRaises(ф.ОшибкаПути):
                ф.файл(п, "наружу/s.pcap")
        self.assertEqual(корень.resolve() / "a" / "b" / "x.pcap", ф.файл(п, "a/b/x.pcap"))
        with self.assertRaises(ф.ОшибкаПути):
            ф.файл(п, "a")
        старые = ф.НАХОДОК_ДО
        ф.НАХОДОК_ДО = 1
        self.addCleanup(setattr, ф, "НАХОДОК_ДО", старые)
        self.assertEqual((1, True), (len(ф.список(п, "", "a")["элементы"]), ф.список(п, "", "a")["обрезано"]))
        ф.НАХОДОК_ДО = старые
        старые_э = ф.ЭЛЕМЕНТОВ_ДО
        ф.ЭЛЕМЕНТОВ_ДО = 1
        self.addCleanup(setattr, ф, "ЭЛЕМЕНТОВ_ДО", старые_э)
        self.assertEqual((1, True), (len(ф.список(п)["элементы"]), ф.список(п)["обрезано"]))

    def test_отпечаток(self):
        from reportgen import fayly_ssylki as ф
        путь = self.корень / "f.pcap"
        путь.write_bytes(b"abc")
        о = ф.отпечаток(путь)
        self.assertEqual(3, о["размер"])
        self.assertEqual("", ф.проверить_отпечаток(путь, о))
        self.assertIn("изменён", ф.проверить_отпечаток(путь, {**о, "размер": 4}))
        self.assertIn("изменён", ф.проверить_отпечаток(путь, {**о, "изменён_нс": о["изменён_нс"] + 1}))
        путь.unlink()
        self.assertIn("удалён", ф.проверить_отпечаток(путь, о))


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
