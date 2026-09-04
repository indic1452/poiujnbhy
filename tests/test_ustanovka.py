# -*- coding: utf-8 -*-
"""Установка на машину отдела: чтобы скрипты не расходились с приложением.

Начальник отдела запустил setup-https.ps1 и получил подряд две беды: скрипт
искал файл настроек не там, где тот лежит, а потом посоветовал «pip install
cryptography» на машине без интернета. Обе — из одной семьи: скрипт помнил
что-то сам, вместо того чтобы спросить, и советовал то, что выполнить негде.

Здесь заперта вся эта семья:

* пути — только у приложения (reportgen paths), а не в памяти скрипта;
* в резервную копию попадает ВСЁ, что нельзя восстановить: разойтись этому
  списку с приложением больше нельзя;
* подсказка по установке пакета называет тот интерпретатор, которым работает
  система, и ставит без сети;
* совместимость с Windows PowerShell 5.1 — на машине отдела именно она, а не
  pwsh 7, под которым гоняются проверки скриптов;
* документация не обещает того, чего нет.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.config import Settings
from reportgen.packages import pip_hint

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "scripts" / "windows"
OFFLINE = ROOT / "scripts" / "offline"


def читать(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def все_скрипты():
    return sorted(list(WINDOWS.glob("*.ps1")) + list(OFFLINE.glob("*.ps1")))


def без_строк_и_примечаний(текст: str) -> str:
    """Убрать примечания и одинарные кавычки: в них ищут ложные совпадения."""
    без = re.sub(r"<#.*?#>", " ", текст, flags=re.S)
    без = re.sub(r"(?m)#.*$", " ", без)
    без = re.sub(r"'[^'\n]*'", "''", без)
    return без


class ПутиТолькоУПриложения(unittest.TestCase):
    """Скрипт, помнящий пути сам, однажды разойдётся с приложением молча."""

    def test_настройки_берутся_из_общего_места(self):
        текст = читать(WINDOWS / "setup-https.ps1")
        self.assertIn("$script:Config", текст)
        # Файл рядом со скриптом упоминается только в предупреждении «система
        # его не читает» — брать настройки оттуда нельзя.
        for строка in текст.splitlines():
            if "Join-Path $PSScriptRoot 'settings.json'" not in строка:
                continue
            self.assertIn("$мимо", строка,
                          "скрипт снова ищет settings.json рядом с собой")

    def test_скрипт_подсказывает_если_настройки_лежат_не_там(self):
        """Ровно та ловушка, в которую попал начальник отдела."""
        текст = читать(WINDOWS / "setup-https.ps1")
        self.assertIn("система не читает", текст)

    def test_копия_и_библиотека_спрашивают_у_приложения(self):
        for имя in ("backup.ps1", "load-library.ps1"):
            with self.subTest(script=имя):
                текст = читать(WINDOWS / имя)
                self.assertTrue(
                    "reportgen --config $script:Config paths --json" in текст
                    or "Get-DataPlace" in текст,
                    f"{имя} снова помнит пути сам")

    def test_приложение_умеет_рассказать_где_данные(self):
        готово = subprocess.run(
            [sys.executable, "-m", "reportgen", "paths", "--json"],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")))
        self.assertEqual(0, готово.returncode, готово.stderr)
        отчёт = json.loads(готово.stdout.strip().splitlines()[-1])
        имена = {место["имя"] for место in отчёт["places"]}
        self.assertIn("reportgen.db", имена)
        self.assertIn("case-files", имена)


class ВКопиюПопадаетВсё(unittest.TestCase):
    """Вложения писем и файлы переписки в копию не попадали вовсе."""

    def test_каждое_хранилище_из_кода_есть_в_описи(self):
        """Появится новый каталог под data_dir — проверка о нём напомнит.

        Именно так и потерялись case-files, talk-files и person-files: их
        завели позже, а список в backup.ps1 остался прежним.
        """
        места = {место["имя"] for место in Settings.load().storage()}
        найдено = set()
        for файл in (ROOT / "src" / "reportgen").rglob("*.py"):
            текст = файл.read_text(encoding="utf-8")
            найдено |= set(re.findall(r'data_dir\)? / "([a-zA-Zа-яё\-]+)"', текст))
        пропущено = найдено - места
        self.assertEqual(set(), пропущено,
                         f"каталоги {sorted(пропущено)} не попадут в резервную копию")

    def test_ничего_невосстановимого_не_забыто(self):
        нужные = {"reportgen.db", "library", "uploads", "exports", "reports",
                  "case-files", "talk-files", "person-files", "tls"}
        в_копию = {место["имя"] for место in Settings.load().storage()
                   if место["в_копию"]}
        self.assertTrue(нужные <= в_копию, f"забыто: {sorted(нужные - в_копию)}")

    def test_кеш_в_копию_не_идёт(self):
        """Он восстановится сам, а весит столько же, сколько библиотека."""
        места = {место["имя"]: место["в_копию"] for место in Settings.load().storage()}
        self.assertFalse(места["kesh"])

    def test_копия_не_режется_архиватором_на_два_гигабайта(self):
        """Compress-Archive в PowerShell 5.1 библиотеку отдела не осилит."""
        текст = без_строк_и_примечаний(читать(WINDOWS / "backup.ps1"))
        self.assertNotIn("Compress-Archive", текст)
        self.assertIn("robocopy", текст)

    def test_копия_проверяется_а_не_объявляется(self):
        """Три вещи, которые проверить запуском нельзя: robocopy и WMI живут
        только в Windows. Поэтому сверяем сам текст — но не наличие слова, а
        именно решение, которое скрипт принимает.
        """
        # Здесь читаем текст как есть: разбор со снятыми кавычками съел бы
        # само сравниваемое значение.
        текст = читать(WINDOWS / "backup.ps1")
        self.assertIn("integrity_check", текст)
        self.assertRegex(
            текст, r"\$итог[^\n]*-ne\s+'ok'",
            "итог integrity_check никуда не сравнивается — проверка показная")
        self.assertRegex(текст, r"\$сбои\s+-gt\s+0",
                         "число сбоев ни на что не влияет")
        self.assertIn("exit 1", текст)

    def test_коды_возврата_robocopy_читаются_правильно(self):
        """У robocopy успех — это 0..7. «1» значит «скопировано».

        Сравнение с нулём объявляло бы удачную копию сбойной каждый раз,
        когда в ней что-то изменилось, — то есть всегда.
        """
        текст = читать(WINDOWS / "backup.ps1")
        self.assertRegex(текст, r"\$код\s+-ge\s+8",
                         "коды robocopy читаются как у обычной программы")
        self.assertNotRegex(текст, r"\$код\s+-ne\s+0")


PWSH = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(PWSH, "нет pwsh — скрипты не прогнать")
class КопияДелаетсяНаСамомДеле(unittest.TestCase):
    """Не «в скрипте написано», а «запустили и посмотрели, что вышло»."""

    #: Всё, что теряется безвозвратно, — по файлу на каждый вид.
    ФАЙЛЫ = {
        "library/ГОСТ Р 53532.pdf": "нормы",
        "uploads/chat-3-спектр.png": "картинка",
        "exports/отчёт-47-312.docx": "отчёт",
        "reports/7/сдан файлом.docx": "сдан файлом",
        "case-files/7/письмо 47-312.pdf": "вложение письма",
        "talk-files/3/схема ствола.vsd": "файл переписки",
        "person-files/2/объективка.docx": "личное дело",
        "tls/корень.crt": "сертификат",
        "kesh/мусор.tmp": "кеш",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.дом = Path(self._tmp.name)
        данные = self.дом / "data"
        for имя, текст in self.ФАЙЛЫ.items():
            путь = данные / имя
            путь.parent.mkdir(parents=True, exist_ok=True)
            путь.write_text(текст, encoding="utf-8")
        база = sqlite3.connect(данные / "reportgen.db")
        база.execute("create table письма(id integer)")
        база.execute("insert into письма values (47)")
        база.commit()
        база.close()
        образец = json.loads(
            (WINDOWS / "settings.example.json").read_text(encoding="utf-8-sig"))
        образец["data_dir"] = str(данные)
        (self.дом / "settings.json").write_text(
            json.dumps(образец, ensure_ascii=False, indent=2), encoding="utf-8")

    def копировать(self):
        готово = subprocess.run(
            [PWSH, "-NoProfile", "-File", str(WINDOWS / "backup.ps1")],
            capture_output=True, text=True, timeout=600,
            env=dict(os.environ, REPORTGEN_HOME=str(self.дом),
                     PYTHONPATH=str(ROOT / "src")))
        return готово

    def test_в_копию_попали_все_файлы_отдела(self):
        готово = self.копировать()
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        слой = self.дом / "backups" / "файлы"
        for имя in self.ФАЙЛЫ:
            есть = (слой / имя).is_file()
            if имя.startswith("kesh/"):
                self.assertFalse(есть, "кеш в копии не нужен")
            else:
                self.assertTrue(есть, f"в копии нет {имя}")

    def test_база_скопирована_и_проверена(self):
        готово = self.копировать()
        дата = [p for p in (self.дом / "backups").iterdir()
                if p.is_dir() and p.name != "файлы"]
        self.assertEqual(1, len(дата))
        self.assertTrue((дата[0] / "reportgen.db").is_file())
        опись = (дата[0] / "опись.txt").read_text(encoding="utf-8-sig")
        self.assertIn("integrity_check ok", опись)
        self.assertIn("case-files", опись)
        self.assertIn("Восстановление", опись)
        del готово

    def test_стёртый_по_ошибке_файл_в_копии_остаётся(self):
        """Копия, повторяющая удаление, от беды не спасает."""
        self.копировать()
        (self.дом / "data" / "case-files" / "7" / "письмо 47-312.pdf").unlink()
        self.копировать()
        self.assertTrue(
            (self.дом / "backups" / "файлы" / "case-files" / "7"
             / "письмо 47-312.pdf").is_file())

    def test_без_базы_копия_объявляется_неполной(self):
        (self.дом / "data" / "reportgen.db").unlink()
        готово = self.копировать()
        self.assertEqual(1, готово.returncode,
                         "копия без базы объявлена удачной")
        self.assertIn("НЕ ПОЛНОСТЬЮ", готово.stdout)


@unittest.skipUnless(PWSH, "нет pwsh — скрипты не прогнать")
class СертификатВыписываетсяСкриптом(unittest.TestCase):
    """setup-https.ps1 от начала до конца, как на машине отдела."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.дом = Path(self._tmp.name)
        (self.дом / "data").mkdir()
        образец = json.loads(
            (WINDOWS / "settings.example.json").read_text(encoding="utf-8-sig"))
        образец["data_dir"] = str(self.дом / "data")
        self.настройки = self.дом / "settings.json"
        self.настройки.write_text(json.dumps(образец, ensure_ascii=False, indent=2),
                                  encoding="utf-8")

    def запустить(self, *ключи):
        return subprocess.run(
            [PWSH, "-NoProfile", "-File", str(WINDOWS / "setup-https.ps1"), *ключи],
            capture_output=True, text=True, timeout=600,
            env=dict(os.environ, REPORTGEN_HOME=str(self.дом),
                     PYTHONPATH=str(ROOT / "src")))

    def test_выписывает_корень_и_серверный_и_включает_https(self):
        готово = self.запустить("-Hosts", "192.168.10.5,otdel-server")
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        for имя in ("корень.crt", "сервер.crt", "сервер.key"):
            self.assertTrue((self.дом / "data" / "tls" / имя).is_file(), имя)
        настройки = json.loads(self.настройки.read_text(encoding="utf-8-sig"))
        self.assertTrue(настройки["https"])
        self.assertIn("192.168.10.5", готово.stdout)
        self.assertIn("otdel-server", готово.stdout)

    def test_на_рабочие_места_дают_готовый_пускач(self):
        """Обходить кабинеты с командной строкой никто не станет.

        Поэтому рабочему месту достаётся не команда, а файл: двойной щелчок,
        одно подтверждение, ни прав администратора, ни PowerShell.
        """
        with tempfile.TemporaryDirectory() as обмен:
            готово = self.запустить("-Export", обмен)
            self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
            папка = Path(обмен)
            self.assertTrue((папка / "корень.crt").is_file())
            пускач = папка / "Доверять серверу.cmd"
            self.assertTrue(пускач.is_file(), "готового файла для рабочего места нет")
            текст = пускач.read_bytes().decode("cp866")
            self.assertIn("certutil -addstore -user Root", текст,
                          "сертификат ставится не в хранилище человека — "
                          "значит, понадобится администратор")
            self.assertIn("корень.crt", текст)
            self.assertNotIn("powershell", текст.lower())
        self.assertIn("Доверять серверу.cmd", готово.stdout)

    def test_повторный_запуск_не_теряет_вписанные_адреса(self):
        self.запустить("-Hosts", "192.168.10.5")
        готово = self.запустить()
        self.assertIn("192.168.10.5", готово.stdout,
                      "второй запуск выбросил адрес из сертификата")

    def test_без_файла_настроек_говорит_что_делать(self):
        self.настройки.unlink()
        готово = self.запустить()
        self.assertEqual(1, готово.returncode)
        self.assertIn("01-install.ps1", готово.stdout)

    def test_ключ_off_возвращает_на_http(self):
        self.запустить()
        готово = self.запустить("-Off")
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        настройки = json.loads(self.настройки.read_text(encoding="utf-8-sig"))
        self.assertFalse(настройки["https"])


