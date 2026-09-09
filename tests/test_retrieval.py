import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.corpus import Chunk
from reportgen.retrieval import BM25Index, Hit, Retriever, reciprocal_rank_fusion, tokenize

CHUNKS = [
    Chunk("lit#1", "lit", "literature", ["Конспект", "EVM"],
          "Модуль вектора ошибки характеризует качество модуляции QPSK."),
    Chunk("lit#2", "lit", "literature", ["Конспект", "Полоса"],
          "Занимаемая полоса частот определяется методом 99 процентов мощности."),
    Chunk("rep#1", "rep", "reports", ["Отчёт 2023"],
          "Паразитная составляющая в спектре обнаружена не была."),
]


class TokenizeTests(unittest.TestCase):
    def test_drops_stopwords_and_stems(self):
        self.assertEqual(tokenize("Измерение занимаемой полосы"), ["измерен", "занимаем", "полос"])

    def test_word_forms_collapse(self):
        self.assertEqual(tokenize("занимаемая полоса"), tokenize("занимаемой полосы"))

    def test_glued_designation_is_also_split(self):
        """«RFC4818» и «RFC 4818» обязаны находить одно и то же.

        Инженер пишет номер как придётся: «RFC 4818», «RFC4818», «RFC-4818».
        Через дефис и пробел разбор давал два слова, а слитно — одно слово
        «rfc4818», которого в указателе нет вовсе: в документе-то написано
        «Request for Comments: 4818». Поиск возвращал ПУСТО, и помощник со
        спокойной совестью отвечал, что такого документа в библиотеке нет.

        Слитную запись оставляем тоже: «ФМ4», «Е1», «КАМ16» в отделе пишут
        слитно, и найтись они должны как есть.
        """
        self.assertEqual(["rfc4818", "rfc", "4818"], tokenize("RFC4818"))
        # Через пробел и дефис — как и было.
        self.assertEqual(["rfc", "4818"], tokenize("RFC 4818"))
        self.assertEqual(["rfc", "4818"], tokenize("RFC-4818"))

    def test_glued_and_spaced_designations_meet(self):
        # Главное свойство: как бы номер ни записали, общий токен найдётся.
        слитно = set(tokenize("RFC4818"))
        через_пробел = set(tokenize("RFC 4818"))
        self.assertTrue(слитно & через_пробел, "записи номера не пересекаются")
        self.assertIn("4818", слитно)

    def test_short_department_notation_is_not_torn_apart(self):
        """«Е1», «ФМ4» — обозначения, а не «буква и число».

        Резать их значит засорить указатель токенами «е» и «4», которые есть
        в каждом документе и не значат ничего.
        """
        # Латиница в записи — работа unmix_scripts, она была и раньше.
        self.assertEqual(["e1"], tokenize("Е1"))
        self.assertEqual(["фm4"], tokenize("ФМ4"))

    def test_dotted_designation_stays_whole(self):
        # «G.703» и «Х.25» — одно обозначение; точка в них разделителем не является.
        self.assertEqual(["g.703"], tokenize("G.703"))
        self.assertEqual(["x.25"], tokenize("Х.25"))

    def test_department_notation_with_two_digits_meets_the_hyphen_form(self):
        # «КАМ16» в документе и «КАМ-16» в вопросе — одно и то же.
        self.assertTrue(set(tokenize("КАМ16")) & set(tokenize("КАМ-16")))


class BM25Tests(unittest.TestCase):
    def setUp(self):
        self.index = BM25Index(CHUNKS)

    def test_ranks_relevant_first(self):
        hits = self.index.search("занимаемая полоса частот")
        self.assertEqual(hits[0].chunk.chunk_id, "lit#2")

    def test_filters_by_doc_type(self):
        hits = self.index.search("спектр", doc_types=["reports"])
        self.assertEqual({hit.chunk.doc_type for hit in hits}, {"reports"})

    def test_no_match_returns_empty(self):
        self.assertEqual(self.index.search("совершенно посторонний запрос ковид"), [])

    def test_breadcrumbs_are_searchable(self):
        # Слова из заголовка нет в теле чанка, но найтись он обязан.
        hits = self.index.search("Отчёт 2023")
        self.assertEqual(hits[0].chunk.chunk_id, "rep#1")

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            self.index.save(path)
            restored = BM25Index.load(path)
        self.assertEqual(len(restored), len(self.index))
        self.assertEqual(
            restored.search("модуляция QPSK")[0].chunk.chunk_id,
            self.index.search("модуляция QPSK")[0].chunk.chunk_id,
        )


