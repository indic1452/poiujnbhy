"""Объекты захвата (setevoy.obekty) — подробности, которые общие сценарии не различают: границы полей и
окон, порядок и выбор каналов, особые случаи протоколов. Каждый тест держит конкретное место кода
(найдено мутационной проверкой, см. docs/21-pakety.md, 21.5а)."""

import gzip
import os
import struct
import unittest
import zlib
from unittest import mock

import _bootstrap  # noqa: F401
import obekty_sintez as ос
import obrazcy_fajlov as обр
from reportgen.setevoy import obekty


def собрать(кадры, **настройки):
    сводки, нагрузки, sdp = ос.разобрать(кадры)
    return obekty.собрать(сводки, нагрузки, sdp=sdp, настройки=obekty.Настройки(**настройки))


def объекты(кадры, **настройки):
    return собрать(кадры, **настройки)["объекты"]


def стороны(кадры):
    сводки, нагрузки, _ = ос.разобрать(кадры)
    return obekty.соединения(сводки, нагрузки)


# == сборка ==========================================================================================================

class СборкаTests(unittest.TestCase):
    def test_пустой_сегмент_не_опора(self):
        # SYN без данных (номер ISN), данные — с ISN + 1: пустой сегмент не задаёт начало потока.
        о = ос.Обмен()
        о.кусок("с", b"", seq=4999)
        о.кусок("с", b"abc", seq=5000)
        (соед,), _ = стороны(о.пакеты)
        сторона = соед.стороны[("10.0.0.2", 80)]
        self.assertEqual((b"abc", []), (bytes(сторона.данные), сторона.пропуски))

    def test_предел_сборки_впритык(self):
        о = ос.Обмен()
        о.кусок("с", b"abc")
        о.кусок("с", b"def")
        for предел, заметки, данные in ((7, [], b"abcdef"), (6, ["сборка потоков TCP остановлена на пределе 0 МБ"], b"abcdef"),
                                        (5, ["сборка потоков TCP остановлена на пределе 0 МБ"], b"abc")):
            with self.subTest(предел=предел), mock.patch.object(obekty, "СБОРКА_ДО", предел):
                (соед,), з = стороны(о.пакеты)
                self.assertEqual((заметки, данные), (з, bytes(соед.стороны[("10.0.0.2", 80)].данные)))

    def test_опора_у_переполнения_номеров(self):
        # Опора 2³⁰ − 3: разности номеров — от неё, а не суммы (у суммы 2·опора + 2³¹ переходит через 2³²).
        о = ос.Обмен(seq_с=(1 << 30) - 3)
        о.кусок("с", b"a" * 10)
        о.кусок("с", b"b" * 10)
        (соед,), _ = стороны(о.пакеты)
        сторона = соед.стороны[("10.0.0.2", 80)]
        self.assertEqual((b"a" * 10 + b"b" * 10, []), (bytes(сторона.данные), сторона.пропуски))

    def test_дальний_сегмент_впереди(self):
        # Сегмент на 1,5 ГиБ впереди — после первого (разность со знаком по модулю 2³²), пропуск не заполнен.
        о = ос.Обмен(seq_с=1000)
        о.кусок("с", b"abc")
        о.кусок("с", b"far", seq=1000 + 3 * (1 << 29))
        (соед,), _ = стороны(о.пакеты)
        сторона = соед.стороны[("10.0.0.2", 80)]
        self.assertEqual((b"abcfar", [(3, 3 * (1 << 29) - 3, False)]), (bytes(сторона.данные), сторона.пропуски))

    def test_строки_и_поиск_с_нуля(self):
        сторона = obekty.Сторона("TCP", "a:1", "b:2")
        сторона.добавить(b"\nab\r\ncd", 7)
        self.assertEqual([(7, 0, 1, b""), (7, 1, 5, b"ab"), (7, 5, 7, b"cd")], obekty._строки(сторона))
        self.assertEqual(0, obekty._найти(b"\nx", b"\n"))
        self.assertIsNone(obekty._найти(b"x", b"\n"))
        self.assertEqual(3, obekty._найти(b"\nx\n\n", b"\n", 3))


# == имена и содержимое ==============================================================================================

