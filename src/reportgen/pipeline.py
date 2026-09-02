"""Сборка отчёта: план → секции → сшивка.

Каждая секция генерируется отдельным вызовом модели со своим узким контекстом
(док. 04). Это даёт три вещи, недостижимые при генерации «одним промптом»:
ровное качество по всему документу, возможность перегенерировать один раздел
и параллельный запуск.
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .corpus import Chunk, tidy_quote

#: Сколько символов фрагмента подаётся модели. Приложение к отчёту берёт то же
#: значение: см. SourceRegistry.render_appendix.
PROMPT_QUOTE_CHARS = 700
from .facts import SEVERITIES, FactPack
from .llm import LLM
from .prompts import SECTION_PROMPT, SYSTEM_PROMPT
from .retrieval import Hit, Retriever

DEFAULT_STYLE = "нейтральный технический, без оценочных суждений"


#: Направления из templates/domains.json рядом с шаблоном. Читается один раз
#: на каталог: шаблонов десяток, а справочник один и тот же.
_DOMAIN_CACHE: Dict[str, frozenset] = {}


def _known_domains(directory: Path) -> frozenset:
    key = str(directory)
    if key not in _DOMAIN_CACHE:
        path = directory / "domains.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            ids = {str(item["id"]) for item in raw.get("domains", []) if item.get("id")}
        except (OSError, ValueError, KeyError, TypeError):
            ids = set()          # справочника нет — проверять нечем
        _DOMAIN_CACHE[key] = frozenset(ids)
    return _DOMAIN_CACHE[key]


def _as_is(value: Any) -> str:
    """Значение — для подсказки модели, без разметки Markdown.

    `plain` экранирует звёздочки и подчёркивания, потому что пишет в
    документ. Здесь текст идёт в подсказку, и «7419\\_8931» модель читает
    как имя каталога с обратными косыми — то есть как другое имя.
    """
    return str(value if value is not None else "")


def _fill(text: str, item: Dict[str, Any], caption: str, number: int) -> str:
    """Подставить в строку поля записи: «Файлы в каталогах {catalogs}».

    Подстановка своя, а не str.format: в инструкциях шаблона встречаются
    фигурные скобки сами по себе, и format на них падает. Здесь неизвестное
    имя остаётся как было — это видно человеку и не роняет генерацию.
    """
    def one(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key == "n":
            return str(number)
        if key == "caption":
            return caption
        if key not in item:
            return match.group(0)
        # Без экранирования: подстановка идёт в заголовок раздела и в
        # инструкцию, а имена каталогов у отдела сплошь с подчёркиваниями —
        # «7419\_8931\_CQPSK\_H» в заголовке письма это уже другое имя,
        # и сверить его с материалами нельзя.
        return _as_is(item[key])

    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", one, text)


def _item_caption(item: Dict[str, Any], pattern: str, number: int) -> str:
    """Как называется запись в заголовке раздела."""
    caption = str(item.get("caption", "") or "").strip()
    if caption:
        return caption
    if pattern:
        return _fill(pattern, item, "", number)
    for key in ("name", "id", "file", "title"):
        value = str(item.get(key, "") or "").strip()
        if value:
            return value
    return str(number)


#: Поля записи, которые в блок для модели не идут: заголовок уже вынесен
#: в название раздела, повторять его данными незачем.
_ITEM_SKIP = ("caption",)

#: Порядок и подписи блока «Условия записи» — как в исходящих отдела. Эти
#: четыре строки в письме стоят всегда первыми и всегда этими словами;
#: модель обязана воспроизвести их дословно, а не пересказать.
_CONDITIONS = (
    ("line_type", "линия связи"),
    ("modulation", "вид модуляции"),
    ("clock_khz", "тактовая частота"),
    ("record_format", "формат записи"),
)


def _item_block(item: Dict[str, Any], titles: Dict[str, str] | None = None,
                units: Dict[str, str] | None = None) -> str:
    """Данные одной записи — текстом для модели.

    Сперва «Условия записи» в готовом виде: их надо перенести в раздел
    дословно, и оставлять это на пересказ модели нельзя — там четыре строки,
    которые в отделе читают глазами и сверяют с описью.

    Дальше остальные поля записи, подписанные по-русски из словаря шаблона:
    «оборудование линии», а не «equipment». Имя ключа модели ничего не
    говорит, а из подписи она понимает, о чём речь.
    """
    titles, units = titles or {}, units or {}

    def value_of(key: str) -> str:
        text = _as_is(item[key])
        unit = units.get(key, "")
        return f"{text} {unit}".strip() if unit else text

    ready = [f"{label}: {value_of(key)};"
             for key, label in _CONDITIONS
             if item.get(key) not in ("", None, [], {})]
    if ready:
        ready[-1] = ready[-1][:-1] + "."

    known = {key for key, _ in _CONDITIONS}
    rest = []
    for key, value in item.items():
        if key in _ITEM_SKIP or key in known or value in ("", None, [], {}):
            continue
        rest.append(f"- {titles.get(key, key)}: {value_of(key)}")

    parts = []
    if ready:
        parts.append("УСЛОВИЯ ЗАПИСИ — перенести в раздел дословно, "
                     "первым абзацем:\n" + "\n".join(ready))
    if rest:
        parts.append("ОСТАЛЬНЫЕ ДАННЫЕ ЭТОЙ ЗАПИСИ (других по ней нет):\n"
                     + "\n".join(rest))
    return "\n\n".join(parts)


def _check_item_domains(section_id: str, table: Dict[str, Any]) -> None:
    """Проверяет форму таблицы направлений по полю записи.

    Ошибка в форме тихо обесценивает фильтр: раздел ищет по всей библиотеке
    и молчит об этом. Поэтому таблицу разбираем при загрузке шаблона.
    """
    unknown = set(table) - {"field", "values"}
    if unknown:
        raise ValueError(
            f"секция '{section_id}': в item_domains лишние поля "
            f"{sorted(unknown)}; допустимы 'field' и 'values'")
    field_name = table.get("field")
    if not isinstance(field_name, str) or not field_name.strip():
        raise ValueError(
            f"секция '{section_id}': в item_domains не указано поле записи "
            f"('field'), по которому выбирается направление")
    values = table.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError(
            f"секция '{section_id}': в item_domains пуста таблица 'values' — "
            f"выбирать не из чего")
    for key, domains in values.items():
        if not isinstance(domains, (list, tuple)) or not domains:
            raise ValueError(
                f"секция '{section_id}': в item_domains значению '{key}' не "
                f"сопоставлено ни одного направления")
        for name in domains:
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"секция '{section_id}': в item_domains у значения "
                    f"'{key}' направление задано не строкой")


@dataclass
class SectionSpec:
    """Описание одной секции шаблона-плана."""

    id: str
    title: str
    instruction: str
    required_facts: Sequence[str] = ()
    optional_facts: Sequence[str] = ()
    findings_min_severity: str | None = None
    retrieval_queries: Sequence[str] = ()
    retrieval_doc_types: Sequence[str] = ()
    #: Ограничить поиск направлениями (спутник, релейка, протоколы …).
    #: Пусто — искать по всей библиотеке.
    retrieval_domains: Sequence[str] = ()
    target_words: int = 250
    style: str = DEFAULT_STYLE
    #: Имя списка в факт-пакете, по которому раздел повторяется.
    #:
    #: В отделе ответ на одну опись содержит раздел на каждую регистрацию:
    #: «Файлы в каталогах …», «Условия записи», разбор, вывод — и так восемь
    #: раз, а на другом письме тридцать. Записывать тридцать одинаковых
    #: секций в шаблон нельзя: их число известно не автору шаблона, а
    #: описи. Здесь пишется имя списка («registrations»), и шаблон
    #: разворачивается по нему на столько разделов, сколько строк в описи.
    repeat_over: str = ""
    #: Поля, которые обязаны быть у каждой строки списка. Их отсутствие —
    #: то же, что отсутствие обязательного измерения: раздел выйдет с
    #: пометкой «не хватает данных».
    item_required: Sequence[str] = ()
    #: Как назвать саму строку в заголовке раздела, если в ней нет `caption`.
    item_title: str = ""
    #: Направления поиска, выбираемые по полю записи.
    #:
    #: Одно письмо отдела разбирает и релейные, и спутниковые регистрации:
    #: раздел один, а полки библиотеки под ними разные. Объединять полки
    #: нельзя — фильтр перестанет фильтровать; заводить два шаблона тоже
    #: нельзя — письмо-то одно. Поэтому направление выбирается по значению
    #: поля записи: {"field": "line_type", "values": {"РРЛС": [...], ...}}.
    #: Значение, которого нет в таблице, оставляет направления раздела.
    item_domains: Dict[str, Any] = field(default_factory=dict)
    #: Данные записи. Ставит `for_item`; в шаблоне этого поля не бывает.
    item: Dict[str, Any] = field(default_factory=dict)
    #: Русские подписи полей записи — из словаря шаблона, чтобы модель
    #: видела «оборудование линии», а не «equipment».
    item_titles: Dict[str, str] = field(default_factory=dict)
    #: Единицы полей записи: «8931 кГц», а не «8931».
    item_units: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SectionSpec":
        for required in ("id", "title", "instruction"):
            if required not in raw:
                raise ValueError(f"секция шаблона: отсутствует поле '{required}'")
        known = set(cls.__dataclass_fields__)
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"секция '{raw['id']}': неизвестные поля {sorted(unknown)}")
        if {"item", "item_titles", "item_units"} & set(raw):
            raise ValueError(
                f"секция '{raw['id']}': поля 'item' и 'item_titles' заполняются "
                f"при разворачивании раздела по описи, в шаблоне их быть не должно")
        spec = cls(**raw)
        if spec.item_required and not spec.repeat_over:
            raise ValueError(
                f"секция '{spec.id}': item_required задан без repeat_over — "
                f"проверять нечего, раздел не повторяется")
        if spec.findings_min_severity and spec.findings_min_severity not in SEVERITIES:
            # Опечатка в пороге доживала до сборки отчёта и роняла её
            # сообщением «tuple.index(x): x not in tuple» — по нему нельзя
            # догадаться ни о шаблоне, ни о секции, ни о том, что писать.
            raise ValueError(
                f"секция '{spec.id}': уровень находок "
                f"'{spec.findings_min_severity}' не заведён; допустимы "
                f"{', '.join(SEVERITIES)}")
        if spec.item_domains:
            if not spec.repeat_over:
                raise ValueError(
                    f"секция '{spec.id}': item_domains задан без repeat_over — "
                    f"выбирать направление не по чему, раздел не повторяется")
            _check_item_domains(spec.id, spec.item_domains)
        return spec

    def for_item(self, item: Dict[str, Any], number: int,
                 titles: Dict[str, str] | None = None,
                 units: Dict[str, str] | None = None) -> "SectionSpec":
        """Раздел под одну строку списка: свой номер, свой заголовок, свои данные.

        Идентификатор получает номер (`registration-3`): по нему секции
        различаются в базе, в проверке структуры и в правках инженера, и он
        обязан быть устойчивым — иначе правка третьего раздела после
        перегенерации уедет в четвёртый.
        """
        caption = _item_caption(item, self.item_title, number)
        # Недостающие поля записи докладываются тем же путём, что и
        # недостающие измерения: через required_facts развёрнутого раздела.
        # Ключ помечен именем записи, иначе в списке нехватки восемь
        # одинаковых строк «modulation» и непонятно, у какой регистрации.
        lacking = [f"{self.repeat_over}[{number}].{key}"
                   for key in self.item_required
                   if item.get(key) in ("", None, [], {})]
        return replace(
            self,
            id=f"{self.id}-{number}",
            title=_fill(self.title, item, caption, number),
            instruction=_fill(self.instruction, item, caption, number),
            required_facts=tuple(self.required_facts) + tuple(lacking),
            # Запрос к библиотеке тоже подставляется по записи: «разбор
            # {modulation} на {line_type}» уходит в поиск словами этой
            # регистрации, а не общими словами шаблона.
            retrieval_queries=tuple(
                _fill(query, item, caption, number)
                for query in self.retrieval_queries),
            retrieval_domains=self._domains_for(item),
            repeat_over="",
            item_required=(),
            item_domains={},
            item=dict(item),
            item_titles=dict(titles or {}),
            item_units=dict(units or {}),
        )

    def _domains_for(self, item: Dict[str, Any]) -> Sequence[str]:
        """Направления поиска для одной записи описи."""
        table = self.item_domains
        if not table:
            return self.retrieval_domains
        value = str(item.get(str(table.get("field", "")), "") or "").strip()
        for key, domains in dict(table.get("values", {})).items():
            if str(key).strip().casefold() == value.casefold():
                return tuple(domains)
        return self.retrieval_domains


@dataclass
class Outline:
    """Шаблон-план отчёта одного типа."""

    report_type: str
    title: str
    sections: List[SectionSpec]
    style: str = DEFAULT_STYLE
    version: str = "1"
    #: Короткое имя направления работы для списков и колонок. Полные
    #: названия начинаются одинаково, и в узком поле видно только общее
    #: начало. Пусто — берём полное название.
    short_title: str = ""
    #: Как называется каждое значение по-русски и в чём оно меряется.
    #: Ключи вроде packet_count — имена для кода; человеку, который заносит
    #: числа, они не говорят ничего, а спросить не у кого. Названия живут в
    #: шаблоне рядом с самими ключами: заводят новый ключ — тут же и
    #: подписывают, иначе словарь разъезжается с шаблоном.
    fact_titles: Dict[str, str] = field(default_factory=dict)
    fact_units: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Outline":
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        outline = cls._from_raw(raw)
        outline._check_domains(Path(path))
        return outline

    def _check_domains(self, path: Path) -> None:
        """Направления поиска в шаблоне обязаны существовать.

        Опечатка в направлении не роняет ничего: поиск просто отбирает по
        несуществующему значению и не находит ни одного фрагмента. Раздел
        выходит без источников, и понять почему нельзя — поэтому ловим при
        загрузке шаблона, а не при первом отчёте.
        """
        known = _known_domains(path.parent)
        if not known:
            return
        for spec in self.sections:
            declared = list(spec.retrieval_domains)
            for domains in dict(spec.item_domains.get("values", {})).values():
                declared.extend(domains)
            unknown = [item for item in declared if item not in known]
            if unknown:
                raise ValueError(
                    f"шаблон '{path.name}', секция '{spec.id}': неизвестные "
                    f"направления {sorted(unknown)}; заведены "
                    f"{sorted(known)}")

    @classmethod
    def _from_raw(cls, raw: Dict[str, Any]) -> "Outline":
        style = raw.get("style", DEFAULT_STYLE)
        # Стиль в шапке шаблона задаёт язык всего документа — «деловой
        # технический, прошедшее время, термины отдела». Раздел его
        # наследует, если не назначил себе свой. Пока наследования не было,
        # стиль в шапке не доходил до модели вовсе: каждая секция брала
        # общий по умолчанию, а автор шаблона был уверен, что задал тон
        # всему отчёту.
        sections = []
        for item in raw["sections"]:
            spec = SectionSpec.from_dict(item)
            if "style" not in item:
                spec = replace(spec, style=style)
            sections.append(spec)
        return cls(
            report_type=raw["report_type"],
            title=raw["title"],
            short_title=str(raw.get("short_title", "") or "").strip(),
            style=style,
            version=str(raw.get("version", "1")),
            fact_titles={str(key): str(value) for key, value
                         in (raw.get("fact_titles") or {}).items()},
            fact_units={str(key): str(value) for key, value
                        in (raw.get("fact_units") or {}).items()},
            sections=sections,
        )

    def fact_title(self, key: str) -> str:
        """Название значения по-русски. Нет в шаблоне — отдаём сам ключ."""
        return self.fact_titles.get(key) or key

    def required_facts(self) -> List[str]:
        seen: List[str] = []
        for section in self.sections:
            for key in section.required_facts:
                if key not in seen:
                    seen.append(key)
        return seen

    def expand(self, facts: "FactPack") -> List[SectionSpec]:
        """Разделы шаблона, развёрнутые по спискам факт-пакета.

        Раздел с `repeat_over` превращается в столько разделов, сколько строк
        в названном списке: по одному на регистрацию из описи. Порядок —
        порядок списка, то есть порядок описи; отчёт от этого остаётся
        воспроизводимым.

        Пустой список — не ошибка: раздела просто не будет. Ошибкой это
        станет позже, при проверке полноты, и там об этом скажут словами.
        """
        out: List[SectionSpec] = []
        for spec in self.sections:
            if not spec.repeat_over:
                out.append(spec)
                continue
            items = facts.item_list(spec.repeat_over)
            for number, item in enumerate(items, start=1):
                out.append(spec.for_item(item, number,
                                          self.fact_titles, self.fact_units))
        return out

    def expanded(self, facts: "FactPack") -> "Outline":
        """Тот же шаблон, но с уже развёрнутыми по описи разделами.

        Всё, что работает со списком разделов, — проверка структуры,
        перегенерация одной секции, подсчёт полноты — обязано видеть
        разделы такими же, какими их видела генерация. Иначе проверка
        требует раздел «{caption}», которого нет и быть не может.
        """
        if not any(spec.repeat_over for spec in self.sections):
            return self
        return replace(self, sections=self.expand(facts))

    def repeats_over(self) -> List[str]:
        """Имена списков факт-пакета, по которым разворачиваются разделы."""
        return [spec.repeat_over for spec in self.sections if spec.repeat_over]


class SourceRegistry:
    """Сквозная нумерация источников [S1], [S2], … по всему отчёту."""

    def __init__(self) -> None:
        self._by_chunk: Dict[str, str] = {}
        self._chunks: List[Chunk] = []
        # Секции могут генерироваться параллельно и метить источники одновременно.
        self._lock = threading.Lock()

    def label(self, chunk: Chunk) -> str:
        with self._lock:
            if chunk.chunk_id not in self._by_chunk:
                self._by_chunk[chunk.chunk_id] = f"S{len(self._chunks) + 1}"
                self._chunks.append(chunk)
            return self._by_chunk[chunk.chunk_id]

    @property
    def chunks(self) -> List[Chunk]:
        return list(self._chunks)

    def items(self) -> List[tuple[str, Chunk]]:
        """Пары (метка, фрагмент) в порядке первого упоминания в отчёте."""
        return [(self._by_chunk[chunk.chunk_id], chunk) for chunk in self._chunks]

    def render_appendix(self, quote_chars: int = PROMPT_QUOTE_CHARS) -> str:
        """Приложение «Источники» — то же, что видела модель.

        Приложение обязано быть НЕ КОРОЧЕ фрагмента, поданного в промпт.
        Верификатор считает числами из источника только то, что нашёл здесь:
        если модель законно взяла «2048 kbit/s» из символов 400–700 плотной
        английской таблицы, а в приложение попали первые 400, утверждение
        отчёта падает с «число отсутствует в факт-пакете» — на числе, которое
        инженер видит своими глазами в источнике. Причина при этом не видна
        нигде, и правка текста не помогает.
        """
        if not self._chunks:
            return "Внешние источники не привлекались."
        limit = max(int(quote_chars), PROMPT_QUOTE_CHARS)
        lines: List[str] = []
        for chunk in self._chunks:
            quote = tidy_quote(chunk.text, limit)
            body = quote.replace("\n", "\n> ")
            lines.append(f"**[{self._by_chunk[chunk.chunk_id]}]** {chunk.citation}\n\n> {body}\n")
        return "\n".join(lines)


@dataclass
class GeneratedSection:
    spec: SectionSpec
    text: str
    sources: List[str] = field(default_factory=list)
    missing_facts: List[str] = field(default_factory=list)


@dataclass
class ReportResult:
    markdown: str
    sections: List[GeneratedSection]
    registry: SourceRegistry
    missing_facts: List[str]
    meta: Dict[str, Any]


def _render_sources(hits: Sequence[Hit], registry: SourceRegistry,
                    quote_chars: int = PROMPT_QUOTE_CHARS) -> tuple[str, List[str]]:
    if not hits:
        return "(релевантных источников не найдено)", []
    blocks: List[str] = []
    labels: List[str] = []
    for hit in hits:
        label = registry.label(hit.chunk)
        labels.append(label)
        # Переводы строк сохраняем: таблицы норм и поля кадров в одну строку
        # нечитаемы и для модели, и для инженера.
        text = _tidy_quote(hit.chunk.text, quote_chars)
        status = hit.chunk.meta.get("status", "current")
        mark = "" if status == "current" else f" [ВНИМАНИЕ: документ не действующий — {status}]"
        blocks.append(f"[{label}] {hit.chunk.citation}{mark}\n{text}")
    return "\n\n".join(blocks), labels


def _tidy_quote(text: str, limit: int) -> str:
    """Цитата для промпта. См. :func:`reportgen.corpus.tidy_quote`."""
    return tidy_quote(text, limit)


def _summarize(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat[:limit].rstrip() + ("…" if len(flat) > limit else "")


def _section_query(spec: SectionSpec, facts: FactPack) -> str:
    parts = [spec.title, *spec.retrieval_queries]
    parts.extend(str(value) for value in facts.equipment.values())
    parts.extend(facts.keywords)
    for key in list(spec.required_facts) + list(spec.optional_facts):
        measurement = facts.measurements.get(key)
        if measurement is not None:
            parts.append(measurement.title)
    return " ".join(parts)


def generate_section(
    spec: SectionSpec,
    facts: FactPack,
    retriever: Retriever | None,
    llm: LLM,
    *,
    previously: Sequence[tuple[str, str]] = (),
    registry: SourceRegistry,
    top_k: int = 6,
) -> GeneratedSection:
    """Генерирует одну секцию. Секции независимы и могут считаться параллельно."""
    missing = facts.missing(spec.required_facts)

    keys = [*spec.required_facts, *spec.optional_facts]
    if spec.item:
        # Раздел развёрнут по описи: данные у него свои, а не из общих
        # измерений. Это тот же слот подсказки — модель ищет данные там же.
        facts_block = _item_block(spec.item, spec.item_titles, spec.item_units)
        extra = facts.render_measurements(keys) if keys else ""
        if extra:
            facts_block += "\n\n" + extra
    else:
        facts_block = (facts.render_measurements(keys) if keys
                       else "(измерения для раздела не заданы)")
    if spec.findings_min_severity:
        findings = facts.findings_at_least(spec.findings_min_severity)
        facts_block += "\n\n" + facts.render_findings(findings)
    if missing:
        facts_block += (
            "\n\nОТСУТСТВУЮТ ОБЯЗАТЕЛЬНЫЕ ДАННЫЕ: "
            + ", ".join(missing)
            + ". Отметь это строкой [ТРЕБУЕТ ПРОВЕРКИ: …]."
        )

    hits: List[Hit] = []
    if retriever is not None:
        query = _section_query(spec, facts)
        try:
            hits = retriever.search(
                query, top_k=top_k,
                doc_types=spec.retrieval_doc_types or None,
                domains=spec.retrieval_domains or None,
            )
        except TypeError:
            # Поисковик старого образца, без направлений.
            hits = retriever.search(
                query, top_k=top_k, doc_types=spec.retrieval_doc_types or None
            )
    sources_block, labels = _render_sources(hits, registry)

    previously_block = (
        "\n".join(f"- {title}: {summary}" for title, summary in previously)
        or "(это первый раздел отчёта)"
    )

    user = SECTION_PROMPT.format(
        header=facts.render_header(),
        title=spec.title,
        instruction=spec.instruction,
        target_words=spec.target_words,
        style=spec.style,
        facts=facts_block,
        sources=sources_block,
        previously=previously_block,
    )
    text = llm.complete(
        SYSTEM_PROMPT,
        user,
        # Русский текст — примерно 2.5 токена на слово, плюс запас на таблицы
        # и ссылки. Скупой лимит обрывал раздел на середине фразы, и инженер
        # видел это как «модель пишет мало».
        max_tokens=max(900, int(spec.target_words * 4)),
    )
    return GeneratedSection(spec=spec, text=text.strip(), sources=labels, missing_facts=missing)


def generate_report(
    facts: FactPack,
    outline: Outline,
    llm: LLM,
    retriever: Retriever | None = None,
    *,
    top_k: int = 6,
    generated_at: str | None = None,
    index_version: str = "—",
    parallel_sections: int = 1,
) -> ReportResult:
    """Полный цикл: секции → сшивка → служебный блок → приложение источников.

    ``parallel_sections`` — сколько секций писать одновременно. Секции идут
    волнами: внутри волны они пишутся параллельно, а каждая следующая волна
    видит краткое содержание всех предыдущих и потому не повторяется. Это
    компромисс между скоростью и связностью: при одном GPU и батчинге в
    llama.cpp волна из двух секций почти вдвое быстрее двух последовательных,
    потому что веса модели читаются из памяти один раз на обе.

    Порядок секций в результате всегда соответствует шаблону, поэтому отчёт
    остаётся воспроизводимым (инвариант 1.4.3).
    """
    if facts.report_type != outline.report_type:
        raise ValueError(
            f"тип отчёта в факт-пакете ('{facts.report_type}') не совпадает "
            f"с шаблоном ('{outline.report_type}')"
        )

    registry = SourceRegistry()
    generated: List[GeneratedSection] = []
    previously: List[tuple[str, str]] = []
    wave_size = max(1, int(parallel_sections))
    # Разделы, повторяющиеся по описи, разворачиваются здесь: дальше по коду
    # разница между «раздел шаблона» и «раздел по записи» уже не нужна.
    plan = outline.expand(facts)

    for start in range(0, len(plan), wave_size):
        wave = plan[start:start + wave_size]
        if len(wave) == 1:
            sections = [generate_section(
                wave[0], facts, retriever, llm,
                previously=previously, registry=registry, top_k=top_k,
            )]
        else:
            seen = list(previously)
            with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                sections = list(pool.map(
                    lambda spec: generate_section(
                        spec, facts, retriever, llm,
                        previously=seen, registry=registry, top_k=top_k,
                    ),
                    wave,
                ))
        for spec, section in zip(wave, sections):
            generated.append(section)
            previously.append((spec.title, _summarize(section.text)))

    meta = {
        "case_id": facts.case_id,
        "report_type": facts.report_type,
        "generated_at": generated_at or date.today().isoformat(),
        "outline_version": outline.version,
        "index_version": index_version,
        "model": getattr(llm, "name", "unknown"),
        "facts_digest": facts.digest(),
    }
    markdown = assemble(facts, outline, generated, registry, meta)
    missing = sorted({key for section in generated for key in section.missing_facts})
    return ReportResult(
        markdown=markdown,
        sections=generated,
        registry=registry,
        missing_facts=missing,
        meta=meta,
    )


def plain(value: Any) -> str:
    """Значение из факт-пакета — в текст документа как есть.

    Номер группы «*1274*» и модель «Р_168_М» — это данные, а не разметка.
    Без экранирования конвертер съедал звёздочки и подчёркивания, и в
    документе оказывалось не то, что записал инженер.
    """
    text = str(value if value is not None else "")
    for sign in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(sign, "\\" + sign)
    return text


def status_line(meta: Dict[str, Any]) -> str:
    """Строка о состоянии документа в шапке отчёта.

    Проверенный отчёт уходит по назначению. Пока строка была прибита гвоздями,
    он уходил с надписью «ЧЕРНОВИК, требует проверки и подписи инженера» —
    и в Markdown, и в DOCX. Для организации, которая этим отвечает на
    входящее письмо, это хуже опечатки.
    """
    if str(meta.get("status") or "draft") != "approved":
        line = "> Статус документа: **ЧЕРНОВИК**. Требует проверки и подписи инженера."
        # Число несведённых ошибок пишем прямо в документ. Черновик выгружают
        # и распечатывают — на бумаге не видно ни панели замечаний, ни того,
        # что верификатор вообще запускался.
        errors = int(meta.get("errors") or 0)
        if errors:
            line += (f" Верификатор нашёл ошибок: {errors} — числа в тексте"
                     " не подтверждены измерениями.")
        return line
    parts = ["> Статус документа: **УТВЕРЖДЁН**."]
    who = str(meta.get("approved_by_name") or "").strip()
    when = str(meta.get("approved_at") or "").strip()
    if who:
        parts.append(f"Утвердил: {who}.")
    if when:
        parts.append(f"Дата утверждения: {when[:10]}.")
    return " ".join(parts)


def assemble(
    facts: FactPack,
    outline: Outline,
    sections: Sequence[GeneratedSection],
    registry: SourceRegistry,
    meta: Dict[str, Any],
) -> str:
    """Сшивка: титул, служебный блок, оглавление, разделы, приложение."""
    lines: List[str] = [f"# {outline.title}", ""]
    lines.append(f"**Обращение:** {plain(facts.case_id)}  ")
    lines.append(f"**Номер группы:** {plain(facts.group_no) or '—'}  ")
    if facts.equipment:
        equipment = ", ".join(f"{plain(k)}: {plain(v)}" for k, v in facts.equipment.items())
        lines.append(f"**Оборудование:** {equipment}  ")
    lines.append(f"**Дата:** {meta['generated_at']}")
    lines.append("")
    lines.append(status_line(meta))
    lines.append("")

    lines.append("## Содержание")
    lines.append("")
    for number, section in enumerate(sections, start=1):
        lines.append(f"{number}. {section.spec.title}")
    lines.append(f"{len(sections) + 1}. Приложение А. Источники")
    lines.append("")

    for number, section in enumerate(sections, start=1):
        lines.append(f"## {number}. {section.spec.title}")
        lines.append("")
        lines.append(section.text)
        lines.append("")

    lines.append(f"## {len(sections) + 1}. Приложение А. Источники")
    lines.append("")
    lines.append(registry.render_appendix())
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("<!-- служебный блок: воспроизводимость (док. 01, инвариант 1.4.3) -->")
    lines.append("")
    lines.append("| Параметр сборки | Значение |")
    lines.append("|---|---|")
    for key in ("generated_at", "model", "outline_version", "index_version", "facts_digest"):
        lines.append(f"| {key} | {meta[key]} |")
    lines.append("")
    return "\n".join(lines)


def check_facts_coverage(facts: FactPack, outline: Outline) -> Dict[str, List[str]]:
    """Каких измерений не хватает до запуска модели.

    Вызывается сразу после автоанализа: инженеру сообщается, какой замер нужно
    доснять, до того как он потратит время на чтение черновика.
    """
    result: Dict[str, List[str]] = {}
    for spec in outline.expand(facts):
        missing = facts.missing(spec.required_facts)
        if missing:
            result[spec.id] = missing
    # Пустая опись — тоже нехватка данных, и молчать о ней нельзя: отчёт
    # выйдет без единого разбора, а инженер узнает об этом, прочитав его.
    for name in outline.repeats_over():
        if not facts.item_list(name):
            result.setdefault("__list__:" + name, []).append(name)
    return result
