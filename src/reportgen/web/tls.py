"""Свой сертификат для работы по https в изолированной сети.

Уведомление на рабочем столе браузер показывает только «защищённой»
странице: https или адрес самой машины. Отдел работает по http на адрес в
сети — и на клиентских местах такого окна у браузера нет вовсе. Свернул
окно, тебя вызывают в кабинет, а ты об этом не знаешь.

Купить сертификат в изолированном контуре не у кого, и выпускать его некому.
Поэтому система выписывает его себе сама — средствами одной стандартной
библиотеки (см. certs.py). Сторонних пакетов для этого не нужно: на машине
без интернета «поставьте пакет» — это не совет, а тупик.

Выписывается ДВА сертификата, а не один, и это не усложнение ради красоты:

* корень (корень.crt) — им подписывается всё остальное. Только его ставят в
  доверенные на рабочих местах, и делают это ОДИН РАЗ: корень не зависит ни
  от адреса машины, ни от её имени;
* серверный (сервер.crt) — тот, который показывает браузеру сама система. В
  нём перечислены адреса, по которым к ней обращаются.

Разница видна в тот день, когда у сервера меняется адрес в сети. С одним
самоподписанным сертификатом пришлось бы обойти все рабочие места заново. С
корнем и серверным система замечает смену адреса сама, перевыписывает
серверный при следующем запуске — и на рабочих местах не трогают ничего.

Ключ и сертификаты кладутся в каталог данных и переживают перезапуск:
выписывать их заново каждый раз нельзя — тогда браузер ругался бы каждый
день. Установки, сделанные до появления корня, продолжают работать со своим
прежним сертификатом: менять то, что уже разнесено по рабочим местам, нельзя.
"""

from __future__ import annotations

import datetime as dt
import logging
import socket
from pathlib import Path
from typing import List, Tuple

from . import certs

__all__ = ["CertificateError", "ensure_certificate", "local_addresses",
           "root_certificate"]

log = logging.getLogger("reportgen.web.tls")

#: Сколько лет живёт свой сертификат. Десять — чтобы про него забыли: в
#: изолированном контуре некому следить за сроками, а протухший сертификат
#: остановит работу отдела в самый неподходящий день.
YEARS = 10

#: Сколько лет живёт серверный сертификат. Меньше корня: его перевыписывают
#: при смене адреса, и рабочих мест это не касается.
SERVER_YEARS = 5

#: Длина ключа. 2048 бит — то же, что у настоящих удостоверителей; выписка
#: занимает около секунды, а делается раз в несколько лет.
KEY_BITS = 2048

#: Имена файлов в каталоге данных.
CERT_NAME = "сервер.crt"
KEY_NAME = "сервер.key"
ROOT_NAME = "корень.crt"
ROOT_KEY_NAME = "корень.key"
HOSTS_NAME = "адреса.txt"


class CertificateError(RuntimeError):
    """Сертификат не выписать — с причиной, понятной человеку."""


def local_addresses() -> List[str]:
    """Адреса этой машины в сети: по ним к системе и обращаются.

    Сертификат обязан перечислять их все: браузер сверяет адрес в строке с
    тем, что написано в сертификате, и на несовпадение ругается отдельно.
    """
    found: List[str] = ["127.0.0.1", "::1"]

    # Самый надёжный способ узнать свой адрес в сети — спросить у системы,
    # с какого адреса она пошла бы наружу. Ни одного пакета при этом не
    # отправляется: сокет только выбирает маршрут. Разбор имени машины
    # (getaddrinfo) этого не заменяет — в сети без DNS он часто отдаёт
    # только петлевой адрес, и сертификат выписывается не на тот адрес,
    # по которому к системе обращаются.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
        if address and address not in found:
            found.append(address)
    except OSError:                              # pragma: no cover — сети может не быть
        pass
    finally:
        probe.close()

    try:
        host = socket.gethostname()
    except OSError:                              # pragma: no cover — редкость
        return found
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            for item in socket.getaddrinfo(host, None, family):
                address = str(item[4][0]).split("%")[0]
                if address not in found:
                    found.append(address)
        except OSError:                          # noqa: PERF203 — адреса может не быть
            continue
    return found


def _stamp(hosts: List[str]) -> str:
    """Строка, по которой видно, изменился ли набор адресов машины."""
    return "\n".join(sorted(set(hosts)))


def root_certificate(data_dir: Path) -> "Path | None":
    """Путь к корню — тому файлу, который ставят на рабочие места.

    None означает установку старого образца: там корня нет, а на рабочих
    местах стоит сам серверный сертификат.
    """
    path = Path(data_dir) / "tls" / ROOT_NAME
    return path if path.is_file() else None


