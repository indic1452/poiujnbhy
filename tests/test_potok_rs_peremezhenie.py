"""РС со свёрточным перемежением символов (rs_peremezhenie): перемежитель по документам, профили DSS, ATSC,
J.83B, поиск вслепую, слой стола, автомат.

Эталон — tests/rs_peremezhenie_sintez.py: свой кодер РС на списках, перемежители пошаговой моделью из
документов (регистр с отводами BO.1516 рис. 24b, очереди ветвей Форни), свёрточный 171/133 с выкалыванием
BO.1516 табл. 7b. Модуль снимает перемежение формулой положения — тесты сверяют одно с другим.
"""

import json
import os
import shutil
import subprocess
import time
import unittest

import numpy as np

import _bootstrap  # noqa: F401
import rs_peremezhenie_sintez as С
from reportgen.potok import dvb, kod, razbor, vykalyvanie
from reportgen.potok import rs_peremezhenie as RP

МЕДЛЕННЫЕ = os.environ.get("REPORTGEN_MEDLENNYE") == "1"


def совпало(данные_бит: np.ndarray, исходные: np.ndarray, k: int, младший: bool = False) -> tuple[int, int]:
    """(слов совпало, слов на выходе): выход снятия — данные слов подряд с какого-то слова исходных."""
    дб = np.packbits(данные_бит, bitorder="little" if младший else "big")
    слова = исходные.reshape(-1, k)
    первое = bytes(дб[:k])
    i = next((w for w in range(len(слова)) if bytes(слова[w]) == первое), None)
    if i is None:
        return 0, len(дб) // k
    m = min(len(дб) // k, len(слова) - i)
    return int((дб[:m * k].reshape(-1, k) == слова[i:i + m]).all(axis=1).sum()), len(дб) // k


# -- эталон и перемежитель по документам -----------------------------------------------------------


class ЭталонTests(unittest.TestCase):
    def test_кодер_как_dvb(self):
        F = С.Поле(0x11D)
        г = np.random.default_rng(1)
        for _ in range(3):
            д = г.integers(0, 256, 188)
            self.assertEqual(С.кодировать(д.tolist(), F, 0, 16), dvb.закодировать(д)[0].tolist())

    def test_рэмси_ii_задержка_по_рисунку(self):
        """Регистр с отводами (рис. 24b): байт k пакета выходит через (D − 1)·k = 12·k байт."""
        x = list(range(1, 146 * 40 + 1))
        y = С.рэмси_ii(x)
        for n in range(146 * 5, 146 * 20):
            self.assertEqual(y[n + 12 * (n % 146)], x[n])

    def test_форни_как_dvb(self):
        x = np.random.default_rng(2).integers(0, 256, 204 * 30).astype(np.uint8)
        self.assertEqual(С.форни(x.tolist(), 12, 17), dvb.перемежить(x).tolist())

    def test_формула_модуля_как_модели_документов(self):
        """n → n + d·((n − φ) mod B) — то же, что пошаговые модели: Рэмси II (13, 146), Форни DVB, ATSC, J.83B."""
        г = np.random.default_rng(3)
        for модель, B, d in ((lambda x: С.рэмси_ii(x, 146, 13), 146, 12), (lambda x: С.форни(x, 12, 17), 12, 204),
                             (lambda x: С.форни(x, 52, 4), 52, 208), (lambda x: С.форни(x, 128, 1), 128, 128),
                             (lambda x: С.форни(x, 16, 8), 16, 128), (lambda x: С.рэмси_ii(x, 120, 7), 120, 6)):
            x = г.integers(1, 256, 30_000)
            y = np.array(модель(x.tolist()))
            self.assertTrue(np.array_equal(RP.перемежить(x, B, d), y), (B, d))
            self.assertTrue(np.array_equal(RP.снять(y, B, d), x[:len(x) - d * (B - 1)]), (B, d))
            # Фаза φ: начало ряда — с символа φ коммутатора.
            φ = 37 % B
            self.assertTrue(np.array_equal(RP.снять(RP.перемежить(x, B, d, φ), B, d, φ), x[:len(x) - d * (B - 1)]))

    def test_допустим_и_подписи(self):
        self.assertTrue(RP.допустим(146, 12))
        self.assertFalse(RP.допустим(146, 13))            # НОД(14, 146) = 2 — не взаимно однозначно
        self.assertEqual(RP.подпись(12, 204), "Форни I = 12, M = 17")
        self.assertEqual(RP.подпись(146, 12), "Рэмси II N1 = 13, N2 = 146")
        self.assertEqual((RP.слой_перемежения(52, 208), RP.слой_перемежения(146, 12)), ("форни 52 4", "рэмси 13 146"))

    def test_сумма_слов_это_xor_выровненного_слова(self):
        """A(s + N) ⊕ A(s) = XOR символов слова s + B·q + (d + 1)·j — проверка прямым перебором."""
        г = np.random.default_rng(4)
        y = г.integers(0, 256, 5000)
        for B, d, M_ in ((12, 204, 17), (146, 12, 1), (8, 16, 3)):
            A = RP.сумма_слов(y, B, d)
            N = M_ * B
            for s in (0, 5, 77, 301):
                места = [s + B * q + (d + 1) * j for q in range(M_) for j in range(B)]
                if max(места) >= len(y) or s + N >= len(A):
                    continue
                self.assertEqual(int(A[s + N] ^ A[s]), int(np.bitwise_xor.reduce(y[места])), (B, d, s))


# -- профили ---------------------------------------------------------------------------------------


class ПрофилиTests(unittest.TestCase):
    def проверить_dss(self, **параметры):
        биты, д = С.dss(500, **параметры)
        код = RP.проверить_профиль(биты, RP.профиль("dss"))
        self.assertIsNotNone(код, параметры)
        self.assertEqual((код.N, код.k, код.B, код.d, код.p, код.fcr), (146, 130, 146, 12, 0x11D, 0))
        ок, всего = совпало(код.данные, д, 130, параметры.get("младший", False))
        self.assertGreater(всего, 400)
        return код, ок, всего

    def test_dss_без_ошибок_бит_в_бит(self):
        for параметры in ({}, {"синхро": False}, {"сдвиг": 5}, {"сдвиг": 21, "инверсия": True},
                          {"синхро": False, "инверсия": True, "сдвиг": 3}, {"младший": True, "сдвиг": 2}):
            код, ок, всего = self.проверить_dss(**параметры)
            self.assertEqual(ок, всего, параметры)
            self.assertEqual(код.чистых_до, 1.0, параметры)
            self.assertEqual(код.инверсия, bool(параметры.get("инверсия")), параметры)
            self.assertEqual(код.синхро is not None, параметры.get("синхро", True), параметры)

    def test_dss_ошибки_пачками_исправляются(self):
        for ber in (1e-4, 1e-3):
            for синхро in (True, False):
                код, ок, всего = self.проверить_dss(ber=ber, сдвиг=13, синхро=синхро, инверсия=синхро)
                self.assertEqual(ок, всего, (ber, синхро))
                self.assertGreater(код.исправлено, 0, (ber, синхро))
                self.assertLess(код.чистых_до, 1.0)

    def test_atsc(self):
        for параметры in ({"сдвиг": 3}, {"сдвиг": 3, "инверсия": True, "ber": 1e-3}):
            биты, д = С.atsc(400, **параметры)
            код = RP.проверить_профиль(биты, RP.профиль("atsc"))
            self.assertIsNotNone(код, параметры)
            self.assertEqual((код.N, код.B, код.d), (207, 52, 208))
            ок, всего = совпало(код.данные, д, 187)
            self.assertEqual(ок, всего, параметры)

    def test_j83b_режимы_и_хвост(self):
        """J.83B: GF(128), расширенный символ; основные режимы — без «полно», (128, 4) — только с ним."""
        for I, J, хвост, полно in ((128, 1, False, False), (64, 2, True, False), (16, 8, False, False),
                                   (128, 4, False, True)):
            # Запись — с работающего перемежителя: при (128, 4) первые 65 тыс. символов уходят на задержки.
            биты, д = С.j83b(900 if J * I <= 128 else 1300, I=I, J=J, сдвиг=4, хвост=хвост, ber=1e-4)
            код = RP.проверить_профиль(биты, RP.профиль("j83b"), полно=полно)
            self.assertIsNotNone(код, (I, J))
            self.assertEqual((код.B, код.d, код.m, код.расширенный, bool(код.хвост)), (I, I * J, 7, True, хвост))
            данные = код.данные.reshape(-1, 7)
            символы = (данные * (1 << np.arange(6, -1, -1))).sum(axis=1)
            слова = д.reshape(-1, 122)
            i = next(w for w in range(len(слова)) if np.array_equal(слова[w], символы[:122]))
            m = len(символы) // 122
            self.assertTrue(np.array_equal(символы[:m * 122].reshape(-1, 122), слова[i:i + m]), (I, J))
        биты, _ = С.j83b(1300, I=128, J=4, сдвиг=4)
        self.assertIsNone(RP.проверить_профиль(биты, RP.профиль("j83b"), полно=False))

    def test_профиль_на_чужом_потоке_нет(self):
        случайные = np.random.default_rng(7).integers(0, 2, 1 << 21).astype(np.uint8)
        обычный = С.случайный_rs(146, 130, 1500)
        for пр in RP.ПРОФИЛИ:
            self.assertIsNone(RP.проверить_профиль(случайные, пр, полно=False), пр.ключ)
            self.assertIsNone(RP.проверить_профиль(обычный, пр, полно=False), пр.ключ)


# -- вслепую ---------------------------------------------------------------------------------------


def общий(N, K, вид, a, b, слов=400, *, сдвиг=3, сид=5, ber=0.0, инверсия=False):
    """RS(N, K) 0x11D fcr 0 → Рэмси II (a = N1, b = N2) или Форни (a = I, b = M), записано с работающего перемежителя."""
    г = np.random.default_rng(сид)
    F = С.Поле(0x11D)
    данные = г.integers(0, 256, (слов, K))
    ряд = [x for д in данные for x in С.кодировать(д.tolist(), F, 0, N - K)]
    if вид == "рэмси":
        y = С._прогретый(С.рэмси_ii(ряд, I=b, D=a), (a - 1) * (b - 1), b)
    else:
        y = С._прогретый(С.форни(ряд, a, b), a * b * (a - 1), a)
    return С._линия(С.в_биты(y, 8), г, None, ber, сдвиг, инверсия), данные.astype(np.uint8).reshape(-1)


class ВслепуюTests(unittest.TestCase):
    def найти(self, биты, профиль="обычно"):
        итог = RP.поиск(биты, профиль=профиль, профили=False, срок=120)
        return итог["найдено"][0] if итог["найдено"] else None

    def test_dss_вслепую_без_профиля(self):
        for параметры in ({"синхро": False, "сдвиг": 5}, {"сдвиг": 5, "ber": 1e-3, "инверсия": True}):
            биты, д = С.dss(500, **параметры)
            код = self.найти(биты, "быстро")
            self.assertIsNotNone(код, параметры)
            self.assertEqual((код.N, код.k, код.B, код.d, код.fcr), (146, 130, 146, 12, 0))
            ок, всего = совпало(код.данные, д, 130)
            self.assertEqual(ок, всего, параметры)

    def test_рэмси_и_форни_любых_параметров(self):
        for N, K, вид, a, b, профиль in ((120, 100, "рэмси", 7, 120, "быстро"), (160, 140, "форни", 16, 10, "обычно"),
                                         (204, 188, "форни", 12, 17, "быстро"), (100, 90, "рэмси", 41, 100, "обычно")):
            биты, д = общий(N, K, вид, a, b, ber=1e-4)
            код = self.найти(биты, профиль)
            self.assertIsNotNone(код, (вид, a, b))
            ожидается = (b, a - 1) if вид == "рэмси" else (a, a * b)
            self.assertEqual(((код.N, код.k), (код.B, код.d)), ((N, K), ожидается))
            ок, всего = совпало(код.данные, д, K)
            self.assertEqual(ок, всего, (вид, a, b))

    def test_ложных_нет(self):
        """Случайные биты, обычный РС подряд, блочное чередование глубины 4 — вслепую не находится."""
        from test_potok_rs_slepoy import поток  # noqa: PLC0415
        блочный, _ = поток(204, 188, I=4, блоков=150)
        for имя, биты in (("случайные", np.random.default_rng(9).integers(0, 2, 1 << 20).astype(np.uint8)),
                          ("RS(204,188) подряд", С.случайный_rs(204, 188, 600)), ("блочное I = 4", блочный)):
            начало = time.monotonic()
            self.assertIsNone(self.найти(биты, "быстро"), имя)
            self.assertLess(time.monotonic() - начало, 30, имя)

    def test_план(self):
        быстро, обычно = RP.план("быстро"), RP.план("обычно")
        self.assertEqual((быстро[0].вид, быстро[0].I), ("рэмси", 13))          # DSS — первым
        self.assertTrue(any(ш.вид == "форни" and (ш.B, ш.M) == (12, 17) for ш in быстро))
        self.assertTrue(any(ш.вид == "форни" and (ш.B, ш.M) == (52, 4) for ш in быстро))
        self.assertLess(len(быстро), len(обычно))
        self.assertTrue(all(ш.m == 8 for ш in обычно))
        self.assertTrue(any(ш.m == 7 for ш in RP.план("глубоко")))


# -- слой стола и автомат --------------------------------------------------------------------------


class СлойTests(unittest.TestCase):
    def test_слой_находки_снимает_то_же_и_целиком(self):
        биты, д = С.dss(500, сдвиг=13, ber=1e-3, инверсия=True)
        код = RP.проверить_профиль(биты, RP.профиль("dss"))
        ряд, подробно, свойства = RP.снять_слоем(биты, код.слой())
        self.assertEqual(свойства["слой"], код.слой())
        ок, всего = совпало(ряд, д, 130)
        self.assertEqual(ок, всего)
        for слой in ("рс 146 130 перемежение рэмси 13 146 синхро 0x1D инверсия", "рс перемежение dss",
                     "рс перемежение вслепую быстро"):
            ряд2, _, _ = RP.снять_слоем(биты, слой)
            self.assertTrue(np.array_equal(ряд2, ряд), слой)
        # Через стол (razbor.снять_вручную) и как шаг находки автомата.
        ряд3, находка = razbor.снять_вручную(биты, код.слой())
        self.assertTrue(np.array_equal(ряд3, ряд))
        self.assertEqual(razbor.слои_находки(RP.описать(код)), [код.слой()])

    def test_заданные_фазы_не_те_ищутся_заново(self):
        """Слой с чужими сдвигом и фазой (кусок большого файла) — синхронизируется заново."""
        биты, д = С.dss(500, синхро=False, сдвиг=3)
        ряд, _, _ = RP.снять_слоем(биты[8 * 1000:], "рс 146 130 перемежение рэмси 13 146 сдвиг 3 фаза 0 слово 0")
        ок, всего = совпало(ряд, д, 130)
        self.assertEqual(ок, всего)

    def test_ошибки_слоя(self):
        for слой, кусок in (("рс 146 130 перемежение рэмси 14 146", "не взаимно однозначен"),
                            ("рс 146 130 перемежение", "форни I M"), ("рс 300 280 перемежение форни 12 17", "N ≤ 2^m"),
                            ("рс перемежение dvb-t2", "профиль"), ("рс 146 130 перемежение рэмси 13 146 поле 0x11C", "не примитивный")):
            with self.assertRaises(ValueError, msg=слой) as ош:
                RP.снять_слоем(np.zeros(1 << 16, dtype=np.uint8), слой)
            self.assertIn(кусок, str(ош.exception), слой)

    def test_шаг_как_автомат_рс(self):
        биты, _ = С.dss(800, синхро=False, сдвиг=6)
        итог = razbor.шаг_как_автомат(биты, "рс", профиль="быстро", срок=60)
        self.assertTrue(итог["найдено"], итог.get("подсказка"))
        self.assertTrue(итог["слои"][0].startswith("рс 146 130 перемежение рэмси 13 146"), итог["слои"])


class ЦепочкаTests(unittest.TestCase):
    """DSS под свёрточным кодом: Витерби (как в автомате — kod / vykalyvanie), затем РС с перемежением."""

    def test_свёрточный_затем_рс(self):
        for скорость, ber, найти in (("1/2", 1e-3, lambda б: kod.найти(б, длинные=False)),
                                     ("2/3", 1e-4, lambda б: vykalyvanie.найти(б, бюджет=60)),
                                     ("6/7", 0.0, lambda б: vykalyvanie.найти(б, бюджет=60))):
            биты, д = С.dss(700, скорость=скорость, ber=ber, сдвиг=5)
            код_св = найти(биты[:1 << 20])
            self.assertIsNotNone(код_св, скорость)
            после = np.asarray(код_св.дальше, dtype=np.uint8)
            рс = RP.найти(после, профиль="быстро", вслепую=False)
            self.assertIsNotNone(рс, скорость)
            ок, всего = совпало(рс.дальше, д, 130)
            self.assertGreaterEqual(ок, всего - 1, скорость)       # последнее слово может быть хвостом выборки


@unittest.skipUnless(МЕДЛЕННЫЕ or os.environ.get("REPORTGEN_AVTOMAT_RS") == "1",
                     "автомат целиком (минуты): REPORTGEN_MEDLENNYE=1 или REPORTGEN_AVTOMAT_RS=1")
class АвтоматTests(unittest.TestCase):
    """razbor.разобрать на DSS всех видов: находит РС с перемежением и снимает (данные = исходным)."""

    def разобрать(self, **параметры):
        биты, д = С.dss(2000, **параметры)
        р = razbor.разобрать(данные=np.packbits(биты).tobytes(), профиль="быстро")
        рс = [н for н in р.находки if "свёрточным перемежением" in н.что]
        self.assertTrue(рс, (параметры, [н.что for н in р.находки]))
        ок, всего = совпало(рс[0].дальше, д, 130)
        self.assertGreaterEqual(ок, всего - 1, параметры)
        return р

    def test_после_витерби(self):
        self.разобрать()
        self.разобрать(синхро=False, сдвиг=11, инверсия=True, ber=1e-3)

    def test_под_свёрточным(self):
        self.разобрать(скорость="1/2", ber=1e-3, сдвиг=3)
        self.разобрать(скорость="2/3", ber=1e-4)
        self.разобрать(скорость="6/7")


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ПунктыСтолаTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_potok_sessii import функции_js  # noqa: PLC0415
        код = функции_js([], ["ВКЛАДКИ_КОДОВ", "МОДУЛЯЦИИ_ПОИСКА", "ОПЕРАЦИИ_СТОЛА"])
        код += "process.stdout.write(JSON.stringify({в: ВКЛАДКИ_КОДОВ, о: ОПЕРАЦИИ_СТОЛА}));"
        готово = subprocess.run(["node", "-e", код], capture_output=True, text=True, timeout=20)
        assert готово.returncode == 0, готово.stderr
        д = json.loads(готово.stdout)
        cls.вкладки = {в["имя"]: в for в in д["в"]}
        cls.операции = {о["id"]: о for о in д["о"]}

    def test_пункты_вкладки_rs_снимают(self):
        from test_potok_turbo_std import ОкноКодовTests  # noqa: PLC0415
        о = self.операции
        for ид in ("c-rs-svert", "c-rs-svert-set", "c-rs-auto"):
            self.assertIn(ид, self.вкладки["RS"]["пункты"])
        self.assertEqual((о["c-rs-auto"]["вид"], о["c-rs-auto"]["сделать"]), ("действие", "рс-автомат"))
        биты, д = С.dss(500, сдвиг=2)
        for слой in (ОкноКодовTests.заполнить(о["c-rs-svert"]), ОкноКодовTests.заполнить(о["c-rs-svert-set"])):
            ряд, _ = razbor.снять_вручную(биты, слой)
            ок, всего = совпало(ряд, д, 130)
            self.assertEqual(ок, всего, слой)
        self.assertEqual(ОкноКодовTests.заполнить(о["c-rs-svert-set"], {"вид": "форни", "a": 12, "b": 17, "N": 204,
                                                                         "K": 188, "синхро": ""}),
                         "рс 204 188 перемежение форни 12 17 поле 0x11D fcr 0 символ 8")

    def test_окно_в_коде(self):
        from test_potok_sessii import APP_JS  # noqa: PLC0415
        текст = APP_JS.read_text(encoding="utf-8")
        self.assertIn("case 'рс-автомат': окноРсКакАвтомат(у); return null;", текст)
        окно = текст[текст.index("function окноРсКакАвтомат("):текст.index("function окноПоискаРс(")]
        self.assertIn("какВАвтомате(у, 'рс', {}, место)", окно)


if __name__ == "__main__":
    unittest.main()