class СборкаКомплекта(unittest.TestCase):
    """Сборка на машине с интернетом. Проверено прогоном обоих сборщиков."""

    def test_комплект_проверяется_на_полноту(self):
        """«Комплект готов» при неполной сборке — худшая из возможных неправд.

        Одна сорвавшаяся закачка колёс давала только предупреждение, оно
        уезжало вверх за экран, и выяснялось это на изолированной машине, где
        доложить неоткуда.
        """
        for скрипт in (OFFLINE / "pack.ps1", OFFLINE / "pack.sh"):
            with self.subTest(script=скрипт.name):
                текст = читать(скрипт)
                self.assertIn("Полнота комплекта", текст)
                self.assertIn("--dry-run", текст,
                              "полнота считается файлами, а не проверкой установки")
                self.assertIn("НЕПОЛНЫЙ", текст)
                # Именно решение, а не строчка текста: после списка пробелов
                # скрипт обязан завершиться ошибкой.
                хвост = текст[текст.index("НЕПОЛНЫЙ"):]
                self.assertRegex(хвост[:900], r"exit 1",
                                 "неполный комплект объявлен, но сборка кончается успехом")

    def test_оба_сборщика_кладут_пакеты_для_проверок(self):
        """Набор проверок — единственный способ убедиться в установке на месте."""
        self.assertRegex(
            читать(OFFLINE / "pack.ps1"),
            r"pip download[^\n]*--requirement \$dev",
            "pack.ps1 не качает колёса для проверок")
        self.assertRegex(
            читать(OFFLINE / "pack.sh"),
            r"pip download[^\n]*\n?[^\n]*requirements-dev\.txt",
            "pack.sh не качает колёса для проверок")

    def test_оба_установщика_ставят_пакеты_для_проверок(self):
        self.assertRegex(
            читать(OFFLINE / "install-offline.ps1"),
            r"pip', 'install'[^\n]*\n?[^\n]*'-r', \$dev",
            "install-offline.ps1 не ставит пакеты для проверок")
        текст = читать(OFFLINE / "install-offline.sh")
        начало = текст.index('if [ -f "$TARGET/app/requirements-dev.txt" ]')
        self.assertIn("pip install", текст[начало:начало + 400],
                      "install-offline.sh не ставит пакеты для проверок")

    def test_скрипты_установки_попадают_в_манифест(self):
        """Иначе подмену самого установщика проверка не заметит.

        А это единственный файл комплекта, который на машине отдела
        запускают с полными правами.
        """
        текст = читать(OFFLINE / "pack.sh")
        место_копирования = текст.index('cp "$ROOT/scripts/offline/install-offline.sh"')
        место_манифеста = текст.index("Манифест и контрольные суммы")
        self.assertLess(место_копирования, место_манифеста,
                        "установщик кладётся после манифеста и не проверяется")

    def test_манифест_объявляет_состав_комплекта(self):
        """Список одних лишь удавшихся файлов не отличает полный от половины."""
        текст = читать(OFFLINE / "pack.ps1")
        манифест = текст[текст.index("$manifest = [pscustomobject]@{"):]
        self.assertIn("expected", манифест[:600],
                      "состав посчитан, но в манифест не попал")
        свой = читать(OFFLINE / "pack.sh")
        манифест = свой[свой.index('manifest = {"created"'):]
        self.assertIn('"expected"', манифест[:900])

    def test_скачанное_проверяется_на_подделку(self):
        """Зеркало отвечает кодом 200 и отдаёт страницу «файл не найден»."""
        текст = читать(OFFLINE / "pack.ps1")
        self.assertIn("function Test-Payload", текст)
        self.assertIn("веб-страница", текст)
        self.assertIn("GGUF", текст)

    def test_обрыв_на_модели_не_рвёт_всю_сборку(self):
        """Иначе восемнадцать скачанных гигабайт остаются без манифеста."""
        текст = читать(OFFLINE / "pack.ps1")
        кусок = текст[текст.index("Модели GGUF"):]
        кусок = кусок[:кусок.index("внешние программы")]
        self.assertIn("try {", кусок)
        self.assertIn("Later", кусок)


@unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"),
                     "нет pwsh — подделку не проверить запуском")
class ПодделкуЛовятЗапуском(unittest.TestCase):
    """Не «в скрипте написано», а «подсунули страницу — поймал»."""

    @classmethod
    def setUpClass(cls):
        cls.pwsh = shutil.which("pwsh") or shutil.which("powershell")
        текст = читать(OFFLINE / "pack.ps1")
        начало = текст.index("function Test-Payload")
        конец = текст.index("function Test-Checksum")
        cls.функция = текст[начало:конец]

    def проверить(self, имя: str, содержимое: bytes) -> str:
        with tempfile.TemporaryDirectory() as кат:
            файл = Path(кат) / имя
            файл.write_bytes(содержимое)
            скрипт = Path(кат) / "проба.ps1"
            скрипт.write_text(
                self.функция + f"\ntry {{ Test-Payload '{файл}' '{имя}'; 'ГОДЕН' }}"
                f" catch {{ $_.Exception.Message }}\n", encoding="utf-8")
            готово = subprocess.run([self.pwsh, "-NoProfile", "-File", str(скрипт)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(0, готово.returncode, готово.stderr)
            return готово.stdout.strip()

    def test_страница_ошибки_вместо_установщика(self):
        ответ = self.проверить("setup.exe", b"<!DOCTYPE html><html>404" + b"x" * 2000)
        self.assertIn("веб-страница", ответ)

    def test_обрывок_вместо_файла(self):
        self.assertIn("страницу ошибки", self.проверить("setup.exe", b"MZ" + b"x" * 10))

    def test_не_программа_windows(self):
        ответ = self.проверить("setup.exe", b"\x7fELF" + b"x" * 4000)
        self.assertIn("не программа Windows", ответ)

    def test_не_модель_gguf(self):
        ответ = self.проверить("model.gguf", b"PK\x03\x04" + b"x" * 4000)
        self.assertIn("не модель GGUF", ответ)

    def test_настоящий_установщик_проходит(self):
        self.assertEqual("ГОДЕН", self.проверить("setup.exe", b"MZ" + b"\x00" * 4000))

    def test_настоящая_модель_проходит(self):
        self.assertEqual("ГОДЕН", self.проверить("m.gguf", b"GGUF" + b"\x00" * 4000))


class ПроверкаКомплектаНаМесте(unittest.TestCase):
    """verify.* отличает битый комплект от неполного, а не путает их."""

    def test_битый_и_неполный_различаются(self):
        for скрипт in (OFFLINE / "verify.ps1", OFFLINE / "verify.sh"):
            with self.subTest(script=скрипт.name):
                текст = читать(скрипт)
                self.assertIn("НЕПОЛНЫЙ", текст)
                self.assertRegex(текст, r"(exit 2|sys\.exit\(2\))",
                                 "неполный комплект неотличим от битого")

    def test_установщики_понимают_оба_кода(self):
        for скрипт in (OFFLINE / "install-offline.ps1", OFFLINE / "install-offline.sh"):
            with self.subTest(script=скрипт.name):
                текст = читать(скрипт)
                self.assertIn("-eq 1", текст)
                self.assertIn("-eq 2", текст)


class ОболочкаНеПониамаетКириллицы(unittest.TestCase):
    """Дважды за день bash упал на «ПРОБЕЛЫ=0»: command not found."""

    def test_имена_переменных_оболочки_латиницей(self):
        беды = []
        for скрипт in OFFLINE.glob("*.sh"):
            текст = читать(скрипт)
            внутри_python = False
            for номер, строка in enumerate(текст.splitlines(), 1):
                # Питоновские вставки живут в heredoc — там кириллица законна.
                if re.search(r"<<'?[A-Z]+'?$", строка):
                    внутри_python = True
                    continue
                if внутри_python:
                    if re.match(r"^[A-Z]+$", строка.strip()):
                        внутри_python = False
                    continue
                if re.match(r"^\s*[А-Яа-яЁё_]+=", строка):
                    беды.append(f"{скрипт.name}:{номер}: {строка.strip()}")
                if re.match(r"^\s*for\s+[А-Яа-яЁё]", строка):
                    беды.append(f"{скрипт.name}:{номер}: {строка.strip()}")
        self.assertEqual([], беды, "bash не принимает кириллицу в именах переменных")


class РазборПутиНеРоняетУстановку(unittest.TestCase):
    def test_split_path_qualifier_под_защитой(self):
        """На пути без буквы диска он БРОСАЕТ, а не возвращает пустоту.

        Так установка обрывалась на сетевом пути, а запасная ветка, написанная
        как раз на этот случай, не выполнялась никогда.
        """
        текст = читать(OFFLINE / "install-offline.ps1")
        место = текст.index("Split-Path $Target -Qualifier")
        вокруг = текст[место - 200:место + 100]
        self.assertIn("try {", вокруг)


class ПодсказкаКоторуюМожноВыполнить(unittest.TestCase):
    """«pip install X» на машине без интернета — тупик, а не совет."""

    def test_подсказка_называет_свой_интерпретатор_и_не_ходит_в_сеть(self):
        подсказка = pip_hint("python-docx")
        self.assertIn(sys.executable, подсказка)
        self.assertIn("--no-index", подсказка)

    def test_каталог_колёс_подставляется_если_он_есть(self):
        with tempfile.TemporaryDirectory() as имя:
            прежнее = os.environ.get("REPORTGEN_WHEELS")
            os.environ["REPORTGEN_WHEELS"] = имя
            try:
                self.assertIn(имя, pip_hint("pillow"))
            finally:
                if прежнее is None:
                    del os.environ["REPORTGEN_WHEELS"]
                else:
                    os.environ["REPORTGEN_WHEELS"] = прежнее

    def test_нигде_не_советуют_чужой_интерпретатор(self):
        виновные = []
        for файл in (ROOT / "src" / "reportgen").rglob("*.py"):
            текст = файл.read_text(encoding="utf-8")
            if "py -m pip install" in текст and файл.name != "packages.py":
                виновные.append(str(файл.relative_to(ROOT)))
        self.assertEqual([], виновные,
                         "py — системный запускатель: пакет уедет мимо окружения")

    def test_голого_pip_install_в_сообщениях_не_осталось(self):
        """Кроме самого сборщика подсказок и пояснений в примечаниях."""
        виновные = []
        for файл in (ROOT / "src" / "reportgen").rglob("*.py"):
            if файл.name in ("packages.py", "certs.py"):
                continue
            for номер, строка in enumerate(
                    файл.read_text(encoding="utf-8").splitlines(), 1):
                чистая = строка.split("#")[0]
                if re.search(r'"[^"]*pip install (?!-r )[a-z]', чистая):
                    виновные.append(f"{файл.relative_to(ROOT)}:{номер}")
        self.assertEqual([], виновные, "подсказка собрана вручную, мимо pip_hint")


class СовместимостьС51(unittest.TestCase):
    """На машине отдела Windows PowerShell 5.1, а проверки идут под pwsh 7."""

    #: То, чего в 5.1 нет вовсе. Ключ — что искать, значение — чем это грозит.
    ЗАПРЕТЫ = {
        r"\?\?": "оператора ?? в 5.1 нет",
        r"\|\|": "конвейерных цепочек || в 5.1 нет",
        r"&&": "конвейерных цепочек && в 5.1 нет",
        r"-AsHashtable": "ConvertFrom-Json -AsHashtable появился в 6.0",
        r"-LeafBase": "Split-Path -LeafBase появился в 6.0",
        r"\$IsWindows": "переменной $IsWindows в 5.1 нет",
        r"-SkipCertificateCheck": "ключа -SkipCertificateCheck в 5.1 нет",
        r"utf8NoBOM": "кодировки utf8NoBOM в 5.1 нет",
        r"ForEach-Object\s+-Parallel": "-Parallel появился в 7.0",
        r"Get-Error": "Get-Error появился в 7.0",
    }

    def test_ничего_из_седьмой_версии_не_используется(self):
        беды = []
        for скрипт in все_скрипты():
            текст = без_строк_и_примечаний(читать(скрипт))
            for строка in текст.splitlines():
                # Проверка версии в той же строке — это как раз забота о 5.1:
                # «если версия 6 и новее, тогда $IsWindows» там законно.
                if "PSVersion" in строка:
                    continue
                for образец, чем in self.ЗАПРЕТЫ.items():
                    if re.search(образец, строка):
                        беды.append(f"{скрипт.relative_to(ROOT)}: {чем}")
        self.assertEqual([], беды)

    def test_поток_ошибок_внешних_программ_читают_осторожно(self):
        """В 5.1 «2>&1» при ErrorActionPreference='Stop' обрывает скрипт.

        Так молча обрывалась вся офлайн-установка: git clone пишет «Cloning
        into...» в поток ошибок, и на этом всё заканчивалось — до создания
        настроек и администратора.
        """
        беды = []
        for скрипт in все_скрипты():
            текст = читать(скрипт)
            if "$ErrorActionPreference = 'Stop'" not in текст:
                continue
            for номер, строка in enumerate(текст.splitlines(), 1):
                if "2>&1" not in строка or строка.strip().startswith("#"):
                    continue
                if "cmd /c" in строка:           # перенаправление внутри cmd
                    continue
                # Ищем защиту в двадцати строках выше: там должно быть
                # переключение на 'Continue' или своя обёртка.
                выше = "\n".join(текст.splitlines()[max(0, номер - 20):номер])
                if "'Continue'" in выше or "Invoke-Native" in выше:
                    continue
                беды.append(f"{скрипт.relative_to(ROOT)}:{номер}")
        self.assertEqual([], беды, "«2>&1» без защиты оборвёт скрипт в 5.1")

    def test_адрес_в_сети_ищут_по_настоящей_метрике(self):
        """У Get-NetIPAddress нет InterfaceMetric — сортировка молчала."""
        текст = читать(WINDOWS / "_common.ps1")
        self.assertNotRegex(
            текст, r"Get-NetIPAddress[^}]*Sort-Object -Property InterfaceMetric",
            "сортировка по свойству, которого у объекта нет")
        self.assertIn("Get-NetIPInterface", текст)

    def test_все_скрипты_в_utf8_с_bom(self):
        """Без BOM 5.1 читает кириллицу как набор знаков вопроса."""
        for скрипт in все_скрипты():
            with self.subTest(script=скрипт.name):
                self.assertEqual(b"\xef\xbb\xbf", скрипт.read_bytes()[:3])


class ЧестностьУстановки(unittest.TestCase):
    """«Готово» и «без замечаний» не должны быть неправдой."""

    def test_проверка_пакетов_смотрит_на_все_нужные(self):
        текст = читать(WINDOWS / "01-install.ps1")
        for пакет in ("fastapi", "uvicorn", "docx", "pymupdf", "numpy",
                      "multipart", "itsdangerous", "jinja2"):
            self.assertIn(f"'{пакет}'", текст,
                          f"установка не проверяет пакет {пакет}")

    def test_нужные_пакеты_совпадают_с_requirements(self):
        """Список в скрипте не должен отстать от списка зависимостей."""
        имена = []
        for строка in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            строка = строка.split("#")[0].strip()
            if строка:
                имена.append(re.split(r"[><=\[]", строка)[0].strip())
        ввоз = {"fastapi": "fastapi", "uvicorn": "uvicorn", "python-docx": "docx",
                "pymupdf": "pymupdf", "numpy": "numpy",
                "python-multipart": "multipart", "itsdangerous": "itsdangerous",
                "jinja2": "jinja2"}
        текст = читать(WINDOWS / "01-install.ps1")
        for пакет in имена:
            self.assertIn(ввоз[пакет], текст,
                          f"{пакет} есть в зависимостях, но установка его не проверяет")

    def test_без_администратора_это_замечание(self):
        текст = читать(OFFLINE / "install-offline.ps1")
        куски = текст[текст.index("Step 'Администратор'"):]
        self.assertIn("Later", куски,
                      "«установка без замечаний» без администратора — неправда")

    def test_запуск_проверяет_очевидное_до_пятиминутного_ожидания(self):
        текст = читать(WINDOWS / "start-all.ps1")
        место_проверки = текст.index("Get-LlamaServer")
        место_ожидания = текст.index("Wait-Http")
        self.assertLess(место_проверки, место_ожидания,
                        "о нехватке модели узнаём через пять минут ожидания")
        self.assertIn(".gguf", текст)

    def test_ожидание_не_молчит_и_видит_смерть_процесса(self):
        текст = читать(WINDOWS / "_common.ps1")
        self.assertIn("ждём...", текст)
        self.assertIn("Get-Process $process", текст)

    def test_остановка_не_трогает_чужую_работу(self):
        """Приём библиотеки идёт часами — обрывать его stop-all не должен.

        Список процессов берётся из WMI, которого вне Windows нет, поэтому
        проверяем сам образец: он обязан отличать веб-сервер от прочей работы.
        """
        текст = читать(WINDOWS / "stop-all.ps1")
        найдено = re.search(r"\$сервер\s*=\s*'([^']+)'", текст)
        self.assertIsNotNone(найдено, "образца для веб-сервера в скрипте нет")
        образец = найдено.group(1)
        self.assertNotEqual("reportgen", образец,
                            "останавливается любой процесс со словом reportgen")
        # Проверяем сам образец на живых примерах командных строк.
        правило = re.compile(образец.replace("\\b", r"\b"))
        останавливаем = [
            r"C:\reportgen\app\.venv\Scripts\python.exe -m reportgen serve",
            r"C:\reportgen\app\.venv\Scripts\python.exe -m reportgen.web",
        ]
        не_трогаем = [
            r"C:\reportgen\app\.venv\Scripts\python.exe -m reportgen ingest D:\lib",
            r"C:\reportgen\app\.venv\Scripts\python.exe -m reportgen embed --force",
            r"C:\reportgen\app\.venv\Scripts\python.exe -m reportgen backup",
        ]
        for строка in останавливаем:
            self.assertTrue(правило.search(строка), f"не остановит сервер: {строка}")
        for строка in не_трогаем:
            self.assertFalse(правило.search(строка), f"оборвёт чужую работу: {строка}")
        self.assertIn("приём библиотеки", текст)


class ОстальныеТупики(unittest.TestCase):
    """Мелочь, на которой человек всё равно застревает."""

    def test_разметка_заменённых_подключает_общее(self):
        """Invoke-Reportgen — функция из _common.ps1, а не программа.

        Сгенерированный скрипт падал на первой же строке: «имя
        Invoke-Reportgen не распознано», — а без разметки поиск считает
        отменённые редакции действующими.
        """
        текст = читать(OFFLINE / "itu.ps1")
        кусок = текст[текст.index("пометить-заменённые.ps1"):]
        self.assertIn("_common.ps1", кусок[:2000],
                      "сгенерированный скрипт не подключает _common.ps1")

    def test_переносят_всё_standards_а_не_одну_папку(self):
        """Заменённые редакции лежат отдельно; без них метить нечего."""
        текст = читать(OFFLINE / "itu.ps1")
        self.assertNotIn("data\\library\\standards\\itu-t", текст)
        self.assertIn("reportgen paths", текст)

    def test_запуск_говорит_что_окружения_нет(self):
        текст = читать(WINDOWS / "start-app.ps1")
        self.assertIn("беру системный python", текст)

    def test_относительный_каталог_данных_разворачивается(self):
        """Иначе запуск из другого места — и база «пропала»."""
        with tempfile.TemporaryDirectory() as имя:
            путь = Path(имя) / "settings.json"
            путь.write_text(json.dumps({"data_dir": "данные"}, ensure_ascii=False),
                            encoding="utf-8")
            настройки = Settings.load(путь)
            self.assertTrue(Path(настройки.data_dir).is_absolute(),
                            "каталог данных остался относительным")
            self.assertTrue(Path(настройки.db_path).is_absolute())


class ДокументацияНеОбещаетЛишнего(unittest.TestCase):
    def test_шаг_про_https_ведёт_в_существующий_каталог(self):
        текст = (ROOT / "docs" / "11-windows.md").read_text(encoding="utf-8")
        кусок = текст[текст.index("## Шаг 9а"):текст.index("## Шаг 9а") + 4000]
        self.assertNotIn("cd C:\\reportgen\\scripts\\windows", кусок,
                         "каталога C:\\reportgen\\scripts\\windows не существует")
        self.assertIn("cd C:\\reportgen\\app\\scripts\\windows", кусок)

    def test_на_рабочие_места_велено_нести_корень(self):
        текст = (ROOT / "docs" / "11-windows.md").read_text(encoding="utf-8")
        кусок = текст[текст.index("## Шаг 9а"):текст.index("## Шаг 9а") + 5000]
        self.assertIn("корень.crt", кусок)
        self.assertNotIn("-Install <путь к сервер.crt>", кусок)

    def test_сказано_что_https_необязателен(self):
        """На рабочих местах ставить нельзя ничего — и это должно быть видно."""
        текст = (ROOT / "docs" / "11-windows.md").read_text(encoding="utf-8")
        кусок = текст[текст.index("## Шаг 9а"):текст.index("## Шаг 9а") + 6000]
        self.assertIn("Если https ставить не хотите", кусок)
        self.assertIn("ВЫЗОВ", кусок)
        self.assertIn("ставить ничего и нельзя", кусок)

    def test_про_пакет_cryptography_больше_не_обещают(self):
        for файл in (ROOT / "docs").glob("*.md"):
            текст = файл.read_text(encoding="utf-8")
            for номер, строка in enumerate(текст.splitlines(), 1):
                if "cryptography" in строка and "больше нет" not in строка:
                    self.fail(f"{файл.name}:{номер}: {строка.strip()}")

    def test_раздела_дашборд_в_документации_не_осталось(self):
        """Он давно называется «Сводка» — человек ищет по названию из меню."""
        виновные = [файл.name for файл in (ROOT / "docs").glob("*.md")
                    if "Дашборд" in файл.read_text(encoding="utf-8")]
        self.assertEqual([], виновные)

    def test_восстановление_описано_по_тому_что_копия_делает(self):
        текст = (ROOT / "docs" / "11-windows.md").read_text(encoding="utf-8")
        кусок = текст[текст.index("## Резервное копирование"):]
        кусок = кусок[:4000]
        self.assertNotIn("library.zip", кусок,
                         "копия давно не делает library.zip")
        self.assertIn("опись.txt", кусок)


class СквознойМаршрутВерен(unittest.TestCase):
    """docs/00-start.md — то, по чему систему ставят от начала до конца."""

    @classmethod
    def setUpClass(cls):
        cls.текст = (ROOT / "docs" / "00-start.md").read_text(encoding="utf-8")

    def test_число_слотов_модели_совпадает_со_скриптом(self):
        """Документация обещала два слота, а скрипт поднимает один."""
        скрипт = читать(WINDOWS / "start-llm.ps1")
        найдено = re.search(r"\[int\]\$Parallel\s*=\s*(\d+)", скрипт)
        self.assertIsNotNone(найдено)
        self.assertIn("--parallel %s`" % найдено.group(1), self.текст)

    def test_объём_материала_совпадает_с_настройками(self):
        """В образце настроек 52000 — столько и должно быть обещано."""
        образец = json.loads(
            (WINDOWS / "settings.example.json").read_text(encoding="utf-8-sig"))
        сколько = образец["assistant_context_chars"]
        self.assertIn("%d 000" % (сколько // 1000), self.текст)

    def test_не_зовут_программу_которой_нет(self):
        """`reportgen` — не программа: она живёт в окружении приложения."""
        for номер, строка in enumerate(self.текст.splitlines(), 1):
            голая = строка.strip()
            if голая.startswith("reportgen ") and "Invoke-Reportgen" not in строка:
                self.fail(f"00-start.md:{номер}: {голая}")

    def test_сказано_что_на_рабочих_местах_ничего_не_ставят(self):
        self.assertIn("Ставить на них **не нужно ничего**", self.текст)
        self.assertIn("ВЫЗОВ", self.текст)

    def test_копия_описана_так_как_делается(self):
        кусок = self.текст[self.текст.index("### Резервная копия"):]
        self.assertIn("опись.txt", кусок[:1500])
        self.assertIn("вложения писем", кусок[:1500])


class НастройкиЧитаютсяПоЧеловечески(unittest.TestCase):
    def test_испорченный_файл_называют_и_показывают_место(self):
        with tempfile.TemporaryDirectory() as имя:
            путь = Path(имя) / "settings.json"
            путь.write_text('{\n  "port": 8080,\n}\n', encoding="utf-8")
            with self.assertRaises(ValueError) as поймано:
                Settings.load(путь)
            сказано = str(поймано.exception)
            self.assertIn(str(путь), сказано)
            self.assertIn("строка", сказано)

    def test_список_из_переменной_среды_не_рассыпается_на_буквы(self):
        прежнее = os.environ.get("REPORTGEN_HTTPS_HOSTS")
        os.environ["REPORTGEN_HTTPS_HOSTS"] = "192.168.10.5, otdel-server"
        try:
            настройки = Settings.load()
            self.assertEqual(["192.168.10.5", "otdel-server"], настройки.https_hosts)
        finally:
            if прежнее is None:
                del os.environ["REPORTGEN_HTTPS_HOSTS"]
            else:
                os.environ["REPORTGEN_HTTPS_HOSTS"] = прежнее

    def test_приписка_отдела_доходит_до_колонтитула(self):
        """Настройка report_footer была в образце, но не читалась никем."""
        from reportgen.export.docx import footer_for

        строка = footer_for("2024-118", "вх-5", "исх-9", "Не является офертой.")
        self.assertIn("Не является офертой.", строка)
        self.assertIn("исх. исх-9", строка)


if __name__ == "__main__":
    unittest.main()
