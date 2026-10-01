"""Захват с сети, часть 2: цепочка кусков pcapng, поточный читатель, прогон захвата
(проигрыватель) и «Декодировать как» (реестр разборщиков, правила, хранение, API).

Цепочка сверяется с тем, что читает существующий читатель захватов проекта
(setevoy.chtenie), поточный читатель — с ним же на тех же файлах; прогон — с
независимым подсчётом по синтезированным пакетам (setevoy_sintez); правила
«Декодировать как» — со стеком протоколов обычного разбора.
"""

import json
import shutil
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import dekodirovat_kak as дк
from reportgen.setevoy.chtenie import прочитать_захват
from reportgen.setevoy.razbor import разобрать_пакет
from reportgen.setevoy.zahvat_seti import chtec, progon, zapis

ИНТЕРФЕЙС = {"канал": zapis.LINKTYPE_ETHERNET, "имя": "eth9", "описание": "стенд", "фильтр": "", "mac": b"", "ipv4": None,
             "скорость": 0}


def дождаться(условие, секунд=10.0):
    конец = time.time() + секунд
    while time.time() < конец:
        if условие():
            return True
        time.sleep(0.02)
    return False


def rtp(номер, нагрузка, ssrc=0x1234):
    return bytes([0x80, 96]) + struct.pack("!HII", номер & 0xFFFF, номер * 160, ssrc) + нагрузка


def кадр_udp(нагрузка, порт_к=5004, порт_от=4000, от="10.0.0.1", к="10.0.0.2"):
    return с.eth(с.ip(с.udp(нагрузка, sport=порт_от, dport=порт_к, src=от, dst=к), 17, src=от, dst=к))


class Папка(unittest.TestCase):
    def setUp(self):
        self.т = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.т, True)


# -- цепочка кусков ------------------------------------------------------------------------

class ЦепочкаTests(Папка):
    def цепочка(self, **к):
        return zapis.ЦепочкаPcapng(self.т, интерфейс=ИНТЕРФЕЙС, **к)

    def test_смена_по_пакетам_и_указатель(self):
        ц = self.цепочка(пакетов_куска=3)
        кадры = [кадр_udp(bytes([i]) * 10) for i in range(8)]
        for i, к in enumerate(кадры):
            ц.пакет(100.0 + i, к)
        ц.закрыть()
        файлы = sorted((self.т / zapis.КУСКИ).glob("*.pcapng"))
        self.assertEqual(["000000.pcapng", "000001.pcapng", "000002.pcapng"], [ф.name for ф in файлы])
        прочитано = [[з.данные for з in прочитать_захват(ф).записи] for ф in файлы]
        self.assertEqual([кадры[0:3], кадры[3:6], кадры[6:8]], прочитано, "каждый кусок — свой целый pcapng")
        указатель = zapis.прочитать_указатель(self.т)
        self.assertEqual([(0, 3, 1, 100.0, 102.0), (1, 3, 4, 103.0, 105.0), (2, 2, 7, 106.0, 107.0)],
                         [(з["кусок"], з["пакетов"], з["первый"], з["начало"], з["конец"]) for з in указатель])
        self.assertEqual([ф.stat().st_size for ф in файлы], [з["байт"] for з in указатель])
        self.assertEqual(sum(ф.stat().st_size for ф in файлы), ц.записано)
        self.assertEqual(ц.записано, ц.на_диске())
        self.assertEqual((2, 8), (ц.номер, ц.пакетов))

    def test_итог_isb_в_каждом_куске(self):
        счёт = {"n": 0}

        def счётчики():
            счёт["n"] += 1
            return {"начало": 1.0, "конец": 2.0, "принято": 5, "отброшено": None, "отфильтровано_принято": 5, "доставлено": 5}
        ц = self.цепочка(пакетов_куска=2, счётчики=счётчики)
        for i in range(4):
            ц.пакет(1.0 + i, кадр_udp(b"x"))
        ц.закрыть()
        self.assertEqual(2, счёт["n"])
        for ф in (self.т / zapis.КУСКИ).glob("*.pcapng"):
            данные = ф.read_bytes()
            хвост = struct.unpack_from("<I", данные, len(данные) - 4)[0]
            self.assertEqual(zapis.БЛОК_ISB, struct.unpack_from("<I", данные, len(данные) - хвост)[0], ф.name)

    def test_смена_по_объёму_и_времени(self):
        ц = self.цепочка(байт_куска=1, пакетов_куска=10 ** 6)
        for i in range(3):
            ц.пакет(float(i), кадр_udp(b"y"))
        self.assertEqual(2, ц.номер, "кусок набрал байт — следующий пакет идёт в новый")
        ц.закрыть()
        т2 = self.т / "в"
        ц = zapis.ЦепочкаPcapng(т2, интерфейс=ИНТЕРФЕЙС, секунд_куска=10)
        for t in (0.0, 5.0, 9.99, 10.0, 15.0, 20.5):
            ц.пакет(t, кадр_udp(b"z"))
        ц.закрыть()
        self.assertEqual([3, 2, 1], [з["пакетов"] for з in zapis.прочитать_указатель(т2)])
        self.assertFalse(ц.нужна_смена(0.0), "закрытая цепочка не меняет кусок")

    def test_кольцо_хранит_последние(self):
        ц = self.цепочка(пакетов_куска=1, кусков=2)
        for i in range(5):
            ц.пакет(float(i), кадр_udp(bytes([i])))
        ц.закрыть()
        self.assertEqual(["000003.pcapng", "000004.pcapng"], sorted(ф.name for ф in (self.т / zapis.КУСКИ).glob("*")))
        self.assertEqual(3, ц.удалено_кусков)
        self.assertEqual(ц.записано - ц.удалено_байт, ц.на_диске())
        self.assertEqual(sum(ф.stat().st_size for ф in (self.т / zapis.КУСКИ).glob("*")), ц.на_диске())
        self.assertEqual(5, len(zapis.прочитать_указатель(self.т)), "указатель помнит и удалённые")

    def test_кольцо_не_удалённый_файл_удаляется_потом(self):
        ц = self.цепочка(пакетов_куска=1, кусков=1)
        настоящий = Path.unlink
        отказ = {"раз": 1}

        def unlink(путь, *а, **к):
            if отказ["раз"]:
                отказ["раз"] -= 1
                raise PermissionError("занят читателем")
            return настоящий(путь, *а, **к)
        with mock.patch.object(Path, "unlink", unlink):
            ц.пакет(0.0, кадр_udp(b"a"))
            ц.пакет(1.0, кадр_udp(b"b"))          # кусок 0 не удалился — держит читатель
            self.assertTrue((self.т / zapis.КУСКИ / "000000.pcapng").exists())
            self.assertEqual(0, ц.удалено_кусков)
            ц.пакет(2.0, кадр_udp(b"c"))          # при следующей смене — удалился
        ц.закрыть()
        self.assertEqual(["000002.pcapng"], sorted(ф.name for ф in (self.т / zapis.КУСКИ).glob("*")))
        self.assertEqual(2, ц.удалено_кусков)

    def test_стоимость_точная(self):
        ц = self.цепочка(пакетов_куска=2)
        for длина in (0, 1, 2, 3, 4, 61):
            было = ц.записано
            нужна = ц.нужна_смена(7.0)
            цена = ц.стоимость(7.0, длина)
            ц.пакет(7.0, b"\xAA" * длина)
            прибавилось = ц.записано - было
            if нужна:
                # Итог куска — от счётчиков (их нет — итога нет): цена сверху не меньше прибавки.
                self.assertGreaterEqual(цена, прибавилось)
                self.assertEqual(32 + длина + (-длина % 4) + zapis.ISB_НАИБОЛЬШИЙ + ц.заголовок, цена)
            else:
                self.assertEqual(прибавилось, цена, длина)
        ц.закрыть()

    def test_isb_наибольший_по_спецификации(self):
        """ISB со всеми опциями этого писателя — ровно ISB_НАИБОЛЬШИЙ байт."""
        блок = zapis.статистика_isb(начало=1.0, конец=2.0, принято=1, отброшено=2, отфильтровано_принято=3, доставлено=4)
        self.assertEqual(zapis.ISB_НАИБОЛЬШИЙ, len(блок))
        без = zapis.статистика_isb(начало=1.0, конец=2.0, принято=1, отброшено=None, отфильтровано_принято=3, доставлено=4)
        self.assertEqual(zapis.ISB_НАИБОЛЬШИЙ - 12, len(без))

    def test_границы_цепочки(self):
        ц = self.цепочка()
        self.assertEqual((64 << 20, 100_000, 0.0, 0), (ц.байт_куска, ц.пакетов_куска, ц.секунд_куска, ц.кусков),
                         "умолчания — как у dumpcap-подобной записи: 64 МБ или 100 000 пакетов на кусок")
        ц.пакет(0.0, кадр_udp(b"a"))
        ц.сбросить()
        путь = ц.путь(0)
        self.assertEqual(ц.записано, путь.stat().st_size, "сбросить — всё записанное на диске")
        ц.закрыть()
        ц.сбросить()                                         # закрытая — без ошибки
        # Кусок ровно набрал байт_куска — следующий пакет идёт в новый (>=, не >).
        т2 = self.т / "ровно"
        один = zapis.ЦепочкаPcapng(т2, интерфейс=ИНТЕРФЕЙС)
        один.пакет(0.0, кадр_udp(b"b"))
        ровно = один.записано
        один.закрыть()
        ц = zapis.ЦепочкаPcapng(т2 / "ещё", интерфейс=ИНТЕРФЕЙС, байт_куска=ровно)
        ц.пакет(0.0, кадр_udp(b"b"))
        self.assertTrue(ц.нужна_смена(0.0))
        ц.закрыть()
        ц = zapis.ЦепочкаPcapng(т2 / "ещё2", интерфейс=ИНТЕРФЕЙС, байт_куска=ровно + 1)
        ц.пакет(0.0, кадр_udp(b"b"))
        self.assertFalse(ц.нужна_смена(0.0))
        ц.закрыть()
        # Итог ISB куска входит в «записано»; та же папка открывается снова (каталог кусков уже есть).
        счётчики = {"начало": 1.0, "конец": 2.0, "принято": 3, "отброшено": 1, "отфильтровано_принято": 3,
                    "доставлено": 3}
        т3 = self.т / "итог"
        for _ in range(2):
            ц = zapis.ЦепочкаPcapng(т3, интерфейс=ИНТЕРФЕЙС, пакетов_куска=2, счётчики=lambda: счётчики)
            for i in range(3):
                ц.пакет(float(i), кадр_udp(b"c"))
            ц.закрыть()
            self.assertEqual(sum(ф.stat().st_size for ф in (т3 / zapis.КУСКИ).glob("*.pcapng")), ц.записано)

    def test_испорченный_указатель(self):
        (self.т / zapis.УКАЗАТЕЛЬ).write_text('{"кусок": 0, "пакетов": 1}\nмусор\n[1]\n{"кусок": "1"}\n{"кусок": 2}\n',
                                               encoding="utf-8")
        self.assertEqual([0, 2], [з["кусок"] for з in zapis.прочитать_указатель(self.т)])
        self.assertEqual([], zapis.прочитать_указатель(self.т / "нет"))


# -- поточный читатель ---------------------------------------------------------------------

