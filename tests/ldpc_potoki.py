"""Реалистичные потоки с LDPC — свой кодер по матрицам проекта и сборка потоков с известным ответом.

Кодер независим от декодера и опознавателя проекта: систематическое кодирование через H —
прямой подстановкой, если правая часть H нижнетреугольная с единичной диагональю (DVB-S2/T2,
ATSC 3.0 тип B: накопитель), иначе своим исключением Гаусса на упакованных строках с опорными
столбцами справа налево (проверочные — в конце слова, данные — в начале). Каждое слово
проверяется независимо: синдром H·c = 0 по строкам H (``синдром_нулевой``).

Сборка потоков — как у передатчиков:

- **DVB-S2** (EN 302 307-1): BBHEADER (CRC-8) + поле данных → скремблер BB → БЧХ → LDPC →
  перемежение бит 8PSK/16APSK (запись по столбцам, чтение по строкам) → PLFRAME: PLHEADER
  (SOF + PLS, 90 бит π/2-BPSK — бит на символ), слоты данных по 90 символов (бит на символ
  — по созвездию), пилоты — 36 символов после каждых 16 слотов; скремблер PL — поворот
  символа на j^R (последовательность Голда, n = 0) — на уровне бит по разметке созвездия;
- **5G NR** (38.212, 5.4.2): первые 2Z позиций не передаются, согласование скорости — E бит
  кругового буфера с k0 = 0 (выкалывание конца чётности или повтор), перемежение Qm строк;
- **CCSDS** (131.0-B-5): ASM 1ACFFC1D + кодовый блок, рандомизатор h(x) = x⁸+x⁷+x⁵+x³+1
  поверх блока (без ASM);
- **кадры модема**: синхрослово + слова подряд.

Условия: ошибки (BER), сдвиг начала, инверсия, порядок бит в байте, 8PSK с неверной разметкой.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import dvbs2, dvbs2_pl, ldpc, ldpc_std

# -- свой кодер ----------------------------------------------------------------------------------


def _плотная(м) -> np.ndarray:
    H = np.zeros((м.m, м.n), dtype=np.uint8)
    for i, с in enumerate(м.строки):
        H[i, с] = 1
    return H


def синдром_нулевой(м, слова: np.ndarray) -> np.ndarray:
    """Независимая проверка: для каждого слова — все ли проверки H выполнены (по строкам H)."""
    слова = np.atleast_2d(np.asarray(слова, dtype=np.uint8))
    итог = np.ones(len(слова), dtype=bool)
    for с in м.строки:
        итог &= (слова[:, с].sum(axis=1) % 2) == 0
    return итог


def _треугольная(м) -> bool:
    k = м.n - м.m
    if k <= 0:
        return False
    for i, с in enumerate(м.строки):
        п = с[с >= k] - k
        if not len(п) or п.max() != i:
            return False
    return True


class Кодер:
    """Систематический кодер по H: данные — на позициях ``данные``, проверочные — по H."""

    def __init__(self, м):
        self.м = м
        self.n = м.n
        if _треугольная(м):
            self.вид = "накопитель"
            k = м.n - м.m
            self.данные = np.arange(k)
            self.проверочные = np.arange(k, м.n)
            return
        self.вид = "Гаусс"
        H = _плотная(м)[:, ::-1]                       # справа налево: опорные — в конце слова
        m, n = H.shape
        слов = (n + 63) // 64
        дополнено = np.zeros((m, слов * 64), dtype=np.uint8)
        дополнено[:, :n] = H
        a = np.packbits(дополнено.reshape(m, слов, 8, 8)[:, :, :, ::-1], axis=-1).reshape(m, слов * 8)
        a = a.view(np.uint64).reshape(m, слов).copy()
        опорные, r = [], 0
        for j in range(n):
            if r >= m:
                break
            w, b = divmod(j, 64)
            маска = np.uint64(1) << np.uint64(b)
            есть = np.flatnonzero((a[r:, w] & маска) != 0)
            if not len(есть):
                continue
            p = r + int(есть[0])
            if p != r:
                a[[r, p]] = a[[p, r]]
            с_единицей = (a[:, w] & маска) != 0
            с_единицей[r] = False
            a[с_единицей] ^= a[r]
            опорные.append(j)
            r += 1
        a = a[:r]
        биты = np.unpackbits(a.view(np.uint8).reshape(r, слов, 8, 1), axis=-1)[:, :, :, ::-1]
        R = биты.reshape(r, слов * 64)[:, :n][:, ::-1]     # обратно в порядок слова
        self.проверочные = np.array([n - 1 - j for j in опорные], dtype=np.int64)
        занято = np.zeros(n, dtype=bool)
        занято[self.проверочные] = True
        self.данные = np.flatnonzero(~занято)
        # Строка i: c[проверочные[i]] = Σ R[i, данные] · c[данные].
        self.R = R[:, self.данные].astype(np.float32)

    def закодировать(self, u: np.ndarray) -> np.ndarray:
        u = np.atleast_2d(np.asarray(u, dtype=np.uint8))
        M = len(u)
        c = np.zeros((M, self.n), dtype=np.uint8)
        c[:, self.данные] = u
        if self.вид == "накопитель":
            k = self.n - self.м.m
            for i, с in enumerate(self.м.строки):
                инф = с[с < k]
                пр = с[(с >= k) & (с < k + i)]
                s = np.bitwise_xor.reduce(c[:, инф], axis=1) if len(инф) else 0
                if len(пр):
                    s = s ^ np.bitwise_xor.reduce(c[:, пр], axis=1)
                c[:, k + i] = s
            return c
        c[:, self.проверочные] = ((u.astype(np.float32) @ self.R.T) % 2).astype(np.uint8)
        return c


@lru_cache(maxsize=16)
def кодер(имя: str) -> Кодер:
    return Кодер(ldpc_std.матрица(имя))


def кодовые(имя_или_матрица, слов: int, сид: int = 1, нули=()) -> np.ndarray:
    """Случайные кодовые слова (M × n); ``нули`` — позиции, которые должны быть нулями (укорочение)."""
    к = кодер(имя_или_матрица) if isinstance(имя_или_матрица, str) else Кодер(имя_или_матрица)
    u = np.random.default_rng(сид).integers(0, 2, (слов, len(к.данные))).astype(np.uint8)
    if len(нули):
        место = {int(p): i for i, p in enumerate(к.данные)}
        u[:, [место[int(p)] for p in нули]] = 0
    c = к.закодировать(u)
    assert синдром_нулевой(к.м, c).all(), "свой кодер дал не кодовое слово"
    return c


# -- условия канала --------------------------------------------------------------------------------

def ошибки(биты: np.ndarray, ber: float, сид: int = 7) -> np.ndarray:
    if ber <= 0:
        return np.asarray(биты, dtype=np.uint8)
    rng = np.random.default_rng(сид)
    return (np.asarray(биты, dtype=np.uint8) ^ (rng.random(len(биты)) < ber)).astype(np.uint8)


def сдвинуть(биты: np.ndarray, сдвиг: int, сид: int = 8) -> np.ndarray:
    return np.concatenate([np.random.default_rng(сид).integers(0, 2, сдвиг).astype(np.uint8), биты])


def в_файл(биты: np.ndarray, *, младший: bool = False, инверсия: bool = False) -> bytes:
    """Биты → байты файла (старший бит первым или младший первым), при желании инверсно."""
    б = np.asarray(биты, dtype=np.uint8)
    б = б[:len(б) // 8 * 8]
    if инверсия:
        б = 1 - б
    if младший:
        б = б.reshape(-1, 8)[:, ::-1].reshape(-1)
    return np.packbits(б).tobytes()


# -- DVB-S2 -------------------------------------------------------------------------------------

#: Разметка 8PSK DVB-S2 (gr-dtv): номер точки k (угол k·π/4) для бит b0b1b2.
РАЗМЕТКА_8PSK = (1, 0, 4, 5, 2, 7, 3, 6)


def _код_бчх(nbch: int, короткий: bool):
    for к in dvbs2.КОДЫ:
        if к.nbch == nbch and к.короткий == короткий and "S2X" not in к.имя:
            return к
    return None


def bbframe(kbch: int, номер: int, rng) -> np.ndarray:
    """BBFRAME: BBHEADER (GS непрерывный, DFL — всё поле) с CRC-8 и случайное поле данных."""
    dfl = kbch - 80
    з = bytes([0b0111_0000, 0, 0, 0]) + dfl.to_bytes(2, "big") + bytes([0x47]) + (0).to_bytes(2, "big")
    з += bytes([dvbs2.crc8(з)])
    заголовок = np.unpackbits(np.frombuffer(з, dtype=np.uint8))
    return np.concatenate([заголовок, rng.integers(0, 2, dfl).astype(np.uint8)])


def fecframes(имя: str, кадров: int, *, бчх: bool = True, сид: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Слова LDPC DVB-S2 (кадров × n) и их данные (kldpc бит; с БЧХ — слова БЧХ после скремблера BB)."""
    к = кодер(имя)
    k = len(к.данные)
    rng = np.random.default_rng(сид)
    код = _код_бчх(k, к.n == 16200) if бчх else None
    if код is not None:
        u = np.array([dvbs2.закодировать(dvbs2.скремблер(bbframe(код.kbch, i, rng)), код)
                      for i in range(кадров)], dtype=np.uint8)
    else:
        u = rng.integers(0, 2, (кадров, k)).astype(np.uint8)
    c = к.закодировать(u)
    assert синдром_нулевой(к.м, c).all()
    return c, u


