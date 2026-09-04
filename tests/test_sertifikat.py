# -*- coding: utf-8 -*-
"""Свой сертификат: выписывается без интернета и без сторонних пакетов.

Начальник отдела запустил setup-https.ps1 на машине, где интернета нет, и
получил «ModuleNotFoundError: No module named 'cryptography'» и совет
«pip install cryptography». Совет, который негде выполнить, — это тупик:
изолированный контур на то и изолированный.

Теперь всё нужное для сертификата собрано в reportgen.web.certs и опирается
только на стандартную библиотеку. Проверяется это не на слово:

* сертификат разбирается ЧУЖИМ, проверенным кодом — пакетом cryptography там,
  где он есть (в отделе его нет, здесь при разработке — есть);
* по выписанной паре поднимается НАСТОЯЩЕЕ соединение TLS, и клиент сверяет
  имя по нашему же корню — как это сделает браузер;
* выписка прогоняется отдельным процессом, которому пакет cryptography
  недоступен вовсе, — ровно как на машине отдела.
"""

import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.web import certs
from reportgen.web.tls import (
    CERT_NAME,
    HOSTS_NAME,
    KEY_NAME,
    ROOT_KEY_NAME,
    ROOT_NAME,
    CertificateError,
    describe,
    ensure_certificate,
    root_certificate,
)

ROOT = Path(__file__).resolve().parents[1]

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    ЕСТЬ_ПАКЕТ = True
except ImportError:                              # pragma: no cover — как в отделе
    ЕСТЬ_ПАКЕТ = False


class РазметкаDER(unittest.TestCase):
    """Основа всего: если DER собран неверно, сертификат никто не прочтёт."""

    def test_короткая_длина_одним_байтом(self):
        self.assertEqual(b"\x04\x03abc", certs._tlv(0x04, b"abc"))

    def test_длина_больше_127_записывается_в_два_приёма(self):
        собрано = certs._tlv(0x04, b"x" * 200)
        self.assertEqual(b"\x04\x81\xc8", собрано[:3])

    def test_целое_с_единицей_в_старшем_бите_получает_ноль_впереди(self):
        """Иначе 200 прочитается как отрицательное число."""
        self.assertEqual(b"\x02\x02\x00\xc8", certs._integer(200))
        self.assertEqual(b"\x02\x01\x7f", certs._integer(127))

    def test_ноль_записывается_одним_байтом(self):
        self.assertEqual(b"\x02\x01\x00", certs._integer(0))

    def test_идентификатор_объекта_складывает_первые_две_доли(self):
        # 1.2.840.113549.1.1.11 — sha256WithRSAEncryption из справочника.
        self.assertEqual(bytes.fromhex("06092a864886f70d01010b"),
                         certs._oid(certs.OID_SHA256_RSA))

    def test_именованные_биты_не_хранят_хвостовых_нулей(self):
        """Так велит DER: keyUsage с битами 0 и 2 — это ровно два байта."""
        self.assertEqual(b"\x03\x02\x05\xa0", certs._named_bits([0, 2]))
        self.assertEqual(b"\x03\x02\x01\x86", certs._named_bits([0, 5, 6]))


