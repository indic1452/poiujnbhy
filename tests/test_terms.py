"""Двуязычный словарь: русский запрос — английские документы.

Половина библиотеки компании написана по-английски: 9800 RFC и все паспорта
на импортные микросхемы. Спрашивают по-русски. Смысловой поиск (bge-m3) язык
переступает сам, лексический — никак: «какие поля в заголовке» в тексте RFC
находит ноль. Проверки ниже держат этот стык.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen import terms as terms_module
from reportgen.ingest.pipeline import ingest_directory
from reportgen.search import DatabaseRetriever
from reportgen.store.db import Database
from reportgen.store.repo import Repositories

ROOT = Path(__file__).resolve().parents[1]
GLOSSARY = ROOT / "templates" / "terms.json"


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        terms_module.forget()

    def tearDown(self):
        self._tmp.cleanup()
        terms_module.forget()

    def write_glossary(self, rows) -> Path:
        path = self.tmp / "terms.json"
        path.write_text(json.dumps({"terms": rows}, ensure_ascii=False),
                        encoding="utf-8")
        return path


class ExpansionTests(TempCase):
    def test_russian_query_gains_english_terms(self):
        path = self.write_glossary([
            {"ru": "заголов", "en": ["header"]},
            {"ru": "мультиплексор", "en": ["multiplexer"]},
        ])
        _, added = terms_module.expand_query("какие поля в заголовке", path)
        self.assertIn("header", added)

    def test_all_word_forms_are_caught(self):
        # Основа вместо слова целиком: падежей у русского слова много, а
        # стеммер в системе приводит их к разным формам.
        path = self.write_glossary([{"ru": "заголов", "en": ["header"]}])
        for query in ("заголовок кадра", "поля заголовка", "в заголовке",
                      "по заголовкам"):
            with self.subTest(query=query):
                _, added = terms_module.expand_query(query, path)
                self.assertIn("header", added)

    def test_both_number_forms_are_added(self):
        # Стеммер русский: английские окончания он не срезает, и «header
        # field» с «header fields» для поиска — разные слова.
        path = self.write_glossary([{"ru": "поле заголов", "en": ["header field"]}])
        _, added = terms_module.expand_query("поле заголовка", path)
        self.assertIn("header field", added)
        self.assertIn("header fields", added)

    def test_english_query_is_not_padded_with_itself(self):
        path = self.write_glossary([{"ru": "заголов", "en": ["header"]}])
        _, added = terms_module.expand_query("header fields", path)
        self.assertEqual([], added)

    def test_expansion_is_capped(self):
        # Без предела длинный вопрос превращается в перечисление полусотни
        # слов, и BM25 перестаёт различать документы.
        rows = [{"ru": f"термин{index:02d}", "en": [f"term{index:02d}"]}
                for index in range(40)]
        path = self.write_glossary(rows)
        query = " ".join(f"термин{index:02d}" for index in range(40))
        _, added = terms_module.expand_query(query, path)
        self.assertLessEqual(len(added), terms_module.MAX_EXPANSIONS)

    def test_longer_term_wins(self):
        path = self.write_glossary([
            {"ru": "полоса", "en": ["band"]},
            {"ru": "полоса пропускания", "en": ["bandwidth"]},
        ])
        _, added = terms_module.expand_query("занимаемая полоса пропускания", path)
        self.assertEqual("bandwidth", added[0])

    def test_short_word_is_matched_whole_not_inside_another(self):
        # «код» встречается внутри «кодировки», «сеть» — внутри «сетевого».
        # Но выбрасывать короткие слова нельзя: АЦП, ЦАП, ФАПЧ, МШУ — это
        # три-четыре буквы, и печатают их постоянно.
        path = self.write_glossary([{"ru": "код", "en": ["code"]}])
        _, inside = terms_module.expand_query("кодировка файла", path)
        self.assertEqual([], inside)
        _, alone = terms_module.expand_query("код кадра", path)
        self.assertIn("code", alone)

    def test_short_word_still_takes_russian_endings(self):
        path = self.write_glossary([{"ru": "код", "en": ["code"]}])
        for query in ("код ошибки", "кода ошибки", "коде ошибки", "кодов ошибок"):
            with self.subTest(query=query):
                _, added = terms_module.expand_query(query, path)
                self.assertIn("code", added, query)

    def test_russian_abbreviations_work(self):
        # Ровно то, что инженер печатает, разбирая паспорт микросхемы.
        path = self.write_glossary([
            {"ru": "ацп", "en": ["adc", "analog to digital converter"]},
            {"ru": "мшу", "en": ["low noise amplifier"]},
        ])
        _, added = terms_module.expand_query("предельные режимы ацп", path)
        self.assertIn("adc", added)
        _, amplifier = terms_module.expand_query("коэффициент шума мшу", path)
        self.assertIn("low noise amplifier", amplifier)

    def test_short_latin_needs_whole_word(self):
        path = self.write_glossary([{"ru": "adc", "en": ["analog to digital"]}])
        _, inside = terms_module.expand_query("adcock антенна", path)
        self.assertEqual([], inside)
        _, alone = terms_module.expand_query("схема adc", path)
        self.assertTrue(alone)

    def test_yo_is_optional(self):
        """«Ё» пишут через раз.

        Инженер напечатает «приемопередатчик», а в словаре стоит
        «приёмопередатчик» — и все паспорта импортных микросхем перестают
        находиться из-за одной буквы.
        """
        path = self.write_glossary([{"ru": "приёмопередатчик", "en": ["transceiver"]}])
        for query in ("приёмопередатчик AD9361", "приемопередатчик AD9361"):
            with self.subTest(query=query):
                _, added = terms_module.expand_query(query, path)
                self.assertIn("transceiver", added, query)

    def test_yo_in_the_query_finds_a_plain_key(self):
        path = self.write_glossary([{"ru": "приемопередатчик", "en": ["transceiver"]}])
        _, added = terms_module.expand_query("приёмопередатчик", path)
        self.assertIn("transceiver", added)

    def test_words_of_a_compound_term_need_not_touch(self):
        """«Поля заголовка» и «какие поля в заголовке» — один вопрос.

        Требование стоять вплотную оставляло без расширения ровно тот запрос,
        ради которого словарь и заводился: между словами стоит предлог.
        """
        path = self.write_glossary([{"ru": "поля заголов", "en": ["header field"]}])
        for query in ("поля заголовка", "какие поля в заголовке",
                      "поля этого заголовка"):
            with self.subTest(query=query):
                _, added = terms_module.expand_query(query, path)
                self.assertIn("header field", added, query)

    def test_words_too_far_apart_do_not_count(self):
        # Иначе любые два слова в длинном вопросе склеятся в термин.
        path = self.write_glossary([{"ru": "поля заголов", "en": ["header field"]}])
        _, added = terms_module.expand_query(
            "поля в таблице описаны отдельно, а вот про заголовок ничего", path)
        self.assertNotIn("header field", added)

    def test_word_order_matters(self):
        path = self.write_glossary([{"ru": "поля заголов", "en": ["header field"]}])
        _, added = terms_module.expand_query("заголовок и поля", path)
        self.assertNotIn("header field", added)

    def test_edited_glossary_is_picked_up(self):
        """Справочник заявлен пополняемым — значит, без перезапуска сервера."""
        import os
        import time

        path = self.write_glossary([{"ru": "заголов", "en": ["header"]}])
        _, before = terms_module.expand_query("заголовок и полоса пропускания", path)
        self.assertNotIn("bandwidth", before)

        self.write_glossary([
            {"ru": "заголов", "en": ["header"]},
            {"ru": "полоса пропускания", "en": ["bandwidth"]},
        ])
        later = time.time() + 2
        os.utime(path, (later, later))
        _, after = terms_module.expand_query("заголовок и полоса пропускания", path)
        self.assertIn("bandwidth", after)

    def test_missing_file_is_not_fatal(self):
        # Поиск без словаря работает, просто хуже. Ронять его нельзя.
        query, added = terms_module.expand_query("заголовок", self.tmp / "нет.json")
        self.assertEqual("заголовок", query)
        self.assertEqual([], added)

    def test_broken_file_is_not_fatal(self):
        path = self.tmp / "битый.json"
        path.write_text("{это не json", encoding="utf-8")
        query, added = terms_module.expand_query("заголовок", path)
        self.assertEqual("заголовок", query)
        self.assertEqual([], added)


class SearchTests(TempCase):
    """Тот самый сценарий: русский вопрос — английский RFC."""

    RFC = (
        "Internet Engineering Task Force (IETF)                  R. Fielding\n"
        "Request for Comments: 7230                                    Adobe\n"
        "Category: Standards Track                                 June 2014\n\n"
        "  Hypertext Transfer Protocol (HTTP/1.1): Message Syntax and Routing\n\n"
        "3.2.  Header Fields\n\n"
        "   Each header field consists of a case-insensitive field name followed\n"
        "   by a colon, optional leading whitespace, the field value, and\n"
        "   optional trailing whitespace.\n"
    )

    def library(self):
        library = self.tmp / "library" / "standards" / "rfc"
        library.mkdir(parents=True)
        (library / "rfc7230.txt").write_text(self.RFC, encoding="utf-8")
        database = Database(":memory:")
        database.migrate()
        repos = Repositories(database)
        ingest_directory(repos, self.tmp / "library")
        return repos

    def test_russian_question_finds_the_english_rfc(self):
        repos = self.library()
        # Без словаря лексический поиск не находит ничего.
        self.assertEqual([], repos.chunks.search_lexical(
            "какие поля в заголовке и что в них лежит", limit=10))
        # Со словарём — находит.
        found = DatabaseRetriever(repos, terms_path=GLOSSARY).search(
            "какие поля в заголовке и что в них лежит", top_k=5)
        self.assertTrue(found, "русский вопрос по-прежнему не находит английский RFC")
        self.assertEqual("standards/rfc/rfc7230", found[0].chunk.doc_id)

    def test_engineer_is_told_what_was_added(self):
        # Иначе английский текст в выдаче выглядит взявшимся ниоткуда.
        repos = self.library()
        retriever = DatabaseRetriever(repos, terms_path=GLOSSARY)
        retriever.search("какие поля в заголовке", top_k=5)
        self.assertTrue(retriever.last_expansion)

    def test_russian_library_is_unaffected(self):
        library = self.tmp / "library" / "standards"
        library.mkdir(parents=True)
        (library / "гост.md").write_text(
            "# ГОСТ Р 53363-2009\n\n## 4. Заголовки кадров\n\n"
            "Поле заголовка кадра содержит контрольную сумму и полезную нагрузку.\n",
            encoding="utf-8")
        database = Database(":memory:")
        database.migrate()
        repos = Repositories(database)
        ingest_directory(repos, self.tmp / "library")

        plain = DatabaseRetriever(repos, terms_path=self.tmp / "нет.json")
        smart = DatabaseRetriever(repos, terms_path=GLOSSARY)
        for query in ("поле заголовка кадра", "контрольная сумма"):
            with self.subTest(query=query):
                before = plain.search(query, top_k=5)
                after = smart.search(query, top_k=5)
                self.assertTrue(before)
                self.assertEqual(len(before), len(after))
                self.assertEqual(before[0].chunk.chunk_id, after[0].chunk.chunk_id)


class GlossaryFileTests(unittest.TestCase):
    """Сам справочник, который поедет на машину заказчика."""

    def setUp(self):
        terms_module.forget()
        self.glossary = terms_module.TermGlossary.load(GLOSSARY)

    def test_file_exists_and_loads(self):
        self.assertTrue(GLOSSARY.is_file(), "нет templates/terms.json")
        self.assertTrue(len(self.glossary), "словарь пуст")

    def test_no_duplicate_russian_stems(self):
        stems = [term.ru for term in self.glossary.terms]
        duplicates = sorted({stem for stem in stems if stems.count(stem) > 1})
        self.assertFalse(duplicates, f"повторяются основы: {duplicates}")

    def test_english_side_is_latin(self):
        for term in self.glossary.terms:
            for english in term.en:
                with self.subTest(ru=term.ru, en=english):
                    self.assertFalse(
                        any("а" <= char <= "я" for char in english),
                        "в английской части кириллица")

    def test_russian_side_is_lowercase(self):
        for term in self.glossary.terms:
            self.assertEqual(term.ru, term.ru.lower())


if __name__ == "__main__":
    unittest.main()


class PromptTests(unittest.TestCase):
    """Что сказано модели про английские источники.

    Половина библиотеки — RFC и паспорта импортных микросхем, а требование
    «технический русский язык» модель понимает буквально и добросовестно
    переводит названия полей: «Transfer-Encoding» превращается в «Кодирование
    передачи». Инженер такого поля не найдёт ни в дампе, ни в самом RFC —
    ответ становится не просто бесполезным, а вредным.
    """

    def prompts(self):
        from reportgen import prompts

        return {
            "отчёт": prompts.SYSTEM_PROMPT,
            "помощник": prompts.ASSISTANT_SYSTEM_PROMPT,
        }

    def test_original_names_are_required(self):
        for name, text in self.prompts().items():
            with self.subTest(prompt=name):
                self.assertIn("оригинальн", text.lower(),
                              "не сказано сохранять оригинальные названия")

    def test_english_sources_are_announced(self):
        for name, text in self.prompts().items():
            with self.subTest(prompt=name):
                self.assertIn("англий", text.lower(),
                              "не сказано, что источники бывают английскими")

    def test_units_are_not_recalculated(self):
        from reportgen import prompts

        text = " ".join(prompts.ASSISTANT_SYSTEM_PROMPT.split())
        self.assertIn("не пересчитывая", text)

    def test_rules_stay_numbered_in_order(self):
        # Правила пронумерованы; сбитая нумерация после вставки читается моделью
        # как две разные инструкции под одним номером.
        for name, text in self.prompts().items():
            with self.subTest(prompt=name):
                numbers = [int(match) for match in re.findall(r"^(\d+)\.", text, re.M)]
                self.assertEqual(list(range(1, len(numbers) + 1)), numbers,
                                 f"нумерация правил сбита: {numbers}")
