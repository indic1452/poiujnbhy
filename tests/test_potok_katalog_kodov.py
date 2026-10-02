"""Каталог кодов (data/kody_katalog.json): схема каждой записи, файлы источников, программная сверка.

Сверка значений каталога своим расчётом, независимо от скриптов сборщиков:
- CRC с параметрами Rocksoft (width, poly, init, refin, refout, xorout) — check строки «123456789»;
- БЧХ, Хэмминг, квадратично-вычетные — g(x) делит x^n + 1, deg g = n − k, hex = запись x^…;
- примитивные многочлены — порядок x по модулю p равен 2^m − 1 (все степени до 64);
- свёрточные 1/n — свободное расстояние по решётке (K ≤ 13) равно dfree записи.
Файлы: положенные — на месте и с sha256 описи; «ссылкой» — в описи с URL (или причиной) и sha256.
"""

import hashlib
import json
import random
import re
import tomllib
import unittest
from heapq import heappop, heappush
from math import gcd
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.potok import katalog_kodov as кк

КОРЕНЬ = Path(__file__).resolve().parents[1]
ОПИСЬ = КОРЕНЬ / "istochniki" / "katalog" / "spisok.json"


def опись() -> dict[str, dict]:
    return {x["путь"]: x for x in json.loads(ОПИСЬ.read_text(encoding="utf-8"))["файлы"]}


# ---------- арифметика GF(2)[x] ----------

def остаток(a: int, b: int) -> int:
    db = b.bit_length()
    while a.bit_length() >= db:
        a ^= b << (a.bit_length() - db)
    return a


def умн_по_модулю(a: int, b: int, p: int) -> int:
    r = 0
    стар = p.bit_length()
    while b:
        if b & 1:
            r ^= a
        b >>= 1
        a <<= 1
        if a.bit_length() == стар:
            a ^= p
    return r


def x_в_степени(e: int, p: int) -> int:
    r, осн = 1, остаток(2, p)
    while e:
        if e & 1:
            r = умн_по_модулю(r, осн, p)
        осн = умн_по_модулю(осн, осн, p)
        e >>= 1
    return r


def простое(n: int) -> bool:
    if n < 2:
        return False
    for q in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % q == 0:
            return n == q
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):   # детерминированно до 3,3·10^24
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def ро_полларда(n: int, сл: random.Random) -> int:
    if n % 2 == 0:
        return 2
    while True:
        c, y, m, g, r, q = сл.randrange(1, n), сл.randrange(1, n), 128, 1, 1, 1
        while g == 1:
            x = y
            for _ in range(r):
                y = (y * y + c) % n
            k = 0
            while k < r and g == 1:
                ys = y
                for _ in range(min(m, r - k)):
                    y = (y * y + c) % n
                    q = q * abs(x - y) % n
                g = gcd(q, n)
                k += m
            r *= 2
        if g == n:
            g = 1
            while g == 1:
                ys = (ys * ys + c) % n
                g = gcd(abs(x - ys), n)
        if g != n:
            return g


