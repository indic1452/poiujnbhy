"""Приём пачки новых файлов.

Инженеры приносят пачку документов и кладут её в библиотеку. Проверки ниже
описывают то, что раньше проходило молча и незаметно портило библиотеку:
исчезнувшие файлы оставались в поиске, перекладка папок плодила дубликаты,
два файла с одним именем схлопывались в один документ, а не принятые файлы
не назывались вовсе.
"""

import os
import shutil
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.ingest.pipeline import ingest_directory
from reportgen.store.db import Database
from reportgen.store.repo import Repositories

TEXT = ("Измерения выполнены анализатором спектра в полосе пропускания 30 кГц "
        "при усреднении по 64 сегментам и подтверждены повторным прогоном. ") * 4


class BatchCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        database = Database(":memory:")
        database.migrate()
        self.repos = Repositories(database)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, name: str, title: str = "Документ") -> Path:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n{TEXT}", encoding="utf-8")
        return path

    def load(self, **kwargs):
        return ingest_directory(self.repos, self.tmp, **kwargs)

    def ids(self):
        return sorted(document.doc_id for document in self.repos.documents.list())

    def document(self, doc_id: str):
        return self.repos.documents.by_doc_id(doc_id)


class MissingFileTests(BatchCase):
    """Файл убрали из каталога."""

    def test_document_leaves_the_search(self):
        """Иначе инженер получает ссылки на файл, которого нет.

        Обход идёт по диску, базу с каталогом никто не сверял: документа нет,
        а его фрагменты продолжали находиться и цитироваться в отчётах.
        """
        self.put("standards/а.md", "А")
        self.put("standards/б.md", "Б")
        self.load()
        (self.tmp / "standards" / "б.md").unlink()
        result = self.load()

        self.assertEqual("archived", self.document("standards/б").status)
        self.assertEqual("current", self.document("standards/а").status)
        self.assertTrue(any("б" in warning for warning in result.warnings))

    def test_record_is_kept_not_deleted(self):
        # По документу разбирают старые обращения; удалять решает человек.
        self.put("standards/а.md", "А")
        self.load()
        (self.tmp / "standards" / "а.md").unlink()
        self.load()
        self.assertIsNotNone(self.document("standards/а"))

    def test_partial_pass_does_not_archive_the_rest(self):
        # С маской «пропавшим» окажется всё, что под неё не подошло.
        self.put("standards/а.md", "А")
        self.put("standards/б.txt", "Б")
        self.load()
        self.load(patterns=("*.md",))
        self.assertEqual("current", self.document("standards/б").status)


class MovedFileTests(BatchCase):
    """Файл переименовали или переложили."""

    def test_move_is_not_a_new_document(self):
        """Идентификатор — это путь, поэтому перекладка выглядит новым файлом.

        Инженер разложил библиотеку по папкам или добавил год в имя — и
        библиотека удваивалась при каждой такой уборке.
        """
        self.put("standards/гост.md", "ГОСТ")
        self.load()
        (self.tmp / "standards" / "гост.md").rename(
            self.tmp / "standards" / "гост-2009.md")
        result = self.load()

        self.assertEqual(["standards/гост-2009"], self.ids())
        self.assertTrue(any("переехал" in warning for warning in result.warnings))

    def test_move_between_folders(self):
        self.put("literature/книга.md", "Книга")
        self.load()
        (self.tmp / "standards").mkdir(exist_ok=True)
        (self.tmp / "literature" / "книга.md").rename(
            self.tmp / "standards" / "книга.md")
        self.load()
        self.assertEqual(["standards/книга"], self.ids())


class DuplicateTests(BatchCase):
    """Один и тот же файл под двумя именами."""

    def test_second_copy_is_not_indexed(self):
        # Иначе в выдаче два одинаковых фрагмента вместо двух разных, и
        # инженер решит, что нашёл подтверждение в двух источниках.
        source = self.put("standards/гост.md", "ГОСТ")
        (self.tmp / "literature").mkdir(exist_ok=True)
        shutil.copy(source, self.tmp / "literature" / "гост (копия).md")
        result = self.load(jobs=1)

        self.assertEqual(1, len(self.repos.documents.list()))
        self.assertTrue(any("уже принят" in warning or "одинаковое содержимое" in warning
                            for warning in result.warnings), result.warnings)

    def test_parallel_load_still_reports_it(self):
        # При разборе в несколько потоков оба потока видят пустую базу —
        # проверкой перед вставкой такую гонку не закрыть.
        source = self.put("standards/гост.md", "ГОСТ")
        (self.tmp / "literature").mkdir(exist_ok=True)
        shutil.copy(source, self.tmp / "literature" / "гост (копия).md")
        result = self.load(jobs=4)
        self.assertTrue(
            len(self.repos.documents.list()) == 1
            or any("одинаковое содержимое" in warning for warning in result.warnings),
            result.warnings,
        )


