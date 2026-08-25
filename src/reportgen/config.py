"""Конфигурация приложения.

Значения берутся из переменных окружения ``REPORTGEN_*`` и (при наличии) из
JSON-файла, путь к которому задан в ``REPORTGEN_CONFIG``. Явные аргументы
конструктора важнее и того, и другого.

Ядро (facts/corpus/retrieval/pipeline/verify) конфигурацию не использует —
оно остаётся библиотекой без зависимостей и без глобального состояния.
Конфигурация нужна только приложению: веб-серверу, приёму документов и CLI.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict

ENV_PREFIX = "REPORTGEN_"


def default_data_dir() -> Path:
    """Корень изменяемых данных. Читается в момент создания Settings, а не импорта."""
    return Path(os.environ.get(f"{ENV_PREFIX}DATA_DIR", "./var")).resolve()


@dataclass
class Settings:
    """Все настройки установки в одном месте."""

    # -- пути ---------------------------------------------------------------
    # Подкаталоги по умолчанию выводятся из data_dir: задав только его, вы
    # переносите всю установку целиком (это же поведение нужно тестам).
    data_dir: Path = field(default_factory=default_data_dir)
    db_path: Path | None = None
    library_dir: Path | None = None
    upload_dir: Path | None = None
    export_dir: Path | None = None
    templates_dir: Path = Path("templates")
    glossary_path: Path = Path("templates/glossary.json")
    #: Справочник направлений техники. None — искать рядом с пакетом.
    domains_path: Path | None = None
    docx_template: Path | None = None

    # -- языковая модель ---------------------------------------------------
    llm_kind: str = "openai"
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_model: str = "local-model"
    llm_api_key: str = "not-needed"
    llm_timeout: float = 600.0
    llm_seed: int | None = 0
    llm_temperature: float = 0.2
    llm_parallel_sections: int = 2

    # -- эмбеддинги и реранкер --------------------------------------------
    embed_enabled: bool = False
    embed_base_url: str = "http://127.0.0.1:8001/v1"
    embed_model: str = "bge-m3"
    embed_api_key: str = "not-needed"
    embed_batch: int = 16
    embed_timeout: float = 120.0

    rerank_enabled: bool = False
    rerank_base_url: str = "http://127.0.0.1:8001/v1"
    rerank_model: str = "bge-reranker-v2-m3"
    rerank_api_key: str = "not-needed"
    rerank_timeout: float = 120.0

    # -- поиск -------------------------------------------------------------
    retrieval_candidates: int = 60
    #: Сколько фрагментов библиотеки попадает в промпт. Больше — ответ полнее и
    #: лучше подкреплён ссылками, но длиннее промпт и медленнее генерация.
    retrieval_top_k: int = 8

    # -- оформление ----------------------------------------------------------
    # Подставляется в интерфейс и в колонтитул DOCX: название компании,
    # подпись отдела, акцентный цвет и логотип (PNG/SVG рядом с настройками).
    brand_name: str = "Экспертиза связи"
    brand_subtitle: str = "Подготовка технических отчётов"
    brand_accent: str = "#15507e"
    brand_logo: Path | None = None
    report_footer: str = ""

    # -- веб ---------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8080
    auth_enabled: bool = True
    session_ttl_hours: int = 12
    max_upload_mb: int = 200
    secret_key: str = ""

    def __post_init__(self) -> None:
        for name in ("data_dir", "db_path", "library_dir", "upload_dir", "export_dir",
                     "templates_dir", "glossary_path", "domains_path", "docx_template",
                     "brand_logo"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Path):
                setattr(self, name, Path(value))
        derived = {
            "db_path": "reportgen.db",
            "library_dir": "library",
            "upload_dir": "uploads",
            "export_dir": "exports",
        }
        for name, suffix in derived.items():
            if getattr(self, name) is None:
                setattr(self, name, self.data_dir / suffix)

    # -- загрузка ----------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides: Any) -> "Settings":
        raw: Dict[str, Any] = {}
        config_path = path or os.environ.get(f"{ENV_PREFIX}CONFIG")
        if config_path and Path(config_path).is_file():
            raw.update(json.loads(Path(config_path).read_text(encoding="utf-8-sig")))

        types = {f.name: f.type for f in fields(cls)}
        for name in types:
            env_value = os.environ.get(f"{ENV_PREFIX}{name.upper()}")
            if env_value is not None:
                raw[name] = env_value

        raw.update(overrides)
        return cls(**{name: _coerce(name, value, types) for name, value in raw.items() if name in types})

    def ensure_dirs(self) -> None:
        for directory in (self.data_dir, self.library_dir, self.upload_dir, self.export_dir):
            Path(directory).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> Dict[str, Any]:
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(self).items()}

    def public_dict(self) -> Dict[str, Any]:
        """Настройки без секретов — их можно отдавать в интерфейс."""
        hidden = {"secret_key", "llm_api_key", "embed_api_key", "rerank_api_key"}
        return {k: v for k, v in self.to_dict().items() if k not in hidden}


_TRUE = {"1", "true", "yes", "on", "да"}
_FALSE = {"0", "false", "no", "off", "нет", ""}


def _coerce(name: str, value: Any, types: Dict[str, Any]) -> Any:
    annotation = str(types.get(name, "str"))
    if not isinstance(value, str):
        return value
    if "bool" in annotation:
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"{name}: ожидалось логическое значение, получено {value!r}")
    if "int" in annotation and "None" in annotation:
        return None if value == "" else int(value)
    if "int" in annotation:
        return int(value)
    if "float" in annotation:
        return float(value)
    if "Path" in annotation:
        return None if value == "" else Path(value)
    return value