def перемежить(слово: np.ndarray, столбцы: str) -> np.ndarray:
    """Перемежение DVB-S2: запись по столбцам (n/c бит в столбце), чтение по строкам в порядке
    ``столбцы`` — независимо от ``ldpc.перемежитель``."""
    c = len(столбцы)
    строк = len(слово) // c
    таблица = np.asarray(слово).reshape(c, строк)          # столбец j — подряд
    return np.stack([таблица[int(j)] for j in столбцы], axis=1).reshape(-1)


@lru_cache(maxsize=1)
def _голд(сколько: int = 70000) -> np.ndarray:
    """R_n скремблера PL (EN 302 307-1, 5.5.4), код Голда n = 0: R = 2·z(n + 131072) + z(n)."""
    N = 1 << 18
    x = np.zeros(N + 200000, dtype=np.uint8)
    y = np.zeros(N + 200000, dtype=np.uint8)
    x[0], y[:18] = 1, 1
    for i in range(len(x) - 18):
        x[i + 18] = x[i + 7] ^ x[i]
        y[i + 18] = y[i + 10] ^ y[i + 7] ^ y[i + 5] ^ y[i]
    z = x ^ y
    return (2 * z[131072:131072 + сколько] + z[:сколько]).astype(np.int64)


def повернуть_8psk(биты: np.ndarray, повороты: np.ndarray, разметка=РАЗМЕТКА_8PSK) -> np.ndarray:
    """Символы 8PSK (по 3 бита) повернуть на повороты·π/2 и снова разметить."""
    тройки = np.asarray(биты, dtype=np.uint8).reshape(-1, 3)
    номер = тройки[:, 0] * 4 + тройки[:, 1] * 2 + тройки[:, 2]
    точка = (np.array(разметка)[номер] + 2 * повороты[:len(номер)]) % 8
    обратно = np.argsort(np.array(разметка))[точка]
    return np.stack([(обратно >> 2) & 1, (обратно >> 1) & 1, обратно & 1], axis=1).reshape(-1).astype(np.uint8)


