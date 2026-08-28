# Образ приложения (веб-интерфейс + CLI). Инференс сюда НЕ входит: llama-server
# живёт отдельным сервисом (см. docker-compose.yml) или systemd-юнитом на хосте.
#
# Сборка выполняется из КОРНЯ репозитория:
#   docker build -f deploy/Dockerfile -t reportgen:0.1.0 .
# либо `docker compose build` из каталога deploy (контекст задан в compose-файле).

FROM python:3.11-slim AS base

# PYTHONDONTWRITEBYTECODE — не мусорить .pyc в томах,
# PYTHONUNBUFFERED — чтобы логи uvicorn сразу попадали в journald/docker logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- зависимости отдельным слоем: правки кода не приводят к переустановке ----
COPY requirements.txt ./

# Офлайн-сборка в изолированном контуре: сначала на машине с интернетом
#   make wheels
# затем перенесите каталог wheels/ в корень репозитория и раскомментируйте
# две строки ниже (COPY + аргумент pip), сборка перестанет ходить в сеть.
# COPY wheels /wheels
# ARG PIP_ARGS="--no-index --find-links=/wheels"
ARG PIP_ARGS=""
RUN pip install $PIP_ARGS -r requirements.txt

# --- сам пакет --------------------------------------------------------------
COPY pyproject.toml README.md ./
COPY src ./src
# --no-deps: зависимости уже установлены слоем выше и зафиксированы requirements.txt
RUN pip install $PIP_ARGS --no-deps .

# Шаблоны-планы и глоссарий кладём в образ как разумное начальное состояние.
# В эксплуатации их правит инженер, поэтому каталог принято монтировать томом:
#   -v /opt/reportgen/templates:/app/templates:ro
COPY templates ./templates

# --- пользователь и данные --------------------------------------------------
# UID фиксирован: он должен совпадать с владельцем каталога данных на хосте,
# иначе при монтировании bind-тома приложение не сможет писать в базу.
RUN useradd --system --uid 10001 --create-home --home-dir /var/lib/reportgen \
        --shell /usr/sbin/nologin reportgen \
    && mkdir -p /var/lib/reportgen/library /var/lib/reportgen/uploads /var/lib/reportgen/exports \
    && chown -R reportgen:reportgen /var/lib/reportgen

ENV REPORTGEN_DATA_DIR=/var/lib/reportgen \
    REPORTGEN_TEMPLATES_DIR=/app/templates \
    REPORTGEN_GLOSSARY_PATH=/app/templates/glossary.json \
    REPORTGEN_HOST=0.0.0.0 \
    REPORTGEN_PORT=8080

VOLUME ["/var/lib/reportgen"]
EXPOSE 8080
USER reportgen

# Проверка живости без curl (его в slim-образе нет): 200 от /api/health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"]

# reportgen-web поднимает uvicorn с настройками из REPORTGEN_*.
# Разовые команды запускаются тем же образом:
#   docker compose run --rm reportgen reportgen useradd --login admin --role owner
CMD ["reportgen-web"]
