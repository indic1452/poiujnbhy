"""Факт-пакет — единственный источник чисел для отчёта.

Факт-пакет формирует слой детерминированного анализа (tshark/scapy для дампов,
DSP-обработка для IQ-записей). Языковая модель получает его как данные и не
имеет права выйти за их пределы — это проверяет :mod:`reportgen.verify`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set

from . import numbers

SEVERITIES = ("info", "low", "medium", "high", "critical")


class FactPackError(ValueError):
    """Факт-пакет не соответствует схеме."""


#: Номер группы, откуда пришло письмо. Пишут по-разному: «1274», «12/345»,
#: «в/ч 74326», «группа связи». Формат задаёт делопроизводство отдела, а не
#: программа, поэтому проверяем только длину — чтобы в поле не уехал абзац.
MAX_GROUP = 120


#: Какого вида должно быть поле факт-пакета. Инженер правит пакет как JSON
#: прямо в интерфейсе, и «measurements: []» вместо «measurements: {}» —
#: обычная описка. Раньше она валила запрос с пятисотой ошибкой без единого
#: слова о причине; теперь говорим, где именно ошиблись.
_SHAPES: Dict[str, tuple] = {
    "measurements": (dict, "объектом «ключ: измерение»"),
    "equipment": (dict, "объектом «поле: значение»"),
    "findings": (list, "списком"),
    "artifacts": (list, "списком"),
    "timeline": (list, "списком"),
    "keywords": (list, "списком строк"),
}


def _check_shapes(raw: Dict[str, Any]) -> None:
    """Проверить вид полей до разбора: иначе разбор срывается без объяснения."""
    for field_name, (kind, expected) in _SHAPES.items():
        value = raw.get(field_name)
        if value is None or isinstance(value, kind):
            continue
        raise FactPackError(
            f"факт-пакет: поле '{field_name}' должно быть {expected}, "
            f"а задано {_kind_name(value)}")


def _kind_name(value: Any) -> str:
    """Название вида значения по-русски — для сообщения инженеру."""
    if isinstance(value, bool):
        return "да/нет"
    if isinstance(value, str):
        return "строкой"
    if isinstance(value, (int, float)):
        return "числом"
    if isinstance(value, list):
        return "списком"
    if isinstance(value, dict):
        return "объектом"
    return "пустым значением"


@dataclass(frozen=True)
class Measurement:
    """Одно измерение с единицей, методом получения и ссылкой на артефакт."""

    key: str
    title: str
    value: Any
    unit: str = ""
    method: str = ""
    uncertainty: str = ""
    source: str = ""
    note: str = ""

    @property
    def display(self) -> str:
        parts = [f"{self.value}"]
        if self.unit:
            parts.append(self.unit)
        text = " ".join(parts)
        if self.uncertainty:
            text += f" (± {self.uncertainty})"
        return text

    @classmethod
    def from_dict(cls, key: str, raw: Dict[str, Any]) -> "Measurement":
        if "value" not in raw:
            raise FactPackError(f"измерение '{key}': отсутствует поле 'value'")
        known = {f for f in cls.__dataclass_fields__ if f != "key"}
        unknown = set(raw) - known
        if unknown:
            raise FactPackError(
                f"измерение '{key}': неизвестные поля {sorted(unknown)}"
            )
        return cls(key=key, title=raw.get("title", key), **{
            k: v for k, v in raw.items() if k != "title"
        })


@dataclass(frozen=True)
class Finding:
    """Находка анализатора: что не так, чем подтверждается."""

    id: str
    severity: str
    title: str
    description: str = ""
    evidence: Sequence[str] = field(default_factory=tuple)
    refs: Sequence[str] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Finding":
        for required in ("id", "severity", "title"):
            if required not in raw:
                raise FactPackError(f"находка: отсутствует поле '{required}'")
        if raw["severity"] not in SEVERITIES:
            raise FactPackError(
                f"находка '{raw['id']}': severity должен быть одним из {SEVERITIES}"
            )
        return cls(
            id=raw["id"],
            severity=raw["severity"],
            title=raw["title"],
            description=raw.get("description", ""),
            evidence=tuple(raw.get("evidence", ())),
            refs=tuple(raw.get("refs", ())),
        )


def clean_group(value: Any) -> str:
    """Номер группы: лишние пробелы убраны, длина в пределах разумного."""
    text = " ".join(str(value or "").split())
    if len(text) > MAX_GROUP:
        raise FactPackError(f"номер группы: длиннее {MAX_GROUP} знаков")
    return text


@dataclass
class FactPack:
    """Полный набор исходных данных по обращению."""

    case_id: str
    report_type: str
    #: Номер группы, откуда пришло письмо. В базе эта колонка по историческим
    #: причинам называется customer — переименовывать её на работающей
    #: установке дороже, чем один раз объяснить это здесь.
    group_no: str = ""
    equipment: Dict[str, Any] = field(default_factory=dict)
    request: str = ""
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    measurements: Dict[str, Measurement] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    # -- загрузка -----------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "FactPack":
        if not isinstance(raw, dict):
            raise FactPackError("факт-пакет должен быть объектом JSON")
        for required in ("case_id", "report_type"):
            if required not in raw:
                raise FactPackError(f"факт-пакет: отсутствует поле '{required}'")
        for required in ("case_id", "report_type"):
            if not isinstance(raw[required], str):
                raise FactPackError(
                    f"факт-пакет: поле '{required}' должно быть строкой, "
                    f"а задано {_kind_name(raw[required])}")
        _check_shapes(raw)
        # `or` вместо значения по умолчанию: в JSON поле бывает выписано
        # с null — это «не заполнено», а не «поле кривое».
        measurements = {
            key: Measurement.from_dict(key, value)
            for key, value in (raw.get("measurements") or {}).items()
        }
        findings = [Finding.from_dict(item) for item in (raw.get("findings") or [])]
        known_keys = {ev for finding in findings for ev in finding.evidence}
        unknown = known_keys - set(measurements)
        if unknown:
            raise FactPackError(
                f"находки ссылаются на несуществующие измерения: {sorted(unknown)}"
            )
        return cls(
            case_id=raw["case_id"],
            report_type=raw["report_type"],
            # customer — прежнее имя ключа: факт-пакеты принятых обращений
            # лежат в базе с ним, и читать их система обязана.
            group_no=clean_group(raw.get("group_no", raw.get("customer", ""))),
            equipment=raw.get("equipment") or {},
            request=raw.get("request") or "",
            artifacts=list(raw.get("artifacts") or []),
            measurements=measurements,
            findings=findings,
            timeline=list(raw.get("timeline") or []),
            keywords=list(raw.get("keywords") or []),
            raw=raw,
        )

    @classmethod
    def load(cls, path: str | Path) -> "FactPack":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return cls.from_dict(data)

    # -- доступ -------------------------------------------------------------

    def missing(self, keys: Iterable[str]) -> List[str]:
        """Какие из требуемых шаблоном измерений отсутствуют."""
        return [key for key in keys if key not in self.measurements]

    def subset(self, keys: Iterable[str]) -> List[Measurement]:
        return [self.measurements[key] for key in keys if key in self.measurements]

    def findings_at_least(self, severity: str) -> List[Finding]:
        threshold = SEVERITIES.index(severity)
        return [f for f in self.findings if SEVERITIES.index(f.severity) >= threshold]

    # -- представление ------------------------------------------------------

    def render_measurements(self, keys: Iterable[str] | None = None) -> str:
        """Компактная таблица измерений для подстановки в промпт."""
        items = self.subset(keys) if keys is not None else list(self.measurements.values())
        if not items:
            return "(измерения отсутствуют)"
        lines = ["| Параметр | Значение | Метод | Источник |", "|---|---|---|---|"]
        for m in items:
            lines.append(
                f"| {m.title} | {m.display} | {m.method or '—'} | {m.source or '—'} |"
            )
        return "\n".join(lines)

    def render_findings(self, findings: Sequence[Finding] | None = None) -> str:
        items = list(self.findings if findings is None else findings)
        if not items:
            return "(находки отсутствуют)"
        lines = []
        for f in items:
            evidence = ", ".join(
                self.measurements[key].title for key in f.evidence if key in self.measurements
            )
            line = f"- [{f.severity}] {f.title}"
            if f.description:
                line += f" — {f.description}"
            if evidence:
                line += f" (подтверждается: {evidence})"
            if f.refs:
                line += f" [нормативные ссылки: {', '.join(f.refs)}]"
            lines.append(line)
        return "\n".join(lines)

    def render_header(self) -> str:
        equipment = ", ".join(f"{k}: {v}" for k, v in self.equipment.items()) or "—"
        artifacts = ", ".join(a.get("name", "?") for a in self.artifacts) or "—"
        return (
            f"Обращение: {self.case_id}\n"
            f"Номер группы: {self.group_no or '—'}\n"
            f"Оборудование: {equipment}\n"
            f"Суть обращения: {self.request or '—'}\n"
            f"Полученные материалы: {artifacts}"
        )

    # -- контроль -----------------------------------------------------------

    #: Поля, значения которых не дают права называть числа в отчёте: хеши и
    #: контрольные суммы состоят из цифровых групп, не имеющих смысла.
    OPAQUE_FIELDS = ("sha256", "sha1", "md5", "hash", "digest", "checksum", "crc", "uuid")

    def allowed_numbers(self) -> Set[str]:
        """Числа, которые модель имеет право использовать в отчёте.

        Только значения полей факт-пакета: измерения с погрешностями, методы,
        примечания, находки, хронология, сведения об оборудовании и артефактах.
        Имена полей и хеши исключены — см. :func:`reportgen.numbers.extract_from_object`.
        """
        values = numbers.extract_from_object(
            self.raw or self._as_dict(), skip_keys=self.OPAQUE_FIELDS
        )
        return values | numbers.derived_forms(values)

    def digest(self) -> str:
        """Хеш факт-пакета — попадает в служебный блок отчёта."""
        payload = json.dumps(self.raw or self._as_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def _as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "report_type": self.report_type,
            "group_no": self.group_no,
            "equipment": self.equipment,
            "request": self.request,
            "artifacts": self.artifacts,
            "measurements": {
                key: {
                    "title": m.title,
                    "value": m.value,
                    "unit": m.unit,
                    "method": m.method,
                    "uncertainty": m.uncertainty,
                    "source": m.source,
                    "note": m.note,
                }
                for key, m in self.measurements.items()
            },
            "findings": [
                {
                    "id": f.id,
                    "severity": f.severity,
                    "title": f.title,
                    "description": f.description,
                    "evidence": list(f.evidence),
                    "refs": list(f.refs),
                }
                for f in self.findings
            ],
            "timeline": self.timeline,
            "keywords": self.keywords,
        }
