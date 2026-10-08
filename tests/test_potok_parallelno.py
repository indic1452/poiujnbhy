"""Параллельная работа анализа потоков: пул исполнителей, честная очередь, отмена, ход из процесса,
восстановление после перезапуска, одновременная запись (docs/20-potok.md, «Параллельная работа»)."""

import json
import os
import tempfile
import threading
import time
import unittest
from functools import partial
from pathlib import Path

import _bootstrap  # noqa: F401
import ispolniteli_zadachi as з
from reportgen.potok import ispolniteli as и
from reportgen.potok import zadaniya
from reportgen.potok.zadaniya import Задания


def дождаться(условие, срок=60.0, шаг=0.02):
    конец = time.monotonic() + срок
    while time.monotonic() < конец:
        if условие():
            return True
        time.sleep(шаг)
    return False


class НастройкиTests(unittest.TestCase):
    def test_по_умолчанию_и_из_окружения(self):
        старое = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(старое)))
        for имя in (и.ПЕРЕМЕННАЯ_РАЗБОРОВ, и.ПЕРЕМЕННАЯ_СТОЛА, и.ПЕРЕМЕННАЯ_НА_ЧЕЛОВЕКА, и.ПЕРЕМЕННАЯ_ПРОЦЕССОВ):
            os.environ.pop(имя, None)
        self.assertEqual(и.ядер(), и.размер_разборов())
        self.assertEqual(min(16, max(4, и.ядер())), и.размер_стола())
        self.assertEqual(3, и.размер_разборов(3))
        self.assertEqual(5, и.размер_стола(5))
        self.assertTrue(и.процессами())
        os.environ.update({и.ПЕРЕМЕННАЯ_РАЗБОРОВ: "7", и.ПЕРЕМЕННАЯ_СТОЛА: "9", и.ПЕРЕМЕННАЯ_ПРОЦЕССОВ: "0"})
        self.assertEqual((7, 9, False), (и.размер_разборов(), и.размер_стола(), и.процессами()))
        self.assertEqual(2, и.размер_разборов(2))                 # настройка важнее переменной
        self.assertTrue(и.процессами(True))
        os.environ[и.ПЕРЕМЕННАЯ_РАЗБОРОВ] = "много"
        self.assertEqual(и.ядер(), и.размер_разборов())           # непонятное — как не задано
        # На человека: при трёх исполнителях и больше один остаётся другим.
        self.assertEqual([1, 2, 2, 3, 7], [и.на_человека(р) for р in (1, 2, 3, 4, 8)])
        self.assertEqual([1, 2, 4], [и.на_человека(4, з_) for з_ in (1, 2, 9)])
        os.environ[и.ПЕРЕМЕННАЯ_НА_ЧЕЛОВЕКА] = "1"
        self.assertEqual(1, и.на_человека(8))


