"""SIP (RFC 3261): стартовая строка, заголовки с краткими формами и продолжениями, разбор From/To/Via/CSeq/
авторизации/Reason, тело по Content-Length и Content-Type — SDP, ISUP без CIC (SIP-I, Q.1912.5) и
multipart (RFC 2046); несколько сообщений в сегменте TCP. Сообщения собираются по примерам RFC 3665."""

import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import prilozh
from reportgen.setevoy.pole import Пакет
from reportgen.setevoy.protokoly import sip
from reportgen.setevoy.razbor import ДОП_УРОВНИ, Разбор, разобрать_пакет
from test_setevoy_oks7 import isup_iam

SDP = (b"v=0\r\no=alice 2890844526 2890844526 IN IP4 client.atlanta.example.com\r\ns=-\r\n"
       b"c=IN IP4 192.0.2.101\r\nt=0 0\r\nm=audio 49172 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n")


def сообщение(первая, заголовки, тело=b"", длина=None):
    строки = [первая] + заголовки
    if длина is not False:
        строки.append(b"Content-Length: %d" % (len(тело) if длина is None else длина))
    return b"\r\n".join(строки) + b"\r\n\r\n" + тело


INVITE = сообщение(
    b"INVITE sip:bob@biloxi.example.com SIP/2.0",
    [b"Via: SIP/2.0/UDP client.atlanta.example.com:5060;branch=z9hG4bK74bf9",
     b"Max-Forwards: 70",
     b'f: "Alice" <sip:alice@atlanta.example.com>;tag=9fxced76sl',
     b"t: Bob <sip:bob@biloxi.example.com>",
     b"i: 3848276298220188511@atlanta.example.com",
     b"CSeq: 1 INVITE",
     b"m: <sip:alice@client.atlanta.example.com;transport=udp>",
     b"User-Agent: Softphone",
     b"  Beta1.5",
     b"c: application/sdp"], SDP)


def по_udp(данные, порт=5060, **кв):
    return разобрать_пакет(с.eth(с.ip(с.udp(данные, порт, порт), 17)), **кв)


def по_tcp(данные, порт=5060):
    return разобрать_пакет(с.eth(с.ip(с.tcp(данные, порт, 40000), 6)))


def поля(п, номер=0):
    у = [x for x in п.уровни if x.протокол == "SIP"][номер]
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


