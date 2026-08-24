# Управление установкой. Все цели рассчитаны на запуск из корня репозитория.
#   make help — список целей.
#
# Переменные можно переопределить в командной строке:
#   make install WHEELS=/mnt/usb/wheels     # офлайн-установка из колёс
#   make run PORT=8081
#   make index FORCE=1

VENV    ?= .venv
BIN     := $(VENV)/bin
# После `make install` все цели работают питоном из venv (там зависимости);
# до установки — системным. Задать явно: make test PY=/usr/bin/python3.11
PY      ?= $(shell test -x $(BIN)/python && echo $(BIN)/python || echo python3)
RUFF    ?= $(shell test -x $(BIN)/ruff && echo $(BIN)/ruff || echo ruff)
WHEELS  ?=                       # каталог с колёсами; задан — pip не ходит в сеть
PIPARGS := $(if $(WHEELS),--no-index --find-links=$(WHEELS),)
HOST    ?= 127.0.0.1
PORT    ?= 8080
LOGIN   ?= admin
NAME    ?= Администратор
TESTS   ?= discover -s tests
# Цели, которые лезут в базу (run, index, admin), читают те же настройки, что и
# сервис. На машине разработчика файла нет — тогда работают значения по умолчанию.
# Тестов это НЕ касается: они обязаны быть независимы от установки.
ENV_FILE ?= /etc/reportgen/reportgen.env
LOADENV  := set -a; [ -r "$(ENV_FILE)" ] && . "$(ENV_FILE)"; set +a;
RUN      := PYTHONPATH=src $(PY) -m reportgen

.DEFAULT_GOAL := help
.PHONY: help install test lint run index admin backup wheels clean

help:  ## список целей
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## venv + зависимости + пакет reportgen (WHEELS=каталог — без интернета)
	$(PY) -m venv $(VENV)
	$(BIN)/pip install $(PIPARGS) --upgrade pip setuptools wheel
	$(BIN)/pip install $(PIPARGS) -r requirements.txt
	$(BIN)/pip install $(PIPARGS) --no-build-isolation -e .
	@echo "готово: $(BIN)/reportgen --help"

test:  ## прогон тестов (стандартный unittest, без сети)
	PYTHONPATH=src:tests $(PY) -m unittest $(TESTS) -v

lint:  ## ruff по исходникам и тестам + проверка синтаксиса деплой-скриптов
	$(RUFF) check src tests
	$(PY) -m compileall -q src
	bash -n deploy/backup.sh

run:  ## веб-сервер на HOST:PORT (для отладки; в продуктиве — systemd)
	$(LOADENV) $(RUN) serve --host $(HOST) --port $(PORT)

index:  ## переиндексация библиотеки в базу (инкрементально; FORCE=1 — целиком)
	$(LOADENV) $(RUN) ingest $(if $(FORCE),--force,)
	@echo "если включён REPORTGEN_EMBED_ENABLED, постройте векторы: reportgen-cli embed"

admin:  ## создать администратора (пароль спросит интерактивно)
	$(LOADENV) $(RUN) useradd --login $(LOGIN) --name "$(NAME)" --role admin

backup:  ## резервная копия базы, библиотеки и экспортов с проверкой восстановления
	deploy/backup.sh

wheels:  ## скачать колёса для переноса в изолированный контур (нужен интернет)
	$(PY) -m pip download -r requirements.txt -d wheels
	$(PY) -m pip download pip setuptools wheel -d wheels
	@echo "перенесите каталог wheels/ на сервер и там: make install WHEELS=<путь>"

clean:  ## удалить кеши и артефакты сборки (данные в var/ не трогаются)
	rm -rf build dist src/*.egg-info *.egg-info .pytest_cache .ruff_cache
	find src tests -name __pycache__ -type d -prune -exec rm -rf {} +
	find src tests -name '*.pyc' -delete