class ОчередьTests(unittest.TestCase):
    """Правило очереди — на нитях: порядок виден точно, без времени запуска процессов."""

    def setUp(self):
        self.пул = и.Пул("проверка-очереди", 1, процессы=False)
        self.addCleanup(self.пул.закрыть)
        self.порядок = []
        self.ворота = threading.Event()

    def задача(self, имя):
        def работа():
            self.ворота.wait(10)
            self.порядок.append(имя)
            return имя
        return работа

    def test_человек_с_двадцатью_файлами_не_задерживает_других(self):
        а = [self.пул.подать(self.задача(f"А{i}"), владелец="А") for i in range(6)]
        self.assertTrue(дождаться(lambda: а[0].состояние == "идёт"))
        б = self.пул.подать(self.задача("Б0"), владелец="Б")
        в = self.пул.подать(self.задача("В0"), владелец="В")
        б1 = self.пул.подать(self.задача("Б1"), владелец="Б")
        # Пока идёт А0: первыми — Б и В (их ещё не обслуживали), затем по кругу.
        self.assertEqual([0, 1, 2, 3], [self.пул.перед(x) for x in (б, в, а[1], б1)])
        self.assertEqual(0, self.пул.перед(а[0]))                # идущая — ни перед кем
        self.ворота.set()
        for x in а + [б, в, б1]:
            x.ждать(10)
        self.assertEqual(["А0", "Б0", "В0", "А1", "Б1", "А2", "А3", "А4", "А5"], self.порядок)

    def test_предел_на_владельца(self):
        пул = и.Пул("проверка-предела", 3, процессы=False, на_владельца=2)
        self.addCleanup(пул.закрыть)
        а = [пул.подать(self.задача(f"А{i}"), владелец="А") for i in range(3)]
        self.assertTrue(дождаться(lambda: sum(x.состояние == "идёт" for x in а) == 2))
        time.sleep(0.1)
        self.assertEqual("ждёт", а[2].состояние)                 # третье место свободно — но не для А
        б = пул.подать(self.задача("Б"), владелец="Б")
        self.assertTrue(дождаться(lambda: б.состояние == "идёт"))
        self.assertEqual({"А": {"ждут": 1, "идут": 2}, "Б": {"ждут": 0, "идут": 1}}, пул.сведения()["по_людям"])
        self.ворота.set()
        self.assertEqual(["А0", "А1", "А2", "Б"], sorted(x.ждать(10) for x in а + [б]))
        пул.на_владельца = None
        self.assertEqual(3, пул.на_владельца)

    def test_первой_и_сверх_предела(self):
        """«Первой» — в начало своей очереди (чужих не обгоняет); «сверх» — одна сверх предела человека, на свободном
        исполнителе (сшивка и разметка пачек: от них ждут остальные задачи человека)."""
        пул = и.Пул("проверка-сверх", 4, процессы=False, на_владельца=2)
        self.addCleanup(пул.закрыть)
        а = [пул.подать(self.задача(f"А{i}"), владелец="А") for i in range(3)]
        self.assertTrue(дождаться(lambda: sum(x.состояние == "идёт" for x in а) == 2))
        первая = пул.подать(self.задача("А-первая"), владелец="А", первой=True)
        self.assertEqual(0, пул.перед(первая))
        time.sleep(0.1)
        self.assertEqual("ждёт", первая.состояние, "на пределе — ждёт, хоть и первая")
        сверх = пул.подать(self.задача("А-сверх"), владелец="А", первой=True, сверх=True)
        self.assertTrue(дождаться(lambda: сверх.состояние == "идёт"), "сверх предела — на свободном исполнителе")
        ещё = пул.подать(self.задача("А-сверх-2"), владелец="А", первой=True, сверх=True)
        time.sleep(0.1)
        self.assertEqual("ждёт", ещё.состояние, "сверх — не больше одной")
        б = пул.подать(self.задача("Б"), владелец="Б")
        self.assertTrue(дождаться(lambda: б.состояние == "идёт"), "четвёртый исполнитель — другому человеку")
        self.ворота.set()
        for x in [*а, первая, сверх, ещё, б]:
            x.ждать(10)
        self.assertLessEqual(первая.начата, а[2].начата, "первая — раньше прежде поданной")

    def test_снять_атомарно_и_отменить_ждущую(self):
        а = self.пул.подать(self.задача("А"), владелец=1)
        self.assertTrue(дождаться(lambda: а.состояние == "идёт"))
        б = self.пул.подать(self.задача("Б"), владелец=1)
        в = self.пул.подать(self.задача("В"), владелец=2)
        self.assertFalse(self.пул.снять([а, б]))                 # А идёт — ничего не тронуто
        self.assertEqual("ждёт", б.состояние)
        self.assertTrue(self.пул.снять([б]))
        self.assertEqual("отменено", б.состояние)
        with self.assertRaises(и.Отменено):
            б.ждать(1)
        self.assertEqual("снята", self.пул.отменить(в))
        self.assertEqual("", self.пул.отменить(в))               # уже закончена
        self.assertTrue(self.пул.снять([]))
        self.ворота.set()
        self.assertEqual("А", а.ждать(10))
        self.assertEqual(["А"], self.порядок)

    def test_отмена_идущей_в_нити_и_ход(self):
        ход = []
        задача = self.пул.подать(з.вечно_с_ходом, при_сообщении=lambda вид, д: ход.append((вид, д)))
        self.assertTrue(дождаться(lambda: len(ход) >= 3))
        self.assertEqual("отменяется", self.пул.отменить(задача))
        with self.assertRaises(и.Отменено):
            задача.ждать(10)
        self.assertEqual(("ход", "ещё"), ход[0])

    def test_размер_меняется_и_закрытие(self):
        self.пул.размер = 0
        self.assertEqual(1, self.пул.размер)
        ждёт = self.пул.подать(self.задача("x"))
        ждёт2 = self.пул.подать(self.задача("y"))
        self.пул.закрыть()
        self.ворота.set()
        with self.assertRaises(и.Отменено):
            ждёт2.ждать(5)
        self.assertIn(ждёт.состояние, ("отменено",))
        with self.assertRaises(и.ОшибкаИсполнителя):
            self.пул.подать(self.задача("z"))

    def test_обработчики_не_роняют_пул(self):
        def плохой(*_):
            raise RuntimeError("обработчик")
        with self.assertLogs("reportgen.potok", "ERROR") as журнал:
            задача = self.пул.подать(з.с_ходом, 3, при_сообщении=плохой, при_конце=плохой)
            self.assertEqual(3, задача.ждать(10))
            self.assertTrue(дождаться(lambda: len(журнал.records) == 4, 5))
        self.assertEqual(5, self.пул.выполнить(з.сложить, 2, 3))


