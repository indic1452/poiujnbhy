"""База одна на всех: «database is locked» не должно рвать многочасовую работу.

SQLite пускает к записи по одному. Базу делят веб-приложение и команды из
консоли, и на пересборке библиотеки они пишут почти непрерывно: приём
документов — чанки и поисковый индекс, построение векторов — сами векторы.
Отдел запустил пересборку, не закрыв интерфейс, и через полчаса получил
«database is locked» — голое сообщение SQLite, из которого не понять ни
причины, ни что делать, ни сохранилось ли сделанное.

Здесь проверяется то, чем это лечится: ждать освобождения не десять секунд, а
минуту; пока консоль работает — фоновый построитель векторов в приложении не
лезет к той же записи; а если ждать всё-таки не дождались, человек читает
объяснение, а не английский текст драйвера.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.store import db as dbmod
from reportgen.store.db import (
    BUSY_TIMEOUT_MS,
    Database,
    DatabaseBusy,
    console_mark_path,
    console_work,
    console_work_active,
)
from reportgen.store.repo import Repositories
from reportgen.web.vectors import VectorIndexer

from test_vectors import build_repos, make_settings  # noqa: E402


class ОжиданиеБазы(unittest.TestCase):
    """Сколько ждём, прежде чем сдаться."""

    def test_ждём_не_меньше_минуты(self):
        # Прежние десять секунд рвались на пересборке: соседний процесс держит
        # запись дольше на любой сколько-нибудь большой пачке.
        self.assertGreaterEqual(BUSY_TIMEOUT_MS, 60_000)

    def test_ожидание_доходит_до_соединения(self):
        # Константа сама по себе ничего не значит: важно, что она проставлена
        # в PRAGMA. Однажды такую настройку задали и забыли применить.
        с_диска = Path(tempfile.mkdtemp(prefix="замок-")) / "база.sqlite3"
        база = Database(с_диска)
        значение = база.connection.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(BUSY_TIMEOUT_MS, int(значение))

    def test_ожидание_настраивается_переменной_среды(self):
        # На слабой машине пересборка идёт дольше, и минуты может не хватить.
        код = (
            "import os; os.environ['REPORTGEN_DB_TIMEOUT_MS']='123456';"
            "import sys; sys.path.insert(0, %r);"
            "from reportgen.store.db import BUSY_TIMEOUT_MS as t; print(t)"
            % str(Path(__file__).resolve().parents[1] / "src")
        )
        готово = subprocess.run([sys.executable, "-c", код],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual("123456", готово.stdout.strip(), готово.stderr)


class ПонятнаяОшибка(unittest.TestCase):
    """«database is locked» человеку на изолированной машине не говорит ничего."""

    def setUp(self):
        self.каталог = Path(tempfile.mkdtemp(prefix="замок-"))
        self.путь = self.каталог / "база.sqlite3"

    def test_занятая_база_объясняет_себя_по_русски(self):
        база = Database(self.путь)
        база.connection.execute("PRAGMA busy_timeout = 200")   # ждать в тесте некогда
        чужой = sqlite3.connect(self.путь, timeout=0.2)
        чужой.execute("BEGIN IMMEDIATE")                        # держим запись
        try:
            with self.assertRaises(DatabaseBusy) as поймано:
                with база.transaction() as connection:
                    connection.execute(
                        "INSERT INTO meta(key, value) VALUES('проба', '1')")
        finally:
            чужой.rollback()
            чужой.close()
        текст = str(поймано.exception)
        self.assertIn("база занята другим процессом", текст)
        self.assertIn("сделанное сохранено", текст)
        self.assertIn("REPORTGEN_DB_TIMEOUT_MS", текст)
        # И ни слова по-английски из недр драйвера.
        self.assertNotIn("database is locked", текст)

    def test_чужая_ошибка_не_подменяется(self):
        # Опечатка в SQL не должна выглядеть как занятая база: это увело бы
        # разбор совсем не туда.
        база = Database(self.путь)
        with self.assertRaises(sqlite3.Error) as поймано:
            with база.transaction() as connection:
                connection.execute("SELECT из ниоткуда")
        self.assertNotIsInstance(поймано.exception, DatabaseBusy)

    def test_дождавшись_очереди_запись_проходит(self):
        # Главное свойство: занятость — это ожидание, а не отказ. Пока ждём в
        # пределах таймаута, работа доводится до конца. Ждать в тесте минуту
        # незачем, поэтому таймаут укорочен — проверяется само поведение.
        import threading

        база = Database(self.путь)
        база.connection.execute("PRAGMA busy_timeout = 5000")
        чужой = sqlite3.connect(self.путь, timeout=1.0, check_same_thread=False)
        чужой.execute("BEGIN IMMEDIATE")

        def отпустить():
            чужой.rollback()
            чужой.close()

        таймер = threading.Timer(0.3, отпустить)
        таймер.start()
        try:
            with база.transaction() as connection:
                connection.execute("INSERT INTO meta(key, value) VALUES('проба', '1')")
        finally:
            таймер.join(10)
        строка = база.connection.execute(
            "SELECT value FROM meta WHERE key = 'проба'").fetchone()
        self.assertEqual("1", строка["value"])


class ПризнакКонсольнойРаботы(unittest.TestCase):
    """Пока консоль перебирает библиотеку, приложению туда не надо."""

    def setUp(self):
        self.каталог = Path(tempfile.mkdtemp(prefix="замок-"))
        self.путь = self.каталог / "база.sqlite3"

    def test_признак_ставится_и_снимается(self):
        self.assertFalse(console_work_active(self.путь))
        with console_work(self.путь, "приём библиотеки"):
            self.assertTrue(console_work_active(self.путь))
            self.assertTrue(console_mark_path(self.путь).exists())
        self.assertFalse(console_work_active(self.путь))
        self.assertFalse(console_mark_path(self.путь).exists())

    def test_признак_снимается_и_после_ошибки(self):
        # Иначе одно падение приёма навсегда останавливало бы построение
        # векторов в приложении.
        with self.assertRaises(RuntimeError):
            with console_work(self.путь, "приём"):
                raise RuntimeError("приём упал")
        self.assertFalse(console_work_active(self.путь))

    def test_брошенный_признак_перестаёт_действовать(self):
        # Процесс убили, файл остался. Приложение не должно молчать вечно.
        метка = console_mark_path(self.путь)
        метка.write_text("1\n", encoding="utf-8")
        старое = time.time() - dbmod.CONSOLE_MARK_STALE - 60
        os.utime(метка, (старое, старое))
        self.assertFalse(console_work_active(self.путь))

    def test_отметка_продлевает_действие(self):
        # Построение векторов идёт часами и не пишет в файл само по себе.
        метка = console_mark_path(self.путь)
        with console_work(self.путь, "векторы") as отметиться:
            старое = time.time() - dbmod.CONSOLE_MARK_STALE - 60
            os.utime(метка, (старое, старое))
            self.assertFalse(console_work_active(self.путь))
            отметиться()
            self.assertTrue(console_work_active(self.путь))


class ПостроительУступаетКонсоли(unittest.TestCase):
    """Фоновый построитель векторов не дерётся с консолью за запись."""

    def setUp(self):
        self.каталог = Path(tempfile.mkdtemp(prefix="замок-"))
        self.путь = self.каталог / "база.sqlite3"

    def _построитель(self):
        repos = build_repos(4, path=self.путь)
        # Клиент, который не должен быть вызван вовсе: если построитель всё же
        # запустится, тест это увидит по числу обращений.
        self.обращений = 0

        class Считающий:
            model = "bge-m3"

            def embed(себя, тексты):
                self.обращений += 1
                return [[0.1, 0.2, 0.3] for _ in тексты]

        return VectorIndexer(repos, make_settings(), client_factory=lambda: Считающий())

    def test_пока_консоль_работает_построение_не_запускается(self):
        индексатор = self._построитель()
        with console_work(self.путь, "пересборка"):
            состояние = индексатор.start()
            self.assertFalse(состояние["running"], "построитель полез в занятую базу")
            self.assertTrue(состояние["console"])
        индексатор.wait(5)
        self.assertEqual(0, self.обращений, "векторы строились вопреки консоли")

    def test_после_консоли_построение_идёт_как_обычно(self):
        индексатор = self._построитель()
        with console_work(self.путь, "пересборка"):
            индексатор.start()
        индексатор.start()
        индексатор.wait(30)
        self.assertGreater(self.обращений, 0, "построитель не ожил после консоли")

    def test_подсказка_объясняет_почему_векторов_нет(self):
        # Человек видит «не хватает 40 000» и жмёт кнопку, которая ничего не
        # делает. Причину надо назвать.
        индексатор = self._построитель()
        with console_work(self.путь, "пересборка"):
            состояние = индексатор.status(fresh=True)
        self.assertIn("пересборка библиотеки из консоли", состояние["hint"])

    def test_приём_документа_не_будит_построителя(self):
        # start_if_needed зовётся после каждой загрузки файла через интерфейс.
        индексатор = self._построитель()
        with console_work(self.путь, "пересборка"):
            состояние = индексатор.start_if_needed()
            self.assertFalse(состояние["running"])
        индексатор.wait(5)
        self.assertEqual(0, self.обращений)


class КомандыСтавятПризнак(unittest.TestCase):
    """Признак ставят сами команды, иначе от него нет толку."""

    def setUp(self):
        self.исходник = (Path(__file__).resolve().parents[1]
                         / "src" / "reportgen" / "cli.py").read_text(encoding="utf-8")

    def test_приём_библиотеки_помечает_базу(self):
        self.assertIn('console_work(repos.db.path, "приём библиотеки")', self.исходник)

    def test_построение_векторов_помечает_базу(self):
        self.assertIn('console_work(repos.db.path, "построение векторов")', self.исходник)

    def test_долгая_работа_отмечается_по_ходу(self):
        # Без отметок пятнадцать минут — и признак сочтён брошенным.
        self.assertIn("отметиться()", self.исходник)
