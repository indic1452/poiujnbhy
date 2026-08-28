# 10. Установка на сервер

Документ описывает установку с нуля на сервер заказчика: **RTX 4080 16 ГБ,
Xeon E5-2680 (14 ядер, Broadwell), 64 ГБ DDR4-2400, Linux, изолированный
контур**. Выбор модели и флаги инференса не обсуждаются заново — они взяты из
[docs/02, раздел 2.2](02-hardware-and-models.md), здесь только развёрнуты в
команды.

Все файлы, на которые ссылается инструкция, лежат в [`deploy/`](../deploy/).

> **Про изолированный контур.** Считаем, что интернета на сервере нет. Всё, что
> нужно скачать (пакеты ОС, CUDA, исходники llama.cpp, GGUF-модель, колёса
> Python), скачивается на машине с интернетом, проверяется по SHA-256 и
> переносится носителем. В каждом разделе для этого есть отдельный абзац
> «офлайн».

## 10.0. Карта установки

```
/opt/reportgen/            код приложения (этот репозиторий) + .venv
/opt/llama.cpp/            исходники и сборка сервера инференса
/opt/models/               файлы моделей .gguf                    (только чтение)
/etc/reportgen/
    reportgen.env          настройки приложения (REPORTGEN_*), права 0640
    llama.env              параметры запуска llama-server
/var/lib/reportgen/        ДАННЫЕ: reportgen.db, library/, uploads/, exports/
/var/backups/reportgen/    резервные копии
/var/log/reportgen/        журнал бэкапов (остальное — в journald)
```

| Сервис | Пользователь | Порт | Кто ходит |
|---|---|---|---|
| `llama-server` | `llama` | 127.0.0.1:8000 | только приложение |
| `reportgen` | `reportgen` | 127.0.0.1:8080 | только nginx |
| `nginx` | `www-data` | 443 (и 80 → редирект) | инженеры из офисной сети/VPN |

Наружу открыт только 443. Порты 8000 и 8080 слушают петлевой интерфейс —
это не «на всякий случай», а требование [docs/07 р. 7.1](07-security-and-ops.md).

## 10.1. Подготовка ОС

Проверено на Ubuntu Server 22.04/24.04 LTS; для RHEL-семейства отличаются
только команды пакетного менеджера.

```bash
sudo apt update && sudo apt install -y \
    build-essential cmake git pkg-config \
    python3 python3-venv python3-dev \
    nginx sqlite3 numactl pciutils tar gzip openssl
```

Python должен быть 3.11 или новее (`python3 -V`). Если в дистрибутиве старее —
ставьте 3.11 из бэкпортов, версия для приложения обязательна: код использует
синтаксис `X | Y` в аннотациях времени выполнения.

Пользователи и каталоги:

```bash
sudo useradd --system --home-dir /var/lib/reportgen --shell /usr/sbin/nologin reportgen
sudo useradd --system --home-dir /opt/llama.cpp   --shell /usr/sbin/nologin llama
sudo mkdir -p /opt/reportgen /opt/models /etc/reportgen \
              /var/lib/reportgen /var/backups/reportgen \
              /var/log/reportgen /var/cache/llama/cuda
sudo chown -R reportgen:reportgen /var/lib/reportgen /var/log/reportgen
sudo chown -R llama:llama /var/cache/llama
sudo chmod 750 /var/lib/reportgen /etc/reportgen
```

### Драйвер NVIDIA и CUDA

```bash
lspci | grep -i nvidia                  # карта видна?
sudo apt install -y nvidia-driver-550-server   # или новее; серверный вариант — без X
sudo reboot
nvidia-smi                              # обязан показать RTX 4080 и 16376MiB
sudo nvidia-smi -pm 1                   # persistence mode: карта не «засыпает» между запросами
```

Для сборки llama.cpp нужен ещё CUDA Toolkit (компилятор `nvcc`) версии 12.x:

```bash
sudo apt install -y nvidia-cuda-toolkit   # либо пакет с сайта NVIDIA — он свежее
nvcc --version
```

Проверьте, что версия драйвера не старше требуемой тулкитом: драйвер 550+
работает с CUDA 12.4. Если `nvidia-smi` работает, а `nvcc` нет — соберётся
CPU-версия, и вы это заметите только по скорости (см. 10.12, «нет CUDA»).

**Офлайн:** пакеты драйвера и тулкита скачиваются как `.deb` (`apt-get download`
или локальное зеркало) и ставятся `sudo dpkg -i` в порядке зависимостей.
Драйвер можно поставить и из `.run`-инсталлятора NVIDIA — он самодостаточен.

### Диск и память

