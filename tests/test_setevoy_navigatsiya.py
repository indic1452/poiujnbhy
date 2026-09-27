"""NMEA 0183 по UDP: сумма «*HH», предложения AIVDM (в том числе из двух частей) → сообщения AIS; канал «AIS»."""

import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет
from reportgen.setevoy.protokoly.navigatsiya import сумма_nmea

ОДНО = b"!AIVDM,1,1,,B,15M67FC000G?ufbE`FepT@3n00Sa,0*5C\r\n"
ДВА = (b"!AIVDM,2,1,1,A,55?MbV02;H;s<HtKR20EHE:0@T4@Dn2222222216L961O5Gf0NSQEp6ClRp8,0*1C\r\n"
       b"!AIVDM,2,2,1,A,88888888880,2*25\r\n")


def udp(нагрузка, порт=10110):
    return с.eth(с.ip(с.udp(нагрузка, 5000, порт), 17))


class NmeaTests(unittest.TestCase):
    def test_сумма(self):
        self.assertEqual(0x5C, сумма_nmea("AIVDM,1,1,,B,15M67FC000G?ufbE`FepT@3n00Sa,0"))

    def test_одно_предложение(self):
        п = разобрать_пакет(udp(ОДНО))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "NMEA", "AIS"], п.стек)
        ф = п.поля_фильтра()
        self.assertEqual(([366053209], [37.802118], [-122.341618]), (ф["ais.mmsi"], ф["ais.lat"], ф["ais.lon"]))

    def test_две_части(self):
        п = разобрать_пакет(udp(ДВА))
        self.assertEqual(["EVER DIADEM"], п.поля_фильтра()["ais.name"])
        self.assertEqual(["3FOF8"], п.поля_фильтра()["ais.callsign"])
        self.assertEqual(1, п.стек.count("AIS"))

    def test_неверная_сумма_не_nmea(self):
        п = разобрать_пакет(udp(ОДНО.replace(b"*5C", b"*5D")))
        self.assertNotIn("NMEA", п.стек)
        п = разобрать_пакет(udp(ОДНО + b"hello\r\n"))
        self.assertNotIn("NMEA", п.стек)
        # Строка без «!»/«$», хоть сумма знаков после первого и сходится, — не NMEA.
        тело = "GPGGA,1,2"
        п = разобрать_пакет(udp(ОДНО + f"X{тело}*{сумма_nmea(тело):02X}\r\n".encode()))
        self.assertNotIn("NMEA", п.стек)

    def test_прочие_предложения(self):
        gga = "GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"
        строка = f"${gga}*{сумма_nmea(gga):02X}\r\n".encode()
        п = разобрать_пакет(udp(строка))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "NMEA"], п.стек)
        self.assertEqual("NMEA 1 предложений: GPGGA", п.инфо)

    def test_канал_ais(self):
        import numpy as np

        from reportgen.potok import ais
        б = ais.из_nmea("15M67FC000G?ufbE`FepT@3n00Sa")
        п = разобрать_пакет(np.packbits(б).tobytes(), "AIS")
        self.assertEqual(["AIS"], п.стек)
        self.assertEqual([366053209], п.поля_фильтра()["ais.mmsi"])
        # Кадр из .Sig («авто»): вид по признакам одного кадра — AIS (у этого типа 18 других
        # совпадений нет; у кадра типа 1 выше первый байт 0x04 сходится и с адресом LAPD).
        б18 = ais.из_nmea("B5NJ;PP005l4ot5Isbl03wsUkP06")
        п = разобрать_пакет(np.packbits(б18).tobytes(), "авто")
        self.assertEqual(["AIS"], п.стек)
        self.assertEqual([367430530], п.поля_фильтра()["ais.mmsi"])


if __name__ == "__main__":
    unittest.main()