def _split(names: "List[str] | None") -> List[str]:
    """Разобрать перечисление адресов.

    Человек напишет их и через запятую в одной строке, и по одному ключу на
    адрес — и будет прав в обоих случаях. Запятой в имени машины быть не
    может, так что разбор безопасен, а имя вида «192.168.10.5,otdel-server»
    в сертификате — это адрес, по которому браузер не откроется никогда.
    """
    out: List[str] = []
    if isinstance(names, str):                   # строку перебирать по буквам нельзя
        names = [names]
    for item in names or []:
        for part in str(item).replace(";", ",").split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _names(hosts: "List[str] | None", extra_hosts: "List[str] | None") -> List[str]:
    """Все имена и адреса, по которым к системе обращаются.

    Адрес, которого в сертификате нет, браузер отвергнет отдельной руганью —
    поэтому лучше вписать лишнее, чем недосчитаться нужного.
    """
    found = list(hosts or local_addresses())
    for name in _split(extra_hosts):
        if name not in found:
            found.append(name)
    host = socket.gethostname()
    for name in (host, host.split(".")[0] if host else "", "localhost"):
        if name and name not in found:
            found.append(name)
    return found


#: За сколько дней до конца срока сертификат выписывается заново. Полгода —
#: чтобы это случилось задолго до того дня, когда браузер начнёт ругаться, и
#: чтобы никто в отделе не следил за сроками вручную.
RENEW_BEFORE_DAYS = 180


def _hasnt_expired(cert_path: Path) -> bool:
    """Сертификат ещё годен и годен будет — иначе перевыписываем заранее.

    Просроченный сертификат останавливает работу отдела в самый неподходящий
    день, и следить за сроками в изолированном контуре некому.
    """
    try:
        сведения = certs.read_certificate(cert_path.read_bytes())
    except (ValueError, IndexError, OSError):    # pragma: no cover — испорчен
        return False
    конец = сведения.get("until_date")
    if конец is None:                            # pragma: no cover — не наш файл
        return False
    осталось = (конец - dt.date.today()).days
    if осталось < RENEW_BEFORE_DAYS:
        log.info("сертификату осталось %d дней — выписываем новый заранее",
                 осталось)
        return False
    return True


def _write_secret(path: Path, body: bytes) -> None:
    """Записать ключ, закрыв его от посторонних насколько позволяет система."""
    path.write_bytes(body)
    try:
        path.chmod(0o600)
    except OSError:                              # pragma: no cover — Windows
        pass


def _issue_root(folder: Path, brand: str) -> "Tuple[bytes, certs.RsaKey]":
    """Выписать корень: он подписывает серверные сертификаты и живёт долго."""
    name = "%s — корень" % brand
    key = certs.generate_key(KEY_BITS)
    der = certs.make_certificate(
        subject_cn=name, organization=brand,
        public=key, signer=key,
        issuer_cn=name, issuer_organization=brand,
        days=365 * YEARS, is_ca=True)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ROOT_NAME).write_bytes(certs.certificate_pem(der))
    _write_secret(folder / ROOT_KEY_NAME, key.private_pem())
    log.info("выписан свой корневой сертификат «%s»", name)
    return der, key


def _issue_server(folder: Path, brand: str, hosts: List[str],
                  root_der: bytes, root_key: certs.RsaKey) -> None:
    """Выписать серверный сертификат на текущие адреса машины."""
    host = socket.gethostname() or "reportgen"
    key = certs.generate_key(KEY_BITS)
    der = certs.make_certificate(
        subject_cn=host, organization=brand,
        public=key, signer=root_key,
        issuer_cn="%s — корень" % brand, issuer_organization=brand,
        days=365 * SERVER_YEARS, hosts=hosts, issuer_key=root_key)
    # Браузеру отдаём цепочку целиком: серверный, за ним корень. Иначе
    # машина, где корень не поставлен, не сможет даже показать, кто его
    # выписал, — а человеку в предупреждении браузера важно видеть, что это
    # свой отдел, а не неизвестно кто.
    (folder / CERT_NAME).write_bytes(
        certs.certificate_pem(der) + certs.certificate_pem(root_der))
    _write_secret(folder / KEY_NAME, key.private_pem())
    (folder / HOSTS_NAME).write_text(_stamp(hosts), encoding="utf-8")
    log.info("выписан свой сертификат на %s", ", ".join(hosts))


