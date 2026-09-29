"""Синтез захватов для тестов объектов: обмены TCP (с номерами последовательности), UDP, RTP, TFTP.

Пакеты собираются setevoy_sintez (поля по RFC, суммы верные); разбор — тем же разборщиком, что у
страницы «Пакеты», сводки — как в хранилище захватов (zahvaty._сводка), нагрузки TCP/UDP — как
Захваты.нагрузки_тр (по «тр», иначе по «нагр»).
"""

import struct

import setevoy_sintez as с
from reportgen.setevoy import zahvaty
from reportgen.setevoy.razbor import разобрать_пакет


class Обмен:
    """Соединение TCP клиент ↔ сервер: куски в порядке передачи; номера последовательности растут сами."""

    def __init__(self, кл="10.0.0.1", сер="10.0.0.2", порт_к=40000, порт_с=80, seq_к=1000, seq_с=5000):
        self.кл, self.сер, self.порт_к, self.порт_с = кл, сер, порт_к, порт_с
        self.seq = {"к": seq_к, "с": seq_с}
        self.пакеты = []

    def кусок(self, кто, данные, seq=None, повтор=False):
        """Пакет со стороны «к» (клиент) или «с» (сервер); ``seq`` — явный номер, ``повтор`` — не двигать номер."""
        номер = self.seq[кто] if seq is None else seq
        if кто == "к":
            кадр = с.eth(с.ip(с.tcp(данные, self.порт_к, self.порт_с, seq=номер, src=self.кл, dst=self.сер), 6,
                              src=self.кл, dst=self.сер))
        else:
            кадр = с.eth(с.ip(с.tcp(данные, self.порт_с, self.порт_к, seq=номер, src=self.сер, dst=self.кл), 6,
                              src=self.сер, dst=self.кл))
        if seq is None and not повтор:
            self.seq[кто] = (номер + len(данные)) & 0xFFFFFFFF
        self.пакеты.append(кадр)
        return кадр

    def куски(self, кто, данные, размер=700):
        for i in range(0, len(данные), размер):
            self.кусок(кто, данные[i:i + размер])


def udp(данные, sport, dport, src="10.0.0.1", dst="10.0.0.2"):
    return с.eth(с.ip(с.udp(данные, sport, dport, src=src, dst=dst), 17, src=src, dst=dst))


