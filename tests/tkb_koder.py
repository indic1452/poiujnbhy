"""Независимый кодер ТКБ по первоисточникам — эталон для тестов режимов (не по коду анализатора).

Всё — по тексту источников, побитово, без матриц анализатора:

- расширенный Хэмминг — регистр деления на порождающий многочлен (Table 1
  802.16.1pc-00/35, стр. 5 PDF; схема Fig. 2): данные проходят на выход и в
  регистр, затем выдвигаются проверочные биты, в конце — бит общей чётности;
- (32, 21) — БЧХ (31, 21) ITU-R M.584 п. 1.4: данные — коэффициенты x^30…x^10,
  остаток деления на x^10+x^9+x^8+x^6+x^5+x^3+1 — x^9…x^0, затем бит чётности;
- код чётности — бит чётности в конце;
- блок: данные строками, коды строк, затем столбцов, затем глубины (стр. 5–9);
  укорочение — сначала полный блок с нулями на снимаемых местах, потом эти места
  выбрасываются (Advantech White Paper, стр. 9: «shortened bits are set to zero
  before encoding … after encoding these shortened bits are removed»);
- гипердиагональ — пошаговый способ патента US7356752: строка чётности = 0;
  для каждой строки — повернуть вправо на 1 и сложить со строкой; в конце —
  повернуть ещё на 1; у трёхмерного — то же плоскостями со сдвигом вниз и вправо;
- спиральное перемежение — AHA4501 стр. 10–11: 0, 65, 130, …, 4095, 1, 66, …
"""

from __future__ import annotations

import numpy as np

#: Table 1 [802.16.1pc-00/35, стр. 5 PDF] — своя копия (сверяется с таблицей анализатора в тестах).
МНОГОЧЛЕНЫ = {(7, 4): "x3 + x + 1", (15, 11): "x4 + x + 1", (31, 26): "x5 + x2 + 1",
              (63, 57): "x6 + x + 1", (127, 120): "x7 +x3 + 1"}
#: ITU-R M.584-2, стр. 4, п. 1.4.
БЧХ_31_21 = "x10 + x9 + x8 + x6 + x5 + x3 + 1"


def степени(запись: str) -> list[int]:
    итог = []
    for член in запись.replace(" ", "").split("+"):
        итог.append(0 if член == "1" else 1 if член == "x" else int(член[1:]))
    return итог


def деление(данные: list[int], порождающий: list[int]) -> list[int]:
    """Регистр деления: данные старшим членом вперёд → остаток (старший член первым)."""
    r = max(порождающий)
    отводы = [0] * r
    for p in порождающий:
        if p < r:
            отводы[p] = 1
    рег = [0] * r                      # рег[i] — коэффициент при x^i
    for b in данные:
        обратная = b ^ рег[r - 1]
        for i in range(r - 1, 0, -1):
            рег[i] = рег[i - 1] ^ (обратная & отводы[i])
        рег[0] = обратная & отводы[0]
    return [рег[i] for i in range(r - 1, -1, -1)]


class Составляющий:
    """Кодер одного составляющего кода: «Х» (расш. Хэмминг), «Хб» (без чётности), «Ч», «БЧХ», «БЧХб» (31, 21)."""

    def __init__(self, вид: str, n: int):
        self.вид, self.n = вид, n
        if вид == "Ч":
            self.k, self.g = n - 1, None
        elif вид in ("БЧХ", "БЧХб"):
            self.k, self.g = 21, степени(БЧХ_31_21)
        elif вид == "Хб":
            (nb, kb), = [(a, b) for (a, b) in МНОГОЧЛЕНЫ if a == n]
            self.k, self.g = kb, степени(МНОГОЧЛЕНЫ[(nb, kb)])
        else:
            (nb, kb), = [(a, b) for (a, b) in МНОГОЧЛЕНЫ if a == n - 1]
            self.k, self.g = kb, степени(МНОГОЧЛЕНЫ[(nb, kb)])

    def __call__(self, данные: list[int]) -> list[int]:
        assert len(данные) == self.k
        if self.вид == "Ч":
            return list(данные) + [sum(данные) % 2]
        слово = list(данные) + деление(list(данные), self.g)
        if self.вид in ("Х", "БЧХ"):
            слово.append(sum(слово) % 2)
        return слово


