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
        # Каталог инженер заводит сам — про него в инструкции должна быть
        # строка вида `standards/`. Кроме «прочего»: каталога misc/ нет,
        # этот тип система ставит сама, когда не поняла документ. Требовать
        # для него косую черту значит требовать написать неправду.
        for doc_type in DOC_TYPES:
            with self.subTest(doc_type=doc_type):
                expected = f"`{doc_type}`" if doc_type == "misc" else f"`{doc_type}/`"
                self.assertIn(expected, self.doc)
        self.assertNotIn("`misc/`", self.doc, "каталога misc/ не существует")

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


class RfcTests(unittest.TestCase):
    """RFC — главный источник по полям кадров и заголовков.

    Как обычный .txt такой файл попадал в базу под названием «rfc791», без
    года, без разделов и без пометки об отмене: система с равной охотой
    сослалась бы и на RFC 2616, и на заменивший его 7230.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    HEADER = (
        "Internet Engineering Task Force (IETF)                  R. Fielding, Ed.\n"
        "Request for Comments: 7230                                         Adobe\n"
        "Obsoletes: 2145, 2616                                    J. Reschke, Ed.\n"
        "Updates: 2817, 2818                                           greenbytes\n"
        "Category: Standards Track                                      June 2014\n"
        "ISSN: 2070-1721\n\n\n"
        "         Hypertext Transfer Protocol (HTTP/1.1): Message Syntax\n"
        "                             and Routing\n\n"
        "Abstract\n\n   Text of the abstract.\n\n"
        "3.  Message Format\n\n   All HTTP/1.1 messages consist of a start-line.\n\n"
        "3.2.  Header Fields\n\n   Each header field consists of a field name.\n\n"
        "Fielding & Reschke           Standards Track                    [Page 1]\n"
    )

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_recognised_by_header_not_by_name(self):
        from reportgen.ingest.formats.rfc import is_rfc_text

        self.assertTrue(is_rfc_text(self.HEADER))
        self.assertFalse(is_rfc_text("Заметка инженера по пролёту Р-14."))

    def test_title_and_number(self):
        from reportgen.ingest.convert import convert_file

        result = convert_file(self.write("какое-то-имя.txt", self.HEADER))
        self.assertEqual(7230, result.meta["rfc"])
        self.assertIn("Hypertext Transfer Protocol", result.title)
        self.assertTrue(result.title.startswith("RFC 7230."))

    def test_long_title_centred_close_to_the_margin(self):
        # Длинное название в 72 колонках центруется почти вплотную к краю.
        # Требование «заметный отступ» такое название теряло, и документ
        # назывался просто «RFC 7230».
        header = (
            "Internet Engineering Task Force (IETF)                  R. Fielding\n"
            "Request for Comments: 7230                                    Adobe\n"
            "Category: Standards Track                                 June 2014\n\n"
            "  Hypertext Transfer Protocol (HTTP/1.1): Message Syntax and Routing\n\n"
            "Abstract\n\n   Text of the abstract.\n"
        )
        from reportgen.ingest.convert import convert_file

        result = convert_file(self.write("rfc7230.txt", header))
        self.assertEqual(
            "RFC 7230. Hypertext Transfer Protocol (HTTP/1.1): "
            "Message Syntax and Routing",
            result.title,
        )

    def test_year_from_header(self):
        from reportgen.ingest.convert import convert_file

        result = convert_file(self.write("rfc7230.txt", self.HEADER))
        self.assertEqual(2014, result.meta["year"])

    def test_relations_are_kept(self):
        from reportgen.ingest.convert import convert_file

        result = convert_file(self.write("rfc7230.txt", self.HEADER))
        self.assertEqual([2145, 2616], result.meta["obsoletes"])
        self.assertEqual([2817, 2818], result.meta["updates"])

    def test_obsoleted_rfc_is_marked_superseded(self):
        # Ссылка на отменённый RFC в отчёте заказчику — прямая ошибка.
        from reportgen.ingest.convert import convert_file

        text = self.HEADER.replace("Obsoletes: 2145, 2616", "Obsoleted by: 7230, 7231")
        result = convert_file(self.write("rfc2616.txt", text))
        self.assertEqual("superseded", result.meta["status"])
        self.assertIn("7230", result.meta["superseded_by"])
        self.assertTrue(any("отменён" in item for item in result.warnings), result.warnings)

    def test_sections_become_headings(self):
        from reportgen.ingest.convert import convert_file

        result = convert_file(self.write("rfc7230.txt", self.HEADER))
        headings = [line for line in result.text.splitlines() if line.startswith("#")]
        self.assertTrue(any("Message Format" in line for line in headings), headings)
        self.assertTrue(any(line.startswith("### 3.2.") for line in headings), headings)

    def test_plain_text_is_untouched(self):
        # Заметка инженера не должна превратиться в «RFC 0».
        from reportgen.ingest.convert import convert_file

        result = convert_file(self.write("заметка.txt", "Уровень сигнала снизился на 6 дБ."))
        self.assertEqual("text", result.meta["source_format"])
        self.assertNotIn("rfc", result.meta)

    def test_classified_as_protocols(self):
        # Короткие латинские слова из других направлений («nr», «sim») не
        # должны ловиться внутри английских слов.
        from reportgen.domains import registry

        found = registry(ROOT / "templates" / "domains.json").classify(
            "RFC 7230. Hypertext Transfer Protocol (HTTP/1.1): Message Syntax and Routing",
            "header fields, request line, status code, message format, transfer-encoding",
        )
        self.assertEqual("protocols", found)


class InterfaceDomainIdTests(unittest.TestCase):
    """Направления, зашитые в интерфейсе, обязаны быть в справочнике.

    После переименования направлений под категории компании примеры вопросов
    у помощника остались со старыми: «modulation» и «measurement». Название
    для них взять было неоткуда, и на первом же экране, который видит новый
    военнослужащий, вместо «Обработка сигналов» стояло английское слово из кода.
    """

    def known_ids(self):
        from reportgen.domains import registry

        return {item["id"] for item in registry(ROOT / "templates" / "domains.json").to_dict()}

    def test_chat_examples_use_real_domains(self):
        app_js = (ROOT / "src" / "reportgen" / "web" / "static" / "app.js").read_text(
            encoding="utf-8")
        block = app_js[app_js.index("const CHAT_EXAMPLES"):]
        block = block[:block.index("];")]
        used = set(re.findall(r"domain:\s*'([\w-]+)'", block))
        self.assertTrue(used, "примеры вопросов исчезли из интерфейса")
        unknown = sorted(used - self.known_ids())
        self.assertFalse(unknown, f"направлений нет в справочнике: {unknown}")


class SupersededStatusTests(unittest.TestCase):
    """Отменённая редакция обязана выпадать из поиска.

    Разборщик RFC ставил пометку об отмене в meta, а колонка `status`
    оставалась «действующий»: приём её не переносил. Пометка была видна в
    карточке, поиск же продолжал выдавать RFC 2616 наравне с заменившим его
    7230 — ровно та ошибка, ради которой разбор шапки и затевался.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.library = self.dir / "library" / "standards" / "rfc"
        self.library.mkdir(parents=True)
        from reportgen.store.db import Database
        from reportgen.store.repo import Repositories

        database = Database(":memory:")
        database.migrate()
        self.repos = Repositories(database)

    def tearDown(self):
        self._tmp.cleanup()

    OBSOLETE = (
        "Network Working Group                                      R. Fielding\n"
        "Request for Comments: 2616                                    UC Irvine\n"
        "Obsoleted by: 7230, 7231\n"
        "Obsoletes: 2068                                               June 1999\n\n"
        "        Hypertext Transfer Protocol -- HTTP/1.1\n\n"
        "1 Introduction\n\n   The Hypertext Transfer Protocol is an application-level"
        " protocol for distributed, collaborative, hypermedia information systems.\n"
    )
    CURRENT = (
        "Internet Engineering Task Force (IETF)                  R. Fielding\n"
        "Request for Comments: 7230                                    Adobe\n"
        "Obsoletes: 2145, 2616                                     June 2014\n\n"
        "  Hypertext Transfer Protocol (HTTP/1.1): Message Syntax and Routing\n\n"
        "Abstract\n\n   The Hypertext Transfer Protocol is a stateless application-level"
        " protocol for distributed, collaborative, hypertext information systems.\n"
    )

    def ingest(self):
        from reportgen.ingest.pipeline import ingest_directory

        (self.library / "rfc2616.txt").write_text(self.OBSOLETE, encoding="utf-8")
        (self.library / "rfc7230.txt").write_text(self.CURRENT, encoding="utf-8")
        ingest_directory(self.repos, self.dir / "library")
        return {doc.doc_id: doc for doc in self.repos.documents.list()}

    def test_status_reaches_the_column_not_only_meta(self):
        docs = self.ingest()
        obsolete = docs["standards/rfc/rfc2616"]
        self.assertEqual("superseded", obsolete.status)
        self.assertEqual("standards/rfc/rfc7230", obsolete.superseded_by)
        self.assertEqual("current", docs["standards/rfc/rfc7230"].status)

    def test_superseded_document_is_not_found(self):
        from reportgen.search import DatabaseRetriever

        self.ingest()
        found = DatabaseRetriever(self.repos).search(
            "hypertext transfer protocol", top_k=10)
        doc_ids = {getattr(hit, "doc_id", None) or hit.chunk.doc_id for hit in found}
        self.assertIn("standards/rfc/rfc7230", doc_ids)
        self.assertNotIn("standards/rfc/rfc2616", doc_ids)

    def test_the_engineers_own_decision_is_not_overwritten(self):
        # Инженер знает про свою библиотеку больше разборщика шапки: если он
        # отправил документ в архив, повторный приём не должен это отменять.
        self.ingest()
        self.repos.documents.set_status("standards/rfc/rfc7230", "archived", "")
        self.ingest()
        docs = {doc.doc_id: doc for doc in self.repos.documents.list()}
        self.assertEqual("archived", docs["standards/rfc/rfc7230"].status)


