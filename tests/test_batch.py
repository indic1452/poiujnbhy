"""Приём пачки новых файлов.

Инженеры приносят пачку документов и кладут её в библиотеку. Проверки ниже
описывают то, что раньше проходило молча и незаметно портило библиотеку:
исчезнувшие файлы оставались в поиске, перекладка папок плодила дубликаты,
два файла с одним именем схлопывались в один документ, а не принятые файлы
не назывались вовсе.
"""

import shutil
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