```bash
lsblk -d -o NAME,ROTA,SIZE          # ROTA=0 — SSD/NVMe; модели должны лежать на нём
sudo dmidecode -t memory | grep -c "Configured Memory Speed: 2400"   # число занятых слотов
lscpu | grep -E "NUMA node\(s\)|Core\(s\)|Model name"
```

Два наблюдения из [docs/02 р. 2.2](02-hardware-and-models.md), которые проще
проверить сейчас, чем удивляться потом: **все четыре канала памяти должны быть
заняты** (это ×2 к скорости MoE-выгрузки) и **если NUMA-узлов два**, процесс
инференса нужно прибивать к одному сокету (`numactl`, см. комментарий в юните).

## 10.2. Сборка llama.cpp с CUDA

```bash
sudo -u llama git clone https://github.com/ggml-org/llama.cpp /opt/llama.cpp
cd /opt/llama.cpp
sudo -u llama cmake -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES=89 \
    -DLLAMA_CURL=OFF
sudo -u llama cmake --build build --config Release -j 14
```

- `-DCMAKE_CUDA_ARCHITECTURES=89` — Ada Lovelace, это и есть RTX 4080. Указание
  одной архитектуры экономит минут двадцать сборки против сборки «под всё».
- `-DLLAMA_CURL=OFF` — в изолированном контуре качать модели по URL всё равно
  нечем, зато отпадает зависимость от libcurl.
- `-j 14` — по числу ядер. Сборка на Broadwell занимает **15–30 минут**.

Проверка, что бинарник действительно с CUDA:

```bash
/opt/llama.cpp/build/bin/llama-server --version
ldd /opt/llama.cpp/build/bin/llama-server | grep -E "cuda|cublas"   # должны быть строки
```

**Офлайн:** на машине с интернетом `git clone` + `git archive` (или просто
архив каталога) и перенос; сборка идёт локально и сети не требует. Альтернатива
сборке — готовые релизные бинарники llama.cpp под CUDA с GitHub Releases:
распакуйте в `/opt/llama.cpp/build/bin/`, но проверьте, что версия CUDA в
архиве совпадает с драйвером.

## 10.3. Выбор и перенос модели

Полное обоснование — [docs/02 р. 2.2](02-hardware-and-models.md), здесь итог.

| Приоритет | Модель | Квант | Файл | VRAM | Скорость |
|---|---|---|---|---|---|
| №1 | GPT-OSS-20B (MoE, ~3.6B активных, Apache-2.0) | native MXFP4 | `gpt-oss-20b-mxfp4.gguf` | ~12–13 ГБ, целиком в VRAM | 60–100 ток/с |
| №2 | Qwen3-30B-A3B (MoE, ~3B активных) | Q4_K_M | `Qwen3-30B-A3B-Q4_K_M.gguf` | ~18 ГБ: 13–14 в VRAM + эксперты в ОЗУ | 15–30 ток/с |
| запасной | Qwen3-14B / Vikhr-Nemo-12B (dense) | Q5_K_M | `*-Q5_K_M.gguf` | ~9–10 ГБ | 35–55 ток/с |

**Начинайте с №1.** Он целиком помещается в 16 ГБ, даёт интерактивную скорость
и не требует подбора `--n-cpu-moe`. Когда система заработает и появится
эвал-набор ([docs/05](05-evaluation.md)), сравните с №2: он умнее, но втрое
медленнее из-за выгрузки экспертов в DDR4-2400. Выбор делается замером на
своих отчётах, а не по бенчмаркам.

Имена репозиториев и файлов на HuggingFace меняются от релиза к релизу —
проверьте их в момент скачивания; в документации зафиксирован класс модели, а
не конкретная ссылка ([docs/02](02-hardware-and-models.md), преамбула).

Перенос в контур:

```bash
# 1. на машине с интернетом: скачать GGUF-файл (репозиторий уточните на месте)
huggingface-cli download <owner>/<repo>-GGUF --include "*mxfp4*.gguf" --local-dir ./m

# 2. посчитать контрольные суммы РЯДОМ с файлами, без путей
cd ./m && sha256sum *.gguf > model.sha256

# 3. перенести каталог ./m целиком на сервер, затем на сервере:
sudo mkdir -p /opt/models
sudo cp /mnt/usb/m/*.gguf /mnt/usb/m/model.sha256 /opt/models/
cd /opt/models && sha256sum -c model.sha256   # обязательно: 12 ГБ по USB бьются молча

# 4. дать файлу стабильное имя — оно попадёт в /etc/reportgen/llama.env
sudo mv /opt/models/<как-скачалось>.gguf /opt/models/gpt-oss-20b-mxfp4.gguf
sudo chown -R llama:llama /opt/models && sudo chmod 444 /opt/models/*.gguf
```

Переименование в п. 4 — не косметика: при обновлении модели меняется одна
строка в env-файле, а не десяток мест, и откат к прежнему файлу занимает минуту
(см. 10.11).

