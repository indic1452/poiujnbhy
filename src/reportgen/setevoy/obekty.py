"""Объекты захвата: всё, что передано, — в файлы (как «Экспорт объектов» Wireshark, только шире).

Объект — файл, тело HTTP, часть формы, письмо и его вложения, звук и видео RTP, сообщение
WebSocket, значение JSON, находка по сигнатуре. У каждого: вид, имя (из Content-Disposition, адреса,
команды FTP, имени TFTP, вложения; иначе составное — поток и место), тип (MIME или кодек), длина,
поток, номера пакетов, заметки (сжатие снято, обрыв, пропуски) и сами байты. Что внутри на самом деле,
определяется по содержимому (магия и структура, ``poisk.опознать``), а не по имени и типу; у архивов
ZIP и tar (в том числе tar в gzip/bzip2/xz) — опись членов, любой член можно взять отдельно.

Документы (структуры — строго по ним):

* Сборка TCP — по номерам последовательности (RFC 793, 3.3) с переходом через 2³²: куски по порядку
  номеров, повторы и перекрытия отброшены, пропуск до 1 МиБ — нулями (и в заметках).
* HTTP/1.x — RFC 9112: 2.2 (одиночный LF как конец строки), 3 (строка запроса), 4 (строка состояния),
  5 (поля; 5.2 — сложенные строки), 6.3 (длина тела: ответ на HEAD и 1xx/204/304 — без тела; 2xx на CONNECT —
  туннель; Transfer-Encoding главнее Content-Length; chunked последним — по кускам, иначе у ответа — до
  закрытия; неверный Content-Length — разбор прекращается; запрос без длины — без тела; ответ — до
  закрытия), 7.1 (chunked: размер шестнадцатеричный, расширения после «;», трейлеры). RFC 9110: 8.4.1
  (Content-Encoding: gzip — RFC 1952, deflate — поток zlib RFC 1950, бывает и «голый» RFC 1951; br
  стандартной библиотекой не снять), 15.2.2 (101 — смена протокола). RFC 6266, 4.3 (имя файла: filename и
  filename* по RFC 5987). multipart/form-data — RFC 7578 (прежде RFC 2388, 3: Content-Disposition: form-data;
  name; filename), границы — RFC 2046, 5.1.1.
* WebSocket — RFC 6455: 4.2.2 (ответ 101, Upgrade), 5.2 (кадр: FIN, RSV1–3, код, MASK, длина 7/7+16/7+64
  бит, ключ маски), 5.3 (маска — XOR по 4 байта ключа), 5.4 (продолжения, код 0), 5.5 (управляющие 8–10),
  5.6 (текст 1, двоичные 2); сжатие permessage-deflate — RFC 7692, 7.2.2 (как Wireshark packet-websocket.c:
  к сообщению дописать 00 00 FF FF и распаковать «голым» DEFLATE; окно общее, если не «no_context_takeover»).
* FTP — RFC 959, 4.1.3 (RETR, STOR, STOU, APPE, LIST, NLST, REST), 4.2 (ответы), PORT и ответ 227
  (h1,h2,h3,h4,p1,p2: порт — p1·256 + p2); RFC 2428 — EPRT |в|адрес|порт| и ответ 229 (|||порт|); MLSD —
  RFC 3659, 7. Канал данных — соединение TCP на объявленный адрес и порт после объявления.
* TFTP — RFC 1350, 5 (RRQ 1 и WRQ 2: имя\\0режим\\0; DATA 3: блок 16 бит и до 512 байт, короткий блок —
  последний; ACK 4; ERROR 5: код и текст); RFC 2347 (OACK 6) и RFC 2348 (blksize).
* Почта — SMTP RFC 5321: 4.1.1.4 (DATA, конец — строка из одной точки), 4.5.2 (прозрачность: точка в
  начале строки снимается); BDAT — RFC 3030, 2 (BDAT размер [LAST]); STARTTLS — RFC 3207. POP3 — RFC 1939, 3
  (многострочный ответ до строки «.», точка в начале строки снимается), RETR; STLS — RFC 2595. IMAP —
  RFC 3501: 4.3 (литерал {n} CRLF и n октетов), 7.4.2 (FETCH: BODY[раздел]<начало>, RFC822, RFC822.TEXT),
  BINARY[…] — RFC 3516, литерал ~{n}. Письмо — .eml; части MIME — модулем email (RFC 2045: 6.7
  quoted-printable, 6.8 base64; имена вложений — RFC 2231 и RFC 2047).
* RTP — RFC 3550: 5.1 (V=2, P, X, CC, M, PT, номер, отметка времени, SSRC), 5.3.1 (расширение: профиль и
  длина в 32-битных словах), A.1 (расширенный номер: переход через 65535). Поток — по адресам, портам и
  SSRC. Кодек — статический тип (RFC 3551, табл. 4 и 5) или a=rtpmap из SDP (как media.py).
  PCMU/PCMA (RFC 3551, 4.5.14: G.711, 8 бит на отсчёт, 8000 Гц) → WAV: RIFF/WAVE, fmt с кодом 0x0007 (μ-law)
  или 0x0006 (A-law) — RFC 2361, прил. A.7–A.8; у не-PCM — cbSize = 0 и блок fact с числом отсчётов, нечётный
  блок data — с добивочным байтом (как libsndfile wav.c); место пакета — по отметке времени, пропуск —
  тишиной: код нулевого отсчёта G.711 (Sun g711.c в Wireshark wsutil: linear2ulaw(0) = 0xFF,
  linear2alaw(0) = 0xD5). G.722 (RFC 3551, 4.5.2: октеты подряд) → .g722. AMR и AMR-WB — RFC 4867: 4.4
  (октетно выровненный: CMR и 4 бита резерва, ToC — F, FT, Q, P, P; речь — с октета), 4.3 (с экономией полосы:
  CMR 4 бита, ToC по 6 бит, речь подряд), 5.1 и 5.3 (файл «#!AMR\\n» или «#!AMR-WB\\n»; кадр — октет
  P|FT|Q|P|P и речь; потерянные — NO_DATA, FT 15). H.264 — RFC 6184, 5.6–5.8 (одиночный NAL 1–23, STAP-A 24,
  STAP-B 25, FU-A 28, FU-B 29) → Annex B ITU-T H.264 (перед каждым NAL — 00 00 00 01). H.265 — RFC 7798, 4.4
  (одиночный, AP — 48, FU — 49: S, E, тип 6 бит; заголовок NAL из заголовка нагрузки и типа FU — как
  FFmpeg rtpdec_hevc.c), без DONL. MP2T (RFC 3551, табл. 5, тип 33; RFC 2250, 2) — пакеты TS подряд → .ts.
  События — RFC 4733, 2.3 (событие, E — конец, длительность; одно нажатие — одна отметка времени;
  коды 0–15 — DTMF 0–9, *, #, A–D; 16 — flash).
* JSON — модуль json (RFC 8259): тела, сообщения WebSocket, нагрузки UDP и не разобранные потоки TCP,
  целиком состоящие из значений-объектов или массивов.
* Прочее — находки по сигнатуре (``poisk.сигнатуры_в_байтах``) в том, что протоколы не разобрали.
"""

from __future__ import annotations

import bisect
import bz2
import csv
import email
import email.errors
import email.policy
import io
import json
import lzma
import re
import struct
import tarfile
import zipfile
import zlib
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from typing import Any
from urllib.parse import unquote, urlsplit

from ..potok import poisk

Сводка = dict[str, Any]

#: Пределы на захват: объектов, байт во всех объектах, байт в одном объекте (и в распаковке одного тела),
#: байт собираемых потоков. Что не вошло, считается и называется в ответе («отброшено»).
ОБЪЕКТОВ_ДО = 5000
БАЙТ_ДО = 128 << 20
ОБЪЕКТ_ДО = poisk.ФАЙЛ_ДО
СБОРКА_ДО = 256 << 20
#: Пропуск в потоке TCP до этого размера заполняется нулями (места в теле не сбиваются), больше — нет.
ПРОПУСК_ДО = 1 << 20
#: Сколько строк описи архива показывать и сколько номеров пакетов отдавать в списке.
СОСТАВ_ДО = 1000
ПАКЕТОВ_В_СПИСКЕ = 50
#: Раздел заголовков HTTP длиннее — не заголовки.
ЗАГОЛОВКИ_ДО = 64 << 10
#: Самая длинная вставляемая тишина — минута при 8000 Гц.
ТИШИНА_ДО = 60 * 8000


@dataclass
class Объект:
    """Объект захвата: вид, имя, тип, поток, пакеты, заметки и байты."""
    вид: str
    имя: str
    тип: str
    данные: bytes
    поток: str
    пакеты: list[int]
    заметки: list[str] = field(default_factory=list)
    #: Что это по содержимому (магия): «JPEG», «ZIP (docx)», «JSON», «текст», «двоичные данные».
    содержимое: str = ""
    #: Расширение по содержимому (пусто — не узнано).
    расширение: str = ""
    #: Опись архива: [{"имя", "длина", "каталог"}] или None.
    состав: list[dict[str, Any]] | None = None
    номер: int = 0

    def в_словарь(self) -> dict[str, Any]:
        return {"номер": self.номер, "вид": self.вид, "имя": self.имя, "тип": self.тип, "длина": len(self.данные),
                "поток": self.поток, "пакеты": self.пакеты[:ПАКЕТОВ_В_СПИСКЕ], "пакетов": len(self.пакеты),
                "первый": self.пакеты[0] if self.пакеты else 0, "заметки": self.заметки,
                "содержимое": self.содержимое, "состав": self.состав}


class Сбор:
    """Объекты под пределами: что не вошло, только считается; объект больше предела — обрезается."""

    def __init__(self, объектов_до: int, байт_до: int, объект_до: int = ОБЪЕКТ_ДО):
        self.объектов_до, self.байт_до, self.объект_до = объектов_до, байт_до, объект_до
        self.объекты: list[Объект] = []
        self.байт = 0
        self.отброшено = {"объектов": 0, "байт": 0}

    def добавить(self, о: Объект) -> None:
        if len(о.данные) > self.объект_до:
            о.заметки.append(f"обрезан по пределу объекта: {self.объект_до} из {len(о.данные)} байт")
            о.данные = о.данные[:self.объект_до]
        if len(self.объекты) >= self.объектов_до or self.байт + len(о.данные) > self.байт_до:
            self.отброшено["объектов"] += 1
            self.отброшено["байт"] += len(о.данные)
            return
        self.объекты.append(о)
        self.байт += len(о.данные)


# == потоки ==========================================================================================

@dataclass
class Сторона:
    """Одно направление соединения: собранные байты, откуда какой кусок, пропуски и разобранное."""
    транспорт: str
    от: str
    к: str
    данные: bytearray = field(default_factory=bytearray)
    #: (смещение начала куска, номер пакета) — по возрастанию смещений.
    карта: list[tuple[int, int]] = field(default_factory=list)
    #: (смещение, байт, заполнен нулями).
    пропуски: list[tuple[int, int, bool]] = field(default_factory=list)
    #: Промежутки [начало, конец), разобранные протоколами: сигнатуры в них не ищутся.
    покрыто: list[tuple[int, int]] = field(default_factory=list)
    #: Смещения начал кусков (как в карте) — для поиска пакета по месту.
    начала: list[int] = field(default_factory=list)

    @property
    def поток(self) -> str:
        return f"{self.транспорт} {self.от} → {self.к}"

    def добавить(self, данные: bytes, номер: int) -> None:
        self.карта.append((len(self.данные), номер))
        self.начала.append(len(self.данные))
        self.данные += данные

    def пакеты(self, начало: int, конец: int) -> list[int]:
        """Номера пакетов, чьи куски попали в [начало, конец), по порядку и без повторов."""
        i = max(0, bisect.bisect_right(self.начала, начало) - 1)
        итог: list[int] = []
        было: set[int] = set()
        while i < len(self.карта) and (self.карта[i][0] < конец or not итог):
            if self.карта[i][1] not in было:
                было.add(self.карта[i][1])
                итог.append(self.карта[i][1])
            i += 1
        return итог

    def заметки(self, начало: int, конец: int) -> list[str]:
        """Пропуски потока в [начало, конец): байт и чем закрыты."""
        return [f"пропуск {байт} байт в потоке (с {место}): "
                + ("заполнен нулями" if заполнен else "не заполнен — дальше данные сдвинуты")
                for место, байт, заполнен in self.пропуски if начало <= место < конец]

    def занять(self, начало: int, конец: int) -> None:
        self.покрыто.append((начало, конец))

    def свободно(self, место: int) -> bool:
        return not any(н <= место < к for н, к in self.покрыто)


