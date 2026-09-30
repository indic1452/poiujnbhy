"""Свои кодеры турбо- и решётчатых кодов для тестов — прямо по тексту стандартов.

Написаны независимо от ``reportgen.potok.turbo_std`` и ``trellis``: побитовые
циклы по формулам и рисункам первоисточников (istochniki/turbo/…), без общих
таблиц и функций с анализатором. Таблицы, которых нет в виде формулы (QPP LTE),
читаются из текста самого стандарта (``*.pdf.txt``).

- UMTS: ETSI TS 125 212 V17.0.0, п. 4.2.3.2 (стр. PDF 24–28), согласование
  скорости — п. 4.2.7.2.2.3 и 4.2.7.5 (стр. 40, 45), разделение и сборка —
  п. 4.2.7.4 (стр. 44–45);
- LTE: ETSI TS 136 212 V17.1.0, п. 5.1.3.2 (стр. 16–18), согласование —
  п. 5.1.4.1 (стр. 19–23);
- CCSDS: 131.0-B-5, п. 6 (стр. 41–45);
- DVB-RCS: EN 301 790 V1.5.1, п. 6.4.4 (стр. 30–33).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

ИСТОЧНИКИ = Path(__file__).resolve().parents[1] / "istochniki" / "turbo"


def страницы(путь: Path) -> dict[int, str]:
    текст = путь.read_text(encoding="utf-8", errors="replace")
    части = re.split(r"\n=====PAGE (\d+)=====\n", текст)
    return {int(части[i]): части[i + 1] for i in range(1, len(части), 2)}


# ---------------------------------------------------------------- UMTS 25.212

def _простое(n: int) -> bool:
    return n > 1 and all(n % d for d in range(2, int(n ** 0.5) + 1))


def _первообразный(p: int) -> int:
    """Наименьший первообразный корень — так составлена табл. 2 (стр. 26)."""
    for v in range(2, p):
        if len({pow(v, k, p) for k in range(1, p)}) == p - 1:
            return v
    raise AssertionError(p)


def umts_перемежитель(K: int) -> list[int]:
    """x'_k = x_{π[k]} (с нуля) — п. 4.2.3.2.3.1–4.2.3.2.3.3."""
    assert 40 <= K <= 5114
    R = 5 if K <= 159 else 10 if (160 <= K <= 200 or 481 <= K <= 530) else 20
    if 481 <= K <= 530:
        p, C = 53, 53
    else:
        p = next(q for q in range(7, 258) if _простое(q) and K <= R * (q + 1))
        C = p - 1 if K <= R * (p - 1) else p if K <= R * p else p + 1
    v = _первообразный(p)
    s = [1]
    for _ in range(1, p - 1):
        s.append(v * s[-1] % p)
    q = [1]
    for _ in range(1, R):
        кандидат = q[-1] + 1
        while not (_простое(кандидат) and math.gcd(кандидат, p - 1) == 1 and кандидат > 6):
            кандидат += 1
        q.append(кандидат)
    if R == 5:
        T = [4, 3, 2, 1, 0]
    elif R == 10:
        T = [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    elif 2281 <= K <= 2480 or 3161 <= K <= 3210:
        T = [19, 9, 14, 4, 0, 2, 5, 7, 12, 18, 16, 13, 17, 15, 3, 1, 6, 11, 8, 10]
    else:
        T = [19, 9, 14, 4, 0, 2, 5, 7, 12, 18, 10, 8, 13, 17, 3, 1, 16, 6, 15, 11]
    r = [0] * R
    for i in range(R):
        r[T[i]] = q[i]
    U = []
    for i in range(R):
        if C == p:
            ряд = [s[(j * r[i]) % (p - 1)] for j in range(p - 1)] + [0]
        elif C == p + 1:
            ряд = [s[(j * r[i]) % (p - 1)] for j in range(p - 1)] + [0, p]
        else:
            ряд = [s[(j * r[i]) % (p - 1)] - 1 for j in range(p - 1)]
        U.append(ряд)
    if C == p + 1 and K == R * C:
        U[R - 1][p], U[R - 1][0] = U[R - 1][0], U[R - 1][p]
    # Строка i переставленной матрицы — строка T(i) исходной, внутри — U_{T(i)}? По тексту
    # (5): U_i относится к i-й строке исходной матрицы, (6): строка i после — строка T(i).
    π = []
    for j in range(C):
        for i in range(R):
            исх = T[i] * C + U[T[i]][j]
            if исх < K:
                π.append(исх)
    assert sorted(π) == list(range(K))
    return π


def _rsc_umts(u: list[int]) -> tuple[list[int], list[tuple[int, int]]]:
    """Составляющий кодер 25.212 рис. 4: g0 = 1 + D² + D³ (обратная), g1 = 1 + D + D³.

    Регистр r1 r2 r3 (r1 — последний вошедший). Хвост: вход — обратная связь (п. 4.2.3.2.2).
    """
    r1 = r2 = r3 = 0
    z = []
    for x in u:
        a = x ^ r2 ^ r3
        z.append(a ^ r1 ^ r3)
        r1, r2, r3 = a, r1, r2
    хвост = []
    for _ in range(3):
        x = r2 ^ r3
        a = 0
        хвост.append((x, a ^ r1 ^ r3))
        r1, r2, r3 = a, r1, r2
    return z, хвост


def umts_кодировать(u: list[int]) -> list[int]:
    """Выход турбокодера 25.212: x1 z1 z'1 … xK zK z'K, затем 12 бит хвоста."""
    K = len(u)
    π = umts_перемежитель(K)
    z, хвост1 = _rsc_umts(u)
    z2, хвост2 = _rsc_umts([u[i] for i in π])
    выход = []
    for k in range(K):
        выход += [u[k], z[k], z2[k]]
    for x, zz in хвост1:
        выход += [x, zz]
    for x, zz in хвост2:
        выход += [x, zz]
    return выход


def umts_согласовать(c: list[int], E: int) -> list[int]:
    """Выкалывание по п. 4.2.7.2.2.3 (нисходящий канал) и 4.2.7.5: E бит из 3K + 12."""
    N = len(c)
    assert N % 3 == 0 and E <= N
    dN = E - N
    X = N // 3
    посл = [[c[3 * k + b] for k in range(X)] for b in range(3)]
    оставить = [[True] * X for _ in range(3)]
    for b, a in ((1, 2), (2, 1)):
        dNi = math.floor(dN / 2) if b == 1 else math.ceil(dN / 2)
        if dNi == 0:
            continue
        e, e_plus, e_minus = X, a * X, a * abs(dNi)
        for m in range(X):
            e -= e_minus
            if e <= 0:
                оставить[b][m] = False
                e += e_plus
    выход = []
    for k in range(X):
        for b in range(3):
            if оставить[b][k]:
                выход.append(посл[b][k])
    assert len(выход) == E, (len(выход), E)
    return выход


# ---------------------------------------------------------------- LTE 36.212

def lte_таблица() -> dict[int, tuple[int, int]]:
    """Табл. 5.1.3-3 (стр. PDF 17–18) из текста стандарта: K → (f1, f2)."""
    с = страницы(ИСТОЧНИКИ / "tcc/3gpp/ETSI_TS_136_212_v17.1.0_LTE.pdf.txt")
    текст = с[17][с[17].index("Table 5.1.3-3"):] + с[18]
    числа = [int(ч) for ч in re.findall(r"^\s*(\d+)\s*$", текст, re.M)]
    строки = {}
    k = 0
    while k + 3 < len(числа):
        i, K, f1, f2 = числа[k:k + 4]
        if 1 <= i <= 188 and 40 <= K <= 6144 and i not in строки:
            строки[i] = (K, f1, f2)
            k += 4
        else:
            k += 1
    assert len(строки) == 188
    return {K: (f1, f2) for K, f1, f2 in строки.values()}


def lte_кодировать(u: list[int], f1: int, f2: int) -> tuple[list[int], list[int], list[int]]:
    """d0, d1, d2 длиной K + 4 (п. 5.1.3.2.1–5.1.3.2.2)."""
    K = len(u)
    c2 = [u[(f1 * i + f2 * i * i) % K] for i in range(K)]
    z, х1 = _rsc_umts(u)
    z2, х2 = _rsc_umts(c2)
    x_t = [x for x, _ in х1]
    z_t = [zz for _, zz in х1]
    x2_t = [x for x, _ in х2]
    z2_t = [zz for _, zz in х2]
    d0 = list(u) + [x_t[0], z_t[1], x2_t[0], z2_t[1]]
    d1 = list(z) + [z_t[0], x_t[2], z2_t[0], x2_t[2]]
    d2 = list(z2) + [x_t[1], z_t[2], x2_t[1], z2_t[2]]
    # По тексту: d0_K = x_K (первый бит хвоста), d0_{K+1} = z_{K+1}, d0_{K+2} = x'_K, d0_{K+3} = z'_{K+1};
    # d1_K = z_K, d1_{K+1} = x_{K+2}, d1_{K+2} = z'_K, d1_{K+3} = x'_{K+2};
    # d2_K = x_{K+1}, d2_{K+1} = z_{K+2}, d2_{K+2} = x'_{K+1}, d2_{K+3} = z'_{K+2}.
    return d0, d1, d2


P_LTE = [0, 16, 8, 24, 4, 20, 12, 28, 2, 18, 10, 26, 6, 22, 14, 30,
         1, 17, 9, 25, 5, 21, 13, 29, 3, 19, 11, 27, 7, 23, 15, 31]


def lte_согласовать(d: tuple[list[int], list[int], list[int]], E: int, rv: int) -> list[int]:
    """п. 5.1.4.1: субблочные перемежители, кольцевой буфер (N_cb = K_w), выбор E бит."""
    D = len(d[0])
    C = 32
    R = -(-D // C)
    ND = R * C - D
    НЕТ = None
    v = []
    for i in range(3):
        y = [НЕТ] * ND + list(d[i])
        if i < 2:
            v.append([y[P_LTE[j] + C * r] for j in range(C) for r in range(R)])
        else:
            KП = R * C
            v.append([y[(P_LTE[k // R] + C * (k % R) + 1) % KП] for k in range(KП)])
    KП = R * C
    w = v[0] + [НЕТ] * (2 * KП)
    for k in range(KП):
        w[KП + 2 * k] = v[1][k]
        w[KП + 2 * k + 1] = v[2][k]
    Ncb = 3 * KП
    k0 = R * (2 * -(-Ncb // (8 * R)) * rv + 2)
    e = []
    j = 0
    while len(e) < E:
        бит = w[(k0 + j) % Ncb]
        if бит is not НЕТ:
            e.append(бит)
        j += 1
    return e


# ---------------------------------------------------------------- CCSDS 131.0-B-5

CCSDS_P = [31, 37, 43, 47, 53, 59, 61, 67]


def ccsds_перемежитель(k: int) -> list[int]:
    """s-й бит на входе b — π(s)-й бит блока (с единицы), табл. 6-3, стр. 42."""
    k1, k2 = 8, k // 8
    assert k1 * k2 == k
    π = []
    for s in range(1, k + 1):
        m = (s - 1) % 2
        i = (s - 1) // (2 * k2)
        j = (s - 1) // 2 - i * k2
        t = (19 * i + 1) % (k1 // 2)
        q = t % 8 + 1
        c = (CCSDS_P[q - 1] * j + 21 * m) % k2
        π.append(2 * (t + c * (k1 // 2) + 1) - m)
    return [x - 1 for x in π]


def _вектор(строка: str) -> list[int]:
    return [int(c) for c in строка]


def _ccsds_кодер(u: list[int], прямые: list[str]) -> tuple[list[int], list[list[int]]]:
    """Рис. 6-2: G0 = 10011 — обратная связь; вектор: левый бит — текущий вход сумматора,
    далее ячейки D1…D4. Возвращает (систематический выход 0 с хвостом, выходы по прямым)."""
    G0 = _вектор("10011")
    G = [_вектор(g) for g in прямые]
    рег = [0, 0, 0, 0]                                  # D1…D4
    сист, выходы = [], [[] for _ in G]
    for t in range(len(u) + 4):
        обратная = sum(G0[i + 1] & рег[i] for i in range(4)) & 1
        вход = u[t] if t < len(u) else обратная          # хвост: вход — обратная связь
        a = вход ^ обратная
        сист.append(вход)
        for n, g in enumerate(G):
            выходы[n].append((g[0] & a) ^ (sum(g[i + 1] & рег[i] for i in range(4)) & 1))
        рег = [a] + рег[:3]
    return сист, выходы


def ccsds_кодировать(u: list[int], скорость: str) -> list[int]:
    """Кодовое слово (k + 4)/r символов в порядке примечания к рис. 6-2 (стр. 45)."""
    π = ccsds_перемежитель(len(u))
    ub = [u[π[s]] for s in range(len(u))]
    if скорость in ("1/2", "1/3"):
        а_прямые, б_прямые = ["11011"], ["11011"]
    elif скорость == "1/4":
        а_прямые, б_прямые = ["10101", "11111"], ["11011"]
    else:
        а_прямые, б_прямые = ["11011", "10101", "11111"], ["11011", "11111"]
    x, а = _ccsds_кодер(u, а_прямые)
    _, б = _ccsds_кодер(ub, б_прямые)
    выход = []
    for t in range(len(u) + 4):
        if скорость == "1/2":
            выход += [x[t], а[0][t]] if t % 2 == 0 else [x[t], б[0][t]]
        elif скорость == "1/3":
            выход += [x[t], а[0][t], б[0][t]]
        elif скорость == "1/4":
            выход += [x[t], а[0][t], а[1][t], б[0][t]]
        else:
            выход += [x[t], а[0][t], а[1][t], а[2][t], б[0][t], б[1][t]]
    return выход


# ---------------------------------------------------------------- DVB-RCS EN 301 790

RCS_ПАРАМЕТРЫ = {48: (11, 24, 0, 24), 64: (7, 34, 32, 2), 212: (13, 106, 108, 2),
                 220: (23, 112, 4, 116), 228: (17, 116, 72, 188), 424: (11, 6, 8, 2),
                 432: (13, 0, 4, 8), 440: (13, 10, 4, 2), 848: (19, 2, 16, 6),
                 856: (19, 428, 224, 652), 864: (19, 2, 16, 6), 752: (19, 376, 224, 600)}
RCS_ВЫКАЛЫВАНИЕ = {"1/3": ("1", "1"), "2/5": ("11", "10"), "1/2": ("1", "0"),
                   "2/3": ("10", "00"), "3/4": ("100", "000"), "4/5": ("1000", "0000"),
                   "6/7": ("100000", "000000")}


def _rcs_шаг(s: tuple[int, int, int], A: int, B: int) -> tuple[tuple[int, int, int], int, int]:
    """Рис. 16: обратная 15 (1 + D + D³), Y — 13 (1 + D² + D³), W — 11 (1 + D³);
    A — в отвод «1», B — в отводы «1», D и D²."""
    s1, s2, s3 = s
    вход = A ^ B ^ s1 ^ s3
    Y = вход ^ s2 ^ s3
    W = вход ^ s3
    return (вход, s1 ^ B, s2 ^ B), Y, W


def _rcs_кодер(пары: list[tuple[int, int]], начало: tuple[int, int, int]):
    s = начало
    Y, W = [], []
    for A, B in пары:
        s, y, w = _rcs_шаг(s, A, B)
        Y.append(y)
        W.append(w)
    return s, Y, W


RCS_ЦИКЛ = {1: [0, 6, 4, 2, 7, 1, 3, 5], 2: [0, 3, 7, 4, 5, 6, 2, 1], 3: [0, 5, 3, 6, 2, 7, 1, 4],
            4: [0, 4, 1, 5, 6, 2, 7, 3], 5: [0, 2, 5, 7, 1, 3, 4, 6], 6: [0, 7, 6, 1, 3, 4, 5, 2]}


def rcs_кодировать(данные: list[int], скорость: str) -> list[int]:
    """Кодовое слово в естественном порядке (п. 6.4.4.4): A,B всех пар, затем Y1,Y2, затем W1,W2."""
    N = len(данные) // 2
    P0, P1, P2, P3 = RCS_ПАРАМЕТРЫ[N]
    пары = [(данные[2 * i], данные[2 * i + 1]) for i in range(N)]
    перемеж = []
    for j in range(N):
        P = [0, N // 2 + P1, P2, N // 2 + P3][j % 4]
        i = (P0 * j + P + 1) % N
        A, B = пары[i]
        перемеж.append((B, A) if j % 2 == 0 else (A, B))

    def кодер(п):
        s_N, _, _ = _rcs_кодер(п, (0, 0, 0))
        S = RCS_ЦИКЛ[N % 7][4 * s_N[0] + 2 * s_N[1] + s_N[2]]
        s0 = ((S >> 2) & 1, (S >> 1) & 1, S & 1)
        s_конец, Y, W = _rcs_кодер(п, s0)
        assert s_конец == s0
        return Y, W

    Y1, W1 = кодер(пары)
    Y2, W2 = кодер(перемеж)
    шY, шW = RCS_ВЫКАЛЫВАНИЕ[скорость]
    выход = [b for п in пары for b in п]
    for j in range(N):
        if шY[j % len(шY)] == "1":
            выход += [Y1[j], Y2[j]]
    for j in range(N):
        if шW[j % len(шW)] == "1":
            выход += [W1[j], W2[j]]
    return выход


def ошибки(биты, доля: float, сид: int = 5) -> np.ndarray:
    биты = np.asarray(биты, dtype=np.uint8)
    return биты ^ (np.random.default_rng(сид).random(биты.shape) < доля).astype(np.uint8)
