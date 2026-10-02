"""Сплошная проверка ссылок каталога: файл существует, страница в пределах PDF, ссылка не указывает
на оглавление; найденные ссылки на оглавление заменяются страницами самого текста (с проверкой
текста на новой странице). Замены — proverka/zameny_istochnikov.json (применяет primenit.py)."""
import json, re
from pv import *
ZAM = []   # {"запись", "файл", "было", "стало", "что"}
vsego = 0; plohie = []
for z in KAT:
    for s in z["источник"]:
        f = s["файл"]; vsego += 1
        if not os.path.exists(os.path.join(KOREN, f)): plohie.append((z["имя"], f, "нет файла")); continue
        if not f.lower().endswith(".pdf"): continue
        for a, b in re.findall(r"стр\. (\d+)(?:[–-](\d+))?", s["страница|строки"]):
            for p in range(int(a), int(b or a) + 1):
                if p > len(doc(f)): plohie.append((z["имя"], f, f"стр. {p} > {len(doc(f))}"))
                elif stranica(f, p).count(".....") >= 5: plohie.append((z["имя"], f, f"стр. {p} — оглавление", s.get("что", "")))
ok("весь каталог", f"ссылки: {vsego} источников — файлы существуют, страницы в пределах PDF", not [p for p in plohie if "оглавление" not in p[2]], str([p for p in plohie if "оглавление" not in p[2]]))
print("ссылок на оглавление:", len(plohie)); [print("  ", p) for p in plohie]

def zamena(imya_ch, f, bylo, stalo, chto, p_new, *obr):
    z = zapis(imya_ch)
    assert any(s["файл"] == f and s["страница|строки"].startswith(bylo) for s in z["источник"]), (imya_ch, f, bylo)
    r = str_ok(z, f, p_new, f"новая страница для «{chto}»", *obr); assert r
    ZAM.append({"запись": z["имя"], "файл": f, "было": bylo, "стало": stalo, "что": chto})
    ispr(z["имя"], f"ссылка «{chto}» указывала на оглавление", f"{f.split('/')[-1]} {bylo}", f"{stalo}", f"на стр. {p_new} — сам текст: {'; '.join(obr)[:120]}")

I = "istochniki/itu/"
zamena("G.975.1 I.2:", I + "T-REC-G.975.1-200402-I.pdf", "стр. 5 ", "стр. 14 (номер страницы PDF)", "I.2", 14, "RS(255,239)/CSOC (n0/k0 = 7/6, J = 8) super FEC code")
zamena("G.975.1 I.9:", I + "T-REC-G.975.1-200402-I.pdf", "стр. 5 ", "стр. 51 (номер страницы PDF)", "снимок risunki/g9751_I9_g_polno.png", 51, "Two interleaved extended BCH(1020,988) super FEC code")
zamena("G.709.1/G.709.5", I + "T-REC-G.709.4-202003-I.pdf", "стр. 5 ", "стр. 13, 17 (номер страницы PDF)", "G.709.4 OTU25/50-RS: FEC-область и Annex A", 17, "Forward error correction for OTU-RS using 10-bit interleaved RS(544,514)")
str_ok(zapis("G.709.1/G.709.5"), I + "T-REC-G.709.4-202003-I.pdf", 13, "G.709.4 9.x: RS(544,514,10) по Annex A", "RS(544,514,10) FEC code shall be computed as specified in Annex A")
zamena("DMT-DSL РС-код", I + "T-REC-G.992.1-199907-I.pdf", "стр. 6 ", "стр. 52 (номер страницы PDF)", "G.992.1 7.6.1", 52, "C(D) = M(D) DR modulo G(D)")
zamena("DMT-DSL РС-код", I + "T-REC-G.992.2-199907-I.pdf", "стр. 5 ", "стр. 30 (номер страницы PDF)", "G.992.2 Reed-Solomon coding", 30, "C(D) = M(D) DR modulo G(D)", "Reed-Solomon coding")
zamena("HomePlug AV / AV2", "istochniki/plc/homeplug_av21_specification_final_public.pdf", "стр. 28 ", "стр. 89 (номер страницы PDF)", "AV2 16/18", 89, "Table 3-8: Rate 16/18 Puncture Pattern")
json.dump(ZAM, open(os.path.join(KOREN, "proverka/zameny_istochnikov.json"), "w"), ensure_ascii=False, indent=1)
