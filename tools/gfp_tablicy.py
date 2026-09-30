"""Сборка таблиц GFP (G.7041) из открытых источников — программой, с проверками.

Источники (папка gfp-istochniki, контрольные суммы — SHA256SUMS.txt):
  wireshark/epan_dissectors_packet-gfp.c  — PTI, EXI, UPI (данные клиента и CMF), PLI,
                                            привязки UPI → разборщик;
  tcpdump/tcpdump-htdocs_linktypes.html    — номера LINKTYPE.
Запуск: python3 tools/gfp_tablicy.py src/reportgen/potok/gfp_tablicy.py ПАПКА_ИСТОЧНИКОВ
(в папке — wireshark/epan_dissectors_packet-gfp.c и tcpdump/tcpdump-htdocs_linktypes.html;
их SHA-256 сверяются).
"""
import hashlib
import html
import re
import sys
from pathlib import Path

ИСТ = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).with_name("gfp-istochniki")
ВЫХОД = Path(sys.argv[1])

gfp_c = (ИСТ / "wireshark/epan_dissectors_packet-gfp.c").read_text()
lt_html = (ИСТ / "tcpdump/tcpdump-htdocs_linktypes.html").read_text()
sha_gfp = hashlib.sha256((ИСТ / "wireshark/epan_dissectors_packet-gfp.c").read_bytes()).hexdigest()
sha_lt = hashlib.sha256((ИСТ / "tcpdump/tcpdump-htdocs_linktypes.html").read_bytes()).hexdigest()
assert sha_gfp == "ba4dccbd2b282ce8451d73f9d0d18f2e4717a669aeef67b81d7c4c75f86444d7"
assert sha_lt == "0f5fe9b909a81556009a007b8c669fe019d16ddd999170acb0aaae6d917836b3"

определения = {м.group(1): int(м.group(2)) for м in re.finditer(r"#define\s+(GFP_\w+)\s+(\d+)", gfp_c)}


def блок(имя):
    м = re.search(r"static const (?:value|range)_string " + имя + r"\[\] = \{(.*?)\n\};", gfp_c, re.S)
    assert м, имя
    return м.group(1)


def диапазоны(имя):
    итог = []
    for а, б, текст in re.findall(r"\{\s*(\w+),\s*(\w+),\s*\"([^\"]*)\"\s*\}", блок(имя)):
        а = int(а) if а.isdigit() else {"UINT16_MAX": 0xFFFF}[а]
        б = int(б) if б.isdigit() else {"UINT16_MAX": 0xFFFF}[б]
        итог.append((а, б, текст))
    return итог


def значения(имя):
    итог = {}
    for ключ, текст in re.findall(r"\{\s*(\w+),\s*\"([^\"]*)\"\s*\}", блок(имя)):
        итог[определения[ключ] if ключ in определения else int(ключ)] = текст
    return итог


PTI = значения("gfp_pti_vals")
EXI = значения("gfp_exi_vals")
PLI = диапазоны("gfp_pli_rvals")
UPI_Д = диапазоны("gfp_upi_data_rvals")
UPI_У = диапазоны("gfp_upi_management_rvals")

# Проверки: значения из файла совпадают с тем, что сказано в сводке источников (строки 113-172).
assert PTI == {0: "User Data", 4: "Client Management", 5: "Management Communications"}, PTI
assert EXI == {0: "Null Extension Header", 1: "Linear Frame", 2: "Ring Frame"}, EXI
assert PLI == [(0, 0, "Idle Frame"), (1, 3, "Control Frame (Reserved)"), (4, 0xFFFF, "Client Frame")], PLI
for табл, всего in ((UPI_Д, 29), (UPI_У, 9)):
    assert len(табл) == всего, len(табл)
    # Диапазоны покрывают 0…255 без дыр и наложений.
    место = 0
    for а, б, _ in табл:
        assert а == место and б >= а, (а, б)
        место = б + 1
    assert место == 256
assert UPI_Д[1] == (1, 1, "Frame-Mapped Ethernet") and UPI_Д[2] == (2, 2, "Frame-Mapped PPP")
assert UPI_Д[9] == (9, 9, "Transparent DVB ASI") and UPI_Д[12] == (12, 12, "Asynchronous Transparent Fibre Channel")
assert UPI_Д[13][2] == "Frame-Mapped MPLS" and UPI_Д[16][2] == "Frame-Mapped IPv4" and UPI_Д[17][2] == "Frame-Mapped IPv6"
assert UPI_У[1] == (1, 1, "Client Signal Fail (Loss of Client Signal)")
assert UPI_У[3] == (3, 3, "Defect Clear Indication (DCI)")

