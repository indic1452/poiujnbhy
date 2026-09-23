"""Проверка самого набора тестов: ничего не должно молчать незаметно.

В файле `test_web.py` два класса носили имя `TalkFileTests`. Второй
объявлением затёр первый, и семь проверок работы с файлами переписки
перестали запускаться — без единой ошибки, без падения, без строчки в
выводе. Одна из них сторожила отказ исполняемым файлам, и отказ тихо
пропал из кода; вернулся он только когда класс переименовали.

Молчащий тест хуже отсутствующего: отсутствующий видно по счётчику, а
затёртый выглядит написанным. Поэтому здесь проверяется не приложение, а
сам набор — на затирание имён и на классы без единой проверки.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

ТЕСТЫ = Path(__file__).resolve().parent


def _модули():
    return sorted(ТЕСТЫ.glob("test_*.py"))


def _разобрать(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class ЗатираниеИмёнTests(unittest.TestCase):
    """Второе объявление с тем же именем молча отменяет первое."""

    def test_в_модуле_нет_двух_классов_с_одним_именем(self):
        for path in _модули():
            with self.subTest(модуль=path.name):
                видели: dict[str, int] = {}
                for node in _разобрать(path).body:
                    if not isinstance(node, ast.ClassDef):
                        continue
                    прежняя = видели.get(node.name)
                    self.assertIsNone(
                        прежняя,
                        f"класс {node.name} объявлен дважды: строки {прежняя} и "
                        f"{node.lineno}; первый набор проверок не запускается")
                    видели[node.name] = node.lineno

    def test_в_классе_нет_двух_методов_с_одним_именем(self):
        """То же затирание на этаж ниже: второй `def` отменяет первый."""
        for path in _модули():
            for node in _разобрать(path).body:
                if not isinstance(node, ast.ClassDef):
                    continue
                видели: dict[str, int] = {}
                for sub in node.body:
                    if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    with self.subTest(модуль=path.name, класс=node.name, метод=sub.name):
                        прежняя = видели.get(sub.name)
                        self.assertIsNone(
                            прежняя,
                            f"{node.name}.{sub.name} объявлен дважды: строки "
                            f"{прежняя} и {sub.lineno}")
                        видели[sub.name] = sub.lineno

    def test_в_модуле_нет_двух_функций_верхнего_уровня_с_одним_именем(self):
        """Помощник тестов, объявленный дважды, — либо опечатка, либо мусор."""
        for path in _модули():
            with self.subTest(модуль=path.name):
                видели: dict[str, int] = {}
                for node in _разобрать(path).body:
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    прежняя = видели.get(node.name)
                    self.assertIsNone(
                        прежняя,
                        f"функция {node.name} объявлена дважды: строки {прежняя} "
                        f"и {node.lineno}")
                    видели[node.name] = node.lineno


def _имя_основания(base: ast.expr) -> str:
    """Имя базового класса: и `Основа`, и `unittest.TestCase`."""
    return getattr(base, "attr", getattr(base, "id", "") or "")


class ПроверкиВнеНабораTests(unittest.TestCase):
    """Проверка, объявленная не там, где её ищет pytest, молчит.

    Попалось на деле: две проверки встали в модуль после строки
    «if __name__ == "__main__":». Там они — не методы класса, а локальные
    функции, и pytest их не собирает. Прогон при этом зелёный, счётчик
    проверок не изменился, а сами проверки не выполнялись ни разу.
    """

    def test_под_точкой_входа_нет_проверок(self):
        for path in _модули():
            for node in _разобрать(path).body:
                if not isinstance(node, ast.If):
                    continue
                условие = ast.unparse(node.test)
                if "__name__" not in условие:
                    continue
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and sub.name.startswith("test"):
                        with self.subTest(модуль=path.name, проверка=sub.name):
                            self.fail(f"{sub.name} объявлена под «{условие}» "
                                      f"(строка {sub.lineno}) и не запускается")


class ПустыеНаборыTests(unittest.TestCase):
    """Класс без проверок в отчёте выглядит как работающий раздел.

    Классы общей подготовки проверок не содержат намеренно. Узнаём их не
    по имени — имена в наборе русские и разные, — а по делу: если в том же
    модуле от класса кто-то наследуется, это основание, а не набор.
    """

    def test_каждый_класс_тестов_что_то_проверяет(self):
        for path in _модули():
            классы = [node for node in _разобрать(path).body
                      if isinstance(node, ast.ClassDef)]
            основания = {_имя_основания(base)
                         for node in классы for base in node.bases}
            for node in классы:
                if node.name in основания:
                    continue          # от него наследуются — это подготовка
                свои = [base for base in node.bases]
                if not any(_имя_основания(base).endswith(("TestCase", "Tests", "Test"))
                           for base in свои):
                    continue          # не набор тестов вовсе
                with self.subTest(модуль=path.name, класс=node.name):
                    проверки = [sub.name for sub in node.body
                                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and sub.name.startswith("test")]
                    унаследованные = any(
                        _имя_основания(base).endswith(("Tests", "Test"))
                        for base in свои)
                    self.assertTrue(
                        проверки or унаследованные,
                        f"{node.name} не содержит ни одной проверки")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
