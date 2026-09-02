"""Разделы, повторяющиеся по описи регистраций.

Ответ отдела на одну опись содержит раздел на каждую регистрацию: «Файлы в
каталогах …», «Условия записи», разбор, вывод — и так восемь раз, а на
другом письме тридцать. Число разделов задаёт опись, а не автор шаблона,
поэтому шаблон разворачивается по списку из факт-пакета.
"""

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.facts import FactPack, FactPackError
from reportgen.llm import StubLLM
from reportgen.pipeline import (
    Outline,
    SectionSpec,
    check_facts_coverage,
    generate_report,
    _item_block,
)

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = {
    "report_type": "opis",
    "title": "Результаты технического анализа",
    "fact_titles": {"line_type": "Линия связи", "equipment": "Оборудование линии"},
    "fact_units": {"clock_khz": "кГц"},
    "sections": [
        {"id": "scope", "title": "Исходные данные", "instruction": "Что поступило."},
        {
            "id": "registration",
            "title": "{caption}",
            "repeat_over": "registrations",
            "item_title": "Файл в каталоге «{catalogs}»",
            "item_required": ["line_type", "modulation", "clock_khz"],
            "instruction": "Разбери запись {n}.",
        },
        {"id": "summary", "title": "Выводы", "instruction": "Сведи воедино."},
    ],
}

PACK = {
    "case_id": "ВХ-2026-0487",
    "report_type": "opis",
    "registrations": [
        {
            "caption": "Файлы в каталогах «A_H», «A_V»",
            "catalogs": "A_H, A_V",
            "line_type": "РРЛС",
            "modulation": "ФМ-4С",
            "clock_khz": 8931,
            "record_format": "оцифрованный участок спектра",
            "equipment": "Ericsson MiniLink (Швеция)",
        },
        {
            "catalogs": "B_V",
            "line_type": "СЛС",
            "modulation": "DVB-S2",
            "clock_khz": 5450,
            "record_format": "SIG",
        },
    ],
}