class КлючRSA(unittest.TestCase):
    """Ключ должен быть настоящим, а не похожим на настоящий."""

    @classmethod
    def setUpClass(cls):
        cls.key = certs.generate_key(1024)

    def test_модуль_нужной_длины(self):
        self.assertEqual(1024, self.key.n.bit_length())

    def test_модуль_это_произведение_двух_простых(self):
        self.assertEqual(self.key.n, self.key.p * self.key.q)
        self.assertNotEqual(self.key.p, self.key.q)
        self.assertTrue(certs._probably_prime(self.key.p))
        self.assertTrue(certs._probably_prime(self.key.q))

    def test_закрытая_экспонента_обратна_открытой(self):
        лямбда = (self.key.p - 1) * (self.key.q - 1) // certs._gcd(
            self.key.p - 1, self.key.q - 1)
        self.assertEqual(1, (self.key.e * self.key.d) % лямбда)

    def test_подпись_разворачивается_открытым_ключом(self):
        """Проверка подписи — это возведение в открытую степень."""
        подпись = self.key.sign("письмо 47/312".encode("utf-8"))
        число = pow(int.from_bytes(подпись, "big"), self.key.e, self.key.n)
        блок = число.to_bytes(self.key.size, "big")
        self.assertTrue(блок.startswith(b"\x00\x01\xff"))
        self.assertIn(certs.SHA256_PREFIX, блок)

    def test_подпись_другого_текста_другая(self):
        self.assertNotEqual(self.key.sign("а".encode("utf-8")),
                            self.key.sign("б".encode("utf-8")))

    def test_ключ_читается_обратно_с_диска(self):
        снова = certs.load_key_pem(self.key.private_pem())
        self.assertEqual((self.key.n, self.key.e, self.key.d, self.key.p, self.key.q),
                         (снова.n, снова.e, снова.d, снова.p, снова.q))

    def test_простое_занимает_всю_отведённую_длину(self):
        """Два старших бита — единицы, иначе модуль выходит короче заказанного.

        Без этого generate_key крутит цикл, пока не повезёт: на машине отдела
        первый запуск по https вместо секунды занимал бы минуты.
        """
        for _ in range(8):
            простое = certs._prime(128, 65537)
            self.assertEqual(128, простое.bit_length())
            self.assertEqual(3, простое >> 126, "старшие два бита не выставлены")

    def test_слишком_короткий_ключ_не_выписывается(self):
        with self.assertRaises(ValueError):
            certs.generate_key(300)


class Выписка(unittest.TestCase):
    """Корень и серверный: что лежит на диске после первого запуска."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.кат = Path(cls._tmp.name)
        cls.cert, cls.key = ensure_certificate(
            cls.кат, extra_hosts=["192.168.10.5", "otdel-server"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_на_диске_и_корень_и_серверный(self):
        папка = self.кат / "tls"
        for имя in (CERT_NAME, KEY_NAME, ROOT_NAME, ROOT_KEY_NAME, HOSTS_NAME):
            self.assertTrue((папка / имя).is_file(), f"нет файла {имя}")

    def test_серверный_файл_несёт_и_корень(self):
        """Браузер должен видеть, кто выписал: цепочка отдаётся целиком."""
        self.assertEqual(2, self.cert.read_bytes().count(b"BEGIN CERTIFICATE"))

    def test_адреса_машины_и_дописанные_вписаны(self):
        сведения = certs.read_certificate(self.cert.read_bytes())
        for адрес in ("127.0.0.1", "192.168.10.5", "otdel-server", "localhost"):
            self.assertIn(адрес, сведения["hosts"])

    def test_перечисление_через_запятую_разбирается(self):
        """«-Hosts 192.168.10.5,otdel-server» одной строкой — обычное дело."""
        with tempfile.TemporaryDirectory() as имя:
            cert, _ = ensure_certificate(Path(имя), extra_hosts=["10.0.0.7,zapas"])
            hosts = certs.read_certificate(cert.read_bytes())["hosts"]
        self.assertIn("10.0.0.7", hosts)
        self.assertIn("zapas", hosts)
        self.assertNotIn("10.0.0.7,zapas", hosts)

    def test_второй_запуск_ничего_не_переписывает(self):
        """Иначе браузер ругался бы каждый день заново."""
        было = self.cert.read_bytes()
        снова, _ = ensure_certificate(self.кат, extra_hosts=["192.168.10.5", "otdel-server"])
        self.assertEqual(было, снова.read_bytes())

    def test_проверка_годности_проходит(self):
        отчёт = describe(self.кат)
        self.assertTrue(отчёт["ok"], отчёт["problem"])
        self.assertTrue(отчёт["root"])
        self.assertRegex(отчёт["until"], r"^\d{2}\.\d{2}\.\d{4}$")

    def test_негодная_пара_не_объявляется_годной(self):
        """«Сертификат выписан» при негодной паре — это ложь, а не отчёт.

        Человек уйдёт с этим на рабочие места и вернётся ни с чем: сервер по
        такой паре не поднимется вовсе.
        """
        with tempfile.TemporaryDirectory() as имя:
            кат = Path(имя)
            ensure_certificate(кат, hosts=["127.0.0.1"])
            чужой = certs.generate_key(2048)
            (кат / "tls" / KEY_NAME).write_bytes(чужой.private_pem())
            отчёт = describe(кат)
            self.assertFalse(отчёт["ok"], "негодная пара объявлена годной")
            self.assertIn("не годится", отчёт["problem"])

    def test_ключи_закрыты_от_посторонних(self):
        if os.name == "nt":                      # pragma: no cover — права от каталога
            self.skipTest("на Windows права наследуются от каталога данных")
        for имя in (KEY_NAME, ROOT_KEY_NAME):
            режим = (self.кат / "tls" / имя).stat().st_mode & 0o777
            self.assertEqual(0o600, режим, f"{имя} открыт посторонним")


class СменаАдреса(unittest.TestCase):
    """Сменился адрес машины — рабочие места обходить заново не должны."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.кат = Path(self._tmp.name)
        self.cert, _ = ensure_certificate(self.кат, hosts=["127.0.0.1", "192.168.10.5"])
        self.корень_было = (self.кат / "tls" / ROOT_NAME).read_bytes()
        self.серверный_было = self.cert.read_bytes()

    def test_новый_адрес_перевыписывает_серверный(self):
        cert, _ = ensure_certificate(self.кат, hosts=["127.0.0.1", "192.168.10.99"])
        self.assertNotEqual(self.серверный_было, cert.read_bytes())
        self.assertIn("192.168.10.99",
                      certs.read_certificate(cert.read_bytes())["hosts"])

    def test_корень_при_этом_остаётся_прежним(self):
        """Он стоит в доверенных на каждом рабочем месте — менять его нельзя."""
        ensure_certificate(self.кат, hosts=["127.0.0.1", "192.168.10.99"])
        self.assertEqual(self.корень_было, (self.кат / "tls" / ROOT_NAME).read_bytes())

    def test_новый_серверный_подписан_тем_же_корнем(self):
        cert, _ = ensure_certificate(self.кат, hosts=["127.0.0.1", "192.168.10.99"])
        новый = certs.certificate_der(cert.read_bytes())
        свой = certs.certificate_der(self.корень_было)
        self.assertTrue(_подпись_сходится(новый, свой),
                        "новый серверный подписан не прежним корнем")

    def test_ключ_renew_перевыписывает_и_без_смены_адреса(self):
        cert, _ = ensure_certificate(self.кат, hosts=["127.0.0.1", "192.168.10.5"],
                                     renew=True)
        self.assertNotEqual(self.серверный_было, cert.read_bytes())
        self.assertEqual(self.корень_было, (self.кат / "tls" / ROOT_NAME).read_bytes())


