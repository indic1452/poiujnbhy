"""Каталог CRC против crccheck (MIT, тот же каталог RevEng): параметры, контрольные значения и расчёт
на случайных данных. Запускается, если crccheck установлен (офлайн-комплект разработчика)."""

import inspect
import random
import unittest

import _bootstrap  # noqa: F401
from reportgen.potok import crc, crc_katalog

try:
    import crccheck.crc as эталон
except ImportError:                                   # pragma: no cover — эталон есть только у разработчика
    эталон = None


@unittest.skipIf(эталон is None, "нет crccheck")
class КаталогCrc(unittest.TestCase):
    def test_каталог_и_расчёт_как_у_crccheck(self):
        чужие = {}
        for кл in vars(эталон).values():
            if inspect.isclass(кл) and issubclass(кл, эталон.CrcBase) and getattr(кл, "_width", 0):
                ключ = (кл._width, кл._poly, кл._initvalue, bool(кл._reflect_input), bool(кл._reflect_output),
                        кл._xor_output)
                чужие.setdefault(ключ, кл)
        наши = {(w, P, init, ri, ro, xo): (имя, контроль)
                for имя, w, P, init, ri, ro, xo, контроль in crc_katalog.REVENG}
        self.assertEqual(set(наши), set(чужие))                     # ни модели лишней, ни недостающей
        rng = random.Random(7)
        for ключ, (имя, контроль) in наши.items():
            with self.subTest(имя):
                self.assertEqual(контроль, чужие[ключ]._check_result)
                for _ in range(5):
                    данные = bytes(rng.randrange(256) for _ in range(rng.randrange(40)))
                    self.assertEqual(crc.crc(данные, *ключ), чужие[ключ].calc(данные))


if __name__ == "__main__":
    unittest.main()