class FusionTests(unittest.TestCase):
    def test_rrf_prefers_consensus(self):
        first = [Hit(CHUNKS[0], 1.0), Hit(CHUNKS[1], 0.9)]
        second = [Hit(CHUNKS[1], 5.0), Hit(CHUNKS[2], 4.0)]
        fused = reciprocal_rank_fusion([first, second])
        self.assertEqual(fused[0].chunk.chunk_id, "lit#2")


class RetrieverTests(unittest.TestCase):
    def test_reranker_reorders(self):
        index = BM25Index(CHUNKS)

        def reranker(query, chunks):
            # Искусственный реранкер: поднимает отчёты наверх.
            return [1.0 if chunk.doc_type == "reports" else 0.0 for chunk in chunks]

        retriever = Retriever(index, reranker=reranker)
        hits = retriever.search("спектр модуляция полоса")
        self.assertEqual(hits[0].chunk.doc_type, "reports")
        self.assertEqual(hits[0].rank, 1)

    def test_dense_scorer_participates(self):
        index = BM25Index(CHUNKS)
        calls = []

        def dense(query, chunks):
            calls.append(query)
            return [float(len(chunks) - i) for i in range(len(chunks))]

        Retriever(index, dense_scorer=dense).search("полоса частот")
        self.assertEqual(calls, ["полоса частот"])


class SupersededTests(unittest.TestCase):
    """Заменённая редакция не идёт в выдачу — как и в поиске по базе.

    Фильтра по состоянию здесь не было вовсе, и два поиска расходились
    молча: приложение отменённую норму не показывало, а «reportgen search»
    и сборка отчёта по файловому указателю — показывали, и она уезжала в
    отчёт со ссылкой как действующая.
    """

    def index(self):
        from reportgen.corpus import Chunk
        from reportgen.retrieval import BM25Index

        return BM25Index([
            Chunk(chunk_id="a#0", doc_id="a", doc_type="standards",
                  title_path=["Норма"], text="Занимаемая полоса не более 3.5 МГц.",
                  meta={"status": "superseded", "superseded_by": "b"}),
            Chunk(chunk_id="b#0", doc_id="b", doc_type="standards",
                  title_path=["Норма"], text="Занимаемая полоса не более 4.0 МГц.",
                  meta={"status": "current"}),
            Chunk(chunk_id="c#0", doc_id="c", doc_type="standards",
                  title_path=["Норма"], text="Занимаемая полоса не более 5.0 МГц."),
        ])

    def test_a_replaced_standard_is_not_returned(self):
        found = self.index().search("занимаемая полоса", top_k=5)
        self.assertEqual({"b", "c"}, {hit.chunk.doc_id for hit in found})

    def test_a_chunk_without_a_status_counts_as_current(self):
        """Старый указатель состояния не хранит — молча пустеть он не должен."""
        found = self.index().search("занимаемая полоса", top_k=5)
        self.assertIn("c", {hit.chunk.doc_id for hit in found})

    def test_the_filter_can_be_lifted_on_purpose(self):
        found = self.index().search("занимаемая полоса", top_k=5, statuses=None)
        self.assertEqual({"a", "b", "c"}, {hit.chunk.doc_id for hit in found})

    def test_both_searches_use_one_and_the_same_list(self):
        # Две копии константы разъехались бы: у поиска по базе своя, у
        # поиска по указателю своя, и отменённая норма вернулась бы через
        # один из них.
        from reportgen.corpus import SEARCHABLE_STATUSES as core
        from reportgen.store.models import SEARCHABLE_STATUSES as stored

        self.assertIs(core, stored)


if __name__ == "__main__":

    unittest.main()
