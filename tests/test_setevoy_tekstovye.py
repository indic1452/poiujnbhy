"""SSH (RFC 4253, 4250), FTP (RFC 959, 2428, 4217) с каналами данных, SMTP (RFC 5321, 3207, 4616), POP3
(RFC 1939, 2595), IMAP (RFC 9051) — сообщения собираются по документам; HASSH считается здесь же по его
определению (MD5 от «kex;шифры;MAC;сжатие» своего направления)."""

import base64
import hashlib
import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import prilozh
from reportgen.setevoy.protokoly import tekstovye
from reportgen.setevoy.razbor import ДОП_УРОВНИ, разобрать_пакет


def по_tcp(данные, от=50000, к=22, src="10.0.0.1", dst="10.0.0.2", номер=1, шаблоны=None):
    return разобрать_пакет(с.eth(с.ip(с.tcp(данные, от, к, src=src, dst=dst), 6, src=src, dst=dst)), номер=номер,
                           шаблоны=шаблоны)


def стек(п):
    return [у.протокол for у in п.уровни][3:]


def поля(п, протокол):
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


def все(п, ключ):
    итог = []

    def обойти(список):
        for x in список:
            if x.ключ == ключ:
                итог.append(x)
            обойти(x.дети)
    for у in п.уровни:
        обойти(у.поля)
    return итог


# -- SSH ----------------------------------------------------------------------------------

def строка_ssh(б):
    return struct.pack(">I", len(б)) + б


def пакет_ssh(код, нагрузка=b""):
    полезное = bytes([код]) + нагрузка
    добивка = 8 - (5 + len(полезное)) % 8
    добивка += 8 if добивка < 4 else 0
    return struct.pack(">IB", 1 + len(полезное) + добивка, добивка) + полезное + bytes(добивка)


СПИСКИ = [b"curve25519-sha256,diffie-hellman-group14-sha256", b"ssh-ed25519,rsa-sha2-512",
          b"aes128-ctr,aes256-gcm@openssh.com", b"aes256-ctr", b"hmac-sha2-256", b"hmac-sha2-512",
          b"none,zlib@openssh.com", b"none", b"", b""]


def kexinit(списки=СПИСКИ):
    return пакет_ssh(20, bytes(range(16)) + b"".join(строка_ssh(x) for x in списки) + b"\x00" + bytes(4))