class EnglishDomainTests(unittest.TestCase):
    """Направление у импортных документов.

    Паспорта на микросхемы приёмопередатчиков и синтезаторов приходят от
    производителя по-английски. В справочнике направлений у «аппаратно-
    программных комплексов» и «методик» латинских слов не было вовсе, и такие
    документы уезжали в «прочее».
    """

    def classify(self, title, text):
        from reportgen.domains import registry

        return registry(ROOT / "templates" / "domains.json").classify(title, text)

    def test_english_datasheet_is_hardware(self):
        found = self.classify(
            "AD9361 RF Agile Transceiver Data Sheet",
            "FEATURES RF 2x2 transceiver with integrated 12-bit DAC and ADC. "
            "Fractional-N PLL synthesizer. Absolute maximum ratings.",
        )
        self.assertEqual("hardware", found)

    def test_english_test_procedure_is_a_method(self):
        found = self.classify(
            "Test procedure for BER measurement",
            "Test setup includes a signal generator and a spectrum analyzer. "
            "Calibration is performed before each BER test.",
        )
        self.assertEqual("method", found)

    def test_russian_documents_are_unaffected(self):
        self.assertEqual("hardware", self.classify(
            "Модем ХХ-100. Руководство по эксплуатации",
            "Состав изделия, стойка, плата, разъём, блок питания, наработка на отказ."))
        self.assertEqual("method", self.classify(
            "Методика контроля излучения передатчика",
            "Порядок измерений, анализатор спектра, погрешность, протокол измерений."))


