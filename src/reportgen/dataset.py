"""Сбор обучающего набора из правок инженеров (док. 03, разделы 3.5, 3.7, 3.8).

Каждая правка инженера — бесплатная разметка: пара «черновик модели → финал
человека» сохраняется веб-слоем в таблицу ``edit_pairs`` вместе с контекстом
секции (шапка обращения, инструкция шаблона, факты, источники). Этот модуль
превращает накопленные пары в готовые к обучению наборы:

* ``sft`` — диалог ``system / user / assistant``, где ответом служит текст
  инженера. Лосс при обучении считается только по ответу;
* ``dpo`` — тройка ``prompt / chosen / rejected``: ``chosen`` — финал,
  ``rejected`` — черновик модели. Именно это выравнивает модель по вкусу
  инженеров (категоричность выводов, объём, детализация).

Два инварианта, которые модуль обязан соблюдать:

1. **Промпт восстанавливается ровно тем же шаблоном**, которым конвейер
   генерировал черновик (:data:`reportgen.prompts.SECTION_PROMPT`). Обучающий
   пример, отличающийся от боевого промпта форматом, учит модель не тому.
2. **Деление на train/test — по кейсам, а не по секциям** (док. 03, 3.5).
   Секции одного отчёта похожи друг на друга; попав в разные части, они дают
   завышенную оценку качества.

Модуль работает на чистой стандартной библиотеке и не ходит в сеть: сеть
нужна только :func:`reverse_annotate`, и то через переданный клиент модели.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Sequence, Tuple

from .facts import FactPack, FactPackError
from .prompts import SECTION_PROMPT, SYSTEM_PROMPT

if TYPE_CHECKING:  # только для подсказок типов — модуль не тянет хранилище
    from .llm import LLM
    from .pipeline import Outline
    from .store.models import EditPair
    from .store.repo import Repositories

# Версия формата обучающего примера. Попадает в карточку датасета (док. 3.8):
# без неё через полгода нельзя понять, чем v3 отличается от v2.
SCHEMA_VERSION = "1"

KINDS = ("sft", "dpo")

DEFAULT_SFT_MIN_DISTANCE = 0.02
DEFAULT_DPO_MIN_DISTANCE = 0.05
DEFAULT_TEST_RATIO = 0.15
DEFAULT_SEED = 0

# Заглушки для полей промпта, не сохранённых вместе с правкой.
NO_FACTS = "(факты этой секции не сохранены вместе с правкой)"
NO_SOURCES = "(источники этой секции не сохранены вместе с правкой)"
NO_PREVIOUSLY = "(это первый раздел отчёта)"
NO_INSTRUCTION = (
    "Инструкция шаблона не сохранена: ориентируйся на название раздела "
    "и на общие правила оформления отчёта."
)
UNKNOWN_CASE_ID = "БЕЗ-НОМЕРА"


class DatasetError(RuntimeError):
    """Обучающий набор не удалось собрать или разметить."""


# ------------------------------------------------------- восстановление ---

def _context_value(context: Dict[str, Any], key: str, default: str) -> str:
    value = context.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _is_degraded(context: Dict[str, Any]) -> bool:
    """Пример считается неполным, если контекст секции не сохранён.

    Такие примеры не выбрасываются: текст инженера ценен сам по себе как
    образец стиля. Но помечаются, чтобы их можно было исключить одной
    строкой, если окажется, что они учат модель писать без опоры на факты.
    """
    if not context:
        return True
    return not any(str(context.get(key, "")).strip()
                   for key in ("facts", "sources", "header", "instruction"))


def restore_prompt(pair: "EditPair") -> Tuple[str, bool]:
    """Восстанавливает промпт секции по сохранённому контексту правки.

    Возвращает пару «текст промпта, признак неполноты». Формат промпта в
    точности совпадает с боевым (:data:`prompts.SECTION_PROMPT`).
    """
    context = dict(pair.context or {})
    degraded = _is_degraded(context)

    header = _context_value(
        context,
        "header",
        f"Обращение: {pair.case_id or UNKNOWN_CASE_ID}\n"
        f"Тип отчёта: {pair.report_type or '—'}",
    )
    title = _context_value(context, "title", pair.section_title or pair.section_id)
    instruction = _context_value(context, "instruction", NO_INSTRUCTION)

    target_words = context.get("target_words") or 0
    try:
        target_words = int(target_words)
    except (TypeError, ValueError):
        target_words = 0
    if target_words <= 0:
        # Ориентир объёма берём из самого ответа инженера — это ровно тот
        # объём, который он счёл правильным для этого раздела.
        target_words = max(50, len((pair.final or "").split()))

    return (
        SECTION_PROMPT.format(
            header=header,
            title=title,
            instruction=instruction,
            target_words=target_words,
            style=_context_value(context, "style", "деловой технический"),
            facts=_context_value(context, "facts", NO_FACTS),
            sources=_context_value(context, "sources", NO_SOURCES),
            previously=_context_value(context, "previously", NO_PREVIOUSLY),
        ),
        degraded,
    )


def _meta(pair: "EditPair") -> Dict[str, Any]:
    return {
        "pair_id": pair.id,
        "case_id": pair.case_id,
        "report_type": pair.report_type,
        "section_id": pair.section_id,
        "section_title": pair.section_title,
        "facts_digest": pair.facts_digest,
        "edit_distance": round(float(pair.edit_distance or 0.0), 4),
        "created_at": pair.created_at,
    }


# ------------------------------------------------------------- выборка ----

def _all_pairs(repos: "Repositories") -> List["EditPair"]:
    """Все пары правок в устойчивом порядке (по возрастанию идентификатора).

    Порядок фиксирован намеренно: датасет — версионируемый артефакт, и два
    прогона на одной базе обязаны дать побайтово одинаковые файлы.
    """
    total = int(repos.edits.count() or 0)
    if total <= 0:
        return []
    pairs = list(repos.edits.list(limit=total + 1))
    return sorted(pairs, key=lambda pair: (pair.id is None, pair.id))


def _select(
    repos: "Repositories",
    *,
    min_distance: float,
    report_types: Iterable[str] | None,
    limit: int | None,
) -> List["EditPair"]:
    allowed = set(report_types) if report_types else None
    selected: List["EditPair"] = []
    for pair in _all_pairs(repos):
        if float(pair.edit_distance or 0.0) < min_distance:
            continue
        if allowed is not None and pair.report_type not in allowed:
            continue
        if not (pair.final or "").strip():
            continue
        selected.append(pair)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def build_sft_examples(
    repos: "Repositories",
    *,
    min_distance: float = DEFAULT_SFT_MIN_DISTANCE,
    report_types: Iterable[str] | None = None,
    limit: int | None = None,
) -> List[Dict[str, Any]]:
    """Примеры для SFT: восстановленный промпт секции → финальный текст инженера.

    :param min_distance: отсечка косметических правок. Пара, где инженер
        поправил запятую, обучению ничего не даёт, но раздувает набор.
    :param report_types: ограничение по типам отчётов — под каждый тип
        разумно держать отдельный LoRA-адаптер (док. 03, 3.6).
    :param limit: максимум примеров (для быстрых прогонов).
    """
    examples: List[Dict[str, Any]] = []
    for pair in _select(repos, min_distance=min_distance,
                        report_types=report_types, limit=limit):
        user, degraded = restore_prompt(pair)
        examples.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
                {"role": "assistant", "content": pair.final},
            ],
            "degraded": degraded,
            "meta": _meta(pair),
        })
    return examples


def build_dpo_examples(
    repos: "Repositories",
    *,
    min_distance: float = DEFAULT_DPO_MIN_DISTANCE,
    report_types: Iterable[str] | None = None,
    limit: int | None = None,
) -> List[Dict[str, Any]]:
    """Примеры для DPO/ORPO: ``chosen`` — финал инженера, ``rejected`` — черновик.

    Порог по расстоянию выше, чем для SFT: предпочтение имеет смысл только
    там, где правка содержательная. На косметике модель научится вкусу
    инженера к пробелам, а не к формулировкам.
    """
    examples: List[Dict[str, Any]] = []
    for pair in _select(repos, min_distance=min_distance,
                        report_types=report_types, limit=limit):
        if not (pair.draft or "").strip():
            continue
        if (pair.draft or "").strip() == (pair.final or "").strip():
            continue
        prompt, degraded = restore_prompt(pair)
        examples.append({
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "chosen": pair.final,
            "rejected": pair.draft,
            "degraded": degraded,
            "meta": _meta(pair),
        })
    return examples


# --------------------------------------------------------------- деление --

def _case_of(example: Dict[str, Any]) -> str:
    meta = example.get("meta") or {}
    return str(meta.get("case_id") or example.get("case_id") or "")


def split_by_case(
    examples: Sequence[Dict[str, Any]],
    test_ratio: float = DEFAULT_TEST_RATIO,
    seed: int = DEFAULT_SEED,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Делит набор на train/test **по кейсам** (док. 03, 3.5).

    Секции одного отчёта написаны одной рукой в один день и сильно похожи;
    если часть из них попадёт в train, а часть в test, метрика на test
    покажет заученное, а не обобщение. Поэтому единица деления — кейс.
    """
    if not 0.0 <= test_ratio < 1.0:
        raise DatasetError(
            f"доля тестовой части должна быть в диапазоне [0, 1), получено {test_ratio}"
        )
    items = list(examples)
    if not items:
        return [], []

    cases = sorted({_case_of(example) for example in items})
    if test_ratio == 0.0 or len(cases) < 2:
        return items, []

    shuffled = list(cases)
    random.Random(seed).shuffle(shuffled)
    count = int(round(len(cases) * test_ratio))
    count = max(1, min(count, len(cases) - 1))
    test_cases = set(shuffled[:count])

    train = [example for example in items if _case_of(example) not in test_cases]
    test = [example for example in items if _case_of(example) in test_cases]
    return train, test