# Привязки UPI → разборщик Wireshark (proto_reg_handoff_gfp).
привязки = {int(н): имя for н, имя in re.findall(r'dissector_add_uint\("gfp\.upi",\s*(\d+),\s*find_dissector\("(\w+)"\)\)', gfp_c)}
assert привязки == {1: "eth_withfcs", 2: "ppp_hdlc", 9: "mp2t", 12: "mpls", 13: "mpls", 16: "ip", 17: "ipv6"}, привязки
# 9 и 12 у Wireshark противоречат его же таблице: 9 — прозрачный DVB ASI (не кадры TS),
# 12 — асинхронный прозрачный Fibre Channel (не MPLS). Не переносим.
del привязки[9], привязки[12]

# Номера LINKTYPE по tcpdump.org.
текст_lt = html.unescape(re.sub(r"<[^>]+>", " ", lt_html))
def linktype(имя):
    м = re.search(r"\b" + имя + r"\s+(\d+)\s+DLT_", текст_lt)
    assert м, имя
    return int(м.group(1))
LT = {имя: linktype(имя) for имя in ("LINKTYPE_ETHERNET", "LINKTYPE_PPP", "LINKTYPE_MPLS",
                                     "LINKTYPE_IPV4", "LINKTYPE_IPV6", "LINKTYPE_GPF_T", "LINKTYPE_GPF_F")}
assert LT == {"LINKTYPE_ETHERNET": 1, "LINKTYPE_PPP": 9, "LINKTYPE_MPLS": 219, "LINKTYPE_IPV4": 228,
              "LINKTYPE_IPV6": 229, "LINKTYPE_GPF_T": 170, "LINKTYPE_GPF_F": 171}, LT
РАЗБОРЩИК_В_LT = {"eth_withfcs": LT["LINKTYPE_ETHERNET"], "ppp_hdlc": LT["LINKTYPE_PPP"],
                  "mpls": LT["LINKTYPE_MPLS"], "ip": LT["LINKTYPE_IPV4"], "ipv6": LT["LINKTYPE_IPV6"]}
UPI_LT = {upi: РАЗБОРЩИК_В_LT[р] for upi, р in привязки.items()}

# Перевод названий (данные — из источника; здесь только перевод слов).
ПЕРЕВОД = {
    "User Data": "данные клиента", "Client Management": "управление клиентом (CMF)",
    "Management Communications": "управляющая связь",
    "Null Extension Header": "нулевой (нет)", "Linear Frame": "линейный (CID, запас, eHEC)",
    "Ring Frame": "кольцевой (не определён: «для дальнейшего изучения»)",
    "Idle Frame": "пустой кадр", "Control Frame (Reserved)": "служебный кадр (резерв)",
    "Client Frame": "кадр клиента",
    "Reserved and not available": "резерв, не используется",
    "Frame-Mapped Ethernet": "Ethernet (кадровое отображение)",
    "Frame-Mapped PPP": "PPP (кадровое отображение)",
    "Transparent Fibre Channel": "Fibre Channel (прозрачное отображение)",
    "Transparent FICON": "FICON (прозрачное отображение)",
    "Transparent ESCON": "ESCON (прозрачное отображение)",
    "Transparent Gbit Ethernet": "Gigabit Ethernet (прозрачное отображение)",
    "Reserved": "резерв",
    "Frame-Mapped Multiple Access Protocol over SDH (MAPOS)": "MAPOS (кадровое отображение)",
    "Transparent DVB ASI": "DVB ASI (прозрачное отображение)",
    "Frame-Mapped IEEE 802.17 Resilient Packet Ring": "IEEE 802.17 RPR (кадровое отображение)",
    "Frame-Mapped Fibre Channel FC-BBW": "Fibre Channel FC-BBW (кадровое отображение)",
    "Asynchronous Transparent Fibre Channel": "Fibre Channel (асинхронное прозрачное отображение)",
    "Frame-Mapped MPLS": "MPLS (кадровое отображение)",
    "Frame-Mapped MPLS (Multicast) [Deprecated]": "MPLS групповой (кадровое отображение, отменён)",
    "Frame-Mapped OSI network layer protocols (IS-IS, ES-IS, CLNP)":
        "сетевые протоколы OSI: IS-IS, ES-IS, CLNP (кадровое отображение)",
    "Frame-Mapped IPv4": "IPv4 (кадровое отображение)",
    "Frame-Mapped IPv6": "IPv6 (кадровое отображение)",
    "Frame-Mapped DVB-ASI": "DVB-ASI (кадровое отображение)",
    "Frame-Mapped 64B/66B encoded Ethernet, including frame preamble":
        "Ethernet 64B/66B с преамбулой (кадровое отображение)",
    "Frame-Mapped 64B/66B encoded Ethernet ordered set information":
        "Ethernet 64B/66B, упорядоченные наборы (кадровое отображение)",
    "Transparent transcoded FC-1200": "FC-1200 перекодированный (прозрачное отображение)",
    "Precision Time Protocol message": "сообщение PTP (точное время)",
    "Synchronization status message": "сообщение состояния синхронизации (SSM)",
    "Reserved for future standardization": "резерв для будущей стандартизации",
    "Reserved for proprietary use": "для нужд производителей",
    "Reserved for proprietary use, formerly Frame-Mapped 64B/66B encoded Ethernet, including frame preamble":
        "для нужд производителей (прежде — Ethernet 64B/66B с преамбулой)",
    "Reserved for proprietary use, formerly Frame-Mapped 64B/66B encoded Ethernet ordered set information":
        "для нужд производителей (прежде — Ethernet 64B/66B, упорядоченные наборы)",
    "Client Signal Fail (Loss of Client Signal)": "отказ сигнала клиента: потеря сигнала (CSF)",
    "Client Signal Fail (Loss of Character Synchronisation)":
        "отказ сигнала клиента: потеря символьной синхронизации (CSF)",
    "Defect Clear Indication (DCI)": "снятие признака дефекта (DCI)",
    "Forward Defect Indication (FDI)": "признак дефекта вперёд (FDI)",
    "Reverse Defect Indication (RDI)": "признак дефекта назад (RDI)",
    "Reserved for future use": "резерв",
}
все_имена = set(PTI.values()) | set(EXI.values()) | {т for *_, т in PLI + UPI_Д + UPI_У}
assert все_имена <= set(ПЕРЕВОД), sorted(все_имена - set(ПЕРЕВОД))
assert set(ПЕРЕВОД) <= все_имена, sorted(set(ПЕРЕВОД) - все_имена)
# Прозрачное отображение (GFP-T) — по названию в таблице.
ПРОЗРАЧНЫЕ = tuple(а for а, б, т in UPI_Д if а == б and "Transparent" in т)
assert ПРОЗРАЧНЫЕ == (3, 4, 5, 6, 9, 12, 21), ПРОЗРАЧНЫЕ