def rtp(тип, номер, отметка, ssrc, нагрузка, маркер=0, csrc=(), расширение=b"", добивка=0):
    """RTP (RFC 3550, 5.1): V=2, P, X, CC, M, PT, номер, отметка, SSRC [, CSRC] [, расширение] нагрузка [добивка]."""
    б0 = 0x80 | (0x20 if добивка else 0) | (0x10 if расширение else 0) | len(csrc)
    заголовок = struct.pack(">BBHII", б0, (маркер << 7) | тип, номер & 0xFFFF, отметка & 0xFFFFFFFF, ssrc)
    заголовок += b"".join(struct.pack(">I", x) for x in csrc)
    if расширение:
        заголовок += struct.pack(">HH", 0xBEDE, len(расширение) // 4) + расширение
    хвост = bytes(добивка - 1) + bytes([добивка]) if добивка else b""
    return заголовок + нагрузка + хвост


def письмо_с_вложением(png):
    """Письмо MIME: текст с точкой в начале строки и вложение PNG в base64 с именем по RFC 2047."""
    import base64
    return ("From: a@b\r\nTo: c@d\r\nSubject: =?UTF-8?B?0J7RgtGH0ZHRgg==?=\r\nMIME-Version: 1.0\r\n"
            "Content-Type: multipart/mixed; boundary=XX\r\n\r\n--XX\r\nContent-Type: text/plain\r\n\r\n"
            ".строка с точкой\r\nтекст\r\n--XX\r\nContent-Type: image/png\r\nContent-Disposition: attachment; "
            "filename=\"=?UTF-8?B?0LrQsNGA0YLQuNC90LrQsC5wbmc=?=\"\r\nContent-Transfer-Encoding: base64\r\n\r\n"
            + base64.encodebytes(png).decode() + "--XX--\r\n").encode()


def захват_всех_видов():
    """Кадры захвата, где есть все виды объектов: HTTP (длина, chunked+gzip, multipart, JSON), WebSocket, FTP,
    TFTP, SMTP, RTP (PCMU, H.264 по SDP, AMR с DTMF), JSON в UDP, JPEG в своём протоколе."""
    import gzip
    import json

    import obrazcy_fajlov as обр
    кадры = []
    о = Обмен(порт_к=41000, порт_с=80)
    о.кусок("к", b"GET /files/%D0%BE%D1%82%D1%87%D1%91%D1%82.docx HTTP/1.1\r\nHost: x\r\n\r\n")
    документ = обр.docx()
    о.куски("с", b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: %d\r\n\r\n"
            % len(документ) + документ)
    о.кусок("к", b"GET /api/data HTTP/1.1\r\nHost: x\r\n\r\n")
    js = json.dumps({"список": [1, 2, 3], "имя": "значение"}, ensure_ascii=False).encode()
    сжато = gzip.compress(js)
    куски = b"".join(b"%x\r\n" % len(сжато[i:i + 16]) + сжато[i:i + 16] + b"\r\n" for i in range(0, len(сжато), 16))
    о.куски("с", b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n"
            b"Content-Encoding: gzip\r\n\r\n" + куски + b"0\r\n\r\n", 60)
    форма = ("--BOUND\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\nпривет\r\n--BOUND\r\n"
             "Content-Disposition: form-data; name=\"file\"; filename=\"photo.png\"\r\nContent-Type: image/png\r\n\r\n"
             .encode() + обр.png() + b"\r\n--BOUND--\r\n")
    о.кусок("к", b"POST /upload HTTP/1.1\r\nHost: x\r\nContent-Type: multipart/form-data; boundary=BOUND\r\n"
            b"Content-Length: %d\r\n\r\n" % len(форма) + форма)
    о.кусок("с", b"HTTP/1.1 204 No Content\r\n\r\n")
    кадры += о.пакеты
    # WebSocket: текст JSON от клиента (с маской) и двоичное сообщение сервера
    w = Обмен(порт_к=41001, порт_с=8080)
    w.кусок("к", b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
    w.кусок("с", b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
    сообщение, ключ = json.dumps({"op": "hello", "n": 1}).encode(), b"\x11\x22\x33\x44"
    w.кусок("к", bytes([0x81, 0x80 | len(сообщение)]) + ключ + bytes(б ^ ключ[i % 4] for i, б in enumerate(сообщение)))
    w.кусок("с", bytes([0x82, 4]) + b"\xde\xad\xbe\xef")
    кадры += w.пакеты
    # FTP: пассивный RETR архива ZIP
    у = Обмен(порт_к=41002, порт_с=21)
    for кто, строка in (("с", b"220 ready\r\n"), ("к", b"USER a\r\n"), ("с", b"331 ok\r\n"), ("к", b"PASV\r\n"),
                        ("с", b"227 Entering Passive Mode (10,0,0,2,195,80).\r\n"),
                        ("к", "RETR /pub/архив.zip\r\n".encode()), ("с", b"150 Opening\r\n")):
        у.кусок(кто, строка)
    кадры += у.пакеты
    д = Обмен(порт_к=41003, порт_с=195 * 256 + 80)
    д.куски("с", обр.zip_([("a.txt", "привет".encode() * 50), ("папка/b.bin", bytes(range(256)))]), 300)
    кадры += д.пакеты
    # TFTP: чтение файла из трёх блоков
    кадры.append(udp("\x00\x01boot/конфиг.cfg\x00octet\x00".encode(), 3000, 69))
    файл = bytes(range(256)) * 4 + b"end"
    for i in range(0, len(файл), 512):
        кадры.append(udp(b"\x00\x03" + (i // 512 + 1).to_bytes(2, "big") + файл[i:i + 512], 4000, 3000,
                         src="10.0.0.2", dst="10.0.0.1"))
        кадры.append(udp(b"\x00\x04" + (i // 512 + 1).to_bytes(2, "big"), 3000, 4000))
    # SMTP: письмо с вложением
    м = Обмен(порт_к=41004, порт_с=25)
    письмо = письмо_с_вложением(обр.png())
    for кто, строка in (("с", b"220 mail\r\n"), ("к", b"EHLO x\r\n"), ("с", b"250 ok\r\n"), ("к", b"DATA\r\n"),
                        ("с", b"354 go\r\n")):
        м.кусок(кто, строка)
    м.куски("к", письмо.replace(b"\r\n.", b"\r\n..") + b"\r\n.\r\n", 300)
    кадры += м.пакеты
    # RTP: PCMU (тип 0), 50 пакетов по 20 мс — тон
    import math
    отсчёты = bytes((0x7F if math.sin(2 * math.pi * 440 * i / 8000) >= 0 else 0xFF) - int(
        abs(math.sin(2 * math.pi * 440 * i / 8000)) * 60) for i in range(8000))
    for i in range(50):
        кадры.append(udp(rtp(0, 100 + i, 160 * i, 0x1234, отсчёты[160 * i:160 * i + 160]), 20000, 30000))
    # SIP с SDP: H.264 (96) и AMR (97) с событиями (101)
    sdp = ("v=0\r\no=- 1 1 IN IP4 10.0.0.1\r\ns=-\r\nc=IN IP4 10.0.0.2\r\nt=0 0\r\n"
           "m=video 30002 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
           "m=audio 30004 RTP/AVP 97 101\r\na=rtpmap:97 AMR/8000\r\na=rtpmap:101 telephone-event/8000\r\n").encode()
    кадры.append(udp(b"INVITE sip:b@10.0.0.2 SIP/2.0\r\nVia: SIP/2.0/UDP 10.0.0.1\r\nCall-ID: 1\r\nCSeq: 1 INVITE\r\n"
                     b"Content-Type: application/sdp\r\nContent-Length: %d\r\n\r\n" % len(sdp) + sdp, 5060, 5060))
    sps, pps, idr = b"\x67\x42\x00\x1e\xab", b"\x68\xce\x3c\x80", b"\x65" + bytes(range(256)) * 8
    nal = [b"\x18" + len(sps).to_bytes(2, "big") + sps + len(pps).to_bytes(2, "big") + pps]
    for i in range(1, len(idr), 700):
        s, e = int(i == 1), int(i + 700 >= len(idr))
        nal.append(bytes([0x60 | 28, (s << 7) | (e << 6) | 5]) + idr[i:i + 700])
    for i, п in enumerate(nal):
        кадры.append(udp(rtp(96, 500 + i, 3000, 0xABCD, п), 40000, 30002))
    for i in range(3):
        кадры.append(udp(rtp(97, 10 + i, 160 * i, 0x55, bytes([0xF0, 7 << 3 | 1 << 2]) + bytes([i + 1] * 31)),
                         40002, 30004))
    for i, (цифра, отметка) in enumerate(((5, 480), (5, 480), (9, 960))):
        кадры.append(udp(rtp(101, 13 + i, отметка, 0x55, bytes([цифра, 0x80, 0, 160])), 40002, 30004))
    # JSON в UDP и JPEG в своём протоколе на TCP
    кадры.append(udp(json.dumps({"датчик": 7, "t": 21.5}, ensure_ascii=False).encode(), 7000, 7001))
    р = Обмен(порт_к=41005, порт_с=9999)
    р.куски("с", b"HDR1" + обр.jpeg() + b"TAIL", 100)
    кадры += р.пакеты
    return кадры


def разобрать(кадры, времена=None):
    """(сводки, нагрузки TCP/UDP целиком, SDP-потоки) — как у хранилища захватов."""
    шаблоны = {}
    сводки, нагрузки = [], []
    for i, кадр in enumerate(кадры, start=1):
        п = разобрать_пакет(кадр, "Ethernet", номер=i, время=(времена[i - 1] if времена else 1000.0 + i * 0.01),
                            шаблоны=шаблоны)
        сводка = zahvaty._сводка(п, п.поля_фильтра())
        сводки.append(сводка)
        место = сводка.get("тр") or сводка.get("нагр")
        нагрузки.append(кадр[место[0]:место[0] + место[1]] if место else None)
    return сводки, нагрузки, шаблоны.get("sdp-потоки", {})