class ПроцессыTests(unittest.TestCase):
    """Настоящие процессы (способ spawn — как на Windows)."""

    @classmethod
    def setUpClass(cls):
        cls.пул = и.Пул("проверка-процессов", 2, процессы=True)
        cls.пул.выполнить(з.сложить, 1, 1)                      # первый исполнитель поднят

    @classmethod
    def tearDownClass(cls):
        cls.пул.закрыть()

    def test_в_другом_процессе_параллельно(self):
        начало = time.monotonic()
        задачи = [self.пул.подать(з.спать, 1.0, i, владелец=i) for i in range(4)]
        self.assertEqual([0, 1, 2, 3], [x.ждать(30) for x in задачи])
        прошло = time.monotonic() - начало
        self.assertLess(прошло, 3.6)                              # по две сразу, а не четыре подряд
        пиды = {self.пул.выполнить(з.pid) for _ in range(4)}
        self.assertNotIn(os.getpid(), пиды)

    def test_ход_из_процесса_по_порядку(self):
        ход = []
        self.assertEqual(20, self.пул.выполнить(з.с_ходом, 20, при_сообщении=lambda вид, д: ход.append(д)))
        self.assertEqual([f"шаг {i}" for i in range(20)], ход)

    def test_ошибки_передаются(self):
        with self.assertRaisesRegex(ValueError, "^ой$"):
            self.пул.выполнить(з.упасть, "ой")
        with self.assertRaisesRegex(и.ОшибкаИсполнителя, "Местная: местная ошибка"):
            self.пул.выполнить(з.упасть_странно)
        with self.assertRaisesRegex(и.ОшибкаИсполнителя, "итог задачи не передать"):
            self.пул.выполнить(з.непередаваемый_итог)
        with self.assertRaisesRegex(и.ОшибкаИсполнителя, "задачу не передать"):
            self.пул.выполнить(lambda: 1)
        with self.assertRaisesRegex(и.ОшибкаИсполнителя, r"аварийно \(код 7\)"):
            self.пул.выполнить(з.выйти, 7)
        self.assertEqual(5, self.пул.выполнить(з.сложить, 2, 3))   # пул жив, упавший заменён

    def test_отмена_и_срок(self):
        задача = self.пул.подать(з.вечно_с_ходом)
        self.assertTrue(дождаться(lambda: задача.состояние == "идёт"))
        time.sleep(0.3)
        self.пул.отменить(задача)
        начало = time.monotonic()
        with self.assertRaises(и.Отменено):
            задача.ждать(10)
        self.assertLess(time.monotonic() - начало, 1.0)            # отозвалась на ходу
        # Широкий except Exception в счёте отмену не глотает.
        задача = self.пул.подать(з.глотает_исключения)
        time.sleep(0.5)
        self.пул.отменить(задача)
        with self.assertRaises(и.Отменено):
            задача.ждать(10)
        # Глухая задача: процесс снимается через ЖДАТЬ_ОТМЕНУ.
        старое = и.ЖДАТЬ_ОТМЕНУ
        и.ЖДАТЬ_ОТМЕНУ = 0.5
        self.addCleanup(setattr, и, "ЖДАТЬ_ОТМЕНУ", старое)
        задача = self.пул.подать(з.вечно_глухо)
        self.assertTrue(дождаться(lambda: задача.состояние == "идёт"))
        self.пул.отменить(задача)
        начало = time.monotonic()
        with self.assertRaises(и.Отменено):
            задача.ждать(10)
        self.assertLess(time.monotonic() - начало, 3.0)
        with self.assertRaisesRegex(и.СрокВышел, "не уложилась в 0.5 с"):
            self.пул.выполнить(з.спать, 30, срок=0.5)
        with self.assertRaises(TimeoutError):
            self.пул.выполнить(з.спать, 30, ждать=0.3)
        self.assertEqual(5, self.пул.выполнить(з.сложить, 2, 3))

    def test_простой_гасит_исполнителей(self):
        пул = и.Пул("проверка-простоя", 1, процессы=True, простой=0.3)
        self.addCleanup(пул.закрыть)
        self.assertEqual(3, пул.выполнить(з.сложить, 1, 2))
        self.assertTrue(дождаться(lambda: пул.сведения()["исполнителей"] == 0, 10))
        self.assertEqual(7, пул.выполнить(з.сложить, 3, 4))      # и снова поднимается

    def test_общий_пул_один_на_имя(self):
        а = и.общий("проверка-общего", 2, процессы=False)
        self.addCleanup(а.закрыть)
        б = и.общий("проверка-общего", 3, процессы=False, на_владельца=1)
        self.assertIs(а, б)
        self.assertEqual((3, 1), (а.размер, а.на_владельца))
        в = и.общий("проверка-общего", 3, процессы=True)          # другой режим — новый пул
        self.addCleanup(в.закрыть)
        self.assertIsNot(а, в)


