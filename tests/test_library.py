"""Загрузка библиотеки: раскладка по типам и направлениям.

Библиотека заказчика — это папки с книгами, стандартами и прошлыми отчётами,
названные по-человечески и вложенные на несколько уровней. Здесь проверяется
то, от чего зависит, найдётся ли потом нужный фрагмент: тип документа,
направление техники и устойчивость приёма к тому, откуда его запустили.
"""

import os
import re
import shutil
import subprocess
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

    def test_company_categories_are_covered(self):
        # Категории, которыми компания пользуется в работе.
        ids = set(domains.registry(ROOT / "templates" / "domains.json").ids)
        self.assertLessEqual(
            {"hf", "satellite", "microwave", "mobile", "protocols",
             "signal", "method", "software", "hardware", "standard"},
            ids,
        )

    def test_renamed_domains_are_carried_over(self):
        # Правка справочника не должна осиротить уже принятые документы.
        from reportgen.store.db import DOMAIN_RENAMES

        ids = set(domains.registry(ROOT / "templates" / "domains.json").ids)
        for old_id, new_id in DOMAIN_RENAMES:
            with self.subTest(old=old_id):
                self.assertNotIn(old_id, ids, "старый идентификатор всё ещё в справочнике")
                self.assertIn(new_id, ids, "новый идентификатор отсутствует")

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


