"""Исполняемое файлом не пересылают — ни в письме, ни в вопросе, ни в беседе.

Машины в отделе под Windows, интернета нет, а файл, пришедший «от своего»,
открывают не глядя. Единственная преграда — отказ на приёме, и он должен
стоять на всех трёх местах, куда человек кладёт файл руками.

Список запретительный, а не разрешительный: инженеры шлют схемы .vsd,
чертежи .dwg, выгрузки приборов десятка форматов, и перечислить всё годное
заранее нельзя. Поэтому здесь же проверяется и обратное — что обычная
рабочая мелочь проходит.
"""

from __future__ import annotations

import unittest

from reportgen.web.api import ОПАСНЫЕ_РАСШИРЕНИЯ

from test_web import WebTestCase


ОТКАЗ = "не пересылают"


class ИсполняемыеФайлыTests(WebTestCase):
    """Отказ на всех трёх приёмных точках и на всём списке расширений."""

    def setUp(self):
        super().setUp()
        self.login("engineer")
        self.case = self.client.post("/api/cases", json={
            "case_id": "ВХ-2026-0731", "title": "Помеха на линии"}).json()["case"]
        self.chat = self.client.post("/api/chats", json={}).json()["chat"]
        boss = self.repos.users.by_login("nachalnik")
        self.talk = self.client.post(
            "/api/talks", json={"members": [boss.id]}).json()["talk_id"]

    # ------------------------------------------------------------------ приём

    def приложить_к_письму(self, name, body=b"MZ\x90\x00"):
        return self.client.post(
            f"/api/cases/{self.case['id']}/files",
            files={"file": (name, body, "application/octet-stream")})

    def приложить_к_вопросу(self, name, body=b"MZ\x90\x00"):
        return self.client.post(
            f"/api/chats/{self.chat['id']}/attachments",
            files={"file": (name, body, "application/octet-stream")})

    def приложить_к_беседе(self, name, body=b"MZ\x90\x00"):
        return self.client.post(
            f"/api/talks/{self.talk}/files",
            data={"text": ""},
            files={"file": (name, body, "application/octet-stream")})

    @property
    def приёмные(self):
        return {
            "письмо": self.приложить_к_письму,
            "вопрос": self.приложить_к_вопросу,
            "беседа": self.приложить_к_беседе,
        }

    # ------------------------------------------------------------------ сами

    def test_отказ_стоит_на_всех_трёх_местах(self):
        """Достаточно одной незакрытой двери, чтобы проверка не значила ничего."""
        for место, приложить in self.приёмные.items():
            with self.subTest(место=место):
                отказ = приложить("obnovlenie.exe")
                self.assertEqual(400, отказ.status_code, отказ.text)
                self.assertIn(ОТКАЗ, отказ.json()["error"])

    def test_отказ_не_зависит_от_регистра_расширения(self):
        """В Windows «.EXE» запускается ровно так же, как «.exe»."""
        for место, приложить in self.приёмные.items():
            for имя in ("OBNOVLENIE.EXE", "skript.Bat", "biblioteka.DlL"):
                with self.subTest(место=место, имя=имя):
                    отказ = приложить(имя)
                    self.assertEqual(400, отказ.status_code, отказ.text)
                    self.assertIn(ОТКАЗ, отказ.json()["error"])

    def test_отказано_каждому_расширению_из_списка(self):
        """Список — не украшение: каждая строка в нём должна срабатывать."""
        self.assertIn(".exe", ОПАСНЫЕ_РАСШИРЕНИЯ)
        self.assertGreater(len(ОПАСНЫЕ_РАСШИРЕНИЯ), 20, "список подозрительно короток")
        for расширение in sorted(ОПАСНЫЕ_РАСШИРЕНИЯ):
            with self.subTest(расширение=расширение):
                отказ = self.приложить_к_беседе(f"vlozhenie{расширение}")
                self.assertEqual(400, отказ.status_code, отказ.text)
                self.assertIn(ОТКАЗ, отказ.json()["error"])

    def test_двойное_расширение_не_обманывает(self):
        """«отчёт.pdf.exe» — самый ходовой приём: смотрят на начало имени."""
        for место, приложить in self.приёмные.items():
            with self.subTest(место=место):
                отказ = приложить("otchyot.pdf.exe")
                self.assertEqual(400, отказ.status_code, отказ.text)

    def test_рабочая_мелочь_проходит(self):
        """Список запретительный: всё, что не названо, инженеру не мешают слать."""
        годное = (
            ("skhema.vsd", b"visio"),
            ("chertyozh.dwg", b"autocad"),
            ("vygruzka.csv", "уровень;дБм\n1;-42\n".encode("utf-8")),
            ("zamer.txt", "КАМ-16, EVM 3%".encode("utf-8")),
            ("arkhiv.zip", b"PK\x03\x04..."),
        )
        for место, приложить in self.приёмные.items():
            for имя, тело in годное:
                with self.subTest(место=место, имя=имя):
                    ответ = приложить(имя, тело)
                    self.assertEqual(200, ответ.status_code, ответ.text)

    def test_отказ_не_оставляет_следа(self):
        """Отказ до записи на диск: иначе запрет был бы только на словах."""
        self.приложить_к_беседе("obnovlenie.exe")
        сообщения = self.client.get(f"/api/talks/{self.talk}").json()["messages"]
        self.assertEqual([], сообщения)

        self.приложить_к_письму("obnovlenie.exe")
        файлы = self.client.get(
            f"/api/cases/{self.case['id']}/files").json()["files"]
        self.assertEqual([], файлы)

    def test_отказ_объясняет_что_прислать_вместо(self):
        """Человек несёт файл не из вредности: тупик без выхода — плохой отказ."""
        отказ = self.приложить_к_беседе("obnovlenie.exe").json()["error"]
        self.assertIn("документ", отказ)
        self.assertIn("снимок экрана", отказ)
        self.assertIn("выгрузку прибора", отказ)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
