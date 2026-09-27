"""NAVTEX / SITOR-B: таблица CCIR 476 (fldigi navtex.cxx и SDRangel util/navtex.cpp совпадают по буквам;
регистр цифр — международный M.476 как у SDRangel), повтор DX/RX через пять мест, сообщения ZCZC … NNNN."""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import navtex
from reportgen.potok.razbor import разобрать as разобрать_поток

ТЕКСТ = "ZCZC EA42\nGALE WARNING 12/03 WIND SW 8, 50-30N 001-15W.\nNNNN\n"


def поток(биты, сид=1, до=333, после=500):
    г = np.random.default_rng(сид)
    return np.concatenate([г.integers(0, 2, до), биты, г.integers(0, 2, после)]).astype(np.uint8)


def место(i):
    """Бит начала знакового места i передачи ``закодировать`` внутри ``поток``: 333 бита спереди."""
    return 333 + 7 * i


class ТаблицаTests(unittest.TestCase):
    def test_все_коды_с_четырьмя_единицами(self):
        """35 = C(7, 4) кодов: 29 знаков и 6 служебных; остальные 93 — ошибки."""
        коды = set(navtex.КОДЫ) | {navtex.LTRS, navtex.FIGS, navtex.АЛЬФА, navtex.БЕТА, navtex.ПОВТОР,
                                   navtex.ЗНАК32}
        self.assertEqual(35, len(коды))
        self.assertEqual({к for к in range(128) if bin(к).count("1") == 4}, коды)

    def test_буквы_и_цифры(self):
        буквы = sorted(б for б, _ in navtex.КОДЫ.values() if б.isalpha())
        self.assertEqual([chr(c) for c in range(ord("A"), ord("Z") + 1)], буквы)
        # цифры — на верхнем ряду клавиатуры, как в МТК-2: Q = 1 … P = 0
        верхний = {б: ц for б, ц in navtex.КОДЫ.values()}
        self.assertEqual("1234567890", "".join(верхний[б] for б in "QWERTYUIOP"))

    def test_коды_по_m476(self):
        """A = B B B Y Y Y B (младший бит — первым); регистр цифр — международный."""
        a = [к for к, (б, _) in navtex.КОДЫ.items() if б == "A"][0]
        self.assertEqual([1, 1, 1, 0, 0, 0, 1], [(a >> i) & 1 for i in range(7)])
        цифры = {б: ц for б, ц in navtex.КОДЫ.values()}
        self.assertEqual(("\x07", "\x05", "'", "+", "="), tuple(цифры[б] for б in "JDSZV"))