class Медленные(Задания):
    """Задания, у которых «разбор» — предсказуемая долгая задача в процессе."""

    выполнить = staticmethod(partial(з.медленное_задание, сек=1.5))


class ЗаданияПараллельноTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.папка = Path(self._tmp.name) / "potok"

    def готово(self, задания, ид, срок=60):
        self.assertTrue(дождаться(lambda: задания.прочитать(ид)["состояние"] in zadaniya.ЗАКОНЧЕНО
                                  and ид not in задания.идущие(), срок), задания.прочитать(ид))
        return задания.прочитать(ид)

    def test_разборы_идут_параллельно_ход_виден(self):
        задания = Медленные(self.папка, разборов=3, на_человека=3)
        начало = time.monotonic()
        иды = [задания.создать(владелец=1 + i % 2, имя=f"{i}.bin", данные=b"\x00" * 16) for i in range(3)]
        # Ход из процесса виден в журнале, пока задание идёт.
        self.assertTrue(дождаться(lambda: any(задания.прочитать(и_)["состояние"] == "идёт"
                                              and задания.прочитать(и_)["журнал"] for и_ in иды), 30))
        состояния = [self.готово(задания, и_) for и_ in иды]
        self.assertEqual(["готово"] * 3, [с["состояние"] for с in состояния])
        self.assertEqual(3, len({с["pid"] for с in состояния}))     # три разных процесса
        self.assertNotIn(os.getpid(), {с["pid"] for с in состояния})
        self.assertLess(time.monotonic() - начало, 1.5 * 3)           # не друг за другом
        self.assertTrue(all("медленно 0" in " ".join(с["журнал"]) for с in состояния))

    def test_остановить_автомат_в_процессе(self):
        # Настоящий автомат в процессе-исполнителе: «Остановить» кладёт метку, разбор кончается с найденным
        # (как по исчерпанию времени) — «готово», а не «ошибка» и не снятый процесс.
        import numpy as np
        задания = Задания(self.папка, разборов=1)
        данные = np.random.default_rng(3).integers(0, 256, 200_000, dtype=np.uint8).tobytes()
        ид = задания.создать(владелец=1, имя="шум.bin", данные=данные, профиль="глубоко")
        self.assertFalse(задания.остановить("нет-такого"))
        self.assertTrue(дождаться(lambda: ид in задания.идущие(), 60))
        time.sleep(2)
        начало = time.monotonic()
        self.assertTrue(задания.остановить(ид))
        состояние = self.готово(задания, ид, срок=240)
        self.assertEqual("готово", состояние["состояние"])
        self.assertLess(time.monotonic() - начало, 240)
        self.assertIn("остановлено оператором", " ".join(состояние["журнал"]))
        self.assertFalse((self.папка / ид / zadaniya.СТОП_ФАЙЛ).exists())     # метка не остаётся
        self.assertFalse(задания.остановить(ид))                               # уже готово

    def test_метка_стопа_исчерпывает_бюджет(self):
        from reportgen.potok import razbor
        стоп = Path(self._tmp.name) / "стоп"
        б = razbor.Бюджет(конец=time.monotonic() + 600, профиль=razbor.ПРОФИЛИ["обычно"])
        было = razbor._ТЕКУЩИЙ.бюджет
        razbor._ТЕКУЩИЙ.бюджет = б
        self.addCleanup(setattr, razbor._ТЕКУЩИЙ, "бюджет", было)
        готово = threading.Event()
        нить = threading.Thread(target=zadaniya._ждать_стопа, args=(стоп, готово), daemon=True)
        нить.start()
        time.sleep(zadaniya.СТОП_КАЖДЫЕ * 2)
        self.assertFalse(б.вышел())                    # метки нет — разбор идёт
        стоп.write_bytes(b"1")
        нить.join(5)
        self.assertFalse(нить.is_alive())
        self.assertTrue(б.вышел())
        self.assertEqual(0.0, б.конец)
        # Без метки, но разбор кончился — нить выходит и бюджет не трогает.
        б2 = razbor.Бюджет(конец=time.monotonic() + 600, профиль=razbor.ПРОФИЛИ["обычно"])
        razbor._ТЕКУЩИЙ.бюджет = б2
        стоп.unlink()
        готово2 = threading.Event()
        нить2 = threading.Thread(target=zadaniya._ждать_стопа, args=(стоп, готово2), daemon=True)
        нить2.start()
        готово2.set()
        нить2.join(5)
        self.assertFalse(нить2.is_alive())
        self.assertFalse(б2.вышел())

    def test_очередь_по_людям_и_отмена(self):
        задания = Медленные(self.папка, разборов=1)
        а = [задания.создать(владелец=1, имя=f"а{i}", данные=b"\x00") for i in range(4)]
        б = задания.создать(владелец=2, имя="б", данные=b"\x00")
        self.assertTrue(дождаться(lambda: задания.прочитать(а[0])["состояние"] == "идёт", 30))
        self.assertEqual(0, задания.прочитать(б)["перед_ним"])          # второй человек — следующий
        self.assertEqual(3, задания.прочитать(а[3])["перед_ним"])
        self.assertEqual("снято", задания.отменить(а[3]))
        self.assertEqual("отменено", задания.прочитать(а[3])["состояние"])
        self.assertEqual("отменяется", задания.отменить(а[0]))
        с = self.готово(задания, а[0], 10)
        self.assertEqual(("отменено", "разбор отменён"), (с["состояние"], с["ошибка"]))
        self.assertIn("разбор отменён", с["журнал"][-1])
        self.assertEqual("", задания.отменить(а[0]))
        # После отмены идущего — задание второго человека, а не следующее первого.
        self.assertTrue(дождаться(lambda: задания.прочитать(б)["состояние"] != "ждёт", 30))
        self.assertEqual("ждёт", задания.прочитать(а[1])["состояние"])
        for и_ in (б, а[1], а[2]):
            self.assertEqual("готово", self.готово(задания, и_)["состояние"])

    def test_удаление_ждущих_и_идущих(self):
        задания = Медленные(self.папка, разборов=1)
        сессия = "aaaaaaaaaaaa"
        идёт = задания.создать(владелец=1, имя="идёт", данные=b"\x00", сессия=сессия)
        ждёт = задания.создать(владелец=1, имя="ждёт", данные=b"\x00", сессия="bbbbbbbbbbbb")
        self.assertTrue(дождаться(lambda: идёт in задания.идущие(), 30))
        with self.assertRaisesRegex(ValueError, "в сессии идёт разбор"):
            задания.удалить_сессию(сессия)
        self.assertTrue((self.папка / идёт).exists())
        self.assertEqual([ждёт], задания.удалить_сессию("bbbbbbbbbbbb"))  # ждущий снимается с очереди
        self.assertFalse((self.папка / ждёт).exists())
        self.assertEqual("готово", self.готово(задания, идёт)["состояние"])
        self.assertEqual([идёт], задания.удалить_сессию(сессия))

    def test_восстановление_после_перезапуска(self):
        self.папка.mkdir(parents=True)
        def задание(ид, состояние, **ещё):
            (self.папка / ид).mkdir()
            (self.папка / ид / "вход.bin").write_bytes(b"\x00")
            (self.папка / ид / "состояние.json").write_text(json.dumps(
                {"ид": ид, "владелец": 1, "имя": ид, "состояние": состояние, "создано": time.time(),
                 "разбирать": True, "этапы": [], "шаги": [], **ещё}, ensure_ascii=False), encoding="utf-8")
        задание("20990101-000000-00000a", "ждёт")
        задание("20990101-000000-00000b", "идёт", найдено_пока=[{"что": "старое"}])
        задание("20990101-000000-00000c", "идёт", перезапусков=1)
        задание("20990101-000000-00000d", "готово")
        задания = Медленные(self.папка, разборов=2)
        а, б, в, г = (f"20990101-000000-00000{x}" for x in "abcd")
        с = задания.прочитать(в)
        self.assertEqual(("ошибка", 2), (с["состояние"], с["перезапусков"]))
        self.assertIn("прерывался перезапуском", с["ошибка"])
        self.assertEqual("готово", self.готово(задания, а)["состояние"])
        с = self.готово(задания, б)
        self.assertEqual(("готово", 1, []), (с["состояние"], с["перезапусков"], с["найдено_пока"]))
        self.assertIn("прерван перезапуском сервера", " ".join(с["журнал"]))
        self.assertEqual("готово", задания.прочитать(г)["состояние"])
        # Без восстановления — ничего не трогается.
        задание("20990101-000000-00000e", "ждёт")
        Медленные(self.папка, разборов=2, восстановить=False)
        self.assertEqual("ждёт", задания.прочитать("20990101-000000-00000e")["состояние"])

    def test_выключение_оставляет_идущие_на_возобновление(self):
        задания = Медленные(self.папка, разборов=1)
        ид = задания.создать(владелец=1, имя="x", данные=b"\x00")
        ждёт = задания.создать(владелец=1, имя="y", данные=b"\x00")
        self.assertTrue(дождаться(lambda: ид in задания.идущие(), 30))
        задания.закрыть()
        self.assertEqual("идёт", задания.прочитать(ид)["состояние"])
        self.assertEqual("ждёт", задания.прочитать(ждёт)["состояние"])

    def test_ошибка_исполнителя_пишется_в_состояние(self):
        class Падающие(Задания):
            выполнить = staticmethod(з.упавшее_задание)
        задания = Падающие(self.папка, разборов=1)
        ид = задания.создать(владелец=1, имя="x", данные=b"\x00")
        с = self.готово(задания, ид)
        self.assertEqual(("ошибка", "разбор упал"), (с["состояние"], с["ошибка"]))

    def test_одновременная_запись_журнала_и_разметки(self):
        задания = Задания(self.папка, разборов=1)
        ид = задания.создать(владелец=1, имя="x", данные=b"\x00" * 8, разбирать=False)
        self.готово(задания, ид)
        нити = [threading.Thread(target=задания.записать_в_журнал, args=(ид, i % 3, {"текст": f"т{i}"}))
                for i in range(40)]
        нити += [threading.Thread(target=задания.записать_разметку,
                                  args=(ид, 10 + i, {"отрезки": [[i, i + 1]], "правила": []})) for i in range(20)]
        for н in нити:
            н.start()
        for н in нити:
            н.join()
        журнал = задания.журнал_стола(ид)
        self.assertEqual({f"т{i}" for i in range(40)}, {з_["текст"] for записи in журнал.values() for з_ in записи})
        self.assertEqual([[[i, i + 1]] for i in range(20)], [задания.разметка(ид, 10 + i)["отрезки"]
                                                             for i in range(20)])

    def test_шаги_без_автомата_идут_в_исполнителях_стола(self):
        задания = Задания(self.папка, разборов=1)
        ид = задания.создать(владелец=1, имя="x", данные=bytes(range(16)), разбирать=False,
                             шаги=[{"вид": "слой", "слой": "обрезка начало 8 конец 0", "вкл": True}])
        с = self.готово(задания, ид)
        self.assertEqual(("готово", 120), (с["состояние"], с["бит"]), с)
        self.assertIn("шаги выполнены: 120 бит", " ".join(с["журнал"]))


class ОтменаЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.к = self.сеть.client

    def test_отмена_права_и_повтор(self):
        к = self.к
        self.сеть.login("engineer")
        задания = Медленные(Path(self.сеть.tmp) / "potok", разборов=1)
        self.сеть.app.state.potok = задания
        сид = к.post("/api/sessions", json={"name": "отмена"}).json()["id"]
        ид = к.post(f"/api/sessions/{сид}/files", data={"analyze": "1"},
                    files={"file": ("a.bin", b"\x00" * 64, "application/octet-stream")}).json()["id"]
        группа = self.сеть.repos.users.by_login("gruppa").id
        self.assertEqual(200, к.patch(f"/api/sessions/{сид}", json={"members": [группа]}).status_code)
        self.сеть.login("gruppa")
        self.assertEqual(403, к.post(f"/api/potok/{ид}/cancel", json={}).status_code)   # чужой узел
        self.сеть.login("admin")
        self.assertEqual(404, к.post(f"/api/potok/{ид}/cancel", json={}).status_code)   # не виден
        self.сеть.login("engineer")
        ответ = к.post(f"/api/potok/{ид}/cancel", json={})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertIn(ответ.json()["cancel"], ("снято", "отменяется"))
        self.assertTrue(дождаться(lambda: к.get(f"/api/potok/{ид}").json()["состояние"] == "отменено", 10))
        self.assertEqual(409, к.post(f"/api/potok/{ид}/cancel", json={}).status_code)
        self.assertEqual("potok.cancel", self.сеть.repos.audit.list()[0].action)


if __name__ == "__main__":
    unittest.main()