class ЧтецTests(Папка):
    def test_pcap_и_pcapng_как_читатель_проекта(self):
        кадры = [кадр_udp(bytes([i]) * (i + 1)) for i in range(5)]
        for имя, данные in (("a.pcap", с.pcap(кадры)), ("b.pcapng", с.pcapng(кадры)),
                            ("c.pcapng", с.pcapng(кадры, tsresol=6)), ("d.pcap", с.pcap(кадры, канал=101))):
            путь = self.т / имя
            путь.write_bytes(данные)
            эталон = прочитать_захват(путь)
            свои = list(chtec.записи(путь))
            self.assertEqual([(round(з.время, 6), з.данные, з.длина, з.канал) for з in эталон.записи],
                             [(round(в, 6), д, дл, к) for в, д, дл, к in свои], имя)

    def test_pcap_наносекунды_и_старший_порядок(self):
        путь = self.т / "н.pcap"
        данные = struct.pack(">IHHiIII", 0xA1B23C4D, 2, 4, 0, 0, 65535, 1) + struct.pack(">IIII", 3, 500_000_000, 2, 9) + b"ab"
        путь.write_bytes(данные)
        self.assertEqual([(3.5, b"ab", 9, "Ethernet")], list(chtec.записи(путь)))

    def test_дописывается_следом(self):
        """Неполная запись — «пока нечего»; дописали — чтение продолжается с того же места."""
        путь = self.т / "идёт.pcapng"
        with open(путь, "wb") as файл:
            писатель = zapis.ПисательPcapng(файл, **ИНТЕРФЕЙС)
            писатель.пакет(1.0, b"A" * 10)
            файл.flush()
            чтец = chtec.ЧтецФайла(путь)
            self.addCleanup(чтец.закрыть)
            self.assertEqual(b"A" * 10, чтец.следующий()[1])
            self.assertIsNone(чтец.следующий())
            блок = zapis.пакет_epb(2.0, b"B" * 7)
            файл.write(блок[:20])
            файл.flush()
            self.assertIsNone(чтец.следующий(), "половина блока — ещё не запись")
            файл.write(блок[20:])
            файл.flush()
            self.assertEqual((2.0, b"B" * 7), чтец.следующий()[:2])
            self.assertEqual("", чтец.испорчен)

    def test_испорченные_файлы(self):
        путь = self.т / "x"
        путь.write_bytes(b"GIF89a....")
        with chtec.ЧтецФайла(путь) as ч:
            self.assertIsNone(ч.следующий())
            self.assertEqual("не pcap и не pcapng", ч.испорчен)
        кадры = [b"q" * 8]
        данные = bytearray(с.pcapng(кадры))
        struct.pack_into("<I", данные, len(данные) - 4, 999)        # хвостовая длина не та
        путь.write_bytes(bytes(данные))
        with chtec.ЧтецФайла(путь) as ч:
            self.assertIsNone(ч.следующий())
            self.assertIn("длины в начале и в конце разные", ч.испорчен)
        данные = bytearray(с.pcap(кадры))
        struct.pack_into("<I", данные, 24 + 8, chtec.ЗАПИСЬ_ДО + 1)
        путь.write_bytes(bytes(данные))
        with chtec.ЧтецФайла(путь) as ч:
            self.assertIsNone(ч.следующий())
            self.assertIn("испорчена", ч.испорчен)
            self.assertIsNone(ч.следующий(), "испорченный больше не читается")

    def test_склейка_кусков_тоже_pcapng(self):
        ц = zapis.ЦепочкаPcapng(self.т, интерфейс=ИНТЕРФЕЙС, пакетов_куска=2)
        for i in range(5):
            ц.пакет(float(i), bytes([i]) * 20)
        ц.закрыть()
        склейка = self.т / "всё.pcapng"
        склейка.write_bytes(b"".join(ф.read_bytes() for ф in sorted((self.т / zapis.КУСКИ).glob("*.pcapng"))))
        self.assertEqual([bytes([i]) * 20 for i in range(5)], [д for _, д, _, _ in chtec.записи(склейка)])
        self.assertEqual(5, len(прочитать_захват(склейка).записи))


    def блок(self, тип, тело):
        тело += b"\0" * (-len(тело) % 4)
        return struct.pack("<II", тип, 12 + len(тело)) + тело + struct.pack("<I", 12 + len(тело))

    def test_opb_и_spb(self):
        """OPB (устаревший, тип 2) и SPB (тип 3: без времени, длина — по snaplen интерфейса)."""
        shb = self.блок(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
        idb = self.блок(1, struct.pack("<HHI", 1, 0, 6))                           # snaplen 6
        opb = self.блок(2, struct.pack("<HHIIII", 0, 0, 0, 3_000_000, 5, 9) + b"ABCDE")
        spb = self.блок(3, struct.pack("<I", 10) + b"0123456789")
        spb_короче = self.блок(3, struct.pack("<I", 3) + b"xyz")
        пустой = self.блок(3, b"")                                                    # тело меньше 4 — пропуск
        путь = self.т / "старые.pcapng"
        путь.write_bytes(shb + idb + opb + spb + spb_короче + пустой)
        self.assertEqual([(3.0, b"ABCDE", 9, "Ethernet"), (3.0, b"012345", 10, "Ethernet"), (3.0, b"xyz", 3, "Ethernet")],
                         list(chtec.записи(путь)))
        без_snaplen = shb + self.блок(1, struct.pack("<HHI", 1, 0, 0)) + spb
        путь.write_bytes(без_snaplen)
        self.assertEqual([b"0123456789"], [д for _, д, _, _ in chtec.записи(путь)], "snaplen 0 — без предела")
        путь.write_bytes(shb + spb)
        self.assertEqual([], list(chtec.записи(путь)), "SPB до IDB — не к чему отнести")
        путь.write_bytes(shb + idb + self.блок(6, b"\0" * 16))
        self.assertEqual([], list(chtec.записи(путь)), "EPB короче 20 байт полей — пропуск")

    def test_opb_с_потерями_spb_обрезанный_и_пустой(self):
        """OPB: номер интерфейса — 16 бит, за ним drops_count (не часть номера, как в EPB);
        SPB: записано не больше, чем есть байт в блоке; SPB с нулевой длиной — пустая запись."""
        shb = self.блок(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
        idb = self.блок(1, struct.pack("<HHI", 1, 0, 0))                           # snaplen 0 — без предела
        opb = self.блок(2, struct.pack("<HHIIII", 0, 3, 0, 1_000_000, 2, 2) + b"OP")
        обрезанный = self.блок(3, struct.pack("<I", 100) + b"01234567")              # в линии 100, в файле 8
        пустой = self.блок(3, struct.pack("<I", 0))
        путь = self.т / "spb.pcapng"
        путь.write_bytes(shb + idb + opb + обрезанный + пустой)
        self.assertEqual([(1.0, b"OP", 2), (1.0, b"01234567", 100), (1.0, b"", 0)],
                         [(в, д, и) for в, д, и, _ in chtec.записи(путь)])

    def test_старший_порядок_и_второй_интерфейс(self):
        """Файл со старшим порядком байт (SHB 1A 2B 3C 4D), EPB на интерфейсе 1 — канал второго IDB."""
        def блок(тип, тело):
            тело += b"\0" * (-len(тело) % 4)
            return struct.pack(">II", тип, 12 + len(тело)) + тело + struct.pack(">I", 12 + len(тело))
        данные = (блок(0x0A0D0D0A, struct.pack(">IHHq", 0x1A2B3C4D, 1, 0, -1))
                  + блок(1, struct.pack(">HHI", 1, 0, 0)) + блок(1, struct.pack(">HHI", 101, 0, 0))
                  + блок(6, struct.pack(">IIIII", 1, 0, 2_000_000, 3, 3) + b"\x45\x00\x00"))
        путь = self.т / "be.pcapng"
        путь.write_bytes(данные)
        записи = list(chtec.записи(путь))
        self.assertEqual([(2.0, b"\x45\x00\x00", 3)], [(в, д, и) for в, д, и, _ in записи])
        self.assertEqual(прочитать_захват(путь).записи[0].канал, записи[0][3], "канал второго IDB — как у читателя")

    def test_заголовок_pcap_без_записей_и_snaplen(self):
        путь = self.т / "пусто.pcap"
        путь.write_bytes(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 1234, 101))
        with chtec.ЧтецФайла(путь) as ч:
            self.assertEqual((0, ""), (ч.snaplen, ч.вид))
            self.assertIsNone(ч.следующий())
            self.assertEqual(("pcap", 1234, "IP", ""), (ч.вид, ч.snaplen, ч.канал, ч.испорчен))
        путь.write_bytes(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 1234, 101)[:23])
        with chtec.ЧтецФайла(путь) as ч:
            self.assertIsNone(ч.следующий())
            self.assertEqual("", ч.вид, "заголовок ещё не дописан")

    def test_пределы_записи_и_блока(self):
        путь = self.т / "п.pcap"
        путь.write_bytes(с.pcap([b"12345678", b"123456789"]))
        with mock.patch.object(chtec, "ЗАПИСЬ_ДО", 8):
            with chtec.ЧтецФайла(путь) as ч:
                self.assertEqual(b"12345678", ч.следующий()[1], "ровно предел — можно")
                self.assertIsNone(ч.следующий())
                self.assertIn("испорчена", ч.испорчен)
        путь = self.т / "п.pcapng"
        путь.write_bytes(с.pcapng([b"x" * 8]))
        размер_epb = 12 + 20 + 8
        with mock.patch.object(chtec, "БЛОК_ДО", размер_epb):
            self.assertEqual([b"x" * 8], [д for _, д, _, _ in chtec.записи(путь)], "блок ровно в предел — можно")
        with mock.patch.object(chtec, "БЛОК_ДО", размер_epb - 4):
            with chtec.ЧтецФайла(путь) as ч:
                self.assertIsNone(ч.следующий())
                self.assertIn("испорчен", ч.испорчен)

    def test_файл_без_дескриптора(self):
        """Файл-объект без fileno (BytesIO): размер — переходом в конец, место чтения не сбивается."""
        import io  # noqa: PLC0415
        данные = с.pcap([b"a" * 5, b"b" * 7])
        ч = chtec.ЧтецФайла(self.т / "нет", файл=io.BytesIO(данные))
        self.assertEqual(len(данные), ч._размер())
        self.assertEqual([b"a" * 5, b"b" * 7], [ч.следующий()[1], ч.следующий()[1]])
        self.assertIsNone(ч.следующий())
        self.assertEqual(len(данные), ч._размер())

    def test_опции_с_обрывком_в_конце(self):
        self.assertEqual({}, chtec.опции_блока(b"\x02\x00\x00", 0, "<"), "меньше 4 байт — не опция")
        self.assertEqual({2: b"ab"}, chtec.опции_блока(struct.pack("<HH", 2, 2) + b"ab\0\0" + b"\x09\x00\x01", 0, "<"))


# -- прогон: дерево, упорядочение, выход ----------------------------------------------------

class ДеревоTests(unittest.TestCase):
    def test_счёт_и_вид_как_у_иерархии(self):
        д = progon.Дерево()
        д.учесть(["Ethernet", "IPv4", "UDP", "DNS"], 100)
        д.учесть(["Ethernet", "IPv4", "UDP"], 60)
        д.учесть(["Ethernet", "ARP"], 42)
        (корень,) = д.в_список()
        self.assertEqual(("Кадры", 3, 202, "все"), (корень["протокол"], корень["пакетов"], корень["байт"], корень["уровень"]))
        eth = корень["дети"][0]
        self.assertEqual(("Ethernet", 3, 202, 0), (eth["протокол"], eth["пакетов"], eth["байт"], eth["кончаются"]))
        self.assertEqual(["IPv4", "ARP"], [д_["протокол"] for д_ in eth["дети"]], "по убыванию пакетов")
        udp = eth["дети"][0]["дети"][0]
        self.assertEqual((2, 160, 1), (udp["пакетов"], udp["байт"], udp["кончаются"]))
        self.assertEqual("транспортный", udp["уровень"])
        self.assertEqual(6, д.узлов)

    def test_потолок_узлов(self):
        д = progon.Дерево(узлов_до=3)
        for имя in ("A", "B", "C", "D"):
            д.учесть(["Ethernet", имя], 1)
        дети = д.в_список()[0]["дети"][0]["дети"]
        self.assertEqual({"A": 1, "…прочие": 3}, {д_["протокол"]: д_["пакетов"] for д_ in дети})
        self.assertEqual(4, д.узлов)

    def test_потолок_таблиц_портов(self):
        т = {}
        for i in range(8):
            progon._учесть(т, f"к{i}", 10, 5)
        progon._учесть(т, "к0", 7, 5)                          # старый ключ и на пределе — свой
        self.assertEqual(["к0", "к1", "к2", "к3", "к4", "прочие"], list(т))
        self.assertEqual(([2, 17], [3, 30]), (т["к0"], т["прочие"]))


class УпорядочениеTests(unittest.TestCase):
    def test_переставленные_выходят_по_номеру(self):
        у = progon.Упорядочение(окно=3)
        вышло = []
        for номер in (1, 3, 2, 4, 6, 5, 7):
            вышло += у.добавить(9, номер, bytes([номер]))
        вышло += у.дожать()
        self.assertEqual(bytes(range(1, 8)), b"".join(вышло))
        self.assertEqual((2, 0, 0), (у.сводка["переставлено_rtp"], у.сводка["пропущено_rtp"], у.сводка["повторы_rtp"]))

    def test_пропуски_повторы_опоздавшие_разрывы(self):
        у = progon.Упорядочение(окно=0)
        вышло = []
        for номер in (10, 11, 11, 14, 12, 5000):
            вышло += у.добавить(1, номер, bytes([номер & 0xFF]))
        вышло += у.дожать()
        с_ = у.сводка
        self.assertEqual((1, 2, 1, 1), (с_["повторы_rtp"], с_["пропущено_rtp"], с_["опоздали_rtp"], с_["разрывы_rtp"]))
        self.assertEqual([10, 11, 14, 5000 & 0xFF], [б[0] for б in вышло])

    def test_переход_через_ноль_и_ssrc_раздельно(self):
        у = progon.Упорядочение(окно=0)
        вышло = []
        for номер in (65534, 65535, 0, 1):
            вышло += у.добавить(1, номер, struct.pack("!H", номер))
        вышло += у.добавить(2, 7, b"B")
        self.assertEqual([65534, 65535, 0, 1], [struct.unpack("!H", б)[0] for б in вышло[:4]])
        self.assertEqual(0, у.сводка["пропущено_rtp"], "65535 → 0 — следующий, не пропуск")
        self.assertEqual({1: 65536 + 1, 2: 7}, у.последний)
        self.assertEqual(65535, у.расширить(1, 65535), "опоздавший после перехода — в прошлом круге")
        self.assertEqual(1, у.сводка["переставлено_rtp"])

    def test_границы_полукруга_и_разрыва(self):
        у = progon.Упорядочение(окно=0)
        у.расширить(1, 0)
        self.assertEqual(32767, у.расширить(1, 32767), "меньше полукруга вперёд — вперёд")
        у = progon.Упорядочение(окно=0)
        у.расширить(1, 0)
        self.assertEqual(-32768, у.расширить(1, 32768), "ровно полкруга — назад (RFC 3550 §A.1)")
        у = progon.Упорядочение(окно=0)
        for номер in (100, 101 + progon.MAX_DROPOUT, 102 + 2 * progon.MAX_DROPOUT + 1):
            у.добавить(1, номер, b"x")
        self.assertEqual((progon.MAX_DROPOUT, 1), (у.сводка["пропущено_rtp"], у.сводка["разрывы_rtp"]),
                         "скачок ровно MAX_DROPOUT — пропуски, на один больше — разрыв")


class ВыходTests(Папка):
    def выход(self, **к):
        в = progon.Выход(self.т / "в.bin", порт=5004, **к)
        self.addCleanup(в.файл.close)
        return в

    def test_срез_байт_и_отбор(self):
        в = self.выход(срез=2)
        в.учесть(кадр_udp(b"hdDATA1"))
        в.учесть(кадр_udp(b"hdDATA2", порт_к=5005))            # другой порт — мимо
        в.учесть(кадр_udp(b"h"))                               # короче среза
        в.учесть(с.eth(b"\0" * 30, тип=0x0806))                # не IP
        в.закрыть()
        self.assertEqual(b"DATA1", (self.т / "в.bin").read_bytes())
        self.assertEqual((2, 2, 1, 5), (в.с["датаграмм"], в.с["взято"], в.с["короче_среза"], в.с["байт"]))

    def test_rtp_срез_и_порядок_и_отправитель(self):
        в = self.выход(срез="rtp", упорядочить=True, источник="10.0.0.1")
        for номер in (2, 1, 3):
            в.учесть(кадр_udp(rtp(номер, bytes([номер]) * 3)))
        в.учесть(кадр_udp(rtp(9, b"X"), от="10.0.0.9"))       # другой отправитель
        в.учесть(кадр_udp(b"\x00" * 3))                        # не RTP
        в.закрыть()
        self.assertEqual(b"\1\1\1\2\2\2\3\3\3", (self.т / "в.bin").read_bytes())
        сводка = в.сводка()
        self.assertEqual((4, 3, 1, 1, ["00001234"]), (сводка["датаграмм"], сводка["взято"], сводка["не_rtp"],
                                                      сводка["переставлено_rtp"], сводка["ssrc"]))

    def test_срез_rtp_без_порядка_дополнение_и_ровно_срез(self):
        в = self.выход(срез="rtp")
        в.учесть(кадр_udp(rtp(1, b"AB")))
        с_дополнением = bytearray(rtp(2, b"CD" + b"\0\0\3"))
        с_дополнением[0] |= 0x20                               # P: последние 3 октета — дополнение
        в.учесть(кадр_udp(bytes(с_дополнением)))
        в.закрыть()
        self.assertEqual(b"ABCD", (self.т / "в.bin").read_bytes())
        в = progon.Выход(self.т / "в2.bin", порт=5004)
        self.addCleanup(в.файл.close)
        в.учесть(кадр_udp(b"whole"))
        в.закрыть()
        self.assertEqual(b"whole", (self.т / "в2.bin").read_bytes(), "по умолчанию — без среза")
        в = progon.Выход(self.т / "в3.bin", порт=5004, срез=5)
        self.addCleanup(в.файл.close)
        в.учесть(кадр_udp(b"12345"))
        в.закрыть()
        self.assertEqual((0, 1, 0), (в.с["короче_среза"], в.с["взято"], в.с["байт"]), "ровно срез — не короче")

    def test_состояние_захвата_перечитывается_не_чаще_300_мс(self):
        (self.т / "состояние.json").write_text(json.dumps({"состояние": "идёт"}), encoding="utf-8")
        к = progon.Кадры({"вид": "захват", "папка": str(self.т)})
        часы = {"t": 100.0}
        with mock.patch.object(progon.time, "monotonic", lambda: часы["t"]):
            self.assertTrue(к.захват_идёт())
            (self.т / "состояние.json").write_text(json.dumps({"состояние": "готово"}), encoding="utf-8")
            часы["t"] = 100.3
            self.assertTrue(к.захват_идёт(), "через 0,3 с — ещё прежнее")
            часы["t"] = 100.31
            self.assertFalse(к.захват_идёт(), "позже — перечитано")
        self.assertFalse(progon.Кадры({"вид": "файлы", "файлы": []}).захват_идёт(), "файлы — не захват")

    def test_фрагмент_без_нагрузки(self):
        в = self.выход()
        ip = bytearray(с.ip(с.udp(b"abcdef", 4000, 5004), 17))
        struct.pack_into("!H", ip, 6, 0x2000)                  # MF — первый фрагмент
        ip[10:12] = b"\0\0"
        в.учесть(с.eth(bytes(ip)))
        в.закрыть()
        self.assertEqual((1, 1, 0), (в.с["датаграмм"], в.с["фрагменты"], в.с["взято"]))


