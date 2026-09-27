"""BGP-4 (RFC 4271, 4760, 6793, 1997, 8092, 5492) и OSPFv2 (RFC 2328, прил. A) — сообщения собираются
struct.pack'ом по документам; суммы (IP для пакета OSPF, Флетчер для LSA) считаются здесь же."""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy.protokoly import bgp, ospf
from reportgen.setevoy.razbor import разобрать_пакет


def сообщение(тип, тело):
    return b"\xff" * 16 + struct.pack(">HB", 19 + len(тело), тип) + тело


def префикс(текст):
    адрес, длина = текст.split("/")
    длина = int(длина)
    байты = (с.a6(адрес) if ":" in адрес else с.a4(адрес))[:(длина + 7) // 8]
    return bytes([длина]) + байты


def атрибут(флаги, тип, значение):
    if флаги & 0x10:
        return struct.pack(">BBH", флаги, тип, len(значение)) + значение
    return struct.pack(">BBB", флаги, тип, len(значение)) + значение


def update(отозвано=(), атрибуты=b"", сети=()):
    о = b"".join(префикс(x) for x in отозвано)
    return сообщение(2, struct.pack(">H", len(о)) + о + struct.pack(">H", len(атрибуты)) + атрибуты
                     + b"".join(префикс(x) for x in сети))


def tcp_пакет(данные, порт=179):
    return с.eth(с.ip(с.tcp(данные, 50000, порт), 6))


def все_поля(п, протокол):
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = []

    def обойти(поля):
        for x in поля:
            итог.append(x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


def тексты(п, протокол):
    return {x.имя: x.текст for x in все_поля(п, протокол)}


class BgpTests(unittest.TestCase):
    def test_update_ipv4(self):
        путь = struct.pack(">BB", 2, 3) + struct.pack(">III", 65001, 65002, 4200000000)
        атр = (атрибут(0x40, 1, b"\0") + атрибут(0x40, 2, путь) + атрибут(0x40, 3, с.a4("10.0.0.1"))
               + атрибут(0x80, 4, struct.pack(">I", 50)) + атрибут(0x40, 5, struct.pack(">I", 200))
               + атрибут(0xC0, 8, struct.pack(">III", (65001 << 16) | 100, 0xFFFFFF01, 0xFFFF029A))
               + атрибут(0xC0, 32, struct.pack(">III", 65001, 1, 2)))
        п = разобрать_пакет(tcp_пакет(update(("192.0.2.0/24",), атр, ("198.51.100.0/24", "203.0.113.128/25"))))
        self.assertEqual("BGP UPDATE: объявлено 198.51.100.0/24, 203.0.113.128/25; отозвано 192.0.2.0/24; "
                         "путь 65001 65002 4200000000", п.инфо)
        т = тексты(п, "BGP")
        self.assertEqual(("IGP", "10.0.0.1", "50", "200"), (т["ORIGIN"], т["Следующий узел"], т["MED"], т["LOCAL_PREF"]))
        сообщества = [x.текст for x in все_поля(п, "BGP") if x.ключ == "bgp.community"]
        self.assertEqual(["65001:100", "NO_EXPORT", "BLACKHOLE"], сообщества)
        self.assertEqual("65001:1:2", т["Большое сообщество"])
        имена = [x.имя for x in все_поля(п, "BGP") if x.ключ == "bgp.attr.type"]
        self.assertEqual(["ORIGIN [T], 1 байт", "AS_PATH [T], 14 байт", "NEXT_HOP [T], 4 байт",
                          "MULTI_EXIT_DISC [O], 4 байт", "LOCAL_PREF [T], 4 байт", "COMMUNITIES [OT], 12 байт",
                          "LARGE_COMMUNITY [OT], 12 байт"], имена)

    def test_размер_номера_as(self):
        """Как heuristic_as2_or_4_from_as_path: доходит до конца — 2; за сегментом годный вид и нет нулевых
        2-байтовых номеров — 2; у 4-байтовых малых номеров старшие байты нули — 4; иначе 4."""
        два = struct.pack(">BBHH", 2, 2, 100, 200)
        self.assertEqual(2, bgp.размер_as(два, 0, len(два)))
        два_и_множество = два + struct.pack(">BBH", 1, 1, 300)
        self.assertEqual(2, bgp.размер_as(два_и_множество, 0, len(два_и_множество)))
        малые_4 = struct.pack(">BBII", 2, 2, 100, 200) + struct.pack(">BBI", 1, 1, 300)
        self.assertEqual(4, bgp.размер_as(малые_4, 0, len(малые_4)))
        большие_4 = struct.pack(">BBIII", 2, 3, 65001, 65002, 4200000000)
        self.assertEqual(4, bgp.размер_as(большие_4, 0, len(большие_4)))
        self.assertEqual(2, bgp.размер_as(b"", 0, 0))

    def test_размер_номера_as_краевые(self):
        # за сегментом «годный вид» (0x01), но старшие два байта номера — нули: номера 4-байтовые
        малый = struct.pack(">BBI", 2, 1, 0x0102) + struct.pack(">BBI", 2, 1, 7)
        self.assertEqual(4, bgp.размер_as(малый, 0, len(малый)))
        # за сегментом не вид сегмента (0x56) — 4, хотя нулевых 2-байтовых номеров нет
        большой = struct.pack(">BBI", 2, 1, 0x12345678)
        self.assertEqual(4, bgp.размер_as(большой, 0, len(большой)))

    def test_as4_path_всегда_4_байта(self):
        """Эвристика сочла бы номер 0x01010203 двумя 2-байтовыми (за ним 0x02 — годный вид), AS4_PATH — 4."""
        путь4 = struct.pack(">BBI", 2, 1, 0x01010203)
        self.assertEqual(2, bgp.размер_as(путь4, 0, len(путь4)))
        п = разобрать_пакет(tcp_пакет(update(атрибуты=атрибут(0xC0, 17, путь4), сети=("10.0.0.0/8",))))
        self.assertIn("путь 16843267", п.инфо)

    def test_ошибки_атрибутов(self):
        п = разобрать_пакет(tcp_пакет(update(атрибуты=атрибут(0x40, 2, struct.pack(">BBHH", 5, 2, 1, 2)))))
        self.assertTrue(any("AS_PATH" in о for о in п.ошибки), п.ошибки)
        атр = атрибут(0x40, 1, b"\0")
        длинный = атр[:2] + bytes([атр[2] + 5]) + атр[3:]
        п = разобрать_пакет(tcp_пакет(update(атрибуты=длинный)))
        self.assertTrue(any("атрибут длиннее списка" in о for о in п.ошибки), п.ошибки)
        п = разобрать_пакет(tcp_пакет(b"\xff" * 16 + struct.pack(">HB", 18, 4)))
        self.assertTrue(any("меньше заголовка" in о for о in п.ошибки), п.ошибки)

    def test_агрегатор_2_байта_и_vpn(self):
        т = тексты(разобрать_пакет(tcp_пакет(update(атрибуты=атрибут(0xC0, 7, struct.pack(">H", 100) + с.a4("1.1.1.1"))))),
                   "BGP")
        self.assertEqual("AS 100, 1.1.1.1", т["Агрегатор"])
        vpn = struct.pack(">HBB", 1, 128, 12) + b"\0" * 8 + с.a4("10.0.0.1") + b"\0" + b"\x70" + b"\1" * 14
        п = разобрать_пакет(tcp_пакет(update(атрибуты=атрибут(0x80, 14, vpn))))
        self.assertEqual([], п.ошибки)
        self.assertNotIn("bgp.mp_reach_nlri", п.поля_фильтра())
        self.assertIn("IPv4 Labeled VPN Unicast", [x.текст for x in все_поля(п, "BGP")])

    def test_route_refresh_не_той_длины(self):
        п = разобрать_пакет(tcp_пакет(сообщение(5, struct.pack(">HBBB", 1, 0, 1, 0))))
        self.assertEqual("BGP ROUTE-REFRESH", п.инфо)
        self.assertNotIn("bgp.route_refresh.afi", п.поля_фильтра())

    def test_as_path_два_байта_и_множество(self):
        путь = struct.pack(">BBHH", 2, 2, 100, 200) + struct.pack(">BBH", 1, 1, 300)
        п = разобрать_пакет(tcp_пакет(update(атрибуты=атрибут(0x40, 2, путь), сети=("10.0.0.0/8",))))
        self.assertIn("путь 100 200 {300}", п.инфо)
        self.assertEqual("путь (1 2)", "путь " + bgp.путь_словами([(3, [1, 2])]))

    def test_mp_reach_и_unreach_ipv6(self):
        reach = struct.pack(">HBB", 2, 1, 32) + с.a6("2001:db8::1") + с.a6("fe80::1") + b"\0" + префикс("2001:db8:1::/48")
        unreach = struct.pack(">HB", 2, 1) + префикс("2001:db8:2::/64")
        атр = атрибут(0x80, 14, reach) + атрибут(0x90, 15, unreach)
        п = разобрать_пакет(tcp_пакет(update(атрибуты=атр)))
        self.assertEqual("BGP UPDATE: объявлено 2001:db8:1::/48; отозвано 2001:db8:2::/64", п.инфо)
        т = [x.текст for x in все_поля(п, "BGP")]
        self.assertIn("2001:db8::1, fe80::1", т)
        self.assertIn("IPv6 Unicast", т)
        self.assertIn("MP_UNREACH_NLRI [OE], 12 байт", [x.имя for x in все_поля(п, "BGP")])

    def test_конец_rib_и_прочие_атрибуты(self):
        п = разобрать_пакет(tcp_пакет(update()))
        self.assertEqual("BGP UPDATE (конец RIB)", п.инфо)
        атр = (атрибут(0xC0, 7, struct.pack(">I", 65010) + с.a4("1.1.1.1")) + атрибут(0x80, 9, с.a4("2.2.2.2"))
               + атрибут(0x80, 10, с.a4("3.3.3.3") + с.a4("4.4.4.4")) + атрибут(0xC0, 16, bytes(range(8)))
               + атрибут(0x40, 6, b"") + атрибут(0xC0, 99, b"\x01\x02"))
        т = тексты(разобрать_пакет(tcp_пакет(update(атрибуты=атр, сети=("10.1.0.0/16",)))), "BGP")
        self.assertEqual(("AS 65010, 1.1.1.1", "2.2.2.2", "3.3.3.3 4.4.4.4", "0001020304050607", "0102"),
                         (т["Агрегатор"], т["Отправитель"], т["Кластеры"], т["Расширенное сообщество"], т["Значение"]))

    def test_open_с_возможностями(self):
        возможности = (struct.pack(">BBHBB", 1, 4, 1, 0, 1) + struct.pack(">BB", 2, 0)
                       + struct.pack(">BBI", 65, 4, 4200000001))
        тело = struct.pack(">BHH4sB", 4, 23456, 90, с.a4("10.9.9.9"), 2 + len(возможности)) \
            + struct.pack(">BB", 2, len(возможности)) + возможности
        п = разобрать_пакет(tcp_пакет(сообщение(1, тело)))
        self.assertEqual("BGP OPEN: AS 4200000001, BGP ID 10.9.9.9", п.инфо)
        возм = [x.текст for x in все_поля(п, "BGP") if x.ключ == "bgp.cap.type"]
        self.assertEqual(["многопротокольность: IPv4 Unicast", "обновление маршрутов", "4-байтовые номера AS: AS 4200000001"],
                         возм)

    def test_notification(self):
        п = разобрать_пакет(tcp_пакет(сообщение(3, b"\x06\x02")))
        self.assertEqual("BGP NOTIFICATION: прекращение (Cease) — выключено администратором", п.инфо)
        п = разобрать_пакет(tcp_пакет(сообщение(3, b"\x02\x02\xfd\xe8")))
        т = тексты(п, "BGP")
        self.assertEqual(("2 (ошибка OPEN)", "2 (неверный AS соседа)", "fde8"), (т["Ошибка"], т["Подкод"], т["Данные"]))

    def test_несколько_сообщений_и_route_refresh(self):
        п = разобрать_пакет(tcp_пакет(сообщение(4, b"") + сообщение(5, struct.pack(">HBB", 2, 0, 1)) + сообщение(4, b"")))
        self.assertEqual("BGP KEEPALIVE; ROUTE-REFRESH; KEEPALIVE", п.инфо)
        self.assertEqual("IPv6 Unicast", тексты(п, "BGP")["Семейство"])

    def test_оборванное_и_испорченное(self):
        полное = update(сети=("10.0.0.0/8",))
        п = разобрать_пакет(tcp_пакет(полное[:-2]))
        self.assertEqual("BGP UPDATE [оборвано]", п.инфо)
        плохое = update(атрибуты=атрибут(0x40, 2, b"\x02\x05\x00\x01"))
        п = разобрать_пакет(tcp_пакет(плохое))
        self.assertTrue(any("AS_PATH" in о for о in п.ошибки))
        длиннее = сообщение(2, struct.pack(">H", 10) + b"\x08")
        self.assertTrue(any("отозванные" in о for о in разобрать_пакет(tcp_пакет(длиннее)).ошибки))

    def test_префиксы(self):
        self.assertEqual(["0.0.0.0/0", "10.0.0.0/8", "10.1.2.3/32"],
                         bgp.префиксы(b"\x00\x08\x0a\x20\x0a\x01\x02\x03", 0, 8))
        with self.assertRaises(bgp._Не):
            bgp.префиксы(b"\x21\x0a\x01\x02\x03\x04", 0, 6)          # 33 бита у IPv4
        with self.assertRaises(bgp._Не):
            bgp.префиксы(b"\x18\x0a\x01", 0, 3)                      # байт не хватает

    def test_не_bgp(self):
        self.assertNotIn("BGP", разобрать_пакет(tcp_пакет(b"\xfe" * 16 + b"\x00\x13\x04")).стек)


def флетчер(lsa: bytes) -> bytes:
    """Проверочные байты ISO 8473 (поле сумм — байты 16 и 17 LSA, считается от байта 2): из условий
    C0 = C1 = 0 по модулю 255 — X ≡ (L − p − 1)·C0 − C1, Y ≡ −C0 − X, где p — место X от начала суммы."""
    данные = bytearray(lsa[2:16] + b"\0\0" + lsa[18:])
    c0 = c1 = 0
    for б in данные:
        c0 = (c0 + б) % 255
        c1 = (c1 + c0) % 255
    L, p = len(данные), 14
    x = ((L - p - 1) * c0 - c1) % 255
    y = (-c0 - x) % 255
    return bytes([x or 255, y or 255])


def lsa(тип, ид, кто, тело, номер=0x80000001, возраст=10, параметры=0x02):
    заголовок = struct.pack(">HBB4s4sIHH", возраст, параметры, тип, с.a4(ид), с.a4(кто), номер, 0, 20 + len(тело))
    полный = заголовок + тело
    return полный[:16] + флетчер(полный) + полный[18:]


def ospf_пакет(тип, тело, подлинность=0, испортить=False, пароль=b"\0" * 8, хвост=b""):
    """Сумма — по пакету без 64-битового поля подлинности (RFC 2328, D.4); ``хвост`` — данные после
    пакета OSPF внутри IP (например, блок LLS, RFC 5613)."""
    заголовок = struct.pack(">BBH4s4sHH8s", 2, тип, 24 + len(тело), с.a4("1.1.1.1"), с.a4("0.0.0.0"), 0, подлинность,
                            пароль)
    сумма = с.сумма(заголовок[:16] + тело)
    if испортить:
        сумма ^= 1
    пакет = заголовок[:12] + struct.pack(">H", сумма) + заголовок[14:] + тело
    return с.eth(с.ip(пакет + хвост, 89, dst="224.0.0.5"))


class OspfTests(unittest.TestCase):
    def test_hello(self):
        тело = struct.pack(">4sHBBI4s4s", с.a4("255.255.255.0"), 10, 0x02, 1, 40, с.a4("10.0.0.1"), с.a4("0.0.0.0")) \
            + с.a4("2.2.2.2") + с.a4("3.3.3.3")
        п = разобрать_пакет(ospf_пакет(1, тело))
        self.assertEqual("OSPF Hello, DR 10.0.0.1, соседей 2, router 1.1.1.1, area 0.0.0.0", п.инфо)
        т = тексты(п, "OSPF")
        self.assertEqual(("255.255.255.0", "10", "E", "40", "2.2.2.2 3.3.3.3", "0x%04x (верна)" % int(т["Сумма"][2:6], 16)),
                         (т["Маска"], т["Интервал Hello, с"], т["Параметры"], т["Интервал отказа, с"], т["Соседи"],
                          т["Сумма"]))
        self.assertEqual([], п.ошибки)

    def test_неверная_сумма(self):
        п = разобрать_пакет(ospf_пакет(1, b"\0" * 20, испортить=True))
        self.assertIn("OSPF: неверная контрольная сумма пакета", п.ошибки)
        # криптографическая подлинность — сумма не используется (RFC 2328, D.4.3)
        self.assertEqual([], разобрать_пакет(ospf_пакет(1, b"\0" * 20, подлинность=2, испортить=True)).ошибки)

    def test_lsu_с_lsa_всех_видов(self):
        router = lsa(1, "1.1.1.1", "1.1.1.1", struct.pack(">BBH", 0x03, 0, 2)
                     + с.a4("10.0.0.2") + с.a4("10.0.0.1") + struct.pack(">BBH", 2, 0, 10)
                     + с.a4("192.168.1.0") + с.a4("255.255.255.0") + struct.pack(">BBH", 3, 0, 1))
        network = lsa(2, "10.0.0.2", "2.2.2.2", с.a4("255.255.255.0") + с.a4("1.1.1.1") + с.a4("2.2.2.2"))
        summary = lsa(3, "172.16.0.0", "3.3.3.3", с.a4("255.255.0.0") + struct.pack(">I", 20))
        external = lsa(5, "8.8.8.0", "4.4.4.4", с.a4("255.255.255.0") + struct.pack(">I", (1 << 31) | 100)
                       + с.a4("0.0.0.0") + struct.pack(">I", 7))
        nssa = lsa(7, "9.9.9.0", "4.4.4.4", с.a4("255.255.255.0") + struct.pack(">I", 5) + с.a4("5.5.5.5")
                   + struct.pack(">I", 0))
        тело = struct.pack(">I", 5) + router + network + summary + external + nssa
        п = разобрать_пакет(ospf_пакет(4, тело))
        self.assertEqual("OSPF LSU: Router-LSA, Network-LSA, Summary-LSA (сеть), AS-External-LSA, "
                         "NSSA AS-External-LSA, router 1.1.1.1, area 0.0.0.0", п.инфо)
        self.assertEqual([], п.ошибки)
        поля = все_поля(п, "OSPF")
        связи = [(x.имя, x.текст) for x in поля if x.ключ == "ospf.lsa.router.link_type"]
        self.assertEqual([("связь (транзитная сеть)", "ID 10.0.0.2, данные 10.0.0.1, метрика 10"),
                          ("связь (тупиковая сеть)", "ID 192.168.1.0, данные 255.255.255.0, метрика 1")], связи)
        т = {x.ключ: x.текст for x in поля}

        def все(ключ):
            return [x.текст for x in поля if x.ключ == ключ]
        self.assertEqual(("E B", "1.1.1.1 2.2.2.2", "20"), (т["ospf.v2.router.lsa.flags"], т["ospf.lsa.net.attachrtr"],
                                                            т["ospf.lsa.summary.metric"]))
        self.assertEqual((["100 (тип 2)", "5 (тип 1)"], ["7", "0"], ["0.0.0.0", "5.5.5.5"]),
                         (все("ospf.lsa.asext.metric"), все("ospf.lsa.asext.extrttag"), все("ospf.lsa.asext.fwdaddr")))
        self.assertEqual(5, len([x for x in поля if x.ключ == "ospf.lsa.chksum" and "верна" in x.текст
                                 and "НЕВЕРНА" not in x.текст]))

    def test_неверная_сумма_lsa(self):
        испорченный = bytearray(lsa(3, "172.16.0.0", "3.3.3.3", с.a4("255.255.0.0") + struct.pack(">I", 20)))
        испорченный[25] ^= 1
        п = разобрать_пакет(ospf_пакет(4, struct.pack(">I", 1) + bytes(испорченный)))
        self.assertIn("OSPF: неверная сумма LSA", п.ошибки)

    def test_сумма_флетчера(self):
        """Возраст в сумму не входит; перестановка байт сумму меняет (в отличие от простой суммы)."""
        a = lsa(3, "172.16.0.0", "3.3.3.3", с.a4("255.255.0.0") + struct.pack(">I", 20))
        self.assertTrue(ospf.флетчер_верен(a[2:]))
        self.assertEqual(a[16:18], lsa(3, "172.16.0.0", "3.3.3.3", с.a4("255.255.0.0") + struct.pack(">I", 20),
                                       возраст=3600)[16:18])
        б = bytearray(a)
        б[26], б[27] = б[27], б[26]                                   # метрика 0, 20 → 20, 0
        self.assertFalse(ospf.флетчер_верен(bytes(б[2:])))

    def test_сумма_без_поля_подлинности(self):
        тело = b"\0" * 20
        self.assertEqual([], разобрать_пакет(ospf_пакет(1, тело, подлинность=1, пароль=b"secret12")).ошибки)

    def test_lls_после_пакета_не_соседи(self):
        тело = struct.pack(">4sHBBI4s4s", с.a4("255.255.255.0"), 10, 0x12, 1, 40, с.a4("10.0.0.1"), с.a4("0.0.0.0"))
        п = разобрать_пакет(ospf_пакет(1, тело + с.a4("2.2.2.2"), хвост=b"\xff\xf6\x00\x03" + b"\0" * 8))
        self.assertIn("соседей 1", п.инфо)

    def test_флетчер_нужны_обе_суммы(self):
        self.assertFalse(ospf.флетчер_верен(bytes([1, 253])))       # C1 ≡ 0, C0 = 254
        self.assertFalse(ospf.флетчер_верен(bytes([1, 254])))       # C0 ≡ 0, C1 = 1
        self.assertTrue(ospf.флетчер_верен(bytes([0, 0])))

    def test_особые_поля_lsa(self):
        """Возраст без бита DoNotAge (RFC 1793); связь с TOS; 257 связей (число — два байта); флаги E и B
        по отдельности; резервный байт перед метрикой Summary-LSA не входит в метрику."""
        связь_tos = с.a4("10.0.0.2") + с.a4("10.0.0.1") + struct.pack(">BBH", 1, 1, 10) + struct.pack(">BxH", 4, 7)
        связь = с.a4("10.0.0.3") + с.a4("10.0.0.1") + struct.pack(">BBH", 3, 0, 5)
        router = lsa(1, "1.1.1.1", "1.1.1.1", struct.pack(">BBH", 0x02, 0, 2) + связь_tos + связь, возраст=0x8005)
        много = lsa(1, "7.7.7.7", "7.7.7.7", struct.pack(">BBH", 0x01, 0, 257) + связь * 257)
        summary = lsa(3, "172.16.0.0", "3.3.3.3", с.a4("255.255.0.0") + struct.pack(">I", 0x7F000014))
        п = разобрать_пакет(ospf_пакет(4, struct.pack(">I", 3) + router + много + summary))
        self.assertEqual([], п.ошибки)
        поля = все_поля(п, "OSPF")

        def все(ключ):
            return [x.текст for x in поля if x.ключ == ключ]
        self.assertEqual("5", все("ospf.lsa.age")[0])
        self.assertEqual(["E", "B"], все("ospf.v2.router.lsa.flags"))
        связи = все("ospf.lsa.router.link_type")
        self.assertEqual(2 + 257, len(связи))
        self.assertEqual(["ID 10.0.0.2, данные 10.0.0.1, метрика 10", "ID 10.0.0.3, данные 10.0.0.1, метрика 5"],
                         связи[:2])
        self.assertEqual(["20"], все("ospf.lsa.summary.metric"))

    def test_lsa_короче_заголовка(self):
        короткий = struct.pack(">HBB4s4sIHH", 1, 0, 1, с.a4("1.1.1.1"), с.a4("1.1.1.1"), 1, 0, 0)
        п = разобрать_пакет(ospf_пакет(4, struct.pack(">I", 3) + короткий * 3))
        self.assertIn("OSPF: LSA неверной длины (короче заголовка или длиннее пакета)", п.ошибки)
        self.assertEqual(1, len([x for x in все_поля(п, "OSPF") if x.ключ == "ospf.lsa"]))

    def test_ospfv3_длинный_как_прежде(self):
        # Hello OSPFv3 с двумя соседями — 44 байта: разбор v2 показал бы маску, DR и соседей
        пакет = struct.pack(">BBH4s4sHBB", 3, 1, 44, с.a4("1.1.1.1"), с.a4("0.0.0.0"), 0, 0, 0) + b"\1" * 28
        п = разобрать_пакет(с.eth(с.ip(пакет, 89)))
        self.assertEqual("OSPF Hello, router 1.1.1.1, area 0.0.0.0", п.инфо)

    def test_dbd_lsr_ack(self):
        заголовок = lsa(1, "1.1.1.1", "1.1.1.1", b"\0" * 4)[:20]
        п = разобрать_пакет(ospf_пакет(2, struct.pack(">HBBI", 1500, 0x42, 0x07, 1234) + заголовок * 2))
        т = тексты(п, "OSPF")
        параметры = [x.текст for x in все_поля(п, "OSPF") if x.ключ == "ospf.v2.options"][0]
        self.assertEqual(("1500", "O E", "I M MS", "1234"), (т["MTU"], параметры, т["Флаги"], т["Номер DD"]))
        self.assertIn("DBD, заголовков LSA 2", п.инфо)
        п = разобрать_пакет(ospf_пакет(3, struct.pack(">I4s4s", 1, с.a4("1.1.1.1"), с.a4("1.1.1.1"))))
        self.assertIn("LSR, запросов 1", п.инфо)
        self.assertEqual("1.1.1.1 от 1.1.1.1", тексты(п, "OSPF")["запрос Router-LSA"])
        п = разобрать_пакет(ospf_пакет(5, заголовок))
        self.assertIn("LSAck, заголовков LSA 1", п.инфо)
        self.assertIn("Router-LSA: 1.1.1.1 от 1.1.1.1", [x.имя for x in все_поля(п, "OSPF")])

    def test_ospfv3_как_прежде(self):
        пакет = struct.pack(">BBH4s4sHBB", 3, 1, 16, с.a4("1.1.1.1"), с.a4("0.0.0.0"), 0, 0, 0)
        п = разобрать_пакет(с.eth(с.ip(пакет, 89)))
        self.assertEqual("OSPF Hello, router 1.1.1.1, area 0.0.0.0", п.инфо)


if __name__ == "__main__":
    unittest.main()