def load(template=None, pack=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "outline_opis.json"
        path.write_text(json.dumps(template or TEMPLATE, ensure_ascii=False),
                        encoding="utf-8")
        outline = Outline.load(path)
    return outline, FactPack.from_dict(pack or PACK)


class ExpandTests(unittest.TestCase):
    def test_one_section_per_record(self):
        outline, facts = load()
        plan = outline.expand(facts)
        self.assertEqual(["scope", "registration-1", "registration-2", "summary"],
                         [spec.id for spec in plan])

    def test_the_heading_names_the_record(self):
        """Заголовок раздела — то, как отдел называет группу файлов.

        Своё название записи важнее шаблонного: в описи бывает и один файл,
        и три в разных каталогах, и назвать их одинаково нельзя.
        """
        outline, facts = load()
        plan = outline.expand(facts)
        self.assertEqual("Файлы в каталогах «A_H», «A_V»", plan[1].title)
        # Своего названия нет — собирается по образцу из шаблона.
        self.assertEqual("Файл в каталоге «B_V»", plan[2].title)

    def test_the_number_of_the_record_reaches_the_instruction(self):
        outline, facts = load()
        plan = outline.expand(facts)
        self.assertIn("Разбери запись 1.", plan[1].instruction)
        self.assertIn("Разбери запись 2.", plan[2].instruction)

    def test_identifiers_stay_put_so_edits_do_not_slide(self):
        """Правку третьего раздела нельзя уронить в четвёртый.

        Идентификатор нумеруется по порядку описи, и порядок описи —
        порядок отчёта: пересборка даёт те же имена разделов. И они разные:
        по идентификатору раздел лежит в базе, по нему же его правят и
        перегенерируют, а два раздела с одним именем схлопнутся в один.
        """
        outline, facts = load()
        first = [spec.id for spec in outline.expand(facts)]
        second = [spec.id for spec in outline.expand(facts)]
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)), f"повторяются: {first}")

    def test_an_empty_list_leaves_no_records_but_is_reported(self):
        # Пустая опись — не молчаливая пустота: отчёт выйдет без единого
        # разбора, и узнать об этом инженер должен до, а не после.
        outline, facts = load(pack={**PACK, "registrations": []})
        self.assertEqual(["scope", "summary"],
                         [spec.id for spec in outline.expand(facts)])
        missing = check_facts_coverage(facts, outline)
        self.assertIn("__list__:registrations", missing)

    def test_a_list_of_the_wrong_shape_says_which_field(self):
        outline, facts = load(pack={**PACK, "registrations": {"a": 1}})
        with self.assertRaises(FactPackError) as caught:
            outline.expand(facts)
        self.assertIn("registrations", str(caught.exception))

    def test_a_record_that_is_not_an_object_says_which_one(self):
        outline, facts = load(pack={**PACK, "registrations": [{"catalogs": "A"}, "мусор"]})
        with self.assertRaises(FactPackError) as caught:
            outline.expand(facts)
        self.assertIn("запись 2", str(caught.exception))

    def test_missing_fields_of_a_record_are_named_with_the_record(self):
        """«не хватает modulation» восемь раз подряд не говорит ни о чём.

        В списке нехватки должно быть видно, у какой именно регистрации
        поле не заполнено.
        """
        broken = {**PACK, "registrations": [
            {"catalogs": "A", "line_type": "РРЛС"},
            {"catalogs": "B", "line_type": "СЛС", "modulation": "DVB-S2", "clock_khz": 5450},
        ]}
        outline, facts = load(pack=broken)
        missing = check_facts_coverage(facts, outline)
        self.assertIn("registration-1", missing)
        self.assertEqual(["registrations[1].modulation", "registrations[1].clock_khz"],
                         missing["registration-1"])
        self.assertNotIn("registration-2", missing)


class ItemBlockTests(unittest.TestCase):
    def test_the_conditions_come_first_and_word_for_word(self):
        """Четыре строки «Условий записи» в письме стоят всегда первыми и
        всегда этими словами; их читают глазами и сверяют с описью."""
        outline, facts = load()
        spec = outline.expand(facts)[1]
        block = _item_block(spec.item, spec.item_titles, spec.item_units)
        head = block.split("\n\n")[0]
        self.assertIn("линия связи: РРЛС;", head)
        self.assertIn("вид модуляции: ФМ-4С;", head)
        self.assertIn("тактовая частота: 8931 кГц;", head)
        self.assertIn("формат записи: оцифрованный участок спектра.", head)

    def test_fields_are_named_in_russian(self):
        # «equipment» модели не говорит ничего, «Оборудование линии» — говорит.
        outline, facts = load()
        spec = outline.expand(facts)[1]
        block = _item_block(spec.item, spec.item_titles, spec.item_units)
        self.assertIn("Оборудование линии: Ericsson MiniLink (Швеция)", block)
        self.assertNotIn("equipment:", block)

    def test_the_prompt_block_is_not_escaped_for_markdown(self):
        """Имя каталога «7419_8931» с обратными косыми — другое имя.

        Экранирование нужно документу, а этот блок идёт в подсказку модели.
        """
        outline, facts = load()
        spec = outline.expand(facts)[1]
        block = _item_block(spec.item, spec.item_titles, spec.item_units)
        self.assertIn("A_H, A_V", block)
        self.assertNotIn("\\_", block)


