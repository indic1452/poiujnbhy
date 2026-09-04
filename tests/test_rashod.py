# -*- coding: utf-8 -*-
"""Расход: номера видно, дни листаются, промежуток свой.

Три жалобы подряд от начальника отдела, и все три — про одно окно.

1. «Указываешь свои номера — пропадают номера». Строка «как дозвониться»
   рисовалась в один ряд с обрезкой по 22 знакам. У человека, вписавшего все
   поля, она вдвое длиннее: замер в браузере — 328 пикселей текста в 168
   доступных. Заполнил объективку и перестал видеть свои же номера.
2. «Дни неудобно листаются». Стрелки двигали окно целиком, на неделю: чтобы
   посмотреть завтрашний день у края окна, приходилось прыгать через всю
   неделю и искать его глазами.
3. «Нужно выбирать свои периоды». Список давал 7 или 14 суток, а расход
   просят на учения, на командировку, на отпуск группы — по числам.

Сервер окно до 31 суток отдавал и раньше (MAX_ROSTER_WINDOW), упиралось всё в
интерфейс. Здесь заперта серверная половина и разметка; поведение самих кнопок
проверено настоящим браузером.
"""

import re
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src" / "reportgen" / "web" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(encoding="utf-8")


class НомераВидно(unittest.TestCase):
    """Строку «как дозвониться» больше не режет по ширине."""

    def правило(self):
        found = re.search(r"\.roster-reach\s*\{(.*?)\}", CSS, re.S)
        self.assertIsNotNone(found, "правила .roster-reach в стилях нет")
        return found.group(1)

    def test_строка_переносится_а_не_обрезается(self):
        тело = self.правило()
        self.assertNotIn("text-overflow: ellipsis", тело)
        self.assertNotIn("white-space: nowrap", тело)
        self.assertIn("white-space: normal", тело)

    def test_ширина_не_ограничена_знаками(self):
        """max-width в знаках и был тем, что прятало номера."""
        self.assertNotIn("max-width", self.правило())

    def test_столбец_с_фамилией_шире_прежнего(self):
        found = re.search(r"\.grid--roster \.roster-name\s*\{(.*?)\}", CSS, re.S)
        self.assertIsNotNone(found)
        ширина = re.search(r"min-width:\s*(\d+)px", found.group(1))
        self.assertIsNotNone(ширина)
        self.assertGreaterEqual(int(ширина.group(1)), 240)


class ЛистаниеПоДням(unittest.TestCase):
    def test_есть_кнопки_на_сутки_в_обе_стороны(self):
        self.assertIn("На сутки назад", APP)
        self.assertIn("На сутки вперёд", APP)

    def test_остались_кнопки_на_окно_целиком(self):
        self.assertIn("На окно назад", APP)
        self.assertIn("На окно вперёд", APP)

    def test_сутки_двигают_и_раскрытый_день(self):
        """Иначе сетка уезжает, а сводка внизу остаётся от вчера."""
        кусок = APP[APP.index("'На сутки вперёд'"):]
        кусок = кусок[:600]
        self.assertIn("rosterState.from = shiftIso(rosterState.from, 1)", кусок)
        self.assertIn("rosterState.day = shiftIso(rosterState.day, 1)", кусок)


class СвойПромежуток(unittest.TestCase):
    def test_кнопка_и_окно_есть(self):
        self.assertIn("Свой период", APP)
        self.assertIn("function pickRosterPeriod", APP)

    def test_предел_совпадает_с_серверным(self):
        """Просить больше, чем сервер отдаёт, — значит молча получить меньше."""
        from reportgen.web.api import MAX_ROSTER_WINDOW
        найдено = re.search(r"ROSTER_MAX_SPAN = (\d+)", APP)
        self.assertIsNotNone(найдено)
        self.assertEqual(MAX_ROSTER_WINDOW, int(найдено.group(1)))

    def test_о_слишком_длинном_промежутке_говорят(self):
        """Молча обрезать нельзя: человек решит, что кнопка не работает."""
        кусок = APP[APP.index("function pickRosterPeriod"):]
        кусок = кусок[:2500]
        self.assertIn("ROSTER_MAX_SPAN", кусок)
        self.assertIn("суток расход не показывает", кусок)

    def test_перевёрнутый_промежуток_отвергается(self):
        кусок = APP[APP.index("function pickRosterPeriod"):][:2500]
        self.assertIn("Конец промежутка раньше начала", кусок)

    def test_поле_выбора_окна_держат_в_согласии(self):
        """Иначе поле показывает «7 сут.» при четырёх показанных днях."""
        self.assertIn("function syncSpanPick", APP)
        кусок = APP[APP.index("rangeLabel.textContent = fmtDate"):][:400]
        self.assertIn("syncSpanPick()", кусок)


