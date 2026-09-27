"""Ethernet 64b/66b (PCS 10GBASE-R): опознавание, скремблер, порядок бит, кадры, FCS.

Синтетика строится здесь же простым независимым кодером: кадры Ethernet с
IPv4/UDP внутри и верной FCS (CRC-32 по своей таблице, не из анализатора),
преамбула, блоки 64b/66b с простоем между кадрами, скремблер 1 + x^39 + x^58
(рекурсия кусками по 39 бит), затем — порядок бит в файле, сдвиг начала,
ошибки.
"""

import struct
import time
import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import pakety, pcs

# -- независимый кодер ---------------------------------------------------------------------

_ТАБЛИЦА_CRC = []
for _байт in range(256):
    _c = _байт
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if _c & 1 else _c >> 1
    _ТАБЛИЦА_CRC.append(_c)


def crc32(данные: bytes) -> int:
    c = 0xFFFFFFFF
    for б in данные:
        c = (c >> 8) ^ _ТАБЛИЦА_CRC[(c ^ б) & 0xFF]
    return c ^ 0xFFFFFFFF


def ip_udp(rng, длина_данных: int) -> bytes:
    тело = rng.integers(0, 256, длина_данных, dtype=np.uint8).tobytes()
    udp = struct.pack("!HHHH", 5000 + int(rng.integers(0, 100)), 53, 8 + len(тело), 0) + тело
    заголовок = bytearray(struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp),
                                      int(rng.integers(0, 65536)), 0x4000, 64, 17, 0,
                                      bytes([10, 0, 0, 1]), bytes([10, 0, 0, 2])))
    сумма = sum(struct.unpack("!10H", bytes(заголовок)))
    while сумма >> 16:
        сумма = (сумма & 0xFFFF) + (сумма >> 16)
    заголовок[10:12] = struct.pack("!H", (~сумма) & 0xFFFF)
    return bytes(заголовок) + udp


def кадры_ethernet(сколько: int, сид: int = 1, наибольшая: int = 600):
    """Кадры без FCS (как их отдаёт анализатор) и с FCS (как в линии)."""
    rng = np.random.default_rng(сид)
    без_fcs, с_fcs = [], []
    for _ in range(сколько):
        кадр = (bytes.fromhex("0200000000aa") + bytes.fromhex("0200000000bb") + b"\x08\x00"
                + ip_udp(rng, int(rng.integers(0, наибольшая))))
        кадр += bytes(max(0, 60 - len(кадр)))                    # дополнение до 64 с FCS
        без_fcs.append(кадр)
        с_fcs.append(кадр + struct.pack("<I", crc32(кадр)))
    return без_fcs, с_fcs


ТЕРМ = (0x87, 0x99, 0xAA, 0xB4, 0xCC, 0xD2, 0xE1, 0xFF)
ПРОСТОЙ = bytes([0x1E]) + bytes(7)
СБОЙ = bytes([0x4B, 0, 0, 1, 0, 0, 0, 0])          # упорядоченный набор «местный сбой»
#: Блок 0x1E: в 3-й позиции символ ошибки /E/ (0x1E, 7 бит), в прочих — простой /I/.
ОШИБКА = bytes([0x1E]) + (0x1E << (7 * 3)).to_bytes(7, "little")


def строки_блоков(кадры, *, сид: int = 2, простоя=(1, 6), терм=ТЕРМ, сбоев: int = 0,
                  ошибочных: int = 0):
    """(заголовки: 1 — данные, 2 — управление; строки по 8 октетов; блоков простоя)."""
    rng = np.random.default_rng(сид)
    заголовки, строки = [2, 2], [ПРОСТОЙ, ПРОСТОЙ]
    простых = 2
    for номер, кадр in enumerate(кадры):
        for _ in range(int(rng.integers(*простоя))):
            заголовки.append(2), строки.append(ПРОСТОЙ)
            простых += 1
        if номер < сбоев:
            заголовки.append(2), строки.append(СБОЙ)
        if номер < ошибочных:
            заголовки.append(2), строки.append(ОШИБКА)
        поток = b"\x55" * 6 + b"\xd5" + кадр
        if rng.random() < 0.5:                       # /S/ в 4-й позиции
            строки.append(bytes([0x33, 0, 0, 0, 0]) + поток[:3])
            поток = поток[3:]
        else:                                        # /S/ в 0-й позиции
            строки.append(bytes([0x78]) + поток[:7])
            поток = поток[7:]
        заголовки.append(2)
        while len(поток) >= 8:
            заголовки.append(1), строки.append(поток[:8])
            поток = поток[8:]
        заголовки.append(2), строки.append(bytes([терм[len(поток)]]) + поток
                                            + bytes(7 - len(поток)))
    заголовки.append(2), строки.append(ПРОСТОЙ)
    простых += 1
    return (np.array(заголовки), np.frombuffer(b"".join(строки), np.uint8).reshape(-1, 8),
            простых)