# -- прогон: источник кадров и сам прогон ------------------------------------------------------

def прогнать(папка: Path, задание: dict, команды=None) -> dict:
    к = команды or __import__("queue").Queue()
    progon.Прогон(папка, задание, к).работать()
    return json.loads((папка / "ход.json").read_text(encoding="utf-8"))


class ПрогонTests(Папка):
    def файл(self, кадры, времена=None, имя="з.pcap"):
        путь = self.т / имя
        путь.write_bytes(с.pcap(кадры, времена=времена))
        return путь

    def test_до_конца_с_деревом_портами_и_выходом(self):
        кадры = [кадр_udp(rtp(i, bytes([i]) * 4)) for i in range(20)] + [с.eth(с.ip(с.udp(с.dns_запрос("a.b"), sport=5353, dport=53), 17))]
        путь = self.файл(кадры)
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0,
                                "выход": {"порт": 5004, "срез": "rtp", "упорядочить": True}})
        self.assertEqual(("готово", "запись пройдена до конца", 21), (ход["состояние"], ход["причина"], ход["пакетов"]))
        self.assertEqual(sum(len(к) for к in кадры), ход["байт"])
        корень = ход["дерево"][0]
        self.assertEqual(21, корень["пакетов"])
        ip = корень["дети"][0]["дети"][0]
        self.assertEqual({"UDP": 21}, {д["протокол"]: д["пакетов"] for д in ip["дети"]})
        self.assertIn(["UDP 5004", 20, sum(len(к) for к in кадры[:20])], ход["порты"])
        self.assertEqual(b"".join(bytes([i]) * 4 for i in range(20)), (self.т / "выход.bin").read_bytes())
        self.assertEqual((20, 80), (ход["выход"]["взято"], ход["выход"]["байт"]))
        self.assertAlmostEqual(10.0, ход["прошло_записи"], 6, "время записи — от первого до последнего пакета")

    def test_sig_и_dpo_прогоняются_как_в_анализаторе(self):
        # Файл «Пакетов» бывает не только pcap/pcapng: .sig/.dpo (кадры с двухбайтовой длиной) анализатор
        # читает — прогон этого же файла должен пройти те же кадры, а не кончиться на «не pcap и не pcapng».
        import potok_sintez as пс  # noqa: PLC0415
        кадры = [с.ip(с.udp(b"x" * (20 + i), 5000 + i, 53), 17) for i in range(12)]
        путь = self.т / "запись.dpo"
        путь.write_bytes(пс.sig(кадры))
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0})
        self.assertEqual(("готово", 12, sum(len(к) for к in кадры)), (ход["состояние"], ход["пакетов"], ход["байт"]))
        self.assertEqual([], ход["заметки"])
        self.assertIn("UDP 53", [п[0] for п in ход["порты"]])
        # Ни pcap, ни .sig — честная заметка, а не молчаливый ноль.
        (self.т / "мусор.bin").write_bytes(b"not a capture at all" * 5)
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(self.т / "мусор.bin")]}, "скорость": 0})
        self.assertEqual(0, ход["пакетов"])
        self.assertTrue(any("мусор.bin" in з and "не похож на сетевую запись" in з for з in ход["заметки"]), ход["заметки"])

    def test_скорость_как_записано_выдерживает_время(self):
        путь = self.файл([кадр_udp(b"a")] * 3, времена=[0.0, 0.3, 0.6])
        начало = time.monotonic()
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 1})
        прошло = time.monotonic() - начало
        self.assertEqual(3, ход["пакетов"])
        self.assertGreaterEqual(прошло, 0.55, "×1 — по отметкам времени")
        начало = time.monotonic()
        прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 2})
        прошло2 = time.monotonic() - начало
        self.assertGreaterEqual(прошло2, 0.27)
        self.assertLess(прошло2, прошло, "×2 — вдвое быстрее")

    def test_пауза_продолжить_скорость_стоп(self):
        import queue  # noqa: PLC0415
        путь = self.файл([кадр_udp(b"a")] * 50, времена=[i * 0.05 for i in range(50)])
        к = queue.Queue()
        поток = threading.Thread(target=lambda: progon.Прогон(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]},
                                                                      "скорость": 1}, к).работать())
        поток.start()
        ход = lambda: json.loads((self.т / "ход.json").read_text(encoding="utf-8"))  # noqa: E731
        self.assertTrue(дождаться(lambda: (self.т / "ход.json").exists()))
        time.sleep(0.2)
        к.put({"к": "пауза"})
        self.assertTrue(дождаться(lambda: ход()["состояние"] == "пауза", 3))
        было = ход()["пакетов"]
        time.sleep(0.6)
        self.assertEqual(было, ход()["пакетов"], "на паузе пакеты не идут")
        self.assertLess(было, 50)
        к.put({"к": "скорость", "з": 0})
        к.put({"к": "продолжить"})
        поток.join(10)
        self.assertEqual(("готово", 50, 0.0), (ход()["состояние"], ход()["пакетов"], ход()["скорость"]))
        к2 = queue.Queue()
        к2.put({"к": "стоп", "почему": "хватит"})
        итог = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 1}, к2)
        self.assertEqual(("остановлен", "хватит"), (итог["состояние"], итог["причина"]))
        к3 = queue.Queue()
        к3.put({"к": "скорость", "з": 5000})
        к3.put({"к": "стоп"})
        прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 1}, к3)
        self.assertEqual(progon.СКОРОСТЬ_ДО, ход()["скорость"], "скорость — не выше потолка")

    def test_правила_декодировать_как_в_прогоне(self):
        путь = self.файл([кадр_udp(с.dns_запрос("x.y"), порт_к=5000)] * 3)
        правило = дк.проверить_правило({"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "DNS"})
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0, "правила": [правило]})
        udp = ход["дерево"][0]["дети"][0]["дети"][0]["дети"][0]
        self.assertEqual(["DNS"], [д["протокол"] for д in udp["дети"]])
        self.assertEqual({правило["ид"]: 3}, ход["правила"])
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0})
        udp = ход["дерево"][0]["дети"][0]["дети"][0]["дети"][0]
        self.assertEqual(["Данные"], [д["протокол"] for д in udp["дети"]], "без правила порт 5000 — данные")

    def test_испорченный_и_пропавший_файл(self):
        плохой = self.т / "плохой.pcap"
        плохой.write_bytes(b"not a capture at all")
        хороший = self.файл([кадр_udp(b"k")] * 2)
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(self.т / "нет.pcap"), str(плохой), str(хороший)]},
                                "скорость": 0})
        self.assertEqual(("готово", 2), (ход["состояние"], ход["пакетов"]))
        self.assertTrue(any("нет.pcap удалён" in з for з in ход["заметки"]), ход["заметки"])
        self.assertTrue(any("плохой.pcap: файл не похож на сетевую запись" in з for з in ход["заметки"]), ход["заметки"])

    def test_следом_за_идущим_захватом(self):
        """Папка приёма с сети: прогон ждёт новых кусков, пока захват идёт, и доходит до конца после."""
        захват = self.т / "захват"
        захват.mkdir()
        состояние = захват / "состояние.json"
        состояние.write_text(json.dumps({"состояние": "идёт"}), encoding="utf-8")
        ц = zapis.ЦепочкаPcapng(захват, интерфейс=ИНТЕРФЕЙС, пакетов_куска=3)
        for i in range(4):
            ц.пакет(float(i), кадр_udp(bytes([i])))
        ц.сбросить()
        import queue  # noqa: PLC0415
        к = queue.Queue()
        прогон_ = self.т / "прогон"
        прогон_.mkdir()
        поток = threading.Thread(target=lambda: progon.Прогон(прогон_, {"источник": {"вид": "захват", "папка": str(захват)},
                                                                        "скорость": 0}, к).работать())
        поток.start()
        ход = lambda: json.loads((прогон_ / "ход.json").read_text(encoding="utf-8"))  # noqa: E731
        self.assertTrue(дождаться(lambda: (прогон_ / "ход.json").exists() and ход()["пакетов"] == 4
                                  and ход()["состояние"] == "ждёт данных", 5), "обработка на лету — 4 пакета и ждёт")
        for i in range(4, 9):
            ц.пакет(float(i), кадр_udp(bytes([i])))
        ц.сбросить()
        self.assertTrue(дождаться(lambda: ход()["пакетов"] == 9, 5), "новые пакеты подхвачены по ходу")
        ц.закрыть()
        состояние.write_text(json.dumps({"состояние": "готово"}), encoding="utf-8")
        поток.join(5)
        self.assertFalse(поток.is_alive())
        self.assertEqual(("готово", 9, 2), (ход()["состояние"], ход()["пакетов"], ход()["файл"]))

    def test_кольцо_удалило_раньше_прогона(self):
        захват = self.т / "захват"
        захват.mkdir()
        (захват / "состояние.json").write_text(json.dumps({"состояние": "готово"}), encoding="utf-8")
        ц = zapis.ЦепочкаPcapng(захват, интерфейс=ИНТЕРФЕЙС, пакетов_куска=1, кусков=2)
        for i in range(5):
            ц.пакет(float(i), кадр_udp(bytes([i])))
        ц.закрыть()
        ход = прогнать(self.т, {"источник": {"вид": "захват", "папка": str(захват)}, "скорость": 0})
        self.assertEqual(2, ход["пакетов"])
        self.assertTrue(any("пропущено кусков: 3" in з for з in ход["заметки"]), ход["заметки"])

    def test_кадры_заметка_о_пропуске_ровно_и_без_пропуска(self):
        def цепочка(папка, удалить=()):
            папка.mkdir()
            (папка / "состояние.json").write_text(json.dumps({"состояние": "готово"}), encoding="utf-8")
            ц = zapis.ЦепочкаPcapng(папка, интерфейс=ИНТЕРФЕЙС, пакетов_куска=1)
            for i in range(3):
                ц.пакет(float(i), кадр_udp(bytes([i])))
            ц.закрыть()
            for н in удалить:
                ц.путь(н).unlink()
            к = progon.Кадры({"вид": "захват", "папка": str(папка)})
            записи = []
            while (з := к.следующий()) not in (None, False):
                записи.append(з)
            к.закрыть()
            return len(записи), к.заметки
        self.assertEqual((3, []), цепочка(self.т / "целый"), "без пропусков — без заметок")
        n, заметки = цепочка(self.т / "без0", удалить=(0,))
        self.assertEqual(2, n)
        self.assertEqual(1, len(заметки))
        self.assertIn("куски 0…0", заметки[0])
        self.assertIn("пропущено кусков: 1", заметки[0])

    def test_кадры_без_файла_состояния_и_дочитывание_последнего(self):
        папка = self.т / "захват"
        папка.mkdir()
        к = progon.Кадры({"вид": "захват", "папка": str(папка)})
        self.assertFalse(к.захват_идёт(), "нет состояния — захват не идёт")
        # Запись, пришедшая между «пусто» и «захват кончился», дочитывается.
        ц = zapis.ЦепочкаPcapng(папка, интерфейс=ИНТЕРФЕЙС)
        ц.пакет(0.0, кадр_udp(b"a"))
        ц.сбросить()
        к = progon.Кадры({"вид": "захват", "папка": str(папка)})
        self.assertIsNotNone(к.следующий())

        def кончился():
            ц.пакет(1.0, кадр_udp(b"b"))
            ц.сбросить()
            return False
        with mock.patch.object(к, "захват_идёт", кончился):
            запись = к.следующий()
        self.assertTrue(запись, "дописанная в последний миг запись не потеряна")
        self.assertEqual(1.0, запись[0])
        к.закрыть()
        ц.закрыть()

    def test_умолчания_прогона(self):
        путь = self.файл([кадр_udp(b"a"), кадр_udp(b"b")])
        п = progon.Прогон(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}}, __import__("queue").Queue())
        self.assertEqual(0.0, п.скорость, "без скорости — максимально")
        п.работать()
        ход = json.loads((self.т / "ход.json").read_text(encoding="utf-8"))
        self.assertEqual((2, 0, "готово"), (ход["пакетов"], ход["ошибок"], ход["состояние"]))

    def test_ход_темп_списки_и_заметки(self):
        путь = self.файл([кадр_udp(b"a")])
        п = progon.Прогон(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}}, __import__("queue").Queue())
        ход = lambda: json.loads((self.т / "ход.json").read_text(encoding="utf-8"))  # noqa: E731
        часы = {"t": 50.0}
        with mock.patch.object(progon.time, "monotonic", lambda: часы["t"]):
            for t, пакетов in ((50.0, 0), (50.0, 0), (51.0, 100), (53.0, 300)):
                часы["t"], п.пакетов = t, пакетов
                п._ход("идёт", сразу=True)                  # два раза в одно время — без деления на ноль
            self.assertEqual(100.0, ход()["темп"], "(300 − 0) / (53 − 50)")
            часы["t"], п.пакетов = 54.5, 400
            п._ход("идёт", сразу=True)
            self.assertEqual(66.7, ход()["темп"], "окно 3 с — отметки 53 и 54,5: (400 − 300) / 1,5")
            часы["t"], п.пакетов = 54.6, 450
            п._ход("идёт")                                 # 0,1 с после прошлого — не пишется
            self.assertEqual(400, ход()["пакетов"])
            часы["t"], п.пакетов = 55.2, 401
            п._ход("идёт")                                 # 0,7 с — пишется
        self.assertEqual(401, ход()["пакетов"])
        п.порты = {"UDP 1": [5, 10], "UDP 2": [3, 900], "UDP 3": [9, 1]}
        п.диалоги = {f"д{i}": [100 - i, i * 10] for i in range(40)}
        п.кадры.заметки = [f"з{i}" for i in range(25)]
        п._ход("идёт", сразу=True)
        х = ход()
        self.assertEqual(["UDP 3", "UDP 1", "UDP 2"], [р[0] for р in х["порты"]], "порты — по числу пакетов")
        self.assertEqual(32, len(х["диалоги"]))
        self.assertEqual(["д39", "д38"], [р[0] for р in х["диалоги"][:2]], "диалоги — по байтам")
        self.assertEqual([f"з{i}" for i in range(5, 25)], х["заметки"], "последние 20 заметок")
        п.кадры.закрыть()

    def test_темп_по_отметкам_времени(self):
        """Скорость ×2: пакеты с отметками 1000,0…1000,4 проходят примерно за 0,2 с."""
        путь = self.т / "время.pcap"
        путь.write_bytes(с.pcap([кадр_udp(bytes([i])) for i in range(5)], времена=[1000.0 + i * 0.1 for i in range(5)]))
        к = __import__("queue").Queue()
        п = progon.Прогон(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 2}, к)
        поток = threading.Thread(target=п.работать)
        начало = time.monotonic()
        поток.start()
        поток.join(10)
        прошло = time.monotonic() - начало
        if поток.is_alive():
            к.put({"к": "стоп"})
            поток.join(5)
        self.assertLess(прошло, 5, "темп — от первой отметки, а не от нуля эпохи")
        self.assertGreaterEqual(прошло, 0.15)
        self.assertEqual(5, п.пакетов)
        self.assertAlmostEqual(0.4, json.loads((self.т / "ход.json").read_text(encoding="utf-8"))["прошло_записи"], 6)

    def test_пауза_и_продолжение_по_отметкам(self):
        """После паузы темп отсчитывается от текущего пакета, а не от нуля."""
        путь = self.т / "пауза.pcap"
        путь.write_bytes(с.pcap([кадр_udp(bytes([i])) for i in range(4)], времена=[5000.0 + i * 0.05 for i in range(4)]))
        к = __import__("queue").Queue()
        п = progon.Прогон(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 1}, к)
        поток = threading.Thread(target=п.работать)
        поток.start()
        self.assertTrue(дождаться(lambda: п.пакетов >= 1, 5))
        к.put({"к": "пауза"})
        self.assertTrue(дождаться(lambda: п.состояние == "пауза", 5))
        к.put({"к": "продолжить"})
        поток.join(8)
        if поток.is_alive():
            к.put({"к": "стоп"})
            поток.join(5)
        self.assertEqual((4, "готово"), (п.пакетов, п.состояние))

    def test_пустой_файл_и_мало_места(self):
        пустой = self.т / "пусто.pcap"
        пустой.write_bytes(с.pcap([]))
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(пустой)]}, "скорость": 0})
        self.assertEqual((0, 0), (ход["пакетов"], ход["прошло_записи"]))
        путь = self.файл([кадр_udp(bytes([i])) for i in range(3)])
        мало = mock.Mock(free=0)
        with mock.patch.object(progon, "ХОД_КАЖДЫЕ", 0.0), \
                mock.patch.object(progon.shutil, "disk_usage", return_value=мало):
            ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0})
            self.assertEqual((3, "готово"), (ход["пакетов"], ход["состояние"]), "без выходных данных место не нужно")
            ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0,
                                    "выход": {"порт": 5004, "срез": 0}})
        self.assertEqual(1, ход["пакетов"])
        self.assertIn("мало места", ход["причина"])

    def test_порты_только_udp_и_tcp(self):
        sctp = с.eth(с.ip(struct.pack("!HHII", 2905, 2905, 0, 0), 132))
        путь = self.файл([sctp, кадр_udp(b"x", порт_к=53)])
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0})
        self.assertEqual(["UDP 53"], [р[0] for р in ход["порты"]])

    def test_диалоги_только_ip_и_счёт_ошибок(self):
        arp = с.eth(bytes.fromhex("000108000604000102030405060a000001000000000000000a000002"), тип=0x0806)
        оборван = кадр_udp(b"abc")[:14 + 10]
        путь = self.файл([arp, оборван, кадр_udp(b"ok")])
        ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0})
        self.assertEqual(["10.0.0.1 ↔ 10.0.0.2"], [д[0] for д in ход["диалоги"]], "ARP и оборванный IPv4 (адреса MAC) — не диалог IP")
        self.assertEqual(1, ход["ошибок"])

    def test_процесс_прогона_выходит_при_открытом_stdin(self):
        """Прогон отдельным процессом кончается сам, хотя сервер держит его stdin открытым."""
        import os  # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        import sys  # noqa: PLC0415
        путь = self.файл([кадр_udp(b"a")])
        (self.т / "задание.json").write_text(json.dumps({"источник": {"вид": "файлы", "файлы": [str(путь)]},
                                                         "скорость": 0}), encoding="utf-8")
        корень = Path(__file__).resolve().parent.parent / "src"
        процесс = subprocess.Popen([sys.executable, "-m", "reportgen.setevoy.zahvat_seti.progon", str(self.т)],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env={**os.environ, "PYTHONPATH": str(корень)})
        try:
            self.assertEqual(0, процесс.wait(60))
        finally:
            процесс.kill()
            процесс.stdin.close()
            процесс.wait()
        self.assertEqual("готово", json.loads((self.т / "ход.json").read_text(encoding="utf-8"))["состояние"])

    def test_ожидание_команд_и_отправитель_выхода(self):
        кадры = [кадр_udp(b"AA", от="10.0.0.1"), кадр_udp(b"BB", от="10.0.0.9")]
        путь = self.файл(кадры)
        к = __import__("queue").Queue()
        п = progon.Прогон(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]},
                                   "выход": {"порт": 5004, "срез": 0, "источник": "10.0.0.1"}}, к)
        начало = time.monotonic()
        п._команды(0.2)
        self.assertGreaterEqual(time.monotonic() - начало, 0.15, "ждёт команду, а не крутится вхолостую")
        threading.Timer(0.05, lambda: к.put({"к": "пауза"})).start()
        п._команды(0.3)
        self.assertTrue(п.пауза, "команда, пришедшая во время ожидания, разобрана")
        п.пауза = False
        п.работать()
        self.assertEqual(b"AA", (self.т / "выход.bin").read_bytes(), "выход — только от заданного отправителя")

    def test_ошибка_внутри_не_роняет(self):
        путь = self.файл([кадр_udp(b"a")])
        with mock.patch.object(progon.Дерево, "учесть", side_effect=RuntimeError("сбой")):
            ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0})
        self.assertEqual(("ошибка", "RuntimeError: сбой"), (ход["состояние"], ход["ошибка"]))

    def test_мало_места_останавливает_выход(self):
        путь = self.файл([кадр_udp(b"a")] * 3)
        место = shutil.disk_usage(self.т)._replace(free=progon.ЗАПАС_ДИСКА - 1)
        with mock.patch.object(progon, "ХОД_КАЖДЫЕ", 0.0), \
                mock.patch.object(progon.shutil, "disk_usage", return_value=место):
            ход = прогнать(self.т, {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": 0,
                                    "выход": {"порт": 5004}})
        self.assertEqual(1, ход["пакетов"])
        self.assertIn("мало места", ход["причина"])

    def test_проверка_скорости_и_выхода(self):
        for з, итог in ((0, 0.0), ("1", 1.0), ("2,5", 2.5), (" ", 0.0), (0.01, 0.01), (1000, 1000.0)):
            self.assertEqual(итог, progon.проверить_скорость(з), з)
        for з in (True, -1, 0.001, 1000.5, "x", float("nan")):
            with self.assertRaises(ValueError, msg=з):
                progon.проверить_скорость(з)
        self.assertIsNone(progon.проверить_выход(None))
        self.assertEqual({"порт": 5, "срез": "rtp", "упорядочить": True, "источник": "10.0.0.1"},
                         progon.проверить_выход({"порт": "5", "срез": "RTP", "упорядочить": 1, "источник": "10.0.0.1"}))
        self.assertEqual(12, progon.проверить_выход({"порт": 5, "срез": 12})["срез"])
        self.assertEqual((65535, 0), tuple(progon.проверить_выход({"порт": 65535, "срез": 0})[к] for к in ("порт", "срез")))
        self.assertEqual(65535, progon.проверить_выход({"порт": 1, "срез": 65535})["срез"])
        self.assertEqual(0, progon.проверить_выход({"порт": 5})["срез"], "без среза — 0")
        for плохо in ([1], {"порт": 0}, {"порт": 65536}, {"порт": 5, "срез": -1}, {"порт": 5, "срез": 65536},
                      {"порт": 5, "источник": "1.2"}):
            with self.assertRaises(ValueError, msg=плохо):
                progon.проверить_выход(плохо)


