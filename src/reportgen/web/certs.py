# -*- coding: utf-8 -*-
"""Выписка сертификата средствами одной лишь стандартной библиотеки.

Отдел работает в изолированном контуре: интернета нет, и `pip install` там —
не действие, а пожелание. Прежняя выписка сертификата опиралась на пакет
cryptography, и на машине, где его не оказалось, установка https упиралась в
совет «поставьте пакет», выполнить который негде. Совет, который нельзя
выполнить, — это тупик, а не сообщение об ошибке.

Поэтому всё, что нужно для своего сертификата, собрано здесь и опирается
только на то, что есть в самом Python: hashlib, secrets и длинная арифметика.
Ничего экзотического тут нет — сертификат X.509 это дерево из четырёх типов
DER, ключ RSA это два простых числа, подпись — возведение в степень по
модулю. Всё это стандартная библиотека умеет.

Что выписывается:

* RSA-2048 с открытой экспонентой 65537 — то же, что выписал бы любой
  удостоверяющий центр; ключ ищется через решето малыми простыми и проверку
  Миллера — Рабина на криптографически стойком источнике случайности;
* сертификат X.509 версии 3 с расширениями, без которых Windows и Chrome
  сертификат не примут: subjectAltName со всеми адресами машины,
  basicConstraints, keyUsage, extendedKeyUsage=serverAuth,
  subjectKeyIdentifier и authorityKeyIdentifier;
* подпись RSASSA-PKCS1-v1_5 с SHA-256.

Проверяется это не на слово: в tests/test_tls.py выписанная пара скармливается
настоящему ssl.SSLContext и по ней поднимается настоящее соединение, а разбор
сверяется с пакетом cryptography там, где он есть.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import ipaddress
import secrets
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

__all__ = [
    "RsaKey",
    "generate_key",
    "make_certificate",
    "certificate_pem",
    "certificate_der",
    "key_identifier",
    "load_key_pem",
]

# --- DER: четыре типа и немного обвязки -------------------------------------
#
# Всё дерево сертификата собирается из последовательностей, целых чисел, строк
# и битовых строк. Кодирование длины в DER одно на всех: до 127 — одним
# байтом, дальше — байт «сколько байтов длины» и сама длина.


def _len(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    body = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _len(len(body)) + body


def _integer(value: int) -> bytes:
    """Целое DER: минимальное дополнение до двух, ведущий ноль для знака."""
    if value == 0:
        return _tlv(0x02, b"\x00")
    if value < 0:                                # в сертификатах не встречается
        raise ValueError("отрицательные числа тут не нужны")
    body = value.to_bytes((value.bit_length() + 7) // 8 or 1, "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return _tlv(0x02, body)


def _sequence(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def _set(*items: bytes) -> bytes:
    return _tlv(0x31, b"".join(items))


def _oid(text: str) -> bytes:
    parts = [int(item) for item in text.split(".")]
    body = bytearray([parts[0] * 40 + parts[1]])
    for number in parts[2:]:
        chunk = bytearray([number & 0x7F])
        number >>= 7
        while number:
            chunk.insert(0, (number & 0x7F) | 0x80)
            number >>= 7
        body += chunk
    return _tlv(0x06, bytes(body))


def _null() -> bytes:
    return b"\x05\x00"


def _boolean(value: bool) -> bytes:
    return _tlv(0x01, b"\xff" if value else b"\x00")


def _octets(body: bytes) -> bytes:
    return _tlv(0x04, body)


def _utf8(text: str) -> bytes:
    return _tlv(0x0C, text.encode("utf-8"))


def _ia5(text: str) -> bytes:
    return _tlv(0x16, text.encode("ascii"))


def _bits(body: bytes, unused: int = 0) -> bytes:
    return _tlv(0x03, bytes([unused]) + body)


def _named_bits(indices: Sequence[int]) -> bytes:
    """Битовая строка с именованными битами: хвостовые нули DER не хранит."""
    if not indices:
        return _bits(b"", 0)
    highest = max(indices)
    body = bytearray((highest // 8) + 1)
    for index in indices:
        body[index // 8] |= 0x80 >> (index % 8)
    return _bits(bytes(body), 7 - (highest % 8))


def _explicit(number: int, body: bytes) -> bytes:
    """Контекстная явная метка [n] — версия сертификата и список расширений."""
    return _tlv(0xA0 | number, body)


def _implicit(number: int, body: bytes) -> bytes:
    """Контекстная неявная метка [n] — имена в subjectAltName."""
    return _tlv(0x80 | number, body)


def _time(when: dt.datetime) -> bytes:
    """До 2050 года — UTCTime, дальше GeneralizedTime: так велит RFC 5280."""
    if when.year < 2050:
        return _tlv(0x17, when.strftime("%y%m%d%H%M%SZ").encode("ascii"))
    return _tlv(0x18, when.strftime("%Y%m%d%H%M%SZ").encode("ascii"))


# --- Значения из справочников ------------------------------------------------

OID_RSA = "1.2.840.113549.1.1.1"
OID_SHA256_RSA = "1.2.840.113549.1.1.11"
OID_CN = "2.5.4.3"
OID_O = "2.5.4.10"
OID_BASIC = "2.5.29.19"
OID_KEY_USAGE = "2.5.29.15"
OID_EXT_KEY_USAGE = "2.5.29.37"
OID_SAN = "2.5.29.17"
OID_SKI = "2.5.29.14"
OID_AKI = "2.5.29.35"
OID_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"

#: Готовая обёртка DigestInfo для SHA-256 — она одна и та же всегда.
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

#: Простые числа для решета: почти все составные кандидаты отсеиваются ими,
#: не доходя до дорогой проверки Миллера — Рабина.
SMALL_PRIMES = [
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
    73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
    157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233,
    239, 241, 251, 257, 263, 269, 271, 277, 281, 283, 293, 307, 311, 313, 317,
    331, 337, 347, 349, 353, 359, 367, 373, 379, 383, 389, 397, 401, 409, 419,
    421, 431, 433, 439, 443, 449, 457, 461, 463, 467, 479, 487, 491, 499, 503,
    509, 521, 523, 541, 547, 557, 563, 569, 571, 577, 587, 593, 599, 601, 607,
    613, 617, 619, 631, 641, 643, 647, 653, 659, 661, 673, 677, 683, 691, 701,
    709, 719, 727, 733, 739, 743, 751, 757, 761, 769, 773, 787, 797, 809, 811,
    821, 823, 827, 829, 839, 853, 857, 859, 863, 877, 881, 883, 887, 907, 911,
    919, 929, 937, 941, 947, 953, 967, 971, 977, 983, 991, 997,
]

#: Сколько раз проверять кандидата по Миллеру — Рабину. Сорок раундов дают
#: вероятность ошибки меньше 4^-40: столько же берут и настоящие библиотеки.
ROUNDS = 40


# --- Ключ RSA ----------------------------------------------------------------

def _probably_prime(number: int, rounds: int = ROUNDS) -> bool:
    """Проверка Миллера — Рабина на случайных основаниях."""
    if number < 2:
        return False
    for prime in SMALL_PRIMES:
        if number == prime:
            return True
        if number % prime == 0:
            return False

    degree = 0
    odd = number - 1
    while odd % 2 == 0:
        odd //= 2
        degree += 1

    for _ in range(rounds):
        base = secrets.randbelow(number - 3) + 2
        witness = pow(base, odd, number)
        if witness in (1, number - 1):
            continue
        for _ in range(degree - 1):
            witness = pow(witness, 2, number)
            if witness == number - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, exponent: int) -> int:
    """Простое число нужной длины, годное для RSA с этой экспонентой.

    Два старших бита ставим единицами: тогда произведение двух таких простых
    гарантированно займёт ровно столько бит, сколько мы обещали, — иначе ключ
    иногда получался бы на бит короче заказанного.
    """
    while True:
        candidate = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if any(candidate % prime == 0 for prime in SMALL_PRIMES if prime < candidate):
            continue
        # Экспонента обязана быть обратима по модулю p-1, иначе закрытого
        # ключа не существует.
        if (candidate - 1) % exponent == 0:
            continue
        if _probably_prime(candidate):
            return candidate


@dataclass(frozen=True)
class RsaKey:
    """Пара ключей RSA: всё, что нужно для подписи и для записи на диск."""

    n: int
    e: int
    d: int
    p: int
    q: int

    @property
    def size(self) -> int:
        """Длина модуля в байтах — размер подписи ровно такой же."""
        return (self.n.bit_length() + 7) // 8

    # -- запись -------------------------------------------------------------
    def public_der(self) -> bytes:
        """SubjectPublicKeyInfo: то, что ложится в сертификат."""
        raw = _sequence(_integer(self.n), _integer(self.e))
        return _sequence(_sequence(_oid(OID_RSA), _null()), _bits(raw))

    def private_der(self) -> bytes:
        """PKCS#8 — его понимают и Python, и OpenSSL, и Windows."""
        dp = self.d % (self.p - 1)
        dq = self.d % (self.q - 1)
        qinv = pow(self.q, -1, self.p)
        inner = _sequence(
            _integer(0), _integer(self.n), _integer(self.e), _integer(self.d),
            _integer(self.p), _integer(self.q), _integer(dp), _integer(dq),
            _integer(qinv))
        return _sequence(_integer(0), _sequence(_oid(OID_RSA), _null()),
                         _octets(inner))

    def private_pem(self) -> bytes:
        return _pem("PRIVATE KEY", self.private_der())

    # -- подпись ------------------------------------------------------------
    def sign(self, data: bytes) -> bytes:
        """RSASSA-PKCS1-v1_5 с SHA-256 — то же, чем подписывают все.

        Подписываем по китайской теореме об остатках: вдвое-втрое быстрее
        прямого возведения в степень, а результат тот же самый.
        """
        digest = SHA256_PREFIX + hashlib.sha256(data).digest()
        room = self.size - len(digest) - 3
        if room < 8:
            raise ValueError("ключ слишком короток для подписи SHA-256")
        block = b"\x00\x01" + b"\xff" * room + b"\x00" + digest
        message = int.from_bytes(block, "big")

        dp = self.d % (self.p - 1)
        dq = self.d % (self.q - 1)
        qinv = pow(self.q, -1, self.p)
        m1 = pow(message % self.p, dp, self.p)
        m2 = pow(message % self.q, dq, self.q)
        joined = (m2 + self.q * ((qinv * (m1 - m2)) % self.p)) % self.n
        return joined.to_bytes(self.size, "big")