class ТестЗапрос(unittest.TestCase):
    def test_invite(self):
        п = по_udp(INVITE)
        self.assertEqual(п.стек[3:], ["SIP", "SDP"])
        ф = поля(п)
        self.assertEqual((ф["sip.Method"].текст, ф["sip.r-uri"].текст), ("INVITE", "sip:bob@biloxi.example.com"))
        self.assertEqual((ф["sip.Method"].смещение, ф["sip.Method"].длина), (42, 6))
        self.assertEqual((ф["sip.r-uri"].смещение, ф["sip.r-uri"].длина), (49, 26))
        self.assertEqual((ф["sip.from.user"].текст, ф["sip.from.tag"].текст, ф["sip.from.display.info"].текст),
                         ("alice", "9fxced76sl", "Alice"))
        self.assertEqual((ф["sip.to.user"].текст, ф["sip.to.display.info"].текст), ("bob", "Bob"))
        self.assertNotIn("sip.to.tag", ф)
        self.assertEqual(ф["sip.Call-ID"].текст, "3848276298220188511@atlanta.example.com")
        self.assertEqual((ф["sip.CSeq.seq"].сырое, ф["sip.CSeq.method"].текст), (1, "INVITE"))
        self.assertEqual((ф["sip.Via.transport"].текст, ф["sip.Via.sent-by.address"].текст, ф["sip.Via.branch"].текст),
                         ("UDP", "client.atlanta.example.com:5060", "z9hG4bK74bf9"))
        self.assertEqual(ф["sip.contact.addr"].текст, "sip:alice@client.atlanta.example.com;transport=udp")
        # Продолжение строки (7.3.1) — одно значение; место — от начала заголовка.
        self.assertEqual(ф["sip.User-Agent"].текст, "Softphone Beta1.5")
        self.assertEqual(ф["sip.User-Agent"].смещение, 42 + INVITE.index(b"User-Agent"))
        # Краткие формы — полными именами.
        self.assertEqual(ф["sip.Content-Type"].текст, "application/sdp")
        self.assertEqual(ф["sip.Content-Length"].текст, str(len(SDP)))
        у = [x for x in п.уровни if x.протокол == "SIP"][0]
        self.assertEqual(у.длина, len(INVITE))
        self.assertEqual(п.инфо, "SIP INVITE sip:bob@biloxi.example.com (alice → bob) (с SDP)")
        self.assertEqual(п.ошибки, [])

    def test_bye_причина_и_неизвестный_метод(self):
        bye = сообщение(b"BYE sip:alice@client.atlanta.example.com SIP/2.0",
                        [b"From: <sip:bob@b>;tag=1", b"To: <sip:alice@a>;tag=2", b"Call-ID: x1", b"CSeq: 2 BYE",
                         b"Reason: Q.850 ;cause=16 ;text=\"Normal call clearing\""])
        п = по_udp(bye)
        ф = поля(п)
        self.assertEqual((ф["sip.reason_cause_q850"].сырое, ф["sip.reason_cause_q850"].текст),
                         (16, "16 (нормальное освобождение)"))
        self.assertEqual(п.инфо, "SIP BYE sip:alice@client.atlanta.example.com (bob → alice), "
                                 "причина 16 (нормальное освобождение)")
        self.assertEqual(ф["sip.to.tag"].текст, "2")
        п = по_udp(сообщение(b"FOO sip:x@y SIP/2.0", [b"Call-ID: 1"]))
        self.assertEqual(поля(п)["sip.unknown_method"].текст, "FOO")
        self.assertNotIn("sip.unknown_method", поля(по_udp(INVITE)))
        п = по_udp(сообщение(b"BYE tel:+74951234567 SIP/2.0", [b"From: tel:+79161234567;tag=a", b"To: <tel:+74951234567>",
                                                               b"Reason: SIP ;cause=200"]))
        self.assertEqual(п.инфо, "SIP BYE tel:+74951234567 (+79161234567 → +74951234567)")

    def test_register_авторизация(self):
        reg = сообщение(b"REGISTER sip:registrar.biloxi.example.com SIP/2.0",
                        [b"From: Bob <sip:bob@biloxi.example.com>;tag=ja743ks76zlflH", b"To: Bob <sip:bob@biloxi.example.com>",
                         b"Call-ID: 1j9FpLxk3uxtm8tn@biloxi.example.com", b"CSeq: 2 REGISTER",
                         b'Authorization: Digest username="bob", realm="atlanta.example.com", '
                         b'nonce="ea9c8e88df84f1cec4341ae6cbe5a359", opaque="", uri="sips:ss2.biloxi.example.com", '
                         b'response="dfe56131d1958046689d83306477ecc"'])
        п = по_udp(reg)
        ф = поля(п)
        self.assertEqual((ф["sip.auth.scheme"].текст, ф["sip.auth.username"].текст, ф["sip.auth.realm"].текст),
                         ("Digest", "bob", "atlanta.example.com"))
        self.assertEqual((ф["sip.auth.nonce"].текст, ф["sip.auth.uri"].текст),
                         ("ea9c8e88df84f1cec4341ae6cbe5a359", "sips:ss2.biloxi.example.com"))
        self.assertNotIn("sip.auth.response", ф)
        self.assertTrue(п.инфо.endswith(", пользователь bob"), п.инфо)


class ТестОтвет(unittest.TestCase):
    def test_коды(self):
        for код, текст, имя in ((200, b"OK", "OK"), (180, b"Ringing", "Ringing"), (486, b"Busy Here", "Busy Here"),
                                (698, b"Weird", "неизвестный")):
            with self.subTest(код):
                п = по_udp(сообщение(b"SIP/2.0 %d %s" % (код, текст), [b"CSeq: 1 INVITE", b"Call-ID: a"]))
                ф = поля(п)
                self.assertEqual((ф["sip.Status-Code"].сырое, ф["sip.Status-Code"].текст), (код, f"{код} ({имя})"))
                self.assertEqual((ф["sip.Status-Code"].смещение, ф["sip.Status-Code"].длина), (50, 3))
                self.assertEqual(п.инфо, f"SIP {код} {текст.decode()} (INVITE)")
        п = по_udp(сообщение(b"SIP/2.0 401 Unauthorized",
                             [b'WWW-Authenticate: Digest realm="atlanta.example.com", qop="auth", nonce="84a4cc6f"']))
        ф = поля(п)
        self.assertEqual((ф["sip.auth.realm"].текст, ф["sip.auth.qop"].текст, ф["sip.auth.nonce"].текст),
                         ("atlanta.example.com", "auth", "84a4cc6f"))
        self.assertEqual(п.инфо, "SIP 401 Unauthorized")