def повернуть_qpsk(биты: np.ndarray, повороты: np.ndarray) -> np.ndarray:
    """QPSK DVB-S2 (b0 — знак I, b1 — знак Q): поворот на j: (I, Q) → (−Q, I)."""
    пары = np.asarray(биты, dtype=np.uint8).reshape(-1, 2).copy()
    r = повороты[:len(пары)] % 4
    b0, b1 = пары[:, 0].copy(), пары[:, 1].copy()
    # j: I' = −Q, Q' = I → b0' = 1 − b1, b1' = b0
    итог = np.empty_like(пары)
    for шаг in range(4):
        где = r == шаг
        x0, x1 = b0[где], b1[где]
        for _ in range(шаг):
            x0, x1 = 1 - x1, x0
        итог[где, 0], итог[где, 1] = x0, x1
    return итог.reshape(-1)


def _повернуть(символы_бит: np.ndarray, бит: int, повороты: np.ndarray) -> np.ndarray:
    """Символы (по ``бит`` бит подряд) повернуть на повороты·π/2 (QPSK или 8PSK DVB-S2) и снова разметить."""
    if бит == 3:
        return повернуть_8psk(символы_бит, повороты)
    if бит == 2:
        return повернуть_qpsk(символы_бит, повороты)
    raise ValueError("скремблер PL в генераторе — для QPSK и 8PSK")


