"""Реестр конвертеров: какой формат кто разбирает и чего для этого не хватает.

Библиотека технической компании — это зоопарк: PDF рядом с DjVu-сканом книги,
DOCX рядом с DOC девяносто седьмого года, презентация с параметрами линии и
таблица Excel с бюджетом канала. Каждый формат разбирается своим способом, и
почти каждый способ требует чего-то стороннего.

Реестр решает три задачи:

* **диспетчеризация** — по расширению найти конвертер;
* **честная диагностика** — если формат не разобрать, сказать не «ошибка», а
  «для .djvu нужен пакет djvulibre, установите его так-то». В изолированном
  контуре это разница между «понятно, что доложить в комплект» и «непонятно,
  почему не работает»;
* **расширяемость** — новый формат добавляется отдельным модулем в
  ``reportgen.ingest.formats`` и одной строкой регистрации, без правки
  диспетчера.
"""

from __future__ import annotations

import importlib
import importlib.util
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from .convert import ConvertedDocument

Converter = Callable[[Path], "ConvertedDocument"]

#: Модули с конвертерами. Загружаются лениво, при первом обращении к реестру.
FORMAT_MODULES = "reportgen.ingest.formats"


@dataclass(frozen=True)
class Requirement:
    """Что нужно, чтобы конвертер работал."""

    kind: str          # "python" — пакет, "binary" — исполняемый файл
    name: str
    hint: str          # как установить, по-русски
    #: Как искать программу, если PATH недостаточно. Установщики под Windows
    #: сплошь и рядом себя в PATH не прописывают: Tesseract, DjVuLibre и 7-Zip
    #: ставятся в Program Files и остаются там. Без этого поля система
    #: сообщала бы, что формат не поддерживается, при установленной программе.
    locate: Callable[[], object] | None = field(default=None, compare=False, repr=False)

    def is_available(self) -> bool:
        if self.kind == "python":
            try:
                return importlib.util.find_spec(self.name) is not None
            except (ImportError, ValueError):
                return False
        if self.locate is not None:
            try:
                return bool(self.locate())
            except OSError:
                return False
        return shutil.which(self.name) is not None

    def to_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "hint": self.hint,
            "available": self.is_available(),
        }


@dataclass(frozen=True)
class ConverterSpec:
    """Конвертер одного или нескольких форматов."""

    name: str
    suffixes: Tuple[str, ...]
    convert: Converter
    requires: Tuple[Requirement, ...] = ()
    #: Если на один суффикс претендуют несколько конвертеров, берётся тот,
    #: у кого приоритет выше и все требования выполнены.
    priority: int = 0
    #: Короткое описание для диагностики и документации.
    note: str = ""

    def missing(self) -> List[Requirement]:
        return [item for item in self.requires if not item.is_available()]

    def is_available(self) -> bool:
        return not self.missing()

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "suffixes": list(self.suffixes),
            "priority": self.priority,
            "note": self.note,
            "available": self.is_available(),
            "requires": [item.to_dict() for item in self.requires],
        }


_REGISTRY: List[ConverterSpec] = []
_LOADED = False


def register(spec: ConverterSpec) -> ConverterSpec:
    """Добавить конвертер. Повторная регистрация того же имени заменяет прежнюю."""
    global _REGISTRY
    _REGISTRY = [item for item in _REGISTRY if item.name != spec.name]
    _REGISTRY.append(spec)
    return spec


def ensure_loaded() -> None:
    """Импортировать модули с конвертерами (однократно)."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        importlib.import_module(FORMAT_MODULES)
    except ImportError:  # pragma: no cover — пакет форматов не установлен
        pass


def reset(loaded: bool = False) -> None:
    """Очистить реестр. Нужно тестам, в рабочем коде не используется."""
    global _REGISTRY, _LOADED
    _REGISTRY = []
    _LOADED = loaded


def all_specs() -> List[ConverterSpec]:
    ensure_loaded()
    return sorted(_REGISTRY, key=lambda spec: (-spec.priority, spec.name))


def find(suffix: str) -> ConverterSpec | None:
    """Конвертер для расширения.

    Возвращает доступный конвертер с наибольшим приоритетом. Если доступного
    нет, но кто-то этот формат заявляет, — возвращает его: вызывающему нужно
    сообщить, чего именно не хватает, а не просто «формат не поддерживается».
    """
    suffix = suffix.lower()
    candidates = [spec for spec in all_specs() if suffix in spec.suffixes]
    if not candidates:
        return None
    available = [spec for spec in candidates if spec.is_available()]
    return available[0] if available else candidates[0]


def supported_suffixes(*, only_available: bool = False) -> Tuple[str, ...]:
    seen: List[str] = []
    for spec in all_specs():
        if only_available and not spec.is_available():
            continue
        for suffix in spec.suffixes:
            if suffix not in seen:
                seen.append(suffix)
    return tuple(sorted(seen))


def report() -> List[Dict[str, object]]:
    """Состояние поддержки форматов — для CLI и интерфейса."""
    return [spec.to_dict() for spec in all_specs()]


def missing_hint(spec: ConverterSpec) -> str:
    """Человеческое объяснение, чего не хватает конвертеру."""
    missing = spec.missing()
    if not missing:
        return ""
    parts = [f"{item.name} ({item.hint})" for item in missing]
    return "не хватает: " + "; ".join(parts)
