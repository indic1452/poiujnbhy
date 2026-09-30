"""Таблицы GFP (ITU-T G.7041/Y.1303) — собраны программой из открытых источников.

Не править руками: файл пишет сценарий сборки, проверяя значения (assert).

* PTI, EXI, PLI, UPI данных клиента и UPI кадров управления клиентом (CMF), привязки
  UPI → разборщик — Wireshark ``epan/dissectors/packet-gfp.c`` (GitLab, master
  f9d091314c916d9ce8f4e99987bb3207be89b880; SHA-256 файла ba4dccbd2b282ce8…):
  gfp_pti_vals, gfp_exi_vals, gfp_pli_rvals, gfp_upi_data_rvals,
  gfp_upi_management_rvals, proto_reg_handoff_gfp. Привязки UPI 9 → mp2t и 12 → mpls
  не перенесены: они противоречат таблице того же файла (9 — прозрачный DVB ASI,
  12 — асинхронный прозрачный Fibre Channel).
* Номера LINKTYPE — tcpdump.org, linktypes.html (SHA-256 0f5fe9b909a81556…): 170 и 171
  там названы LINKTYPE_GPF_T/LINKTYPE_GPF_F (опечатка libpcap), 219 — LINKTYPE_MPLS.
* Русские названия — перевод английских из той же таблицы (каждое переведено).
"""

#: PTI: значение → (имя в источнике, по-русски).
PTI = {0: ('User Data', 'данные клиента'), 4: ('Client Management', 'управление клиентом (CMF)'), 5: ('Management Communications', 'управляющая связь')}
#: EXI: значение → (имя в источнике, по-русски).
EXI = {0: ('Null Extension Header', 'нулевой (нет)'), 1: ('Linear Frame', 'линейный (CID, запас, eHEC)'), 2: ('Ring Frame', 'кольцевой (не определён: «для дальнейшего изучения»)')}
#: PLI: (от, до, имя в источнике, по-русски).
PLI = (
    (0, 0, 'Idle Frame', 'пустой кадр'),
    (1, 3, 'Control Frame (Reserved)', 'служебный кадр (резерв)'),
    (4, 65535, 'Client Frame', 'кадр клиента'),
)
#: UPI кадров данных клиента (PTI 000; им же пользуется PTI 101): (от, до, имя, по-русски).
UPI_ДАННЫЕ = (
    (0, 0, 'Reserved and not available', 'резерв, не используется'),
    (1, 1, 'Frame-Mapped Ethernet', 'Ethernet (кадровое отображение)'),
    (2, 2, 'Frame-Mapped PPP', 'PPP (кадровое отображение)'),
    (3, 3, 'Transparent Fibre Channel', 'Fibre Channel (прозрачное отображение)'),
    (4, 4, 'Transparent FICON', 'FICON (прозрачное отображение)'),
    (5, 5, 'Transparent ESCON', 'ESCON (прозрачное отображение)'),
    (6, 6, 'Transparent Gbit Ethernet', 'Gigabit Ethernet (прозрачное отображение)'),
    (7, 7, 'Reserved', 'резерв'),
    (8, 8, 'Frame-Mapped Multiple Access Protocol over SDH (MAPOS)', 'MAPOS (кадровое отображение)'),
    (9, 9, 'Transparent DVB ASI', 'DVB ASI (прозрачное отображение)'),
    (10, 10, 'Frame-Mapped IEEE 802.17 Resilient Packet Ring', 'IEEE 802.17 RPR (кадровое отображение)'),
    (11, 11, 'Frame-Mapped Fibre Channel FC-BBW', 'Fibre Channel FC-BBW (кадровое отображение)'),
    (12, 12, 'Asynchronous Transparent Fibre Channel', 'Fibre Channel (асинхронное прозрачное отображение)'),
    (13, 13, 'Frame-Mapped MPLS', 'MPLS (кадровое отображение)'),
    (14, 14, 'Frame-Mapped MPLS (Multicast) [Deprecated]', 'MPLS групповой (кадровое отображение, отменён)'),
    (15, 15, 'Frame-Mapped OSI network layer protocols (IS-IS, ES-IS, CLNP)', 'сетевые протоколы OSI: IS-IS, ES-IS, CLNP (кадровое отображение)'),
    (16, 16, 'Frame-Mapped IPv4', 'IPv4 (кадровое отображение)'),
    (17, 17, 'Frame-Mapped IPv6', 'IPv6 (кадровое отображение)'),
    (18, 18, 'Frame-Mapped DVB-ASI', 'DVB-ASI (кадровое отображение)'),
    (19, 19, 'Frame-Mapped 64B/66B encoded Ethernet, including frame preamble', 'Ethernet 64B/66B с преамбулой (кадровое отображение)'),
    (20, 20, 'Frame-Mapped 64B/66B encoded Ethernet ordered set information', 'Ethernet 64B/66B, упорядоченные наборы (кадровое отображение)'),
    (21, 21, 'Transparent transcoded FC-1200', 'FC-1200 перекодированный (прозрачное отображение)'),
    (22, 22, 'Precision Time Protocol message', 'сообщение PTP (точное время)'),
    (23, 23, 'Synchronization status message', 'сообщение состояния синхронизации (SSM)'),
    (24, 239, 'Reserved for future standardization', 'резерв для будущей стандартизации'),
    (240, 252, 'Reserved for proprietary use', 'для нужд производителей'),
    (253, 253, 'Reserved for proprietary use, formerly Frame-Mapped 64B/66B encoded Ethernet, including frame preamble', 'для нужд производителей (прежде — Ethernet 64B/66B с преамбулой)'),
    (254, 254, 'Reserved for proprietary use, formerly Frame-Mapped 64B/66B encoded Ethernet ordered set information', 'для нужд производителей (прежде — Ethernet 64B/66B, упорядоченные наборы)'),
    (255, 255, 'Reserved and not available', 'резерв, не используется'),
)
#: UPI кадров управления клиентом (PTI 100, CMF): (от, до, имя, по-русски).
UPI_УПРАВЛЕНИЕ = (
    (0, 0, 'Reserved and not available', 'резерв, не используется'),
    (1, 1, 'Client Signal Fail (Loss of Client Signal)', 'отказ сигнала клиента: потеря сигнала (CSF)'),
    (2, 2, 'Client Signal Fail (Loss of Character Synchronisation)', 'отказ сигнала клиента: потеря символьной синхронизации (CSF)'),
    (3, 3, 'Defect Clear Indication (DCI)', 'снятие признака дефекта (DCI)'),
    (4, 4, 'Forward Defect Indication (FDI)', 'признак дефекта вперёд (FDI)'),
    (5, 5, 'Reverse Defect Indication (RDI)', 'признак дефекта назад (RDI)'),
    (6, 223, 'Reserved for future use', 'резерв'),
    (224, 254, 'Reserved for proprietary use', 'для нужд производителей'),
    (255, 255, 'Reserved and not available', 'резерв, не используется'),
)
#: UPI → LINKTYPE pcap для кадров клиента (по привязкам разборщиков Wireshark).
UPI_LINKTYPE = {1: 1, 2: 9, 13: 219, 16: 228, 17: 229}
#: UPI прозрачного отображения (GFP-T) — по названию в таблице.
UPI_ПРОЗРАЧНЫЕ = (3, 4, 5, 6, 9, 12, 21)
LINKTYPE_GFP_T = 170
LINKTYPE_GFP_F = 171
LINKTYPE_MPLS = 219
