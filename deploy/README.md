# deploy/ — файлы развёртывания

Здесь лежит всё, что нужно, чтобы поднять систему на сервере заказчика.
Пошаговая инструкция — в [docs/10-deployment.md](../docs/10-deployment.md);
этот файл только объясняет, что есть в каталоге и в каком порядке брать.

| Файл | Что это | Куда ставится |
|---|---|---|
| `llama-server.service` | systemd-юнит сервера инференса llama.cpp с флагами под RTX 4080 (docs/02 р. 2.2) | `/etc/systemd/system/` |
| `reportgen.service` | systemd-юнит приложения (uvicorn на 127.0.0.1:8080) | `/etc/systemd/system/` |
| `nginx.conf` | обратный прокси: TLS, загрузка до 200 МБ, таймаут 600 с на генерацию | `/etc/nginx/sites-available/reportgen` |
| `backup.sh` | онлайн-бэкап базы, библиотеки, экспортов и шаблонов + проверка восстановления | запускается из `/opt/reportgen/deploy/` по cron или таймеру |
| `Dockerfile` | образ приложения (без инференса) | сборка из корня репозитория |
| `docker-compose.yml` | связка «llama-server + приложение» для варианта в контейнерах | `docker compose up -d` из этого каталога |

## Два способа развернуть

**1. Systemd на хосте — рекомендуемый.** Меньше слоёв между моделью и GPU,
проще подбирать `--n-cpu-moe` и смотреть `nvidia-smi`, ничего не ломается при
обновлении docker или nvidia-container-toolkit:

```bash
sudo cp deploy/llama-server.service deploy/reportgen.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llama-server reportgen
```

**2. Docker compose** — если в контуре так принято. Для GPU-сервиса нужен
`nvidia-container-toolkit`; если его нет, оставьте в compose только приложение,
а llama.cpp поднимите юнитом с хоста (в `docker-compose.yml` это расписано
в комментариях).

Смешивать можно: инференс юнитом на хосте, приложение в контейнере.

## Порядок первого запуска

1. `cp .env.example /etc/reportgen/reportgen.env` и заполнить — прежде всего
   `REPORTGEN_SECRET_KEY` (`openssl rand -hex 32`).
2. Модель GGUF в `/opt/models`, `/etc/reportgen/llama.env` заполнить по образцу
   из шапки `llama-server.service`, поднять `llama-server`, проверить `curl`.
3. `make install` в `/opt/reportgen`, завести обёртку `reportgen-cli`
   (docs/10 р. 10.6) и создать администратора:
   `sudo reportgen-cli useradd --login admin --role admin`.
4. Библиотека в `/var/lib/reportgen/library/<тип>/`, затем `sudo reportgen-cli ingest`.
5. Поднять `reportgen`, подключить nginx с сертификатом, проверить вход.
6. Поставить `backup.sh` в расписание и один раз прогнать руками.

Полностью, с проверками и типовыми проблемами, — в
[docs/10-deployment.md](../docs/10-deployment.md).

## Чего здесь нет

- Секретов и сертификатов: `/etc/reportgen/*.env`, ключи TLS и модели в
  репозиторий не попадают.
- Конфигурации мониторинга: метрики llama.cpp отдаёт на `/metrics`
  (флаг `--metrics` в юните), приложение — на `/api/health`; чем это собирать,
  зависит от того, что уже стоит в контуре.
