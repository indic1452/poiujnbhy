from app.pipeline import relevance


def test_relevant_topic():
    ok, s = relevance.is_relevant(
        "Массированный удар по объектам энергетики Украины", "повреждены подстанции"
    )
    assert ok and s > 0


def test_relevant_english():
    ok, s = relevance.is_relevant("NATO pledges new weapons for Kyiv", "air defence and artillery")
    assert ok and s > 0


def test_irrelevant_offtopic():
    ok, s = relevance.is_relevant("Прогноз погоды на выходные", "тёплая погода без осадков")
    assert not ok
    assert s == 0
