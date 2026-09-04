# -*- coding: utf-8 -*-
"""Карточка «Библиотека собралась»: итог приёма, переживающий консоль.

Хозяин системы пересобирает тринадцать тысяч документов и должен одним
взглядом убедиться, что всё собралось. Сегодня итог приёма виден только в
консоли PowerShell, а её содержимое уезжает вверх задолго до конца: сколько
принято и какие файлы не приняты — узнать уже неоткуда, IngestResult живёт
только в памяти процесса.

Главное, что здесь заперто: числа берутся КАК ЕСТЬ, а не вычитанием одного из
другого. «Файлов на диске минус документов в базе» назвать потерей нельзя —
приём намеренно не заводит второй документ для файла с тем же содержимым, и
таких пар в библиотеке отдела много (скан и распознанная версия, .md и
выгрузка в .txt). Такая арифметика показала бы «потеряно 400» на безупречной
сборке.
"""

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.corpus import Chunk
from reportgen.ingest.pipeline import ingest_directory, save_ingest_report
from reportgen.store.db import Database
from reportgen.store.repo import LibraryReportRepo, Repositories


class ХранениеИтога(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.корень = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.репо = Repositories(Database(self.корень / "б.sqlite"))

    def библиотека(self, имя="библиотека"):
        путь = self.корень / имя / "standards"
        путь.mkdir(parents=True)
        return self.корень / имя, путь

    def test_итог_переживает_закрытие_консоли(self):
        корень, полка = self.библиотека()
        (полка / "документ.md").write_text(
            "# Методика\n\n" + "Содержательный текст методики. " * 20, encoding="utf-8")
        итог = ingest_directory(self.репо, корень)
        self.assertEqual(1, итог.added)

        # Читаем ДРУГИМИ репозиториями поверх той же базы: так и живёт
        # веб-сервер, поднятый после того, как консоль закрыли.
        свежие = Repositories(Database(self.корень / "б.sqlite"))
        запись = свежие.library_report.load(str(корень))
        self.assertEqual(1, запись["added"])
        self.assertEqual(1, запись["files_seen"])
        self.assertTrue(запись["finished_at"])

    def test_приём_папки_не_затирает_итог_всей_библиотеки(self):
        """«load-library.ps1 -Path …» — штатная операция пополнения."""
        корень, полка = self.библиотека()
        for имя in ("первый", "второй", "третий"):
            (полка / f"{имя}.md").write_text(f"# {имя}\n\n" + "Текст. " * 40,
                                             encoding="utf-8")
        ingest_directory(self.репо, корень)

        отдельная = self.корень / "пачка" / "standards"
        отдельная.mkdir(parents=True)
        (отдельная / "новый.md").write_text(
            "# Новый\n\n" + "Совсем другой текст пачки. " * 40, encoding="utf-8")
        ingest_directory(self.репо, self.корень / "пачка")

        целиком = self.репо.library_report.load(str(корень))
        self.assertEqual(3, целиком["added"], "итог сборки библиотеки затёрт приёмом папки")
        отдельно = self.репо.library_report.load(str(self.корень / "пачка"))
        self.assertEqual(1, отдельно["added"])

    def test_непринятые_файлы_названы_поимённо(self):
        корень, полка = self.библиотека()
        (полка / "хороший.md").write_text("# Раз\n\n" + "Текст. " * 40, encoding="utf-8")
        (полка / "пустой.md").write_text("", encoding="utf-8")
        ingest_directory(self.репо, корень)
        запись = self.репо.library_report.load(str(корень))
        self.assertEqual(1, запись["failed"])
        self.assertEqual(1, запись["failures_total"])
        self.assertTrue(any("пустой" in строка for строка in запись["failures"]))

    def test_список_непринятых_обрезан_по_пределу(self):
        """На тринадцати тысячах файлов полный список весит мегабайты."""
        from reportgen.ingest.pipeline import IngestResult
        итог = IngestResult()
        итог.failures = [f"файл{n}.pdf: не читается" for n in range(500)]
        запись = save_ingest_report(self.репо, self.корень, итог)
        self.assertEqual(500, запись["failures_total"])
        self.assertEqual(LibraryReportRepo.MAX_NAMES, len(запись["failures"]))

    def test_итога_нет_у_библиотеки_собранной_прежде(self):
        self.assertEqual({}, self.репо.library_report.load(str(self.корень)))


class ЧислаПоДокументам(unittest.TestCase):
    """Одним запросом: карточку открывают на полумиллионе фрагментов."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.корень = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.репо = Repositories(Database(self.корень / "б.sqlite"))

    def документ(self, doc_id, фрагментов=3, склеен=False):
        д = self.репо.documents.upsert(
            doc_id=doc_id, doc_type="standards", title=doc_id,
            source_path=f"{doc_id}.pdf", sha256=doc_id,
            meta={"text_quality": "glued"} if склеен else {},
            domain="misc", year=2020, status="current", superseded_by="",
            size=1, mtime_ns=1)
        if фрагментов:
            self.репо.chunks.replace_for_document(д, [
                Chunk(chunk_id=f"{doc_id}#{i}", doc_id=doc_id, doc_type="standards",
                      title_path=["Д"], text="слово " * 40)
                for i in range(фрагментов)])
        return д

    def test_считает_всё_разом(self):
        self.документ("целый")
        self.документ("склеенный", склеен=True)
        self.документ("пустой", фрагментов=0)
        числа = self.репо.library_report.counts()
        self.assertEqual(3, числа["documents"])
        self.assertEqual(6, числа["chunks"])
        self.assertEqual(1, числа["empty"])
        self.assertEqual(1, числа["glued"])

    def test_пустая_база_не_ломает_счёт(self):
        self.assertEqual({"documents": 0, "chunks": 0, "empty": 0, "glued": 0},
                         self.репо.library_report.counts())

    def test_отбор_документов_без_фрагментов(self):
        """По этому отбору человек и попадает из карточки в список."""
        self.документ("целый")
        self.документ("пустой", фрагментов=0)
        найдено = self.репо.documents.list(None, None, None, "", "empty")
        self.assertEqual(["пустой"], [д.doc_id for д in найдено])

    def test_прежний_отбор_склеенных_работает_по_старому(self):
        self.документ("целый")
        self.документ("склеенный", склеен=True)
        найдено = self.репо.documents.list(None, None, None, "", "glued")
        self.assertEqual(["склеенный"], [д.doc_id for д in найдено])


class СводкаЧерезСервер(unittest.TestCase):
    """То же самое, но так, как это увидит человек: через настоящий сервер.

    Проверка не косметическая: итог приёма пишет ОДНО соединение с базой, а
    читает его веб-сервер СВОИМ. Незавершённая транзакция для него не
    существует — карточка была бы пустой ровно в том случае, ради которого она
    и сделана: консоль закрыли, итог смотрят в браузере.
    """

    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415 — общая обвязка сервера
        self.обвязка = WebTestCase("run")
        self.обвязка.setUp()
        self.addCleanup(self.обвязка.tearDown)
        self.client = self.обвязка.client

    def test_сводка_отдаётся_и_считает_документы(self):
        self.обвязка.login("admin")
        тело = self.client.get("/api/library/summary").json()
        self.assertIn("counts", тело)
        self.assertIn("documents", тело["counts"])
        self.assertIn("report", тело)

    def test_сохранённый_итог_доезжает_до_карточки(self):
        """Пишет итог одно соединение, читает — другое: без завершения
        транзакции карточка осталась бы пустой."""
        from reportgen.ingest.pipeline import IngestResult  # noqa: PLC0415

        репо = self.обвязка.repos
        корень = str(self.обвязка.service.settings.library_dir)
        итог = IngestResult(added=7, updated=1, skipped=2, failed=3)
        итог.failures = ["скан.pdf: текста нет"]
        save_ingest_report(репо, корень, итог)

        self.обвязка.login("admin")
        тело = self.client.get("/api/library/summary").json()
        self.assertEqual(7, тело["report"]["added"])
        self.assertEqual(3, тело["report"]["failed"])
        self.assertEqual(["скан.pdf: текста нет"], тело["report"]["failures"])


if __name__ == "__main__":
    unittest.main()
