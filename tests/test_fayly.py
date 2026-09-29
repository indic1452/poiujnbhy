"""Запись файла подменой временного: отказы Windows («Отказано в доступе») переживаются."""

import os
import tempfile
import threading
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen import fayly
from reportgen.fayly import записать_атомарно


class ЗаписьTests(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="rg-fayly-"))
        self.путь = self.папка / "состояние.json"

    def лишние(self):
        return sorted(п.name for п in self.папка.iterdir() if п.name != "состояние.json")

    def test_текст_и_байты(self):
        записать_атомарно(self.путь, "Привет")
        self.assertEqual("Привет", self.путь.read_text(encoding="utf-8"))
        записать_атомарно(self.путь, b"\x00\xff")
        self.assertEqual(b"\x00\xff", self.путь.read_bytes())
        записать_атомарно(str(self.путь), "ъ", кодировка="cp1251")
        self.assertEqual(b"\xfa", self.путь.read_bytes())
        self.assertEqual([], self.лишние())

    def test_занятый_файл_подменяется_после_ожидания(self):
        # Первые три подмены отказывают, как на Windows, пока файл открыт другим потоком.
        отказов, паузы = [3], []

        def заменить(откуда, куда):
            if отказов[0]:
                отказов[0] -= 1
                raise PermissionError(5, "Отказано в доступе")
            os.replace(откуда, куда)

        self.путь.write_text("старое", encoding="utf-8")
        записать_атомарно(self.путь, "новое", заменить=заменить, ждать=паузы.append)
        self.assertEqual("новое", self.путь.read_text(encoding="utf-8"))
        self.assertEqual(list(fayly.ПАУЗЫ[:3]), паузы)
        self.assertEqual([], self.лишние())

    def test_отказ_дольше_всех_пауз(self):
        # Файл занят дольше всех пауз — ошибка наружу, прежний файл цел, временного не осталось.
        попыток, паузы = [0], []

        def заменить(откуда, куда):
            попыток[0] += 1
            raise PermissionError(5, "Отказано в доступе")

        self.путь.write_text("старое", encoding="utf-8")
        with self.assertRaises(PermissionError):
            записать_атомарно(self.путь, "новое", заменить=заменить, ждать=паузы.append)
        self.assertEqual((len(fayly.ПАУЗЫ) + 1, list(fayly.ПАУЗЫ)), (попыток[0], паузы))
        self.assertEqual("старое", self.путь.read_text(encoding="utf-8"))
        self.assertEqual([], self.лишние())
        # Паузы растут и в сумме — несколько секунд: короткие занятости переживаются.
        self.assertEqual(sorted(fayly.ПАУЗЫ), list(fayly.ПАУЗЫ))
        self.assertTrue(3 <= sum(fayly.ПАУЗЫ) <= 10)

    def test_другие_ошибки_не_повторяются(self):
        попыток = [0]

        def заменить(откуда, куда):
            попыток[0] += 1
            raise FileNotFoundError(2, "нет")

        with self.assertRaises(FileNotFoundError):
            записать_атомарно(self.путь, "x", заменить=заменить, ждать=lambda _: self.fail("не ждать"))
        self.assertEqual(1, попыток[0])
        self.assertEqual([], self.лишние())

    def test_одновременные_записи_не_мешают_друг_другу(self):
        # Раньше все писали в один «состояние.tmp»: одна запись подменяла чужой временный.
        ошибки = []

        def писать(н):
            try:
                for i in range(40):
                    записать_атомарно(self.путь, f"{н}:{i}" * 50)
            except Exception as ошибка:  # noqa: BLE001
                ошибки.append(ошибка)

        потоки = [threading.Thread(target=писать, args=(н,)) for н in range(6)]
        for п in потоки:
            п.start()
        for п in потоки:
            п.join()
        self.assertEqual([], ошибки)
        текст = self.путь.read_text(encoding="utf-8")
        кусок = текст[:len(текст) // 50]
        self.assertEqual(кусок * 50, текст)
        self.assertEqual([], self.лишние())

    def test_временные_имена_разные(self):
        имена = {fayly._временный(self.путь).name for _ in range(100)}
        self.assertEqual(100, len(имена))
        self.assertTrue(all(и.startswith("состояние.json.") and и.endswith(".tmp") for и in имена))


if __name__ == "__main__":
    unittest.main()