Модели держим только на чтение: их не должен изменить ни один сервис
([docs/07 р. 7.4](07-security-and-ops.md)).

## 10.4. Запуск llama-server

Сначала руками, чтобы увидеть ошибки:

```bash
sudo -u llama /opt/llama.cpp/build/bin/llama-server \
    -m /opt/models/gpt-oss-20b-mxfp4.gguf \
    --alias gpt-oss-20b-mxfp4 \
    --host 127.0.0.1 --port 8000 \
    -c 32768 -ngl 999 --n-cpu-moe 0 \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    --flash-attn -t 14 --parallel 2
```

Что означает каждый флаг — в комментариях
[`deploy/llama-server.service`](../deploy/llama-server.service) и в
[docs/02 р. 2.2](02-hardware-and-models.md). Если сборка свежая и ругается на
`--flash-attn`, укажите значение: `--flash-attn on`.

Контекст 32768 при `--parallel 2` — это 16384 токена на один разговор, и от
этого числа посчитаны настройки помощника: до 26 000 знаков материала из
библиотеки плюс 4000 токенов на ответ (`REPORTGEN_ASSISTANT_CONTEXT_CHARS`,
`REPORTGEN_ASSISTANT_MAX_TOKENS`; вся арифметика — в `.env.example`).
Посекционной генерации отчёта хватило бы и половины, а вот помощнику — нет:
переполнение llama.cpp обрабатывает молча, выбрасывая начало промпта вместе с
системной инструкцией, после чего модель перестаёт ставить ссылки на
источники. Меньшее окно поставить можно, но вместе с ним снижают и бюджет
материала: 24576 — 18000 знаков, 16384 — 11000 (ориентиры из шапки юнита).
Контекст живёт в KV-кэше на видеокарте, поэтому после его изменения занятую
память смотрят заново и при необходимости добирают `--n-cpu-moe`.

Проверка живости и качества ответа:

```bash
curl -s http://127.0.0.1:8000/health                       # {"status":"ok"}

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-oss-20b-mxfp4",
       "messages":[{"role":"user","content":"Ответь одним предложением по-русски: что такое занимаемая полоса частот?"}],
       "temperature":0.2,"max_tokens":128}' | python3 -m json.tool
```

Ответ должен прийти за секунды и быть по-русски. Заодно посмотрите занятую
память — по ней подбирается `--n-cpu-moe`:

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

Цель — **около 14.5 из 16 ГБ**. Процедура подбора расписана в конце
[`deploy/llama-server.service`](../deploy/llama-server.service).

Теперь то же самое юнитом:

```bash
sudo install -m 0640 -o root -g reportgen /dev/null /etc/reportgen/llama.env
sudoedit /etc/reportgen/llama.env        # содержимое — в шапке юнита
sudo cp deploy/llama-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llama-server
journalctl -u llama-server -f            # ждём «server is listening»
```

Юнит берёт значения из `llama.env`, поэтому контекст задаётся там —
`LLAMA_CTX=32768`; готовый образец файла целиком приведён в шапке юнита.

## 10.5. Установка приложения

```bash
sudo git clone <ваш-репозиторий> /opt/reportgen     # или распакуйте архив
cd /opt/reportgen
sudo make install                                    # venv + зависимости + пакет
sudo chown -R root:reportgen /opt/reportgen
```

После установки доступны две команды:

```bash
/opt/reportgen/.venv/bin/reportgen --help        # CLI: ingest, generate, verify, useradd…
/opt/reportgen/.venv/bin/reportgen-web           # веб-сервер (его запускает systemd)
```

### Офлайн-установка из колёс

На машине с интернетом (той же архитектуры и с тем же Python 3.11):

```bash
make wheels          # положит колёса в ./wheels
tar -czf reportgen-offline.tar.gz --exclude=.git --exclude=var .
```

На сервере:

```bash
cd /opt/reportgen && sudo make install WHEELS=/opt/reportgen/wheels
```

`make install` с `WHEELS=` добавляет pip флаги `--no-index --find-links`, то
есть сеть не потребуется вовсе. Если архитектуры машин различаются, скачивайте
колёса с явной платформой:

```bash
pip download -r requirements.txt -d wheels \
    --only-binary=:all: --python-version 3.11 --platform manylinux2014_x86_64
```

Проверить, что окружение собралось без сети:

```bash
/opt/reportgen/.venv/bin/python -c "import fastapi, uvicorn, docx, pymupdf, numpy; print('зависимости на месте')"
```

## 10.6. Первичная настройка

### Настройки

```bash
sudo cp /opt/reportgen/.env.example /etc/reportgen/reportgen.env
sudo chown root:reportgen /etc/reportgen/reportgen.env
sudo chmod 640 /etc/reportgen/reportgen.env
sudoedit /etc/reportgen/reportgen.env
```

