"""Независимый кодер GFP-F (G.7041) рецензента — эталон для тестов.

Перенесён из независимой проверки без изменений логики: по Wireshark packet-gfp.c, RFC 2823 и
открытым выдержкам; все CRC побитные; код reportgen не используется. Здесь же — сценарии
(смесь кадров и ожидаемый результат приёмника) и потоки без GFP (случайные, HDLC, ATM,
SDH STM-1), на которых GFP находиться не должен.

Файл младшим битом вперёд (file_bytes(..., lsb_first=True)): биты линии развернуты внутри
каждого байта файла — первый бит линии в младшем разряде первого байта.
"""
import random
import struct

# ---------- CRC ----------
def crc16_hec(data: bytes) -> int:
    """CRC-16 x^16+x^12+x^5+1, начальное 0, старшим битом вперёд, без выходного XOR."""
    r = 0
    for b in data:
        for i in range(7, -1, -1):
            bit = (b >> i) & 1
            top = (r >> 15) & 1
            r = (r << 1) & 0xFFFF
            if top ^ bit:
                r ^= 0x1021
    return r


def crc32_bzip2(data: bytes) -> int:
    r = 0xFFFFFFFF
    for b in data:
        for i in range(7, -1, -1):
            bit = (b >> i) & 1
            top = (r >> 31) & 1
            r = (r << 1) & 0xFFFFFFFF
            if top ^ bit:
                r ^= 0x04C11DB7
    return r ^ 0xFFFFFFFF


def crc32_eth(data: bytes) -> int:
    r = 0xFFFFFFFF
    for b in data:
        r ^= b
        for _ in range(8):
            r = (r >> 1) ^ (0xEDB88320 if r & 1 else 0)
    return r ^ 0xFFFFFFFF


assert crc32_bzip2(b"123456789") == 0xFC891918
assert crc32_eth(b"123456789") == 0xCBF43926
assert crc16_hec(b"123456789") == 0x31C3   # CRC-16/XMODEM