class DepartmentWordingTests(unittest.TestCase):
    """Документ раскладывается по полке и тогда, когда написан по-нашему.

    Слово в справочнике ищется подстрокой, поэтому словарная форма ловит не
    все падежи: «перемежение» не находится в «перемежения», а «рид-соломон»
    — в «Рида-Соломона». Отдел пишет ещё и своими сокращениями: СЛС, ФМ-4,
    КАМ-16, «оцифрованный участок спектра».

    Проверяем не только итоговую полку: направление ставится по числу
    совпадений, и в живом тексте их набирается с запасом — пропажа одного
    слова осталась бы незамеченной. Поэтому каждое слово проверяется
    отдельно, на короткой строке, где совпасть больше нечему.
    """

    #: Строка → направление, которое обязано её узнать.
    WORDING = [
        ("перемежения кодовых слов", "signal"),
        ("кода Рида-Соломона", "signal"),
        ("точки созвездия", "signal"),
        ("аддитивный скремблер", "signal"),
        ("синхрослова", "signal"),
        ("пилот-символов", "signal"),
        ("ФМ-4С", "signal"),
        ("КАМ-256", "signal"),
        ("оцифрованный участок", "signal"),
        ("в режиме холостого хода", "signal"),
        ("регистрации СЛС", "satellite"),
        ("массив кадров BBFrame", "satellite"),
        ("заголовки BBHeader", "satellite"),
        ("дейтаграммы", "protocols"),
        ("технология IPsec", "protocols"),
        ("мультиплексных потоков", "protocols"),
        ("квитирования", "protocols"),
        ("ретрансмиссии", "protocols"),
        ("юстировки антенны", "microwave"),
        ("наведения антенны", "microwave"),
        ("мачты", "microwave"),
        ("глубоких замираний", "hf"),
        ("погрешности", "method"),
        ("методики", "method"),
        ("поверки", "method"),
        ("калибровки", "method"),
        ("изделия", "hardware"),
        ("стойки", "hardware"),
        ("неисправности", "hardware"),
        ("наработки", "hardware"),
        ("подключения", "hardware"),
        ("приложения", "software"),
        ("утилиты", "software"),
        ("трассировки", "software"),
        ("рекомендации", "standard"),
        ("редакции", "standard"),
        ("радиопокрытия", "mobile"),
    ]

    def setUp(self):
        from reportgen.domains import registry

        self.registry = registry(ROOT / "templates" / "domains.json")

    def classify(self, title, text):
        return self.registry.classify(title, text)

    def test_every_wording_is_recognised_by_its_domain(self):
        for text, domain_id in self.WORDING:
            with self.subTest(text=text):
                domain = self.registry.get(domain_id)
                self.assertIsNotNone(domain, f"нет направления {domain_id}")
                self.assertGreaterEqual(
                    domain.score(text.lower()), 1,
                    f"«{text}» не узнаётся направлением «{domain_id}»")

    def test_a_letter_of_the_department_lands_on_the_right_shelf(self):
        self.assertEqual("signal", self.classify(
            "Декодирование каскадных кодов",
            "Параметры перемежения кодовых слов кода Рида-Соломона; "
            "точки созвездия при модуляционном декодировании."))
        self.assertEqual("microwave", self.classify(
            "Разбор сигналов РРЛС",
            "Сигналы РРЛС на пролёте; юстировка антенны и профиль трассы."))
        self.assertEqual("satellite", self.classify(
            "Разбор сигналов СЛС",
            "Регистрации СЛС стандарта DVB-S2: массив кадров BBFrame, "
            "снятие заголовков BBHeader."))


