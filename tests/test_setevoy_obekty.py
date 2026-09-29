"""Объекты захвата (setevoy.obekty): сборка TCP, HTTP, WebSocket, FTP, TFTP, почта, RTP, JSON, сигнатуры,
описи архивов, пределы и выгрузка ZIP — на синтетических захватах (пакеты — по RFC, суммы верные)."""

import csv
import gzip
import io
import json
import struct
import unittest
import zipfile
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


def http_обмен(запрос, ответ, размер=700):
    о = ос.Обмен()
    о.кусок("к", запрос)
    о.куски("с", ответ, размер)
    return о.пакеты


def ответ(тело, *поля, код=b"200 OK"):
    return b"HTTP/1.1 " + код + b"\r\n" + b"".join(п + b"\r\n" for п in поля) + b"\r\n" + тело


# == сборка TCP =================================================================================================

class СборкаTcpTests(unittest.TestCase):
    def стороны(self, кадры):
        сводки, нагрузки, _ = ос.разобрать(кадры)
        соединения, заметки = obekty.соединения(сводки, нагрузки)
        return соединения, заметки

    def test_порядок_повтор_перекрытие_и_переход_через_2_32(self):
        о = ос.Обмен(seq_с=0xFFFFFFF0)
        данные = bytes(range(60))
        о.кусок("с", данные[20:40], seq=(0xFFFFFFF0 + 20) & 0xFFFFFFFF)       # раньше срока
        о.кусок("с", данные[0:20], seq=0xFFFFFFF0)
        о.кусок("с", данные[0:20], seq=0xFFFFFFF0)                          # повтор
        о.кусок("с", данные[30:60], seq=(0xFFFFFFF0 + 30) & 0xFFFFFFFF)       # перекрытие 10 байт
        (соед,), заметки = self.стороны(о.пакеты)
        сторона = соед.стороны[("10.0.0.2", 80)]
        self.assertEqual(данные, bytes(сторона.данные))
        self.assertEqual([(0, 2), (20, 1), (40, 4)], сторона.карта)
        self.assertEqual([], сторона.пропуски)
        self.assertEqual([], заметки)
        self.assertEqual([2, 1], сторона.пакеты(0, 21))
        self.assertEqual([1, 4], сторона.пакеты(25, 60))
        self.assertEqual([4], сторона.пакеты(59, 59))
        self.assertEqual(("TCP", 1, 1), (соед.транспорт, соед.номер, соед.первый))
        self.assertEqual((("10.0.0.2", 80), ("10.0.0.1", 40000)), соед.точки)
        self.assertEqual("TCP 10.0.0.2:80 → 10.0.0.1:40000", сторона.поток)

    def test_пропуск_нулями_и_большой_без_заполнения(self):
        о = ос.Обмен()
        о.кусок("с", b"a" * 10, seq=5000)
        о.кусок("с", b"b" * 10, seq=5030)
        (соед,), _ = self.стороны(о.пакеты)
        с = соед.стороны[("10.0.0.2", 80)]
        self.assertEqual(b"a" * 10 + bytes(20) + b"b" * 10, bytes(с.данные))
        self.assertEqual([(10, 20, True)], с.пропуски)
        self.assertEqual(["пропуск 20 байт в потоке (с 10): заполнен нулями"], с.заметки(10, 11))
        self.assertEqual([], с.заметки(0, 10))
        self.assertEqual([], с.заметки(11, 40))
        with mock.patch.object(obekty, "ПРОПУСК_ДО", 19):
            (соед,), _ = self.стороны(о.пакеты)
        с = соед.стороны[("10.0.0.2", 80)]
        self.assertEqual(b"a" * 10 + b"b" * 10, bytes(с.данные))
        self.assertEqual(["пропуск 20 байт в потоке (с 10): не заполнен — дальше данные сдвинуты"], с.заметки(0, 20))
        with mock.patch.object(obekty, "ПРОПУСК_ДО", 20):
            (соед,), _ = self.стороны(о.пакеты)
        self.assertEqual(30, len(соед.стороны[("10.0.0.2", 80)].данные) - 10)

    def test_без_номеров_по_порядку_захвата_и_предел_сборки(self):
        сводки, нагрузки, _ = ос.разобрать(ос.Обмен().пакеты + [ос.Обмен().кусок("с", b"x" * 10),
                                                               ос.Обмен().кусок("с", b"y" * 10)])
        for с in сводки:
            с.pop("seq", None)
        (соед,), заметки = obekty.соединения(сводки, нагрузки)
        self.assertEqual(b"x" * 10 + b"y" * 10, bytes(соед.стороны[("10.0.0.2", 80)].данные))
        with mock.patch.object(obekty, "СБОРКА_ДО", 15):
            (соед,), заметки = obekty.соединения(сводки, нагрузки)
        self.assertEqual(b"x" * 10, bytes(соед.стороны[("10.0.0.2", 80)].данные))
        self.assertEqual(["сборка потоков TCP остановлена на пределе 0 МБ"], заметки)
        сводки2, нагрузки2, _ = ос.разобрать([ос.Обмен().кусок("с", b"x" * 10), ос.Обмен().кусок("с", b"y" * 10,
                                                                                              seq=5010)])
        with mock.patch.object(obekty, "СБОРКА_ДО", 10):
            (соед,), заметки = obekty.соединения(сводки2, нагрузки2)
        self.assertEqual(b"x" * 10, bytes(соед.стороны[("10.0.0.2", 80)].данные))
        self.assertEqual(1, len(заметки))

    def test_udp_и_не_транспорт(self):
        кадры = [ос.udp(b"abc", 5000, 6000), ос.udp(b"de", 6000, 5000, src="10.0.0.2", dst="10.0.0.1"),
                 ос.udp(b"f", 5000, 6000)]
        сводки, нагрузки, _ = ос.разобрать(кадры)
        сводки.append({"номер": 9, "время": 1.0, "стек": ["Ethernet", "ARP"], "порт_от": None})
        нагрузки.append(b"zz")
        (соед,), _ = obekty.соединения(сводки, нагрузки)
        self.assertEqual("UDP", соед.транспорт)
        self.assertEqual([(("10.0.0.1", 5000), b"abc", 1), (("10.0.0.2", 6000), b"de", 2),
                          (("10.0.0.1", 5000), b"f", 3)], [д[:3] for д in соед.датаграммы])
        сводки[0]["стек"] = ["Ethernet", "IPv4", "SCTP"]
        self.assertEqual(2, len(obekty.соединения(сводки, нагрузки)[0][0].датаграммы))


# == имена, типы, содержимое ==========================================================================================

class ИменаTests(unittest.TestCase):
    def test_имена_и_расширения(self):
        self.assertEqual("a.txt", obekty.имя_файла("..\\путь/к/..a.txt.. "))
        self.assertEqual("ab", obekty.имя_файла("a\x00\x1fb\x7f"))
        self.assertEqual("abc", obekty.имя_файла("abcdef", 3))
        self.assertEqual("имя.bin", obekty.с_расширением("имя", "bin"))
        self.assertEqual("имя.tar", obekty.с_расширением("имя.tar", "gz"))
        self.assertEqual("имя.абв.gz", obekty.с_расширением("имя.абв", "gz"))
        self.assertEqual("имя", obekty.с_расширением("имя", ""))
        self.assertEqual("x.123456789.gz", obekty.с_расширением("x.123456789", "gz"))

    def test_типы(self):
        for тип, расширение in (("application/xml; charset=utf-8", "xml"), ("TEXT/JAVASCRIPT", "js"),
                                ("image/png", "png"), ("application/x-невиданное", ""), ("", "")):
            with self.subTest(тип):
                self.assertEqual(расширение, obekty.расширение_типа(тип))
        self.assertEqual("application/vnd.rar", obekty.тип_расширения("rar"))
        self.assertEqual("image/png", obekty.тип_расширения("png"))
        self.assertEqual("application/octet-stream", obekty.тип_расширения("невиданное"))

    def test_имена_из_заголовков_и_адресов(self):
        self.assertEqual("отчёт.pdf", obekty.имя_из_disposition("attachment; filename*=UTF-8''%D0%BE%D1%82%D1%87%D1%91%D1%82.pdf"))
        self.assertEqual("a.txt", obekty.имя_из_disposition('attachment; filename="../../a.txt"'))
        self.assertEqual("отчёт.pdf", obekty.имя_из_disposition('attachment; filename="=?UTF-8?B?0L7RgtGH0ZHRgi5wZGY=?="'))
        self.assertEqual("", obekty.имя_из_disposition("inline"))
        self.assertEqual("=?x-нет?B?QQ==?=", obekty._раскодировать_2047("=?x-нет?B?QQ==?="))
        self.assertEqual("index", obekty.имя_из_адреса("http://h/a/"))
        self.assertEqual("отчёт.docx", obekty.имя_из_адреса("/f/%D0%BE%D1%82%D1%87%D1%91%D1%82.docx?x=1#y"))
        self.assertEqual("", obekty.имя_из_адреса("http://h"))

    def test_json_значения(self):
        self.assertEqual([(0, 2), (3, 12)], obekty.json_значения('{}\n{"я": 1}'.encode()))
        self.assertEqual([(1, 3)], obekty.json_значения(b" []  "))
        self.assertIsNone(obekty.json_значения(b"{} []", одно=True))
        self.assertIsNone(obekty.json_значения(b"1"))
        self.assertIsNone(obekty.json_значения(b"{} x"))
        self.assertIsNone(obekty.json_значения(b"{"))
        self.assertIsNone(obekty.json_значения(b"  "))
        self.assertIsNone(obekty.json_значения(b"\xff"))

    def test_содержимое(self):
        self.assertEqual(("PNG", "png"), obekty.содержимое(обр.png()))
        self.assertEqual(("ZIP (docx)", "docx"), obekty.содержимое(обр.docx()))
        self.assertEqual(("пусто", ""), obekty.содержимое(b""))
        self.assertEqual(("JSON", "json"), obekty.содержимое(b'{"a": 1}'))
        self.assertEqual(("HTML", "html"), obekty.содержимое(b"  <!DOCTYPE html><p>x"))
        self.assertEqual(("HTML", "html"), obekty.содержимое(b"<html>"))
        self.assertEqual(("текст", "txt"), obekty.содержимое("<htmlx> привет\r\n\tвсем".encode()))
        self.assertEqual(("двоичные данные", ""), obekty.содержимое(b"\xff\xfe\x00"))
        self.assertEqual(("двоичные данные", ""), obekty.содержимое(b"a" * 94 + b"\x01" * 6))
        self.assertEqual(("текст", "txt"), obekty.содержимое(b"a" * 95 + b"\x01" * 5))

    def test_снять_кодирование(self):
        тело = "текст ".encode() * 50
        self.assertEqual((тело, ["сжатие gzip снято"]), obekty.снять_кодирование(gzip.compress(тело), ["GZIP "]))
        self.assertEqual((тело, ["сжатие x-gzip снято"]), obekty.снять_кодирование(gzip.compress(тело), ["x-gzip"]))
        self.assertEqual((тело, ["сжатие deflate снято"]), obekty.снять_кодирование(zlib.compress(тело), ["deflate"]))
        голый = zlib.compressobj(wbits=-15)
        сырое = голый.compress(тело) + голый.flush()
        self.assertEqual((тело, ["сжатие deflate снято"]), obekty.снять_кодирование(сырое, ["deflate"]))
        двойное = gzip.compress(zlib.compress(тело))
        self.assertEqual((тело, ["сжатие gzip снято", "сжатие deflate снято"]),
                         obekty.снять_кодирование(двойное, ["deflate", "identity", "", "gzip"]))
        self.assertEqual((b"x", ["сжатие br не снято (brotli нет в стандартной библиотеке)"]),
                         obekty.снять_кодирование(b"x", ["gzip", "br"]))
        self.assertEqual((b"x", ["сжатие compress не снято"]), obekty.снять_кодирование(b"x", ["compress"]))
        итог, заметки = obekty.снять_кодирование(b"not gzip", ["gzip"])
        self.assertEqual(b"not gzip", итог)
        self.assertTrue(заметки[0].startswith("сжатие gzip: не распаковалось ("))
        итог, заметки = obekty.снять_кодирование(gzip.compress(тело)[:-12], ["gzip"])
        self.assertEqual(["сжатие gzip снято; сжатые данные оборваны — распаковано сколько есть"], заметки)
        with mock.patch.object(obekty, "ОБЪЕКТ_ДО", 100):
            итог, заметки = obekty.снять_кодирование(gzip.compress(тело), ["gzip"])
        self.assertEqual((тело[:100], ["сжатие gzip снято; распаковка остановлена на пределе 0 МБ"]), (итог, заметки))