def закодировать_блок(данные: np.ndarray, коды: list[Составляющий]) -> np.ndarray:
    """Данные (kz × ky × kx или ky × kx) → полный блок (nz × ny × nx): строки, столбцы, глубина."""
    трёх = len(коды) == 3
    д = данные if трёх else данные[None]
    kz, ky, kx = д.shape
    cx, cy = коды[0], коды[1]
    cz = коды[2] if трёх else None
    nz = cz.n if трёх else 1
    блок = np.zeros((nz, cy.n, cx.n), np.uint8)
    for z in range(kz):
        строки = np.array([cx(list(д[z, y])) for y in range(ky)], np.uint8)          # ky × nx
        столбцы = np.array([cy(list(строки[:, x])) for x in range(cx.n)], np.uint8)  # nx × ny
        блок[z] = столбцы.T
    if трёх:
        for y in range(cy.n):
            for x in range(cx.n):
                блок[:, y, x] = cz(list(блок[:kz, y, x]))
    return блок if трёх else блок[0]


def гипер_2d(массив: np.ndarray) -> np.ndarray:
    """Строка гиперчётности — пошагово по патенту US7356752 (поворот вправо на 1 перед каждой строкой)."""
    строка = np.zeros(массив.shape[1], np.uint8)
    for r in массив:
        строка = np.roll(строка, 1) ^ r
    return np.roll(строка, 1)


def гипер_3d(массив: np.ndarray) -> np.ndarray:
    """Плоскость гиперчётности: каждая следующая плоскость — со сдвигом на строку вниз и столбец вправо."""
    плоскость = np.zeros(массив.shape[1:], np.uint8)
    for p in массив:
        плоскость = np.roll(плоскость, (1, 1), axis=(0, 1)) ^ p
    return np.roll(плоскость, (1, 1), axis=(0, 1))


def кодер(коды: list[Составляющий], *, укор=(0, 0, 0), строк=0, B=0, Q=0, гипер=False, выколоть=0,
          спираль=False):
    """Кодер режима: данные (биты) одного блока → передаваемые биты. Возвращает (кодировать, k)."""
    трёх = len(коды) == 3
    Ix, Iy = укор[0], укор[1]
    Iz = укор[2] if трёх else 0
    kx, ky = коды[0].k - Ix, коды[1].k - Iy
    kz = коды[2].k - Iz if трёх else 1
    X = коды[0].n - Ix
    снято = строк * kx + B                        # бит данных первой плоскости под укорочением
    k = kx * ky * kz - снято - Q

    def кодировать(данные: np.ndarray) -> np.ndarray:
        assert len(данные) == k
        # Полный массив данных (без укорочения осей) с нулями на снимаемых местах.
        полные = np.zeros((коды[2].k if трёх else 1, коды[1].k, коды[0].k), np.uint8)
        мои = np.zeros((kz, ky, kx), np.uint8).reshape(-1)
        мои[снято + Q:] = данные
        мои = мои.reshape(kz, ky, kx)
        полные[Iz:, Iy:, Ix:] = мои
        блок = закодировать_блок(полные if трёх else полные[0], коды)
        блок = блок if трёх else блок[None]
        блок = блок[Iz:, Iy:, Ix:]                                  # укорочение осей
        if гипер:
            if трёх:
                блок = np.concatenate([блок, гипер_3d(блок)[None]], axis=0)
            else:
                блок = np.concatenate([блок[0], гипер_2d(блок[0])[None]], axis=0)[None]
        плоский = блок.reshape(-1)
        убрать = np.zeros(len(плоский), bool)
        убрать[:строк * X + B] = True                              # R строк и B бит первой плоскости
        if спираль:
            Y = блок.shape[1]
            порядок = [r * X + (j + r) % X for j in range(X) for r in range(Y)]
            выход = плоский[порядок]
        else:
            выход = плоский[~убрать]
        return выход[:len(выход) - выколоть] if выколоть else выход
    return кодировать, k


