# -*- coding: utf-8 -*-
"""Окно модели: промпт влезает целиком, а лишнее читается частями.

Отдел получил «Ошибка при обращении к модели: 36061 токенов, а размер
32768» — ответа не было вовсе. Бюджет при этом соблюдался: в настройках
стоял предел в 52 000 знаков материала, и он не нарушался.

Считались, однако, ЧАСТИ промпта, а в окно уходит целое. Не входили в счёт
ни системная инструкция (4315 знаков), ни шапки шаблона (3376), ни блок
«документы, названные в вопросе». Вдобавок сам предел был выведен из оценки
«2,4 знака на токен», взятой от английского текста: русское техническое
слово разбивается мельче, и на живом материале вышло около полутора.

    (32768 − 4000 − 512) × 1,5 − 4315 − 3376 ≈ 34 700 знаков
    а в настройке стояло                      52 000

И вторая жалоба, из того же корня: «нужны все данные, пусть разбиваются на
части». Прежде не поместившееся молча отбрасывалось, и ответ собирался по
половине найденного.
"""

import unittest

import _bootstrap  # noqa: F401
from reportgen.prompts import (
    ASSISTANT_PROMPT,
    ASSISTANT_SYSTEM_PROMPT,
    ASSISTANT_TASK,
)
from test_assistant import AssistantTestCase

ВОПРОС = "Как измеряется занимаемая полоса частот?"


class ОкноВыводитсяИзМодели(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)

    def test_предел_считается_по_окну_а_не_по_настройке(self):
        self.settings.llm_context_tokens = 32768
        self.settings.assistant_max_tokens = 4000
        self.settings.assistant_chars_per_token = 1.5
        self.settings.assistant_context_chars = 0
        # Задание считается отдельно от шаблона: оно вынесено в свою строку
        # ради уступки на тесном окне. Не вычесть его — значит обещать
        # материалу на четыре с половиной тысячи знаков больше, чем есть.
        ждём = int((32768 - 4000 - 512) * 1.5
                   - len(ASSISTANT_SYSTEM_PROMPT) - len(ASSISTANT_PROMPT)
                   - len(ASSISTANT_TASK))
        self.assertEqual(ждём, self.assistant._context_chars())

    def test_меньшее_окно_даёт_меньший_предел(self):
        self.settings.assistant_context_chars = 0
        self.settings.llm_context_tokens = 32768
        просторно = self.assistant._context_chars()
        self.settings.llm_context_tokens = 16384
        self.assertLess(self.assistant._context_chars(), просторно)

    def test_настройка_может_только_уменьшить(self):
        """52 000 при реальных 34 700 — это и было переполнение."""
        self.settings.assistant_context_chars = 0
        выведено = self.assistant._context_chars()
        self.settings.assistant_context_chars = выведено * 2
        self.assertEqual(выведено, self.assistant._context_chars(),
                         "настройка подняла предел выше окна модели")
        self.settings.assistant_context_chars = 9000
        self.assertEqual(9000, self.assistant._context_chars(),
                         "осознанно заданное маленькое окно не соблюдено")

    def test_оценка_знаков_на_токен_по_умолчанию_с_запасом(self):
        """Прежние 2,4 знака на токен и переполнили окно.

        Величина взята от английского текста; русское техническое слово
        разбивается мельче. Ошибаться здесь можно только в одну сторону:
        заниженная оценка отнимает у ответа немного материала, завышенная
        роняет ответ целиком.
        """
        from reportgen.web.assistant import CHARS_PER_TOKEN

        self.assertLessEqual(CHARS_PER_TOKEN, 1.6,
                             "оценка завышена — окно снова лопнет")
        self.settings.assistant_chars_per_token = 0    # «не задано»
        self.settings.assistant_context_chars = 0
        по_умолчанию = self.assistant._context_chars()
        self.settings.assistant_chars_per_token = CHARS_PER_TOKEN
        self.assertEqual(по_умолчанию, self.assistant._context_chars(),
                         "без настройки берётся не CHARS_PER_TOKEN")

    def test_ответу_оставлено_место(self):
        """Окно делится с ответом: 4000 токенов на него — не пожелание."""
        self.settings.assistant_context_chars = 0
        self.settings.assistant_max_tokens = 4000
        с_ответом = self.assistant._context_chars()
        self.settings.assistant_max_tokens = 8000
        self.assertLess(self.assistant._context_chars(), с_ответом)