# ----------------------------------------------------------------- вывод --

def _target_text(example: Dict[str, Any]) -> str:
    if "chosen" in example:
        return str(example.get("chosen") or "")
    messages = example.get("messages") or []
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return str(message.get("content") or "")
    return ""


def _prompt_text(example: Dict[str, Any]) -> str:
    if "prompt" in example:
        return str(example.get("prompt") or "")
    for message in example.get("messages") or []:
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def write_jsonl(examples: Sequence[Dict[str, Any]], path: str | Path) -> int:
    """Пишет набор в JSONL (UTF-8, кириллица без экранирования).

    Экранированный ``\\u0424`` формально валиден, но делает набор нечитаемым
    глазами — а инженер обязан иметь возможность открыть файл и прочитать,
    чему учится модель.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False, sort_keys=False))
            handle.write("\n")
            written += 1
    return written


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    """Читает JSONL обратно — нужно для проверки выгрузки и для дообучения."""
    items: List[Dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise DatasetError(f"{path}, строка {number}: не разобрать JSON — {error}") from error
    return items


def dataset_stats(examples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка по набору: сколько, из каких кейсов, насколько правили."""
    items = list(examples)
    distances = [
        float((example.get("meta") or {}).get("edit_distance") or 0.0) for example in items
    ]
    report_types: Dict[str, int] = {}
    sections: Dict[str, int] = {}
    cases = set()
    for example in items:
        meta = example.get("meta") or {}
        cases.add(_case_of(example))
        key = str(meta.get("report_type") or "—")
        report_types[key] = report_types.get(key, 0) + 1
        section = str(meta.get("section_id") or "—")
        sections[section] = sections.get(section, 0) + 1

    targets = [len(_target_text(example)) for example in items]
    prompts = [len(_prompt_text(example)) for example in items]
    return {
        "examples": len(items),
        "cases": len(cases) if items else 0,
        "degraded": sum(1 for example in items if example.get("degraded")),
        "report_types": dict(sorted(report_types.items())),
        "sections": dict(sorted(sections.items())),
        "edit_distance": {
            "mean": round(sum(distances) / len(distances), 4) if distances else 0.0,
            "min": round(min(distances), 4) if distances else 0.0,
            "max": round(max(distances), 4) if distances else 0.0,
        },
        "target_chars": {
            "mean": round(sum(targets) / len(targets), 1) if targets else 0.0,
            "max": max(targets) if targets else 0,
        },
        "prompt_chars": {
            "mean": round(sum(prompts) / len(prompts), 1) if prompts else 0.0,
            "max": max(prompts) if prompts else 0,
        },
    }


