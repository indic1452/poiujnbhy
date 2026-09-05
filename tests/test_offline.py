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
import shutil
import subprocess
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

    def test_falls_back_when_download_fails(self):
        # Мало определить адрес: зеркало может отдать обрыв или файл с неверной
        # суммой — тогда нужен следующий источник, а не остановка сборки.
        self.assertIn("Get-FromFirstWorking", self.text)

    def test_python_installer_matches_wheels(self):
        # Колёса под 3.11 и установщик 3.12 в одном комплекте — тупик на
        # объекте: другого установщика взять негде.
        self.assertIn("версии Python не совпадают", self.text)

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

    def test_checks_administrator_rights(self):
        # Без прав администратора тихая установка в Program Files не сработает,
        # а скрипт дошёл бы до конца и отрапортовал об успехе.
        self.assertIn("WindowsBuiltInRole", self.text)

    def test_checks_free_space_before_copying(self):
        self.assertIn("Get-PSDrive", self.text)

    def test_installs_tools_before_deploying_code(self):
        # Git из комплекта нужен уже при разворачивании кода: иначе
        # клонирования из git-бандла не будет, и обещанный путь обновления
        # через git pull не создастся никогда.
        tools = self.text.index("Внешние программы для разбора форматов")
        code = self.text.index('Step "Каталоги в $Target"')
        self.assertLess(tools, code, "программы ставятся после разворачивания кода")

    def test_refreshes_path_after_installing_tools(self):
        # PATH текущего процесса после тихой установки ещё старый.
        self.assertIn("Update-PathFromRegistry", self.text)

    def test_checks_python_installer_exit_code(self):
        self.assertRegex(self.text, r"установщик Python вернул код")

    def test_sets_home_for_non_default_target(self):
        # Скрипты запуска ищут установку по REPORTGEN_HOME, иначе смотрят в
        # C:\reportgen: установка с -Target выглядела бы успешной, а
        # start-all.ps1 не нашёл бы ни моделей, ни настроек.
        self.assertIn("REPORTGEN_HOME", self.text)


class BrandingTests(unittest.TestCase):
    """Название отдела меняется в одном файле — и целиком."""

    def setUp(self):
        folder = ROOT / "scripts" / "windows"
        self.example = json.loads(
            (folder / "settings.example.json").read_text(encoding="utf-8-sig"))
        self.docs = (ROOT / "docs" / "11-windows.md").read_text(encoding="utf-8")

    def test_the_short_name_is_in_the_example_too(self):
        """Иначе переименовавший отдел получал два названия на одном экране.

        В примере настроек стояло только полное название. Человек правил
        его, а в шапке слева оставалось прежнее сокращение — потому что
        ключа brand_short в файле просто не было.
        """
        self.assertIn("brand_short", self.example)
        self.assertIn("brand_name", self.example)

    def test_every_branding_key_is_explained(self):
        for key in ("brand_name", "brand_short", "brand_subtitle",
                    "brand_accent", "brand_logo"):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", self.docs)

    def test_the_docs_warn_to_change_both_names_together(self):
        self.assertIn("вместе с `brand_name`", self.docs)

    def test_the_docs_do_not_pretend_the_department_is_a_company(self):
        # «Ваша компания» в примере — след от прежней, не отдельской версии.
        self.assertNotIn('"brand_name": "Ваша компания"', self.docs)


class LanAccessTests(unittest.TestCase):
    """Доступ отделу по сети: адрес должен быть тем, что набирают в браузере."""

    def setUp(self):
        folder = ROOT / "scripts" / "windows"
        self.start_app = (folder / "start-app.ps1").read_text(encoding="utf-8-sig")
        self.common = (folder / "_common.ps1").read_text(encoding="utf-8-sig")
        self.example = json.loads(
            (folder / "settings.example.json").read_text(encoding="utf-8-sig"))
        self.docs = (ROOT / "docs" / "11-windows.md").read_text(encoding="utf-8")

    def test_by_default_the_system_listens_only_to_itself(self):
        """Служебные материалы не выставляют в сеть по умолчанию."""
        self.assertEqual("127.0.0.1", self.example["host"])
        self.assertTrue(self.example["auth_enabled"])

    def test_the_start_script_tells_the_address_colleagues_will_type(self):
        """«http://0.0.0.0:8080» не открывается ниоткуда.

        Человек, поставивший host = 0.0.0.0, шёл спрашивать, что вводить
        коллегам.
        """
        self.assertIn("function Get-LanAddress {", self.common)
        # И она действительно зовётся при запуске, а не просто существует.
        self.assertIn("$lan = Get-LanAddress", self.start_app)
        self.assertIn("коллегам по сети отдела", self.start_app)
        self.assertIn("$host_ -eq '0.0.0.0'", self.start_app)

    def test_a_local_only_setup_says_how_to_open_it_to_the_department(self):
        self.assertIn("host = 0.0.0.0", self.start_app)

    def test_the_loopback_address_is_not_offered_as_a_network_one(self):
        # 127.0.0.1 и 169.254.* — не адреса отдела.
        self.assertIn("'127.0.0.1'", self.common)
        self.assertIn("169.254.*", self.common)

    def test_the_docs_have_the_firewall_rule(self):
        self.assertIn("New-NetFirewallRule", self.docs)
        self.assertIn('"host": "0.0.0.0"', self.docs)


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

    def test_line_endings_are_crlf(self):
        # Эти файлы открывают Блокнотом на изолированной машине, куда не
        # приехать с исправлением. Кроме того, редактор на другой системе
        # молча переписывает файл с чужим переводом строк целиком, и правка из
        # трёх строк выглядит как переписанный файл — однажды так и вышло.
        # Вид закреплён в .gitattributes, здесь он проверяется.
        for path in self.scripts() + sorted((ROOT / "tests" / "powershell").glob("*.ps1")):
            with self.subTest(script=path.name):
                байты = path.read_bytes()
                одиночные = байты.replace(b"\r\n", b"").count(b"\n")
                self.assertEqual(0, одиночные, "перевод строки LF вместо CRLF")

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


