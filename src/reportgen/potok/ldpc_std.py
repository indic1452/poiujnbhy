# -*- coding: utf-8 -*-
"""Встроенные коды LDPC стандартов — без загрузки: 5G NR, DVB-S2/S2X/T2 и прочие.

Таблицы не вписаны по памяти, а взяты из открытых исходников и сверены:

- **DVB-S2, DVB-S2X, DVB-T2** — таблицы адресов (группы по 360 бит, проверочная
  часть — накопитель) из xdsopl/LDPC; пять кодов DVB-S2 (64800 бит, скорости
  1/2, 2/3, 3/4, 4/5, 9/10) сверены с файлами alist набора AFF3CT — матрицы
  совпали целиком;
- **5G NR** (базовые графы 1 и 2, все 51 размер Z) — сдвиги V базовых графов
  из набора AFF3CT (файл на каждое Z: сдвиг = V mod Z у всех размеров набора),
  из srsRAN_4G и из Sionna — совпали по всем наборам iLS; первые 2Z позиций
  слова кодер не передаёт (srsRAN: «2 variable nodes are systematically
  punctured»), данные — первые 22Z (граф 1) или 10Z (граф 2) позиций;
- **IEEE 802.11n (Wi-Fi)** — 12 кодов (n = 648, 1296, 1944; скорости 1/2, 2/3,
  3/4, 5/6): базовые матрицы из tavildar/LDPC — записи на C и на MATLAB
  совпали; (648, 324) совпал с матрицей из Sionna, (648, 540) — с alist
  AFF3CT; у всех ранг даёт скорость, проверочная часть — как в стандарте
  (столбец 1…0…1 и двухдиагональная), данные — первые k позиций;
- **прочие** (по одному источнику, имена — как у файлов источника):
  10GBPS-ETHERNET (2048, 1723), CCSDS (128, 64), WIFI (648, 540), WIMAX
  (576, 288), (576, 480), (960, 720), (1440, 720), WRAN (480, 360) и AR4JA
  (базовая 12 × 20, Z = 512; последние 4 блок-столбца не передаются — по
  шаблону выкалывания из того же файла) — наборы AFF3CT и CommPy; ранг H
  сверен с k.

Имя кода — как слово в слое: ``ldpc nr-bg1-z384``, ``ldpc dvb-s2-64800-48600``.
"""

from __future__ import annotations

import json
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ДАННЫЕ = Path(__file__).with_name("data")
#: Столбцов базового графа NR и столбцов данных в нём.
NR_СТОЛБЦОВ = {1: 68, 2: 52}
NR_ДАННЫХ = {1: 22, 2: 10}