def ensure_certificate(data_dir: Path, brand: str = "2 специальный отдел",
                       hosts: "List[str] | None" = None,
                       extra_hosts: "List[str] | None" = None,
                       renew: bool = False) -> "Tuple[Path, Path]":
    """Вернуть пути к сертификату и ключу, выписав их при первом запуске.

    Ключ `renew` выбрасывает прежний серверный сертификат, оставляя корень:
    так делают, когда у машины сменился адрес, а обходить рабочие места
    заново незачем.
    """
    folder = Path(data_dir) / "tls"
    cert_path = folder / CERT_NAME
    key_path = folder / KEY_NAME
    root_path = folder / ROOT_NAME
    root_key_path = folder / ROOT_KEY_NAME

    # Установка старого образца: сертификат уже разнесён по рабочим местам, и
    # подменять его на новый молча нельзя — люди перестанут заходить. Но по
    # прямой просьбе («перевыписать») отступать некуда: старый сертификат
    # либо просрочен, либо выписан не на тот адрес, и работать по нему всё
    # равно нельзя. Тогда переходим на корень с серверным и говорим об этом.
    if cert_path.is_file() and key_path.is_file() and not root_path.is_file():
        if not renew:
            return cert_path, key_path
        log.warning("прежний сертификат выписан по-старому: выписываем заново "
                    "с корнем. На рабочих местах установите новый корень.crt")
        for прежний in (cert_path, key_path):
            прежний.rename(прежний.with_suffix(прежний.suffix + ".прежний"))

    names = _names(hosts, extra_hosts)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if root_path.is_file() and root_key_path.is_file():
            root_der = certs.certificate_der(root_path.read_bytes())
            root_key = certs.load_key_pem(root_key_path.read_bytes())
        else:
            root_der, root_key = _issue_root(folder, brand)

        свежий = (cert_path.is_file() and key_path.is_file()
                  and not renew
                  and (folder / HOSTS_NAME).is_file()
                  and (folder / HOSTS_NAME).read_text(encoding="utf-8") == _stamp(names)
                  and _hasnt_expired(cert_path))
        if not свежий:
            if renew or cert_path.is_file():
                log.info("перевыписываем серверный сертификат; на рабочих "
                         "местах менять ничего не нужно")
            _issue_server(folder, brand, names, root_der, root_key)
    except OSError as error:
        raise CertificateError(
            "не удалось записать сертификат в %s: %s" % (folder, error)) from error
    except ValueError as error:                  # pragma: no cover — испорченный файл
        raise CertificateError(
            "прежний сертификат в %s испорчен: %s. Удалите каталог tls — "
            "система выпишет его заново" % (folder, error)) from error
    return cert_path, key_path


def describe(data_dir: Path) -> dict:
    """Что сейчас выписано: для отчёта человеку и для проверки после выписки.

    Сертификат разбирается собственным разбором, а годность пары проверяется
    так же, как её проверит сам сервер, — загрузкой в ssl. Рапортовать
    «готово», не убедившись, что файлы годны, нельзя: человек уйдёт с этим на
    рабочие места и вернётся ни с чем.
    """
    import ssl                                   # noqa: PLC0415 — только для проверки

    folder = Path(data_dir) / "tls"
    cert_path = folder / CERT_NAME
    key_path = folder / KEY_NAME
    root_path = folder / ROOT_NAME
    report: dict = {
        "cert": str(cert_path),
        "key": str(key_path),
        "root": str(root_path) if root_path.is_file() else "",
        "hosts": [],
        "until": "",
        "ok": False,
        "problem": "",
    }
    if not (cert_path.is_file() and key_path.is_file()):
        report["problem"] = "сертификат не выписан"
        return report
    try:
        info = certs.read_certificate(cert_path.read_bytes())
        report["hosts"] = info["hosts"]
        report["until"] = info["until"]
    except (ValueError, IndexError) as error:    # pragma: no cover — испорченный файл
        report["problem"] = "сертификат не читается: %s" % error
        return report
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))
    except (ssl.SSLError, OSError) as error:
        report["problem"] = "пара сертификат/ключ не годится: %s" % error
        return report
    report["ok"] = True
    return report


def main(argv: "List[str] | None" = None) -> int:
    """Выписать сертификат из командной строки: этим пользуются скрипты."""
    import argparse                              # noqa: PLC0415
    import json                                  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="python -m reportgen.web.tls",
        description="Свой сертификат для работы по https")
    parser.add_argument("--data-dir", required=True, help="каталог данных")
    parser.add_argument("--brand", default="2 специальный отдел")
    parser.add_argument("--host", action="append", default=[],
                        help="дополнительное имя или адрес (можно несколько)")
    parser.add_argument("--renew", action="store_true",
                        help="перевыписать серверный сертификат заново")
    parser.add_argument("--check", action="store_true",
                        help="только посмотреть, что уже выписано")
    parser.add_argument("--json", action="store_true", help="вывод для скриптов")
    args = parser.parse_args(argv)

    folder = Path(args.data_dir)
    try:
        if not args.check:
            ensure_certificate(folder, brand=args.brand,
                               extra_hosts=list(args.host), renew=args.renew)
        report = describe(folder)
    except CertificateError as error:
        report = {"cert": "", "key": "", "root": "", "hosts": [], "until": "",
                  "ok": False, "problem": str(error)}

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        if report["ok"]:
            print("Сертификат выписан и проверен.")
            print("  файл:   %s" % report["cert"])
            print("  корень: %s" % (report["root"] or "—"))
            print("  адреса: %s" % ", ".join(report["hosts"]))
            print("  годен до: %s" % report["until"])
        else:
            print("Сертификат не готов: %s" % report["problem"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main())
