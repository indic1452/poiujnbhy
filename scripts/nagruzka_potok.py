"""Нагрузочная проверка разбора потоков: N человек одновременно на одном сервере.

Запуск (из корня репозитория; не входит в обычный прогон тестов — минуты и все ядра):

    python scripts/nagruzka_potok.py                       # 10 человек, 3 из них ещё и с пакетами
    python scripts/nagruzka_potok.py --lyudey 4 --paketov 1 --vyhod итог.json
    python scripts/nagruzka_potok.py --src /путь/к/другой/версии/src   # сравнить «до» и «после»

Что делает:

1. Поднимает настоящий сервер (``python -m reportgen serve``) на свободном порту с пустой папкой
   данных во временном каталоге и заводит учётки ``u01``…``uNN`` (как тесты входа).
2. Одиночный прогон: один человек по очереди разбирает каждый из файлов — время и итог
   каждого разбора (эталон), затем один раз проходит операции стола — время каждой операции.
3. Нагрузка: все люди одновременно создают сессии, грузят свои файлы с автоматическим
   разбором, ждут итог, работают на столе (поиск периода, автокорреляция, скремблер,
   моддекодер просмотр и «Дек. всех», ТКБ, NIST, таблица кадров), листают биты; первые
   ``--paketov`` человек ещё и грузят захват в «Пакеты» через существующий API. У каждого
   параллельно «вкладка», которая всё время дёргает лёгкие запросы (состояние, сессия,
   окно бит, сырые байты, сетка, дерево, разметка) — их задержки и меряются.
4. Печатает таблицу: задержки лёгких запросов (p50/p95/наибольшая), тяжёлых — по видам,
   время разборов против одиночного, ошибки (5xx, обрывы, сроки), целостность (итог каждого
   разбора под нагрузкой совпадает с итогом одиночного прогона того же файла).

Только стандартная библиотека и numpy (для синтетики — генераторы из tests/potok_sintez.py).
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import os
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

КОРЕНЬ = Path(__file__).resolve().parents[1]
ОБРАЗЕЦ = КОРЕНЬ.parent / "250_V_8085_8PSK_7556_K2964.bit"
ПАРОЛЬ = "пароль123"
#: Срок одного запроса клиента, с: дольше — «обрыв по сроку» (ошибка).
СРОК_ЗАПРОСА = 120.0
#: Пауза «вкладки» между лёгкими запросами, с.
ПАУЗА_ВКЛАДКИ = 0.25


# -- данные ---------------------------------------------------------------------------------

def файлы_потоков(сколько: int, образец: Path | None) -> list[tuple[str, bytes]]:
    """Разные файлы потоков: синтетика из генераторов тестов (+ начало образца K2964, если есть)."""
    sys.path[:0] = [str(КОРЕНЬ / "tests"), str(КОРЕНЬ / "src")]
    import numpy as np  # noqa: PLC0415

    import potok_sintez as с  # noqa: PLC0415
    from reportgen.potok.bity import в_байты, в_биты  # noqa: PLC0415

    def цепочка(n: int, сид: int) -> bytes:
        x = в_биты(с.hdlc(с.пакеты_ip(n, сид=сид), флагов_между=4))
        код = с.свёрточный(с.скремблировать(x, (3, 20)))
        return в_байты(код ^ (np.random.default_rng(сид).random(len(код)) < 0.005).astype(np.uint8))

    заготовки = [
        ("e1-a.bin", lambda: с.e1(2000, сид=3)),
        ("hdlc-a.bin", lambda: с.hdlc(с.пакеты_ip(400, сид=1))),
        ("dvb-a.bin", lambda: с.dvb(300, сид=4)),
        ("svyortka-a.bin", lambda: цепочка(24, 2)),
        ("e1-b.bin", lambda: с.e1(1500, сид=7)),
        ("hdlc-b.bin", lambda: с.hdlc(с.пакеты_ip(300, сид=5))),
        ("dvb-b.bin", lambda: с.dvb(400, сид=9)),
        ("slip-a.bin", lambda: с.slip(с.пакеты_ip(300, сид=11))),
        ("hdlc-c.bin", lambda: с.hdlc(с.пакеты_ip(250, сид=13), fcs=32)),
        ("svyortka-b.bin", lambda: цепочка(20, 6)),
    ]
    итог = [(имя, bytes(сделать())) for имя, сделать in заготовки]
    if образец is not None and образец.exists():
        # Начало настоящей записи 8PSK: целиком она разбирается десятью минутами — для нагрузки хватит куска (16 КБ — секунды).
        итог[3] = ("k2964-nachalo.bit", образец.read_bytes()[:16 * 1024])
    return [итог[i % len(итог)] for i in range(сколько)]


def захват_pcap(сид: int) -> bytes:
    import struct  # noqa: PLC0415

    sys.path[:0] = [str(КОРЕНЬ / "tests")]
    import potok_sintez as с  # noqa: PLC0415
    пакеты = с.пакеты_ip(600, сид=сид)
    return struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101) + b"".join(
        struct.pack("<IIII", 1700000000 + i, 0, len(п), len(п)) + п for i, п in enumerate(пакеты))


# -- сервер ---------------------------------------------------------------------------------

def свободный_порт() -> int:
    with socket.socket() as с:
        с.bind(("127.0.0.1", 0))
        return с.getsockname()[1]


def завести_людей(папка: Path, src: Path, сколько: int) -> None:
    код = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "from reportgen.store.db import Database\n"
        "from reportgen.store.repo import Repositories\n"
        "r = Repositories(Database(sys.argv[2]))\n"
        "for i in range(1, int(sys.argv[3]) + 1):\n"
        "    r.users.create(f'u{i:02d}', sys.argv[4], f'Нагрузкин {i:02d}', 'engineer')\n"
    )
    subprocess.run([sys.executable, "-c", код, str(src), str(папка / "reportgen.db"), str(сколько), ПАРОЛЬ],
                   check=True)


class Сервер:
    def __init__(self, src: Path, папка: Path, порт: int, окружение: dict[str, str]):
        env = {**os.environ, "PYTHONPATH": str(src), "REPORTGEN_DATA_DIR": str(папка),
               "REPORTGEN_DB_PATH": str(папка / "reportgen.db"), "REPORTGEN_AUTH_ENABLED": "1",
               "REPORTGEN_EMBED_ENABLED": "0", "REPORTGEN_RERANK_ENABLED": "0", **окружение}
        self.журнал = open(папка / "server.log", "wb")  # noqa: SIM115
        self.процесс = subprocess.Popen([sys.executable, "-m", "reportgen", "serve", "--host", "127.0.0.1",
                                         "--port", str(порт)], cwd=src.parent, env=env,
                                        stdout=self.журнал, stderr=subprocess.STDOUT)
        self.адрес = f"http://127.0.0.1:{порт}"
        for _ in range(600):
            try:
                urllib.request.urlopen(self.адрес + "/api/health", timeout=2).read()
                return
            except OSError:
                if self.процесс.poll() is not None:
                    raise RuntimeError("сервер не поднялся: см. " + str(папка / "server.log")) from None
                time.sleep(0.1)
        raise RuntimeError("сервер не поднялся за 60 с")

    def остановить(self) -> None:
        self.процесс.terminate()
        try:
            self.процесс.wait(20)
        except subprocess.TimeoutExpired:
            self.процесс.kill()
        self.журнал.close()


# -- клиент ---------------------------------------------------------------------------------

class Замеры:
    """Задержки и ошибки всех людей: потокобезопасно."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.лёгкие: list[float] = []
        self.тяжёлые: dict[str, list[float]] = {}
        self.ошибки: list[str] = []
        self.отказы_4xx: list[str] = []

    def лёгкий(self, сек: float) -> None:
        with self._lock:
            self.лёгкие.append(сек)

    def тяжёлый(self, вид: str, сек: float) -> None:
        with self._lock:
            self.тяжёлые.setdefault(вид, []).append(сек)

    def ошибка(self, текст: str) -> None:
        with self._lock:
            self.ошибки.append(текст)

    def отказ(self, текст: str) -> None:
        with self._lock:
            self.отказы_4xx.append(текст)