class ТестSSH(unittest.TestCase):
    def test_строка_версии(self):
        п = по_tcp(b"SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.1\r\n")
        self.assertEqual(стек(п), ["SSH"])
        ф = поля(п, "SSH")
        self.assertEqual(ф["ssh.protocol"].текст, "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.1")
        self.assertEqual((ф["ssh.protocol"].смещение, ф["ssh.protocol"].длина), (54, 40))
        self.assertEqual((ф["ssh.protoversion"].текст, ф["ssh.protoversion"].смещение, ф["ssh.protoversion"].длина),
                         ("2.0", 58, 3))
        self.assertEqual((ф["ssh.softwareversion"].текст, ф["ssh.softwareversion"].смещение,
                          ф["ssh.softwareversion"].длина), ("OpenSSH_8.9p1", 62, 13))
        self.assertEqual((ф["ssh.comments"].текст, ф["ssh.comments"].смещение, ф["ssh.comments"].длина),
                         ("Ubuntu-3ubuntu0.1", 76, 17))
        self.assertEqual(п.инфо, "SSH версия 2.0, OpenSSH_8.9p1")
        п = по_tcp(b"SSH-1.99-Cisco-1.25\n")
        ф = поля(п, "SSH")
        self.assertEqual((ф["ssh.protoversion"].текст, ф["ssh.softwareversion"].текст), ("1.99", "Cisco-1.25"))
        self.assertNotIn("ssh.comments", ф)

    def test_kexinit_клиента_и_сервера(self):
        п = по_tcp(b"SSH-2.0-PuTTY_0.78\r\n" + kexinit())
        ф = поля(п, "SSH")
        self.assertEqual(ф["ssh.message_code"].сырое, 20)
        self.assertEqual(ф["ssh.cookie"].текст, bytes(range(16)).hex())
        self.assertEqual(ф["ssh.kex_algorithms"].текст, СПИСКИ[0].decode())
        self.assertEqual(ф["ssh.encryption_algorithms_server_to_client"].текст, "aes256-ctr")
        self.assertEqual(ф["ssh.compression_algorithms_client_to_server"].текст, "none,zlib@openssh.com")
        строка = ";".join(x.decode() for x in (СПИСКИ[0], СПИСКИ[2], СПИСКИ[4], СПИСКИ[6]))
        self.assertEqual(ф["ssh.kex.hassh_algorithms"].текст, строка)
        отпечаток = hashlib.md5(строка.encode()).hexdigest()
        self.assertEqual(ф["ssh.kex.hassh"].текст, отпечаток)
        self.assertNotIn("ssh.kex.hasshserver", ф)
        self.assertEqual(п.инфо, f"SSH версия 2.0, PuTTY_0.78; KEXINIT (клиент, HASSH {отпечаток})")
        п = по_tcp(kexinit(), от=22, к=50000)
        ф = поля(п, "SSH")
        строка = ";".join(x.decode() for x in (СПИСКИ[0], СПИСКИ[3], СПИСКИ[5], СПИСКИ[7]))
        self.assertEqual(ф["ssh.kex.hasshserver_algorithms"].текст, строка)
        self.assertEqual(ф["ssh.kex.hasshserver"].текст, hashlib.md5(строка.encode()).hexdigest())
        self.assertNotIn("ssh.kex.hassh", ф)

    def test_направление_без_порта_22(self):
        п = по_tcp(b"SSH-2.0-x\r\n" + kexinit(), от=50000, к=2222)
        self.assertIn("ssh.kex.hassh", поля(п, "SSH"))
        п = по_tcp(b"SSH-2.0-x\r\n" + kexinit(), от=2222, к=50000)
        self.assertIn("ssh.kex.hasshserver", поля(п, "SSH"))

    def test_места_kexinit(self):
        п = по_tcp(kexinit())
        ф = поля(п, "SSH")
        self.assertEqual((ф["ssh.packet_length"].смещение, ф["ssh.padding_length"].смещение), (54, 58))
        self.assertEqual((ф["ssh.cookie"].смещение, ф["ssh.cookie"].длина), (60, 16))
        self.assertEqual((ф["ssh.kex_algorithms"].смещение, ф["ssh.kex_algorithms"].длина), (76, 4 + len(СПИСКИ[0])))
        самый_последний = 76 + sum(4 + len(x) for x in СПИСКИ)
        self.assertEqual(ф["ssh.first_kex_packet_follows"].смещение, самый_последний)
        self.assertEqual(ф["ssh.packet_length"].сырое + 4, len(kexinit()))
        self.assertEqual((len(kexinit())) % 8, 0)

    def test_оборванный_kexinit(self):
        п = по_tcp(kexinit()[:60])
        self.assertIn("SSH: KEXINIT оборван", п.ошибки)
        self.assertIn("KEXINIT (оборван)", п.инфо)

    def test_прочие_сообщения(self):
        данные = (пакет_ssh(5, строка_ssh(b"ssh-userauth")) + пакет_ssh(1, struct.pack(">I", 11) + строка_ssh(b"bye")
                                                                       + строка_ssh(b"")))
        п = по_tcp(данные)
        ф = поля(п, "SSH")
        self.assertEqual(ф["ssh.service_name"].текст, "ssh-userauth")
        self.assertEqual((ф["ssh.disconnect_reason"].текст, ф["ssh.disconnect_description"].текст),
                         ("BY_APPLICATION", "bye"))
        self.assertEqual(п.инфо, "SSH Service Request: ssh-userauth; Disconnect: BY_APPLICATION")
        ключ = строка_ssh(b"ssh-ed25519") + строка_ssh(bytes(32))
        п = по_tcp(пакет_ssh(31, строка_ssh(ключ) + строка_ssh(bytes(32)) + строка_ssh(bytes(8))), от=22, к=5000)
        ф = поля(п, "SSH")
        self.assertEqual((ф["ssh.host_key.type"].текст, ф["ssh.host_key.type"].смещение), ("ssh-ed25519", 68))
        self.assertIn("ключ ssh-ed25519", п.инфо)
        п = по_tcp(пакет_ssh(21) + b"\x8f" * 40)
        self.assertEqual(п.инфо, "SSH New Keys — дальше шифрование; зашифрованные данные")
        self.assertEqual(поля(п, "SSH")["ssh.encrypted_packet"].текст, "40 байт")

    def test_зашифрованное_на_22_и_чужое(self):
        п = по_tcp(b"\x8f\x13" * 20)
        self.assertEqual(стек(п), ["SSH"])
        self.assertEqual(п.инфо, "SSH зашифрованные данные")
        self.assertNotIn("SSH", стек(по_tcp(b"\x8f\x13" * 20, от=5000, к=6000)))
        # На другом порту — по строке версии (эвристика) или по правдоподобному пакету.
        self.assertEqual(стек(по_tcp(b"SSH-2.0-dropbear\r\n", от=5000, к=6000)), ["SSH"])
        self.assertEqual(стек(по_tcp(b"SSH-2.0-dropbear\r\n", от=5000, к=6000)), ["SSH"])
        # Строка «SSH-» без версии — просто текст (общий разбор строк), без полей версии.
        п = по_tcp(b"SSH-bad\r\n", от=5000, к=6000)
        self.assertNotIn("ssh.protocol", поля(п, "SSH"))

    def test_регистрация(self):
        self.assertIs(prilozh.ПОРТЫ_TCP[22], tekstovye.ssh)
        self.assertIs(prilozh.ПОРТЫ_TCP[21], tekstovye.ftp)
        self.assertIs(prilozh.ПОРТЫ_TCP[25], tekstovye.smtp)
        self.assertIs(prilozh.ПОРТЫ_TCP[587], tekstovye.smtp)
        self.assertIs(prilozh.ПОРТЫ_TCP[110], tekstovye.pop3)
        self.assertIs(prilozh.ПОРТЫ_TCP[143], tekstovye.imap)
        self.assertIn(tekstovye.ftp_data, prilozh.ПЕРЕД_ПОРТАМИ)
        self.assertEqual(ДОП_УРОВНИ["FTP-DATA"], "прикладной")


