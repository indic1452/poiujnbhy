"""MPEG-TS: секции PSI/SI (PAT, PMT, SDT) и IP в MPE — сборка секций из пакетов и CRC-32.

Потоки собираются по ISO/IEC 13818-1 и EN 300 468 / EN 301 192 (как их
разбирает Wireshark) и разбираются обратно.
"""

import unittest

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import mpeg_ts
from reportgen.potok.dvbs2 import crc32_mpeg2


def секция(tid, тело, *, ид=1, синтаксис=True):
    """Секция с длинным заголовком: table_id, длина, id расширения, версия, номера, тело, CRC-32."""
    заголовок = ид.to_bytes(2, "big") + bytes([0xC1, 0, 0]) + тело
    длина = len(заголовок) + 4
    с_ = bytes([tid, (0xB0 if синтаксис else 0x30) | (длина >> 8), длина & 0xFF]) + заголовок
    return с_ + crc32_mpeg2(с_).to_bytes(4, "big")


def pat(программы):
    return секция(0x00, b"".join(н.to_bytes(2, "big") + (0xE000 | pid).to_bytes(2, "big")
                                for н, pid in программы.items()))


def pmt(программа, потоки, pcr=0x100, info=b"", es_info=b""):
    тело = (0xE000 | pcr).to_bytes(2, "big") + (0xF000 | len(info)).to_bytes(2, "big") + info
    тело += b"".join(bytes([тип]) + (0xE000 | pid).to_bytes(2, "big") + (0xF000 | len(es_info)).to_bytes(2, "big")
                     + es_info for тип, pid in потоки)
    return секция(0x02, тело, ид=программа)


def sdt(службы):
    тело = (1).to_bytes(2, "big") + b"\xff"
    for ид, (тип, поставщик, имя) in службы.items():
        описатель = bytes([тип, len(поставщик)]) + поставщик + bytes([len(имя)]) + имя
        описатель = bytes([0x48, len(описатель)]) + описатель
        тело += ид.to_bytes(2, "big") + b"\xfc" + (0x8000 | len(описатель)).to_bytes(2, "big") + описатель
    return секция(0x42, тело)


def mpe(датаграмма, mac=b"\x01\x00\x5e\x01\x02\x03", скремблирование=0):
    """Секция датаграмм (EN 301 192 7.1): MAC 6,5; флаги; номера; MAC 4..1; датаграмма; CRC-32."""
    тело = bytes([mac[5], mac[4], 0xC1 | (скремблирование << 4), 0, 0, mac[3], mac[2], mac[1], mac[0]]) + датаграмма
    длина = len(тело) + 4
    с_ = bytes([0x3E, 0xB0 | (длина >> 8), длина & 0xFF]) + тело
    return с_ + crc32_mpeg2(с_).to_bytes(4, "big")


class Мультиплекс:
    def __init__(self):
        self.пакеты, self.счётчики = [], {}

    def секции(self, pid, *секции, адаптация=0):
        """Секции подряд в пакетах PID: указатель 0 в первом, набивка 0xFF в последнем;
        ``адаптация`` — байт поля адаптации в каждом пакете (управление полем адаптации 11)."""
        данные = b"".join(секции)
        первый = True
        поле = (bytes([адаптация - 1]) + b"\x00" + b"\xff" * (адаптация - 2)) if адаптация else b""
        while данные or первый:
            место = 184 - len(поле) - (1 if первый else 0)
            кусок, данные = данные[:место], данные[место:]
            нагрузка = (b"\x00" if первый else b"") + кусок
            нагрузка += b"\xff" * (184 - len(поле) - len(нагрузка))
            cc = self.счётчики.get(pid, 0)
            self.счётчики[pid] = (cc + 1) % 16
            afc = 0x30 if адаптация else 0x10
            self.пакеты.append(bytes([0x47, (0x40 if первый else 0) | (pid >> 8), pid & 0xFF, afc | cc]) + поле + нагрузка)
            первый = False
        return self

    def байты(self):
        return b"".join(self.пакеты)


def поток():
    м = Мультиплекс()
    м.секции(0, pat({1: 0x100, 2: 0x200}))
    м.секции(0x100, pmt(1, [(0x1B, 0x101), (0x0F, 0x102)]))
    м.секции(0x200, pmt(2, [(0x0D, 0x300)]))
    м.секции(0x11, sdt({1: (0x19, b"\x01\xbf\xe0\xde\xd1\xd0", b"\x01\xbd\xde\xd2\xde\xe1\xe2\xd8"),
                        2: (0x0C, b"DATA", b"IP")}))
    for н in range(6):
        м.секции(0x300, mpe(с.udp("10.0.0.1", "239.1.2.3", 4000, 5000 + н, bytes([н]) * (150 + 40 * н))))
    return м