@dataclass
class Соединение:
    """Соединение TCP или обмен UDP между двумя конечными точками."""
    транспорт: str
    номер: int                                   # по порядку первого пакета (как tcp.stream/udp.stream)
    первый: int                                  # номер первого пакета с данными
    #: Конечные точки (адрес, порт): отправитель первого пакета, затем получатель.
    точки: tuple[tuple[str, int], tuple[str, int]]
    стороны: dict[tuple[str, int], Сторона] = field(default_factory=dict)
    протоколы: set[str] = field(default_factory=set)
    #: TCP до сборки: отправитель → [(seq, данные, номер пакета)].
    сегменты: dict[tuple[str, int], list[tuple[int | None, bytes, int]]] = field(default_factory=dict)
    #: UDP: (отправитель, данные, номер пакета, стек протоколов).
    датаграммы: list[tuple[tuple[str, int], bytes, int, tuple[str, ...]]] = field(default_factory=list)
    разобрано: bool = False


def _точка(адрес: str, порт: int) -> str:
    return f"{адрес}:{порт}"


def соединения(сводки: Sequence[Сводка], нагрузки: Sequence[bytes | None]) -> tuple[list[Соединение], list[str]]:
    """Соединения TCP (собранные по номерам) и обмены UDP (датаграммами) — по порядку первого пакета."""
    по_ключу: dict[tuple, Соединение] = {}
    for с, нагрузка in zip(сводки, нагрузки, strict=False):
        if not нагрузка or с.get("порт_от") is None:
            continue
        стек = с["стек"]
        транспорт = "TCP" if "TCP" in стек else "UDP" if "UDP" in стек else ""
        if not транспорт:
            continue
        отправитель, получатель = (с["источник"], с["порт_от"]), (с["получатель"], с["порт_к"])
        ключ = (транспорт,) + tuple(sorted((отправитель, получатель)))
        соед = по_ключу.get(ключ)
        if соед is None:
            соед = по_ключу[ключ] = Соединение(транспорт, len(по_ключу) + 1, с["номер"], (отправитель, получатель))
        if отправитель not in соед.стороны:
            соед.стороны[отправитель] = Сторона(транспорт, _точка(*отправитель), _точка(*получатель))
        соед.протоколы.update(стек)
        if транспорт == "TCP":
            соед.сегменты.setdefault(отправитель, []).append((с.get("seq"), bytes(нагрузка), с["номер"]))
        else:
            соед.датаграммы.append((отправитель, bytes(нагрузка), с["номер"], tuple(стек)))
    заметки: list[str] = []
    осталось = [СБОРКА_ДО]
    for соед in по_ключу.values():
        for отправитель, сегменты in соед.сегменты.items():
            _собрать_tcp(соед.стороны[отправитель], сегменты, осталось)
    if осталось[0] <= 0:
        заметки.append(f"сборка потоков TCP остановлена на пределе {СБОРКА_ДО >> 20} МБ")
    return list(по_ключу.values()), заметки


def _дописать(сторона: Сторона, данные: bytes, номер: int, осталось: list[int]) -> bool:
    if len(данные) > осталось[0]:
        осталось[0] = 0
        return False
    осталось[0] -= len(данные)
    сторона.добавить(данные, номер)
    return True