# ---------- клиенты ----------
def ip_csum(h: bytes) -> int:
    s = sum(struct.unpack("!%dH" % (len(h) // 2), h))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def ipv4_udp(rng: random.Random, n: int) -> bytes:
    data = bytes(rng.randrange(256) for _ in range(n))
    udp = struct.pack("!HHHH", rng.randrange(1024, 65535), 5000 + rng.randrange(10), 8 + len(data), 0) + data
    src = bytes([10, 0, rng.randrange(256), rng.randrange(1, 255)])
    dst = bytes([192, 168, rng.randrange(256), rng.randrange(1, 255)])
    h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), rng.randrange(65536), 0x4000, 64, 17, 0, src, dst)
    h = h[:10] + struct.pack("!H", ip_csum(h)) + h[12:]
    return h + udp


def ethernet(rng: random.Random, n: int, fcs=True) -> bytes:
    dst = bytes([0x00, 0x1B, 0x21] + [rng.randrange(256) for _ in range(3)])
    src = bytes([0x00, 0x0C, 0x29] + [rng.randrange(256) for _ in range(3)])
    body = dst + src + b"\x08\x00" + ipv4_udp(rng, n)
    if len(body) < 60:
        body += bytes(60 - len(body))
    return body + (struct.pack("<I", crc32_eth(body)) if fcs else b"")


# ---------- кадр GFP ----------
def payload_area(pti: int, upi: int, client: bytes, *, pfcs=False, cid=None) -> bytes:
    exi = 1 if cid is not None else 0
    t = bytes([(pti << 5) | ((1 if pfcs else 0) << 4) | exi, upi])
    area = t + struct.pack("!H", crc16_hec(t))
    if cid is not None:
        e = bytes([cid, 0])
        area += e + struct.pack("!H", crc16_hec(e))
    area += client
    if pfcs:
        area += struct.pack("!I", crc32_bzip2(client))
    return area


class Stream:
    """Набор кадров до скремблирования и маски: список (pli, payload_area|b'')."""

    def __init__(self, mask: int = 0xB6AB31E0, scramble: bool = True, x43_seed: int | None = None,
                 invert_payload: bool = False):
        self.mask = mask
        self.scramble = scramble
        self.invert_payload = invert_payload
        self.frames: list[dict] = []
        self.x43_seed = x43_seed

    def idle(self, n=1):
        for _ in range(n):
            self.frames.append(dict(kind="idle", area=b""))

    def data(self, client: bytes, upi=1, **kw):
        self.frames.append(dict(kind="data", area=payload_area(0, upi, client, **kw), client=client, upi=upi,
                                cid=kw.get("cid"), pfcs=kw.get("pfcs", False)))

    def cmf(self, upi=1):
        self.frames.append(dict(kind="cmf", area=payload_area(4, upi, b""), client=b"", upi=upi))

    def build(self) -> tuple[bytes, list[int]]:
        """Байты потока; и начало каждого кадра (байт)."""
        out = bytearray()
        starts = []
        # x^43+1 самосинхронизирующийся: y[n] = x[n] ^ y[n-43]; начальное состояние случайное
        rng = random.Random(self.x43_seed if self.x43_seed is not None else 1)
        state = [rng.randrange(2) for _ in range(43)]  # последние 43 выходных бита
        pos = 0
        mhi, mlo = (self.mask >> 16) & 0xFFFF, self.mask & 0xFFFF
        for f in self.frames:
            area = f["area"]
            pli = len(area)
            core = struct.pack("!HH", pli ^ mhi, crc16_hec(struct.pack("!H", pli)) ^ mlo)
            starts.append(len(out))
            out += core
            a = bytearray(area)
            if self.invert_payload:
                a = bytearray(b ^ 0xFF for b in a)
            if self.scramble:
                for i in range(len(a)):
                    b = a[i]
                    y = 0
                    for k in range(7, -1, -1):
                        x = (b >> k) & 1
                        o = x ^ state[pos % 43]
                        state[pos % 43] = o
                        pos += 1
                        y = (y << 1) | o
                    a[i] = y
            out += a
        return bytes(out), starts


# ---------- преобразования бит ----------
def to_bits_msb(data: bytes) -> list[int]:
    return [(b >> (7 - i)) & 1 for b in data for i in range(8)]


def bits_to_bytes(bits: list[int], lsb_first=False) -> bytes:
    bits = bits + [0] * (-len(bits) % 8)
    out = bytearray()
    for i in range(0, len(bits), 8):
        v = 0
        for k in range(8):
            if lsb_first:
                v |= bits[i + k] << k
            else:
                v = (v << 1) | bits[i + k]
        out.append(v)
    return bytes(out)


def file_bytes(stream: bytes, *, shift=0, lsb_first=False, invert=False, seed=0) -> bytes:
    """Линейные биты (старшим вперёд) -> сдвиг на shift случайных бит -> файл (порядок бит в байте)."""
    rng = random.Random(seed)
    bits = [rng.randrange(2) for _ in range(shift)] + to_bits_msb(stream)
    if invert:
        bits = [b ^ 1 for b in bits]
    return bits_to_bytes(bits, lsb_first)


def flip_bits(data: bytes, positions) -> bytes:
    a = bytearray(data)
    for p in positions:
        a[p // 8] ^= 0x80 >> (p % 8)
    return bytes(a)


# ---------- сценарии ----------


def smes(rng, st: Stream, n_data=120, cid=None, pfcs_share=0.5, cmf_every=40, idle_max=3,
         upi_mix=(1,), eth=True):
    """Типичная смесь: пустые, Ethernet/IP, CMF."""
    st.idle(3)
    for i in range(n_data):
        st.idle(rng.randrange(idle_max + 1))
        if cmf_every and i % cmf_every == cmf_every - 1:
            st.cmf(upi=rng.choice([1, 2]))
        upi = rng.choice(upi_mix)
        if upi == 1:
            client = ethernet(rng, rng.randrange(20, 400))
        else:
            client = ipv4_udp(rng, rng.randrange(20, 400))
        st.data(client, upi=upi, pfcs=rng.random() < pfcs_share,
                cid=(cid if cid is None or isinstance(cid, int) else rng.choice(cid)))
    st.idle(2)


def ozhidanie(st: Stream, lost: set[int] = frozenset()):
    """Что должен вернуть приёмник: номера кадров (в порядке st.frames) и клиенты.

    lost — номера кадров, на заголовке которых потеря синхронизма (многобитовая ошибка).
    Прогрев x43: после захвата (начало и каждая потеря) кадр, чей заголовок типа начинается
    раньше 43-го бита нагрузки от захвата, не разбирается."""
    clients = []
    frames_ok = 0
    idle = cmf = data = 0
    payload_bits = 0   # от последнего захвата
    for i, f in enumerate(st.frames):
        if i in lost:
            payload_bits = 0
            continue
        frames_ok += 1
        a = len(f["area"])
        if f["kind"] == "idle":
            idle += 1
            continue
        warm = st.scramble and payload_bits < 43
        payload_bits += 8 * a
        if warm:
            continue
        if f["kind"] == "cmf":
            cmf += 1
        else:
            data += 1
            clients.append(f["client"])
    return dict(kadrov=frames_ok, pustyh=idle, cmf=cmf, dannyh=data, klienty=clients)


def ozhidanie_s(st, lost=frozenset(), start_frame=0):
    """Как ozhidanie, но приёмник видит поток только с кадра start_frame."""
    import copy
    st2 = copy.copy(st)
    st2.frames = st.frames[start_frame:]
    return ozhidanie(st2, {i - start_frame for i in lost if i >= start_frame})


# ---------- потоки без GFP ----------

def crc16_x25(d):
    r = 0xFFFF
    for b in d:
        r ^= b
        for _ in range(8):
            r = (r >> 1) ^ (0x8408 if r & 1 else 0)
    return r ^ 0xFFFF

def hdlc(rng, n_bytes=1 << 20):
    bits = []
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    while len(bits) < 8 * n_bytes:
        for _ in range(rng.randrange(1, 6)):
            bits += flag
        payload = ethernet(rng, rng.randrange(20, 300), fcs=False) if rng.random() < .5 else bytes(rng.randrange(256) for _ in range(rng.randrange(4, 200)))
        fr = payload + crc16_x25(payload).to_bytes(2, "little")
        ones = 0
        for b in fr:
            for k in range(8):   # младшим битом вперёд
                x = (b >> k) & 1
                bits.append(x)
                ones = ones + 1 if x else 0
                if ones == 5:
                    bits.append(0); ones = 0
        bits += flag
    return bits_to_bytes(bits[:8 * n_bytes])

def hec8(h):
    r = 0
    for b in h:
        r ^= b
        for _ in range(8):
            r = ((r << 1) ^ 0x07) & 0xFF if r & 0x80 else (r << 1) & 0xFF
    return r ^ 0x55

def atm(rng, n_bytes=1 << 20):
    out = bytearray()
    state = [rng.randrange(2) for _ in range(43)]; pos = 0
    while len(out) < n_bytes:
        if rng.random() < 0.3:
            h = bytes([0, 0, 0, 1]); pl = bytes([0x6A] * 48)
        else:
            vpi, vci = rng.choice([(0, 32), (1, 100), (2, 5)])
            h = bytes([(vpi >> 4) & 0xF, ((vpi & 0xF) << 4) | (vci >> 12), (vci >> 4) & 0xFF, (vci & 0xF) << 4])
            pl = bytes(rng.randrange(256) for _ in range(48))
        a = bytearray(pl)
        for i in range(48):
            y = 0
            for k in range(7, -1, -1):
                o = ((a[i] >> k) & 1) ^ state[pos % 43]; state[pos % 43] = o; pos += 1; y = (y << 1) | o
            a[i] = y
        out += h + bytes([hec8(h)]) + a
    return bytes(out[:n_bytes])

def sdh(rng, n_bytes=1 << 20):
    # STM-1: 9 строк × 270 байт, A1A2 без скремблера, остальное — кадровый скремблер x^7+x^6+1 (начало 1111111)
    seq = []
    s = 0x7F
    for _ in range(2430):
        b = 0
        for _ in range(8):
            bit = (s >> 6) & 1
            b = (b << 1) | bit
            fb = ((s >> 6) ^ (s >> 5)) & 1
            s = ((s << 1) | fb) & 0x7F
        seq.append(b)
    out = bytearray()
    while len(out) < n_bytes:
        fr = bytearray(rng.randrange(256) for _ in range(2430))
        # RSOH/MSOH: много постоянных байт
        for row in range(9):
            for col in range(9):
                fr[row * 270 + col] = 0x00 if row > 2 else fr[row * 270 + col]
        fr[0:9] = bytes([0xF6, 0xF6, 0xF6, 0x28, 0x28, 0x28, 0x01, 0xAA, 0xAA])
        # VC-4 со «незанятой» нагрузкой в каждом втором кадре (нули)
        if rng.random() < 0.5:
            for row in range(9):
                fr[row * 270 + 10: row * 270 + 270] = bytes(260)
        sc = bytearray(fr)
        for i in range(9, 2430):
            sc[i] ^= seq[i]
        out += sc
    return bytes(out[:n_bytes])

def sluch(rng, n_bytes=1 << 20):
    return rng.randbytes(n_bytes)