class ПромптВлезаетЦеликом(AssistantTestCase):
    """Меряется собранная строка, а не сумма её частей."""

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)
        self.settings.assistant_context_chars = 0

    def токенов(self, prepared) -> int:
        знаков = (len(prepared["prompt"]) + len(ASSISTANT_SYSTEM_PROMPT)
                  + sum(len(item["content"]) for item in prepared["history"]))
        доля = float(self.settings.assistant_chars_per_token)
        return int(знаков / доля)

    def готово(self, question=ВОПРОС):
        return self.assistant._prepare(self.ivanov, self.chat.id, question,
                                       top_k=None)

    def test_влезает_при_обычном_вопросе(self):
        prepared = self.готово()
        предел = (self.settings.llm_context_tokens
                  - self.settings.assistant_max_tokens)
        self.assertLessEqual(self.токенов(prepared), предел)

    def test_влезает_при_тесном_окне(self):
        """Проверка обязана держать и то окно, которого мы не ждали."""
        self.settings.llm_context_tokens = 8192
        self.settings.assistant_max_tokens = 1000
        prepared = self.готово()
        self.assertLessEqual(self.токенов(prepared),
                             8192 - 1000)

    def test_влезает_при_длинном_разговоре(self):
        for _ in range(4):
            self.repos.chats.add_message(self.chat.id, "user", "в" * 2000)
            self.repos.chats.add_message(self.chat.id, "assistant", "о" * 2000)
        prepared = self.готово()
        предел = (self.settings.llm_context_tokens
                  - self.settings.assistant_max_tokens)
        self.assertLessEqual(self.токенов(prepared), предел)

    def test_хотя_бы_один_источник_остаётся(self):
        """Без источников помощник — обычная модель без ссылок на нормы."""
        self.settings.llm_context_tokens = 4096
        self.settings.assistant_max_tokens = 500
        prepared = self.готово()
        self.assertTrue(prepared["sources"], "материал вырезан до нуля")