Обязательно поменять:

| Переменная | Значение |
|---|---|
| `REPORTGEN_SECRET_KEY` | `openssl rand -hex 32` — без него cookie-сессии не подписаны |
| `REPORTGEN_LLM_MODEL` | ровно то, что в `--alias` у llama-server: имя попадает в служебный блок отчёта (инвариант воспроизводимости, [docs/01 р. 1.4](01-architecture.md)) |
| `REPORTGEN_BRAND_NAME`, `REPORTGEN_REPORT_FOOTER` | название компании и реквизиты в колонтитуле |

Остальные значения в образце уже подобраны под это железо: таймаут 600 с, две
параллельные секции под `--parallel 2`, лимит загрузки 200 МБ и бюджет
материала для помощника (26 000 знаков) — он посчитан от окна 32768 на два
слота, см. 10.4.

### Обёртка для CLI (сделайте это до всего остального)

Команды CLI, работающие с базой, должны видеть **те же** настройки, что и
сервис. `sudo -u reportgen reportgen …` их не видит: `EnvironmentFile` читает
systemd, а не sudo, и команда молча создаст вторую базу в текущем каталоге.
Один раз заведите обёртку:

```bash
sudo tee /usr/local/bin/reportgen-cli >/dev/null <<'SH'
#!/bin/sh
# CLI с настройками сервиса, от имени пользователя reportgen.
set -a
. /etc/reportgen/reportgen.env
set +a
exec runuser -u reportgen -- /opt/reportgen/.venv/bin/reportgen "$@"
SH
sudo chmod 755 /usr/local/bin/reportgen-cli
sudo reportgen-cli --help
```

Дальше в документе используется именно `reportgen-cli`. Из каталога
`/opt/reportgen` то же самое делают цели `make admin`, `make index`, `make run`:
они читают `/etc/reportgen/reportgen.env`, если он есть.

### Администратор

```bash
sudo reportgen-cli useradd --login admin --role owner \
    --name "Иванов И. И." --department "Отдел радиоконтроля"
# пароль спросит интерактивно; короче 8 символов не примет
sudo reportgen-cli users
```

Первая запись — создатель системы (`owner`): её нельзя ни отключить, ни
разжаловать, и через интерфейс такую не завести. Остальных заводят в разделе
«Сотрудники» или той же командой; должности: `head` — начальник отдела,
`deputy` — заместитель, `lead` — начальник группы (эти три с правами
администратора), `senior` — старший инженер, `engineer` — инженер. Письма и
отчёты ведут все должности, роли «только на чтение» больше нет. Права и
перенос прежних ролей `viewer`/`admin` при обновлении базы —
[docs/09 р. 9.3](09-web-app.md).

Отдел и группа (`--department`, `--team`) заполняются не для порядка: по ним
дашборд собирает списочный состав и нагрузку. Без них человек в сводке будет
без подразделения.

> Команда должна выполняться **от пользователя `reportgen`** (обёртка это
> обеспечивает): иначе база и каталоги окажутся созданы от root, и сервис потом
> не сможет в них писать. Это самая частая ошибка первой установки.

### Библиотека и индексация

Разложите документы по типам — каталог определяет `doc_type`, а он участвует
в поиске и в оформлении ссылок:

```
/var/lib/reportgen/library/
    literature/     учебники, статьи, методики
    standards/      ГОСТ, рекомендации ITU/ETSI
    datasheets/     даташиты на оборудование
    reports/        прошлые отчёты (индекс B)
    regulations/    внутренние регламенты и правила оформления
```

```bash
sudo -u reportgen cp -r /mnt/usb/library/* /var/lib/reportgen/library/
sudo reportgen-cli ingest       # или из /opt/reportgen: make index
sudo reportgen-cli library      # что попало в базу
```

Индексация инкрементальная: файл с неизменившимся SHA-256 пропускается, так
что команду можно гонять хоть по расписанию. Полная переиндексация —
`make index FORCE=1`.

Поиск по этой библиотеке живёт в приложении (раздел «Библиотека» в боковом
меню и `GET /api/search`). Команда `reportgen search` — про другое: она
работает с файловым индексом, который строит
`reportgen index --corpus … --out …`, и нужна для отладки конвейера без базы.

Плотный поиск (эмбеддинги) включается **позже**, когда лексического станет мало:
поднимите рядом сервер `bge-m3` на порту 8001, поставьте
`REPORTGEN_EMBED_ENABLED=true` и постройте векторы командой `reportgen-cli embed`.
Держать эмбеддер и LLM на одной карте одновременно в 16 ГБ тесно — схема
описана в [docs/02 р. 2.5](02-hardware-and-models.md): либо эмбеддер на CPU,
либо разовая индексация на GPU при выгруженной LLM.

