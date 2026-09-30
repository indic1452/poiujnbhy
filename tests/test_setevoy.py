"""Анализатор пакетов: чтение захватов и разборщики — на пакетах, собранных по RFC."""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import прочитать_захват, разобрать_пакет
from reportgen.setevoy.razbor import _crc32c


def поля(пакет):
    return пакет.поля_фильтра()


class ЧтениеTests(unittest.TestCase):
    ПАКЕТЫ = [с.eth(с.ip(с.udp(с.dns_запрос("example.com"), 5000, 53), 17)) for _ in range(3)]

    def test_pcap_и_pcapng(self):
        для_pcap = прочитать_захват(данные=с.pcap(self.ПАКЕТЫ, времена=[1.5, 2.25, 3.0]))
        self.assertEqual(("pcap", 3, "Ethernet"), (для_pcap.формат, len(для_pcap.записи),
                                                  для_pcap.записи[0].канал))
        self.assertEqual([1.5, 2.25, 3.0], [round(з.время, 6) for з in для_pcap.записи])
        для_ng = прочитать_захват(данные=с.pcapng(self.ПАКЕТЫ, tsresol=9, времена=[10.0, 10.5, 11.25]))
        self.assertEqual(("pcapng", 3), (для_ng.формат, len(для_ng.записи)))
        self.assertEqual([10.0, 10.5, 11.25], [round(з.время, 9) for з in для_ng.записи])
        self.assertEqual(self.ПАКЕТЫ, [з.данные for з in для_ng.записи])
        # Разрешение времени степенью двойки (старший бит if_tsresol).
        для_2 = прочитать_захват(данные=с.pcapng(self.ПАКЕТЫ, tsresol=0x80 | 10, времена=[1.0, 2.0, 3.0]))
        self.assertEqual([1.0, 2.0, 3.0], [round(з.время, 3) for з in для_2.записи])

    def test_оборванный_файл_и_не_захват(self):
        файл = с.pcap(self.ПАКЕТЫ)
        оборван = прочитать_захват(данные=файл[:-10])
        self.assertEqual(2, len(оборван.записи))
        self.assertIn("файл оборван", оборван.заметки[0])
        with self.assertRaises(ValueError):
            прочитать_захват(данные="просто текст, не захват".encode() * 10)


class КанальныйTests(unittest.TestCase):
    def test_vlan_mpls_arp(self):
        вложенный = с.ip(с.icmp(8, 0, struct.pack(">HH", 7, 1) + b"ping"), 1)
        vlan = с.eth(struct.pack(">HH", (5 << 13) | 100, 0x0800) + вложенный, тип=0x8100)
        п = разобрать_пакет(vlan)
        self.assertEqual(["Ethernet", "VLAN", "IPv4", "ICMP"], п.стек)
        self.assertEqual(([100], [5]), (поля(п)["vlan.id"], поля(п)["vlan.priority"]))
        mpls = с.eth(struct.pack(">II", (1000 << 12) | 64, (2000 << 12) | 0x100 | 63) + вложенный, тип=0x8847)
        п = разобрать_пакет(mpls)
        self.assertEqual(["Ethernet", "MPLS", "IPv4", "ICMP"], п.стек)
        self.assertEqual([1000, 2000], поля(п)["mpls.label"])
        arp = с.eth(struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, bytes.fromhex("66778899aabb"),
                                с.a4("10.0.0.1"), b"\0" * 6, с.a4("10.0.0.9")), тип=0x0806)
        п = разобрать_пакет(arp)
        self.assertEqual("Кто 10.0.0.9? Сообщите 10.0.0.1", п.инфо)

    def test_802_3_llc_stp(self):
        bpdu = struct.pack(">HBBB", 0, 0, 0, 0) + struct.pack(">H6s", 32768, bytes(6)) + struct.pack(">I", 4) \
            + struct.pack(">H6s", 32769, bytes.fromhex("0011223344ff")) + struct.pack(">HHHHH", 0x8001, 0, 20 * 256,
                                                                                        2 * 256, 15 * 256)
        кадр = bytes.fromhex("0180c2000000" "66778899aabb") + struct.pack(">H", 3 + len(bpdu)) + b"\x42\x42\x03" + bpdu
        п = разобрать_пакет(кадр)
        self.assertEqual(["Ethernet", "LLC", "STP"], п.стек)
        self.assertEqual([4], поля(п)["stp.root.cost"])

    def test_авто_для_кадров_без_типа(self):
        голый = с.ip(с.udp(b"x" * 10, 1000, 2000), 17)
        self.assertEqual(["IPv4", "UDP", "Данные"], разобрать_пакет(голый, "авто").стек)
        self.assertEqual(["PPP", "IPv4", "UDP", "Данные"],
                         разобрать_пакет(b"\xff\x03\x00\x21" + голый, "авто").стек)
        self.assertEqual(["Данные"], разобрать_пакет(b"\x01\x02\x03\x04" * 5, "авто").стек)


