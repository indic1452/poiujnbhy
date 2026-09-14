# -*- coding: utf-8 -*-
"""Сплошная проверка находимости по всей библиотеке.

Отдел: «подобные проблемы, как с G.732, нужно решать, чтобы не было
пропусков и был полноценный анализ и вывод, по всей библиотеке».

Разбирать по одному документу, когда их тридцать тысяч, бессмысленно. Беда у
ненаходимого документа тихая: он числится в списке, виден человеку, а в
ответ не попадает никогда — со стороны это выглядит как «модель глупая».
"""

import hashlib
import unittest

import _bootstrap  # noqa: F401
from reportgen.audit import (
    MIN_DESIGNATION_DIGITS,
    MIN_DOC_CHARS,
    ПОРЯДОК,
    audit_library,
    summarize,
)
from reportgen.corpus import Chunk
from reportgen.store import Database, Repositories

#: Кусок нормальной длины: столько текста даёт разобравшийся документ.
ЖИВОЙ = ("Рекомендация описывает структуру цикла и порядок следования "
         "канальных интервалов в первичном цифровом тракте. ") * 12


class Библиотека(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)
        self.repos = Repositories(self.db)

    def положить(self, doc_id, title, куски, *, meta=None, status="current",
                 vectors=False):
        документ = self.repos.documents.upsert(
            doc_id, "standards", title, f"/lib/{doc_id}.pdf",
            hashlib.sha256(doc_id.encode()).hexdigest(),
            meta=meta or {}, domain="standard", status=status)
        self.repos.chunks.replace_for_document(документ, [Chunk(
            chunk_id=f"{doc_id}#{n}", doc_id=doc_id, doc_type="standards",
            title_path=[title], text=текст, meta={})
            for n, текст in enumerate(куски)])
        if vectors:
            for n in range(len(куски)):
                self.db.execute(
                    "INSERT OR REPLACE INTO embeddings(chunk_uid, model, dim, "
                    "vector) VALUES(?,?,?,?)",
                    (f"{doc_id}#{n}", "bge-m3", 1, b"\x00\x00\x80\x3f"))
        return документ

    def беды(self, отчёты, doc_id):
        for отчёт in отчёты:
            if отчёт.doc_id == doc_id:
                return отчёт.codes
        return []


class ЧтоСчитаетсяПропуском(Библиотека):
    def test_документ_без_фрагментов_не_находится_вовсе(self):
        self.положить("lib/scan", "Справочник по антеннам", [])
        отчёты = audit_library(self.repos)
        self.assertEqual(["нет-фрагментов"], self.беды(отчёты, "lib/scan"))
        self.assertTrue(отчёты[0].blocked)

    def test_две_строки_колонтитула_это_тоже_пропуск(self):
        self.положить("lib/pusto", "Методика измерений", ["ГОСТ 24375"])
        отчёты = audit_library(self.repos)
        self.assertIn("текста-почти-нет", self.беды(отчёты, "lib/pusto"))
        self.assertTrue(отчёты[0].blocked)

    def test_порог_пустоты_назван_числом(self):
        self.положить("lib/край", "Заметка", ["я" * (MIN_DOC_CHARS - 1)])
        self.положить("lib/норм", "Заметка вторая", ["я" * MIN_DOC_CHARS])
        отчёты = audit_library(self.repos)
        self.assertIn("текста-почти-нет", self.беды(отчёты, "lib/край"))
        self.assertNotIn("текста-почти-нет", self.беды(отчёты, "lib/норм"))

    def test_склеенный_текст_виден(self):
        self.положить("lib/glued", "Основы радиосвязи",
                      ["Методыцифровогокодирования" * 20],
                      meta={"text_quality": "glued"})
        отчёты = audit_library(self.repos)
        self.assertIn("текст-склеен", self.беды(отчёты, "lib/glued"))

    def test_негодное_название(self):
        self.положить("lib/blank", "Оглавление", [ЖИВОЙ])
        отчёты = audit_library(self.repos)
        self.assertIn("название-негодное", self.беды(отчёты, "lib/blank"))

    def test_не_действующий_документ_назван(self):
        """Он убран из поиска намеренно, но молчать об этом нельзя."""
        self.положить("lib/old", "Наставление по связи", [ЖИВОЙ],
                      status="superseded")
        отчёты = audit_library(self.repos)
        self.assertIn("не-действующий", self.беды(отчёты, "lib/old"))

    def test_здоровый_документ_в_отчёт_не_попадает(self):
        self.положить("lib/ok", "МСЭ-Т G.703. Стыки цифровых трактов",
                      ["Рекомендация G.703 описывает стык. " + ЖИВОЙ],
                      vectors=True)
        self.assertEqual([], audit_library(self.repos))