def export_dataset(
    repos: "Repositories",
    out_dir: str | Path,
    *,
    kind: str = "sft",
    min_distance: float | None = None,
    report_types: Iterable[str] | None = None,
    limit: int | None = None,
    test_ratio: float = DEFAULT_TEST_RATIO,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """Выгружает набор в каталог: ``train.jsonl``, ``test.jsonl``, ``manifest.json``.

    ``manifest.json`` — карточка датасета из док. 03, 3.8: дата, число
    примеров, применённые фильтры и версия схемы. Без карточки набор через
    полгода невозможно воспроизвести и не с чем сравнить.
    """
    if kind not in KINDS:
        raise DatasetError(f"неизвестный вид набора '{kind}' (доступны: {', '.join(KINDS)})")

    types = list(report_types) if report_types else None
    if kind == "sft":
        threshold = DEFAULT_SFT_MIN_DISTANCE if min_distance is None else min_distance
        examples = build_sft_examples(
            repos, min_distance=threshold, report_types=types, limit=limit
        )
    else:
        threshold = DEFAULT_DPO_MIN_DISTANCE if min_distance is None else min_distance
        examples = build_dpo_examples(
            repos, min_distance=threshold, report_types=types, limit=limit
        )

    train, test = split_by_case(examples, test_ratio=test_ratio, seed=seed)
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    train_path = directory / "train.jsonl"
    test_path = directory / "test.jsonl"
    write_jsonl(train, train_path)
    write_jsonl(test, test_path)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filters": {
            "min_distance": threshold,
            "report_types": types,
            "limit": limit,
            "test_ratio": test_ratio,
            "seed": seed,
        },
        "counts": {
            "total": len(examples),
            "train": len(train),
            "test": len(test),
        },
        "files": {"train": train_path.name, "test": test_path.name},
        "stats": {
            "all": dataset_stats(examples),
            "train": dataset_stats(train),
            "test": dataset_stats(test),
        },
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


