"""Свой сертификат для работы по https в изолированной сети.

Уведомление на рабочем столе браузер показывает только «защищённой»
странице: https или адрес самой машины. Отдел работает по адресу в сети, и
по http такого окна у браузера нет вовсе — человека вызывают в кабинет, а он
не знает. Купить сертификат в изолированном контуре не у кого, поэтому
система выписывает его себе сама.
"""

import ipaddress
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.web.tls import (
    CERT_NAME,
    KEY_NAME,
    YEARS,
    ensure_certificate,
    local_addresses,
)

try:
    from cryptography import x509
    HAS_CRYPTO = True
except ImportError:                              # pragma: no cover
    HAS_CRYPTO = False


class AddressTests(unittest.TestCase):
    def test_the_loopback_is_always_there(self):
        found = local_addresses()
        self.assertIn("127.0.0.1", found)

    def test_the_network_address_is_found(self):
        """Разбор имени машины его часто не даёт: в сети отдела нет DNS.

        Сертификат, выписанный только на петлевой адрес, браузер отвергнет:
        он сверяет адрес в строке с тем, что написано в сертификате.
        """
        found = [item for item in local_addresses()
                 if not item.startswith("127.") and item != "::1"]
        self.assertTrue(found, "адрес в сети не определился")


@unittest.skipUnless(HAS_CRYPTO, "нет пакета cryptography")
class CertificateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)

    def issue(self, **kwargs):
        return ensure_certificate(self.folder, **kwargs)

    def read(self, path: Path):
        return x509.load_pem_x509_certificate(path.read_bytes())

    def test_a_certificate_and_a_key_appear(self):
        cert, key = self.issue()
        self.assertTrue(cert.is_file() and key.is_file())
        self.assertEqual(CERT_NAME, cert.name)
        self.assertEqual(KEY_NAME, key.name)

    def test_it_is_not_reissued_on_every_start(self):
        """Иначе браузер ругался бы каждый день заново."""
        cert, _ = self.issue()
        first = cert.read_bytes()
        again, _ = self.issue()
        self.assertEqual(first, again.read_bytes())

    def test_the_network_addresses_are_written_into_it(self):
        cert, _ = self.issue()
        names = self.read(cert).extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        addresses = {str(item) for item in names.get_values_for_type(x509.IPAddress)}
        self.assertIn("127.0.0.1", addresses)

    def test_an_extra_address_can_be_added(self):
        """У машины бывает второй адрес или псевдоним в hosts."""
        cert, _ = self.issue(extra_hosts=["192.168.10.5", "otdel-server"])
        names = self.read(cert).extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        addresses = {str(item) for item in names.get_values_for_type(x509.IPAddress)}
        hosts = set(names.get_values_for_type(x509.DNSName))
        self.assertIn("192.168.10.5", addresses)
        self.assertIn("otdel-server", hosts)

    def test_localhost_is_there_too(self):
        cert, _ = self.issue()
        names = self.read(cert).extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        self.assertIn("localhost", set(names.get_values_for_type(x509.DNSName)))

    def test_it_lives_long_enough_to_be_forgotten(self):
        """В изолированном контуре некому следить за сроками.

        Долго живёт КОРЕНЬ — тот единственный файл, который разносят по
        рабочим местам: обходить их из-за срока никто не должен. Серверный
        живёт меньше, но следить и за ним не надо: система перевыписывает его
        сама, заранее (см. RENEW_BEFORE_DAYS).
        """
        from reportgen.web.tls import ROOT_NAME, SERVER_YEARS

        cert, _ = self.issue()
        корень = self.read(cert.parent / ROOT_NAME)
        after = getattr(корень, 'not_valid_after_utc', None) or корень.not_valid_after
        before = getattr(корень, 'not_valid_before_utc', None) or корень.not_valid_before
        self.assertGreaterEqual(round((after - before).days / 365), YEARS)

        серверный = self.read(cert)
        after = getattr(серверный, 'not_valid_after_utc', None) or серверный.not_valid_after
        before = getattr(серверный, 'not_valid_before_utc', None) or серверный.not_valid_before
        self.assertGreaterEqual(round((after - before).days / 365), SERVER_YEARS)

    def test_the_key_is_not_left_open_to_everyone(self):
        import os
        import stat

        _, key = self.issue()
        if os.name == "nt":                      # pragma: no cover — Windows
            self.skipTest("права на файл на Windows наследуются от каталога")
        mode = stat.S_IMODE(key.stat().st_mode)
        self.assertEqual(0, mode & 0o077, f"ключ открыт: {oct(mode)}")


if __name__ == "__main__":
    unittest.main()
