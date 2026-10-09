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
