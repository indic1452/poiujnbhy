"""Ссылка внутри документа открывает документ.

Отдел: «я задаю вопрос, в чём разница стандарта G.733 от G.734. Помимо
словесного описания в файлах, существует ещё и ссылка в этих документах на
документ G.704, где для каждого описана байтовая структура. Сможет ли модель
это всё проанализировать и выдать полный подробный ответ?»

Прежде — нет, и вот почему. Обозначение из ВОПРОСА система сверяла с описью
и документ подкладывала. Обозначение из НАЙДЕННОГО ТЕКСТА не читал никто.
Сами G.733 и G.734 байтовую структуру цикла не приводят — они на неё
ссылаются, и ответ выходил словесным пересказом отличий без единой цифры.

Здесь собрана ровно эта библиотека: две рекомендации, обе со ссылкой на
третью, и в третьей — то, ради чего вопрос и задавался.
"""

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.config import Settings
from reportgen.corpus import Chunk
from reportgen.llm import StubLLM
from reportgen.store import Database, Repositories
from reportgen.web.assistant import AssistantService
from reportgen.web.service import ReportService

ROOT = Path(__file__).resolve().parents[1]

G733 = (
    "Рекомендация G.733. Характеристики первичной аппаратуры "
    "мультиплексирования, работающей со скоростью 1544 кбит/с. Цикл содержит "
    "24 канальных интервала. Структура цикла и распределение битов приведены "
    "в Рекомендации G.704. Закон компандирования — мю-255. "
) * 6

G734 = (
    "Рекомендация G.734. Характеристики синхронной аппаратуры "
    "мультиплексирования, работающей со скоростью 1544 кбит/с. Цикл содержит "
    "24 канальных интервала. Структура цикла определена в Рекомендации "
    "G.704. Скорость передачи в стыке 1544 кбит/с. "
) * 6

G704 = (
    "Рекомендация G.704. Структуры синхронных циклов для иерархических "
    "скоростей. Байтовая структура цикла: канальный интервал 0 несёт "
    "циклический синхросигнал, интервал 16 — сигнализацию. Для 1544 кбит/с "
    "цикл состоит из 193 битов: 24 байта по 8 битов и один бит цикловой "
    "синхронизации F. Сверхцикл — 12 или 24 цикла. "
) * 6

ВОДА = (
    "Организация связи. Общие принципы построения систем передачи и "
    "распределения каналов по направлениям. Вопросы решаются при "
    "проектировании. "
) * 6

ВОПРОС = "В чём разница стандарта G.733 от G.734?"