class ЛишнееЧитаетсяЧастями(AssistantTestCase):
    """«Нужны все данные, пусть разбиваются на части»."""

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)
        self.settings.assistant_context_chars = 0
        # Окно нарочно тесное: иначе на учебной библиотеке всё помещается и
        # проверять оказывается нечего. Но не теснее, чем нужно: при 9000
        # токенов места не остаётся и самим выпискам, и они уступают — это
        # отдельный случай, он проверяется ниже.
        self.settings.llm_context_tokens = 14000
        self.settings.assistant_max_tokens = 500
        # Материала должно быть заведомо больше окна — и настолько больше,
        # чтобы это не зависело от того, сколько места придержано под
        # выписки. Иначе выключение разбора частями само по себе освобождает
        # место, всё помещается, и проверять оказывается нечего.
        self.набить()
        self.spoken = []
        настоящий = self.reports.get_llm()
        # Выписка настоящего размера, а не одна строка. С короткой заглушкой
        # проверка ничего не стережёт: место под выписки можно не держать
        # вовсе, и всё равно всё поместится.
        выписка = ("Занимаемая полоса измеряется методом 99 процентов "
                   "мощности по спектру сигнала [S1]. ") * 25

        class Записывающий:
            name = "проба"

            def complete(_себя, system, user, **kwargs):
                self.spoken.append((system, user))
                return выписка

            def stream(_себя, system, user, **kwargs):
                yield настоящий.complete(system, user, **kwargs)

        self.reports.get_llm = lambda: Записывающий()

    def набить(self):
        from reportgen.corpus import Chunk

        текст = ("Занимаемая полоса частот измеряется методом 99 процентов "
                 "мощности по спектру сигнала; спектр оценивается методом "
                 "периодограмм с усреднением по сегментам. ") * 14
        for номер in range(8):
            doc_id = f"lib/polosa{номер}"
            документ = self.repos.documents.upsert(
                doc_id, "literature", f"Измерение полосы, часть {номер}",
                f"/lib/{doc_id}.pdf", "sha-полоса-" + str(номер),
                meta={}, domain="signal")
            self.repos.chunks.replace_for_document(документ, [Chunk(
                chunk_id=f"{doc_id}#{кусок}", doc_id=doc_id,
                doc_type="literature",
                title_path=[f"Измерение полосы, часть {номер}"],
                text=текст, meta={}) for кусок in range(3)])

    def готово(self):
        return self.assistant._prepare(self.ivanov, self.chat.id, ВОПРОС,
                                       top_k=None)

    def выписки(self):
        from reportgen.prompts import DIGEST_SYSTEM_PROMPT

        return [user for system, user in self.spoken
                if system == DIGEST_SYSTEM_PROMPT]

    def test_не_поместившееся_прочитано_отдельными_проходами(self):
        prepared = self.готово()
        self.assertTrue(self.выписки(),
                        "лишний материал молча отброшен, проходов не было")

    def test_выписки_попали_в_промпт(self):
        prepared = self.готово()
        self.assertIn("ВЫПИСКИ ИЗ ОСТАЛЬНОГО МАТЕРИАЛА", prepared["prompt"])

    def test_человеку_сказано_что_ответ_собран_иначе(self):
        prepared = self.готово()
        self.assertIn("выписк", (prepared["warning"] or "").lower(),
                      f"замечание не сказано: {prepared['warning']!r}")

    def test_проходы_видны_в_следе_разбора(self):
        prepared = self.готово()
        self.assertTrue([строка for строка in prepared["trail"]
                         if "выписка" in строка],
                        f"следа проходов нет: {prepared['trail']}")

    def test_разбор_частями_можно_выключить(self):
        self.settings.assistant_digest_passes = 0
        prepared = self.готово()
        self.assertEqual([], self.выписки())
        self.assertNotIn("ВЫПИСКИ", prepared["prompt"])
        self.assertIn("не вошли", (prepared["warning"] or "").lower(),
                      "материал отброшен молча")

    def test_число_проходов_соблюдается(self):
        self.settings.assistant_digest_passes = 1
        self.готово()
        self.assertEqual(1, len(self.выписки()))

    def test_с_выписками_промпт_всё_равно_влезает(self):
        """Место под выписки держится ЗАРАНЕЕ, иначе окно снова лопнет."""
        prepared = self.готово()
        знаков = (len(prepared["prompt"]) + len(ASSISTANT_SYSTEM_PROMPT)
                  + sum(len(item["content"]) for item in prepared["history"]))
        токенов = int(знаков / float(self.settings.assistant_chars_per_token))
        self.assertLessEqual(
            токенов,
            self.settings.llm_context_tokens - self.settings.assistant_max_tokens,
            f"с выписками промпт вылез за окно: {токенов} токенов")

    def test_материал_не_теряется_молча(self):
        """Каждый не поместившийся фрагмент либо прочитан, либо назван.

        Место под выписки держится ЗАРАНЕЕ. Без этого запаса выписки,
        добавленные в промпт, вытесняли бы новые фрагменты — и те уходили бы
        уже никем не прочитанными и не названными.
        """
        prepared = self.готово()
        замечание = (prepared["warning"] or "").lower()
        self.assertTrue(self.выписки(), "проходов не было")
        # Прочитано ВСЁ, что не влезло: место под выписки держится заранее,
        # и добавленные выписки не выталкивают новых фрагментов «в никуда».
        self.assertIn("прочитаны отдельными проходами", замечание)
        self.assertNotIn("не поместились", замечание,
                         "часть материала выпала уже после выписок")

    def test_выписка_длиннее_просимого_не_выталкивает_источники(self):
        """Модель отвечает длиннее, чем её просили, постоянно.

        Место под выписки придержано заранее, и выйти за него нельзя: иначе
        они вытолкнут из промпта сами источники, ради которых всё и
        затевалось. Здесь просим по сто слов, а заглушка отвечает втрое
        длиннее — лишние выписки должны быть отсечены, а не втиснуты.
        """
        self.settings.assistant_digest_words = 100
        prepared = self.готово()
        отсечено = [строка for строка in prepared["trail"]
                    if "не поместилась в отведённое место" in строка]
        self.assertTrue(отсечено, f"ничего не отсечено: {prepared['trail']}")
        блок = prepared["prompt"].split("ВЫПИСКИ ИЗ ОСТАЛЬНОГО", 1)
        self.assertEqual(2, len(блок), "выписок в промпте нет вовсе")
        self.assertEqual(1, блок[1].count("— часть "),
                         "в промпт втиснуто больше выписок, чем отведено места")

    def test_в_совсем_тесном_окне_уступают_выписки_а_не_источник(self):
        """Порядок уступок: сперва пересказ, потом первоисточник — никогда.

        При окне в 9000 токенов места не хватает даже выпискам. Тогда они и
        уходят: без единого источника помощник превращается в обычную модель
        без ссылок на нормы, а это худший исход из всех.
        """
        self.settings.llm_context_tokens = 9000
        prepared = self.готово()
        self.assertTrue(prepared["sources"], "источники вырезаны до нуля")
        self.assertNotIn("ВЫПИСКИ ИЗ ОСТАЛЬНОГО", prepared["prompt"])
        self.assertIn("выписки не поместились",
                      (prepared["warning"] or "").lower())

    def test_метки_источников_в_выписке_сохранены(self):
        """По метке [S12] инженер откроет тот же источник, что и всегда."""
        self.готово()
        for текст in self.выписки():
            with self.subTest(проход=текст[:40]):
                self.assertRegex(текст, r"\[S\d+\]",
                                 "в часть материала не попали метки")


if __name__ == "__main__":
    unittest.main()