class IdentifierCollisionTests(BatchCase):
    """Два файла с одним именем и разными расширениями."""

    def test_both_files_survive(self):
        """Обычное дело: исходник и выгрузка, скан и распознанная версия.

        Идентификатор строится без расширения, поэтому оба файла писались в
        одну запись: отчёт сообщал «добавлено 2», а в библиотеке оказывался
        один документ — содержимое второго пропадало молча.
        """
        self.put("reports/otchet.md", "Отчёт из markdown")
        (self.tmp / "reports" / "otchet.txt").write_text(
            f"Отчёт из txt\n\n{TEXT}", encoding="utf-8")
        result = self.load(jobs=1)

        self.assertEqual(["reports/otchet.md", "reports/otchet.txt"], self.ids())
        self.assertTrue(any("один идентификатор" in warning
                            for warning in result.warnings))

    def test_collision_is_resolved_the_same_way_in_parallel(self):
        self.put("reports/otchet.md", "Отчёт из markdown")
        (self.tmp / "reports" / "otchet.txt").write_text(
            f"Отчёт из txt\n\n{TEXT}", encoding="utf-8")
        self.load(jobs=4)
        self.assertEqual(["reports/otchet.md", "reports/otchet.txt"], self.ids())

    def test_single_file_keeps_the_plain_identifier(self):
        # Иначе пришлось бы переиндексировать всю уже принятую библиотеку.
        self.put("reports/otchet.md", "Отчёт")
        self.load()
        self.assertEqual(["reports/otchet"], self.ids())


class SilentSkipTests(BatchCase):
    """Файлы, которые приём не берёт."""

    def test_documents_in_the_root_are_reported(self):
        # Инженер бросил новый ГОСТ прямо в корень библиотеки.
        self.put("standards/гост.md", "ГОСТ")
        (self.tmp / "новый-гост.md").write_text(f"# Новый\n\n{TEXT}", encoding="utf-8")
        result = self.load()
        self.assertTrue(any("в корне библиотеки" in warning
                            for warning in result.warnings), result.warnings)
        self.assertIn("новый-гост", " ".join(result.warnings))

    def test_unreadable_formats_are_counted(self):
        self.put("standards/гост.md", "ГОСТ")
        for name in ("схема.dwg", "чертёж.dwg"):
            (self.tmp / "standards" / name).write_text("чертёж", encoding="utf-8")
        result = self.load()
        joined = " ".join(result.warnings)
        self.assertIn("формат не читается", joined)
        self.assertIn(".dwg", joined)

    def test_clean_library_says_nothing(self):
        self.put("standards/гост.md", "ГОСТ")
        result = self.load()
        self.assertEqual([], result.warnings)


class RepeatedLoadTests(BatchCase):
    """Обычный случай: к загруженной библиотеке добавили несколько файлов."""

    def test_only_new_files_are_processed(self):
        for index in range(4):
            self.put(f"standards/д{index}.md", f"Документ {index}")
        self.load()
        self.put("standards/новый.md", "Новый")
        result = self.load()
        self.assertEqual(1, result.added)
        self.assertEqual(4, result.skipped)
        self.assertEqual(0, result.failed)


class FastSkipTests(BatchCase):
    """Повторный прогон не должен перечитывать библиотеку с диска.

    Приём считал SHA-256 каждого файла до всякой проверки, то есть читал все
    гигабайты. Добавить пять документов к десяти тысячам означало перечитать
    десять тысяч. На замере stat() дешевле хеша примерно в сотню раз.
    """

    def test_untouched_file_is_not_read(self):
        path = self.put("standards/а.md", "А")
        self.load()
        stat = path.stat()
        document = self.document("standards/а")
        self.assertEqual(stat.st_size, document.size)
        self.assertEqual(stat.st_mtime_ns, document.mtime_ns)

        with unittest.mock.patch("reportgen.ingest.pipeline.sha256_file") as hashed:
            result = self.load()
        self.assertEqual(1, result.skipped)
        hashed.assert_not_called()

    def test_changed_file_is_reindexed(self):
        self.put("standards/а.md", "А")
        self.load()
        (self.tmp / "standards" / "а.md").write_text(
            f"# А, новая редакция\n\n{TEXT} Добавлен раздел 5.", encoding="utf-8")
        result = self.load()
        self.assertEqual(1, result.updated)
        self.assertEqual("А, новая редакция", self.document("standards/а").title)

    def test_touched_but_identical_file_is_skipped(self):
        # Дата поменялась, содержимое нет: хеш совпадёт, и переиндексации не
        # будет — но приметы надо обновить, иначе хеш считается каждый раз.
        path = self.put("standards/а.md", "А")
        self.load()
        later = time.time() + 5
        os.utime(path, (later, later))
        result = self.load()
        self.assertEqual(1, result.skipped)
        self.assertEqual(0, result.updated)
        self.assertEqual(path.stat().st_mtime_ns, self.document("standards/а").mtime_ns)

    def test_force_reindexes_anyway(self):
        self.put("standards/а.md", "А")
        self.load()
        result = self.load(force=True)
        self.assertEqual(1, result.updated)

    def test_old_database_gets_the_marks(self):
        """База заказчика заполнена до появления этих колонок.

        Без дозаписи ускорение не включилось бы никогда: пропуск по хешу
        возвращается до записи документа, и приметы остались бы пустыми.
        """
        self.put("standards/а.md", "А")
        self.load()
        with self.repos.db.transaction() as connection:
            connection.execute("UPDATE documents SET size = NULL, mtime_ns = NULL")

        result = self.load()
        self.assertEqual(1, result.skipped)
        document = self.document("standards/а")
        self.assertIsNotNone(document.size)
        self.assertIsNotNone(document.mtime_ns)

        with unittest.mock.patch("reportgen.ingest.pipeline.sha256_file") as hashed:
            self.load()
        hashed.assert_not_called()


