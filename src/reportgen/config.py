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
    #: Двуязычный словарь терминов: русский запрос — английские документы
    #: (RFC, паспорта микросхем). Пусто — берётся templates/terms.json.
    terms_path: Path | None = None
    docx_template: Path | None = None

    # -- языковая модель ---------------------------------------------------
    llm_kind: str = "openai"
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_model: str = "local-model"
    llm_api_key: str = "not-needed"
    #: Сколько ждать ответа модели. 900 с — столько же стоит в образце
    #: настроек, который копирует установщик: развёрнутый разбор на 4000
    #: токенов при 20 токенах в секунду занимает больше трёх минут, а на
    #: холодном старте с догрузкой весов — заметно больше.
    llm_timeout: float = 900.0
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
    #: Реранкер — ОТДЕЛЬНЫЙ сервер, на своём порту: 8001 занят эмбеддингами
    #: (см. scripts/windows/start-embed.ps1 и док. 11). Здесь стоял 8001, и
    #: установка, включившая реранк без явного адреса, спрашивала оценки у
    #: эмбеддера: тот отвечал ошибкой, реранк молча пропускался.
    rerank_base_url: str = "http://127.0.0.1:8002/v1"
    rerank_model: str = "bge-reranker-v2-m3"
    rerank_api_key: str = "not-needed"
    rerank_timeout: float = 120.0

    # -- поиск -------------------------------------------------------------
    retrieval_candidates: int = 60
    #: Сколько фрагментов библиотеки попадает в промпт. Больше — ответ полнее и
    #: лучше подкреплён ссылками, но длиннее промпт и медленнее генерация.
    retrieval_top_k: int = 8

    # -- развёрнутость ответа помощника --------------------------------------
    # Величины, от которых зависит полнота ответа. Их держат вместе, потому
    # что вместе они упираются в окно контекста модели.
    #
    # АРИФМЕТИКА ОКНА. llama-server запускается с -c 32768 --parallel 2, то
    # есть на один разговор приходится 16384 токена. Из них:
    #   assistant_max_tokens          — ответ                     4000
    #   системная инструкция          — ~900
    #   история разговора             — ~600
    #   остаётся под материал         — примерно 10800 токенов
    # Русский текст при этой токенизации — около 2,4 знака на токен, значит
    # материал не должен превышать ~26 000 знаков. Это и есть
    # assistant_context_chars: жёсткая граница, ниже которой блок ИСТОЧНИКИ
    # обрезается по одному фрагменту с конца выдачи.
    #
    # Что бывает, если границу не держать: llama.cpp молча выбрасывает начало
    # промпта. Уезжает системная инструкция — и модель перестаёт ставить
    # ссылки [S1], начинает округлять числа и отвечать по памяти. Со стороны
    # выглядит как «модель поглупела», а причина в переполненном окне.

    #: Сколько знаков каждого найденного фрагмента видит модель. Значение
    #: подобрано под нарезку корпуса: TARGET_CHARS в corpus.py — 2200, то
    #: есть при 2200 фрагмент доходит до модели целиком. При 1400 (как было)
    #: у каждого третьего фрагмента отрезалась треть — ровно та, где стояла
    #: таблица допусков или конец описания поля кадра.
    assistant_source_chars: int = 2200
    #: Жёсткий предел на весь блок ИСТОЧНИКИ в знаках (см. арифметику выше).
    assistant_context_chars: int = 26000
    #: Сколько фрагментов ищем для разговора. Больше, чем для отчёта: в
    #: отчёте материал ограничен факт-пакетом, в разговоре — только вопросом.
    assistant_top_k: int = 12
    #: Сколько соседних фрагментов подтягивать к найденному с каждой стороны.
    #: Таблица параметров или описание поля кадра редко умещается в один
    #: фрагмент: начало осталось в предыдущем, продолжение — в следующем.
    assistant_neighbours: int = 1
    #: Для скольких лучших фрагментов подтягивать соседей. Всем подряд не
    #: нужно: хвост выдачи и так на грани относимости.
    assistant_neighbour_top: int = 4
    #: Показывать ли модели оглавление документов, из которых взяты фрагменты.
    #: С ним она видит, что ещё есть в документе, и может сказать, где искать
    #: недостающее, вместо «в источниках этого нет».
    assistant_outlines: bool = True
    #: Сколько знаков приложенных файлов (дампов, логов, документов) видит
    #: модель — ВСЕХ вместе, а не каждого. Дамп на 40 МБ в окно не влезет
    #: никогда, поэтому берётся начало: там заголовки сессии и первые
    #: ошибки, по которым обычно и понятно, что случилось. Предел делится
    #: между файлами поровну, но короткий файл не занимает чужого — то,
    #: что он не выбрал, достаётся длинным. Входит в общий бюджет
    #: assistant_context_chars.
    assistant_attachment_chars: int = 8000
    #: Сколько знаков подсказки занимает карта библиотеки: перечень полок с
    #: числами и названия документов. Без неё помощник знает о библиотеке
    #: только то, что попало в найденные фрагменты, и не может ни отправить
    #: к соседнему тому, ни честно сказать «по этой линии у нас ничего нет».
    #: Ноль — не показывать карту вовсе.
    assistant_catalog_chars: int = 2500
    #: Сколько заходов разбора делает помощник, прежде чем отвечать. В каждом
    #: заходе он сам решает, что сделать: поискать другими словами, посмотреть
    #: оглавление тома, прочитать конкретную главу — или сказать, что материала
    #: достаточно. Ноль — прежнее поведение: один поиск и один проход модели.
    #: Каждый заход стоит короткого обращения к модели (одна строка ответа),
    #: поэтому на медленной машине число имеет смысл снизить.
    assistant_rounds: int = 4
    #: Потолок длины ответа в токенах. 4000 — это порядка 2,5 тыс. слов;
    #: упереться в него можно только на очень развёрнутом разборе.
    assistant_max_tokens: int = 4000
    #: Сколько слов ждать от ответа на содержательный вопрос. Число уходит в
    #: инструкцию модели: без него локальная модель отвечает справкой в
    #: три абзаца, сколько её ни проси «отвечать полно».
    assistant_target_words: int = 500

    # -- оформление ----------------------------------------------------------
    # Подставляется в интерфейс и в колонтитул DOCX: название компании,
    # подпись отдела, акцентный цвет и логотип (PNG/SVG рядом с настройками).
    #: Отдел, в котором работает система. Полное название стоит в окне
    #: входа и в заголовке окна, сокращённое — в шапке и на эмблеме: в
    #: строке меню длинному названию не хватит места, а сокращение в отделе
    #: и так у всех на слуху.
    brand_name: str = "2 специальный отдел"
    brand_short: str = "2СО"
    brand_subtitle: str = "Подготовка, учёт и проверка технических отчётов"
    brand_accent: str = "#15507e"
    brand_logo: Path | None = None
    #: Фон окна входа: свой файл JPG или PNG. Задать можно явно, но обычно
    #: достаточно положить файл рядом с settings.json под именем
    #: login-bg.jpg (или .png) — он подхватится сам.
    #:
    #: Зачем: рисованная заставка нравится не всем, а изолированная машина
    #: ничего не скачает. Снимок Земли из космоса берут на онлайн-компьютере
    #: и приносят вместе с остальной сборкой — тем же путём, что и модели.
    brand_login_image: Path | None = None
    report_footer: str = ""

    # -- веб ---------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8080
    auth_enabled: bool = True
    session_ttl_hours: int = 12
    max_upload_mb: int = 200

    def __post_init__(self) -> None:
        for name in ("data_dir", "db_path", "library_dir", "upload_dir", "export_dir",
                     "templates_dir", "glossary_path", "domains_path", "terms_path",
                     "docx_template",
                     "brand_logo", "brand_login_image"):
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
        settings = cls(**{name: _coerce(name, value, types)
                          for name, value in raw.items() if name in types})
        if settings.brand_login_image is None and config_path:
            settings.brand_login_image = _find_login_image(Path(config_path).parent)
        return settings

    def ensure_dirs(self) -> None:
        for directory in (self.data_dir, self.library_dir, self.upload_dir, self.export_dir):
            Path(directory).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> Dict[str, Any]:
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(self).items()}

    def public_dict(self) -> Dict[str, Any]:
        """Настройки без секретов — их можно отдавать в интерфейс."""
        hidden = {"llm_api_key", "embed_api_key", "rerank_api_key"}
        return {k: v for k, v in self.to_dict().items() if k not in hidden}


#: Имена, под которыми фон окна входа подхватывается сам — без правки
#: настроек. Порядок задаёт приоритет.
LOGIN_IMAGE_NAMES = ("login-bg.jpg", "login-bg.jpeg", "login-bg.png", "login-bg.webp")


def _find_login_image(folder: Path) -> Path | None:
    """Фон окна входа рядом с файлом настроек.

    Инженер приносит снимок с онлайн-машины и кладёт файл в тот же каталог,
    где лежит settings.json. Лезть в настройки при этом не нужно: путь всё
    равно был бы у всех разный, а имя одно.
    """
    for name in LOGIN_IMAGE_NAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


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
