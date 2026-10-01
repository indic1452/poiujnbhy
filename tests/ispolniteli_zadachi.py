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