class LoadLibraryScriptTests(unittest.TestCase):
    """Загрузка библиотеки — одной командой, без ручной сборки из кусков."""

    def setUp(self):
        self.script = ROOT / "scripts" / "windows" / "load-library.ps1"
        self.text = self.script.read_text(encoding="utf-8")

    def test_script_exists_and_has_bom(self):
        # Без BOM Windows PowerShell 5.1 читает файл как ANSI, и русские
        # сообщения превращаются в кракозябры.
        self.assertTrue(self.script.is_file())
        self.assertTrue(self.script.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_refuses_to_run_before_installation_finished(self):
        # Пока нет окружения и настроек, разбор всё равно не сработает —
        # лучше сказать это сразу и назвать нужную команду.
        self.assertIn("install-offline.ps1", self.text)

    def test_covers_the_whole_sequence(self):
        for command in ("formats", "ingest", "embed", "library"):
            with self.subTest(command=command):
                self.assertIn(command, self.text)

    def test_exposes_the_same_switches_as_ingest(self):
        for switch in ("$DocType", "$Domain", "$Force", "$NoEmbed"):
            with self.subTest(switch=switch):
                self.assertIn(switch, self.text)

    def test_doc_type_values_match_the_code(self):
        for doc_type in DOC_TYPES:
            with self.subTest(doc_type=doc_type):
                self.assertIn(f"'{doc_type}'", self.text)

    def test_documented(self):
        doc = (ROOT / "docs" / "18-library.md").read_text(encoding="utf-8")
        self.assertIn("load-library.ps1", doc)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_script_parses(self):
        check = (
            "$e=$null;$t=$null;"
            f"[System.Management.Automation.Language.Parser]::ParseFile('{self.script}',"
            "[ref]$t,[ref]$e)|Out-Null; if($e.Count){$e|%{Write-Host $_.Message}; exit 1}"
        )
        done = subprocess.run(["pwsh", "-NoProfile", "-Command", check],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


class UsersScriptTests(unittest.TestCase):
    """Забытый пароль администратора чинится на самой машине, без интерфейса."""

    def setUp(self):
        self.script = ROOT / "scripts" / "windows" / "users.ps1"
        self.text = self.script.read_text(encoding="utf-8")

    def test_script_exists_and_has_bom(self):
        self.assertTrue(self.script.is_file())
        self.assertTrue(self.script.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_covers_list_add_and_reset(self):
        for command in ("users", "useradd", "passwd"):
            with self.subTest(command=command):
                self.assertIn(command, self.text)

    def test_roles_match_the_code(self):
        from reportgen.store.models import ROLES

        for role in ROLES:
            with self.subTest(role=role):
                self.assertIn(f"'{role}'", self.text)

    def test_password_reset_needs_no_old_password(self):
        # Пароль забыт — значит, старый спрашивать не у кого.
        from reportgen.cli import build_parser

        parser = build_parser()
        passwd = [a for a in parser._actions if a.choices and "passwd" in a.choices][0].choices["passwd"]
        options = {option for action in passwd._actions for option in action.option_strings}
        self.assertNotIn("--old-password", options)
        self.assertIn("--login", options)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_script_parses(self):
        check = (
            "$e=$null;$t=$null;"
            f"[System.Management.Automation.Language.Parser]::ParseFile('{self.script}',"
            "[ref]$t,[ref]$e)|Out-Null; if($e.Count){$e|%{Write-Host $_.Message}; exit 1}"
        )
        done = subprocess.run(["pwsh", "-NoProfile", "-Command", check],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()


class EmbeddedImageTests(unittest.TestCase):
    """Картинки внутри документов тоже должны попадать в базу.

    В технических отчётах половина существенного лежит на иллюстрациях:
    спектрограмма с подписанными частотами, снимок экрана анализатора,
    вклеенная страница методики. Раньше в текст попадала только подпись
    «рисунок 1» — то есть эти данные не искались вовсе.
    """

    def test_container_formats_are_listed(self):
        from reportgen.ingest.convert import EMBEDDED_IMAGE_SUFFIXES

        for suffix in (".docx", ".pptx", ".xlsx", ".odt", ".odp"):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, EMBEDDED_IMAGE_SUFFIXES)

    def test_can_be_switched_off(self):
        from reportgen.ingest.convert import embedded_ocr_enabled

        for value, expected in (("0", False), ("false", False), ("off", False),
                                ("1", True), ("yes", True)):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"REPORTGEN_OCR_EMBEDDED": value}):
                    self.assertIs(expected, embedded_ocr_enabled())

    def test_small_images_are_skipped(self):
        # Логотип в шапке бланка и маркер списка распознавать незачем.
        from reportgen.ingest.formats.ocr import MIN_EMBEDDED_HEIGHT, MIN_EMBEDDED_WIDTH

        self.assertGreaterEqual(MIN_EMBEDDED_WIDTH, 100)
        self.assertGreaterEqual(MIN_EMBEDDED_HEIGHT, 40)

    def test_recognised_text_is_marked_as_machine_read(self):
        from reportgen.ingest.formats.ocr import embedded_images_block

        block = embedded_images_block([("shema.png", "Uroven signala -62 dBm")])
        self.assertIn("распознан машинно", block)
        self.assertIn("сверяйте с оригиналом", block)
        self.assertIn("Uroven signala -62 dBm", block)

    def test_no_images_no_block(self):
        from reportgen.ingest.formats.ocr import embedded_images_block

        self.assertEqual("", embedded_images_block([]))


class UnreadableTextTests(unittest.TestCase):
    """PDF без карты символов отдаёт заглушки вместо букв.

    Формально разбор удаётся, предупреждений нет, и документ с заголовком
    «······ ····» молча уезжает в базу. В библиотеке из старых переведённых
    в PDF книг это встречается регулярно.
    """

    def test_placeholder_text_is_detected(self):
        from reportgen.ingest.convert import readable_share

        self.assertLess(readable_share("······ ··········· ·····"), 0.1)

    def test_normal_text_passes(self):
        from reportgen.ingest.convert import readable_share

        self.assertGreater(readable_share("Основы спутниковой связи, модуляция QPSK."), 0.9)

    def test_empty_text_is_not_an_error(self):
        from reportgen.ingest.convert import readable_share

        self.assertEqual(0.0, readable_share("   \n  "))


class IngestSpeedTests(unittest.TestCase):
    """Разбор упирается в процессор — и должен занимать больше одного ядра."""

    def test_jobs_default_leaves_a_core_free(self):
        from reportgen.ingest.pipeline import resolve_jobs

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REPORTGEN_INGEST_JOBS", None)
            with mock.patch("os.cpu_count", return_value=8):
                self.assertEqual(7, resolve_jobs())
            with mock.patch("os.cpu_count", return_value=1):
                self.assertEqual(1, resolve_jobs())
            # Больше восьми потоков смысла не имеет: упрёмся в диск.
            with mock.patch("os.cpu_count", return_value=64):
                self.assertEqual(8, resolve_jobs())

    def test_explicit_jobs_win(self):
        from reportgen.ingest.pipeline import resolve_jobs

        self.assertEqual(3, resolve_jobs(3))

    def test_environment_override(self):
        from reportgen.ingest.pipeline import resolve_jobs

        with mock.patch.dict(os.environ, {"REPORTGEN_INGEST_JOBS": "5"}):
            self.assertEqual(5, resolve_jobs())

    def test_tesseract_runs_single_threaded(self):
        # По умолчанию tesseract разворачивает OpenMP на все ядра. При
        # параллельном приёме четыре таких процесса на четырёхъядерной машине
        # не заканчивают за девять минут работу, которая занимает три секунды.
        from reportgen.ingest.formats.ocr import _tesseract_env

        env = _tesseract_env()
        self.assertEqual("1", env["OMP_THREAD_LIMIT"])
        self.assertEqual("1", env["OMP_NUM_THREADS"])


class ParallelIngestTests(unittest.TestCase):
    """Параллельный приём обязан давать тот же результат, что и последовательный."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        library = self.dir / "library" / "literature"
        library.mkdir(parents=True)
        for index in range(12):
            (library / f"файл-{index:02d}.md").write_text(
                f"# Документ {index}\n\nПолоса частот и модуляция, запись {index}.\n" * 4,
                encoding="utf-8",
            )
        self.library = self.dir / "library"

    def tearDown(self):
        self._tmp.cleanup()

    def ingest(self, jobs):
        from reportgen.ingest.pipeline import ingest_directory
        from reportgen.store.db import Database
        from reportgen.store.repo import Repositories

        repos = Repositories(Database(":memory:"))
        result = ingest_directory(repos, self.library, jobs=jobs)
        documents = {d.doc_id: d.chunk_count for d in repos.documents.list()}
        return result, documents

    def test_same_result_as_serial(self):
        serial, serial_docs = self.ingest(1)
        parallel, parallel_docs = self.ingest(4)
        self.assertEqual(serial.added, parallel.added)
        self.assertEqual(serial.chunks, parallel.chunks)
        self.assertEqual(serial_docs, parallel_docs)
        self.assertEqual(12, len(parallel_docs))

    def test_report_is_stable(self):
        # Потоки завершаются в произвольном порядке: списки в отчёте о приёме
        # должны выглядеть одинаково от запуска к запуску.
        first, _ = self.ingest(4)
        second, _ = self.ingest(4)
        self.assertEqual(first.documents, second.documents)
