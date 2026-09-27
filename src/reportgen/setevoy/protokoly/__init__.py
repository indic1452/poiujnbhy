"""Дополнительные разборщики протоколов — по группам, каждый модуль регистрирует себя сам.

Модуль группы добавляет свои разборщики в таблицы ``razbor`` (ДОП_ETHERTYPE,
ДОП_IP, ДОП_LLC, ДОП_SCTP_PPID, ДОП_SCTP_ПОРТ, ДОП_PPP) и ``prilozh``
(ПОРТЫ_TCP, ПОРТЫ_UDP, КАК, ЭВРИСТИКИ_TCP, ЭВРИСТИКИ_UDP). Разборщик
прикладного уровня возвращает True, если данные его, — иначе ничего не
оставляет (уровни откатываются). Поля — с местом в байтах и именем для
фильтра в духе Wireshark (``diameter.cmd.code``, ``m3ua.class``).
"""

import importlib
import pkgutil

for _модуль in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_модуль.name}")
