# -*- coding: utf-8 -*-
"""Панель источников: что модель получила и на что сослалась.

Три вопроса отдела по одному экрану, и все три об одном — о доверии к ответу.

1. «Опыт в примечаниях пишет, что нет документа ITU-T G.733, хотя вот он в
   библиотеке». Документ на полке, пять фрагментов; вопрос был задан так:
   «в чем разница стандарта g 733 от 734». Обозначения не опознавались, с
   описью не сверялись, в материал не подкладывались.

2. «Некоторые ссылки перечёркнуты». Перечёркнутая метка — ссылка в никуда,
   и это правильно. Неправильно было другое: так же гасились ссылки на
   фрагменты, дошедшие до модели ВЫПИСКАМИ, — законные метки, которые сама
   же система и выдала.

3. «Сначала источников было несколько, в конце остался только один, как это
   работает и почему до сих пор не понятно». Панель схлопывалась до
   процитированного, и проверить ответ становилось нечем: инженер видел, на
   что модель сослалась, но не видел, что она прочитала и промолчала.
"""

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.config import Settings
from reportgen.corpus import Chunk
from reportgen.designations import implied_designations
from reportgen.llm import StubLLM
from reportgen.store import Database, Repositories
from reportgen.web.assistant import AssistantService
from reportgen.web.service import ReportService

ROOT = Path(__file__).resolve().parents[1]

G733 = (
    "Characteristics of primary PCM multiplex equipment operating at "
    "1544 kbit/s. Цикл содержит 24 канальных интервала, закон "
    "компандирования мю-255. Структура цикла приведена в Рекомендации "
    "G.704. "
) * 6

G734 = (
    "Characteristics of synchronous digital multiplex equipment operating "
    "at 1544 kbit/s. Стык 1544 кбит/с, синхронная схема объединения. "
    "Структура цикла определена в Рекомендации G.704. "
) * 6

ВОДА = (
    "Организация связи. Общие принципы построения систем передачи и "
    "распределения каналов по направлениям. "
) * 6

#: Ровно так, как отдел напечатал в живой системе.
ВОПРОС = "В чем разница стандарта g 733 от 734?"