## 10.7. Запуск приложения через systemd

```bash
sudo cp /opt/reportgen/deploy/reportgen.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now reportgen
systemctl status reportgen
curl -s http://127.0.0.1:8080/api/health | python3 -m json.tool
```

Здоровый ответ выглядит так:

```json
{"status": "ok", "database": "ok",
 "counts": {"users": 1, "documents": 128, "chunks": 4310, "cases": 0, "reports": 0,
            "edit_pairs": 0, "chats": 0, "chat_messages": 0, "chat_attachments": 0,
            "absences": 0, "audit": 3},
 "llm": {"kind": "openai", "model": "gpt-oss-20b-mxfp4"}, "auth_enabled": true}
```

Логи — `journalctl -u reportgen -f`. Юнит запрещает процессу писать куда-либо,
кроме `/var/lib/reportgen`; если данные лежат в другом месте, поправьте
`ReadWritePaths=`, иначе получите «Permission denied» на пустом месте.

## 10.8. nginx и TLS

Сертификат внутреннего УЦ — предпочтительный вариант: браузеры инженеров ему
уже доверяют. Самоподписанный годится для пилота:

```bash
sudo mkdir -p /etc/nginx/ssl
sudo openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/reportgen.key \
    -out    /etc/nginx/ssl/reportgen.crt \
    -subj "/C=RU/O=Ваша компания/CN=reportgen.example.local" \
    -addext "subjectAltName=DNS:reportgen.example.local,IP:10.0.0.10"
sudo chmod 600 /etc/nginx/ssl/reportgen.key
```

```bash
sudo cp /opt/reportgen/deploy/nginx.conf /etc/nginx/sites-available/reportgen
sudoedit /etc/nginx/sites-available/reportgen        # заменить server_name и пути к сертификату
sudo ln -sf /etc/nginx/sites-available/reportgen /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Файрвол:

```bash
sudo ufw allow 443/tcp && sudo ufw allow 80/tcp && sudo ufw enable
ss -tlnp | grep -E ':(80|443|8000|8080)'   # 8000 и 8080 обязаны быть на 127.0.0.1
```

Два числа в конфиге связаны с настройками приложения, и разводить их нельзя:
`client_max_body_size 200m` ↔ `REPORTGEN_MAX_UPLOAD_MB=200`,
`proxy_read_timeout 600s` ↔ `REPORTGEN_LLM_TIMEOUT=600`.

## 10.9. Развёртывание в Docker (альтернатива)

Если в контуре принято всё держать в контейнерах, есть
[`deploy/docker-compose.yml`](../deploy/docker-compose.yml):

```bash
cp .env.example .env && $EDITOR .env
cd deploy && docker compose up -d && docker compose ps
```

Для GPU-сервиса нужен `nvidia-container-toolkit`; проверка:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Флаги инференса записаны в compose-файле прямо в `command`, а модель, каталог
моделей, выгрузка экспертов и контекст читаются из `.env` рядом с ним:
`LLM_GGUF`, `LLM_ALIAS`, `MODELS_DIR`, `N_CPU_MOE`, `LLAMA_CTX` (по умолчанию
32768 — то же окно, что и у юнита, 10.4). В `.env.example` этих пяти строк
нет — он про настройки приложения, — так что дописывайте их руками.

Если этой возможности нет — **держите инференс на хосте юнитом**, а в
контейнере оставьте только приложение (в compose-файле это расписано). Такой
смешанный режим обычно и удобнее: подбор `--n-cpu-moe` и смена модели не
требуют пересборки образов.

## 10.10. Бэкапы

Что копируется и почему — в шапке [`deploy/backup.sh`](../deploy/backup.sh).
Коротко: база и библиотека невосстановимы, индекс и векторы пересобираются.

```bash
sudo /opt/reportgen/deploy/backup.sh          # первый прогон — руками, смотрим вывод
sudo ls -lh /var/backups/reportgen/
```

Скрипт делает онлайн-копию SQLite через `.backup` (останавливать сервис не
нужно), архивирует библиотеку, экспорты и шаблоны, считает SHA-256, **сам
проверяет, что копия восстановима**, и удаляет лишние по `BACKUP_KEEP`.
Каталог `uploads/` не копируется намеренно: файл, приложенный к вопросу
помощнику, после разбора с диска удаляется, а его текст остаётся в базе.

Расписание — ежедневно ночью:

```bash
echo '15 3 * * * root /opt/reportgen/deploy/backup.sh >> /var/log/reportgen/backup.log 2>&1' \
  | sudo tee /etc/cron.d/reportgen-backup