class InterruptTests(BatchCase):
    """Ctrl+C на большой пачке.

    Инженер запустил приём тысячи сканов и увидел, что попал не в тот
    каталог. Раньше Ctrl+C ничего не давал: все файлы уже отданы пулу
    потоков, а выход из него дожидается очереди целиком — пачка
    дорабатывалась до конца, отчёт терялся, на экране оставалась
    трассировка.
    """

    def build(self, count: int = 8):
        for index in range(count):
            self.put(f"standards/д{index}.md", f"Документ {index}")

    def load_with_interrupt(self, after: int = 2):
        """Приём, прерванный после нескольких готовых файлов."""
        from concurrent import futures as real_futures

        original = real_futures.as_completed

        def interrupting(pending, *args, **kwargs):
            for number, future in enumerate(original(pending, *args, **kwargs), start=1):
                if number > after:
                    raise KeyboardInterrupt
                yield future

        with unittest.mock.patch(
            "reportgen.ingest.pipeline.futures.as_completed", interrupting
        ):
            return self.load(jobs=4)

    def test_interrupt_does_not_escape(self):
        # Иначе инженер видит трассировку вместо отчёта.
        self.build()
        result = self.load_with_interrupt()
        self.assertIsNotNone(result)

    def test_partial_result_is_reported(self):
        self.build()
        result = self.load_with_interrupt()
        self.assertTrue(any("прерван" in warning for warning in result.warnings),
                        result.warnings)

    def test_processed_documents_are_kept(self):
        self.build()
        self.load_with_interrupt()
        self.assertTrue(self.repos.documents.list(), "разобранное потеряно")

    def test_second_run_finishes_the_rest(self):
        self.build()
        self.load_with_interrupt()
        self.load(jobs=4)
        self.assertEqual(8, len(self.repos.documents.list()))


if __name__ == "__main__":
    unittest.main()


class ReportSplitTests(BatchCase):
    """Отказы и замечания — разные списки.

    «Файл пуст» (документа не будет, надо чинить) стоял вперемешку с «формат
    не читается» (так и задумано), да ещё отсортированный по алфавиту вместе
    с ним. Даже добравшись до списка, инженер не мог отличить строки,
    требующие действий, от справочных.
    """

    def build(self):
        self.put("standards/хороший.md", "Хороший")
        (self.tmp / "standards" / "пустой.md").write_text("", encoding="utf-8")
        (self.tmp / "standards" / "схема.dwg").write_text("чертёж", encoding="utf-8")
        (self.tmp / "новый-гост.md").write_text(f"# В корне\n\n{TEXT}", encoding="utf-8")

    def test_unaccepted_file_is_a_failure(self):
        self.build()
        result = self.load()
        self.assertTrue(any("пустой" in line for line in result.failures), result.failures)
        self.assertFalse(any("пустой" in line for line in result.notes))

    def test_expected_skips_are_notes(self):
        self.build()
        result = self.load()
        joined = " ".join(result.notes)
        self.assertIn(".dwg", joined)
        self.assertIn("в корне библиотеки", joined)
        self.assertFalse(any(".dwg" in line for line in result.failures))

    def test_accepted_with_a_remark_is_a_note(self):
        # Документ принят, просто не целиком — чинить нечего.
        import zipfile

        path = self.tmp / "literature" / "архив.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("книга.md", f"# Книга\n\n{TEXT}")
        result = self.load()
        self.assertEqual(0, result.failed)
        self.assertEqual([], result.failures)

    def test_combined_list_still_works(self):
        self.build()
        result = self.load()
        self.assertEqual(result.failures + result.notes, result.warnings)