class TemplateGuardTests(unittest.TestCase):
    def test_item_required_without_repeat_over_is_refused(self):
        broken = json.loads(json.dumps(TEMPLATE))
        broken["sections"][0]["item_required"] = ["line_type"]
        with self.assertRaises(ValueError) as caught:
            load(template=broken)
        self.assertIn("repeat_over", str(caught.exception))

    def test_the_data_of_a_record_cannot_be_written_into_the_template(self):
        broken = json.loads(json.dumps(TEMPLATE))
        broken["sections"][1]["item"] = {"line_type": "РРЛС"}
        with self.assertRaises(ValueError):
            load(template=broken)

    def test_an_unknown_search_domain_is_caught_at_load(self):
        """Опечатка в направлении не роняет ничего: поиск отбирает по
        несуществующему значению и не находит ни одного фрагмента.

        Раздел выходит без источников, и понять почему нельзя.
        """
        broken = json.loads(json.dumps(TEMPLATE))
        broken["sections"][1]["retrieval_domains"] = ["microwaive"]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "domains.json").write_text(
                json.dumps({"domains": [{"id": "microwave"}, {"id": "satellite"}]},
                           ensure_ascii=False), encoding="utf-8")
            path = Path(tmp) / "outline_opis.json"
            path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                Outline.load(path)
        self.assertIn("microwaive", str(caught.exception))
        self.assertIn("microwave", str(caught.exception))


SHIPPED = "outline_signal_analysis.json"
EXAMPLE = "opis-signals-2026.json"


