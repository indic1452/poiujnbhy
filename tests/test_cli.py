import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.cli import main
from reportgen.config import Settings, settings_warnings

ROOT = Path(__file__).resolve().parents[1]
CASE = str(ROOT / "examples" / "cases" / "case-2024-118.json")
OUTLINE = str(ROOT / "templates" / "outline_signal_issue.json")
CORPUS = str(ROOT / "examples" / "corpus")
GLOSSARY = str(ROOT / "templates" / "glossary.json")


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(argv)
    return code, out.getvalue()


class DatabaseCliTests(unittest.TestCase):
    """Команды, работающие с установленной системой (SQLite)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "test.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_useradd_and_users(self):
        code, out = run(["--db", self.db, "useradd", "--login", "admin",
                         "--role", "owner", "--password", "пароль12345"])
        self.assertEqual(code, 0, out)
        code, out = run(["--db", self.db, "users"])
        self.assertEqual(code, 0)
        self.assertIn("admin", out)
        # В списке видно должность по-русски, а не служебный owner.
        self.assertIn("Создатель системы", out)

    def test_useradd_records_department_and_team(self):
        code, out = run(["--db", self.db, "useradd", "--login", "ivanov",
                         "--role", "engineer", "--password", "пароль12345",
                         "--name", "Иванов И. И.", "--department", "Отдел связи",
                         "--team", "1 группа"])
        self.assertEqual(code, 0, out)
        from reportgen.store.repo import Repositories
        repos = Repositories.open(self.db)
        user = repos.users.by_login("ivanov")
        self.assertEqual("Отдел связи", user.department)
        self.assertEqual("1 группа", user.team)
        repos.close()

    def test_useradd_rejects_short_password(self):
        code, out = run(["--db", self.db, "useradd", "--login", "u", "--password", "123"])
        self.assertEqual(code, 1)
        self.assertIn("8 символов", out)

    def test_useradd_rejects_duplicate(self):
        run(["--db", self.db, "useradd", "--login", "admin", "--password", "пароль12345"])
        code, out = run(["--db", self.db, "useradd", "--login", "admin",
                         "--password", "пароль12345"])
        self.assertEqual(code, 1)
        self.assertIn("уже существует", out)

    def test_passwd_changes_password(self):
        run(["--db", self.db, "useradd", "--login", "u1", "--password", "пароль12345"])
        code, out = run(["--db", self.db, "passwd", "--login", "u1",
                         "--password", "другойпароль"])
        self.assertEqual(code, 0, out)

    def test_passwd_unknown_user(self):
        code, out = run(["--db", self.db, "passwd", "--login", "нет", "--password", "пароль12345"])
        self.assertEqual(code, 1)

    def test_users_on_empty_base(self):
        code, out = run(["--db", self.db, "users"])
        self.assertEqual(code, 1)
        self.assertIn("useradd", out)

    def test_library_on_empty_base(self):
        code, out = run(["--db", self.db, "library"])
        self.assertEqual(code, 1)
        self.assertIn("пуста", out)

    def test_stats_prints_json(self):
        import json as _json

        code, out = run(["--db", self.db, "stats"])
        self.assertEqual(code, 0, out)
        payload = _json.loads(out)
        self.assertEqual(payload["cases"]["total"], 0)


class EmbedCommandTests(unittest.TestCase):
    """«reportgen embed» — построение векторов из консоли."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "test.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_batch_setting_reaches_the_indexer(self):
        """REPORTGEN_EMBED_BATCH не влиял на построение вовсе.

        Пачки всегда шли по 16, сколько ни ставь: команда создавала клиента
        без batch и звала индексатор без batch. На сервере, который держит
        по 64 текста за раз, это вчетверо дольше — а библиотека строится
        часами.
        """
        import os
        from unittest import mock

        from reportgen import embeddings

        seen = {}

        class Spy(embeddings.StubEmbedder):
            def __init__(self, **kwargs):
                super().__init__()
                seen["client_batch"] = kwargs.get("batch")

        def spy_index(repos, client, *, batch=32, only_missing=True, progress=None):
            seen["index_batch"] = batch
            return 0

        with mock.patch.object(embeddings, "EmbeddingClient", Spy), \
                mock.patch.object(embeddings, "index_embeddings", spy_index), \
                mock.patch.dict(os.environ, {"REPORTGEN_EMBED_BATCH": "64"}):
            code, out = run(["--db", self.db, "embed"])
        self.assertEqual(0, code, out)
        self.assertEqual(64, seen["client_batch"])
        self.assertEqual(64, seen["index_batch"])


