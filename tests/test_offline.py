"""Проверки офлайн-комплекта: состав, скрипты сборки и установки.

Комплект собирают на одной машине, а разворачивают на изолированной, куда уже
не приехать с исправлением. Поэтому всё, что можно проверить без Windows,
проверяется здесь: состав bundle.example.json, поддержка всех видов источников
в pack.ps1, всех способов установки в install-offline.ps1, кодировка скриптов
и соответствие команд реальному CLI.
"""

import json
import os
import re
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import _bootstrap  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = ROOT / "scripts" / "offline"
CONFIG = OFFLINE / "bundle.example.json"

KNOWN_SOURCE_KINDS = {"url", "sourceforge", "github-release", "github-raw", "tdf-stable"}
KNOWN_INSTALL_KINDS = {"exe", "msi"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class BundleConfigTests(unittest.TestCase):
    """Состав комплекта описан полно и без противоречий."""

    def setUp(self):
        self.plan = json.loads(read(CONFIG))

    def test_config_parses(self):
        self.assertIn("models", self.plan)
        self.assertIn("tools", self.plan)
        self.assertIn("tessdata", self.plan)

    def test_model_ids_unique(self):
        ids = [model["id"] for model in self.plan["models"]]
        self.assertEqual(len(ids), len(set(ids)), f"повторяющиеся идентификаторы моделей: {ids}")

    def test_models_have_everything_needed_to_download(self):
        for model in self.plan["models"]:
            with self.subTest(model=model["id"]):
                self.assertTrue(model["repo"])
                self.assertTrue(model["file"].endswith(".gguf"))
                self.assertGreater(model["approx_gb"], 0)

    def test_all_three_roles_present(self):
        # Без эмбеддера смысловой поиск не работает, без реранкера сильно хуже
        # выдача: на изолированной машине докачать их будет неоткуда.
        roles = {model["role"] for model in self.plan["models"]}
        self.assertEqual({"llm", "embeddings", "rerank"}, roles)

    def test_spare_model_present(self):
        # Если основная модель не влезет в видеопамять конкретной машины,
        # запасная поменьше должна лежать в том же комплекте.
        main = [m for m in self.plan["models"] if m["role"] == "llm"]
        self.assertGreaterEqual(len(main), 2, "нет запасной модели поменьше")
        self.assertLess(min(m["approx_gb"] for m in main), max(m["approx_gb"] for m in main))

    def test_tool_ids_unique(self):
        ids = [tool["id"] for tool in self.plan["tools"]["items"]]
        self.assertEqual(len(ids), len(set(ids)), f"повторяющиеся идентификаторы программ: {ids}")

    def test_tools_are_fully_described(self):
        for tool in self.plan["tools"]["items"]:
            with self.subTest(tool=tool["id"]):
                self.assertTrue(tool["name"])
                self.assertTrue(tool["why"], "должно быть написано, зачем программа нужна")
                self.assertTrue(tool["sources"], "без источников программу нечем скачать")
                self.assertIn(tool["install"]["kind"], KNOWN_INSTALL_KINDS)
                self.assertTrue(tool["check"], "нечем проверить, что программа встала")

    def test_source_kinds_are_known(self):
        for tool in self.plan["tools"]["items"]:
            for source in tool["sources"]:
                with self.subTest(tool=tool["id"], kind=source.get("kind")):
                    self.assertIn(source["kind"], KNOWN_SOURCE_KINDS)

    def test_every_source_carries_what_its_kind_needs(self):
        required = {
            "url": ("url",),
            "sourceforge": ("project",),
            "github-release": ("repo", "pattern"),
            "github-raw": ("repo", "path"),
            "tdf-stable": ("template",),
        }
        for tool in self.plan["tools"]["items"]:
            for source in tool["sources"]:
                for field in required[source["kind"]]:
                    with self.subTest(tool=tool["id"], kind=source["kind"], field=field):
                        self.assertTrue(source.get(field), f"у источника нет поля {field}")

    def test_format_tools_all_present(self):
        # Ровно те программы, без которых не читаются форматы библиотеки.
        ids = {tool["id"] for tool in self.plan["tools"]["items"]}
        self.assertLessEqual({"libreoffice", "tesseract", "djvulibre", "sevenzip"}, ids)

    def test_base_tools_all_present(self):
        ids = {tool["id"] for tool in self.plan["tools"]["items"]}
        self.assertLessEqual({"python", "vcredist", "git"}, ids)

    def test_tessdata_has_russian(self):
        # Тихая установка Tesseract русский язык не ставит — он обязан приехать
        # файлом в комплекте, иначе сканы русских книг превратятся в мусор.
        paths = {file["path"] for file in self.plan["tessdata"]["files"]}
        self.assertIn("rus.traineddata", paths)

    def test_tessdata_flavour_exists(self):
        flavour = self.plan["tessdata"]["install_from"]
        prefixes = {file["as"].split("/")[0] for file in self.plan["tessdata"]["files"]}
        self.assertIn(flavour, prefixes)

    def test_tessdata_has_orientation_data(self):
        # Без osd Tesseract ругается на определение ориентации страницы.
        names = {file["path"] for file in self.plan["tessdata"]["files"]}
        self.assertIn("osd.traineddata", names)

    def test_llama_keeps_spare_release(self):
        self.assertTrue(self.plan["llama_cpp"]["keep_previous"])

    def test_llama_search_depth_set(self):
        self.assertGreaterEqual(self.plan["llama_cpp"].get("search_depth", 0), 5)

    def test_python_version_matches_installer(self):
        python = [tool for tool in self.plan["tools"]["items"] if tool["id"] == "python"][0]
        url = python["sources"][0]["url"]
        self.assertRegex(url, r"python-3\.1[1-9]\.\d+-amd64\.exe$")


class PackScriptTests(unittest.TestCase):
    """pack.ps1 умеет всё, что перечислено в настройках."""

    def setUp(self):
        self.plan = json.loads(read(CONFIG))
        self.text = read(OFFLINE / "pack.ps1")

    def test_handles_every_source_kind_in_config(self):
        used = {source["kind"] for tool in self.plan["tools"]["items"] for source in tool["sources"]}
        for kind in used:
            with self.subTest(kind=kind):
                self.assertIn(f"'{kind}'", self.text, f"pack.ps1 не разбирает источник {kind}")

    def test_sourceforge_asks_for_windows_build(self):
        # SourceForge отдаёт «лучший выпуск» по системе клиента: без явной
        # пометки сборка из WSL молча получит .tar.gz вместо .exe.
        self.assertIn("platform=windows", self.text)

    def test_probe_mode_exists(self):
        self.assertIn("$Probe", self.text)

    def test_list_parameters_are_split(self):
        # "powershell -File pack.ps1 -Only a,b" отдаёт один элемент "a,b".
        self.assertIn("Split-List", self.text)

    def test_downloads_test_dependencies(self):
        # Прогон тестов — единственная проверка установки без сети и модели.
        # Без httpx из requirements-dev.txt три модуля падают на импорте.
        self.assertIn("requirements-dev.txt", self.text)

    def test_llama_release_is_chosen_by_assets(self):
        # "Последний выпуск" llama.cpp по мнению GitHub бывает без сборок под
        # Windows (например, v0.2.0). Брать его вслепую нельзя — нужно искать
        # первый выпуск, в котором лежат нужные архивы.
        self.assertNotIn("releases/latest", self.text)
        self.assertIn("Find-LlamaRelease", self.text)

    def test_llama_failure_shows_available_assets(self):
        # Сообщение "нет файла по шаблону" без списка того, что есть,
        # не даёт ничего починить.
        self.assertIn("в нём есть:", self.text)

    def test_checksums_are_verified(self):
        self.assertIn("Test-Checksum", self.text)
        self.assertIn("MD5", self.text)
        self.assertIn("SHA256", self.text)


class InstallScriptTests(unittest.TestCase):
    """install-offline.ps1 ставит то, что собрал pack.ps1."""

    def setUp(self):
        self.plan = json.loads(read(CONFIG))
        self.text = read(OFFLINE / "install-offline.ps1")

    def test_handles_every_install_kind(self):
        used = {tool["install"]["kind"] for tool in self.plan["tools"]["items"]}
        for kind in used:
            with self.subTest(kind=kind):
                self.assertIn(f"'.{kind}'", self.text, f"install-offline.ps1 не ставит {kind}")

    def test_copies_russian_language_data(self):
        self.assertIn("tessdata", self.text)
        self.assertIn("--list-langs", self.text)

    def test_pip_never_goes_online(self):
        for match in re.finditer(r"pip install([^\r\n]*)", self.text):
            with self.subTest(line=match.group(0)):
                self.assertIn("--no-index", match.group(1))

    def test_uses_real_cli_signature(self):
        # useradd принимает --login, а не позиционный аргумент: вызов
        # "reportgen useradd admin" просто упадёт в конце установки.
        for match in re.finditer(r"reportgen useradd([^\r\n]*)", self.text):
            tail = match.group(1)
            if tail.startswith(")"):
                continue                      # упоминание в тексте справки
            with self.subTest(call=match.group(0)):
                self.assertTrue(tail.lstrip().startswith("--login"), "нет --login")

    def test_reports_what_went_wrong_at_the_end(self):
        # Установщик не останавливается на мелочах, но обязан собрать
        # замечания и показать их в конце — иначе их не заметят.
        self.assertIn("$script:Warnings", self.text)

    def test_can_run_without_questions(self):
        self.assertIn("$Unattended", self.text)

    def test_installs_test_dependencies(self):
        self.assertIn("requirements-dev.txt", self.text)

    def test_sets_home_for_non_default_target(self):
        # Скрипты запуска ищут установку по REPORTGEN_HOME, иначе смотрят в
        # C:\reportgen: установка с -Target выглядела бы успешной, а
        # start-all.ps1 не нашёл бы ни моделей, ни настроек.
        self.assertIn("REPORTGEN_HOME", self.text)


class ScriptHygieneTests(unittest.TestCase):
    """Общие требования ко всем скриптам комплекта."""

    def scripts(self):
        return sorted(OFFLINE.glob("*.ps1")) + sorted((ROOT / "scripts" / "windows").glob("*.ps1"))

    def test_powershell_scripts_are_utf8_with_bom(self):
        # Windows PowerShell 5.1 читает файл без BOM как ANSI: русский текст
        # в сообщениях превращается в кракозябры.
        for path in self.scripts():
            with self.subTest(script=path.name):
                self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"), "нет BOM")

    def test_no_shadowing_of_automatic_variables(self):
        # $Home, $Host, $Args и подобные — автоматические переменные
        # PowerShell; присваивание им уже однажды увело установку не туда.
        forbidden = re.compile(
            r"^\s*\$(Home|Host|Args|Input|Error|Matches|PSItem|Profile|Pwd)\s*=", re.IGNORECASE | re.MULTILINE
        )
        for path in self.scripts():
            with self.subTest(script=path.name):
                found = forbidden.findall(read(path))
                self.assertFalse(found, f"перекрыты автоматические переменные: {found}")

    def test_no_string_concatenation_in_message_calls(self):
        # Warn 'текст' + $путь — это три позиционных аргумента, и путь молча
        # теряется: пользователь видит «распакуйте вручную в » без каталога.
        broken = re.compile(r"^\s*(Warn|Note|Ok|Step|Fail|Later)\s+'[^']*'\s*\+", re.MULTILINE)
        for path in self.scripts():
            with self.subTest(script=path.name):
                self.assertFalse(broken.findall(read(path)), "склейка строки через + после имени функции")


class ByteOrderMarkTests(unittest.TestCase):
    """Файлы, пришедшие из Windows, часто начинаются с BOM.

    Блокнот и Windows PowerShell 5.1 (`Set-Content -Encoding UTF8`) добавляют
    в начало файла метку порядка байтов. Обычный `utf-8` на ней падает с
    «Unexpected UTF-8 BOM», и настройки, факт-пакет или глоссарий молча
    становятся нечитаемыми — уже после установки, на первом же обращении.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_with_bom(self, name: str, payload) -> Path:
        path = self.dir / name
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return path

    def test_settings_with_bom_load(self):
        from reportgen.config import Settings

        path = self.write_with_bom("settings.json", {"llm_model": "проверка"})
        self.assertEqual("проверка", Settings.load(path).llm_model)

    def test_fact_pack_with_bom_loads(self):
        from reportgen.facts import FactPack

        source = json.loads(read(ROOT / "examples" / "cases" / "case-2024-118.json"))
        path = self.write_with_bom("case.json", source)
        self.assertTrue(FactPack.load(path).case_id)

    def test_glossary_with_bom_loads(self):
        from reportgen.cli import _load_glossary

        path = self.write_with_bom("glossary.json", {"ОСШ": "отношение сигнал/шум"})
        self.assertEqual({"ОСШ": "отношение сигнал/шум"}, _load_glossary(str(path)))

    def test_powershell_writes_settings_without_bom(self):
        # Обратная сторона: скрипты не должны создавать файл с BOM вообще.
        for name in ("scripts/offline/install-offline.ps1", "scripts/windows/01-install.ps1"):
            with self.subTest(script=name):
                text = read(ROOT / name)
                self.assertNotRegex(
                    text,
                    r"Set-Content\s+\$(script:)?[Cc]onfig[^\r\n]*-Encoding UTF8",
                    "settings.json пишется через Set-Content -Encoding UTF8 (в PowerShell 5.1 это BOM)",
                )
                self.assertIn("UTF8Encoding($false)", text)


class WindowsScriptTests(unittest.TestCase):
    """Скрипты запуска умеют то, что советует документация."""

    def test_start_all_forwards_context(self):
        # docs/11 советует уменьшить контекст при нехватке видеопамяти —
        # значит, у точки входа должен быть такой ключ.
        text = read(ROOT / "scripts" / "windows" / "start-all.ps1")
        self.assertIn("$Context", text)
        self.assertIn("'-Context'", text)

    def test_advice_switches_exist_in_scripts(self):
        doc = read(ROOT / "docs" / "11-windows.md")
        scripts = "\n".join(read(path) for path in (ROOT / "scripts" / "windows").glob("*.ps1"))
        for switch in sorted(set(re.findall(r"`(-[A-Z][A-Za-z]+)[ `]", doc))):
            with self.subTest(switch=switch):
                self.assertIn(f"${switch[1:]}", scripts, f"в скриптах нет ключа {switch}")


class OfflineDocsTests(unittest.TestCase):
    """Документация не разошлась со скриптами."""

    def setUp(self):
        self.doc = read(ROOT / "docs" / "15-offline.md")

    def test_mentioned_switches_exist(self):
        pack = read(OFFLINE / "pack.ps1")
        for switch in re.findall(r"pack\.ps1[^\n]*?(-[A-Z][A-Za-z]+)", self.doc):
            with self.subTest(switch=switch):
                self.assertIn(f"${switch[1:]}", pack, f"в pack.ps1 нет ключа {switch}")

    def test_config_file_name_matches(self):
        self.assertTrue("bundle.example.json" in self.doc, "документ ссылается на старое имя файла настроек")

    def test_windows_doc_has_no_phantom_index_file(self):
        # После ingest файла build/index.json не существует: поиск по
        # установленной системе идёт по базе.
        doc = read(ROOT / "docs" / "11-windows.md")
        self.assertNotIn("build/index.json", doc)

    def test_probe_documented(self):
        # Проверка адресов до многочасовой закачки — главный приём этого
        # документа, он обязан быть описан.
        self.assertTrue("-Probe" in self.doc, "в docs/15-offline.md не описан ключ -Probe")


class WindowsBinaryDiscoveryTests(unittest.TestCase):
    """Программы, установленные под Windows, находятся без правки PATH.

    Установщики Tesseract, DjVuLibre и 7-Zip в PATH себя не прописывают, а
    тихая установка из комплекта тем более. Если реестр форматов проверяет
    только PATH, то после успешной установки система заявит, что .djvu, .7z и
    сканы не поддерживаются, и библиотека отсканированных методичек уйдёт в
    базу пустой — молча, без единой ошибки.
    """

    def test_every_binary_requirement_knows_where_to_look(self):
        from reportgen.ingest import registry

        registry.ensure_loaded()
        for spec in registry.all_specs():
            for requirement in spec.requires:
                if requirement.kind != "binary":
                    continue
                with self.subTest(converter=spec.name, binary=requirement.name):
                    self.assertIsNotNone(
                        requirement.locate,
                        "требование ищет программу только в PATH — под Windows "
                        "установленная программа останется невидимой",
                    )

    def test_locate_overrides_path_lookup(self):
        from reportgen.ingest import registry

        found = registry.Requirement("binary", "нетакой", "подсказка",
                                     locate=lambda: r"C:\Program Files\X\x.exe")
        absent = registry.Requirement("binary", "нетакой", "подсказка")
        self.assertTrue(found.is_available())
        self.assertFalse(absent.is_available())

    def test_locate_does_not_leak_into_description(self):
        from reportgen.ingest import registry

        requirement = registry.Requirement("binary", "x", "y", locate=lambda: "z")
        self.assertEqual({"kind", "name", "hint", "available"}, set(requirement.to_dict()))

    def test_standard_windows_directories_are_listed(self):
        from reportgen.ingest.formats import archive, djvu, ocr

        self.assertTrue(any("Tesseract-OCR" in path for path in ocr._WINDOWS_TESSERACT))
        self.assertTrue(any("DjVuLibre" in path for path in djvu._WINDOWS_DIRS))
        self.assertTrue(any("7-Zip" in path for path in archive._WINDOWS_ARCHIVE_DIRS))

    def test_archive_tool_looks_beyond_path(self):
        from reportgen.ingest.formats import archive

        target = r"C:\Program Files\7-Zip\7z.exe"

        class WindowsPathStub:
            def __init__(self, *parts):
                self.text = "\\".join(str(part).rstrip("\\") for part in parts)

            def __truediv__(self, other):
                return WindowsPathStub(self.text, other)

            def __str__(self):
                return self.text

            def is_file(self):
                return self.text == target

        with mock.patch.object(archive.shutil, "which", return_value=None), \
             mock.patch.object(archive.os, "name", "nt"), \
             mock.patch.object(archive, "Path", WindowsPathStub):
            self.assertEqual(target, archive.archive_binary("7z"))
            self.assertIsNone(archive.archive_binary("unrar"))


class ProxyBypassTests(unittest.TestCase):
    """Обращения к своей же модели не должны уходить в системный прокси.

    Корпоративный образ Windows почти всегда приезжает с настроенным прокси, а
    ``urllib`` по умолчанию его подхватывает — включая запросы на 127.0.0.1.
    Исключение «<local>» не помогает: CPython обходит по нему только имена без
    точки. Итог был бы такой: llama-server работает и отвечает в браузере, а
    генерация отчёта висит до таймаута, и в логах модели — ни одного запроса.
    """

    def test_opener_has_no_proxies(self):
        import urllib.request

        from reportgen import _http

        handlers = [h for h in _http._OPENER.handlers
                    if isinstance(h, urllib.request.ProxyHandler)]
        self.assertTrue(all(not handler.proxies for handler in handlers),
                        "в опенере остался прокси-обработчик с адресами")

    def test_environment_proxy_does_not_reach_the_opener(self):
        import urllib.request

        from reportgen import _http

        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:9"}, clear=False):
            default = urllib.request.build_opener()
            default_proxies = [h.proxies for h in default.handlers
                               if isinstance(h, urllib.request.ProxyHandler)]
            self.assertTrue(any(p for p in default_proxies),
                            "проверка бессмысленна: опенер по умолчанию тоже без прокси")
            ours = [h.proxies for h in _http._OPENER.handlers
                    if isinstance(h, urllib.request.ProxyHandler)]
            self.assertFalse(any(p for p in ours))

    def test_clients_use_the_shared_opener(self):
        # Прямой вызов urllib.request.urlopen в клиентах вернул бы прокси
        # обратно, причём незаметно: тесты с моками продолжали бы проходить.
        for name in ("llm.py", "embeddings.py", "rerank.py"):
            with self.subTest(module=name):
                text = read(ROOT / "src" / "reportgen" / name)
                self.assertNotIn("urllib.request.urlopen(", text)
                self.assertIn("_http.urlopen(", text)


if __name__ == "__main__":
    unittest.main()