# ------------------------------------------------- обратная разметка -------

REVERSE_SYSTEM_PROMPT = """Ты — инженер-аналитик, который переносит данные из готового \
технического отчёта в машиночитаемый факт-пакет.

Жёсткие правила:

1. Ты НЕ вычисляешь и НЕ додумываешь ничего. В факт-пакет попадают только те значения, \
которые буквально написаны в тексте отчёта.
2. Ты обязан извлечь ВСЕ числовые значения, встречающиеся в отчёте как результаты \
измерений или расчётов, а также все находки (обнаруженные отклонения и неисправности).
3. Единица измерения хранится отдельно от значения: значение — число, единица — строка.
4. Выводы и оценки («уровень превышает норму») — это находки, а не измерения.
5. Ответ — один объект JSON и ничего кроме него: без пояснений, без комментариев, \
без обрамляющего текста."""

REVERSE_PROMPT_TEMPLATE = """### СХЕМА ФАКТ-ПАКЕТА

Верхний уровень: case_id (строка), report_type (строка), group_no — номер \
группы, откуда пришло письмо (строка), \
request (строка), equipment (объект «название: значение»), keywords (список строк), \
measurements (объект), findings (список), timeline (список объектов с полями date и event).

Измерение — элемент объекта measurements. Ключ измерения — короткий латинский \
идентификатор в нижнем регистре (snr, evm, occupied_bandwidth, packet_count). \
Допустимые поля измерения и только они: title, value, unit, method, uncertainty, \
source, note. Поле value обязательно.

Находка — элемент списка findings. Допустимые поля и только они: id, severity, title, \
description, evidence, refs. Поля id, severity, title обязательны. Значение severity — \
одно из: info, low, medium, high, critical. В evidence перечисляются ключи измерений \
из measurements этого же факт-пакета и ничего больше.

Образец формы ответа (значения — заглушки, копировать их нельзя):

<SAMPLE>

### ТИП ОТЧЁТА

<TYPE>

<OUTLINE>

### ТЕКСТ ОТЧЁТА

<REPORT>

### ЗАДАНИЕ

Извлеки из текста отчёта все измерения и все находки и верни один объект JSON по \
описанной схеме. Ни одного числа, которого нет в тексте. Ни одной находки, которой \
нет в тексте. Если величина упомянута без единицы измерения, оставь unit пустой строкой."""

_SAMPLE_JSON = """{
  "case_id": "SUP-0000-000",
  "report_type": "signal_issue",
  "group_no": "1274",
  "request": "краткая формулировка обращения",
  "equipment": {"линия": "...", "модем": "..."},
  "keywords": ["..."],
  "measurements": {
    "snr": {"title": "Отношение сигнал/шум", "value": 0.0, "unit": "дБ",
            "method": "как получено", "uncertainty": "0.1 дБ", "source": "откуда значение"}
  },
  "findings": [
    {"id": "F1", "severity": "medium", "title": "формулировка одной строкой",
     "description": "что именно наблюдается", "evidence": ["snr"], "refs": ["ГОСТ ..., разд. 1"]}
  ],
  "timeline": [{"date": "2024-01-01", "event": "получены материалы"}]
}"""

_FENCE_RE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.DOTALL)
_CASE_ID_RE = re.compile(r"Обращение:?\**\s*([A-ZА-Я0-9][\w\-/.]*)")


def _strip_fence(text: str) -> str:
    """Снимает ограждение из тройных обратных кавычек, если модель его добавила."""
    match = _FENCE_RE.search(text)
    return match.group(1) if match else text


