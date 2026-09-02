"""Разбор в несколько заходов: помощник ищет, читает и ищет снова.

Раньше на вопрос приходился один поиск и один проход модели. Не нашлось с
первого запроса — ответа не будет, и модель об этом не узнает. Инженер
поступает иначе: ищет, смотрит, чего не хватает, лезет в оглавление тома,
читает главу и ищет ещё раз другими словами.
"""

import unittest

import _bootstrap  # noqa: F401

from reportgen.web.research import (
    Step,
    found_note,
    parse_step,
    render_found,
    render_trail,
)


class ParseTests(unittest.TestCase):
    """Разбор шага намеренно снисходителен: модель локальная и разная."""

    def test_plain_forms(self):
        self.assertEqual(Step("искать", "кадр BBFrame"), parse_step("ИСКАТЬ: кадр BBFrame"))
        self.assertEqual(Step("оглавление", "kniga"), parse_step("ОГЛАВЛЕНИЕ: kniga"))
        self.assertEqual(Step("хватит"), parse_step("ХВАТИТ"))

    def test_case_and_dashes_do_not_matter(self):
        for text in ("искать - кадр", "Искать — кадр", "ИСКАТЬ:кадр", "- искать: кадр"):
            with self.subTest(text=text):
                self.assertEqual(Step("искать", "кадр"), parse_step(text))

    def test_a_thought_before_the_step_is_tolerated(self):
        """Модель любит предварять ответ рассуждением — это не повод падать."""
        reply = "Мне не хватает описания кадра.\nИСКАТЬ: структура кадра BBFrame"
        self.assertEqual(Step("искать", "структура кадра BBFrame"), parse_step(reply))

    def test_english_words_are_understood(self):
        # Модель сбилась на язык обучения. Ронять из-за этого разбор незачем.
        self.assertEqual(Step("искать", "frame header"), parse_step("SEARCH: frame header"))
        self.assertEqual(Step("хватит"), parse_step("ENOUGH"))

    def test_reading_a_named_section(self):
        step = parse_step("ЧИТАТЬ: справочник | Глава 4. Кадры")
        self.assertEqual("читать", step.kind)
        self.assertEqual("справочник", step.argument)
        self.assertEqual("Глава 4. Кадры", step.section)

    def test_reading_without_a_section(self):
        step = parse_step("ЧИТАТЬ: справочник")
        self.assertEqual("справочник", step.argument)
        self.assertEqual("", step.section)

    def test_quotes_are_stripped(self):
        self.assertEqual("справочник",
                         parse_step('ЧИТАТЬ: «справочник»').argument)

    def test_a_search_without_a_query_is_not_a_step(self):
        # Искать пустоту хуже, чем закончить разбор.
        self.assertIsNone(parse_step("ИСКАТЬ:"))

    def test_prose_is_not_a_step(self):
        self.assertIsNone(parse_step("Думаю, стоит посмотреть в справочнике."))
        self.assertIsNone(parse_step(""))

    def test_a_very_long_query_is_cut(self):
        step = parse_step("ИСКАТЬ: " + "слово " * 200)
        self.assertLessEqual(len(step.argument), 300)


class TrailTests(unittest.TestCase):
    def test_the_trail_names_what_was_done(self):
        trail = [Step("искать", "кадр BBFrame"), Step("читать", "том", "Глава 4")]
        text = render_trail(trail)
        self.assertIn("ищу: кадр BBFrame", text)
        self.assertIn("читаю: том → Глава 4", text)

    def test_the_answer_of_a_step_is_shown_next_to_it(self):
        """Иначе модель просит оглавление, не видит его и просит снова."""
        step = Step("оглавление", "том")
        step.note = "1. Область применения; 2. Нормы"
        self.assertIn("2. Нормы", render_trail([step]))

    def test_an_empty_trail_says_it_is_the_first_round(self):
        self.assertIn("первый заход", render_trail([]))

    def test_an_empty_round_is_called_empty(self):
        # Без этого модель не отличает «нашлось не то» от «не нашлось ничего»
        # и переспрашивает теми же словами до конца заходов.
        self.assertIn("ничего нового", found_note(0))
        self.assertIn("1", found_note(1))
        self.assertIn("7", found_note(7))


class FoundTests(unittest.TestCase):
    def test_the_list_has_no_texts(self):
        """В заходах модель видит опись собранного, а не сами тексты.

        Полные тексты пошли бы в окно по второму разу и вытеснили бы всё
        остальное, а для решения «чего не хватает» нужен перечень.
        """
        text = render_found([
            {"citation": "Том — Глава 1", "doc_id": "a", "chunk_uid": "a#0"},
            {"citation": "Том — Глава 2", "doc_id": "a", "chunk_uid": "a#1"},
        ])
        self.assertIn("1) Том — Глава 1", text)
        self.assertIn("2) Том — Глава 2", text)

    def test_the_numbering_is_not_the_citation_labels(self):
        # В готовом ответе метки [S1] свои; совпадать они не обязаны, и
        # путать их нельзя.
        self.assertNotIn("[S", render_found([{"citation": "Том", "doc_id": "a"}]))

    def test_nothing_found_says_so(self):
        self.assertIn("ничего не найдено", render_found([]))


if __name__ == "__main__":
    unittest.main()