def простые_делители(n: int, сл: random.Random | None = None) -> set[int]:
    сл = сл or random.Random(1)
    if n == 1:
        return set()
    if простое(n):
        return {n}
    for q in range(2, 1000):
        if n % q == 0:
            return {q} | простые_делители(_без(n, q), сл)
    d = ро_полларда(n, сл)
    return простые_делители(d, сл) | простые_делители(n // d, сл)


def _без(n: int, q: int) -> int:
    while n % q == 0:
        n //= q
    return n


def примитивен(p: int) -> bool:
    m = p.bit_length() - 1
    if m < 1 or not p & 1 and m > 1:
        return False
    if m == 1:
        return p == 0b11
    N = (1 << m) - 1
    return x_в_степени(N, p) == 1 and all(x_в_степени(N // q, p) != 1 for q in простые_делители(N))


# ---------- CRC и свёрточные ----------

def crc(п: dict, данные: bytes = b"123456789") -> int:
    w, poly, reg = п["width"], int(п["poly"], 16), int(п["init"], 16)
    старший, маска = 1 << (w - 1), (1 << w) - 1
    for байт in данные:
        if п["refin"]:
            байт = int(f"{байт:08b}"[::-1], 2)
        for i in range(7, -1, -1):
            обр = bool(reg & старший) ^ ((байт >> i) & 1)
            reg = (reg << 1) & маска
            if обр:
                reg ^= poly
    if п["refout"]:
        reg = int(f"{reg:0{w}b}"[::-1], 2)
    return reg ^ int(п["xorout"], 16)


def dfree(порождающие: list[int]) -> int:
    """Свободное расстояние кода 1/n без обратной связи: кратчайший по весу путь 0 → … → 0 (Дейкстра)."""
    m = max(g.bit_length() for g in порождающие) - 1

    def шаг(с: int, бит: int) -> tuple[int, int]:
        рег = (бит << m) | с
        return рег >> 1, sum(bin(рег & g).count("1") & 1 for g in порождающие)

    с0, в0 = шаг(0, 1)
    расст, очередь = {с0: в0}, [(в0, с0)]
    while очередь:
        в, с = heappop(очередь)
        if с == 0:
            return в
        if расст.get(с, 1 << 30) < в:
            continue
        for бит in (0, 1):
            с2, дв = шаг(с, бит)
            if в + дв < расст.get(с2, 1 << 30):
                расст[с2] = в + дв
                heappush(очередь, (в + дв, с2))
    raise AssertionError("путь в нулевое состояние не найден")


def многочлены_свёрточного(текст: str) -> list[int]:
    if re.search(r"[xX]\^?\d", текст):
        t = текст.translate(кк._СТЕПЕНИ)
        return [кк._степени_в_число(m.group(0)) for m in кк._МНОГОЧЛЕН.finditer(t)]
    голова = текст.split("(")[0]
    return [int(x, 8) for x in re.findall(r"(?<![\w.])[0-7]+(?![\w.])", голова)]


class Схема(unittest.TestCase):
    def test_каталог_и_поля(self):
        кат = кк.каталог()
        self.assertEqual(кат["записей"], len(кат["записи"]))
        self.assertGreater(len(кат["записи"]), 1700)
        self.assertEqual(set(кат["области"]), set(кк.ОБЛАСТИ))
        допустимые = set(кк.ПОЛЯ) | set(кк.НЕОБЯЗАТЕЛЬНЫЕ)
        иды = set()
        for z in кат["записи"]:
            with self.subTest(z.get("ид")):
                for поле in кк.ПОЛЯ:
                    self.assertIn(поле, z)
                self.assertLessEqual(set(z), допустимые)
                self.assertNotIn(z["ид"], иды)
                иды.add(z["ид"])
                self.assertRegex(z["ид"], rf"^({'|'.join(кк.ОБЛАСТИ)})-[0-9a-f]{{8}}$")
                self.assertIn(z["область"], кк.ОБЛАСТИ)
                self.assertIn(z["в проекте"], кк.В_ПРОЕКТЕ)
                for поле in ("имя", "семейство", "вид", "класс", "где применяется", "проверка"):
                    self.assertIsInstance(z[поле], str)
                    self.assertTrue(z[поле].strip(), поле)
                self.assertIsInstance(z["параметры"], dict)
                # без источника — только «не найдено», где открытого документа нет вовсе (HDD/SSD, MoCA, IEEE 1901)
                self.assertTrue(z["источник"] or z["класс"] == "не найдено", "у записи нет источника")
                for с in z["источник"]:
                    self.assertLessEqual(set(с), {"файл", "страница|строки", "url", "что", "положен"})
                    self.assertTrue(с.get("файл") or с.get("url"), с)
                    if "файл" in с:
                        self.assertIsInstance(с["положен"], bool)
                for о in z.get("также в", []):
                    self.assertIn(о, кк.ОБЛАСТИ)
        for z in кат["записи"]:
            for и in z.get("см. также", []):
                self.assertIn(и, иды)
            for с in z.get("слиты", []):
                self.assertNotIn(с["ид"], иды)
                self.assertIs(кк.запись(с["ид"]), z)

    def test_сводка_областей_сходится(self):
        кат = кк.каталог()
        for обл, св in кат["области"].items():
            зз = кк.записи(обл)
            self.assertEqual(св["записей"], len(зз))
            self.assertEqual(sum(св["в проекте"].values()), len(зз))
        сводка = кк.сводка()
        self.assertEqual(sum(сводка["по областям"].values()), сводка["записей"])

    def test_пути_относительные(self):
        for z in кк.записи():
            for с in z["источник"]:
                f = с.get("файл")
                if f:
                    self.assertTrue(f.startswith("istochniki/katalog/"), f)
                    self.assertNotIn("..", f)
            текст = json.dumps(z["параметры"], ensure_ascii=False)
            self.assertNotIn("/tmp/", текст)
            self.assertNotIn("scratchpad", текст)


class Файлы(unittest.TestCase):
    def test_файлы_источников(self):
        о = опись()
        for z in кк.записи():
            for с in z["источник"]:
                f = с.get("файл")
                if not f:
                    continue
                with self.subTest(z["ид"], файл=f):
                    p = КОРЕНЬ / f
                    if с["положен"]:
                        self.assertTrue(p.exists(), f)
                    elif f in о:
                        x = о[f]
                        self.assertFalse(x["положен"])
                        self.assertRegex(x["sha256"], r"^[0-9a-f]{64}$")
                        self.assertTrue(x.get("url") or с.get("url") or x.get("причина"))
                    else:   # папка: что-то из неё в описи
                        self.assertTrue(any(п.startswith(f.rstrip("/") + "/") for п in о), f)

    def test_опись_и_sha256_положенных(self):
        о = опись()
        self.assertGreater(len(о), 3000)
        for путь, x in о.items():
            self.assertTrue(путь.startswith("istochniki/katalog/"), путь)
            self.assertRegex(x["sha256"], r"^[0-9a-f]{64}$")
            p = КОРЕНЬ / путь
            if not x["положен"]:
                self.assertIn("причина", x)
                continue
            with self.subTest(путь):
                self.assertTrue(p.is_file(), путь)
                self.assertEqual(p.stat().st_size, x["размер"])
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    for кусок in iter(lambda: f.read(1 << 20), b""):
                        h.update(кусок)
                self.assertEqual(h.hexdigest(), x["sha256"])
        self.assertLessEqual(max((x["размер"] for x in о.values() if x["положен"]), default=0), 40_000_000)

    def test_таблицы_на_месте_и_читаются(self):
        упомянуто = 0
        for z in кк.записи():
            for путь in кк.таблицы_записи(z):
                if any(c in путь for c in "*<…{"):
                    continue
                упомянуто += 1
                p = кк.путь_таблицы(путь.rstrip("."))
                self.assertTrue(p.exists(), (z["ид"], путь))
        self.assertGreater(упомянуто, 100)
        for p in (кк.ДАННЫЕ / "kody").rglob("*.json"):
            with self.subTest(p.name):
                json.loads(p.read_text(encoding="utf-8"))

    def test_не_найдено(self):
        нн = json.loads((КОРЕНЬ / "istochniki/katalog/ne_naydeno.json").read_text(encoding="utf-8"))
        self.assertEqual(нн["всего"], len(нн["позиции"]))
        for x in нн["позиции"]:
            self.assertIn(x["область"], кк.ОБЛАСТИ)
            self.assertTrue(x["что"] and x["статус"])

    def test_источники_не_в_колесе(self):
        pp = tomllib.loads((КОРЕНЬ / "pyproject.toml").read_text(encoding="utf-8"))
        st = pp["tool"]["setuptools"]
        self.assertEqual(st["packages"]["find"]["where"], ["src"])
        self.assertTrue(all(x.startswith("reportgen") for x in st["packages"]["find"]["include"]))
        данные = st["package-data"]["reportgen.potok"]
        self.assertIn("data/*.json", данные)
        self.assertTrue(any(x.startswith("data/kody/") for x in данные), данные)
        self.assertFalse(any("istochniki" in x for v in st["package-data"].values() for x in v))
        manifest = КОРЕНЬ / "MANIFEST.in"
        if manifest.exists():
            self.assertNotRegex(manifest.read_text(encoding="utf-8"), r"(?m)^\s*(graft|recursive-include)\s+istochniki")


class Сверка(unittest.TestCase):
    def test_crc_check_rocksoft(self):
        записи = [z for z in кк.записи() if all(k in z["параметры"] for k in
                                                   ("width", "poly", "init", "refin", "refout", "xorout", "check"))]
        self.assertGreaterEqual(len(записи), 110)
        for z in записи:
            with self.subTest(z["имя"]):
                self.assertEqual(crc(z["параметры"]), int(z["параметры"]["check"], 16))

    def test_crc_известные_значения(self):
        # опорные значения не из каталога: CRC-32/ISO-HDLC (zlib) и CRC-16/XMODEM строки «123456789»
        п32 = {"width": 32, "poly": "0x04C11DB7", "init": "0xFFFFFFFF", "refin": True, "refout": True,
               "xorout": "0xFFFFFFFF"}
        self.assertEqual(crc(п32), 0xCBF43926)
        import zlib  # noqa: PLC0415
        self.assertEqual(crc(п32, b"reportgen"), zlib.crc32(b"reportgen"))
        self.assertEqual(crc({"width": 16, "poly": "0x1021", "init": "0x0", "refin": False, "refout": False,
                              "xorout": "0x0"}), 0x31C3)

    def test_бчх_делит_xn_плюс_1(self):
        записи = [z for z in кк.записи() if "g(x) hex" in z["параметры"]]
        self.assertGreaterEqual(len(записи), 240)
        for z in записи:
            with self.subTest(z["имя"]):
                n, k = кк.n_k(z)[0]
                g = int(z["параметры"]["g(x) hex"], 16)
                self.assertEqual(g.bit_length() - 1, n - k)
                self.assertEqual(остаток((1 << n) | 1, g), 0)
                if "g(x) восьмеричная" in z["параметры"]:
                    self.assertEqual(int(z["параметры"]["g(x) восьмеричная"], 8), g)
                запись = z["параметры"].get("g(x) запись", "")
                if "^" in запись and "…" not in запись and "см." not in запись:
                    self.assertEqual(кк.многочлен_в_числа(запись), {g})

    def test_хэмминг_и_квадратично_вычетные(self):
        проверено = 0
        for z in кк.записи():
            if z["семейство"] not in ("Коды Хэмминга", "Квадратично-вычетные коды"):
                continue
            п = z["параметры"]
            текст = п.get("g(x)") or п.get("многочлен")
            m = re.search(r"\(0x([0-9A-Fa-f]+)\)", текст or "")
            if not m or not кк.n_k(z):
                continue
            with self.subTest(z["имя"]):
                n, k = кк.n_k(z)[0]
                g = int(m.group(1), 16)
                self.assertEqual(кк.многочлен_в_числа(текст.split("(")[0]), {g})
                self.assertEqual(g.bit_length() - 1, n - k)
                self.assertEqual(остаток((1 << n) | 1, g), 0)
                проверено += 1
        self.assertGreaterEqual(проверено, 16)

    def test_примитивные_многочлены(self):
        записи = [z for z in кк.записи() if z["класс"] == "примитивный многочлен" and "hex" in z["параметры"]]
        self.assertGreaterEqual(len(записи), 60)
        for z in записи:
            with self.subTest(z["имя"]):
                p = int(z["параметры"]["hex"], 16)
                self.assertTrue(примитивен(p))
                if "многочлен" in z["параметры"]:
                    self.assertEqual(кк.многочлен_в_числа(z["параметры"]["многочлен"]), {p})
        self.assertFalse(примитивен(0b10101))        # x^4+x^2+1 = (x^2+x+1)^2
        self.assertFalse(примитивен(0b11111))        # x^4+x^3+x^2+x+1: порядок 5
        self.assertTrue(примитивен((1 << 64) | 0b11011))   # x^64+x^4+x^3+x+1

    def test_свёрточные_dfree(self):
        проверено = 0
        for z in кк.записи():
            п = z["параметры"]
            if not all(k in п for k in ("K", "многочлены", "dfree", "n,k")):
                continue
            m = re.fullmatch(r"\s*(\d+)\s*,\s*1\s*", str(п["n,k"]))
            if not m or not isinstance(п["K"], int) or not isinstance(п["dfree"], int) or п["K"] > 13:
                continue
            gs = многочлены_свёрточного(п["многочлены"])
            if len(gs) != int(m.group(1)):
                continue
            while all(g % 2 == 0 for g in gs):      # запись с выравниванием влево
                gs = [g >> 1 for g in gs]
            with self.subTest(z["имя"]):
                self.assertLessEqual(max(g.bit_length() for g in gs), п["K"])
                self.assertEqual(dfree(gs), п["dfree"])
            проверено += 1
        self.assertGreaterEqual(проверено, 250)
        self.assertEqual(dfree([0o171, 0o133]), 10)    # опора не из каталога: K=7 1/2

    def test_параметры_dvb_t_и_ccsds(self):
        z = кк.найти(n=204, k=188, семейство="DVB-T")[0]
        self.assertIn("kosmos", z["также в"])           # DVB-S слит
        self.assertTrue(кк.найти(многочлен="1 + X^14 + X^15", область="efir"))
        ccsds = кк.найти("CCSDS TM свёрточный", область="kosmos")
        self.assertTrue(ccsds)
        self.assertIn("171", json.dumps(ccsds[0]["параметры"], ensure_ascii=False))


class Модуль(unittest.TestCase):
    def test_поиск(self):
        crc16 = кк.найти(многочлен="x^16+x^12+x^5+1")
        self.assertTrue(any(z["имя"] == "CRC-16/IBM-SDLC" for z in crc16))
        self.assertTrue(any(z["область"] == "kosmos" for z in crc16))
        по_hex = {z["ид"] for z in кк.найти(многочлен="0x1021")}
        self.assertTrue(по_hex & {z["ид"] for z in crc16})
        ldpc = кк.найти("DVB-T2", класс="LDPC")
        self.assertTrue(ldpc and all(z["класс"] == "LDPC" for z in ldpc))
        self.assertEqual(len(кк.найти(класс="БЧХ", предел=5)), 5)
        self.assertTrue(all(z["в проекте"] == "нет" for z in кк.найти(в_проекте="нет")))
        with self.assertRaises(ValueError):
            кк.найти(многочлен="не многочлен")

    def test_многочлен_в_числа(self):
        self.assertEqual(кк.многочлен_в_числа("x^16 + x^12 + x^5 + 1"), {0x11021})
        self.assertEqual(кк.многочлен_в_числа("1 + D^3 + D^4"), {0b11001})
        self.assertEqual(кк.многочлен_в_числа("x¹⁶+x¹²+x⁵+1"), {0x11021})
        self.assertEqual(кк.многочлен_в_числа("0x1021"), {0x1021, 0x11021})

    def test_n_k_и_таблица(self):
        z = кк.найти("GSM xCCH")[0]
        таблицы = кк.таблицы_записи(z)
        self.assertTrue(таблицы)
        self.assertIsInstance(кк.таблица(таблицы[0]), (dict, list))
        with self.assertRaises(ValueError):
            кк.путь_таблицы("../../../etc/passwd")
        self.assertIn((204, 188), кк.n_k({"имя": "РС(204,188)", "параметры": {}}))
        self.assertEqual(кк.n_k({"имя": "x", "параметры": {"n,k": "2048, 1723 (325)"}}), [(2048, 1723)])

    def test_семейства_и_файл_источника(self):
        сем = кк.семейства("obshchie")
        self.assertGreater(len(сем), 20)
        f = next((с["файл"] for z in кк.записи() for с in z["источник"] if с.get("положен")), None)
        if f:
            self.assertIsNotNone(кк.файл_источника(f))
        # Первоисточники в репозитории не лежат (только опись): несуществующий файл — None, опись — путь.
        self.assertIsNone(кк.файл_источника("istochniki/katalog/нет-такого.pdf"))
        self.assertIsNotNone(кк.файл_источника("istochniki/katalog/spisok.json"))

    def test_в_репозитории_только_опись_источников(self):
        """Первоисточники (PDF, HTML, картинки, копии кода) в репозиторий не кладутся — только опись
        (README с адресами и sha256, spisok.json, скрипт докачки); файлы — в истории git и по URL."""
        import shutil  # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        if not shutil.which("git") or not (КОРЕНЬ / ".git").exists():
            self.skipTest("нет git")
        файлы = subprocess.run(["git", "ls-files", "istochniki"], cwd=КОРЕНЬ, capture_output=True, text=True,
                               check=True).stdout.split()
        лишние = [f for f in файлы if not f.endswith((".md", ".json", ".py"))]
        self.assertEqual([], лишние)

    def test_командная_строка(self):
        import contextlib  # noqa: PLC0415
        import io  # noqa: PLC0415
        вывод = io.StringIO()
        with contextlib.redirect_stdout(вывод):
            self.assertEqual(кк.main(["DVB-S2", "--класс", "LDPC"]), 0)
        self.assertIn("найдено:", вывод.getvalue())


if __name__ == "__main__":
    unittest.main()