class ПрогоныTests(Папка):
    def прогоны(self, **к):
        п = progon.Прогоны(self.т / "progon", процессом=False, **к)
        self.addCleanup(п.остановить_все)
        return п

    def задание(self, n=5, скорость=0):
        путь = self.т / f"з{n}.pcap"
        путь.write_bytes(с.pcap([кадр_udp(bytes([i])) for i in range(n)], времена=[i * 0.2 for i in range(n)]))
        return {"источник": {"вид": "файлы", "файлы": [str(путь)]}, "скорость": скорость}

    def test_запуск_команды_сначала_удаление(self):
        п = self.прогоны()
        ид = п.начать(владелец=1, кто="И", источник={"вид": "pakety", "ид": "x"}, задание=self.задание(20, 1), имя="проба")
        self.assertTrue(дождаться(lambda: п.состояние(ид)["пакетов"] >= 1))
        п.команда(ид, "пауза")
        self.assertTrue(дождаться(lambda: п.состояние(ид)["состояние"] == "пауза", 3))
        п.команда(ид, "скорость", "0")
        self.assertEqual(0.0, п._мета(ид)["скорость"], "скорость запомнена в мете")
        п.команда(ид, "продолжить")
        # «готово» пишется последним делом потока — «жив» гаснет следом, когда поток вышел.
        self.assertTrue(дождаться(lambda: п.состояние(ид)["состояние"] == "готово" and not п.состояние(ид)["жив"], 5))
        с_ = п.состояние(ид)
        self.assertEqual((20, 1, "проба", False), (с_["пакетов"], с_["владелец"], с_["имя"], с_["жив"]))
        with self.assertRaises(ValueError):
            п.команда(ид, "пауза")                          # закончился — только «сначала»
        п.команда(ид, "стоп")                               # стоп законченного — не ошибка
        with self.assertRaises(ValueError):
            п.команда(ид, "назад")
        with self.assertRaises(ValueError):
            п.команда(ид, "скорость", "-3")
        п.сначала(ид, {**self.задание(3), "выход": {"порт": 5004, "срез": 0, "упорядочить": False, "источник": ""}})
        self.assertTrue(дождаться(lambda: п.состояние(ид)["состояние"] == "готово" and п.состояние(ид)["пакетов"] == 3, 5))
        self.assertEqual(3, п.выход(ид).stat().st_size, "выход после «сначала» — заново")
        self.assertEqual({"порт": 5004, "срез": 0, "упорядочить": False, "источник": ""}, п._мета(ид)["выход"])
        self.assertIn("перезапущен", п._мета(ид))
        self.assertEqual(ид, п.найти(1, {"вид": "pakety", "ид": "x"}))
        self.assertIsNone(п.найти(2, {"вид": "pakety", "ид": "x"}))
        п.удалить(ид)
        with self.assertRaises(KeyError):
            п.состояние(ид)
        for плохой in ("../x", "", "20260101-000000-zzzzzz"):
            with self.assertRaises(KeyError):
                п.состояние(плохой)
            with self.assertRaises(KeyError):
                п.выход(плохой)
        with self.assertRaises(KeyError):
            п.сначала("20260101-000000-abcdef")

    def test_процесс_среда_мета_и_прерванный_без_хода(self):
        import os  # noqa: PLC0415
        self.assertTrue(progon.Прогоны(self.т / "умолч").процессом, "по умолчанию — отдельным процессом")
        вызовы = []

        class Процесс:
            stdin = mock.Mock()

            def __init__(self, аргументы, **к):
                вызовы.append((аргументы, к))

            def poll(self):
                return 0                                    # «процесс» сразу кончился, ход не писал

            def wait(self, *_):
                return 0
        п = progon.Прогоны(self.т / "проц", процессом=True, фабрика_процесса=Процесс)
        import reportgen  # noqa: PLC0415
        корень = str(Path(reportgen.__file__).resolve().parents[1])
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/прежний"}):
            ид = п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "a"}, задание={"источник": {}})
        self.assertEqual(корень + os.pathsep + "/прежний", вызовы[0][1]["env"]["PYTHONPATH"])
        self.assertEqual(["-m", "reportgen.setevoy.zahvat_seti.progon"], вызовы[0][0][1:3])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTHONPATH", None)
            п.начать(владелец=2, кто="", источник={"вид": "pakety", "ид": "b"}, задание={"источник": {}, "скорость": 0})
        self.assertEqual(корень, вызовы[1][1]["env"]["PYTHONPATH"])
        self.assertEqual(0, п._мета(ид)["скорость"], "без скорости — 0 (максимально)")
        (п.папка / ид / "журнал.txt").write_text("x" * 400 + "y" * 600, encoding="utf-8")
        с_ = п.состояние(ид)
        self.assertEqual(("ошибка", 0, 0), (с_["состояние"], с_["пакетов"], с_["байт"]),
                         "хода нет, процесс кончился — прервался")
        self.assertEqual("прогон прервался: " + "y" * 600, с_["ошибка"], "хвост журнала — 600 знаков")
        with self.assertRaises(KeyError):
            п.выход(ид)                                      # у прогона без выходных данных файла нет
        п.сначала(ид, {"источник": {}})
        self.assertEqual(0, п._мета(ид)["скорость"])
        п.сначала(ид, {"источник": {}, "скорость": 2})
        self.assertEqual(2, п._мета(ид)["скорость"])
        ид3 = п.начать(владелец=3, кто="", источник={"вид": "pakety", "ид": "c"}, задание={"источник": {}, "скорость": 4})
        self.assertEqual(4, п._мета(ид3)["скорость"])

    def test_найти_последний_прогон_источника(self):
        п = self.прогоны()
        for ид in ("20260101-000000-aaaaaa", "20260102-000000-bbbbbb", "20251231-235959-cccccc"):
            (п.папка / ид).mkdir(parents=True)
            (п.папка / ид / "прогон.json").write_text(json.dumps({"ид": ид, "владелец": 1, "источник": {"вид": "x"}}),
                                                      encoding="utf-8")
        self.assertEqual("20260102-000000-bbbbbb", п.найти(1, {"вид": "x"}))

    def test_пределы_одновременности(self):
        п = self.прогоны()
        долгое = self.задание(50, 0.01)
        иды = [п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": str(i)}, задание=долгое) for i in range(2)]
        with self.assertRaises(ValueError) as к:
            п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "3"}, задание=долгое)
        self.assertIn("у вас уже идут", str(к.exception))
        for i in range(2):
            иды.append(п.начать(владелец=2, кто="", источник={"вид": "pakety", "ид": str(i)}, задание=долгое))
        with self.assertRaises(ValueError) as к:
            п.начать(владелец=3, кто="", источник={"вид": "pakety", "ид": "3"}, задание=долгое)
        self.assertIn("на сервере уже идут", str(к.exception))
        п.остановить(иды[0])
        self.assertEqual("остановлен", п.состояние(иды[0])["состояние"])
        п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "3"}, задание=долгое)

    def test_прерванный_процесс_и_перезапуск_сервера(self):
        п = self.прогоны()
        ид = п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "x"}, задание=self.задание(3))
        self.assertTrue(дождаться(lambda: п.состояние(ид)["состояние"] == "готово"))
        ход = self.т / "progon" / ид / "ход.json"
        ход.write_text(json.dumps({"состояние": "идёт", "пакетов": 1}), encoding="utf-8")
        (self.т / "progon" / ид / "журнал.txt").write_text("Traceback: упал", encoding="utf-8")
        с_ = п.состояние(ид)
        self.assertEqual("ошибка", с_["состояние"], "ход «идёт», а процесса нет — прервался")
        self.assertIn("упал", с_["ошибка"])
        новый = progon.Прогоны(self.т / "progon", процессом=False)
        self.assertEqual(("остановлен", "сервер перезапускался во время прогона"),
                         (json.loads(ход.read_text(encoding="utf-8"))["состояние"],
                          json.loads(ход.read_text(encoding="utf-8"))["причина"]))
        self.assertEqual(ид, новый.найти(1, {"вид": "pakety", "ид": "x"}))

    def test_уборка_старых(self):
        п = self.прогоны()
        иды = []
        with mock.patch.object(progon, "ХРАНИТЬ", 2):
            for i in range(4):
                иды.append(п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": str(i)}, задание=self.задание(1)))
                self.assertTrue(дождаться(lambda: not п._идут[иды[-1]].жив()))
                time.sleep(1.05)                              # ид — по времени до секунды
            оставшиеся = sorted(ф.name for ф in (self.т / "progon").iterdir())
            # Только что начатый при уборке мог ещё идти (он не в счёт) или уже кончиться.
            self.assertIn(оставшиеся, (sorted(иды)[-3:], sorted(иды)[-2:]))
            п.начать(владелец=2, кто="", источник={"вид": "pakety", "ид": "чужой"}, задание=self.задание(1))
            п._прибрать(1)
            self.assertEqual(sorted(иды)[-2:], sorted(ф.name for ф in (self.т / "progon").iterdir()
                                                     if п._мета(ф.name).get("владелец") == 1),
                             "свои законченные сверх ХРАНИТЬ удаляются, чужие — не трогаются")
            self.assertEqual(3, len(list((self.т / "progon").iterdir())))

    def test_отдельным_процессом(self):
        п = progon.Прогоны(self.т / "progon", процессом=True)
        self.addCleanup(п.остановить_все)
        ид = п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "x"},
                      задание={**self.задание(4), "выход": {"порт": 5004, "срез": 0}})
        self.assertIsNotNone(п._идут[ид].процесс)
        self.assertTrue(дождаться(lambda: п.состояние(ид)["состояние"] == "готово", 30), п.состояние(ид))
        self.assertEqual(4, п.состояние(ид)["пакетов"])
        долгое = self.задание(100, 1)
        ид2 = п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "y"}, задание=долгое)
        self.assertTrue(дождаться(lambda: п.состояние(ид2)["состояние"] == "идёт", 30))
        п.остановить(ид2)
        self.assertEqual("остановлен", п.состояние(ид2)["состояние"])
        self.assertFalse(п.состояние(ид2)["жив"])

    def test_законченные_процессы_не_копятся(self):
        # Сервер живёт неделями, а прогон на лету — у каждого захвата: законченный процесс не должен
        # держать открытый канал stdin (дескриптор) и место в таблице идущих.
        п = progon.Прогоны(self.т / "progon", процессом=True)
        self.addCleanup(п.остановить_все)
        ид = п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "x"}, задание=self.задание(2))
        процесс = п._идут[ид].процесс
        self.assertTrue(дождаться(lambda: п.состояние(ид)["состояние"] == "готово", 30), п.состояние(ид))
        self.assertTrue(дождаться(lambda: процесс.poll() is not None, 10))
        ид2 = п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "y"}, задание=self.задание(2))
        self.assertNotIn(ид, п._идут)
        self.assertTrue(процесс.stdin.closed, "канал команд законченного процесса закрыт")
        self.assertIn(ид2, п._идут)
        self.assertEqual("готово", п.состояние(ид)["состояние"], "состояние законченного — по его ходу")

    def test_процесс_не_запустился_потоком(self):
        def отказ(*а, **к):
            raise OSError("нет python")
        п = self.прогоны(фабрика_процесса=отказ)
        п.процессом = True
        ид = п.начать(владелец=1, кто="", источник={"вид": "pakety", "ид": "x"}, задание=self.задание(2))
        self.assertTrue(дождаться(lambda: п.состояние(ид)["состояние"] == "готово"))
        self.assertIn("идёт потоком", (self.т / "progon" / ид / "журнал.txt").read_text(encoding="utf-8"))