# -- FTP ----------------------------------------------------------------------------------

class ТестFTP(unittest.TestCase):
    def test_команды_и_ответы(self):
        п = по_tcp(b"USER anonymous\r\n", к=21)
        ф = поля(п, "FTP")
        self.assertEqual((ф["ftp.request.command"].текст, ф["ftp.request.arg"].текст), ("USER", "anonymous"))
        self.assertEqual((ф["ftp.user"].текст, ф["ftp.user"].смещение, ф["ftp.user"].длина), ("anonymous", 59, 9))
        self.assertEqual(п.инфо, "FTP USER anonymous")
        п = по_tcp(b"PASS s3cret\r\n", к=21)
        self.assertEqual(поля(п, "FTP")["ftp.password"].текст, "s3cret")
        self.assertEqual(п.инфо, "FTP PASS ***")
        п = по_tcp(b"220-Welcome\r\n220 ProFTPD ready\r\n", от=21, к=50000)
        коды = все(п, "ftp.response.code")
        self.assertEqual([(x.сырое, x.смещение) for x in коды], [(220, 54), (220, 67)])
        self.assertEqual(все(п, "ftp.response.arg")[1].текст, "ProFTPD ready")
        self.assertEqual(п.инфо, "FTP 220 Welcome; 220 ProFTPD ready")
        п = по_tcp(b"retr file.txt\r\n", к=21)
        self.assertEqual(поля(п, "FTP")["ftp.request.command"].текст, "RETR")

    def test_pasv_и_данные(self):
        общий = {}
        по_tcp(b"PASV\r\n", к=21, номер=1, шаблоны=общий)
        п = по_tcp(b"227 Entering Passive Mode (192,168,1,10,19,137).\r\n", от=21, к=50000, номер=2,
                   шаблоны=общий)
        ф = поля(п, "FTP")
        self.assertEqual((ф["ftp.data_channel"].текст, ф["ftp.data_channel"].сырое),
                         ("192.168.1.10:5001 (PASV)", "192.168.1.10|5001"))
        по_tcp(b"RETR report.pdf\r\n", к=21, номер=3, шаблоны=общий)
        п = по_tcp(b"%PDF-1.7 ...", от=5001, к=50001, src="192.168.1.10", dst="10.0.0.1", номер=4, шаблоны=общий)
        self.assertEqual(стек(п), ["FTP-DATA"])
        ф = поля(п, "FTP-DATA")
        self.assertEqual((ф["ftp-data.channel"].текст, ф["ftp-data.command"].текст),
                         ("192.168.1.10:5001", "RETR report.pdf"))
        self.assertEqual(п.инфо, "FTP-DATA RETR report.pdf, 12 байт")
        # Клиент пишет на канал (STOR после нового PASV в ту же сторону).
        п = по_tcp(b"xyz", от=50001, к=5001, src="10.0.0.1", dst="192.168.1.10", номер=5, шаблоны=общий)
        self.assertEqual(стек(п), ["FTP-DATA"])
        # Пакет раньше объявления (разбор одного пакета со всем словарём захвата) — не канал.
        п = по_tcp(b"xyz", от=50001, к=5001, src="10.0.0.1", dst="192.168.1.10", номер=2, шаблоны=общий)
        self.assertNotIn("FTP-DATA", стек(п))
        п = по_tcp(b"xyz", от=50001, к=5002, src="10.0.0.1", dst="192.168.1.10", номер=5, шаблоны=общий)
        self.assertNotIn("FTP-DATA", стек(п))

    def test_port_eprt_epsv(self):
        общий = {}
        п = по_tcp(b"PORT 10,0,0,1,4,1\r\n", к=21, номер=1, шаблоны=общий)
        self.assertEqual(поля(п, "FTP")["ftp.data_channel"].текст, "10.0.0.1:1025 (PORT)")
        по_tcp(b"LIST\r\n", к=21, номер=2, шаблоны=общий)
        п = по_tcp(b"drwxr-xr-x 2 ftp ftp 4096 pub\r\n", от=20, к=1025, src="10.0.0.2", dst="10.0.0.1", номер=3,
                   шаблоны=общий)
        self.assertEqual(п.инфо, "FTP-DATA LIST, 31 байт")
        п = по_tcp(b"EPRT |2|2001:0db8:0:0::1|6000|\r\n", к=21, номер=4, шаблоны=общий)
        self.assertEqual(поля(п, "FTP")["ftp.data_channel"].сырое, "2001:db8::1|6000")
        п = по_tcp(b"EPRT |1|10.0.0.7|6001|\r\n", к=21, номер=5, шаблоны=общий)
        self.assertEqual(поля(п, "FTP")["ftp.data_channel"].сырое, "10.0.0.7|6001")
        for плохой in (b"EPRT |1|2001:db8::1|6000|", b"EPRT |2|10.0.0.7|6000|", b"EPRT |3|10.0.0.7|6000|",
                       b"EPRT |1|10.0.0.7|x|", b"EPRT |1|zzz|6000|", b"EPRT |1|10.0.0.7", b"PORT 10,0,0,1,4",
                       b"PORT 10,0,0,300,4,1", b"EPRT"):
            п = по_tcp(плохой + b"\r\n", к=21, номер=6, шаблоны=общий)
            self.assertNotIn("ftp.data_channel", поля(п, "FTP"), плохой)
        п = по_tcp(b"229 Entering Extended Passive Mode (|||6446|)\r\n", от=21, к=50000, src="10.0.0.2",
                   dst="10.0.0.1", номер=7, шаблоны=общий)
        self.assertEqual(поля(п, "FTP")["ftp.data_channel"].сырое, "10.0.0.2|6446")
        п = по_tcp(b"229 Entering Extended Passive Mode (!!!6447!)\r\n", от=21, к=50000, src="10.0.0.2",
                   dst="10.0.0.1", номер=8, шаблоны=общий)
        self.assertEqual(поля(п, "FTP")["ftp.data_channel"].сырое, "10.0.0.2|6447")
        п = по_tcp(b"229 Entering Extended Passive Mode (|!|6448|)\r\n", от=21, к=50000, номер=9, шаблоны=общий)
        self.assertNotIn("ftp.data_channel", поля(п, "FTP"))
        п = по_tcp(b"227 Entering Passive Mode 10,0,0,9,0,21\r\n", от=21, к=50000, номер=10, шаблоны=общий)
        self.assertEqual(поля(п, "FTP")["ftp.data_channel"].сырое, "10.0.0.9|21")
        п = по_tcp(b"227 Entering Passive Mode\r\n", от=21, к=50000, номер=11, шаблоны=общий)
        self.assertNotIn("ftp.data_channel", поля(п, "FTP"))

    def test_передача_без_канала_и_auth_tls(self):
        общий = {}
        п = по_tcp(b"RETR a\r\n", к=21, шаблоны=общий)
        self.assertEqual(общий, {})
        self.assertEqual(п.инфо, "FTP RETR a")
        п = по_tcp(b"AUTH TLS\r\n", к=21)
        self.assertEqual(поля(п, "FTP")["ftp.starttls"].текст, "AUTH TLS (RFC 4217)")
        self.assertNotIn("ftp.starttls", поля(по_tcp(b"AUTH GSSAPI\r\n", к=21), "FTP"))

    def test_чужие_строки(self):
        п = по_tcp(b"hello world, this is not ftp\r\n", к=21)
        ф = поля(п, "FTP")
        self.assertNotIn("ftp.request.command", ф)
        self.assertEqual(п.инфо, "FTP hello world, this is not ftp")
        self.assertNotIn("FTP", стек(по_tcp(bytes(range(200)), к=21)))