class CliTests(unittest.TestCase):
    def test_full_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = str(Path(tmp) / "index.json")
            report = str(Path(tmp) / "report.md")

            code, out = run(["index", "--corpus", CORPUS, "--out", index])
            self.assertEqual(code, 0, out)
            self.assertTrue(Path(index).exists())

            code, out = run(["check-facts", "--facts", CASE, "--outline", OUTLINE])
            self.assertEqual(code, 0, out)

            code, out = run([
                "generate", "--facts", CASE, "--outline", OUTLINE, "--index", index,
                "--out", report, "--llm", "stub", "--generated-at", "2024-07-16",
                "--glossary", GLOSSARY,
            ])
            self.assertEqual(code, 0, out)
            self.assertTrue(Path(report).exists())
            self.assertTrue(Path(report).with_suffix(".meta.json").exists())

            code, out = run(["verify", "--facts", CASE, "--report", report, "--outline", OUTLINE])
            self.assertEqual(code, 0, out)

    def test_verify_blocks_invented_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_text(
                "# Отчёт\n\n## 1. Выводы\n\nЗапас по мощности составил 7.3 дБ.\n",
                encoding="utf-8",
            )
            code, out = run(["verify", "--facts", CASE, "--report", str(report)])
            self.assertEqual(code, 1)
            self.assertIn("ЭКСПОРТ ЗАБЛОКИРОВАН", out)

    def test_check_facts_reports_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            import json

            raw = json.loads(Path(CASE).read_text(encoding="utf-8"))
            del raw["measurements"]["snr"]
            path = Path(tmp) / "case.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            code, out = run(["check-facts", "--facts", str(path), "--outline", OUTLINE])
            self.assertEqual(code, 1)
            self.assertIn("snr", out)

    def test_bad_fact_pack_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text('{"report_type": "signal_issue"}', encoding="utf-8")
            code, out = run(["check-facts", "--facts", str(path), "--outline", OUTLINE])
            self.assertEqual(code, 2)
            self.assertIn("case_id", out)


class SettingsWarningTests(unittest.TestCase):
    """Настройки, от которых система не падает, а работает вполсилы.

    Такую беду не видно ниоткуда, а на изолированной машине спросить
    некого. Раньше умолчание адреса реранкера совпадало с адресом
    эмбеддингов: реранк спрашивал оценки у эмбеддера, тот отвечал ошибкой,
    и реранк молча пропускался. Умолчание поправлено, но в уже написанном
    settings.json отдела старый адрес остался — сказать об этом должна
    сама система.
    """

    def test_one_address_for_two_services_is_named(self):
        settings = Settings.load(
            embed_enabled=True, rerank_enabled=True,
            embed_base_url="http://127.0.0.1:8001/v1",
            rerank_base_url="http://127.0.0.1:8001/v1")
        trouble = "\n".join(settings_warnings(settings))
        self.assertIn("один адрес", trouble)
        # И сказано, что именно править: иначе предупреждение бесполезно.
        self.assertIn("rerank_base_url", trouble)
        self.assertIn("8002", trouble)

    def test_rerank_without_dense_search_is_named(self):
        settings = Settings.load(embed_enabled=False, rerank_enabled=True)
        self.assertIn("embed_enabled", "\n".join(settings_warnings(settings)))

    def test_a_correct_setup_is_silent(self):
        # Предупреждение на пустом месте приучает не читать предупреждения.
        settings = Settings.load(embed_enabled=True, rerank_enabled=True)
        self.assertEqual([], settings_warnings(settings))

    def test_a_switched_off_reranker_is_not_a_trouble(self):
        settings = Settings.load(
            embed_enabled=True, rerank_enabled=False,
            embed_base_url="http://127.0.0.1:8001/v1",
            rerank_base_url="http://127.0.0.1:8001/v1")
        self.assertEqual([], settings_warnings(settings))


if __name__ == "__main__":
    unittest.main()


