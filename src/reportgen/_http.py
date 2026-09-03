"""Единственная точка выхода в сеть — и она принципиально мимо прокси.

Все адреса комплекса локальные: llama-server, эмбеддер и реранкер слушают
127.0.0.1. Но ``urllib.request.urlopen`` пользуется опенером по умолчанию, а
тот всегда содержит ``ProxyHandler(getproxies())``: под Windows это
переменные ``*_proxy``, а если их нет — ветка реестра
``HKCU\\...\\Internet Settings`` (ProxyEnable/ProxyServer). Корпоративный образ
почти всегда приезжает с настроенным прокси.

Исключение ``<local>`` в «Свойствах браузера» здесь не спасает: CPython
обходит по нему только имена **без точки**, а «127.0.0.1» точки содержит. В
результате запрос к своей же модели уходит на недоступный в изолированном
контуре прокси — и генерация отчёта падает по таймауту, хотя llama-server
работает и отвечает в браузере.

Поэтому опенер строится явно с пустым ``ProxyHandler``: прямое соединение,
что бы ни было настроено в системе. Заодно это гарантия, что содержимое
библиотеки и текст отчёта физически не могут уйти на сторонний сервер.
"""

from __future__ import annotations

import json
import urllib.request

#: Опенер без единого прокси-обработчика.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def urlopen(request, timeout=None):
    """``urlopen`` в обход системного прокси. Ошибки — те же, что у urllib."""
    return _OPENER.open(request, timeout=timeout)


def explain(error: BaseException, limit: int = 300) -> str:
    """Ошибка вместе с тем, что сервер написал в теле ответа.

    ``HTTPError`` печатается как «HTTP Error 500: Internal Server Error» —
    и это всё, что видел человек. А настоящая причина лежит в теле ответа:
    llama.cpp пишет туда, например, «input is too large to process. increase
    the physical batch size». Без неё «Internal Server Error» на экране
    библиотеки — это тупик: сервер работает, а что ему не нравится, узнать
    неоткуда.
    """
    text = str(error)
    body = _body_of(error)
    return f"{text}: {body}" if body else text


def _body_of(error: BaseException) -> str:
    read = getattr(error, "read", None)
    if read is None:
        return ""
    try:
        raw = read()
    except Exception:                  # noqa: BLE001 — тело читается один раз
        return ""
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:                  # noqa: BLE001
        return ""
    # llama.cpp и vLLM отвечают JSON-ом {"error": {"message": "..."}}.
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        inner = parsed.get("error")
        if isinstance(inner, dict):
            text = str(inner.get("message") or inner)
        elif inner:
            text = str(inner)
        elif parsed.get("message"):
            text = str(parsed["message"])
    return " ".join(text.split())[:300]


def refused(error: BaseException) -> bool:
    """Соединение отвергнуто: сервер не поднят, повторять бессмысленно.

    «Connection refused» приходит мгновенно — на том конце никто не слушает,
    и через секунду не начнёт. Три попытки с паузами 1 и 2 секунды
    добавляли ровно три секунды к КАЖДОМУ запросу, то есть к каждому
    вопросу помощника, не давая ни одного шанса на успех. Занятый или
    медленный сервер (таймаут, разрыв соединения) — другое дело: там
    повтор помогает, и его мы оставляем.

    Причина у ``URLError`` лежит в ``reason``, поэтому смотрим и туда.
    """
    if isinstance(error, ConnectionRefusedError):
        return True
    reason = getattr(error, "reason", None)
    return isinstance(reason, ConnectionRefusedError)