class СрокНеЗастаётВрасплох(unittest.TestCase):
    """Просроченный сертификат остановит отдел в самый неподходящий день."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.кат = Path(self._tmp.name)
        self.cert, _ = ensure_certificate(self.кат, hosts=["127.0.0.1"])

    def test_подходящий_срок_перевыписывает_заранее(self):
        """Следить за сроками в изолированном контуре некому."""
        from reportgen.web import tls as модуль

        было = self.cert.read_bytes()
        корень_было = (self.кат / "tls" / ROOT_NAME).read_bytes()
        прежний = модуль.RENEW_BEFORE_DAYS
        модуль.RENEW_BEFORE_DAYS = 100_000     # как будто срок уже на носу
        try:
            снова, _ = ensure_certificate(self.кат, hosts=["127.0.0.1"])
        finally:
            модуль.RENEW_BEFORE_DAYS = прежний
        self.assertNotEqual(было, снова.read_bytes(),
                            "сертификат дожидается просрочки")
        self.assertEqual(корень_было, (self.кат / "tls" / ROOT_NAME).read_bytes(),
                         "перевыписан корень — рабочие места придётся обходить")

    def test_свежий_сертификат_не_трогают(self):
        было = self.cert.read_bytes()
        снова, _ = ensure_certificate(self.кат, hosts=["127.0.0.1"])
        self.assertEqual(было, снова.read_bytes())


class СтараяУстановка(unittest.TestCase):
    """Где сертификат уже разнесён по рабочим местам, трогать его нельзя."""

    def test_прежний_самоподписанный_остаётся_как_есть(self):
        with tempfile.TemporaryDirectory() as имя:
            папка = Path(имя) / "tls"
            папка.mkdir(parents=True)
            (папка / CERT_NAME).write_bytes(
                "-----BEGIN CERTIFICATE-----\nстарый\n".encode("utf-8"))
            (папка / KEY_NAME).write_bytes(
                "-----BEGIN PRIVATE KEY-----\nстарый\n".encode("utf-8"))
            cert, key = ensure_certificate(Path(имя))
            self.assertIn("старый".encode("utf-8"), cert.read_bytes())
            self.assertFalse((папка / ROOT_NAME).exists(),
                             "корень выписан поверх работающей установки")
            self.assertIsNone(root_certificate(Path(имя)))

    def test_по_прямой_просьбе_переходит_на_корень_и_говорит_об_этом(self):
        """«Перевыписать» на старой установке не должно молча ничего не делать.

        Раньше ключ -Renew на такой установке не делал ничего, а скрипт
        рапортовал «выписан и проверен»: человек шёл с этим дальше, а
        сертификат оставался прежним.
        """
        with tempfile.TemporaryDirectory() as имя:
            папка = Path(имя) / "tls"
            папка.mkdir(parents=True)
            (папка / CERT_NAME).write_bytes(
                "-----BEGIN CERTIFICATE-----\nстарый\n".encode("utf-8"))
            (папка / KEY_NAME).write_bytes(
                "-----BEGIN PRIVATE KEY-----\nстарый\n".encode("utf-8"))
            cert, key = ensure_certificate(Path(имя), renew=True,
                                           hosts=["127.0.0.1"])
            self.assertNotIn("старый".encode("utf-8"), cert.read_bytes())
            self.assertTrue((папка / ROOT_NAME).is_file(),
                            "корень так и не выписан")
            отчёт = describe(Path(имя))
            self.assertTrue(отчёт["ok"], отчёт["problem"])
            # Прежние файлы не выброшены: если что-то пойдёт не так, к ним
            # можно вернуться.
            self.assertTrue((папка / (CERT_NAME + ".прежний")).is_file())


def _подпись_сходится(лист_der: bytes, корень_der: bytes) -> bool:
    """Проверить подпись листа ключом корня — своими силами, без пакетов."""
    тело, _ = certs._read(лист_der, 0)
    tbs_len_end = 1
    длина = тело[1]
    if длина & 0x80:
        tbs_len_end = 1 + (длина & 0x7F)
    tbs_целиком = тело[:1 + tbs_len_end + _размер(тело)]
    _tbs, позиция = certs._read(тело, 0)
    _алгоритм, позиция = certs._read(тело, позиция)
    подпись_биты, _ = certs._read(тело, позиция)
    подпись = подпись_биты[1:]                   # первый байт — число лишних бит

    корень_тело, _ = certs._read(корень_der, 0)
    корень_tbs, _ = certs._read(корень_тело, 0)
    n, e = _открытый_ключ(корень_tbs)
    число = pow(int.from_bytes(подпись, "big"), e, n)
    блок = число.to_bytes((n.bit_length() + 7) // 8, "big")
    import hashlib
    ожидаем = certs.SHA256_PREFIX + hashlib.sha256(tbs_целиком).digest()
    return блок.endswith(ожидаем) and блок.startswith(b"\x00\x01\xff")


def _размер(тело: bytes) -> int:
    длина = тело[1]
    if not (длина & 0x80):
        return длина
    счёт = длина & 0x7F
    return int.from_bytes(тело[2:2 + счёт], "big")


def _открытый_ключ(tbs: bytes):
    позиция = 0
    for _ in range(6):
        _кусок, позиция = certs._read(tbs, позиция)
    spki, _ = certs._read(tbs, позиция)
    _алг, смещение = certs._read(spki, 0)
    биты, _ = certs._read(spki, смещение)
    последовательность, _ = certs._read(биты[1:], 0)
    n, дальше = certs._read_int(последовательность, 0)
    e, _ = certs._read_int(последовательность, дальше)
    return n, e


class НастоящееСоединение(unittest.TestCase):
    """Самая честная проверка: по сертификату поднимается настоящий TLS."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.кат = Path(cls._tmp.name)
        cls.cert, cls.key = ensure_certificate(cls.кат, hosts=["127.0.0.1", "otdel-server"])
        cls.корень = cls.кат / "tls" / ROOT_NAME

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def поднять(self, имя_в_строке: str):
        сервер = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        сервер.load_cert_chain(str(self.cert), str(self.key))
        клиент = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        клиент.load_verify_locations(str(self.корень))
        клиент.check_hostname = True

        гнездо = socket.socket()
        гнездо.bind(("127.0.0.1", 0))
        гнездо.listen(1)
        self.addCleanup(гнездо.close)
        порт = гнездо.getsockname()[1]

        def обслужить():
            try:
                сырое, _ = гнездо.accept()
                with сервер.wrap_socket(сырое, server_side=True) as канал:
                    канал.sendall(b"2CO")
            except OSError:                      # pragma: no cover — клиент ушёл
                pass

        поток = threading.Thread(target=обслужить, daemon=True)
        поток.start()
        self.addCleanup(поток.join, 5)
        грубое = socket.create_connection(("127.0.0.1", порт), timeout=10)
        self.addCleanup(грубое.close)
        return клиент.wrap_socket(грубое, server_hostname=имя_в_строке)

    def test_свой_корень_доверяет_своему_серверу(self):
        with self.поднять("127.0.0.1") as канал:
            self.assertEqual(b"2CO", канал.recv(3))
            self.assertTrue(канал.version().startswith("TLS"))

    def test_имя_машины_тоже_годится(self):
        with self.поднять("otdel-server") as канал:
            self.assertEqual(b"2CO", канал.recv(3))

    def test_чужое_имя_отвергается(self):
        """Сертификат, годный на любое имя, не защищает ни от чего."""
        with self.assertRaises(ssl.SSLError):
            self.поднять("чужая-машина").close()


