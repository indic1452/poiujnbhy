"""Независимая проверка каталога mobilnaya (второй агент): построчная сверка записей с первоисточником (pymupdf, не tekst/ сборщика),
прогон своих кодеров против тестовых векторов (libosmocore, стандарты); журнал → proverka/zhurnal.json, сводка → proverka/itog.json."""
import collections, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pv
import p_gsm, p_3gpp, p_pmr, p_prochie, p_tret   # noqa: F401 — каждый модуль пишет в pv.ZHURNAL
Z = pv.ZHURNAL
imena = {x["запись"] for x in Z}
itog = {"проверок": len(Z), "итоги": collections.Counter(x["итог"] for x in Z), "записей_проверено": len(imena), "записей_в_каталоге": len(pv.KAT),
        "доля": round(len(imena) / len(pv.KAT), 3), "расхождения": [x for x in Z if x["итог"] != "совпало"], "проверенные_записи": sorted(imena)}
pv.sohranit(os.path.join(pv.KOREN, "proverka", "zhurnal.json"))
json.dump(itog, open(os.path.join(pv.KOREN, "proverka", "itog.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in itog.items() if k != "проверенные_записи"}, ensure_ascii=False)[:2000])