def поток_dvbs2(имя: str, кадров: int, *, бчх: bool = True, вид: str = "", pl: bool = False,
                пилоты: bool = False, скремблер_pl: bool = False, modcod: int | None = None,
                сид: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Поток DVB-S2: (биты, слова LDPC кадров × n, данные кадров × kldpc).

    ``вид`` — перемежение перед модуляцией (8PSK, 16APSK); ``pl`` — PLFRAME: PLHEADER 90 бит (π/2-BPSK,
    бит на символ), данные — биты символов созвездия, пилоты — 36 символов (точка (1 + j)/√2: метка 0 —
    нули) после каждых 16 слотов; ``скремблер_pl`` — поворот символов данных и пилотов на R·π/2
    (последовательность Голда заново после каждого PLHEADER; QPSK или 8PSK)."""
    c, u = fecframes(имя, кадров, бчх=бчх, сид=сид)
    бит = {"": 2, "8PSK": 3, "16APSK": 4, "32APSK": 5}[вид]
    if вид:
        столбцы = ldpc_std.столбцы_перемежения(имя, вид)
        переданы = np.array([перемежить(слово, столбцы) for слово in c])
    else:
        переданы = c.copy()
    if not pl:
        if скремблер_pl:
            переданы = np.array([_повернуть(с, бит, _голд()) for с in переданы])
        return переданы.reshape(-1), c, u
    короткий = c.shape[1] == 16200
    if modcod is None:
        modcod = _modcod(имя, бит)
    pls = dvbs2_pl.слово(dvbs2_pl.код_s2(modcod, короткий, пилоты))
    заголовок = np.concatenate([dvbs2_pl.SOF_БИТЫ, pls])
    пилот = np.zeros(36 * бит, dtype=np.uint8)
    слот16 = 16 * 90 * бит
    части = []
    for слово in переданы:
        куски = []
        for i, место in enumerate(range(0, len(слово), слот16)):
            if i and пилоты:
                куски.append(пилот)
            куски.append(слово[место:место + слот16])
        тело = np.concatenate(куски)
        if скремблер_pl:
            тело = _повернуть(тело, бит, _голд())
        части += [заголовок, тело]
    return np.concatenate(части).astype(np.uint8), c, u


#: Стандартные метки DVB-S2 (EN 302 307-1, рис. 9, 10; gr-dtv): угол метки v — РАЗМЕТКА[v]·π/4.
РАЗМЕТКА_QPSK = (1, 7, 3, 5)


def поток_dvbs2_разметка(имя: str, кадров: int, *, вид: str = "", пилоты: bool = False,
                         скремблер_pl: bool = True, поворот: int = 0, отражение: bool = False, сид: int = 1,
                         своя: tuple[int, ...] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """PLFRAME DVB-S2, где демодулятор решил ВСЕ символы по созвездию данных (QPSK или 8PSK): заголовок
    π/2-BPSK (символ i — угол π/4 + (i mod 2)·π/2 + b·π, EN 302 307-1, 5.5.2) и пилоты ((1 + j)/√2, 5.5.3) —
    метками ближайших точек; скремблер PL — поворот на R·π/2 с первого символа после заголовка; разметка
    демодулятора — стандартная, повёрнутая на ``поворот``·π/4 (и отражённая), или ``своя`` — метка
    демодулятора у точки под углом k·π/4 (k = 0…7). Всё — своим кодом, без dvbs2_pl: (биты, слова LDPC, данные)."""
    c, u = fecframes(имя, кадров, бчх=True, сид=сид)
    бит = {"": 2, "8PSK": 3}[вид]
    разметка = np.array(РАЗМЕТКА_QPSK if бит == 2 else РАЗМЕТКА_8PSK)
    метка_угла = np.full(8, -1)
    метка_угла[разметка] = np.arange(1 << бит)
    if своя is not None:
        метка_угла = np.array(своя)
    переданы = np.array([перемежить(с, ldpc_std.столбцы_перемежения(имя, вид)) for с in c]) if вид else c
    короткий = c.shape[1] == 16200
    pls = dvbs2_pl.слово(dvbs2_pl.код_s2(_modcod(имя, бит), короткий, пилоты))
    з = np.concatenate([dvbs2_pl.SOF_БИТЫ, pls]).astype(np.int64)
    углы_заголовка = (1 + 2 * (np.arange(90) % 2 + 2 * з)) % 8
    R = _голд()
    символов_кадр = []
    for слово in переданы:
        метки = слово.reshape(-1, бит) @ (1 << np.arange(бит - 1, -1, -1))
        углы = []
        for i, место in enumerate(range(0, len(метки), 16 * 90)):
            if i and пилоты:
                углы.append(np.full(36, 1))
            углы.append(разметка[метки[место:место + 16 * 90]])
        тело = np.concatenate(углы)
        if скремблер_pl:
            тело = (тело + 2 * R[:len(тело)]) % 8
        символов_кадр.append(np.concatenate([углы_заголовка, тело]))
    углы = np.concatenate(символов_кадр)
    углы = ((-углы if отражение else углы) + поворот) % 8
    метки = метка_угла[углы]
    assert (метки >= 0).all(), "поворот QPSK — только на кратное π/2"
    биты = ((метки[:, None] >> np.arange(бит - 1, -1, -1)) & 1).astype(np.uint8).reshape(-1)
    return биты, c, u


def сверхкадр_ldpc(имя: str, сверхкадров: int, *, заголовок: int = 800, слот: int = 400, маркер: int = 4,
                   пилот: int = 72, пилот_через: int = 7, слотов: int | None = None, бчх: bool = True,
                   данные: np.ndarray | None = None, сид: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Сверхкадр модема с известными символами и словами LDPC подряд в нагрузке (не PLFRAME): заголовок, затем
    слоты по ``слот`` бит — в начале каждого ``пилот_через``-го блок пилотов, за ним маркер (одна из двух
    взаимно инверсных комбинаций — своя у каждого слота), дальше нагрузка. Известное — одно и то же во всех
    сверхкадрах; слова кода переходят из сверхкадра в сверхкадр подряд. ``данные`` — нагрузка вместо слов
    (для отрицательных проверок). Итог: (биты, слова LDPC, их данные)."""
    rng = np.random.default_rng(сид + 100)
    части: list[tuple[bool, np.ndarray | int]] = [(True, rng.integers(0, 2, заголовок).astype(np.uint8))]
    образец = rng.integers(0, 2, маркер).astype(np.uint8)
    i = 0
    while слотов is None or i < слотов:
        if слотов is None and i >= 1 << 16:
            raise ValueError("сверхкадр: задайте число слотов")
        места = слот
        if i % пилот_через == 0:
            части.append((True, rng.integers(0, 2, пилот).astype(np.uint8)))
            места -= пилот
        части.append((True, образец ^ np.uint8(rng.integers(0, 2))))
        части.append((False, места - маркер))
        i += 1
        if слотов is None:
            break
    шаблон = np.concatenate([ч if известно else np.full(ч, 2, np.uint8) for известно, ч in части])
    нагрузка = int((шаблон == 2).sum())
    if данные is None:
        L = ldpc_std.длина_в_потоке(имя)
        if нагрузка % L:
            raise ValueError(f"сверхкадр: нагрузка {нагрузка} бит не делится на слово {L}")
        c, u = fecframes(имя, сверхкадров * нагрузка // L, бчх=бчх, сид=сид)
        поток = c.reshape(-1)
    else:
        c = u = np.zeros((0, 0), np.uint8)
        поток = np.asarray(данные, np.uint8)[:сверхкадров * нагрузка]
    кадры = np.tile(шаблон, (сверхкадров, 1))
    кадры[кадры == 2] = поток
    return кадры.reshape(-1), c, u


def в_тетрадах(биты: np.ndarray) -> bytes:
    """Биты → байты файла, где младшая тетрада байта идёт первой (каждая — старшим битом вперёд)."""
    б = np.asarray(биты, dtype=np.uint8)
    б = б[:len(б) // 8 * 8].reshape(-1, 8)
    return np.packbits(np.concatenate([б[:, 4:], б[:, :4]], axis=1).reshape(-1)).tobytes()


def _modcod(имя: str, бит_на_символ: int) -> int:
    """MODCOD DVB-S2 по коду: номинальная скорость — по коду БЧХ той же длины (у короткого кадра
    «1/2» — k = 7200)."""
    from fractions import Fraction
    м = ldpc_std.матрица(имя)
    код = _код_бчх(м.n - м.m, м.n == 16200)
    скорость = Fraction(код.имя.split()[-1])
    созвездие = {2: "QPSK", 3: "8PSK", 4: "16APSK", 5: "32APSK"}[бит_на_символ]
    for mc, (с, _, ск) in dvbs2_pl.MODCOD.items():
        if с == созвездие and Fraction(ск) == скорость:
            return mc
    raise ValueError(f"нет MODCOD для {имя} {созвездие}")


# -- 5G NR ---------------------------------------------------------------------------------------

def _обратная_gf2(A: np.ndarray) -> np.ndarray:
    n = len(A)
    M = np.concatenate([A.astype(np.uint8), np.eye(n, dtype=np.uint8)], axis=1)
    for j in range(n):
        p = j + int(np.flatnonzero(M[j:, j])[0])
        if p != j:
            M[[j, p]] = M[[p, j]]
        где = np.flatnonzero(M[:, j])
        M[где[где != j]] ^= M[j]
    return M[:, n:]


class КодерNR:
    """Кодер 5G NR по строению базового графа (38.212, 5.3.2): ядро — 4 блок-строки и 4 блок-столбца
    чётности (обращается как 4Z × 4Z), расширение — у строки i ≥ 4 своя позиция чётности с единичным
    блоком (сдвиг 0): она — сумма остальных. Быстрее общего Гаусса при больших Z (H 17664 × 26112)."""

    def __init__(self, bg: int, Z: int):
        self.м = ldpc_std.матрица(f"nr-bg{bg}-z{Z}")
        self.Z = Z
        self.k = ldpc_std.NR_ДАННЫХ[bg] * Z
        self.данные = np.arange(self.k)
        k, я = self.k, 4 * Z
        H = np.zeros((я, я), dtype=np.uint8)
        for i in range(я):
            с = self.м.строки[i]
            H[i, с[(с >= k) & (с < k + я)] - k] = 1
        self.обр = _обратная_gf2(H).astype(np.float32)

    def закодировать(self, u: np.ndarray) -> np.ndarray:
        u = np.atleast_2d(np.asarray(u, dtype=np.uint8))
        Z, k = self.Z, self.k
        c = np.zeros((len(u), self.м.n), dtype=np.uint8)
        c[:, :k] = u
        s = np.zeros((len(u), 4 * Z), dtype=np.uint8)
        for i in range(4 * Z):
            с = self.м.строки[i]
            s[:, i] = np.bitwise_xor.reduce(c[:, с[с < k]], axis=1)
        c[:, k:k + 4 * Z] = ((s.astype(np.float32) @ self.обр.T) % 2).astype(np.uint8)
        for i in range(4 * Z, self.м.m):
            с = self.м.строки[i]
            своя = k + i
            assert своя == с.max()
            c[:, своя] = np.bitwise_xor.reduce(c[:, с[с != своя]], axis=1)
        return c


@lru_cache(maxsize=4)
def кодер_nr(bg: int, Z: int) -> КодерNR:
    return КодерNR(bg, Z)


def голд_nr(c_init: int, длина: int) -> np.ndarray:
    """Скремблер 5G NR прямо по 38.211, 5.2.1 (независимо от проекта): c(n) = x1(n + Nc) + x2(n + Nc), Nc = 1600,
    x1(n+31) = x1(n+3) + x1(n), x1(0) = 1; x2(n+31) = x2(n+3) + x2(n+2) + x2(n+1) + x2(n), x2(i) — бит i c_init."""
    Nc = 1600
    x1 = np.zeros(Nc + длина + 31, dtype=np.uint8)
    x2 = np.zeros(Nc + длина + 31, dtype=np.uint8)
    x1[0] = 1
    x2[:31] = [(c_init >> i) & 1 for i in range(31)]
    for n in range(Nc + длина):
        x1[n + 31] = x1[n + 3] ^ x1[n]
        x2[n + 31] = x2[n + 3] ^ x2[n + 2] ^ x2[n + 1] ^ x2[n]
    return x1[Nc:Nc + длина] ^ x2[Nc:Nc + длина]


def nr(bg: int, Z: int, слов: int, *, E: int | None = None, Qm: int = 1, сид: int = 1
       ) -> tuple[np.ndarray, np.ndarray]:
    """Слова 5G NR после согласования скорости: (биты, полные слова). E — бит на слово."""
    имя = f"nr-bg{bg}-z{Z}"
    if Z >= 128:
        к = кодер_nr(bg, Z)
        c = к.закодировать(np.random.default_rng(сид).integers(0, 2, (слов, к.k)).astype(np.uint8))
        assert синдром_нулевой(к.м, c[:2]).all(), "кодер NR дал не кодовое слово"
    else:
        c = кодовые(имя, слов, сид)
    N = c.shape[1] - 2 * Z
    E = E or N
    буфер = c[:, 2 * Z:]
    индексы = np.arange(E) % N
    e = буфер[:, индексы]
    if Qm > 1:
        e = e.reshape(слов, Qm, E // Qm).transpose(0, 2, 1).reshape(слов, E)
    return e.reshape(-1), c


def nr_со_скремблером(bg: int, Z: int, слов: int, *, E: int, c_init: int, блоков: int = 1, сид: int = 1
                      ) -> tuple[np.ndarray, np.ndarray]:
    """5G NR (BPSK, Qm = 1): слова по E бит, транспортный блок — ``блоков`` кодовых блоков подряд; скремблер
    Голда (38.211, 7.3.1.1) — по всему транспортному блоку заново (c_init без номера слота — маска та же)."""
    биты, c = nr(bg, Z, слов, E=E, сид=сид)
    маска = голд_nr(c_init, блоков * E)
    слова = биты.reshape(слов, E) ^ маска.reshape(блоков, E)[np.arange(слов) % блоков]
    return слова.reshape(-1), c


# -- CCSDS ---------------------------------------------------------------------------------------

ASM = np.unpackbits(np.frombuffer(bytes.fromhex("1ACFFC1D"), dtype=np.uint8))


@lru_cache(maxsize=4)
def псп_ccsds(сколько: int) -> np.ndarray:
    """Рандомизатор CCSDS 131.0-B (h(x) = x⁸+x⁷+x⁵+x³+1, все единицы в начале)."""
    р = [1] * 8
    итог = np.empty(сколько, dtype=np.uint8)
    for i in range(сколько):
        итог[i] = р[0]
        новый = р[0] ^ р[3] ^ р[5] ^ р[7]
        р = р[1:] + [новый]
    return итог


def ccsds(имя: str, слов: int, *, asm: bool = True, рандомизатор: bool = True, сид: int = 1
          ) -> tuple[np.ndarray, np.ndarray]:
    """Блоки CCSDS: [ASM] + переданная часть слова (по схеме стандарта) [⊕ ПСП]."""
    схема = ldpc_std.схема(имя)
    нули = схема.укорочены
    c = кодовые(имя, слов, сид, нули=нули)
    блоки = c[:, схема.переданы]
    if рандомизатор:
        блоки = блоки ^ псп_ccsds(блоки.shape[1])
    if asm:
        блоки = np.concatenate([np.tile(ASM, (слов, 1)), блоки], axis=1)
    return блоки.reshape(-1).astype(np.uint8), c


# -- кадры модема, неизвестная QC ---------------------------------------------------------------

def в_кадрах(слова: np.ndarray, синхро: np.ndarray, слов_в_кадре: int = 1) -> np.ndarray:
    части = []
    for i in range(0, len(слова) - слов_в_кадре + 1, слов_в_кадре):
        части += [синхро, слова[i:i + слов_в_кадре].reshape(-1)]
    return np.concatenate(части).astype(np.uint8)


def случайная_qc(mb: int = 6, nb: int = 24, Z: int = 64, сид: int = 11):
    """Неизвестная QC-LDPC (не из встроенных): база mb × nb, проверочная часть — двухдиагональная."""
    rng = np.random.default_rng(сид)
    P = np.full((mb, nb), -1)
    kb = nb - mb
    for i in range(mb):
        for j in rng.choice(kb, size=max(3, kb // 3), replace=False):
            P[i, j] = rng.integers(0, Z)
        P[i, kb + i] = 0
        if i:
            P[i, kb + i - 1] = 0
    return ldpc.из_прототипа("\n".join(" ".join(map(str, р)) for р in P), Z)