# -- почта --------------------------------------------------------------------------------

class ТестПочты(unittest.TestCase):
    def test_smtp(self):
        план = base64.b64encode(b"\x00ivan\x00p@ss").decode()
        п = по_tcp(f"EHLO client.example\r\nMAIL FROM:<ivan@example.org> SIZE=100\r\nRCPT TO:<olga@example.net>\r\n"
                   f"AUTH PLAIN {план}\r\n".encode(), к=25)
        ф = поля(п, "SMTP")
        self.assertEqual([x.текст for x in все(п, "smtp.req.command")], ["EHLO", "MAIL FROM", "RCPT TO", "AUTH"])
        self.assertEqual(ф["smtp.req.mail_from"].текст, "ivan@example.org")
        self.assertEqual(ф["smtp.req.rcpt_to"].текст, "olga@example.net")
        self.assertEqual((ф["smtp.auth.username"].текст, ф["smtp.auth.password"].текст), ("ivan", "p@ss"))
        self.assertEqual(ф["smtp.req.parameter"].текст, "client.example")
        self.assertEqual(п.инфо, "SMTP EHLO client.example; MAIL FROM <ivan@example.org> SIZE=100; "
                                 "RCPT TO <olga@example.net>; AUTH PLAIN")
        п = по_tcp(b"STARTTLS\r\n", к=587)
        self.assertEqual(поля(п, "SMTP")["smtp.starttls"].текст, "STARTTLS (RFC 3207)")
        п = по_tcp(b"250-mx.example Hello\r\n250 STARTTLS\r\n", от=25, к=50000)
        self.assertEqual([x.сырое for x in все(п, "smtp.response.code")], [250, 250])
        п = по_tcp("From: Ivan <ivan@example.org>\r\nSubject: Отчёт\r\nX-Other: y\r\n".encode(), к=25)
        ф = поля(п, "SMTP")
        self.assertEqual((ф["imf.from"].текст, ф["imf.subject"].текст), ("Ivan <ivan@example.org>", "Отчёт"))
        self.assertEqual(п.инфо, "SMTP тема «Отчёт»")
        п = по_tcp(b"Message-ID: <1@x>\r\n", к=25)
        self.assertEqual(поля(п, "SMTP")["imf.message_id"].текст, "<1@x>")

    def test_smtp_auth_неверный(self):
        for арг in ("AUTH PLAIN !!!", "AUTH PLAIN " + base64.b64encode(b"ivan:pass").decode(), "AUTH LOGIN",
                    "AUTH PLAIN a b"):
            п = по_tcp(арг.encode() + b"\r\n", к=25)
            self.assertNotIn("smtp.auth.username", поля(п, "SMTP"), арг)

    def test_pop3(self):
        п = по_tcp(b"USER ivan\r\nPASS secret\r\nSTLS\r\n", к=110)
        ф = поля(п, "POP3")
        self.assertEqual((ф["pop.user"].текст, ф["pop.password"].текст), ("ivan", "secret"))
        self.assertEqual(ф["pop.starttls"].текст, "STLS (RFC 2595)")
        self.assertEqual(п.инфо, "POP3 USER ivan; PASS; STLS")
        п = по_tcp(b"+OK POP3 ready\r\n", от=110, к=50000)
        self.assertEqual(поля(п, "POP3")["pop.response.indicator"].текст, "+OK")
        self.assertEqual(п.инфо, "POP3 +OK POP3 ready")

    def test_imap(self):
        п = по_tcp(b'a1 LOGIN "ivan" "se cret"\r\na2 STARTTLS\r\n', к=143)
        ф = поля(п, "IMAP")
        self.assertEqual((ф["imap.request.username"].текст, ф["imap.request.password"].текст), ("ivan", "se cret"))
        self.assertEqual(ф["imap.starttls"].текст, "STARTTLS (RFC 2595)")
        self.assertEqual([x.текст for x in все(п, "imap.request_tag")], ["a1", "a2"])
        self.assertEqual(все(п, "imap.request.command")[1].смещение, 54 + 27 + 3)
        self.assertEqual(п.инфо, "IMAP a1 LOGIN; a2 STARTTLS")
        п = по_tcp(b"* OK IMAP4rev2 ready\r\na1 OK LOGIN completed\r\n", от=143, к=50000)
        self.assertEqual([x.текст for x in все(п, "imap.response.status")], ["OK", "OK"])
        п = по_tcp(b"a3 LOGIN\r\n", к=143)
        self.assertNotIn("imap.request.username", поля(п, "IMAP"))
        п = по_tcp(b"a3 LOGIN ivan\r\n", к=143)
        ф = поля(п, "IMAP")
        self.assertEqual(ф["imap.request.username"].текст, "ivan")
        self.assertNotIn("imap.request.password", ф)