class Человек:
    def __init__(self, адрес: str, логин: str, замеры: Замеры):
        self.адрес, self.логин, self.замеры = адрес, логин, замеры
        self.открыватель = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def запрос(self, метод: str, путь: str, *, тело: Any = None, файл: tuple[str, bytes] | None = None,
               поля: dict[str, str] | None = None, вид: str = "", ждать: tuple[int, ...] = (200,)) -> Any:
        """Запрос; ``вид`` — «лёгкий» или имя тяжёлой операции (задержка — в замеры)."""
        заголовки = {}
        данные = None
        if файл is not None:
            граница = "----nagruzka" + secrets.token_hex(8)
            части = []
            for имя, значение in (поля or {}).items():
                части.append(f'--{граница}\r\nContent-Disposition: form-data; name="{имя}"\r\n\r\n{значение}\r\n'
                             .encode())
            части.append(f'--{граница}\r\nContent-Disposition: form-data; name="file"; filename="{файл[0]}"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + файл[1] + b"\r\n")
            части.append(f"--{граница}--\r\n".encode())
            данные = b"".join(части)
            заголовки["Content-Type"] = f"multipart/form-data; boundary={граница}"
        elif тело is not None:
            данные = json.dumps(тело).encode()
            заголовки["Content-Type"] = "application/json"
        запрос = urllib.request.Request(self.адрес + путь, data=данные, method=метод, headers=заголовки)
        начало = time.perf_counter()
        try:
            with self.открыватель.open(запрос, timeout=СРОК_ЗАПРОСА) as ответ:
                сырое = ответ.read()
                код = ответ.status
                тип = ответ.headers.get("Content-Type", "")
        except urllib.error.HTTPError as ошибка:
            сырое, код, тип = ошибка.read(), ошибка.code, ошибка.headers.get("Content-Type", "")
        except TimeoutError:
            self.замеры.ошибка(f"{self.логин} {метод} {путь}: срок {СРОК_ЗАПРОСА:g} с")
            return None
        except OSError as ошибка:
            self.замеры.ошибка(f"{self.логин} {метод} {путь}: {ошибка}")
            return None
        прошло = time.perf_counter() - начало
        if вид == "лёгкий":
            self.замеры.лёгкий(прошло)
        elif вид:
            self.замеры.тяжёлый(вид, прошло)
        if код >= 500:
            self.замеры.ошибка(f"{self.логин} {метод} {путь}: {код} {сырое[:200]!r}")
            return None
        if код not in ждать:
            self.замеры.отказ(f"{self.логин} {метод} {путь}: {код} {сырое[:200]!r}")
            return None
        return json.loads(сырое) if "json" in тип else сырое

    def войти(self) -> None:
        if self.запрос("POST", "/api/auth/login", тело={"login": self.логин, "password": ПАРОЛЬ}) is None:
            raise RuntimeError(f"{self.логин}: вход не удался")

    def дождаться(self, ид: str, срок: float = 1800.0) -> dict[str, Any] | None:
        конец = time.monotonic() + срок
        while time.monotonic() < конец:
            с = self.запрос("GET", f"/api/potok/{ид}", вид="лёгкий")
            if с and с.get("состояние") not in ("ждёт", "идёт"):
                return с
            time.sleep(0.5)
        self.замеры.ошибка(f"{self.логин}: разбор {ид} не закончился за {срок:g} с")
        return None


def подпись(состояние: dict[str, Any] | None) -> list[Any]:
    """Итог разбора для сравнения: этапы (уровень, что, выгрузка и её размер) и не найденное."""
    if not состояние:
        return ["нет"]
    return [состояние.get("состояние"),
            [(э.get("уровень"), э.get("что"), э.get("выгрузка"), э.get("выгрузка_байт"))
             for э in состояние.get("этапы") or []]]


def плоскость_8(человек: Человек) -> dict[str, Any]:
    return {"модуляция": "ФМ8", "демодулятор": "ФМ8 Грей", "вид_кода": "DVB-S2 8PSK"}


def операции_стола(человек: Человек, ид: str) -> None:
    """Работа на столе над массивом 0 задания: тяжёлые операции окна поиска и лёгкие просмотры."""
    з = f"/api/potok/{ид}"
    шаги: list[tuple[str, str, str, Any]] = [
        ("периоды", "GET", f"{з}/periods?stage=0", None),
        ("автокорреляция", "GET", f"{з}/autocorr?stage=0&max=4096", None),
        ("поиск периода", "POST", f"{з}/period-search", {"stage": 0, "from": 8, "to": 4096}),
        ("скремблер", "POST", f"{з}/scrambler-search", {"stage": 0, "degree": 9, "taps": 2}),
        ("моддекодер просмотр", "POST", f"{з}/moddecoder/preview",
         {"stage": 0, "параметры": плоскость_8(человек), "бит": 4096}),
        ("моддекодер все", "POST", f"{з}/moddecoder/all",
         {"stage": 0, "параметры": плоскость_8(человек), "с": 1, "по": 96, "лучших": 10}),
        ("ткб поиск", "POST", f"{з}/tkb/search", {"stage": 0, "с": 0, "по": 40}),
        ("статистика NIST", "POST", f"{з}/stats", {"stage": 0, "from": 0, "length": 200000}),
        ("таблица кадров", "POST", f"{з}/frames", {"stage": 0, "period": 256, "limit": 200}),
        ("поиск образца", "POST", f"{з}/search", {"stage": 0, "pattern": "7E", "kind": "hex"}),
        ("слой: обрезка", "POST", f"{з}/try", {"stage": 0, "steps": [
            {"вид": "слой", "слой": "обрезка начало 8 конец 0", "вкл": True}]}),
    ]
    for вид, метод, путь, тело in шаги:
        человек.запрос(метод, путь, тело=тело, вид=вид, ждать=(200, 400))
        # Между операциями человек листает биты.
        человек.запрос("GET", f"{з}/bits?stage=0&start=0&count=8192", вид="лёгкий")
        человек.запрос("GET", f"{з}/grid?stage=0&period=256&rows=100&cols=256", вид="лёгкий")


def вкладка(человек: Человек, где: dict[str, str], стоп: threading.Event) -> None:
    """Вторая вкладка того же человека: всё время лёгкие запросы — их задержка и есть отзывчивость."""
    номер = 0
    while not стоп.is_set():
        ид, сессия = где.get("ид"), где.get("сессия")
        пути = ["/api/sessions", "/api/potok"]
        if сессия:
            пути.append(f"/api/sessions/{сессия}")
        if ид:
            пути += [f"/api/potok/{ид}", f"/api/potok/{ид}/bits?stage=0&start={номер * 4096 % 65536}&count=4096",
                     f"/api/potok/{ид}/raw?stage=0&offset=0&length=65536", f"/api/potok/{ид}/marks?stage=0",
                     f"/api/potok/{ид}/tree", f"/api/potok/{ид}/journal"]
        человек.запрос("GET", пути[номер % len(пути)], вид="лёгкий")
        номер += 1
        стоп.wait(ПАУЗА_ВКЛАДКИ)


def работа_человека(человек: Человек, файл: tuple[str, bytes], пакеты: bytes | None,
                    старт: threading.Barrier, итоги: dict[str, Any], стоп_вкладок: threading.Event) -> None:
    где: dict[str, str] = {}
    вкладки = threading.Thread(target=вкладка, args=(человек, где, стоп_вкладок), daemon=True)
    старт.wait()
    вкладки.start()
    начало = time.monotonic()
    сессия = человек.запрос("POST", "/api/sessions", тело={"name": f"нагрузка {человек.логин}"})
    if not сессия:
        return
    где["сессия"] = сессия["id"]
    захват = None
    if пакеты is not None:
        захват = человек.запрос("POST", "/api/pakety", файл=(f"{человек.логин}.pcap", пакеты))
    ответ = человек.запрос("POST", f"/api/sessions/{сессия['id']}/files", файл=файл, поля={"analyze": "1"})
    if not ответ:
        return
    где["ид"] = ответ["id"]
    состояние = человек.дождаться(ответ["id"])
    итоги[человек.логин] = {"файл": файл[0], "подпись": подпись(состояние),
                            "разбор_с": (состояние or {}).get("секунд"),
                            "до_итога_с": round(time.monotonic() - начало, 2)}
    операции_стола(человек, ответ["id"])
    if захват:
        for _ in range(600):
            с = человек.запрос("GET", f"/api/pakety/{захват['id']}", вид="лёгкий")
            if с and с.get("состояние") not in ("ждёт", "идёт"):
                итоги[человек.логин]["пакеты"] = с.get("состояние")
                break
            time.sleep(0.5)
        человек.запрос("GET", f"/api/pakety/{захват['id']}/list?limit=200", вид="лёгкий")
    итоги[человек.логин]["всё_с"] = round(time.monotonic() - начало, 2)


# -- отчёт ----------------------------------------------------------------------------------

def доля(ряд: list[float], p: float) -> float:
    if not ряд:
        return float("nan")
    ряд = sorted(ряд)
    return ряд[min(len(ряд) - 1, max(0, math.ceil(p * len(ряд)) - 1))]


def мс(x: float) -> str:
    return "—" if x != x else f"{x * 1000:.0f}"


def прогон(src: Path, люди: int, пакетов: int, образец: Path | None, окружение: dict[str, str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nagruzka-") as tmp:
        папка = Path(tmp)
        завести_людей(папка, src, люди)
        сервер = Сервер(src, папка, свободный_порт(), окружение)
        try:
            файлы = файлы_потоков(люди, образец)
            # 1. Одиночный прогон: эталон времени и итога каждого файла, затем операции стола.
            один = Человек(сервер.адрес, "u01", Замеры())
            один.войти()
            эталон: dict[str, Any] = {}
            for имя, данные in dict(файлы).items():
                начало = time.monotonic()
                ид = один.запрос("POST", "/api/potok", файл=(имя, данные))["id"]
                состояние = один.дождаться(ид)
                эталон[имя] = {"подпись": подпись(состояние), "разбор_с": (состояние or {}).get("секунд"),
                               "до_итога_с": round(time.monotonic() - начало, 2)}
                print(f"  одиночный {имя}: {эталон[имя]['до_итога_с']} с", flush=True)
            ид = один.запрос("POST", "/api/potok", файл=файлы[0])["id"]
            один.дождаться(ид)
            операции_стола(один, ид)
            одиночные_операции = {в: statistics.median(р) for в, р in один.замеры.тяжёлые.items()}
            одиночные_лёгкие = list(один.замеры.лёгкие)
            # 2. Нагрузка: все одновременно.
            замеры = Замеры()
            народ = [Человек(сервер.адрес, f"u{i:02d}", замеры) for i in range(1, люди + 1)]
            for ч in народ:
                ч.войти()
            старт = threading.Barrier(люди)
            стоп = threading.Event()
            итоги: dict[str, Any] = {}
            нити = [threading.Thread(target=работа_человека, args=(ч, файлы[i], захват_pcap(100 + i) if i < пакетов
                                                                     else None, старт, итоги, стоп))
                    for i, ч in enumerate(народ)]
            начало = time.monotonic()
            for н in нити:
                н.start()
            for н in нити:
                н.join()
            стоп.set()
            всего = time.monotonic() - начало
            разборы_до = max((и["до_итога_с"] for и in итоги.values()), default=float("nan"))
            несовпали = [f"{л}: {и['файл']}" for л, и in итоги.items() if и["подпись"] != эталон[и["файл"]]["подпись"]]
            return {
                "люди": люди, "пакетов": пакетов, "ядер": os.cpu_count(), "окружение": окружение,
                "эталон": эталон, "одиночные_операции_с": одиночные_операции,
                "одиночные_лёгкие_мс": {"p50": доля(одиночные_лёгкие, .5) * 1000, "p95": доля(одиночные_лёгкие, .95) * 1000},
                "итоги": итоги, "всего_с": round(всего, 2), "разборы_все_готовы_с": разборы_до,
                "сумма_одиночных_с": round(sum(эталон[и["файл"]]["до_итога_с"] for и in итоги.values()), 2),
                "наибольший_одиночный_с": max(эталон[и["файл"]]["до_итога_с"] for и in итоги.values()),
                "лёгкие": {"n": len(замеры.лёгкие), "p50_мс": доля(замеры.лёгкие, .5) * 1000,
                           "p95_мс": доля(замеры.лёгкие, .95) * 1000, "max_мс": max(замеры.лёгкие, default=0) * 1000},
                "тяжёлые": {в: {"n": len(р), "p50_с": доля(р, .5), "max_с": max(р)} for в, р in замеры.тяжёлые.items()},
                "ошибки": замеры.ошибки, "отказы_4xx": замеры.отказы_4xx, "несовпали": несовпали,
            }
        finally:
            сервер.остановить()


def напечатать(итог: dict[str, Any]) -> None:
    л = итог["лёгкие"]
    print(f"\nЛюдей: {итог['люди']} (с пакетами {итог['пакетов']}), ядер: {итог['ядер']}, "
          f"настройки: {итог['окружение'] or 'по умолчанию'}")
    print(f"Лёгкие запросы под нагрузкой: n={л['n']}, p50 {л['p50_мс']:.0f} мс, p95 {л['p95_мс']:.0f} мс, "
          f"наибольшая {л['max_мс']:.0f} мс (в одиночку p50 {итог['одиночные_лёгкие_мс']['p50']:.0f}, "
          f"p95 {итог['одиночные_лёгкие_мс']['p95']:.0f} мс)")
    print(f"Разборы: все готовы через {итог['разборы_все_готовы_с']:.1f} с; сумма одиночных "
          f"{итог['сумма_одиночных_с']:.1f} с, самый долгий одиночный {итог['наибольший_одиночный_с']:.1f} с; "
          f"вся работа {итог['всего_с']:.1f} с")
    print("Тяжёлые операции стола (под нагрузкой p50 / наибольшая, в одиночку):")
    for в, р in sorted(итог["тяжёлые"].items()):
        один = итог["одиночные_операции_с"].get(в)
        print(f"  {в:22s} {р['p50_с'] * 1000:7.0f} / {р['max_с'] * 1000:7.0f} мс   "
              f"{'—' if один is None else f'{один * 1000:.0f} мс'}")
    print(f"Ошибок (5xx, обрывы, сроки): {len(итог['ошибки'])}; отказов 4xx: {len(итог['отказы_4xx'])}; "
          f"итог разбора не совпал с одиночным: {len(итог['несовпали'])}")
    for строка in (итог["ошибки"] + итог["отказы_4xx"] + итог["несовпали"])[:20]:
        print("   ", строка)


def main(argv: list[str] | None = None) -> int:
    п = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    п.add_argument("--lyudey", type=int, default=10, help="сколько человек одновременно (10)")
    п.add_argument("--paketov", type=int, default=3, help="сколько из них ещё грузят захват в «Пакеты» (3)")
    п.add_argument("--src", type=Path, default=КОРЕНЬ / "src", help="папка src проверяемой версии")
    п.add_argument("--obrazec", type=Path, default=ОБРАЗЕЦ, help="запись K2964 (если есть — кусок в нагрузке)")
    п.add_argument("--vyhod", type=Path, default=None, help="записать итог в JSON")
    п.add_argument("--env", action="append", default=[], help="переменная окружения сервера: ИМЯ=значение")
    а = п.parse_args(argv)
    окружение = dict(е.split("=", 1) for е in а.env)
    итог = прогон(а.src.resolve(), а.lyudey, а.paketov, а.obrazec, окружение)
    напечатать(итог)
    if а.vyhod:
        а.vyhod.write_text(json.dumps(итог, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if итог["ошибки"] or итог["несовпали"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