```

Проверка ранее сделанной копии (регламент [docs/07 р. 7.4](07-security-and-ops.md) —
раз в квартал; поскольку проверка идёт после каждого бэкапа, квартальная
процедура сводится к чтению журнала и одному ручному восстановлению):

```bash
sudo /opt/reportgen/deploy/backup.sh --check /var/backups/reportgen/20250312-031500
```

Восстановление (инструкция дублируется в `MANIFEST.txt` внутри каждой копии):

```bash
sudo systemctl stop reportgen
sudo -u reportgen cp /var/backups/reportgen/<копия>/reportgen.db /var/lib/reportgen/
sudo rm -f /var/lib/reportgen/reportgen.db-wal /var/lib/reportgen/reportgen.db-shm
sudo -u reportgen tar -xzf /var/backups/reportgen/<копия>/library.tar.gz -C /var/lib/reportgen
sudo systemctl start reportgen
sudo reportgen-cli ingest                                       # пересобрать индекс
```

Копии складывайте не только на этот же диск: том с бэкапами должен переживать
смерть сервера. Если библиотека большая, `library.tar.gz` можно делать реже
(она меняется редко), а базу — ежедневно.

## 10.11. Обновления

### Модель

Смена модели — это релиз, а не «подтянется само»
([docs/07 р. 7.4](07-security-and-ops.md)):

1. Положить новый GGUF рядом со старым, **старый не удалять**.
2. Прогнать эвал-набор ([docs/05](05-evaluation.md)) на новой модели:
   `sudo reportgen-cli eval --golden … --llm openai --base-url http://127.0.0.1:8000/v1 --model <новая>`.
3. Сравнить с предыдущим прогоном. Стало не лучше — не выкатывать.
4. Поменять `LLAMA_MODEL` и `LLAMA_ALIAS` в `/etc/reportgen/llama.env`,
   `REPORTGEN_LLM_MODEL` в `/etc/reportgen/reportgen.env`.
5. `sudo systemctl restart llama-server reportgen`, подобрать заново
   `--n-cpu-moe` (у другой модели другой размер), проверить скорость.
6. Откат — вернуть две строки в env-файлах и перезапустить: минута.

Окно контекста — тоже часть релиза: если у новой модели оно меньше 32768,
снижайте `LLAMA_CTX` и синхронно `REPORTGEN_ASSISTANT_CONTEXT_CHARS`, иначе
помощник будет переполнять окно молча (10.4).

Имя модели пишется в служебный блок каждого отчёта, поэтому по архиву всегда
видно, чем сгенерирован конкретный документ.

### Приложение

```bash
sudo /opt/reportgen/deploy/backup.sh                  # 1. копия ДО обновления
cd /opt/reportgen && sudo git fetch && sudo git checkout <тег>
sudo make install                                     # 2. обновить зависимости и пакет
sudo systemctl restart reportgen
curl -s http://127.0.0.1:8080/api/health | python3 -m json.tool
```

Схема базы применяется при старте автоматически и идемпотентно
(`store/db.py` → `migrate()`), отдельной команды миграции нет. Откат — вернуть
предыдущий тег и, если схема успела измениться, восстановить базу из копии,
сделанной шагом 1.

Шаблоны-планы (`templates/outline_*.json`) обновляются просто заменой файла —
перезапуск не нужен, каталог перечитывается ([docs/09 р. 9.7](09-web-app.md)).

## 10.12. Ожидаемые тайминги на этом железе

Числа — ориентиры для приёмки: если получилось в разы хуже, что-то настроено
не так, и в 10.14 скорее всего описано, что именно.

| Операция | Ожидание | Замечание |
|---|---|---|
| Сборка llama.cpp с CUDA | 15–30 мин | `-j 14`, Broadwell |
| Старт llama-server, загрузка 12–13 ГБ | 15–45 с | с NVMe и холодным кешем; повторный — 5–15 с |
| Генерация, MoE 20B целиком в VRAM | 60–100 ток/с | секция 400–800 слов ≈ **10–20 с** |
| Генерация, MoE 30B с выгрузкой экспертов | 15–30 ток/с | та же секция ≈ **40–80 с** |
| Обработка промпта секции (5–7 тыс. токенов) | 3–8 с | со второй секции меньше: работает кеш общего префикса |
| **Полный отчёт на 25–30 страниц (10–14 секций)** | **3–6 мин** на модели №1, **12–25 мин** на №2 | генерация идёт в HTTP-запросе ([docs/09 р. 9.9](09-web-app.md)) |
| Верификация готового отчёта | < 1 с | чистый Python, без модели |
| Экспорт в DOCX | 1–3 с | |
| Приём текстового PDF (200 стр.) | 5–20 с | PyMuPDF, одно ядро |
| Первичная индексация 500 документов (~50 тыс. страниц) | 30–90 мин | однократно; повторные прогоны инкрементальны |
| Эмбеддинги 100 тыс. чанков на GPU (LLM выгружена) | десятки минут | по [docs/02 р. 2.5](02-hardware-and-models.md) |
| Эмбеддинги 100 тыс. чанков на CPU | 3–9 часов | ночной прогон, 14 ядер без AVX-512 |
| Бэкап базы (100–500 МБ) | секунды | `.backup`, без остановки сервиса |
| Бэкап библиотеки 20 ГБ | 10–25 мин | упирается в gzip на одном ядре |

