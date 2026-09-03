"""Свой сертификат для работы по https в изолированной сети.

Уведомление на рабочем столе браузер показывает только «защищённой»
странице: https или адрес самой машины. Отдел работает по http на адрес в
сети — и на клиентских местах такого окна у браузера нет вовсе. Свернул
окно, тебя вызывают в кабинет, а ты об этом не знаешь.

Купить сертификат в изолированном контуре не у кого, и выпускать его некому.
Поэтому система выписывает его себе сама: на своё имя и на свой адрес в сети.
Браузер при первом заходе скажет, что не знает такого удостоверителя, — это
ожидаемо, и лечится один раз на каждой машине: либо «Всё равно перейти», либо
установкой корневого сертификата в хранилище Windows (тогда предупреждения
не будет вовсе). После этого страница считается защищённой, и уведомления на
рабочем столе работают.

Ключ и сертификат кладутся в каталог данных и переживают перезапуск: заново
выписывать их каждый раз нельзя — тогда браузер ругался бы каждый день.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import socket
from pathlib import Path
from typing import List, Tuple

__all__ = ["CertificateError", "ensure_certificate", "local_addresses"]

log = logging.getLogger("reportgen.web.tls")

#: Сколько лет живёт свой сертификат. Десять — чтобы про него забыли: в
#: изолированном контуре некому следить за сроками, а протухший сертификат
#: остановит работу отдела в самый неподходящий день.
YEARS = 10

#: Имена файлов в каталоге данных.
CERT_NAME = "сервер.crt"
KEY_NAME = "сервер.key"


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


def _cryptography():
    try:
        from cryptography import x509                       # noqa: PLC0415
        from cryptography.hazmat.primitives import hashes    # noqa: PLC0415
        from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415
        from cryptography.x509.oid import NameOID            # noqa: PLC0415
    except ImportError as error:                 # pragma: no cover — зависит от среды
        raise CertificateError(
            "для работы по https нужен пакет cryptography "
            "(pip install cryptography). Без него система работает по http, "
            "но браузер не будет показывать уведомления на рабочем столе"
        ) from error
    return x509, hashes, serialization, rsa, NameOID


def ensure_certificate(data_dir: Path, brand: str = "2 специальный отдел",
                       hosts: "List[str] | None" = None,
                       extra_hosts: "List[str] | None" = None) -> "Tuple[Path, Path]":
    """Вернуть пути к сертификату и ключу, выписав их при первом запуске."""
    folder = Path(data_dir) / "tls"
    cert_path = folder / CERT_NAME
    key_path = folder / KEY_NAME
    if cert_path.is_file() and key_path.is_file():
        return cert_path, key_path

    x509, hashes, serialization, rsa, NameOID = _cryptography()
    addresses = list(hosts or local_addresses())
    for name in extra_hosts or []:
        if name not in addresses:
            addresses.append(name)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname() or "reportgen"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, brand),
    ])
    # Имена, по которым к системе обращаются: имя машины и все её адреса.
    # Адрес, которого здесь нет, браузер отвергнет отдельной руганью.
    names: List[object] = []
    for address in addresses:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(address)))
        except ValueError:
            names.append(x509.DNSName(address))
    host = socket.gethostname()
    if host:
        names.append(x509.DNSName(host))
        names.append(x509.DNSName("localhost"))

    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)                    # сам себе удостоверитель
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365 * YEARS))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    folder.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    # Ключ — секрет: на Windows права наследуются от каталога данных, на
    # прочих системах закрываем его явно.
    try:
        key_path.chmod(0o600)
    except OSError:                              # pragma: no cover — Windows
        pass
    log.info("выписан свой сертификат на %s", ", ".join(addresses))
    return cert_path, key_path