def раскладка(п, протокол, ключи=None):
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = []

    def обойти(список):
        for x in список:
            if ключи is None or x.ключ in ключи:
                итог.append((x.ключ, x.смещение, x.длина))
            обойти(x.дети)
    обойти(у.поля)
    return итог


def как(данные, имя, от=40000, к=40001):
    return разобрать_пакет(с.eth(с.ip(с.tcp(данные, от, к), 6)), как={f"tcp:{к}": имя})


class ТестГраницSSH(unittest.TestCase):
    def заголовок(self, дл, добивка, код=21):
        return struct.pack(">IBB", дл, добивка, код)

    def test_правдоподобие(self):
        # Без строки версии и не на порту 22 SSH узнаётся только при выборе аналитика — по правдоподобию пакета.
        def ssh_ли(данные):
            п = как(данные, "SSH")
            return стек(п) == ["SSH"] and not п.ошибки
        self.assertTrue(ssh_ли(self.заголовок(12, 4) + bytes(10)))
        for плохой, что in ((self.заголовок(12, 3) + bytes(10), "добивка < 4"),
                            (self.заголовок(12, 12) + bytes(10), "добивка = длине"),
                            (self.заголовок(13, 4) + bytes(11), "не кратно 8"),
                            (self.заголовок(12, 4, 9) + bytes(10), "неизвестный номер"),
                            (self.заголовок(35004, 4), "больше 35000"),
                            (self.заголовок(12, 4)[:5], "короче 6 байт")):
            with self.subTest(что):
                self.assertFalse(ssh_ли(плохой))
        self.assertTrue(ssh_ли(self.заголовок(12, 11) + bytes(10)))
        self.assertTrue(ssh_ли(self.заголовок(34996, 4, 2)))
        self.assertTrue(ssh_ли(self.заголовок(12, 4)))
        # На порту 22 неправдоподобное — зашифрованные данные SSH.
        self.assertEqual(по_tcp(self.заголовок(12, 3) + bytes(10)).инфо, "SSH зашифрованные данные")

    def test_направление(self):
        for от, к, клиент in ((22, 21, False), (21, 22, True), (23, 21, True), (21, 23, False), (22, 23, False),
                              (23, 22, True), (1, 0, True), (0, 1, False), (0, 0, False), (7, 7, False)):
            with self.subTest(от=от, к=к):
                ф = поля(по_tcp(b"SSH-2.0-x\r\n" + kexinit(), от=от, к=к), "SSH")
                self.assertEqual("ssh.kex.hassh" in ф, клиент)
                self.assertEqual("ssh.kex.hasshserver" in ф, not клиент)

    def test_оборванные_строки_kexinit(self):
        полное = kexinit()
        cookie = 4 + 1 + 1 + 16
        for длина, ждём in ((cookie + 3, True), (cookie + 4, True), (cookie + 4 + len(СПИСКИ[0]) - 1, True)):
            п = по_tcp(полное[:длина])
            self.assertEqual("SSH: KEXINIT оборван" in п.ошибки, ждём, длина)
        # Строка, выходящая за нагрузку (в добивку), — тоже обрыв.
        нагрузка = bytes(range(16)) + строка_ssh(b"x") + struct.pack(">I", 30)
        пакет = struct.pack(">IB", 1 + 1 + len(нагрузка) + 12 - (5 + 1 + len(нагрузка)) % 8, 0)
        дл = 1 + 1 + len(нагрузка)
        добивка = 8 - (4 + дл) % 8 + 8
        пакет = struct.pack(">IB", дл + добивка, добивка) + b"\x14" + нагрузка + bytes(добивка)
        self.assertIn("SSH: KEXINIT оборван", по_tcp(пакет).ошибки)

    def test_флаг_первого_пакета(self):
        с_флагом = пакет_ssh(20, bytes(16) + b"".join(строка_ssh(x) for x in СПИСКИ) + b"\x01" + bytes(4))
        ф = поля(по_tcp(с_флагом), "SSH")
        self.assertEqual(ф["ssh.first_kex_packet_follows"].сырое, 1)
        без_резерва = пакет_ssh(20, bytes(16) + b"".join(строка_ssh(x) for x in СПИСКИ) + b"\x01" + bytes(3))
        ф = поля(по_tcp(без_резерва), "SSH")
        self.assertNotIn("ssh.first_kex_packet_follows", ф)

    def test_строка_и_правдоподобие(self):
        self.assertEqual(tekstovye._ssh_строка(b"\x00\x00\x00\x00", 0, 4), (b"", 4))
        self.assertEqual(tekstovye._ssh_строка(b"\x00\x00\x00\x01a", 0, 5), (b"a", 5))
        self.assertIsNone(tekstovye._ssh_строка(b"\x00\x00\x00\x02a", 0, 5))
        self.assertIsNone(tekstovye._ssh_строка(b"\x00\x00\x00", 0, 3))
        for короткое in (b"", b"\x00\x00\x00\x0c\x0a", b"\x00"):
            self.assertFalse(tekstovye._правдоподобен(короткое, 0, len(короткое)))
        self.assertTrue(tekstovye._правдоподобен(пакет_ssh(21), 0, 16))

    def test_раскладка(self):
        п = по_tcp(kexinit())
        конец_списков = 76 + sum(4 + len(x) for x in СПИСКИ)
        self.assertEqual(раскладка(п, "SSH", {"ssh.message_code", "ssh.packet_length", "ssh.padding_length",
                                              "ssh.first_kex_packet_follows", "ssh.kex.hassh_algorithms",
                                              "ssh.kex.hassh"}),
                         [("ssh.message_code", 54, len(kexinit())), ("ssh.packet_length", 54, 4),
                          ("ssh.padding_length", 58, 1), ("ssh.first_kex_packet_follows", конец_списков, 1),
                          ("ssh.kex.hassh_algorithms", конец_списков, 0), ("ssh.kex.hassh", конец_списков, 0)])
        п = по_tcp(пакет_ssh(1, struct.pack(">I", 2) + строка_ssh(b"bad mac") + строка_ssh(b"")) + b"\x8f" * 7)
        self.assertEqual(раскладка(п, "SSH", {"ssh.disconnect_reason", "ssh.disconnect_description",
                                              "ssh.encrypted_packet"}),
                         [("ssh.disconnect_reason", 60, 4), ("ssh.disconnect_description", 64, 11),
                          ("ssh.encrypted_packet", 54 + 32, 7)])
        self.assertEqual(п.уровни[-1].длина, 32 + 7)
        п = по_tcp(пакет_ssh(6, строка_ssh(b"ssh-connection")), от=22, к=5000)
        self.assertEqual(раскладка(п, "SSH", {"ssh.service_name"}), [("ssh.service_name", 60, 18)])
        self.assertEqual(п.инфо, "SSH Service Accept: ssh-connection")
        # Пакет, за которым идут ещё данные, — поле ровно на пакет (длина + 4).
        п = по_tcp(пакет_ssh(21) + b"\x8f" * 9)
        self.assertEqual(раскладка(п, "SSH", {"ssh.message_code", "ssh.encrypted_packet"}),
                         [("ssh.message_code", 54, 16), ("ssh.encrypted_packet", 70, 9)])
        # Пакет длиннее данных — поле пакета до конца данных.
        п = по_tcp(kexinit()[:40])
        self.assertEqual(раскладка(п, "SSH", {"ssh.message_code"}), [("ssh.message_code", 54, 40)])

    def test_disconnect_без_описания_и_служба_в_добивке(self):
        п = по_tcp(пакет_ssh(1, struct.pack(">I", 3)))
        ф = поля(п, "SSH")
        self.assertEqual(ф["ssh.disconnect_reason"].текст, "KEY_EXCHANGE_FAILED")
        self.assertNotIn("ssh.disconnect_description", ф)
        п = по_tcp(пакет_ssh(1, b"\x00\x00\x03"))
        self.assertNotIn("ssh.disconnect_reason", поля(п, "SSH"))
        # Строка службы выходит в добивку — не показывается.
        пакет = struct.pack(">IBB", 12, 4, 5) + struct.pack(">I", 6) + b"ab" + bytes(4)
        п = по_tcp(пакет)
        self.assertNotIn("ssh.service_name", поля(п, "SSH"))

    def test_возврат_при_выборе_аналитика(self):
        for имя, данные in (("SSH", b"SSH-2.0-x\r\n"), ("FTP", b"USER a\r\n"), ("SMTP", b"HELO a\r\n"),
                            ("POP3", b"USER a\r\n"), ("IMAP", b"a1 NOOP\r\n")):
            with self.subTest(имя):
                п = как(данные, имя)
                self.assertEqual((стек(п), п.ошибки), ([имя], []))
                п = как(bytes(range(200)), имя)
                self.assertEqual(стек(п), ["Данные"])
                self.assertTrue(п.ошибки)
        self.assertTrue(как(b"SSH-bad\r\n", "SSH").ошибки)
        общий = {"ftp-data": {"10.0.0.2|40001": [0, "RETR x"]}}
        п = разобрать_пакет(с.eth(с.ip(с.tcp(b"abc", 40000, 40001), 6)), номер=5, шаблоны=общий,
                            как={"tcp:40001": "FTP-DATA"})
        self.assertEqual((стек(п), п.ошибки), (["FTP-DATA"], []))
        п = разобрать_пакет(с.eth(с.ip(с.tcp(b"abc", 40000, 40002), 6)), номер=5, шаблоны=общий,
                            как={"tcp:40002": "FTP-DATA"})
        self.assertTrue(п.ошибки)