def запись(табл):
    return "(\n" + "".join(f"    ({а}, {б}, {т!r}, {ПЕРЕВОД[т]!r}),\n" for а, б, т in табл) + ")"


ВЫХОД.write_text(f'''"""Таблицы GFP (ITU-T G.7041/Y.1303) — собраны программой из открытых источников.

Не править руками: файл пишет сценарий сборки, проверяя значения (assert).

* PTI, EXI, PLI, UPI данных клиента и UPI кадров управления клиентом (CMF), привязки
  UPI → разборщик — Wireshark ``epan/dissectors/packet-gfp.c`` (GitLab, master
  f9d091314c916d9ce8f4e99987bb3207be89b880; SHA-256 файла {sha_gfp[:16]}…):
  gfp_pti_vals, gfp_exi_vals, gfp_pli_rvals, gfp_upi_data_rvals,
  gfp_upi_management_rvals, proto_reg_handoff_gfp. Привязки UPI 9 → mp2t и 12 → mpls
  не перенесены: они противоречат таблице того же файла (9 — прозрачный DVB ASI,
  12 — асинхронный прозрачный Fibre Channel).
* Номера LINKTYPE — tcpdump.org, linktypes.html (SHA-256 {sha_lt[:16]}…): 170 и 171
  там названы LINKTYPE_GPF_T/LINKTYPE_GPF_F (опечатка libpcap), 219 — LINKTYPE_MPLS.
* Русские названия — перевод английских из той же таблицы (каждое переведено).
"""

#: PTI: значение → (имя в источнике, по-русски).
PTI = {{{", ".join(f"{к}: ({в!r}, {ПЕРЕВОД[в]!r})" for к, в in PTI.items())}}}
#: EXI: значение → (имя в источнике, по-русски).
EXI = {{{", ".join(f"{к}: ({в!r}, {ПЕРЕВОД[в]!r})" for к, в in EXI.items())}}}
#: PLI: (от, до, имя в источнике, по-русски).
PLI = {запись(PLI)}
#: UPI кадров данных клиента (PTI 000; им же пользуется PTI 101): (от, до, имя, по-русски).
UPI_ДАННЫЕ = {запись(UPI_Д)}
#: UPI кадров управления клиентом (PTI 100, CMF): (от, до, имя, по-русски).
UPI_УПРАВЛЕНИЕ = {запись(UPI_У)}
#: UPI → LINKTYPE pcap для кадров клиента (по привязкам разборщиков Wireshark).
UPI_LINKTYPE = {UPI_LT!r}
#: UPI прозрачного отображения (GFP-T) — по названию в таблице.
UPI_ПРОЗРАЧНЫЕ = {ПРОЗРАЧНЫЕ!r}
LINKTYPE_GFP_T = {LT["LINKTYPE_GPF_T"]}
LINKTYPE_GFP_F = {LT["LINKTYPE_GPF_F"]}
LINKTYPE_MPLS = {LT["LINKTYPE_MPLS"]}
''', encoding="utf-8")
print("записано", ВЫХОД, "UPI данных:", len(UPI_Д), "CMF:", len(UPI_У), "привязки:", UPI_LT)
