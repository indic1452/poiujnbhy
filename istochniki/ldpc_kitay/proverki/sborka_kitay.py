"""Сборка src/reportgen/potok/data/ldpc_kitay.json из первоисточников КНР — с проверками (assert).

Запуск из корня репозитория: ``python3 istochniki/ldpc_kitay/proverki/sborka_kitay.py``.
Ничего не качает: читает только файлы из istochniki/ldpc_kitay/.

- ABS-S (先进卫星广播系统): базовые матрицы 10 кодов (n = 15360, циркулянт 32 × 32) — приложения
  «附表A–J» патента CN101150730 (Академия радиовещания ГУРТ КНР, изобретатели Sun Fengwen,
  Yang Ming, Zhang Juntan, Shi Yuhai; то же семейство — US8689092 Availink). Текст таблиц —
  распознанный Google Patents, две независимые копии: заявка CN101150730A и патент CN101150730B.
  OCR разрывает числа («4 9» вместо «49»): строки таблицы разбираются перебором склеек с
  ограничениями (тройки «строка столбец сдвиг», строки не убывают и растут не более чем на 1,
  столбцы в строке растут, столбец < 480, сдвиг < 32); берётся вариант, общий для обеих копий.
  Где копии расходятся — решено сверкой со сканом CN101150730B (РЕШЕНО_ПО_СКАНУ) или
  строением (РЕШЕНО_ПО_СТРОЕНИЮ). Проверки: нет 4-циклов, распределение степеней бит совпадает
  с п. 1 формулы изобретения (столбец за столбцом — число бит каждой степени).
- CMMB (GY/T 220.1-2006, прил. C и D): строки H (номера единиц) и вектор отображения бит
  COL_ORDER (формула 10: c[COL_ORDER(i)] = p[i] при i ≤ 9215 − K, иначе s[i + K − 9216]);
  проверки: размеры, веса строк, квазицикличность (строка r + 18 (9) = строка r, сдвинутая
  на 36 по модулю 9216), COL_ORDER — перестановка.
- DTMB (GB 20600-2006): alist трёх кодов (7493, 3048/4572/6096) из dtmb-sdr (MIT, выведены
  автором из стандарта); проверки: размеры, квазицикличность с циркулянтом 127, ранг.
- BeiDou (BDS-SIS-ICD B1C 1.0, B2a 1.0, PPP-B2b 1.0): 64-ричные коды LDPC — таблицы
  H index/element из текста ICD, сверены с PocketSDR (BSD-2, src/sdr_ldpc.c) — совпали.
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from collections import Counter
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parents[3]
ИСТ = КОРЕНЬ / "istochniki" / "ldpc_kitay"
sys.path.insert(0, str(КОРЕНЬ / "src"))
from reportgen.potok import ldpc  # noqa: E402

ВЫХОД = КОРЕНЬ / "src" / "reportgen" / "potok" / "data" / "ldpc_kitay.json"
sys.setrecursionlimit(20000)

# ---------------------------------------------------------------- ABS-S

СКОРОСТИ_ABS = dict(zip("ABCDEFGHIJ", ["1/4", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "13/15", "9/10"], strict=True))
#: Распределение степеней бит (число бит степени d по возрастанию d), п. 1 формулы CN101150730B.
СТЕПЕНИ_ABS = {
    "1/4": [11264, 256, 3840], "2/5": [8960, 256, 4608, 1536], "1/2": [7424, 2304, 3840, 1792],
    "3/5": [5888, 3328, 4352, 1792], "2/3": [4864, 4096, 4864, 1536], "3/4": [3584, 4608, 5888, 1280],
    "4/5": [2816, 5888, 5376, 1280], "5/6": [2304, 4608, 6912, 1536], "13/15": [1792, 5632, 6912, 1024],
    "9/10": [1280, 3584, 10496]}
#: Строки, где копии A и B дают разные, но по строению допустимые тройки: значение прочитано
#: со скана CN101150730B (istochniki/ldpc_kitay/abs-s/CN101150730B.pdf) глазами.
РЕШЕНО_ПО_СКАНУ = {
    ("3/5", 285): (174, 15, 6), ("3/5", 303): (184, 208, 16), ("3/4", 253): (97, 131, 28),
    ("5/6", 121): (30, 66, 16), ("9/10", 252): (42, 29, 18), ("5/6", 0): (0, 40, 16),
    ("5/6", 290): (72, 258, 6)}
#: Строки, где на скане пробел напечатан не там («34 20» вместо «342 0»): выбран вариант, при
#: котором все 120 (для 1/4) информационных столбцов — степени 6 и сохраняется пара нулевых
#: сдвигов в столбцах c и c + 8, как во всех соседних строках.
РЕШЕНО_ПО_СТРОЕНИЮ = {
    ("1/4", 165): (247, 8, 30), ("1/4", 228): (340, 342, 0), ("1/4", 235): (350, 441, 0),
    ("5/6", 80): (20, 24, 25)}


def _таблицы(путь: Path) -> dict[str, str]:
    текст = путь.read_text(encoding="utf-8")
    текст = текст[текст.index("附表A："):]
    части = re.split(r"附表([A-J])：[^\n]*", текст)
    итог = {}
    for i in range(1, len(части), 2):
        итог.setdefault(части[i], части[i + 1])
    return итог


def _варианты(строка: str, M: int, последняя: bool) -> set[tuple]:
    """Все допустимые разборы строки таблицы в тройки (склейки по одиночным пробелам)."""
    лексемы = [(m.group(2), len(m.group(1)) == 1 and i > 0)
               for i, m in enumerate(re.finditer(r"(\s*)(\d+)", строка))]
    разборы: list[list[str]] = []

    def шаг(i: int, накоплено: list[str]) -> None:
        if i == len(лексемы):
            разборы.append(накоплено)
            return
        s, j = лексемы[i][0], i + 1
        while True:
            шаг(j, накоплено + [s])
            if j < len(лексемы) and лексемы[j][1] and len(s) < 3:
                s += лексемы[j][0]
                j += 1
            else:
                break
    шаг(0, [])
    итог = set()
    for ч in разборы:
        if len(ч) % 3 or (len(ч) != 18 and not последняя) or any(len(x) > 1 and x[0] == "0" for x in ч):
            continue
        v = list(map(int, ч))
        тр = tuple(tuple(v[i:i + 3]) for i in range(0, len(v), 3))
        if any(r >= M or c >= 480 or k >= 32 for r, c, k in тр):
            continue
        if any(r2 < r1 or r2 > r1 + 1 or (r2 == r1 and c2 <= c1)
               for (r1, c1, _), (r2, c2, _) in zip(тр, тр[1:], strict=False)):
            continue
        итог.add(тр)
    return итог


def циклы4(тройки) -> int:
    """4-циклы поднятого графа: B[i,a] − B[i,b] + B[j,b] − B[j,a] ≡ 0 (mod 32)."""
    строки: dict[int, dict[int, int]] = {}
    for r, c, k in тройки:
        строки.setdefault(r, {})[c] = k
    сп = list(строки.values())
    n = 0
    for i in range(len(сп)):
        for j in range(i + 1, len(сп)):
            for a, b in itertools.combinations(sorted(set(сп[i]) & set(сп[j])), 2):
                n += (сп[i][a] - сп[i][b] + сп[j][b] - сп[j][a]) % 32 == 0
    return n


def abs_s() -> dict[str, dict]:
    копии = [_таблицы(ИСТ / "abs-s" / "CN101150730B_opisanie.txt"),
             _таблицы(ИСТ / "abs-s" / "CN101150730A_opisanie.txt")]
    итог = {}
    for буква, R in СКОРОСТИ_ABS.items():
        a, b = map(int, R.split("/"))
        M = 480 * (b - a) // b
        строки = [[s for s in к[буква].split("\n") if re.search(r"\d", s)] for к in копии]
        assert len(строки[0]) == len(строки[1]), R
        тройки: list[tuple] = []
        общих = одной = решено = 0
        for n in range(len(строки[0])):
            вар = [_варианты(с[n], M, n == len(с) - 1) for с in строки]
            общие = вар[0] & вар[1]
            if len(общие) == 1:
                тройки += next(iter(общие))
                общих += 1
                continue
            кандидаты = вар[0] | вар[1]
            assert кандидаты, f"{R}, строка {n}: ни одна копия не разбирается"
            if len(кандидаты) == 1:
                тройки += next(iter(кандидаты))
                одной += 1
                continue
            нужное = РЕШЕНО_ПО_СКАНУ.get((R, n)) or РЕШЕНО_ПО_СТРОЕНИЮ[(R, n)]
            годные = [к for к in кандидаты if нужное in к]
            assert len(годные) == 1, (R, n, кандидаты)
            тройки += годные[0]
            решено += 1
        assert sorted({r for r, _, _ in тройки}) == list(range(M)), R
        ст = Counter(Counter(c for _, c, _ in тройки).values())
        assert [ст[d] * 32 for d in sorted(ст)] == СТЕПЕНИ_ABS[R], (R, ст)
        assert циклы4(тройки) == 0, R
        k = 15360 * a // b
        print(f"ABS-S {R}: {len(тройки)} троек, строк таблицы {len(строки[0])}: общих {общих}, "
              f"из одной копии {одной}, решено {решено}; 4-циклов нет, степени = п. 1 формулы")
        итог[f"abs-s-15360-{k}"] = {
            "семейство": "КНР: ABS-S", "n": 15360, "k": k, "скорость": R, "Z": 32, "столбцов": 480,
            "тройки": [list(t) for t in тройки],
            "откуда": f"ABS-S (GD/J 043—2011), QC-LDPC 15360, скорость {R}: базовая матрица {M} × 480, "
                      f"Z = 32 — прил. {буква} патента CN101150730 (Академия радиовещания ГУРТ; US8689092)",
            **({"примечание": "в стандарт ABS-S, по открытым статьям, вошли 8 скоростей 1/2…9/10; "
                              "1/4 и 2/5 — только в патенте"} if R in ("1/4", "2/5") else {})}
    return итог


# ---------------------------------------------------------------- CMMB

def cmmb() -> dict[str, dict]:
    текст = (ИСТ / "cmmb" / "GY_T_220.1-2006.txt").read_text(encoding="utf-8")
    текст = re.sub(r"=====PAGE \d+\nGY/T 220\.1—2006 \n\d+ \n", "\n", текст)

    def между(а: str, б: str) -> str:
        i = текст.index(а)
        return текст[i + len(а):текст.index(б, i)]
    итог = {}
    for R, k, w, период, dH, dC in (
            ("1/2", 4608, 6, 18, между("D1 1/2 LDPC码校验矩阵H", "D2 3/4 LDPC码校验矩阵H"),
             между("C1 码率R=1/2的LDPC码字比特映射向量", "C2 码率R=3/4的LDPC码字比特映射向量")),
            ("3/4", 6912, 12, 9, между("D2 3/4 LDPC码校验矩阵H", "附 录 E"),
             между("C2 码率R=3/4的LDPC码字比特映射向量", "附 录 D"))):
        m = 9216 - k
        строки = {}
        for мм in re.finditer(r"(\d+):((?:\s+\d+(?!\d*:))+)", dH):
            r = int(мм.group(1))
            assert r not in строки, (R, r)
            строки[r] = [int(x) for x in мм.group(2).split()]
        assert sorted(строки) == list(range(m)), R
        H = [строки[r] for r in range(m)]
        assert all(len(с) == w and с == sorted(с) and с[-1] < 9216 for с in H), R
        # Квазицикличность: строка r + период — строка r, сдвинутая на 36 (9216 / 36 = 256 групп).
        assert all(sorted((c + 36) % 9216 for c in H[r]) == H[r + период] for r in range(m - период)), R
        порядок = [int(x) for x in re.findall(r"\d+", dC.split("ORDER i", 1)[1])]
        assert sorted(порядок) == list(range(9216)), R
        print(f"CMMB {R}: H {m} × 9216, вес строк {w}, квазициклична (период {период} строк, сдвиг 36); "
              f"COL_ORDER — перестановка 9216")
        итог[f"cmmb-9216-{k}"] = {
            "семейство": "КНР: CMMB", "n": 9216, "k": k, "скорость": R, "строки": H,
            "данные": порядок[m:],
            "откуда": f"CMMB (GY/T 220.1-2006), LDPC (9216, {k}): H — прил. D, проверочные — на "
                      f"COL_ORDER(0…{m - 1}), данные — на COL_ORDER({m}…9215) (прил. C, формула 10)"}
    return итог


# ---------------------------------------------------------------- DTMB

def dtmb() -> dict[str, dict]:
    итог = {}
    for номер, k in ((1, 3048), (2, 4572), (3, 6096)):
        # alist Маккея (столбцы дополнены нулями до наибольшего веса) — разбор проекта.
        м = ldpc.из_alist((ИСТ / "dtmb" / f"dtmb_ldpc_rate{номер}.alist").read_text())
        n, m = м.n, м.m
        assert (n, m) == (7493, 7493 - k)
        строки = [с.tolist() for с in м.строки]
        # Квазицикличность: циркулянты 127 × 127 — строка r + 1 внутри блока = строка r, сдвинутая на 1.
        for r in range(m):
            if (r + 1) % 127:
                assert sorted((c // 127) * 127 + (c % 127 + 1) % 127 for c in строки[r]) == sorted(строки[r + 1]), r
        print(f"DTMB ({n}, {k}): H {m} × {n} из alist dtmb-sdr, квазициклична (Z = 127)")
        итог[f"dtmb-7493-{k}"] = {
            "семейство": "КНР: DTMB", "n": 7493, "k": k, "скорость": {1: "0,4", 2: "0,6", 3: "0,8"}[номер],
            "строки": строки, "выколоты": "0-4",
            "данные_с": m,
            "откуда": f"DTMB (GB 20600-2006), LDPC (7493, {k}), в потоке 7488 бит: первые 5 проверочных "
                      f"не передаются, данные — последние {k}; H — alist из dtmb-sdr (MIT, по стандарту)"}
    return итог


# ---------------------------------------------------------------- BeiDou

def _блоки_icd(путь: Path) -> dict[tuple, list[int]]:
    т = re.sub(r"=====PAGE \d+\n(?:.*\n){0,4}?\s*20\d\d-\d\d\s*\n", "\n", путь.read_text(encoding="utf-8"))
    return {(int(м.group(1)), int(м.group(2)), м.group(3)): [int(x) for x in re.findall(r"\d+", м.group(4))]
            for м in re.finditer(r"H\s*(\d+)\s*,\s*(\d+)\s*,\s*(index|element)\s*=\s*\[(.*?)\]", т, re.S)}


def beidou() -> dict[str, dict]:
    icd = {}
    for f in ("BDS-SIS-ICD-B1C-1.0.txt", "BDS-SIS-ICD-B2a-1.0.txt", "BDS-SIS-ICD-PPP-B2b-1.0.txt"):
        icd.update(_блоки_icd(ИСТ / "beidou" / f))
    с = (ИСТ / "beidou" / "PocketSDR_sdr_ldpc.c").read_text(encoding="utf-8")

    def массив(имя: str) -> list[int]:
        м = re.search(r"static const uint8_t " + имя + r"\[\]\[4\] = \{.*?\n(.*?)\n\};", с, re.S)
        return [int(x) for x in re.findall(r"\d+", re.sub(r"//.*", "", м.group(1)))]
    итог = {}
    for имя, пс, (m, n), сигнал in (
            ("beidou-b1c-sf2-1200-600", "BCNV1_SF2", (100, 200), "B1C (B-CNAV1, подкадр 2), ICD B1C 1.0 п. 6.2.2.2"),
            ("beidou-b1c-sf3-528-264", "BCNV1_SF3", (44, 88), "B1C (B-CNAV1, подкадр 3), ICD B1C 1.0 п. 6.2.2.3"),
            ("beidou-b2a-576-288", "BCNV2", (48, 96), "B2a (B-CNAV2), ICD B2a 1.0 п. 6.2.2"),
            ("beidou-b2b-972-486", "BCNV3", (81, 162), "PPP-B2b (B-CNAV3), ICD PPP-B2b 1.0 п. 6.2.2")):
        индексы, элементы = icd[(m, n, "index")], icd[(m, n, "element")]
        assert индексы == массив(f"H_{пс}_idx") and элементы == массив(f"H_{пс}_ele"), имя
        assert len(индексы) == 4 * m and max(индексы) < n and 0 < min(элементы) and max(элементы) < 64
        print(f"BeiDou {имя}: H {m} × {n} над GF(64), ICD = PocketSDR")
        итог[имя] = {
            "семейство": "КНР: BeiDou", "n": 6 * n, "k": 6 * (n - m), "скорость": "1/2", "gf64": True,
            "индексы": [индексы[4 * i:4 * i + 4] for i in range(m)],
            "элементы": [элементы[4 * i:4 * i + 4] for i in range(m)],
            "откуда": f"BeiDou {сигнал}: 64-ричный LDPC ({n}, {n - m}) над GF(2⁶), p(x) = 1 + x + x⁶, "
                      f"символ — 6 бит, старший первым; здесь — двоичный образ ({6 * n}, {6 * (n - m)})"}
    return итог


def main() -> None:
    коды = {**abs_s(), **cmmb(), **dtmb(), **beidou()}
    ВЫХОД.write_text(json.dumps(коды, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"записано кодов: {len(коды)} → {ВЫХОД.relative_to(КОРЕНЬ)}")
    # Ранг: rank H = n − k, и проверочные позиции (не данные) — невырожденная часть H.
    from reportgen.potok import gf2, ldpc_kitay  # noqa: PLC0415
    ldpc_kitay.записи.cache_clear()
    for имя in коды:
        м = ldpc_kitay.матрица(имя)
        H = м.плотная()
        проверочные = [j for j in range(м.n) if j not in set(м.данные.tolist())]
        assert gf2.ранг(H) == м.n - len(м.данные) == gf2.ранг(H[:, проверочные]), имя
        print(f"ранг {имя}: rank H = rank H[проверочные] = n − k = {м.n - len(м.данные)}")


if __name__ == "__main__":
    main()