class Библиотека(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"))
        self.repos = Repositories(Database(":memory:"))
        # Названия и опознаватели — как в выгрузке МСЭ, из которой отдел и
        # собирал библиотеку. Направление «прочее»: в описи оно так и стоит.
        self.положить(
            "standards/T-REC-G.733",
            "ITU-T Rec. G.733 (11/88) Characteristics of primary PCM "
            "multiplex equipment operating at 1544 kbit/s", G733, кусков=5)
        self.положить(
            "standards/T-REC-G.734",
            "ITU-T Rec. G.734 (11/88) Characteristics of synchronous digital "
            "multiplex equipment operating at 1544 kbit/s", G734, кусков=5)
        self.положить("lib/svyaz", "Организация связи в войсках", ВОДА)
        self.reports = ReportService(repos=self.repos, settings=self.settings,
                                     llm=StubLLM())
        self.assistant = AssistantService(reports=self.reports)
        self.user = self.repos.users.create("ivanov", "пароль123", "Иванов",
                                            "engineer")
        self.chat = self.assistant.create_chat(self.user)

    def положить(self, doc_id, title, текст, кусков=1):
        документ = self.repos.documents.upsert(
            doc_id, "standards", title, f"/{doc_id}.pdf",
            "sha-" + doc_id[-10:], meta={}, domain="other")
        self.repos.chunks.replace_for_document(документ, [Chunk(
            chunk_id=f"{doc_id}#{n}", doc_id=doc_id, doc_type="standards",
            title_path=[title], text=текст, meta={}) for n in range(кусков)])


class ДокументЧислитсяПоЛюбойЗаписи(Библиотека):
    def prepared(self, вопрос=ВОПРОС):
        return self.assistant._prepare(self.user, self.chat.id, вопрос,
                                       top_k=None)

    def сверка(self, вопрос=ВОПРОС):
        """Блок «ДОКУМЕНТЫ, НАЗВАННЫЕ В ВОПРОСЕ» из собранного промпта.

        Смотреть на промпт целиком нельзя: слова «в библиотеке НЕ ЧИСЛИТСЯ»
        стоят и в самом задании — там, где модели объясняют, когда так
        отвечать можно.
        """
        блок, _ = self.assistant._mentioned(вопрос, [])
        self.assertIn(блок.strip(), self.prepared(вопрос)["prompt"],
                      "сверка до промпта не дошла")
        return блок

    def test_обе_рекомендации_сверены_с_описью(self):
        """То самое место, где помощнику разрешено говорить «этого нет»."""
        блок = self.сверка()
        self.assertIn("G.733 — ЧИСЛИТСЯ", блок)
        self.assertIn("G.734 — ЧИСЛИТСЯ", блок)
        self.assertNotIn("НЕ ЧИСЛИТСЯ", блок)

    def test_обе_рекомендации_попали_в_материал(self):
        документы = {item["doc_id"] for item in self.prepared()["sources"]}
        self.assertIn("standards/T-REC-G.733", документы)
        self.assertIn("standards/T-REC-G.734", документы)

    def test_догадка_которой_в_описи_нет_молчит(self):
        """Догадку, не нашедшуюся в описи, инженеру не докладывают.

        «G.733, 704 и 711»: про 704 и 711 система лишь ДОГАДАЛАСЬ, что это
        номера той же серии. Их в библиотеке нет — и написать «G.704 в
        библиотеке НЕ ЧИСЛИТСЯ» значит соврать с другой стороны: инженер
        про G.704 не спрашивал, он написал число.
        """
        вопрос = "что сказано в G.733, 704 и 711"
        self.assertEqual(["G.704", "G.711"], implied_designations(вопрос),
                         "образец подобран неудачно: догадок не было, "
                         "и докладывать было бы нечего")
        блок, _ = self.assistant._mentioned(вопрос, [])
        self.assertIn("G.733 — ЧИСЛИТСЯ", блок)
        self.assertNotIn("G.704", блок)
        self.assertNotIn("G.711", блок)

    def test_ненайденное_обозначение_по_прежнему_докладывается(self):
        """Про названный полностью документ говорить правду обязаны."""
        self.assertIn("RFC 4818 — в библиотеке НЕ ЧИСЛИТСЯ",
                      self.сверка("есть ли у нас RFC 4818"))


class ПанельПоказываетВесьМатериал(Библиотека):
    def ответить(self, текст_ответа):
        class Свой(StubLLM):
            def complete(self, system, user, **kwargs):
                return текст_ответа

        self.reports.llm = Свой()
        return self.assistant.ask(self.user, self.chat.id, ВОПРОС)["answer"]

    def test_непроцитированное_остаётся_в_панели(self):
        answer = self.ответить("Отличие видно из [S1].")
        метки = [item["label"] for item in answer["sources"]]
        self.assertGreater(len(метки), 1,
                           "панель схлопнулась до процитированного")
        self.assertEqual(["S1"], [item["label"] for item in answer["sources"]
                                  if item["cited"]])
        self.assertTrue([item for item in answer["sources"]
                         if not item["cited"]],
                        "непроцитированные фрагменты потеряны")

    def test_счётчик_считает_только_процитированное(self):
        answer = self.ответить("Отличие видно из [S1].")
        self.assertEqual(1, answer["meta"]["cited"])

    def test_ответ_без_ссылок_не_выдаёт_три_первых_за_источники(self):
        """Прежде при отсутствии ссылок в панель клали sources[:3].

        Выглядело как «ответ опирается на эти три», хотя не опирался ни на
        что: рядом честно писалось «ответ не опирается на библиотеку», а
        панель это же и опровергала.
        """
        answer = self.ответить("Отвечаю по памяти, без ссылок.")
        self.assertEqual(0, answer["meta"]["cited"])
        self.assertEqual([], [item["label"] for item in answer["sources"]
                              if item["cited"]])

    def test_выдуманная_метка_в_панель_не_попадает(self):
        answer = self.ответить("Есть [S1], а ещё [S99].")
        self.assertNotIn("S99", [item["label"] for item in answer["sources"]])
        self.assertEqual(1, answer["meta"]["cited"])


class МеткиВыписокЗаконны(Библиотека):
    """Фрагмент не влез в окно, прочитан отдельным проходом — метка живая.

    Иначе система сама выдаёт модели метку [S12] в выписках, сама получает
    её обратно в ответе и сама же перечёркивает как выдуманную.
    """

    def setUp(self):
        super().setUp()
        # Окно, в которое влезает не всё найденное: остальное уходит в разбор
        # частями. Так это и выглядит на живой библиотеке — 22 найденных
        # фрагмента при восьми поместившихся.
        self.settings.llm_context_tokens = 12288
        self.settings.assistant_top_k = 12
        for n in range(8):
            self.положить(f"lib/pcm{n}",
                          f"Аппаратура ИКМ-30, том {n}",
                          ("Скорость 1544 кбит/с, цикл 125 мкс, канальные "
                           "интервалы и сверхцикл. ") * 20)

    def выписки(self):
        class Свой(StubLLM):
            def complete(self, system, user, **kwargs):
                if "ВЫПИШИ" in system.upper() or "выписк" in system.lower():
                    return "Из фрагмента следует, что цикл 125 мкс [S9]."
                return "по фрагментам"

        self.reports.llm = Свой()
        return self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                       top_k=None)

    def test_образец_подобран_так_что_разбор_частями_состоялся(self):
        """Иначе следующие два теста ничего не проверяют."""
        prepared = self.выписки()
        self.assertTrue(prepared.get("digest_sources"),
                        "в это окно всё поместилось: выписок не было, "
                        "и проверять здесь нечего")
        self.assertTrue(prepared["sources"], "материал пуст целиком")

    def test_выписанные_фрагменты_не_задваиваются_с_полными(self):
        prepared = self.выписки()
        целиком = {item["label"] for item in prepared["sources"]}
        for item in prepared["digest_sources"]:
            self.assertNotIn(item["label"], целиком,
                             "выписка задвоилась с полным фрагментом")

    def test_невошедшие_выписки_из_панели_убираются(self):
        """Окно совсем тесное — выписки уступают место источникам.

        Тогда их меток в промпте нет, и в панели им тоже не место: иначе
        панель обещает инженеру то, чего модель не видела.
        """
        self.settings.llm_context_tokens = 4096
        prepared = self.выписки()
        self.assertIn("выписки не поместились", prepared["warning"] or "",
                      "образец подобран неудачно: уступки не случилось")
        self.assertEqual([], prepared["digest_sources"])

    def test_ссылка_на_выписку_засчитывается(self):
        prepared = self.выписки()
        метка = prepared["digest_sources"][0]["label"]
        готово = self.assistant._finish(
            self.user, prepared, f"Цикл 125 мкс [{метка}].")
        self.assertEqual(1, готово["answer"]["meta"]["cited"])
        помеченные = [item for item in готово["answer"]["sources"]
                      if item["label"] == метка]
        self.assertTrue(помеченные, "выписанный фрагмент выпал из панели")
        self.assertTrue(помеченные[0]["cited"])
        self.assertTrue(помеченные[0]["digest"],
                        "инженер не увидит, что модель читала пересказ")


if __name__ == "__main__":
    unittest.main()