class РазборTests(unittest.TestCase):
    def test_сообщение(self):
        п = navtex.передачи(поток(navtex.закодировать(ТЕКСТ)))
        self.assertEqual(1, len(п))
        self.assertEqual((333, False, ТЕКСТ, 0, 0), (п[0].бит, п[0].инверсия, п[0].текст, п[0].исправлено,
                                                     п[0].потеряно))
        self.assertEqual([{"станция": "E", "вид": "A", "номер": "42", "полное": True,
                           "текст": "GALE WARNING 12/03 WIND SW 8, 50-30N 001-15W."}], п[0].сообщения)

    def test_повтор_заменяет_испорченный_знак(self):
        б = поток(navtex.закодировать(ТЕКСТ))
        б[место(40) + 1] ^= 1                              # DX знака на месте 40 — вес 3 или 5
        п = navtex.передачи(б)[0]
        self.assertEqual((ТЕКСТ, 1, 0), (п.текст, п.исправлено, п.потеряно))

    def test_границы_участка(self):
        """Вокруг передачи нули (негодные коды): участок — с первого знака до конца последнего окна из 14
        с 10 годными, то есть ещё 4 нулевых кода после передачи."""
        б = navtex.закодировать(ТЕКСТ)
        п = navtex.передачи(np.concatenate([np.zeros(70, np.uint8), б, np.zeros(700, np.uint8)]))[0]
        self.assertEqual((70, len(б) // 7 + 4), (п.бит, len(п.коды)))

    def test_мера_считает_только_годные(self):
        б = поток(navtex.закодировать(ТЕКСТ))
        б[место(40) + 1] ^= 1
        п = navtex.передачи(б)[0]
        годных = sum(navtex.годный(к) for к in п.коды)
        self.assertLess(годных, len(п.коды))
        self.assertTrue(navtex.найти(б).мера.startswith(f"{годных} знаков с 4 единицами из 7"))

    def test_испорченный_повтор_не_мешает(self):
        б = поток(navtex.закодировать(ТЕКСТ))
        б[место(45) + 1] ^= 1                              # RX знака с DX на месте 40
        п = navtex.передачи(б)[0]
        self.assertEqual((ТЕКСТ, 0, 0), (п.текст, п.исправлено, п.потеряно))

    def test_испорчены_оба(self):
        б = поток(navtex.закодировать(ТЕКСТ))
        б[[место(40) + 1, место(45) + 2]] ^= 1
        п = navtex.передачи(б)[0]
        self.assertEqual(1, п.потеряно)
        self.assertEqual(1, п.текст.count("*"))
        self.assertEqual(len(ТЕКСТ), len(п.текст))

    def test_обратная_полярность(self):
        п = navtex.передачи(1 - поток(navtex.закодировать(ТЕКСТ)))
        self.assertEqual((True, ТЕКСТ), (п[0].инверсия, п[0].текст))
        self.assertIn("полярность обратная", navtex.найти(1 - поток(navtex.закодировать(ТЕКСТ))).подробно[0])

    def test_полярность_у_каждой_передачи_своя(self):
        прямо = поток(navtex.закодировать(ТЕКСТ))
        н = navtex.найти(np.concatenate([прямо, 1 - поток(navtex.закодировать(ТЕКСТ), сид=2)]))
        self.assertEqual(2, н.свойства["передач"])
        self.assertNotIn("полярность", н.подробно[0])

    def test_любой_битовый_сдвиг(self):
        for до in (330, 331, 336, 340):
            with self.subTest(до=до):
                п = navtex.передачи(поток(navtex.закодировать(ТЕКСТ), до=до))
                self.assertEqual(ТЕКСТ, п[0].текст)
                self.assertEqual(0, (до - п[0].бит) % 7)          # начало участка может захватить
                self.assertLessEqual(0, до - п[0].бит)            # случайно годные знаки шума

    def test_шум_перед_передачей_не_печатается(self):
        """Годные по случайности знаки перед фазированием и без него — не текст."""
        for фазирование in (8, 0):
            for сид in range(1, 30):
                with self.subTest(сид=сид, фазирование=фазирование):
                    п = navtex.передачи(поток(navtex.закодировать(ТЕКСТ, фазирование=фазирование), сид=сид))
                    self.assertEqual(ТЕКСТ, п[0].текст)

    def test_места_dx_по_повтору(self):
        """Без фазирования и с лишним знаком спереди места DX — нечётные: их выдаёт совпадение через 5."""
        б = navtex.закодировать(ТЕКСТ, фазирование=0)
        лишний = np.array([(navtex.АЛЬФА >> i) & 1 for i in range(7)], dtype=np.uint8)
        for биты in (б, np.concatenate([лишний, б])):
            with self.subTest(длина=len(биты)):
                self.assertEqual(ТЕКСТ, navtex.передачи(поток(биты))[0].текст)

    def test_конец_передачи_по_двум_альфа(self):
        """α и в DX, и в RX — конец: знаки после него не читаются."""
        б = np.concatenate([navtex.закодировать("ZCZC EA42\nFIRST\nNNNN\n"),
                            navtex.закодировать("SECOND", фазирование=0)])
        п = navtex.передачи(поток(б))[0]
        self.assertEqual("ZCZC EA42\nFIRST\nNNNN\n", п.текст)

    def test_замирание_на_четыре_места(self):
        """Четыре места подряд испорчены (280 мс): передача не рвётся, повторы через 5 мест всё чинят."""
        б = поток(navtex.закодировать(ТЕКСТ))
        for i in range(40, 44):
            б[место(i) + 1] ^= 1
        п = navtex.передачи(б)
        self.assertEqual([(ТЕКСТ, 2, 0)], [(x.текст, x.исправлено, x.потеряно) for x in п])

    def test_оборванная_запись(self):
        """Запись оборвана на месте 80: повторы мест 21 + 2k < 80 — знаки k ≤ 29 (FIGS и LTRS — тоже знаки);
        последние знаки без повтора не подтвердить, шум за обрывом — не текст. (При сиде 1 шум на месте 83
        случайно совпадает со знаком — вероятность 1/128 — поэтому сиды со 2-го.)"""
        for сид in range(2, 16):
            with self.subTest(сид=сид):
                п = navtex.передачи(поток(navtex.закодировать(ТЕКСТ)[:7 * 80], сид=сид))[0]
                self.assertEqual("ZCZC EA42\nGALE WARNING 12/0", п.текст)

    def test_регистры(self):
        п = navtex.передачи(поток(navtex.закодировать("ZCZC OB07\nA1B2 C3:D4?\nNNNN\n")))[0]
        self.assertEqual("ZCZC OB07\nA1B2 C3:D4?\nNNNN\n", п.текст)

    def test_служебные_знаки_пропускаются(self):
        """β, знак 32, α и фазирующий 2 внутри текста не печатаются; звонок и «кто там?» (J, D в цифрах) — тоже."""
        dx = [navtex.LTRS, 0x47, navtex.БЕТА, navtex.ЗНАК32, navtex.ПОВТОР, navtex.FIGS, 0x17, 0x53, 0x2E,
              navtex.LTRS, 0x72]
        места = []
        for i in range(len(dx) + 3):                        # повтор знака i — во второй половине пары i + 2
            места += [dx[i] if i < len(dx) else navtex.АЛЬФА, dx[i - 2] if 2 <= i < len(dx) + 2 else navtex.АЛЬФА]
        текст, исправлено, потеряно, повторов = navtex.разобрать(места)
        self.assertEqual(("A1B", 0, 0), (текст, исправлено, потеряно))

    def test_сообщение_без_конца(self):
        п = navtex.передачи(поток(navtex.закодировать("ZCZC KC99\nICE REPORT")))[0]
        self.assertEqual([{"станция": "K", "вид": "C", "номер": "99", "текст": "ICE REPORT", "полное": False}],
                         п.сообщения)
        н = navtex.найти(поток(navtex.закодировать("ZCZC KC99\nICE REPORT")))
        self.assertIn("KC99 — ледовая обстановка (без NNNN): ICE REPORT", н.подробно)

    def test_два_сообщения_и_сводка(self):
        б = np.concatenate([поток(navtex.закодировать(ТЕКСТ)),
                            поток(navtex.закодировать("ZCZC EB01\nSTORM\nNNNN\n"), сид=2),
                            поток(navtex.закодировать("ZCZC SA11\nBUOY ADRIFT\nNNNN\n"), сид=3)])
        н = navtex.найти(б)
        self.assertEqual({"передач": 3, "сообщений": 3, "станций": 2}, н.свойства)
        self.assertIn("станции: E × 2, S × 1", н.подробно)
        self.assertIn("виды: A (навигационное предупреждение) × 2, B (метеорологическое предупреждение) × 1",
                      н.подробно)
        self.assertEqual(1.0, н.уверенность)


class НеNavtexTests(unittest.TestCase):
    def test_случайные_биты(self):
        self.assertIsNone(navtex.найти(np.random.default_rng(5).integers(0, 2, 200_000).astype(np.uint8)))

    def test_чередование(self):
        """1010… даёт годный знак «R» при каждом сдвиге кратном 7 — одна буква, не передача."""
        self.assertIsNone(navtex.найти(np.resize(np.array([1, 0], np.uint8), 100_000)))

    def test_без_повтора(self):
        """Годные знаки CCIR 476 без повтора через 5 мест (как в режиме A) — не режим B."""
        г = np.random.default_rng(7)
        коды = г.choice(sorted(navtex.КОДЫ), 300)
        биты = np.array([(int(к) >> b) & 1 for к in коды for b in range(7)], dtype=np.uint8)
        self.assertIsNone(navtex.найти(поток(биты)))

    def test_короткий_участок(self):
        """30 знаковых мест (меньше 40) — мало для передачи; 36 с шумом по краям — уже передача."""
        коротко = navtex.закодировать("ZCZC EA42", фазирование=0)
        self.assertEqual(30, len(коротко) // 7)
        self.assertIsNone(navtex.найти(поток(коротко)))
        self.assertIsNotNone(navtex.найти(поток(navtex.закодировать("ZCZC EA42 X", фазирование=0))))

    def test_мало_разных_знаков(self):
        """Повтор пяти знаков (или одного, как в простое 1110100…) — не передача; шести — передача."""
        коды = [0x47, 0x72, 0x1D, 0x53, 0x56]                 # A B C D E
        биты = np.array([(коды[0] >> b) & 1 for _ in range(200) for b in range(7)], dtype=np.uint8)
        self.assertIsNone(navtex.найти(поток(биты)))
        self.assertIsNone(navtex.найти(поток(navtex.закодировать("ABCDE" * 8))))
        self.assertIsNotNone(navtex.найти(поток(navtex.закодировать("ABCDEF" * 7))))

    def test_текст_без_сообщения(self):
        н = navtex.найти(поток(navtex.закодировать("SOME TEXT WITHOUT HEADER 12345")))
        self.assertEqual(0.9, н.уверенность)
        self.assertIn("SOME TEXT WITHOUT HEADER 12345", н.подробно[-1])


class АвтоматTests(unittest.TestCase):
    def test_автомат(self):
        б = поток(navtex.закодировать(ТЕКСТ))
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        self.assertTrue(any(н.что.startswith("NAVTEX") for н in р.находки), [н.что for н in р.находки])


if __name__ == "__main__":
    unittest.main()