# == HTTP =============================================================================================================

class HttpTests(unittest.TestCase):
    def test_длина_chunked_gzip_и_имена(self):
        документ = обр.pdf()
        js = json.dumps({"имя": "значение"}, ensure_ascii=False).encode()
        сжато = gzip.compress(js)
        куски = b"".join(b"%x;ext=1\r\n" % len(сжато[i:i + 7]) + сжато[i:i + 7] + b"\r\n" for i in range(0, len(сжато), 7))
        о = ос.Обмен()
        о.кусок("к", b"GET /d/%D0%B4%D0%BE%D0%BA HTTP/1.1\r\nHost: x\r\n\r\n")
        о.куски("с", ответ(документ, b"Content-Type: application/pdf", b"Content-Length: %d" % len(документ)))
        о.кусок("к", b"\r\nGET /api/ HTTP/1.1\r\n\r\n")
        о.куски("с", ответ(куски + b"0\r\nX-T: 1\r\n\r\n", b"Transfer-Encoding: chunked", b"Content-Encoding: gzip",
                           b"Content-Type: application/json"), 30)
        итог = собрать(о.пакеты)
        а, б = итог["объекты"]
        self.assertEqual(("HTTP", "док.pdf", "application/pdf", документ, "PDF"), (а.вид, а.имя, а.тип, а.данные, а.содержимое))
        self.assertEqual(["ответ 200 на GET /d/док"], а.заметки)
        self.assertEqual(("TCP 10.0.0.2:80 → 10.0.0.1:40000", [2]), (а.поток, а.пакеты))
        self.assertEqual(("JSON", "index.json", js), (б.вид, б.имя, б.данные))
        self.assertEqual(["ответ 200 на GET /api/", "сжатие gzip снято"], б.заметки)
        self.assertEqual((1, 2), (а.номер, б.номер))

    def test_сообщения_http_границы(self):
        ответы = (b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc"
                  b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n"
                  b"HTTP/1.1 304 Not Modified\r\nContent-Length: 9\r\n\r\n"
                  b"HTTP/1.1 204 No Content\r\n\r\n"
                  b"HTTP/1.1 200 OK\r\n\r\n" + "до конца".encode())
        сообщения, конец = obekty.сообщения_http(ответы, запрос=False, методы=["POST", "HEAD", "GET", "GET", "GET"])
        self.assertEqual([(100, b""), (200, b"abc"), (200, b""), (304, b""), (204, b""), (200, "до конца".encode())],
                         [(с.код, с.тело) for с in сообщения])
        self.assertEqual(["POST", "POST", "HEAD", "GET", "GET", "GET"], [с.метод for с in сообщения])
        self.assertEqual(len(ответы), конец)
        self.assertEqual(["тело до закрытия соединения"], сообщения[-1].заметки)

    def test_запросы_upgrade_connect_и_ошибки(self):
        запросы = (b"POST /a HTTP/1.1\r\nContent-Length: 2\r\n\r\nxyGET /b HTTP/1.0\r\n\r\n"
                   b"GET /c HTTP/1.1\r\nUpgrade: websocket\r\n\r\n\x81\x00")
        сообщения, конец = obekty.сообщения_http(запросы, запрос=True)
        self.assertEqual([("POST", "/a", b"xy"), ("GET", "/b", b""), ("GET", "/c", b"")],
                         [(с.метод, с.адрес, с.тело) for с in сообщения])
        self.assertEqual(len(запросы) - 2, конец)
        туннель, конец = obekty.сообщения_http(b"CONNECT h:443 HTTP/1.1\r\n\r\n\x16\x03", запрос=True)
        self.assertEqual((1, 26), (len(туннель), конец))
        плохой, конец = obekty.сообщения_http(b"POST / HTTP/1.1\r\nContent-Length: 3, 4\r\n\r\nabcGET", запрос=True)
        self.assertEqual((["неверный Content-Length (3, 4) — разбор остановлен"], 0, 41, 0),
                         (плохой[0].заметки, плохой[0].начало, плохой[0].конец, конец))
        для_te, _ = obekty.сообщения_http(b"POST / HTTP/1.1\r\nTransfer-Encoding: gzip\r\n\r\nzz", запрос=True)
        self.assertEqual(["Transfer-Encoding без chunked в запросе — длину не определить (RFC 9112, 6.3)"], для_te[0].заметки)
        одинаковые, _ = obekty.сообщения_http(b"POST / HTTP/1.1\r\nContent-Length: 2, 2\r\n\r\nab", запрос=True)
        self.assertEqual(b"ab", одинаковые[0].тело)
        буквы, _ = obekty.сообщения_http(b"POST / HTTP/1.1\r\nContent-Length: x\r\n\r\nab", запрос=True)
        self.assertEqual(b"", буквы[0].тело)
        без_конца, конец = obekty.сообщения_http(b"GET / HTTP/1.1\r\nHost: x\r\n", запрос=True)
        self.assertEqual(([], 0), (без_конца, конец))
        чужое, конец = obekty.сообщения_http(b"\n\r\nHELLO", запрос=True)
        self.assertEqual(([], 3), (чужое, конец))

    def test_ответ_без_длины_и_коды_передачи(self):
        сообщения, _ = obekty.сообщения_http(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n" + gzip.compress(b"abc"),
                                             запрос=False, методы=["GET"])
        self.assertEqual((["тело до закрытия соединения"], ["gzip"]), (сообщения[0].заметки, сообщения[0].кодировки))
        сообщения, _ = obekty.сообщения_http(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n0\r\n\r\n",
                                             запрос=False)
        self.assertEqual(["gzip"], сообщения[0].кодировки)
        пусто, _ = obekty.сообщения_http(b"HTTP/1.1 200 OK\r\n\r\n", запрос=False)
        self.assertEqual([], пусто[0].заметки)
        о = ос.Обмен()
        о.кусок("к", b"GET /z HTTP/1.1\r\n\r\n")
        о.кусок("с", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\nContent-Type: text/plain\r\n\r\n"
                + b"%x\r\n" % len(gzip.compress(b"hello")) + gzip.compress(b"hello") + b"\r\n0\r\n\r\n")
        (о_,) = объекты(о.пакеты)
        self.assertEqual((b"hello", "z.txt"), (о_.данные, о_.имя))
        self.assertIn("сжатие gzip снято", о_.заметки)
        (сырой,) = объекты(о.пакеты, снимать_сжатие=False)
        self.assertEqual(gzip.compress(b"hello"), сырой.данные)
        self.assertIn("сжатие не снималось (настройка)", сырой.заметки)

    def test_chunked_обрывы(self):
        self.assertEqual((b"", 5, ["chunked: обрыв — нет размера куска"]), obekty._chunked(b"zzzzz", 0))
        self.assertEqual((b"ab", 5, ["chunked: обрыв — кусок 5 байт получен не весь"]), obekty._chunked(b"5\r\nab", 0))
        self.assertEqual((b"abcde", 11, ["chunked: обрыв в трейлерах"]), obekty._chunked(b"5\nabcde\n0\nX", 0))
        self.assertEqual((b"abcde", 10, []), obekty._chunked(b"5\nabcde0\n\ntail", 0))
        self.assertEqual((b"abcde", 12, []), obekty._chunked(b"5\r\nabcde\r\n0\r\n\r\ntail", 0)[:1] + (12, []))
        self.assertEqual((b"ab", 17, []), obekty._chunked(b"2 ;x=1\r\nab\r\n0\r\n\r\n", 0))

    def test_форма_multipart_и_json_запроса(self):
        форма = ("--B\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\nпривет\r\n--B\r\n"
                 "Content-Disposition: form-data; name=\"f\"; filename=\"=?UTF-8?B?0YQucG5n?=\"\r\nContent-Type: image/png\r\n\r\n"
                 ).encode() + обр.png() + b"\r\n--B--\r\n"
        о = ос.Обмен()
        о.кусок("к", b"POST /up HTTP/1.1\r\nContent-Type: multipart/form-data; boundary=B\r\nContent-Length: %d\r\n\r\n"
                % len(форма) + форма)
        о.кусок("к", b"PUT /j HTTP/1.1\r\nContent-Length: 8\r\n\r\n{\"a\":1}")
        о.кусок("с", b"HTTP/1.1 204 No Content\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        а, б, в = объекты(о.пакеты)
        self.assertEqual(("HTTP", "up-часть-1.txt", "text/plain", "привет".encode()), (а.вид, а.имя, а.тип, а.данные))
        self.assertEqual(["запрос POST /up", "часть формы 1, поле «note»"], а.заметки)
        self.assertEqual(("ф.png", "image/png", обр.png()), (б.имя, б.тип, б.данные))
        self.assertEqual("часть формы 2, поле «f»", б.заметки[-1])
        self.assertEqual(("JSON", "j.json", b'{"a":1}'), (в.вид, в.имя, в.данные))
        self.assertEqual([], obekty._части_формы(b"x", "text/plain"))

    def test_имя_из_disposition_расхождение_и_обрыв(self):
        тело = обр.png()
        кадры = http_обмен(b"GET /a HTTP/1.1\r\n\r\n", ответ(тело, b"Content-Type: image/jpeg",
                                                             b"Content-Disposition: attachment; filename=\"pic.jpg\"",
                                                             b"Content-Length: %d" % (len(тело) + 5)))
        (о,) = объекты(кадры)
        self.assertEqual(("pic.jpg", "PNG", "png"), (о.имя, о.содержимое, о.расширение))
        self.assertEqual(["ответ 200 на GET /a", f"обрыв: получено {len(тело)} из {len(тело) + 5} байт",
                          "по содержимому — PNG, а не .jpg"], о.заметки)
        кадры = http_обмен(b"GET /photo.jpeg HTTP/1.1\r\n\r\n", ответ(обр.jpeg(), b"Content-Length: %d" % len(обр.jpeg())))
        (о,) = объекты(кадры)
        self.assertEqual(("photo.jpeg", ["ответ 200 на GET /photo.jpeg"], "image/jpeg"), (о.имя, о.заметки, о.тип))
        кадры = http_обмен(b"GET /x HTTP/1.1\r\n\r\n", ответ(b"\x01\x02\x03", b"Content-Type: application/octet-stream",
                                                             b"Content-Length: 3"))
        (о,) = объекты(кадры)
        self.assertEqual(("x.bin", "application/octet-stream"), (о.имя, о.тип))
        кадры = http_обмен(b"GET /x HTTP/1.1\r\n\r\n", ответ(обр.docx(), b"Content-Length: %d" % len(обр.docx())))
        (о,) = объекты(кадры)
        self.assertEqual(("x.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), (о.имя, о.тип))
        кадры = http_обмен(b"GET /a.jar HTTP/1.1\r\n\r\n", ответ(обр.zip_([("x", "y")]), b"Content-Length: %d" % len(обр.zip_([("x", "y")]))))
        (о,) = объекты(кадры)
        self.assertEqual(["ответ 200 на GET /a.jar"], о.заметки)

    def test_только_ответ_без_запроса_и_без_сервера(self):
        о = ос.Обмен()
        о.кусок("к", b"GET /only HTTP/1.1\r\n\r\n")
        self.assertEqual([], объекты(о.пакеты))
        о = ос.Обмен()
        о.кусок("с", ответ(b"abc", b"Content-Length: 3"))
        self.assertEqual([], [x for x in объекты(о.пакеты) if x.вид == "HTTP"])


# == WebSocket ========================================================================================================

class WebSocketTests(unittest.TestCase):
    def кадр(self, код, данные, fin=True, ключ=b"", rsv1=False):
        б0 = (0x80 if fin else 0) | (0x40 if rsv1 else 0) | код
        n = len(данные)
        длина = bytes([n]) if n < 126 else b"\x7e" + struct.pack(">H", n) if n < 65536 else b"\x7f" + struct.pack(">Q", n)
        длина = bytes([длина[0] | (0x80 if ключ else 0)]) + длина[1:]
        return bytes([б0]) + длина + ключ + (obekty._снять_маску(данные, ключ) if ключ else данные)

    def test_маска_фрагменты_длины_и_управляющие(self):
        большое = bytes(range(256)) * 300
        поток = (self.кадр(1, b'{"a"', fin=False, ключ=b"\x01\x02\x03\x04") + self.кадр(9, b"ping")
                 + self.кадр(0, b': 1}', ключ=b"\x05\x06\x07\x08") + self.кадр(2, большое[:200])
                 + self.кадр(2, большое) + self.кадр(8, b"\x03\xe8") + self.кадр(10, b""))
        сообщения, конец = obekty.кадры_websocket(поток, 0)
        self.assertEqual(len(поток), конец)
        self.assertEqual([(1, b'{"a": 1}', ["собрано из 2 кадров"]), (2, большое[:200], []), (2, большое, [])],
                         [(к, д, з) for _, _, к, д, з in сообщения])
        self.assertEqual(0, сообщения[0][0])
        self.assertEqual(b"", obekty._снять_маску(b"", b"\x01\x02\x03\x04"))
        self.assertEqual(b"\x00\x00\x00\x00\x05", obekty._снять_маску(b"\x01\x02\x03\x04\x04", b"\x01\x02\x03\x04"))

    def test_обрыв_и_чужое(self):
        for поток, конец in ((b"\x81\x05ab", 0), (b"\xa1\x00", 0), (b"\x83\x00", 0), (b"\x81\x7e\x00", 0),
                             (b"\x81\x7f" + bytes(7), 0), (b"\x81", 0), (b"\x80\x00\x81\x00", 4)):
            with self.subTest(поток=поток):
                сообщения, к = obekty.кадры_websocket(поток, 0)
                self.assertEqual(конец, к)
        сообщения, _ = obekty.кадры_websocket(b"\x80\x00\x81\x00", 0)
        self.assertEqual([(2, 4, 1, b"", [])], сообщения)

    def test_permessage_deflate_окно_общее_и_отдельное(self):
        def сжать(сжиматель, данные):
            return (сжиматель.compress(данные) + сжиматель.flush(zlib.Z_SYNC_FLUSH))[:-4]
        общий = zlib.compressobj(wbits=-15)
        поток = self.кадр(1, сжать(общий, b"hello hello"), rsv1=True) + self.кадр(1, сжать(общий, b"hello hello"), rsv1=True)
        сообщения, _ = obekty.кадры_websocket(поток, 0, сжатие=True)
        self.assertEqual([(b"hello hello", ["сжатие permessage-deflate снято"])] * 2, [(д, з) for _, _, _, д, з in сообщения])
        отдельные = self.кадр(1, сжать(zlib.compressobj(wbits=-15), b"x"), rsv1=True) * 2
        сообщения, _ = obekty.кадры_websocket(отдельные, 0, сжатие=True, общее_окно=False)
        self.assertEqual([b"x", b"x"], [д for _, _, _, д, _ in сообщения])
        сообщения, _ = obekty.кадры_websocket(self.кадр(1, b"\xff\xff\xff", rsv1=True), 0, сжатие=True)
        self.assertTrue(сообщения[0][4][0].startswith("permessage-deflate: не распаковалось ("))
        сообщения, _ = obekty.кадры_websocket(self.кадр(1, b"raw", rsv1=True), 0)
        self.assertEqual(b"raw", сообщения[0][3])

    def test_в_захвате(self):
        о = ос.Обмен(порт_с=8080)
        о.кусок("к", b"GET /ws HTTP/1.1\r\nUpgrade: WebSocket\r\n\r\n")
        о.кусок("с", b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Extensions: permessage-deflate; "
                     b"client_no_context_takeover\r\n\r\n")
        о.кусок("к", self.кадр(1, json.dumps({"op": 1}).encode(), ключ=b"\x09\x09\x09\x09"))
        о.кусок("с", self.кадр(1, b"plain text") + self.кадр(2, b"\x00\x01"))
        а, б, в = объекты(о.пакеты)
        self.assertEqual(("JSON", "ws-1-к1.json", "application/json", b'{"op": 1}', ["текстовое сообщение"]),
                         (а.вид, а.имя, а.тип, а.данные, а.заметки))
        self.assertEqual(("WebSocket", "ws-1-с1.txt", "text/plain", [4]), (б.вид, б.имя, б.тип, б.пакеты))
        self.assertEqual(("ws-1-с2.bin", ["двоичное сообщение"]), (в.имя, в.заметки))
        о2 = ос.Обмен(порт_с=8080)
        о2.кусок("к", b"GET /ws HTTP/1.1\r\nUpgrade: h2c\r\n\r\n")
        о2.кусок("с", b"HTTP/1.1 101 Switching Protocols\r\n\r\n" + self.кадр(1, b"text"))
        self.assertEqual([], [x for x in объекты(о2.пакеты) if x.вид == "WebSocket"])


# == FTP ==============================================================================================================

class FtpTests(unittest.TestCase):
    def управление(self, строки, порт_к=40001):
        у = ос.Обмен(порт_к=порт_к, порт_с=21)
        for кто, строка in строки:
            у.кусок(кто, строка.encode() + b"\r\n")
        return у

    def test_пассивный_активный_epsv_eprt_stou_list_rest(self):
        у = self.управление([("с", "220 ok"), ("к", "PASV"), ("с", "227 Entering Passive Mode (10,0,0,2,195,80)"),
                             ("к", "REST 100"), ("к", "RETR /pub/архив.zip")])
        данные1 = ос.Обмен(порт_к=40002, порт_с=50000)
        данные1.куски("с", обр.zip_([("a", b"1")]), 50)
        у2 = self.управление([("к", "PORT 10,0,0,1,156,68"), ("с", "200 ok"), ("к", "STOR up.bin")], порт_к=40011)
        данные2 = ос.Обмен(кл="10.0.0.1", сер="10.0.0.2", порт_к=40004, порт_с=20)
        данные2.кусок("к", b"payload-2")
        у3 = self.управление([("к", "EPSV"), ("с", "229 Entering Extended Passive Mode (|||6446|)"), ("к", "STOU"),
                              ("с", "150 FILE: new.dat")], порт_к=40012)
        данные3 = ос.Обмен(порт_к=40005, порт_с=6446)
        данные3.кусок("к", b"stou-data")
        у4 = self.управление([("к", "EPRT |1|10.0.0.1|6275|"), ("к", "LIST")], порт_к=40013)
        данные4 = ос.Обмен(кл="10.0.0.1", сер="10.0.0.2", порт_к=6275, порт_с=20)
        данные4.кусок("с", b"drwx a\r\n")
        кадры = у.пакеты + данные1.пакеты + у2.пакеты + данные2.пакеты + у3.пакеты + данные3.пакеты + у4.пакеты + данные4.пакеты
        а, б, в, г = объекты(кадры)
        self.assertEqual(("FTP", "архив.zip", обр.zip_([("a", b"1")])), (а.вид, а.имя, а.данные))
        self.assertEqual(["RETR /pub/архив.zip; канал PASV 10.0.0.2:50000",
                          "докачка с байта 100 (REST) — начала файла в захвате нет"], а.заметки)
        self.assertEqual(("up.bin", b"payload-2", ["STOR up.bin; канал PORT 10.0.0.1:40004"]), (б.имя, б.данные, б.заметки))
        self.assertEqual(("new.dat", ["STOU; канал EPSV 10.0.0.2:6446"]), (в.имя, в.заметки))
        self.assertEqual(("список-каталога-8.txt", b"drwx a\r\n", ["LIST; канал EPRT 10.0.0.1:6275"]), (г.имя, г.данные, г.заметки))

    def test_передача_без_канала_и_канал_без_команды(self):
        у = self.управление([("к", "RETR x")])
        self.assertEqual([("RETR", "x", None)], [(п.команда, п.аргумент, п.порт) for п in obekty.ftp_управление(
            obekty.соединения(*ос.разобрать(у.пакеты)[:2])[0][0])])
        у = self.управление([("к", "PORT 10,0,0,1,3,1"), ("к", "EPRT |1|x|"), ("к", "EPRT"), ("к", "REST x"),
                             ("к", "RETR y"), ("с", "150 FILE: z"), ("к", "STOU"), ("с", "150 no name"),
                             ("с", "229 (|||x|)"), ("с", "227 bad")])
        передачи = obekty.ftp_управление(obekty.соединения(*ос.разобрать(у.пакеты)[:2])[0][0])
        self.assertEqual([("RETR", "10.0.0.1", 769, "PORT", 0, ""), ("STOU", "10.0.0.1", 769, "PORT", 0, "")],
                         [(п.команда, п.адрес, п.порт, п.как, п.докачка, п.имя) for п in передачи])
        данные = ос.Обмен(порт_к=40002, порт_с=50001)
        данные.кусок("с", b"orphan")
        у = self.управление([("к", "PASV"), ("с", "227 (10,0,0,2,195,81)")])
        кадры = у.пакеты + данные.пакеты
        (о,) = объекты(кадры)
        self.assertEqual(("FTP", "ftp-data-2.txt", ["канал данных без команды передачи в захвате"]), (о.вид, о.имя, о.заметки))

    def test_канал_до_объявления_не_берётся(self):
        данные = ос.Обмен(порт_к=40002, порт_с=50000)
        данные.кусок("с", b"early")
        у = self.управление([("к", "PASV"), ("с", "227 (10,0,0,2,195,80)"), ("к", "RETR a")])
        self.assertEqual([], объекты(данные.пакеты + у.пакеты))

    def test_роли_по_порту(self):
        у = ос.Обмен(порт_к=40001, порт_с=21)
        у.кусок("к", b"RETR a\r\n")
        у.кусок("с", b"hello\r\n")
        соед = obekty.соединения(*ос.разобрать(у.пакеты)[:2])[0][0]
        клиент, сервер = obekty._роли(соед, obekty.re.compile(rb"\d{3}[ -]"))
        self.assertEqual(("10.0.0.1:40001", "10.0.0.2:21"), (клиент.от, сервер.от))
        у = ос.Обмен(порт_к=21, порт_с=40001)
        у.кусок("с", b"220 x\r\n")
        соед = obekty.соединения(*ос.разобрать(у.пакеты)[:2])[0][0]
        клиент, сервер = obekty._роли(соед, obekty.re.compile(rb"\d{3}[ -]"))
        self.assertEqual(("10.0.0.1:21", "10.0.0.2:40001", b""), (клиент.от, сервер.от, bytes(клиент.данные)))


# == TFTP =============================================================================================================

class TftpTests(unittest.TestCase):
    def test_чтение_запись_повтор_пропуск_oack_ошибка(self):
        файл = bytes(range(256)) * 5
        кадры = [ос.udp("\x00\x01dir/конфиг.cfg\x00octet\x00".encode(), 3000, 69)]
        кадры.append(ос.udp(b"\x00\x06blksize\x00600\x00", 4000, 3000, src="10.0.0.2", dst="10.0.0.1"))
        for блок, кусок in ((1, файл[:600]), (1, файл[:600]), (2, файл[600:1200]), (3, файл[1200:])):
            кадры.append(ос.udp(b"\x00\x03" + блок.to_bytes(2, "big") + кусок, 4000, 3000, src="10.0.0.2", dst="10.0.0.1"))
            кадры.append(ос.udp(b"\x00\x04" + блок.to_bytes(2, "big"), 3000, 4000))
        кадры.append(ос.udp(b"\x00\x02up.bin\x00octet\x00", 3001, 69))
        кадры.append(ос.udp(b"\x00\x04\x00\x00", 4001, 3001, src="10.0.0.2", dst="10.0.0.1"))
        кадры.append(ос.udp(b"\x00\x03\x00\x02" + b"B" * 512, 3001, 4001))
        кадры.append(ос.udp(b"\x00\x05\x00\x03disk full\x00", 4001, 3001, src="10.0.0.2", dst="10.0.0.1"))
        а, б = объекты(кадры)
        self.assertEqual(("TFTP", "конфиг.cfg", файл, "UDP 10.0.0.2:4000 → 10.0.0.1:3000", [3, 7, 9]),
                         (а.вид, а.имя, а.данные, а.поток, а.пакеты))
        self.assertEqual(["чтение (RRQ), режим octet, блок 600"], а.заметки)
        self.assertEqual(("up.bin", b"B" * 512), (б.имя, б.данные))
        self.assertEqual(["запись (WRQ), режим octet, блок 512", "пропущено блоков: 1",
                          "обрыв: последнего (короткого) блока в захвате нет", "ошибка TFTP 3: disk full"], б.заметки)

    def test_без_данных_и_развёртка(self):
        self.assertEqual([], объекты([ос.udp(b"\x00\x01a\x00octet\x00", 3000, 69)]))
        self.assertEqual([65534, 65535, 65536, 65537, 65536], obekty._развернуть([65534, 65535, 0, 1, 0]))
        self.assertEqual([5, 3, 6], obekty._развернуть([5, 3, 6]))
        self.assertEqual([10, 10 - 32768], obekty._развернуть([10, 10 + 32768]))
        self.assertEqual([10, 10 - 32767], obekty._развернуть([10, (10 - 32767) % 65536]))

    def test_второй_запрос_того_же_клиента_делит_блоки(self):
        кадры = [ос.udp(b"\x00\x01a\x00octet\x00", 3000, 69),
                 ос.udp(b"\x00\x03\x00\x01AAA", 4000, 3000, src="10.0.0.2", dst="10.0.0.1"),
                 ос.udp(b"\x00\x01b\x00octet\x00", 3000, 69),
                 ос.udp(b"\x00\x03\x00\x01BB", 4002, 3000, src="10.0.0.2", dst="10.0.0.1"),
                 ос.udp(b"\x00\x03\x00", 4002, 3000, src="10.0.0.2", dst="10.0.0.1")]
        а, б = объекты(кадры)
        self.assertEqual([("a.txt", b"AAA"), ("b.txt", b"BB")], [(а.имя, а.данные), (б.имя, б.данные)])


# == почта ============================================================================================================

class ПочтаTests(unittest.TestCase):
    def test_smtp_data_точки_вложение_base64_и_qp(self):
        письмо = ос.письмо_с_вложением(обр.png())
        qp = (b"From: a\r\nSubject: =?UTF-8?Q?=D0=A2=D0=B5=D1=81=D1=82?=\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; "
              b"boundary=Q\r\n\r\n--Q\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable"
              b"\r\n\r\n=D0=9F=D1=80=D0=B8\r\n--Q\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; "
              b"filename*=UTF-8''%D1%84.bin\r\n\r\nxyz\r\n--Q\r\nContent-Type: text/html\r\nContent-Disposition: attachment\r\n\r\n"
              b"<p>\r\n--Q--\r\n")
        м = ос.Обмен(порт_к=41004, порт_с=25)
        for кто, строка in (("с", b"220 mail\r\n"), ("к", b"EHLO x\r\n"), ("к", b"DATA\r\n")):
            м.кусок(кто, строка)
        м.куски("к", письмо.replace(b"\r\n.", b"\r\n..") + b"\r\n.\r\n", 200)
        м.кусок("к", b"DATA\r\n" + qp + b".\r\n")
        м.кусок("к", b"DATA\r\n.\r\nSTARTTLS\r\nDATA\r\nnever\r\n.\r\n")
        а, б, в, г, д, е, ж, з = объекты(м.пакеты)
        self.assertEqual(("почта", "Отчёт.eml", "message/rfc822", письмо + b"\r\n", "TCP 10.0.0.1:41004 → 10.0.0.2:25"),
                         (а.вид, а.имя, а.тип, а.данные, а.поток))
        self.assertEqual(("часть письма", "письмо-1-1-часть-1.txt", ".строка с точкой\r\nтекст".encode()),
                         (б.вид, б.имя, б.данные.rstrip(b"\r\n")))
        self.assertEqual(("вложение", "картинка.png", обр.png(), ["часть 2 письма «Отчёт»", "base64 снято"]),
                         (в.вид, в.имя, в.данные, в.заметки))
        self.assertEqual(("Тест.eml", "При".encode()), (г.имя, д.данные.rstrip(b"\r\n")))
        self.assertEqual(["часть 1 письма «Тест»", "quoted-printable снято"], д.заметки)
        self.assertEqual(("ф.bin", "вложение"), (е.имя, е.вид))
        self.assertEqual(("письмо-1-2-вложение-3.html", "вложение"), (ж.имя, ж.вид))
        self.assertEqual(("письмо-1-3.eml", b""), (з.имя, з.данные))

    def test_smtp_bdat_и_обрыв(self):
        письмо = b"Subject: B\r\n\r\nbody"
        м = ос.Обмен(порт_к=41004, порт_с=25)
        м.кусок("с", b"220 x\r\n")
        м.кусок("к", b"BDAT 10\r\n" + письмо[:10] + b"bdat %d last\r\n" % (len(письмо) - 10) + письмо[10:])
        м.кусок("к", b"BDAT 5\r\nabc")
        м.кусок("к", b"x", )
        а, б = объекты(м.пакеты)
        self.assertEqual(("B.eml", письмо, []), (а.имя, а.данные, а.заметки))
        self.assertEqual((b"abcx", ["обрыв: BDAT LAST в захвате нет"]), (б.данные, б.заметки))
        м = ос.Обмен(порт_к=41004, порт_с=25)
        м.кусок("к", b"HELO x\r\nDATA\r\nSubject: C\r\n\r\ntext")
        (о,) = объекты(м.пакеты)
        self.assertEqual(("C.eml", ["обрыв: конца письма (строки «.») в захвате нет"]), (о.имя, о.заметки))
        м = ос.Обмен(порт_к=41004, порт_с=25)
        м.кусок("к", b"HELO x")
        self.assertEqual([], объекты(м.пакеты))

    def test_pop3_приветствие_auth_list_retr_stls(self):
        письмо = b"Subject: P\r\n\r\n..dot\r\nline"
        п = ос.Обмен(порт_к=41014, порт_с=110)
        п.кусок("с", b"+OK ready\r\n")
        п.кусок("к", b"AUTH PLAIN\r\nAHUAcA==\r\nLIST\r\nRETR 7\r\nDELE 7\r\nSTLS\r\nRETR 8\r\n")
        п.кусок("с", b"+ \r\n+OK auth\r\n+OK list\r\n1 10\r\n.\r\n+OK\r\n" + письмо + b"\r\n.\r\n-ERR no\r\n+OK tls\r\n+OK\r\nx\r\n.\r\n")
        (о,) = объекты(п.пакеты)
        self.assertEqual(("P.eml", b"Subject: P\r\n\r\n.dot\r\nline\r\n", "TCP 10.0.0.2:110 → 10.0.0.1:41014"),
                         (о.имя, о.данные, о.поток))
        п = ос.Обмен(порт_к=41014, порт_с=110)
        п.кусок("к", b"RETR 1\r\n")
        п.кусок("с", b"+OK\r\nSubject: Q\r\n\r\ncut")
        (о,) = объекты(п.пакеты)
        self.assertEqual(("Q.eml", ["обрыв: конца письма в захвате нет"]), (о.имя, о.заметки))
        п = ос.Обмен(порт_к=41014, порт_с=110)
        п.кусок("к", b"RETR 1\r\n")
        п.кусок("с", b"+OK")
        self.assertEqual([], объекты(п.пакеты))

    def test_imap_body_разделы_rfc822_binary(self):
        письмо = b"Subject: I\r\n\r\nhi"
        и = ос.Обмен(порт_к=41005, порт_с=143)
        и.кусок("с", b"* OK IMAP\r\n")
        и.кусок("к", b"a FETCH 1:3 (BODY[])\r\n")
        и.кусок("с", b"* 3 FETCH (UID 9 BODY[] {%d}\r\n" % len(письмо) + письмо + b")\r\n"
                     b"* 4 FETCH (BODY[HEADER] {3}\r\nabc)\r\n* 4 FETCH (BODY[1.MIME] {2}\r\nab)\r\n"
                     b"* 4 FETCH (RFC822.HEADER {2}\r\nab)\r\n* 5 FETCH (BODY[2]<10> {4}\r\nPART)\r\n"
                     b"* 6 FETCH (BINARY[1.2] ~{3}\r\nBIN)\r\n* 7 FETCH (RFC822 {%d}\r\n" % len(письмо) + письмо
                + b")\r\n* 8 FETCH (RFC822.TEXT {2}\r\nTX)\r\n* 9 FETCH (BODY[] {99}\r\nshort")
        а, б, в, г, д, е = объекты(и.пакеты)
        self.assertEqual(("I.eml", письмо), (а.имя, а.данные))
        self.assertEqual(("часть письма", "imap-5-2.txt", b"PART", ["часть с байта 10"]), (б.вид, б.имя, б.данные, б.заметки))
        self.assertEqual(("imap-6-1-2.txt", b"BIN"), (в.имя, в.данные))
        self.assertEqual(("I.eml", "imap-8-text.txt", b"TX"), (г.имя, д.имя, д.данные))
        self.assertEqual((b"short", ["обрыв: получено 5 из 99 байт"]), (е.данные, е.заметки))

    def test_письмо_без_заголовков(self):
        сбор = obekty.Сбор(10, 1 << 20)
        with mock.patch.object(obekty.email, "message_from_bytes", side_effect=ValueError("x")):
            obekty.письмо(b"x", "п", [1], ["з"], сбор, "осн")
        self.assertEqual([("осн.eml", ["з", "заголовки письма не разобрались"])], [(о.имя, о.заметки) for о in сбор.объекты])
        сбор = obekty.Сбор(10, 1 << 20)
        obekty.письмо(b"Subject: \r\n\r\nbody", "п", [1], [], сбор, "осн")
        self.assertEqual(["осн.eml"], [о.имя for о in сбор.объекты])
        self.assertEqual(b"a\r\nb\r\n.c", obekty.без_точек(b"a\r\n.b\r\n..c"))


# == RTP ==============================================================================================================

def wav_заголовок(код, отсчётов):
    """Заголовок, выписанный вручную по RFC 2361 и раскладке libsndfile: RIFF, WAVE, fmt (18), fact, data."""
    return (b"RIFF" + struct.pack("<I", 4 + 26 + 12 + 8 + отсчётов + (отсчётов & 1)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHHH", 18, код, 1, 8000, 8000, 1, 8, 0)
            + b"fact" + struct.pack("<II", 4, отсчётов) + b"data" + struct.pack("<I", отсчётов))


class RtpTests(unittest.TestCase):
    def test_pcmu_wav_переход_номера_и_пропуск_тишиной(self):
        куски = [bytes([10 + i]) * 160 for i in range(6)]
        номера = [65533, 65534, 65535, 0, 2, 3]
        кадры = [ос.udp(ос.rtp(0, н, 1000 + 160 * (i if i < 4 else i + 1), 0x11223344, куски[i]), 20000, 30000)
                 for i, н in enumerate(номера)]
        кадры.insert(2, кадры[1])                                         # повтор
        (о,) = объекты(кадры)
        отсчёты = b"".join(куски[:4]) + b"\xff" * 160 + b"".join(куски[4:])
        self.assertEqual(wav_заголовок(7, 1120) + отсчёты, о.данные)
        self.assertEqual(("RTP-звук", "rtp-10.0.0.1-20000-10.0.0.2-30000-11223344.wav", "audio/wav"), (о.вид, о.имя, о.тип))
        self.assertEqual(["SSRC 0x11223344, пакетов 6", "кодек PCMU/8000 (тип 0)", "пропущено пакетов: 1",
                          "повторов отброшено: 1", "пропуски заполнены тишиной: 160 отсчётов"], о.заметки)
        self.assertEqual(([1, 2, 4, 5, 6, 7], "RIFF (wav)"), (о.пакеты, о.содержимое))

    def test_pcma_нечётная_длина_без_тишины_и_сырой(self):
        кадры = [ос.udp(ос.rtp(8, 1, 0, 7, b"\x01\x02\x03"), 20000, 30000), ос.udp(ос.rtp(8, 3, 3 + 160, 7, b"\x04"), 20000, 30000)]
        (о,) = объекты(кадры)
        отсчёты = b"\x01\x02\x03" + b"\xd5" * 160 + b"\x04"
        self.assertEqual(wav_заголовок(6, 164) + отсчёты, о.данные)
        (о,) = объекты(кадры, тишина=False)
        self.assertEqual(wav_заголовок(6, 4) + b"\x01\x02\x03\x04", о.данные)
        self.assertEqual(["SSRC 0x00000007, пакетов 2", "кодек PCMA/8000 (тип 8)", "пропущено пакетов: 1"], о.заметки)
        (о,) = объекты(кадры, звук="сырой")
        self.assertEqual((отсчёты, "al", "audio/PCMA"), (о.данные, о.имя.rsplit(".")[-1], о.тип))
        кадры = [ос.udp(ос.rtp(0, 1, 0, 7, b"\x01"), 20000, 30000), ос.udp(ос.rtp(0, 2, 1 + obekty.ТИШИНА_ДО + 1, 7, b"\x02"), 20000, 30000)]
        (о,) = объекты(кадры, звук="сырой")
        self.assertEqual((b"\x01\x02", "ul"), (о.данные, о.имя.rsplit(".")[-1]))
        self.assertIn(f"разрыв отметок времени {obekty.ТИШИНА_ДО + 1} тактов — тишина не вставлена", о.заметки)
        кадры = [ос.udp(ос.rtp(0, 1, 0, 7, b"\x01"), 20000, 30000), ос.udp(ос.rtp(0, 2, 1 + obekty.ТИШИНА_ДО, 7, b"\x02"), 20000, 30000)]
        (о,) = объекты(кадры, звук="сырой")
        self.assertEqual(2 + obekty.ТИШИНА_ДО, len(о.данные))

    def test_заголовок_rtp_csrc_расширение_добивка(self):
        д = ос.rtp(96, 5, 6, 7, b"PAY", маркер=1, csrc=(1, 2), расширение=b"\x00" * 8, добивка=3)
        self.assertEqual((96, 1, 5, 6, 7, b"PAY"), obekty.заголовок_rtp(д))
        self.assertIsNone(obekty.заголовок_rtp(b"\x80" * 11))
        self.assertIsNone(obekty.заголовок_rtp(b"\x40" + bytes(15)))
        self.assertIsNone(obekty.заголовок_rtp(b"\x90\x00" + bytes(10) + b"\x00\x00"))
        self.assertIsNone(obekty.заголовок_rtp(b"\xa0\x00" + bytes(9) + b"\x09"))
        self.assertEqual(b"", obekty.заголовок_rtp(b"\x80\x00" + bytes(10))[5])

    def test_h264_stap_fu_одиночный_и_nal(self):
        sps, pps = b"\x67\x42\x00\x1e", b"\x68\xce"
        idr = b"\x65" + bytes(range(200))
        пакеты = [b"\x18" + len(sps).to_bytes(2, "big") + sps + len(pps).to_bytes(2, "big") + pps + b"\x00",
                  bytes([0x60 | 28, 0x80 | 5]) + idr[1:100], bytes([0x60 | 28, 5]) + idr[100:150],
                  bytes([0x60 | 28, 0x40 | 5]) + idr[150:], b"\x41" + b"P" * 5,
                  b"\x19" + b"\x00\x07" + (2).to_bytes(2, "big") + b"\x06\x01",
                  bytes([0x60 | 29, 0x80 | 1]) + b"\x00\x01" + b"AB", bytes([0x60 | 29, 0x40 | 1]) + b"CD",
                  b"\x1a" + bytes(5), b""]
        кадры = [ос.udp(ос.rtp(34 + 62, 100 + i, 0, 9, п), 40000, 30002) for i, п in enumerate(пакеты)]
        sdp = {"10.0.0.2|30002": [0, {"вид": "video", "rtpmap": {"96": "H264/90000"}}]}
        сводки, нагрузки, _ = ос.разобрать(кадры)
        (о,) = obekty.собрать(сводки, нагрузки, sdp=sdp)["объекты"]
        с = b"\x00\x00\x00\x01"
        self.assertEqual(с + sps + с + pps + с + idr + с + b"\x41PPPPP" + с + b"\x06\x01" + с + b"\x61ABCD", о.данные)
        self.assertEqual(("RTP-видео", "video/H264", "H.264 (Annex B)"), (о.вид, о.тип, о.содержимое))
        self.assertIn("пакеты вида 26 (MTAP/зарезервированные) пропущены", о.заметки)
        self.assertIn("кодек H264/90000 (тип 96, по SDP)", о.заметки)
        (о,) = obekty.собрать(сводки, нагрузки, sdp=sdp, настройки=obekty.Настройки(видео="nal"))["объекты"]
        self.assertTrue(о.данные.startswith(struct.pack(">I", 4) + sps + struct.pack(">I", 2) + pps))
        self.assertTrue(о.имя.endswith(".nal"))

    def test_h264_оборванные_фрагменты(self):
        пакеты = [(1, bytes([0x7C, 0x85]) + b"a"), (3, bytes([0x7C, 0x45]) + b"c"),       # пропуск в середине
                  (4, bytes([0x7C, 0x05]) + b"x"),                                        # продолжение без начала
                  (5, bytes([0x7C, 0x85]) + b"s"), (6, bytes([0x7C, 0x85]) + b"t"),       # два начала подряд
                  (7, bytes([0x7C, 0x45]) + b"u"), (8, bytes([0x7C, 0x85]) + b"v")]       # не закрыт
        данные, заметки = obekty._h264(пакеты, "annexb")
        self.assertEqual(b"\x00\x00\x00\x01\x65tu", данные)
        self.assertEqual(["NAL, оборванных пропуском пакетов (FU без начала или конца): 5 — выброшены"], заметки)
        self.assertEqual((b"", []), obekty._h264([(1, bytes([0x7C, 0x85]))], "annexb")[:1] + ([],))
        self.assertEqual((b"", []), obekty._h264([(1, b"\x7c")], "annexb"))

    def test_h265_ap_fu_paci(self):
        vps = b"\x40\x01\x0c"
        ap = b"\x60\x01" + len(vps).to_bytes(2, "big") + vps + (3).to_bytes(2, "big") + b"\x42\x01\x01"
        fu = [b"\x62\x01" + bytes([0x80 | 19]) + b"AA", b"\x62\x01" + bytes([19]) + b"BB", b"\x62\x01" + bytes([0x40 | 19]) + b"CC"]
        данные, заметки = obekty._h265([(1, ap), (2, fu[0]), (3, fu[1]), (4, fu[2]), (5, b"\x64\x01xx"), (6, b"\x26\x01Z"),
                                        (7, b"\x62"), (8, b"\x62\x01" + bytes([19]) + b"q"), (10, b"\x62\x01" + bytes([0x80 | 1]) + b"r"),
                                        (12, b"\x62\x01" + bytes([0x40 | 1]) + b"s")], "annexb")
        с = b"\x00\x00\x00\x01"
        self.assertEqual(с + vps + с + b"\x42\x01\x01" + с + b"\x26\x01AABBCC" + с + b"\x26\x01Z", данные)
        self.assertEqual(["NAL, оборванных пропуском пакетов (FU без начала или конца): 3 — выброшены",
                          "пакеты вида 50 (PACI/зарезервированные) пропущены"], заметки)
        данные, _ = obekty._h265([(1, b"\x62\x01" + bytes([0x80 | 1]) + b"r"), (2, b"\x62\x01" + bytes([0x80 | 1]) + b"s"),
                                  (3, b"\x62\x01" + bytes([0x40 | 1]) + b"t")], "nal")
        self.assertEqual(struct.pack(">I", 4) + b"\x02\x01st", данные)

    def test_amr_октеты_полоса_потери_dtmf(self):
        окт = [bytes([0xF0, 7 << 3 | 1 << 2]) + bytes([i]) * 31 for i in (1, 2, 3)]
        кадры = [ос.udp(ос.rtp(97, 10 + i, 160 * i, 0x55, п), 40002, 30004) for i, п in zip((0, 1, 3), окт, strict=True)]
        кадры += [ос.udp(ос.rtp(101, 20 + i, отм, 0x55, bytes([код, 0x80, 0, 160])), 40002, 30004)
                  for i, (код, отм) in enumerate(((1, 480), (1, 480), (11, 800), (16, 900), (20, 1000)))]
        кадры.append(ос.udp(ос.rtp(101, 25, 1100, 0x55, b"\x01"), 40002, 30004))
        sdp = {"10.0.0.2|30004": [0, {"вид": "audio", "rtpmap": {97: "AMR/8000", 101: "telephone-event/8000"}}]}
        сводки, нагрузки, _ = ос.разобрать(кадры)
        (о,) = obekty.собрать(сводки, нагрузки, sdp=sdp)["объекты"]
        кадр = lambda п: bytes([7 << 3 | 1 << 2]) + п[2:]  # noqa: E731
        self.assertEqual(b"#!AMR\n" + кадр(окт[0]) + кадр(окт[1]) + bytes([15 << 3 | 1 << 2]) + кадр(окт[2]), о.данные)
        self.assertIn("DTMF (RFC 4733): 1#flash[20]", о.заметки)
        self.assertIn("потерянные кадры — NO_DATA: 1", о.заметки)
        self.assertEqual(("RTP-звук", "audio/AMR", "AMR"), (о.вид, о.тип, о.содержимое))
        (о,) = obekty.собрать(сводки, нагрузки, sdp=sdp, настройки=obekty.Настройки(тишина=False))["объекты"]
        self.assertEqual(b"#!AMR\n" + кадр(окт[0]) + кадр(окт[1]) + кадр(окт[2]), о.данные)

    def test_amr_полоса_wb_и_отказы(self):
        # С экономией полосы (RFC 4867, 4.3): CMR 15, ToC F=0 FT=0 Q=1, 95 бит речи, добивка до октета.
        речь = int("1" * 95, 2)
        биты = (15 << (6 + 95)) | ((0 << 5 | 0 << 1 | 1) << 95) | речь
        всего = 4 + 6 + 95
        нагрузка = (биты << (-всего % 8)).to_bytes((всего + 7) // 8, "big")
        кадры = obekty._amr_полоса(нагрузка, obekty.AMR_БИТ)
        self.assertEqual([(0, 1, ((1 << 95) - 1 << 1).to_bytes(12, "big"))], кадры)
        self.assertIsNone(obekty._amr_полоса(нагрузка + b"\x00", obekty.AMR_БИТ))
        self.assertIsNone(obekty._amr_полоса(b"\xf0", obekty.AMR_БИТ))
        self.assertIsNone(obekty._amr_полоса(bytes([0xF0 | 0, 0b11110100]), obekty.AMR_БИТ))
        self.assertEqual([(15, 0, b"")], obekty._amr_полоса(bytes([0xF7, 0x80]), obekty.AMR_БИТ))
        self.assertIsNone(obekty._amr_октеты(b"\xf0", obekty.AMR_БИТ))
        self.assertIsNone(obekty._amr_октеты(b"\xf0" + bytes([13 << 3]), obekty.AMR_БИТ))
        self.assertIsNone(obekty._amr_октеты(b"\xf0" + bytes([0x80 | 7 << 3]), obekty.AMR_БИТ))
        self.assertEqual([(15, 1, b""), (14, 0, b"")], obekty._amr_октеты(bytes([0xF0, 0x80 | 15 << 3 | 4, 14 << 3]), obekty.AMR_WB_БИТ))
        self.assertEqual([(9, 0, bytes(5))], obekty._amr_октеты(bytes([0xF0, 9 << 3]) + bytes(5), obekty.AMR_WB_БИТ))
        кадры_rtp = [ос.udp(ос.rtp(98, 1, 0, 5, нагрузка), 40006, 30006),
                     ос.udp(ос.rtp(98, 2, 320, 5, b"\xff\xff"), 40006, 30006)]
        sdp = {"10.0.0.2|30006": [0, {"вид": "audio", "rtpmap": {"98": "AMR-WB/16000"}}]}
        сводки, нагрузки, _ = ос.разобрать(кадры_rtp)
        (о,) = obekty.собрать(сводки, нагрузки, sdp=sdp)["объекты"]
        self.assertTrue(о.имя.endswith(".awb"))
        self.assertIn("пакет не разобран как AMR — пропущен", о.заметки)

    def test_прочие_кодеки_и_вид_по_sdp(self):
        кадры = [ос.udp(ос.rtp(9, 1, 0, 1, b"G722"), 20000, 30000), ос.udp(ос.rtp(33, 1, 0, 2, обр.ts(1)), 20002, 30002),
                 ос.udp(ос.rtp(18, 1, 0, 3, b"G729"), 20004, 30004), ос.udp(ос.rtp(127, 1, 0, 4, b"\x00\x01"), 20006, 30006),
                 ос.udp(ос.rtp(126, 1, 0, 5, b"vv"), 20008, 30008)]
        sdp = {"10.0.0.2|30008": [0, {"вид": "video", "rtpmap": {}}]}
        сводки, нагрузки, _ = ос.разобрать(кадры)
        а, б, в, г, д = obekty.собрать(сводки, нагрузки, sdp=sdp)["объекты"]
        self.assertEqual(("RTP-звук", "audio/G722", b"G722"), (а.вид, а.тип, а.данные))
        self.assertTrue(а.имя.endswith(".g722"))
        self.assertEqual(("RTP-видео", "video/MP2T"), (б.вид, б.тип))
        self.assertEqual(("RTP-звук", "G729/8000", b"G729", True), (в.вид, в.тип, в.данные, в.имя.endswith(".rtp.bin")))
        self.assertIn("кодек G729: нагрузки RTP подряд, без заголовков", в.заметки)
        self.assertEqual(("RTP-поток", "application/octet-stream"), (г.вид, г.тип))
        self.assertIn("кодек не известен: нагрузки RTP подряд, без заголовков", г.заметки)
        self.assertEqual("RTP-видео", д.вид)

    def test_кодек_и_описание_sdp(self):
        self.assertEqual(("PCMU", 8000, "A"), obekty._кодек(None, 0))
        self.assertEqual(("", 0, ""), obekty._кодек(None, 99))
        self.assertEqual(("opus", 48000, "A"), obekty._кодек({"вид": "audio", "rtpmap": {111: "opus/48000"}}, 111))
        self.assertEqual(("x", 0, ""), obekty._кодек({"вид": "text", "rtpmap": {"5": "x/abc"}}, 5))
        self.assertEqual(("", 0, "V"), obekty._кодек({"вид": "Video"}, 99))
        self.assertEqual({"a": 1}, obekty._описание_sdp({"h|1": {"a": 1}}, [("h", 2), ("h", 1)]))
        self.assertIsNone(obekty._описание_sdp({}, [("h", 1)]))

    def test_только_события_и_rtcp(self):
        кадры = [ос.udp(ос.rtp(101, 1, 0, 5, bytes([5, 0x80, 0, 1])), 40002, 30004)]
        sdp = {"10.0.0.2|30004": [0, {"вид": "audio", "rtpmap": {101: "telephone-event/8000"}}]}
        сводки, нагрузки, _ = ос.разобрать(кадры)
        (о,) = obekty.собрать(сводки, нагрузки, sdp=sdp)["объекты"]
        self.assertIn("DTMF (RFC 4733): 5", о.заметки)
        сводки[0]["стек"] = сводки[0]["стек"] + ["RTCP"]
        self.assertEqual([], [x for x in obekty.собрать(сводки, нагрузки, sdp=sdp)["объекты"] if x.вид.startswith("RTP")])


# == JSON, сигнатуры, опись архивов ====================================================================================

class JsonСигнатурыTests(unittest.TestCase):
    def test_json_в_udp_и_tcp(self):
        о = ос.Обмен(порт_с=9000)
        о.кусок("с", b'{"a": 1}\n[2, 3]\n')
        кадры = [ос.udp(b'{"t": 1}', 7000, 7001), ос.udp(b"not json", 7000, 7001)] + о.пакеты
        а, б, в = объекты(кадры)
        self.assertEqual(("JSON", "json-1-1.json", b'{"t": 1}', [1]), (а.вид, а.имя, а.данные, а.пакеты))
        self.assertEqual([("json-2-1.json", b'{"a": 1}'), ("json-2-2.json", b"[2, 3]")], [(б.имя, б.данные), (в.имя, в.данные)])
        (а2, *_) = объекты(кадры, json_отступ=True)
        self.assertEqual(b'{\n  "t": 1\n}', а2.данные)
        self.assertIn("перезаписано с отступами (настройка)", а2.заметки)

    def test_сигнатуры_длина_неизвестна_и_обрыв(self):
        о = ос.Обмен(порт_с=9999)
        о.куски("с", b"HDR" + обр.png() + b"XX" + b"<?xml version='1.0'?><a/>", 40)
        а, б = объекты(о.пакеты)
        self.assertEqual(("по сигнатуре", "поток1-3.png", обр.png(), "image/png"), (а.вид, а.имя, а.данные, а.тип))
        self.assertEqual(("поток1-73.xml", ["длина по формату неизвестна — до конца потока"]), (б.имя, б.заметки))
        о = ос.Обмен(порт_с=9999)
        о.кусок("с", b"H" + обр.zip_([("a", "b")])[:-10] + b"\x00" * 3)
        (о_,) = объекты(о.пакеты)
        self.assertEqual("длина по формату неизвестна — до конца потока", о_.заметки[0])
        self.assertTrue(о_.заметки[1].startswith("опись не прочиталась: "))
        о = ос.Обмен(порт_с=9999)
        данные = обр.cab()
        о.кусок("с", b"H" + данные[:-10])
        (о_,) = объекты(о.пакеты)
        self.assertEqual(f"обрыв: получено {len(данные) - 10} из {len(данные)} байт", о_.заметки[0])
        with mock.patch.object(obekty, "ОБЪЕКТ_ДО", 50):
            о = ос.Обмен(порт_с=9999)
            о.кусок("с", b"<?xml " + b"x" * 100)
            (о_,) = объекты(о.пакеты)
        self.assertEqual(50, len(о_.данные))

    def test_опись_zip_tar_gz_и_члены(self):
        z = обр.zip_([("папка/", b""), ("папка/a.txt", b"A" * 10), ("b.bin", b"B")])
        состав, заметки = obekty.состав(z, "zip")
        self.assertEqual([("папка/", 0, True), ("папка/a.txt", 10, False), ("b.bin", 1, False)],
                         [(ч["имя"], ч["длина"], ч["каталог"]) for ч in состав])
        self.assertEqual(([], False), (заметки, состав[1]["шифр"]))
        self.assertEqual(("папка/a.txt", b"A" * 10), obekty.член(z, "zip", 1))
        for номер in (0, 3, -1):
            with self.subTest(номер=номер), self.assertRaises(ValueError):
                obekty.член(z, "zip", номер)
        t = обр.tar_([("x.txt", b"xyz")], "w:gz")
        состав, _ = obekty.состав(t, "gz")
        self.assertEqual([("папка", 0, True), ("x.txt", 3, False)], [(ч["имя"], ч["длина"], ч["каталог"]) for ч in состав])
        self.assertEqual(("x.txt", b"xyz"), obekty.член(t, "gz", 1))
        with self.assertRaises(ValueError):
            obekty.член(t, "gz", 0)
        with self.assertRaises(ValueError):
            obekty.член(t, "gz", 2)
        сжато = gzip.compress(b"plain", mtime=0)
        с_именем = сжато[:3] + bytes([сжато[3] | 0x08 | 0x04]) + сжато[4:10] + b"\x02\x00XX" + "имя.txt".encode("latin-1", "replace").replace(b"?", b"i") + b"\x00" + сжато[10:]
        self.assertEqual(([{"имя": "iii.txt", "длина": 5, "каталог": False}], []), obekty.состав(с_именем, "gz"))
        self.assertEqual(("iii.txt", b"plain"), obekty.член(с_именем, "gz", 0))
        self.assertEqual(("содержимое", b"plain"), obekty.член(сжато, "gz", 0))
        self.assertEqual(([{"имя": "содержимое", "длина": 5, "каталог": False}], []), obekty.состав(__import__("bz2").compress(b"plain"), "bz2"))
        self.assertEqual("", obekty._имя_gzip(b"\x1f\x8b\x08\x08" + bytes(6) + b"\x00"))
        self.assertEqual("", obekty._имя_gzip(b"\x1f\x8b"))
        with self.assertRaises(ValueError):
            obekty.член(сжато, "gz", 1)
        with self.assertRaises(ValueError):
            obekty.член(b"x", "pdf", 0)
        self.assertEqual((None, ["RAR: без сторонних библиотек содержимое не раскрывается — архив отдаётся целиком"]),
                         obekty.состав(b"x", "rar"))
        self.assertEqual((None, []), obekty.состав(b"x", "pdf"))
        состав, заметки = obekty.состав(b"PK\x03\x04broken", "zip")
        self.assertIsNone(состав)
        self.assertTrue(заметки[0].startswith("опись не прочиталась: "))
        self.assertEqual((None, []), obekty.состав(b"\x1f\x8b not gzip", "tar"))

    def test_опись_предел_шифр_и_большие_члены(self):
        z = обр.zip_([(f"f{i}", b"x") for i in range(5)])
        with mock.patch.object(obekty, "СОСТАВ_ДО", 3):
            состав, заметки = obekty.состав(z, "zip")
            self.assertEqual((3, ["в описи первые 3 из 5"]), (len(состав), заметки))
            t = обр.tar_([(f"f{i}", b"x") for i in range(4)])
            состав, заметки = obekty.состав(t, "tar")
            self.assertEqual((3, ["в описи первые 3 из 5"]), (len(состав), заметки))
        шифр = bytearray(z)
        шифр[6] |= 1
        центр = z.index(b"PK\x01\x02")
        шифр[центр + 8] |= 1
        self.assertTrue(obekty.состав(bytes(шифр), "zip")[0][0]["шифр"])
        with self.assertRaises(ValueError):
            obekty.член(bytes(шифр), "zip", 0)
        with mock.patch.object(obekty, "ОБЪЕКТ_ДО", 0):
            with self.assertRaises(ValueError):
                obekty.член(z, "zip", 0)
            with self.assertRaises(ValueError):
                obekty.член(обр.tar_([("a", b"12")]), "tar", 1)


# == пределы, выгрузка ================================================================================================

class ПределыВыгрузкаTests(unittest.TestCase):
    def захват(self):
        return [ос.udp(json.dumps({"n": i}).encode(), 7000, 7001) for i in range(4)]

    def test_пределы_числа_байт_и_размера(self):
        итог = собрать(self.захват(), объектов_до=2)
        self.assertEqual((2, {"объектов": 2, "байт": 16}), (len(итог["объекты"]), итог["отброшено"]))
        итог = собрать(self.захват(), байт_до=17)
        self.assertEqual((2, {"объектов": 2, "байт": 16}), (len(итог["объекты"]), итог["отброшено"]))
        итог = собрать(self.захват(), байт_до=16)
        self.assertEqual(2, len(итог["объекты"]))
        итог = собрать(self.захват(), объект_до=5)
        self.assertEqual((b'{"n":', ["обрезан по пределу объекта: 5 из 8 байт"]), (итог["объекты"][0].данные, итог["объекты"][0].заметки[:1]))
        итог = собрать(self.захват(), объект_до=8)
        self.assertEqual([], итог["объекты"][0].заметки)
        self.assertEqual({1: 1000.01, 2: 1000.02, 3: 1000.03, 4: 1000.04}, итог["время"])

    def test_zip_имена_опись_отброшено_и_виды(self):
        итог = собрать(self.захват() + [ос.udp(ос.rtp(0, 1, 0, 1, b"\x01"), 20000, 30000)], объектов_до=4)
        итог["объекты"][1].имя = "../json-1-1.json"
        итог["объекты"][2].имя = 'a<b>:c"|?*.json'
        z = zipfile.ZipFile(io.BytesIO(obekty.zip_объектов(итог, настройки=obekty.Настройки(пояс=180))))
        rtp = "0004-rtp-10.0.0.1-20000-10.0.0.2-30000-00000001.wav"
        self.assertEqual(["0001-json-1-1.json", "0002-json-1-1.json", "0003-a_b__c____.json", rtp, "опись.csv",
                          "отброшено.txt"], z.namelist())
        строки = list(csv.DictReader(io.StringIO(z.read("опись.csv").decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(list(obekty.ОПИСЬ_СТОЛБЦЫ), list(строки[0]))
        self.assertEqual({"номер": "1", "вид": "JSON", "имя": "json-1-1.json", "в архиве": "0001-json-1-1.json",
                          "тип": "application/json", "содержимое": "JSON", "длина": "8", "поток": "UDP 10.0.0.1:7000 → 10.0.0.2:7001",
                          "пакеты": "1", "время": "1970-01-01T03:16:40.010000+03:00",
                          "sha256": __import__("hashlib").sha256(b'{"n": 0}').hexdigest(), "заметки": ""}, строки[0])
        self.assertEqual("Отброшено по пределам: объектов 1, байт 8.\n", z.read("отброшено.txt").decode())
        только = zipfile.ZipFile(io.BytesIO(obekty.zip_объектов(итог, ["RTP-звук"], папка="звук/")))
        self.assertEqual(["звук/" + rtp, "звук/опись.csv", "звук/отброшено.txt"], только.namelist())
        итог["объекты"][0].имя = итог["объекты"][1].имя = "same"
        итог["объекты"][0].номер = итог["объекты"][1].номер = 7
        итог["объекты"][2].имя = итог["объекты"][3].имя = "same.txt"
        итог["объекты"][2].номер = итог["объекты"][3].номер = 8
        итог["отброшено"] = {"объектов": 0, "байт": 0}
        z = zipfile.ZipFile(io.BytesIO(obekty.zip_объектов(итог, настройки=obekty.Настройки(имена="номер"))))
        self.assertEqual(["объект-0007", "объект-0007-2", "объект-0008.txt", "объект-0008-2.txt", "опись.csv"], z.namelist())
        итог["заметки"] = ["сборка"]
        self.assertIn("отброшено.txt", zipfile.ZipFile(io.BytesIO(obekty.zip_объектов(итог))).namelist())

    def test_имена_выгрузки_и_время(self):
        о = obekty.Объект("HTTP", "a.b/отчёт.pdf", "", b"", "TCP 10.0.0.2:80 → 10.0.0.1:40000", [3], номер=12)
        self.assertEqual("0012-отчёт.pdf", obekty.имя_выгрузки(о, obekty.Настройки(), 0))
        self.assertEqual("объект-0012.pdf", obekty.имя_выгрузки(о, obekty.Настройки(имена="номер"), 0))
        self.assertEqual("0012-TCP_10.0.0.2_80_10.0.0.1_40000-19700101T030000-отчёт.pdf",
                         obekty.имя_выгрузки(о, obekty.Настройки(имена="поток", пояс=180), 0))
        self.assertEqual("0012-TCP_10.0.0.2_80_10.0.0.1_40000-19700101T000001-отчёт.pdf",
                         obekty.имя_выгрузки(о, obekty.Настройки(имена="поток"), 1.5))
        о.имя = "безрасширения"
        self.assertEqual("объект-0012", obekty.имя_выгрузки(о, obekty.Настройки(имена="номер"), None))
        self.assertEqual("", obekty.время_текстом(None, 0))
        self.assertEqual("1969-12-31T22:30:00.000000-01:30", obekty.время_текстом(0, -90))
        self.assertEqual("объект", obekty.безопасное_имя(".."))
        self.assertEqual("a_b", obekty.безопасное_имя("a..b"))
        json_ = obekty.Объект("JSON", "", "", b"", "", [])
        self.assertEqual({"JSON": 2, "HTTP": 1}, obekty.по_видам({"объекты": [json_, о, json_]}))

    def test_настройки_ключ_и_словарь(self):
        self.assertEqual((True, True, "wav", "annexb", False, obekty.ОБЪЕКТ_ДО, obekty.БАЙТ_ДО, obekty.ОБЪЕКТОВ_ДО),
                         obekty.Настройки(имена="номер", пояс=60).ключ())
        о = obekty.Объект("HTTP", "a", "t", b"xy", "п", list(range(1, 60)), ["з"], "С", "c", [{"имя": "x"}], 3)
        self.assertEqual({"номер": 3, "вид": "HTTP", "имя": "a", "тип": "t", "длина": 2, "поток": "п",
                          "пакеты": list(range(1, 51)), "пакетов": 59, "первый": 1, "заметки": ["з"], "содержимое": "С",
                          "состав": [{"имя": "x"}]}, о.в_словарь())
        self.assertEqual(0, obekty.Объект("x", "", "", b"", "", []).в_словарь()["первый"])

    def test_все_виды_вместе_по_порядку_пакетов(self):
        итог = собрать(ос.захват_всех_видов())
        self.assertEqual(["HTTP", "JSON", "HTTP", "HTTP", "JSON", "WebSocket", "FTP", "TFTP", "почта", "часть письма",
                          "вложение", "RTP-звук", "RTP-видео", "RTP-звук", "JSON", "по сигнатуре"],
                         [о.вид for о in итог["объекты"]])
        self.assertEqual(list(range(1, 17)), [о.номер for о in итог["объекты"]])
        первые = [о.пакеты[0] for о in итог["объекты"]]
        self.assertEqual(sorted(первые), первые)
        zip_ = next(о for о in итог["объекты"] if о.вид == "FTP")
        self.assertEqual(["a.txt", "папка/b.bin"], [ч["имя"] for ч in zip_.состав])


if __name__ == "__main__":
    unittest.main()
