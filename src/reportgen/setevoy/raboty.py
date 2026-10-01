"""Очередь фоновых работ «Анализа пакетов»: честно между людьми, параллельно, в процессах.

Разборы и отборы разных людей идут одновременно — каждый своим процессом
(``python -m reportgen.setevoy.fon …``) с пониженным приоритетом ОС (Windows —
BELOW_NORMAL_PRIORITY_CLASS, прочие — nice 10): разбор пакета на Python — сотни
микросекунд, и в процессе сервера тяжёлая работа одного человека отнимала бы время у
страниц всех (одна блокировка интерпретатора на всех), а с обычным приоритетом —
ядра у страниц. Сколько работ идёт сразу — вдвое больше ядер (``разборов``, до 16;
отборов — по ядрам, до 8): каждый сразу видит свой разбор, ядра делятся поровну;
сверх — очередь. Из очереди первым берётся
работа того, у кого сейчас меньше всего идущих работ этого вида; при равенстве — того, чью
работу запускали давнее (по кругу), затем — кто раньше встал: один человек с десятью
файлами не задерживает остальных.

Работа, которая ждёт данных (файл ещё загружается, приём с сети молчит) или стоит на
паузе, место не занимает: она спит и процессора не ест.

Маленькие файлы (меньше ``процессом_от`` байт) разбираются потоком в сервере: запуск
процесса дольше их разбора. Если процесс не запустить, работа идёт потоком.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hranilishe import прочитать_json

#: Сколько работ одного вида держать запущенными сверх мест (ждущие данных не считаются), не больше.
ЗАПУЩЕННЫХ_НА_МЕСТО = 4
СПЯТ = ("ждёт данных", "пауза")


@dataclass
class Работа:
    вид: str                          # «разбор» или «отбор»
    ключ: str                         # уникально: папка захвата или отбора
    владелец: int
    аргументы: list[str]              # для fon.main после вида
    процессом: bool = True
    ход_файл: Path | None = None      # где работа пишет ход (по нему видно, что она спит)
    поставлена: float = field(default_factory=time.monotonic)
    порядок: int = 0
    процесс: subprocess.Popen | None = None
    поток: threading.Thread | None = None
    команды: queue.Queue | None = None
    кончилась: Callable[[Работа], None] | None = None
    #: Взята из очереди, но процесс (поток) ещё не запущен: жива — иначе соседний обход счёл бы её кончившейся.
    запускается: bool = False

    def жива(self) -> bool:
        if self.запускается:
            return True
        if self.процесс is not None:
            return self.процесс.poll() is None
        return self.поток is not None and self.поток.is_alive()

    def спит(self) -> bool:
        if self.ход_файл is None:
            return False
        return (прочитать_json(self.ход_файл, {}) or {}).get("состояние") in СПЯТ

    def послать(self, команда: dict[str, Any]) -> None:
        if self.процесс is not None:
            with contextlib.suppress(OSError, ValueError, AttributeError):
                self.процесс.stdin.write((json.dumps(команда, ensure_ascii=False) + "\n").encode("utf-8"))
                self.процесс.stdin.flush()
        elif self.команды is not None:
            self.команды.put(команда)

    def ждать(self, секунд: float) -> bool:
        if self.процесс is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.процесс.wait(секунд)
        elif self.поток is not None:
            self.поток.join(секунд)
        return not self.жива()

    def закрыть(self) -> None:
        if self.процесс is not None:
            with contextlib.suppress(OSError, ValueError, AttributeError):
                self.процесс.stdin.close()
            if self.процесс.poll() is None:
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    self.процесс.wait(5)
            if self.процесс.poll() is None:
                with contextlib.suppress(OSError):
                    self.процесс.kill()


def _исполнить(вид: str, аргументы: list[str], команды: queue.Queue) -> None:
    from . import fon  # noqa: PLC0415
    if вид == "разбор":
        fon.Разбор(Path(аргументы[0]), команды).работать()
    else:
        fon.Отбор(Path(аргументы[0]), Path(аргументы[1]), команды).работать()


class Диспетчер:
    """Очередь и запуск работ; один на хранилище захватов."""

    def __init__(self, *, разборов: int = 0, отборов: int = 0, процессы: bool = True,
                 фабрика: Callable[..., subprocess.Popen] | None = None):
        ядер = os.cpu_count() or 2
        # Работы идут с пониженным приоритетом ОС (страницы сервера — впереди), поэтому мест больше, чем ядер:
        # каждый видит свой разбор сразу, а ядра делятся поровну, а не «кто первый встал».
        self.места = {"разбор": разборов or max(2, min(16, 2 * ядер)), "отбор": отборов or max(2, min(8, ядер))}
        self.процессы = процессы
        self.фабрика = фабрика or subprocess.Popen
        self._lock = threading.Lock()
        self._событие = threading.Event()
        self._ждут: list[Работа] = []
        self._идут: dict[str, Работа] = {}
        self._счёт = itertools.count()
        #: Когда (по счёту запусков) у человека последний раз запускалась работа: при равенстве идущих
        #: первым идёт тот, кого обслуживали давнее, — по кругу, а не все файлы первого подряд.
        self._запуски = itertools.count()
        self._последний: dict[int, int] = {}
        self._поток: threading.Thread | None = None
        self._закрыт = False

    # -- снаружи --

    def поставить(self, работа: Работа) -> None:
        with self._lock:
            self.снять_из_очереди(работа.ключ, _под_замком=True)
            работа.порядок = next(self._счёт)
            работа.поставлена = time.monotonic()
            self._ждут.append(работа)
            if self._поток is None or not self._поток.is_alive():
                self._поток = threading.Thread(target=self._цикл, daemon=True, name="reportgen-pakety-ochered")
                self._поток.start()
        self._событие.set()
        self.обойти()

    def снять_из_очереди(self, ключ: str, *, _под_замком: bool = False) -> bool:
        def снять() -> bool:
            было = len(self._ждут)
            self._ждут = [р for р in self._ждут if р.ключ != ключ]
            return len(self._ждут) != было
        if _под_замком:
            return снять()
        with self._lock:
            return снять()

    def команда(self, ключ: str, команда: dict[str, Any]) -> bool:
        with self._lock:
            р = self._идут.get(ключ)
        if р is None or not р.жива():
            return False
        р.послать(команда)
        return True

    def остановить(self, ключ: str, почему: str = "остановлен", ждать: float = 10.0) -> None:
        """Снять с очереди или остановить и дождаться (папку после этого можно удалять)."""
        self.снять_из_очереди(ключ)
        with self._lock:
            р = self._идут.get(ключ)
        if р is None:
            return
        р.послать({"к": "стоп", "почему": почему})
        р.ждать(ждать)
        р.закрыть()
        with self._lock:
            if self._идут.get(ключ) is р:
                del self._идут[ключ]
        self._событие.set()

    def идёт(self, ключ: str) -> bool:
        with self._lock:
            р = self._идут.get(ключ)
        return р is not None and р.жива()

    def в_очереди(self, ключ: str) -> int | None:
        """Место в очереди своего вида (с 1) или None — не в очереди."""
        with self._lock:
            for р in self._ждут:
                if р.ключ == ключ:
                    своего_вида = sorted((x for x in self._ждут if x.вид == р.вид), key=lambda x: x.порядок)
                    return своего_вида.index(р) + 1
        return None

    def сводка(self) -> dict[str, Any]:
        with self._lock:
            return {"места": dict(self.места),
                    "идут": {в: sum(1 for р in self._идут.values() if р.вид == в and р.жива()) for в in self.места},
                    "ждут": {в: sum(1 for р in self._ждут if р.вид == в) for в in self.места}}

    def закрыть(self) -> None:
        self._закрыт = True
        with self._lock:
            работы = list(self._идут.values())
            self._ждут = []
        for р in работы:
            р.послать({"к": "стоп", "почему": "сервер останавливается"})
        for р in работы:
            р.ждать(5)
            р.закрыть()
        self._событие.set()

    # -- внутри --

    def _цикл(self) -> None:
        while not self._закрыт:
            self._событие.wait(0.5)
            self._событие.clear()
            with contextlib.suppress(Exception):
                self.обойти()

    def обойти(self) -> None:
        """Убрать кончившиеся работы и запустить ждущие, сколько позволяют места."""
        кончились = []
        запустить: list[Работа] = []
        with self._lock:
            for ключ, р in list(self._идут.items()):
                if not р.жива():
                    кончились.append(р)
                    del self._идут[ключ]
            for вид, мест in self.места.items():
                идут = [р for р in self._идут.values() if р.вид == вид]
                заняты = sum(1 for р in идут if not р.спит())
                while заняты < мест and len(идут) < мест * ЗАПУЩЕННЫХ_НА_МЕСТО:
                    ждут = [р for р in self._ждут if р.вид == вид]
                    if not ждут:
                        break
                    у_кого: dict[int, int] = {}
                    for р in идут:
                        у_кого[р.владелец] = у_кого.get(р.владелец, 0) + 1
                    р = min(ждут, key=lambda x: (у_кого.get(x.владелец, 0), self._последний.get(x.владелец, -1), x.порядок))
                    self._последний[р.владелец] = next(self._запуски)
                    self._ждут.remove(р)
                    р.запускается = True
                    self._идут[р.ключ] = р
                    идут.append(р)
                    заняты += 1
                    запустить.append(р)
        for р in кончились:
            р.закрыть()
            if р.кончилась is not None:
                with contextlib.suppress(Exception):
                    р.кончилась(р)
        for р in запустить:
            self._запустить(р)

    def _запустить(self, р: Работа) -> None:
        try:
            self._запустить_(р)
        finally:
            р.запускается = False

    def _запустить_(self, р: Работа) -> None:
        if р.процессом and self.процессы:
            import reportgen  # noqa: PLC0415
            корень = str(Path(reportgen.__file__).resolve().parents[1])
            среда = dict(os.environ)
            среда["PYTHONPATH"] = корень + (os.pathsep + среда["PYTHONPATH"] if среда.get("PYTHONPATH") else "")
            журнал_путь = Path(р.аргументы[-1]) / "журнал.txt"
            try:
                with open(журнал_путь, "ab") as журнал:
                    флаги = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                             if sys.platform == "win32" else 0)
                    р.процесс = self.фабрика([sys.executable, "-m", "reportgen.setevoy.fon", р.вид, *р.аргументы],
                                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=журнал,
                                             env=среда, cwd=str(Path(р.аргументы[-1])), creationflags=флаги)
                return
            except OSError as ошибка:
                with contextlib.suppress(OSError):
                    журнал_путь.write_text(f"процесс не запустился ({ошибка}) — работа идёт потоком", encoding="utf-8")
        р.команды = queue.Queue()
        р.поток = threading.Thread(target=_исполнить, args=(р.вид, р.аргументы, р.команды), daemon=True,
                                   name=f"reportgen-pakety-{р.вид}")
        р.поток.start()