class ИменаСодержимоеTests(unittest.TestCase):
    def test_имена(self):
        self.assertEqual("c.txt", obekty.имя_файла("a/b/c.txt"))
        self.assertEqual("c.txt", obekty.имя_из_адреса("/a/b/c.txt?x=1"))
        self.assertEqual("просто имя", obekty._раскодировать_2047("просто имя"))
        self.assertEqual("имя.txt", obekty._раскодировать_2047("=?UTF-8?B?0LjQvNGPLnR4dA==?="))

    def test_окно_текста_64_кб(self):
        кириллица = "текст ".encode() * 12000                         # > 64 КиБ, граница окна — внутри буквы
        self.assertEqual(("текст", "txt"), obekty.содержимое(кириллица))
        self.assertEqual(("двоичные данные", ""), obekty.содержимое(b"a" * 40000 + b"\xff" + b"a" * 40000))
        self.assertEqual(("текст", "txt"), obekty.содержимое(b"a" * 70000 + b"\xff"))

    def test_json_одно_значение_и_html_в_первом_килобайте(self):
        self.assertEqual(("текст", "txt"), obekty.содержимое(b'{"a":1}{"b":2}'))
        self.assertEqual(("HTML", "html"), obekty.содержимое(b" " * 1018 + b"<html>"))
        self.assertEqual(("текст", "txt"), obekty.содержимое(b" " * 1019 + b"<html>"))

    def test_gzip_окно_32_кб_и_не_zlib(self):
        случайные = os.urandom(20000)
        сжатое = gzip.compress(случайные * 2)                           # ссылка назад на 20 000 байт
        self.assertEqual((случайные * 2, ["сжатие gzip снято"]), obekty.снять_кодирование(сжатое, ["gzip"]))
        тело, заметки = obekty.снять_кодирование(zlib.compress(b"abc"), ["gzip"])
        self.assertTrue(заметки[0].startswith("сжатие gzip: не распаковалось"))

    def test_поля_http_сложенные(self):
        поля = obekty.поля_http(b"X-A: 1\r\nX-A: 2\r\nX-A: 3\r\n  4\r\n\t5\r\n 0\r\n")
        self.assertEqual({"x-a": ["1", "2", "3 4 5 0"]}, поля)
        self.assertEqual({"b": ["1"]}, obekty.поля_http(b" lost\r\nB: 1\r\n"))

    def test_chunked_кусок_до_конца_данных(self):
        self.assertEqual((b"abc", 6, ["chunked: обрыв — нет размера куска"]), obekty._chunked(b"3\r\nabc", 0))
        self.assertEqual((b"ab", 5, ["chunked: обрыв — кусок 3 байт получен не весь"]), obekty._chunked(b"3\r\nab", 0))


# == HTTP ==============================================================================================================