Два инженера, работающие одновременно, — это ровно те `--parallel 2` слота.
Третий встанет в очередь; на 16 ГБ увеличивать число слотов не стоит, лучше
подождать: контекст на слот важнее.

## 10.13. Чек-лист после установки

Проверяйте по порядку, каждая строка — одна команда и один ожидаемый результат.

| # | Проверка | Команда | Ожидание |
|---|---|---|---|
| 1 | GPU видна | `nvidia-smi` | RTX 4080, 16376 MiB |
| 2 | Инференс работает | `curl -s 127.0.0.1:8000/health` | `{"status":"ok"}` |
| 3 | Модель в VRAM, а не в ОЗУ | `nvidia-smi --query-gpu=memory.used --format=csv` | 12–15 ГБ занято |
| 4 | Скорость приемлемая | запрос из 10.4 с `max_tokens=256` | ответ за 3–10 с |
| 5 | Приложение живо | `curl -s 127.0.0.1:8080/api/health` | `"status": "ok"` |
| 6 | Есть создатель системы | `sudo reportgen-cli users` | строка «Создатель системы», состояние «работает» |
| 7 | Библиотека проиндексирована | `sudo reportgen-cli library` | документы и чанки, не «библиотека пуста» |
| 8 | Поиск находит | раздел «Библиотека», запрос «занимаемая полоса» (или `GET /api/search?q=…` с сессионной cookie) | осмысленные фрагменты со ссылками на документы |
| 9 | Вход через браузер | `https://<сервер>/` | форма входа, сертификат принят, после входа открывается «Дашборд» |
| 10 | Сквозной прогон | зарегистрировать письмо с факт-пакетом из `examples/cases/case-2024-118.json`, сгенерировать, утвердить, выгрузить DOCX | отчёт без ошибок верификатора |
| 11 | Верификатор действительно блокирует | вписать в секцию выдуманное число и сохранить | ошибка `error`, кнопка утверждения заблокирована |
| 12 | Загрузка большого файла | загрузить PDF на 150 МБ | загрузился, не 413 |
| 13 | Бэкап и восстановление | `deploy/backup.sh` | «проверка пройдена» в конце вывода |
| 14 | Автозапуск | `sudo reboot`, после загрузки — пункты 2 и 5 | оба сервиса поднялись сами |
| 15 | Журнал действий пишется | раздел «Метрики» под администратором, карточка внизу страницы | видны входы и генерации |
| 16 | Помощник отвечает по библиотеке | вопрос в разделе «Помощник» | ответ со ссылками вида `[S1]` и списком источников в правой панели |

Пункт 11 — не формальность. Проверка «числа только из факт-пакета» —
центральный инвариант системы ([docs/01 р. 1.4](01-architecture.md)); если он
не сработал, всё остальное не имеет значения.

## 10.14. Типовые проблемы

### Не используется CUDA: генерация 3–6 ток/с вместо 60–100

Признаки: в журнале `llama-server` нет строк про `CUDA0` и выгруженные слои,
`nvidia-smi` показывает 0 МБ занятой памяти, при этом все 14 ядер CPU в полке.

Что проверить по порядку:

```bash
nvidia-smi                                                  # драйвер вообще работает?
ldd /opt/llama.cpp/build/bin/llama-server | grep -c cuda    # бинарник собран с CUDA?
journalctl -u llama-server | grep -iE "cuda|offload|device" # что сервер думает про GPU
```

Причины, по частоте: собрано без `-DGGML_CUDA=ON` (пересоберите); в юните
случайно включили `PrivateDevices=yes` — она скрывает `/dev/nvidia*`, и сервер
молча уезжает на CPU (в поставляемом юните её нет и быть не должно);
пользователь `llama` не имеет доступа к устройствам (проверьте
`ls -l /dev/nvidia*`, обычно там `crw-rw-rw-`); драйвер обновился, а модули не
перезагружены (`sudo reboot`).

### CUDA out of memory при старте

Считайте по формулам [docs/02 р. 2.1](02-hardware-and-models.md), затем:

1. увеличьте `LLAMA_N_CPU_MOE` на 4 и перезапустите — это главный регулятор;
2. уменьшите контекст: `LLAMA_CTX=24576`, при нехватке — `16384`, и вместе с
   ним `REPORTGEN_ASSISTANT_CONTEXT_CHARS` (18000, затем 11000). Порознь эти
   два числа менять нельзя: на разговор приходится `LLAMA_CTX / LLAMA_PARALLEL`
   токенов, и материал, который в них не поместился, llama.cpp отрежет молча
   вместе с системной инструкцией;