def generate_key(bits: int = 2048, exponent: int = 65537) -> RsaKey:
    """Новая пара ключей. Две-три секунды на обычной машине — раз в десять лет."""
    if bits < 512 or bits % 2:
        raise ValueError("длина ключа должна быть чётной и не меньше 512 бит")
    half = bits // 2
    while True:
        p = _prime(half, exponent)
        q = _prime(half, exponent)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        # Наименьшее общее кратное, а не произведение: так делает RFC 8017,
        # и закрытая экспонента получается короче.
        lam = (p - 1) * (q - 1) // _gcd(p - 1, q - 1)
        try:
            d = pow(exponent, -1, lam)
        except ValueError:                       # pragma: no cover — отсеяно раньше
            continue
        if d.bit_length() < bits // 2:           # редкая слабая пара
            continue
        return RsaKey(n=n, e=exponent, d=d, p=p, q=q)


def _gcd(first: int, second: int) -> int:
    while second:
        first, second = second, first % second
    return first


def load_key_pem(text: bytes) -> RsaKey:
    """Прочитать свой же ключ обратно: нужно, чтобы доподписать новый сертификат."""
    body = _unpem(text, "PRIVATE KEY")
    outer, _ = _read(body, 0)
    version, offset = _read(outer, 0)
    del version
    _algorithm, offset = _read(outer, offset)
    inner, _ = _read(outer, offset)
    # Внутри строки октетов лежит своя последовательность RSAPrivateKey —
    # её надо развернуть, прежде чем читать числа.
    numbers_body, _ = _read(inner, 0)
    numbers: List[int] = []
    position = 0
    while position < len(numbers_body) and len(numbers) < 9:
        value, position = _read_int(numbers_body, position)
        numbers.append(value)
    if len(numbers) < 6:
        raise ValueError("это не ключ RSA")
    return RsaKey(n=numbers[1], e=numbers[2], d=numbers[3], p=numbers[4], q=numbers[5])