class AutoSortingTests(unittest.TestCase):
    """Тип документа определяется по содержимому, когда каталог молчит.

    Библиотеку приносят как есть: папка «Разное», выгрузка со старого
    сервера. Раньше всё это уезжало в «литературу» и перемешивалось: паспорт
    модема оказывался книгой, приказ по предприятию — тоже.
    """

    SAMPLES = (
        ("standards", "ГОСТ Р 53363-2009", "gost.pdf",
         "Настоящий стандарт распространяется на цифровые радиорелейные линии. "
         "Нормативные ссылки. Термины и определения."),
        ("datasheets", "Модем ХХ-100", "modem.pdf",
         "Руководство по эксплуатации. Состав изделия. Технические характеристики. "
         "Комплект поставки."),
        ("reports", "Отчёт 2024-118", "otchet.docx",
         "Технический отчёт по результатам анализа. Заказчик сообщил. "
         "Выявлено несоответствие. Выводы."),
        ("regulations", "Приказ №14", "prikaz.doc",
         "УТВЕРЖДАЮ. Генеральный директор. Настоящий регламент вводится в действие."),
        ("literature", "Основы спутниковой связи", "book.pdf",
         "Учебное пособие. УДК 621.396. ISBN 978-5. Издательство. Оглавление. "
         "Список литературы."),
    )

    def test_each_kind_is_recognised(self):
        from reportgen.ingest.sorting import detect_doc_type

        for expected, title, filename, text in self.SAMPLES:
            with self.subTest(expected=expected):
                found, why = detect_doc_type(title=title, filename=filename, text=text)
                self.assertEqual(expected, found, why)

    #: То же самое по-английски. Паспорта на микросхемы приёмопередатчиков и
    #: синтезаторов производитель пишет только так, и без этих оборотов вся
    #: импортная элементная база оказывалась в «прочем».
    ENGLISH_SAMPLES = (
        ("datasheets", "AD9361 RF Agile Transceiver Data Sheet", "ad9361.pdf",
         "FEATURES. SPECIFICATIONS. ABSOLUTE MAXIMUM RATINGS. "
         "PIN CONFIGURATION AND FUNCTION DESCRIPTIONS. ORDERING INFORMATION."),
        ("datasheets", "", "ADRV9002.pdf",
         "ADRV9002 Data Sheet. Electrical characteristics. "
         "Recommended operating conditions. Functional block diagram."),
        ("literature", "Digital Communications, 5th edition", "proakis.pdf",
         "Preface. Table of contents. Chapter 1 Introduction. "
         "Bibliography. Published by McGraw-Hill."),
    )

    def test_english_documents_are_recognised(self):
        from reportgen.ingest.sorting import detect_doc_type

        for expected, title, filename, text in self.ENGLISH_SAMPLES:
            with self.subTest(expected=expected, filename=filename):
                found, why = detect_doc_type(title=title, filename=filename, text=text)
                self.assertEqual(expected, found, why)

    def test_unknown_goes_to_misc(self):
        from reportgen.ingest.sorting import detect_doc_type

        found, why = detect_doc_type(title="Список телефонов", filename="phones.xlsx",
                                     text="Иванов 101 Петров 102")
        self.assertEqual("misc", found)
        self.assertTrue(why)

    def test_rfc_is_a_standard(self):
        from reportgen.ingest.sorting import detect_doc_type

        found, _ = detect_doc_type(title="RFC 7230", filename="rfc7230.txt",
                                   text="Request for Comments: 7230", meta={"rfc": 7230})
        self.assertEqual("standards", found)

    def test_a_tie_is_not_a_guess(self):
        from reportgen.ingest.sorting import detect_doc_type, score_doc_types

        scores = score_doc_types(text="настоящий стандарт руководство по эксплуатации")
        self.assertGreaterEqual(len(scores), 2)
        found, why = detect_doc_type(text="")
        self.assertEqual("misc", found)

    def test_folder_wins_over_content(self):
        # Если библиотека разложена, спорить с инженером незачем.
        from reportgen.ingest.convert import guess_doc_type

        root = Path("/лит")
        self.assertEqual("standards", guess_doc_type(root / "standards" / "x.pdf", root))
        self.assertIsNone(guess_doc_type(root / "Разное" / "x.pdf", root, default=None))

    def test_misc_is_a_real_type(self):
        from reportgen.corpus import DOC_TYPES

        self.assertIn("misc", DOC_TYPES)

    def test_misc_domain_catches_the_rest(self):
        from reportgen.domains import registry

        found = registry(ROOT / "templates" / "domains.json")
        self.assertEqual("misc", found.catch_all_id())
        self.assertEqual("misc", found.classify("Список телефонов", "Иванов 101 Петров 102"))