class СетевойTests(unittest.TestCase):
    def test_ipv4_сумма_и_порча(self):
        пакет = с.eth(с.ip(с.udp(b"hello", 1111, 2222), 17))
        п = разобрать_пакет(пакет)
        self.assertEqual([], п.ошибки)
        self.assertEqual(["10.0.0.1", "10.0.0.2"], поля(п)["ip.addr"])
        испорчен = bytearray(пакет)
        испорчен[14 + 8] ^= 1                                 # TTL — сумма заголовка больше не сходится
        п = разобрать_пакет(bytes(испорчен))
        self.assertIn("IPv4: неверная контрольная сумма заголовка", п.ошибки)

    def test_icmp_недостижим_с_исходным_пакетом(self):
        исходный = с.ip(с.udp(b"data", 5555, 53), 17, src="10.0.0.2", dst="8.8.8.8")
        тело = b"\0\0\0\0" + исходный[:28]
        п = разобрать_пакет(с.eth(с.ip(с.icmp(3, 3, тело), 1, src="8.8.8.8", dst="10.0.0.2")))
        self.assertEqual(["Ethernet", "IPv4", "ICMP", "исходный IPv4", "исходный UDP"], п.стек)
        self.assertEqual("ICMP получатель недостижим (порт)", п.инфо)
        self.assertEqual("ICMP", п.протокол)

    def test_ipv6_расширения_и_icmpv6(self):
        цель = с.a6("2001:db8::99")
        сообщение = struct.pack(">BBHI", 135, 0, 0, 0) + цель
        s = с.сумма(с._псевдо("2001:db8::1", "2001:db8::2", 58, len(сообщение), v6=True) + сообщение)
        сообщение = сообщение[:2] + struct.pack(">H", s) + сообщение[4:]
        переходы = struct.pack(">BB6x", 58, 0)               # заголовок «переходы», 8 байт
        п = разобрать_пакет(с.eth(с.ip6(переходы + сообщение, 0), тип=0x86DD))
        self.assertEqual(["Ethernet", "IPv6", "ICMPv6"], п.стек)
        self.assertEqual([], п.ошибки)
        self.assertIn("цель 2001:db8::99", п.инфо)

    def test_gre_и_vxlan_внутри(self):
        внутренний = с.eth(с.ip(с.udp(b"inner", 1, 2, src="192.168.0.1", dst="192.168.0.2"), 17,
                                src="192.168.0.1", dst="192.168.0.2"))
        gre = struct.pack(">HH", 0x2000, 0x6558) + struct.pack(">I", 42) + внутренний
        п = разобрать_пакет(с.eth(с.ip(gre, 47)))
        self.assertEqual(["Ethernet", "IPv4", "GRE", "Ethernet", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual([42], поля(п)["gre.key"])
        vx = struct.pack(">B3xI", 0x08, 5001 << 8) + внутренний
        п = разобрать_пакет(с.eth(с.ip(с.udp(vx, 40000, 4789), 17)))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "VXLAN", "Ethernet", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual([5001], поля(п)["vxlan.vni"])
        # Адреса пакета — самые внутренние: так их ждёт аналитик в списке.
        self.assertEqual(("192.168.0.1", "192.168.0.2"), (п.источник, п.получатель))

    def test_sctp_crc32c(self):
        self.assertEqual(0xE3069283, _crc32c(b"123456789"))    # проверочное значение CRC-32C
        кусок = struct.pack(">BBHIHHI", 0, 3, 16 + 5, 1, 0, 0, 46) + b"hello" + b"\0\0\0"
        заголовок = struct.pack(">HHII", 2905, 2905, 0xdeadbeef, 0)
        crc = _crc32c(заголовок + кусок)
        sctp = заголовок[:8] + struct.pack("<I", crc) + кусок
        п = разобрать_пакет(с.eth(с.ip(sctp, 132)))
        self.assertEqual([], п.ошибки)
        self.assertIn("SCTP 2905 → 2905: DATA", п.инфо)


class ТранспортныйTests(unittest.TestCase):
    def test_tcp_флаги_опции_сумма(self):
        опции = struct.pack(">BBH", 2, 4, 1460) + b"\x01" + struct.pack(">BBB", 3, 3, 7) \
            + struct.pack(">BB", 4, 2) + struct.pack(">BBII", 8, 10, 111, 0) + b"\x00\x00"
        п = разобрать_пакет(с.eth(с.ip(с.tcp(b"", 51000, 443, seq=100, флаги=0x02, опции=опции), 6)))
        self.assertEqual([], п.ошибки)
        self.assertEqual(([1460], [7], [1], [0]), (поля(п)["tcp.options.mss_val"], поля(п)["tcp.options.wscale.shift"],
                                                   поля(п)["tcp.flags.syn"], поля(п)["tcp.flags.ack"]))
        self.assertIn("[SYN]", п.инфо)
        испорчен = bytearray(с.eth(с.ip(с.tcp(b"abc", 1, 2), 6)))
        испорчен[-1] ^= 0xFF
        self.assertTrue(any("TCP" in о for о in разобрать_пакет(bytes(испорчен)).ошибки))


class ПрикладнойTests(unittest.TestCase):
    def test_dns_запрос_и_ответ_со_сжатием(self):
        п = разобрать_пакет(с.eth(с.ip(с.udp(с.dns_запрос("www.example.com", 28), 5000, 53), 17)))
        self.assertEqual("DNS", п.протокол)
        self.assertEqual("DNS Запрос 0xbeef AAAA www.example.com", п.инфо)
        ответ = с.dns_ответ("www.example.com", ["93.184.216.34", "93.184.216.35"])
        п = разобрать_пакет(с.eth(с.ip(с.udp(ответ, 53, 5000), 17)))
        self.assertEqual(["93.184.216.34", "93.184.216.35"], поля(п)["dns.a"])
        self.assertEqual(["www.example.com", "www.example.com"], поля(п)["dns.resp.name"])
        self.assertIn("→ A 93.184.216.34, A 93.184.216.35", п.инфо)
        # DNS по TCP — с двухбайтовой длиной впереди.
        запрос = с.dns_запрос("example.org")
        п = разобрать_пакет(с.eth(с.ip(с.tcp(struct.pack(">H", len(запрос)) + запрос, 5001, 53), 6)))
        self.assertEqual(["example.org"], поля(п)["dns.qry.name"])

    def test_dns_петля_указателей_не_вешает(self):
        плохой = struct.pack(">HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\xc0\x0c" + b"\0\1\0\1"
        п = разобрать_пакет(с.eth(с.ip(с.udp(плохой, 5000, 53), 17)))
        self.assertNotEqual("DNS", п.протокол)
        # Петля через метку: имя «a», затем указатель на само имя — тоже отвергается.
        петля = struct.pack(">HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\x01a\xc0\x0c" + b"\0\1\0\1"
        self.assertNotEqual("DNS", разобрать_пакет(с.eth(с.ip(с.udp(петля, 5000, 53), 17))).протокол)
        # Петля из двух указателей внутри прежней записи: 17 → 13 → 15 → 13 …
        # Каждый указатель по отдельности ведёт назад от второго имени — ловит
        # только граница, убывающая с каждым переходом. Разбор — с пределом времени.
        import threading
        двойная = struct.pack(">HHHHHH", 1, 0x0100, 2, 0, 0, 0) + b"\x00\xc0\x0f\xc0\x0d" + b"\xc0\x0d" + b"\0\1\0\1"
        итог = []
        нить = threading.Thread(target=lambda: итог.append(
            разобрать_пакет(с.eth(с.ip(с.udp(двойная, 5000, 53), 17))).протокол), daemon=True)
        нить.start()
        нить.join(10)
        self.assertEqual(["UDP"], итог, "петля указателей DNS не остановлена")
        # Указатель вперёд — не на прежнее вхождение (RFC 1035, 4.1.4).
        вперёд = struct.pack(">HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\xc0\x12" + b"\0\1\0\1" + b"\x01b\x00"
        self.assertNotEqual("DNS", разобрать_пакет(с.eth(с.ip(с.udp(вперёд, 5000, 53), 17))).протокол)

    def test_dhcp(self):
        тело = struct.pack(">BBBBIHH4s4s4s4s", 1, 1, 6, 0, 0x3903F326, 0, 0x8000, bytes(4), bytes(4),
                           bytes(4), bytes(4)) + bytes.fromhex("0011223344ff") + bytes(10) + bytes(192) \
            + b"\x63\x82\x53\x63" + b"\x35\x01\x01" + b"\x0c\x04test" + b"\x37\x03\x01\x03\x06" + b"\xff"
        п = разобрать_пакет(с.eth(с.ip(с.udp(тело, 68, 67, src="0.0.0.0", dst="255.255.255.255"), 17,
                                       src="0.0.0.0", dst="255.255.255.255")))
        self.assertEqual("DHCP", п.протокол)
        self.assertEqual("DHCP DISCOVER, xid 0x3903f326, клиент 00:11:22:33:44:ff", п.инфо)
        self.assertEqual(["test"], поля(п)["dhcp.option.12"])
        # Без «магического числа» 63 82 53 63 опции DHCP не читаются.
        без = тело.replace(b"\x63\x82\x53\x63", bytes(4))
        п = разобрать_пакет(с.eth(с.ip(с.udp(без, 68, 67), 17)))
        self.assertNotIn("DHCP", п.стек)

    def test_ntp(self):
        тело = bytes([0x23, 0, 6, 0xEC]) + bytes(8) + b"\0\0\0\0" + bytes(24) + struct.pack(">II", 3913056000, 0)
        п = разобрать_пакет(с.eth(с.ip(с.udp(тело, 123, 123), 17)))
        self.assertEqual("NTP клиент, версия 4, стратум 0", п.инфо)
        передача = next(ф for ф in п.уровни[-1].поля if ф.ключ == "ntp.xmt")
        self.assertEqual("2024-01-01 00:00:00.000000 UTC", передача.текст)

    def test_snmp_get(self):
        def tlv(тег, значение):
            return bytes([тег, len(значение)]) + значение
        oid = tlv(0x06, bytes([0x2B, 6, 1, 2, 1, 1, 5, 0]))              # 1.3.6.1.2.1.1.5.0
        привязка = tlv(0x30, oid + tlv(0x05, b""))
        pdu = tlv(0xA0, tlv(0x02, b"\x01") + tlv(0x02, b"\x00") + tlv(0x02, b"\x00") + tlv(0x30, привязка))
        сообщение = tlv(0x30, tlv(0x02, b"\x01") + tlv(0x04, b"public") + pdu)
        п = разобрать_пакет(с.eth(с.ip(с.udp(сообщение, 40000, 161), 17)))
        self.assertEqual("SNMP get-request, сообщество «public», 1.3.6.1.2.1.1.5.0", п.инфо)

    def test_http_запрос_и_ответ(self):
        запрос = b"GET /index.html HTTP/1.1\r\nHost: example.com\r\nUser-Agent: test\r\n\r\n"
        п = разобрать_пакет(с.eth(с.ip(с.tcp(запрос, 50000, 8080), 6)))
        self.assertEqual("HTTP GET /index.html HTTP/1.1 (узел example.com)", п.инфо)
        self.assertEqual(["example.com"], поля(п)["http.host"])
        ответ = b"HTTP/1.1 404 Not Found\r\nContent-Length: 5\r\n\r\nnope!"
        п = разобрать_пакет(с.eth(с.ip(с.tcp(ответ, 8080, 50000), 6)))
        self.assertEqual([404], поля(п)["http.response.code"])

    def test_tls_client_hello_sni_alpn(self):
        sni = b"secure.example.net"
        ext_sni = struct.pack(">HHHBH", 0, len(sni) + 5, len(sni) + 3, 0, len(sni)) + sni
        alpn_список = b"\x02h2\x08http/1.1"
        ext_alpn = struct.pack(">HHH", 16, len(alpn_список) + 2, len(alpn_список)) + alpn_список
        ext_ver = struct.pack(">HHB", 43, 5, 4) + b"\x03\x04\x03\x03"
        расширения = ext_sni + ext_alpn + ext_ver
        hello = struct.pack(">H", 0x0303) + bytes(32) + b"\x00" + struct.pack(">H", 4) + b"\x13\x01\x13\x02" \
            + b"\x01\x00" + struct.pack(">H", len(расширения)) + расширения
        рукопожатие = b"\x01" + len(hello).to_bytes(3, "big") + hello
        запись = b"\x16\x03\x01" + struct.pack(">H", len(рукопожатие)) + рукопожатие
        п = разобрать_пакет(с.eth(с.ip(с.tcp(запись, 50123, 443), 6)))
        self.assertEqual("TLS", п.протокол)
        self.assertEqual("TLS ClientHello SNI=secure.example.net ALPN=h2,http/1.1", п.инфо)
        self.assertEqual(["TLS 1.3, TLS 1.2"], поля(п)["tls.handshake.extensions.supported_version"])

    def test_порт_не_делает_протокол(self):
        # Случайные байты на порту 53 — не DNS; на 443/UDP — не выдумываем QUIC из мусора.
        п = разобрать_пакет(с.eth(с.ip(с.udp(b"\x00\x01" + b"\xff" * 20, 5000, 53), 17)))
        self.assertEqual("UDP", п.протокол)

    def test_прочие(self):
        # TFTP, Syslog, RADIUS, GTP-U, BGP, Modbus, MQTT, RTP.
        self.assertEqual("TFTP чтение (RRQ) «boot.bin»",
                         разобрать_пакет(с.eth(с.ip(с.udp(b"\x00\x01boot.bin\x00octet\x00", 1234, 69), 17))).инфо)
        self.assertIn("Syslog ERR: link down", разобрать_пакет(с.eth(с.ip(с.udp(b"<11>link down", 1, 514), 17))).инфо)
        радиус = struct.pack(">BBH", 1, 7, 20 + 7) + bytes(16) + b"\x01\x07alice"
        self.assertEqual("RADIUS Access-Request, пользователь «alice»",
                         разобрать_пакет(с.eth(с.ip(с.udp(радиус, 1000, 1812), 17))).инфо)
        внутри = с.ip(с.udp(b"user", 7, 8, src="172.16.0.1", dst="172.16.0.2"), 17, src="172.16.0.1", dst="172.16.0.2")
        gtp = struct.pack(">BBHI", 0x30, 255, len(внутри), 0x1234) + внутри
        п = разобрать_пакет(с.eth(с.ip(с.udp(gtp, 2152, 2152), 17)))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "GTP", "IPv4", "UDP", "Данные"], п.стек)
        keepalive = b"\xff" * 16 + struct.pack(">HB", 19, 4)
        self.assertEqual("BGP KEEPALIVE", разобрать_пакет(с.eth(с.ip(с.tcp(keepalive, 179, 50000), 6))).инфо)
        modbus = struct.pack(">HHHBBHH", 1, 0, 6, 17, 3, 0, 10)
        self.assertIn("функция 3 (чтение регистров хранения)",
                      разобрать_пакет(с.eth(с.ip(с.tcp(modbus, 50000, 502), 6))).инфо)
        тема = b"sensors/t1"
        publish = bytes([0x30, 2 + len(тема) + 3]) + struct.pack(">H", len(тема)) + тема + b"21C"
        self.assertEqual("MQTT PUBLISH «sensors/t1»", разобрать_пакет(с.eth(с.ip(с.tcp(publish, 50000, 1883), 6))).инфо)
        rtp = struct.pack(">BBHII", 0x80, 0, 17, 160, 0xABCD) + b"\xd5" * 160
        self.assertEqual("RTP тип 0, SSRC 0x0000abcd, номер 17",
                         разобрать_пакет(с.eth(с.ip(с.udp(rtp, 16384, 16386), 17))).инфо)
        # Бит выравнивания P — признаки не строгие, RTP не объявляем.
        с_p = bytes([0xA0]) + rtp[1:]
        self.assertNotIn("RTP", разобрать_пакет(с.eth(с.ip(с.udp(с_p, 16384, 16386), 17))).стек)

    def test_оборванный_пакет_не_роняет(self):
        полный = с.eth(с.ip(с.tcp(b"GET / HTTP/1.1\r\n\r\n", 5, 80), 6))
        for длина in (5, 14, 20, 34, 40, 54):
            with self.subTest(длина=длина):
                п = разобрать_пакет(полный[:длина])
                self.assertTrue(п.ошибки or п.уровни)
        # Стек меток MPLS без дна, оборванный посреди метки.
        mpls = разобрать_пакет(с.eth(struct.pack(">I", (16 << 12) | 64) + b"\x00\x01", тип=0x8847))
        self.assertIn("пакет оборван: заголовок длиннее записанных байт", mpls.ошибки)


if __name__ == "__main__":
    unittest.main()


class ФильтрTests(unittest.TestCase):
    ПАКЕТЫ = [
        с.eth(с.ip(с.udp(с.dns_запрос("www.example.com"), 5000, 53), 17, src="10.1.1.1", dst="8.8.8.8")),
        с.eth(с.ip(с.tcp(b"GET /a HTTP/1.1\r\nHost: x.org\r\n\r\n", 40000, 80), 6, src="10.1.1.2", dst="1.2.3.4")),
        с.eth(struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, bytes(6), с.a4("10.1.1.1"), bytes(6),
                          с.a4("10.1.1.9")), тип=0x0806),
        с.eth(с.ip(с.udp(b"x" * 900, 7000, 7001), 17, src="192.168.5.5", dst="10.1.1.1")),
    ]

    def отобрать(self, текст):
        from reportgen.setevoy.filtr import отобрать
        поля = [разобрать_пакет(п, номер=i + 1).поля_фильтра() for i, п in enumerate(self.ПАКЕТЫ)]
        return [i + 1 for i in отобрать(поля, текст)]

    def test_выражения(self):
        for текст, ждём in (("", [1, 2, 3, 4]), ("dns", [1]), ("tcp or arp", [2, 3]), ("ip", [1, 2, 4]),
                            ("ip.addr == 10.1.1.0/24", [1, 2, 4]), ("ip.addr == 192.168.0.0/16", [4]),
                            ("ip.src == 10.1.1.1", [1]),
                            ("not ip.addr == 8.8.8.8 and udp", [4]), ("port in {53 80}", [1, 2]),
                            ("dns.qry.name contains \"EXAMPLE\"", [1]), ("http.request.method == GET", [2]),
                            ("frame.len > 500", [4]), ("(udp && !dns) || arp", [3, 4]),
                            ("http.host matches \"^x\\\\.\"", [2]), ("ip.dst != 10.1.1.1", [1, 2]),
                            ("tcp.dstport >= 80 and tcp.dstport <= 80", [2]),
                            ("данные", [4]), ("data", [4]), ("ошибки", [1, 2, 4]), ("not данные and udp", [1])):
            with self.subTest(текст=текст):
                self.assertEqual(ждём, self.отобрать(текст))

    def test_ошибки_фильтра(self):
        from reportgen.setevoy.filtr import ОшибкаФильтра, собрать
        for плохое in ("ip.addr ==", "(tcp", "tcp udp", "port in 53", "== 5"):
            with self.subTest(плохое=плохое), self.assertRaises(ОшибкаФильтра):
                собрать(плохое)


class ХранилищеTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from reportgen.setevoy.zahvaty import Захваты
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.захваты = Захваты(Path(self._tmp.name))

    def дождаться(self, ид):
        import time
        for _ in range(200):
            if self.захваты.прочитать(ид)["состояние"] in ("готово", "ошибка"):
                return self.захваты.прочитать(ид)
            time.sleep(0.05)
        self.fail("захват не разобрался")

    def test_поток_tcp_с_повтором_и_статистика(self):
        кл, сер = "10.0.0.1", "10.0.0.2"
        сегменты = [
            с.tcp(b"GET / HTTP/1.1\r\nHost: a\r\n\r\n", 40000, 80, seq=1000, src=кл, dst=сер),
            с.tcp(b"HTTP/1.1 200 OK\r\n\r\nhello ", 80, 40000, seq=5000, src=сер, dst=кл),
            с.tcp(b"HTTP/1.1 200 OK\r\n\r\nhello ", 80, 40000, seq=5000, src=сер, dst=кл),   # повтор
            с.tcp(b"world", 80, 40000, seq=5025, src=сер, dst=кл),
        ]
        пакеты = [с.eth(с.ip(сег, 6, src=кл if i == 0 else сер, dst=сер if i == 0 else кл))
                  for i, сег in enumerate(сегменты)]
        ид = self.захваты.создать(владелец=1, имя="t.pcap", данные=с.pcap(пакеты))
        self.assertEqual("готово", self.дождаться(ид)["состояние"])
        from reportgen.setevoy import statistika
        поток = statistika.поток(self.захваты.сводки(ид), self.захваты.нагрузки(ид), 2)
        self.assertEqual(["→", "←"], [к["направление"] for к in поток["куски"]])
        self.assertEqual("HTTP/1.1 200 OK\r\n\r\nhello world", поток["куски"][1]["текст"])
        self.assertEqual(("10.0.0.1:40000", "10.0.0.2:80"), (поток["клиент"], поток["сервер"]))
        иерархия = statistika.иерархия(self.захваты.сводки(ид))[0]
        self.assertEqual(4, иерархия["пакетов"])
        http = statistika.http(self.захваты.сводки(ид), self.захваты.поля(ид))
        self.assertEqual([("GET", "a", 200)], [(з["метод"], з["узел"], з["код"]) for з in http])
        диалоги = statistika.диалоги(self.захваты.сводки(ид), "tcp")
        self.assertEqual((1, 3), (диалоги[0]["пакетов_аб"], диалоги[0]["пакетов_ба"]))

    def test_сборка_фрагментов_ipv4(self):
        ответ = с.dns_ответ("frag.example.com", [f"10.9.{i}.{j}" for i in range(4) for j in range(10)])
        датаграмма = с.udp(ответ, 53, 5000)
        первая, вторая = датаграмма[:400], датаграмма[400:]
        f1 = с.ip(первая, 17, ид=0x77, флаги=0x2000)                 # MF, смещение 0
        f2 = с.ip(вторая, 17, ид=0x77, флаги=400 // 8)               # смещение 400
        ид = self.захваты.создать(владелец=1, имя="frag.pcap", данные=с.pcap([с.eth(f1), с.eth(f2)]))
        self.дождаться(ид)
        сводки = self.захваты.сводки(ид)
        self.assertIn("[собран из 2 фрагментов]", сводки[1]["инфо"])
        self.assertEqual("DNS", сводки[1]["протокол"])
        пакет = self.захваты.пакет(ид, 2)
        self.assertEqual(["IPv4", "UDP", "DNS"], [у["протокол"] for у in пакет["собранный"]["уровни"]])
        self.assertEqual([2], self.захваты.отобрать(ид, "dns.a == 10.9.3.9") and [
            i + 1 for i in self.захваты.отобрать(ид, "dns.a == 10.9.3.9")])

    def test_неизвестный_формат_и_выгрузка(self):
        import zlib
        кадры = []
        for i in range(60):
            тело = bytes([0xA5, 0x01]) + i.to_bytes(2, "big") + bytes([len(кадры) % 3]) + bytes(range(i % 7 + 3))
            кадры.append(с.eth(с.ip(с.udp(тело + zlib.crc32(тело).to_bytes(4, "little"), 9000, 9001), 17)))
        ид = self.захваты.создать(владелец=1, имя="u.pcap", данные=с.pcap(кадры))
        self.дождаться(ид)
        from reportgen.setevoy import statistika
        группы = statistika.неизвестные(self.захваты.сводки(ид), self.захваты.нагрузки(ид))
        self.assertEqual("UDP порт 9000", группы[0]["группа"])
        текст = " ".join(группы[0]["подробно"])
        self.assertIn("счётчик", текст)
        self.assertIn("CRC-32", текст)
        pcap = self.захваты.выгрузить_pcap(ид, [0, 5])
        заново = прочитать_захват(данные=pcap)
        self.assertEqual([кадры[0], кадры[5]], [з.данные for з in заново.записи])


class СтраницаПакетовTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)

    def загрузить(self, данные, имя="t.pcap"):
        import time
        к = self.сеть.client
        ответ = к.post("/api/pakety", files={"file": (имя, данные, "application/octet-stream")})
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид = ответ.json()["id"]
        for _ in range(200):
            if к.get(f"/api/pakety/{ид}").json()["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.05)
        return ид

    def test_весь_путь(self):
        к = self.сеть.client
        self.сеть.login("engineer")
        пакеты = [с.eth(с.ip(с.udp(с.dns_запрос(f"h{i}.example.com"), 5000 + i, 53), 17)) for i in range(5)]
        пакеты.append(с.eth(с.ip(с.tcp(b"GET / HTTP/1.1\r\nHost: a.b\r\n\r\n", 40000, 80), 6)))
        ид = self.загрузить(с.pcapng(пакеты))
        self.assertEqual("готово", к.get(f"/api/pakety/{ид}").json()["состояние"])
        список = к.get(f"/api/pakety/{ид}/list?filter=dns&limit=2").json()
        self.assertEqual((6, 5, 2), (список["всего"], список["отобрано"], len(список["items"])))
        self.assertEqual(400, к.get(f"/api/pakety/{ид}/list?filter=(dns").status_code)
        пакет = к.get(f"/api/pakety/{ид}/packet/6").json()
        self.assertEqual(["Ethernet", "IPv4", "TCP", "HTTP"], [у["протокол"] for у in пакет["уровни"]])
        self.assertTrue(all(п["имя"] for у in пакет["уровни"] for п in у["поля"]), "скрытых полей в дереве нет")
        self.assertEqual(404, к.get(f"/api/pakety/{ид}/packet/99").status_code)
        dns = к.get(f"/api/pakety/{ид}/stats?kind=dns").json()["items"]
        self.assertEqual("h0.example.com", dns[0]["имя"])
        иерархия = к.get(f"/api/pakety/{ид}/stats?kind=hierarchy").json()["items"][0]
        self.assertEqual(6, иерархия["пакетов"])
        for вид in ("conversations", "endpoints", "time", "http", "tls", "errors", "unknown"):
            with self.subTest(вид=вид):
                self.assertEqual(200, к.get(f"/api/pakety/{ид}/stats?kind={вид}").status_code)
        поток = к.get(f"/api/pakety/{ид}/stream/6").json()
        self.assertIn("GET / HTTP/1.1", поток["куски"][0]["текст"])
        выгрузка = к.get(f"/api/pakety/{ид}/export?filter=tcp")
        self.assertEqual([пакеты[5]], [з.данные for з in прочитать_захват(данные=выгрузка.content).записи])
        csv = к.get(f"/api/pakety/{ид}/export?format=csv").content.decode("utf-8-sig")
        self.assertEqual(7, len(csv.strip().splitlines()))
        вопрос = к.post(f"/api/pakety/{ид}/ask", json={"number": 6}).json()
        self.assertIn("пакет №6", вопрос["question"])
        # Чужой захват не виден.
        self.сеть.login("gruppa")
        self.assertEqual(404, к.get(f"/api/pakety/{ид}").status_code)
        self.сеть.login("engineer")
        self.assertEqual(200, к.delete(f"/api/pakety/{ид}").status_code)
        self.assertEqual(400, к.post("/api/pakety", files={"file": ("x.pcap", b"not a capture at all" * 5,
                                                                    "application/octet-stream")}).status_code)

    def test_dpo_и_выбор_файла_без_фильтра(self):
        # .dpo — те же кадры с двухбайтовой длиной, что и .sig: вид узнаётся по содержимому.
        к = self.сеть.client
        self.сеть.login("engineer")
        import potok_sintez as пс
        кадры = [с.ip(с.udp(b"x" * (20 + i), 5000 + i, 53), 17) for i in range(12)]
        ид = self.загрузить(пс.sig(кадры), имя="запись.dpo")
        состояние = к.get(f"/api/pakety/{ид}").json()
        self.assertEqual(("готово", 12), (состояние["состояние"], к.get(f"/api/pakety/{ид}/list").json()["всего"]))
        # Окно выбора файла не фильтрует по расширению: Windows прячет .sig/.dpo при фильтре.
        from pathlib import Path
        js = (Path(__file__).resolve().parents[1] / "src/reportgen/web/static/app.js").read_text(encoding="utf-8")
        for фильтр in ("accept: '.pcap", "accept: '.etl", "accept: '.alist"):
            self.assertNotIn(фильтр, js)

    def test_из_разбора_потока(self):
        к = self.сеть.client
        self.сеть.login("engineer")
        import potok_sintez as пс
        from reportgen.potok.bity import в_биты  # noqa: F401
        кадры = пс.hdlc(пс.пакеты_ip(30), флагов_между=4)
        ответ = к.post("/api/potok", data={"profile": "быстро"},
                       files={"file": ("h.bin", кадры, "application/octet-stream")}).json()["id"]
        import time
        for _ in range(600):
            состояние = к.get(f"/api/potok/{ответ}").json()
            if состояние["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.2)
        этап = next(э for э in состояние["этапы"] if э.get("выгрузка") in ("pcap", "sig"))
        ид = к.post("/api/pakety/from-potok", json={"job": ответ, "stage": этап["номер"]}).json()["id"]
        for _ in range(200):
            if к.get(f"/api/pakety/{ид}").json()["состояние"] == "готово":
                break
            time.sleep(0.05)
        список = к.get(f"/api/pakety/{ид}/list?filter=ip").json()
        self.assertEqual(30, список["отобрано"])


class ФильтрПоБайтамTests(unittest.TestCase):
    def test_срезы_полубайты_contains(self):
        from reportgen.setevoy.filtr import ОшибкаФильтра, собрать
        кадр = bytes.fromhex("00112233445566778899aabb0800450000") + b"JFIF"
        п = {"_frame": кадр, "_payload": bytes([0xA5, 0x01, 0x02])}
        for текст, ждём in (("frame[12:2] == 0800", True), ("frame[12:2] == 08:00", True),
                            ("frame[12:2] == 0x0800", True), ("frame[12:2] == 2048", True),
                            ("frame[12] == 8", True), ("frame[12] != 8", False), ("frame[0:4] > 0x00112232", True),
                            ("payload[0].hi == 0xa", True), ("payload[0].lo == 5", True), ("payload[0].hi == 5", False),
                            ("payload[1] in {1 2}", True), ("frame[100] == 1", False), ("frame[100]", False),
                            ("frame contains 45:00", True), ("frame contains \"JFIF\"", True),
                            ("frame contains ffd8", False), ("payload matches \"\\\\x01\\\\x02\"", True)):
            with self.subTest(текст=текст):
                self.assertEqual(ждём, собрать(текст)(п))
        for плохое in ("frame[1:9] == 1", "frame == 1", "frame[0] == zz"):
            with self.subTest(плохое=плохое), self.assertRaises(ОшибкаФильтра):
                собрать(плохое)({"_frame": b"\0" * 20})


class МатрицаTests(unittest.TestCase):
    def test_столбец_распознаёт_поля(self):
        import random

        from reportgen.setevoy import statistika
        случ = random.Random(3)
        ряды = []
        for i in range(200):
            тело = bytes(случ.randrange(256) for _ in range(случ.randrange(8, 40)))
            ряды.append(b"\xa5\x5a" + i.to_bytes(2, "big") + bytes([случ.choice([1, 2, 7])])
                        + len(тело).to_bytes(2, "big") + тело)
        self.assertEqual(["постоянное поле: 0xa5"], statistika.столбец(ряды, 0)["вывод"])
        self.assertEqual(["счётчик: +1 у 100 % соседних пакетов"], statistika.столбец(ряды, 2, 2)["вывод"])
        self.assertEqual(["поле типа: 3 значений"], statistika.столбец(ряды, 4)["вывод"])
        self.assertEqual(["поле длины: значение = длина пакета − 7 у 100 % пакетов"],
                         statistika.столбец(ряды, 5, 2)["вывод"])
        полубайты = statistika.столбец(ряды, 0)["полубайты"]
        self.assertEqual(200, полубайты["старший"][0xA])
        профиль = statistika.профиль_столбцов(ряды, 0, 8)
        self.assertEqual(["a5", "5a", "00", None], [к["постоянное"] for к in профиль[:4]])  # старший байт счётчика — 00
        self.assertEqual(0, statistika.столбец(ряды, 500)["есть"])

    def test_столбец_без_ложных_выводов(self):
        from reportgen.setevoy import statistika
        # Счётчик 8 бит переходит через 255 → 0: всё равно «+1 у 100 %».
        ряды = [bytes([i % 256, 0x10]) + bytes(20) for i in range(600)]
        self.assertEqual(["счётчик: +1 у 100 % соседних пакетов"], statistika.столбец(ряды, 0)["вывод"])
        self.assertEqual(1.0, statistika.столбец(ряды, 0)["счётчик"])
        # Все пакеты одной длины: постоянный байт 0x10 = длина − 12 — это не поле длины.
        self.assertEqual(["постоянное поле: 0x10"], statistika.столбец(ряды, 1)["вывод"])
        # 99 % одно значение и редкие другие — постоянное поле, а не «поле типа».
        ряды = [bytes([7 if i % 100 else i // 100]) + bytes(3) for i in range(1000)]
        вывод = statistika.столбец(ряды, 0)["вывод"]
        self.assertTrue(вывод and вывод[0].startswith("постоянное поле"), вывод)
        self.assertFalse(any("типа" in в for в in вывод), вывод)

    def test_через_сервер(self):
        import time

        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        сеть = Сеть()
        сеть.setUp()
        self.addCleanup(сеть.tearDown)
        сеть.login("engineer")
        к = сеть.client
        пакеты = [с.eth(с.ip(с.udp(b"\xa5\x5a" + bytes([i % 3]) + b"xyz", 9000, 9001), 17)) for i in range(30)]
        ид = к.post("/api/pakety", files={"file": ("m.pcap", с.pcap(пакеты), "application/octet-stream")}).json()["id"]
        for _ in range(200):
            if к.get(f"/api/pakety/{ид}").json()["состояние"] == "готово":
                break
            time.sleep(0.05)
        м = к.get(f"/api/pakety/{ид}/matrix?base=payload&start=0&count=6").json()
        self.assertEqual((30, 6, "a55a00"), (м["отобрано"], len(м["столбцы"]), м["строки"][0]["hex"][:6]))
        столбец = к.get(f"/api/pakety/{ид}/column?base=payload&pos=2").json()
        self.assertEqual(3, столбец["различных"])
        отбор = к.get(f"/api/pakety/{ид}/list?filter=" + "payload[2] == 1 and frame contains a5:5a").json()
        self.assertEqual(10, отбор["отобрано"])
        self.assertEqual(400, к.get(f"/api/pakety/{ид}/matrix?base=zz").status_code)