class ОбозначениеТолькоВНазвании(Библиотека):
    """Тот самый случай G.732.

    В названии «T-REC-G.732» номер есть, в тексте его нет ни разу. Словесный
    поиск по номеру такой документ не достаёт — и отдел это увидел.
    """

    def test_номер_из_названия_отсутствует_в_тексте(self):
        self.положить("lib/g732", "T-REC-G.732", [ЖИВОЙ], vectors=True)
        отчёты = audit_library(self.repos)
        self.assertEqual(["обозначение-только-в-названии"],
                         self.беды(отчёты, "lib/g732"))

    def test_номер_есть_в_тексте_замечания_нет(self):
        self.положить("lib/g703", "МСЭ-Т G.703. Стыки",
                      ["Настоящая Рекомендация G.703 определяет стык. " + ЖИВОЙ],
                      vectors=True)
        self.assertEqual([], self.беды(audit_library(self.repos), "lib/g703"))

    def test_номер_в_другой_записи_тоже_считается(self):
        """В тексте пишут «G.732 (11/88)» и «Recommendation G.732»."""
        self.положить("lib/g732", "T-REC-G.732",
                      ["Recommendation G.732 (11/88). " + ЖИВОЙ], vectors=True)
        self.assertEqual([], self.беды(audit_library(self.repos), "lib/g732"))

    def test_тире_вместо_дефиса_не_повод_жаловаться(self):
        """В названии ГОСТа дефис, в тексте самого ГОСТа — тире.

        Сличать строку целиком нельзя: жалоба вышла бы на каждый второй
        ГОСТ библиотеки, а ложная жалоба дороже пропущенной.
        """
        self.положить("lib/gost", "ГОСТ Р 53363-2009",
                      ["ГОСТ Р 53363—2009. Цифровые радиорелейные линии. "
                       + ЖИВОЙ], vectors=True)
        self.assertEqual([], self.беды(audit_library(self.repos), "lib/gost"))

    def test_короткий_номер_не_проверяется(self):
        """«25» из «X.25» есть в любом документе — молчим."""
        self.assertEqual(2, MIN_DESIGNATION_DIGITS - 1)
        self.положить("lib/x25", "МСЭ-Т X.25. Протокол",
                      ["Настоящая рекомендация описывает протокол. " + ЖИВОЙ],
                      vectors=True)
        self.assertEqual([], self.беды(audit_library(self.repos), "lib/x25"))

    def test_у_документа_без_фрагментов_номер_не_проверяем(self):
        """Там уже названа беда потяжелее, и вторая только запутает."""
        self.положить("lib/g732", "T-REC-G.732", [])
        self.assertEqual(["нет-фрагментов"],
                         self.беды(audit_library(self.repos), "lib/g732"))


class ВекторыЭтоБедаВсейБиблиотеки(Библиотека):
    """Тридцать тысяч одинаковых строк утопили бы всё остальное."""

    def test_векторов_нет_ни_у_кого_в_строках_документов_молчим(self):
        for номер in range(3):
            self.положить(f"lib/d{номер}", f"Документ по связи {номер}", [ЖИВОЙ])
        отчёты = audit_library(self.repos)
        self.assertEqual([], отчёты, "беда всей библиотеки попала в каждую строку")

    def test_про_это_говорит_сводка(self):
        for номер in range(3):
            self.положить(f"lib/d{номер}", f"Документ по связи {номер}", [ЖИВОЙ])
        строки = summarize(audit_library(self.repos), total=3, vectors=0, chunks=3)
        self.assertTrue(any("СМЫСЛОВОЙ ПОИСК НЕ РАБОТАЕТ" in строка
                            for строка in строки), строки)

    def test_отставший_документ_виден_когда_векторы_есть(self):
        self.положить("lib/есть", "Первый документ по связи", [ЖИВОЙ],
                      vectors=True)
        self.положить("lib/нет", "Второй документ по связи", [ЖИВОЙ])
        отчёты = audit_library(self.repos)
        self.assertEqual(["нет-векторов"], self.беды(отчёты, "lib/нет"))
        self.assertEqual([], self.беды(отчёты, "lib/есть"))


class СводкаГоворитГлавное(Библиотека):
    def test_считает_ненаходимые_отдельно(self):
        self.положить("lib/scan", "Справочник по антеннам", [], vectors=True)
        self.положить("lib/blank", "Оглавление", [ЖИВОЙ], vectors=True)
        строки = summarize(audit_library(self.repos), total=2, vectors=1, chunks=1)
        сводка = "\n".join(строки)
        self.assertIn("документов в библиотеке: 2", сводка)
        self.assertIn("НЕ НАХОДЯТСЯ ИЛИ ПОЧТИ НЕ НАХОДЯТСЯ: 1", сводка)
        self.assertIn("нет-фрагментов: 1", сводка)
        self.assertIn("название-негодное: 1", сводка)

    def test_пустая_библиотека(self):
        self.assertEqual(["библиотека пуста"], summarize([], total=0))

    def test_ненаходимые_идут_первыми(self):
        self.положить("lib/blank", "Оглавление", [ЖИВОЙ], vectors=True)
        self.положить("lib/scan", "Справочник по антеннам", [], vectors=True)
        отчёты = audit_library(self.repos)
        self.assertEqual("lib/scan", отчёты[0].doc_id,
                         "ненаходимый документ должен стоять первым")

    def test_беды_ненаходимости_стоят_в_начале_порядка(self):
        """Отдельного «сначала ненаходимые» в сортировке нет намеренно.

        Порядок бед сам ставит их первыми, и вторая проверка того же самого
        прикрывала бы первую. Здесь это записано как правило, а не как
        договорённость на словах: сдвинете «нет-фрагментов» в конец ПОРЯДКА —
        тест скажет об этом раньше, чем инженер увидит перемешанный список.
        """
        self.положить("lib/scan", "Справочник по антеннам", [], vectors=True)
        self.положить("lib/pusto", "Методика измерений", ["ГОСТ"], vectors=True)
        ненаходимые = set()
        for отчёт in audit_library(self.repos):
            ненаходимые.update(беда.code for беда in отчёт.findings
                               if беда.blocking)
        self.assertTrue(ненаходимые, "образец подобран неудачно")
        места = [ПОРЯДОК.index(код) for код in ненаходимые]
        self.assertEqual(list(range(len(ПОРЯДОК)))[:len(места)], sorted(места),
                         "беды ненаходимости обязаны стоять в начале ПОРЯДКА")


if __name__ == "__main__":
    unittest.main()
