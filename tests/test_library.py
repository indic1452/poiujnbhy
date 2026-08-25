"""Загрузка библиотеки: раскладка по типам и направлениям.

Библиотека заказчика — это папки с книгами, стандартами и прошлыми отчётами,
названные по-человечески и вложенные на несколько уровней. Здесь проверяется
то, от чего зависит, найдётся ли потом нужный фрагмент: тип документа,
направление техники и устойчивость приёма к тому, откуда его запустили.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _bootstrap  # noqa: F401
from reportgen import domains
from reportgen.corpus import DOC_TYPES
from reportgen.ingest.convert import guess_doc_type

ROOT = Path(__file__).resolve().parents[1]


class DomainRegistryLocationTests(unittest.TestCase):
    """Справочник направлений находится независимо от каталога запуска.

    Инструкция велит запускать приём библиотеки из ``scripts\\windows``, а
    справочник искался относительно текущего каталога. Там его нет — и все
    документы молча оставались без направления: поиск с фильтром по
    направлению не находил ничего, и понять причину было невозможно.
    """

    def setUp(self):
        self._cwd = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_registry_found_from_any_directory(self):
        found = domains.registry()
        self.assertTrue(found.domains, "справочник направлений не найден вне корня проекта")

    def test_classification_works_from_any_directory(self):
        result = domains.registry().classify(
            "Радиорелейные линии",
            "радиорелейная линия, пролёт, замирания, антенна, уровень принимаемого сигнала",
        )
        self.assertTrue(result, "классификатор без справочника молча возвращает пустое направление")

    def test_environment_override_wins(self):
        with mock.patch.dict(os.environ, {"REPORTGEN_DOMAINS_PATH": "нет-такого.json"}):
            self.assertEqual(Path("нет-такого.json"), domains.default_path())


class DocTypeFromFolderTests(unittest.TestCase):
    """Тип документа берётся из имени каталога верхнего уровня."""

    def test_known_top_level_folder(self):
        root = Path("/лит")
        self.assertEqual("standards", guess_doc_type(root / "standards" / "ГОСТ.docx", root))

    def test_nested_folders_keep_the_top_level_type(self):
        root = Path("/лит")
        path = root / "standards" / "ГОСТ" / "2009" / "гост.docx"
        self.assertEqual("standards", guess_doc_type(path, root))

    def test_unknown_folder_falls_back_to_literature(self):
        root = Path("/лит")
        self.assertEqual("literature", guess_doc_type(root / "Мои книги" / "том.pdf", root))

    def test_all_five_types_are_recognised(self):
        root = Path("/лит")
        for doc_type in DOC_TYPES:
            with self.subTest(doc_type=doc_type):
                self.assertEqual(doc_type, guess_doc_type(root / doc_type / "файл.txt", root))


class IngestOptionsTests(unittest.TestCase):
    """Тип и направление можно задать на всю папку, не переименовывая её."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = str(self.dir / "test.db")
        library = self.dir / "Книги по спутнику" / "разное"
        library.mkdir(parents=True)
        (library / "том.md").write_text(
            "# Спутниковые линии\n\nПролёт, транспондер, модуляция QPSK.\n", encoding="utf-8"
        )
        self.library = self.dir

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        import contextlib
        import io

        from reportgen.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(argv)
        return code, out.getvalue()

    def test_doc_type_and_domain_apply_to_the_whole_folder(self):
        code, _ = self.run_cli(["--db", self.db, "ingest", str(self.library),
                                "--doc-type", "standards", "--domain", "satellite"])
        self.assertEqual(0, code)
        code, output = self.run_cli(["--db", self.db, "library"])
        self.assertEqual(0, code)
        self.assertIn("standards", output)
        self.assertIn("satellite", output)

    def test_unknown_doc_type_is_refused_with_the_list(self):
        code, output = self.run_cli(["--db", self.db, "ingest", str(self.library),
                                     "--doc-type", "книги"])
        self.assertEqual(1, code)
        self.assertIn("standards", output, "в ошибке нет списка допустимых типов")

    def test_unknown_domain_is_refused_with_the_list(self):
        code, output = self.run_cli(["--db", self.db, "ingest", str(self.library),
                                     "--domain", "спутник"])
        self.assertEqual(1, code)
        self.assertIn("satellite", output, "в ошибке нет списка допустимых направлений")

    def test_second_run_changes_nothing(self):
        self.run_cli(["--db", self.db, "ingest", str(self.library)])
        code, output = self.run_cli(["--db", self.db, "ingest", str(self.library)])
        self.assertEqual(0, code)
        self.assertIn("без изменений 1", output)


class LibraryDocsTests(unittest.TestCase):
    """Инструкция по загрузке не разошлась с кодом."""

    def setUp(self):
        self.doc = (ROOT / "docs" / "18-library.md").read_text(encoding="utf-8")

    def test_all_doc_types_documented(self):
        for doc_type in DOC_TYPES:
            with self.subTest(doc_type=doc_type):
                self.assertIn(f"`{doc_type}/`", self.doc)

    def test_all_domains_documented(self):
        for name in domains.registry(ROOT / "templates" / "domains.json").ids:
            with self.subTest(domain=name):
                self.assertIn(name, self.doc)

    def test_document_statuses_documented(self):
        from reportgen.store.models import DOC_STATUSES

        for status in DOC_STATUSES:
            with self.subTest(status=status):
                self.assertIn(f"`{status}`", self.doc)

    def test_mentioned_commands_exist(self):
        from reportgen.cli import build_parser

        actions = [a for a in build_parser()._actions if a.choices and "ingest" in a.choices]
        known = set(actions[0].choices)
        for command in re.findall(r"Invoke-Reportgen ([a-z-]+)", self.doc):
            with self.subTest(command=command):
                self.assertIn(command, known, f"в CLI нет команды {command}")


if __name__ == "__main__":
    unittest.main()