class ТестТело(unittest.TestCase):
    def test_sip_i_multipart(self):
        iam = isup_iam()[2:]                                       # без CIC (Q.1912.5)
        граница = b"unique-boundary-1"
        тело = (b"--" + граница + b"\r\nContent-Type: application/sdp\r\n\r\n" + SDP + b"\r\n--" + граница
                + b"\r\nContent-Type: application/ISUP; version=itu-t92+\r\nContent-Disposition: signal\r\n\r\n" + iam
                + b"\r\n--" + граница + b"--\r\n")
        п = по_udp(сообщение(b"INVITE sip:4951234567@gw SIP/2.0",
                             [b"From: <sip:74951112233@a>", b"To: <sip:4951234567@gw>",
                              b'Content-Type: multipart/mixed; boundary="' + граница + b'"'], тело))
        self.assertEqual(п.стек[3:], ["SIP", "SDP", "ISUP"])
        части = [x for x in [y for y in п.уровни if y.протокол == "SIP"][0].поля if x.ключ == "sip.multipart.part"]
        self.assertEqual([x.текст for x in части], ["application/sdp", "application/ISUP; version=itu-t92+"])
        isup_у = [x for x in п.уровни if x.протокол == "ISUP"][0]
        self.assertEqual(п.данные[isup_у.смещение:isup_у.смещение + isup_у.длина], iam)
        self.assertTrue(isup_у.итог.startswith("IAM, → 4951234567"), isup_у.итог)
        self.assertEqual(п.инфо, "SIP INVITE sip:4951234567@gw (74951112233 → 4951234567) (с SDP) (с ISUP)")

    def test_тело_не_разобрано(self):
        п = по_udp(сообщение(b"MESSAGE sip:bob@b SIP/2.0", [b"Content-Type: text/plain"], b"Hello!"))
        self.assertEqual(п.стек[3:], ["SIP"])
        self.assertEqual(поля(п)["sip.msg_body"].текст, "6 байт: Hello!")
        # SDP в заголовке, но тело не SDP — полем.
        п = по_udp(сообщение(b"INVITE sip:bob@b SIP/2.0", [b"Content-Type: application/sdp"], b"x=0\r\n"))
        self.assertEqual((п.стек[3:], поля(п)["sip.msg_body"].длина), (["SIP"], 5))
        # ISUP без CIC, но не ISUP.
        п = по_udp(сообщение(b"INFO sip:bob@b SIP/2.0", [b"Content-Type: application/isup"], b"\x01\x02"))
        self.assertEqual(п.стек[3:], ["SIP"])

    def test_content_length(self):
        # UDP: лишнее после тела отбрасывается (18.3), тело короче Content-Length — ошибка.
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [], b"abc", длина=1))
        ф = поля(п)
        self.assertEqual((ф["sip.msg_body"].длина, ф["sip.extra"].текст), (1, "2 байт"))
        self.assertEqual(ф["sip.extra"].имя, "После тела (отброшено, RFC 3261 18.3)")
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [], b"abc", длина=10))
        self.assertEqual(п.ошибки, ["SIP: тело короче Content-Length (3 из 10)"])
        # Без Content-Length по UDP — тело до конца.
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [], b"abc", длина=False))
        self.assertEqual(поля(п)["sip.msg_body"].длина, 3)

    def test_tcp_два_сообщения(self):
        первое = сообщение(b"SIP/2.0 100 Trying", [b"CSeq: 1 INVITE"])
        второе = сообщение(b"SIP/2.0 180 Ringing", [b"CSeq: 1 INVITE"])
        п = по_tcp(первое + второе)
        у = [x for x in п.уровни if x.протокол == "SIP"]
        self.assertEqual([(x.смещение - 54, x.длина) for x in у], [(0, len(первое)), (len(первое), len(второе))])
        self.assertEqual(п.инфо, "SIP 100 Trying (INVITE); 180 Ringing (INVITE)")
        п = по_tcp(первое + b"\x00\x01garbage")
        self.assertEqual(поля(п)["sip.extra"].имя, "После сообщения (не SIP)")
        # По UDP второе сообщение не ищется.
        п = по_udp(первое + второе)
        self.assertEqual(len([x for x in п.уровни if x.протокол == "SIP"]), 1)