class HttpTests(unittest.TestCase):
    def коды(self, ответы, методы):
        сообщения, конец = obekty.сообщения_http(ответы, запрос=False, методы=методы)
        return [(с.код, с.метод, с.тело) for с in сообщения], конец

    def test_границы_1xx(self):
        # 099 и 200 — не 1xx (тело по длине), 199 — 1xx (без тела, метод не расходует).
        ответы = (b"HTTP/1.1 099 X\r\nContent-Length: 1\r\n\r\na" b"HTTP/1.1 199 X\r\nContent-Length: 1\r\n\r\n"
                  b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nb")
        self.assertEqual(([(99, "GET", b"a"), (199, "POST", b""), (200, "POST", b"b")], len(ответы)),
                         self.коды(ответы, ["GET", "POST", "HEAD"]))

    def test_смена_протокола_и_connect(self):
        дальше = b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nz"
        for код, метод, стоп in ((101, "GET", True), (102, "GET", False), (200, "CONNECT", True), (299, "CONNECT", True),
                                 (300, "CONNECT", False), (199, "CONNECT", False), (200, "GET", False)):
            ответ = b"HTTP/1.1 %d X\r\nContent-Length: 0\r\n\r\n" % код
            with self.subTest(код=код, метод=метод):
                сообщения, конец = obekty.сообщения_http(ответ + дальше, запрос=False, методы=[метод, "GET"])
                self.assertEqual(1 if стоп else 2, len(сообщения))
                self.assertEqual(len(ответ) if стоп else len(ответ + дальше), конец)

    def test_запрос_с_upgrade_или_connect_последний(self):
        дальше = b"GET /2 HTTP/1.1\r\n\r\n"
        for запрос, стоп in ((b"GET / HTTP/1.1\r\nUpgrade: h2c\r\n\r\n", True), (b"CONNECT h:1 HTTP/1.1\r\n\r\n", True),
                             (b"GET / HTTP/1.1\r\nHost: h\r\n\r\n", False)):
            with self.subTest(запрос=запрос):
                сообщения, _ = obekty.сообщения_http(запрос + дальше, запрос=True)
                self.assertEqual(1 if стоп else 2, len(сообщения))

    def test_пустые_строки_и_хвост(self):
        сообщения, конец = obekty.сообщения_http(b"\r\n\n\r\nGET / HTTP/1.1\r\n\r\n\r\n", запрос=True)
        self.assertEqual(([5], [None], 25), ([с.начало for с in сообщения], [с.код for с in сообщения], конец))


# == WebSocket =========================================================================================================

class WebSocketTests(unittest.TestCase):
    def test_расширенные_длины(self):
        средний = bytes(range(256)) * 2 + b"xy"
        большой = bytes(70000)
        кадры = (b"\x82\x7e" + struct.pack(">H", len(средний)) + средний
                 + b"\x82\x7f" + struct.pack(">Q", len(большой)) + большой)
        сообщения, конец = obekty.кадры_websocket(кадры, 0)
        self.assertEqual(([средний, большой], len(кадры)), ([с[3] for с in сообщения], конец))
        for оборван in (b"\x82\x7e\x01", b"\x82\x7f\x00\x00\x00\x00\x00\x00\x01"):
            with self.subTest(оборван=оборван):
                self.assertEqual(([], 0), obekty.кадры_websocket(оборван, 0))

    def test_маска_на_длинах_не_кратных_4(self):
        ключ = b"\x01\x02\x03\x04"
        for n in (1, 5, 7):
            данные = bytes(range(10, 10 + n))
            with self.subTest(n=n):
                self.assertEqual(bytes(б ^ ключ[i % 4] for i, б in enumerate(данные)), obekty._снять_маску(данные, ключ))


# == FTP ===============================================================================================================

class FtpTests(unittest.TestCase):
    def управление(self, строки, порт_к=40001, порт_с=21):
        у = ос.Обмен(порт_к=порт_к, порт_с=порт_с)
        for кто, строка in строки:
            у.кусок(кто, строка.encode() + b"\r\n")
        return у

    def передачи(self, у):
        (соед,), _ = стороны(у.пакеты)
        return obekty.ftp_управление(соед)

    def test_адреса_каналов_по_октетам(self):
        п = self.передачи(self.управление([("к", "PORT 10,1,2,3,4,5"), ("к", "RETR a"), ("к", "PASV"),
                                           ("с", "227 ok (10,6,7,8,0,21)"), ("к", "RETR b"),
                                           ("к", "EPRT |1|10.9.9.9|6275"), ("к", "RETR c"), ("к", "EPRT |1|x"),
                                           ("к", "RETR d")]))
        self.assertEqual([("10.1.2.3", 1029, "PORT"), ("10.6.7.8", 21, "PASV"), ("10.9.9.9", 6275, "EPRT"),
                          ("10.9.9.9", 6275, "EPRT")], [(х.адрес, х.порт, х.как) for х in п])

    def test_роли_когда_обе_стороны_похожи_на_сервер(self):
        # Обе стороны начинают с «NNN »: решает порт — сервер тот, у кого порт меньше; равные порты — вторая точка.
        for порт_к, порт_с, клиент in ((20, 2121, "10.0.0.2:2121"), (21, 21, "10.0.0.2:21")):
            у = ос.Обмен(порт_к=порт_к, порт_с=порт_с)
            у.кусок("к", b"123 hi\r\n")
            у.кусок("с", b"220 ok\r\n")
            (соед,), _ = стороны(у.пакеты)
            with self.subTest(порт_к=порт_к):
                self.assertEqual(клиент, obekty._роли(соед, obekty.re.compile(rb"\d{3}[ -]"))[0].от)

    def test_канал_данных_по_порту_и_адресу_вместе(self):
        у = self.управление([("к", "PASV"), ("с", "227 ok (10,0,0,2,195,80)"), ("к", "RETR a.bin")])
        чужой_адрес = ос.Обмен(кл="10.0.0.1", сер="10.0.0.9", порт_к=40050, порт_с=50000)
        чужой_адрес.кусок("с", b"WRONG1")
        чужой_порт = ос.Обмен(порт_к=40051, порт_с=50001)
        чужой_порт.кусок("с", b"WRONG2")
        верный = ос.Обмен(порт_к=40052, порт_с=50000)
        верный.кусок("с", b"R")
        верный.кусок("с", b"IGHT")
        о = [х for х in объекты(у.пакеты + чужой_адрес.пакеты + чужой_порт.пакеты + верный.пакеты) if х.вид == "FTP"]
        self.assertEqual([("a.bin", b"RIGHT")], [(х.имя, х.данные) for х in о])
        self.assertEqual(2, len(о[0].пакеты))


# == почта ===============================================================================================================

class ПочтаTests(unittest.TestCase):
    def test_smtp_два_письма_подряд_и_data_с_хвостом(self):
        м = ос.Обмен(порт_к=41004, порт_с=25)
        м.кусок("к", b"DATA x\r\nDATA\r\nSubject: A\r\n\r\na\r\n.\r\nDATA\r\nSubject: B\r\n\r\nb\r\n.\r\nQUIT\r\n")
        о = [х for х in объекты(м.пакеты) if х.вид == "почта"]
        self.assertEqual([("A.eml", b"Subject: A\r\n\r\na\r\n"), ("B.eml", b"Subject: B\r\n\r\nb\r\n")],
                         [(х.имя, х.данные) for х in о])

    def test_smtp_пустое_письмо(self):
        м = ос.Обмен(порт_к=41004, порт_с=25)
        м.кусок("к", b"DATA\r\n.\r\nQUIT\r\n")
        (о,) = [х for х in объекты(м.пакеты) if х.вид == "почта"]
        self.assertEqual((b"", []), (о.данные, о.заметки))

    def test_smtp_bdat_без_last_до_конца_и_пакеты(self):
        м = ос.Обмен(порт_к=41004, порт_с=25)
        м.кусок("к", b"EHLO x\r\n")
        м.кусок("к", b"BDAT 3\r\nabc")
        (о,) = объекты(м.пакеты)
        self.assertEqual((b"abc", ["обрыв: BDAT LAST в захвате нет"], [2]), (о.данные, о.заметки, о.пакеты))
        м = ос.Обмен(порт_к=41004, порт_с=25)
        м.кусок("к", b"BDAT 3\r\nabcBDAT 2 LAST\r\nde")
        м.кусок("к", b"BDAT 2 LAST\r\nfg")
        а, б = объекты(м.пакеты)
        self.assertEqual([(b"abcde", "письмо-1-1.eml"), (b"fg", "письмо-1-2.eml")], [(а.данные, а.имя), (б.данные, б.имя)])
        self.assertEqual(([1], [2]), (а.пакеты, б.пакеты))

    def test_pop3_приветствие_в_первом_пакете_и_пустое_письмо(self):
        п = ос.Обмен(порт_к=41014, порт_с=110)
        п.кусок("с", b"+OK ready\r\n")
        п.кусок("к", b"AUTH X\r\nabc\r\nLIST 1\r\nRETR 1\r\nRETR 2\r\n")
        п.кусок("с", b"+ go\r\n+ more\r\n+OK auth\r\n+OK 1 10\r\n+OK\r\n.\r\n+OK\r\nSubject: R\r\n\r\nx\r\n.\r\n")
        а, б = [х for х in объекты(п.пакеты) if х.вид == "почта"]
        self.assertEqual([("письмо-1-1.eml", b""), ("R.eml", b"Subject: R\r\n\r\nx\r\n")],
                         [(а.имя, а.данные), (б.имя, б.данные)])

    def test_pop3_приветствие_после_команды_не_пропускается(self):
        п = ос.Обмен(порт_к=41014, порт_с=110)
        п.кусок("к", b"RETR 3\r\n")
        п.кусок("с", b"+OK\r\n")
        п.кусок("с", b"Subject: S\r\n\r\ny\r\n.\r\n")
        (о,) = [х for х in объекты(п.пакеты) if х.вид == "почта"]
        self.assertEqual(("S.eml", [3]), (о.имя, о.пакеты))

    def test_imap_литерал_с_начала_и_окно_номера(self):
        и = ос.Обмен(порт_к=41005, порт_с=143)
        и.кусок("с", b"* 7 FETCH (BODY[1] {2}\r\nab)\r\n")
        (о,) = объекты(и.пакеты)
        self.assertEqual("imap-7-1.txt", о.имя)
        for отступ, номер in ((4096, "8"), (4097, "x")):
            и = ос.Обмен(порт_к=41005, порт_с=143)
            начало = b"* 8 FETCH (X"
            и.кусок("с", начало + b" " * (отступ - len(начало)) + b"BODY[2] {2}\r\ncd)\r\n")
            with self.subTest(отступ=отступ):
                self.assertEqual(f"imap-{номер}-2.txt", объекты(и.пакеты)[0].имя)
        и = ос.Обмен(порт_к=41005, порт_с=143)
        и.кусок("с", b"RFC822 {5}\r\nhello")                           # захват начат посреди ответа
        (о,) = объекты(и.пакеты)
        self.assertEqual(("почта", b"hello"), (о.вид, о.данные))


# == TFTP ===============================================================================================================

class TftpTests(unittest.TestCase):
    def данные(self, блок, содержимое, sport=4000, dport=3000):
        return ос.udp(b"\x00\x03" + struct.pack(">H", блок) + содержимое, sport, dport, src="10.0.0.2", dst="10.0.0.1")

    def test_файл_кратный_512_и_пустой_последний_блок(self):
        кадры = [ос.udp(b"\x00\x01f\x00octet\x00", 3000, 69), self.данные(1, b"A" * 512), self.данные(2, b"")]
        (о,) = объекты(кадры)
        self.assertEqual((b"A" * 512, [1, 2, 3], ["чтение (RRQ), режим octet, блок 512"]), (о.данные, о.пакеты, о.заметки))

    def test_запрос_без_нуля_в_конце_и_не_на_порт_69(self):
        self.assertEqual([], объекты([ос.udp(b"\x00\x01f\x00octet", 3000, 69), self.данные(1, b"x")]))
        self.assertEqual([], [о for о in объекты([ос.udp(b"\x00\x01f\x00octet\x00", 3000, 5000), self.данные(1, b"x")])
                              if о.вид == "TFTP"])

    def test_oack_с_несколькими_параметрами(self):
        oack = ос.udp(b"\x00\x06tsize\x001000\x00blksize\x00600\x00", 4000, 3000, src="10.0.0.2", dst="10.0.0.1")
        кадры = [ос.udp(b"\x00\x01f\x00octet\x00tsize\x000\x00blksize\x00600\x00", 3000, 69), oack,
                 self.данные(1, b"B" * 600), self.данные(2, b"C" * 10)]
        (о,) = объекты(кадры)
        self.assertEqual(["чтение (RRQ), режим octet, блок 600"], о.заметки)
        oack_ts = ос.udp(b"\x00\x06tsize\x00600\x00", 4000, 3000, src="10.0.0.2", dst="10.0.0.1")
        (о,) = объекты([кадры[0], oack_ts, self.данные(1, b"B" * 550)])      # tsize — не размер блока
        self.assertEqual(["чтение (RRQ), режим octet, блок 512", "обрыв: последнего (короткого) блока в захвате нет"],
                         о.заметки)

    def test_три_запроса_и_два_клиента(self):
        кадры = []
        for порт, имя, данные in ((3000, b"a", b"1"), (3000, b"b", b"22"), (3000, b"c", b"333"), (3100, b"d", b"4444")):
            кадры.append(ос.udp(b"\x00\x01" + имя + b"\x00octet\x00", порт, 69))
            кадры.append(self.данные(1, данные, sport=4000 + порт, dport=порт))
        кадры[4], кадры[6] = кадры[6], кадры[4]                  # запрос другого клиента — между
        кадры[5], кадры[7] = кадры[7], кадры[5]
        self.assertEqual({"a.txt": b"1", "b.txt": b"22", "c.txt": b"333", "d.txt": b"4444"},
                         {о.имя: о.данные for о in объекты(кадры)})

    def test_запись_раньше_чтения(self):
        кадры = [ос.udp(b"\x00\x02w\x00octet\x00", 3000, 69),
                 ос.udp(b"\x00\x03\x00\x01WRITE", 3000, 4000),
                 ос.udp(b"\x00\x01r\x00octet\x00", 3000, 69),
                 self.данные(1, b"READ")]
        self.assertEqual({"w.txt": b"WRITE", "r.txt": b"READ"}, {о.имя: о.данные for о in объекты(кадры)})


# == RTP ===============================================================================================================

class ЗаголовокRtpTests(unittest.TestCase):
    def test_csrc_расширение_и_границы(self):
        self.assertEqual((0, 0, 1, 2, 3, b"PAYLOAD"), obekty.заголовок_rtp(ос.rtp(0, 1, 2, 3, b"PAYLOAD", csrc=(9,))))
        с_расширением = ос.rtp(0, 1, 2, 0x12345678, b"xyz", csrc=(7,), расширение=b"\x10\x20\x30\x40")
        self.assertEqual(b"xyz", obekty.заголовок_rtp(с_расширением)[5])
        пустое = bytes([0x90]) + ос.rtp(0, 1, 2, 3, b"")[1:] + b"\xbe\xde\x00\x00"
        self.assertEqual(b"", obekty.заголовок_rtp(пустое)[5])
        self.assertIsNone(obekty.заголовок_rtp(пустое[:-1]))
        self.assertIsNone(obekty.заголовок_rtp(ос.rtp(0, 1, 2, 3, b"")[:11]))


def биты_в_байты(биты):
    биты += "0" * (-len(биты) % 8)
    return int(биты, 2).to_bytes(len(биты) // 8, "big")


class ЗвукВидеоTests(unittest.TestCase):
    def test_wav_добивка_нечётного(self):
        for отсчёты in (b"\x01", b"\x01\x02\x03", b"\x01\x02"):
            with self.subTest(n=len(отсчёты)):
                данные = obekty.wav(отсчёты, 7)
                self.assertEqual(58 + len(отсчёты) + len(отсчёты) % 2, len(данные))     # RIFF, fmt 18, fact, data
                self.assertTrue(данные.endswith(отсчёты + b"\x00" * (len(отсчёты) % 2)))

    def test_amr_октеты_границы(self):
        self.assertEqual([(15, 1, b"")], obekty._amr_октеты(b"\xf0\x7d", obekty.AMR_БИТ))       # бит P в ToC
        self.assertIsNone(obekty._amr_октеты(b"\xf0" + bytes([12 << 3]), obekty.AMR_БИТ))        # FT = длине таблицы
        речь = bytes(range(23))
        self.assertEqual([(1, 1, речь)], obekty._amr_октеты(b"\xf0\x0c" + речь, obekty.AMR_WB_БИТ))  # 177 бит

    def test_amr_полоса_границы(self):
        два_пустых = биты_в_байты("1111" + "111111" + "011111")
        self.assertEqual([(15, 1, b""), (15, 1, b"")], obekty._amr_полоса(два_пустых, obekty.AMR_БИТ))
        self.assertIsNone(obekty._amr_полоса(два_пустых + b"\x00", obekty.AMR_БИТ))          # добивка ≥ 8 бит
        self.assertEqual([(14, 1, b"")], obekty._amr_полоса(биты_в_байты("1111" + "011101"), obekty.AMR_БИТ))
        for ft in (12, 13):
            with self.subTest(ft=ft):
                self.assertIsNone(obekty._amr_полоса(биты_в_байты("1111" + "0" + format(ft, "04b") + "1"), obekty.AMR_БИТ))
        речь = "10" * 59                                                             # 118 бит — ровно до конца
        итог = obekty._amr_полоса(биты_в_байты("1111" + "000101" + речь), obekty.AMR_БИТ)
        self.assertEqual([(2, 1, биты_в_байты(речь))], итог)
        речь = "1" + "0" * 175 + "1"                                                 # 177 бит (AMR-WB 8,85)
        итог = obekty._amr_полоса(биты_в_байты("1111" + "000011" + речь), obekty.AMR_WB_БИТ)
        self.assertEqual([(1, 1, биты_в_байты(речь))], итог)
        self.assertEqual(23, len(итог[0][2]))

    def test_h264_тип_23_stap_и_короткий_fu(self):
        данные, заметки = obekty._h264([(1, b"\x17abc")], "annexb")
        self.assertEqual((b"\x00\x00\x00\x01\x17abc", []), (данные, заметки))
        данные, _ = obekty._h264([(1, b"\x18\x00\x01\x09\x00\x00")], "annexb")                # размер 0 в конце
        self.assertEqual(b"\x00\x00\x00\x01\x09", данные)
        данные, заметки = obekty._h264([(1, b"\x7c\x85"), (2, b"\x7c\x45tail")], "annexb")    # начало FU без данных
        self.assertEqual(b"", данные)
        self.assertEqual(["NAL, оборванных пропуском пакетов (FU без начала или конца): 1 — выброшены"], заметки)
        данные, _ = obekty._h265([(1, b"\x60\x01\x00\x01\x09\x00\x00")], "annexb")
        self.assertEqual(b"\x00\x00\x00\x01\x09", данные)
