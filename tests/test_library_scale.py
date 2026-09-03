"""Библиотека отдела: тринадцать тысяч документов и полмиллиона фрагментов.

На такой библиотеке ломается не логика, а то, что при сотне документов
работало незаметно: раздел отдавался целиком одним ответом, короткий раздел
оседал в указателе строкой-обрывком, а плохо разобранный файл выглядел в
списке точно так же, как целый.
"""

import unittest
from unittest import mock
from pathlib import Path

import _bootstrap  # noqa: F401
from fastapi.testclient import TestClient

from reportgen.config import Settings
from reportgen.corpus import MIN_CHARS, merge_short_sections, split_document
from reportgen.ingest.convert import glued_text_warning
from reportgen.llm import StubLLM
from reportgen.store.db import Database
from reportgen.store.repo import Repositories
from reportgen.web.api import LIBRARY_PAGE, LIBRARY_PAGE_MAX
from reportgen.web.app import create_app
from reportgen.web.service import ReportService

ROOT = Path(__file__).resolve().parents[1]


class PagingTests(unittest.TestCase):
    """Раздел отдавался целиком: несколько мегабайт JSON на каждое открытие."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=False,
            templates_dir=str(ROOT / "templates"))
        self.repos = Repositories(Database(":memory:"))
        for index in range(120):
            self.repos.documents.upsert(
                doc_id=f"literature/tom-{index:03d}", doc_type="literature",
                title=f"Том {index:03d} по спутниковой связи",
                source_path=f"tom-{index}.md",
                sha256=str(index).ljust(64, "0"))
        self.repos.documents.upsert(
            doc_id="standards/gost-r-51317", doc_type="standards",
            title="ГОСТ Р 51317 по электромагнитной совместимости",
            source_path="gost.md", sha256="g" * 64)
        service = ReportService(repos=self.repos, settings=settings, llm=StubLLM())
        self.client = TestClient(create_app(settings, self.repos, service))
        self.addCleanup(self.client.close)

    def page(self, **params) -> dict:
        response = self.client.get("/api/library", params=params)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_a_page_is_a_page_and_not_the_whole_library(self):
        data = self.page()
        self.assertEqual(LIBRARY_PAGE, len(data["items"]))
        self.assertEqual(121, data["total"])
        self.assertEqual(3, data["pages"])

    def test_the_second_page_continues_the_first(self):
        first = [item["doc_id"] for item in self.page(page=1)["items"]]
        second = [item["doc_id"] for item in self.page(page=2)["items"]]
        self.assertEqual(50, len(second))
        self.assertFalse(set(first) & set(second))

    def test_a_page_beyond_the_last_gives_the_last(self):
        # Иначе человек, нажав «вперёд» лишний раз, получал пустой экран и
        # решал, что библиотека кончилась.
        data = self.page(page=99)
        self.assertEqual(3, data["page"])
        self.assertTrue(data["items"])

    def test_the_search_is_by_name_and_by_identifier(self):
        by_name = self.page(q="ГОСТ Р 51317")
        self.assertEqual(1, by_name["total"])
        by_id = self.page(q="gost-r-51317")
        self.assertEqual(1, by_id["total"])

    def test_a_piece_of_a_word_is_enough(self):
        """Названия в библиотеке длинные, целиком их никто не набирает."""
        self.assertEqual(1, self.page(q="электромагнитной")["total"])

    def test_the_page_size_has_a_ceiling(self):
        # Иначе «per_page=100000» возвращает ту же беду, от которой уходили.
        data = self.page(per_page=100000)
        self.assertEqual(LIBRARY_PAGE_MAX, data["per_page"])

    def test_a_filter_narrows_the_count_too(self):
        data = self.page(doc_type="standards")
        self.assertEqual(1, data["total"])
        self.assertEqual(1, len(data["items"]))


class MergeTests(unittest.TestCase):
    """Фрагмент в одну строку — это место в выдаче, занятое ничем."""

    def test_a_short_first_section_joins_the_next_one(self):
        """Раньше короткий раздел клеился только к ПРЕДЫДУЩЕМУ.

        Первому разделу приклеиваться было некуда, и он оседал в указателе
        строкой «Общие положения».
        """
        text = "# Том\n\n## Введение\n\nОдна строка.\n\n## Глава 1\n\n" + "Текст. " * 80
        pieces = list(merge_short_sections(split_document(text)))
        self.assertEqual(1, len(pieces))
        self.assertIn("Одна строка", pieces[0][1])
        self.assertIn("Текст.", pieces[0][1])

    def test_short_sections_in_a_row_become_one_fragment(self):
        text = "".join(f"## Раздел {i}\n\nКоротко.\n\n" for i in range(10))
        pieces = list(merge_short_sections(split_document(text)))
        self.assertTrue(all(len(text) >= 40 for _, text in pieces))
        self.assertLess(len(pieces), 10)

    def test_the_heading_of_a_glued_section_is_not_lost(self):
        # Иначе слова из заголовка пропали бы из поиска вместе с ним.
        text = "## Первый\n\nМало.\n\n## Приложения\n\n" + "Много текста. " * 60
        merged = list(merge_short_sections(split_document(text)))[0][1]
        self.assertIn("Приложения", merged)

    def test_a_long_section_is_left_alone(self):
        long_text = "Абзац про измерения. " * 40
        text = f"## Глава\n\n{long_text}\n\n## Другая\n\n{long_text}"
        pieces = list(merge_short_sections(split_document(text)))
        self.assertEqual(2, len(pieces))

    def test_the_threshold_is_the_corpus_one(self):
        self.assertEqual(200, MIN_CHARS)


class GluedTextTests(unittest.TestCase):
    """«Методыцифровогокодирования» — так выглядит плохо разобранный PDF."""

    GOOD = "Занимаемая полоса частот измеряется методом 99 процентов мощности. " * 12
    BAD = "Занимаемаяполосачастотизмеряетсяметодом99процентовмощности." * 12

    def test_glued_text_is_named(self):
        self.assertIn("склеен", glued_text_warning(self.BAD))

    def test_normal_text_is_not_complained_about(self):
        self.assertEqual("", glued_text_warning(self.GOOD))

    def test_a_short_caption_is_not_a_document(self):
        # В подписи «Рис. 1» пробелов и не должно быть много.
        self.assertEqual("", glued_text_warning("Рис.1"))

    def test_numbers_and_frames_are_not_glued_text(self):
        """Таблица из чисел — не склейка: букв в ней почти нет."""
        table = "|1234|5678|9012|\n" * 60
        self.assertEqual("", glued_text_warning(table))

    def test_the_warning_says_what_to_do(self):
        self.assertIn("OCR", glued_text_warning(self.BAD))


class WholeLibraryTests(unittest.TestCase):
    """Ничто не должно молча отсекать часть библиотеки."""

    def setUp(self):
        from reportgen.corpus import Chunk

        self.repos = Repositories(Database(":memory:"))
        self.document = self.repos.documents.upsert(
            doc_id="literature/kniga", doc_type="literature", title="Толстая книга",
            source_path="kniga.md", sha256="k" * 64)
        self.repos.chunks.replace_for_document(self.document, [
            Chunk(chunk_id=f"literature/kniga#{i:04d}", doc_id="literature/kniga",
                  doc_type="literature", title_path=["Толстая книга", f"Глава {i // 100 + 1}"],
                  text=f"Фрагмент {i} про измерение полосы частот.")
            for i in range(1500)
        ])

    def test_a_chapter_past_the_first_four_hundred_is_found(self):
        """Раньше главу искали в первых четырёхстах фрагментах.

        В книге на полторы тысячи фрагментов двенадцатой главы там просто
        нет, и помощник отвечал «раздела не нашёл» о разделе, который в
        документе есть.
        """
        found = self.repos.chunks.find_sections(self.document.id, "Глава 13", limit=5)
        self.assertTrue(found)
        self.assertTrue(all("Глава 13" in " ".join(c.title_path) for c in found))

    def test_the_search_for_a_chapter_ignores_the_case(self):
        # SQLite'овский lower() не знает кириллицы: «Глава» и «глава» были
        # для него разными словами.
        self.assertTrue(self.repos.chunks.find_sections(self.document.id, "глава 13"))
        self.assertTrue(self.repos.chunks.find_sections(self.document.id, "ГЛАВА 13"))

    def test_a_long_book_can_be_read_past_the_four_hundredth_fragment(self):
        tail = self.repos.chunks.for_document(self.document.id, limit=10, offset=1490)
        self.assertEqual(10, len(tail))
        self.assertEqual("literature/kniga#1499", tail[-1].chunk_id)

    def test_the_count_is_the_whole_document(self):
        self.assertEqual(1500, self.repos.chunks.count_for_document(self.document.id))


class VectorIndexTests(unittest.TestCase):
    """Матрица векторов на настоящем корпусе — float32, а не списки Python."""

    def build(self, count: int = 500, dim: int = 16):
        repos = Repositories(Database(":memory:"))
        repos.vectors.put_many("bge-m3", {
            f"kniga#{i:04d}": [float((i * j) % 7) + 0.5 for j in range(dim)]
            for i in range(count)})
        return repos

    def test_the_index_holds_every_vector(self):
        index = self.build(500).vectors.load_index("bge-m3")
        self.assertEqual(500, len(index))
        self.assertEqual(16, index.dim)

    def test_the_matrix_is_float32_and_not_a_list_of_lists(self):
        """На корпусе отдела списки Python — это восемнадцать гигабайт."""
        index = self.build(64).vectors.load_index("bge-m3")
        self.assertTrue(hasattr(index.matrix, "dtype"), "матрица не numpy")
        self.assertEqual("float32", str(index.matrix.dtype))

    def test_the_search_returns_the_nearest_first(self):
        repos = Repositories(Database(":memory:"))
        repos.vectors.put_many("bge-m3", {
            "a": [1.0, 0.0], "b": [0.0, 1.0], "c": [0.9, 0.1]})
        best = repos.vectors.load_index("bge-m3").search([1.0, 0.0], k=2)
        self.assertEqual(["a", "c"], [uid for uid, _ in best])

    def test_a_query_of_another_dimension_gives_nothing(self):
        # Вектор чужой модели: молча выдавать бессмысленные оценки нельзя.
        index = self.build(20, dim=8).vectors.load_index("bge-m3")
        self.assertEqual([], index.search([1.0, 0.0], k=5))

    def test_vectors_of_another_model_do_not_get_into_the_index(self):
        repos = self.build(10, dim=16)
        repos.vectors.put_many("e5-large", {"chuzhoy#0": [0.5] * 32})
        index = repos.vectors.load_index("bge-m3")
        self.assertEqual(10, len(index))
        self.assertNotIn("chuzhoy#0", index.uids)

    def test_a_vector_of_the_wrong_length_is_dropped_not_zeroed(self):
        """Под тем же именем модели может лежать строка другой длины.

        Так бывает после смены модели без полной перестройки. Заменить её
        нулями нельзя: нулевой вектор — это не «нет ответа», а фрагмент,
        который ровно одинаково не похож ни на один запрос и при этом
        занимает место в выдаче.
        """
        repos = self.build(10, dim=16)
        repos.vectors.put_many("bge-m3", {"korotkiy#0": [0.5] * 4})
        index = repos.vectors.load_index("bge-m3")
        self.assertNotIn("korotkiy#0", index.uids)
        self.assertEqual(10, len(index))
        self.assertEqual(10, index.matrix.shape[0])

    def test_vectors_added_while_reading_do_not_break_the_search(self):
        """Ровно та ошибка, что видел отдел, спросив помощника при постройке.

        Матрица отводится по числу векторов, а пока её наполняют, фоновое
        построение дописывает новые. Строк приходит больше, чем мест, и
        человеку в ответ на вопрос прилетало «index 83616 is out of bounds
        for axis 0 with size 83616». Лишние векторы берутся в следующий
        раз: число изменилось, значит, кэш всё равно перечитается.
        """
        from unittest import mock

        repos = self.build(500, dim=8)
        real = repos.db.scalar

        def stale(sql, params=()):
            value = real(sql, params)
            if "count(*) FROM embeddings" in sql:
                return value - 84          # столько дописали, пока считали
            return value

        with mock.patch.object(repos.db, "scalar", stale):
            index = repos.vectors.load_index("bge-m3")
        self.assertEqual(416, len(index))
        self.assertEqual(416, index.matrix.shape[0])
        # И поиск по такой матрице работает, а не падает.
        self.assertEqual(5, len(index.search([1.0] * 8, k=5)))

    def test_vectors_removed_while_reading_do_not_leave_empty_rows(self):
        """Обратный случай: строк пришло меньше, чем мест в матрице.

        Пустая строка матрицы — это вектор из мусора: он одинаково «похож»
        на любой запрос и лезет в выдачу вперёд настоящих.
        """
        from unittest import mock

        repos = self.build(100, dim=8)
        real = repos.db.scalar

        def ahead(sql, params=()):
            value = real(sql, params)
            return value + 50 if "count(*) FROM embeddings" in sql else value

        with mock.patch.object(repos.db, "scalar", ahead):
            index = repos.vectors.load_index("bge-m3")
        self.assertEqual(100, len(index))
        self.assertEqual(100, index.matrix.shape[0])

    def test_an_empty_library_is_an_empty_index(self):
        repos = Repositories(Database(":memory:"))
        index = repos.vectors.load_index("bge-m3")
        self.assertEqual(0, len(index))
        self.assertEqual([], index.search([1.0], k=3))

    def test_rows_are_not_unpacked_into_python_lists(self):
        """Строка матрицы читается из байтов как есть.

        Через array("f") каждая из полумиллиона строк заводила тысячу чисел
        Python — одиннадцать секунд на одну загрузку матрицы. Распаковка
        списками допустима один раз, чтобы узнать длину вектора; на строку
        матрицы её быть не должно.
        """
        repos = self.build(300, dim=16)
        from reportgen.store import repo as repo_module

        calls = []
        original = repo_module.unpack_vector

        def counted(blob):
            calls.append(len(blob))
            return original(blob)

        with mock.patch.object(repo_module, "unpack_vector", counted):
            index = repos.vectors.load_index("bge-m3")
        self.assertEqual(300, len(index))
        self.assertLessEqual(len(calls), 1, f"распаковок списками: {len(calls)}")

    def test_the_rows_are_not_sorted_on_the_way_out(self):
        """Сортировка ключа стоит дороже всего чтения.

        Ключ фрагмента текстовый, вектор — четыре килобайта. На «ORDER BY
        chunk_uid» SQLite заводит временное дерево и переносит туда всю
        библиотеку: два с лишним гигабайта через временный файл, двадцать две
        секунды вместо шести. А порядок здесь не нужен: ключи собираются
        рядом с матрицей, строка к строке.
        """
        repos = self.build(200, dim=8)
        seen = []
        original = repos.db.stream

        def spy(sql, params=(), chunk=2000):
            seen.append(" ".join(str(sql).split()).lower())
            return original(sql, params, chunk)

        with mock.patch.object(repos.db, "stream", spy):
            index = repos.vectors.load_index("bge-m3")
        self.assertEqual(200, len(index))
        self.assertTrue(seen, "матрица прочиталась без потока")
        for sql in seen:
            self.assertNotIn("order by", sql, f"сортировка вернулась: {sql}")

    def test_a_key_belongs_to_its_own_row_in_any_order(self):
        """Порядок убрали — значит, связь «ключ ↔ строка» держится сама."""
        repos = Repositories(Database(":memory:"))
        repos.vectors.put_many("bge-m3", {
            "yakor": [1.0, 0.0], "sever": [0.0, 1.0], "zapad": [-1.0, 0.0]})
        original = repos.db.stream

        def reversed_stream(sql, params=(), chunk=2000):
            for rows in original(sql, params, chunk):
                yield list(reversed(rows))

        with mock.patch.object(repos.db, "stream", reversed_stream):
            index = repos.vectors.load_index("bge-m3")
        self.assertEqual(3, len(index))
        self.assertEqual("yakor", index.search([1.0, 0.0], k=1)[0][0])
        self.assertEqual("sever", index.search([0.0, 1.0], k=1)[0][0])

    def test_the_matrix_is_the_same_numbers_as_before(self):
        """Способ распаковки сменился — числа обязаны остаться теми же."""
        repos = Repositories(Database(":memory:"))
        repos.vectors.put_many("bge-m3", {"a": [3.0, 4.0], "b": [0.0, 5.0]})
        index = repos.vectors.load_index("bge-m3")
        # Строки нормированы: (3,4) → (0.6,0.8), (0,5) → (0,1).
        self.assertAlmostEqual(0.6, float(index.matrix[0][0]), places=5)
        self.assertAlmostEqual(0.8, float(index.matrix[0][1]), places=5)
        self.assertAlmostEqual(1.0, float(index.matrix[1][1]), places=5)


class MissingVectorTests(unittest.TestCase):
    """Узнать, чего не хватает, — по ключам, а не по самим векторам.

    ``missing`` звался в начале каждого захода построения и тянул из базы
    двухгигабайтные BLOB, чтобы посмотреть на ключи. На библиотеке отдела
    это шесть секунд распаковки и полмиллиарда лишних объектов — на каждый
    заход, а заходов до трёх.
    """

    def setUp(self):
        self.repos = Repositories(Database(":memory:"))
        self.repos.vectors.put_many("bge-m3", {
            f"kniga#{i:04d}": [float(i), 1.0] for i in range(0, 40, 2)})
        self.uids = [f"kniga#{i:04d}" for i in range(40)]

    def test_the_missing_ones_are_named(self):
        gaps = self.repos.vectors.missing(self.uids)
        self.assertEqual([f"kniga#{i:04d}" for i in range(1, 40, 2)], gaps)

    def test_the_vector_itself_is_never_read(self):
        rows = []
        original = self.repos.db.query

        def spy(sql, params=()):
            rows.append(" ".join(str(sql).split()))
            return original(sql, params)

        with mock.patch.object(self.repos.db, "query", spy):
            self.repos.vectors.missing(self.uids)
        self.assertTrue(rows, "запросов не было вовсе")
        for sql in rows:
            self.assertNotIn("vector", sql.lower(), f"вектор всё ещё читается: {sql}")

    def test_nothing_is_asked_of_the_base_for_an_empty_list(self):
        with mock.patch.object(self.repos.db, "query", side_effect=AssertionError):
            self.assertEqual(set(), self.repos.vectors.present([]))
            self.assertEqual([], self.repos.vectors.missing([]))

    def test_a_full_library_leaves_nothing_missing(self):
        even = [f"kniga#{i:04d}" for i in range(0, 40, 2)]
        self.assertEqual([], self.repos.vectors.missing(even))
        self.assertEqual(set(even), self.repos.vectors.present(even))


class QualityCheckTests(unittest.TestCase):
    """Плохо разобранные документы в УЖЕ готовой библиотеке.

    Склейку система замечает при приёме. Но библиотека отдела собрана
    раньше: тринадцать тысяч документов лежат без единой пометки, и найти
    среди них плохие можно было только глазами. Перезагружать библиотеку
    ради этого нельзя — повторный разбор PDF занимает часы и ничего не
    изменит: файл как разобрался, так и разберётся.
    """

    GOOD = "Занимаемая полоса частот измеряется методом 99 процентов мощности. " * 12
    BAD = "Занимаемаяполосачастотизмеряетсяметодом99процентовмощности." * 12

    def build(self, *texts):
        from reportgen.corpus import Chunk
        from reportgen.web.quality import QualityChecker

        repos = Repositories(Database(":memory:"))
        for index, text in enumerate(texts):
            document = repos.documents.upsert(
                doc_id=f"literature/tom-{index}", doc_type="literature",
                title=f"Том {index}", source_path=f"{index}.md",
                sha256=str(index).ljust(64, "0"))
            repos.chunks.replace_for_document(document, [
                Chunk(chunk_id=f"literature/tom-{index}#{j:04d}",
                      doc_id=f"literature/tom-{index}", doc_type="literature",
                      title_path=["Том"], text=text)
                for j in range(3)
            ])
        return repos, QualityChecker(repos)

    def run_check(self, *texts):
        repos, checker = self.build(*texts)
        checker.start()
        checker.wait(10)
        return repos, checker

    def test_a_badly_parsed_document_is_marked_without_reloading_it(self):
        repos, checker = self.run_check(self.GOOD, self.BAD, self.GOOD)
        self.assertEqual(1, checker.count_glued())
        marked = [d.doc_id for d in repos.documents.list()
                  if d.meta.get("text_quality") == "glued"]
        self.assertEqual(["literature/tom-1"], marked)

    def test_the_state_says_how_many_and_why(self):
        _, checker = self.run_check(self.BAD, self.BAD, self.GOOD)
        state = checker.status()
        self.assertEqual(2, state["glued"])
        self.assertIn("2", state["hint"])
        self.assertIn("склеен", state["hint"])

    def test_a_clean_library_is_not_alarmed_about(self):
        _, checker = self.run_check(self.GOOD, self.GOOD)
        self.assertEqual(0, checker.count_glued())
        self.assertIn("не нашлось", checker.status()["hint"])

    def test_a_fixed_document_loses_the_mark(self):
        """Иначе пометка висела бы вечно и чинили бы уже починенное."""
        from reportgen.corpus import Chunk

        repos, checker = self.run_check(self.BAD)
        self.assertEqual(1, checker.count_glued())
        document = repos.documents.by_doc_id("literature/tom-0")
        repos.chunks.replace_for_document(document, [
            Chunk(chunk_id="literature/tom-0#0000", doc_id="literature/tom-0",
                  doc_type="literature", title_path=["Том"], text=self.GOOD)])
        checker.start()
        checker.wait(10)
        self.assertEqual(0, checker.count_glued())

    def test_a_document_without_fragments_is_not_called_glued(self):
        """Скан без распознавания — другая беда, и лечится она иначе."""
        repos, checker = self.build()
        repos.documents.upsert(
            doc_id="literature/skan", doc_type="literature", title="Скан",
            source_path="skan.pdf", sha256="s" * 64)
        checker.start()
        checker.wait(10)
        self.assertEqual(0, checker.count_glued())

    def test_the_library_can_be_filtered_down_to_the_bad_ones(self):
        # Среди тринадцати тысяч документов пометка бесполезна, если по ней
        # нельзя отобрать.
        repos, _ = self.run_check(self.GOOD, self.BAD, self.BAD)
        self.assertEqual(2, repos.documents.count_all(quality="glued"))
        self.assertEqual(3, repos.documents.count_all())
        found = repos.documents.list(quality="glued")
        self.assertEqual(["literature/tom-1", "literature/tom-2"],
                         [d.doc_id for d in found])

    def test_only_the_beginning_of_a_document_is_read(self):
        """У книги полторы тысячи фрагментов; склейка видна на первых же."""
        repos, _ = self.build(self.GOOD)
        sample = repos.documents.text_samples()[0]["sample"]
        self.assertLessEqual(len(sample), 4000)
        self.assertTrue(sample)


if __name__ == "__main__":
    unittest.main()