3. уменьшите `LLAMA_PARALLEL` до 1 и синхронно
   `REPORTGEN_LLM_PARALLEL_SECTIONS=1`;
4. убедитесь, что KV-кэш квантованный (`--cache-type-k/v q8_0` — в юните есть);
5. проверьте, что карту не занял кто-то ещё: `nvidia-smi` покажет процессы —
   графическая сессия, старый экземпляр сервера, эмбеддер.

### Генерация «работает, но медленно»

| Симптом | Причина | Что делать |
|---|---|---|
| 5–15 ток/с на модели №1 | часть слоёв ушла в ОЗУ | уменьшить `--n-cpu-moe`, проверить занятую VRAM |
| Скорость упала вдвое после апгрейда памяти | заняты не все каналы DDR4 | `dmidecode -t memory`, переставить планки на 4 или 8 слотов |
| Машина двухсокетная, скорость плавает | NUMA | `numactl --cpunodebind=0 --membind=0` (закомментированный вариант в юните) |
| Первый запрос долгий, остальные быстрые | прогрев и кеш префикса | это норма |
| Тормозит вся машина, диск в полке | своп: модель + индексация не поместились в 64 ГБ | не запускать индексацию одновременно с генерацией |
| Долгая обработка промпта | слишком большой `REPORTGEN_RETRIEVAL_TOP_K` | 6–8 фрагментов достаточно, больше — вредит и скорости, и качеству |

### Поиск не находит очевидные документы

```bash
sudo reportgen-cli library                       # документ вообще в базе?
sudo reportgen-cli library --doc-type standards  # и в правильном ли типе?
```

Частые причины: библиотека не проиндексирована после копирования файлов
(`reportgen-cli ingest`); документ — скан, и приём пометил его `needs_ocr`
(текста в нём нет, распознавание — отдельная задача,
[docs/02 р. 2.6](02-hardware-and-models.md)); файл лежит не в том подкаталоге,
и фильтр по типу его отсекает; запрос слишком общий — увеличьте
`REPORTGEN_RETRIEVAL_CANDIDATES`; лексического поиска не хватает по существу —
включайте эмбеддинги и реранкер (`REPORTGEN_EMBED_ENABLED`,
`REPORTGEN_RERANK_ENABLED`), это самый выгодный по качеству шаг
([docs/02 р. 2.5](02-hardware-and-models.md)).

### Прочее

| Симптом | Причина | Решение |
|---|---|---|
| `413 Request Entity Too Large` | `client_max_body_size` меньше `REPORTGEN_MAX_UPLOAD_MB` | привести оба к 200 и перезагрузить nginx |
| `504 Gateway Time-out` на генерации | `proxy_read_timeout` меньше времени генерации | 600 с в `nginx.conf`, столько же в `REPORTGEN_LLM_TIMEOUT` |
| `database is locked` | параллельная запись или копирование базы через `cp` | не запускать несколько воркеров uvicorn, для копий — только `deploy/backup.sh` |
| `Permission denied` в `/var/lib/reportgen` | каталог создан от root или путь вне `ReadWritePaths=` | `chown -R reportgen:reportgen`, поправить юнит |
| «Интерфейс не установлен» на главной | нет файлов в `src/reportgen/web/static` | API при этом работает; собрать интерфейс или обновить установку |
| Не пускает после смены `REPORTGEN_SECRET_KEY` | все сессии инвалидированы | это ожидаемо, войти заново |
| CLI показывает пустую базу, а интерфейс — полную | команда запущена без настроек сервиса и создала свою базу в текущем каталоге | всегда через `reportgen-cli`; лишнюю базу `./var/reportgen.db` удалить |
| В отчёте нет ссылок на источники | библиотека пуста или поиск ничего не вернул | см. предыдущий раздел; без источников секция помечается как ненадёжная |
| `reportgen: command not found` | команды живут в venv | пользуйтесь обёрткой `reportgen-cli` (10.6) или полным путём `/opt/reportgen/.venv/bin/reportgen` |

Если проблема не отсюда — начинайте с двух команд, они покрывают почти всё:

```bash
journalctl -u reportgen -n 100 --no-pager
journalctl -u llama-server -n 100 --no-pager
```

> **После обновления кода переиндексируйте библиотеку.** Правила разбиения
> текста на токены иногда меняются (например, номера пунктов «5.3.2» перестали
> распадаться на три числа). Индекс, построенный старыми правилами, ищется
> хуже — но молча. Команда: `reportgen ingest --force`, затем `reportgen embed`.