class ПодсказкаПроHttps(unittest.TestCase):
    """Сказать «нельзя» мало: человек уходит ни с чем.

    Раньше здесь называли команду включения https. Но на рабочих местах
    отдела ставить что бы то ни было запрещено, и советовать это — значит
    снова отправить человека в тупик. Поэтому подсказка говорит другое: что
    срочное найдёт его и так, и что от него самого ничего не требуется.
    """

    def кусок(self):
        начало = APP.index("Окно Windows поверх других: здесь недоступно")
        return APP[начало:начало + 1400]

    def test_не_велит_ничего_устанавливать(self):
        текст = self.кусок()
        self.assertIn("ничего не", текст)
        self.assertNotIn("setup-https.ps1", текст)

    def test_не_посылает_искать_разрешение_в_браузере(self):
        """Ровно то, на чём человек застрял: в Firefox разрешения там нет."""
        текст = self.кусок()
        self.assertIn("искать не нужно", текст)
        self.assertIn("Firefox", текст)

    def test_сказано_чем_срочное_достучится(self):
        текст = self.кусок()
        self.assertIn("ВЫЗОВ", текст)
        self.assertIn("повторяется", текст)

    def test_вызов_можно_посмотреть_своими_глазами(self):
        self.assertIn("'Проверить вызов'", APP)
        self.assertIn("Смотрите на заголовок вкладки", APP)

    def test_мёртвого_переключателя_нет(self):
        """Отключённая отметка и гонит человека в настройки браузера.

        Отметку показываем ТОЛЬКО там, где окно Windows возможно; где нет —
        на её месте объяснение и кнопка проверки, а не серый переключатель.
        """
        import re as _re

        self.assertNotIn("disabled: !deskAllowed()", APP)
        self.assertRegex(
            APP, r"deskAllowed\(\)\s*\n\s*\? h\('label', \{ class: 'check-line' \}, desk,",
            "отметка показывается независимо от того, работает ли она")
        self.assertRegex(
            APP, r":\s*h\('div', \{ class: 'card card-pad' \},\s*\n\s*"
                 r"h\('b', \{\}, 'Окно Windows поверх других: здесь недоступно'\)",
            "объяснения вместо мёртвой отметки нет")


class СерверОтдаётСвоёОкно(unittest.TestCase):
    """Серверная половина: окно произвольной длины в пределах месяца."""

    def setUp(self):
        from test_web import WebTestCase
        self.обвязка = WebTestCase("run")
        self.обвязка.setUp()
        self.addCleanup(self.обвязка.tearDown)

    def test_окно_в_четыре_дня(self):
        тело = self.обвязка.client.get(
            "/api/roster?date_from=2026-09-10&days=4").json()
        self.assertEqual(4, len(тело["days"]))
        self.assertEqual("2026-09-10", тело["date_from"])
        self.assertEqual("2026-09-13", тело["date_to"])

    def test_месяц_целиком(self):
        from reportgen.web.api import MAX_ROSTER_WINDOW
        тело = self.обвязка.client.get(
            f"/api/roster?date_from=2026-09-01&days={MAX_ROSTER_WINDOW}").json()
        self.assertEqual(MAX_ROSTER_WINDOW, len(тело["days"]))

    def test_номера_доезжают_до_расхода(self):
        """Расход отвечает «где человек» — телефон половина этого ответа."""
        человек = self.обвязка.repos.users.by_login("engineer")
        self.обвязка.repos.users.update(
            человек.id, phone_open="12-34", phone_secure="56-78",
            phone_mobile="8-916-100-20-30", room="214")
        тело = self.обвязка.client.get("/api/roster").json()
        мой = [строка for строка in тело["staff"] if строка["id"] == человек.id]
        self.assertTrue(мой, "военнослужащего нет в расходе")
        self.assertEqual("12-34", мой[0]["phone_open"])
        self.assertEqual("56-78", мой[0]["phone_secure"])
        self.assertEqual("8-916-100-20-30", мой[0]["phone_mobile"])
        self.assertEqual("214", мой[0]["room"])


if __name__ == "__main__":
    unittest.main()
