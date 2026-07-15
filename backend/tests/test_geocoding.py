from app.categories import guess_event_type
from app.geocoding import get_geocoder


def _geo():
    get_geocoder.cache_clear()
    return get_geocoder()


def test_geocode_object():
    g = _geo()
    r = g.geocode("Афипский НПЗ", object_hint="Афипский НПЗ")
    assert r is not None
    assert r.source == "object"
    assert round(r.lat, 2) == 44.90
    assert r.admin1 == "Краснодарский край"
    assert r.confidence >= 0.85


def test_geocode_city_and_translit():
    g = _geo()
    ru = g.geocode("Харьков")
    en = g.geocode("Kharkiv")
    assert ru and en
    assert ru.matched_name == en.matched_name == "Харьков"
    assert ru.admin1 == "Харьковская область"


def test_geocode_unknown_returns_none():
    assert _geo().geocode("Такогогородананет") is None


def test_find_mentions_handles_inflection():
    g = _geo()
    got = g.find_mentions("Удар по Афипскому НПЗ и обстрел Харьковской области")
    assert "Афипский НПЗ" in got
    assert "Харьков" in got


def test_find_mentions_no_false_friend():
    # "Краснодарском" не должно матчить "Красноармейск" (алиас Покровска)
    got = _geo().find_mentions("Пожар в Краснодарском крае")
    assert "Покровск" not in got


def test_event_type_classifier():
    assert guess_event_type("Атака дронов на НПЗ") == "удар_дрон"
    assert guess_event_type("Обстрел жилого района из артиллерии") == "обстрел"
    assert guess_event_type("Новый пакет военной помощи Киеву") == "поставки_вооружений"
    assert guess_event_type("Переговоры о перемирии") == "дипломатия"
    assert guess_event_type("Просто новость ни о чём") == "прочее"


def test_reverse_geocode_nearest():
    g = _geo()
    reg = g.reverse(44.90, 38.84)  # рядом с Афипским
    assert reg is not None
    assert reg.country == "RU"
