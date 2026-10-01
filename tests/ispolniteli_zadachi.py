"""Задачи для проверки исполнителей: функции уровня модуля — их передаёт pickle в процесс."""

from __future__ import annotations

import os
import time

from reportgen.potok import ispolniteli


def сложить(a, b):
    return a + b


def pid():
    return os.getpid()


def спать(сек, итог=None):
    time.sleep(сек)
    return итог


def с_ходом(сколько, пауза=0.0):
    for i in range(сколько):
        ispolniteli.сообщить("ход", f"шаг {i}")
        time.sleep(пауза)
    return сколько


def упасть(текст):
    raise ValueError(текст)


def упасть_странно():
    class Местная(Exception):
        pass
    raise Местная("местная ошибка")


def вечно_с_ходом():
    while True:
        ispolniteli.сообщить("ход", "ещё")
        time.sleep(0.01)


def вечно_глухо():
    while True:
        time.sleep(0.05)


def выйти(код):
    os._exit(код)


def непередаваемый_итог():
    return lambda: 1


def глотает_исключения():
    """Широкий except Exception не должен глотать отмену."""
    for _ in range(10000):
        try:
            ispolniteli.сообщить("ход", "x")
            time.sleep(0.01)
        except Exception:  # noqa: BLE001
            pass
    return "не отменилась"


def медленное_задание(папка, каталоги, разбирать, сек=1.5):
    """Вместо разбора: пишет «идёт», шлёт ход, спит ``сек`` (останавливается отменой), итог — «готово»."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from reportgen.fayly import записать_атомарно  # noqa: PLC0415
    путь = Path(папка) / "состояние.json"
    с = json.loads(путь.read_text(encoding="utf-8"))
    с.update(состояние="идёт", начато=time.time())
    записать_атомарно(путь, json.dumps(с, ensure_ascii=False))
    конец = time.monotonic() + сек
    шаг = 0
    while time.monotonic() < конец:
        ispolniteli.сообщить("ход", f"медленно {шаг} pid {os.getpid()}")
        шаг += 1
        time.sleep(0.05)
    return {"состояние": "готово", "закончено": time.time(), "секунд": сек, "pid": os.getpid()}


def упавшее_задание(папка, каталоги, разбирать, текст="разбор упал"):
    raise ValueError(текст)