def поток(кодировать, k: int, блоков: int, *, синхро: np.ndarray | None = None, ошибок: float = 0.0,
          приставка: int = 0, сид: int = 1, маска: np.ndarray | None = None, чередовать: bool = False):
    """Блоки со случайными данными, синхрослово перед каждым блоком, ошибки: (поток, данные блоков)."""
    rng = np.random.default_rng(сид)
    данные = rng.integers(0, 2, (блоков, k)).astype(np.uint8)
    части = [rng.integers(0, 2, приставка).astype(np.uint8)]
    for i, д in enumerate(данные):
        if синхро is not None:
            части.append(синхро ^ 1 if чередовать and i % 2 else синхро)
        блок = кодировать(д)
        if маска is not None:
            блок = блок ^ маска[:len(блок)]
        части.append(блок)
    ряд = np.concatenate(части)
    if ошибок:
        ряд = ряд ^ (rng.random(len(ряд)) < ошибок).astype(np.uint8)
    return ряд, данные


def поток_с_метками(кодировать, k: int, блоков: int, слово: np.ndarray, *, блоков_на_метку: int = 1,
                    меток_на_блок: int = 1, ошибок: float = 0.0, приставка: int = 0, сид: int = 1):
    """Поток с синхрометками: (поток, данные блоков, номер бита первой метки).

    AHA4501 [стр. 9–10 PDF, п. 2.6.1]: «OSYNC is asserted with the first data bit
    of each x output blocks … The system can then use OSYNC to insert a
    synchronization mark in the encoded data stream» — метка перед первым битом
    каждого x-го блока (``блоков_на_метку``). US7085987: «synchronization marks
    are … placed throughout the block, with inverted sync marks placed at the
    beginning of each ETPC block» — ``меток_на_блок`` меток на блок через равные
    доли, первая — инвертированная.
    """
    rng = np.random.default_rng(сид)
    данные = rng.integers(0, 2, (блоков, k)).astype(np.uint8)
    кодовые = np.concatenate([кодировать(д) for д in данные])
    N = len(кодовые) // блоков
    части = [rng.integers(0, 2, приставка).astype(np.uint8)]
    if меток_на_блок > 1:
        assert N % меток_на_блок == 0
        доля = N // меток_на_блок
        for i in range(блоков * меток_на_блок):
            части.append(слово ^ 1 if i % меток_на_блок == 0 else слово)
            части.append(кодовые[i * доля:(i + 1) * доля])
    else:
        for i in range(0, блоков, блоков_на_метку):
            части.append(слово)
            части.append(кодовые[i * N:(i + блоков_на_метку) * N])
    ряд = np.concatenate(части)
    if ошибок:
        ряд = ряд ^ (rng.random(len(ряд)) < ошибок).astype(np.uint8)
    return ряд, данные, приставка


def рандомизатор_802_16(n: int) -> np.ndarray:
    """C80216a-02/55, стр. 3, п. 8.3.3.1.1: «LFSR possessing characteristic polynomial 1 + X14 + X15 …
    preset … to the value 100101010000000» — своя запись: регистр целым числом, ячейка i (1…15) —
    бит 15 − i; выход — ячейки 14 и 15 (биты 1 и 0), вдвигается в ячейку 1 (бит 14)."""
    рег = int("100101010000000", 2)
    выход = np.zeros(n, np.uint8)
    for i in range(n):
        b = ((рег >> 1) ^ рег) & 1
        выход[i] = b
        рег = (рег >> 1) | (b << 14)
    return выход