# -- «Декодировать как» -------------------------------------------------------------------------

def стек(кадр, правила=(), канал="Ethernet"):
    как = {"правила": [дк.проверить_правило(п) for п in правила]} if правила else None
    return разобрать_пакет(кадр, канал, как=как)


class РеестрTests(unittest.TestCase):
    def test_все_группы_и_известные_разборщики(self):
        р = дк.реестр()
        группы = {в.группа for в in р.values()}
        self.assertLessEqual({"канал", "EtherType", "IP", "UDP", "TCP"}, группы)
        for имя in ("Ethernet", "IPv4", "ARP", "UDP", "TCP", "DNS", "RTP/RTCP", "VLAN (802.1Q)"):
            self.assertIn(имя, р, имя)
        self.assertEqual("IP", р["UDP"].группа)
        self.assertEqual("EtherType 0x0806", р["ARP"].ключ)
        список = дк.список_разборщиков()
        self.assertEqual("канал", список[0]["группа"], "сначала кадр целиком")
        self.assertEqual(set(р), {з["имя"] for з in список})
        self.assertEqual({"имя", "группа", "описание", "ключ"}, set(список[0]))
        self.assertIs(р, дк.реестр(), "без изменений таблиц — тот же реестр")

    def test_новый_разборщик_в_таблице_попадает_в_реестр(self):
        from reportgen.setevoy import razbor  # noqa: PLC0415

        def мой(р, м):
            """MYPROTO (проба): тестовый разборщик."""
            у = р.уровень("Мой", "Мой-протокол", м)
            у.итог = "мой"
        razbor.ДОП_ETHERTYPE[0x88B5] = мой
        try:
            р = дк.реестр()
            self.assertIn("MYPROTO", р)
            self.assertEqual(("EtherType", "EtherType 0x88b5", 2), (р["MYPROTO"].группа, р["MYPROTO"].ключ,
                                                                    р["MYPROTO"].нужно))
            п = стек(кадр_udp(b"x"), [{"вид": "поле", "над": "Ethernet", "поле": "eth.type", "значение": 2048, "как": "MYPROTO"}])
            self.assertEqual(["Ethernet", "Мой"], п.стек, "новый разборщик сразу годится для «Разобрать как…»")
        finally:
            del razbor.ДОП_ETHERTYPE[0x88B5]
        self.assertNotIn("MYPROTO", дк.реестр())

    def test_имя_из_описания_и_запас(self):
        def f(р, м):
            """PIM (RFC 7761): сообщения."""
        def g_udp(р, м):
            """нижний регистр — не имя."""
        self.assertEqual("PIM", дк._имя_из(f, "запас"))
        self.assertEqual("запас", дк._имя_из(g_udp, "запас"))
        self.assertEqual("G", дк._имя_из(g_udp, ""))
        self.assertEqual(3, дк._обязательных(lambda р, м, к, п=0: None))
        self.assertEqual(2, дк._обязательных(lambda р, м: None))