def _собрать_tcp(сторона: Сторона, сегменты: list[tuple[int | None, bytes, int]], осталось: list[int]) -> None:
    """Куски по номерам последовательности (относительно первого, со знаком — переход через 2³²);
    повторы и перекрытия — прочь, пропуски — нулями (до ПРОПУСК_ДО) и в заметки."""
    if any(seq is None for seq, _, _ in сегменты):          # сводки без номеров — по порядку захвата
        for _, данные, номер in сегменты:
            if not _дописать(сторона, данные, номер, осталось):
                return
        return
    опора = сегменты[0][0]
    куски = sorted(((((seq - опора + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)), i, д, н)
                   for i, (seq, д, н) in enumerate(сегменты))
    сдвиг = куски[0][0]
    for отн, _, данные, номер in куски:
        место, длина = отн - сдвиг, len(сторона.данные)
        if место > длина:
            пропуск = место - длина
            заполнить = пропуск <= ПРОПУСК_ДО
            сторона.пропуски.append((длина, пропуск, заполнить))
            if заполнить:
                сторона.данные += bytes(пропуск)
            else:
                сдвиг += пропуск
        elif место + len(данные) <= длина:
            continue                                        # повтор
        else:
            данные = данные[длина - место:]                 # перекрытие: только новое
        if not _дописать(сторона, данные, номер, осталось):
            return


def _пакет(сторона: Сторона, место: int) -> int:
    """Номер пакета, в котором лежит байт ``место`` стороны."""
    return сторона.карта[max(0, bisect.bisect_right(сторона.начала, место) - 1)][1]


def _строки(сторона: Сторона) -> list[tuple[int, int, int, bytes]]:
    """(номер пакета, начало, конец с переводом строки, строка без CR LF) — все строки стороны."""
    итог, место, д = [], 0, bytes(сторона.данные)
    while место < len(д):
        конец = д.find(b"\n", место)
        конец = len(д) if конец < 0 else конец + 1
        итог.append((_пакет(сторона, место), место, конец, д[место:конец].rstrip(b"\r\n")))
        место = конец
    return итог


# == имена, типы, содержимое =============================================================================

#: Расширение по типу тела, где у mimetypes стандартной библиотеки другое или нет (application/xml → .xsl).
РАСШИРЕНИЯ_ТИПОВ = {"application/xml": "xml", "text/javascript": "js", "application/x-www-form-urlencoded": "txt",
                    "audio/wav": "wav", "application/gzip": "gz"}
#: Тип по расширению, узнанному по содержимому, — строки «!:mime» libmagic (Magdir/archive, compress,
#: animation, flash, matroska, mach, filesystems, rtf, riff, ssl, msooxml); прочее — mimetypes.
ТИПЫ_СОДЕРЖИМОГО = {
    "rar": "application/vnd.rar", "7z": "application/x-7z-compressed", "zst": "application/zstd",
    "ts": "video/MP2T", "flv": "video/x-flv", "mkv": "video/x-matroska", "webm": "video/webm",
    "macho": "application/x-mach-binary", "iso": "application/x-iso9660-image", "rtf": "text/rtf",
    "gz": "application/gzip", "bz2": "application/x-bzip2", "xz": "application/x-xz", "webp": "image/webp",
    "flac": "audio/flac", "pem": "application/x-pem-file", "crt": "application/x-pem-file",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text", "wav": "audio/wav",
}
#: Архивы, у которых показывается опись: ZIP и его производные (DOCX … EPUB), tar, сжатые gzip/bzip2/xz.
АРХИВЫ_ZIP = frozenset({"zip", "docx", "xlsx", "pptx", "vsdx", "odt", "ods", "odp", "odg", "epub", "jar", "apk"})
СЖАТЫЕ = {"gz": lambda: zlib.decompressobj(16 + zlib.MAX_WBITS), "bz2": bz2.BZ2Decompressor,
          "xz": lzma.LZMADecompressor}
#: Имена одного вида у разных расширений (для сверки имени с содержимым).
СИНОНИМЫ = {"jpeg": "jpg", "tiff": "tif", "htm": "html", "mpeg": "mpg"}


def _мим():
    import mimetypes  # noqa: PLC0415
    return mimetypes.MimeTypes()                    # только встроенная таблица, без файлов системы


def расширение_типа(тип: str) -> str:
    """Расширение по MIME-типу тела: сначала своя таблица, затем mimetypes; пусто — не знаем."""
    тип = тип.split(";")[0].strip().lower()
    if тип in РАСШИРЕНИЯ_ТИПОВ:
        return РАСШИРЕНИЯ_ТИПОВ[тип]
    return (_мим().guess_extension(тип) or ".")[1:] if тип else ""


def тип_расширения(расширение: str) -> str:
    return ТИПЫ_СОДЕРЖИМОГО.get(расширение) or _мим().guess_type("x." + расширение)[0] or "application/octet-stream"


def имя_файла(текст: str, предел: int = 150) -> str:
    """Имя для показа и выгрузки: без пути, управляющих знаков и точек по краям."""
    текст = re.sub(r"[\x00-\x1f\x7f]", "", текст).replace("\\", "/").rsplit("/", 1)[-1]
    return текст.strip().strip(".").strip()[:предел]


def с_расширением(имя: str, расширение: str) -> str:
    if not расширение or re.search(r"\.[0-9A-Za-z]{1,8}$", имя):
        return имя
    return f"{имя}.{расширение}"


def _раскодировать_2047(текст: str) -> str:
    """Имя с «=?кодировка?B|Q?…?=» (RFC 2047) — в текст; без таких слов — как есть."""
    if "=?" not in текст:
        return текст
    try:
        return str(make_header(decode_header(текст)))
    except (ValueError, LookupError, email.errors.MessageError):
        return текст


def имя_из_disposition(значение: str) -> str:
    """filename* (RFC 6266, 4.3; RFC 5987) или filename из Content-Disposition."""
    сообщение = email.message.EmailMessage()
    try:
        сообщение["Content-Disposition"] = значение
        имя = сообщение.get_filename() or ""
    except (ValueError, IndexError, TypeError):
        return ""
    return имя_файла(_раскодировать_2047(имя))


def имя_из_адреса(адрес: str) -> str:
    """Последняя часть пути адреса (с раскрытыми %XX, UTF-8); «/» в конце — index."""
    путь = urlsplit(адрес).path
    if путь.endswith("/"):
        return "index"
    return имя_файла(unquote(путь.rsplit("/", 1)[-1], errors="replace"))


def json_значения(данные: bytes, одно: bool = False) -> list[tuple[int, int]] | None:
    """Места [начало, конец) значений JSON (объектов или массивов), из которых целиком состоят данные;
    None — не JSON. ``одно`` — только одно значение (тело, сообщение)."""
    try:
        текст = данные.decode("utf-8")
    except UnicodeDecodeError:
        return None
    декодер, итог, место, байт = json.JSONDecoder(), [], 0, 0
    while True:
        пробелы = len(текст[место:]) - len(текст[место:].lstrip())
        байт += len(текст[место:место + пробелы].encode())
        место += пробелы
        if место >= len(текст):
            break
        if текст[место] not in "{[":
            return None
        try:
            _, конец = декодер.raw_decode(текст, место)
        except json.JSONDecodeError:
            return None
        длина = len(текст[место:конец].encode())
        итог.append((байт, байт + длина))
        байт += длина
        место = конец
    return итог if итог and (len(итог) == 1 or not одно) else None


def _похоже_на_текст(данные: bytes) -> bool:
    try:
        текст = данные[:1 << 16].decode("utf-8")
    except UnicodeDecodeError:
        return False
    печатных = sum(1 for з in текст if з.isprintable() or з in "\r\n\t")
    return печатных >= 0.95 * len(текст)


def содержимое(данные: bytes) -> tuple[str, str]:
    """(что внутри, расширение) — по магии и структуре (poisk.опознать), затем JSON, HTML, текст."""
    найдено = poisk.опознать(данные)
    if найдено is not None:
        что, расширение = str(найдено["что"]), str(найдено["расширение"])
        return (что if расширение == next(ф.расширение for ф in poisk.ФОРМАТЫ if ф.имя == что)
                else f"{что} ({расширение})"), расширение
    if not данные:
        return "пусто", ""
    if json_значения(данные, одно=True):
        return "JSON", "json"
    if not _похоже_на_текст(данные):
        return "двоичные данные", ""
    if re.match(rb"\s*<(?:!doctype html|html)[\s>]", данные[:1024], re.I):
        return "HTML", "html"
    return "текст", "txt"


def _распаковать(данные: bytes, распаковщик) -> tuple[bytes, str | None]:
    """Распаковка до ОБЪЕКТ_ДО: (итог, беда или None)."""
    try:
        итог = распаковщик.decompress(данные, ОБЪЕКТ_ДО)
    except (zlib.error, OSError, EOFError, ValueError, lzma.LZMAError) as ошибка:
        return данные, f"не распаковалось ({ошибка})"
    if len(итог) >= ОБЪЕКТ_ДО:
        return итог, f"распаковка остановлена на пределе {ОБЪЕКТ_ДО >> 20} МБ"
    if not распаковщик.eof:
        return итог, "сжатые данные оборваны — распаковано сколько есть"
    return итог, None


def снять_кодирование(тело: bytes, кодировки: Sequence[str]) -> tuple[bytes, list[str]]:
    """Content-Encoding / кодировки передачи — в обратном порядке (RFC 9110, 8.4.1): gzip и x-gzip,
    deflate (поток zlib, иначе «голый» DEFLATE); прочие (br, compress) — «не снято»."""
    заметки: list[str] = []
    for кодировка in reversed([к.strip().lower() for к in кодировки if к.strip()]):
        if кодировка == "identity":
            continue
        if кодировка in ("gzip", "x-gzip"):
            итог, беда = _распаковать(тело, zlib.decompressobj(16 + zlib.MAX_WBITS))
        elif кодировка == "deflate":
            итог, беда = _распаковать(тело, zlib.decompressobj(zlib.MAX_WBITS))
            if беда and беда.startswith("не распаковалось"):
                итог, беда = _распаковать(тело, zlib.decompressobj(-zlib.MAX_WBITS))
        else:
            заметки.append(f"сжатие {кодировка} не снято"
                           + (" (brotli нет в стандартной библиотеке)" if кодировка == "br" else ""))
            break
        if беда and беда.startswith("не распаковалось"):
            заметки.append(f"сжатие {кодировка}: {беда}")
            break
        тело = итог
        заметки.append(f"сжатие {кодировка} снято" + (f"; {беда}" if беда else ""))
    return тело, заметки


# == HTTP/1.x ==============================================================================================

#: Строка запроса (RFC 9112, 3: метод — token RFC 9110, 5.6.2) и строка состояния (4).
ЗАПРОС = re.compile(rb"([!#$%&'*+.^_`|~0-9A-Za-z-]+) ([^ \r\n]+) HTTP/\d\.\d\r?\n")
ОТВЕТ = re.compile(rb"HTTP/\d\.\d (\d{3})(?: [^\r\n]*)?\r?\n")
КОНЕЦ_ЗАГОЛОВКОВ = re.compile(rb"\r?\n\r?\n")
#: chunked (RFC 9112, 7.1): размер, расширения после «;», CRLF; трейлеры до пустой строки.
КУСОК = re.compile(rb"([0-9A-Fa-f]{1,16})[ \t]*(?:;[^\r\n]*)?\r?\n")
ТРЕЙЛЕРЫ = re.compile(rb"(?:[^\r\n]+\r?\n)*\r?\n")


@dataclass
class Сообщение:
    начало: int
    конец: int
    поля: dict[str, list[str]]
    тело: bytes
    заметки: list[str]
    метод: str = ""
    адрес: str = ""
    код: int = 0
    кодировки: list[str] = field(default_factory=list)       # кодировки передачи, кроме chunked

    def поле(self, имя: str) -> str:
        return ", ".join(self.поля.get(имя, []))


def _значение(б: bytes) -> str:
    try:
        return б.decode("utf-8")
    except UnicodeDecodeError:
        return б.decode("latin-1")


def поля_http(д: bytes) -> dict[str, list[str]]:
    """Поля заголовка (RFC 9112, 5): имя в нижнем регистре → значения; сложенная строка — через пробел (5.2)."""
    поля: dict[str, list[str]] = {}
    последнее = None
    for строка in д.split(b"\n"):
        строка = строка.rstrip(b"\r")
        if строка[:1] in (b" ", b"\t"):
            if последнее is not None:
                поля[последнее][-1] += " " + _значение(строка.strip())
            continue
        имя, двоеточие, значение = строка.partition(b":")
        if not двоеточие:
            continue
        последнее = _значение(имя.strip()).lower()
        поля.setdefault(последнее, []).append(_значение(значение.strip()))
    return поля


def _chunked(д: bytes, м: int) -> tuple[bytes, int, list[str]]:
    части, место = [], м
    while True:
        строка = КУСОК.match(д, место)
        if строка is None:
            return b"".join(части), len(д), ["chunked: обрыв — нет размера куска"]
        размер, место = int(строка.group(1), 16), строка.end()
        if not размер:
            трейлеры = ТРЕЙЛЕРЫ.match(д, место)
            return b"".join(части), трейлеры.end() if трейлеры else len(д), [] if трейлеры else [
                "chunked: обрыв в трейлерах"]
        части.append(д[место:место + размер])
        if место + размер > len(д):
            return b"".join(части), len(д), [f"chunked: обрыв — кусок {размер} байт получен не весь"]
        место += размер
        место += 2 if д.startswith(b"\r\n", место) else 1 if д.startswith(b"\n", место) else 0


def _тело(д: bytes, м: int, поля: dict[str, list[str]], *, запрос: bool, без_тела: bool
          ) -> tuple[bytes, int | None, list[str], list[str]]:
    """(тело, конец сообщения или None — разбор дальше невозможен, заметки, кодировки передачи) — RFC 9112, 6.3."""
    if без_тела:
        return b"", м, [], []
    передача = [к.strip().lower() for к in ", ".join(поля.get("transfer-encoding", [])).split(",") if к.strip()]
    if передача:
        if передача[-1] == "chunked":
            тело, конец, заметки = _chunked(д, м)
            return тело, конец, заметки, передача[:-1]
        if запрос:
            return b"", None, ["Transfer-Encoding без chunked в запросе — длину не определить (RFC 9112, 6.3)"], []
        return д[м:], len(д), ["тело до закрытия соединения"], передача
    длины = {з.strip() for з in ", ".join(поля.get("content-length", [])).split(",") if з.strip()}
    if длины:
        if len(длины) != 1 or not next(iter(длины)).isdigit():
            return b"", None, [f"неверный Content-Length ({', '.join(sorted(длины))}) — разбор остановлен"], []
        длина = int(next(iter(длины)))
        тело = д[м:м + длина]
        заметки = [f"обрыв: получено {len(тело)} из {длина} байт"] if len(тело) < длина else []
        return тело, м + длина, заметки, []
    if запрос:
        return b"", м, [], []
    return д[м:], len(д), ["тело до закрытия соединения"] if len(д) > м else [], []


def сообщения_http(д: bytes, *, запрос: bool, методы: Sequence[str] = ()) -> tuple[list[Сообщение], int]:
    """Запросы или ответы подряд и где разбор кончился. У ответов ``методы`` — методы запросов по порядку:
    ответ на HEAD, 1xx, 204 и 304 — без тела; 101 и 2xx на CONNECT — дальше не HTTP. Запрос с Upgrade
    или CONNECT — последний: за ним другой протокол."""
    итог: list[Сообщение] = []
    место, i = 0, 0
    while место < len(д):
        while д.startswith(b"\n", место) or д.startswith(b"\r\n", место):        # RFC 9112, 2.2
            место += 1 if д[место] == 0x0A else 2
        стартовая = (ЗАПРОС if запрос else ОТВЕТ).match(д, место)
        граница = КОНЕЦ_ЗАГОЛОВКОВ.search(д, место, место + ЗАГОЛОВКИ_ДО) if стартовая else None
        if граница is None:
            break
        поля = поля_http(д[стартовая.end():граница.end()])
        if запрос:
            метод, адрес, код = _значение(стартовая.group(1)), _значение(стартовая.group(2)), 0
            без_тела = False
        else:
            код = int(стартовая.group(1))
            метод, адрес = (методы[i] if i < len(методы) else ""), ""
            без_тела = метод == "HEAD" or 100 <= код < 200 or код in (204, 304)
        тело, конец, заметки, кодировки = _тело(д, граница.end(), поля, запрос=запрос, без_тела=без_тела)
        итог.append(Сообщение(место, граница.end() if конец is None else конец, поля, тело, заметки,
                              метод, адрес, код, кодировки))
        if конец is None:
            break
        место = конец
        if запрос and (метод == "CONNECT" or "upgrade" in поля):
            break
        if not запрос:
            if код == 101 or метод == "CONNECT" and 200 <= код < 300:
                break
            if not 100 <= код < 200:
                i += 1
    return итог, место


# == WebSocket (RFC 6455) ==================================================================================

def _снять_маску(данные: bytes, ключ: bytes) -> bytes:
    """RFC 6455, 5.3: октет i XOR октет (i mod 4) ключа."""
    if not данные:
        return данные
    полный = (ключ * (len(данные) // 4 + 1))[:len(данные)]
    return (int.from_bytes(данные, "big") ^ int.from_bytes(полный, "big")).to_bytes(len(данные), "big")


def кадры_websocket(д: bytes, м: int, *, сжатие: bool = False, общее_окно: bool = True
                    ) -> tuple[list[tuple[int, int, int, bytes, list[str]]], int]:
    """Сообщения [(начало, конец, код 1|2, данные, заметки)] из кадров с ``м`` и где разбор кончился."""
    сообщения: list[tuple[int, int, int, bytes, list[str]]] = []
    текущее: list[Any] | None = None
    окно = zlib.decompressobj(-zlib.MAX_WBITS)
    место = м
    while место + 2 <= len(д):
        б0, б1 = д[место], д[место + 1]
        код, длина, заголовок = б0 & 0x0F, б1 & 0x7F, 2
        if длина == 126:
            длина, заголовок = struct.unpack_from(">H", д, место + 2)[0] if место + 4 <= len(д) else 1 << 62, 4
        elif длина == 127:
            длина, заголовок = struct.unpack_from(">Q", д, место + 2)[0] if место + 10 <= len(д) else 1 << 62, 10
        ключ = д[место + заголовок:место + заголовок + 4] if б1 & 0x80 else b""
        заголовок += 4 if б1 & 0x80 else 0
        if б0 & 0x30 or код not in (0, 1, 2, 8, 9, 10) or место + заголовок + длина > len(д):
            break                                           # RSV2/RSV3 без расширения, чужой код, обрыв
        нагрузка = _снять_маску(bytes(д[место + заголовок:место + заголовок + длина]), bytes(ключ))
        if код in (1, 2):
            текущее = [место, код, bool(б0 & 0x40), [нагрузка]]
        elif код == 0 and текущее is not None:
            текущее[3].append(нагрузка)
        место += заголовок + длина
        if б0 & 0x80 and код in (0, 1, 2) and текущее is not None:
            данные, заметки = b"".join(текущее[3]), []
            if текущее[2] and сжатие:                       # RFC 7692, 7.2.2
                распаковщик = окно if общее_окно else zlib.decompressobj(-zlib.MAX_WBITS)
                try:
                    данные = распаковщик.decompress(данные + b"\x00\x00\xff\xff", ОБЪЕКТ_ДО)
                    заметки.append("сжатие permessage-deflate снято")
                except zlib.error as ошибка:
                    заметки.append(f"permessage-deflate: не распаковалось ({ошибка})")
            if len(текущее[3]) > 1:
                заметки.append(f"собрано из {len(текущее[3])} кадров")
            сообщения.append((текущее[0], место, текущее[1], данные, заметки))
            текущее = None
    return сообщения, место


# == настройки выдачи =======================================================================================

@dataclass
class Настройки:
    """Как выдавать объекты. По умолчанию — сжатие снято, пропуски RTP — тишиной, звук G.711 — WAV,
    видео — Annex B, JSON — как передан, имена — из протокола, время — UTC."""
    снимать_сжатие: bool = True
    тишина: bool = True
    звук: str = "wav"                 # «wav» или «сырой» (G.711 без заголовка: .ul/.al, как FFmpeg pcm_mulaw/alaw)
    видео: str = "annexb"             # «annexb» (00 00 00 01 перед NAL) или «nal» (длина 4 байта + NAL)
    json_отступ: bool = False         # JSON-объекты и выгрузки — с отступами (объект перезаписывается)
    объект_до: int = ОБЪЕКТ_ДО
    байт_до: int = БАЙТ_ДО
    объектов_до: int = ОБЪЕКТОВ_ДО
    имена: str = "протокол"           # «протокол», «поток» (поток и время) или «номер»
    пояс: int = 0                     # смещение часового пояса, минут (имена и опись)

    def ключ(self) -> tuple:
        """То, от чего зависят сами объекты (имена и пояс — только выгрузка)."""
        return (self.снимать_сжатие, self.тишина, self.звук, self.видео, self.json_отступ, self.объект_до,
                self.байт_до, self.объектов_до)


# == FTP (RFC 959, RFC 2428) ===================================================================================

ПЕРЕДАЧИ_FTP = frozenset({"RETR", "STOR", "STOU", "APPE", "LIST", "NLST", "MLSD"})
ШЕСТЬ_ЧИСЕЛ = re.compile(r"(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3})")


@dataclass
class Передача:
    команда: str
    аргумент: str
    номер: int                        # пакет команды
    адрес: str | None = None          # канал данных: адрес и порт, как объявлены
    порт: int | None = None
    как: str = ""                     # PORT, PASV, EPRT, EPSV
    объявлен: int = 0                 # пакет объявления канала
    докачка: int = 0
    имя: str = ""
    управление: str = ""


def _роли(соед: Соединение, сервер_первым: re.Pattern[bytes]) -> tuple[Сторона, Сторона]:
    """(клиент, сервер): сервер — чьи данные начинаются приветствием или ответом; иначе — у кого порт меньше.
    Молчавшая сторона — пустая."""
    стороны = {т: соед.стороны.get(т) or Сторона(соед.транспорт, _точка(*т), _точка(*д))
               for т, д in (соед.точки, соед.точки[::-1])}
    (точка_а, а), (точка_б, б) = стороны.items()
    for клиент, сервер in ((а, б), (б, а)):
        if сервер_первым.match(bytes(сервер.данные[:16])) and not сервер_первым.match(bytes(клиент.данные[:16])):
            return клиент, сервер
    return (а, б) if точка_а[1] > точка_б[1] else (б, а)


def ftp_управление(соед: Соединение) -> list[Передача]:
    """Команды и ответы канала управления по порядку пакетов: объявления каналов данных и передачи."""
    клиент, сервер = _роли(соед, re.compile(rb"\d{3}[ -]"))
    события = sorted([(н, 0, м, "к", с) for н, м, _, с in _строки(клиент)]
                     + [(н, 1, м, "с", с) for н, м, _, с in _строки(сервер)])
    передачи: list[Передача] = []
    канал: tuple[str | None, int, str, int] | None = None
    докачка = 0
    for номер, _, _, кто, строка in события:
        текст = _значение(строка).strip()
        if кто == "к":
            команда, _, аргумент = текст.partition(" ")
            команда = команда.upper()
            if команда == "PORT" and (ч := ШЕСТЬ_ЧИСЕЛ.search(аргумент)):
                канал = (".".join(ч.group(1, 2, 3, 4)), int(ч.group(5)) * 256 + int(ч.group(6)), "PORT", номер)
            elif команда == "EPRT" and len(аргумент) > 1:
                части = аргумент.split(аргумент[0])
                if len(части) >= 4 and части[3].isdigit():
                    канал = (части[2], int(части[3]), "EPRT", номер)
            elif команда == "REST" and аргумент.strip().isdigit():
                докачка = int(аргумент.strip())
            elif команда in ПЕРЕДАЧИ_FTP:
                адрес, порт, как, объявлен = канал if канал else (None, None, "", 0)
                передачи.append(Передача(команда, аргумент.strip(), номер, адрес, порт, как, объявлен, докачка,
                                         управление=сервер.поток))
                докачка = 0
        elif текст.startswith("227") and (ч := ШЕСТЬ_ЧИСЕЛ.search(текст)):
            канал = (".".join(ч.group(1, 2, 3, 4)), int(ч.group(5)) * 256 + int(ч.group(6)), "PASV", номер)
        elif текст.startswith("229") and (ч := re.search(r"\((.)\1\1(\d+)\1\)", текст)):
            канал = (сервер.от.rsplit(":", 1)[0], int(ч.group(2)), "EPSV", номер)
        elif текст[:3] in ("125", "150") and передачи and передачи[-1].команда == "STOU" and (
                ч := re.search(r"FILE:\s*(\S+)", текст)):
            передачи[-1].имя = ч.group(1)
    return передачи


def _ftp_данные(передачи: list[Передача], tcp: list[Соединение], сервер_клиент: dict[str, tuple[str, str]],
                сбор: Сбор) -> None:
    """Каналы данных к передачам: соединение на объявленный порт (и адрес — объявленный или сторон управления)
    с первым пакетом после объявления; каждое — не больше одного раза."""
    for п in передачи:
        if п.порт is None:
            continue
        адреса = {п.адрес, *сервер_клиент.get(п.управление, ())}
        for соед in tcp:
            if соед.разобрано or соед.первый < п.объявлен or not any(
                    т[1] == п.порт and т[0] in адреса for т in соед.точки):
                continue
            соед.разобрано = True
            сторона = max(соед.стороны.values(), key=lambda с: len(с.данные))
            for с in соед.стороны.values():
                с.занять(0, len(с.данные))
            if п.команда in ("LIST", "NLST", "MLSD"):
                имя = f"список-каталога-{соед.номер}.txt"
            else:
                имя = имя_файла(п.имя or п.аргумент) or f"ftp-{п.команда.lower()}-{соед.номер}"
            заметки = [f"{п.команда} {п.аргумент}".strip() + f"; канал {п.как} {п.адрес or ''}:{п.порт}"]
            if п.докачка:
                заметки.append(f"докачка с байта {п.докачка} (REST) — начала файла в захвате нет")
            заметки += сторона.заметки(0, len(сторона.данные))
            сбор.добавить(Объект("FTP", имя, "", bytes(сторона.данные), сторона.поток,
                                 сторона.пакеты(0, len(сторона.данные)), заметки))
            break


# == почта: SMTP, POP3, IMAP ======================================================================================

def без_точек(данные: bytes) -> bytes:
    """Прозрачность (RFC 5321, 4.5.2; RFC 1939, 3): точка в начале строки снимается."""
    return re.sub(rb"(?m)^\.", b"", данные)


def письмо(данные: bytes, поток: str, пакеты: list[int], заметки: list[str], сбор: Сбор, основа: str) -> None:
    """Письмо — .eml (вид «почта»), его части MIME — «вложение» (с именем) или «часть письма»."""
    try:
        сообщение = email.message_from_bytes(данные, policy=email.policy.default)
        тема = имя_файла(_раскодировать_2047(str(сообщение.get("subject", "") or "")))
    except (ValueError, IndexError, TypeError, LookupError):
        сбор.добавить(Объект("почта", f"{основа}.eml", "message/rfc822", данные, поток, пакеты,
                             заметки + ["заголовки письма не разобрались"]))
        return
    сбор.добавить(Объект("почта", с_расширением(тема or основа, "eml"), "message/rfc822", данные, поток, пакеты,
                         list(заметки)))
    if not сообщение.is_multipart():
        return
    for i, часть in enumerate((ч for ч in сообщение.walk() if not ч.is_multipart()), start=1):
        try:
            имя = имя_файла(_раскодировать_2047(часть.get_filename() or ""))
            тело = часть.get_payload(decode=True) or b""
            тип = часть.get_content_type()
            распоряжение = часть.get_content_disposition()
        except (ValueError, IndexError, TypeError, LookupError):
            continue
        кодирование = str(часть.get("content-transfer-encoding", "") or "").strip().lower()
        з = [f"часть {i} письма «{тема or основа}»"] + ([f"{кодирование} снято"]
                                                     if кодирование in ("base64", "quoted-printable") else [])
        if имя or распоряжение == "attachment":
            сбор.добавить(Объект("вложение", имя or f"{основа}-вложение-{i}", тип, тело, поток, пакеты, з))
        else:
            сбор.добавить(Объект("часть письма", f"{основа}-часть-{i}", тип, тело, поток, пакеты, з))


def smtp(соед: Соединение, сбор: Сбор) -> None:
    """DATA (до строки «.») и BDAT (RFC 3030) в потоке клиента; после STARTTLS — шифр, разбор кончается."""
    клиент = _роли(соед, re.compile(rb"\d{3}[ -]"))[0]
    д, место, n = bytes(клиент.данные), 0, 0
    куски: list[bytes] = []
    начало_bdat = 0
    while место < len(д):
        конец_строки = д.find(b"\n", место)
        if конец_строки < 0:
            break
        строка, после = д[место:конец_строки].strip(), конец_строки + 1
        слово = строка.split(b" ", 1)[0].upper()
        if слово == b"DATA" and строка.upper() == b"DATA":
            конец = д.find(b"\r\n.\r\n", после - 2)
            заметки = [] if конец >= 0 else ["обрыв: конца письма (строки «.») в захвате нет"]
            конец_письма = конец + 2 if конец >= 0 else len(д)
            n += 1
            письмо(без_точек(д[после:конец_письма]), клиент.поток, клиент.пакеты(после, конец_письма),
                   заметки + клиент.заметки(после, конец_письма), сбор, f"письмо-{соед.номер}-{n}")
            клиент.занять(место, конец + 5 if конец >= 0 else len(д))
            место = конец + 5 if конец >= 0 else len(д)
            continue
        if слово == b"BDAT" and (ч := re.fullmatch(rb"BDAT (\d+)( LAST)?", строка, re.I)):
            if not куски:
                начало_bdat = после
            размер = int(ч.group(1))
            куски.append(д[после:после + размер])
            место = после + размер
            if ч.group(2) or место >= len(д):
                n += 1
                письмо(b"".join(куски), клиент.поток, клиент.пакеты(начало_bdat, место),
                       [] if ч.group(2) else ["обрыв: BDAT LAST в захвате нет"], сбор, f"письмо-{соед.номер}-{n}")
                клиент.занять(начало_bdat, место)
                куски = []
            continue
        if слово == b"STARTTLS":
            break
        место = после


POP3_КОМАНДЫ = frozenset({"USER", "PASS", "APOP", "AUTH", "STAT", "LIST", "RETR", "DELE", "NOOP", "RSET", "QUIT",
                          "TOP", "UIDL", "CAPA", "STLS"})


def pop3(соед: Соединение, сбор: Сбор) -> None:
    """Команды клиента по порядку, ответы сервера по одному на команду (продолжения AUTH «+ …» пропускаются);
    многострочные (RETR, TOP, CAPA, LIST и UIDL без аргумента) — до строки «.»; RETR — письмо."""
    клиент, сервер = _роли(соед, re.compile(rb"[+-](?:OK|ERR)"))
    команды = [(н, _значение(с).strip().partition(" ")) for н, _, _, с in _строки(клиент)]
    команды = [(н, к.upper(), а.strip()) for н, (к, _, а) in команды if к.upper() in POP3_КОМАНДЫ]
    д, место = bytes(сервер.данные), 0
    if команды and д.startswith(b"+OK") and _пакет(сервер, 0) < команды[0][0]:
        место = д.find(b"\n") + 1 or len(д)                 # приветствие
    for _, команда, аргумент in команды:
        while д.startswith(b"+ ", место):
            место = (д.find(b"\n", место) + 1) or len(д)
        конец_строки = д.find(b"\n", место)
        if конец_строки < 0:
            break
        хорошо, место = д.startswith(b"+OK", место), конец_строки + 1
        if команда == "STLS" and хорошо:
            break
        if not хорошо or not (команда in ("RETR", "TOP", "CAPA") or команда in ("LIST", "UIDL") and not аргумент):
            continue
        конец = д.find(b"\r\n.\r\n", место - 2)
        конец_тела = конец + 2 if конец >= 0 else len(д)
        if команда == "RETR":
            письмо(без_точек(д[место:конец_тела]), сервер.поток, сервер.пакеты(место, конец_тела),
                   ([] if конец >= 0 else ["обрыв: конца письма в захвате нет"]) + сервер.заметки(место, конец_тела),
                   сбор, f"письмо-{соед.номер}-{аргумент or 'x'}")
            сервер.занять(место, конец_тела)
        место = конец + 5 if конец >= 0 else len(д)


#: Элемент FETCH с литералом (RFC 3501, 7.4.2 и 4.3; BINARY и ~{n} — RFC 3516).
ЛИТЕРАЛ_IMAP = re.compile(rb"(BODY\[([^\]]*)\](?:<(\d+)>)?|BINARY\[([^\]]*)\](?:<\d+>)?|RFC822(\.HEADER|\.TEXT)?)"
                          rb" ~?\{(\d+)\}\r\n", re.I)


def imap(соед: Соединение, сбор: Сбор) -> None:
    """Литералы ответов FETCH на стороне сервера: BODY[]/RFC822 — письмо целиком; BODY[n]/BINARY[n]/TEXT —
    часть письма; заголовки (HEADER, MIME) — пропускаются. Литерал не просматривается изнутри."""
    сервер = _роли(соед, re.compile(rb"\* (?:OK|PREAUTH|BYE)"))[1]
    д, место = bytes(сервер.данные), 0
    while (н := ЛИТЕРАЛ_IMAP.search(д, место)) is not None:
        длина = int(н.group(6))
        начало, конец = н.end(), н.end() + длина
        место = конец
        данные = д[начало:конец]
        номер = re.findall(rb"\* (\d+) FETCH", д[max(0, н.start() - 4096):н.start()])
        номер = _значение(номер[-1]) if номер else "x"
        раздел = _значение(н.group(2) if н.group(2) is not None else н.group(4) or b"").upper()
        заметки = [] if len(данные) == длина else [f"обрыв: получено {len(данные)} из {длина} байт"]
        заметки += сервер.заметки(начало, конец)
        if н.group(3):
            заметки.append(f"часть с байта {int(н.group(3))}")
        сервер.занять(н.start(), конец)
        if раздел.startswith(("HEADER", "MIME")) or раздел.endswith(("HEADER", "MIME")) or (
                н.group(5) or b"").upper() == b".HEADER":
            continue
        if н.group(1).upper().startswith(b"RFC822") and not н.group(5) or н.group(2) is not None and not раздел:
            письмо(данные, сервер.поток, сервер.пакеты(начало, конец), заметки, сбор, f"письмо-{соед.номер}-{номер}")
        else:
            сбор.добавить(Объект("часть письма", f"imap-{номер}-{раздел.replace('.', '-') or 'text'}", "", данные,
                                 сервер.поток, сервер.пакеты(начало, конец), заметки))


# == TFTP (RFC 1350, 2347, 2348) ===================================================================================

def _развернуть(номера: Sequence[int], модуль: int = 1 << 16) -> list[int]:
    """Номера по модулю — в растущие (RFC 3550, A.1): каждый следующий — ближайший к наибольшему прежнему."""
    итог, опора = [], None
    for н in номера:
        if опора is None:
            опора = н
        else:
            опора_н = опора + ((н - опора + модуль // 2) % модуль) - модуль // 2
            итог.append(опора_н)
            опора = max(опора, опора_н)
            continue
        итог.append(н)
    return итог


def tftp(udp: list[Соединение], сбор: Сбор, занятые: set[int]) -> None:
    """RRQ/WRQ на порт 69 (или помеченные TFTP) → блоки DATA обмена клиента с тем же адресом сервера после
    запроса: по номерам блоков (с переходом через 65535), повторы — прочь; короткий блок — последний."""
    запросы = []
    for соед in udp:
        for отправитель, данные, номер, стек in соед.датаграммы:
            if "TFTP" in стек and данные[:2] in (b"\x00\x01", b"\x00\x02") and данные.count(b"\x00", 2) >= 2:
                имя, режим = данные[2:].split(b"\x00")[:2]
                запросы.append((номер, данные[1], отправитель, имя, режим, соед))
    запросы.sort(key=lambda з: з[0])
    for i, (номер, вид, клиент, имя, режим, соед_запроса) in enumerate(запросы):
        сервер = соед_запроса.стороны[клиент].к.rsplit(":", 1)[0]
        следующий = next((з[0] for з in запросы[i + 1:] if з[2] == клиент), 1 << 62)
        занятые.add(номер)
        блоки: list[tuple[int, bytes, int]] = []
        размер_блока, ошибка, поток = 512, "", ""
        for соед in udp:
            if клиент not in соед.точки:
                continue
            for отправитель, данные, н, _ in соед.датаграммы:
                if not номер < н < следующий or отправитель[0] not in (клиент[0], сервер) or len(данные) < 4:
                    continue
                код = struct.unpack_from(">H", данные)[0]
                отправляет_данные = (отправитель != клиент) == (вид == 1)
                if код == 3 and отправляет_данные:
                    блоки.append((struct.unpack_from(">H", данные, 2)[0], данные[4:], н))
                    поток = соед.стороны[отправитель].поток
                    занятые.add(н)
                elif код == 6:                              # OACK (RFC 2347), blksize (RFC 2348)
                    части = данные[2:].split(b"\x00")
                    for ключ, значение in zip(части[::2], части[1::2], strict=False):
                        if ключ.lower() == b"blksize" and значение.isdigit():
                            размер_блока = int(значение)
                    занятые.add(н)
                elif код == 5:
                    ошибка = f"ошибка TFTP {struct.unpack_from('>H', данные, 2)[0]}: " + _значение(
                        данные[4:].split(b"\x00")[0])
                    занятые.add(н)
                elif код == 4:
                    занятые.add(н)
        if not блоки:
            continue
        по_порядку, было, пакеты = sorted(zip(_развернуть([б for б, _, _ in блоки]), range(len(блоки)), strict=True)), set(), []
        части, заметки = [], []
        for номер_блока, j in по_порядку:
            if номер_блока in было:
                continue
            было.add(номер_блока)
            части.append(блоки[j][1])
            пакеты.append(блоки[j][2])
        пропущено = (max(было) - min(было) + 1) - len(было)
        if пропущено or min(было) != 1:
            заметки.append(f"пропущено блоков: {пропущено + min(было) - 1}")
        if len(части[-1]) >= размер_блока:
            заметки.append("обрыв: последнего (короткого) блока в захвате нет")
        if ошибка:
            заметки.append(ошибка)
        заметки.insert(0, f"{'чтение (RRQ)' if вид == 1 else 'запись (WRQ)'}, режим {_значение(режим)}, блок {размер_блока}")
        сбор.добавить(Объект("TFTP", имя_файла(_значение(имя)) or f"tftp-{номер}", "", b"".join(части), поток,
                             sorted(пакеты), заметки))


# == RTP (RFC 3550) =====================================================================================================

#: Статические типы нагрузки: RFC 3551, табл. 4 (звук, A) и 5 (видео, V; MP2T — AV): кодировка, частота, вид.
RTP_СТАТИЧЕСКИЕ = {
    0: ("PCMU", 8000, "A"), 3: ("GSM", 8000, "A"), 4: ("G723", 8000, "A"), 5: ("DVI4", 8000, "A"),
    6: ("DVI4", 16000, "A"), 7: ("LPC", 8000, "A"), 8: ("PCMA", 8000, "A"), 9: ("G722", 8000, "A"),
    10: ("L16", 44100, "A"), 11: ("L16", 44100, "A"), 12: ("QCELP", 8000, "A"), 13: ("CN", 8000, "A"),
    14: ("MPA", 90000, "A"), 15: ("G728", 8000, "A"), 16: ("DVI4", 11025, "A"), 17: ("DVI4", 22050, "A"),
    18: ("G729", 8000, "A"), 25: ("CelB", 90000, "V"), 26: ("JPEG", 90000, "V"), 28: ("nv", 90000, "V"),
    31: ("H261", 90000, "V"), 32: ("MPV", 90000, "V"), 33: ("MP2T", 90000, "AV"), 34: ("H263", 90000, "V"),
}
#: Коды нулевого отсчёта G.711 (Sun g711.c: linear2ulaw(0), linear2alaw(0)) и коды формата WAVE (RFC 2361, A.7–A.8).
ТИШИНА_G711 = {"PCMU": 0xFF, "PCMA": 0xD5}
WAVE_КОДЫ = {"PCMU": 0x0007, "PCMA": 0x0006}
#: Биты речи по типу кадра (FT): AMR — RFC 4867, табл. 1 (0–7 и SID 8); 9–11 (SID GSM-EFR, TDMA, PDC) и AMR-WB
#: 0–9 — Wireshark packet-amr.c (Framebits_NB, Framebits_WB); FT 14 и 15 — без речи (4.3.2).
AMR_БИТ = (95, 103, 118, 134, 148, 159, 204, 244, 39, 43, 38, 37)
AMR_WB_БИТ = (132, 177, 253, 285, 317, 365, 397, 461, 477, 40)
#: Кадр AMR — 20 мс: 160 тактов при 8000 Гц, 320 — у AMR-WB при 16000 Гц (RFC 4867, 4.1).
AMR_ТАКТОВ = {"AMR": 160, "AMR-WB": 320}
СТАРТ_NAL = b"\x00\x00\x00\x01"
#: События RFC 4733, 3.2: 0–9, *, #, A–D, flash.
DTMF = "0123456789*#ABCD"


def заголовок_rtp(д: bytes) -> tuple[int, int, int, int, int, bytes] | None:
    """(PT, маркер, номер, отметка, SSRC, нагрузка) — RFC 3550, 5.1 и 5.3.1; добивка (P) снимается."""
    if len(д) < 12 or д[0] >> 6 != 2:
        return None
    cc, тип = д[0] & 0x0F, д[1] & 0x7F
    начало = 12 + 4 * cc
    if д[0] & 0x10:
        if начало + 4 > len(д):
            return None
        начало += 4 + 4 * struct.unpack_from(">H", д, начало + 2)[0]
    конец = len(д) - (д[-1] if д[0] & 0x20 else 0)
    if начало > конец:
        return None
    номер, отметка, ssrc = struct.unpack_from(">HII", д, 2)
    return тип, д[1] >> 7, номер, отметка, ssrc, д[начало:конец]


def wav(отсчёты: bytes, код: int, частота: int = 8000) -> bytes:
    """RIFF/WAVE (RFC 2361; раскладка — libsndfile wav.c): fmt из 18 байт (код, 1 канал, частота, байт/с,
    выравнивание 1, 8 бит, cbSize 0), fact (число отсчётов), data; нечётный data — с добивочным байтом."""
    fmt = struct.pack("<HHIIHHH", код, 1, частота, частота, 1, 8, 0)
    тело = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"fact" + struct.pack("<II", 4, len(отсчёты))
            + b"data" + struct.pack("<I", len(отсчёты)) + отсчёты + b"\x00" * (len(отсчёты) & 1))
    return b"RIFF" + struct.pack("<I", len(тело)) + тело


def _amr_октеты(нагрузка: bytes, биты: Sequence[int]) -> list[tuple[int, int, bytes]] | None:
    """RFC 4867, 4.4: CMR и резерв (октет), ToC по октету (F, FT, Q, P, P), речь — с октета, ровно до конца."""
    место, оглавление = 1, []
    while True:
        if место >= len(нагрузка):
            return None
        б = нагрузка[место]
        место += 1
        оглавление.append(((б >> 3) & 0x0F, (б >> 2) & 1))
        if not б & 0x80:
            break
    кадры = []
    for ft, q in оглавление:
        if ft in (14, 15):
            кадры.append((ft, q, b""))
            continue
        if ft >= len(биты):
            return None
        n = (биты[ft] + 7) // 8
        кадры.append((ft, q, нагрузка[место:место + n]))
        место += n
    return кадры if место == len(нагрузка) else None


def _amr_полоса(нагрузка: bytes, биты: Sequence[int]) -> list[tuple[int, int, bytes]] | None:
    """RFC 4867, 4.3: CMR 4 бита, ToC по 6 бит (F, FT, Q), речь подряд; добивка до октета — меньше 8 бит."""
    всего = 8 * len(нагрузка)
    число = int.from_bytes(нагрузка, "big")

    def взять(место: int, n: int) -> int:
        return (число >> (всего - место - n)) & ((1 << n) - 1)

    место, оглавление = 4, []
    while True:
        if место + 6 > всего:
            return None
        запись = взять(место, 6)
        место += 6
        оглавление.append(((запись >> 1) & 0x0F, запись & 1))
        if not запись & 0x20:
            break
    кадры = []
    for ft, q in оглавление:
        n = 0 if ft in (14, 15) else биты[ft] if ft < len(биты) else -1
        if n < 0 or место + n > всего:
            return None
        речь = взять(место, n) << (-n % 8)
        кадры.append((ft, q, речь.to_bytes((n + 7) // 8, "big")))
        место += n
    return кадры if всего - место < 8 else None


def _nal(nal: bytes, видео: str) -> bytes:
    return (СТАРТ_NAL if видео == "annexb" else struct.pack(">I", len(nal))) + nal


def _h264(пакеты: list[tuple[int, bytes]], видео: str) -> tuple[bytes, list[str]]:
    """RFC 6184: одиночный NAL (1–23), STAP-A (24) и STAP-B (25, с DON), FU-A (28) и FU-B (29, DON в первом);
    заголовок NAL из FU — F и NRI индикатора и тип из заголовка FU (5.8)."""
    итог, заметки, фрагмент, оборвано, предыдущий = bytearray(), [], None, 0, None
    for номер, д in пакеты:
        if предыдущий is not None and номер != предыдущий + 1 and фрагмент is not None:
            фрагмент, оборвано = None, оборвано + 1
        предыдущий = номер
        if not д:
            continue
        тип = д[0] & 0x1F
        if 1 <= тип <= 23:
            итог += _nal(д, видео)
        elif тип in (24, 25):
            место = 3 if тип == 25 else 1
            while место + 2 <= len(д):
                n = struct.unpack_from(">H", д, место)[0]
                итог += _nal(д[место + 2:место + 2 + n], видео)
                место += 2 + n
        elif тип in (28, 29):
            if len(д) < 3:
                continue
            s, e = д[1] >> 7, (д[1] >> 6) & 1
            if s:
                if фрагмент is not None:
                    оборвано += 1
                фрагмент = bytearray([д[0] & 0xE0 | д[1] & 0x1F]) + д[4 if тип == 29 else 2:]
            elif фрагмент is not None:
                фрагмент += д[2:]
            else:
                оборвано += 1
                continue
            if e:
                итог += _nal(bytes(фрагмент), видео)
                фрагмент = None
        else:
            заметки.append(f"пакеты вида {тип} (MTAP/зарезервированные) пропущены")
    if фрагмент is not None:
        оборвано += 1
    if оборвано:
        заметки.append(f"NAL, оборванных пропуском пакетов (FU без начала или конца): {оборвано} — выброшены")
    return bytes(итог), sorted(set(заметки))


def _h265(пакеты: list[tuple[int, bytes]], видео: str) -> tuple[bytes, list[str]]:
    """RFC 7798: заголовок нагрузки 2 байта (F, тип 6 бит, LayerId, TID); одиночный NAL (< 48), AP (48: размер
    16 бит и NAL), FU (49: S, E, тип 6 бит; заголовок NAL — (байт0 & 0x81) | тип << 1 и байт1), PACI (50) — пропуск."""
    итог, заметки, фрагмент, оборвано, предыдущий = bytearray(), [], None, 0, None
    for номер, д in пакеты:
        if предыдущий is not None and номер != предыдущий + 1 and фрагмент is not None:
            фрагмент, оборвано = None, оборвано + 1
        предыдущий = номер
        if len(д) < 3:
            continue
        тип = (д[0] >> 1) & 0x3F
        if тип < 48:
            итог += _nal(д, видео)
        elif тип == 48:
            место = 2
            while место + 2 <= len(д):
                n = struct.unpack_from(">H", д, место)[0]
                итог += _nal(д[место + 2:место + 2 + n], видео)
                место += 2 + n
        elif тип == 49:
            s, e, тип_fu = д[2] >> 7, (д[2] >> 6) & 1, д[2] & 0x3F
            if s:
                if фрагмент is not None:
                    оборвано += 1
                фрагмент = bytearray([(д[0] & 0x81) | (тип_fu << 1), д[1]]) + д[3:]
            elif фрагмент is not None:
                фрагмент += д[3:]
            else:
                оборвано += 1
                continue
            if e:
                итог += _nal(bytes(фрагмент), видео)
                фрагмент = None
        else:
            заметки.append(f"пакеты вида {тип} (PACI/зарезервированные) пропущены")
    if фрагмент is not None:
        оборвано += 1
    if оборвано:
        заметки.append(f"NAL, оборванных пропуском пакетов (FU без начала или конца): {оборвано} — выброшены")
    return bytes(итог), sorted(set(заметки))


def _кодек(описание: dict | None, тип: int) -> tuple[str, int, str]:
    """(кодировка, частота, вид A/V/AV) — по a=rtpmap из SDP, иначе по статической таблице RFC 3551."""
    if описание:
        rtpmap = описание.get("rtpmap") or {}
        запись = rtpmap.get(тип, rtpmap.get(str(тип)))
        if запись:
            кодировка, _, частота = str(запись).partition("/")
            вид = {"audio": "A", "video": "V"}.get(str(описание.get("вид", "")).lower(), "")
            return кодировка, int(частота) if частота.isdigit() else 0, вид
    кодировка, частота, вид = RTP_СТАТИЧЕСКИЕ.get(тип, ("", 0, ""))
    if not кодировка and описание:
        вид = {"audio": "A", "video": "V"}.get(str(описание.get("вид", "")).lower(), "")
    return кодировка, частота, вид


def _описание_sdp(sdp: dict, адрес_порты: Sequence[tuple[str, int]]) -> dict | None:
    for адрес, порт in адрес_порты:
        запись = sdp.get(f"{адрес}|{порт}")
        if запись:
            return запись[1] if isinstance(запись, (list, tuple)) else запись
    return None


def rtp(udp: list[Соединение], sdp: dict, сбор: Сбор, занятые: set[int], настройки: Настройки) -> None:
    """Потоки RTP по (адреса, порты, SSRC): пакеты по расширенному номеру, повторы — прочь, пропуски — в заметки;
    преобразование по кодеку преобладающего типа нагрузки; события RFC 4733 — строкой DTMF в заметках."""
    потоки: dict[tuple, list[tuple[int, int, int, int, bytes, int]]] = defaultdict(list)
    for соед in udp:
        for отправитель, данные, номер, стек in соед.датаграммы:
            if "RTP" not in стек or "RTCP" in стек or (з := заголовок_rtp(данные)) is None:
                continue
            тип, маркер, номер_rtp, отметка, ssrc, нагрузка = з
            получатель = соед.точки[1] if отправитель == соед.точки[0] else соед.точки[0]
            потоки[(отправитель, получатель, ssrc)].append((номер_rtp, отметка, тип, маркер, нагрузка, номер))
            занятые.add(номер)
    for (отправитель, получатель, ssrc), пакеты in потоки.items():
        описание = _описание_sdp(sdp, (получатель, отправитель))
        расширенные = _развернуть([п[0] for п in пакеты])
        по_порядку, было, повторов = [], set(), 0
        for р, п in sorted(zip(расширенные, пакеты, strict=True), key=lambda x: x[0]):
            if р in было:
                повторов += 1
                continue
            было.add(р)
            по_порядку.append((р, п))
        пропущено = (по_порядку[-1][0] - по_порядку[0][0] + 1) - len(по_порядку)
        кодеки = {т: _кодек(описание, т) for т in {п[2] for п in пакеты}}
        события = [т for т, к in кодеки.items() if к[0].lower() == "telephone-event"]
        основные = Counter(п[2] for _, п in по_порядку if п[2] not in события and кодеки[п[2]][0] != "CN")
        заметки = [f"SSRC 0x{ssrc:08x}, пакетов {len(по_порядку)}"]
        if пропущено:
            заметки.append(f"пропущено пакетов: {пропущено}")
        if повторов:
            заметки.append(f"повторов отброшено: {повторов}")
        dtmf = _dtmf([п for _, п in по_порядку if п[2] in события])
        if dtmf:
            заметки.append(f"DTMF (RFC 4733): {dtmf}")
        тип = основные.most_common(1)[0][0] if основные else (события[0] if события else по_порядку[0][1][2])
        кодировка, частота, вид_м = кодеки[тип]
        заметки.insert(1, f"кодек {кодировка or '?'}/{частота or '?'} (тип {тип}"
                          + (", по SDP" if описание and (описание.get('rtpmap') or {}) else "") + ")")
        свои = [(р, п) for р, п in по_порядку if п[2] == тип]
        вид = {"A": "RTP-звук", "V": "RTP-видео", "AV": "RTP-видео"}.get(вид_м, "RTP-поток")
        данные, расширение, мим, з = _преобразовать(кодировка.upper(), частота, свои, настройки)
        основа = f"rtp-{отправитель[0]}-{отправитель[1]}-{получатель[0]}-{получатель[1]}-{ssrc:08x}".replace(":", "_")
        сторона_поток = f"UDP {_точка(*отправитель)} → {_точка(*получатель)}"
        сбор.добавить(Объект(вид, f"{основа}.{расширение}", мим or (f"{кодировка}/{частота}" if кодировка else ""), данные, сторона_поток,
                             sorted(п[5] for _, п in по_порядку), заметки + з))


def _dtmf(пакеты: list[tuple]) -> str:
    """Одно событие — одна отметка времени (RFC 4733, 2.3): цифра по коду события."""
    итог, отметки = [], set()
    for _, отметка, _, _, нагрузка, _ in пакеты:
        if len(нагрузка) >= 4 and отметка not in отметки:
            отметки.add(отметка)
            итог.append(DTMF[нагрузка[0]] if нагрузка[0] < 16 else "flash" if нагрузка[0] == 16 else f"[{нагрузка[0]}]")
    return "".join(итог)


def _преобразовать(кодировка: str, частота: int, пакеты: list[tuple[int, tuple]], н: Настройки
                   ) -> tuple[bytes, str, str, list[str]]:
    """(байты, расширение, тип, заметки) по кодеку: G.711 → WAV или .ul/.al, G.722 → .g722, AMR → .amr/.awb,
    H.264/H.265 → Annex B или NAL с длиной, MP2T → .ts; прочее — нагрузки подряд (.rtp.bin)."""
    нагрузки = [(р, п[4]) for р, п in пакеты]
    if кодировка in ТИШИНА_G711:
        отсчёты, з = bytearray(), []
        начало = пакеты[0][1][1]
        for _, п in пакеты:
            место = (п[1] - начало) & 0xFFFFFFFF
            if н.тишина and len(отсчёты) < место:
                пропуск = место - len(отсчёты)
                if пропуск <= ТИШИНА_ДО:
                    отсчёты += bytes([ТИШИНА_G711[кодировка]]) * пропуск
                else:
                    з.append(f"разрыв отметок времени {пропуск} тактов — тишина не вставлена")
            отсчёты += п[4]
        if н.тишина and len(отсчёты) > sum(len(п[4]) for _, п in пакеты):
            з.append(f"пропуски заполнены тишиной: {len(отсчёты) - sum(len(п[4]) for _, п in пакеты)} отсчётов")
        if н.звук == "wav":
            return wav(bytes(отсчёты), WAVE_КОДЫ[кодировка], частота or 8000), "wav", "audio/wav", з
        return bytes(отсчёты), "ul" if кодировка == "PCMU" else "al", f"audio/{кодировка}", з
    if кодировка == "G722":
        return b"".join(д for _, д in нагрузки), "g722", "audio/G722", []
    if кодировка in AMR_ТАКТОВ:
        биты = AMR_БИТ if кодировка == "AMR" else AMR_WB_БИТ
        итог = bytearray(b"#!AMR\n" if кодировка == "AMR" else b"#!AMR-WB\n")
        предыдущий, кадров, з = None, 1, []
        for р, п in пакеты:
            кадры = _amr_октеты(п[4], биты)
            if кадры is None:
                кадры = _amr_полоса(п[4], биты)
                if кадры is not None and "экономией" not in " ".join(з):
                    з.append("режим с экономией полосы (RFC 4867, 4.3)")
            if кадры is None:
                з.append("пакет не разобран как AMR — пропущен")
                continue
            if н.тишина and предыдущий is not None and р > предыдущий + 1:
                потеряно = (р - предыдущий - 1) * кадров
                итог += bytes([15 << 3 | 1 << 2]) * потеряно               # NO_DATA (RFC 4867, 5.3)
                з.append(f"потерянные кадры — NO_DATA: {потеряно}")
            предыдущий, кадров = р, len(кадры)
            for ft, q, речь in кадры:
                итог += bytes([ft << 3 | q << 2]) + речь
        return bytes(итог), "amr" if кодировка == "AMR" else "awb", f"audio/{кодировка}", sorted(set(з))
    if кодировка in ("H264", "H265"):
        байты, з = (_h264 if кодировка == "H264" else _h265)(нагрузки, н.видео)
        расширение = ("h264" if кодировка == "H264" else "h265") if н.видео == "annexb" else "nal"
        return байты, расширение, f"video/{кодировка}", з
    if кодировка == "MP2T":
        return b"".join(д for _, д in нагрузки), "ts", "video/MP2T", []
    return (b"".join(д for _, д in нагрузки), "rtp.bin", "",
            [f"кодек {кодировка or 'не известен'}: нагрузки RTP подряд, без заголовков"])


# == JSON и находки по сигнатуре в неразобранном ======================================================================

def _json_и_сигнатуры(сторона: Сторона, соед: Соединение, сбор: Сбор) -> None:
    """Не разобранная протоколами сторона: целиком значения JSON — объектами JSON; иначе — сигнатуры."""
    данные = bytes(сторона.данные)
    if not данные:
        return
    if not сторона.покрыто and (значения := json_значения(данные)):
        for i, (начало, конец) in enumerate(значения, start=1):
            сбор.добавить(Объект("JSON", f"json-{соед.номер}-{i}.json", "application/json", данные[начало:конец],
                                 сторона.поток, сторона.пакеты(начало, конец), сторона.заметки(начало, конец)))
        return
    for н in poisk.сигнатуры_в_байтах(данные, предел=200):
        начало = int(н["байт"])
        if not сторона.свободно(начало):
            continue
        длина = int(н["длина"])
        заметки = [] if длина else ["длина по формату неизвестна — до конца потока"]
        конец = начало + (длина or min(len(данные) - начало, ОБЪЕКТ_ДО))
        if конец > len(данные):
            заметки.append(f"обрыв: получено {len(данные) - начало} из {длина} байт")
        сбор.добавить(Объект("по сигнатуре", f"поток{соед.номер}-{начало}.{н['расширение']}", "",
                             данные[начало:конец], сторона.поток, сторона.пакеты(начало, конец),
                             заметки + сторона.заметки(начало, конец)))


# == опись архивов =======================================================================================================

def _имя_gzip(данные: bytes) -> str:
    """FNAME из заголовка gzip (RFC 1952, 2.3.1: FEXTRA — длина 2 байта и поле; FNAME — строка до нуля)."""
    if len(данные) < 10 or not данные[3] & 0x08:
        return ""
    место = 10
    if данные[3] & 0x04:
        место += 2 + int.from_bytes(данные[10:12], "little")
    конец = данные.find(b"\x00", место)
    return имя_файла(данные[место:конец].decode("latin-1")) if конец > место else ""


def _tar(данные: bytes) -> tarfile.TarFile | None:
    try:
        return tarfile.open(fileobj=io.BytesIO(данные), mode="r:*")
    except (tarfile.TarError, EOFError, OSError, zlib.error, lzma.LZMAError, ValueError):
        return None


def состав(данные: bytes, расширение: str) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """Опись: ZIP (и DOCX … EPUB) — zipfile; tar и tar в gzip/bzip2/xz — tarfile; просто сжатый — один член.
    RAR, 7z, CAB — только признаются (стандартной библиотекой не раскрыть)."""
    try:
        if расширение in АРХИВЫ_ZIP:
            with zipfile.ZipFile(io.BytesIO(данные)) as z:
                члены = z.infolist()
                return [{"имя": ч.filename, "длина": ч.file_size, "сжато": ч.compress_size, "каталог": ч.is_dir(),
                         "шифр": bool(ч.flag_bits & 1)} for ч in члены[:СОСТАВ_ДО]], (
                    [f"в описи первые {СОСТАВ_ДО} из {len(члены)}"] if len(члены) > СОСТАВ_ДО else [])
        if расширение == "tar" or расширение in СЖАТЫЕ:
            t = _tar(данные)
            if t is not None:
                with t:
                    члены = t.getmembers()
                return [{"имя": ч.name, "длина": ч.size, "каталог": ч.isdir()} for ч in члены[:СОСТАВ_ДО]], (
                    [f"в описи первые {СОСТАВ_ДО} из {len(члены)}"] if len(члены) > СОСТАВ_ДО else [])
            if расширение in СЖАТЫЕ:
                распаковано, беда = _распаковать(данные, СЖАТЫЕ[расширение]())
                имя = (_имя_gzip(данные) if расширение == "gz" else "") or "содержимое"
                return [{"имя": имя, "длина": len(распаковано), "каталог": False}], [беда] if беда else []
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, ValueError, NotImplementedError) as ошибка:
        return None, [f"опись не прочиталась: {ошибка}"]
    if расширение in ("rar", "7z", "cab"):
        return None, [f"{расширение.upper()}: без сторонних библиотек содержимое не раскрывается — архив отдаётся целиком"]
    return None, []


def член(данные: bytes, расширение: str, номер: int) -> tuple[str, bytes]:
    """Член архива по номеру в описи: (имя, байты). ValueError — нет такого, каталог, шифр или больше предела."""
    if расширение in АРХИВЫ_ZIP:
        with zipfile.ZipFile(io.BytesIO(данные)) as z:
            члены = z.infolist()
            if not 0 <= номер < len(члены) or члены[номер].is_dir():
                raise ValueError("нет такого члена архива")
            if члены[номер].file_size > ОБЪЕКТ_ДО:
                raise ValueError("член архива больше предела")
            try:
                return члены[номер].filename, z.read(члены[номер])
            except (RuntimeError, zipfile.BadZipFile, NotImplementedError, zlib.error) as ошибка:
                raise ValueError(f"член не читается: {ошибка}") from None
    t = _tar(данные) if расширение == "tar" or расширение in СЖАТЫЕ else None
    if t is not None:
        with t:
            члены = t.getmembers()
            if not 0 <= номер < len(члены) or not члены[номер].isfile():
                raise ValueError("нет такого члена архива")
            if члены[номер].size > ОБЪЕКТ_ДО:
                raise ValueError("член архива больше предела")
            return члены[номер].name, t.extractfile(члены[номер]).read()
    if расширение in СЖАТЫЕ and номер == 0:
        распаковано, _ = _распаковать(данные, СЖАТЫЕ[расширение]())
        return (_имя_gzip(данные) if расширение == "gz" else "") or "содержимое", распаковано
    raise ValueError("у объекта нет описи")


# == сборка всего ==========================================================================================================

def _дополнить(о: Объект, настройки: Настройки) -> None:
    """Вид по содержимому, расширение в имени, сверка с заявленным, опись архива; JSON — с отступами по настройке."""
    if о.вид == "JSON" and настройки.json_отступ:
        try:
            о.данные = json.dumps(json.loads(о.данные), ensure_ascii=False, indent=2).encode()
            о.заметки.append("перезаписано с отступами (настройка)")
        except (ValueError, UnicodeDecodeError):
            pass
    о.содержимое, о.расширение = содержимое(о.данные)
    заявлено = расширение_типа(о.тип) if о.тип and "/" in о.тип else ""
    о.имя = с_расширением(о.имя, заявлено if заявлено not in ("", "bin") else о.расширение or заявлено or "bin")
    имя_расш = о.имя.rsplit(".", 1)[-1].lower() if "." in о.имя else ""
    if о.расширение and poisk.опознать(о.данные) and СИНОНИМЫ.get(имя_расш, имя_расш) != о.расширение and not (
            о.расширение == "zip" and имя_расш in АРХИВЫ_ZIP):
        о.заметки.append(f"по содержимому — {о.содержимое}, а не .{имя_расш}")
    if not о.тип or о.тип == "application/octet-stream" and о.расширение:
        о.тип = тип_расширения(о.расширение) if о.расширение else "application/octet-stream"
    о.состав, заметки = состав(о.данные, о.расширение)
    о.заметки += заметки


def собрать(сводки: Sequence[Сводка], нагрузки: Sequence[bytes | None], *, sdp: dict | None = None,
            настройки: Настройки | None = None) -> dict[str, Any]:
    """Все объекты захвата (сводки и нагрузки TCP/UDP целиком, по порядку пакетов) под пределами настроек:
    {"объекты": [Объект], "отброшено": {"объектов", "байт"}, "заметки": [...], "время": {номер: время}}."""
    настройки = настройки or Настройки()
    сбор = Сбор(настройки.объектов_до, настройки.байт_до, настройки.объект_до)
    все, заметки = соединения(сводки, нагрузки)
    tcp = [с for с in все if с.транспорт == "TCP"]
    udp = [с for с in все if с.транспорт == "UDP"]
    передачи: list[Передача] = []
    управление: dict[str, tuple[str, str]] = {}
    for соед in tcp:
        if _http(соед, сбор, настройки):
            соед.разобрано = True
        elif "FTP" in соед.протоколы:
            передачи += ftp_управление(соед)
            for с in соед.стороны.values():
                управление[с.поток] = (с.от.rsplit(":", 1)[0], с.к.rsplit(":", 1)[0])
                с.занять(0, len(с.данные))
            соед.разобрано = True
        elif "SMTP" in соед.протоколы:
            smtp(соед, сбор)
        elif "POP3" in соед.протоколы:
            pop3(соед, сбор)
        elif "IMAP" in соед.протоколы:
            imap(соед, сбор)
    _ftp_данные(передачи, tcp, управление, сбор)
    for соед in tcp:
        if not соед.разобрано and "FTP-DATA" in соед.протоколы:
            соед.разобрано = True
            сторона = max(соед.стороны.values(), key=lambda с: len(с.данные))
            сторона.занять(0, len(сторона.данные))
            сбор.добавить(Объект("FTP", f"ftp-data-{соед.номер}", "", bytes(сторона.данные), сторона.поток,
                                 сторона.пакеты(0, len(сторона.данные)), ["канал данных без команды передачи в захвате"]))
    for соед in tcp:
        for сторона in соед.стороны.values():
            _json_и_сигнатуры(сторона, соед, сбор)
    занятые: set[int] = set()
    tftp(udp, сбор, занятые)
    rtp(udp, sdp or {}, сбор, занятые, настройки)
    for соед in udp:
        свободные: dict[tuple[str, int], Сторона] = {}
        for отправитель, данные, номер, _ in соед.датаграммы:
            if номер in занятые:
                continue
            с = соед.стороны[отправитель]
            if json_значения(данные, одно=True):
                сбор.добавить(Объект("JSON", f"json-{соед.номер}-{номер}.json", "application/json", данные, с.поток,
                                     [номер]))
                continue
            свободные.setdefault(отправитель, Сторона("UDP", с.от, с.к)).добавить(данные, номер)
        for сторона in свободные.values():
            _json_и_сигнатуры(сторона, соед, сбор)
    сбор.объекты.sort(key=lambda о: о.пакеты[0] if о.пакеты else 0)
    for i, о in enumerate(сбор.объекты, start=1):
        о.номер = i
        _дополнить(о, настройки)
    время = {с["номер"]: с["время"] for с in сводки}
    return {"объекты": сбор.объекты, "отброшено": сбор.отброшено, "заметки": заметки, "время": время}


def _http(соед: Соединение, сбор: Сбор, настройки: Настройки) -> bool:
    """HTTP/1.x в соединении: запросы у той стороны, что начинается строкой запроса; ответы — у другой."""
    клиент = next((с for с in соед.стороны.values() if ЗАПРОС.match(bytes(с.данные[:ЗАГОЛОВКИ_ДО]).lstrip(b"\r\n"))),
                  None)
    if клиент is None:
        return False
    сервер = next((с for с in соед.стороны.values() if с is not клиент), None)
    запросы, конец_к = сообщения_http(bytes(клиент.данные), запрос=True)
    клиент.занять(0, конец_к)
    ответы, конец_с = сообщения_http(bytes(сервер.данные), запрос=False, методы=[з.метод for з in запросы]) \
        if сервер else ([], 0)
    if сервер:
        сервер.занять(0, конец_с)
    окончательные = [о for о in ответы if not 100 <= о.код < 200 or о.код == 101]
    for i, з in enumerate(запросы, start=1):
        if з.тело:
            _объект_http(соед, клиент, з, з.адрес, f"запрос {з.метод} {unquote(з.адрес, errors='replace')}", сбор, настройки, i)
    for i, о in enumerate(окончательные):
        з = запросы[i] if i < len(запросы) else None
        if о.тело:
            _объект_http(соед, сервер, о, з.адрес if з else "", f"ответ {о.код}"
                         + (f" на {з.метод} {unquote(з.адрес, errors='replace')}" if з else ""), сбор, настройки, i + 1)
        if о.код == 101 and з is not None and "websocket" in з.поле("upgrade").lower():
            _websocket(соед, клиент, сервер, з.конец, о.конец, о.поле("sec-websocket-extensions"), сбор)
    return True


def _объект_http(соед: Соединение, сторона: Сторона, с: Сообщение, адрес: str, что: str, сбор: Сбор,
                 настройки: Настройки, n: int) -> None:
    тип_полный = с.поля.get("content-type", [""])[-1]
    тип = тип_полный.split(";")[0].strip().lower()
    заметки = [что] + с.заметки
    тело = с.тело
    if настройки.снимать_сжатие:
        тело, з = снять_кодирование(тело, с.кодировки)
        заметки += з
        тело, з = снять_кодирование(тело, с.поле("content-encoding").split(","))
        заметки += з
    elif с.кодировки or с.поле("content-encoding"):
        заметки.append("сжатие не снималось (настройка)")
    заметки += сторона.заметки(с.начало, с.конец)
    пакеты = сторона.пакеты(с.начало, с.конец)
    имя = имя_из_disposition(с.поле("content-disposition")) if с.поля.get("content-disposition") else ""
    имя = имя or имя_из_адреса(адрес) or f"http-{соед.номер}-{n}"
    if тип == "multipart/form-data":
        for i, (часть_имя, часть_тип, данные, поле) in enumerate(_части_формы(тело, тип_полный), start=1):
            сбор.добавить(Объект("HTTP", часть_имя or f"{имя}-часть-{i}", часть_тип, данные, сторона.поток, пакеты,
                                 заметки + [f"часть формы {i}" + (f", поле «{поле}»" if поле else "")]))
        return
    вид = "JSON" if (тип == "application/json" or тип.endswith("+json") or тип in ("", "text/plain")) and \
        json_значения(тело, одно=True) else "HTTP"
    сбор.добавить(Объект(вид, имя, тип_полный.strip(), тело, сторона.поток, пакеты, заметки))


def _части_формы(тело: bytes, тип_полный: str) -> list[tuple[str, str, bytes, str]]:
    """multipart/form-data: (имя файла, тип, байты, имя поля) каждой части — модулем email (RFC 2046, 5.1.1)."""
    try:
        сообщение = email.message_from_bytes(b"Content-Type: " + тип_полный.encode("utf-8", "replace") + b"\r\n\r\n"
                                             + тело, policy=email.policy.HTTP)
        if not сообщение.is_multipart():
            return []
        итог = []
        for часть in сообщение.iter_parts():
            итог.append((имя_файла(_раскодировать_2047(часть.get_filename() or "")),
                         часть.get_content_type() if часть.get("content-type") else "",
                         часть.get_payload(decode=True) or b"",
                         str(часть.get_param("name", "", header="content-disposition") or "")))
        return итог
    except (ValueError, IndexError, TypeError, LookupError):
        return []


def _websocket(соед: Соединение, клиент: Сторона, сервер: Сторона, начало_к: int, начало_с: int,
               расширения: str, сбор: Сбор) -> None:
    """После 101 — кадры WebSocket в обе стороны; permessage-deflate — по заголовку расширений ответа."""
    сжатие = "permessage-deflate" in расширения
    for сторона, начало, запрет in ((клиент, начало_к, "client_no_context_takeover"),
                                    (сервер, начало_с, "server_no_context_takeover")):
        сообщения, конец = кадры_websocket(bytes(сторона.данные), начало, сжатие=сжатие,
                                           общее_окно=запрет not in расширения)
        сторона.занять(начало, конец)
        for i, (м, к, код, данные, заметки) in enumerate(сообщения, start=1):
            json_ли = код == 1 and json_значения(данные, одно=True)
            сбор.добавить(Объект("JSON" if json_ли else "WebSocket",
                                 f"ws-{соед.номер}-{'к' if сторона is клиент else 'с'}{i}."
                                 + ("json" if json_ли else "txt" if код == 1 else "bin"),
                                 "application/json" if json_ли else "text/plain" if код == 1 else "",
                                 данные, сторона.поток, сторона.пакеты(м, к),
                                 заметки + ["текстовое сообщение" if код == 1 else "двоичное сообщение"]
                                 + сторона.заметки(м, к)))


# == выгрузка ===============================================================================================================

def по_видам(итог: dict[str, Any]) -> dict[str, int]:
    return dict(Counter(о.вид for о in итог["объекты"]))


def безопасное_имя(имя: str) -> str:
    """Имя члена ZIP: без каталогов, «..», управляющих и запрещённых в Windows знаков."""
    имя = имя_файла(имя)
    имя = re.sub(r'[<>:"|?*]', "_", имя).replace("..", "_")
    return имя or "объект"


def имя_выгрузки(о: Объект, настройки: Настройки, время: float | None) -> str:
    """Имя объекта в выгрузке по шаблону: «протокол» — номер и имя; «поток» — поток, время и имя; «номер»."""
    import datetime  # noqa: PLC0415
    if настройки.имена == "номер":
        расширение = re.sub(r"[^0-9A-Za-z]", "", о.имя.rsplit(".", 1)[-1])[:8] if "." in о.имя else ""
        return f"объект-{о.номер:04d}" + (f".{расширение}" if расширение else "")
    if настройки.имена == "поток":
        пояс = datetime.timezone(datetime.timedelta(minutes=настройки.пояс))
        когда = datetime.datetime.fromtimestamp(время or 0, пояс).strftime("%Y%m%dT%H%M%S")
        поток = re.sub(r"[^0-9A-Za-z.]+", "_", о.поток).strip("_")
        return f"{о.номер:04d}-{поток}-{когда}-{безопасное_имя(о.имя)}"
    return f"{о.номер:04d}-{безопасное_имя(о.имя)}"


def время_текстом(время: float | None, пояс: int) -> str:
    import datetime  # noqa: PLC0415
    if время is None:
        return ""
    return datetime.datetime.fromtimestamp(время, datetime.timezone(datetime.timedelta(minutes=пояс))).isoformat(
        timespec="microseconds")


ОПИСЬ_СТОЛБЦЫ = ("номер", "вид", "имя", "в архиве", "тип", "содержимое", "длина", "поток", "пакеты", "время",
                 "sha256", "заметки")


def строка_описи(о: Объект, путь: str, настройки: Настройки, время: float | None) -> dict[str, Any]:
    import hashlib  # noqa: PLC0415
    return {"номер": о.номер, "вид": о.вид, "имя": о.имя, "в архиве": путь, "тип": о.тип, "содержимое": о.содержимое,
            "длина": len(о.данные), "поток": о.поток, "пакеты": " ".join(map(str, о.пакеты)),
            "время": время_текстом(время, настройки.пояс), "sha256": hashlib.sha256(о.данные).hexdigest(),
            "заметки": "; ".join(о.заметки)}


def zip_объектов(итог: dict[str, Any], виды: Sequence[str] = (), настройки: Настройки | None = None,
                 папка: str = "") -> bytes:
    """Все объекты (или только ``виды``) одним ZIP: имена без повторов и без «..», опись.csv (и что отброшено)."""
    настройки = настройки or Настройки()
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        записать_объекты(z, итог, виды, настройки, папка)
    return буфер.getvalue()


def записать_объекты(z: zipfile.ZipFile, итог: dict[str, Any], виды: Sequence[str], настройки: Настройки,
                     папка: str = "") -> list[dict[str, Any]]:
    """Объекты в открытый ZIP (в ``папку``) и опись.csv; возвращает строки описи."""
    строки, занято = [], set()
    for о in итог["объекты"]:
        if виды and о.вид not in виды:
            continue
        имя = имя_выгрузки(о, настройки, итог["время"].get(о.пакеты[0]) if о.пакеты else None)
        основа, точка, расширение = имя.rpartition(".")
        путь, n = f"{папка}{имя}", 1
        while путь.lower() in занято:
            n += 1
            путь = f"{папка}{основа or расширение}-{n}{точка}{расширение if основа else ''}"
        занято.add(путь.lower())
        z.writestr(путь, о.данные)
        строки.append(строка_описи(о, путь, настройки, итог["время"].get(о.пакеты[0]) if о.пакеты else None))
    буфер = io.StringIO()
    запись = csv.DictWriter(буфер, fieldnames=ОПИСЬ_СТОЛБЦЫ, delimiter=";")
    запись.writeheader()
    запись.writerows(строки)
    z.writestr(f"{папка}опись.csv", ("﻿" + буфер.getvalue()).encode("utf-8"))
    if итог["отброшено"]["объектов"] or итог["заметки"]:
        z.writestr(f"{папка}отброшено.txt",
                   (f"Отброшено по пределам: объектов {итог['отброшено']['объектов']}, "
                    f"байт {итог['отброшено']['байт']}.\n" + "".join(з + "\n" for з in итог["заметки"])).encode())
    return строки