def parse_json_object(text: str) -> Dict[str, Any]:
    """Устойчивый разбор JSON-ответа модели.

    Локальные модели любят добавить «Вот результат:» перед JSON и обернуть
    его в ограждение из обратных кавычек. Разбор это переживает; всё
    остальное — честная ошибка, а не молчаливое «ну примерно так».
    """
    if not text or not text.strip():
        raise DatasetError("модель вернула пустой ответ вместо факт-пакета")
    candidate = _strip_fence(text).strip()
    for attempt in (candidate, _braced(candidate)):
        if attempt is None:
            continue
        try:
            data = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            raise DatasetError(
                f"модель вернула {type(data).__name__} вместо объекта JSON с факт-пакетом"
            )
        return data
    preview = " ".join(candidate.split())[:200]
    raise DatasetError(f"ответ модели не содержит корректного JSON: {preview}")


def _braced(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start:end + 1]


def _normalize_factpack(data: Dict[str, Any]) -> Dict[str, Any]:
    """Приводит частые вольности модели к форме схемы, не меняя значений."""
    result = dict(data)


    measurements = result.get("measurements")
    if isinstance(measurements, list):
        # Модель отдала список вместо объекта — собираем словарь по ключу.
        collected: Dict[str, Any] = {}
        for item in measurements:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or item.get("name") or item.get("id") or "").strip()
            if not key:
                continue
            collected[key] = {k: v for k, v in item.items() if k not in ("key", "name", "id")}
        result["measurements"] = collected

    findings = result.get("findings")
    if isinstance(findings, dict):
        result["findings"] = [
            {**value, "id": value.get("id", key)}
            for key, value in findings.items()
            if isinstance(value, dict)
        ]
    if isinstance(result.get("findings"), list):
        normalized = []
        for item in result["findings"]:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if isinstance(entry.get("severity"), str):
                entry["severity"] = entry["severity"].strip().lower()
            normalized.append(entry)
        result["findings"] = normalized
    return result


def reverse_annotate(
    llm: "LLM",
    report_markdown: str,
    report_type: str,
    *,
    outline: "Outline | None" = None,
    max_tokens: int = 4000,
) -> Dict[str, Any]:
    """«Обратная разметка» исторического отчёта (док. 03, 3.5).

    У компании есть ответы (готовые отчёты), но нет входов (факт-пакетов):
    их восстанавливают сильной моделью. Результат обязателен к выборочной
    ручной проверке — мусорный факт-пакет научит модель выдумывать связи,
    которых в фактах нет.

    :param llm: клиент модели; наружу ничего не уходит, контур локальный.
    :param report_markdown: текст исторического отчёта.
    :param report_type: тип отчёта; подставляется в результат принудительно.
    :param outline: шаблон-план соответствующего типа. Если передан, модели
        сообщаются ожидаемые ключи измерений — это резко снижает разнобой
        в именовании (``snr`` против ``SNR_db``).
    :raises DatasetError: модель вернула не JSON или факт-пакет не проходит
        валидацию :meth:`reportgen.facts.FactPack.from_dict`.
    """
    if not (report_markdown or "").strip():
        raise DatasetError("обратная разметка: текст отчёта пуст")

    outline_block = ""
    if outline is not None:
        keys = list(outline.required_facts())
        titles = [spec.title for spec in outline.sections]
        outline_block = (
            "### ОЖИДАЕМЫЕ КЛЮЧИ И РАЗДЕЛЫ\n\n"
            "Используй по возможности эти ключи измерений: "
            + (", ".join(keys) if keys else "(в шаблоне не заданы)")
            + ".\nРазделы шаблона этого типа отчёта: "
            + "; ".join(titles)
            + "."
        )

    user = (
        REVERSE_PROMPT_TEMPLATE
        .replace("<SAMPLE>", _SAMPLE_JSON)
        .replace("<TYPE>", report_type)
        .replace("<OUTLINE>", outline_block)
        .replace("<REPORT>", report_markdown)
    )

    try:
        answer = llm.complete(REVERSE_SYSTEM_PROMPT, user, max_tokens=max_tokens)
    except Exception as error:  # noqa: BLE001 — важна причина, а не её класс
        raise DatasetError(f"обратная разметка: обращение к модели не удалось — {error}") from error

    data = _normalize_factpack(parse_json_object(answer))
    data["report_type"] = report_type
    if not str(data.get("case_id") or "").strip():
        match = _CASE_ID_RE.search(report_markdown)
        data["case_id"] = match.group(1) if match else UNKNOWN_CASE_ID

    try:
        FactPack.from_dict(data)
    except FactPackError as error:
        raise DatasetError(
            f"обратная разметка: извлечённый факт-пакет не проходит валидацию — {error}"
        ) from error
    return data