class ПравилаTests(unittest.TestCase):
    def test_проверка_правил(self):
        п = дк.проверить_правило({"вид": "поле", "над": "UDP", "поле": "UDP.Port", "значение": "0x1388", "как": "DNS"})
        self.assertEqual({"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "DNS", "вкл": True},
                         {к: з for к, з in п.items() if к != "ид"})
        self.assertRegex(п["ид"], r"^[0-9a-f]{8}$")
        self.assertEqual(5000, дк.проверить_правило({**п, "значение": "5000"})["значение"])
        self.assertEqual("IPv4", дк.проверить_правило({**п, "значение": "IPv4"})["значение"])
        п = дк.проверить_правило({"вид": "протокол", "протокол": "GTP", "действие": "данные", "как": "DNS", "вкл": False})
        self.assertEqual(("", False, ""), (п["как"], п["вкл"], п["после"]), "«данные» — без разборщика")
        плохие = [
            [], {"вид": "x"}, {"вид": "поле", "над": "UDP", "поле": "udp port", "значение": 1, "как": "DNS"},
            {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 1},
            {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 1, "как": "НетТакого"},
            {"вид": "поле", "над": "", "поле": "udp.port", "значение": 1, "как": "DNS"},
            {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": True, "как": "DNS"},
            {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": -1, "как": "DNS"},
            {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": "a<b", "как": "DNS"},
            {"вид": "поле", "над": "U\x01", "поле": "udp.port", "значение": 1, "как": "DNS"},
            {"вид": "протокол", "протокол": "GTP", "действие": "вместо"},
            {"вид": "протокол", "протокол": "GTP", "действие": "сжечь"},
            {"вид": "протокол", "протокол": "x" * 65},
            {"вид": "протокол", "протокол": "GTP", "ид": "zz"},
        ]
        for плохое in плохие:
            with self.assertRaises(ValueError, msg=плохое):
                дк.проверить_правило(плохое)
        with self.assertRaises(ValueError):
            дк.проверить_правила({"a": 1})
        with self.assertRaises(ValueError):
            дк.проверить_правила([{"вид": "протокол", "протокол": "A"}] * (дк.ПРАВИЛ_ДО + 1))
        with self.assertRaises(ValueError):
            дк.проверить_правила([{"вид": "протокол", "протокол": "A", "ид": "00000001"},
                                  {"вид": "протокол", "протокол": "B", "ид": "00000001"}])

    def test_описание_словами(self):
        о = [дк.описать(дк.проверить_правило(п)) for п in (
            {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "DNS"},
            {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "данные"},
            {"вид": "протокол", "протокол": "GTP"},
            {"вид": "протокол", "протокол": "GTP", "после": "UDP", "действие": "снять"},
            {"вид": "протокол", "протокол": "GTP", "действие": "снять", "как": "IPv4"},
            {"вид": "протокол", "протокол": "GTP", "действие": "вместо", "как": "DNS"})]
        self.assertEqual(["UDP: udp.port = 5000 → DNS", "UDP: udp.port = 5000 → данные (не разбирать)",
                          "GTP: убрать — байты и всё вложенное как данные",
                          "GTP поверх UDP: снять заголовок, вложенное — как обычно",
                          "GTP: снять заголовок, вложенное — IPv4", "GTP: разбирать как DNS"], о)


class РазборПоПравиламTests(unittest.TestCase):
    dns = staticmethod(lambda порт=5000: кадр_udp(с.dns_запрос("example.com"), порт_к=порт))

    def test_без_правил_как_обычно(self):
        кадр = self.dns()
        обычный = разобрать_пакет(кадр)
        с_пустыми = разобрать_пакет(кадр, как={"правила": []})
        self.assertEqual(обычный.стек, с_пустыми.стек)
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], обычный.стек)
        self.assertFalse(hasattr(с_пустыми, "правила"))

    def test_по_полю_порт(self):
        п = стек(self.dns(), [{"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "DNS", "ид": "00000001"}])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "DNS"], п.стек)
        self.assertEqual(["00000001"], п.правила)
        self.assertEqual(len(self.dns()), п.исходная_длина, "без исходной длины — длина записанного")
        п2 = дк.разобрать_по_правилам(self.dns(), исходная_длина=1500, как={"правила": [дк.проверить_правило(
            {"вид": "протокол", "протокол": "UDP"})]}, номер=7, время=2.5)
        self.assertEqual((1500, 7, 2.5), (п2.исходная_длина, п2.номер, п2.время))
        self.assertIn("example.com", п.инфо)
        п = стек(self.dns(5001), [{"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "DNS"}])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], п.стек, "другой порт — правило молчит")
        п = стек(self.dns(), [{"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "DNS", "вкл": False}])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], п.стек, "выключенное правило не действует")

    def test_по_полю_ethertype_текстом_и_числом(self):
        кадр = self.dns()
        for значение in ("0x0800", 2048, "0x800"):
            п = стек(кадр, [{"вид": "поле", "над": "Ethernet", "поле": "eth.type", "значение": значение, "как": "данные"}])
            self.assertEqual(["Ethernet", "Данные"], п.стек, значение)

    def test_по_полю_не_подошло(self):
        п = стек(self.dns(), [{"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "IPv6"}])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], п.стек, "29 байт — не IPv6 (заголовок 40)")
        self.assertTrue(any("разобрать как IPv6: данные не подошли" in о for о in п.ошибки), п.ошибки)
        self.assertEqual(["Ethernet", "IPv4", "UDP"], [у.протокол for у in п.уровни[:3]], "откат — только своих уровней")

    def test_убрать_протокол_везде_и_поверх(self):
        кадр = self.dns()
        п = стек(кадр, [{"вид": "протокол", "протокол": "UDP"}])
        self.assertEqual(["Ethernet", "IPv4", "Данные"], п.стек)
        self.assertEqual(len(кадр) - 14 - 20, п.уровни[-1].длина, "данные — до конца IP")
        п = стек(кадр, [{"вид": "протокол", "протокол": "UDP", "после": "IPv6"}])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], п.стек, "только поверх IPv6 — здесь не действует")
        п = стек(кадр, [{"вид": "протокол", "протокол": "UDP", "после": "IPv4"}])
        self.assertEqual(["Ethernet", "IPv4", "Данные"], п.стек)

    def test_снять_заголовок(self):
        кадр = self.dns()
        п = стек(кадр, [{"вид": "протокол", "протокол": "IPv4", "действие": "снять"}])
        self.assertEqual(["Ethernet", "Данные", "UDP", "Данные"], п.стек)
        self.assertEqual((14, 20), (п.уровни[1].смещение, п.уровни[1].длина))
        п = стек(кадр, [{"вид": "протокол", "протокол": "UDP", "действие": "снять", "как": "DNS"}])
        self.assertEqual(["Ethernet", "IPv4", "Данные", "DNS"], п.стек)
        self.assertEqual(8, п.уровни[2].длина)
        п = стек(кадр, [{"вид": "протокол", "протокол": "UDP", "действие": "снять", "после": "IPv6"}])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], п.стек, "«после» не совпало")

    def test_вместо(self):
        кадр = self.dns()
        п = стек(кадр, [{"вид": "протокол", "протокол": "IPv4", "действие": "вместо", "как": "ARP"}])
        self.assertEqual(["Ethernet", "Данные"], п.стек)
        self.assertTrue(п.ошибки)
        # VXLAN-подобное вложение: UDP 4789 → кадр Ethernet целиком.
        внутри = кадр_udp(b"hello", порт_к=9)
        внешний = кадр_udp(b"\x08\0\0\0\0\0\x01\0" + внутри, порт_к=4789)
        п = стек(внешний, [{"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 4789, "как": "Ethernet"}])
        self.assertEqual(["Ethernet", "IPv4", "UDP"], п.стек[:3])

    def test_зацикливание_ограничено(self):
        """«UDP 4789 → Ethernet» с тем же кадром внутри снова и снова — не больше ПЕРЕХВАТОВ_ДО поворотов."""
        кадр = кадр_udp(b"x", порт_к=4789)
        for _ in range(12):
            кадр = кадр_udp(кадр, порт_к=4789)
        п = стек(кадр, [{"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 4789, "как": "Ethernet"}])
        self.assertEqual(дк.ПЕРЕХВАТОВ_ДО + 1, п.стек.count("Ethernet"))

    def test_конец_вложенного_по_длине_udp(self):
        """Кадр с дополнением Ethernet до 60 байт: данные — по длине UDP, не до конца кадра."""
        кадр = кадр_udp(b"ab")
        кадр += b"\0" * (60 - len(кадр))
        п = стек(кадр, [{"вид": "протокол", "протокол": "Данные", "после": "UDP", "действие": "вместо", "как": "данные"}])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual(2, п.уровни[-1].длина)

    def test_таблица_узнаёт_изменение_на_месте(self):
        правила = [дк.проверить_правило({"вид": "протокол", "протокол": "UDP"})]
        т1 = дк.таблица(правила)
        self.assertIs(т1, дк.таблица(правила))
        правила[0]["вкл"] = False
        т2 = дк.таблица(правила)
        self.assertIsNot(т1, т2)
        self.assertTrue(т2.пуста())

    def test_с_правилами(self):
        правила = [дк.проверить_правило({"вид": "протокол", "протокол": "UDP"}),
                   дк.проверить_правило({"вид": "протокол", "протокол": "TCP", "вкл": False})]
        итог = дк.с_правилами({"udp:5000": "DNS", "правила": ["старое"]}, правила)
        self.assertEqual({"udp:5000": "DNS", "правила": [правила[0]]}, итог)
        self.assertEqual({}, дк.с_правилами(None, []))

    def test_правила_через_проверку_как_анализатора(self):
        from reportgen.setevoy.prilozh import проверить_как  # noqa: PLC0415
        итог = проверить_как({"udp:5000": "DNS", "правила": [{"вид": "протокол", "протокол": "UDP"}]})
        self.assertEqual("UDP", итог["правила"][0]["протокол"])
        with self.assertRaises(ValueError):
            проверить_как({"правила": [{"вид": "x"}]})


class ДКГраницыTests(unittest.TestCase):
    """Границы «Декодировать как» — по итогам мутационной проверки."""

    def test_разборщик_вернул_нет(self):
        from reportgen.setevoy.pole import Пакет  # noqa: PLC0415
        from reportgen.setevoy.razbor import Разбор  # noqa: PLC0415

        def отказ(р, м):
            р.уровень("Проба", "Проба", м)
            return False

        def ок(р, м, конец):
            р.уровень("Проба", "Проба", м).длина = конец - м
        р = Разбор(Пакет(1, 0.0, b"\0" * 20, 20, "Ethernet"), None, None)
        self.assertFalse(дк.Разборщик("П", "канал", "", "", отказ, 2).вызвать(р, 0, 20))
        self.assertEqual([], р.п.уровни, "«нет» от разборщика — его уровни откатываются")
        self.assertTrue(дк.Разборщик("П", "канал", "", "", ок, 3).вызвать(р, 4, 20))
        self.assertEqual([("Проба", 4, 16)], [(у.протокол, у.смещение, у.длина) for у in р.п.уровни])

    def test_псевдозаголовок_для_сумм(self):
        """UDP, разобранный по правилу, проверяет сумму с адресами ближайшего IP ниже (или нулями)."""
        правило = [{"вид": "протокол", "протокол": "UDP", "действие": "вместо", "как": "UDP"}]
        for кадр in (кадр_udp(b"abcde"),
                     с.eth(с.ip6(с.udp(b"abcde", 1, 2, src="2001:db8::1", dst="2001:db8::2", v6=True), 17), тип=0x86DD)):
            п = стек(кадр, правило)
            udp = next(у for у in п.уровни if у.протокол == "UDP")
            сумма = next(ф for ф in udp.поля if ф.ключ == "udp.checksum")
            self.assertIn("[верна]", сумма.текст, п.стек)
            self.assertEqual([], п.ошибки)
        # Под UDP нет IP (правило «Ethernet → UDP»): псевдозаголовок — нули, как у IPv4.
        правило = [{"вид": "поле", "над": "Ethernet", "поле": "eth.type", "значение": "0x88b5", "как": "UDP"}]
        верный = с.eth(с.udp(b"xyz", 7, 9, src="0.0.0.0", dst="0.0.0.0"), тип=0x88B5)
        п = стек(верный, правило)
        self.assertEqual(["Ethernet", "UDP", "Данные"], п.стек)
        self.assertIn("[верна]", next(ф for ф in п.уровни[1].поля if ф.ключ == "udp.checksum").текст)
        без_суммы = с.eth(struct.pack("!HHHH", 7, 9, 11, 0) + b"xyz", тип=0x88B5)
        п = стек(без_суммы, правило)
        self.assertEqual("0x0000 (не используется)", next(ф for ф in п.уровни[1].поля if ф.ключ == "udp.checksum").текст,
                         "IPv4-вид нулевого псевдозаголовка: сумма 0 — «не считалась» (RFC 768)")

    def test_служебные_реестра(self):
        self.assertEqual(3, дк._обязательных(object()), "без подписи — как у большинства: (р, м, конец)")
        self.assertEqual(4, дк._обязательных(lambda р, м, к, п: None))

        def длинный(р, м):
            """Раз два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать
            четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать двадцать двадцать один
            двадцать два двадцать три двадцать четыре двадцать пять.

            Второй абзац в описание не идёт."""
        о = дк._описание(длинный)
        self.assertEqual(240, len(о))
        self.assertNotIn("\n", о)
        self.assertNotIn("Второй", дк._описание(длинный))

    def test_ключи_встроенных_разборщиков(self):
        р = дк.реестр()
        ключи = {"VLAN (802.1Q)": "EtherType 0x8100", "MPLS": "EtherType 0x8847", "PPPoE": "EtherType 0x8863",
                 "LLDP": "EtherType 0x88cc", "CESoETH (MEF 8)": "EtherType 0x88d8", "TCP": "протокол IP 6",
                 "UDP": "протокол IP 17", "ICMP": "протокол IP 1", "ICMPv6": "протокол IP 58", "IGMP": "протокол IP 2",
                 "GRE": "протокол IP 47", "ESP": "протокол IP 50", "AH": "протокол IP 51", "VRRP": "протокол IP 112",
                 "SCTP": "протокол IP 132", "OSPF": "протокол IP 89", "LLC (802.2)": "длина вместо EtherType"}
        for имя, ключ in ключи.items():
            self.assertIn(имя, р, имя)
            self.assertEqual(ключ, р[имя].ключ, имя)
        self.assertEqual("кадр целиком с этого места", р["Ethernet"].описание)
        self.assertIn("по признакам заголовка", р["Авто (по признакам)"].описание)
        for группа in {в.группа for в in р.values()}:
            свои = [в for в in р.values() if в.группа == группа]
            self.assertEqual(len(свои), len({id(в.функция) for в in свои}), f"{группа}: разборщик — один раз")
            self.assertEqual(len(свои), len({в.имя.lower() for в in свои}), f"{группа}: имена без повторов")

    def test_значения_полей_на_границах(self):
        for з in (0, 1, (1 << 64) - 1):
            self.assertEqual(з, дк._значение(з))
        for з in (-1, 1 << 64):
            with self.assertRaises(ValueError):
                дк._значение(з)
        self.assertEqual(дк.ПРАВИЛ_ДО, len(дк.проверить_правила(
            [{"вид": "протокол", "протокол": f"P{i}"} for i in range(дк.ПРАВИЛ_ДО)])))

    def test_таблица_пуста_и_включено_по_умолчанию(self):
        self.assertTrue(дк.Таблица([]).пуста())
        for правило in ({"вид": "протокол", "протокол": "A", "действие": "данные"},
                        {"вид": "протокол", "протокол": "A", "действие": "снять"},
                        {"вид": "поле", "над": "A", "поле": "a.b", "значение": 1, "как": "данные"}):
            self.assertFalse(дк.Таблица([правило]).пуста(), правило)
        self.assertTrue(дк.Таблица([{"вид": "протокол", "протокол": "A", "действие": "данные", "вкл": False}]).пуста())

    def test_совпадение_поля(self):
        from reportgen.setevoy.pole import Уровень  # noqa: PLC0415
        у = Уровень("X", "X", 0, 4)
        у.поле("Код", "x.kod", "Альфа (1)", 0, 1, 1)
        у.поле("Тип", "x.tip", "7", 1, 1, "строка")
        self.assertTrue(дк._совпало(у, "x.kod", "Альфа"), "по первому слову текста")
        self.assertTrue(дк._совпало(у, "x.kod", 1), "по сырому")
        self.assertTrue(дк._совпало(у, "x.tip", 7), "по тексту")
        self.assertFalse(дк._совпало(у, "x.kod", "Бета"))
        self.assertFalse(дк._совпало(у, "x.nет", 1))
        self.assertIsNone(дк._число(у, "x.tip"), "не целое — не длина")
        self.assertIsNone(дк._число(у, "x.нет"))
        self.assertEqual(1, дк._число(у, "x.kod"))

    def test_конец_вложенного(self):
        """Дополнение кадра Ethernet не попадает в данные: конец — по длине UDP, IPv4, IPv6."""
        def дополнить(к):
            return к + b"\xEE" * (80 - len(к))
        п = стек(дополнить(кадр_udp(b"ab")), [{"вид": "протокол", "протокол": "Данные", "после": "UDP", "действие": "вместо",
                                                "как": "данные"}])
        self.assertEqual(2, п.уровни[-1].длина, "по длине UDP")
        п = стек(дополнить(кадр_udp(b"abcd")), [{"вид": "протокол", "протокол": "UDP"}])
        self.assertEqual(12, п.уровни[-1].длина, "по длине IPv4 (Total Length), без дополнения")
        v6 = с.eth(с.ip6(с.udp(b"abcd", 1, 2, src="2001:db8::1", dst="2001:db8::2", v6=True), 17), тип=0x86DD)
        п = стек(дополнить(v6), [{"вид": "протокол", "протокол": "UDP"}])
        self.assertEqual(["Ethernet", "IPv6", "Данные"], п.стек)
        self.assertEqual(12, п.уровни[-1].длина, "по длине IPv6 (Payload Length)")
        # Длина UDP меньше 8 — поле испорчено: конец берётся по IPv4.
        кадр = bytearray(дополнить(кадр_udp(b"abcd")))
        struct.pack_into("!H", кадр, 14 + 20 + 4, 5)
        п = стек(bytes(кадр), [{"вид": "протокол", "протокол": "Данные", "после": "UDP", "действие": "вместо", "как": "данные"}])
        self.assertEqual(4, п.уровни[-1].длина)
        # Отмеченная уровнем ниже нагрузка — её конец.
        from reportgen.setevoy.pole import Пакет  # noqa: PLC0415
        from reportgen.setevoy.razbor import Разбор  # noqa: PLC0415
        р = Разбор(Пакет(1, 0.0, b"\0" * 30, 30, "Ethernet"), None, None)
        р.нагрузка(10, 16)
        self.assertEqual(16, дк.конец_вложенного(р, 10))
        self.assertEqual(30, дк.конец_вложенного(р, 11), "нагрузка с другого места — не та")
        self.assertEqual(30, дк.конец_вложенного(р, 0))

    def test_снятый_заголовок_нулевой_длины(self):
        from reportgen.setevoy.pole import Уровень  # noqa: PLC0415
        з = дк._снятый(Уровень("X", "X", 5, 0), 5, b"\0" * 10)
        self.assertEqual((5, 0, "X убран, 0 байт"), (з.смещение, з.длина, з.итог))
        self.assertEqual(0, дк._снятый(Уровень("X", "X", 5, 0), 3, b"\0" * 10).длина, "не отрицательная")

    def test_снять_с_после_на_первом_уровне(self):
        arp = с.eth(bytes.fromhex("0001080006040001") + b"\0" * 20, тип=0x0806)
        п = стек(arp, [{"вид": "протокол", "протокол": "Ethernet", "после": "ARP", "действие": "снять"}])
        self.assertEqual(["Ethernet", "ARP"], п.стек, "под первым уровнем ничего нет — «после ARP» не совпало")
        п = стек(arp, [{"вид": "протокол", "протокол": "Ethernet", "после": "ARP", "действие": "снять", "как": "данные"}])
        self.assertEqual(["Ethernet", "ARP"], п.стек)
        self.assertEqual([], п.ошибки)
        п = стек(кадр_udp(с.dns_запрос("a.b"), порт_к=5000),
                 [{"вид": "протокол", "протокол": "UDP", "после": "IPv4", "действие": "снять", "как": "DNS"}])
        self.assertEqual(["Ethernet", "IPv4", "Данные", "DNS"], п.стек)
        п = стек(кадр_udp(с.dns_запрос("a.b"), порт_к=5000),
                 [{"вид": "протокол", "протокол": "UDP", "после": "Ethernet", "действие": "снять", "как": "DNS"}])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], п.стек)

    def test_поворотов_не_больше_предела(self):
        кадр = кадр_udp(b"x", порт_к=40000)
        for _ in range(12):
            кадр = кадр_udp(кадр, порт_к=40000)
        п = стек(кадр, [{"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 40000, "как": "Ethernet"}])
        self.assertEqual(дк.ПЕРЕХВАТОВ_ДО + 1, п.стек.count("Ethernet"))
        # Выше — ещё и предел вложенности IP самого разбора (razbor.по_протоколу); цепочка одних
        # Ethernet упирается только в предел поворотов: ровно ПЕРЕХВАТОВ_ДО, не на один больше.
        кадр = b"x" * 50
        for _ in range(14):
            кадр = с.eth(кадр, тип=0x88B5)
        п = стек(кадр, [{"вид": "поле", "над": "Ethernet", "поле": "eth.type", "значение": "0x88b5", "как": "Ethernet"}])
        self.assertEqual(дк.ПЕРЕХВАТОВ_ДО + 1, п.стек.count("Ethernet"))
        self.assertEqual("Данные", п.стек[-1])

    def test_сумма_ноль_поверх_ipv4_по_правилу(self):
        """UDP по правилу поверх IPv4 с суммой 0 — «не используется» (RFC 768: для IPv4 сумма
        необязательна); без IP под UDP, но с длинным кадром — тоже нули, а не байты Ethernet."""
        кадр = bytearray(кадр_udp(b"abcdef"))
        кадр[14 + 20 + 6:14 + 20 + 8] = b"\0\0"
        правило = [{"вид": "протокол", "протокол": "UDP", "действие": "вместо", "как": "UDP"}]
        п = стек(bytes(кадр), правило)
        сумма = next(ф for у in п.уровни if у.протокол == "UDP" for ф in у.поля if ф.ключ == "udp.checksum")
        self.assertEqual("0x0000 (не используется)", сумма.текст)
        правило = [{"вид": "поле", "над": "Ethernet", "поле": "eth.type", "значение": "0x88b5", "как": "UDP"}]
        длинный = с.eth(struct.pack("!HHHH", 7, 9, 8 + 60, 0) + b"z" * 60, тип=0x88B5)
        п = стек(длинный, правило)
        self.assertEqual("0x0000 (не используется)",
                         next(ф for ф in п.уровни[1].поля if ф.ключ == "udp.checksum").текст)

    def test_снять_после_на_втором_и_третьем_уровне(self):
        кадр = кадр_udp(с.dns_запрос("a.b"), порт_к=5000)
        п = стек(кадр, [{"вид": "протокол", "протокол": "UDP", "после": "IPv4", "действие": "снять"}])
        self.assertEqual(["Ethernet", "IPv4", "Данные"], п.стек[:3], "«после IPv4» совпало — заголовок UDP снят")
        self.assertIn("UDP убран", п.уровни[2].итог)
        # Два уровня под местом перехвата: «снять IPv4 после Ethernet, вложенное — как UDP».
        п = стек(кадр, [{"вид": "протокол", "протокол": "IPv4", "после": "Ethernet", "действие": "снять", "как": "UDP"}])
        self.assertEqual(["Ethernet", "Данные", "UDP"], п.стек[:3])
        self.assertIn("IPv4 убран", п.уровни[1].итог)

    def test_пустая_нагрузка_ipv4(self):
        """IPv4 без нагрузки (Total Length = 20) и дополнение кадра за ним: правило-тождество
        «UDP как UDP» даёт тот же стек, что и обычный разбор; «UDP — данными» — пусто."""
        кадр = с.eth(с.ip(b"", 17) + struct.pack("!HHHH", 4000, 5004, 12, 0) + b"wxyz")
        обычный = стек(кадр)
        тождество = стек(кадр, [{"вид": "протокол", "протокол": "UDP", "действие": "вместо", "как": "UDP"}])
        self.assertEqual([(у.протокол, у.смещение, у.длина) for у in обычный.уровни],
                         [(у.протокол, у.смещение, у.длина) for у in тождество.уровни])
        п = стек(кадр, [{"вид": "протокол", "протокол": "UDP", "действие": "вместо", "как": "данные"}])
        self.assertEqual(["Ethernet", "IPv4"], п.стек, "вложенное пустое — данных нет")

    def test_неизвестный_канал_и_оборванный_пакет(self):
        правило = [{"вид": "протокол", "протокол": "GTP", "действие": "вместо", "как": "данные"}]
        п = стек(b"\1\2\3\4", правило, канал="нет такого")
        self.assertEqual([("Данные", 0, 4)], [(у.протокол, у.смещение, у.длина) for у in п.уровни])
        оборван = кадр_udp(b"abc")[:14 + 10]
        обычный, по_правилам = стек(оборван), стек(оборван, правило)
        self.assertEqual(обычный.стек, по_правилам.стек)
        self.assertEqual(обычный.ошибки, по_правилам.ошибки)
        self.assertEqual([у.итог for у in обычный.уровни], [у.итог for у in по_правилам.уровни])
        self.assertTrue(по_правилам.уровни[-1].итог.endswith(" [оборван]"), по_правилам.уровни[-1].итог)

    def test_инфо_как_у_обычного_разбора(self):
        """Правило, которое не сработало, не меняет ни уровней, ни строки «Инфо»."""
        правило = [{"вид": "протокол", "протокол": "GTP", "действие": "вместо", "как": "данные"}]
        arp = с.eth(bytes.fromhex("0001080006040001") + b"\0" * 20, тип=0x0806)
        for кадр in (кадр_udp(с.dns_запрос("a.b"), порт_к=53), кадр_udp(b"\1\2\3", порт_к=40001), arp,
                     с.eth(с.ip(с.icmp(8, 0, b"ping"), 1))):
            обычный, по_правилам = стек(кадр), стек(кадр, правило)
            self.assertTrue(обычный.инфо)
            self.assertEqual(обычный.инфо, по_правилам.инфо)
            self.assertEqual(обычный.стек, по_правилам.стек)

    def test_включённые_правила_и_папка(self):
        без_вкл = {"ид": "a1", "вид": "протокол", "протокол": "GTP"}
        self.assertEqual([без_вкл], дк.с_правилами(None, [без_вкл])["правила"], "без «вкл» — включено")
        self.assertNotIn("правила", дк.с_правилами({"x": 1}, [{**без_вкл, "вкл": False}]))
        т = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, т, True)
        х = дк.ПравилаЛюдей(т / "глубже" / "ещё")
        х.добавить(3, {"вид": "протокол", "протокол": "GTP", "после": "UDP"})
        self.assertEqual(1, len(х.прочитать(3)), "папка правил создаётся со всеми родителями")

    def test_реестр_ospf_и_кэш_таблиц(self):
        from reportgen.setevoy import razbor  # noqa: PLC0415
        ospf = дк.реестр()[razbor.IP_ПРОТОКОЛЫ[89]]
        self.assertIs(razbor.ДОП_IP.get(89, razbor.ospf), ospf.функция, "OSPF — полный разборщик, если он есть")
        дк._кэш_таблиц.clear()
        списки, наибольший = [], 0
        for i in range(40):
            списки.append([дк.проверить_правило({"вид": "протокол", "протокол": f"P{i}"})])
            дк.таблица(списки[-1])
            наибольший = max(наибольший, len(дк._кэш_таблиц))
        self.assertEqual(33, наибольший, "кэш таблиц — не больше 33, потом очищается")


class ПравилаЛюдейTests(Папка):
    def test_хранение(self):
        х = дк.ПравилаЛюдей(self.т / "dk")
        self.assertEqual([], х.прочитать(7))
        п1 = х.добавить(7, {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "DNS"})
        п2 = х.добавить(7, {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "RTP/RTCP"})
        self.assertEqual(1, len(п2), "правило для того же места заменяет прежнее")
        self.assertEqual("RTP/RTCP", п2[0]["как"])
        х.добавить(7, {"вид": "протокол", "протокол": "GTP"})
        х.добавить(7, {"вид": "протокол", "протокол": "GTP", "после": "UDP"})
        self.assertEqual(3, len(х.прочитать(7)))
        ид = х.прочитать(7)[1]["ид"]
        self.assertFalse(х.изменить(7, ид, вкл=False)[1]["вкл"])
        self.assertEqual(2, len(х.удалить(7, ид)))
        with self.assertRaises(KeyError):
            х.удалить(7, ид)
        with self.assertRaises(KeyError):
            х.изменить(7, "00000000", вкл=True)
        self.assertEqual([], х.прочитать(8), "у каждого свои")
        (self.т / "dk" / "9.json").write_text("{испорчен", encoding="utf-8")
        self.assertEqual([], х.прочитать(9))
        self.assertNotEqual(п1[0]["ид"], п2[0]["ид"], "замена — новое правило со своим ид")


# -- сервер: прогон и «Декодировать как» ---------------------------------------------------------

class СерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.к = self.сеть.client
        self.settings = self.сеть.app.state.settings
        self.settings.capture_worker_process = False
        self.addCleanup(lambda: getattr(self.сеть.app.state, "progony", None) and
                        self.сеть.app.state.progony.остановить_все())
        self.сеть.login("engineer")

    def загрузить(self, кадры):
        ответ = self.к.post("/api/pakety", files={"file": ("стенд.pcap", с.pcap(кадры), "application/octet-stream")})
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид = ответ.json()["id"]
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/pakety/{ид}").json()["состояние"] == "готово"))
        return ид

    def действия(self):
        return [з.action for з in self.сеть.repos.audit.list()]

    def test_декодировать_как_через_сервер(self):
        ид = self.загрузить([кадр_udp(с.dns_запрос("a.b"), порт_к=5000)] * 2)
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"],
                         [у["протокол"] for у in self.к.get(f"/api/pakety/{ид}/packet/1").json()["уровни"]])
        разборщики = self.к.get("/api/dekodirovat-kak/razborshchiki").json()
        self.assertIn("DNS", [р["имя"] for р in разборщики["items"]])
        self.assertEqual(("данные", ["udp.port"]), (разборщики["данные"], разборщики["поля"]["UDP"]))
        ответ = self.к.post("/api/dekodirovat-kak", json={"rule": {"вид": "поле", "над": "UDP", "поле": "udp.port",
                                                                   "значение": 5000, "как": "DNS"}})
        self.assertEqual(200, ответ.status_code, ответ.text)
        правило = ответ.json()["items"][0]
        self.assertEqual("UDP: udp.port = 5000 → DNS", правило["описание"])
        self.assertIn("dekodirovat_kak.add", self.действия())
        self.assertEqual(400, self.к.post("/api/dekodirovat-kak", json={"rule": {"вид": "поле"}}).status_code)
        self.assertEqual(400, self.к.post("/api/dekodirovat-kak", json={"rule": {"вид": "протокол", "протокол": "A",
                                                                                 "действие": "вместо", "как": "Нет"}}).status_code)
        ответ = self.к.post(f"/api/pakety/{ид}/decode-rules", json={})
        self.assertEqual({"ok": True, "rules": 1}, ответ.json())
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/pakety/{ид}").json()["состояние"] == "готово"))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "DNS"],
                         [у["протокол"] for у in self.к.get(f"/api/pakety/{ид}/packet/1").json()["уровни"]])
        # «Разбирать как» по портам не стирает правила «Декодировать как».
        self.к.post(f"/api/pakety/{ид}/decode-as", json={"rules": {"udp:9": "DNS"}})
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/pakety/{ид}").json()["состояние"] == "готово"))
        как = self.к.get(f"/api/pakety/{ид}").json()["как"]
        self.assertEqual(("DNS", 1), (как["udp:9"], len(как["правила"])))
        # Новый захват сразу разбирается с правилами человека.
        ид2 = self.загрузить([кадр_udp(с.dns_запрос("c.d"), порт_к=5000)])
        self.assertEqual("DNS", self.к.get(f"/api/pakety/{ид2}/packet/1").json()["уровни"][-1]["протокол"])
        # Выключить — список, удалить, чужой не видит.
        self.assertFalse(self.к.patch(f"/api/dekodirovat-kak/{правило['ид']}", json={"вкл": False}).json()["items"][0]["вкл"])
        self.assertEqual(404, self.к.patch("/api/dekodirovat-kak/00000000", json={"вкл": True}).status_code)
        self.сеть.login("gruppa")
        self.assertEqual([], self.к.get("/api/dekodirovat-kak").json()["items"])
        self.assertEqual(404, self.к.delete(f"/api/dekodirovat-kak/{правило['ид']}").status_code)
        self.сеть.login("engineer")
        self.assertEqual([], self.к.delete(f"/api/dekodirovat-kak/{правило['ид']}").json()["items"])
        ответ = self.к.put("/api/dekodirovat-kak", json={"rules": [{"вид": "протокол", "протокол": "UDP"}]})
        self.assertEqual(1, len(ответ.json()["items"]))
        self.assertEqual(400, self.к.put("/api/dekodirovat-kak", json={"rules": "x"}).status_code)

    def test_прогон_через_сервер(self):
        кадры = [кадр_udp(rtp(i, bytes([65 + i]) * 2)) for i in range(6)]
        ид = self.загрузить(кадры)
        источник = {"вид": "pakety", "ид": ид}
        for тело, код in (({"источник": {"вид": "pakety", "ид": "20260101-000000-abcdef"}}, 404),
                          ({"источник": {"вид": "x", "ид": ид}}, 400), ({"источник": "x"}, 400),
                          ({"источник": источник, "скорость": -1}, 400),
                          ({"источник": источник, "выход": {"порт": 0}}, 400)):
            self.assertEqual(код, self.к.post("/api/progon", json=тело).status_code, тело)
        ответ = self.к.post("/api/progon", json={"источник": источник, "скорость": 0,
                                                 "выход": {"порт": 5004, "срез": "rtp"}, "имя": "проба"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        прогон_ = ответ.json()["id"]
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/progon/{прогон_}").json()["состояние"] == "готово"))
        ход = self.к.get(f"/api/progon/{прогон_}").json()
        self.assertEqual((6, "проба", 1), (ход["пакетов"], ход["имя"], ход["владелец"] and 1))
        self.assertEqual(прогон_, self.к.get(f"/api/progon?source=pakety:{ид}").json()["progon"]["ид"],
                         "страница снова находит свой прогон")
        self.assertIsNone(self.к.get("/api/progon?source=pakety:20260101-000000-abcdef").json()["progon"])
        выход = self.к.get(f"/api/progon/{прогон_}/output")
        self.assertEqual(b"AABBCCDDEEFF", выход.content)
        сид = self.к.post("/api/sessions", json={"name": "Стенд"}).json()["id"]
        ответ = self.к.post(f"/api/progon/{прогон_}/to-session", json={"session": сид, "bit_order": "msb"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        работа = ответ.json()["id"]
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/potok/{работа}").json()["состояние"] in ("готово", "ошибка")))
        self.assertEqual(b"AABBCCDDEEFF", self.к.get(f"/api/potok/{работа}/raw").content)
        self.assertEqual(400, self.к.post(f"/api/progon/{прогон_}/to-session", json={"session": сид, "bit_order": "?"}).status_code)
        # Команды: «сначала» с другой скоростью и без выхода, неизвестная команда — 400.
        ответ = self.к.post(f"/api/progon/{прогон_}/control", json={"команда": "сначала", "скорость": 0, "выход": None})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/progon/{прогон_}").json()["состояние"] == "готово"))
        self.assertEqual(404, self.к.get(f"/api/progon/{прогон_}/output").status_code, "без выхода — выхода нет")
        self.assertEqual(400, self.к.post(f"/api/progon/{прогон_}/control", json={"команда": "назад"}).status_code)
        self.assertEqual(409, self.к.post(f"/api/progon/{прогон_}/control", json={"команда": "пауза"}).status_code)
        for действие in ("progon.start", "progon.session", "progon.control"):
            self.assertIn(действие, self.действия())
        # Чужой прогон — 404.
        self.сеть.login("gruppa")
        for ответ in (self.к.get(f"/api/progon/{прогон_}"), self.к.post(f"/api/progon/{прогон_}/control", json={"команда": "стоп"}),
                      self.к.get(f"/api/progon/{прогон_}/output"), self.к.delete(f"/api/progon/{прогон_}")):
            self.assertEqual(404, ответ.status_code)
        self.assertEqual(404, self.к.post("/api/progon", json={"источник": источник}).status_code, "чужой захват")
        self.сеть.login("engineer")
        self.assertEqual({"ok": True}, self.к.delete(f"/api/progon/{прогон_}").json())
        self.assertEqual(404, self.к.get(f"/api/progon/{прогон_}").status_code)

    def test_многофайловый_захват_открывается_целиком(self):
        """«Открыть в анализаторе» у законченного многофайлового захвата — все куски одним захватом
        «Анализа пакетов» (куски по порядку), а не только первый кусок; у идущего — вживую, следом за записью."""
        import socket  # noqa: PLC0415

        from test_zahvat_seti import свободный_порт  # noqa: PLC0415
        порт = свободный_порт()
        ответ = self.к.post("/api/zahvat", json={"режим": "udp", "адрес": "127.0.0.1", "порты": str(порт), "имя": "Стенд",
                                                 "куски": {"пакетов": 1000}, "на_лету": False})
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид = ответ.json()["id"]
        self.addCleanup(lambda: self.сеть.app.state.zahvat_seti.остановить_все())
        о = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(о.close)
        for i in range(2500):
            о.sendto(i.to_bytes(4, "big"), ("127.0.0.1", порт))
            if i % 200 == 0:
                time.sleep(0.01)
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/zahvat/{ид}").json()["пакетов"] == 2500))
        # Идущий приём целиком — анализ вживую: запись «Анализа пакетов» читает куски следом за записью.
        ответ = self.к.post(f"/api/zahvat/{ид}/to-pakety", json={})
        self.assertEqual(200, ответ.status_code, ответ.text)
        пакеты = ответ.json()["id"]
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/pakety/{пакеты}").json()["пакетов"] == 2500))
        self.assertNotEqual("готово", self.к.get(f"/api/pakety/{пакеты}").json()["состояние"], "приём идёт — анализ ждёт")
        self.к.post(f"/api/zahvat/{ид}/stop", json={})
        self.assertEqual(3, self.к.get(f"/api/zahvat/{ид}/chunks").json()["total"])
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/pakety/{пакеты}").json()["состояние"] == "готово"))
        с_ = self.к.get(f"/api/pakety/{пакеты}").json()
        self.assertEqual((2500, "Стенд — вживую.pcapng", f"zahvat:{ид}#весь"), (с_["пакетов"], с_["имя"], с_["от"]))
        последний = self.к.get(f"/api/pakety/{пакеты}/packet/2500").json()
        self.assertEqual(["Ethernet", "IPv4", "UDP"], [у["протокол"] for у in последний["уровни"]][:3])
        self.assertEqual(пакеты, self.к.post(f"/api/zahvat/{ид}/to-pakety", json={}).json()["id"], "второй раз — тот же")
        self.assertFalse(list((self.сеть.app.state.zahvat_seti.папка / ид).glob("*.tmp")), "склейки на диске нет")
        # Кусок по-прежнему открывается отдельно.
        ответ = self.к.post(f"/api/zahvat/{ид}/to-pakety?chunk=2", json={})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertNotEqual(пакеты, ответ.json()["id"])

    def test_куски_идущего_захвата(self):
        """Закрытые куски идущего захвата открываются в анализаторе; пишущийся — 409."""
        import socket  # noqa: PLC0415

        from test_zahvat_seti import свободный_порт  # noqa: PLC0415
        порт = свободный_порт()
        ответ = self.к.post("/api/zahvat", json={"режим": "udp", "адрес": "127.0.0.1", "порты": str(порт),
                                                 "куски": {"пакетов": 1000}, "на_лету": True})
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид, прогон_ = ответ.json()["id"], ответ.json()["progon"]
        self.addCleanup(lambda: self.сеть.app.state.zahvat_seti.остановить_все())
        self.assertTrue(прогон_, ответ.json())
        о = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(о.close)
        for i in range(2500):
            о.sendto(bytes([i & 0xFF]) * 8, ("127.0.0.1", порт))
            if i % 200 == 0:
                time.sleep(0.01)
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/zahvat/{ид}/chunks").json()["total"] >= 3))
        куски = self.к.get(f"/api/zahvat/{ид}/chunks").json()["items"]
        self.assertEqual(1000, куски[0]["пакетов"])
        self.assertTrue(куски[-1]["идёт"])
        ответ = self.к.post(f"/api/zahvat/{ид}/to-pakety?chunk={куски[-1]['кусок']}", json={})
        self.assertEqual(409, ответ.status_code)
        self.assertIn("ещё пишется", ответ.json()["error"])
        ответ = self.к.post(f"/api/zahvat/{ид}/to-pakety?chunk=1", json={})
        self.assertEqual(200, ответ.status_code, ответ.text)
        пакеты = ответ.json()["id"]
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/pakety/{пакеты}").json()["состояние"] == "готово"))
        self.assertEqual((1000, f"zahvat:{ид}#1"), (self.к.get(f"/api/pakety/{пакеты}").json()["пакетов"],
                                                    self.к.get(f"/api/pakety/{пакеты}").json()["от"]))
        self.assertEqual(пакеты, self.к.get(f"/api/zahvat/{ид}/chunks").json()["items"][1]["в_пакетах"],
                         "отметка — в живом состоянии идущего захвата")
        # Обработка на лету идёт следом за записью.
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/progon/{прогон_}").json()["пакетов"] >= 2500, 20))
        self.assertEqual("ждёт данных", self.к.get(f"/api/progon/{прогон_}").json()["состояние"])
        self.к.post(f"/api/zahvat/{ид}/stop", json={})
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/progon/{прогон_}").json()["состояние"] == "готово", 10))
        self.assertEqual(2500, self.к.get(f"/api/progon/{прогон_}").json()["пакетов"])
        self.assertEqual(пакеты, self.к.get(f"/api/zahvat/{ид}/chunks").json()["items"][1]["в_пакетах"],
                         "отметка пережила конец захвата")


# -- страница: помощники «Декодировать как» и прогона (вырезаются из app.js, выполняются в node) --

@unittest.skipUnless(shutil.which("node"), "нет node")
class СтраницаTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_oblik import вырезать  # noqa: PLC0415
        cls.js = (Path(_bootstrap.ROOT) / "src" / "reportgen" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        cls.вырезать = staticmethod(вырезать)

    def выполнить(self, код: str):
        import subprocess  # noqa: PLC0415
        итог = subprocess.run([shutil.which("node"), "-e", код], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(0, итог.returncode, итог.stderr)
        return json.loads(итог.stdout.strip().splitlines()[-1])

    def test_адрес_в_анализатор_кусок_и_весь(self):
        # Нулевой кусок — тоже кусок: без номера сервер открывает весь многофайловый захват.
        код = self.вырезать(self.js, "адресЗахватаВПакеты") + r"""
        console.log(JSON.stringify([адресЗахватаВПакеты('/z', 0), адресЗахватаВПакеты('/z', 3), адресЗахватаВПакеты('/z'),
                                    адресЗахватаВПакеты('/z', null)]));"""
        self.assertEqual(["/z/to-pakety?chunk=0", "/z/to-pakety?chunk=3", "/z/to-pakety", "/z/to-pakety"], self.выполнить(код))
        # «Скачать pcapng» — ссылкой: гигабайты многочасового захвата не держатся в памяти страницы.
        self.assertNotIn("api.download(путь + '/file')", self.js)
        self.assertIn("href: путь + '/file', download: ''", self.js)

    def test_поле_и_значение_правила(self):
        код = self.вырезать(self.js, "ключПравилаДК") + self.вырезать(self.js, "значениеПравилаДК") + r"""
        console.log(JSON.stringify({
            ключи: ['udp.dstport', 'udp.srcport', 'tcp.dstport', 'sctp.srcport', 'eth.type', 'udp.port', 'xudp.dstport',
                    'udp.dstportx', null].map(ключПравилаДК),
            значения: ['5004', '0x0800 (IPv4)', '  17 (UDP)', 'a<b>\c', '', null, 'x'.repeat(80)].map(значениеПравилаДК),
        }));"""
        итог = self.выполнить(код)
        self.assertEqual(["udp.port", "udp.port", "tcp.port", "sctp.port", "eth.type", "udp.port", "xudp.dstport",
                          "udp.dstportx", ""], итог["ключи"])
        self.assertEqual(["5004", "0x0800", "17", "abc", "", "", "x" * 64], итог["значения"])

    def test_длительность_словами(self):
        код = self.вырезать(self.js, "fmtNumber") + self.вырезать(self.js, "fmtДлительность") + r"""
        console.log(JSON.stringify([0, 1.234, 9.999, 10, 59.94, 60, 61, 3599, 3600, 3725, 90061, -5, 'x'].map(fmtДлительность)));"""
        self.assertEqual(["0,00 с", "1,23 с", "10,00 с", "10,0 с", "59,9 с", "1 мин 00 с", "1 мин 01 с", "59 мин 59 с",
                          "1 ч 00 мин 00 с", "1 ч 02 мин 05 с", "25 ч 01 мин 01 с", "0,00 с", "0,00 с"], self.выполнить(код))

    def test_место_правой_кнопки(self):
        """Где щёлкнули: поле уровня, уровень дерева полей, строка дерева прогона, строка иерархии."""
        код = r"""
        function эл(класс, данные, родитель, атрибуты, текст) {
            return { класс, dataset: данные || {}, родитель, атрибуты: атрибуты || {}, textContent: текст || '',
                previousElementSibling: null, дети: [],
                matches(с) {
                    const м = /^([.\w-]*)(?:\[data-([\w-]+)\])?$/.exec(с);
                    const классы = м[1].split('.').filter(Boolean);
                    if (!классы.every((к) => (this.класс || '').split(' ').includes(к))) return false;
                    if (м[2]) { const к = м[2].replace(/-(\w)/g, (_, б) => б.toUpperCase()); return к in this.dataset; }
                    return true;
                },
                closest(с) { for (let у = this; у; у = у.родитель) if (у.matches(с)) return у; return null; },
                getAttribute(и) { return this.атрибуты[и] || null; },
                querySelector(с) { return this.дети.find((д) => д.matches(с)) || null; } };
        }
        """ + self.вырезать(self.js, "предыдущийУровеньДК") + self.вырезать(self.js, "местоДК") + r"""
        const eth = эл('pk-layer', { dkProto: 'Ethernet' });
        const ip = эл('pk-layer', { dkProto: 'IPv4' }); ip.previousElementSibling = eth;
        const udp = эл('pk-layer', { dkProto: 'UDP' }); udp.previousElementSibling = ip;
        const сводка = эл('summary', {}, udp);
        const поле = эл('pk-field', { dkKey: 'udp.dstport', dkValue: '5004' }, udp);
        const текст = эл('span', {}, поле);
        const безКлюча = эл('pk-field', {}, udp);
        const строка = эл('pg-tree-row', { dkProto: 'RTP', dkParent: 'UDP' });
        const корень = эл('pg-tree-row', {});
        const р1 = эл('pk-hier-row', {}, null, { 'aria-level': '1' }); р1.дети.push(эл('pk-hier-name', {}, null, {}, 'Кадры'));
        const р2 = эл('pk-hier-row', {}, null, { 'aria-level': '2' }); р2.дети.push(эл('pk-hier-name', {}, null, {}, 'Ethernet'));
        const р3 = эл('pk-hier-row', {}, null, { 'aria-level': '3' }); р3.дети.push(эл('pk-hier-name', {}, null, {}, 'IPv4'));
        const р4 = эл('pk-hier-row', {}, null, { 'aria-level': '3' }); р4.дети.push(эл('pk-hier-name', {}, null, {}, 'ARP'));
        р2.previousElementSibling = р1; р3.previousElementSibling = р2; р4.previousElementSibling = р3;
        console.log(JSON.stringify([текст, сводка, безКлюча, eth, строка, корень, р1, р2, р4, эл('div', {})].map(местоДК)));"""
        self.assertEqual([
            {"протокол": "UDP", "ниже": "IPv4", "поле": "udp.dstport", "значение": "5004"},
            {"протокол": "UDP", "ниже": "IPv4"}, {"протокол": "UDP", "ниже": "IPv4"}, {"протокол": "Ethernet", "ниже": ""},
            {"протокол": "RTP", "ниже": "UDP"}, None, None, {"протокол": "Ethernet", "ниже": ""},
            {"протокол": "ARP", "ниже": "Ethernet"}, None], self.выполнить(код))

    def test_переход_по_кускам(self):
        from test_oblik import PRELUDE  # noqa: PLC0415
        код = PRELUDE + r"""
        const журнал = [];
        const api = { get: async (п) => { журнал.push('get ' + п); return { total: 5 }; },
                      post: async (п) => { журнал.push('post ' + п); return { id: 'P1' }; } };
        function navigate(к) { журнал.push('go ' + к); } function toastError() {}
        """ + self.вырезать(self.js, "навигацияКусков") + r"""
        const текст = (у) => typeof у === 'string' ? у : у.textContent !== undefined ? у.textContent
            : у.kids.filter((к) => !к.hidden).map(текст).join(' ');
        const найти = (у, т) => typeof у === 'string' ? null : (текст(у) === т ? у : у.kids.map((к) => найти(к, т)).find(Boolean) || null);
        const ид = '20260930-104359-0b9c8e';
        const узлы = [{ от: 'zahvat:' + ид + '#0' }, { от: 'zahvat:' + ид + '#4' }, { от: 'zahvat:' + ид + '#весь' },
            { от: 'zahvat:' + ид + '#1' }, { от: 'zahvat:' + ид + '#3' },
            { от: '12#1' }, { от: '' }, {}, { от: 'zahvat:../x#1' }, { от: 'zahvat:../x#весь' }].map((с) => навигацияКусков(с));
        const сразу = узлы.map((у) => у && текст(у));
        setTimeout(async () => {
            const после = узлы.map((у) => у && текст(у));
            await найти(узлы[3], 'следующий кусок →').attrs.onclick({ currentTarget: {} });
            await найти(узлы[3], '← предыдущий кусок').attrs.onclick({ currentTarget: {} });
            console.log(JSON.stringify({ сразу, после, журнал, ссылка: найти(узлы[2], 'К приёму').attrs.href }));
        }, 20);"""
        итог = self.выполнить(код)
        ид = "20260930-104359-0b9c8e"
        self.assertEqual("Кусок 1 приёма с сети следующий кусок → К приёму", итог["сразу"][0], "до ответа — без числа кусков")
        после = итог["после"]
        self.assertEqual("Кусок 1 из 5 приёма с сети следующий кусок → К приёму", после[0])
        self.assertEqual("Кусок 5 из 5 приёма с сети ← предыдущий кусок К приёму", после[1], "у последнего нет «следующего»")
        self.assertEqual("Весь приём с сети, все куски подряд К приёму", после[2])
        self.assertEqual("Кусок 2 из 5 приёма с сети ← предыдущий кусок следующий кусок → К приёму", после[3])
        self.assertEqual("Кусок 4 из 5 приёма с сети ← предыдущий кусок следующий кусок → К приёму", после[4])
        self.assertEqual([None] * 5, после[5:])
        self.assertEqual(f"#/zahvat/{ид}", итог["ссылка"])
        self.assertEqual([f"get /api/zahvat/{ид}/chunks?offset=0&limit=1"] * 4, итог["журнал"][:4])
        self.assertEqual([f"post /api/zahvat/{ид}/to-pakety?chunk=2", "go #/pakety/P1",
                          f"post /api/zahvat/{ид}/to-pakety?chunk=0", "go #/pakety/P1"], итог["журнал"][4:])

    def test_подключено_к_страницам(self):
        маршрут = self.вырезать(self.js, "renderRoute")
        self.assertIn("остановитьОпросПрогона();", маршрут)
        self.assertIn("закрытьМенюДК();", маршрут)
        пакеты = self.вырезать(self.js, "рисоватьЗахват")
        self.assertIn("панельПрогона({ вид: 'pakety', ид: capId }", пакеты)
        self.assertIn("подключитьДК(page, разобратьЗаново);", пакеты)
        self.assertIn("await api.post(путь + '/decode-rules')", пакеты)
        self.assertIn("dataset: { dkProto: у.протокол }", пакеты, "уровень дерева полей знает свой протокол")
        self.assertIn("dataset: поле.ключ ? { dkKey: поле.ключ, dkValue: String(поле.текст) } : {}", пакеты)
        захват = self.вырезать(self.js, "рисоватьЗахватСети")
        self.assertIn("панельПрогона({ вид: 'zahvat', ид: capId }", захват)
        self.assertIn("с.можно_обработать !== false", захват, "чужому — без проигрывателя")
        код = self.вырезать(self.js, "остановитьОпросПрогона") + r"""
        const прогонОпрос = { таймер: setTimeout(() => { console.log('"не снят"'); process.exit(1); }, 50) };
        остановитьОпросПрогона();
        остановитьОпросПрогона();
        setTimeout(() => console.log(JSON.stringify(прогонОпрос.таймер)), 100);"""
        self.assertIsNone(self.выполнить(код))


if __name__ == "__main__":
    unittest.main()