class ТестГраницFTPиПочты(unittest.TestCase):
    def test_раскладка_ftp(self):
        п = по_tcp(b"PASS s3cret\r\n220 ok\r\n", к=21)
        self.assertEqual(раскладка(п, "FTP", {"ftp.request.command", "ftp.request.arg", "ftp.password",
                                              "ftp.response.code", "ftp.response.arg"}),
                         [("ftp.request.command", 54, 4), ("ftp.request.arg", 59, 6), ("ftp.password", 59, 6),
                          ("ftp.response.code", 67, 3), ("ftp.response.arg", 71, 2)])

    def test_port_255_и_256(self):
        п = по_tcp(b"PORT 10,0,0,255,255,255\r\n", к=21)
        self.assertEqual(поля(п, "FTP")["ftp.data_channel"].текст, "10.0.0.255:65535 (PORT)")
        п = по_tcp(b"PORT 10,0,0,256,4,1\r\n", к=21)
        self.assertNotIn("ftp.data_channel", поля(п, "FTP"))

    def test_eprt_строго(self):
        self.assertNotIn("ftp.data_channel", поля(по_tcp(b"EPRT |1|10.0.0.7|6001\r\n", к=21), "FTP"))
        self.assertNotIn("ftp.data_channel", поля(по_tcp(b"EPRT |1|10.0.0.7|6001|x\r\n", к=21), "FTP"))
        self.assertIn("ftp.data_channel", поля(по_tcp(b"EPRT !1!10.0.0.7!6001!\r\n", к=21), "FTP"))

    def test_раскладка_ftp_data(self):
        общий = {"ftp-data": {"10.0.0.2|40001": [0, "STOR y"]}}
        п = разобрать_пакет(с.eth(с.ip(с.tcp(b"abcde", 40000, 40001), 6)), номер=5, шаблоны=общий)
        self.assertEqual(раскладка(п, "FTP-DATA"), [("ftp-data.channel", 54, 0), ("ftp-data.command", 54, 0),
                                                    ("ftp-data.data", 54, 5)])
        self.assertEqual(п.уровни[-1].длина, 5)

    def test_сводка_120(self):
        длинная = ("USER " + "u" * 200 + "\r\n").encode()
        for порт, имя in ((21, "FTP"), (110, "POP3")):
            п = по_tcp(длинная, к=порт)
            self.assertEqual(len(п.уровни[-1].итог), 120, имя)
        п = по_tcp(("HELO " + "h" * 200 + "\r\n").encode(), к=25)
        self.assertEqual(len(п.уровни[-1].итог), 120)
        п = по_tcp(("a1 SELECT " + "m" * 200 + "\r\n").encode(), к=143)
        self.assertEqual(len(п.уровни[-1].итог), 120)

    def test_smtp_раскладка_и_метки(self):
        п = по_tcp(b"250 ok\r\nMAIL FROM:<a@b>\r\nRCPT TO:<c@d>\r\nSubject: hi\r\n", к=25)
        self.assertEqual(раскладка(п, "SMTP", {"smtp.response.code", "smtp.req.command", "smtp.req.parameter",
                                               "imf.subject"}),
                         [("smtp.response.code", 54, 3), ("smtp.req.command", 62, 9), ("smtp.req.parameter", 72, 5),
                          ("smtp.req.command", 79, 7), ("smtp.req.parameter", 87, 5), ("imf.subject", 94, 11)])
        у = п.уровни[-1]
        self.assertEqual([x.имя for x in у.поля if x.ключ in ("smtp.req.mail_from", "smtp.req.rcpt_to")],
                         ["Отправитель", "Получатель"])
        self.assertEqual([x.имя for x in у.поля if x.ключ == "imf.subject"], ["Subject"])

    def test_smtp_auth_plain_без_ответа_и_с_мусором(self):
        п = по_tcp(b"AUTH PLAIN\r\n", к=25)
        self.assertEqual(стек(п), ["SMTP"])
        self.assertNotIn("smtp.auth.username", поля(п, "SMTP"))
        мусор = base64.b64encode(b"\x00ivan\x00p@ss").decode() + "!"
        self.assertNotIn("smtp.auth.username", поля(по_tcp(f"AUTH PLAIN {мусор}\r\n".encode(), к=25), "SMTP"))

    def test_pop3_раскладка(self):
        п = по_tcp(b"USER ivan\r\nPASS pw\r\n+OK done\r\n", к=110)
        self.assertEqual(раскладка(п, "POP3", {"pop.request.command", "pop.user", "pop.password",
                                               "pop.response.indicator"}),
                         [("pop.request.command", 54, 4), ("pop.user", 59, 4), ("pop.request.command", 65, 4),
                          ("pop.password", 70, 2), ("pop.response.indicator", 74, 3)])


if __name__ == "__main__":
    unittest.main()
