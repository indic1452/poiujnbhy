"""Запуск веб-сервера: python -m reportgen.web"""

from __future__ import annotations

from ..config import Settings
from .app import run


def main() -> None:
    run(Settings.load())


if __name__ == "__main__":
    main()