@lru_cache(maxsize=1)
def _dvb() -> Dict[str, dict]:
    return json.loads((ДАННЫЕ / "ldpc_dvb.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _nr() -> dict:
    return json.loads((ДАННЫЕ / "ldpc_nr.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _прочие() -> Dict[str, dict]:
    return json.loads((ДАННЫЕ / "ldpc_prochie.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _wifi() -> Dict[str, dict]:
    return json.loads((ДАННЫЕ / "ldpc_wifi.json").read_text(encoding="utf-8"))


def _nr_набор(bg: int, Z: int) -> Optional[dict]:
    """Набор iLS графа, в который входит размер Z, — с таблицей V; None — такого Z нет."""
    for набор in _nr()["графы"][str(bg)].values():
        if Z in набор["Z"]:
            return набор
    return None


def _nr_имя(имя: str) -> Optional[tuple]:
    """«nr-bg1-z384» → (1, 384); не NR — None."""
    части = имя.lower().split("-")
    if len(части) != 3 or части[0] != "nr" or not части[1].startswith("bg") or not части[2].startswith("z"):
        return None
    try:
        bg, Z = int(части[1][2:]), int(части[2][1:])
    except ValueError:
        return None
    return (bg, Z) if bg in NR_СТОЛБЦОВ and _nr_набор(bg, Z) is not None else None


def есть(имя: str) -> bool:
    return _nr_имя(имя) is not None or имя in _dvb() or имя in _wifi() or имя in _прочие()


def список() -> List[Dict[str, object]]:
    """Все встроенные коды: имя, семейство, n, k, скорость, откуда, выколотые по стандарту."""
    итог = []
    for bg in (1, 2):
        for набор in _nr()["графы"][str(bg)].values():
            for Z in набор["Z"]:
                n, k = NR_СТОЛБЦОВ[bg] * Z, NR_ДАННЫХ[bg] * Z
                итог.append({"имя": f"nr-bg{bg}-z{Z}", "семейство": f"5G NR, базовый граф {bg}",
                             "n": n, "k": k, "скорость": str(Fraction(k, n - 2 * Z)),
                             "откуда": _nr()["откуда"], "выколоты": f"0-{2 * Z - 1}"})
    for имя, к in list(_dvb().items()) + list(_wifi().items()) + list(_прочие().items()):
        итог.append({"имя": имя, "семейство": к["семейство"], "n": к["n"], "k": к["k"],
                     "скорость": к["скорость"], "откуда": к["откуда"], "выколоты": к.get("выколоты", "")})
    return sorted(итог, key=lambda э: (э["семейство"], э["n"], э["k"]))


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
    if имя in _dvb():
        к = _dvb()[имя]
        м = ldpc.из_адресов("\n".join(" ".join(map(str, с)) for с in к["строки"]), к["n"], к["k"])
        м.откуда = к["откуда"]
        return м
    if имя in _wifi():
        к = _wifi()[имя]
        м = ldpc.из_прототипа("\n".join(" ".join(map(str, ряд)) for ряд in к["база"]), к["Z"])
        м.откуда = к["откуда"]
        м.данные = np.arange(к["k"], dtype=np.int64)
        return м
    if имя in _прочие():
        к = _прочие()[имя]
        return ldpc.Матрица(к["n"], [np.array(с, dtype=np.int64) for с in к["строки"]], к["откуда"])
    raise ValueError(f"встроенного кода «{имя}» нет")


#: Перемежение бит DVB-S2 перед модуляцией (EN 302 307-1, 5.3.3): столбцов — бит на символ,
#: порядок чтения столбцов — по GNU Radio gr-dtv (dvbs2_interleaver_bb) и xdsopl/LDPC
#: (itls_handler): у 8PSK при скорости 3/5 — 210, иначе 012; у 16APSK и 32APSK — по порядку.
ПЕРЕМЕЖЕНИЯ_DVB_S2 = (("8PSK", "012"), ("16APSK", "0123"), ("32APSK", "01234"))
СКОРОСТЬ_3_5 = {"dvb-s2-64800-38880", "dvb-s2-16200-9720"}


def перемежения(имя: str) -> List[Tuple[str, str]]:
    """Перемежения бит по стандарту кода: [(модуляция, порядок столбцов)]; у прочих кодов — нет."""
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
    """Схема передачи по стандарту: у 5G NR первые 2Z позиций не передаются, у AR4JA — последние
    4 блок-столбца; у прочих — всё слово. ``перемежение`` — модуляция (8PSK, 16APSK,
    32APSK), перед которой биты кода DVB-S2 перемежаются."""
    from . import ldpc  # noqa: PLC0415
    м = матрица(имя)
    nr = _nr_имя(имя)
    if nr:
        выколоты = np.arange(2 * nr[1], dtype=np.int64)
    else:
        выколоты = ldpc.позиции(str((_dvb().get(имя) or _прочие().get(имя) or {}).get("выколоты", "")), м.n)
    с = ldpc.Схема(м, выколоты, np.array([], dtype=np.int64))
    if перемежение:
        столбцы = dict(перемежения(имя)).get(перемежение)
        if столбцы is None:
            raise ValueError(f"у кода «{имя}» перемежения {перемежение} по стандарту нет")
        с = ldpc.Схема(м, выколоты, np.array([], dtype=np.int64), с.переданы[ldpc.перемежитель(с.длина, столбцы)])
    return с


@lru_cache(maxsize=None)
def проба(имя: str, сколько: int, перемежение: str = "") -> Tuple[int, List[np.ndarray]]:
    """Длина слова по схеме стандарта и проверки для пробы кода на потоке — строятся один раз.

    Матрицы всех кодов в памяти не держатся (у DVB-S2 — по мегабайту и больше),
    а проверки для пробы малы: автомат пробует все коды, не строя матрицы заново.
    """
    from . import ldpc  # noqa: PLC0415
    с = схема(имя, перемежение)
    return с.длина, ldpc.проверки_для_пробы(с, сколько)[0]


@lru_cache(maxsize=1)
def _по_именам() -> Dict[str, Dict[str, object]]:
    return {str(э["имя"]): э for э in список()}


def выколоты_по_стандарту(имя: str) -> bool:
    """Часть позиций слова по стандарту не передаётся (5G NR, AR4JA)."""
    return bool((_по_именам().get(имя) or {}).get("выколоты"))


def _длина_стандарта(э: Dict[str, object]) -> int:
    """Длина слова в потоке по схеме стандарта: n без выколотых позиций."""
    from . import ldpc  # noqa: PLC0415
    return int(э["n"]) - len(ldpc.позиции(str(э["выколоты"]), int(э["n"])))


def подходящие(блок: Optional[int], *, бит: Optional[int] = None, слов_до: int = 64) -> List[str]:
    """Встроенные коды, чьё слово (со схемой стандарта) может лечь в блок данных целое число раз.

    Слово в потоке бывает короче слова стандарта (выкалывание при согласовании
    скорости, укорочение) — но не вдвое: длина слова — от половины до целого.
    Без блока — все коды. ``бит`` — длина потока: код, у которого в нём не
    уместится и трёх слов (хотя бы укороченных вдвое), не пробуется.
    """
    итог = []
    for э in список():
        L0 = _длина_стандарта(э)
        if бит is not None and 3 * (L0 // 2) > бит:
            continue
        if not блок or any(блок % м == 0 and L0 // 2 <= блок // м <= L0 for м in range(1, слов_до + 1)):
            итог.append(str(э["имя"]))
    return итог
