"""Встроенные коды LDPC стандартов и открытых описаний — без загрузки.

Таблицы не вписаны по памяти: каждая взята из первоисточника (стандарт, патент) или открытого
кода и сверена программно (assert) со вторым источником; оригиналы и сценарии сверки — в
``istochniki/ldpc/`` (сценарии ``proverki/postroit_*.py`` строят файлы ``data/ldpc_*.json``):

- **DVB-S2, DVB-S2X, DVB-T2** — таблицы адресов (группы по 360 бит, проверочная часть —
  накопитель) из xdsopl/LDPC; числа сверены с текстом ETSI EN 302 307-1/-2, EN 302 755 и с
  gr-dvbs2, пять кодов DVB-S2 — ещё и с alist AFF3CT;
- **5G NR** (базовые графы 1 и 2, все 51 размер Z) — сдвиги V из AFF3CT, srsRAN_4G и Sionna
  (совпали); первые 2Z позиций не передаются, данные — первые 22Z (граф 1) или 10Z (граф 2);
- **IEEE 802.11n (Wi-Fi)** — 12 кодов: tavildar/LDPC (C и MATLAB совпали), Sionna, AFF3CT;
- **IEEE 802.16e (WiMAX)** — все 114 кодов (6 базовых матриц × 19 длин): yaldpc и FEC
  (dshekhalev) совпали, 95 — ещё и с alist wimax_ldpc_lib (у 5/6 там иной сдвиг одного блока);
- **CCSDS**: AR4JA (9 кодов, 131.0-B-5, 7.4: θ, φ из текста стандарта = labrador-ldpc; вся H =
  alist ldpc-toolbox; последние M позиций не передаются), C2 (8176, 7156) и передаваемый
  (8160, 7136) (табл. 7-1 = ldpc-toolbox; 18 ведущих нулей не передаются, два нуля в конце), TC
  (128, 64), (512, 256) (231.0-B-4: H labrador-ldpc, сверена с порождающей W стандарта);
- **ATSC 3.0** — 24 кода (A/322, прил. A = gr-atsc3): тип B — как DVB; тип A — часть M1 с
  накопителем и часть M2 с единичной матрицей (6.1.3.1);
- **DTMB** — 3 кода (dtmb-sdr; источник один, ранг проверен);
- **F-LDPC (TrellisWare)** — основа Datum FlexLDPC: строится по патенту US 7,673,213 (модуль
  ``ldpc_flex``); в списке — коды режимов Datum (M7/PSM-500 и M7XC LDPC-Gen2) со значениями
  патента по умолчанию (гипотеза), любой другой (K, J, P) — по имени ``fldpc-kK-jJ-pP``;
- **прочие** по одному источнику (AFF3CT): 10GBASE-T (2048, 1723), WRAN (480, 360).

Имя кода — как слово в слое: ``ldpc nr-bg1-z384``, ``ldpc dvb-s2-64800-48600``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from fractions import Fraction
from functools import cache, lru_cache
from pathlib import Path

import numpy as np

from . import ldpc_flex

ДАННЫЕ = Path(__file__).with_name("data")
#: Столбцов базового графа NR и столбцов данных в нём.
NR_СТОЛБЦОВ = {1: 68, 2: 52}
NR_ДАННЫХ = {1: 22, 2: 10}
#: Вид перемежения F-LDPC перед модулятором (патент, абзац (149)).
КАНАЛ_1367 = "1367"


@cache
def _json(имя: str):
    return json.loads((ДАННЫЕ / f"ldpc_{имя}.json").read_text(encoding="utf-8"))


def _dvb() -> dict[str, dict]:
    return _json("dvb")


def _nr() -> dict:
    return _json("nr")


def _прочие() -> dict[str, dict]:
    return _json("prochie")


def _wifi() -> dict[str, dict]:
    return _json("wifi")


def _nr_набор(bg: int, Z: int) -> dict | None:
    """Набор iLS графа, в который входит размер Z, — с таблицей V; None — такого Z нет."""
    for набор in _nr()["графы"][str(bg)].values():
        if Z in набор["Z"]:
            return набор
    return None


def _nr_имя(имя: str) -> tuple | None:
    """«nr-bg1-z384» → (1, 384); не NR — None."""
    части = имя.lower().split("-")
    if len(части) != 3 or части[0] != "nr" or not части[1].startswith("bg") or not части[2].startswith("z"):
        return None
    try:
        bg, Z = int(части[1][2:]), int(части[2][1:])
    except ValueError:
        return None
    return (bg, Z) if bg in NR_СТОЛБЦОВ and _nr_набор(bg, Z) is not None else None


# -- реестр: имя → описание и построитель матрицы -------------------------------------------------

def _по_строкам(n: int, строки: list, откуда: str):
    from . import ldpc  # noqa: PLC0415 — ldpc зовёт этот модуль
    return ldpc.Матрица(n, [np.array(с, dtype=np.int64) for с in строки], откуда)


def _из_пар(n: int, m: int, пары: tuple[np.ndarray, np.ndarray], откуда: str):
    from . import ldpc  # noqa: PLC0415
    строка, столбец = пары
    ключ, раз = np.unique(строка.astype(np.int64) * n + столбец, return_counts=True)
    ключ = ключ[раз % 2 == 1]                       # Π_a ⊕ Π_b: совпавшие единицы сокращаются
    return ldpc._по_строкам(n, m, ключ // n, ключ % n, откуда)


def _ar4ja(k: int, скорость: str):
    """AR4JA (CCSDS 131.0-B-5, 7.4): H из M×M блоков, Π_k по θ_k и φ_k(j, M) (7.4.2.4)."""
    д = _json("ccsds")["ar4ja"]
    M = д["M"][f"{k}-{скорость}"]
    индекс = M.bit_length() - 8                       # φ задан для M = 2⁷ … 2¹³
    i = np.arange(M)
    j = 4 * i // M

    def π(номер: int) -> np.ndarray:
        φ = np.array([д["phi"][jj][номер - 1][индекс] for jj in range(4)])
        return M // 4 * ((д["theta"][номер - 1] + j) % 4) + (φ[j] + i) % (M // 4)

    строка, столбец = [], []
    for rb, cb, слагаемые in д["расположение"][скорость]:
        for с in слагаемые:
            строка.append(rb * M + i)
            столбец.append(cb * M + (i if с == "I" else π(с)))
    блоков = max(cb for _, cb, _ in д["расположение"][скорость]) + 1
    м = _из_пар(блоков * M, 3 * M, (np.concatenate(строка), np.concatenate(столбец)),
                f"AR4JA CCSDS 131.0-B-5, k = {k}, скорость {скорость}, M = {M}")
    м.данные = np.arange(k, dtype=np.int64)
    return м


def _c2(передаваемый: bool):
    """C2 (8176, 7156) по табл. 7-1; передаваемый (8160, 7136): ещё два нулевых бита в конце."""
    N = 511
    строка, столбец = [], []
    for r, ряд in enumerate(_json("ccsds")["c2"]):
        for c, сдвиги in enumerate(ряд):
            for s in сдвиги:
                j = np.arange(N)
                строка.append(r * N + j)
                столбец.append(c * N + (j + s) % N)
    n, m = 16 * N, 2 * N
    if передаваемый:
        # Два нуля в конце слова (7.3.5, 4) — в H проверками «бит = 0».
        строка.append(np.array([m, m + 1]))
        столбец.append(np.array([n, n + 1]))
        n, m = n + 2, m + 2
    м = _из_пар(n, m, (np.concatenate(строка), np.concatenate(столбец)),
                "CCSDS C2 131.0-B-5, табл. 7-1" + ("; (8160, 7136) по 7.3.5" if передаваемый else ""))
    # Данные: подкод (8176, 7154) систематичен по первым 7154 позициям (7.3.4); у передаваемого
    # первые 18 — нули (не передаются).
    м.данные = np.arange(18 if передаваемый else 0, 7154, dtype=np.int64)
    return м


def _tc(n: int):
    м = _по_строкам(n, _json("ccsds")["tc"][str(n)], f"CCSDS TC 231.0-B-4, ({n}, {n // 2})")
    м.данные = np.arange(n // 2, dtype=np.int64)   # G = [I | W] (4.3.2)
    return м


def _wimax(n: int, вид: str):
    """802.16e: базовая 24 столбца, z = n/24, сдвиг ⌊s·z/96⌋ (у 2/3A — s mod z)."""
    from . import ldpc  # noqa: PLC0415
    z = n // 24
    база = _json("wimax")["матрицы"][вид]
    текст = "\n".join(" ".join(str(-1 if s < 0 else (s % z if вид == "23A" else s * z // 96)) for s in ряд)
                      for ряд in база)
    м = ldpc.из_прототипа(текст, z)
    м.откуда = f"IEEE 802.16e, {вид[:1]}/{вид[1:2]}{вид[2:]}, n = {n}, z = {z}"
    return м


def _atsc(имя: str):
    """ATSC 3.0 (A/322, 6.1.3): тип B — адреса с Qldpc и накопитель; тип A — части M1 и M2."""
    from . import ldpc  # noqa: PLC0415
    к = _json("atsc")[имя]
    n, k = к["n"], к["k"]
    if к["тип"] == "B":
        м = ldpc.из_адресов("\n".join(" ".join(map(str, с)) for с in к["строки"]), n, k)
        м.откуда = к["откуда"]
        return м
    M1, M2 = к["M1"], к["M2"]
    Q1, Q2 = M1 // 360, M2 // 360
    m = M1 + M2
    t = np.arange(360)
    строка, столбец = [], []
    # Позиция в слове (λ) бита p_j части 1: λ[k + 360·t + s] = p[Q1·s + t] (шаг vi), части 2 — (шаг viii).
    место_p = np.empty(m, dtype=np.int64)
    j = np.arange(M1)
    место_p[:M1] = k + 360 * (j % Q1) + j // Q1
    j2 = np.arange(M2)
    место_p[M1:] = k + M1 + 360 * (j2 % Q2) + j2 // Q2
    for i, адреса in enumerate(к["строки"]):
        x = np.array(адреса, dtype=np.int64)[:, None]
        адрес = np.where(x < M1, (x + t * Q1) % M1, M1 + (x - M1 + t * Q2) % M2)
        # Группы 0 … k/360 − 1 — биты данных; дальше — биты части 1 в порядке слова (шаг vii).
        бит = np.broadcast_to(i * 360 + t, адрес.shape)
        строка.append(адрес.reshape(-1))
        столбец.append(бит.reshape(-1))
    # Часть 1: накопитель — проверка j: p_j ⊕ p_{j−1}; часть 2: единичная — p_{M1+j}.
    строка += [j, j[1:], M1 + j2]
    столбец += [место_p[j], место_p[j[1:] - 1], место_p[M1 + j2]]
    м = _из_пар(n, m, (np.concatenate(строка), np.concatenate(столбец)), к["откуда"])
    м.данные = np.arange(k, dtype=np.int64)
    return м


def _dtmb(имя: str):
    к = _json("dtmb")[имя]
    м = _по_строкам(к["n"], к["строки"], к["откуда"])
    м.данные = np.arange(к["данные_с"], к["n"], dtype=np.int64)
    return м


def _flex(имя: str):
    K, J, P = ldpc_flex.разобрать_имя(имя)
    return ldpc_flex.матрица(K, ldpc_flex.равносильный(K, J, P)[0])


@lru_cache(maxsize=1)
def _реестр() -> dict[str, dict]:
    """Все встроенные коды, кроме 5G NR и F-LDPC вне режимов Datum (они — по имени):
    имя → {семейство, n, k, скорость, откуда, выколоты, укорочены, строить}."""
    р: dict[str, dict] = {}

    def добавить(имя: str, семейство: str, n: int, k: int, скорость, откуда: str,
                 строить: Callable, выколоты: str = "", укорочены: str = "", **ещё):
        р[имя] = {"семейство": семейство, "n": n, "k": k, "скорость": str(скорость), "откуда": откуда,
                  "выколоты": выколоты, "укорочены": укорочены, "строить": строить, **ещё}

    for имя, к in list(_dvb().items()) + list(_wifi().items()) + list(_прочие().items()):
        добавить(имя, к["семейство"], к["n"], к["k"], к["скорость"], к["откуда"], None,
                 к.get("выколоты", ""))
    ccsds = _json("ccsds")
    for ключ, M in ccsds["ar4ja"]["M"].items():
        k, скорость = int(ключ.split("-")[0]), ключ.split("-")[1]
        блоков = max(cb for _, cb, _ in ccsds["ar4ja"]["расположение"][скорость]) + 1
        n = блоков * M
        добавить(f"ar4ja-{n}-{k}", "CCSDS AR4JA", n, k, скорость, ccsds["откуда"]["ar4ja"],
                 lambda k=k, с=скорость: _ar4ja(k, с), f"{n - M}-{n - 1}")
    добавить("ccsds-c2-8176-7156", "CCSDS C2", 8176, 7156, Fraction(7156, 8176), ccsds["откуда"]["c2"],
             lambda: _c2(False))
    добавить("ccsds-c2-8160-7136", "CCSDS C2", 8178, 7136, Fraction(7136, 8160), ccsds["откуда"]["c2"],
             lambda: _c2(True), укорочены="0-17")
    for n in (128, 512):
        добавить(f"ccsds-{n}-{n // 2}", "CCSDS TC", n, n // 2, "1/2", ccsds["откуда"]["tc"], lambda n=n: _tc(n))
    wimax = _json("wimax")
    for n in range(576, 2305, 96):
        for вид, строк in (("12", 12), ("23A", 8), ("23B", 8), ("34A", 6), ("34B", 6), ("56", 4)):
            k = n - строк * n // 24
            добавить(f"wimax-{n}-{k}{вид[2:].lower()}", "IEEE 802.16e (WiMAX)", n, k, Fraction(k, n),
                     wimax["откуда"], lambda n=n, в=вид: _wimax(n, в))
    for имя, к in _json("atsc").items():
        добавить(имя, "ATSC 3.0", к["n"], к["k"], к["скорость"], к["откуда"], lambda и=имя: _atsc(и))
    for имя, к in _json("dtmb").items():
        добавить(имя, к["семейство"], к["n"], к["k"], к["скорость"], к["откуда"], lambda и=имя: _dtmb(и),
                 к["выколоты"])
    for имя in ldpc_flex.имена_datum():
        _добавить_flex(р, имя)
    return р


def _добавить_flex(р: dict, имя: str) -> None:
    K, J0, P0 = ldpc_flex.разобрать_имя(имя)
    J, P = ldpc_flex.равносильный(K, J0, P0)
    L = ldpc_flex.групп(K, J)
    выколото = ldpc_flex.выколоты(K, J, P)
    р[имя] = {"семейство": "F-LDPC TrellisWare (Datum FlexLDPC — гипотеза)", "n": K + L, "k": K,
              "скорость": str(ldpc_flex.скорость(J, P)),
              "откуда": f"патент US 7,673,213 (кодер 1500): K = {K}, J = {J0}, выкалывание {P0}/16"
                        + (f" (то же слово, что J = {J} без выкалывания)" if (J, P) != (J0, P0) else ""),
              "выколоты": ",".join(str(x) for x in выколото), "укорочены": "", "строить": lambda и=имя: _flex(и),
              "flex": (K, J, P)}


#: Коды F-LDPC вне списка режимов Datum — по имени, без добавления в список (он — режимы и стандарты).
_ПО_ИМЕНИ: dict[str, dict] = {}


def _описание(имя: str) -> dict | None:
    р = _реестр()
    if имя in р:
        return р[имя]
    if ldpc_flex.разобрать_имя(имя):
        if имя not in _ПО_ИМЕНИ:
            _добавить_flex(_ПО_ИМЕНИ, имя)
        return _ПО_ИМЕНИ[имя]
    return None


def есть(имя: str) -> bool:
    return _nr_имя(имя) is not None or _описание(имя) is not None


def список() -> list[dict[str, object]]:
    """Все встроенные коды: имя, семейство, n, k, скорость, откуда, выколотые и укороченные по стандарту."""
    итог = []
    for bg in (1, 2):
        for набор in _nr()["графы"][str(bg)].values():
            for Z in набор["Z"]:
                n, k = NR_СТОЛБЦОВ[bg] * Z, NR_ДАННЫХ[bg] * Z
                итог.append({"имя": f"nr-bg{bg}-z{Z}", "семейство": f"5G NR, базовый граф {bg}",
                             "n": n, "k": k, "скорость": str(Fraction(k, n - 2 * Z)),
                             "откуда": _nr()["откуда"], "выколоты": f"0-{2 * Z - 1}", "укорочены": ""})
    for имя, к in _реестр().items():
        выколоты = к["выколоты"]
        if "flex" in к:                                # длинный перечень — сводкой
            выколоты = f"{len(ldpc_flex.выколоты(*к['flex']))} позиций чётности" if выколоты else ""
        итог.append({"имя": имя, **{п: к[п] for п in ("семейство", "n", "k", "скорость", "откуда", "укорочены")},
                     "выколоты": выколоты})
    return sorted(итог, key=lambda э: (э["семейство"], э["n"], э["k"], э["имя"]))


@lru_cache(maxsize=24)
def матрица(имя: str):
    """Матрица проверок встроенного кода (ldpc.Матрица)."""
    from . import ldpc  # noqa: PLC0415 — ldpc зовёт этот модуль
    nr = _nr_имя(имя)
    if nr is not None:
        bg, Z = nr
        V = _nr_набор(bg, Z)["V"]
        # Сдвиг блока — V mod Z (38.212, 5.3.2): его и берёт из_прототипа.
        м = ldpc.из_прототипа("\n".join(" ".join(map(str, ряд)) for ряд in V), Z)
        м.откуда = f"5G NR, базовый граф {bg}, Z = {Z}"
        м.данные = np.arange(NR_ДАННЫХ[bg] * Z, dtype=np.int64)
        return м
    к = _описание(имя)
    if к is None:
        raise ValueError(f"встроенного кода «{имя}» нет")
    if к["строить"] is not None:
        return к["строить"]()
    if имя in _dvb():
        д = _dvb()[имя]
        м = ldpc.из_адресов("\n".join(" ".join(map(str, с)) for с in д["строки"]), д["n"], д["k"])
        м.откуда = д["откуда"]
        return м
    if имя in _wifi():
        д = _wifi()[имя]
        м = ldpc.из_прототипа("\n".join(" ".join(map(str, ряд)) for ряд in д["база"]), д["Z"])
        м.откуда = д["откуда"]
        м.данные = np.arange(д["k"], dtype=np.int64)
        return м
    д = _прочие()[имя]
    return ldpc.Матрица(д["n"], [np.array(с, dtype=np.int64) for с in д["строки"]], д["откуда"])


#: Перемежение бит DVB-S2 перед модуляцией (EN 302 307-1, 5.3.3): столбцов — бит на символ,
#: порядок чтения столбцов — по GNU Radio gr-dtv (dvbs2_interleaver_bb) и xdsopl/LDPC
#: (itls_handler): у 8PSK при скорости 3/5 — 210, иначе 012; у 16APSK и 32APSK — по порядку.
ПЕРЕМЕЖЕНИЯ_DVB_S2 = (("8PSK", "012"), ("16APSK", "0123"), ("32APSK", "01234"))
СКОРОСТЬ_3_5 = {"dvb-s2-64800-38880", "dvb-s2-16200-9720"}


def перемежения(имя: str) -> list[tuple[str, str]]:
    """Перемежения бит по стандарту кода: [(вид, порядок)]; у DVB-S2 — по модуляциям, у F-LDPC —
    перемежение 1367·i mod M перед модулятором (патент, абзац (149)); у прочих — нет."""
    if ldpc_flex.разобрать_имя(имя):
        return [(КАНАЛ_1367, "")]
    if not имя.startswith("dvb-s2-"):
        return []
    return [(вид, "210" if вид == "8PSK" and имя in СКОРОСТЬ_3_5 else столбцы)
            for вид, столбцы in ПЕРЕМЕЖЕНИЯ_DVB_S2]


def столбцы_перемежения(имя: str, вид: str) -> str:
    """Порядок столбцов перемежения ``вид`` (8PSK, 16APSK, 32APSK) для кода: по стандарту, если
    код его знает, иначе — по порядку."""
    for свой_вид, столбцы in перемежения(имя):
        if свой_вид == вид:
            return столбцы
    return "".join(str(j) for j in range({"8PSK": 3, "16APSK": 4, "32APSK": 5}[вид]))


def схема(имя: str, перемежение: str = ""):
    """Схема передачи по стандарту: не передаются первые 2Z позиций 5G NR, последние M у AR4JA,
    выколотая чётность F-LDPC, первые 5 у DTMB; у C2 (8160, 7136) 18 первых — укорочены.
    ``перемежение`` — модуляция (8PSK, 16APSK, 32APSK), перед которой биты кода DVB-S2
    перемежаются, или «1367» — перемежение F-LDPC перед модулятором."""
    from . import ldpc  # noqa: PLC0415
    м = матрица(имя)
    nr = _nr_имя(имя)
    к = _описание(имя) or {}
    if nr:
        выколоты = np.arange(2 * nr[1], dtype=np.int64)
    elif "flex" in к:
        с = ldpc_flex.схема(*к["flex"], канал=перемежение == КАНАЛ_1367)
        if перемежение not in ("", КАНАЛ_1367):
            raise ValueError(f"у кода «{имя}» перемежения {перемежение} по стандарту нет")
        return с
    else:
        выколоты = ldpc.позиции(str(к.get("выколоты", "")), м.n)
    укорочены = ldpc.позиции(str(к.get("укорочены", "")), м.n)
    с = ldpc.Схема(м, выколоты, укорочены)
    if перемежение:
        столбцы = dict(перемежения(имя)).get(перемежение)
        if столбцы is None or перемежение == КАНАЛ_1367:
            raise ValueError(f"у кода «{имя}» перемежения {перемежение} по стандарту нет")
        с = ldpc.Схема(м, выколоты, укорочены, с.переданы[ldpc.перемежитель(с.длина, столбцы)])
    return с


@cache
def проба(имя: str, сколько: int, перемежение: str = "") -> tuple[int, list[np.ndarray]]:
    """Длина слова по схеме стандарта и проверки для пробы кода на потоке — строятся один раз.

    Матрицы всех кодов в памяти не держатся (у DVB-S2 — по мегабайту и больше),
    а проверки для пробы малы: автомат пробует все коды, не строя матрицы заново.
    """
    from . import ldpc  # noqa: PLC0415
    с = схема(имя, перемежение)
    return с.длина, ldpc.проверки_для_пробы(с, сколько)[0]


def выколоты_по_стандарту(имя: str) -> bool:
    """Часть позиций слова по стандарту не передаётся (5G NR, AR4JA, F-LDPC с выкалыванием, DTMB)."""
    if _nr_имя(имя):
        return True
    к = _описание(имя)
    return bool(к and (к["выколоты"] or к["укорочены"]))


def _длина_стандарта(э: dict[str, object]) -> int:
    """Длина слова в потоке по схеме стандарта: n без выколотых и укороченных позиций."""
    к = _описание(str(э["имя"]))
    if к is not None and "flex" in к:
        K, J, P = к["flex"]
        return K + len(ldpc_flex.оставлены(ldpc_flex.групп(K, J), P))
    from . import ldpc  # noqa: PLC0415
    n = int(э["n"])
    return n - len(ldpc.позиции(str(э["выколоты"]), n)) - len(ldpc.позиции(str(э.get("укорочены", "")), n))


@lru_cache(maxsize=1)
def _длины() -> dict[str, int]:
    return {str(э["имя"]): _длина_стандарта(э) for э in список()}


def длина_в_потоке(имя: str) -> int:
    """Длина слова кода в потоке по схеме стандарта."""
    if имя in _длины():
        return _длины()[имя]
    return схема(имя).длина


def годится(L0: int, блок: int | None, бит: int | None = None, слов_до: int = 64) -> bool:
    """Слово длины L0 (в потоке по стандарту) может лечь в блок данных целое число раз (от половины
    до целого слова: выкалывание, укорочение) и в потоке уместится хотя бы трижды."""
    if бит is not None and 3 * (L0 // 2) > бит:
        return False
    return not блок or any(блок % м == 0 and L0 // 2 <= блок // м <= L0 for м in range(1, слов_до + 1))


def подходящие(блок: int | None, *, бит: int | None = None, слов_до: int = 64,
               имена: list[str] | None = None) -> list[str]:
    """Встроенные коды, чьё слово (со схемой стандарта) может лечь в блок данных целое число раз.

    Слово в потоке бывает короче слова стандарта (выкалывание при согласовании
    скорости, укорочение) — но не вдвое: длина слова — от половины до целого.
    Без блока — все коды. ``бит`` — длина потока: код, у которого в нём не
    уместится и трёх слов (хотя бы укороченных вдвое), не пробуется. ``имена`` — только из
    них (любые встроенные, в том числе F-LDPC вне списка режимов Datum).
    """
    if имена is not None:
        return [и for и in имена if есть(и) and годится(длина_в_потоке(и), блок, бит, слов_до)]
    return [имя for имя, L0 in _длины().items() if годится(L0, блок, бит, слов_до)]
