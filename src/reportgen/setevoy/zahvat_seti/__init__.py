"""Захват с сети: сетевые карты сервера, приём UDP на порт, захват кадров с карты, обработка.

Модули:

* ``karty`` — перечень карт (Linux: sysfs и getifaddrs; Windows: GetAdaptersAddresses);
* ``pcap_bib`` — libpcap/Npcap через ctypes;
* ``istochniki`` — источники кадров: приём UDP, libpcap/Npcap, AF_PACKET, SIO_RCVALL;
* ``parametry`` — проверка параметров из запроса и фильтр (он же — выражение BPF);
* ``zapis`` — запись pcapng и синтез заголовков Ethernet/IP/UDP;
* ``menedzher`` — захваты в фоне: счётчики, пределы, остановка, хранение;
* ``obrabotka`` — после остановки: порты UDP захвата и нагрузка порта одним потоком.
"""