def _read(body: bytes, position: int) -> "Tuple[bytes, int]":
    """Прочитать одно значение DER, вернув содержимое и место следующего."""
    position += 1
    size = body[position]
    position += 1
    if size & 0x80:
        count = size & 0x7F
        size = int.from_bytes(body[position:position + count], "big")
        position += count
    return body[position:position + size], position + size


def _read_int(body: bytes, position: int) -> "Tuple[int, int]":
    raw, nxt = _read(body, position)
    return int.from_bytes(raw, "big"), nxt


# --- Сертификат ---------------------------------------------------------------

def key_identifier(key: RsaKey) -> bytes:
    """Отпечаток открытого ключа: по нему Windows строит цепочку доверия."""
    raw = _sequence(_integer(key.n), _integer(key.e))
    return hashlib.sha1(raw).digest()            # noqa: S324 — так велит RFC 5280


def _name(common: str, organization: str) -> bytes:
    return _sequence(
        _set(_sequence(_oid(OID_CN), _utf8(common))),
        _set(_sequence(_oid(OID_O), _utf8(organization))))


def _san(hosts: Iterable[str]) -> bytes:
    names: List[bytes] = []
    for host in hosts:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            names.append(_implicit(2, host.encode("idna") if not host.isascii()
                                   else host.encode("ascii")))
        else:
            names.append(_implicit(7, address.packed))
    return _sequence(*names)


