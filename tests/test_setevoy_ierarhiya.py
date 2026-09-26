# -*- coding: utf-8 -*-
"""Иерархия протоколов: уровни узлов, «кончаются на узле», страница с деревом и схемой."""

import re
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.setevoy.statistika import иерархия, уровень_протокола

СТАТИКА = Path(__file__).resolve().parents[1] / "src" / "reportgen" / "web" / "static"


def сводка(стек, длина=100):
    return {"стек": стек, "длина": длина}


class Уровни(unittest.TestCase):
    def test_уровни_по_таблице_и_по_месту(self):
        self.assertEqual(уровень_протокола("Ethernet"), "канальный")
        self.assertEqual(уровень_протокола("IPv6"), "сетевой")
        self.assertEqual(уровень_протокола("UDP"), "транспортный")
        self.assertEqual(уровень_протокола("DNS", ("Ethernet", "IPv4", "UDP")), "прикладной")
        self.assertEqual(уровень_протокола("Данные", ("TCP",)), "данные")
        self.assertEqual(уровень_протокола("собранный TCP"), "транспортный")
        self.assertEqual(уровень_протокола("Нечто", ("Ethernet",)), "прочий")
        self.assertEqual(уровень_протокола("Нечто", ("исходный UDP",)), "прикладной")

    def test_дерево_с_уровнями_и_остатком(self):
        дерево = иерархия([
            сводка(["Ethernet", "IPv4", "UDP", "DNS"], 90),
            сводка(["Ethernet", "IPv4", "UDP", "DNS"], 110),
            сводка(["Ethernet", "IPv4", "UDP"], 60),
            сводка(["Ethernet", "ARP"], 42),
        ])[0]
        self.assertEqual((дерево["протокол"], дерево["уровень"], дерево["пакетов"], дерево["байт"]),
                         ("Кадры", "все", 4, 302))
        eth = дерево["дети"][0]
        ip, arp = eth["дети"]
        self.assertEqual((ip["протокол"], ip["уровень"], ip["пакетов"]), ("IPv4", "сетевой", 3))
        self.assertEqual(arp["уровень"], "канальный")
        udp = ip["дети"][0]
        self.assertEqual((udp["уровень"], udp["кончаются"]), ("транспортный", 1))
        dns = udp["дети"][0]
        self.assertEqual((dns["уровень"], dns["байт"], dns["кончаются"]), ("прикладной", 200, 2))
        self.assertEqual(eth["кончаются"], 0)


class Страница(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (СТАТИКА / "app.js").read_text(encoding="utf-8")
        cls.css = (СТАТИКА / "styles.css").read_text(encoding="utf-8")
        cls.вид = cls.js.split("function иерархия(data) {")[1].split("async function диалоги(")[0]

    def test_схема_дерево_карточка(self):
        for что in ("pk-ice", "role: 'tree'", "role: 'treeitem'", "'aria-expanded'",
                    "Показать пакеты", "Исключить", "'not ' + свой", "Приблизить на схеме",
                    "ArrowDown", "ArrowLeft", "Enter", "dblclick"):
            self.assertIn(что, self.вид, что)

    def test_цвет_уровня_из_токенов_обеих_тем(self):
        блок = self.css.split("/* -- пакеты: иерархия протоколов")[1]
        светлые = re.search(r"\.pk-hier \{(.*?)\n\}", блок, re.S).group(1)
        self.assertIn("--lvl-link: #2a78d6", светлые)
        self.assertIn(':root[data-theme="dark"] .pk-hier', блок)
        self.assertIn(':root:not([data-theme="light"]) .pk-hier', блок)
        # Подписи — цветом текста, не цветом ряда.
        self.assertIn(".pk-ice-label { fill: var(--text)", блок)

    def test_класс_не_пересекается_с_деревом_полей(self):
        """.pk-tree — дерево полей пакета (высота 46vh): иерархия — свой класс."""
        self.assertNotIn("class: 'pk-tree'", self.вид)
        self.assertIn("class: 'pk-hier'", self.вид)


if __name__ == "__main__":
    unittest.main()