class MpegTsTests(unittest.TestCase):
    def test_таблицы_и_службы(self):
        р = mpeg_ts.разобрать(поток().байты())
        self.assertEqual({1: 0x100, 2: 0x200}, р.программы)
        self.assertEqual({1: [(0x1B, 0x101), (0x0F, 0x102)], 2: [(0x0D, 0x300)]}, р.потоки)
        # Кириллица: первый байт 0x01 — ISO 8859-5 (EN 300 468 прил. A).
        self.assertEqual((0x19, "Проба", "Новости"), р.службы[1])
        self.assertEqual((0x0C, "DATA", "IP"), р.службы[2])
        self.assertEqual(0, р.crc_неверно)
        self.assertEqual(0, р.разрывов)

    def test_mpe_до_ip(self):
        р = mpeg_ts.разобрать(поток().байты())
        self.assertEqual([с.udp("10.0.0.1", "239.1.2.3", 4000, 5000 + н, bytes([н]) * (150 + 40 * н))
                          for н in range(6)], р.датаграммы)
        self.assertEqual({"01:00:5e:01:02:03": 6}, dict(р.mac))

    def test_находка(self):
        н = mpeg_ts.находка(поток().байты())
        текст = "\n".join(н.подробно)
        self.assertEqual("MPEG-TS: таблицы PSI/SI и IP в MPE", н.что)
        self.assertIn("программа 1 «Новости» (Проба, ТВ HD H.264): PMT PID 0x0100; PID 0x0101 — видео H.264/AVC; "
                      "PID 0x0102 — звук AAC (ADTS)", текст)
        self.assertIn("программа 2 «IP» (DATA, передача данных): PMT PID 0x0200; PID 0x0300 — DSM-CC тип D", текст)
        self.assertIn("MPE: IP-датаграмм 6", текст)
        self.assertEqual("кадры", н.вид_дальше)

    def test_crc_и_разрывы(self):
        м = поток()
        испорченный = bytearray(м.пакеты[0])
        испорченный[10] ^= 0x01                           # PAT: номер программы с ошибкой
        м.пакеты[0] = bytes(испорченный)
        del м.пакеты[5]                                    # потерян пакет — разрыв счётчика
        р = mpeg_ts.разобрать(м.байты())
        self.assertEqual(1, р.crc_неверно)
        self.assertEqual({}, р.программы)
        self.assertEqual(1, р.разрывов)

    def test_nit_описатели_и_поле_адаптации(self):
        м = Мультиплекс()
        # Программа 0 в PAT — PID сетевой информации (NIT), не программа.
        м.секции(0, pat({0: 0x10, 7: 0x700}), адаптация=8)
        # program_info_length > 255 и описатели у каждого потока.
        м.секции(0x700, pmt(7, [(0x02, 0x701), (0x04, 0x702)], info=b"\x05\x04ABCD" * 45,
                           es_info=b"\x0a\x04rus\x00"), адаптация=5)
        р = mpeg_ts.разобрать(м.байты())
        self.assertEqual({7: 0x700}, р.программы)
        self.assertEqual({7: [(0x02, 0x701), (0x04, 0x702)]}, р.потоки)

    def test_скремблированный_mpe_и_только_mpe(self):
        м = Мультиплекс()
        м.секции(0x300, mpe(b"\x45" + bytes(27), скремблирование=1))
        м.секции(0x300, mpe(с.udp("10.0.0.1", "10.0.0.2", 1, 2, b"x")))
        р = mpeg_ts.разобрать(м.байты())
        self.assertEqual([с.udp("10.0.0.1", "10.0.0.2", 1, 2, b"x")], р.датаграммы)
        # Поток без PAT, но с IP в MPE, — всё равно находка.
        н = mpeg_ts.находка(м.байты())
        self.assertEqual("MPEG-TS: таблицы PSI/SI и IP в MPE", н.что)

    def test_без_таблиц_нет_находки(self):
        пакеты = b"".join(bytes([0x47, 0x01, 0x00, 0x10 | (н & 15)]) + bytes(184) for н in range(50))
        self.assertIsNone(mpeg_ts.находка(пакеты))

    def test_текст_dvb(self):
        self.assertEqual("Мир", mpeg_ts.текст_dvb(b"\x01\xbc\xd8\xe0"))
        self.assertEqual("Мир", mpeg_ts.текст_dvb(b"\x15" + "Мир".encode()))
        self.assertEqual("Мир", mpeg_ts.текст_dvb(b"\x10\x00\x05\xbc\xd8\xe0"))
        self.assertEqual("abc", mpeg_ts.текст_dvb(b"abc"))
        self.assertEqual("", mpeg_ts.текст_dvb(b""))


if __name__ == "__main__":
    unittest.main()
