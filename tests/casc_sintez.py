"""Синтетический поток «как модем CASC» — эталон для тестов, независимо от reportgen.potok.

Сборка (как у передатчика, всё считается здесь же, по определению):

1. **Данные** — байты: заполнение 0xFF и в каждом слове запись случайных байт — как у канала с простоем.
2. **Аддитивная ПСП** 1 + x² + x³ + x⁹ + x¹² (отводы 12, 9, 3, 2: s[n] = s[n−2] ⊕ s[n−3] ⊕ s[n−9] ⊕ s[n−12]),
   сброс в начале каждого слова кода в начальное состояние ``НАЧАЛЬНОЕ``; накладывается на биты байт данных
   старшим битом вперёд.
3. **QC-LDPC с проверками в начале слова**: H = [H_ч | H_д] из циркулянтов Z × Z; проверочная часть —
   блочная лестница (единичные циркулянты на диагонали и под ней), у каждого блочного столбца данных —
   три блочные строки со случайными сдвигами. Слово — [проверки | данные]; кодирование прямой подстановкой
   по лестнице: p_0 = s_0, p_i = s_i ⊕ p_{i−1}, s — синдром данных. Биты данных кладутся в слово байтами
   младшим битом вперёд (как у кодера модема): в порядке линии ПСП видна только после «реверс 8».
4. **Кадр** — синхрослово и m слов подряд.
5. **Пилоты** — вставка известных бит: через каждые ``P − c`` бит потока — ``c`` бит значения пилота
   (QPSK-символ «11»), поток со вставкой начинается с места 0 периода; места вставки — ``места`` (от начала
   периода, подряд).
6. **Ошибки линии** — независимые с вероятностью BER.

Возвращаемое: ``Поток`` — биты линии, истинные H, слова, данные — для сверки.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ОТВОДЫ = (12, 9, 3, 2)
НАЧАЛЬНОЕ = np.ones(12, dtype=np.uint8)


def псп(длина: int, отводы=ОТВОДЫ, начальное=НАЧАЛЬНОЕ) -> np.ndarray:
    """s[0…L−1] = начальное, дальше s[n] = ⊕ s[n − t] по отводам."""
    L = max(отводы)
    s = np.zeros(длина + L, dtype=np.uint8)
    s[:L] = начальное
    for i in range(L, длина + L):
        v = 0
        for t in отводы:
            v ^= int(s[i - t])
        s[i] = v
    return s[:длина]


@dataclass
class Код:
    Z: int
    mb: int
    kb: int
    сдвиги: dict          # (блочная строка, блочный столбец данных) → сдвиг

    @property
    def n(self) -> int:
        return (self.mb + self.kb) * self.Z

    @property
    def r(self) -> int:
        return self.mb * self.Z

    @property
    def k(self) -> int:
        return self.kb * self.Z

    def строки(self) -> list[np.ndarray]:
        """Строки H (номера позиций слова [проверки | данные])."""
        Z, итог = self.Z, []
        for b in range(self.mb):
            for t in range(Z):
                поз = [b * Z + t] + ([(b - 1) * Z + t] if b else [])
                for (bb, c), a in self.сдвиги.items():
                    if bb == b:
                        поз.append(self.r + c * Z + (t + a) % Z)
                итог.append(np.array(sorted(поз), dtype=np.int64))
        return итог

    def закодировать(self, u: np.ndarray) -> np.ndarray:
        """Данные (слов × k) → слова [проверки | данные]."""
        u = np.asarray(u, dtype=np.uint8)
        Z = self.Z
        s = np.zeros((len(u), self.mb, Z), dtype=np.uint8)
        t = np.arange(Z)
        for (b, c), a in self.сдвиги.items():
            s[:, b, :] ^= u[:, c * Z + (t + a) % Z]
        p = np.zeros_like(s)
        p[:, 0] = s[:, 0]
        for b in range(1, self.mb):
            p[:, b] = s[:, b] ^ p[:, b - 1]
        return np.concatenate([p.reshape(len(u), -1), u], axis=1)


def код(Z: int = 64, mb: int = 8, kb: int = 24, сид: int = 5) -> Код:
    rng = np.random.default_rng(сид)
    сдвиги = {}
    for c in range(kb):
        for b in rng.choice(mb, size=3, replace=False):
            сдвиги[(int(b), c)] = int(rng.integers(0, Z))
    return Код(Z, mb, kb, сдвиги)


@dataclass
class Поток:
    биты: np.ndarray            # биты линии (со вставкой и ошибками)
    код: Код
    слова: np.ndarray           # кодовые слова (кадр за кадром)
    данные: np.ndarray          # байты данных слов (слов × k/8), до ПСП
    кадр: int                   # длина кадра без вставки
    синхро: np.ndarray
    период: int
    места: tuple[int, ...]


def данные(слов: int, байт: int, сид: int = 3) -> np.ndarray:
    """Байты данных: заполнение 0xFF и в каждом слове — запись случайных байт (от четверти слова до всего без
    16 байт) на случайном месте:
    слова различны и покрывают пространство данных, а заполнения хватает на окна ПСП."""
    rng = np.random.default_rng(сид)
    d = np.full((слов, байт), 0xFF, dtype=np.uint8)
    for i in range(слов):
        длина = int(rng.integers(байт // 4, байт - 16))
        начало = int(rng.integers(0, байт))
        места = (начало + np.arange(длина)) % байт           # запись — по кругу: все места покрыты поровну
        d[i, места] = rng.integers(0, 256, длина, dtype=np.uint8)
    return d


def поток(кадров: int = 700, *, слов_в_кадре: int = 4, синхро_бит: int = 64, период: int = 128,
          места: tuple[int, ...] = (38, 39), значения: str = "11", ber: float = 1e-4, Z: int = 64,
          mb: int = 8, kb: int = 24, сид: int = 1) -> Поток:
    rng = np.random.default_rng(сид)
    к = код(Z, mb, kb, сид=сид + 4)
    слов = кадров * слов_в_кадре
    d = данные(слов, к.k // 8, сид=сид + 2)
    биты_д = np.unpackbits(d, axis=1)                                     # старшим битом вперёд
    скремб = биты_д ^ псп(к.k)[None, :]
    u = скремб.reshape(слов, -1, 8)[:, :, ::-1].reshape(слов, к.k)       # байты — младшим битом вперёд
    W = к.закодировать(u)
    синхро = rng.integers(0, 2, синхро_бит, dtype=np.uint8)
    кадры = np.concatenate([np.tile(синхро, (кадров, 1)), W.reshape(кадров, -1)], axis=1)
    ряд = кадры.reshape(-1)
    # Вставка: места периода от начала потока; на остальных местах — ряд подряд.
    своих = период - len(места)
    периодов = -(-len(ряд) // своих)
    маска = np.ones(период, dtype=bool)
    маска[list(места)] = False
    линия = np.zeros(периодов * период, dtype=np.uint8)
    линия.reshape(периодов, период)[:, list(места)] = np.array([int(ч) for ч in значения], dtype=np.uint8)
    места_данных = np.flatnonzero(np.tile(маска, периодов))[:len(ряд)]
    линия[места_данных] = ряд
    линия = линия[:int(места_данных[-1]) + 1]
    ошибки = rng.random(len(линия)) < ber
    линия ^= ошибки.astype(np.uint8)
    return Поток(линия, к, W, d, кадры.shape[1], синхро, период, tuple(места))


def в_байты(биты: np.ndarray) -> bytes:
    целых = len(биты) // 8 * 8
    return np.packbits(биты[:целых]).tobytes()


# -- поток «как запись модема CASC» с кодом casc-8064-6048 -------------------------------------------
#
# Код — базовая матрица 6 × 24 из data/ldpc_vosstanovlennye.json (данные, а не код анализатора); кодер — свой:
# проверочная часть — блочные столбцы 0…5 (столбец 0 — веса 3 со сдвигами 1, 0, 1; дальше — двойная
# диагональ), как у 802.11n: p0 — сумма синдромов данных всех блочных строк, остальные — подстановкой.
# Сдвиг s блока (i, j): в строке i·Z + r единица в столбце j·Z + (r + s) mod Z.
#
# Линия: кадр — синхрослово 504 бит и два слова по 8064 бит; через каждые 126 бит линии — пилот-символ
# (2 бита «11» на местах 32, 33 периода 128); символы ФМ-4 (пары бит, выровненные по пилоту) приёмник
# отдаёт повёрнутыми (``приёмник``: обмен I/Q, инверсия первого, второго бита); шум — BER; в файл биты
# пишутся младшим битом байта вперёд (как у записей отдела).
# Данные: поток циклов по 8192 бит (синхрослово 140 бит и нагрузка — заполнение 0xFF и записи случайных
# байт), нарезанный по 6048 бит на слово; аддитивная ПСП 1 + x² + x³ + x⁹ + x¹² со сбросом на каждое слово.

import json
from pathlib import Path

CASC_ДАННЫЕ = Path(__file__).resolve().parents[1] / "src" / "reportgen" / "potok" / "data" / "ldpc_vosstanovlennye.json"


def _сдвиг(x: np.ndarray, s: int) -> np.ndarray:
    """Блок со сдвигом s как сомножитель: (P^s · x)[r] = x[(r + s) mod Z] — по последней оси."""
    return np.roll(x, -s, axis=-1)


def база_casc() -> tuple[np.ndarray, int, int]:
    к = json.loads(CASC_ДАННЫЕ.read_text(encoding="utf-8"))["casc-8064-6048"]
    return np.array(к["база"], dtype=np.int64), int(к["Z"]), int(к["данные_с"])


def закодировать_casc(u: np.ndarray) -> np.ndarray:
    """Данные (слов × 6048) → слова casc-8064-6048 [проверки 2016 | данные 6048]."""
    база, Z, данные_с = база_casc()
    mb, nb = база.shape
    u = np.asarray(u, dtype=np.uint8).reshape(len(u), nb - mb, Z)
    s = np.zeros((len(u), mb, Z), dtype=np.uint8)
    for i in range(mb):
        for j in range(mb, nb):
            if база[i, j] >= 0:
                s[:, i] ^= _сдвиг(u[:, j - mb], int(база[i, j]))
    p = np.zeros_like(s)
    p[:, 0] = np.bitwise_xor.reduce(s, axis=1)
    p[:, 1] = s[:, 0] ^ _сдвиг(p[:, 0], int(база[0, 0]))
    p[:, 2] = s[:, 1] ^ p[:, 1]
    p[:, 3] = s[:, 2] ^ p[:, 0] ^ p[:, 2]
    p[:, 4] = s[:, 3] ^ p[:, 3]
    p[:, 5] = s[:, 4] ^ p[:, 4]
    слова = np.concatenate([p.reshape(len(u), -1), u.reshape(len(u), -1)], axis=1)
    assert слова.shape[1] - данные_с == u.shape[1] * Z
    return слова


def синдром_casc(слова: np.ndarray) -> np.ndarray:
    """Синдром по базовой матрице (слов × 2016) — проверка кодера."""
    база, Z, _ = база_casc()
    mb, nb = база.shape
    x = np.asarray(слова, dtype=np.uint8).reshape(len(слова), nb, Z)
    s = np.zeros((len(слова), mb, Z), dtype=np.uint8)
    for i in range(mb):
        for j in range(nb):
            if база[i, j] >= 0:
                s[:, i] ^= _сдвиг(x[:, j], int(база[i, j]))
    return s.reshape(len(слова), -1)


def пары(биты: np.ndarray, обмен: bool, инв1: bool, инв2: bool, фаза: int = 0) -> np.ndarray:
    """Символы ФМ-4 — пары (фаза + 2j, фаза + 2j + 1): (a, b) → (b, a) при обмене, затем инверсии."""
    x = np.asarray(биты, dtype=np.uint8).copy()
    a = np.arange(фаза, len(x) - 1, 2)
    первый, второй = x[a].copy(), x[a + 1].copy()
    if обмен:
        первый, второй = второй, первый
    x[a], x[a + 1] = первый ^ np.uint8(инв1), второй ^ np.uint8(инв2)
    return x


@dataclass
class ПотокCASC:
    байты: bytes                # файл: биты младшим вперёд
    линия: np.ndarray           # биты линии после приёмника и шума (в порядке линии)
    слова: np.ndarray           # кодовые слова (кадр за кадром)
    данные: np.ndarray          # поток данных до ПСП (циклы 8192), нарезанный по словам: слов × 6048
    синхро: np.ndarray          # синхрослово кадра 504 бит
    синхро_цикла: np.ndarray    # синхрослово цикла данных 140 бит


def поток_casc(кадров: int = 200, *, ber: float = 1e-2, приёмник: tuple[bool, bool, bool] = (False, True, True),
               пилот: str = "11", места: tuple[int, int] = (32, 33), сид: int = 11) -> ПотокCASC:
    rng = np.random.default_rng(сид)
    слов = 2 * кадров
    k = 6048
    # Поток данных: циклы 8192 бит — синхрослово 140 бит и нагрузка байтами (заполнение 0xFF с записями).
    циклов = -(-слов * k // 8192)
    синхро_цикла = rng.integers(0, 2, 140, dtype=np.uint8)
    нагрузка = np.unpackbits(данные(циклов, (8192 - 140 + 7) // 8, сид=сид + 1), axis=1)[:, :8192 - 140]
    поток = np.concatenate([np.tile(синхро_цикла, (циклов, 1)), нагрузка], axis=1).reshape(-1)[:слов * k]
    д = поток.reshape(слов, k)
    u = д ^ псп(k)[None, :]
    W = закодировать_casc(u)
    синхро = rng.integers(0, 2, 504, dtype=np.uint8)
    ряд = np.concatenate([np.tile(синхро, (кадров, 1)), W.reshape(кадров, -1)], axis=1).reshape(-1)
    период = 128
    маска = np.ones(период, dtype=bool)
    маска[list(места)] = False
    периодов = -(-len(ряд) // (период - 2))
    линия = np.zeros(периодов * период, dtype=np.uint8)
    линия.reshape(периодов, период)[:, list(места)] = np.array([int(ч) for ч in пилот], dtype=np.uint8)
    куда = np.flatnonzero(np.tile(маска, периодов))[:len(ряд)]
    линия[куда] = ряд
    линия = линия[:периодов * период]
    линия = пары(линия, *приёмник, фаза=места[0] % 2)
    линия ^= (rng.random(len(линия)) < ber).astype(np.uint8)
    байты = np.packbits(линия[:len(линия) // 8 * 8], bitorder="little").tobytes()
    return ПотокCASC(байты, линия, W, д, синхро, синхро_цикла)