class OfflineWebPagesTests(unittest.TestCase):
    """Интерфейс не должен ничего грузить снаружи.

    Штатные страницы FastAPI (/docs, /redoc) тянут swagger-ui с cdn.jsdelivr.net
    и иконку с сайта проекта. В изолированном контуре это белая страница без
    единой ошибки в логах.
    """

    def test_builtin_docs_pages_are_off(self):
        from reportgen.web.app import create_app
        from reportgen.config import Settings

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(data_dir=Path(directory), auth_enabled=False))
            self.assertIsNone(app.docs_url)
            self.assertIsNone(app.redoc_url)

    def test_own_docs_page_has_no_external_requests(self):
        from reportgen.web.app import API_DOCS_PAGE

        for host in ("cdn.", "jsdelivr", "unpkg", "googleapis", "tiangolo", "http://", "https://"):
            with self.subTest(host=host):
                self.assertNotIn(host, API_DOCS_PAGE)

    def test_interface_files_have_no_external_requests(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        for path in sorted(static.rglob("*")):
            if path.suffix not in {".html", ".css", ".js"}:
                continue
            text = read(path)
            for host in ("cdn.", "jsdelivr", "unpkg", "fonts.googleapis", "//ajax."):
                with self.subTest(file=path.name, host=host):
                    self.assertNotIn(host, text)


class DocxTemplateDocsTests(unittest.TestCase):
    """Список стилей бланка в документации не должен разойтись с кодом."""

    def test_documented_styles_match_the_exporter(self):
        exporter = read(ROOT / "src" / "reportgen" / "export" / "docx.py")
        doc = read(ROOT / "docs" / "15-offline.md")
        used = set(re.findall(r'_style\(\s*"([^"]+)"', exporter))
        used |= {name for name in re.findall(r'"(List (?:Bullet|Number))"', exporter)}
        for style in sorted(used):
            with self.subTest(style=style):
                self.assertIn(style, doc, f"стиль {style} используется, но не описан в docs/15")


class LlamaAssetMatchingTests(unittest.TestCase):
    """Подбор архивов llama.cpp — проверка настоящим PowerShell.

    У заказчика в комплект попал только cudart, а сборка сервера — нет:
    установка встала на «llama-server.exe не найден» уже на изолированной
    машине. Выпуск без сборки сервера обязан отвергаться целиком.
    """

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_asset_rules(self):
        script = ROOT / "tests" / "powershell" / "test_llama_assets.ps1"
        done = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


class ItuParsingTests(unittest.TestCase):
    """Разбор страниц МСЭ-Т в качалке — проверка настоящим PowerShell.

    Сеть при разработке качалки была закрыта, поэтому единственное, что
    отделяет её от выдуманных ссылок, — разбор сохранённых образцов страниц.
    Главное здесь: заменённая редакция обязана опознаваться заменённой. Если
    она проедет как действующая, отчёт сошлётся на отменённую норму, а это
    ровно то, ради чего система и заводилась.
    """

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_itu_pages(self):
        script = ROOT / "tests" / "powershell" / "test_itu.ps1"
        done = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


class TableResizeTests(unittest.TestCase):
    """Колонки таблиц тянутся мышью, и ручка обязана ловить курсор.

    Ручка, выступающая за правый край ячейки, визуально на месте, но соседний
    заголовок (тоже sticky и позже в разметке) накрывает её собой — курсор до
    неё не доходит, и таблица «не тянется» без единой ошибки в консоли.
    """

    def setUp(self):
        self.css = read(ROOT / "src" / "reportgen" / "web" / "static" / "styles.css")
        self.js = read(ROOT / "src" / "reportgen" / "web" / "static" / "app.js")

    def test_grip_stays_inside_the_cell(self):
        block = self.css[self.css.index(".col-grip {"):]
        block = block[:block.index("}")]
        self.assertIn("right: 0", block)
        self.assertNotIn("right: -", block, "ручка выступает за край и будет перекрыта")

    def test_drag_listens_on_document(self):
        # Указатель во время перетаскивания уходит за пределы ручки.
        self.assertIn("document.addEventListener('pointermove'", self.js)

    def test_width_is_remembered(self):
        self.assertIn("reportgen.cols.", self.js)

    def test_double_click_resets_column(self):
        self.assertIn("dblclick", self.js)

    def test_minimum_width_is_enforced(self):
        # Иначе колонку можно утащить в ноль и потерять её насовсем.
        self.assertIn("Math.max(56", self.js)


class RfcScriptTests(unittest.TestCase):
    """Скрипт выгрузки RFC.

    RFC качается тысячами файлов по одному, и часть номеров никогда не
    публиковалась — 404 по ним норма. Опасность в другом: если обрыв связи
    засчитывать за «такого RFC нет», половина архива тихо не доедет, а отчёт
    покажет успех.
    """

    def setUp(self):
        self.text = read(OFFLINE / "rfc.ps1")

    def test_missing_number_and_broken_link_are_counted_apart(self):
        self.assertIn("$absent++", self.text)
        self.assertIn("$broken++", self.text)
        self.assertIn("if ($code -eq 404)", self.text)

    def test_download_is_resumable(self):
        # Повторный запуск обязан пропускать уже скачанное: качать 9800
        # файлов заново из-за одного обрыва никто не станет.
        self.assertIn("$skipped++", self.text)
        self.assertIn("Test-Path $target", self.text)

    def test_obsoleted_by_is_written_without_bom(self):
        # Питон читает шапку сам; BOM в начале файла ломает разбор заголовка.
        self.assertIn("UTF8Encoding($false)", self.text)
        self.assertIn("Obsoleted by: ", self.text)

    def test_source_can_be_switched(self):
        # У части заказчиков rfc-editor.org закрыт корпоративным шлюзом.
        self.assertIn("$BaseUrl", self.text)

    def test_paths_are_joined_portably(self):
        self.assertNotIn("'standards\\rfc'", self.text)


class StartGuideTests(unittest.TestCase):
    """Сквозной маршрут docs/00-start.md.

    Инструкция, в которой команда набрана с ошибкой, хуже отсутствующей:
    человек на изолированной машине не может ни проверить её, ни спросить.
    Поэтому каждое имя скрипта и каждый ключ в документе сверяются с самими
    скриптами.
    """

    GUIDE = ROOT / "docs" / "00-start.md"
    #: Ключи самого powershell.exe, а не разбираемого скрипта.
    HOST_SWITCHES = {"executionpolicy", "file", "noprofile", "command"}

    def setUp(self):
        self.text = read(self.GUIDE)

    def scripts(self):
        return {path.name: path for path in (ROOT / "scripts").rglob("*.ps1")}

    def test_every_script_mentioned_exists(self):
        mentioned = set(re.findall(r"([\w][\w-]*\.ps1)", self.text))
        self.assertTrue(mentioned, "в маршруте не осталось ни одной команды")
        unknown = sorted(mentioned - set(self.scripts()))
        self.assertFalse(unknown, f"в документе есть несуществующие скрипты: {unknown}")

    def test_every_switch_is_declared(self):
        scripts = self.scripts()
        bad = []
        for name, tail in re.findall(r"([\w][\w-]*\.ps1)((?:\s+-\w+(?:\s+[^\s\\|#]+)?)*)",
                                     self.text):
            path = scripts.get(name)
            if path is None:
                continue
            block = re.search(r"^param\((.*?)^\)", read(path), re.S | re.M)
            declared = {word.lower() for word in re.findall(r"\$(\w+)", block.group(1))} if block else set()
            # Ключ всегда стоит после пробела. Без этого «-server» из
            # \\otdel-server\obmen считался ключом скрипта.
            for switch in re.findall(r"(?:^|\s)-(\w+)", tail):
                if switch.lower() in self.HOST_SWITCHES:
                    continue
                if switch.lower() not in declared:
                    bad.append(f"{name} -{switch}")
        self.assertFalse(sorted(set(bad)), f"ключей нет в самих скриптах: {sorted(set(bad))}")

    def test_links_to_other_documents_resolve(self):
        links = set(re.findall(r"\]\((\d\d-[\w-]+\.md)\)", self.text))
        missing = sorted(link for link in links if not (ROOT / "docs" / link).exists())
        self.assertFalse(missing, f"битые ссылки: {missing}")

    def test_readme_points_at_the_guide(self):
        self.assertIn("docs/00-start.md", read(ROOT / "README.md"))

    def test_installer_sends_the_reader_to_the_guide(self):
        # Установщик заканчивается списком «что дальше» — маршрут там должен быть.
        self.assertIn("00-start.md", read(OFFLINE / "install-offline.ps1"))

    def test_library_is_loaded_by_one_command(self):
        # Прошлый совет «Invoke-Reportgen ingest ; Invoke-Reportgen embed» уже
        # приводил к вопросу «откуда и что вызывать».
        installer = read(OFFLINE / "install-offline.ps1")
        self.assertIn("load-library.ps1", installer)
        self.assertNotIn("Invoke-Reportgen ingest", installer)


if __name__ == "__main__":
    unittest.main()


class NativeStderrHygieneTests(unittest.TestCase):
    """Ни один вызов внешней программы не смеет обрывать скрипт.

    Windows PowerShell 5.1 при `$ErrorActionPreference = 'Stop'` считает
    ошибкой каждую строку, которую внешняя программа написала в поток ошибок,
    как только этот поток перенаправлен. Ключ `2>$null` от этого НЕ спасает:
    строка попадает в поток ошибок раньше, чем её отбрасывают.

    Ровно так один ответ 403 на первой из 6217 рекомендаций МСЭ оборвал всю
    выгрузку — трассировкой PowerShell вместо объяснения. Перенаправлять поток
    ошибок можно только внутри обёртки, которая на время вызова опускает
    `$ErrorActionPreference` до 'Continue'.
    """

    #: Обёртки, внутри которых перенаправление разрешено: каждая опускает
    #: $ErrorActionPreference перед вызовом и поднимает обратно в finally.
    ОБЁРТКИ = ("Invoke-Native", "Invoke-Curl", "Invoke-Http", "Test-Url", "Get-Setting")

    def scripts(self):
        return sorted(OFFLINE.glob("*.ps1")) + sorted((ROOT / "scripts" / "windows").glob("*.ps1"))

    @staticmethod
    def без_шума(text):
        """Убрать пояснения и склеить строки, разорванные обратной кавычкой.

        Без склейки проверка слепа ровно там, где опаснее всего: длинный вызов
        curl переносят, и «2>$null» оказывается на следующей строке. Такую
        мутацию проверка однажды и пропустила.
        """
        text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
        text = re.sub(r"`\r?\n\s*", " ", text)
        return "\n".join(строка for строка in text.splitlines() if not строка.lstrip().startswith("#"))

    @staticmethod
    def функции(text):
        """Разложить скрипт на куски «имя функции -> её текст»."""
        куски = []
        границы = [(m.start(), m.group(1)) for m in re.finditer(r"^function\s+([\w-]+)", text, re.MULTILINE)]
        for i, (начало, имя) in enumerate(границы):
            конец = границы[i + 1][0] if i + 1 < len(границы) else len(text)
            куски.append((имя, text[начало:конец]))
        # Всё, что до первой функции, и хвост скрипта — код верхнего уровня.
        первая = границы[0][0] if границы else len(text)
        куски.append(("<верхний уровень>", text[:первая]))
        return куски

    def test_stderr_is_redirected_only_inside_a_guarded_wrapper(self):
        перенаправление = re.compile(r"&\s*\$?[\w:.]*[Cc]url[\w.]*\b[^\n]*2>", re.MULTILINE)
        for path in self.scripts():
            text = self.без_шума(read(path))
            if "$ErrorActionPreference = 'Stop'" not in text:
                continue
            for имя, кусок in self.функции(text):
                with self.subTest(script=path.name, function=имя):
                    if not перенаправление.search(кусок):
                        continue
                    self.assertIn(
                        имя,
                        self.ОБЁРТКИ,
                        f"{path.name}: поток ошибок curl перенаправлен в «{имя}» — "
                        "один ответ 403 оборвёт весь запуск",
                    )
                    self.assertIn(
                        "$ErrorActionPreference = 'Continue'",
                        кусок,
                        f"{path.name}: обёртка «{имя}» не опускает $ErrorActionPreference",
                    )
                    self.assertIn("finally", кусок, f"{path.name}: «{имя}» не возвращает прежнее значение")


class ItuDownloadTests(unittest.TestCase):
    """Качалка МСЭ-Т: закрытый сайт обязан быть виден как закрытый.

    Отдел запустил выгрузку, перечни всех 25 серий прочитались, 6584
    рекомендации опознались — а первая же страница ответила 403. Под Windows
    PowerShell 5.1 это оборвало скрипт трассировкой, под PowerShell 7 — прошло
    молча: 403 засчитался за «нет ссылки на PDF», итог сказал «у части
    рекомендаций публикуется только платная версия», код возврата 0. Пустой
    каталог уехал бы на изолированную машину как готовая библиотека.
    """

    def setUp(self):
        self.text = read(OFFLINE / "itu.ps1")

    def test_closed_page_is_not_counted_as_paid_edition(self):
        # Счётчики разные: $noPage — страница не открылась, $nolink — открылась,
        # но платная. Слить их значит выдать закрытый сайт за платные документы.
        self.assertIn("$noPage++", self.text)
        self.assertIn("$nolink++", self.text)
        страница = self.text[self.text.index("if (-not $page) {"):]
        страница = страница[: страница.index("$подряд = 0")]
        self.assertIn("$noPage++", страница)
        self.assertNotIn("$nolink++", страница, "закрытая страница засчитана в платные")

    def test_empty_result_is_a_failure(self):
        self.assertIn("не скачано НИ ОДНОГО документа", self.text)
        хвост = self.text[self.text.index("не скачано НИ ОДНОГО документа"):]
        self.assertIn("exit 1", хвост[:1200], "пустая выгрузка завершается успехом")

    def test_run_stops_after_a_wall_of_failures(self):
        # Шесть тысяч запросов по 700 мс — это час. Час впустую, чтобы в конце
        # сказать «ничего не скачано», никому не нужен.
        self.assertIn("$StopAfterFailures", self.text)
        self.assertIn("if ($подряд -ge $StopAfterFailures)", self.text)

    def test_reason_is_written_in_russian_words(self):
        self.assertIn("сервер отказал (403)", self.text)
        self.assertIn("слишком часто (429)", self.text)
        self.assertIn("страницы нет (404)", self.text)

    def test_failures_are_listed_in_a_file(self):
        # Десять предупреждений в консоли из шести тысяч — не разбор.
        self.assertIn("не-скачано.csv", self.text)

    def test_server_name_is_negotiated(self):
        # МСЭ отдаёт перечень из кэша кому угодно, а страницу рекомендации —
        # только знакомому имени программы.
        self.assertIn("$script:UserAgents", self.text)
        self.assertIn("Mozilla/5.0", self.text)
        self.assertIn("if ($ответ.code -eq 403", self.text)

    def test_cookies_are_kept_for_the_whole_run(self):
        # Файл отдаётся через dologin_pub.asp: тот ставит печенье и
        # перенаправляет. Без общей банки вместо PDF приезжает страница.
        self.assertIn("$script:CookieJar", self.text)
        self.assertIn("'-c', $script:CookieJar, '-b', $script:CookieJar", self.text)

    def test_saved_file_must_really_be_a_pdf(self):
        # Проверки «больше 4 КБ» не хватает: страница «доступ закрыт» тяжелее.
        self.assertIn("function Test-PdfFile", self.text)
        self.assertIn("'%PDF-'", self.text)
        self.assertIn("$этоPdf", self.text)

    def test_truncated_file_is_not_accepted(self):
        # Обрыв посреди передачи: ответ 200, подпись на месте, хвоста нет.
        self.assertIn("$оборван = ($ответ.exit -ne 0)", self.text)
        self.assertIn("связь оборвалась посреди файла", self.text)

    def test_probe_checks_every_step(self):
        # Раньше проверялись только перечни — а они открываются даже тогда,
        # когда всё остальное закрыто.
        проба = self.text[self.text.index("if ($Probe) {"):]
        проба = проба[: проба.index("# --- Перечень")]
        self.assertIn("страница G.703", проба)
        self.assertIn("Доходит ли дело до самого файла", проба)
        self.assertIn("Test-PdfFile", проба)


class RfcTruncationTests(unittest.TestCase):
    """RFC: обрезанный файл не должен попасть в библиотеку.

    Связь оборвалась посреди передачи — сервер уже ответил 200, а на диск лёг
    огрызок в десять байт. Приём положил бы его в библиотеку как документ, и в
    перечне появился бы RFC, которого нет.
    """

    def setUp(self):
        self.text = read(OFFLINE / "rfc.ps1")

    def test_curl_exit_code_is_checked_alongside_http_code(self):
        self.assertIn("$оборван = ($code -eq 200 -and $ответ.exit -ne 0)", self.text)

    def test_broken_link_is_explained(self):
        self.assertIn("связь оборвалась посреди файла", self.text)
        self.assertIn("ответа не было (связь или шлюз)", self.text)

    def test_index_failure_names_the_code(self):
        self.assertIn("не удалось скачать указатель (ответ $код)", self.text)


class ItuLiveRunTests(unittest.TestCase):
    """Качалка МСЭ прогоняется целиком — настоящим PowerShell по настоящему HTTP.

    Разбор текста скрипта ловит не всё: 403 «проезжал» именно на исполнении.
    Поэтому здесь поднимается подставной сайт МСЭ, который ведёт себя так же,
    как настоящий в каждом из известных отказов, и скрипт запускается по нему.
    """

    ПЕРЕЧЕНЬ = (
        "<html><body><table>"
        '<tr><td><a href="/rec/T-REC-G.703/en">G.703</a></td>'
        "<td>Physical and electrical characteristics of hierarchical interfaces</td>"
        "<td>In force</td></tr>"
        '<tr><td><a href="/rec/T-REC-G.704/en">G.704</a></td>'
        "<td>Synchronous frame structures used at 1544 and 2048 kbit/s</td>"
        "<td>In force</td></tr>"
        "</table></body></html>"
    )
    СТРАНИЦА = (
        '<html><body><a href="/rec/dologin_pub.asp?lang=e&amp;'
        'id=T-REC-G.703-201611-I!!PDF-E&amp;type=items">PDF</a></body></html>'
    )
    ФАЙЛ = b"%PDF-1.4\n" + b"x" * 9000 + b"\n%%EOF\n"
    #: Сколько раз сервер уже попросил подождать.
    ЗАНЯТ_РАЗ = 0
    #: Шесть языков, английский — далеко не первый в разметке.
    СТРАНИЦА_ШЕСТЬ_ЯЗЫКОВ = (
        "<html><body>"
        + "".join(
            '<a href="dologin_pub.asp?id=T-REC-G.703-201604-I!!PDF-%s&amp;type=items">%s</a>' % (я, я)
            for я in ("A", "C", "F", "E", "S", "R"))
        + "</body></html>"
    )
    #: Страница, где английского издания нет, а русское есть.
    СТРАНИЦА_РУССКАЯ = (
        "<html><body>"
        '<a href="dologin_pub.asp?lang=r&amp;id=T-REC-G.703-201604-I!!PDF-R&amp;type=items">PDF</a>'
        "</body></html>"
    )
    #: Страница рекомендации, где ссылка на файл записана от корня сайта.
    СТРАНИЦА_АБСОЛЮТНАЯ = (
        "<html><body>"
        '<a href="/rec/dologin_pub.asp?lang=e&amp;id=T-REC-G.703-201604-I!!SOFT-E&amp;type=items">Word</a>'
        '<a href="/rec/dologin_pub.asp?lang=e&amp;id=T-REC-G.703-201604-I!!PDF-E&amp;type=items">PDF</a>'
        "</body></html>"
    )
    #: Страница рекомендации, где ссылка на файл тоже записана относительной.
    СТРАНИЦА_ОТНОСИТЕЛЬНАЯ = (
        "<html><body>"
        '<a href="dologin_pub.asp?lang=e&amp;id=T-REC-G.703-201604-I!!SOFT-E&amp;type=items">Word</a>'
        '<a href="dologin_pub.asp?lang=e&amp;id=T-REC-G.703-201604-I!!PDF-E&amp;type=items">PDF</a>'
        "</body></html>"
    )

    @classmethod
    def обработчик(cls, режим):
        from http.server import BaseHTTPRequestHandler

        сам = cls

        class Обработчик(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def отдать(self, код, тело, тип="text/html"):
                self.send_response(код)
                self.send_header("Content-Type", тип)
                self.send_header("Content-Length", str(len(тело)))
                self.end_headers()
                self.wfile.write(тело)

            def как_у_мсэ(self, путь, режим):
                """Разметка адресов, снятая с настоящего сайта МСЭ."""
                if путь.startswith("/rec/T-REC-G/en") or путь.startswith("/rec/browse/T-REC-G/en"):
                    if режим == "перенаправление" and not путь.startswith("/rec/browse/"):
                        self.send_response(302)
                        self.send_header("Location", "/rec/browse/T-REC-G/en")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    строки = "".join(
                        '<tr><td><a href="../recommendation.asp?lang=en&amp;parent=T-REC-%s">%s</a></td>'
                        '<td>Characteristics of digital transmission equipment %s</td>'
                        '<td>In force</td></tr>' % (н, н, н)
                        for н in ("G.703", "G.704"))
                    if режим == "перенаправление":
                        # После перехода страница лежит на уровень глубже, и
                        # «../» ведёт из /rec/browse/T-REC-G/ в /rec/browse/.
                        строки = строки.replace("../recommendation.asp", "../../recommendation.asp")
                    return self.отдать(200, ("<html><body><table>%s</table></body></html>" % строки).encode())

                # Сервер занят: первый запрос страницы отвергается просьбой
                # подождать. Это не отказ, и терять из-за него документ нельзя.
                # Отвергаем больше раз, чем curl повторяет сам (у него две
                # попытки): иначе проверялась бы не наша уступка, а его.
                if режим == "занят" and путь.startswith("/rec/recommendation.asp"):
                    if сам.ЗАНЯТ_РАЗ < 3:
                        сам.ЗАНЯТ_РАЗ += 1
                        return self.отдать(503, b"Busy")

                # Шесть языков на странице. Сервер отдаёт только английское
                # издание — значит, промах в порядке предпочтения сразу виден.
                if режим == "много-языков":
                    if путь.startswith("/rec/recommendation.asp"):
                        return self.отдать(200, сам.СТРАНИЦА_ШЕСТЬ_ЯЗЫКОВ.encode())
                    if путь.startswith("/rec/dologin_pub.asp"):
                        if "PDF-E" not in self.path:
                            return self.отдать(404, b"no such edition")
                        return self.отдать(200, сам.ФАЙЛ, "application/pdf")

                # У части рекомендаций английского издания нет, а русское есть.
                if режим == "только-русский":
                    if путь.startswith("/rec/recommendation.asp"):
                        return self.отдать(200, сам.СТРАНИЦА_РУССКАЯ.encode())
                    if путь.startswith("/rec/dologin_pub.asp"):
                        if "PDF-R" not in self.path:
                            return self.отдать(404, b"no such edition")
                        return self.отдать(200, сам.ФАЙЛ, "application/pdf")

                # Запасной путь: ссылка из перечня мертва, канонический адрес жив.
                if режим == "запасной" and путь.startswith("/rec/recommendation.asp"):
                    return self.отдать(403, b"Forbidden")
                if режим == "запасной" and путь.startswith("/rec/T-REC-G."):
                    # На канонической странице МСЭ ссылка на файл записана от
                    # корня — иначе она указывала бы внутрь «каталога» с
                    # номером рекомендации, которого не существует.
                    return self.отдать(200, сам.СТРАНИЦА_АБСОЛЮТНАЯ.encode())

                if путь.startswith("/rec/recommendation.asp"):
                    return self.отдать(200, сам.СТРАНИЦА_ОТНОСИТЕЛЬНАЯ.encode())
                if путь.startswith("/rec/dologin_pub.asp"):
                    return self.отдать(200, сам.ФАЙЛ, "application/pdf")
                # Всё прочее — чужой путь; у МСЭ это 403, а не 404.
                return self.отдать(403, b"Forbidden")

            def do_GET(self):
                путь = self.path
                агент = self.headers.get("User-Agent", "")

                # Настоящая структура адресов МСЭ: перечень ссылается на
                # рекомендации ОТНОСИТЕЛЬНО («../recommendation.asp»), а сами
                # страницы лежат в /rec/. Обращение к корню сайта сервер
                # отвергает — так ведёт себя SharePoint у МСЭ.
                if режим in ("относительные", "перенаправление", "запасной",
                             "занят", "только-русский", "много-языков"):
                    return self.как_у_мсэ(путь, режим)

                if путь.startswith("/rec/T-REC-") and "." not in путь.split("/")[-2]:
                    return self.отдать(200, сам.ПЕРЕЧЕНЬ.encode())
                if путь.startswith("/rec/T-REC-"):
                    if режим == "403":
                        return self.отдать(403, b"Forbidden")
                    if режим == "браузер" and "Mozilla" not in агент:
                        return self.отдать(403, b"Forbidden")
                    if режим == "качалка" and "reportgen" not in агент:
                        return self.отдать(403, b"Forbidden")
                    if режим == "печенье":
                        # Так и ведёт себя МСЭ: печенье ставится на странице,
                        # а спрашивается при выдаче файла.
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html")
                        self.send_header("Set-Cookie", "ITUsession=1; Path=/")
                        тело = сам.СТРАНИЦА.encode()
                        self.send_header("Content-Length", str(len(тело)))
                        self.end_headers()
                        self.wfile.write(тело)
                        return
                    return self.отдать(200, сам.СТРАНИЦА.encode())
                if путь.startswith("/rec/dologin_pub.asp"):
                    if режим == "не-pdf":
                        return self.отдать(200, b"<html>" + b"z" * 9000 + b"</html>")
                    if режим == "печенье" and "ITUsession" not in self.headers.get("Cookie", ""):
                        return self.отдать(200, b"<html>" + b"z" * 9000 + b"</html>")
                    if режим == "referer" and "/rec/T-REC-" not in self.headers.get("Referer", ""):
                        return self.отдать(200, b"<html>" + b"z" * 9000 + b"</html>")
                    if режим == "обрыв":
                        self.send_response(200)
                        self.send_header("Content-Length", "500000")
                        self.end_headers()
                        self.wfile.write(сам.ФАЙЛ)
                        self.wfile.flush()
                        self.connection.close()
                        return
                    return self.отдать(200, сам.ФАЙЛ, "application/pdf")
                return self.отдать(404, b"no")

        return Обработчик

    def прогнать(self, режим, *ключи):
        import threading
        from http.server import ThreadingHTTPServer

        сервер = ThreadingHTTPServer(("127.0.0.1", 0), self.обработчик(режим))
        поток = threading.Thread(target=сервер.serve_forever, daemon=True)
        поток.start()
        каталог = tempfile.mkdtemp(prefix="itu-")
        # Убирать каталог сразу нельзя: тест ещё читает из него скачанное.
        self.addCleanup(shutil.rmtree, каталог, ignore_errors=True)
        try:
            готово = subprocess.run(
                [
                    "pwsh", "-NoProfile", "-File", str(OFFLINE / "itu.ps1"),
                    "-BaseUrl", f"http://127.0.0.1:{сервер.server_address[1]}",
                    "-Series", "G", "-Destination", каталог, "-DelayMs", "0", *ключи,
                ],
                capture_output=True, text=True, timeout=180,
            )
            файлы = sorted((Path(каталог) / "standards" / "itu-t" / "G").glob("*.pdf"))
            return готово, файлы, Path(каталог)
        finally:
            сервер.shutdown()
            сервер.server_close()

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_working_source_downloads_everything(self):
        готово, файлы, _ = self.прогнать("хорошо")
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        self.assertEqual(2, len(файлы), готово.stdout)
        self.assertTrue(файлы[0].read_bytes().startswith(b"%PDF-"))

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_forbidden_source_fails_loudly_and_names_the_reason(self):
        готово, файлы, каталог = self.прогнать("403")
        self.assertEqual(1, готово.returncode, "закрытый сайт засчитан за успех:\n" + готово.stdout)
        self.assertEqual([], файлы)
        self.assertIn("сервер отказал (403)", готово.stdout)
        self.assertIn("не скачано НИ ОДНОГО документа", готово.stdout)
        # И ни слова о платных версиях: это была не платность, а отказ.
        итог = готово.stdout[готово.stdout.index("файлов: 0"):]
        self.assertNotIn("платная версия", итог, "отказ сервера выдан за платный документ")

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_server_name_is_negotiated_when_the_first_is_refused(self):
        # Сайт пускает только браузерное имя — скрипт обязан подобрать его сам.
        готово, файлы, _ = self.прогнать("браузер")
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        self.assertEqual(2, len(файлы), готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_the_second_name_is_tried_when_the_first_is_refused(self):
        # Здесь отказано ПЕРВОМУ имени из списка: без подбора выгрузка встанет.
        готово, файлы, _ = self.прогнать("качалка")
        self.assertEqual(0, готово.returncode, "имя программы не подобрано:\n" + готово.stdout)
        self.assertEqual(2, len(файлы), готово.stdout)
        self.assertIn("перешёл на другое", готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_html_page_is_not_saved_as_pdf(self):
        готово, файлы, _ = self.прогнать("не-pdf")
        self.assertEqual(1, готово.returncode, готово.stdout)
        self.assertEqual([], файлы, "html-страница легла в библиотеку под именем .pdf")
        self.assertIn("не PDF", готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_truncated_file_is_not_saved(self):
        готово, файлы, _ = self.прогнать("обрыв")
        self.assertEqual(1, готово.returncode, готово.stdout)
        self.assertEqual([], файлы, "обрезанный файл сохранён как готовый документ")
        self.assertIn("оборвалась посреди файла", готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_wall_of_failures_stops_the_run_early(self):
        готово, _, _ = self.прогнать("403", "-StopAfterFailures", "1")
        self.assertIn("дальше идти незачем", готово.stdout)
        self.assertEqual(1, готово.returncode)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_session_cookie_is_carried_from_page_to_file(self):
        # МСЭ ставит печенье на странице рекомендации, а спрашивает его при
        # выдаче файла. Без общей на весь запуск банки вместо PDF приезжает
        # страница — и раньше она ложилась в библиотеку под именем .pdf.
        готово, файлы, _ = self.прогнать("печенье")
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        self.assertEqual(2, len(файлы), "печенье не доехало от страницы до файла:\n" + готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_referer_is_sent_when_asking_for_the_file(self):
        # Без ссылки на страницу, с которой пришли, dologin_pub.asp отдаёт
        # страницу, а не файл.
        готово, файлы, _ = self.прогнать("referer")
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        self.assertEqual(2, len(файлы), "Referer не передан:\n" + готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_relative_links_are_resolved_against_the_page(self):
        """Ссылка «../recommendation.asp» — та самая, на которой всё встало.

        МСЭ пишет в перечне относительные ссылки. Прежний разбор приклеивал их
        к корню сайта и терял «/rec/»; SharePoint отвечал 403 на чужой путь, и
        выгрузка из 6217 документов не привезла ни одного. Здесь сервер устроен
        так же, как настоящий: по корню — отказ, по /rec/ — страница.
        """
        готово, файлы, _ = self.прогнать("относительные")
        self.assertEqual(0, готово.returncode, "ссылки развёрнуты не туда:\n" + готово.stdout)
        self.assertEqual(2, len(файлы), готово.stdout)
        self.assertTrue(файлы[0].read_bytes().startswith(b"%PDF-"))

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_relative_links_follow_redirects(self):
        """Считать надо от страницы, на которой оказались, а не запрошенной.

        curl ходит с -L. Если МСЭ перебросит перечень на другой адрес, все
        относительные ссылки на нём отсчитываются от нового адреса. Считать от
        запрошенного значило бы снова промахнуться мимо каталога.
        """
        готово, файлы, _ = self.прогнать("перенаправление")
        self.assertEqual(0, готово.returncode, "адрес считался не от итоговой страницы:\n" + готово.stdout)
        self.assertEqual(2, len(файлы), готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_canonical_address_is_tried_when_the_listed_link_is_dead(self):
        """Ссылка из перечня мертва — берём канонический адрес рекомендации.

        Он у МСЭ неизменен много лет и не зависит от того, как записана ссылка
        в перечне. Одна лишняя попытка на документ дешевле пропущенного
        документа, а на шести тысячах это заметная разница в улове.
        """
        готово, файлы, _ = self.прогнать("запасной")
        self.assertEqual(0, готово.returncode, "запасной адрес не сработал:\n" + готово.stdout)
        self.assertEqual(2, len(файлы), готово.stdout)
        self.assertIn("канонический адрес", готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_busy_server_is_waited_out_not_counted_as_failure(self):
        """«Слишком часто» и «занят» — просьба подождать, а не отказ.

        На выгрузке в шесть тысяч документов такие ответы приходят пачками, и
        если засчитывать их в неудачу, из библиотеки выпадают целые серии.
        """
        type(self).ЗАНЯТ_РАЗ = 0
        готово, файлы, _ = self.прогнать("занят")
        self.assertEqual(0, готово.returncode, "документ потерян из-за просьбы подождать:\n" + готово.stdout)
        self.assertEqual(2, len(файлы), готово.stdout)
        self.assertIn("уступаю", готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_another_language_is_taken_when_english_is_missing(self):
        """Английского издания нет — берём следующее по списку предпочтений.

        Сервер здесь отдаёт только русское издание и отвечает 404 на любое
        другое. Раньше такая рекомендация просто пропускалась.
        """
        готово, файлы, _ = self.прогнать("только-русский")
        self.assertEqual(0, готово.returncode, "издание на другом языке не взято:\n" + готово.stdout)
        self.assertEqual(2, len(файлы), готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_preferred_language_wins_over_document_order(self):
        """Из шести изданий берётся предпочтённое, а не первое в разметке.

        На странице МСЭ ссылки идут в своём порядке — арабское может стоять
        раньше английского. Брать первую попавшуюся значит класть в библиотеку
        документ на случайном языке.
        """
        готово, файлы, _ = self.прогнать("много-языков")
        self.assertEqual(0, готово.returncode, "взято не предпочтённое издание:\n" + готово.stdout)
        self.assertEqual(2, len(файлы), готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_rubbish_left_by_an_older_run_is_replaced(self):
        """Файл от прежней редакции скрипта не должен остаться навсегда.

        Прежняя редакция сохраняла html-страницу «доступ закрыт» под именем
        .pdf. Она тяжелее четырёх килобайт, а повторный запуск пропускал
        готовое по одному размеру — и такой файл жил бы в библиотеке вечно.
        """
        import threading
        from http.server import ThreadingHTTPServer

        сервер = ThreadingHTTPServer(("127.0.0.1", 0), self.обработчик("хорошо"))
        threading.Thread(target=сервер.serve_forever, daemon=True).start()
        каталог = tempfile.mkdtemp(prefix="itu-")
        self.addCleanup(shutil.rmtree, каталог, ignore_errors=True)
        мусор = Path(каталог) / "standards" / "itu-t" / "G" / "T-REC-G.703.pdf"
        мусор.parent.mkdir(parents=True)
        мусор.write_bytes(b"<html>" + b"z" * 9000 + b"</html>")
        try:
            готово = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(OFFLINE / "itu.ps1"),
                 "-BaseUrl", f"http://127.0.0.1:{сервер.server_address[1]}",
                 "-Series", "G", "-Destination", каталог, "-DelayMs", "0"],
                capture_output=True, text=True, timeout=180,
            )
        finally:
            сервер.shutdown()
            сервер.server_close()
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        self.assertTrue(
            мусор.read_bytes().startswith(b"%PDF-"),
            "html-страница от прежнего запуска осталась в библиотеке под именем .pdf",
        )

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_probe_pinpoints_the_step_that_fails(self):
        import threading
        from http.server import ThreadingHTTPServer

        сервер = ThreadingHTTPServer(("127.0.0.1", 0), self.обработчик("403"))
        threading.Thread(target=сервер.serve_forever, daemon=True).start()
        try:
            готово = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(OFFLINE / "itu.ps1"),
                 "-BaseUrl", f"http://127.0.0.1:{сервер.server_address[1]}", "-Probe"],
                capture_output=True, text=True, timeout=120,
            )
        finally:
            сервер.shutdown()
            сервер.server_close()
        # Перечень открывается, страница — нет: проверка обязана это показать.
        self.assertIn("перечень серии G", готово.stdout)
        self.assertIn("страница G.703", готово.stdout)
        self.assertIn("сервер отказал (403)", готово.stdout)
        self.assertEqual(1, готово.returncode, готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_probe_goes_all_the_way_to_the_file(self):
        """Страницы открываются, а вместо файла приезжает html.

        Раньше проверка кончалась на перечне и говорила «источник отвечает» —
        а выгрузка потом складывала эти html-страницы в библиотеку под именем
        .pdf. Проверка обязана дойти до самого файла.
        """
        import threading
        from http.server import ThreadingHTTPServer

        сервер = ThreadingHTTPServer(("127.0.0.1", 0), self.обработчик("не-pdf"))
        threading.Thread(target=сервер.serve_forever, daemon=True).start()
        try:
            готово = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(OFFLINE / "itu.ps1"),
                 "-BaseUrl", f"http://127.0.0.1:{сервер.server_address[1]}", "-Probe"],
                capture_output=True, text=True, timeout=120,
            )
        finally:
            сервер.shutdown()
            сервер.server_close()
        self.assertEqual(1, готово.returncode, "проверка одобрила источник, отдающий html вместо PDF:\n" + готово.stdout)
        self.assertIn("не скачался", готово.stdout)


class RfcLiveRunTests(unittest.TestCase):
    """Выгрузка RFC прогоняется целиком — настоящим PowerShell по настоящему HTTP.

    Здесь качаются тысячи файлов подряд, и цена ошибки та же: обрыв связи на
    четырёхтысячном номере не должен ни оборвать запуск, ни оставить в
    библиотеке огрызок вместо документа.
    """

    НОМЕРА = (791, 792, 793, 794, 795)
    УКАЗАТЕЛЬ = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rfc-index xmlns="http://www.rfc-editor.org/rfc-index">'
        + "".join(
            f"<rfc-entry><doc-id>RFC{n:04d}</doc-id><title>Проверка {n}</title>"
            f"<current-status>PROPOSED STANDARD</current-status></rfc-entry>"
            for n in НОМЕРА
        )
        + "</rfc-index>"
    )
    ТЕКСТ = ("Network Working Group\nRequest for Comments: 791\n\n" + "текст " * 300).encode()

    @classmethod
    def обработчик(cls):
        from http.server import BaseHTTPRequestHandler

        сам = cls

        class Обработчик(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def отдать(self, код, тело):
                self.send_response(код)
                self.send_header("Content-Length", str(len(тело)))
                self.end_headers()
                self.wfile.write(тело)

            def do_GET(self):
                if self.path.endswith("rfc-index.xml"):
                    return self.отдать(200, сам.УКАЗАТЕЛЬ.encode())
                if self.path.endswith("rfc794.txt"):
                    # Обрыв посреди передачи: заголовок обещает больше, чем придёт.
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    self.wfile.write(b"x" * 10)
                    self.wfile.flush()
                    self.connection.close()
                    return
                if self.path.endswith("rfc795.txt"):
                    return self.отдать(404, b"no")
                return self.отдать(200, сам.ТЕКСТ)

        return Обработчик

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_broken_transfer_neither_stops_the_run_nor_lands_in_the_library(self):
        import threading
        from http.server import ThreadingHTTPServer

        сервер = ThreadingHTTPServer(("127.0.0.1", 0), self.обработчик())
        threading.Thread(target=сервер.serve_forever, daemon=True).start()
        каталог = tempfile.mkdtemp(prefix="rfc-")
        self.addCleanup(shutil.rmtree, каталог, ignore_errors=True)
        try:
            готово = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(OFFLINE / "rfc.ps1"),
                 "-BaseUrl", f"http://127.0.0.1:{сервер.server_address[1]}",
                 "-Destination", каталог],
                capture_output=True, text=True, timeout=180,
            )
        finally:
            сервер.shutdown()
            сервер.server_close()

        файлы = sorted((Path(каталог) / "standards" / "rfc").glob("*.txt"))
        имена = [f.name for f in файлы]
        # Обрыв на 794 не остановил выгрузку: 791-793 доехали.
        self.assertEqual(["rfc791.txt", "rfc792.txt", "rfc793.txt"], имена, готово.stdout)
        # И огрызок не сохранён: иначе в библиотеке появился бы RFC из десяти байт.
        self.assertNotIn("rfc794.txt", имена, "обрезанный файл сохранён как документ")
        self.assertIn("связь оборвалась посреди файла", готово.stdout)
        # 795 не публиковался — это норма, а не ошибка связи.
        self.assertIn("номеров без текста: 1", готово.stdout)


class ItuSelfTestTests(unittest.TestCase):
    """Ключ -SelfTest: проверка скрипта на месте, без выхода в сеть.

    Выгрузка МСЭ идёт часами, и узнать, что скрипт несовместим с этой версией
    PowerShell, лучше за десять секунд до начала, чем через час после. Именно
    так однажды и вышло: под Windows PowerShell 5.1 любая строка, написанная
    curl в поток ошибок, обрывала запуск — и выяснилось это после того, как
    отдел прочитал перечни всех 25 серий.

    Проверка обязана уметь две вещи: сказать «исправен», когда всё цело, и
    честно упасть, когда нет. Вторая важнее: молчаливо одобряющая проверка
    хуже отсутствующей.
    """

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_self_test_passes_on_a_healthy_machine(self):
        готово = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(OFFLINE / "itu.ps1"), "-SelfTest"],
            capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(0, готово.returncode, готово.stdout + готово.stderr)
        self.assertIn("самопроверка пройдена", готово.stdout)
        # Ни одна проверка не должна молча выпасть из прогона.
        self.assertNotIn("  X  ", готово.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "нужен PowerShell")
    def test_self_test_fails_loudly_when_curl_is_missing(self):
        # Без curl выгрузка невозможна, и проверка обязана это сказать, а не
        # отрапортовать «исправен». Прячем curl, оставив путь к самому pwsh.
        окружение = dict(os.environ)
        окружение["PATH"] = str(Path(shutil.which("pwsh")).parent)
        подстава = tempfile.mkdtemp(prefix="bez-curl-")
        self.addCleanup(shutil.rmtree, подстава, ignore_errors=True)
        окружение["PATH"] = подстава + os.pathsep + окружение["PATH"]
        # В подставном каталоге curl отсутствует; если он есть в каталоге
        # pwsh, проверка потеряет смысл — тогда тест пропускаем.
        if shutil.which("curl", path=окружение["PATH"]):
            self.skipTest("curl лежит рядом с pwsh — спрятать его нечем")
        готово = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(OFFLINE / "itu.ps1"), "-SelfTest"],
            capture_output=True, text=True, timeout=180, env=окружение,
        )
        self.assertEqual(1, готово.returncode, "сломанная машина одобрена:\n" + готово.stdout)
        self.assertIn("самопроверка не пройдена", готово.stdout)
        self.assertIn("выгрузку запускать нельзя", готово.stdout)