def скремблировать(x: np.ndarray, отводы, rng) -> np.ndarray:
    """y[n] = x[n] ⊕ y[n−a] ⊕ y[n−b], кусками по min(отводы) бит."""
    с, шаг = max(отводы), min(отводы)
    y = np.zeros(len(x) + с, np.uint8)
    y[:с] = rng.integers(0, 2, с)
    for i in range(с, len(y), шаг):
        j = min(i + шаг, len(y))
        кусок = x[i - с:j - с].copy()
        for t in отводы:
            кусок ^= y[i - t:j - t]
        y[i:j] = кусок
    return y[с:]


def поток_66(кадры, *, отводы=(39, 58), октет="little", в_блоке=False, в_байте=False,
             инверсия=False, сдвиг=0, ошибок=0.0, сид=3, **кв):
    заголовки, строки, простых = строки_блоков(кадры, сид=сид, **кв)
    rng = np.random.default_rng(сид + 100)
    полезная = np.unpackbits(строки, axis=1, bitorder=октет)
    if отводы:
        полезная = скремблировать(полезная.reshape(-1), отводы, rng).reshape(-1, 64)
    синхро = np.stack([заголовки == 2, заголовки == 1], axis=1).astype(np.uint8)  # 01/10
    блоки = np.concatenate([синхро, полезная], axis=1)
    if в_блоке:
        блоки = блоки[:, ::-1]
    биты = блоки.reshape(-1)[сдвиг:]
    if в_байте:
        биты = биты[:len(биты) // 8 * 8].reshape(-1, 8)[:, ::-1].reshape(-1)
    if инверсия:
        биты = биты ^ 1
    if ошибок:
        биты = биты ^ (rng.random(len(биты)) < ошибок).astype(np.uint8)
    return np.ascontiguousarray(биты), простых


# -- проверки ------------------------------------------------------------------------------


class Pcs64b66bTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.кадры, cls.с_fcs = кадры_ethernet(600)
        cls.поток, cls.простых = поток_66(cls.с_fcs, сбоев=5, ошибочных=7)
        cls.р = pcs.разобрать(cls.поток)
        cls.н = pcs.найти(cls.поток)

    def test_опознаёт(self):
        н = self.н
        self.assertIsNotNone(н)
        self.assertEqual(н.уровень, "канальный")
        self.assertIn("64b/66b", н.что)
        self.assertEqual(н.вид_дальше, "кадры")
        self.assertGreaterEqual(н.уверенность, 0.999)
        self.assertGreater(н.сверка, 0.999)
        self.assertIn("FCS верна у 100 % из 600", н.мера)
        р = self.р
        self.assertEqual(р.фаза, 0)
        self.assertEqual((р.порядок_в_файле, р.инверсия, р.порядок_в_октете),
                         ("как передан", False, "младший"))
        self.assertEqual(р.отводы, (39, 58))
        self.assertIn("802.3", р.откуда_полином)
        self.assertLess(р.прочие_фазы, 0.6)

    def test_кадры_совпадают(self):
        self.assertEqual(self.р.кадры, self.кадры)
        self.assertEqual(self.н.дальше, self.кадры)
        self.assertEqual((self.р.собрано, self.р.верных, self.р.с_преамбулой, self.р.оборвано),
                         (600, 600, 600, 0))

    def test_ip_в_кадрах(self):
        внутри = pakety.найти_в_кадрах(self.н.дальше)
        self.assertIsNotNone(внутри)
        self.assertEqual(внутри.уверенность, 1.0)
        self.assertEqual(len(внутри.дальше), 600)
        self.assertEqual({п.обёртка for п in внутри.дальше}, {"Ethernet"})
        self.assertEqual(внутри.дальше[0].получатель, "10.0.0.2")

    def test_типы_простой_наборы(self):
        р = self.р
        for тип in (0x78, 0x33, 0x1E, 0x4B) + ТЕРМ:
            self.assertIn(тип, р.типы, hex(тип))
        # Первый блок уходит на разгон дескремблера — он простой.
        self.assertEqual(р.простой, self.простых - 1)
        self.assertEqual(р.с_ошибкой, 7)                     # и в простой они не попали
        self.assertEqual(р.наборы, {"00 00 01": 5})
        self.assertEqual(р.негодных, 0)
        self.assertEqual(р.блоков, len(self.поток) // 66 - 1)
        for тип, (k, верно, всего) in р.концы.items():
            self.assertEqual(k, ТЕРМ.index(тип))
            self.assertEqual(верно, всего)
        текст = "\n".join(self.н.подробно)
        self.assertIn(f"простой (блоки 0x1E из одних /I/): {self.простых - 1}", текст)
        self.assertIn("0x1E×", текст)
        self.assertIn("как передан", текст)
        self.assertIn("00 00 01×5", текст)

    def test_начало_с_любого_бита(self):
        for сдвиг in (1, 2, 33, 65, 66 * 7 + 29):
            поток, _ = поток_66(self.с_fcs[:150], сдвиг=сдвиг)
            р = pcs.разобрать(поток)
            self.assertIsNotNone(р, сдвиг)
            # Кадр, в середину которого попало начало, теряется — остальные целы.
            self.assertGreaterEqual(len(р.кадры), 149, сдвиг)
            self.assertEqual(р.кадры, self.кадры[150 - len(р.кадры):150], сдвиг)
            self.assertEqual(р.фаза, (-сдвиг) % 66)
        self.assertEqual(len(р.кадры), 149)                  # 7 блоков с лишним — внутри 1-го

    def test_случайный_и_вырожденный_поток(self):
        rng = np.random.default_rng(9)
        self.assertIsNone(pcs.найти(rng.integers(0, 2, 1 << 21).astype(np.uint8)))
        self.assertIsNone(pcs.найти(np.zeros(1 << 20, np.uint8)))
        self.assertIsNone(pcs.найти(np.tile(np.array([0, 1], np.uint8), 1 << 19)))
        self.assertIsNone(pcs.найти(self.поток[:66 * 100]))

    def test_заголовки_без_структуры(self):
        """Заголовки 01/10 есть, а полезная часть случайна — никакой полином не даст типов."""
        rng = np.random.default_rng(4)
        блоки = rng.integers(0, 2, (20000, 66)).astype(np.uint8)
        блоки[:, 1] = 1 - блоки[:, 0]
        начало = time.perf_counter()
        self.assertIsNone(pcs.найти(блоки.reshape(-1)))
        self.assertLess(time.perf_counter() - начало, 15)

    def test_редкие_ошибки(self):
        поток, _ = поток_66(self.с_fcs, ошибок=1e-5, сид=7)
        н = pcs.найти(поток)
        self.assertIsNotNone(н)
        self.assertGreater(н.уверенность, 0.9)
        self.assertGreater(len(н.дальше), 560)
        исходные = set(self.кадры)
        self.assertTrue(all(к in исходные for к in н.дальше))
        self.assertIsNotNone(pakety.найти_в_кадрах(н.дальше))

    def test_обратный_порядок_в_байте(self):
        поток, _ = поток_66(self.с_fcs[:200], в_байте=True, сдвиг=5)
        р = pcs.разобрать(поток)
        self.assertEqual(р.порядок_в_файле, "обратный в байте")
        self.assertEqual(р.кадры, self.кадры[:200])

    def test_обратный_порядок_в_блоке(self):
        поток, _ = поток_66(self.с_fcs[:200], в_блоке=True, сдвиг=11)
        р = pcs.разобрать(поток)
        self.assertEqual(р.порядок_в_файле, "обратный в блоке")
        self.assertEqual(р.кадры, self.кадры[:200])

    def test_обратная_полярность(self):
        поток, _ = поток_66(self.с_fcs[:200], инверсия=True)
        р = pcs.разобрать(поток)
        self.assertTrue(р.инверсия)
        self.assertEqual(р.кадры, self.кадры[:200])
        self.assertIn("полярность обратная", "\n".join(pcs.находка(р).подробно))

    def test_октеты_старшим_битом(self):
        """Порядок в октете по типам не различить — решает FCS."""
        поток, _ = поток_66(self.с_fcs[:200], октет="big")
        р = pcs.разобрать(поток)
        self.assertEqual(р.порядок_в_октете, "старший")
        self.assertEqual(р.кадры, self.кадры[:200])

    def test_другой_полином_находится_перебором(self):
        поток, _ = поток_66(self.с_fcs[:120], отводы=(13, 33))
        р = pcs.разобрать(поток)
        self.assertIsNotNone(р)
        self.assertEqual(р.отводы, (13, 33))
        self.assertIn("перебором", р.откуда_полином)
        self.assertEqual(р.кадры, self.кадры[:120])

    def test_без_скремблера(self):
        поток, _ = поток_66(self.с_fcs[:200], отводы=())
        р = pcs.разобрать(поток)
        self.assertEqual(р.отводы, ())
        self.assertEqual(р.кадры, self.кадры[:200])

    def test_таблица_концов_сверяется_по_fcs(self):
        """Кодер с «переставленными» 0x99 и 0xAA: число октетов решает FCS, а не таблица."""
        терм = (0x87, 0xAA, 0x99) + ТЕРМ[3:]
        поток, _ = поток_66(self.с_fcs[:300], терм=терм)
        р = pcs.разобрать(поток)
        self.assertEqual(р.кадры, self.кадры[:300])
        self.assertEqual(р.концы[0x99][0], 2)
        self.assertEqual(р.концы[0xAA][0], 1)
        self.assertEqual(len(р.поправки), 2)
        self.assertIn("принято по данным", "\n".join(pcs.находка(р).подробно))

    def test_кадры_с_плохой_fcs(self):
        """Кадры с испорченной FCS не уходят дальше и не «поправляют» таблицу концов."""
        # Длина с FCS кратна 4 — конец с 0 или 4 октетами (0x87, 0xCC) при любом начале.
        плохие = [len(к) % 4 == 0 for к in self.с_fcs[:300]]
        испорченные = [к[:-1] + bytes([к[-1] ^ 0xFF]) if п else к
                       for к, п in zip(self.с_fcs[:300], плохие, strict=False)]
        р = pcs.разобрать(поток_66(испорченные)[0])
        self.assertEqual(р.кадры, [к for к, п in zip(self.кадры[:300], плохие, strict=False) if not п])
        self.assertEqual(р.поправки, [])
        self.assertEqual(р.концы[0x87][:2], (0, 0))
        self.assertEqual(р.концы[0xCC][:2], (4, 0))
        self.assertAlmostEqual(pcs.находка(р).уверенность, 1 - sum(плохие) / 300)

    def test_функции_кадра(self):
        тело = self.с_fcs[0]
        self.assertTrue(pcs.fcs_верна(тело))
        self.assertFalse(pcs.fcs_верна(тело[:-1] + bytes([тело[-1] ^ 1])))
        self.assertEqual(pcs.без_преамбулы(pcs.ПРЕАМБУЛА + тело), (тело, True))
        self.assertEqual(pcs.без_преамбулы(b"\x55\x55\xd5" + тело), (тело, False))
        self.assertEqual(pcs.без_преамбулы(тело), (тело, False))

    def test_скорость_8_мбит(self):
        кадры, с_fcs = кадры_ethernet(1400, сид=11, наибольшая=1400)
        поток, _ = поток_66(с_fcs, сид=12)
        self.assertGreater(len(поток), 8_000_000)
        начало = time.perf_counter()
        н = pcs.найти(поток)
        прошло = time.perf_counter() - начало
        self.assertLess(прошло, 8.0)
        self.assertEqual(н.дальше, кадры)


if __name__ == "__main__":
    unittest.main()
