"""Ссылки на источники: [S1], [S1, S2], [S1—S3].

Метка — то, чем ответ связан с библиотекой отдела. По ней человек открывает
фрагмент, по ней же система считает, опирается ли ответ на источники.

Разбиралась она одним строгим образцом: закрывающая скобка сразу за цифрами.
А модель, когда утверждение опирается на два документа, пишет по-русски
естественно — «[S1, S2]». Такая метка ссылкой не становилась, в панель уходили
первые три фрагмента подборки вместо процитированных, а рядом с ответом висело
«ответ не опирается на библиотеку» — про ответ, сославшийся на всё, что нужно.

Тот же образец стоял в проверке отчёта и в метриках, поэтому разбор здесь один
на всех, и правила у интерфейса и сервера обязаны совпадать.
"""

import unittest

import _bootstrap  # noqa: F401

from reportgen.citations import MAX_RANGE, expand_box, labels_in
from reportgen.evaluate import APPENDIX_MARKER, citation_precision
from reportgen.web.assistant import _used_labels


class ExpandTests(unittest.TestCase):
    def test_a_single_label(self):
        self.assertEqual(["S1"], expand_box("[S1]"))

    def test_two_labels_through_a_comma(self):
        self.assertEqual(["S1", "S2"], expand_box("[S1, S2]"))

    def test_the_other_separators(self):
        for box in ("[S1; S2]", "[S1 и S2]", "[ S1 , S2 ]", "[S1,S2]"):
            self.assertEqual(["S1", "S2"], expand_box(box), box)

    def test_a_range(self):
        self.assertEqual(["S1", "S2", "S3"], expand_box("[S1—S3]"))
        self.assertEqual(["S1", "S2", "S3"], expand_box("[S1-S3]"))

    def test_a_range_written_backwards_keeps_both_ends(self):
        """Обе метки настоящие: терять их молча хуже, чем показать не в том
        порядке, в каком написали."""
        self.assertEqual(["S1", "S2", "S3"], expand_box("[S3—S1]"))

    def test_a_russian_letter_instead_of_the_latin_one(self):
        """По раскладке «С» вместо «S» — обычная опечатка."""
        self.assertEqual(["S1", "S2"], expand_box("[С1, С2]"))

    def test_a_lowercase_letter(self):
        self.assertEqual(["S1"], expand_box("[s1]"))

    def test_a_leading_zero(self):
        self.assertEqual(["S1"], expand_box("[S01]"))

    def test_a_repeat_inside_one_bracket_is_counted_once(self):
        self.assertEqual(["S1", "S2"], expand_box("[S1, S2, S1]"))

    def test_a_huge_range_is_cut(self):
        """«[S1—S9999]» — это опечатка, а не ссылка на девять тысяч фрагментов."""
        self.assertEqual(MAX_RANGE + 1, len(expand_box("[S1—S9999]")))


class TextTests(unittest.TestCase):
    def test_labels_are_found_across_the_answer(self):
        text = "Полоса 36 МГц [S1, S2]. Методика измерения — в [S3]."
        self.assertEqual({"S1", "S2", "S3"}, labels_in(text))

    def test_a_bracket_that_is_not_a_citation_is_ignored(self):
        self.assertEqual(set(), labels_in("Смотри [ГОСТ 1-3] и [примечание]."))

    def test_an_empty_text_has_no_labels(self):
        self.assertEqual(set(), labels_in(""))
        self.assertEqual(set(), labels_in(None))


class AnswerTests(unittest.TestCase):
    """То же самое там, где по меткам отбираются источники ответа."""

    def test_a_group_counts_as_a_citation(self):
        """Иначе счётчик показывал ноль, а панель — первые три фрагмента."""
        self.assertEqual({"S1", "S2"}, _used_labels("Порог 12 дБ [S1, S2]."))

    def test_a_plain_label_still_works(self):
        self.assertEqual({"S3"}, _used_labels("Методика в [S3]."))


class MetricTests(unittest.TestCase):
    """Метрика точности цитирования считает по меткам, а не по скобкам."""

    def report(self, body: str) -> str:
        return (f"## Раздел\n\n{body}\n\n## {APPENDIX_MARKER}\n"
                "- [S1] Том по спутниковой связи\n- [S2] Стандарт\n")

    def test_a_group_where_one_label_leads_nowhere(self):
        """«[S1, S2]» — две ссылки, и если одна в никуда, это должно быть видно."""
        self.assertEqual(0.6667,
                         citation_precision(self.report("Порог [S1, S2] и [S3].")))

    def test_a_group_where_both_labels_are_known(self):
        self.assertEqual(1.0, citation_precision(self.report("Порог [S1, S2].")))

    def test_a_report_without_citations_is_not_punished(self):
        self.assertEqual(1.0, citation_precision(self.report("Просто текст.")))


if __name__ == "__main__":
    unittest.main()