@unittest.skipUnless(ЕСТЬ_ПАКЕТ, "нет пакета cryptography — сверять не с чем")
class СверкаЧужимРазбором(unittest.TestCase):
    """Наш DER читает посторонний, проверенный разбор — и находит всё нужное."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.кат = Path(cls._tmp.name)
        cls.cert, _ = ensure_certificate(cls.кат, hosts=["127.0.0.1", "192.168.10.5",
                                                        "otdel-server"])
        цепочка = cls.cert.read_bytes()
        куски = re.findall(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\n",
                           цепочка, re.S)
        cls.лист = x509.load_pem_x509_certificate(куски[0])
        cls.корень = x509.load_pem_x509_certificate(куски[1])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_лист_подписан_корнем(self):
        self.корень.public_key().verify(
            self.лист.signature, self.лист.tbs_certificate_bytes,
            padding.PKCS1v15(), self.лист.signature_hash_algorithm)

    def test_корень_подписан_сам_собой(self):
        self.корень.public_key().verify(
            self.корень.signature, self.корень.tbs_certificate_bytes,
            padding.PKCS1v15(), self.корень.signature_hash_algorithm)

    def test_подпись_на_sha256(self):
        self.assertIsInstance(self.лист.signature_hash_algorithm, hashes.SHA256)

    def test_корень_это_удостоверитель_а_лист_нет(self):
        self.assertTrue(self.корень.extensions.get_extension_for_class(
            x509.BasicConstraints).value.ca)
        self.assertFalse(self.лист.extensions.get_extension_for_class(
            x509.BasicConstraints).value.ca)

    def test_у_листа_есть_serverAuth(self):
        """Без него Windows не считает сертификат годным для сервера."""
        назначение = self.лист.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage).value
        self.assertIn(x509.oid.ExtendedKeyUsageOID.SERVER_AUTH, list(назначение))

    def test_у_корня_право_подписывать_сертификаты(self):
        права = self.корень.extensions.get_extension_for_class(x509.KeyUsage).value
        self.assertTrue(права.key_cert_sign)

    def test_отпечаток_ключа_связывает_лист_с_корнем(self):
        """По нему Windows строит цепочку доверия."""
        ссылка = self.лист.extensions.get_extension_for_class(
            x509.AuthorityKeyIdentifier).value
        отпечаток = self.корень.extensions.get_extension_for_class(
            x509.SubjectKeyIdentifier).value
        self.assertEqual(отпечаток.digest, ссылка.key_identifier)

    def test_адреса_и_имена_на_месте(self):
        имена = self.лист.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        адреса = {str(item) for item in имена.get_values_for_type(x509.IPAddress)}
        self.assertIn("127.0.0.1", адреса)
        self.assertIn("192.168.10.5", адреса)
        self.assertIn("otdel-server", имена.get_values_for_type(x509.DNSName))

    def test_срок_начинается_вчера(self):
        """Часы на машинах отдела расходятся: сертификат «из будущего» отвергнут."""
        начало = self.лист.not_valid_before
        конец = self.лист.not_valid_after
        self.assertLess((конец - начало).days, 366 * 6)
        self.assertGreater((конец - начало).days, 366 * 4)

    def test_наш_разбор_совпадает_с_чужим(self):
        наше = certs.read_certificate(self.cert.read_bytes())
        имена = self.лист.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        чужое = ([str(i) for i in имена.get_values_for_type(x509.IPAddress)]
                 + имена.get_values_for_type(x509.DNSName))
        self.assertEqual(sorted(чужое), sorted(наше["hosts"]))


class БезСтороннихПакетов(unittest.TestCase):
    """Главное: на машине отдела пакета cryptography нет и не будет."""

    def test_приложение_нигде_его_не_ввозит(self):
        виновные = []
        for файл in (ROOT / "src" / "reportgen").rglob("*.py"):
            текст = файл.read_text(encoding="utf-8")
            if re.search(r"^\s*(from|import)\s+cryptography", текст, re.M):
                виновные.append(str(файл.relative_to(ROOT)))
        self.assertEqual([], виновные,
                         "система снова зависит от пакета, которого в отделе нет")

    def test_выписка_работает_когда_пакета_нет_вовсе(self):
        """Отдельным процессом, которому пакет недоступен, — как в отделе."""
        with tempfile.TemporaryDirectory() as имя:
            заглушка = Path(имя) / "заглушка"
            (заглушка / "cryptography").mkdir(parents=True)
            (заглушка / "cryptography" / "__init__.py").write_text(
                "raise ImportError(\"No module named 'cryptography'\")",
                encoding="utf-8")
            кат = Path(имя) / "данные"
            среда = dict(os.environ)
            среда["PYTHONPATH"] = os.pathsep.join(
                [str(заглушка), str(ROOT / "src")])
            готово = subprocess.run(
                [sys.executable, "-m", "reportgen.web.tls",
                 "--data-dir", str(кат), "--host", "192.168.10.5", "--json"],
                capture_output=True, text=True, env=среда, timeout=300)
            self.assertEqual(0, готово.returncode, готово.stderr)
            отчёт = json.loads(готово.stdout.strip().splitlines()[-1])
            self.assertTrue(отчёт["ok"], отчёт["problem"])
            self.assertIn("192.168.10.5", отчёт["hosts"])
            # И пара годится для настоящего сервера.
            контекст = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            контекст.load_cert_chain(отчёт["cert"], отчёт["key"])


class ОтчётДляСкриптов(unittest.TestCase):
    """setup-https.ps1 читает вывод как JSON — значит, он обязан быть JSON."""

    def test_ключ_check_ничего_не_выписывает(self):
        with tempfile.TemporaryDirectory() as имя:
            готово = subprocess.run(
                [sys.executable, "-m", "reportgen.web.tls",
                 "--data-dir", имя, "--check", "--json"],
                capture_output=True, text=True,
                env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), timeout=120)
            отчёт = json.loads(готово.stdout.strip().splitlines()[-1])
            self.assertFalse(отчёт["ok"])
            self.assertEqual("сертификат не выписан", отчёт["problem"])
            self.assertFalse((Path(имя) / "tls").exists())
            self.assertEqual(1, готово.returncode,
                             "скрипт должен понять, что сертификата нет")

    def test_когда_писать_некуда_отвечает_по_человечески(self):
        """Человек должен прочитать причину, а не питоновский стек."""
        with tempfile.TemporaryDirectory() as имя:
            занято = Path(имя) / "данные"
            занято.write_text("это файл, а не каталог", encoding="utf-8")
            with self.assertRaises(CertificateError) as поймано:
                ensure_certificate(занято)
            self.assertIn("не удалось записать", str(поймано.exception))


class АдресаМашины(unittest.TestCase):
    def test_петля_всегда_на_месте(self):
        from reportgen.web.tls import local_addresses
        self.assertIn("127.0.0.1", local_addresses())

    def test_адрес_в_сети_находится(self):
        """Сертификат только на петлю браузер на рабочем месте отвергнет."""
        from reportgen.web.tls import local_addresses
        свои = [item for item in local_addresses()
                if not item.startswith("127.") and item != "::1"]
        годные = []
        for item in свои:
            try:
                ipaddress.ip_address(item)
            except ValueError:
                continue
            годные.append(item)
        self.assertTrue(годные, "адрес в сети не определился")


if __name__ == "__main__":
    unittest.main()
