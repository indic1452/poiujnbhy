"""Поднятие временного локального PostgreSQL для тестов и демо.

initdb/postgres не запускаются от root, поэтому под root процесс поднимается
от системного пользователя ``postgres`` через ``su``. Если тесты идут не от
root — команды выполняются напрямую.
"""
from __future__ import annotations

import glob
import os
import shlex
import shutil
import socket
import subprocess
import tempfile


def _pg_bin() -> str:
    for base in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if os.path.exists(os.path.join(base, "initdb")):
            return base
    # из PATH
    initdb = shutil.which("initdb")
    if initdb:
        return os.path.dirname(initdb)
    raise RuntimeError("PostgreSQL (initdb) не найден")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LocalPostgres:
    def __init__(self, dbname: str = "newsdb") -> None:
        self.bin = _pg_bin()
        self.dbname = dbname
        self.tmp = tempfile.mkdtemp(prefix="pgtest_")
        self.data = os.path.join(self.tmp, "data")
        self.sock = os.path.join(self.tmp, "sock")
        self.port = _free_port()
        self._as_postgres = os.geteuid() == 0
        os.makedirs(self.sock, exist_ok=True)

    def _run(self, cmd: str) -> None:
        full = f"{self.bin}/{cmd}"
        if self._as_postgres:
            subprocess.run(["su", "postgres", "-c", full], check=True)
        else:
            subprocess.run(full, shell=True, check=True)

    def start(self) -> str:
        os.makedirs(self.data, exist_ok=True)
        if self._as_postgres:
            subprocess.run(["chown", "-R", "postgres:postgres", self.tmp], check=True)
            os.chmod(self.tmp, 0o777)

        self._run(f"initdb -D {shlex.quote(self.data)} -U postgres --auth=trust -E UTF8 >/dev/null")
        opts = f"-p {self.port} -c listen_addresses=127.0.0.1 -k {shlex.quote(self.sock)}"
        log = os.path.join(self.tmp, "pg.log")
        self._run(
            f"pg_ctl -D {shlex.quote(self.data)} -o {shlex.quote(opts)} "
            f"-l {shlex.quote(log)} -w start"
        )
        self._run(f"createdb -h 127.0.0.1 -p {self.port} -U postgres {self.dbname}")
        return f"postgresql+asyncpg://postgres@127.0.0.1:{self.port}/{self.dbname}"

    def stop(self) -> None:
        try:
            self._run(f"pg_ctl -D {shlex.quote(self.data)} -m immediate -w stop")
        except subprocess.CalledProcessError:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)