class ТестГраницы(unittest.TestCase):
    def test_не_sip(self):
        for данные, что in ((b"HTTP/1.1 200 OK\r\n\r\n", "HTTP"), (b"INVITE sip:a SIP/2.1\r\n\r\n", "версия"),
                            (b"SIP/2.0 99 X\r\n\r\n", "код из двух цифр"), (b"SIP/2.0 700 X\r\n\r\n", "код 700"),
                            (b"INVITE sip:a SIP/2.0\r\nVia SIP\r\n\r\n", "без двоеточия"),
                            (b"INVITE sip:a SIP/2.0\r\nMax Forwards: 1\r\n\r\n", "пробел в имени"),
                            (b"INVITE sip:a SIP/2.0\r\nVia: x\r\n", "нет пустой строки"),
                            (b"INVITE sip:a SIP/2.0", "одна строка"), (b"INVITE sip:a SIP/2.0\r\n: x\r\n\r\n", "пустое имя"),
                            (b"1NVITE sip:a SIP/2.0\r\n\r\n", "метод с цифры")):
            with self.subTest(что):
                self.assertNotIn("SIP", по_udp(данные).стек)

    def test_только_lf_и_без_заголовков(self):
        п = по_udp(b"OPTIONS sip:b SIP/2.0\nCall-ID: z\n\n")
        self.assertEqual((поля(п)["sip.Call-ID"].текст, п.инфо), ("z", "SIP OPTIONS sip:b"))
        п = по_udp(b"ACK sip:b SIP/2.0\r\n\r\n")
        self.assertEqual(п.инфо, "SIP ACK sip:b")

    def test_вызовы_захвата(self):
        шаблоны = {}
        по_udp(INVITE, номер=3, шаблоны=шаблоны)
        по_udp(сообщение(b"SIP/2.0 200 OK", [b"i: 3848276298220188511@atlanta.example.com", b"From: <sip:x@y>"]),
               номер=4, шаблоны=шаблоны)
        self.assertEqual(шаблоны[sip.ВЫЗОВЫ], {"3848276298220188511@atlanta.example.com": [3, "alice", "bob"]})

    def test_помощники(self):
        self.assertEqual(sip._адрес('"A B" <sip:a@b;x=1>;tag=7'), ("A B", "sip:a@b;x=1", {"tag": "7"}))
        self.assertEqual(sip._адрес("sip:a@b;tag=8"), ("", "sip:a@b", {"tag": "8"}))
        self.assertEqual(sip._адрес("<sip:a@b"), ("", "<sip:a@b", {}))
        self.assertEqual(sip._пользователь("sips:u@h"), "u")
        self.assertEqual(sip._пользователь("urn:service:sos"), "urn:service:sos")

    def test_разметка_частей_и_лишнего(self):
        граница = b"b1"
        тело = (b"--b1\r\nContent-Type: text/plain\r\n\r\nAAA\r\n--b1\r\nContent-Type: text/x\r\n\r\nBB"
                b"\r\n--b1--\r\n")
        данные = сообщение(b"MESSAGE sip:b SIP/2.0", [b"Content-Type: multipart/mixed;boundary=" + граница], тело)
        п = по_udp(данные + b"XYZW")
        у = [y for y in п.уровни if y.протокол == "SIP"][0]
        начало_тела = у.смещение + len(данные) - len(тело)
        части = [(x.смещение - начало_тела, x.длина) for x in у.поля if x.ключ == "sip.multipart.part"]
        второй = тело.index(b"--b1\r\nContent-Type: text/x")
        self.assertEqual(части, [(0, второй), (второй, тело.index(b"--b1--") - второй)])
        лишнее = поля(п)["sip.extra"]
        self.assertEqual((лишнее.смещение - у.смещение, лишнее.длина, лишнее.текст), (len(данные), 4, "4 байт"))
        # Ровно до конца — без лишнего; Content-Length 0 — без поля тела.
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [b"Max-Forwards: 2"], b""))
        self.assertNotIn("sip.extra", поля(п))
        self.assertNotIn("sip.msg_body", поля(п))
        # Число в другом заголовке — не Content-Length.
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [b"Max-Forwards: 2"], b"abcdef", длина=False))
        self.assertEqual((поля(п)["sip.msg_body"].длина, "sip.extra" in поля(п)), (6, False))

    def test_заголовки_на_краях(self):
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [b"A: 1", b"B: 2", b"C: 3", b"  4", b"D: 5"]))
        self.assertEqual([поля(п)[к].текст for к in ("sip.A", "sip.B", "sip.C", "sip.D")], ["1", "2", "3 4", "5"])
        # Отображаемое имя с запятой — не список (запятая делит только Contact).
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [b'From: "Doe, John" <sip:j@x>;tag=1',
                                                       b"Contact: <sip:a@b>, <sip:c@d>"]))
        ф = поля(п)
        self.assertEqual((ф["sip.from.display.info"].текст, ф["sip.from.addr"].текст, ф["sip.contact.addr"].текст),
                         ("Doe, John", "sip:j@x", "sip:a@b"))
        # CSeq не из двух частей с числом — без разбора.
        for cseq in (b"CSeq: x INVITE", b"CSeq: 1 INVITE extra", b"CSeq: 1"):
            self.assertNotIn("sip.CSeq.seq", поля(по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [cseq]))), cseq)
        # Via: пробел перед «;» допустим (SEMI = SWS ";" SWS); не SIP/2.0 — без разбора.
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [b"Via: SIP/2.0/TCP 10.0.0.1:5060 ;branch=z9hG4bK1"]))
        ф = поля(п)
        self.assertEqual((ф["sip.Via.transport"].текст, ф["sip.Via.sent-by.address"].текст, ф["sip.Via.branch"].текст),
                         ("TCP", "10.0.0.1:5060", "z9hG4bK1"))
        self.assertNotIn("sip.Via.transport", поля(по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [b"Via: FOO bar"]))))
        # Схема авторизации — до пробела или табуляции; пустое значение — пустая схема.
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [b'Authorization: Digest\tusername="u" x']))
        self.assertEqual((поля(п)["sip.auth.scheme"].текст, поля(п)["sip.auth.username"].текст), ("Digest", "u"))
        п = по_udp(сообщение(b"OPTIONS sip:b SIP/2.0", [b"Authorization:"]))
        self.assertEqual(поля(п)["sip.auth.scheme"].текст, "")
        # Неизвестный метод: поле — ровно на методе.
        ф = поля(по_udp(сообщение(b"FOO sip:bob@biloxi SIP/2.0", [])))["sip.unknown_method"]
        self.assertEqual(ф.длина, 3)

    def test_строки_и_сообщение_прямо(self):
        self.assertEqual(sip._строки(b"\nX", 0, 2), ([], 1))
        self.assertEqual(sip._строки(b"A: 1\r\n", 0, 6), (None, None))
        р = Разбор(Пакет(1, 0.0, b"xyz", 3, "RAW"), None, {})
        self.assertFalse(sip.sip(р, 0, 3))
        self.assertEqual(р.п.уровни, [])

    def test_регистрация(self):
        self.assertIs(prilozh.ПОРТЫ_UDP[5060], sip.sip)
        self.assertIs(prilozh.ПОРТЫ_TCP[5060], sip.sip_tcp)
        self.assertIs(prilozh.КАК["udp"]["SIP"], sip.sip)
        self.assertEqual(ДОП_УРОВНИ["SIP"], "прикладной")
        self.assertEqual((len(sip.МЕТОДЫ), len(sip.ЗАГОЛОВКИ), len(sip.СОКРАЩЕНИЯ), len(sip.КОДЫ)), (17, 127, 20, 75))
        п = по_udp(INVITE, 5070, как={"udp:5070": "SIP"})
        self.assertEqual(п.стек[3:], ["SIP", "SDP"])


if __name__ == "__main__":
    unittest.main()