class ДогрузкаПачкиИзКонсоли(unittest.TestCase):
    """«reportgen ingest <папка внутри библиотеки>» — штатная догрузка.

    Документы приносят пачками, и перебирать ради полусотни новых файлов всю
    библиотеку незачем: под это в load-library.ps1 и заведён ключ -Path. Но
    консольный приём считал указанный каталог корнем корпуса целиком, а
    веб-приём — всегда от корня библиотеки. Расхождение стоило дорого: тот же
    файл, принятый двумя путями, заводился двумя записями с разными
    идентификаторами, и в выдачу шли парные фрагменты.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        корень = Path(self._tmp.name)
        self.библиотека = корень / "library"
        self.пачка = self.библиотека / "standards" / "новая-пачка"
        self.пачка.mkdir(parents=True)
        текст = ("Рекомендация описывает порядок стафингования в цифровом "
                 "тракте и допуски на отклонение тактовой частоты. " * 8)
        (self.пачка / "рекомендация.md").write_text(
            "# Рекомендация\n\n" + текст, encoding="utf-8")
        (self.библиотека / "standards" / "старое.md").write_text(
            "# Старое\n\n" + текст, encoding="utf-8")
        import json
        self.конфиг = корень / "config.json"
        self.конфиг.write_text(json.dumps({"data_dir": str(корень)}),
                               encoding="utf-8")

    def _документы(self):
        from reportgen.store.repo import Repositories
        repos = Repositories.open(str(Path(self._tmp.name) / "reportgen.db"))
        try:
            return sorted(d.doc_id for d in repos.documents.list())
        finally:
            repos.close()

    def _принять(self, путь):
        return run(["--config", str(self.конфиг), "ingest", str(путь)])

    def test_папка_внутри_библиотеки_принимается(self):
        код, вывод = self._принять(self.пачка)
        self.assertEqual(0, код, вывод)
        self.assertIn("добавлено 1", вывод)
        self.assertNotIn("подходящих файлов не найдено", вывод)

    def test_идентификатор_такой_же_как_при_полной_загрузке(self):
        self._принять(self.пачка)
        self.assertEqual(["standards/новая-пачка/рекомендация"], self._документы())

    def test_полная_загрузка_после_догрузки_не_плодит_двойников(self):
        self._принять(self.пачка)
        код, вывод = self._принять(self.библиотека)
        self.assertEqual(0, код, вывод)
        self.assertEqual(
            ["standards/новая-пачка/рекомендация", "standards/старое"],
            self._документы(),
        )
        self.assertNotIn("одинаковое содержимое", вывод)

    def test_догрузка_не_уводит_в_архив_остальную_библиотеку(self):
        self._принять(self.библиотека)
        self._принять(self.пачка)
        from reportgen.store.repo import Repositories
        repos = Repositories.open(str(Path(self._tmp.name) / "reportgen.db"))
        try:
            старое = repos.documents.by_doc_id("standards/старое")
            self.assertEqual("current", старое.status)
        finally:
            repos.close()

    def test_один_файл_внутри_библиотеки_считается_от_её_корня(self):
        код, вывод = self._принять(self.пачка / "рекомендация.md")
        self.assertEqual(0, код, вывод)
        self.assertEqual(["standards/новая-пачка/рекомендация"], self._документы())

    def _со_стороны(self):
        """Пачка, лежащая ВНЕ библиотеки: принесли на флешке, грузим оттуда."""
        чужое = Path(self._tmp.name) / "флешка" / "standards"
        чужое.mkdir(parents=True)
        (чужое / "чужой.md").write_text(
            "# Чужой\n\n" + "Текст принесённого документа. " * 20, encoding="utf-8")
        return чужое.parent

    def test_папка_вне_библиотеки_считается_от_себя(self):
        # Считать её от корня библиотеки нельзя: получился бы путь с «..», а
        # это не идентификатор. Прежнее поведение здесь и есть правильное.
        код, вывод = self._принять(self._со_стороны())
        self.assertEqual(0, код, вывод)
        self.assertEqual(["standards/чужой"], self._документы())

    def test_один_файл_вне_библиотеки_считается_от_своего_каталога(self):
        код, вывод = self._принять(self._со_стороны() / "standards" / "чужой.md")
        self.assertEqual(0, код, вывод)
        self.assertEqual(["чужой"], self._документы())

    def test_тип_файла_со_стороны_берётся_из_текста(self):
        """Каталог файла со стороны — не каталог типа внутри библиотеки.

        Идентификатор у такого файла выходит одинаковый, откуда ни считай, а
        вот ТИП — нет. Считая его от корня библиотеки, приём поднялся бы по
        чужому дереву вверх и увидел там папку «standards» — то есть присвоил
        бы тип по имени папки на флешке, до которой библиотеке дела нет.
        Указали на файл — каталог этого файла и есть корень, а тип берётся из
        содержимого, как и для файла, положенного в корень библиотеки.
        """
        from reportgen.store.repo import Repositories

        чужое = self._со_стороны() / "standards"
        (чужое / "книга.md").write_text(
            "# Глава 1\n\n" + "Изложение основ теории связи. " * 30, encoding="utf-8")
        код, вывод = self._принять(чужое / "книга.md")
        self.assertEqual(0, код, вывод)
        repos = Repositories.open(str(Path(self._tmp.name) / "reportgen.db"))
        try:
            документ = repos.documents.by_doc_id("книга")
            self.assertNotEqual("каталог", документ.meta.get("doc_type_source"),
                                "тип взят по имени папки за пределами библиотеки")
        finally:
            repos.close()