class СсылкаОткрываетДокумент(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"))
        self.repos = Repositories(Database(":memory:"))
        for doc_id, title, текст in (
            ("lib/g733", "МСЭ-Т G.733", G733),
            ("lib/g734", "МСЭ-Т G.734", G734),
            ("lib/g704", "МСЭ-Т G.704", G704),
            ("lib/obshee", "Организация связи в войсках", ВОДА),
        ):
            документ = self.repos.documents.upsert(
                doc_id, "standards", title, f"/lib/{doc_id}.pdf",
                "sha-" + doc_id[-8:], meta={}, domain="standard")
            self.repos.chunks.replace_for_document(документ, [Chunk(
                chunk_id=f"{doc_id}#0", doc_id=doc_id, doc_type="standards",
                title_path=[title], text=текст, meta={})])
        self.reports = ReportService(repos=self.repos, settings=self.settings,
                                     llm=StubLLM())
        self.assistant = AssistantService(reports=self.reports)
        self.user = self.repos.users.create("ivanov", "пароль123", "Иванов",
                                            "engineer")
        self.chat = self.assistant.create_chat(self.user)

    def готово(self, вопрос=ВОПРОС):
        return self.assistant._prepare(self.user, self.chat.id, вопрос,
                                       top_k=None)

    def документы(self, prepared):
        return {item["doc_id"] for item in prepared["sources"]}

    def test_обе_рекомендации_из_вопроса_в_материале(self):
        """Это чинилось раньше: голое «G.733» опознаётся как обозначение."""
        документы = self.документы(self.готово())
        self.assertIn("lib/g733", документы)
        self.assertIn("lib/g734", документы)

    def test_g704_подтягивается_по_ссылке(self):
        """Ради чего всё и затевалось: байтовая структура цикла.

        Ни G.733, ни G.734 её не приводят. Вопрос про них, а ответ — в
        третьем документе, на который обе ссылаются.
        """
        self.assertIn("lib/g704", self.документы(self.готово()),
                      "документ по ссылке не подтянулся — "
                      "ответ будет без байтовой структуры")

    def test_байтовая_структура_дошла_до_промпта(self):
        prepared = self.готово()
        self.assertIn("193", prepared["prompt"],
                      "числа из документа по ссылке в промпт не попали")

    def test_человек_видит_что_открыли_по_ссылке(self):
        """Инженер должен понимать, откуда в ответе третий документ."""
        след = self.готово()["trail"]
        self.assertTrue([строка for строка in след if "по ссылке" in строка],
                        f"след не показывает переход по ссылке: {след}")

    def test_следование_можно_выключить(self):
        self.settings.assistant_follow_refs = 0
        prepared = self.готово()
        self.assertNotIn("lib/g704", self.документы(prepared))
        self.assertEqual([], [строка for строка in prepared["trail"]
                              if "по ссылке" in строка])

    def положить(self, doc_id, title, текст):
        документ = self.repos.documents.upsert(
            doc_id, "standards", title, f"/lib/{doc_id}.pdf",
            "sha-" + doc_id[-8:], meta={}, domain="standard")
        self.repos.chunks.replace_for_document(документ, [Chunk(
            chunk_id=f"{doc_id}#0", doc_id=doc_id, doc_type="standards",
            title_path=[title], text=текст, meta={})])

    def дописать(self, chunk_uid, хвост):
        with self.repos.db.transaction() as connection:
            connection.execute(
                "UPDATE chunks SET text = text || ? WHERE chunk_uid = ?",
                (хвост, chunk_uid))

    def test_предел_числа_документов_соблюдается(self):
        """Одна рекомендация МСЭ ссылается на десяток соседних.

        Проверяем на ДВУХ разных ссылках: с одной предел не отличить от
        отсутствия предела — второй документ и так бы не появился.
        """
        self.положить("lib/g711", "МСЭ-Т G.711",
                      "Импульсно-кодовая модуляция речевых частот. " * 8)
        self.дописать("lib/g733#0", " Кодирование по Рекомендации G.711.")
        self.settings.assistant_follow_refs = 2
        оба = self.документы(self.готово())
        self.assertIn("lib/g704", оба)
        self.assertIn("lib/g711", оба, "образец подобран неудачно: "
                      "второй ссылки не видно и предел нечем проверить")

        self.settings.assistant_follow_refs = 1
        след = [строка for строка in self.готово()["trail"]
                if "по ссылке" in строка]
        self.assertEqual(1, len(след), f"предел не соблюдён: {след}")

    def test_по_ссылке_из_ссылки_не_ходим(self):
        """Один шаг. Иначе одна рекомендация утащит за собой двадцать.

        G.704 в этой библиотеке ссылается на G.706 — и его в материале быть
        не должно: он назван не в вопросе и не в том, что нашлось по вопросу.
        """
        документ = self.repos.documents.upsert(
            "lib/g706", "standards", "МСЭ-Т G.706", "/lib/g706.pdf", "sha-g706",
            meta={}, domain="standard")
        self.repos.chunks.replace_for_document(документ, [Chunk(
            chunk_id="lib/g706#0", doc_id="lib/g706", doc_type="standards",
            title_path=["МСЭ-Т G.706"], text="Процедуры цикловой "
            "синхронизации, относящиеся к Рекомендации G.704. " * 6, meta={})])
        with self.repos.db.transaction() as connection:
            connection.execute(
                "UPDATE chunks SET text = text || ? WHERE chunk_uid = ?",
                (" Дополнительно см. Рекомендацию G.706.", "lib/g704#0"))
        self.assertNotIn("lib/g706", self.документы(self.готово()))

    def test_чужой_вопрос_ссылок_не_тянет(self):
        """Следование не должно включаться там, где ссылок в найденном нет."""
        prepared = self.готово("Как организована связь в войсках?")
        self.assertEqual([], [строка for строка in prepared["trail"]
                              if "по ссылке" in строка])


if __name__ == "__main__":
    unittest.main()