def _extension(oid: str, critical: bool, body: bytes) -> bytes:
    parts = [_oid(oid)]
    if critical:
        parts.append(_boolean(True))
    parts.append(_octets(body))
    return _sequence(*parts)


def make_certificate(*, subject_cn: str, organization: str, public: RsaKey,
                     signer: RsaKey, issuer_cn: str, issuer_organization: str,
                     days: int, hosts: "Sequence[str] | None" = None,
                     is_ca: bool = False, serial: "int | None" = None,
                     issuer_key: "RsaKey | None" = None,
                     now: "dt.datetime | None" = None) -> bytes:
    """Собрать и подписать сертификат X.509 v3. Возвращает DER.

    `public` — чей ключ удостоверяем, `signer` — чьим ключом подписываем. Для
    самоподписанного корня это один и тот же ключ.
    """
    moment = now or dt.datetime.now(dt.timezone.utc)
    # На сутки назад: часы на машинах отдела расходятся, и сертификат «из
    # будущего» браузер отвергает так же решительно, как просроченный.
    start = moment - dt.timedelta(days=1)
    finish = moment + dt.timedelta(days=days)

    extensions = [
        _extension(OID_BASIC, True,
                   _sequence(_boolean(True)) if is_ca else _sequence()),
        _extension(OID_KEY_USAGE, True,
                   _named_bits([0, 5, 6]) if is_ca else _named_bits([0, 2])),
        _extension(OID_SKI, False, _octets(key_identifier(public))),
        _extension(OID_AKI, False,
                   _sequence(_implicit(0, key_identifier(issuer_key or signer)))),
    ]
    if not is_ca:
        # Без serverAuth Windows считает сертификат негодным для сервера, даже
        # если он лежит в доверенных.
        extensions.append(_extension(OID_EXT_KEY_USAGE, False,
                                     _sequence(_oid(OID_SERVER_AUTH))))
    if hosts:
        extensions.append(_extension(OID_SAN, False, _san(hosts)))

    algorithm = _sequence(_oid(OID_SHA256_RSA), _null())
    tbs = _sequence(
        _explicit(0, _integer(2)),               # версия 3
        _integer(serial if serial is not None else secrets.randbits(159) | 1),
        algorithm,
        _name(issuer_cn, issuer_organization),
        _sequence(_time(start), _time(finish)),
        _name(subject_cn, organization),
        public.public_der(),
        _explicit(3, _sequence(*extensions)))
    return _sequence(tbs, algorithm, _bits(signer.sign(tbs)))


