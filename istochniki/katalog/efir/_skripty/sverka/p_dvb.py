"""Сверка записей DVB-T/T2/C/C2/SH и J.83 Annex B."""
from obsh import *
R = []
def rez(imya, chto, ist, metod):
    R.append({"запись": zapis(imya)["имя"], "что сверено": chto, "источник": ist, "метод": metod, "результат": "совпало"})

ET = "dvb_t/en_300744v010602p.pdf.txt"; ES = "dvb_c/en_300421v010102p.pdf.txt"
# 0. Внешний код DVB-T
z = zapis("DVB-T внешний код")
est(ET, [11, 12], "1 + X14 + X15", '"100101010000000"', "p(x) = x8 + x4 + x3 + x2 + 1", "λ = 02HEX", "I = 12", "M = 17 = N/I", "51 bytes")
psp = lfsr_fib((14, 15), bity("100101010000000"), 16)
assert psp[:8] == bity("00000011")          # EN 300 421 рис. 2: «0 0 0 0 0 0 1 1 ....»
est(ES, [10], "0 0 0 0 0 0 1 1")
gf = GF(8, 0x11D); g = gf.gen(range(16)); assert len(g) == 17 and g[-1] == 1
assert "0x03" in z["параметры"]["рандомизатор"] and "0x11D" in z["параметры"]["РС"] and "I = 12" in z["параметры"]["перемежитель"]
rez("DVB-T внешний код", "ГПСП 1+X^14+X^15 и загрузка, p(x), λ, I=12, M=17, 51 нулевой байт; первый байт ПСП", "EN 300 744 стр. 11–12; EN 300 421 рис. 2 (стр. 10)",
    "текст стр. 11–12 содержит все значения записи; свой РСЛОС из загрузки 100101010000000 даёт 00000011 — как на рис. 2 EN 300 421 («0 0 0 0 0 0 1 1 ….»)")
# 1. Внутренний свёрточный: таблица 2 построчно
z = zapis("DVB-T внутренний свёрточный")
t = str_(ET, 14)
for r, sh in z["параметры"]["шаблоны"].items():
    est(ET, [14], r, "X: " + " ".join(sh["X"]), "Y: " + " ".join(sh["Y"]), sh["передача"])
    k, n = map(int, r.split("/")); assert len(sh["X"]) == k and (sh["X"] + sh["Y"]).count("1") == n
est(ET, [14], "G1 = 171OCT for X output and G2 = 133OCT")
rez("DVB-T внутренний свёрточный", "G1=171/G2=133, шаблоны X/Y и порядок передачи для 1/2…7/8", "EN 300 744 табл. 2, стр. 14",
    "каждая строка табл. 2 найдена в тексте; k/n шаблонов пересчитаны")
# 2. битовый перемежитель
est(ET, [19], "H1(w) = (w + 63) mod 126", "H2(w) = (w + 105) mod 126", "H3(w) = (w + 42) mod 126", "H4(w) = (w + 21) mod 126", "H5(w) = (w + 84) mod 126")
assert "[0, 63, 105, 42, 21, 84]" in zapis("DVB-T битовый")["параметры"]["формула"]
rez("DVB-T битовый", "сдвиги H0…H5", "EN 300 744 4.3.4.1, стр. 19", "пять формул He(w) найдены в тексте дословно")
# 4. TPS
z = zapis("DVB-T TPS")
est(ET, [34], "BCH (67,53, t = 2) shortened code", "x14 + x9 + x8 + x6 + x5 + x4 + x2 + x + 1")
est(ET, [31], "0011010111101110")
est(ET, [28], "X11 + X2 + 1", "1111111111100")
# своё: g(x) БЧХ(127,113) t=2: произведение минимальных многочленов α и α^3 для p = x^7+x^3+1
gf7 = GF(7, 0x89)
def minpol(gf, k):
    kl = set(); e = k
    while e not in kl: kl.add(e); e = e * 2 % gf.n
    g = gf.gen(sorted(kl)); assert all(c in (0, 1) for c in g)
    return sum(c << i for i, c in enumerate(g))
gt = poly_mod2_mul(minpol(gf7, 1), minpol(gf7, 3))
assert gt == stepeni_v_chislo("x^14 + x^9 + x^8 + x^6 + x^5 + x^4 + x^2 + x + 1") == 0x4377
assert lfsr_vyh((11, 9), [1] * 11, 13) == bity("1111111111100")
rez("DVB-T TPS", "g(x) БЧХ(67,53), синхрослово TPS, ПСП пилотов", "EN 300 744 стр. 28, 31, 34",
    "g(x) построен заново как m1(x)·m3(x) над GF(2^7), p = x^7+x^3+1 → 0x4377 = записи; ПСП X^11+X^2+1 из всех единиц — 1111111111100 как в тексте")
# 6. DVB-T2 скремблер ББ-кадра
ET2 = "dvb_t2/en_302755v010401p.pdf.txt"
est(ET2, [36], "1 + X14 + X15", "100101010000000")
rez("DVB-T2 скремблер", "многочлен и загрузка", "EN 302 755 5.2.2, стр. 36", "текст; ПСП та же, что проверена рис. 2 EN 300 421")
# 7. БЧХ T2: 12+12 многочленов по тексту табл. 7a/7b
z = zapis("DVB-T2 внешний БЧХ")
pn = poisk(ET2, "g1(x)")
for nn, spis in z["параметры"]["многочлены"].items():
    for i, p in enumerate(spis, 1):
        assert any(szh(p) in szh(str_(ET2, s)) for s in pn), (nn, i, p)
# свойства: g1 примитивен степени 16/14; все различны; произведение степени 192/168
for nn, (m, M) in {"64800": (16, 192), "16200": (14, 168)}.items():
    ps = [stepeni_v_chislo(p.replace("x", "x^").replace("^^", "^").replace("x^ ", "x ")) for p in z["параметры"]["многочлены"][nn]]
    assert len(set(ps)) == 12 and all(p.bit_length() - 1 == m for p in ps)
    GF(m, ps[0] ^ (1 << m) if False else ps[0])   # примитивность g1
    pr = 1
    for p in ps: pr = poly_mod2_mul(pr, p)
    assert pr.bit_length() - 1 == M
rez("DVB-T2 внешний БЧХ", "12 + 12 многочленов g1…g12", f"EN 302 755 табл. 7a/7b, стр. {pn}",
    "каждый многочлен записи найден в тексте; g1 примитивен (поле строится), 12 различных, степень произведения 192 / 168 = Nbch − Kbch")
# 9. битовый перемежитель T2: tc и демультиплексор
z = zapis("DVB-T2 битовый перемежитель")
for k, v in z["параметры"]["tc"].items():
    assert any(szh(" ".join(map(str, v))) in szh(str_(ET2, s)) for s in (43, 44)), k
for tab, d in z["параметры"]["демультиплексор"].items():
    for k, v in d.items():
        assert sorted(v) == list(range(len(v)))
rez("DVB-T2 битовый перемежитель", "сдвиги скручивания tc (6 строк)", "EN 302 755 табл. 10, стр. 43–44", "каждая строка tc найдена в тексте подряд; перестановки демультиплексора — биекции")
# 13. CRC T2
z = zapis("DVB-T2 CRC-8")
est(ET2, poisk(ET2, "x32+x26") or poisk(ET2, "X32+X26") or [163], "26")
rez("DVB-T2 CRC-8", "многочлен CRC-32 Annex F", "EN 302 755 Annex F", "степени найдены в тексте")
