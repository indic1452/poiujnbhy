"""Категории тематики и ключевые слова для фильтра релевантности."""
from __future__ import annotations

CATEGORIES: list[str] = [
    "Фронт/боевые действия",
    "Дипломатия/переговоры",
    "Санкции",
    "Военная помощь/поставки",
    "Внутренняя политика РФ",
    "Западная коалиция/НАТО",
    "Прочее",
]

DEFAULT_CATEGORY = "Прочее"

# Ключевые слова темы (RU + EN). Материал считается релевантным,
# если найдено хотя бы одно совпадение. Регистр игнорируется.
TOPIC_KEYWORDS: list[str] = [
    # RU
    "украин", "россия", "росси", "войн", "фронт", "всу", "вооружённ", "вооруженн",
    "миноборон", "обстрел", "удар", "дрон", "беспилотник", "ракет", "пво",
    "наступлен", "оборон", "мобилизац", "санкц", "нато", "зеленск", "путин",
    "коалиц", "поставк", "вооружен", "боеприпас", "гаубиц", "танк", "энергетик",
    "донбасс", "херсон", "запорож", "харьков", "бахмут", "авдеевк", "курск",
    "переговор", "перемир", "фаб", "hIMARS", "химарс", "патриот", "leopard",
    # EN
    "ukraine", "russia", "russian", " war", "military", "frontline", "offensive",
    "sanction", "nato", "zelensky", "zelenskyy", "putin", "kremlin", "coalition",
    "missile", "drone", "artillery", "shelling", "airstrike", "troops", "kyiv",
    "donbas", "kherson", "zaporizh", "kharkiv", "bakhmut", "weapons", "aid package",
    "ceasefire", "mobiliz", "himars", "patriot", "leopard", "abrams", "f-16",
]

# Тип события (для карты и статистики)
EVENT_TYPES: list[str] = [
    "удар_ракетный",
    "удар_дрон",
    "удар_авиа",
    "обстрел",
    "работа_ПВО",
    "бои_наступление",
    "потеря_техники",
    "поставки_вооружений",
    "дипломатия",
    "санкции",
    "инфраструктура_ЧП",
    "прочее",
]

DEFAULT_EVENT_TYPE = "прочее"

# Ключевые слова → тип события (проверяются по порядку, первое совпадение).
_EVENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("работа_ПВО", ["пво", "сбит", "перехвач", "средства поражения отражены", "air defence", "shot down", "intercept"]),
    ("удар_дрон", ["дрон", "бпла", "беспилотник", "shahed", "герань", "geran", "uav", "drone"]),
    ("удар_ракетный", ["ракет", "крылат", "баллист", "искандер", "кинжал", "калибр", "missile", "ballistic", "cruise"]),
    ("удар_авиа", ["авиауд", "фаб", "каб", "бомбардир", "airstrike", "air strike", "guided bomb", "glide bomb"]),
    ("обстрел", ["обстрел", "артиллер", "миномёт", "минометн", "shelling", "artillery", "mortar"]),
    ("поставки_вооружений", ["поставк", "военная помощ", "военной помощ", "военную помощ", "пакет помощи", "aid package", "weapons package", "arms delivery", "military aid"]),
    ("санкции", ["санкц", "sanction", "эмбарго", "embargo"]),
    ("дипломатия", ["переговор", "перемир", "диплом", "встреч", "talks", "ceasefire", "negotiat", "summit"]),
    ("потеря_техники", ["уничтож", "подбит", "потер", "destroyed", "losses", "knocked out"]),
    ("бои_наступление", ["наступлен", "штурм", "прорыв", "продвин", "бои", "offensive", "assault", "advance", "counterattack"]),
    ("инфраструктура_ЧП", ["энергет", "подстанц", "нпз", "нефтеперераб", "электрост", "refinery", "substation", "power plant", "energy", "grid", "gres", "аэс"]),
]


def guess_event_type(text: str) -> str:
    low = (text or "").lower()
    for etype, keys in _EVENT_KEYWORDS:
        if any(k in low for k in keys):
            return etype
    return DEFAULT_EVENT_TYPE


# Цвета маркеров по типу события (карта + simplestyle в GeoJSON)
EVENT_COLORS: dict[str, str] = {
    "удар_ракетный": "#e0563c",
    "удар_дрон": "#e0863c",
    "удар_авиа": "#d64541",
    "обстрел": "#c0392b",
    "работа_ПВО": "#3fb0e0",
    "бои_наступление": "#9b59b6",
    "потеря_техники": "#8e6e53",
    "поставки_вооружений": "#27ae60",
    "дипломатия": "#2980b9",
    "санкции": "#f1c40f",
    "инфраструктура_ЧП": "#e67e22",
    "прочее": "#95a5a6",
}


def event_color(event_type: str | None) -> str:
    return EVENT_COLORS.get(event_type or "", EVENT_COLORS["прочее"])