def _pem(label: str, body: bytes) -> bytes:
    text = base64.b64encode(body).decode("ascii")
    lines = [text[i:i + 64] for i in range(0, len(text), 64)]
    return ("-----BEGIN %s-----\n%s\n-----END %s-----\n"
            % (label, "\n".join(lines), label)).encode("ascii")


def _unpem(text: bytes, label: str) -> bytes:
    head = ("-----BEGIN %s-----" % label).encode("ascii")
    tail = ("-----END %s-----" % label).encode("ascii")
    start = text.index(head) + len(head)
    finish = text.index(tail, start)
    return base64.b64decode(b"".join(text[start:finish].split()))


def certificate_pem(der: bytes) -> bytes:
    return _pem("CERTIFICATE", der)


def certificate_der(text: bytes) -> bytes:
    """Первый сертификат из файла PEM: в цепочке за ним может идти корень."""
    return _unpem(text, "CERTIFICATE")


def read_certificate(text: bytes) -> dict:
    """Достать из своего же сертификата имена и срок — для отчёта человеку.

    Разбор нарочно неполный: нам нужны ровно те два поля, которые человек
    сверяет глазами, — по каким адресам сертификат годен и до какого числа.
    """
    der = certificate_der(text)
    body, _ = _read(der, 0)                      # Certificate
    tbs, _ = _read(body, 0)                      # TBSCertificate
    position = 0
    _version, position = _read(tbs, position)    # [0] версия
    _serial, position = _read(tbs, position)
    _algorithm, position = _read(tbs, position)
    _issuer, position = _read(tbs, position)
    validity, position = _read(tbs, position)
    _subject, position = _read(tbs, position)
    _public, position = _read(tbs, position)
    extensions_body, _ = _read(tbs, position)    # [3] расширения

    # Срок: второе значение в Validity.
    _start, offset = _read(validity, 0)
    finish_raw = validity[offset + 2:offset + 2 + validity[offset + 1]]
    until = _human_time(finish_raw)

    hosts: List[str] = []
    block, _ = _read(extensions_body, 0)
    position = 0
    while position < len(block):
        one, position = _read(block, position)
        oid_raw, inner = _read(one, 0)
        if _tlv(0x06, oid_raw) != _oid(OID_SAN):
            continue
        if one[inner] == 0x01:                   # critical BOOLEAN
            _flag, inner = _read(one, inner)
        value, _ = _read(one, inner)             # OCTET STRING
        names, _ = _read(value, 0)               # GeneralNames
        place = 0
        while place < len(names):
            tag = names[place]
            raw, place = _read(names, place)
            if tag == 0x82:
                hosts.append(raw.decode("ascii", "replace"))
            elif tag == 0x87:
                hosts.append(str(ipaddress.ip_address(raw)))
    return {"hosts": hosts, "until": until[0], "until_date": until[1]}


def _human_time(raw: bytes) -> "Tuple[str, dt.date | None]":
    """UTCTime или GeneralizedTime: и для человека, и для сравнения со сроком."""
    text = raw.decode("ascii", "replace").rstrip("Z")
    if len(text) == 12:                          # ГГММДДЧЧММСС
        year = int(text[:2])
        text = ("19" if year >= 50 else "20") + text
    if len(text) < 8:                            # pragma: no cover — не наш файл
        return text, None
    try:
        когда = dt.date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:                           # pragma: no cover — не наш файл
        return text, None
    return "%s.%s.%s" % (text[6:8], text[4:6], text[:4]), когда