class ItemDomainsTests(unittest.TestCase):
    """Полка библиотеки выбирается по записи, а не по шаблону."""

    BASE = {
        "id": "registration",
        "title": "{caption}",
        "instruction": "разбери {modulation}",
        "repeat_over": "registrations",
        "retrieval_domains": ["signal"],
        "item_domains": {
            "field": "line_type",
            "values": {"РРЛС": ["microwave", "signal"],
                       "СЛС": ["satellite", "protocols"]},
        },
    }

    def spec(self, **changes):
        raw = dict(self.BASE)
        raw.update(changes)
        return SectionSpec.from_dict(raw)

    def test_the_line_chooses_the_shelf(self):
        spec = self.spec()
        self.assertEqual(("microwave", "signal"),
                         tuple(spec.for_item({"line_type": "РРЛС"}, 1).retrieval_domains))
        self.assertEqual(("satellite", "protocols"),
                         tuple(spec.for_item({"line_type": "СЛС"}, 2).retrieval_domains))

    def test_an_unlisted_line_keeps_the_domains_of_the_section(self):
        """Заведут завтра тропосферную линию — раздел не останется без поиска."""
        got = self.spec().for_item({"line_type": "ТРЛ"}, 1)
        self.assertEqual(("signal",), tuple(got.retrieval_domains))

    def test_spaces_and_case_do_not_matter(self):
        got = self.spec().for_item({"line_type": " ррлс "}, 1)
        self.assertEqual(("microwave", "signal"), tuple(got.retrieval_domains))

    def test_the_expanded_section_no_longer_carries_the_table(self):
        # Иначе повторное разворачивание выбирало бы направление второй раз,
        # уже по данным чужой записи.
        got = self.spec().for_item({"line_type": "РРЛС"}, 1)
        self.assertEqual({}, got.item_domains)

    def test_the_query_speaks_of_this_registration(self):
        spec = self.spec(retrieval_queries=["разбор {modulation} на {line_type}"])
        got = spec.for_item({"line_type": "РРЛС", "modulation": "КАМ-16"}, 1)
        self.assertEqual(("разбор КАМ-16 на РРЛС",), tuple(got.retrieval_queries))

    def test_a_table_without_a_repeating_section_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.spec(repeat_over="")
        self.assertIn("repeat_over", str(caught.exception))

    def test_a_broken_table_is_refused_at_load(self):
        cases = {
            "нет поля": {"values": {"РРЛС": ["signal"]}},
            "поле пустое": {"field": "  ", "values": {"РРЛС": ["signal"]}},
            "нет таблицы": {"field": "line_type"},
            "таблица пуста": {"field": "line_type", "values": {}},
            "направлений нет": {"field": "line_type", "values": {"РРЛС": []}},
            "направление не строка": {"field": "line_type",
                                      "values": {"РРЛС": [7]}},
            "лишнее поле": {"field": "line_type", "values": {"РРЛС": ["signal"]},
                            "default": ["signal"]},
        }
        for name, table in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.spec(item_domains=table)

    def test_an_unknown_domain_in_the_table_is_caught_when_the_template_loads(self):
        """Опечатка в направлении не роняет ничего — раздел просто слепнет."""
        raw = json.loads(json.dumps(TEMPLATE))
        repeating = next(section for section in raw["sections"]
                         if section.get("repeat_over"))
        repeating["item_domains"] = {
            "field": "line_type",
            "values": {"РРЛС": ["microwaive"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "domains.json").write_text(
                json.dumps({"domains": [{"id": "microwave", "keywords": []}]},
                           ensure_ascii=False), encoding="utf-8")
            path = Path(tmp) / "outline_opis.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                Outline.load(path)
        self.assertIn("microwaive", str(caught.exception))


class ShippedTemplateTests(unittest.TestCase):
    """Шаблон технического анализа, снятый с настоящего ответа отдела."""

    def test_the_template_loads_and_repeats_over_the_list(self):
        outline = Outline.load(ROOT / "templates" / SHIPPED)
        self.assertEqual(["registrations"], outline.repeats_over())

    def test_one_template_covers_both_kinds_of_line(self):
        """В одном письме отдела идут и релейные, и спутниковые регистрации.

        Двумя шаблонами такое письмо не собрать: разделы пришлось бы
        сшивать руками, а сквозная нумерация рисунков разъехалась бы.
        """
        facts = FactPack.load(ROOT / "examples" / "cases" / EXAMPLE)
        kinds = {item["line_type"] for item in facts.item_list("registrations")}
        self.assertEqual({"РРЛС", "СЛС"}, kinds)
        outline = Outline.load(ROOT / "templates" / SHIPPED)
        plan = outline.expand(facts)
        by_line = {spec.item.get("line_type"): tuple(spec.retrieval_domains)
                   for spec in plan if spec.item}
        self.assertEqual(("microwave", "signal", "hardware"), by_line["РРЛС"])
        self.assertEqual(("satellite", "signal", "protocols"), by_line["СЛС"])

    def test_the_search_query_speaks_of_this_registration(self):
        outline = Outline.load(ROOT / "templates" / SHIPPED)
        facts = FactPack.load(ROOT / "examples" / "cases" / EXAMPLE)
        queries = [query
                   for spec in outline.expand(facts)
                   for query in spec.retrieval_queries]
        self.assertIn("РРЛС КАМ-256 разбор сигнала", queries)
        self.assertIn("СЛС DVB-S2 разбор сигнала", queries)

    def test_the_example_pack_gives_a_section_per_registration(self):
        outline = Outline.load(ROOT / "templates" / SHIPPED)
        facts = FactPack.load(ROOT / "examples" / "cases" / EXAMPLE)
        plan = outline.expand(facts)
        self.assertEqual(8, len(plan))
        self.assertEqual({}, check_facts_coverage(facts, outline))

    def test_the_report_assembles_with_a_section_per_registration(self):
        outline = Outline.load(ROOT / "templates" / SHIPPED)
        facts = FactPack.load(ROOT / "examples" / "cases" / EXAMPLE)
        result = generate_report(facts, outline, StubLLM(), None,
                                 generated_at="2026-09-02")
        self.assertEqual(8, len(result.sections))
        self.assertIn("Файл в каталоге «7745_34000_QAM-256»", result.markdown)
        # Порядок разделов — порядок описи, и он воспроизводим.
        again = generate_report(facts, outline, StubLLM(), None,
                                generated_at="2026-09-02")
        self.assertEqual([s.spec.title for s in result.sections],
                         [s.spec.title for s in again.sections])


if __name__ == "__main__":
    unittest.main()
