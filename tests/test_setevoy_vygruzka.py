"""Выходные данные захвата: API объектов и выгрузок (ZIP, опись, pcapng, поля, потоки, отчёт), дерево протоколов с
долями (против независимого расчёта), соотношения узла, настройка выдачи; функции страницы — в node."""

import csv
import hashlib
import io
import json
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import zipfile
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401
import obekty_sintez as ос
import setevoy_sintez as с
from reportgen.setevoy import obekty, statistika, vygruzka
from test_potok_sessii import функции_js
from test_web import make_app

from fastapi.testclient import TestClient


def таблица_csv(данные: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(данные.decode("utf-8-sig")), delimiter=";"))


def блоки_pcapng(данные: bytes) -> list[tuple[int, bytes]]:
    блоки, место = [], 0
    while место < len(данные):
        тип, длина = struct.unpack_from("<II", данные, место)
        self_длина = struct.unpack_from("<I", данные, место + длина - 4)[0]
        assert self_длина == длина
        блоки.append((тип, данные[место + 8:место + длина - 4]))
        место += длина
    return блоки


class ВыгрузкиЧерезСервер(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        app, repos, _ = make_app(Path(cls._tmp.name), with_library=False)
        repos.users.create("engineer", "пароль123", "Инженеров И. И.", "engineer")
        repos.users.create("other", "пароль123", "Другов Д. Д.", "engineer")
        cls.к = TestClient(app)
        cls.к.post("/api/auth/login", json={"login": "engineer", "password": "пароль123"})
        cls.кадры = ос.захват_всех_видов()
        cls.времена = [1759140000 + i * 0.25 for i in range(len(cls.кадры))]
        ответ = cls.к.post("/api/pakety", files={"file": ("все.pcap", с.pcap(cls.кадры, времена=cls.времена),
                                                            "application/octet-stream")})
        cls.ид = ответ.json()["id"]
        for _ in range(400):
            if cls.к.get(f"/api/pakety/{cls.ид}").json()["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.05)
        cls.путь = f"/api/pakety/{cls.ид}"

    @classmethod
    def tearDownClass(cls):
        cls.к.close()
        cls._tmp.cleanup()

    def get(self, адрес, **параметры):
        ответ = self.к.get(self.путь + адрес, params=параметры)
        self.assertEqual(200, ответ.status_code, ответ.text[:300])
        return ответ

    # -- объекты --

    def test_список_объектов_фильтр_вид_путь_и_права(self):
        д = self.get("/objects").json()
        self.assertEqual((16, {"HTTP": 3, "JSON": 3, "WebSocket": 1, "FTP": 1, "TFTP": 1, "почта": 1, "часть письма": 1,
                               "вложение": 1, "RTP-звук": 2, "RTP-видео": 1, "по сигнатуре": 1}),
                         (д["всего"], д["виды"]))
        self.assertEqual({"объектов": 0, "байт": 0}, д["отброшено"])
        self.assertEqual(sum(о["длина"] for о in д["items"]), д["байт"])
        self.assertEqual(["TFTP", "RTP-звук", "RTP-видео", "RTP-звук", "JSON"],
                         [о["вид"] for о in self.get("/objects", filter="udp").json()["items"]])
        self.assertEqual(["RTP-звук", "RTP-звук"], [о["вид"] for о in self.get("/objects", kind="RTP-звук").json()["items"]])
        по_пути = self.get("/objects", path="Ethernet/IPv4/UDP/RTP").json()
        self.assertEqual((3, {"RTP-звук": 2, "RTP-видео": 1}), (по_пути["всего"], по_пути["виды"]))
        self.assertEqual(400, self.к.get(self.путь + "/objects", params={"filter": "(tcp"}).status_code)
        другой = TestClient(self.к.app)
        self.assertEqual(401, другой.get(self.путь + "/objects").status_code)
        другой.post("/api/auth/login", json={"login": "other", "password": "пароль123"})
        for адрес in ("/objects", "/object/1", "/objects.zip", "/outputs", "/bundle.zip?items=pcap", "/report",
                      "/fields?path=Ethernet", "/streams.zip", "/stats-file"):
            with self.subTest(адрес):
                self.assertEqual(404, другой.get(self.путь + адрес).status_code)

    def test_один_объект_член_архива_и_заголовки(self):
        документ = self.get("/object/1")
        self.assertEqual("application/octet-stream", документ.headers["content-type"])
        self.assertEqual("attachment; filename=\"object-1.docx\"; filename*=UTF-8''%D0%BE%D1%82%D1%87%D1%91%D1%82.docx",
                         документ.headers["content-disposition"])
        self.assertEqual("nosniff", документ.headers["x-content-type-options"])
        звук = self.get("/object/12")
        self.assertEqual(("audio/wav", b"RIFF"), (звук.headers["content-type"], звук.content[:4]))
        член = self.get("/object/7", member=0)
        self.assertEqual("привет".encode() * 50, член.content)
        self.assertIn("filename=\"object-7-0.txt\"; filename*=UTF-8''a.txt", член.headers["content-disposition"])
        self.assertEqual(404, self.к.get(self.путь + "/object/7", params={"member": 9}).status_code)
        self.assertEqual(404, self.к.get(self.путь + "/object/99").status_code)
        по_номеру = self.get("/object/1", names="num")
        self.assertIn("filename*=UTF-8''%D0%BE%D0%B1%D1%8A%D0%B5%D0%BA%D1%82-0001.docx", по_номеру.headers["content-disposition"])
        без_сжатия = self.get("/object/2", decompress="0")
        self.assertEqual(b"\x1f\x8b", без_сжатия.content[:2])

    def test_zip_объектов_виды_и_путь(self):
        z = zipfile.ZipFile(io.BytesIO(self.get("/objects.zip").content))
        self.assertEqual(17, len(z.namelist()))
        self.assertEqual(16, len(таблица_csv(z.read("опись.csv"))))
        z = zipfile.ZipFile(io.BytesIO(self.get("/objects.zip", kind="FTP,TFTP").content))
        self.assertEqual(["0007-архив.zip", "0008-конфиг.cfg", "опись.csv"], z.namelist())
        z = zipfile.ZipFile(io.BytesIO(self.get("/objects.zip", path="Ethernet/IPv4/UDP/TFTP").content))
        self.assertEqual(["0008-конфиг.cfg", "опись.csv"], z.namelist())

    # -- выходные данные --

    def test_пункты_выходных_данных(self):
        д = self.get("/outputs").json()
        self.assertEqual(list(vygruzka.КЛЮЧИ), [и["ключ"] for и in д["items"]])
        сколько = {и["ключ"]: и["сколько"] for и in д["items"]}
        self.assertEqual({"pcap": 102, "pcapng": 102, "list-csv": 102, "list-json": 102, "dissect-json": 102,
                          "fields-csv": 15, "stats": 12, "objects": 13, "rtp": 3, "streams": 13, "report": 2}, сколько)
        self.assertEqual(["pcap", "list-csv", "stats", "objects", "rtp", "report"], [и["ключ"] for и in д["items"] if и["отмечен"]])

    def test_общий_zip_опись_и_настройки(self):
        self.assertEqual(400, self.к.get(self.путь + "/bundle.zip").status_code)
        self.assertEqual(400, self.к.get(self.путь + "/bundle.zip", params={"items": "pcap,лишнее"}).status_code)
        z = zipfile.ZipFile(io.BytesIO(self.get("/bundle.zip", items=",".join(vygruzka.КЛЮЧИ), tz="180").content))
        имена = z.namelist()
        for нужное in ("пакеты/все-отбор.pcap", "пакеты/все-отбор.pcapng", "пакеты/список.csv", "пакеты/список.json",
                       "пакеты/разбор.json", "поля/HTTP.csv", "поля/RTP.csv", "статистика/иерархия.csv",
                       "статистика/диалоги-tcp.json", "статистика/неизвестные.csv", "объекты/FTP/0007-архив.zip",
                       "объекты/по_сигнатуре/0016-поток13-4.jpg", "rtp/0012-rtp-10.0.0.1-20000-10.0.0.2-30000-00001234.wav",
                       "потоки/0001-TCP-10.0.0.2_80-10.0.0.1_41000.bin", "отчёт/обзор.html", "отчёт/обзор.txt",
                       "опись.csv", "опись.json"):
            with self.subTest(нужное):
                self.assertIn(нужное, имена)
        опись = таблица_csv(z.read("опись.csv"))
        self.assertEqual(list(vygruzka.ОПИСЬ), list(опись[0]))
        self.assertEqual(len(имена) - 2, len(опись))
        for строка in опись:
            данные = z.read(строка["путь"])
            self.assertEqual((строка["sha256"], int(строка["размер"])), (hashlib.sha256(данные).hexdigest(), len(данные)))
        self.assertEqual(опись, [{к: str(з) for к, з in з_.items()} for з_ in json.loads(z.read("опись.json"))])
        jpeg = next(с_ for с_ in опись if с_["путь"].endswith(".jpg"))
        self.assertEqual(("TCP 10.0.0.2:9999 → 10.0.0.1:41005", "по сигнатуре"), (jpeg["поток"], jpeg["протокол"]))
        self.assertTrue(jpeg["время"].endswith("+03:00"))
        self.assertEqual("2025-09-29T13:00:00.000000+03:00", опись[0]["время"])
        self.assertNotIn("заметки.txt", имена)
        список = json.loads(z.read("пакеты/список.json"))
        self.assertEqual((102, "2025-09-29T13:00:00.250000+03:00"), (len(список), список[1]["время_iso"]))
        разбор = json.loads(z.read("пакеты/разбор.json"))
        self.assertEqual((102, "Ethernet"), (len(разбор), разбор[0]["уровни"][0]["протокол"]))
        # Настройки: имена по номеру, JSON с отступами, сжатие не снимать, предел объёма.
        z = zipfile.ZipFile(io.BytesIO(self.get("/bundle.zip", items="objects,list-json", names="num", json="pretty",
                                                 decompress="0").content))
        self.assertIn("объекты/HTTP/объект-0002.json", z.namelist())
        self.assertEqual(b"\x1f\x8b", z.read("объекты/HTTP/объект-0002.json")[:2])
        self.assertTrue(z.read("пакеты/список.json").startswith(b"[\n  {"))
        z = zipfile.ZipFile(io.BytesIO(self.get("/bundle.zip", items="pcap,objects", maxzip="1", maxcount="2").content))
        заметки = z.read("заметки.txt").decode()
        self.assertIn("объектов отброшено по пределам: 14", заметки)
        self.assertEqual(["пакеты/все-отбор.pcap", "объекты/HTTP/0001-upload-часть-1.txt", "объекты/HTTP/0002-photo.png"],
                         [с_["путь"] for с_ in таблица_csv(z.read("опись.csv"))])

    def test_pcapng_json_и_разбор_потоком(self):
        pcapng = self.get("/export", format="pcapng").content
        блоки = блоки_pcapng(pcapng)
        self.assertEqual((0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)), блоки[0])
        self.assertEqual((1, struct.pack("<HHI", 1, 0, 262144)), блоки[1])
        пакеты = [б for т, б in блоки if т == 6]
        self.assertEqual(len(self.кадры), len(пакеты))
        интерфейс, выше, ниже, записано, длина = struct.unpack_from("<IIIII", пакеты[3])
        self.assertEqual((0, int(round(self.времена[3] * 1e6))), (интерфейс, выше << 32 | ниже))
        self.assertEqual((len(self.кадры[3]), len(self.кадры[3]), self.кадры[3]), (записано, длина, пакеты[3][20:20 + записано]))
        только_rtp = блоки_pcapng(self.get("/export", format="pcapng", path="Ethernet/IPv4/UDP/RTP").content)
        self.assertEqual(60, sum(1 for т, _ in только_rtp if т == 6))
        ответ = self.get("/export", format="pcap", path="Ethernet/IPv4/UDP/RTP")
        self.assertIn("-RTP-", urllib_unquote(ответ.headers["content-disposition"]))
        список = json.loads(self.get("/export", format="json").content)
        self.assertEqual((102, 1, "Ethernet/IPv4/TCP/HTTP"), (len(список), список[0]["номер"], список[0]["стек"]))
        разбор = json.loads(self.get("/export", format="dissect", path="Ethernet/IPv4/UDP/TFTP").content)
        self.assertEqual([23], [п["номер"] for п in разбор])
        self.assertEqual(["TFTP"], [п["уровни"][3]["протокол"] for п in json.loads(
            self.get("/export", format="dissect", filter="frame.number == 23", json="pretty").content)])

    def test_поля_протокола(self):
        строки = таблица_csv(self.get("/fields", path="Ethernet/IPv4/TCP/HTTP", format="csv").content)
        self.assertEqual(["1", "GET", "/files/%D0%BE%D1%82%D1%87%D1%91%D1%82.docx"],
                         [строки[0]["номер"], строки[0]["http.request.method"], строки[0]["http.request.uri"]])
        json_ = json.loads(self.get("/fields", path="Ethernet/IPv4/UDP/TFTP", format="json").content)
        self.assertEqual([23], [р["номер"] for р in json_])
        for параметры in ({"path": "", "format": "csv"}, {"path": "Ethernet", "format": "xml"}):
            with self.subTest(параметры):
                self.assertEqual(400, self.к.get(self.путь + "/fields", params=параметры).status_code)

    def test_сырые_потоки_и_отчёт(self):
        z = zipfile.ZipFile(io.BytesIO(self.get("/streams.zip").content))
        опись = таблица_csv(z.read("опись.csv"))
        self.assertEqual(len(z.namelist()) - 2, len(опись))
        первый = z.read("0001-TCP-10.0.0.2_80-10.0.0.1_41000.bin")
        self.assertTrue(первый.startswith(b"HTTP/1.1 200 OK"))
        z = zipfile.ZipFile(io.BytesIO(self.get("/streams.zip", path="Ethernet/IPv4/UDP/TFTP").content))
        self.assertEqual(["0005-UDP-10.0.0.1_3000-10.0.0.2_69.bin", "опись.csv", "опись.json"], z.namelist())
        страница = self.get("/report").content.decode()
        for чего_нет in ("http://", "https://", "<script", "src=", "<link"):
            self.assertNotIn(чего_нет, страница)
        for что in ("Обзор захвата «все.pcap»", "<h2>Протоколы</h2>", "архив.zip", "prefers-color-scheme: dark"):
            self.assertIn(что, страница)
        текст = self.get("/report", format="txt").content.decode()
        self.assertIn("Объекты по видам:\n  FTP: 1\n", текст)
        self.assertIn("Пакетов: 102\n", текст)

    def test_таблицы_статистики_файлом(self):
        иерархия = таблица_csv(self.get("/stats-file", kind="hierarchy").content)
        self.assertEqual(("", "Кадры", "102", "100.0"), (иерархия[0]["путь"], иерархия[0]["протокол"], иерархия[0]["пакетов"],
                                                        иерархия[0]["доля_пакетов"]))
        как_json = json.loads(self.get("/stats-file", kind="hierarchy", format="json").content)
        self.assertEqual(len(иерархия), len(как_json))
        диалоги = таблица_csv(self.get("/stats-file", kind="conversations-ip").content)
        self.assertEqual([("10.0.0.1", "10.0.0.2", "102")], [(д["а"], д["б"], д["пакетов"]) for д in диалоги])
        for kind in ("conversations-eth", "conversations-tcp", "conversations-udp", "endpoints", "time", "dns", "http",
                     "tls", "errors", "unknown"):
            with self.subTest(kind):
                self.get("/stats-file", kind=kind, format="json")
        for параметры in ({"kind": "xx"}, {"kind": "time", "format": "xml"}):
            self.assertEqual(400, self.к.get(self.путь + "/stats-file", params=параметры).status_code)

    def test_узел_протокола_и_обзор(self):
        узел = self.get("/stats", kind="protocol", path="Ethernet/IPv4/UDP/RTP").json()
        self.assertEqual({"направления": [{"а": "10.0.0.1", "б": "10.0.0.2", "пакетов_аб": 60, "байт_аб": 13419,
                                           "пакетов_ба": 0, "байт_ба": 0}],
                          "диалогов": 1, "узлов": 2, "первый": {"номер": 37, "время": self.времена[36]},
                          "последний": {"номер": 97, "время": self.времена[96]}}, узел)
        обзор = self.get("/stats", kind="overview").json()
        self.assertEqual(16, sum(обзор["объекты"].values()))
        self.assertEqual({"пакетов": 0}, self.get("/stats", kind="overview", filter="frame.number == 0").json())


def urllib_unquote(текст):
    import urllib.parse
    return urllib.parse.unquote(текст)


class НастройкиВыдачиTests(unittest.TestCase):
    def настройки(self, **п):
        from reportgen.web import api
        class Запрос:
            query_params = п
        return api._настройки_выдачи(Запрос())

    def test_значения_по_умолчанию_и_пределы(self):
        н = self.настройки()
        self.assertEqual(obekty.Настройки(), н)
        н = self.настройки(decompress="0", silence="false", audio="raw", video="nal", json="pretty", maxobj="0",
                           maxzip="5000", maxcount="7", names="flow", tz="-900")
        self.assertEqual(obekty.Настройки(снимать_сжатие=False, тишина=False, звук="сырой", видео="nal", json_отступ=True,
                                          объект_до=1 << 20, байт_до=1024 << 20, объектов_до=7, имена="поток",
                                          пояс=-14 * 60), н)
        н = self.настройки(decompress="no", maxobj="x", maxcount="99999999", names="num", tz="900", audio="wav", video="?")
        self.assertEqual((False, 64 << 20, 50000, "номер", 840, "wav", "annexb"),
                         (н.снимать_сжатие, н.объект_до, н.объектов_до, н.имена, н.пояс, н.звук, н.видео))
        self.assertEqual((1, 5), (self.настройки(maxcount="1").объектов_до, self.настройки(tz="5").пояс))


class ВыгрузкаФункцииTests(unittest.TestCase):
    def test_csv_и_json(self):
        данные = vygruzka.csv_байты([{"а": [1, 2], "б": {"x": "я"}}, {"в": 3, "а": 4}])
        self.assertEqual("﻿а;б;в\r\n1 | 2;\"{\"\"x\"\": \"\"я\"\"}\";\r\n4;;3\r\n", данные.decode("utf-8"))
        self.assertEqual(b"\xef\xbb\xbfx\r\n", vygruzka.csv_байты([{"x": 1, "y": 2}][:0], ["x"]))
        self.assertEqual('{"я": 1}'.encode(), vygruzka.json_байты({"я": 1}))
        self.assertEqual('{\n  "я": "2"\n}'.encode(), vygruzka.json_байты({"я": Path("2")}, отступ=True))
        self.assertEqual("a_b.c-d", vygruzka.безопасно("a/b.c-d"))
        self.assertEqual("x", vygruzka.безопасно("///"))

    def test_список_и_поля_по_протоколам(self):
        сводки, _, _ = ос.разобрать([ос.udp(b"\x00\x01a\x00octet\x00", 3000, 69)])
        self.assertEqual([{"номер": 1, "время": 1000.01, "время_iso": "1970-01-01T00:16:40.010000+00:00", "источник": "10.0.0.1",
                           "получатель": "10.0.0.2", "протокол": "TFTP", "длина": сводки[0]["длина"],
                           "стек": "Ethernet/IPv4/UDP/TFTP", "сведения": сводки[0]["инфо"], "ошибки": ""}],
                         vygruzka.список_пакетов(сводки))
        пакет = {"номер": 5, "время": 1.0, "уровни": [
            {"протокол": "A", "поля": [{"имя": "Поле", "ключ": "a.f", "текст": "1", "дети": [
                {"имя": "Внутри", "ключ": "", "текст": "2", "дети": []}]}, {"имя": "Без ключа", "ключ": "", "текст": "3"}]},
            {"протокол": "B", "поля": [{"имя": "x", "ключ": "b.x", "текст": "7", "дети": []},
                                       {"имя": "x", "ключ": "b.x", "текст": "8", "дети": []}]}]}
        self.assertEqual({"A": [{"номер": 5, "время": 1.0, "a.f": "1", "a.f.Внутри": "2", "Без ключа": "3"}],
                          "B": [{"номер": 5, "время": 1.0, "b.x": "7 | 8"}]}, vygruzka.поля_по_протоколам([пакет]))
        self.assertEqual({"B": [{"номер": 5, "время": 1.0, "b.x": "7 | 8"}]}, vygruzka.поля_по_протоколам([пакет], ["A", "B"]))
        self.assertEqual({}, vygruzka.поля_по_протоколам([пакет], ["A", "C"]))
        разобрано = list(vygruzka.разбор([{**пакет, "длина": 9, "протокол": "B", "собранный": {"уровни": ["у"]}}]))
        self.assertEqual({"номер": 5, "время": 1.0, "длина": 9, "канал": "", "протокол": "B", "уровни": пакет["уровни"],
                          "собранный": ["у"]}, разобрано[0])

    def test_архив_повторы_предел_заметки(self):
        а = vygruzka.Архив(предел=10, пояс=60)
        self.assertEqual("x/a.bin", а.положить("x/a.bin", b"12345", "что", поток="п", пакеты=list(range(1, 1003)),
                                               протокол="P", время=0))
        self.assertEqual("x/A-2.bin", а.положить("x/A.bin", b"1", "что"))
        self.assertEqual("x/b-2", а.положить("x/b", b"1", "") and а.положить("x/b", b"1", ""))
        self.assertIsNone(а.положить("y", b"12345", "много"))
        self.assertEqual(["y (5 байт) — сверх предела объёма 10 байт"], а.не_вошло)
        z = zipfile.ZipFile(а.закрыть(["заметка"]))
        self.assertEqual(["x/a.bin", "x/A-2.bin", "x/b", "x/b-2", "заметки.txt", "опись.csv", "опись.json"], z.namelist())
        self.assertEqual("заметка\ny (5 байт) — сверх предела объёма 10 байт\n", z.read("заметки.txt").decode())
        первая = таблица_csv(z.read("опись.csv"))[0]
        self.assertEqual(" ".join(map(str, range(1, 1001))) + " …", первая["пакеты"])
        self.assertEqual(("1970-01-01T01:00:00.000000+01:00", "5"), (первая["время"], первая["размер"]))
        self.assertEqual("", таблица_csv(z.read("опись.csv"))[1]["время"])
        z = zipfile.ZipFile(vygruzka.Архив().закрыть())
        self.assertEqual(["опись.csv", "опись.json"], z.namelist())

    def test_потоки_по_пути_и_udp_по_направлениям(self):
        кадры = [ос.udp(b"ab", 5000, 6000), ос.udp(b"cd", 6000, 5000, src="10.0.0.2", dst="10.0.0.1"), ос.udp(b"ef", 5000, 6000)]
        о = ос.Обмен()
        о.кусок("с", b"tcp-data")
        сводки, нагрузки, _ = ос.разобрать(кадры + о.пакеты)
        потоки = vygruzka.потоки(сводки, нагрузки)
        self.assertEqual([("0001-UDP-10.0.0.1_5000-10.0.0.2_6000.bin", b"abef", "UDP"),
                          ("0001-UDP-10.0.0.2_6000-10.0.0.1_5000.bin", b"cd", "UDP"),
                          ("0002-TCP-10.0.0.2_80-10.0.0.1_40000.bin", b"tcp-data", "TCP")],
                         [(и, д, т) for и, д, _, т in потоки])
        self.assertEqual(["0002-TCP-10.0.0.2_80-10.0.0.1_40000.bin"],
                         [и for и, *_ in vygruzka.потоки(сводки, нагрузки, ["Ethernet", "IPv4", "TCP"])])

    def test_отчёт_пустой_и_полный(self):
        страница, текст = vygruzka.отчёт("x.pcap", {"пакетов": 0}, [], {}, [])
        self.assertIn("Пакетов нет", страница)
        self.assertEqual("Обзор захвата «x.pcap»\n\nПод фильтр не попало ни одного пакета.", текст)
        сводки, нагрузки, _ = ос.разобрать(ос.захват_всех_видов())
        итог = obekty.собрать(сводки, нагрузки)
        обзор = statistika.обзор(сводки, [{}] * len(сводки), нагрузки)
        дерево = statistika.иерархия_строками(statistika.иерархия(сводки)[0])
        страница, текст = vygruzka.отчёт("<все>.pcap", обзор, дерево, obekty.по_видам(итог), итог["объекты"], 180)
        self.assertIn("Обзор захвата «&lt;все&gt;.pcap»", страница)
        self.assertIn("<td>\u00a0\u00a0IPv4</td>", страница)
        self.assertIn("  1. [HTTP] отчёт.docx — 401 байт, TCP 10.0.0.2:80 → 10.0.0.1:41000\n", текст)
        self.assertIn("+03:00", страница)

    def test_статистика_все_таблицы(self):
        сводки, нагрузки, _ = ос.разобрать(ос.захват_всех_видов())
        таблицы = vygruzka.статистика(сводки, [{}] * len(сводки), нагрузки)
        self.assertEqual(["иерархия", "диалоги-eth", "диалоги-ip", "диалоги-tcp", "диалоги-udp", "узлы", "время", "dns",
                          "http", "tls", "ошибки", "неизвестные"], list(таблицы))
        self.assertEqual(120, len(таблицы["время"]))
        self.assertEqual(len(сводки), sum(р["пакетов"] for р in таблицы["время"]))
        self.assertEqual(round(сводки[0]["время"], 6), таблицы["время"][0]["начало"])


class ДеревоПротоколовTests(unittest.TestCase):
    def setUp(self):
        self.сводки, _, _ = ос.разобрать(ос.захват_всех_видов())

    def независимо(self):
        """Пакеты и байты каждого пути — прямым счётом по стекам, без дерева."""
        пакетов, байт = Counter(), Counter()
        for с_ in self.сводки:
            for i in range(len(с_["стек"]) + 1):
                путь = "/".join(с_["стек"][:i])
                пакетов[путь] += 1
                байт[путь] += с_["длина"]
        return пакетов, байт

    def test_доли_против_независимого_расчёта(self):
        дерево = statistika.иерархия(self.сводки)[0]
        строки = statistika.иерархия_строками(дерево)
        пакетов, байт = self.независимо()
        длительность = max(с_["время"] for с_ in self.сводки) - min(с_["время"] for с_ in self.сводки)
        self.assertEqual(round(длительность, 6), дерево["длительность"])
        self.assertEqual(set(пакетов), {р["путь"] for р in строки})
        for р in строки:
            родитель = р["путь"].rpartition("/")[0] if "/" in р["путь"] else ""
            with self.subTest(р["путь"]):
                self.assertEqual((пакетов[р["путь"]], байт[р["путь"]]), (р["пакетов"], р["байт"]))
                self.assertAlmostEqual(100 * пакетов[р["путь"]] / пакетов[""], р["доля_пакетов"], places=3)
                self.assertAlmostEqual(100 * байт[р["путь"]] / байт[""], р["доля_байт"], places=3)
                self.assertEqual(round(8 * байт[р["путь"]] / длительность), р["бит_с"])
                if р["путь"]:
                    self.assertAlmostEqual(100 * пакетов[р["путь"]] / пакетов[родитель], р["от_родителя_пакетов"], places=3)
                    self.assertAlmostEqual(100 * байт[р["путь"]] / байт[родитель], р["от_родителя_байт"], places=3)
                дети = [п for п in пакетов if п.rpartition("/")[0] == р["путь"] and п and п != р["путь"] and
                        п.count("/") == (р["путь"].count("/") + 1 if р["путь"] else 0)]
                self.assertEqual(пакетов[р["путь"]] - sum(пакетов[д] for д in дети), р["кончаются"])
                self.assertEqual(байт[р["путь"]] - sum(байт[д] for д in дети), р["кончаются_байт"])
        корень = строки[0]
        self.assertEqual((0, 100.0, 100.0), (корень["глубина"], корень["от_родителя_пакетов"], корень["от_родителя_байт"]))

    def test_нулевая_длительность_и_пусто(self):
        одна = statistika.иерархия(self.сводки[:1])[0]
        self.assertEqual((0.0, 0), (одна["длительность"], statistika.иерархия_строками(одна)[0]["бит_с"]))
        пусто = statistika.иерархия([])[0]
        self.assertEqual((0.0, 0.0), (пусто["длительность"], statistika.иерархия_строками(пусто)[0]["доля_пакетов"]))

    def test_узел_протокола(self):
        узел = statistika.узел_протокола(self.сводки, ["Ethernet", "IPv4", "TCP"], направлений=1)
        self.assertEqual((1, 2, 1, 1), (узел["диалогов"], узел["узлов"], len(узел["направления"]), узел["первый"]["номер"]))
        tcp = [с_ for с_ in self.сводки if "TCP" in с_["стек"]]
        н = узел["направления"][0]
        self.assertEqual((sum(1 for с_ in tcp if с_["источник"] == "10.0.0.1"), sum(с_["длина"] for с_ in tcp if с_["источник"] == "10.0.0.2")),
                         (н["пакетов_аб"], н["байт_ба"]))
        self.assertEqual(max((с_["время"], с_["номер"]) for с_ in tcp)[1], узел["последний"]["номер"])
        self.assertEqual({"направления": [], "диалогов": 0, "узлов": 0, "первый": None, "последний": None},
                         statistika.узел_протокола(self.сводки, ["Нет"]))
        без_адресов = [{"номер": 1, "время": 5.0, "стек": ["X"], "источник": "", "получатель": "", "длина": 1},
                       {"номер": 2, "время": 4.0, "стек": ["X"], "источник": "a", "получатель": "b", "длина": 1},
                       {"номер": 3, "время": 5.0, "стек": ["X"], "источник": "b", "получатель": "a", "длина": 2},
                       {"номер": 4, "время": 1.0, "стек": ["X"], "источник": "c", "получатель": "a", "длина": 9}]
        узел = statistika.узел_протокола(без_адресов, ["X"])
        self.assertEqual(({"номер": 4, "время": 1.0}, {"номер": 3, "время": 5.0}, 3, 2),
                         (узел["первый"], узел["последний"], узел["узлов"], узел["диалогов"]))
        self.assertEqual({"а": "a", "б": "c", "пакетов_аб": 0, "байт_аб": 0, "пакетов_ба": 1, "байт_ба": 9}, узел["направления"][0])
        self.assertEqual({"а": "a", "б": "b", "пакетов_аб": 1, "байт_аб": 1, "пакетов_ба": 1, "байт_ба": 2}, узел["направления"][1])


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ФункцииСтраницыTests(unittest.TestCase):
    """Функции страницы «Пакеты» берутся прямо из app.js и выполняются в node."""

    def выполнить(self, код):
        текст = функции_js(["прочитатьНастройкиВыдачи", "поясМинут", "параметрыВыдачи", "метрикиУзла", "сравнитьУзлы",
                            "заметенУзел", "сегментыУзла", "пакетыКратко"], ["НАСТРОЙКИ_ВЫДАЧИ", "СТОЛБЦЫ_ДЕРЕВА"])
        ответ = subprocess.run(["node", "-e", текст + "\nconsole.log(JSON.stringify((() => {" + код + "})()));"],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual("", ответ.stderr)
        return json.loads(ответ.stdout)

    def test_настройки_пояс_параметры(self):
        итог = self.выполнить("""
            const н = прочитатьНастройкиВыдачи({сжатие: false, тишина: 1, звук: 'raw', видео: 'nal', json: 'pretty',
                объектМб: 100, архивМб: 0.4, объектов: 7.6, порог: -3, имена: 'flow', пояс: '+05:30', отмечено: ['pcap', 5]});
            const по = прочитатьНастройкиВыдачи(null);
            const странные = прочитатьНастройкиВыдачи({звук: 'mp3', видео: 'x', json: 1, имена: 'x', пояс: '5', объектМб: '9',
                порог: 12.5, архивМб: Infinity});
            return [н, по, странные, поясМинут('UTC', -180), поясМинут('-01:30', 0), поясМинут('браузер', -180),
                поясМинут('браузер', 0), параметрыВыдачи(по, 0), параметрыВыдачи(н, 0), параметрыВыдачи(по, 60),
                параметрыВыдачи(Object.assign({}, по, {объектМб: 64, архивМб: 128, объектов: 5000}), -60)];
        """)
        н, по, странные, *пояса, п_по, п_н, п_по_пояс, п_пределы = итог
        self.assertEqual({"сжатие": False, "тишина": True, "звук": "raw", "видео": "nal", "json": "pretty", "объектМб": 64,
                          "архивМб": 1, "объектов": 8, "имена": "flow", "пояс": "+05:30", "порог": 0, "отмечено": ["pcap"]}, н)
        self.assertEqual({"сжатие": True, "тишина": True, "звук": "wav", "видео": "annexb", "json": "compact", "объектМб": 64,
                          "архивМб": 128, "объектов": 5000, "имена": "proto", "пояс": "браузер", "порог": 0, "отмечено": None}, по)
        self.assertEqual(("wav", "annexb", "compact", "proto", "браузер", 64, 12.5, 128),
                         (странные["звук"], странные["видео"], странные["json"], странные["имена"], странные["пояс"],
                          странные["объектМб"], странные["порог"], странные["архивМб"]))
        self.assertEqual([0, -90, 180, 0], пояса)
        self.assertEqual("", п_по)
        self.assertEqual("&decompress=0&audio=raw&video=nal&json=pretty&maxzip=1&maxcount=8&names=flow&tz=330", п_н)
        self.assertEqual("&tz=-60", п_по_пояс)
        self.assertEqual("&tz=60", п_пределы)
        self.assertEqual("&silence=0&maxobj=3", self.выполнить(
            "return параметрыВыдачи(Object.assign({}, НАСТРОЙКИ_ВЫДАЧИ, {тишина: false, объектМб: 3}), 0);"))

    def test_метрики_узла_как_на_сервере(self):
        сводки, _, _ = ос.разобрать(ос.захват_всех_видов())
        дерево = statistika.иерархия(сводки)[0]
        строки = statistika.иерархия_строками(дерево)
        метрики = self.выполнить("""
            const корень = """ + json.dumps(дерево, ensure_ascii=False) + """;
            const итог = [];
            (function обойти(у, родитель) {
                итог.push(метрикиУзла(у, родитель, корень, корень.длительность));
                у.дети.forEach((д) => обойти(д, у));
            })(корень, null);
            return итог;
        """)
        for р, м in zip(строки, метрики, strict=True):
            with self.subTest(р["путь"]):
                self.assertEqual((р["пакетов"], р["байт"], р["бит_с"], р["кончаются"], р["кончаются_байт"]),
                                 (м["пакетов"], м["байт"], м["битС"], м["кончаются"], м["кончаютсяБайт"]))
                for к_сервер, к_страница in (("доля_пакетов", "доляПакетов"), ("доля_байт", "доляБайт"),
                                             ("от_родителя_пакетов", "отРодителяПакетов"), ("от_родителя_байт", "отРодителяБайт")):
                    self.assertAlmostEqual(р[к_сервер], м[к_страница], places=3)

    def test_метрики_без_длительности_и_нулей(self):
        м = self.выполнить("return [метрикиУзла({пакетов: 2, байт: 10, кончаются: 1}, {пакетов: 0, байт: 0}, {пакетов: 0, байт: 0}, 0),"
                           " метрикиУзла({пакетов: 1, байт: 3}, null, {пакетов: 4, байт: 6}, 2)];")
        self.assertEqual({"доляПакетов": 0, "пакетов": 2, "доляБайт": 0, "байт": 10, "битС": 0, "отРодителяПакетов": 0,
                          "отРодителяБайт": 0, "кончаются": 1, "кончаютсяБайт": 0}, м[0])
        self.assertEqual({"доляПакетов": 25, "пакетов": 1, "доляБайт": 50, "байт": 3, "битС": 12, "отРодителяПакетов": 100,
                          "отРодителяБайт": 100, "кончаются": 0, "кончаютсяБайт": 0}, м[1])

    def test_порядок_порог_сегменты_пакеты(self):
        итог = self.выполнить("""
            const у = (п, м) => ({протокол: п, м: м});
            const а = у('Б', {пакетов: 5}), б = у('А', {пакетов: 5}), в = у('В', {пакетов: 9});
            return [
                [в, а, б].sort((x, y) => сравнитьУзлы(x, y, 'пакетов', true)).map((x) => x.протокол),
                [в, а, б].sort((x, y) => сравнитьУзлы(x, y, 'пакетов', false)).map((x) => x.протокол),
                [заметенУзел({доляПакетов: 0.5, доляБайт: 0.4}, 0), заметенУзел({доляПакетов: 0.5, доляБайт: 0.4}, 0.5),
                 заметенУзел({доляПакетов: 0.4, доляБайт: 0.5}, 0.5), заметенУзел({доляПакетов: 0.4, доляБайт: 0.4}, 0.5)],
                сегментыУзла({пакетов: 10, байт: 100, кончаются: 2, кончаются_байт: 0, дети: [
                    {протокол: 'X', уровень: 'прикладной', пакетов: 8, байт: 100}]}, 'пакетов'),
                сегментыУзла({пакетов: 10, байт: 100, кончаются: 2, кончаются_байт: 0, дети: [
                    {протокол: 'X', уровень: 'прикладной', пакетов: 8, байт: 100}]}, 'байт'),
                сегментыУзла({пакетов: 0, байт: 0, кончаются: 0, кончаются_байт: 3, дети: [{протокол: 'Y', уровень: 'все', пакетов: 0, байт: 0}]}, 'байт'),
                [пакетыКратко({пакетов: 0}), пакетыКратко({пакетов: 1, пакеты: [7]}), пакетыКратко({пакетов: 3, пакеты: [9, 4, 6]}),
                 пакетыКратко({пакетов: 60, пакеты: [5, 5]}), пакетыКратко({пакетов: 2})],
            ];
        """)
        self.assertEqual(["В", "А", "Б"], итог[0])
        self.assertEqual(["А", "Б", "В"], итог[1])
        self.assertEqual([True, True, True, False], итог[2])
        self.assertEqual([{"протокол": "X", "уровень": "прикладной", "доля": 80},
                          {"протокол": "кончаются здесь", "уровень": "все", "доля": 20}], итог[3])
        self.assertEqual([{"протокол": "X", "уровень": "прикладной", "доля": 100}], итог[4])
        self.assertEqual([{"протокол": "Y", "уровень": "все", "доля": 0}, {"протокол": "кончаются здесь", "уровень": "все", "доля": 0}], итог[5])
        self.assertEqual(["—", "7", "4–9 (3)", "5–5 (60)", "Infinity–-Infinity (2)"], итог[6])


if __name__ == "__main__":
    unittest.main()
